"""The real repeat action must finish a final upgrade and reach its next task."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
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
from auto_battle_repeat_action import AutoBattleRepeatAction


def image(name):
    return cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/bond_stop" / name, np.uint8), cv2.IMREAD_COLOR)


class FinalBondSettlementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_final_upgrade_finishes_repeat_and_runs_next_task(self):
        self.scenario(maximum=False)

    def test_explicit_max_on_final_battle_stops_before_next_task(self):
        self.scenario(maximum=True)

    def scenario(self, *, maximum):
        panel = image("jp_bond_upgrade_panel.png")
        experience = image("jp_experience_result.png")
        test = self
        observed = {"settlements": 0, "tail": False}

        class Pages(MemoryController):
            def click(self, x, y):
                test.assertTrue(1203 <= x <= 1204 and 72 <= y <= 73)
                self.inputs += 1
                self.image = experience
                return True

        controller = Pages(image("jp_bond_slot5_max.png") if maximum else panel)

        class Settlement(CustomAction):
            def run(self, context, argv):
                root = context.get_task_job().job_id
                observed["root"] = root
                observed["settlements"] += 1
                test.assertTrue(bond.bond_stops.is_last_battle(root))
                test.assertFalse(bond.bond_stops.can_defer(root))
                if maximum:
                    # Exercise the actual StopTask exit so the outer tail must not run.
                    context.run_task(bond.GUARD_NODE)
                    return False
                stopped = bond.inspect_victory_settlement(
                    context, NS(ok=True, reason="victory"), context.tasker.controller)
                test.assertIsNone(stopped)
                test.assertIsNone(bond.bond_stops.deferred(root))
                continuation = bond.bond_stops.upgrade_continuation(root)
                test.assertIsNotNone(continuation)
                test.assertEqual(continuation.reason, "bond_upgrade_final")
                test.assertTrue(context.run_task("结算-羁绊升级继续").status.succeeded)
                test.assertEqual(controller.inputs, 1)
                test.assertTrue(context.run_recognition("战斗_经验结算页", experience).hit)
                test.assertFalse(context.run_recognition(bond.GUARD_NODE, experience).hit)
                test.assertIsNone(bond.bond_stops.get(root))
                return True

        class Tail(CustomAction):
            def run(self, context, argv):
                observed["tail"] = True
                return True

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "pipeline").mkdir()
            nodes = {
                bond.CONFIG_NODE: {"attach": {"native_bond_stop_mode": "all"}},
                bond.GUARD_NODE: {"enabled": True},
                "test_repeat": {
                    "action": {"type": "Custom", "param": {
                        "custom_action": "auto_battle_repeat",
                        "custom_action_param": {
                            "battle_count": 1, "battle_entry_node": "test_final_settlement",
                            "reset_hit_nodes": [],
                        },
                    }},
                    "next": ["parent_tail"], "on_error": [],
                },
                "test_final_settlement": {
                    "action": {"type": "Custom", "param": {"custom_action": "settlement"}},
                    "next": [], "on_error": [],
                },
                "parent_tail": {
                    "action": {"type": "Custom", "param": {"custom_action": "tail"}},
                    "next": [], "on_error": [],
                },
            }
            (temp / "pipeline/test.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded)
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_action("auto_battle_repeat", AutoBattleRepeatAction())
            resource.register_custom_action("settlement", Settlement())
            resource.register_custom_action("tail", Tail())
            resource.register_custom_action("save_native_bond_stop", bond.SaveNativeBondStop())
            resource.register_custom_recognition("native_bond_max", bond.NativeBondMax())
            resource.register_custom_recognition("native_bond_upgrade_continue", bond.NativeBondUpgradeContinue())
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            with patch.object(bond, "Path", side_effect=lambda value: temp / value):
                job = tasker.post_task("test_repeat")
                deadline = time.monotonic() + 10
                try:
                    while not job.status.done and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(job.status.done, observed)
                    self.assertEqual(observed["settlements"], 1)
                    self.assertEqual(observed["tail"], not maximum)
                    self.assertEqual(controller.inputs, 0 if maximum else 1)
                    if maximum:
                        self.assertEqual(bond.bond_stops.get(job.job_id).reason, "bond_max")
                    else:
                        self.assertTrue(job.status.succeeded, observed)
                        self.assertIsNone(bond.bond_stops.get(job.job_id))
                finally:
                    tasker.post_stop().wait()
                    bond.bond_stops.stop_owner("")


if __name__ == "__main__":
    unittest.main()
