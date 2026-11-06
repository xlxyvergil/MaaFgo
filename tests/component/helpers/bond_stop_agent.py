"""Agent-side fixture for test_bond_stop_agent; no device access."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.tasker import Tasker

identifier, scenario, report_path, binary = sys.argv[1:]
Library.open(Path(binary))
Tasker.set_log_dir("")
Tasker.set_stdout_level(LoggingLevelEnum.Off)

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
import bond_stop_action as bond

report = {"seed_reason": None, "guard_reason": None, "reads": {"seed": 0, "guard": 0}, "tail": False}
phase = "seed"


def save():
    Path(report_path).write_text(json.dumps(report), encoding="utf-8")


def read_max(context, image):
    report["reads"][phase] += 1
    return phase == "guard" and scenario == "late_max" and report["reads"][phase] >= 2


# 明确模拟正常羁绊页、MAX与无升级遮挡；截图、嵌套等待及冻结由真实框架执行。
bond.read_bond_page = lambda context, image: True
bond.read_any_bond_max = read_max
bond.read_bond_upgrade = lambda context, image: False


@AgentServer.custom_action("victory_handoff")
class VictoryHandoff(CustomAction):
    def run(self, context, argv):
        global phase
        controller = context.tasker.controller
        result = bond.inspect_victory_settlement(
            context, SimpleNamespace(ok=True, reason="victory"), controller)
        report["seed_reason"] = result.reason if result else ""
        phase = "guard"
        save()
        return result is None


@AgentServer.custom_recognition("record_bond_guard")
class RecordBondGuard(CustomRecognition):
    def analyze(self, context, argv):
        result = bond.NativeBondMax().analyze(context, argv)
        stopped = bond.bond_stops.get(context.get_task_job().job_id)
        report["guard_reason"] = stopped.reason if stopped else ""
        save()
        return result


@AgentServer.custom_action("tail")
class Tail(CustomAction):
    def run(self, context, argv):
        report["tail"] = True
        save()
        return True


AgentServer.start_up(identifier)
try:
    AgentServer.join()
finally:
    AgentServer.shut_down()
