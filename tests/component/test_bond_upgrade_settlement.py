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

    def test_panel_defers_and_dismisses_only_with_existing_next_battle(self):
        panels = {"legacy": image("jp_bond_level_up_overlay.png"),
                  "elisabeth": image("jp_bond_upgrade_panel.png")}
        experience = image("jp_experience_result.png")
        report = {}
        test = self

        class Pages(MemoryController):
            def click(self, x, y):
                test.assertTrue(1203 <= x <= 1204 and 72 <= y <= 73)
                self.inputs += 1
                self.image = experience
                return True

        controller = Pages(panels["elisabeth"])

        class Probe(CustomAction):
            def run(self, context, argv):
                root = context.get_task_job().job_id
                definitions = json.loads((ROOT / "assets/options/原生自动战斗羁绊满停止.json").read_text("utf-8"))["option"]
                enabled = next(c for c in definitions["原生自动战斗羁绊满停止"]["cases"] if c["name"] == "Yes")
                context.override_pipeline(enabled["pipeline_override"])
                for frame in panels.values():
                    test.assertTrue(bond.read_bond_upgrade(context, frame))
                    test.assertFalse(bond.read_bond_page(context, frame))
                    test.assertEqual(detect_scene(context, frame, check_order_change=False), Scene.VICTORY)
                for name in ("jp_bond_slot5_max.png", "jp_bond_before_upgrade.png", "jp_experience_result.png",
                             "jp_loot_header_and_next.png", "jp_bond_upgrade_banner.png",
                             "jp_bond_upgrade_banner_occluded.png"):
                    test.assertFalse(bond.read_bond_upgrade(context, image(name)), name)
                for label, frame in panels.items():
                    for mode in ("all", "slots"):
                        for remaining in (True, False):
                            with test.subTest(panel=label, mode=mode, remaining=remaining):
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
                                controller.image = frame
                                before = controller.inputs
                                with patch.object(bond, "_save_evidence", side_effect=lambda c, r: r):
                                    stopped = bond.inspect_victory_settlement(context, NS(ok=True, reason="victory"), context.tasker.controller)
                                if remaining:
                                    test.assertIsNone(stopped)
                                    test.assertIsNotNone(bond.bond_stops.deferred(root))
                                    test.assertTrue(context.run_recognition("结算-羁绊升级继续", frame).hit)
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
                                    test.assertFalse(context.run_recognition("结算-羁绊升级继续", frame).hit)
                                    test.assertEqual(controller.inputs, before)
                report["passed"] = True
                return True

        self.run_probe(controller, Probe(), report)

    def test_bond_waits_through_early_banner_for_panel_then_rechecks_next_battle(self):
        initial = image("jp_bond_before_upgrade.png")
        early = image("jp_bond_upgrade_banner_occluded.png")
        panel = image("jp_bond_upgrade_panel.png")
        experience = image("jp_experience_result.png")
        test = self
        report = {}

        class Pages(MemoryController):
            replaying = False
            replay_frames = 0
            observations = None
            root = None

            def screencap(self):
                if self.replaying:
                    frames = (initial, early, early, panel)
                    phase = min(self.replay_frames, len(frames) - 1)
                    self.image = frames[phase]
                    self.observations.append((phase, self.inputs, bond.bond_stops.deferred(self.root)))
                    self.replay_frames += 1
                return super().screencap()

            def click(self, x, y):
                test.assertTrue(1203 <= x <= 1204 and 72 <= y <= 73)
                self.inputs += 1
                self.replaying = False
                self.image = experience
                return True

        controller = Pages(initial)

        class Probe(CustomAction):
            def run(self, context, argv):
                root = context.get_task_job().job_id
                context.override_pipeline({
                    "结算-羁绊快速复核": {"pre_delay": 0},
                    "结算-等待羁绊出现": {"pre_delay": 0},
                    bond.GUARD_NODE: {"attach": {
                        "positive_attempts": 1, "positive_timeout_ms": 1000,
                        "page_wait_attempts": 6, "page_wait_timeout_ms": 3000,
                    }},
                })
                test.assertTrue(bond.read_bond_page(context, initial))
                test.assertFalse(bond.read_any_bond_max(context, initial))
                test.assertEqual(bond.read_bond_max(context, initial), ())
                test.assertFalse(bond.read_bond_upgrade(context, initial))
                test.assertFalse(bond.read_bond_page(context, early))
                test.assertFalse(bond.read_bond_upgrade(context, early))
                test.assertTrue(bond.read_bond_upgrade(context, panel))
                for mode in ("all", "slots"):
                    with test.subTest(mode=mode):
                        bond.bond_stops.finish(root)
                        bond.bond_stops.begin(root)
                        bond.bond_stops.battle_started(root, 1, another_battle_planned=True)
                        bond.bond_stops.set_formation(root, [
                            {"slot": i, "status": "identified", "is_support": i == 2}
                            for i in range(1, 7)])
                        context.override_pipeline({bond.CONFIG_NODE: {"attach": {
                            "native_bond_stop_mode": mode,
                            **{f"native_bond_stop_slot_{i}": i == 6 for i in range(1, 7)},
                        }}})
                        controller.replaying = True
                        controller.replay_frames = 0
                        controller.observations = []
                        controller.root = root
                        before = controller.inputs
                        errors = []
                        read_maxima = bond._read_maxima

                        def record_maxima(*args):
                            try:
                                return read_maxima(*args)
                            except ValueError as exc:
                                errors.append(str(exc))
                                raise

                        with patch.object(bond, "_read_maxima", side_effect=record_maxima), \
                                patch.object(bond, "_save_evidence", side_effect=lambda c, r: r):
                            stopped = bond.inspect_victory_settlement(
                                context, NS(ok=True, reason="victory"), context.tasker.controller)
                        test.assertIsNone(stopped)
                        test.assertIn("bond_result_page_not_visible", errors)
                        test.assertTrue(any(phase == 1 for phase, _, _ in controller.observations))
                        test.assertTrue(any(phase == 3 for phase, _, _ in controller.observations))
                        for phase, inputs, deferred_state in controller.observations:
                            if phase < 3:
                                test.assertEqual(inputs, before)
                                test.assertIsNone(deferred_state)
                        deferred = bond.bond_stops.deferred(root)
                        test.assertIsNotNone(deferred)
                        test.assertTrue(bond.read_bond_page(context, deferred.image))
                        test.assertTrue(bond.read_bond_upgrade(context, deferred.latest_image))
                        test.assertEqual(controller.inputs, before)
                        test.assertTrue(context.run_task("结算-羁绊升级继续").status.succeeded)
                        test.assertEqual(controller.inputs, before + 1)
                        test.assertTrue(context.run_recognition("战斗_经验结算页", experience).hit)
                        test.assertFalse(context.run_recognition("结算-羁绊升级继续", experience).hit)
                        test.assertIsNone(bond.inspect_bond_stop(context, experience))
                        test.assertIsNone(bond.bond_stops.get(root))
                        bond.bond_stops.battle_started(root, 2, keep_formation=True)
                        test.assertIsNone(bond.bond_stops.deferred(root))
                        rechecked = bond.inspect_bond_stop(context, image("jp_bond_slot5_max.png"))
                        test.assertEqual(rechecked.reason, "bond_max")
                        test.assertEqual(rechecked.battle, 2)
                        test.assertEqual(controller.inputs, before + 1)
                report["passed"] = True
                return True

        self.run_probe(controller, Probe(), report)

    def run_probe(self, controller, probe, report):
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
            resource.register_custom_action("probe", probe)
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
