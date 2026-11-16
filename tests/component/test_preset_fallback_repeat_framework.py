"""Real repeat/Context/session handoff after fallback, without game input.

Navigation and settlement pages are omitted; perception and physical Executor
calls are controlled boundaries. The production repeat wrapper, AutoBattleAction,
default runtime loop, plan parsing and formation sessions remain real.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import maa
import numpy as np
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import ROOT, battle_action, repeat_action
from battle.core.enums import Scene
from battle.core.models import BattleState, Confidence, ServantState, SkillState
from battle.runtime.formation_session import context_session, session_override, sessions
from battle.runtime.runtime import AutoBattleRuntime


class NoDeviceController(CustomController):
    def connect(self): return True
    def request_uuid(self): return "preset-fallback-repeat-local-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
    def click(self, *args): raise AssertionError("No game input permitted")
    start_app = stop_app = swipe = touch_down = touch_move = touch_up = click
    click_key = input_text = key_down = key_up = click


class PresetFallbackRepeatFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_fallback_victory_keeps_repeat_session_and_next_battle_reloads_original_plan(self):
        entry = "原生自动战斗入口"
        source = json.loads((ROOT / "assets/resource/base/pipeline/地狱的试炼.json").read_text("utf-8"))
        wrapper = copy.deepcopy(source["地狱的试炼-入口"])
        wrapper["attach"]["battle_count"] = 2
        param = {"plan": {"turns": [{"servant_skills": [
            {"servant_slot": 1, "skill_index": 2}], "np_order": [2]}]}}
        battle_node = {
            "action": {"type": "Custom", "param": {
                "custom_action": "auto_battle", "custom_action_param": param}},
            "attach": {},
            "pre_delay": 0, "post_delay": 0, "timeout": 1, "on_error": [],
        }
        owners, seen_nodes, made, later_tasks = [], [], [], []
        main = BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), (), (
            ServantState(slot=1, confidence=Confidence(1), skills=(
                SkillState(True, Confidence(1)), SkillState(True, Confidence(1)),
                SkillState(True, Confidence(1)))),))
        victory = BattleState(Scene.VICTORY, Confidence(1), (), (), ())

        class CheckRound(CustomAction):
            def run(self, context, argv):
                root, token = context_session(context)
                snapshot, battle = sessions.read(root, token)
                owners.append((root, token, battle, snapshot))
                seen_nodes.append(copy.deepcopy(context.get_node_data(entry)))
                return CustomAction.RunResult(success=True)

        class CheckSharedEntry(CustomAction):
            def run(self, context, argv):
                later_tasks.append((context.get_task_job().job_id,
                                    copy.deepcopy(context.get_node_data(entry))))
                return CustomAction.RunResult(success=True)

        def create(*args, **kwargs):
            runtime = AutoBattleRuntime(*args, **kwargs)
            index = len(made)
            # The first preset skill cannot confirm its expected return scene.
            # Its replacement observes victory; the next battle must execute the
            # original preset skill again, rather than retain the default mode.
            frames = [main] if index == 0 else [victory] if index == 1 else [main, victory]
            runtime._observe = Mock(side_effect=frames)
            runtime.executor.cast_servant_skill = Mock()
            runtime.executor.open_command_cards = Mock(return_value=True)
            runtime._run_special_skill_pipeline = Mock(return_value=None)
            runtime._wait_until = Mock(return_value=index != 0)
            made.append(runtime)
            return runtime

        pipeline = {
            "地狱的试炼-入口": wrapper,
            "地狱的试炼-单场入口": {"next": ["检查本场会话"], "timeout": 1, "on_error": []},
            "检查本场会话": {"action": {"type": "Custom", "param": {
                "custom_action": "check_round"}}, "next": [entry], "timeout": 1, "on_error": []},
            "只读检查共享入口": {"action": {"type": "Custom", "param": {
                "custom_action": "check_shared"}}, "timeout": 1, "on_error": []},
            entry: battle_node,
        }
        # The real repeat action installs these references in its cloned Context.
        for name, node in session_override("fixture").items():
            pipeline.setdefault(name, {})
            for target in node.get("next", []):
                pipeline.setdefault(target, {})
        with tempfile.TemporaryDirectory(prefix="maa-preset-fallback-repeat-") as temporary:
            directory = Path(temporary)
            (directory / "pipeline").mkdir()
            (directory / "pipeline/test.json").write_text(json.dumps(pipeline, ensure_ascii=False), "utf-8")
            controller, resource, tasker = NoDeviceController(), Resource(), Tasker()
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(resource.register_custom_action("auto_battle_repeat", repeat_action.AutoBattleRepeatAction()))
            self.assertTrue(resource.register_custom_action("auto_battle", battle_action.AutoBattleAction()))
            self.assertTrue(resource.register_custom_action("check_round", CheckRound()))
            self.assertTrue(resource.register_custom_action("check_shared", CheckSharedEntry()))
            self.assertTrue(resource.post_bundle(str(directory)).wait().succeeded)
            self.assertTrue(tasker.bind(resource, controller))
            with patch.object(battle_action, "AutoBattleRuntime", side_effect=create), \
                 patch.object(battle_action, "recover_for_default_battle", return_value=True) as recovery, \
                 patch.object(battle_action, "inspect_victory_settlement") as settlement:
                try:
                    job = tasker.post_task("地狱的试炼-入口", pipeline_override={entry: {
                        "attach": {"native_battle_preset_fallback": True}}})
                    deadline = time.monotonic() + 30
                    while not job.status.done and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(job.status.done, "预设回退后的重复挑战未在有界时间内结束")
                    self.assertTrue(job.status.succeeded, repr([
                        (runtime.decider.plan, runtime.executor.cast_servant_skill.call_args_list)
                        for runtime in made]))
                    self.assertTrue(tasker.post_task("只读检查共享入口").wait().succeeded)
                finally:
                    tasker.post_stop().wait()
            self.assertEqual(len(made), 3)
            recovery.assert_called_once_with(made[0])
            self.assertEqual(settlement.call_count, 2)
            self.assertEqual([owner[2] for owner in owners], [1, 2])
            self.assertEqual(len({owner[:2] for owner in owners}), 1)
            self.assertTrue(all(owner[3] is None for owner in owners))
            self.assertFalse(sessions.valid(*owners[0][:2]))
            self.assertEqual(seen_nodes[0], seen_nodes[1])
            self.assertEqual(seen_nodes[0]["action"]["param"]["custom_action_param"], param)
            self.assertIs(seen_nodes[0]["attach"]["native_battle_preset_fallback"], True)
            self.assertEqual(len(later_tasks), 1)
            self.assertNotEqual(later_tasks[0][0], owners[0][0])
            self.assertNotIn("native_battle_preset_fallback", later_tasks[0][1].get("attach", {}))
            self.assertEqual(later_tasks[0][1]["action"], seen_nodes[0]["action"])
            self.assertEqual(made[0].decider.plan, made[2].decider.plan)
            self.assertIsNotNone(made[2].decider.plan)
            self.assertIsNone(made[1].decider.plan)
            made[0].executor.cast_servant_skill.assert_called_once()
            made[1].executor.cast_servant_skill.assert_not_called()
            made[2].executor.cast_servant_skill.assert_called_once()
            self.assertEqual(made[2].executor.cast_servant_skill.call_args.args[:2], (1, 2))


if __name__ == "__main__":
    unittest.main()
