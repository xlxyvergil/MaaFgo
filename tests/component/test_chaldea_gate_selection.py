"""Replay real daily-quest rows through MaaFramework without a device."""

import json
import unittest

import cv2
import numpy as np
from maa.tasker import Tasker

from test_main_story_framework import ROOT, FrameworkResources, ScreenController


class ChaldeaGateSelectionTests(FrameworkResources, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        options_path = ROOT / "assets/options/迦勒底之门-副本类型.json"
        cls.options = json.loads(options_path.read_text(encoding="utf-8"))["option"]

    def select(self, fixture, option, kind, server="cn", hide_lower_row=False):
        path = ROOT / "tests/fixtures/chaldea_gate" / fixture
        crop = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(crop)
        screen = np.zeros((720, 1280, 3), dtype=np.uint8)
        height, width = crop.shape[:2]
        screen[:height, -width:] = crop
        if hide_lower_row:
            screen[490:] = 0

        group = self.options[f"chaldea_gate_quest_subtype_{kind}"]
        case = next(case for case in group["cases"] if case["name"] == option)
        selector = "迦门-选择修炼场副本" if kind == "training" else "迦门-选择种火副本"
        override = case["pipeline_override"] | {
            "测试-迦门选择": {"next": [selector], "on_error": [], "timeout": 400},
            selector: {"next": [], "on_error": [], "pre_delay": 0, "post_delay": 0},
        }
        controller = ScreenController(screen)
        self.assertTrue(controller.post_connection().wait().succeeded)
        resource = self.resource(server)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("测试-迦门选择", override).wait()
            self.assertEqual(status.succeeded, bool(controller.clicks))
            self.assertEqual(controller.swipes, [])
            return controller.clicks
        finally:
            tasker.post_stop().wait()

    def assert_selected_row(self, clicks, top, bottom):
        self.assertEqual(len(clicks), 1)
        x, y = clicks[0]
        self.assertTrue(850 < x < 980, (x, y))
        self.assertTrue(top < y < bottom, (x, y))

    def test_ember_checks_level_and_ordered_class(self):
        fixture = "cn_ember_60.png"
        self.assert_selected_row(self.select(fixture, "弓术60", "ember"), 493, 633)
        self.assertEqual(self.select(fixture, "弓术60", "ember", hide_lower_row=True), [])
        reversed_fixture = "cn_reversed_60.png"
        self.assert_selected_row(self.select(reversed_fixture, "骑剑60", "ember"), 517, 657)
        self.assertEqual(self.select(reversed_fixture, "剑骑60", "ember"), [])

    def test_training_selects_80_and_rejects_60_as_ember(self):
        fixture = "cn_training_60_and_80.png"
        self.assert_selected_row(self.select(fixture, "枪80", "training"), 522, 663)
        self.assertEqual(self.select(fixture, "枪杀60", "ember"), [])

    def test_japanese_level_prefix_and_list_title(self):
        clicks = self.select("jp_ember_60.png", "弓术60", "ember", server="jp")
        self.assert_selected_row(clicks, 160, 301)
