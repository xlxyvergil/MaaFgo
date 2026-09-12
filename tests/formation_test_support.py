"""离线编队测试导入适配：只替换 Agent 注册，不模拟视觉算法。"""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from maa.agent.agent_server import AgentServer

with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls), \
     patch.object(AgentServer, "custom_recognition", lambda *args: lambda cls: cls), \
     patch.object(AgentServer, "tasker_sink", lambda *args: lambda cls: cls):
    import formation_action as formation
    import auto_battle_action as battle_action
    import auto_battle_repeat_action as repeat_action

