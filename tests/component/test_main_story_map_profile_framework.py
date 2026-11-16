"""日服报错实拍标题与关闭按钮；内存控制器验证识别及居中落点。"""
from pathlib import Path
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker

from test_main_story_framework import FrameworkResources, ScreenController, StoryRunner
from main_story_recognition import MainStoryNext, load_templates

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "主线-御主资料返回"


class MapProfileFrameworkTests(FrameworkResources, unittest.TestCase):
    def test_actual_jp_header_requires_title_and_close_and_clicks_top_left(self):
        self.check_profile_header("jp")

    def test_actual_cn_header_requires_title_and_close_and_clicks_top_left(self):
        self.check_profile_header("cn")

    def check_profile_header(self, server):
        path = ROOT / f"tests/fixtures/main_story/{server}_master_profile_header.png"
        source = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        resource = self.resource(server)
        for variant, expected in (("both", True), ("title_only", False), ("close_only", False)):
            with self.subTest(variant=variant):
                image = source.copy()
                if variant == "title_only":
                    image[:100, :230] = 0
                if variant == "close_only":
                    image[:95, 500:] = 0
                controller = ScreenController(image)
                self.assertTrue(controller.post_connection().wait().succeeded)
                hits, errors = [], []

                class Probe(CustomAction):
                    def run(self, context, argv):
                        try:
                            r = StoryRunner(context)
                            result = r.reco(PROFILE, image)
                            hits.append(bool(result))
                            if result:
                                r.action(PROFILE, result)
                            return True
                        except Exception as exc:
                            errors.append(repr(exc))
                            return False

                resource.register_custom_action("profile_probe", Probe())
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                node = {"action": {"type": "Custom", "param": {"custom_action": "profile_probe"}}}
                try:
                    self.assertTrue(tasker.post_task("profile_probe", {"profile_probe": node}).wait().succeeded, errors)
                    self.assertEqual(hits, [expected])
                    self.assertEqual(len(controller.clicks), int(expected))
                    for x, y in controller.clicks:
                        self.assertTrue(0 <= x < 230 and 0 <= y < 100)
                    self.assertEqual(controller.swipes, [])
                finally:
                    tasker.post_stop().wait()

    def test_center_swipe_starts_on_marker_instead_of_obscured_click_point(self):
        for server in ("cn", "jp"):
            with self.subTest(server=server):
                resource = self.resource(server)
                resource.register_custom_recognition("main_story_next", MainStoryNext())
                template, _ = load_templates(server)
                marker = cv2.resize(template, (round(template.shape[1] * 2 / 3),
                                               round(template.shape[0] * 2 / 3)), interpolation=cv2.INTER_AREA)
                image = np.zeros((720, 1280, 3), np.uint8)
                h, w = marker.shape[:2]
                image[368:368+h, 89:89+w] = marker
                controller = ScreenController(image)
                self.assertTrue(controller.post_connection().wait().succeeded)
                boxes, errors = {}, []

                class Probe(CustomAction):
                    def run(self, context, argv):
                        try:
                            r = StoryRunner(context)
                            click = r.reco("主线-地图NEXT", image)
                            drag = r.reco("主线-地图NEXT标记", image)
                            boxes["click"], boxes["drag"] = list(click.box), list(drag.box)
                            r.action("巡-找到目标-设为中心", drag)
                            return True
                        except Exception as exc:
                            errors.append(repr(exc))
                            return False

                resource.register_custom_action("marker_probe", Probe())
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                node = {"action": {"type": "Custom", "param": {"custom_action": "marker_probe"}}}
                try:
                    self.assertTrue(tasker.post_task("marker_probe", {"marker_probe": node}).wait().succeeded, errors)
                    self.assertEqual(boxes["drag"], [89, 368, w, h])
                    self.assertGreater(boxes["click"][1], 368+h)
                    self.assertEqual(controller.clicks, [])
                    self.assertEqual(len(controller.swipes), 1)
                    x1, y1, x2, y2 = controller.swipes[0]
                    self.assertTrue(89 <= x1 <= 89+w and 368 <= y1 <= 368+h)
                    self.assertTrue(632 <= x2 <= 655 and 349 <= y2 <= 370)
                finally:
                    tasker.post_stop().wait()


if __name__ == "__main__":
    unittest.main()
