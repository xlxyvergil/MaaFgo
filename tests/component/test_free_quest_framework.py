"""真实 MaaFramework 多行 Free Quest 识别；只使用内存画面。"""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from free_quest_action import FreeQuestAction, FreeQuestRunner
from free_quest_recognition import FreeQuestBadge
from test_main_story_framework import FrameworkResources, ScreenController


class FreeQuestFrameworkTests(FrameworkResources, unittest.TestCase):
    def test_real_badge_digit_is_not_mistaken_for_outer_ring_zero(self):
        resource = self.resource("jp")
        resource.register_custom_recognition("free_quest_badge", FreeQuestBadge())
        badge = cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/free_quest_badge_1.png", np.uint8), 1)
        image = np.zeros((720, 1280, 3), np.uint8)
        h, w = badge.shape[:2]
        image[200:200+h, 800:800+w] = badge
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        results = []

        class Probe(CustomAction):
            def run(self, ctx, argv):
                results.append(FreeQuestRunner(ctx.clone()).reco("Free-地图数字", image))
                return True

        resource.register_custom_action("free_digit_probe", Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            self.assertTrue(tasker.post_task("free_digit_probe", {"free_digit_probe": {
                "action": {"type": "Custom", "param": {"custom_action": "free_digit_probe"}}, "on_error": []
            }}).wait().succeeded)
            self.assertTrue(results[0])
            digit = results[0].raw_detail["best"]["detail"]["digit"]["best"]
            self.assertEqual(digit["text"], "1")
            self.assertGreater(digit["score"], .99)
            self.assertEqual(controller.clicks, [])
        finally:
            tasker.post_stop().wait()

    def test_single_map_step_preserves_next_patrol_initialization(self):
        resource = self.resource("jp")
        controller = ScreenController(np.zeros((720, 1280, 3), np.uint8))
        self.assertTrue(controller.post_connection().wait().succeeded)
        checks = []

        class Probe(CustomAction):
            def run(self, ctx, argv):
                runner = FreeQuestRunner(ctx.clone())
                before = list(runner.patrol())
                runner.swipe_map("巡-初始化左滑")
                checks.extend((before == list(runner.patrol()), before.count("巡-初始化左滑") == 5))
                return True

        resource.register_custom_action("free_patrol_probe", Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            self.assertTrue(tasker.post_task("free_patrol_probe", {"free_patrol_probe": {
                "action": {"type": "Custom", "param": {"custom_action": "free_patrol_probe"}}, "on_error": []
            }}).wait().succeeded)
            self.assertEqual(checks, [True, True])
            self.assertEqual(len(controller.swipes), 1)
        finally:
            tasker.post_stop().wait()

    def test_clear_is_scoped_to_row_and_dynamic_roi_does_not_leak(self):
        for server in ("jp", "cn"):
            with self.subTest(server=server):
                resource = self.resource(server)
                def template(node):
                    name = resource.get_node_data(node)["recognition"]["param"]["template"][0]
                    path = ROOT / "assets/resource" / server / "image" / name
                    if not path.exists():
                        path = ROOT / "assets/resource/base/image" / name
                    return cv2.imdecode(np.fromfile(path, np.uint8), 1)
                label, clear = template("Free-列表标记"), template("Free-完成角标")
                image = np.zeros((720, 1280, 3), np.uint8)
                for y in (157, 357):
                    h, w = label.shape[:2]
                    image[y:y+h, 650:650+w] = label
                h, w = clear.shape[:2]
                image[451:451+h, 642:642+w] = clear
                controller = ScreenController(image)
                self.assertTrue(controller.post_connection().wait().succeeded)
                checks = []

                class Probe(CustomAction):
                    def run(self, ctx, argv):
                        runner = FreeQuestRunner(ctx.clone())
                        before = runner.ctx.get_node_data("Free-列表标记")["recognition"]["param"]["roi"]
                        for _ in range(2):
                            rows = list(runner.rows(image))
                            checks.append([(row.box.y, cleared) for row, cleared in rows])
                        after = runner.ctx.get_node_data("Free-列表标记")["recognition"]["param"]["roi"]
                        checks.append(before == after)
                        return True

                resource.register_custom_action("free_rows_probe", Probe())
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                try:
                    status = tasker.post_task("free_rows_probe", {"free_rows_probe": {
                        "action": {"type": "Custom", "param": {"custom_action": "free_rows_probe"}},
                        "on_error": [],
                    }}).wait()
                    self.assertTrue(status.succeeded)
                    self.assertEqual(checks, [[(157, False), (357, True)], [(157, False), (357, True)], True])
                    self.assertEqual(controller.clicks, [])
                    self.assertEqual(controller.swipes, [])
                    self.assertEqual(resource.get_node_data("自动清free本调度")["on_error"], [])
                finally:
                    tasker.post_stop().wait()


if __name__ == "__main__":
    unittest.main()
