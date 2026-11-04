"""A loopback-only, same-origin HTTP host; no Maa or controller imports."""
from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import DEFAULT_PORT, PROTOCOL_VERSION, SERVICE_NAME
from .storage import BattleStorage, EditorError, MAX_JSON_BYTES, battle_name, decode_json, project_identity, utc_now, within
from .validation import DEFAULTS, validate_battle
from .screenshot_import import IMAGE_TYPES, MAX_IMAGE_BYTES, ScreenshotWorker

logger = logging.getLogger(__name__)


class EditorHTTPServer(ThreadingHTTPServer):
    # Closing the service waits for already accepted saves; their atomic writes finish.
    daemon_threads = False
    block_on_close = True
    # POSIX needs SO_REUSEADDR to restart while closed HTTP sockets are in
    # TIME_WAIT. Keep Windows' existing bind behavior; never enable SO_REUSEPORT.
    allow_reuse_address = os.name != "nt"

    def __init__(self, root: str | Path, port: int = DEFAULT_PORT, *, owner_pid: int | None = None, owner_created: float | None = None, idle_timeout: float = 3600, require_project_binding: bool = False):
        if owner_pid is not None and owner_created is None:
            raise ValueError("MXU 宿主必须同时提供 PID 与进程创建时间")
        self.storage = BattleStorage(root)
        self.root = self.storage.root
        self.require_project_binding = require_project_binding
        self.mode = "mxu" if owner_pid is not None else "standalone"
        self.owner_pid, self.owner_created = owner_pid, owner_created
        self.owner_executable = None
        if owner_pid is not None:
            import psutil
            owner = psutil.Process(owner_pid)
            if abs(owner.create_time() - owner_created) > 0.02:
                raise ValueError("MXU 宿主身份不匹配，拒绝绑定可能已被复用的 PID")
            self.owner_executable = owner.exe()
        self.idle_timeout = max(1.0, float(idle_timeout))
        self.last_activity = time.monotonic()
        self.started_at = utc_now()
        self.token = secrets.token_urlsafe(32)
        self._catalog = None
        self._catalog_lock = threading.Lock()
        self._bond_planning = threading.BoundedSemaphore(1)
        self._card_renderer = None
        self._card_renderer_lock = threading.Lock()
        self._screenshot_worker = ScreenshotWorker(self.root)
        self._stopping = threading.Event()
        candidates = (self.root / "assets" / "web" / "battle-editor", self.root / "web" / "battle-editor")
        self.static_root = next((directory for directory in candidates if (directory / "index.html").is_file() and not (directory / "src").is_dir()), None)
        if self.static_root is None:
            raise FileNotFoundError("编队编辑器静态产物缺失；请构建前端或重新安装完整资源包，不能直接托管 Vite 源码页面")
        within(self.static_root, self.root)
        super().__init__(("127.0.0.1", port), EditorHandler)

    def health(self) -> dict:
        return {"service": SERVICE_NAME, "protocolVersion": PROTOCOL_VERSION, "projectId": project_identity(self.root), "pid": os.getpid(), "projectRoot": str(self.root), "mode": self.mode, "ownerPid": self.owner_pid, "ownerCreated": self.owner_created, "startedAt": self.started_at, "idleTimeout": self.idle_timeout, "requiresProjectBinding": self.require_project_binding}

    def capabilities(self) -> dict:
        return {"projectRoot": str(self.root), "battleDirectory": str(self.storage.battle_dir), "mode": self.mode, "idleTimeout": self.idle_timeout, "features": {"formation": True, "skills": True, "masterSkills": True, "orderChange": True, "retirement": True, "mixedCards": True, "enemyTarget": True, "drafts": True, "hashConflicts": True, "backups": True, "conditionalActions": False, "customSkillSelections": True, "applyToCurrentTask": False}, "supportedSpecialSkillFields": ["tdTypeChanges", "skillActSelectSelections"], "defaults": DEFAULTS, "npCardPreview": {"enabled": True, "regions": ["JP", "CN"], "defaultRegion": "JP", "endpoint": "/api/np-card", "faceEndpoint": "/api/command-card"}}

    def card_preview(self, query: str, *, card_type: str):
        from .np_cards import NpCardRenderer, parse_np_card_query, parse_face_card_query
        request = (parse_np_card_query if card_type == "np" else parse_face_card_query)(query)
        with self._card_renderer_lock:
            if self._card_renderer is None:
                self._card_renderer = NpCardRenderer(self.root)
        return self._card_renderer.render(request)

    def server_close(self):
        try:
            super().server_close()
        finally:
            self._screenshot_worker.close()

    def catalog(self) -> dict:
        with self._catalog_lock:
            if self._catalog is None:
                from .catalog import build_catalog
                self._catalog = build_catalog(self.root)
            return self._catalog

    def validate(self, data) -> dict:
        try:
            catalog = self.catalog()
        except (ImportError, FileNotFoundError):
            # Formation-only documents are useful even if optional extra metadata is absent.
            catalog = None
        return validate_battle(data, catalog)

    def bond_settings(self):
        from .bond_optimizer import BondPlanner
        return BondPlanner(self.root, self.catalog()).settings()

    def optimize_bond(self, data, options):
        # An isolated, bounded worker keeps CPU-heavy all-catalog planning from
        # blocking heartbeats, shutdown or other editing requests. No Maa/device
        # code is loaded by this adapter and it never writes a battle file.
        if not self._bond_planning.acquire(blocking=False):
            raise EditorError('bond_busy', '已有羁绊规划正在执行，请稍候', 409)
        worker = r'''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'agent'))
from battle_editor.bond_optimizer import BondPlanner
from battle_editor.storage import EditorError
try:
    request = json.load(sys.stdin)
    result = BondPlanner(root).optimize(request.get('data'), request.get('options'))
    response = {'ok': True, 'result': result}
except EditorError as error:
    response = {'ok': False, 'error': {'code': error.code, 'message': error.message, 'status': error.status}}
print(json.dumps(response, ensure_ascii=False, allow_nan=False))
'''
        try:
            kwargs = {'cwd':str(self.root), 'input':json.dumps({'data':data,'options':options},ensure_ascii=False),
                      'capture_output':True, 'encoding':'utf-8', 'timeout':45,
                      'env':os.environ | {'PYTHONIOENCODING':'utf-8'}}
            if os.name == 'nt': kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            process = subprocess.run([sys.executable,'-c',worker,str(self.root)], **kwargs)
            if process.returncode or len(process.stdout.encode('utf-8')) > 8 * 1024 * 1024:
                raise EditorError('bond_planning_failed', '羁绊规划未完成，当前方案未修改', 422)
            response = json.loads(process.stdout)
            if not response.get('ok'):
                error = response.get('error') or {}
                raise EditorError(error.get('code','bond_planning_failed'),error.get('message','羁绊规划失败'),error.get('status',422))
            return response['result']
        except subprocess.TimeoutExpired as error:
            raise EditorError('bond_timeout', '规划超过45秒，请缩小候选范围后重试，当前方案未修改', 408) from error
        except (ValueError, KeyError) as error:
            raise EditorError('bond_planning_failed', '羁绊规划响应无效，当前方案未修改', 422) from error
        finally:
            self._bond_planning.release()

    def stop(self):
        if not self._stopping.is_set():
            self._stopping.set()
            threading.Thread(target=self.shutdown, name="battle-editor-shutdown", daemon=True).start()

    def monitor(self):
        """PID and creation time survive Agent restarts without following a reused PID."""
        while not self._stopping.wait(1):
            if self.owner_pid is not None:
                try:
                    import psutil
                    process = psutil.Process(self.owner_pid)
                    alive = process.is_running() and abs(process.create_time() - self.owner_created) <= 0.02 and process.exe() == self.owner_executable
                    if process.status() == psutil.STATUS_ZOMBIE:
                        alive = False
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    alive = False
                if not alive:
                    logger.info("MXU 宿主已退出，停止编队编辑服务")
                    self.stop()
            elif time.monotonic() - self.last_activity >= self.idle_timeout:
                logger.info("独立模式空闲超时，停止编队编辑服务")
                self.stop()

    def serve_forever(self, poll_interval=0.25):
        watcher = threading.Thread(target=self.monitor, name="battle-editor-lifecycle", daemon=True)
        watcher.start()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            self._stopping.set()

    def import_source(self, source) -> dict:
        if not isinstance(source, str) or not source.strip() or len(source) > 300000:
            raise EditorError("invalid_source", "请输入已有 Battle 文件名、Chaldea 分享链接或 ID")
        source = source.strip()
        # A local import is a basename, never a path or arbitrary URL.
        existing_local = False
        if not re.fullmatch(r"[0-9]+", source):
            try:
                local_name = battle_name(source)
                existing_local = within(self.storage.battle_dir / local_name, self.storage.battle_dir).is_file()
            except EditorError:
                pass
        if existing_local or source.lower().endswith(".json") or (not re.fullmatch(r"[0-9]+", source) and not source.startswith(("http://", "https://", "data=", "?", "id=", "G", "H4s"))):
            record = self.storage.read_battle(source)
            return record | {"validation": self.validate(record["data"]), "source": source}
        if source.startswith(("http://", "https://")):
            try:
                parsed = urlsplit(source)
                host = (parsed.hostname or "").lower()
                if parsed.username or parsed.password or parsed.port not in (None, 80, 443) or not (host == "chaldea.center" or host.endswith(".chaldea.center")):
                    raise ValueError("not a Chaldea share")
            except ValueError as exc:
                raise EditorError("invalid_source", "只接受 Chaldea 官方分享链接，不提供任意 URL 代理") from exc
        # Existing fetch_share_data owns parsing and network behavior. Isolate its legacy
        # module globals and logging in a child, use an absolute cache path, and disable
        # its eager cache writes so imported data always enters the validation/draft flow.
        worker = r'''
import base64, contextlib, gzip, io, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path[:0] = [str(root / 'agent'), str(root / 'agent' / 'custom')]
source = json.loads(sys.stdin.read())['source']
with contextlib.redirect_stdout(sys.stderr):
    import chaldea
    qid, tid, encoded = chaldea.parse_import_source(source)
    if not (qid or tid or encoded):
        raise ValueError('无法解析 Chaldea 分享来源')
    original_decode = chaldea.decode_content
    def bounded_decode(content):
        text = content[1:] if content.startswith('G') else content
        compressed = base64.urlsafe_b64decode(text + '=' * ((-len(text)) % 4))
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            if len(stream.read(4 * 1024 * 1024 + 1)) > 4 * 1024 * 1024:
                raise ValueError('分享解码后超过 4 MiB')
        return original_decode(content)
    chaldea.decode_content = bounded_decode
    boundary = (root / 'config' / 'Battle').resolve()
    original_load = chaldea._load_cache_file
    def safe_load(path):
        resolved = pathlib.Path(path).resolve()
        if not resolved.is_relative_to(boundary) or not resolved.is_file() or resolved.stat().st_size > 4 * 1024 * 1024:
            return None
        return original_load(str(resolved))
    chaldea._load_cache_file = safe_load
    chaldea._load_local_file = lambda source: None
    chaldea.CACHE_DIR = str(boundary)
    chaldea._save_cache = lambda *args, **kwargs: None
    chaldea._build_cache_name = lambda *args, **kwargs: 'editor_import'
    data, qid, tid = chaldea.fetch_share_data(source)
    if not isinstance(data, dict):
        raise ValueError('未能获取或解码 Chaldea 分享数据')
json.dump({'data': data, 'questId': qid, 'teamId': tid}, sys.stdout, ensure_ascii=False, allow_nan=False)
'''
        kwargs = {"cwd": str(self.root), "input": json.dumps({"source": source}), "encoding": "utf-8", "capture_output": True, "timeout": 65, "env": os.environ | {"PYTHONIOENCODING": "utf-8"}}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run([sys.executable, "-c", worker, str(self.root)], **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise EditorError("import_timeout", "分享导入超时，请检查网络后重试", 504) from exc
        if proc.returncode:
            logger.warning("分享导入失败：%s", proc.stderr[-1800:])
            raise EditorError("import_failed", "分享来源无法解析、获取或解码，请核对链接 / ID 和网络后重试", 422)
        result = decode_json(proc.stdout.encode("utf-8"))
        return result | {"source": source, "validation": self.validate(result["data"])}


class EditorHandler(BaseHTTPRequestHandler):
    server: EditorHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "MaaFGOEditor/1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)
        self._body_read = 0

    def log_message(self, fmt, *args):
        logger.info("%s %s", self.address_string(), fmt % args)

    def _security(self, token=False):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if len(self.headers.get_all("Host", [])) != 1 or host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise EditorError("invalid_host", "只允许本机编辑器地址访问", 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            raise EditorError("invalid_origin", "拒绝跨站访问本地编辑服务", 403)
        if self.headers.get("Sec-Fetch-Site", "none") not in {"same-origin", "none"}:
            raise EditorError("invalid_origin", "拒绝跨站访问本地编辑服务", 403)
        if token and not hmac.compare_digest(self.headers.get("X-Editor-Token", ""), self.server.token):
            raise EditorError("invalid_token", "编辑器会话已失效，请刷新页面重新连接", 403)

    def _headers(self, status, content_type, length, *, cache=False, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "private, max-age=60" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()

    def _json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(raw))
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _content_length(self):
        values = self.headers.get_all("Content-Length", [])
        if not values:
            return 0
        if len(values) != 1 or not re.fullmatch(r"[0-9]+", values[0].strip()):
            raise EditorError("invalid_length", "Content-Length 无效或重复")
        try:
            return int(values[0].strip())
        except ValueError as exc:
            raise EditorError("invalid_length", "Content-Length 无效") from exc

    def _discard_unread_body(self):
        """Drain only a bounded, declared remainder before closing an error response.

        Closing a Windows TCP socket with unread request data can send RST and
        discard the HTTP rejection in flight. This is transport cleanup after
        rejection, never permission to parse or execute an unauthenticated body.
        Unknown/chunked/oversized framing is not read, and slow senders cannot
        hold an error response beyond the short drain deadline.
        """
        if self.headers.get_all("Transfer-Encoding", []):
            return
        try:
            length = self._content_length()
        except EditorError:
            return
        remaining = length - self._body_read
        if length > MAX_JSON_BYTES or remaining <= 0:
            return
        deadline = time.monotonic() + 0.2
        original_timeout = self.connection.gettimeout()
        try:
            while remaining:
                timeout = min(0.2, deadline - time.monotonic())
                if timeout <= 0:
                    break
                self.connection.settimeout(timeout)
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    break
                self._body_read += len(chunk)
                remaining -= len(chunk)
        except (OSError, ValueError):
            # A partial/closed request must still get a best-effort error reply.
            pass
        finally:
            self.connection.settimeout(original_timeout)

    def _body(self):
        if self.headers.get_all("Transfer-Encoding", []):
            raise EditorError("invalid_body", "不接受分块请求正文")
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise EditorError("invalid_content_type", "请使用 application/json 请求正文", 415)
        length = self._content_length()
        if length > MAX_JSON_BYTES:
            raise EditorError("too_large", "请求正文超过 4 MiB 限制", 413)
        chunks = []
        while self._body_read < length:
            chunk = self.rfile.read1(min(length - self._body_read, 65536))
            if not chunk:
                raise EditorError("invalid_body", "请求正文未完整接收")
            self._body_read += len(chunk)
            chunks.append(chunk)
        raw = b"".join(chunks)
        value = decode_json(raw)
        if not isinstance(value, dict):
            raise EditorError("invalid_body", "请求正文必须是 JSON 对象")
        return value

    def _image_body(self):
        if self.headers.get_all('Transfer-Encoding', []):
            raise EditorError('invalid_body', '不接受分块请求正文')
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() not in IMAGE_TYPES:
            raise EditorError('image_format', '支持 PNG、JPG/JPEG、WebP 静态图片。', 415)
        length = self._content_length()
        if length <= 0 or length > MAX_IMAGE_BYTES:
            raise EditorError('image_size', '图片为空或超过16 MiB，请换一张较小的截图。', 413)
        chunks = []
        while self._body_read < length:
            chunk = self.rfile.read1(min(length - self._body_read, 65536))
            if not chunk:
                raise EditorError('invalid_body', '图片未完整接收，请重试')
            self._body_read += len(chunk);chunks.append(chunk)
        return b''.join(chunks)

    def _file(self, path: Path):
        if not path.is_file():
            raise EditorError("not_found", "资源不存在，请确认静态页面已经构建并随包安装", 404)
        raw = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in (".js", ".mjs"):
            mime = "text/javascript"
        elif path.suffix.lower() == ".webp":
            # Some packaged Windows Python installs have no WebP MIME registry entry.
            mime = "image/webp"
        if mime.startswith("text/") or mime in ("application/json", "image/svg+xml"):
            mime += "; charset=utf-8"
        # The HTML entry must pick up a newly installed hashed JS bundle immediately.
        self._headers(200, mime, len(raw), cache=path.suffix.lower() != ".html")
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _dispatch(self):
        self._security()
        try:
            path = unquote(urlsplit(self.path).path, encoding="utf-8", errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise EditorError("invalid_path", "请求路径编码无效") from exc
        if "\x00" in path or "\\" in path:
            raise EditorError("unsafe_path", "请求路径无效", 403)
        method = "GET" if self.command == "HEAD" else self.command
        if path == "/health" and method == "GET":
            self._json(200, self.server.health())
            return
        if path.startswith("/api/"):
            project = self.headers.get("X-Editor-Project")
            if project is None and self.server.require_project_binding:
                raise EditorError("project_required", "此服务需要绑定安装目录，请从本次启动器打开新页面；旧页面内容仍保留。", 403)
            if project is not None and (len(self.headers.get_all("X-Editor-Project", [])) != 1
                    or project != project_identity(self.server.root)):
                raise EditorError("project_mismatch", "当前端口已切换到另一份 MaaFGO，拒绝将旧页面的数据写入此目录；请从启动器打开新页面。", 409)
        if path == "/api/session" and method == "GET":
            self.server.last_activity = time.monotonic()
            self._json(200, {"token": self.server.token, "projectId": project_identity(self.server.root)})
            return
        if path.startswith("/api/"):
            self._security(token=True)
            self.server.last_activity = time.monotonic()
            if self.server._stopping.is_set():
                raise EditorError("service_stopping", "编辑服务正在停止，请重新运行启动入口", 503)
            image_request = method == 'POST' and path == '/api/import-image'
            body = self._image_body() if image_request else self._body() if method in ("POST", "PUT") else None
            storage = self.server.storage
            if method == "GET" and path in ("/api/np-card", "/api/command-card"):
                preview = self.server.card_preview(urlsplit(self.path).query, card_type="np" if path == "/api/np-card" else "face")
                self._headers(200, "image/png", len(preview.png), cache=True,
                              extra_headers={"X-Editor-Card-Portrait-Source": preview.portrait_source})
                if self.command != "HEAD":
                    self.wfile.write(preview.png)
            elif method == "GET" and path == "/api/capabilities":
                self._json(200, self.server.capabilities())
            elif method == "GET" and path == "/api/catalog":
                self._json(200, self.server.catalog())
            elif method == 'GET' and path == '/api/bond/settings':
                self._json(200, self.server.bond_settings())
            elif method == 'POST' and path == '/api/bond/optimize':
                self._json(200, self.server.optimize_bond(body.get('data'), body.get('options')))
            elif method == "GET" and path == "/api/battles":
                self._json(200, storage.list_battles())
            elif method == "GET" and path.startswith("/api/battles/"):
                self._json(200, storage.read_battle(path[len("/api/battles/"):]))
            elif method == "POST" and path == "/api/validate":
                self._json(200, self.server.validate(body.get("data")))
            elif (method == "POST" and path == "/api/battles") or (method == "PUT" and path.startswith("/api/battles/")):
                if "data" not in body:
                    raise EditorError("missing_data", "缺少 Chaldea data 对象")
                validation = self.server.validate(body["data"])
                if not validation["valid"]:
                    raise EditorError("validation_failed", "存在阻断问题，请修正后保存；当前内容仍可保存草稿或下载保真副本", 422, validation)
                create = method == "POST"
                name = body.get("name") if create else path[len("/api/battles/"):]
                record = storage.save_battle(name, body["data"], body.get("expectedHash"), create=create)
                self._json(201 if create else 200, record | {"validation": validation})
            elif method == "GET" and path == "/api/drafts":
                self._json(200, storage.list_drafts())
            elif method == "GET" and path.startswith("/api/drafts/"):
                self._json(200, storage.read_draft(path[len("/api/drafts/"):]))
            elif method == "PUT" and path.startswith("/api/drafts/"):
                self._json(200, storage.save_draft(path[len("/api/drafts/"):], body))
            elif method == "POST" and path == "/api/import":
                self._json(200, self.server.import_source(body.get("source")))
            elif method == "POST" and path == "/api/import-bbc":
                from .bbc_import import import_bbc_document
                result = import_bbc_document(self.server.root, body.get("config"), body.get("name"))
                self._json(200, result | {"validation": self.server.validate(result["data"])})
            elif method == "POST" and path == "/api/share-image/encode":
                from .share_image import encode_share
                self._json(200, encode_share(body.get("data")))
            elif image_request:
                result = self.server._screenshot_worker.recognize(body)
                if result.get("kind") == "chaldea":
                    team_id = result.get("teamId")
                    if type(team_id) is not int or not 0 < team_id <= 9007199254740991:
                        raise EditorError("chaldea_team_id", "Chaldea 图片中的 Team ID 无效，请使用清晰原图。", 422)
                    # Only the validated ID is used, never an OCR-derived URL or
                    # bare number (legacy parsing treats long numbers as quest IDs).
                    source = f"https://link.chaldea.center/laplace/share?id={team_id}"
                    try:
                        imported = self.server.import_source(source)
                    except EditorError as exc:
                        message = f"已识别 Chaldea 图片的 Team ID {team_id}，但未能获取队伍配置；请检查网络、队伍是否仍公开，或尝试手动导入分享链接。"
                        raise EditorError("chaldea_fetch_failed", message, exc.status) from exc
                    if imported.get("teamId") != team_id:
                        raise EditorError("chaldea_team_mismatch", "Chaldea 返回的队伍 ID 与图片不一致，已停止导入，请核对原始分享。", 422)
                    result = result | {"schema": 1, "source": source, "data": imported["data"],
                                       "validation": imported["validation"], "warnings": result.get("warnings", [])}
                elif result.get("kind") == "share":
                    result = result | {"validation": self.server.validate(result["data"])}
                self._json(200, result)
            elif method == "POST" and path == "/api/heartbeat":
                self._json(200, {"ok": True, "mode": self.server.mode})
            elif method == "POST" and path == "/api/shutdown":
                self._json(200, {"ok": True})
                self.server.stop()
            else:
                raise EditorError("not_found", "接口或请求方法不存在", 404)
            return
        if method != "GET":
            raise EditorError("method_not_allowed", "只允许读取静态资源", 405)
        if path.startswith("/media/"):
            pieces = path.split("/")
            if len(pieces) != 4:
                raise EditorError("unsafe_path", "图片路径无效", 403)
            from .catalog import resolve_media
            media = resolve_media(self.server.root, pieces[2], pieces[3])
            if media is None:
                raise EditorError("not_found", "图片资源不存在", 404)
            self._file(within(Path(media), self.server.root))
            return
        relative = path.lstrip("/") or "index.html"
        resource = within(self.server.static_root / relative, self.server.static_root)
        self._file(resource)

    def _handle(self):
        try:
            self._dispatch()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Closing the import dialog aborts its fetch. The response can no
            # longer be delivered; do not try to send a second error response.
            logger.debug('编辑器客户端已断开请求')
        except EditorError as exc:
            self._discard_unread_body()
            value = {"code": exc.code, "message": exc.message}
            if exc.details is not None:
                value["details"] = exc.details
            self._json(exc.status, {"error": value})
        except OSError as exc:
            self._discard_unread_body()
            logger.exception("文件或网络操作失败")
            self._json(500, {"error": {"code": "io_error", "message": f"文件操作失败：{exc}。请检查目录权限、磁盘空间或文件占用后重试"}})
        except Exception:
            self._discard_unread_body()
            logger.exception("编辑服务请求失败")
            self._json(500, {"error": {"code": "internal_error", "message": "编辑服务处理失败，编辑内容未清除；请查看 config/BattleEditor 服务日志"}})

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_OPTIONS = _handle


def create_server(project_root: str | Path, port: int = DEFAULT_PORT, **kwargs) -> EditorHTTPServer:
    return EditorHTTPServer(project_root, port, **kwargs)
