"""国服真实续关及撤退确认的识别回归；不连接设备或点击界面。"""
from pathlib import Path
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker

from test_main_story_framework import FrameworkResources, ScreenController
from battle.core.enums import Scene
from battle.perception.perception import detect_scene
from battle.runtime.runtime import AutoBattleRuntime

ROOT = Path(__file__).resolve().parents[2]


class CnDefeatFrameworkTests(FrameworkResources, unittest.TestCase):
    def fixture(self, relative):
        path = ROOT / "tests/fixtures" / relative
        image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image, relative)
        return image

    def probe(self, image, callback, server="cn"):
        resource = self.resource(server)
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        outcomes, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    outcomes.append(callback(context, controller))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("cn_defeat_probe", Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            result = tasker.post_task("cn_defeat_probe", {
                "cn_defeat_probe": {"action": {"type": "Custom", "param": {
                    "custom_action": "cn_defeat_probe"
                }}, "on_error": []}
            }).wait()
            self.assertTrue(result.succeeded, errors)
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
            return outcomes[0]
        finally:
            tasker.post_stop().wait()

    def test_actual_cn_continue_screen_is_terminal_defeat_without_clicks(self):
        image = self.fixture("interlude/cn_defeat_continue.png")

        def callback(context, controller):
            leaf = context.run_recognition("战斗_失败_常规", image)
            scene = detect_scene(context, image)
            runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
            runtime.ctx, runtime.controller = context, controller
            with patch("battle.runtime.runtime._ANIMATION_TIMEOUT_S", 3):
                settled = runtime._wait_turn_settled()
            box = leaf.box if leaf else None
            return (bool(leaf and leaf.hit), scene, settled,
                    None if box is None else (box.x, box.y, box.w, box.h))

        hit, scene, settled, box = self.probe(image, callback)
        self.assertTrue(hit)
        self.assertEqual(scene, Scene.DEFEAT)
        self.assertTrue(settled)
        self.assertTrue(180 <= box[0] < 490 and 270 <= box[1] < 375, box)

    def test_foreground_confirmation_and_dimmed_continue_are_not_defeat(self):
        continue_image = self.fixture("interlude/cn_defeat_continue.png")
        spirit_image = self.fixture("interlude/cn_defeat_spirit_continue.png")
        negatives = (
            self.fixture("interlude/cn_retreat_confirmation.png"),
            self.fixture("main_story/cn_spirit_confirmation_retreat_negative.png"),
            self.fixture("interlude/cn_list_direct.png"),
            (continue_image * .55).astype(np.uint8),
            (spirit_image * .55).astype(np.uint8),
        )

        def callback(context, controller):
            return [bool((result := context.run_recognition("战斗_失败", frame))
                         and result.hit) for frame in negatives]

        self.assertEqual(self.probe(continue_image, callback), [False] * len(negatives))

    def test_existing_cn_spirit_stone_layout_remains_terminal_defeat(self):
        image = self.fixture("interlude/cn_defeat_spirit_continue.png")

        def callback(context, controller):
            result = context.run_recognition("战斗_失败_常规", image)
            box = result.box if result else None
            return (bool(result and result.hit), detect_scene(context, image),
                    None if box is None else (box.x, box.y, box.w, box.h))

        hit, scene, box = self.probe(image, callback)
        self.assertTrue(hit)
        self.assertEqual(scene, Scene.DEFEAT)
        self.assertTrue(500 <= box[0] < 800 and 350 <= box[1] < 455, box)

    def test_left_retreat_button_alone_does_not_mean_defeat(self):
        image = self.fixture("interlude/cn_defeat_continue.png")
        button_only = image.copy()
        button_only[90:170, 540:740] = 0

        def callback(context, controller):
            result = context.run_recognition("战斗_失败", button_only)
            return bool(result and result.hit)

        self.assertFalse(self.probe(button_only, callback))

    def test_actual_retreat_confirmation_and_existing_consumption_negative(self):
        image = self.fixture("interlude/cn_retreat_confirmation.png")
        negative = self.fixture("main_story/cn_spirit_confirmation_retreat_negative.png")
        synthetic = self.fixture("main_story/cn_retreat_confirmation_synthetic.png")

        def callback(context, controller):
            return [bool((result := context.run_recognition("主线-撤退确认", frame))
                         and result.hit) for frame in (image, negative, synthetic)]

        self.assertEqual(self.probe(image, callback), [True, False, True])

    def test_actual_normal_battle_animation_is_not_defeat(self):
        image = self.fixture("interlude/cn_battle_not_defeat.png")

        def callback(context, controller):
            result = context.run_recognition("战斗_失败", image)
            return bool(result and result.hit), detect_scene(context, image)

        hit, scene = self.probe(image, callback)
        self.assertFalse(hit)
        self.assertNotEqual(scene, Scene.DEFEAT)

    def test_cn_override_does_not_replace_jp_defeat_resource(self):
        resource = self.resource("jp")
        node = resource.get_node_data("战斗_失败_常规")
        self.assertEqual(node["recognition"]["type"], "TemplateMatch")
        self.assertEqual(node["recognition"]["param"]["template"], ["battle/scene_defeat.png"])


if __name__ == "__main__":
    unittest.main()
