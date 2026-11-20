"""真实 MaaFramework/OCR 回放进本教程浮层与任务完成结算；内存控制器，不连接设备。"""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from test_main_story_framework import FrameworkResources, ScreenController


class EntryOverlayFrameworkTests(FrameworkResources, unittest.TestCase):
    def hits(self, folder, name, server, node):
        image = cv2.imdecode(np.fromfile(ROOT / "tests/fixtures" / folder / name, np.uint8), 1)
        self.assertIsNotNone(image)
        resource = self.resource(server)
        outcomes, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    result = context.run_recognition(node, image)
                    outcomes.append(bool(result and result.hit))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("entry_overlay_probe", Probe())
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("entry_overlay_probe", {
                "entry_overlay_probe": {"action": {"type": "Custom", "param": {
                    "custom_action": "entry_overlay_probe"
                }}, "on_error": []}
            }).wait()
            self.assertTrue(status.succeeded, errors)
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
            return outcomes[0]
        finally:
            tasker.post_stop().wait()

    def test_cn_battle_tutorial_forward_is_clicked_leaf(self):
        self.assertTrue(self.hits(
            "main_story", "cn_battle_tutorial_forward.png", "cn", "进本-战斗教程前进"))

    def test_battle_screen_is_not_mistaken_for_tutorial_or_completion(self):
        for node in ("进本-战斗教程前进", "战斗_任务完成"):
            with self.subTest(node=node):
                self.assertFalse(self.hits(
                    "battle_preset_fallback_cn", "partial_cards.png", "cn", node))

    def test_cn_quest_complete_reward_is_victory_scene(self):
        self.assertTrue(self.hits(
            "main_story", "cn_quest_complete_reward.png", "cn", "战斗_任务完成"))


if __name__ == "__main__":
    unittest.main()
