"""Real loot patches and bond-MAX frame through Maa; no device connection."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import cv2
import maa
import numpy as np
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom"), str(Path(__file__).resolve().parent)]
from test_formation_framework import MemoryController
import bond_stop_action as bond
from battle.core.enums import Scene
from battle.perception.perception import detect_scene


def image(name):
    return cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/bond_stop" / name, np.uint8), cv2.IMREAD_COLOR)


class LootSettlementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_loot_requires_both_features_and_single_click_still_checks_max(self):
        loot = image("jp_loot_header_and_next.png")
        maximum = image("jp_bond_slot5_max.png")
        self.assertEqual(loot.shape, (720, 1280, 3))
        report = {}
        test = self

        class Pages(MemoryController):
            def click(self, x, y):
                self.inputs += 1
                report["click"] = (x, y)
                self.image = maximum
                return True

        controller = Pages(loot)

        class Probe(CustomAction):
            def run(self, context, argv):
                cases = json.loads((ROOT / "assets/options/原生自动战斗羁绊满停止.json").read_text("utf-8"))["option"]
                parent = next(c for c in cases["原生自动战斗羁绊满停止"]["cases"] if c["name"] == "Yes")
                test.assertTrue(context.override_pipeline(parent["pipeline_override"]))
                for case in cases["原生自动战斗羁绊检查号位"]["cases"]:
                    test.assertTrue(context.override_pipeline(case["pipeline_override"]))
                next_only = loot.copy()
                next_only[20:85, 90:480] = 0
                header_only = loot.copy()
                header_only[570:720, 900:1280] = 0
                for frame, expected in ((loot, True), (next_only, False), (header_only, False),
                                        (np.zeros_like(loot), False)):
                    recognized = context.run_recognition("战斗_战利品弹窗", frame)
                    test.assertEqual(bool(recognized and recognized.hit), expected)
                    victory = context.run_recognition("战斗_胜利", frame)
                    test.assertEqual(bool(victory and victory.hit), expected)
                test.assertEqual(detect_scene(context, loot, check_order_change=False), Scene.VICTORY)
                test.assertFalse(context.run_recognition("战斗_战后RESULT", loot).hit)
                with patch.object(bond, "save_bond_stop"):
                    stopped = bond.inspect_victory_settlement(context, NS(ok=True, reason="victory"), context.tasker.controller)
                test.assertIsNotNone(stopped)
                test.assertEqual(stopped.reason, "bond_max")
                test.assertEqual(controller.inputs, 1)
                x, y = report["click"]
                test.assertTrue(950 <= x < 1275 and 600 <= y < 685)
                report["reason"] = stopped.reason
                report["passed"] = True
                return True

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "pipeline").mkdir()
            (temp / "pipeline/test.json").write_text(json.dumps({"probe": {
                "action": {"type": "Custom", "param": {"custom_action": "probe"}},
                "next": [], "on_error": []}}), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded)
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_action("probe", Probe())
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                self.assertTrue(tasker.post_task("probe").wait().succeeded, report)
                self.assertTrue(report.get("passed"), report)
            finally:
                tasker.post_stop().wait()
                bond.bond_stops.stop_owner("")


if __name__ == "__main__":
    unittest.main()
