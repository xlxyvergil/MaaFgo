"""国日服强化列表真实OCR回放；无设备、无触控。"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from strengthening_recognition import StrengtheningEntry, StrengtheningScan
from strengthening_action import StrengtheningRunner
from main_story_action import StoryStopped
from test_main_story_framework import FrameworkResources, ScreenController


class StrengtheningFrameworkTests(FrameworkResources, unittest.TestCase):
    def probe(self, image, callback, server="cn"):
        resource = self.resource(server)
        resource.register_custom_recognition("strengthening_entry", StrengtheningEntry())
        resource.register_custom_recognition("strengthening_scan", StrengtheningScan())
        outcomes, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    outcomes.append(callback(context))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("strengthening_probe", Probe())
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("strengthening_probe", {
                "strengthening_probe": {"action": {"type": "Custom", "param": {
                    "custom_action": "strengthening_probe"}}, "on_error": []}
            }).wait()
            self.assertTrue(status.succeeded, errors)
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
            return outcomes[0]
        finally:
            tasker.post_stop().wait()

    def fixture(self, name="cn_list.png"):
        return cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/strengthening" / name, np.uint8), 1)

    def test_jp_list_reads_discounted_and_regular_ap_without_defaulting_to_zero(self):
        image = self.fixture("jp_list.png")
        def check(ctx):
            runner = StrengtheningRunner(ctx)
            self.assertTrue(runner.panel(image))
            self.assertEqual(runner.list_label, "強化クエスト")
            result = ctx.run_recognition("强化-条目扫描", image)
            raw = result.best_result.detail
            report = json.loads(raw) if isinstance(raw, str) else raw
            self.assertEqual([e["ap_cost"] for e in report["entries"]], [11, 23, 23], report)
            self.assertEqual(report["uncertain_count"], 0, report)
            self.assertIn("沖田", report["entries"][0]["title"])
        self.probe(image, check, "jp")

    def test_cn_and_jp_list_titles_do_not_cross_match(self):
        for server, name in (("cn", "jp_list.png"), ("jp", "cn_list.png")):
            image = self.fixture(name)
            self.probe(image, lambda ctx: self.assertFalse(StrengtheningRunner(ctx).panel(image)), server)

    def test_jp_completed_servant_disappears_and_next_entries_keep_their_ap_costs(self):
        image = self.fixture("jp_list_after_completion.png")
        def check(ctx):
            self.assertTrue(StrengtheningRunner(ctx).panel(image))
            result = ctx.run_recognition("强化-条目扫描", image)
            raw = result.best_result.detail
            report = json.loads(raw) if isinstance(raw, str) else raw
            self.assertEqual([e["ap_cost"] for e in report["entries"]], [23, 23, 21], report)
            self.assertIn("玉藻", report["entries"][0]["title"])
            self.assertTrue(all("沖田" not in e["title"] for e in report["entries"]))
            self.assertEqual(report["uncertain_count"], 0, report)
        self.probe(image, check, "jp")

    def test_real_list_selects_first_unfinished_small_quest(self):
        def check(ctx):
            runner = StrengtheningRunner(ctx)
            self.assertTrue(runner.panel(self.fixture()))
            self.assertFalse(runner.on_map(self.fixture()))
            result = ctx.run_recognition("强化-条目扫描", self.fixture())
            raw = result.best_result.detail
            detail = json.loads(raw) if isinstance(raw, str) else raw
            self.assertEqual(len(detail["entries"]), 3, detail)
            self.assertEqual(detail["uncertain_count"], 0, detail)
            self.assertIn("织田信长", detail["entries"][0]["title"])
            self.assertEqual(detail["entries"][0]["box"][0], 950)
        self.probe(self.fixture(), check)

    def test_wrong_page_preserves_screen_and_stops(self):
        def check(ctx):
            with self.assertRaisesRegex(StoryStopped, "强化任务"):
                StrengtheningRunner(ctx).run()
        self.probe(np.zeros((720, 1280, 3), np.uint8), check)

    def test_real_jp_support_and_formation_start_resume_current_quest(self):
        for name in ("jp_support_start.png", "jp_formation_start.png"):
            with self.subTest(page=name):
                image = self.fixture(name)
                def check(ctx):
                    runner = StrengtheningRunner(ctx)
                    runner.limit = 1
                    runner.fight = Mock()
                    runner.settle = Mock()
                    runner.locate = Mock()
                    runner.enter = Mock()
                    with patch("strengthening_action.bond_stops.battle_started"):
                        self.assertIn("完成 1", runner.run())
                    runner.fight.assert_called_once()
                    runner.settle.assert_called_once()
                    runner.locate.assert_not_called()
                    runner.enter.assert_not_called()
                    self.assertEqual(runner.completed, 1)
                self.probe(image, check, "jp")

    def test_battle_and_download_dialog_are_not_mistaken_for_supported_start_pages(self):
        frames = (self.fixture("cn_download_error.png"),
                  cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/battle_preset_fallback_cn/main_after_cancel.png", np.uint8), 1))
        for image in frames:
            def check(ctx):
                runner = StrengtheningRunner(ctx)
                runner.fight = Mock()
                with self.assertRaisesRegex(StoryStopped, "助战选择或编队确认"):
                    runner.run()
                runner.fight.assert_not_called()
            self.probe(image, check)

    def test_quest_limit_is_isolated_and_validated(self):
        def check(ctx):
            ctx.override_pipeline({"强化-配置": {"attach": {"quest_limit": 2}},
                                   "主线-配置": {"attach": {"use_spirit_stone": True}}})
            runner = StrengtheningRunner(ctx)
            self.assertEqual(runner.limit, 2)
            self.assertFalse(runner.use_spirit_stone)
            self.assertTrue(ctx.get_node_data("主线-配置")["attach"]["use_spirit_stone"])
            ctx.override_pipeline({"强化-配置": {"attach": {"quest_limit": 0}}})
            with self.assertRaisesRegex(ValueError, "挑战次数上限"):
                StrengtheningRunner(ctx)
        self.probe(self.fixture(), check)

    def test_download_error_uses_existing_retry_button_with_limit(self):
        image = cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/strengthening/cn_download_error.png", np.uint8), 1)
        def check(ctx):
            runner = StrengtheningRunner(ctx)
            runner.action = Mock()
            for _ in range(3):
                self.assertTrue(runner.interrupt(image, entering=True))
            self.assertEqual(runner.action.call_count, 3)
            self.assertEqual(runner.action.call_args.args[0], "进本-断网重连")
            with self.assertRaisesRegex(StoryStopped, "重试连接3次"):
                runner.interrupt(image, entering=True)
            self.assertEqual(runner.action.call_count, 3)
            self.assertFalse(runner.panel(image))
        self.probe(image, check)
