import sys
import time
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


# 多个 Agent 分开写文件，避免跨进程轮转冲突；不经过 MXU 的输出通道。
_DIAGNOSTIC_PATH = (Path(__file__).resolve().parents[2] / "debug"
                    / f"recognition-{os.getpid()}.log")
_DIAGNOSTIC_MAX_BYTES = 4 * 1024 * 1024
_DIAGNOSTIC_BACKUP_COUNT = 2
_diagnostic_handler = None
_diagnostic_lock = threading.RLock()


class _DiagnosticFileHandler(RotatingFileHandler):
    def handleError(self, record):
        # 交给 diagnostic 回退，避免 logging 静默丢失排查记录。
        raise


def _diagnostic_backup_name(name):
    # 轮转文件也以 .log 结尾，兼容 MXU 的日志导出与清理。
    base, index = name.rsplit(".", 1)
    return f"{Path(base).with_suffix('')}.{index}.log"


def diagnostic(msg):
    """只保存详细识别日志；写文件失败时告知用户并回退到客户端。"""
    global _diagnostic_handler
    failure = None
    with _diagnostic_lock:
        if _diagnostic_handler is not False:
            try:
                if _diagnostic_handler is None:
                    _DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
                    handler = _DiagnosticFileHandler(
                        _DIAGNOSTIC_PATH, maxBytes=_DIAGNOSTIC_MAX_BYTES,
                        backupCount=_DIAGNOSTIC_BACKUP_COUNT, encoding="utf-8",
                    )
                    handler.namer = _diagnostic_backup_name
                    handler.setFormatter(logging.Formatter(
                        "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S",
                    ))
                    _diagnostic_handler = handler
                record = logging.LogRecord(
                    "maafgo.recognition", logging.INFO, __file__, 0, str(msg), (), None,
                )
                _diagnostic_handler.handle(record)
                return
            except Exception as exc:
                failure = exc
                if _diagnostic_handler is not None:
                    try:
                        _diagnostic_handler.close()
                    except OSError:
                        pass
                _diagnostic_handler = False
    if failure is not None:
        warning(
            f"详细识别日志无法写入 {_DIAGNOSTIC_PATH}，后续细节将暂时显示在运行日志中。"
            "请检查该目录是否可写、磁盘是否有剩余空间，处理后停止并重新启动任务。"
            f"原因：{failure}"
        )
    info(msg)

# 核心修改：MFA GUI 监听的是标准输出，且需要特定的前缀
# 开发者原话："focus或者字符串前面拼接info："

# 使用方式：在对应py脚本里import mfaalog，然后按照logger的方式填写即可
# mfaalog.error(f"[ExecuteBbcTask] 参数不完整: team={team_config}, count={run_count}, apple={apple_type}")

def _print_to_gui(prefix, msg):
    """
    基础输出函数
    :param prefix: 魔法前缀，如 'info:', 'error:', 'focus:'
    :param msg: 实际消息内容
    """
    # 获取当前时间（可选，有些GUI会自动加时间，你可以先试带时间的，如果重复了就去掉）
    # timestamp = time.strftime("%H:%M:%S", time.localtime())
    
    # 组合最终字符串
    # 格式可能需要是: "info:你的消息"
    final_msg = f"{prefix}{msg}"
    
    # 关键点1: 必须 flush，否则 Python 会缓存输出，导致 GUI 看起来卡顿或不显示
    print(final_msg, flush=True)

def info(msg):
    """普通日志"""
    # 对应开发者说的 "拼接 info:"
    _print_to_gui("info:", f"🟣 >>> {msg}")

def warning(msg):
    """警告日志"""
    # 尝试猜测 warning 的前缀，通常是 warn: 或 warning:，如果没有就用 info: [WARN]
    _print_to_gui("warn:", f"⚠️ >>> {msg}")

def error(msg):
    """错误日志"""
    # 尝试猜测 error 的前缀
    _print_to_gui("error:", f"🔴 >>> {msg}")

def debug(msg):
    """调试日志"""
    # debug 可能会被 GUI 过滤，如果显示不出来，可以改用 info: [DEBUG]
    _print_to_gui("debug:", msg)

def focus(task_id):
    """
    特殊指令：尝试让 GUI 聚焦/高亮某个任务
    开发者提到的 "focus" 可能指这个
    """
    _print_to_gui("focus:", task_id)

# ---------------------------------------------------------
# 必须保留的设置：防止中文乱码
# 如果 GUI 收到乱码，它可能直接丢弃整条日志，导致你看不到任何东西
if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(encoding='utf-8') # type: ignore
    sys.stderr.reconfigure(encoding='utf-8') # type: ignore

# ---------------------------------------------------------
# 自测代码（直接运行这个文件测试）
if __name__ == "__main__":
    print("正在测试 MFA 日志协议...", flush=True)
    
    # 1. 开发者明确提到的格式
    info("这条日志应该能显示了！(基于 info: 前缀)")
    
    # 2. 测试其他等级
    time.sleep(0.5)
    warning("这是一条警告测试")
    error("这是一条错误测试")
    
    # 3. 测试 focus (假设任务ID是 task_1)
    time.sleep(0.5)
    focus("task_1")
    info("应该已经尝试聚焦任务了")
