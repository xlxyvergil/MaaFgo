import json
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import List, Tuple
from uuid import uuid4

DIRECT_RUN_EXTRA_ARGS: list[str] = []


def _collect_direct_run_args(argv: list[str]) -> list[str]:
    args = []
    if "-d" in argv or "--direct-run" in argv:
        args.append("-d")
    return args


DIRECT_RUN_EXTRA_ARGS = _collect_direct_run_args(sys.argv)


class _SingleInstanceLock:
    """跨平台进程互斥（同一二进制/脚本只允许一个主进程运行）。

    - Windows: msvcrt.locking(非阻塞)
    - macOS/Linux: fcntl.flock(非阻塞)

    只要当前进程不退出且文件句柄不关闭，锁就会一直持有；进程崩溃时 OS 会自动释放锁。
    """

    def __init__(self, lock_key: str):
        self.lock_key = str(lock_key)
        self._fp = None
        self.lock_path = None

    @staticmethod
    def _make_lock_path(lock_key: str) -> str:
        # 只做"同一二进制互斥"：用可执行文件绝对路径做 key，避免不同安装目录互相影响。
        h = hashlib.sha256(lock_key.encode("utf-8")).hexdigest()[:16]
        filename = f"mfw_single_instance_{h}.lock"
        return os.path.join(tempfile.gettempdir(), filename)

    def acquire(self) -> bool:
        if self._fp is not None:
            return True

        self.lock_path = self._make_lock_path(self.lock_key)
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)

        # a+：文件不存在时创建；不截断
        self._fp = open(self.lock_path, "a+", encoding="utf-8")
        self._fp.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                # 确保文件至少 1 字节，否则某些环境下锁定长度可能有坑
                self._fp.seek(0, os.SEEK_END)
                if self._fp.tell() == 0:
                    self._fp.write("0")
                    self._fp.flush()
                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except Exception:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None
            return False

    def release(self) -> None:
        if self._fp is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                self._fp.close()
            finally:
                self._fp = None


# 单实例锁实例
_single_instance_lock: _SingleInstanceLock | None = None


@dataclass
class UpdaterRuntimeOptions:
    parent_pid: int | None = None
    parent_create_time: float | None = None
    shutdown_timeout: float = 180.0
    wait_poll_interval: float = 0.25
    mfw_exe_path: str | None = None


RUNTIME_OPTS = UpdaterRuntimeOptions()

FULL_UPDATE_EXCLUDES = [
    "config",
    "bundle",
    "backup",
    "hotfix",
    "release_notes",
    "debug",
    "update",
    "MFWUpdater1.exe",
    "MFWUpdater1",
]


def _norm_path(p: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(p))
    except Exception:
        return p


def _is_expected_parent_process(proc, *, expected_create_time: float | None) -> bool:
    """
    判断 pid 对应的进程是否仍然是“我们启动更新器时的那个主进程”。
    通过 create_time 防止 PID 复用导致误判。
    """
    if expected_create_time is None:
        return True
    try:
        actual = proc.create_time()
    except Exception:
        # 无法获取 create_time 时，宁可“认为是同一个”，由后续占用检查兜底
        return True
    # 允许极小的浮点误差
    return abs(actual - expected_create_time) < 1e-3


def wait_for_parent_exit(*, parent_pid: int | None, parent_create_time: float | None) -> None:
    """
    等待指定父进程退出（跨平台）。
    若检测到 PID 已被复用（create_time 不匹配），视为父进程已退出。
    """
    if not parent_pid or parent_pid <= 0:
        return

    import psutil

    update_logger.info(
        "[步骤0] 等待主程序退出: pid=%s create_time=%s timeout=%ss",
        parent_pid,
        parent_create_time,
        RUNTIME_OPTS.shutdown_timeout,
    )
    deadline = time.time() + float(RUNTIME_OPTS.shutdown_timeout)
    while True:
        try:
            proc = psutil.Process(parent_pid)
            if not _is_expected_parent_process(proc, expected_create_time=parent_create_time):
                update_logger.warning(
                    "[步骤0] 检测到 PID 可能已复用（create_time 不匹配），视为主程序已退出: pid=%s",
                    parent_pid,
                )
                return
            if not proc.is_running():
                return
            # 在退出过程中，可能短暂处于僵尸态（posix），这里继续等待即可
        except psutil.NoSuchProcess:
            return
        except psutil.AccessDenied:
            # 权限不足时降级为 pid_exists 轮询
            if not psutil.pid_exists(parent_pid):
                return
        except Exception as exc:
            update_logger.debug("[步骤0] 等待主程序退出时出现异常（将继续轮询）: %s", exc)

        if time.time() >= deadline:
            raise TimeoutError(
                f"等待主程序退出超时: pid={parent_pid} timeout={RUNTIME_OPTS.shutdown_timeout}s"
            )
        time.sleep(float(RUNTIME_OPTS.wait_poll_interval))


def _get_mfw_instance_key() -> str:
    """获取 MFW 主程序的实例键（用于单实例检测）
    
    更新器工作目录和主进程工作目录相同，直接使用主程序可执行文件路径。
    """
    # 优先使用传入的主程序路径
    mfw_exe = RUNTIME_OPTS.mfw_exe_path
    if mfw_exe:
        return os.path.abspath(mfw_exe)
    
    # 由于工作目录相同，直接使用当前目录下的主程序路径
    if sys.platform.startswith("win32"):
        default_exe = os.path.join(os.getcwd(), "MFW.exe")
    else:
        default_exe = os.path.join(os.getcwd(), "MFW")
    
    # 使用绝对路径作为实例键（与 main.py 保持一致）
    return os.path.abspath(default_exe)


def is_mfw_running() -> bool:
    """
    使用单实例锁检测 MFW 是否在运行。
    如果锁被占用，说明 MFW 正在运行。
    """
    global _single_instance_lock
    if _single_instance_lock is None:
        instance_key = _get_mfw_instance_key()
        _single_instance_lock = _SingleInstanceLock(instance_key)
    # 尝试获取锁，如果失败说明 MFW 正在运行
    return not _single_instance_lock.acquire()


def ensure_mfw_not_running():
    """
    确保MFW不在运行，如果正在运行则等待或退出
    使用单实例锁检测，替换原有的进程检测方式
    返回True表示可以继续，False表示应该退出
    """
    # 先等待“触发更新的那个主程序实例”完全退出
    try:
        wait_for_parent_exit(
            parent_pid=RUNTIME_OPTS.parent_pid,
            parent_create_time=RUNTIME_OPTS.parent_create_time,
        )
    except Exception as exc:
        update_logger.error("[步骤0] 等待主程序退出失败: %s", exc)
        # 继续走单实例锁检查逻辑，尽量自愈

    max_checks = max(3, int(RUNTIME_OPTS.shutdown_timeout // 5))
    check_count = 0
    update_logger.info(f"[步骤1] 检查MFW进程状态（使用单实例锁检测，最多检查{max_checks}次）...")
    print(f"[步骤1] 检查MFW进程状态（最多检查{max_checks}次）...")

    while check_count < max_checks:
        if not is_mfw_running():
            update_logger.info(f"[步骤1] MFW进程未运行，可以继续更新")
            print("[步骤1] MFW进程未运行，可以继续更新")
            return True
        check_count += 1
        update_logger.warning(
            f"[步骤1] MFW仍在运行（第{check_count}/{max_checks}次检查），5秒后重新检查..."
        )
        print(f"[步骤1] MFW仍在运行（第{check_count}/{max_checks}次检查），5秒后重新检查...")
        for sec in range(5, 0, -1):
            print(f"  {sec}秒后重新检查...")
            time.sleep(1)

    # 如果MFW仍在运行，记录错误并退出
    if is_mfw_running():
        error_message = f"更新失败：经过{max_checks}次检查后MFW仍在运行，无法继续更新"
        update_logger.error(f"[步骤1] {error_message}")
        update_logger.error(error_message)
        print(error_message)
        sys.exit(error_message)

    return True


def move_specific_files_to_temp_backup(update_file_path):
    """
    只将更新包中会覆盖的文件移动到临时备份目录

    Args:
        update_file_path: 更新包路径

    Returns:
        tuple: (临时目录路径, 移动成功的文件列表, 移动失败的文件列表)
    """
    current_dir = os.getcwd()
    import tempfile
    import zipfile

    temp_backup_dir = tempfile.mkdtemp(prefix="mfw_backup_")
    moved_files = []
    failed_files = []

    print(f"创建临时备份目录: {temp_backup_dir}")

    try:
        # 获取更新包中的文件列表
        with zipfile.ZipFile(update_file_path, "r") as zip_ref:
            update_files = zip_ref.namelist()

        # 只备份更新包中会覆盖的文件
        for file_info in update_files:
            file_path = os.path.join(current_dir, file_info)

            # 检查文件是否存在
            if os.path.exists(file_path):
                backup_path = os.path.join(temp_backup_dir, file_info)

                # 确保备份目录结构存在
                backup_dir = os.path.dirname(backup_path)
                if backup_dir and not os.path.exists(backup_dir):
                    os.makedirs(backup_dir)

                try:
                    # 移动文件到临时备份目录
                    shutil.move(file_path, backup_path)
                    moved_files.append(file_info)
                    print(f"已备份: {file_info}")
                except Exception as e:
                    failed_files.append((file_info, str(e)))
                    print(f"备份失败: {file_info} - {e}")

        return temp_backup_dir, moved_files, failed_files

    except Exception as e:
        print(f"备份过程出错: {e}")
        return temp_backup_dir, moved_files, failed_files


def restore_files_from_backup(backup_dir):
    """
    从备份目录恢复文件到当前目录

    Args:
        backup_dir: 备份目录路径
    """
    current_dir = os.getcwd()

    try:
        if os.path.exists(backup_dir):
            # 恢复备份文件
            for root, dirs, files in os.walk(backup_dir):
                # 计算相对路径
                rel_path = os.path.relpath(root, backup_dir)
                target_root = (
                    current_dir
                    if rel_path == "."
                    else os.path.join(current_dir, rel_path)
                )

                # 确保目标目录存在
                if not os.path.exists(target_root):
                    os.makedirs(target_root)

                # 恢复文件
                for file in files:
                    backup_path = os.path.join(root, file)
                    target_path = os.path.join(target_root, file)

                    # 如果目标文件已存在，先删除
                    if os.path.exists(target_path):
                        if os.path.isdir(target_path):
                            shutil.rmtree(target_path)
                        else:
                            os.remove(target_path)

                    # 移动文件回原位置
                    shutil.move(backup_path, target_path)
                    print(
                        f"已恢复: {os.path.join(rel_path, file) if rel_path != '.' else file}"
                    )

            # 删除备份目录
            shutil.rmtree(backup_dir)
            print("备份目录已清理")

    except Exception as e:
        print(f"恢复文件时出错: {e}")


def extract_zip_file_with_validation(update_file_path):
    """
    解压指定的压缩文件，使用循环逐个文件解压并验证

    Args:
        update_file_path: 要解压的压缩文件的路径

    Returns:
        bool: 解压是否成功
    """
    import zipfile

    # 检查MFW是否在运行
    if not ensure_mfw_not_running():
        return False

    if not update_file_path.lower().endswith(".zip"):
        update_logger.error(f"不支持的文件格式: {update_file_path}")
        return False

    extract_dir = Path(tempfile.mkdtemp(prefix="mfw_unpack_"))
    try:
        with zipfile.ZipFile(update_file_path, "r") as archive:
            file_list = archive.namelist()
            total_files = len(file_list)
            print(f"[解压] 找到 {total_files} 个文件需要解压")
            update_logger.info(f"[解压] 找到 {total_files} 个文件需要解压")
            
            extracted_count = 0
            for idx, file_info in enumerate(file_list, 1):
                try:
                    print(f"[解压] [{idx}/{total_files}] 正在解压: {file_info}")
                    archive.extract(file_info, extract_dir)
                    extracted_path = extract_dir / file_info
                    if not extracted_path.exists():
                        raise Exception(f"文件解压后不存在: {file_info}")
                    if sys.platform != "win32" and file_info in {"MFW", "MFWUpdater"}:
                        os.chmod(extracted_path, 0o755)
                    extracted_count += 1
                    print(f"[解压] ✓ 已解压 ({extracted_count}/{total_files}): {file_info}")
                except Exception as exc:
                    error_msg = f"提取 {file_info} 失败: {exc}"
                    print(f"[解压] ✗ 错误: {error_msg}")
                    print(f"[解压] 等待5秒后继续...")
                    for sec in range(5, 0, -1):
                        print(f"  {sec}秒后继续...")
                        time.sleep(1)
                    # 继续处理下一个文件
                    continue
            
            print(f"[解压] 解压完成，共成功解压 {extracted_count}/{total_files} 个文件")
            update_logger.info(f"[解压] 解压完成，共成功解压 {extracted_count}/{total_files} 个文件")
        
        print("[解压] 开始复制文件到目标目录...")
        _copy_temp_to_root(extract_dir, verbose=True)
        print("[解压] 文件复制完成")
        return True
    except Exception as exc:
        error_msg = f"解压过程出错: {exc}"
        print(f"[解压] ✗ 严重错误: {error_msg}")
        print(f"[解压] 等待5秒后继续...")
        for sec in range(5, 0, -1):
            print(f"  {sec}秒后继续...")
            time.sleep(1)
        update_logger.error(error_msg)
        cleanup_update_artifacts(update_file_path)
        start_mfw_process()
        return False
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def start_mfw_process():
    try:
        if sys.platform.startswith("win32"):
            cmd = [".\\MFW.exe"]
        elif sys.platform.startswith(("darwin", "linux")):
            cmd = ["./MFW"]
        else:
            update_logger.error("不支持的操作系统")
            return
        if DIRECT_RUN_EXTRA_ARGS:
            cmd.extend(DIRECT_RUN_EXTRA_ARGS)
        update_logger.info("重启/启动 MFW 进程: %s", " ".join(cmd))
        subprocess.Popen(cmd)
    except Exception as exc:
        update_logger.error(f"启动MFW程序失败: {exc}")


def cleanup_update_artifacts(update_file_path, metadata_path=None):
    target_files = [Path(update_file_path)]
    if metadata_path:
        target_files.append(Path(metadata_path))
    else:
        target_files.append(Path(update_file_path).parent / "update_metadata.json")

    update_logger.debug(f"[步骤6] 准备清理 {len(target_files)} 个更新文件...")
    cleaned_count = 0
    for path in target_files:
        try:
            if path.exists():
                path.unlink()
                update_logger.info(f"[步骤6] 已清理更新文件: {path}")
                cleaned_count += 1
            else:
                update_logger.debug(f"[步骤6] 文件不存在，跳过清理: {path}")
        except Exception as exc:
            update_logger.error(f"[步骤6] 清理更新文件失败: {path} -> {exc}")
            update_logger.error(f"清理更新 artifacts 失败: {path} -> {exc}")

    update_logger.debug(
        f"[步骤6] 更新文件清理完成，共清理 {cleaned_count}/{len(target_files)} 个文件"
    )


def ensure_update_directories():
    """
    确保 update/new_version 和 update/update_back 存在，并返回路径
    """
    update_root = os.path.join(os.getcwd(), "update")
    new_version_dir = os.path.join(update_root, "new_version")
    update_back_dir = os.path.join(update_root, "update_back")
    os.makedirs(new_version_dir, exist_ok=True)
    os.makedirs(update_back_dir, exist_ok=True)
    return new_version_dir, update_back_dir


def generate_metadata_samples(target_dir: str | Path | None = None):
    if target_dir is None:
        target_dir = os.path.join(os.getcwd(), "update", "new_version")
    os.makedirs(target_dir, exist_ok=True)
    combos = [
        ("github", "full"),
        ("github", "hotfix"),
        ("mirror", "full"),
        ("mirror", "hotfix"),
    ]
    for source, mode in combos:
        package_name = f"{source}_{mode}_{uuid4().hex[:8]}.zip"
        metadata = {
            "source": source,
            "mode": mode,
            "version": f"v{uuid4().hex[:6]}",
            "package_name": package_name,
            "download_time": datetime.utcnow().isoformat() + "Z",
            "attempts": random.randint(1, 3),
        }
        file_name = f"metadata_{uuid4().hex}.json"
        path = os.path.join(target_dir, file_name)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            print(f"生成元数据: {path}")
        except Exception as exc:
            update_logger.error(f"写入元数据失败: {exc}")


def setup_update_logger():
    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    log_path = debug_dir / "updater.log"
    updater_logger = logging.getLogger("updater")
    updater_logger.setLevel(logging.DEBUG)
    updater_logger.handlers.clear()
    rotating_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=3,
        encoding="utf-8",
    )
    rotating_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    rotating_handler.setFormatter(formatter)
    updater_logger.addHandler(rotating_handler)
    return updater_logger


update_logger = setup_update_logger()
if DIRECT_RUN_EXTRA_ARGS:
    update_logger.info(
        "启动参数检测到直接运行标志，即将向 MFW 透传: %s",
        DIRECT_RUN_EXTRA_ARGS,
    )


def load_update_metadata(update_dir):
    metadata_path = os.path.join(update_dir, "update_metadata.json")
    if not os.path.exists(metadata_path):
        return {}
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        update_logger.error(f"读取更新元数据失败: {exc}")
        return {}


def save_update_metadata(metadata_path: str, metadata: dict) -> None:
    try:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        update_logger.error(f"写入更新元数据失败: {exc}")


def read_file_list(file_list_path):
    entries = []
    if not os.path.exists(file_list_path):
        return entries
    try:
        with open(file_list_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                entries.append(line)
    except Exception as exc:
        update_logger.error(f"读取 file_list.txt 失败: {exc}")
    return entries


@dataclass
class SafeDeleteResult:
    success: bool
    backups: List[Tuple[str, str]]
    backup_dir: str | None


def _copy_to_backup(abs_path, backup_root, root):
    if not os.path.exists(abs_path):
        return None
    rel = os.path.relpath(abs_path, root)
    backup_path = os.path.join(backup_root, rel)
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    if os.path.isdir(abs_path):
        if os.path.exists(backup_path):
            shutil.rmtree(backup_path)
        shutil.copytree(abs_path, backup_path)
    else:
        shutil.copy2(abs_path, backup_path)
    return abs_path, backup_path


def _restore_from_backup(backups):
    for src, backup in reversed(backups):
        try:
            if not os.path.exists(backup):
                continue
            if os.path.isdir(backup):
                if os.path.exists(src):
                    shutil.rmtree(src)
                shutil.copytree(backup, src, dirs_exist_ok=False)
            else:
                os.makedirs(os.path.dirname(src), exist_ok=True)
                if os.path.exists(src):
                    os.remove(src)
                shutil.copy2(backup, src)
        except Exception as exc:
            update_logger.error(f"恢复 {src} 失败: {exc}")


def _is_under_any(abs_path: str, parents: set[str]) -> bool:
    """判断 abs_path 是否等于 parents 中任意路径，或位于其子路径下。"""
    for parent in parents:
        if not parent:
            continue
        if abs_path == parent or abs_path.startswith(parent + os.sep):
            return True
    return False


def _collect_root_entries_for_delete(
    root: str,
    *,
    keep_abs: set[str] | None = None,
    exclude_abs: set[str] | None = None,
    skip_abs: set[str] | None = None,
) -> list[str]:
    """
    从 root 的一级目录/文件中，筛选出需要删除的绝对路径列表。
    - keep_abs/exclude_abs: 作为“保留列表”，会保留其自身及其子路径
    - skip_abs: 仅跳过与某个一级 entry 完全相等的路径（与历史行为一致）
    """
    keep_abs = keep_abs or set()
    exclude_abs = exclude_abs or set()
    skip_abs = skip_abs or set()

    delete_candidates: list[str] = []
    for entry in os.listdir(root):
        abs_entry = os.path.abspath(os.path.join(root, entry))
        if abs_entry in skip_abs:
            continue
        if _is_under_any(abs_entry, keep_abs) or _is_under_any(abs_entry, exclude_abs):
            continue
        delete_candidates.append(abs_entry)
    return delete_candidates


def _safe_backup_then_delete(
    delete_candidates: list[str],
    *,
    root: str,
    cleanup_backup_on_success: bool,
) -> tuple[bool, list[tuple[str, str]], str | None]:
    """
    先备份 delete_candidates，再执行删除。失败则回滚。
    返回 (success, backups, backup_dir)。
    """
    backup_dir = tempfile.mkdtemp(prefix="mfw_delete_backup_")
    backups: list[tuple[str, str]] = []
    try:
        for abs_entry in delete_candidates:
            if not os.path.exists(abs_entry):
                continue
            backup_entry = _copy_to_backup(abs_entry, backup_dir, root)
            if backup_entry:
                backups.append(backup_entry)

        for abs_entry in delete_candidates:
            if not os.path.exists(abs_entry):
                continue
            if os.path.isdir(abs_entry):
                shutil.rmtree(abs_entry)
            else:
                os.remove(abs_entry)

        if cleanup_backup_on_success:
            shutil.rmtree(backup_dir, ignore_errors=True)
            return True, backups, None
        return True, backups, backup_dir
    except Exception as exc:
        update_logger.error(f"安全删除失败: {exc}")
        _restore_from_backup(backups)
        shutil.rmtree(backup_dir, ignore_errors=True)
        return False, [], None


def _cleanup_root_except(exclude_relatives):
    root = os.getcwd()
    exclude_abs = {
        os.path.abspath(os.path.join(root, rel)) for rel in exclude_relatives if rel
    }
    for entry in os.listdir(root):
        abs_entry = os.path.abspath(os.path.join(root, entry))
        if any(
            abs_entry == ex or abs_entry.startswith(ex + os.sep) for ex in exclude_abs
        ):
            continue
        if os.path.isdir(abs_entry):
            shutil.rmtree(abs_entry, ignore_errors=True)
        else:
            try:
                os.remove(abs_entry)
            except FileNotFoundError:
                pass


def safe_delete_all_except(exclude_relatives):
    root = os.getcwd()
    exclude_abs = {
        os.path.abspath(os.path.join(root, rel)) for rel in exclude_relatives if rel
    }
    delete_candidates = _collect_root_entries_for_delete(root, exclude_abs=exclude_abs)
    success, backups, backup_dir = _safe_backup_then_delete(
        delete_candidates,
        root=root,
        cleanup_backup_on_success=False,
    )
    if not success:
        return SafeDeleteResult(False, [], None)
    return SafeDeleteResult(True, backups, backup_dir)


def _extract_zip_to_temp(zip_path: Path):
    import zipfile

    temp_dir = Path(tempfile.mkdtemp(prefix="mfw_full_extract_"))
    try:
        with zipfile.ZipFile(zip_path, "r", metadata_encoding="utf-8") as archive:
            file_list = archive.namelist()
            total_files = len(file_list)
            print(f"[解压] 找到 {total_files} 个文件需要解压到临时目录")
            update_logger.info(f"[解压] 找到 {total_files} 个文件需要解压到临时目录")
            
            extracted_count = 0
            for idx, file_info in enumerate(file_list, 1):
                try:
                    print(f"[解压] [{idx}/{total_files}] 正在解压: {file_info}")
                    archive.extract(file_info, temp_dir)
                    extracted_count += 1
                    if extracted_count % 50 == 0 or extracted_count == total_files:
                        print(f"[解压] 已解压 {extracted_count}/{total_files} 个文件...")
                except Exception as exc:
                    error_msg = f"解压文件 {file_info} 失败: {exc}"
                    print(f"[解压] ✗ 错误: {error_msg}")
                    update_logger.error(f"[解压] {error_msg}")
                    print(f"[解压] 等待5秒后继续...")
                    for sec in range(5, 0, -1):
                        print(f"  {sec}秒后继续...")
                        time.sleep(1)
                    # 继续处理下一个文件
                    continue
            
            print(f"[解压] 解压完成，共成功解压 {extracted_count}/{total_files} 个文件")
            update_logger.info(f"[解压] 解压完成，共成功解压 {extracted_count}/{total_files} 个文件")
        return temp_dir
    except Exception as exc:
        error_msg = f"解压更新包到临时目录失败: {exc}"
        print(f"[解压] ✗ 严重错误: {error_msg}")
        update_logger.error(error_msg)
        print(f"[解压] 等待5秒后继续...")
        for sec in range(5, 0, -1):
            print(f"  {sec}秒后继续...")
            time.sleep(1)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None


def _copy_temp_to_root(temp_dir: Path, *, verbose: bool = False):
    current_dir = os.getcwd()
    for root_dir, dirs, files in os.walk(temp_dir):
        rel_root = os.path.relpath(root_dir, temp_dir)
        dest_root = (
            os.path.join(current_dir, rel_root)
            if rel_root not in (".", "")
            else current_dir
        )
        os.makedirs(dest_root, exist_ok=True)
        for d in dirs:
            os.makedirs(os.path.join(dest_root, d), exist_ok=True)
        for file in files:
            src_file = os.path.join(root_dir, file)
            dest_file = os.path.join(dest_root, file)
            os.makedirs(os.path.dirname(dest_file), exist_ok=True)
            shutil.copy2(src_file, dest_file)
            if sys.platform != "win32" and os.path.basename(dest_file) in {
                "MFW",
                "MFWUpdater",
            }:
                os.chmod(dest_file, 0o755)
            if verbose:
                print(f"✓ 已复制: {dest_file}")


def _increment_attempts(metadata: dict, metadata_path: str):
    metadata["attempts"] = metadata.get("attempts", 0) + 1
    try:
        save_update_metadata(metadata_path, metadata)
    except Exception as exc:
        update_logger.error(f"更新尝试次数记录失败: {exc}")


def _handle_full_update_failure(
    package_path: str,
    metadata_path: str,
    metadata: dict,
    backups: List[Tuple[str, str]] | None = None,
):
    if backups:
        _cleanup_root_except(FULL_UPDATE_EXCLUDES)
        _restore_from_backup(backups)
    _increment_attempts(metadata, metadata_path)
    start_mfw_process()


def perform_full_update(package_path: str, metadata_path: str, metadata: dict) -> bool:
    # 检查MFW是否在运行
    if not ensure_mfw_not_running():
        return False

    metadata = metadata or {}
    temp_dir = _extract_zip_to_temp(Path(package_path))
    if not temp_dir:
        _handle_full_update_failure(package_path, metadata_path, metadata)
        return False

    delete_result = safe_delete_all_except(FULL_UPDATE_EXCLUDES)
    if not delete_result.success:
        shutil.rmtree(temp_dir, ignore_errors=True)
        _handle_full_update_failure(package_path, metadata_path, metadata)
        return False

    try:
        _copy_temp_to_root(temp_dir)
    except Exception as exc:
        update_logger.error(f"覆盖目录失败: {exc}")
        _handle_full_update_failure(
            package_path, metadata_path, metadata, delete_result.backups
        )
        if delete_result.backup_dir:
            shutil.rmtree(delete_result.backup_dir, ignore_errors=True)
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if delete_result.backup_dir:
        shutil.rmtree(delete_result.backup_dir, ignore_errors=True)
    return True


def safe_delete_paths(relative_paths):
    root = os.getcwd()
    backup_dir = tempfile.mkdtemp(prefix="mfw_delete_backup_")
    backups = []
    try:
        for rel_path in relative_paths:
            abs_path = os.path.abspath(os.path.join(root, rel_path))
            if not abs_path.startswith(root) or not os.path.exists(abs_path):
                continue
            backup_entry = _copy_to_backup(abs_path, backup_dir, root)
            if backup_entry:
                backups.append(backup_entry)
            if os.path.isdir(abs_path):
                shutil.rmtree(abs_path)
            else:
                os.remove(abs_path)
        shutil.rmtree(backup_dir, ignore_errors=True)
        return True
    except Exception as exc:
        update_logger.error(f"删除失败: {exc}")
        _restore_from_backup(backups)
        shutil.rmtree(backup_dir, ignore_errors=True)
        return False


def safe_delete_except(keep_relative_paths, skip_paths=None, extra_keep=None):
    root = os.getcwd()
    keep_abs = set()
    for rel_path in keep_relative_paths:
        abs_path = os.path.abspath(os.path.join(root, rel_path))
        keep_abs.add(abs_path)
        dirname = os.path.abspath(os.path.dirname(abs_path))
        if dirname and dirname != root:
            keep_abs.add(dirname)
    for rel_path in extra_keep or []:
        abs_path = os.path.abspath(os.path.join(root, rel_path))
        keep_abs.add(abs_path)
        if os.path.isdir(abs_path):
            keep_abs.add(os.path.abspath(abs_path))
    skip_abs = {os.path.abspath(path) for path in (skip_paths or [])}
    delete_candidates = _collect_root_entries_for_delete(
        root,
        keep_abs=keep_abs,
        skip_abs=skip_abs,
    )
    success, _, _ = _safe_backup_then_delete(
        delete_candidates,
        root=root,
        cleanup_backup_on_success=True,
    )
    return success


def backup_model_dir():
    repo_root = os.getcwd()
    model_path = os.path.join(repo_root, "model")
    if not os.path.isdir(model_path):
        return None
    backup_root = tempfile.mkdtemp(prefix="mfw_model_backup_")
    backup_model = os.path.join(backup_root, "model")
    try:
        shutil.copytree(model_path, backup_model)
        return backup_root
    except Exception as exc:
        update_logger.error(f"备份 model 目录失败: {exc}")
        shutil.rmtree(backup_root, ignore_errors=True)
        return None


def restore_model_dir(backup_root):
    if not backup_root or not os.path.isdir(backup_root):
        return
    backup_model = os.path.join(backup_root, "model")
    if not os.path.isdir(backup_model):
        shutil.rmtree(backup_root, ignore_errors=True)
        return
    target = os.path.join(os.getcwd(), "model")
    if os.path.exists(target):
        shutil.rmtree(target)
    try:
        shutil.copytree(backup_model, target)
    except Exception as exc:
        update_logger.error(f"恢复 model 目录失败: {exc}")
    finally:
        shutil.rmtree(backup_root, ignore_errors=True)


def extract_interface_folder(zip_path):
    import zipfile

    repo_root = os.getcwd()
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            interface_member = next(
                (
                    name
                    for name in zf.namelist()
                    if os.path.basename(name).lower()
                    in {"interface.json", "interface.jsonc"}
                ),
                None,
            )
            if not interface_member:
                update_logger.error("未在更新包中找到 interface.json/ interface.jsonc")
                return False

            interface_dir = os.path.dirname(interface_member)
            prefix = f"{interface_dir.rstrip('/')}/" if interface_dir else ""

            members_to_extract = [m for m in zf.namelist() if (not prefix or m.startswith(prefix)) and (m[len(prefix):] if prefix else m).strip()]
            total_files = len(members_to_extract)
            print(f"[解压] 找到 {total_files} 个文件需要解压 interface 文件夹")
            update_logger.info(f"[解压] 找到 {total_files} 个文件需要解压 interface 文件夹")
            
            extracted_count = 0
            for idx, member in enumerate(members_to_extract, 1):
                try:
                    if prefix and not member.startswith(prefix):
                        continue
                    relative_path = member[len(prefix) :] if prefix else member
                    if not relative_path:
                        continue
                    print(f"[解压] [{idx}/{total_files}] 正在解压: {relative_path}")
                    target_path = os.path.join(repo_root, relative_path)
                    if member.endswith("/"):
                        os.makedirs(target_path, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zf.open(member) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                    if sys.platform != "win32" and relative_path in {"MFW", "MFWUpdater"}:
                        os.chmod(target_path, 0o755)
                    extracted_count += 1
                    if extracted_count % 10 == 0 or extracted_count == total_files:
                        print(f"[解压] 已解压 {extracted_count}/{total_files} 个文件...")
                except Exception as exc:
                    error_msg = f"解压文件 {member} 失败: {exc}"
                    print(f"[解压] ✗ 错误: {error_msg}")
                    update_logger.error(f"[解压] {error_msg}")
                    print(f"[解压] 等待5秒后继续...")
                    for sec in range(5, 0, -1):
                        print(f"  {sec}秒后继续...")
                        time.sleep(1)
                    # 继续处理下一个文件
                    continue
            
            print(f"[解压] interface 文件夹解压完成，共成功解压 {extracted_count}/{total_files} 个文件")
            update_logger.info(f"[解压] interface 文件夹解压完成，共成功解压 {extracted_count}/{total_files} 个文件")
        return True
    except Exception as exc:
        error_msg = f"解压 interface 文件夹失败: {exc}"
        print(f"[解压] ✗ 严重错误: {error_msg}")
        update_logger.error(error_msg)
        print(f"[解压] 等待5秒后继续...")
        for sec in range(5, 0, -1):
            print(f"  {sec}秒后继续...")
            time.sleep(1)
        return False


def load_change_entries(zip_path):
    import zipfile

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            candidate = next(
                (
                    name
                    for name in zf.namelist()
                    if os.path.basename(name).lower() in {"change.json", "changes.json"}
                ),
                None,
            )
            if not candidate:
                update_logger.error("更新包中未包含 change.json/changes.json")
                return None
            with zf.open(candidate) as change_file:
                data = json.load(change_file)
                deleted = data.get("deleted", [])
                modified = data.get("modified", [])
                entries: list[str] = []
                if isinstance(deleted, list):
                    entries.extend(deleted)
                if isinstance(modified, list):
                    entries.extend(modified)
                return entries
    except Exception as exc:
        update_logger.error(f"读取 change.json 失败: {exc}")
        return None


def _get_bundle_path_from_metadata(metadata: dict) -> str | None:
    """
    从 metadata 中获取 bundle 路径。
    尝试从常见位置查找 bundle 路径。
    """
    update_logger.debug(
        "[步骤4] 开始从 metadata 和 interface 配置中获取 bundle 路径..."
    )
    # 尝试从 interface.json 中获取
    repo_root = os.getcwd()
    interface_paths = [
        os.path.join(repo_root, "interface.json"),
        os.path.join(repo_root, "interface.jsonc"),
    ]
    for interface_path in interface_paths:
        if os.path.exists(interface_path):
            update_logger.debug(f"[步骤4] 找到 interface 文件: {interface_path}")
            try:
                with open(interface_path, "r", encoding="utf-8") as f:
                    interface_data = json.load(f)
                    # 假设 bundle 路径在当前目录或 bundle 目录下
                    bundle_name = interface_data.get("name", "")
                    if bundle_name:
                        update_logger.debug(
                            f"[步骤4] 从 interface 配置中获取到 bundle 名称: {bundle_name}"
                        )
                        bundle_paths = [
                            os.path.join(repo_root, "bundle", bundle_name),
                            os.path.join(repo_root, bundle_name),
                        ]
                        for bp in bundle_paths:
                            if os.path.exists(bp):
                                update_logger.debug(f"[步骤4] 找到 bundle 路径: {bp}")
                                return bp
                        update_logger.warning(
                            f"[步骤4] bundle 名称存在但路径不存在: {bundle_paths}"
                        )
                    else:
                        update_logger.warning(
                            f"[步骤4] interface 配置中未找到 bundle 名称 (name 字段)"
                        )
            except Exception as exc:
                update_logger.warning(
                    f"[步骤4] 读取 interface 文件失败: {interface_path} -> {exc}"
                )
                pass
    update_logger.warning("[步骤4] 未找到有效的 interface 配置文件或 bundle 路径")
    return None


def _read_config_file(config_path: str) -> dict:
    """
    读取指定路径的JSON/JSONC配置文件。
    基于 app/utils/update.py 中的实现。
    """
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            # 尝试使用 jsonc，如果不可用则使用 json
            try:
                import jsonc

                return jsonc.load(f)
            except ImportError:
                return json.load(f)
    except Exception as exc:
        update_logger.error(f"读取配置文件失败 {config_path}: {exc}")
        return {}


def _extract_zip_to_hotfix_dir(zip_path: str, extract_to: str) -> str | None:
    """
    解压 zip 文件到指定目录，自动查找 interface.json 并返回解压后的根目录。
    基于 app/utils/update.py 中的 extract_zip 实现。

    Args:
        zip_path: zip 文件路径
        extract_to: 解压目标目录

    Returns:
        str | None: 解压后的根目录路径，如果失败返回 None
    """
    import zipfile

    update_logger.info(f"[步骤3] 开始解压更新包: {zip_path} -> {extract_to}")
    extract_to_path = Path(extract_to)
    extract_to_path.mkdir(parents=True, exist_ok=True)
    update_logger.debug(f"[步骤3] 创建/确认解压目标目录: {extract_to_path}")

    interface_names = {"interface.json", "interface.jsonc"}

    try:
        with zipfile.ZipFile(zip_path, "r", metadata_encoding="utf-8") as archive:
            members = archive.namelist()
            total_files = len(members)
            update_logger.info(
                f"[步骤3] 打开更新包成功，包含 {total_files} 个文件/目录"
            )

            # 查找 interface.json 或 interface.jsonc
            update_logger.debug("[步骤3] 查找 interface.json/interface.jsonc 文件...")
            interface_dir_parts: tuple[str, ...] | None = None
            for member in members:
                member_path = Path(member.replace("\\", "/"))
                if member_path.name.lower() in interface_names:
                    interface_dir_parts = tuple(member_path.parent.parts)
                    update_logger.info(
                        f"[步骤3] 找到 interface 文件: {member}，所在目录: {'/'.join(interface_dir_parts)}"
                    )
                    break

            if not interface_dir_parts:
                update_logger.warning(
                    "[步骤3] 未在更新包中找到 interface.json/interface.jsonc 文件，将解压所有文件"
                )

            # 解压文件
            update_logger.info("[步骤3] 开始解压文件...")
            print("[解压] 开始解压文件...")
            extracted_count = 0
            total_to_extract = len([m for m in members if (not interface_dir_parts or tuple(Path(m.replace("\\", "/")).parts[:len(interface_dir_parts) if interface_dir_parts else 0]) == interface_dir_parts) and m.strip()])
            
            for idx, member in enumerate(members, 1):
                member_path = Path(member.replace("\\", "/"))
                member_parts = tuple(p for p in member_path.parts if p and p != ".")

                # 如果找到了 interface 目录，只解压该目录下的文件
                if interface_dir_parts:
                    if member_parts[: len(interface_dir_parts)] != interface_dir_parts:
                        continue
                    # 移除 interface 目录前缀
                    relative_parts = member_parts[len(interface_dir_parts) :]
                else:
                    relative_parts = member_parts

                if not relative_parts:
                    continue

                try:
                    target_path = extract_to_path.joinpath(*relative_parts)
                    if member.endswith("/"):
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        print(f"[解压] [{extracted_count + 1}/{total_to_extract}] 正在解压: {member}")
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, open(
                            target_path, "wb"
                        ) as target:
                            shutil.copyfileobj(source, target)
                        extracted_count += 1
                        if extracted_count % 10 == 0 or extracted_count == total_to_extract:
                            print(f"[解压] 已解压 {extracted_count}/{total_to_extract} 个文件...")
                except Exception as exc:
                    error_msg = f"解压文件 {member} 失败: {exc}"
                    print(f"[解压] ✗ 错误: {error_msg}")
                    update_logger.error(f"[步骤3] {error_msg}")
                    print(f"[解压] 等待5秒后继续...")
                    for sec in range(5, 0, -1):
                        print(f"  {sec}秒后继续...")
                        time.sleep(1)
                    # 继续处理下一个文件
                    continue

            update_logger.info(f"[步骤3] 文件解压完成，共解压 {extracted_count} 个文件")
            print(f"[解压] 文件解压完成，共解压 {extracted_count} 个文件")

            # 返回解压后的根目录
            return str(extract_to_path)
    except Exception as exc:
        error_msg = f"解压文件失败 {zip_path} -> {extract_to}: {exc}"
        print(f"[解压] ✗ 严重错误: {error_msg}")
        update_logger.exception(f"[步骤3] {error_msg}")
        print(f"[解压] 等待5秒后继续...")
        for sec in range(5, 0, -1):
            print(f"  {sec}秒后继续...")
            time.sleep(1)
        return None


def _load_interface_data(bundle_path: Path) -> tuple[list[Path], dict]:
    """读取 bundle 下的 interface.jsonc/interface.json，返回 (候选路径列表, 解析后的数据)。"""
    interface_paths = [bundle_path / "interface.jsonc", bundle_path / "interface.json"]
    for path in interface_paths:
        if not path.exists():
            continue
        update_logger.info(f"[步骤5] 找到 interface 文件: {path}")
        interface_data = _read_config_file(str(path))
        if interface_data:
            update_logger.info("[步骤5] 成功读取 interface 配置")
            return interface_paths, interface_data
    update_logger.warning("[步骤5] 未找到有效的 interface 配置文件")
    return interface_paths, {}


def _get_resource_dirs_from_interface(interface_data: dict) -> list[Path]:
    """从 interface 配置解析 resource.path，按原始 path 去重，仅保留存在的目录。"""
    resource_list = interface_data.get("resource", [])
    if not isinstance(resource_list, list):
        return []
    seen: set[str] = set()
    dirs: list[Path] = []
    for resource in resource_list:
        if not isinstance(resource, dict):
            continue
        raw_paths = resource.get("path", [])
        if not isinstance(raw_paths, list):
            continue
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or raw_path in seen:
                continue
            seen.add(raw_path)
            resolved = Path(raw_path.replace("{PROJECT_DIR}", "."))
            if resolved.is_dir():
                dirs.append(resolved)
    return dirs


def _backup_resources_and_cleanup_pipelines(
    resource_dirs: list[Path],
    backup_root: Path,
) -> list[tuple[Path, Path]]:
    """
    备份资源目录到 backup_root，并删除每个资源目录下的 pipeline 目录（若存在）。
    返回 (original_path, backup_path) 列表，用于失败回滚。
    """
    backup_root.mkdir(parents=True, exist_ok=True)
    backups: list[tuple[Path, Path]] = []
    for resource_path in resource_dirs:
        backup_target = backup_root / resource_path.name
        update_logger.info(f"[步骤5] 备份资源目录: {resource_path} -> {backup_target}")
        if backup_target.is_dir():
            shutil.rmtree(backup_target)
        shutil.copytree(str(resource_path), str(backup_target))
        update_logger.info(f"[步骤5] 资源目录备份完成: {resource_path}")
        backups.append((resource_path, backup_target))

        pipeline_path = resource_path / "pipeline"
        if pipeline_path.exists():
            update_logger.info(f"[步骤5] 删除旧 pipeline 目录: {pipeline_path}")
            shutil.rmtree(str(pipeline_path))
            update_logger.info(f"[步骤5] pipeline 目录删除完成: {pipeline_path}")
    return backups


def _update_interface_version(interface_paths: list[Path], version: str) -> bool:
    update_logger.info(f"[步骤5] 开始更新 interface 配置文件中的版本号为: {version}")
    for path in interface_paths:
        if not path.exists():
            continue
        interface = _read_config_file(str(path))
        if not interface:
            continue
        old_version = interface.get("version", "unknown")
        interface["version"] = version
        try:
            import jsonc

            with open(path, "w", encoding="utf-8") as f:
                jsonc.dump(interface, f, indent=4, ensure_ascii=False)
        except ImportError:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(interface, f, indent=4, ensure_ascii=False)
        update_logger.info(
            f"[步骤5] 版本号更新成功: {path.name} ({old_version} -> {version})"
        )
        return True
    update_logger.warning("[步骤5] 未能更新 interface 配置文件中的版本号")
    return False


def _rollback_resource_backups(resource_backups: list[tuple[Path, Path]]) -> None:
    if not resource_backups:
        update_logger.warning("[步骤5] 没有需要恢复的资源备份")
        return
    update_logger.warning(
        f"[步骤5] 更新失败，正在恢复 {len(resource_backups)} 个资源备份目录..."
    )
    restore_count = 0
    for original_path, backup_path in reversed(resource_backups):
        try:
            if not backup_path.exists():
                update_logger.warning(f"[步骤5] 备份路径不存在，跳过恢复: {backup_path}")
                continue
            update_logger.info(f"[步骤5] 恢复资源目录: {backup_path} -> {original_path}")
            if original_path.exists():
                if original_path.is_file():
                    original_path.unlink()
                else:
                    shutil.rmtree(original_path)
            if backup_path.is_dir():
                shutil.copytree(backup_path, original_path)
            else:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, original_path)
            update_logger.info(f"[步骤5] 资源目录恢复成功: {original_path}")
            restore_count += 1
        except Exception as restore_err:
            update_logger.exception(
                f"[步骤5] 恢复资源目录失败: {original_path} -> {restore_err}"
            )
    update_logger.info(
        f"[步骤5] 资源备份恢复完成，成功恢复 {restore_count}/{len(resource_backups)} 个目录"
    )


def apply_github_hotfix(package_path, metadata=None):
    """
    应用 GitHub 热更新。
    基于 app/utils/update.py:1339-1412 的逻辑实现。
    更新器只负责读取元数据并应用更新，不进行下载或远程检查。
    主程序已经下载了更新包并判断过可以热更新，更新器只需要应用即可。

    Args:
        package_path: 更新包路径（从元数据中获取）
        metadata: 更新元数据，包含 source、mode、version 等信息

    Returns:
        bool: 更新是否成功
    """
    update_logger.info("=" * 50)
    update_logger.info("[GitHub热更新] 开始执行热更新流程")
    update_logger.info(f"[GitHub热更新] 更新包路径: {package_path}")

    # 检查MFW是否在运行
    update_logger.info("[步骤1] 检查MFW进程状态...")
    if not ensure_mfw_not_running():
        update_logger.error("[步骤1] MFW进程检查失败，无法继续更新")
        return False
    update_logger.info("[步骤1] MFW进程检查通过，可以继续更新")

    # 验证元数据
    update_logger.info("[步骤2] 验证更新元数据...")
    if not metadata:
        update_logger.warning("[步骤2] 缺少更新元数据，无法执行热更新")
        return False

    version = metadata.get("version", "")
    package_name = metadata.get("package_name", "unknown")
    update_logger.info(
        f"[步骤2] 元数据验证通过 - 版本: {version}, 包名: {package_name}"
    )

    update_logger.info(f"[步骤3] 准备解压更新包: 版本={version}, 包名={package_name}")

    # 步骤3: 解压更新包到 hotfix 目录
    hotfix_dir = os.path.join(os.getcwd(), "hotfix")
    update_logger.info(f"[步骤3] 准备解压到 hotfix 目录: {hotfix_dir}")
    hotfix_root = _extract_zip_to_hotfix_dir(package_path, hotfix_dir)
    if not hotfix_root:
        update_logger.error("[步骤3] 解压更新包失败")
        return False
    update_logger.info(f"[步骤3] 更新包解压完成，根目录: {hotfix_root}")

    # 步骤4: 获取 bundle 路径
    update_logger.info("[步骤4] 获取 Bundle 路径...")
    bundle_path = _get_bundle_path_from_metadata(metadata)
    if not bundle_path:
        update_logger.warning("[步骤4] Bundle 配置不存在，跳过热更新")
        return False
    bundle_path_obj = Path(bundle_path)
    update_logger.info(f"[步骤4] Bundle 路径获取成功: {bundle_path_obj}")

    # 步骤5: 使用安全覆盖模式进行热更新
    update_logger.info("[步骤5] 使用安全覆盖模式进行热更新")
    project_path = bundle_path_obj
    if not os.path.exists(hotfix_root):
        update_logger.error("[步骤5] hotfix 目录不存在，无法覆盖")
        return False
    update_logger.info(f"[步骤5] 验证 hotfix 目录存在: {hotfix_root}")

    # 备份并删除资源文件中的 pipeline 目录，以供后续无损覆盖
    resource_backup_dir = Path.cwd() / "backup" / "resource"
    update_logger.info(f"[步骤5] 创建资源备份目录: {resource_backup_dir}")
    resource_backup_dir.mkdir(parents=True, exist_ok=True)

    update_logger.info("[步骤5] 读取 interface 配置文件...")
    interface_paths, interface_data = _load_interface_data(bundle_path_obj)
    resource_dirs = _get_resource_dirs_from_interface(interface_data)
    update_logger.info(f"[步骤5] 获取到 {len(resource_dirs)} 个资源目录")
    resource_backups: list[tuple[Path, Path]] = []

    try:
        update_logger.info("[步骤5] 开始备份资源目录和清理 pipeline 目录...")
        resource_backups = _backup_resources_and_cleanup_pipelines(
            resource_dirs, resource_backup_dir
        )
        update_logger.info(
            f"[步骤5] 资源备份和清理完成，共处理 {len(resource_backups)} 个资源目录"
        )

        update_logger.info(f"[步骤5] 开始覆盖项目目录: {hotfix_root} -> {project_path}")
        # 允许目标目录已存在（Python 3.8+ 支持 dirs_exist_ok）
        # 这样在 bundle 目录本身已存在时不会因 WinError 183 直接失败
        shutil.copytree(hotfix_root, project_path, dirs_exist_ok=True)
        update_logger.info(f"[步骤5] 项目目录覆盖完成: {project_path}")

        if _update_interface_version(interface_paths, version):
            update_logger.info("[步骤5] interface 配置同步完毕")

        # 步骤5: 完成
        update_logger.info("[步骤5] 热更新文件操作成功完成!")
        update_logger.info("=" * 50)

        # 步骤6: 清理更新数据
        update_logger.info("[步骤6] 开始清理更新数据...")
        download_dir = Path(package_path).parent
        metadata_file = str(download_dir / "update_metadata.json")
        update_logger.info(
            f"[步骤6] 准备清理: 更新包={package_path}, 元数据={metadata_file}"
        )
        cleanup_update_artifacts(package_path, metadata_file)
        update_logger.info("[步骤6] 更新数据清理完成")
        update_logger.info("=" * 50)
        update_logger.info("[GitHub热更新] 热更新流程全部完成!")
        update_logger.info("=" * 50)

        return True

    except Exception as e:
        # 资源目录异常回滚
        update_logger.error(f"[步骤5] 热更新过程中出现错误: {e}")
        _rollback_resource_backups(resource_backups)
        update_logger.exception("[GitHub热更新] 热更新失败，详细信息:")
        update_logger.error("=" * 50)
        return False
    finally:
        # 清理资源备份目录
        if resource_backup_dir.exists():
            try:
                update_logger.info(f"[步骤5] 清理资源备份目录: {resource_backup_dir}")
                shutil.rmtree(resource_backup_dir)
                update_logger.info("[步骤5] 资源备份目录清理完成")
            except Exception as cleanup_err:
                update_logger.warning(f"[步骤5] 清理资源备份目录失败: {cleanup_err}")


def apply_mirror_hotfix(package_path):
    # 检查MFW是否在运行
    if not ensure_mfw_not_running():
        return False

    deletes = load_change_entries(package_path)
    if deletes is None:
        return False
    if not safe_delete_paths(deletes):
        update_logger.error("执行镜像热更新的安全删除阶段失败")
        return False
    return extract_zip_file_with_validation(package_path)


def find_latest_zip_file(directory):
    """
    查找目录中最新的 zip 包
    """
    try:
        candidates = [
            os.path.join(directory, file_name)
            for file_name in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, file_name))
            and file_name.lower().endswith(".zip")
        ]
        if not candidates:
            return None
        return max(candidates, key=os.path.getmtime)
    except FileNotFoundError:
        return None
    except Exception as e:
        update_logger.error(f"查找更新包时出错: {e}")
        return None


def move_update_archive_to_backup(src_path, backup_dir, metadata_path=None):
    """
    将更新包移动到备份目录，避免名称冲突
    """
    base_name = os.path.basename(src_path)
    dest_path = os.path.join(backup_dir, base_name)
    if os.path.exists(dest_path):
        name, ext = os.path.splitext(base_name)
        dest_path = os.path.join(
            backup_dir,
            f"{name}_{time.strftime('%Y%m%d%H%M%S')}{ext}",
        )
    try:
        shutil.move(src_path, dest_path)
        print(f"更新包已移入备份目录: {dest_path}")
        if metadata_path and os.path.exists(metadata_path):
            metadata_dest = os.path.join(backup_dir, f"{base_name}.metadata.json")
            shutil.move(metadata_path, metadata_dest)
            print(f"更新元数据已随包移动: {metadata_dest}")
    except Exception as e:
        update_logger.error(f"移动更新包到备份目录失败: {e}")


def standard_update():
    """
    标准更新模式
    """
    update_logger.info("标准更新模式开始")
    # 检查MFW是否在运行
    if not ensure_mfw_not_running():
        return

    new_version_dir, update_back_dir = ensure_update_directories()
    update_logger.debug(
        "更新目录准备完毕: new_version_dir=%s, update_back_dir=%s",
        new_version_dir,
        update_back_dir,
    )
    metadata_path = os.path.join(new_version_dir, "update_metadata.json")
    metadata = load_update_metadata(new_version_dir)
    update_logger.debug(
        "读取元数据完成: metadata_path=%s, metadata=%s",
        metadata_path,
        metadata,
    )
    if metadata:
        metadata["attempts"] = metadata.get("attempts", 0) + 1
        save_update_metadata(metadata_path, metadata)
    file_list_path = os.path.join(os.getcwd(), "file_list.txt")
    file_list = read_file_list(file_list_path)
    update_logger.info(
        "读取更新信息：metadata=%s, file_list=%s",
        metadata,
        file_list,
    )

    package_name = metadata.get("package_name") if metadata else None
    package_path = os.path.join(new_version_dir, package_name) if package_name else None
    if not package_path or not os.path.isfile(package_path):
        package_path = find_latest_zip_file(new_version_dir)
    update_logger.debug("选定的更新包路径: %s", package_path)

    if not package_path:
        print("未找到更新文件，清理元数据并启动MFW")
        update_logger.warning("未找到有效更新包，尝试清理元数据并重启 MFW")
        # 删除元数据文件
        if os.path.exists(metadata_path):
            try:
                os.remove(metadata_path)
                update_logger.info("已删除元数据文件: %s", metadata_path)
            except Exception as exc:
                update_logger.error(f"删除元数据文件失败: {exc}")
        # 启动MFW程序
        start_mfw_process()
        # 自身退出
        sys.exit(0)

    attempts = metadata.get("attempts", 0) if metadata else 0
    if metadata and attempts > 3:
        update_logger.warning("更新尝试次数已大于3次，清理更新包与元数据")
        if package_path and os.path.exists(package_path):
            os.remove(package_path)
        if os.path.exists(metadata_path):
            os.remove(metadata_path)
        sys.exit("更新尝试次数超过限制，已清理旧更新包")

    source = metadata.get("source", "unknown") if metadata else "unknown"
    mode = metadata.get("mode", "full") if metadata else "full"
    version = metadata.get("version", "")
    update_logger.info(
        "检测到更新包: name=%s source=%s mode=%s version=%s",
        os.path.basename(package_path),
        source,
        mode,
        version,
    )
    print(
        f"检测到更新包: {os.path.basename(package_path)} "
        f"来源: {source} 模式: {mode} 版本: {version}"
    )

    success = False
    if metadata:
        if source == "github":
            if mode == "full":
                success = perform_full_update(package_path, metadata_path, metadata)
            else:
                success = apply_github_hotfix(package_path, metadata)
        elif source == "mirror":
            if mode == "full":
                success = perform_full_update(package_path, metadata_path, metadata)
            else:
                success = apply_mirror_hotfix(package_path)
        else:
            success = extract_zip_file_with_validation(package_path)
    else:
        success = extract_zip_file_with_validation(package_path)

    if success:
        update_logger.info("更新文件处理成功，准备备份包并重启主程序")
        print("更新文件处理完成")
        metadata_path = os.path.join(new_version_dir, "update_metadata.json")
        move_update_archive_to_backup(
            package_path, update_back_dir, metadata_path=metadata_path
        )
        update_logger.info(
            "更新包已移动到备份目录，并将元数据一并转移: package=%s",
            package_path,
        )
    else:
        update_logger.error("更新文件处理失败")
        error_message = "更新文件处理失败"
        update_logger.error(error_message)
        sys.exit(error_message)

    # 重启程序
    print("重启MFW程序...")
    start_mfw_process()


def recovery_mode():
    """
    恢复模式
    """
    update_logger.info("恢复模式开始")
    # 检查MFW是否在运行
    if not ensure_mfw_not_running():
        return

    input("按回车键开始恢复更新...")

    _, update_back_dir = ensure_update_directories()
    update_file = find_latest_zip_file(update_back_dir)

    update_logger.info("恢复模式开始, update_file=%s", update_file)

    if update_file:
        if extract_zip_file_with_validation(update_file):
            update_logger.info("恢复更新包执行成功，准备重启")
            print("恢复更新成功")
        else:
            error_message = "恢复更新失败"
            update_logger.error(error_message)
            sys.exit(error_message)
    else:
        print("未找到恢复更新文件")
        update_logger.warning("恢复更新失败：没有可用的备份压缩包")

    # 重启程序
    start_mfw_process()
    print("程序已重启")


if __name__ == "__main__":
    # 不再启动时删除旧日志，而是追加写入（便于排查历史错误）
    # 在日志中记录本次更新开始
    import time

    separator = "=" * 60
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    update_logger.info("\n%s\n[%s] 更新程序启动\n%s", separator, timestamp, separator)
    update_logger.info("更新程序启动, argv=%s", sys.argv)

    try:
        if len(sys.argv) > 1:
            if sys.argv[1] == "-update":
                # 解析 -update 后的可选参数（保持兼容：无参数也能运行）
                import argparse

                parser = argparse.ArgumentParser(add_help=False)
                parser.add_argument(
                    "--parent-pid",
                    type=int,
                    default=None,
                    help="触发更新器的主程序PID（用于精确等待退出）",
                )
                parser.add_argument(
                    "--parent-create-time",
                    type=float,
                    default=None,
                    help="主程序进程创建时间（防 PID 复用误判）",
                )
                parser.add_argument(
                    "--shutdown-timeout",
                    type=float,
                    default=180.0,
                    help="等待主程序/占用进程退出的超时时间（秒）",
                )
                parser.add_argument(
                    "--wait-poll",
                    type=float,
                    default=0.25,
                    help="等待轮询间隔（秒）",
                )
                parser.add_argument(
                    "--mfw-exe-path",
                    type=str,
                    default=None,
                    help="主程序可执行文件路径（更可靠的占用检测）",
                )
                # 兼容透传给主程序的 direct-run 标志（更新器自身不消费，但不能因未知参数失败）
                parser.add_argument("-d", "--direct-run", action="store_true")

                known, _unknown = parser.parse_known_args(sys.argv[2:])
                RUNTIME_OPTS.parent_pid = known.parent_pid
                RUNTIME_OPTS.parent_create_time = known.parent_create_time
                RUNTIME_OPTS.shutdown_timeout = float(known.shutdown_timeout)
                RUNTIME_OPTS.wait_poll_interval = float(known.wait_poll)
                RUNTIME_OPTS.mfw_exe_path = known.mfw_exe_path

                standard_update()
            elif sys.argv[1] == "-generate-metadata":
                target = sys.argv[2] if len(sys.argv) > 2 else None
                generate_metadata_samples(target)
            else:
                mode = input(
                    "1. 更新模式 / Standard update\n2. 恢复模式 / Recovery update\n"
                )
                if mode == "1":
                    standard_update()
                elif mode == "2":
                    recovery_mode()
                else:
                    print("无效输入 / Invalid input")
                    input("按回车键继续... / Press Enter to continue...")
        else:
            mode = input(
                "1. 更新模式 / Standard update\n2. 恢复模式 / Recovery update\n"
            )
            if mode == "1":
                standard_update()
            elif mode == "2":
                recovery_mode()
            else:
                print("无效输入 / Invalid input")
                input("按回车键继续... / Press Enter to continue...")
    except Exception as e:
        # 捕获所有未处理的异常并记录
        error_message = f"更新程序发生未捕获的异常: {type(e).__name__}: {e}"
        update_logger.error(error_message)
        print(f"\n{error_message}")

        input("按回车键退出... / Press Enter to exit...")
        sys.exit(1)
