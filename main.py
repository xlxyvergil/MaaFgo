#   This file is part of MFW-ChainFlow Assistant.

#   MFW-ChainFlow Assistant is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published
#   by the Free Software Foundation, either version 3 of the License,
#   or (at your option) any later version.

#   MFW-ChainFlow Assistant is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
#   the GNU General Public License for more details.

#   You should have received a copy of the GNU General Public License
#   along with MFW-ChainFlow Assistant. If not, see <https://www.gnu.org/licenses/>.

#   Contact: err.overflow@gmail.com
#   Copyright (C) 2024-2025  MFW-ChainFlow Assistant. All rights reserved.

"""
MFW-ChainFlow Assistant
MFW-ChainFlow Assistant 启动文件
作者:overflow65537
"""

import os
import sys
import argparse
import atexit
import hashlib
import tempfile


# 设置工作目录为运行方式位置
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
    os.environ["MAAFW_BINARY_PATH"] = os.getcwd()
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))


from app.utils.logger import logger
from qasync import QEventLoop, asyncio

# 应用qasync Windows平台补丁
import app.utils.qasync_patch
from qfluentwidgets import ConfigItem, FluentTranslator
from PySide6.QtCore import Qt, QTranslator
from PySide6.QtWidgets import QApplication


from app.common.__version__ import __version__
from app.common.config import cfg
from app.view.main_window.main_window import MainWindow
from app.common.config import Language
from app.utils.crypto import crypto_manager


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
        # 只做“同一二进制互斥”：用可执行文件绝对路径做 key，避免不同安装目录互相影响。
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


if __name__ == "__main__":
    _instance_key = os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__
    )
    _single_instance = _SingleInstanceLock(_instance_key)
    if not _single_instance.acquire():
        try:
            # 使用通用弹窗提示
            from app.utils.startup_dialog import show_instance_running_dialog

            show_instance_running_dialog()
        except Exception:
            # 兜底：控制台环境
            print("程序已经在运行中，请先关闭已打开的实例后再启动。", file=sys.stderr)
            sys.exit(0)

    atexit.register(_single_instance.release)

    logger.info(f"MFW 版本:{__version__}")
    logger.info(f"当前工作目录: {os.getcwd()}")

    import faulthandler
    from pathlib import Path

    log_dir = Path("debug")
    log_dir.mkdir(exist_ok=True)
    crash_log = open(log_dir / "crash.log", "a", encoding="utf-8")
    faulthandler.enable(file=crash_log, all_threads=True)
    # 检查并加载密钥
    crypto_manager.ensure_key_exists()

    # 启动参数解析
    parser = argparse.ArgumentParser(
        description="MFW-ChainFlow Assistant", add_help=True
    )
    parser.add_argument(
        "-d", "--direct-run", action="store_true", help="启动后直接运行任务流"
    )
    parser.add_argument(
        "-c", "--config", dest="config_id", help="启动后切换到指定配置ID"
    )
    parser.add_argument(
        "-dev", "--dev", dest="enable_dev", action="store_true", help="显示测试页面"
    )
    args, qt_extra = parser.parse_known_args(sys.argv[1:])
    qt_argv = [sys.argv[0]] + qt_extra

    # 全局异常钩子
    def global_except_hook(exc_type, exc_value, exc_traceback):
        logger.exception(
            "未捕获的全局异常:", exc_info=(exc_type, exc_value, exc_traceback)
        )
        # 显示异常弹窗
        try:
            from app.utils.startup_dialog import show_uncaught_exception_dialog

            show_uncaught_exception_dialog(exc_type, exc_value, exc_traceback)
        except Exception as dialog_err:
            # 弹窗失败时仅记录日志，避免递归
            logger.error(f"显示异常弹窗失败: {dialog_err}")

    sys.excepthook = global_except_hook

    # DPI缩放配置
    if cfg.get(cfg.dpiScale) != "Auto":
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(cfg.get(cfg.dpiScale))

    # 首次启动时自动检测系统语言
    from app.common.config import init_language_on_first_run

    init_language_on_first_run()

    # 创建Qt应用实例
    app = QApplication(qt_argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)

    # 国际化配置
    locale: ConfigItem = cfg.get(cfg.language)
    translator = FluentTranslator(locale.value)
    galleryTranslator = QTranslator()

    # 确定语言代码
    language_code = "zh_cn"  # 默认中文
    if locale == Language.CHINESE_SIMPLIFIED:
        galleryTranslator.load(os.path.join(".", "app", "i18n", "i18n.zh_CN.qm"))
        language_code = "zh_cn"
        logger.info("加载简体中文翻译")
    elif locale == Language.CHINESE_TRADITIONAL:
        galleryTranslator.load(os.path.join(".", "app", "i18n", "i18n.zh_HK.qm"))
        language_code = "zh_hk"
        logger.info("加载繁体中文翻译")
    elif locale == Language.ENGLISH:
        language_code = "en_us"
        logger.info("加载英文翻译")
    app.installTranslator(translator)
    app.installTranslator(galleryTranslator)

    # 尝试导入 maa 库，检测是否缺少 VC++ Redistributable
    try:
        import maa
        from maa.context import Context
        from maa.custom_action import CustomAction
        from maa.custom_recognition import CustomRecognition
    except (ImportError, OSError) as e:
        error_msg = str(e).lower()
        # 检测是否是 DLL 加载失败或 VC++ 相关错误
        if any(
            keyword in error_msg
            for keyword in [
                "dll",
                "vcruntime",
                "msvcp",
                "api-ms-win",
                "找不到指定的模块",
                "specified module could not be found",
                "failed to load",
                "cannot load",
            ]
        ):
            from app.utils.startup_dialog import show_vcredist_missing_dialog

            show_vcredist_missing_dialog()
        else:
            # 其他导入错误，正常抛出
            raise

    # 异步事件循环初始化
    loop = QEventLoop(app)

    # 异步异常处理
    def handle_async_exception(loop, context):
        logger.exception("异步任务异常:", exc_info=context.get("exception"))

    loop.set_exception_handler(handle_async_exception)

    asyncio.set_event_loop(loop)

    # 初始化 GPU 信息缓存
    try:
        from app.utils.gpu_cache import gpu_cache

        gpu_cache.initialize()
    except Exception as e:
        logger.warning(f"GPU 信息缓存初始化失败，忽略: {e}")

    # 创建主窗口
    w = MainWindow(
        loop=loop,
        auto_run=args.direct_run,
        switch_config_id=args.config_id,
        force_enable_test=args.enable_dev,
    )
    w.show()

    # 连接应用退出信号到事件循环停止
    app.aboutToQuit.connect(loop.stop)

    # 运行事件循环
    with loop:
        loop.run_forever()
        logger.debug("关闭异步任务完成")

        # Cancel all pending tasks before closing the loop
        try:
            # Get and cancel all pending tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()

            # Wait for all tasks to be cancelled (gather handles empty list safely)
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )

            # Double-check for any remaining tasks created during cancellation
            remaining = asyncio.all_tasks(loop)
            if remaining:
                logger.warning(f"发现 {len(remaining)} 个未取消的任务，正在强制取消")
                for task in remaining:
                    task.cancel()
                loop.run_until_complete(
                    asyncio.gather(*remaining, return_exceptions=True)
                )
        except Exception as e:
            logger.warning(f"取消待处理任务时出错: {e}")
