"""Upgrade deferral and gated dismissal through real Maa + retained JP images."""
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


class UpgradeSettlementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_banner_defers_and_dismisses_only_with_existing_next_battle(self):
        banner = image("jp_bond_upgrade_banner.png")
        experience = image("jp_experience_result.png")
        report = {}
        test = self

        class Pages(MemoryController):
            def click(self, x, y):
                test.assertTrue(1203 <= x <= 1204 and 72 <= y <= 73)
                self.inputs += 1
                self.image = experience
                return True

        controller = Pages(banner)

        class Probe(CustomAction):
            def run(self, context, argv):
                root = context.get_task_job().job_id
                definitions = json.loads((ROOT / "assets/options/原生自动战斗羁绊满停止.json").read_text("utf-8"))["option"]
                enabled = next(c for c in definitions["原生自动战斗羁绊满停止"]["cases"] if c["name"] == "Yes")
                context.override_pipeline(enabled["pipeline_override"])
                for frame in (banner, image("jp_bond_level_up_overlay.png")):
                    test.assertTrue(bond.read_bond_upgrade(context, frame))
                    test.assertEqual(detect_scene(context, frame, check_order_change=False), Scene.VICTORY)
                for name in ("jp_bond_slot5_max.png", "jp_experience_result.png", "jp_loot_header_and_next.png"):
                    test.assertFalse(bond.read_bond_upgrade(context, image(name)), name)
                for mode in ("all", "slots"):
                    for remaining in (True, False):
                        with test.subTest(mode=mode, remaining=remaining):
                            bond.bond_stops.finish(root)
                            bond.bond_stops.begin(root)
                            bond.bond_stops.battle_started(root, 1, another_battle_planned=remaining)
                            bond.bond_stops.set_formation(root, [
                                {"slot": i, "status": "identified", "is_support": i == 2}
                                for i in range(1, 7)])
                            context.override_pipeline({bond.CONFIG_NODE: {"attach": {
                                "native_bond_stop_mode": mode,
                                **{f"native_bond_stop_slot_{i}": i == 1 for i in range(1, 7)}
                            }}})
                            controller.image = banner
                            before = controller.inputs
                            with patch.object(bond, "_save_evidence", side_effect=lambda c, r: r):
                                stopped = bond.inspect_victory_settlement(context, NS(ok=True, reason="victory"), context.tasker.controller)
                            if remaining:
                                test.assertIsNone(stopped)
                                test.assertIsNotNone(bond.bond_stops.deferred(root))
                                test.assertTrue(context.run_recognition("结算-羁绊升级继续", banner).hit)
                                test.assertTrue(context.run_task("结算-羁绊升级继续").status.succeeded)
                                test.assertEqual(controller.inputs, before + 1)
                                test.assertTrue(context.run_recognition("战斗_经验结算页", experience).hit)
                                test.assertFalse(context.run_recognition("结算-羁绊升级继续", experience).hit)
                                # Fresh frame/callback after the popup keeps this battle deferred.
                                test.assertIsNone(bond.inspect_bond_stop(context, experience))
                                test.assertIsNone(bond.bond_stops.get(root))
                            else:
                                test.assertEqual(stopped.reason, "bond_check_unconfirmed")
                                test.assertIsNone(bond.bond_stops.deferred(root))
                                test.assertFalse(context.run_recognition("结算-羁绊升级继续", banner).hit)
                                test.assertEqual(controller.inputs, before)
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
            resource.register_custom_recognition("native_bond_upgrade_continue", bond.NativeBondUpgradeContinue())
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
