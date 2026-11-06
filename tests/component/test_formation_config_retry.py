"""Production pipeline retry graph through real MaaFramework, no live device.

Visual recognizers are replaced by explicit scene states; detection accuracy is
verified separately with captured screenshots. Production actions/next/budgets
remain unchanged, except short timeouts and removed delays for test speed.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

import maa
import numpy as np
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import formation as f

ROOT = Path(__file__).resolve().parents[2]
OPEN = "自动编队-打开配置"
DETECT = "自动编队-检测编辑决定"


class ConfigController(CustomController):
    def __init__(self, succeed_after, unknown=False, both_visible=False):
        self.scene = "confirm"
        self.clicks = []
        self.round_clicks = 0
        self.succeed_after = succeed_after
        self.unknown = unknown
        self.both_visible = both_visible
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return "config-retry-memory-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), np.uint8)
    def click(self, x, y):
        if (x, y) != (222, 678):
            return False
        self.clicks.append((x, y))
        self.round_clicks += 1
        if self.unknown:
            self.scene = "unknown"
        elif self.succeed_after is not None and self.round_clicks >= self.succeed_after:
            self.scene = "edit"
        return True

    def reject(self, *args): return False
    start_app = stop_app = swipe = touch_down = touch_move = touch_up = reject
    click_key = input_text = key_down = key_up = reject


class FormationConfigRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.source = json.loads((ROOT / "assets/resource/base/pipeline/自动编队.json").read_text("utf8"))

    def run_graph(self, *, succeed_after, unknown=False, both_visible=False, runs=1):
        controller = ConfigController(succeed_after, unknown, both_visible)
        nodes = {name: copy.deepcopy(self.source[name]) for name in (OPEN, DETECT)}
        for node in nodes.values():
            node.update(pre_delay=0, post_delay=0, timeout=40, rate_limit=1)
            node["recognition"] = {"type": "Custom", "param": {"custom_recognition": "screen"}}
        results = []

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                if argv.node_name == DETECT and controller.scene == "edit":
                    return (1100, 650, 100, 40)
                if argv.node_name == OPEN and (controller.scene == "confirm"
                                              or (controller.scene == "edit" and controller.both_visible)):
                    return (200, 650, 50, 40)
                return None

        class Invoke(CustomAction):
            def run(self, context, argv):
                action = f.AutoFormationFromChaldea.__new__(f.AutoFormationFromChaldea)
                action.context = context
                for _ in range(runs):
                    controller.scene = "confirm"
                    controller.round_clicks = 0
                    result = action._run_pipeline(OPEN)
                    results.append(result)
                    if not result:
                        return False
                return True

        nodes["invoke"] = {"action": {"type": "Custom", "param": {"custom_action": "invoke"}},
                           "on_error": []}
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            (bundle / "pipeline").mkdir()
            (bundle / "pipeline/config.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
            resource.register_custom_recognition("screen", Screen())
            resource.register_custom_action("invoke", Invoke())
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                job = tasker.post_task("invoke")
                deadline = time.monotonic() + 4
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(job.status.done, "配置变更重试必须有限结束")
                succeeded = job.status.succeeded
            finally:
                tasker.post_stop().wait()
        return succeeded, controller, results

    def test_first_next_is_non_clicking_decision_detector(self):
        self.assertEqual(self.source[OPEN]["next"], [DETECT, OPEN])
        self.assertEqual(self.source[OPEN]["max_hit"], 3)
        self.assertEqual(self.source[OPEN]["on_error"], [])
        self.assertEqual(self.source[DETECT]["action"]["type"], "DoNothing")
        self.assertEqual(self.source[DETECT]["next"], [])
        recognitions = self.source[DETECT]["recognition"]["param"]["all_of"]
        self.assertEqual(recognitions[0]["recognition"]["param"]["template"], "battle/编队决定.png")
        self.assertEqual(recognitions[1]["recognition"]["param"]["expected"], ["决定|決定"])

    def test_normal_transition_clicks_config_once_not_decision(self):
        ok, controller, results = self.run_graph(succeed_after=1)
        self.assertTrue(ok)
        self.assertEqual(controller.clicks, [(222, 678)])
        self.assertEqual(results, [True])

    def test_one_dropped_click_retries_then_returns_success(self):
        ok, controller, _ = self.run_graph(succeed_after=2)
        self.assertTrue(ok)
        self.assertEqual(controller.clicks, [(222, 678)] * 2)

    def test_decision_detection_has_priority_over_another_config_click(self):
        ok, controller, _ = self.run_graph(succeed_after=1, both_visible=True)
        self.assertTrue(ok)
        self.assertEqual(len(controller.clicks), 1)

    def test_three_dropped_clicks_fail_without_global_recovery(self):
        ok, controller, results = self.run_graph(succeed_after=None)
        self.assertFalse(ok)
        self.assertEqual(len(controller.clicks), 3)
        self.assertEqual(results, [False])

    def test_unknown_page_never_receives_another_config_click(self):
        ok, controller, _ = self.run_graph(succeed_after=None, unknown=True)
        self.assertFalse(ok)
        self.assertEqual(len(controller.clicks), 1)

    def test_each_nested_call_gets_its_own_retry_budget(self):
        ok, controller, results = self.run_graph(succeed_after=2, runs=2)
        self.assertTrue(ok)
        self.assertEqual(len(controller.clicks), 4)
        self.assertEqual(results, [True, True])


if __name__ == "__main__":
    unittest.main()
