"""国服强制剧情选项：真实边框/灰色跳过，动态布局只向内存控制器点击。"""
from pathlib import Path
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker
from test_main_story_framework import FrameworkResources, ScreenController, StoryRunner

ROOT = Path(__file__).resolve().parents[2]
NODE = "跳过剧情-强制选项"
FIXTURE = ROOT / "tests/fixtures/main_story/cn_forced_choices_generic.png"


def fixture():
    image = cv2.imdecode(np.fromfile(FIXTURE, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError(f"缺少匿名剧情选项截图：{FIXTURE}")
    return image


def scene(rows=(), *, skip="gray"):
    """保留实拍角点，拼接中段改变宽高；不缩放模板或边框。"""
    source = fixture()
    image = np.full_like(source, 32)
    if skip != "absent":
        patch = source[8:73, 1120:1274].copy()
        if skip == "enabled":
            enabled_path = ROOT / "tests/fixtures/main_story/cn_story_skip_enabled.png"
            patch = cv2.imdecode(np.fromfile(enabled_path, np.uint8), cv2.IMREAD_COLOR)
            if patch is None:
                raise AssertionError("缺少国服真实白色跳过按钮裁图")
        elif skip == "bright":
            patch = np.clip(patch.astype(np.float32) * 3.0, 0, 255).astype(np.uint8)
        image[8:73, 1120:1274] = patch
    original = source[164:260, 150:1130]
    for x, y, width, height in rows:
        # The side strips preserve the chamfered corners. Fill the center from
        # real black option pixels; recognition must not depend on its wording.
        row = np.zeros((height, width, 3), np.uint8)
        row[:48, :100] = original[:48, :100]
        row[-48:, :100] = original[-48:, :100]
        row[:48, -80:] = original[:48, -80:]
        row[-48:, -80:] = original[-48:, -80:]
        row[:8, 100:-80] = original[:8, 400:401]
        row[-8:, 100:-80] = original[-8:, 400:401]
        image[y:y+height, x:x+width] = row
    return image


class GenericDialogueFrameworkTests(FrameworkResources, unittest.TestCase):
    def probe(self, resource, image, expected_row=None, *, node=NODE):
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        report = {}
        self._probe_number = getattr(self, "_probe_number", 0) + 1
        probe_name = f"generic_choice_probe_{self._probe_number}"

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    runner = StoryRunner(context)
                    result = runner.reco(node, image)
                    report["hit"] = bool(result)
                    if result:
                        runner.action(node, result)
                    return True
                except Exception as exc:
                    report["error"] = repr(exc)
                    return False

        resource.register_custom_action(probe_name, Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            result = tasker.post_task(probe_name, {
                node: {"pre_delay": 0, "post_delay": 0},
                probe_name: {"action": {"type": "Custom", "param": {
                    "custom_action": probe_name
                }}, "on_error": []},
            }).wait()
            self.assertTrue(result.succeeded, report)
            self.assertEqual(report.get("hit"), expected_row is not None, report)
            self.assertEqual(len(controller.clicks), int(expected_row is not None), report)
            if expected_row is not None:
                x, y, width, height = expected_row
                click_x, click_y = controller.clicks[0]
                self.assertTrue(x+15 < click_x < x+width-15, controller.clicks)
                self.assertTrue(y+10 < click_y < y+height-10, controller.clicks)
            self.assertEqual(controller.swipes, [])
        finally:
            tasker.post_stop().wait()

    def test_real_screenshot_selects_first_option(self):
        self.probe(self.resource("cn"), fixture(), (150, 164, 980, 96))

    def test_number_position_width_and_multiline_height_are_not_fixed(self):
        resource = self.resource("cn")
        layouts = (
            ((235, 218, 780, 96),),
            ((120, 125, 1020, 96), (245, 295, 650, 96)),
            ((100, 110, 1080, 96), (180, 250, 900, 96), (260, 390, 720, 96)),
            ((310, 170, 560, 144), (150, 350, 980, 96)),
        )
        for rows in layouts:
            with self.subTest(rows=rows):
                self.probe(resource, scene(rows), rows[0])

    def test_skip_must_be_gray_and_present(self):
        resource = self.resource("cn")
        rows = ((150, 180, 980, 96),)
        for skip in ("absent", "bright", "enabled"):
            with self.subTest(skip=skip):
                self.probe(resource, scene(rows, skip=skip))

    def test_requires_both_borders_on_same_option(self):
        resource = self.resource("cn")
        intact = scene(((150, 180, 980, 96),))
        left_missing = intact.copy()
        left_missing[178:278, 145:255] = 32
        right_missing = intact.copy()
        right_missing[178:278, 1045:1135] = 32
        wrong_row = scene()
        wrong_row[180:276, 150:250] = intact[180:276, 150:250]
        wrong_row[330:426, 1050:1130] = intact[180:276, 1050:1130]
        for name, image in (("left_missing", left_missing), ("right_missing", right_missing),
                            ("edges_on_different_rows", wrong_row), ("skip_only", scene())):
            with self.subTest(name=name):
                self.probe(resource, image)

    def test_does_not_click_an_upper_left_border_using_a_lower_right_border(self):
        resource = self.resource("cn")
        image = scene(((150, 180, 980, 96), (150, 330, 980, 96)))
        image[178:278, 1045:1135] = 32
        # The first left corner is incomplete. Its missing right corner must
        # not be borrowed from the lower row to permit an unsafe upper click.
        self.probe(resource, image)

    def test_map_and_plain_dark_popup_do_not_trigger(self):
        resource = self.resource("cn")
        image = scene()
        image[170:300, 150:1130] = 0
        self.probe(resource, image)
        map_path = ROOT / "tests/fixtures/main_story/cn_tiamat_map_absent.png"
        image = cv2.imdecode(np.fromfile(map_path, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)
        self.probe(resource, image)
        self.probe(resource, image, node="跳过剧情-灰色继续")

    def test_gray_skip_with_continue_marker_advances_inside_dialogue(self):
        image = cv2.imdecode(np.fromfile(
            ROOT / "tests/fixtures/main_story/cn_forced_choice_wait.png", np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)
        self.probe(self.resource("cn"), image, (400, 550, 420, 130), node="跳过剧情-灰色继续")

    def test_continue_requires_gray_skip_and_actual_marker(self):
        resource = self.resource("cn")
        source = cv2.imdecode(np.fromfile(
            ROOT / "tests/fixtures/main_story/cn_forced_choice_wait.png", np.uint8), cv2.IMREAD_COLOR)
        missing_marker = source.copy()
        missing_marker[630:715, 1185:1275] = 32
        enabled_skip = source.copy()
        enabled_skip[8:73, 1120:1274] = cv2.imdecode(np.fromfile(
            ROOT / "tests/fixtures/main_story/cn_story_skip_enabled.png", np.uint8), cv2.IMREAD_COLOR)
        missing_skip = source.copy()
        missing_skip[:80, 1120:] = 32
        for label, image in (("missing_marker", missing_marker), ("enabled_skip", enabled_skip),
                             ("missing_skip", missing_skip), ("expanded_choices", fixture())):
            with self.subTest(label=label):
                self.probe(resource, image, node="跳过剧情-灰色继续")


if __name__ == "__main__":
    unittest.main()
