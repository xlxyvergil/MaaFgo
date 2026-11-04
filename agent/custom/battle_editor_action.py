"""MXU task entry for the same launcher used by the portable CMD."""
from pathlib import Path
import os
import sys

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

import mfaalog

_AGENT_DIR = Path(__file__).resolve().parents[1]
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from battle_editor.launcher import EditorLaunchError, open_editor, stop_editor


def _mxu_owner():
    """The Agent's direct parent is MXU; never bind a server to this Agent PID."""
    try:
        import psutil
    except ImportError:
        return None, None
    try:
        parent = psutil.Process(os.getpid()).parent()
        if parent is not None and parent.name().lower() in {"maafgo.exe", "mxu.exe", "maafgo", "mxu"}:
            return parent.pid, parent.create_time()
    except (psutil.Error, OSError, RuntimeError):
        pass
    return None, None


@AgentServer.custom_action("open_battle_editor")
class OpenBattleEditor(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            node = context.get_node_data(argv.node_name) or {}
            operation = (node.get("attach") or {}).get("editor_operation", "open")
            root = _AGENT_DIR.parent
            if operation == "stop":
                stopped = stop_editor(root)
                mfaalog.info("[编队编辑器] 服务已停止" if stopped else "[编队编辑器] 服务尚未运行")
            elif operation == "open":
                owner_pid, owner_created = _mxu_owner()
                result = open_editor(root, owner_pid=owner_pid, owner_created=owner_created)
                if result.replaced_pid is not None:
                    mfaalog.info(f"[编队编辑器] 已正常退出旧服务 PID={result.replaced_pid}，原目录：{result.replaced_project_root}")
                    mfaalog.warning("[编队编辑器] 旧页面已断开；尚未同步的修改仍留在原页面，可下载JSON备份后使用本次新打开的页面。")
                mfaalog.info(f"[编队编辑器] {'复用' if result.reused else '启动'}服务：{result.url}")
                mfaalog.info(f"[编队编辑器] 保存目录：{root / 'config' / 'Battle'}")
                if owner_pid is None and not result.reused:
                    mfaalog.warning("[编队编辑器] 未识别到 MXU 主进程，服务以独立模式运行；可在页面或启动任务选项中停止")
                if not result.browser_opened:
                    mfaalog.warning(f"[编队编辑器] 浏览器未自动打开，请手动访问 {result.url}")
            else:
                raise EditorLaunchError(f"未知的编辑器操作：{operation}")
            return CustomAction.RunResult(success=True)
        except (EditorLaunchError, OSError) as exc:
            mfaalog.error(f"[编队编辑器] {exc}")
            return CustomAction.RunResult(success=False)
