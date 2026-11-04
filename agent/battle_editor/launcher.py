"""Shared, short-lived launcher for the CMD and Maa Custom Action entries."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser

if __package__ in (None, ""):
    # The portable CMD executes this file directly, including with embedded Python.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from battle_editor.storage import project_identity
else:
    from .storage import project_identity


DEFAULT_PORT = 18765
PROTOCOL_VERSION = 1
SERVICE_NAME = "maafgo-battle-editor"


class EditorLaunchError(RuntimeError):
    """A startup failure that can be reported in the Maa task log."""


@dataclass(frozen=True)
class LaunchResult:
    url: str
    pid: int
    project_root: str
    mode: str
    reused: bool = False
    browser_opened: bool = False
    log_path: str = ""
    replaced_pid: int | None = None
    replaced_project_root: str | None = None


def _root(value: str | Path | None = None) -> Path:
    return Path(value).resolve() if value is not None else Path(__file__).resolve().parents[2]


def _url(port: int) -> str:
    if type(port) is not int or not 1 <= port <= 65535:
        raise EditorLaunchError("编辑器端口必须是 1–65535 的整数")
    return f"http://127.0.0.1:{port}"


def _request(url: str, *, token: str | None = None, body: dict | None = None, project_id: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if project_id is not None:
        headers["X-Editor-Project"] = project_id
    if token is not None:
        headers["X-Editor-Token"] = token
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers)
    # A desktop proxy setting must not send local control requests off the machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=1.5) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("响应不是 JSON 对象")
    return value


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.settimeout(0.3)
        return stream.connect_ex(("127.0.0.1", port)) == 0


def _service_health(port: int) -> dict | None:
    """Read compatible editor identity without implicitly adopting its directory."""
    base = _url(port)
    try:
        health = _request(base + "/health")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        if _port_in_use(port):
            raise EditorLaunchError(
                f"端口 {port} 已被其他程序占用，或编辑器尚未就绪；不会打开其他服务。"
            ) from exc
        return None
    if (health.get("service") != SERVICE_NAME or type(health.get("protocolVersion")) is not int
            or health["protocolVersion"] != PROTOCOL_VERSION):
        raise EditorLaunchError(f"端口 {port} 的服务不是兼容的 MaaFGO 编队编辑器")
    actual_root = health.get("projectRoot")
    if not isinstance(actual_root, str) or not actual_root or not Path(actual_root).is_absolute():
        raise EditorLaunchError("编辑器返回了无效的安装目录")
    if type(health.get("pid")) is not int or health["pid"] <= 0:
        raise EditorLaunchError("编辑器返回了无效的进程身份")
    try:
        expected_id = project_identity(_root(actual_root))
    except (OSError, ValueError, RuntimeError) as exc:
        raise EditorLaunchError("编辑器返回了无效的安装目录") from exc
    if health.get("projectId") not in (None, expected_id):
        raise EditorLaunchError("编辑器返回的项目身份与安装目录不一致")
    return health


def _same_root(first, second) -> bool:
    return os.path.normcase(str(_root(first))) == os.path.normcase(str(_root(second)))


def service_status(project_root: str | Path | None = None, port: int = DEFAULT_PORT) -> dict | None:
    """Status/stop remain scoped to this installation, even with launch takeover."""
    health = _service_health(port)
    if health is not None and not _same_root(health["projectRoot"], _root(project_root)):
        raise EditorLaunchError(f"端口 {port} 正在服务另一份 MaaFGO：{health['projectRoot']}；本目录状态查询不会接管它。")
    return health


@contextmanager
def _launch_lock(port: int, timeout: float):
    """Serialize launches across installations, without retaining a lock in the daemon."""
    user = hashlib.sha256(getpass.getuser().encode("utf-8")).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"{SERVICE_NAME}-{user}-{port}.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        deadline = time.monotonic() + max(0.1, timeout)
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise EditorLaunchError("另一启动器正在处理此端口，请稍后重试") from exc
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _verify_backend_process(health: dict, port: int):
    """Only a matching editor entry owning this listener may be asked to retire."""
    try:
        import psutil
        process = psutil.Process(health["pid"])
        created, command = process.create_time(), process.cmdline()
        root = _root(health["projectRoot"])
        entry = root / "agent" / "battle_editor" / "main.py"
        def option(name):
            return command[command.index(name) + 1] if name in command and command.index(name) + 1 < len(command) else None
        if (not any(_same_root(arg, entry) for arg in command[1:] if arg and not arg.startswith("-"))
                or option("--project-root") is None or not _same_root(option("--project-root"), root)
                or option("--port") != str(port)):
            raise EditorLaunchError("旧服务的启动参数不能确认为编队后端，拒绝自动停止")
        if not any(connection.status == psutil.CONN_LISTEN and connection.laddr
                   and connection.laddr.ip == "127.0.0.1" and connection.laddr.port == port
                   for connection in process.net_connections(kind="tcp")):
            raise EditorLaunchError("旧服务PID与端口监听身份不一致，拒绝自动停止")
        if not process.is_running() or process.create_time() != created:
            raise EditorLaunchError("旧服务进程身份已变化，请重试启动")
        return process, created, command
    except ImportError as exc:
        raise EditorLaunchError("缺少psutil，无法核实旧后端身份；请手动停止旧服务") from exc
    except (psutil.Error, OSError, ValueError) as exc:
        raise EditorLaunchError("无法核实旧后端进程身份，未停止任何程序") from exc


def _retire_service(health: dict, port: int, timeout: float):
    process, created, command = _verify_backend_process(health, port)
    import psutil
    base = _url(port)
    project_id = project_identity(_root(health["projectRoot"]))
    try:
        session = _request(base + "/api/session", project_id=project_id)
        current = _service_health(port)
        identity = ("pid", "projectRoot", "projectId", "startedAt")
        if (current is None or any(current.get(key) != health.get(key) for key in identity)
                or not process.is_running() or process.create_time() != created or process.cmdline() != command):
            raise EditorLaunchError("旧服务身份在接管期间发生变化，未发送退出请求")
        _request(base + "/api/shutdown", token=session["token"], body={}, project_id=project_id)
    except (psutil.Error, OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        raise EditorLaunchError("旧后端未完成正常退出请求，未强制终止进程；请稍后重试") from exc
    deadline = time.monotonic() + max(0.1, timeout)
    while process.is_running():
        try:
            # POSIX children can be fully exited but not yet reaped by their launcher.
            if process.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        except psutil.Error as exc:
            raise EditorLaunchError("无法确认旧后端已退出，未强制终止进程；请稍后重试") from exc
        if time.monotonic() >= deadline:
            raise EditorLaunchError("旧后端仍在完成已接收的请求，暂不启动新服务；未强制终止进程，请稍后重试")
        time.sleep(0.05)
    if _port_in_use(port):
        raise EditorLaunchError("旧后端已退出，但端口被另一程序占用；未停止该程序，请重试")


def _check_install(root: Path):
    entry = root / "agent" / "battle_editor" / "main.py"
    if not entry.is_file():
        raise EditorLaunchError(f"编辑器服务文件缺失：{entry}")
    static_roots = (root / "assets" / "web" / "battle-editor", root / "web" / "battle-editor")
    if not any((path / "index.html").is_file() and not (path / "src").is_dir() for path in static_roots):
        raise EditorLaunchError("编辑器页面未构建或缺失，请在开发目录运行 npm --prefix web/battle-editor run build")
    return entry


def _open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except (OSError, webbrowser.Error):
        return False


def _open_editor_locked(
    project_root: str | Path | None = None,
    *,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    owner_pid: int | None = None,
    owner_created: float | None = None,
    idle_timeout: int = 3600,
    timeout: float = 20.0,
    replace_existing: bool = True,
) -> LaunchResult:
    """Start or reuse the detached server, confirm readiness, then open its UI."""
    root = _root(project_root)
    base = _url(port)
    log_path = root / "config" / "BattleEditor" / "service.log"
    health = _service_health(port)
    replaced = None
    if health is not None and not _same_root(health["projectRoot"], root):
        if not replace_existing:
            raise EditorLaunchError(f"端口 {port} 正在服务另一份 MaaFGO：{health['projectRoot']}；自动接管已禁用。")
        _check_install(root)
        _retire_service(health, port, min(timeout, 10.0))
        replaced, health = health, None
    reused = health is not None
    if health is None:
        entry = _check_install(root)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, "-X", "utf8", str(entry), "--project-root", str(root), "--port", str(port),
                "--idle-timeout", str(idle_timeout), "--require-project-binding"]
        if owner_pid is not None:
            args += ["--owner-pid", str(owner_pid)]
        if owner_created is not None:
            args += ["--owner-created", str(owner_created)]
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            with log_path.open("ab", buffering=0) as log_stream:
                child = subprocess.Popen(
                    args, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                    stdout=log_stream, stderr=log_stream, close_fds=True,
                    creationflags=flags,
                )
            # Keep the process handle alive and reap it when it exits, without
            # retaining the launcher or coupling the daemon to a Maa task.
            threading.Thread(target=child.wait, name="battle-editor-child", daemon=True).start()
        except OSError as exc:
            raise EditorLaunchError(f"启动编辑器失败：{exc}；日志：{log_path}") from exc
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                health = service_status(root, port)
            except EditorLaunchError as exc:
                # A listener can become reachable before its first health response.
                last_error = exc
                health = None
            if health is not None:
                reused = int(health["pid"]) != child.pid
                break
            if child.poll() is not None:
                # Another simultaneous launcher might have won the bind race.
                time.sleep(0.15)
                try:
                    health = service_status(root, port)
                except EditorLaunchError as exc:
                    last_error = exc
                if health is not None:
                    reused = True
                    break
                raise EditorLaunchError(
                    f"编辑器服务提前退出（{child.returncode}）。{last_error or ''} 日志：{log_path}"
                )
            time.sleep(0.1)
        if health is None:
            # Only stop the process created by this launcher; never kill a port owner.
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
            raise EditorLaunchError(f"编辑器启动超时。{last_error or ''} 日志：{log_path}")
    url = base + "/?project=" + project_identity(root)
    return LaunchResult(
        url=url, pid=int(health["pid"]), project_root=str(root),
        mode=str(health.get("mode", "standalone")), reused=reused,
        browser_opened=_open_browser(url) if open_browser else False, log_path=str(log_path),
        replaced_pid=replaced["pid"] if replaced else None,
        replaced_project_root=replaced["projectRoot"] if replaced else None,
    )


def open_editor(project_root: str | Path | None = None, *, port: int = DEFAULT_PORT,
                open_browser: bool = True, owner_pid: int | None = None,
                owner_created: float | None = None, idle_timeout: int = 3600,
                timeout: float = 20.0, replace_existing: bool = True) -> LaunchResult:
    _url(port)
    with _launch_lock(port, timeout):
        return _open_editor_locked(project_root, port=port, open_browser=open_browser,
            owner_pid=owner_pid, owner_created=owner_created, idle_timeout=idle_timeout,
            timeout=timeout, replace_existing=replace_existing)


def stop_editor(project_root: str | Path | None = None, *, port: int = DEFAULT_PORT) -> bool:
    root = _root(project_root)
    if service_status(root, port) is None:
        return False
    base = _url(port)
    try:
        project_id = project_identity(root)
        session = _request(base + "/api/session", project_id=project_id)
        _request(base + "/api/shutdown", token=session["token"], body={}, project_id=project_id)
    except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        raise EditorLaunchError(f"停止服务失败：{exc}") from exc
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _port_in_use(port):
            return True
        time.sleep(0.1)
    raise EditorLaunchError("服务尚未完成停止，请查看编辑器日志")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打开 MaaFGO 编队与战斗编辑器")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--owner-pid", type=int)
    parser.add_argument("--owner-created", type=float)
    parser.add_argument("--idle-timeout", type=int, default=3600)
    parser.add_argument("--no-takeover", action="store_true", help="不自动替换另一安装目录的编辑服务")
    args = parser.parse_args(argv)
    try:
        if args.stop:
            value = {"stopped": stop_editor(args.project_root, port=args.port)}
        elif args.status:
            value = {"health": service_status(args.project_root, args.port)}
        else:
            value = asdict(open_editor(
                args.project_root, port=args.port, open_browser=not args.no_browser,
                owner_pid=args.owner_pid, owner_created=args.owner_created, idle_timeout=args.idle_timeout,
                replace_existing=not args.no_takeover,
            ))
        if sys.stdout is not None:
            print(json.dumps(value, ensure_ascii=False))
        return 0
    except (EditorLaunchError, OSError) as exc:
        if sys.stderr is not None:
            print(f"[编队编辑器] {exc}", file=sys.stderr)
        elif os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "MaaFGO 编队编辑器", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
