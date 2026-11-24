"""
BBC 停止监听器 - 监听任务停止信号，自动关闭 BBC

原理：
MXU 停止任务时会对 Tasker 调用 post_stop()，Maa 框架会执行一个
entry 为 "MaaTaskerPostStop" 的内部任务。该事件会通过 tasker sink
转发到 agent 进程，此处据此判断"任务被停止"，然后强杀 BBC 进程。

不依赖 MXU 的断开/kill agent 行为，agent 存活期间即可生效。
"""
import os
import sys

import psutil
from maa.agent.agent_server import AgentServer
from maa.event_sink import NotificationType
from maa.tasker import TaskerEventSink

import mfaalog

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

from bbc_process_utils import is_bbc_process

# MXU 停止任务时 post_stop() 触发的内部任务 entry 名
STOP_ENTRY = "MaaTaskerPostStop"


def _kill_bbc_processes() -> int:
    """强杀所有 BBchannel 进程，返回被杀数量"""
    killed = 0
    # 停止事件回调需要尽快返回，避免读取所有进程的命令行。
    for proc in psutil.process_iter(['name']):
        try:
            if is_bbc_process(proc.info['name']):
                mfaalog.info(f"[BbcStopListener] 强制杀死 BBC 进程 PID: {proc.pid}")
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


@AgentServer.tasker_sink()
class BbcStopOnTaskStop(TaskerEventSink):
    """任务被停止时关闭 BBC"""

    def on_tasker_task(self, tasker, noti_type, detail):
        # 停止任务还有完成事件，只在开始时清理一次。
        if noti_type != NotificationType.Starting or getattr(detail, 'entry', '') != STOP_ENTRY:
            return

        mfaalog.info("[BbcStopListener] 检测到任务停止信号，关闭 BBC...")
        try:
            killed = _kill_bbc_processes()
            mfaalog.info(f"[BbcStopListener] 已关闭 {killed} 个 BBC 进程")
        except Exception as e:
            mfaalog.warning(f"[BbcStopListener] 关闭 BBC 失败: {e}")
