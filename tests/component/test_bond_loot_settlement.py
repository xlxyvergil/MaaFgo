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
from maa.context import ContextEventSink
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.event_sink import NotificationType
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

    def test_experience_page_after_missed_bond_reports_and_releases_guard(self):
        self.check_handoff(stage="experience")

    def test_third_miss_at_loot_page_runs_stop_exit_before_any_clicks(self):
        self.check_handoff(stage="loot", missed_battles=3)

    def check_handoff(self, *, stage, missed_battles=1):
        loot = image("jp_loot_header_and_next.png")
        experience = image("jp_experience_result.png")
        maximum = image("jp_bond_slot5_max.png")
        self.assertEqual(loot.shape, (720, 1280, 3))
        report = {}
        test = self
        notices = []
        stop_notices = []

        controller = MemoryController({"bond": maximum, "experience": experience, "loot": loot}[stage])

        class Notices(ContextEventSink):
            def on_node_action(self, context, noti_type, detail):
                if noti_type != NotificationType.Starting:
                    return
                if detail.name == bond.SKIP_NOTICE_NODE:
                    notices.append(detail.focus["Node.Action.Starting"])
                elif detail.name == "结算-羁绊停止出口":
                    stop_notices.append(detail.focus["Node.Action.Starting"])

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
                root = context.get_task_job().job_id
                victory = NS(ok=True, reason="victory")
                with patch.object(bond, "_save_evidence", side_effect=lambda _, result: result):
                    for battle_number in range(1, missed_battles + 1):
                        bond.bond_stops.battle_started(root, battle_number)
                        stopped = bond.inspect_victory_settlement(context, victory, context.tasker.controller)
                        if battle_number < missed_battles:
                            test.assertIsNone(stopped)
                            test.assertFalse(context.run_recognition("结算-羁绊满停止", experience).hit)
                    if missed_battles == 3:
                        context.run_task("结束战斗_不回主界面")
                if stage == "bond":
                    test.assertIsNotNone(stopped)
                    test.assertEqual(stopped.reason, "bond_max")
                    test.assertEqual(notices, [])
                elif missed_battles == 3:
                    test.assertIsNotNone(stopped)
                    test.assertEqual(stopped.reason, "bond_check_unconfirmed")
                    test.assertEqual(stopped.battle, 3)
                    test.assertEqual(len(notices), 2)
                    test.assertEqual(stop_notices, [stopped.message])
                else:
                    test.assertIsNone(stopped)
                    test.assertIsNone(bond.bond_stops.get(context.get_task_job().job_id))
                    # 后续真实结算守卫必须让位给经验页推进或战利品的“下一步”。
                    test.assertFalse(context.run_recognition("结算-羁绊满停止", experience).hit)
                    test.assertEqual(notices, ["第1场未能确认是否满羁绊，已跳过本场羁绊检查，继续结算。"])
                test.assertEqual(controller.inputs, 0)
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
            resource.register_custom_action("save_native_bond_stop", bond.SaveNativeBondStop())
            resource.register_custom_recognition("native_bond_max", bond.NativeBondMax())
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            self.assertIsNotNone(tasker.add_context_sink(Notices()))
            try:
                self.assertTrue(tasker.post_task("probe").wait().succeeded, report)
                self.assertTrue(report.get("passed"), report)
            finally:
                tasker.post_stop().wait()
                bond.bond_stops.stop_owner("")


if __name__ == "__main__":
    unittest.main()
