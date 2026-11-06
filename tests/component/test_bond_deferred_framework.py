"""Real guard/StopTask routing for a recognized bond-upgrade obstruction.

Only the visual result is mocked here; retained-image matcher regression lives
in test_bond_result_images.py. All task execution uses an in-memory controller.
"""
import copy
import json
import os
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


class BondDeferredFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def scenario(self, *, mode="slot", planned=True, bad_mapping=False, recognition_error=False, targets=()):
        source = json.loads((ROOT / "assets/resource/base/pipeline/原生自动战斗调度.json").read_text("utf-8"))
        nodes = {name: copy.deepcopy(source[name]) for name in (
            action.GUARD_NODE, "结算-羁绊停止出口", "结算-羁绊停止异常出口",
        )}
        nodes[action.GUARD_NODE]["enabled"] = True
        nodes[action.CONFIG_NODE] = {"attach": {
            "native_bond_stop_mode": mode, "native_bond_stop_slot": 6,
            **{f"native_bond_stop_slot_{i}": i in targets for i in range(1, 7)},
        }}
        nodes["root"] = {
            "action": {"type": "Custom", "param": {"custom_action": "setup"}},
            "next": ["[JumpBack]settlement", "parent_tail"], "on_error": [],
        }
        # Production JumpBack dispatch uses a bounded settlement entry. After
        # its return the parent must move on instead of selecting it again.
        nodes["settlement"] = {"max_hit": 1, "next": [action.GUARD_NODE, "settled"], "on_error": []}
        for name in ("settled", "parent_tail"):
            nodes[name] = {"action": {"type": "Custom", "param": {"custom_action": "record"}}}
        for node in nodes.values():
            node.update(pre_delay=0, post_delay=0, timeout=200, rate_limit=1)
        observed = {"seen": []}

        class Setup(CustomAction):
            def run(self, context, argv):
                root = context.get_task_job().job_id
                observed["root"] = root
                bond_stops.begin(root)
                bond_stops.battle_started(root, 1, another_battle_planned=planned)
                # Five own servants, support at formation slot4. Target slot6
                # therefore maps to result column5 without servant identities.
                slots = [{"slot": number, "status": "unknown", "is_support": number == 4}
                         for number in range(1, 7)]
                if bad_mapping:
                    slots[0]["is_support"] = None
                bond_stops.set_formation(root, slots)
                return True

        class Record(CustomAction):
            def run(self, context, argv):
                observed["seen"].append(argv.node_name)
                return True

        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            (temp / "pipeline").mkdir()
            (temp / "pipeline/test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            resource = Resource()
            resource.use_cpu()
            resource.register_custom_recognition("native_bond_max", action.NativeBondMax())
            resource.register_custom_action("save_native_bond_stop", action.SaveNativeBondStop())
            resource.register_custom_action("setup", Setup())
            resource.register_custom_action("record", Record())
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            controller = MemoryController(np.zeros((720, 1280, 3), np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            page_side_effect = ValueError("bond_result_recognition_unavailable:page") if recognition_error else None
            with patch.object(action, "read_bond_page", return_value=False, side_effect=page_side_effect), \
                 patch.object(action, "read_bond_upgrade", return_value=True) as upgrade, \
                 patch.object(action, "Path", side_effect=lambda value: temp / value):
                job = tasker.post_task("root")
                deadline = time.monotonic() + 5
                try:
                    while not job.status.done and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(job.status.done, "deferred guard callback did not finish")
                    self.assertTrue(job.status.succeeded, observed)
                    self.assertEqual(observed["root"], job.job_id)
                    stopped = bond_stops.get(job.job_id)
                    deferred = bond_stops.deferred(job.job_id)
                    allowed = (mode in {"all", "slot", "slots"} and planned and not recognition_error
                               and (mode == "all" or not bad_mapping))
                    self.assertEqual(bool(stopped), not allowed)
                    self.assertEqual(bool(deferred), allowed)
                    self.assertEqual(observed["seen"], ["settled", "parent_tail"] if allowed else [])
                    result = deferred if allowed else stopped
                    self.assertEqual(result.reason, "bond_upgrade_deferred" if allowed else "bond_check_unconfirmed")
                    self.assertTrue(Path(result.screenshot_path).is_file())
                    self.assertFalse(result.screenshot_error)
                    self.assertTrue(Path(result.screenshot_path).is_relative_to(temp))
                    self.assertEqual(controller.inputs, 0)
                    if recognition_error:
                        # A generic recognition failure must not be reclassified
                        # as an upgrade, even when another battle is planned.
                        upgrade.assert_not_called()
                finally:
                    tasker.post_stop().wait()
                    bond_stops.finish(job.job_id)

    def test_selected_slot_with_remaining_battle_returns_through_jumpback_to_parent_tail(self):
        self.scenario(mode="slot", planned=True)

    def test_selected_slot_on_last_battle_stops_without_parent_tail(self):
        self.scenario(mode="slot", planned=False)

    def test_multiple_slots_with_remaining_battle_defer(self):
        self.scenario(mode="slots", targets=(1, 6), planned=True)

    def test_multiple_slots_on_last_battle_stop(self):
        self.scenario(mode="slots", targets=(1, 6), planned=False)

    def test_all_mode_with_remaining_battle_returns_through_jumpback(self):
        self.scenario(mode="all", planned=True)

    def test_all_mode_on_last_battle_stops_without_parent_tail(self):
        self.scenario(mode="all", planned=False)

    def test_all_mode_deferral_does_not_require_per_slot_mapping(self):
        self.scenario(mode="all", planned=True, bad_mapping=True)

    def test_mapping_failure_cannot_use_upgrade_deferral(self):
        self.scenario(mode="slot", planned=True, bad_mapping=True)

    def test_ordinary_recognition_failure_cannot_use_upgrade_deferral(self):
        self.scenario(mode="slot", planned=True, recognition_error=True)


if __name__ == "__main__":
    unittest.main()
