"""真实 MaaFramework/OCR 回放国服单骑出击队伍确认；内存控制器，不连接设备。"""
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


class SoloSortieRestrictionFrameworkTests(FrameworkResources, unittest.TestCase):
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

        resource.register_custom_action("solo_sortie_probe", Probe())
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("solo_sortie_probe", {
                "solo_sortie_probe": {"action": {"type": "Custom", "param": {
                    "custom_action": "solo_sortie_probe"
                }}, "on_error": []}
            }).wait()
            self.assertTrue(status.succeeded, errors)
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
            return outcomes[0]
        finally:
            tasker.post_stop().wait()

    def test_live_cn_solo_sortie_confirmation_is_restricted_formation(self):
        # 2026-11-20国服实机：单骑出击队伍确认。开始按钮和单骑横幅必须同时命中。
        self.assertTrue(self.hits(
            "main_story", "cn_solo_sortie_confirm.png", "cn", "进本-队伍确认"))
        self.assertTrue(self.hits(
            "main_story", "cn_solo_sortie_confirm.png", "cn", "主线限制-单骑出击"))

    def test_normal_support_party_is_not_marked_as_solo_sortie(self):
        # 普通队伍确认带固定助战，但没有单骑横幅或锁定槽；不能整关跳过 Chaldea 编队。
        self.assertTrue(self.hits(
            "strengthening", "jp_formation_start.png", "jp", "进本-队伍确认"))
        self.assertFalse(self.hits(
            "strengthening", "jp_formation_start.png", "jp", "主线限制-单骑出击"))


if __name__ == "__main__":
    unittest.main()
