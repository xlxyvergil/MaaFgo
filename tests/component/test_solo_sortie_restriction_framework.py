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
sys.path.insert(0, str(ROOT / "tests"))
from formation_test_support import battle_action, formation
from test_main_story_framework import FrameworkResources, ScreenController


class SoloSortieRestrictionFrameworkTests(FrameworkResources, unittest.TestCase):
    def recognize(self, folder, name, server, nodes, *, enabled=True, dimmed=False):
        image = cv2.imdecode(np.fromfile(ROOT / "tests/fixtures" / folder / name, np.uint8), 1)
        self.assertIsNotNone(image)
        if dimmed:
            image = cv2.convertScaleAbs(image, alpha=0.55)
        resource = self.resource(server)
        for name, recognizer in (
            ("formation_confirmation_lighting", formation.FormationConfirmationLighting()),
            ("formation_required_slots_filled", formation.FormationRequiredSlotsFilled()),
            ("formation_restriction_enabled", battle_action.FormationRestrictionEnabled()),
        ):
            self.assertTrue(resource.register_custom_recognition(name, recognizer))
        outcomes, errors = {}, []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    for node in nodes:
                        outcomes[node] = context.run_recognition(node, image)
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
                }}, "on_error": []},
                "进本-编队限制配置": {"attach": {"auto_handle_restrictions": enabled}},
            }).wait()
            self.assertTrue(status.succeeded, errors)
            for node in nodes:
                self.assertIsNotNone(outcomes[node], node)
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
            return outcomes
        finally:
            tasker.post_stop().wait()

    def test_live_cn_solo_sortie_confirmation_is_restricted_formation(self):
        outcomes = self.recognize("main_story", "cn_solo_sortie_confirm.png", "cn", (
            "进本-队伍确认", "主线限制-单骑出击", "进本-灰色变更配置", "进本-限制编队直接开战",
        ))
        for name, result in outcomes.items():
            self.assertTrue(result.hit, (name, result.raw_detail))
        self.assertEqual(outcomes["进本-限制编队直接开战"].box, outcomes["进本-队伍确认"].box)

    def test_normal_support_party_is_not_marked_as_solo_sortie(self):
        # 普通队伍确认带固定助战，但没有单骑横幅或锁定槽；不能整关跳过 Chaldea 编队。
        outcomes = self.recognize("strengthening", "jp_formation_start.png", "jp", (
            "进本-队伍确认页可开始", "主线限制-单骑出击", "进本-灰色变更配置", "进本-限制编队直接开战",
        ))
        self.assertTrue(outcomes["进本-队伍确认页可开始"].hit)
        for name in ("主线限制-单骑出击", "进本-灰色变更配置", "进本-限制编队直接开战"):
            self.assertFalse(outcomes[name].hit, (name, outcomes[name].raw_detail))

    def test_game_auto_formation_button_requires_lit_button(self):
        node = "主线限制-打开自动编队"
        for folder, name, server, dimmed, expected in (
            ("strengthening", "jp_formation_start.png", "jp", False, True),
            ("strengthening", "jp_formation_start.png", "jp", True, False),
            ("main_story", "cn_solo_sortie_confirm.png", "cn", False, False),
        ):
            with self.subTest(server=server, dimmed=dimmed):
                result = self.recognize(folder, name, server, (node,), dimmed=dimmed)[node]
                self.assertEqual(result.hit, expected, result.raw_detail)

    def test_dimmed_formation_does_not_start_battle_through_popup(self):
        outcomes = self.recognize("main_story", "cn_solo_sortie_confirm.png", "cn", (
            "进本-队伍确认", "进本-灰色变更配置", "进本-限制编队直接开战",
        ), dimmed=True)
        self.assertTrue(outcomes["进本-队伍确认"].hit)
        self.assertFalse(outcomes["进本-灰色变更配置"].hit)
        self.assertFalse(outcomes["进本-限制编队直接开战"].hit)

    def test_disabled_option_stops_before_restriction_recognition(self):
        name = "进本-限制编队直接开战"
        result = self.recognize("main_story", "cn_solo_sortie_confirm.png", "cn", (name,),
                                enabled=False)[name]
        self.assertFalse(result.hit)
        self.assertEqual([item["name"] for item in result.raw_detail], ["进本-编队限制已开启"])


if __name__ == "__main__":
    unittest.main()
