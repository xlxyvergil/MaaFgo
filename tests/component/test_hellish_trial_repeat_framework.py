"""Real nested Tasker hit budgets; no emulator, clicks, or battle completion."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import maa
import numpy as np
from maa.controller import CustomController
from maa.context import ContextEventSink
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.event_sink import NotificationType
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import ROOT, repeat_action
from battle.runtime.formation_session import SESSION_NODE, session_override, sessions


class NoDeviceController(CustomController):
    def connect(self): return True
    def request_uuid(self): return "hellish-trial-local-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
    def click(self, *args): raise AssertionError("No game input permitted")
    start_app = stop_app = swipe = touch_down = touch_move = touch_up = click
    click_key = input_text = key_down = key_up = click


class TrialRepeatFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def run_trial(self, count, fail_at=None):
        source = json.loads((ROOT / "assets/resource/base/pipeline/地狱的试炼.json").read_text(encoding="utf-8"))
        wrapper = copy.deepcopy(source["地狱的试炼-入口"])
        wrapper["attach"]["battle_count"] = count
        rounds = []
        owners = []
        progress = []

        class Progress(ContextEventSink):
            def on_node_action(self, context, noti_type, detail):
                if noti_type == NotificationType.Starting and detail.name == "地狱的试炼-单场入口":
                    progress.append(context.get_node_data(detail.name)["focus"]["Node.Action.Starting"])

        class Record(CustomAction):
            def run(self, context, argv):
                root = context.get_task_job().job_id
                token = context.get_node_data(SESSION_NODE)["attach"]["session_id"]
                owners.append((root, token))
                rounds.append(len(rounds) + 1)
                progress.append(len(rounds))
                return CustomAction.RunResult(success=len(rounds) != fail_at)

        # The real wrapper and its reset list run unchanged. The screen-dependent
        # single battle is represented by a max_hit=1 gate with a recorded result.
        pipeline = {
            "地狱的试炼-入口": wrapper,
            "地狱的试炼-单场入口": {"next": ["地狱的试炼-活动页本期关卡"], "timeout": 1, "on_error": []},
            "地狱的试炼-活动页本期关卡": {
                "max_hit": 1, "action": {"type": "Custom", "param": {"custom_action": "record"}},
                "timeout": 1, "on_error": [],
            },
        }
        for name, node in session_override("fixture").items():
            pipeline.setdefault(name, {})
            for target in node.get("next", []):
                pipeline.setdefault(target, {})
        with tempfile.TemporaryDirectory(prefix="maa-trial-repeat-") as temporary:
            directory = Path(temporary)
            (directory / "pipeline").mkdir()
            (directory / "pipeline/test.json").write_text(json.dumps(pipeline, ensure_ascii=False), encoding="utf-8")
            controller, resource, tasker = NoDeviceController(), Resource(), Tasker()
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(resource.register_custom_action("auto_battle_repeat", repeat_action.AutoBattleRepeatAction()))
            self.assertTrue(resource.register_custom_action("record", Record()))
            self.assertTrue(resource.post_bundle(str(directory)).wait().succeeded)
            self.assertTrue(tasker.bind(resource, controller))
            tasker.add_context_sink(Progress())
            try:
                result = tasker.post_task("地狱的试炼-入口").wait()
                self.assertEqual(result.succeeded, fail_at is None)
                self.assertEqual(rounds, list(range(1, (count if fail_at is None else fail_at) + 1)))
                self.assertEqual(progress, [item for current in rounds
                                            for item in (f"当前第{current}场战斗。", current)])
                self.assertTrue(owners)
                self.assertEqual(len(set(owners)), 1)
                self.assertFalse(sessions.valid(*owners[0]))
            finally:
                tasker.post_stop().wait()

    def test_three_rounds_refresh_shared_max_hit_budget(self):
        self.run_trial(3)

    def test_default_one_keeps_full_entry(self):
        self.run_trial(1)

    def test_second_failure_stops_without_a_third_round(self):
        self.run_trial(3, fail_at=2)


if __name__ == "__main__":
    unittest.main()
