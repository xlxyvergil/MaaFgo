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

    def test_bond_max_stops_before_experience_or_loot(self):
        self.check_handoff(stage="bond")

    def test_experience_page_after_missed_bond_stops_without_click(self):
        self.check_handoff(stage="experience")

    def test_loot_page_after_missed_bond_stops_without_click(self):
        self.check_handoff(stage="loot")

    def check_handoff(self, *, stage):
        loot = image("jp_loot_header_and_next.png")
        experience = image("jp_experience_result.png")
        maximum = image("jp_bond_slot5_max.png")
        self.assertEqual(loot.shape, (720, 1280, 3))
        report = {}
        test = self

        controller = MemoryController({"bond": maximum, "experience": experience, "loot": loot}[stage])

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
                for index in range(4):
                    frame = experience.copy()
                    if index == 1: frame[165:245, 640:940] = 0
                    if index == 2: frame[610:690, 350:930] = 0
                    if index == 3: frame[0:120, 450:850] = 0
                    recognized = context.run_recognition("战斗_经验结算页", frame)
                    test.assertEqual(bool(recognized and recognized.hit), index == 0,
                        {name: str(context.run_recognition(name, frame)) for name in (
                            "战斗_战后RESULT", "战斗_经验结算标题", "战斗_经验结算触屏提示")})
                test.assertEqual(detect_scene(context, experience, check_order_change=False), Scene.VICTORY)
                test.assertFalse(bond.read_bond_page(context, experience))
                with patch.object(bond, "save_bond_stop"):
                    stopped = bond.inspect_victory_settlement(context, NS(ok=True, reason="victory"), context.tasker.controller)
                test.assertIsNotNone(stopped)
                expected = "bond_max" if stage == "bond" else "bond_check_unconfirmed"
                test.assertEqual(stopped.reason, expected)
                if stage != "bond":
                    test.assertIn("本场无法补查", stopped.message)
                test.assertEqual(controller.inputs, 0)
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
