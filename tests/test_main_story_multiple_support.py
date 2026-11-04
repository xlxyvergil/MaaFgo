"""自动推主线多助战识别后的本关接管、开关与配置恢复回归。"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from test_main_story import Context
from main_story_action import StoryRunner, StoryStopped
from battle.runtime.formation_session import sessions

ROOT = Path(__file__).resolve().parents[1]


def merge(target, update):
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


class CloneContext(Context):
    """节点配置隔离，任务与设备状态按真实Context共享。"""

    def __init__(self, frames=(), config=None):
        super().__init__(frames, config)
        self.clones = []
        self.executions = []
        self.fail_tasks = set()

    def clone(self):
        child = copy.copy(self)
        child.nodes = copy.deepcopy(self.nodes)
        self.clones.append(child)
        return child

    def override_pipeline(self, override):
        merge(self.nodes, override)
        self.override = copy.deepcopy(override)
        return True

    def run_task(self, name, pipeline_override=None):
        effective = copy.deepcopy(self.nodes)
        merge(effective, pipeline_override or {})
        self.tasks.append((name, copy.deepcopy(pipeline_override)))
        self.executions.append((name, effective))
        return NS(status=NS(succeeded=name not in self.fail_tasks))


def runner(frames=(), config=None):
    context = CloneContext(frames, config)
    clock = [0.0]
    runtime = StoryRunner(context, clock=lambda: clock[0],
                          sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return runtime, context


class MultipleSupportStateTests(unittest.TestCase):
    def test_multiple_supports_without_fixed_label_keep_game_formation(self):
        runtime, context = runner()
        self.assertFalse(runtime._handle_restricted_formation({
            "进本-队伍确认", "主线限制-多个助战", "主线限制-打开自动编队"}))
        self.assertTrue(runtime.current_restricted)
        self.assertFalse(runtime._restriction_needs_auto)
        self.assertEqual(context.actions, [])

    def test_multiple_supports_with_handling_disabled_stop_before_inputs(self):
        runtime, context = runner(config={"auto_handle_restrictions": False})
        with self.assertRaisesRegex(StoryStopped, "自动处理编队限制情况"):
            runtime._handle_restricted_formation({"进本-队伍确认", "主线限制-多个助战"})
        self.assertFalse(runtime.current_restricted)
        self.assertEqual(context.actions, [])
        self.assertEqual(context.tasks, [])

    def test_multiple_support_signal_outside_formation_does_not_mark_quest(self):
        runtime, _ = runner()
        self.assertFalse(runtime._handle_restricted_formation({"主线限制-多个助战"}))
        self.assertFalse(runtime.current_restricted)

    def test_multiple_support_detection_does_not_change_bbc(self):
        runtime, _ = runner(config={"battle_mode": "bbc"})
        self.assertFalse(runtime._handle_restricted_formation({
            "进本-队伍确认", "主线限制-多个助战"}))
        self.assertFalse(runtime.current_restricted)


class MultipleSupportIsolationTests(unittest.TestCase):
    def setUp(self):
        sessions.finish(12345)
        self.addCleanup(sessions.finish, 12345)

    def make_runtime(self, *, restricted):
        runtime, context = runner([{"进本-战斗主界面已出现"}])
        context.nodes["进本-编队处理"]["next"] = ["执行手动编队检查"]
        context.nodes["原生自动战斗入口"]["action"]["param"]["custom_action_param"] = {
            "chaldea_import_source": "missing-team.json",
            "chaldea_share": {"actions": [{"bad": "must not read"}]},
            "plan": {"turns": [{"np_order": [1]}]},
            "max_turns": 99,
        }
        runtime.current_restricted = restricted
        return runtime, context

    def assert_session_finished(self, context):
        tokens = [child.nodes["编队身份-会话"]["attach"]["session_id"]
                  for child in context.clones
                  if child.nodes.get("编队身份-会话", {}).get("attach", {}).get("session_id")]
        self.assertTrue(tokens, "fight should initialize its own session")
        for token in tokens:
            self.assertFalse(sessions.valid(12345, token))

    def test_multiple_supports_bypass_chaldea_for_this_quest_only(self):
        runtime, context = self.make_runtime(restricted=False)
        before = copy.deepcopy(context.nodes)
        context.frames[:] = [
            *[{"进本-队伍确认", "主线限制-多个助战"}] * 3,
            {"进本-战斗主界面已出现"},
        ]
        runtime.fight()
        first_entry, first_battle = (nodes for _, nodes in context.executions)
        self.assertEqual(first_entry["进本-编队处理"]["next"], ["进本-点击队伍确认"])
        self.assertEqual(json.loads(first_battle["原生自动战斗入口"]["action"]["param"]["custom_action_param"]),
                         {"native_battle_formation_reset": True})
        self.assertEqual(context.actions, [])
        self.assertEqual(context.nodes, before)
        self.assert_session_finished(context)

        runtime.reset_restriction_state()
        with patch.object(runtime, "reach_formation"):
            runtime.fight()
        next_entry, next_battle = (nodes for _, nodes in context.executions[2:])
        self.assertEqual(next_entry["进本-编队处理"]["next"], ["执行手动编队检查"])
        self.assertEqual(next_battle["原生自动战斗入口"]["action"], before["原生自动战斗入口"]["action"])
        self.assert_session_finished(context)


if __name__ == "__main__":
    unittest.main()
