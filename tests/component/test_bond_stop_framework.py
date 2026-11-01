"""真实框架调用羁绊守卫、嵌套等待和冻结确认；仅内存画面，无设备输入。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import maa
import numpy as np
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom"), str(ROOT / "tests/component")]
import bond_stop_action as action
from battle.runtime.bond_stop_state import bond_stops
from test_formation_framework import MemoryController


class BondStopFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_real_guard_nested_wait_and_freeze_stop_before_parent_tail(self):
        source = json.loads((ROOT / "assets/resource/base/pipeline/原生自动战斗调度.json").read_text("utf-8"))
        for is_max in (False, True):
            with self.subTest(is_max=is_max), tempfile.TemporaryDirectory() as temp:
                nodes = {name: copy.deepcopy(source[name]) for name in (
                    action.GUARD_NODE, "结算-羁绊停止等待动画", "结算-羁绊停止出口",
                    "结算-等待羁绊出现", "结算-羁绊快速复核")}
                nodes[action.GUARD_NODE]["enabled"] = True
                nodes[action.GUARD_NODE]["attach"].update(positive_attempts=2, positive_timeout_ms=1000)
                nodes["结算-羁绊快速复核"]["pre_delay"] = 0
                nodes["结算-羁绊停止等待动画"]["pre_delay"] = 0
                nodes["结算-羁绊停止等待动画"]["post_wait_freezes"].update(
                    time=20, rate_limit=5, timeout=1000)
                nodes[action.CONFIG_NODE] = {"attach": {"native_bond_stop_mode": "all"}}
                nodes["root"] = {"next": [action.GUARD_NODE, "tail"], "on_error": []}
                nodes["tail"] = {"action": {"type": "Custom", "param": {"custom_action": "tail"}}}
                directory = Path(temp) / "pipeline"
                directory.mkdir()
                (directory / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
                seen = []
                class Tail(CustomAction):
                    def run(self, context, argv):
                        seen.append("tail")
                        return True
                resource = Resource()
                resource.use_cpu()
                resource.register_custom_recognition("native_bond_max", action.NativeBondMax())
                resource.register_custom_action("save_native_bond_stop", action.SaveNativeBondStop())
                resource.register_custom_action("tail", Tail())
                self.assertTrue(resource.post_bundle(temp).wait().succeeded)
                controller = MemoryController(np.zeros((720, 1280, 3), np.uint8))
                self.assertTrue(controller.post_connection().wait().succeeded)
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                with patch.object(action, "read_bond_page", return_value=True), \
                     patch.object(action, "read_any_bond_max", return_value=is_max), \
                     patch.object(action, "Path", side_effect=lambda value: Path(temp) / value):
                    job = tasker.post_task("root")
                    deadline = time.monotonic() + 5
                    try:
                        while not job.status.done and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertTrue(job.status.done, "guard callback did not finish")
                        self.assertTrue(job.status.succeeded)
                        stopped = bond_stops.get(job.job_id)
                        self.assertEqual(bool(stopped), is_max)
                        self.assertEqual(seen, [] if is_max else ["tail"])
                        if is_max:
                            self.assertEqual(stopped.reason, "bond_max")
                            self.assertTrue(Path(stopped.screenshot_path).is_file())
                        self.assertEqual(controller.inputs, 0)
                    finally:
                        tasker.post_stop().wait()
                        bond_stops.finish(job.job_id)


if __name__ == "__main__":
    unittest.main()
