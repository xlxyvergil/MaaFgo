"""真实茶壶 UI 小裁区回归；只运行 OpenCV，不加载设备或游戏任务。"""
from pathlib import Path
import json
import unittest
from unittest.mock import patch

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
IMAGE_ROOT = ROOT / "assets/resource/base/image"
FIXTURE_ROOT = ROOT / "tests/fixtures/teapot"
THRESHOLD = 0.90


def read_image(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError(f"无法读取图像：{path}")
    return image


def template_score(image, template, roi, green_mask=False):
    x, y, width, height = roi
    region = image[y:y + height, x:x + width]
    needle = read_image(IMAGE_ROOT / template)
    if min(region.shape[:2]) == 0 or any(a < b for a, b in zip(region.shape[:2], needle.shape[:2])):
        return -1.0
    mask = np.any(needle != (0, 255, 0), axis=2).astype(np.uint8) * 255 if green_mask else None
    scores = cv2.matchTemplate(region, needle, cv2.TM_CCOEFF_NORMED, mask=mask)
    scores = np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1)
    return float(cv2.minMaxLoc(scores)[1])


def static_recognition_matches(image, recognition):
    """离线重放正式 Pipeline 的静态模板组合，避免复制业务状态机。"""
    kind, param = recognition["type"], recognition["param"]
    if kind == "And":
        return all(static_recognition_matches(image, item["recognition"])
                   for item in param["all_of"])
    if kind == "Or":
        return any(static_recognition_matches(image, item["recognition"])
                   for item in param["any_of"])
    if kind != "TemplateMatch":
        raise AssertionError(f"本测试仅支持静态模板识别，遇到 {kind}")
    return template_score(image, param["template"], param["roi"], param.get("green_mask", False)) >= param["threshold"]


class TeapotVisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads((FIXTURE_ROOT / "fixtures.json").read_text(encoding="utf-8"))
        cls.pipeline = json.loads((ROOT / "assets/resource/base/pipeline/原生战斗茶壶.json").read_text(encoding="utf-8"))

    def assemble(self, *parts):
        width, height = self.fixtures["canvasSize"]
        canvas = np.zeros((height, width, 3), dtype=np.uint8)
        for name in parts:
            part = self.fixtures["parts"][name]
            crop = read_image(FIXTURE_ROOT / part["image"])
            x, y, crop_width, crop_height = part["roi"]
            self.assertEqual(crop.shape[:2], (crop_height, crop_width))
            canvas[y:y + crop_height, x:x + crop_width] = crop
        return canvas

    def score(self, image, name, roi):
        return template_score(image, f"battle/teapot/{name}.png", roi, name == "entry_icons")

    def test_trial_full_empty_strip_rejects_real_item_and_transition_crops(self):
        combined = self.pipeline["茶壶-试炼无自动道具栏"]["recognition"]["param"]["all_of"]
        self.assertEqual(combined[0], "茶壶-识别编队页")
        recognition = combined[1]["recognition"]
        self.assertEqual(recognition["param"]["threshold"], .95)
        # The fixture is a second live frame, independent of the production
        # template's original frame. Only this crop is not a full-page proof.
        self.assertTrue(static_recognition_matches(self.assemble("trial_empty_auto_items"), recognition))
        for part in ("bar_tea_on", "bar_tea_off", "bar_storm_on", "bar_transition"):
            with self.subTest(part=part):
                self.assertFalse(static_recognition_matches(self.assemble(part), recognition))

    def test_entry_icons_ignore_background_colors_and_require_both_icons(self):
        recognition = self.pipeline["茶壶-识别交替过渡"]["recognition"]
        self.assertTrue(recognition["param"]["green_mask"])
        self.assertTrue(self.pipeline["茶壶-识别任意入口"]["recognition"]["param"]["any_of"][0]
                        ["recognition"]["param"]["green_mask"])
        original = self.assemble("bar_transition")
        yy, xx = np.indices(original.shape[:2])
        # 保留真实截图的圆形图标内部，只改周边和两图标间的背景。
        foreground = ((xx - 690.5)**2 + (yy - 678.5)**2 < 16.3**2)
        foreground |= ((xx - 734.5)**2 + (yy - 678.5)**2 < 16.3**2)
        for region in ("base", "cn"):
            with patch(__name__ + ".IMAGE_ROOT", ROOT / f"assets/resource/{region}/image"):
                for color in ((0, 0, 0), (255, 255, 255), (70, 20, 120), (10, 130, 200)):
                    with self.subTest(region=region, background=color):
                        changed = np.full_like(original, color)
                        changed[foreground] = original[foreground]
                        self.assertTrue(static_recognition_matches(changed, recognition))
                        missing_info = changed.copy()
                        missing_info[650:710, 665:712] = color
                        self.assertFalse(static_recognition_matches(missing_info, recognition))
                        missing_help = changed.copy()
                        missing_help[650:710, 713:765] = color
                        self.assertFalse(static_recognition_matches(missing_help, recognition))

    def test_cn_localized_toggle_overrides_distinguish_both_states(self):
        nodes = {"on": "茶壶-直接已开启", "off": "茶壶-直接已关闭"}
        for state in nodes:
            with self.subTest(state=state):
                image = self.assemble(f"cn_toggle_{state}")
                recognitions = {
                    key: self.pipeline[node]["recognition"]["param"]["all_of"][1]["recognition"]
                    for key, node in nodes.items()
                }
                # 国服中文开关不能用日服 ON/OFF 字样识别。
                self.assertFalse(static_recognition_matches(image, recognitions[state]))
                with patch(__name__ + ".IMAGE_ROOT", ROOT / "assets/resource/cn/image"):
                    self.assertTrue(static_recognition_matches(image, recognitions[state]))
                    other = "off" if state == "on" else "on"
                    self.assertFalse(static_recognition_matches(image, recognitions[other]))

    def test_cn_popup_ignores_left_storm_on_and_changing_tea_expiry(self):
        image = self.assemble("cn_popup_title", "cn_popup_storm_on", "cn_popup_tea_off")
        with patch(__name__ + ".IMAGE_ROOT", ROOT / "assets/resource/cn/image"):
            for text in ("1 day", "99 days", "00:00"):
                with self.subTest(expiry=text):
                    changed = image.copy()
                    changed[414:450, 775:855] = (20, 70, 110)
                    cv2.putText(changed, text, (776, 435), cv2.FONT_HERSHEY_SIMPLEX,
                                0.4, (0, 255, 255), 1)
                    self.assertGreaterEqual(self.score(changed, "toggle_off", [775, 365, 80, 85]), THRESHOLD)
                    self.assertLess(self.score(changed, "toggle_on", [775, 365, 80, 85]), THRESHOLD)

    def test_popup_action_guards_recognize_the_correct_tea_state(self):
        actions = {
            "on": self.pipeline["茶壶-弹窗开启状态并关闭"]["recognition"],
            "off": self.pipeline["茶壶-弹窗关闭状态并开启"]["recognition"],
        }
        for case in self.fixtures["popupCases"]:
            with self.subTest(state=case["name"]):
                image = self.assemble(*case["parts"])
                self.assertTrue(static_recognition_matches(image, actions[case["name"]]))
                opposite = "off" if case["name"] == "on" else "on"
                self.assertFalse(static_recognition_matches(image, actions[opposite]),
                                 "左侧风暴罐的 ON 不能被用于判断右侧茶壶状态")

    def test_popup_and_outer_bar_can_be_visible_together(self):
        image = self.assemble(*self.fixtures["popupCases"][1]["parts"])
        self.assertGreaterEqual(self.score(image, "popup_frame", [160, 75, 100, 100]), THRESHOLD)
        self.assertGreaterEqual(self.score(image, "bar_teapot_icon", [750, 640, 95, 80]), THRESHOLD)
        self.assertGreaterEqual(self.score(image, "bar_toggle_on", [840, 640, 95, 80]), THRESHOLD)
        self.assertLess(self.score(image, "formation_clear", [780, 94, 90, 60]), THRESHOLD,
                        "外层开关仍可见时，不能将被弹窗遮挡的页面视为可直接操作的编队页")

    def test_popup_frame_without_tea_icon_is_not_a_tea_dialog(self):
        image = self.assemble("popup_frame", "popup_tea_on", "popup_storm_on", "header_popup")
        self.assertGreaterEqual(self.score(image, "popup_frame", [160, 75, 100, 100]), THRESHOLD)
        self.assertLess(self.score(image, "popup_teapot_icon", [680, 365, 80, 70]), THRESHOLD)
        for name in ("茶壶-弹窗开启状态并关闭", "茶壶-弹窗关闭状态并开启"):
            self.assertFalse(static_recognition_matches(image, self.pipeline[name]["recognition"]),
                             "通用无字弹窗框不能替代右侧茶壶图标")

    def test_outer_bar_rejects_opposite_state_and_storm_can(self):
        storm_recognition = self.pipeline["茶壶-识别风暴罐"]["recognition"]
        for state in ("on", "off"):
            with self.subTest(state=state):
                image = self.assemble("bar_tea_" + state)
                other = "off" if state == "on" else "on"
                self.assertFalse(static_recognition_matches(image, storm_recognition),
                                 "茶壶帧不能被当作中性等待的风暴罐帧")
                self.assertGreaterEqual(self.score(image, "bar_teapot_icon", [750, 640, 95, 80]), THRESHOLD)
                self.assertGreaterEqual(self.score(image, "bar_toggle_" + state, [840, 640, 95, 80]), THRESHOLD)
                self.assertLess(self.score(image, "bar_toggle_" + other, [840, 640, 95, 80]), THRESHOLD)
        wind = self.assemble("bar_storm_on")
        self.assertTrue(static_recognition_matches(wind, storm_recognition))
        self.assertFalse(static_recognition_matches(self.assemble("bar_transition"), storm_recognition),
                         "渐变入口帧不能提供明确风暴罐证据")
        self.assertGreaterEqual(self.score(wind, "bar_storm_icon", [750, 640, 95, 80]), THRESHOLD)
        self.assertLess(self.score(wind, "bar_teapot_icon", [750, 640, 95, 80]), THRESHOLD,
                        "风暴罐 ON 不是茶壶 ON；必须先确认对应道具图标")

    def test_transition_frame_keeps_entry_presence_without_stable_item_state(self):
        image = self.assemble("bar_transition")
        transition_recognition = self.pipeline["茶壶-识别交替过渡"]["recognition"]
        self.assertTrue(static_recognition_matches(image, transition_recognition))
        self.assertFalse(static_recognition_matches(self.assemble(), transition_recognition))
        popup_without_info = self.assemble("popup_frame", "popup_tea_icon", "popup_tea_on", "header_popup")
        self.assertFalse(static_recognition_matches(popup_without_info, transition_recognition),
                         "无外层 info 的弹窗不能提供交替过渡证据")
        self.assertGreaterEqual(self.score(image, "entry_icons", [670, 640, 125, 80]), THRESHOLD)
        for template in ("bar_teapot_icon", "bar_storm_icon"):
            self.assertLess(self.score(image, template, [750, 640, 95, 80]), THRESHOLD)
        # 转场时按钮可能仍清晰可见；实际状态必须与茶壶图标联合命中。
        for name in ("茶壶-直接已开启", "茶壶-直接已关闭"):
            self.assertFalse(static_recognition_matches(image, self.pipeline[name]["recognition"]))

    def test_bar_templates_ignore_countdown_changes_in_both_regions(self):
        for region, parts in (("base", {"on": "bar_tea_on", "off": "bar_tea_off"}),
                              ("cn", {"on": "cn_toggle_on", "off": "cn_toggle_off"})):
            with patch(__name__ + ".IMAGE_ROOT", ROOT / f"assets/resource/{region}/image"):
                for state, part in parts.items():
                    with self.subTest(region=region, state=state):
                        image = self.assemble(part)
                        for text in ("1 day", "99 days", "00:00"):
                            changed = image.copy()
                            changed[687:720, 840:935] = (20, 70, 110)
                            cv2.putText(changed, text, (842, 708), cv2.FONT_HERSHEY_SIMPLEX,
                                        0.45, (0, 255, 255), 1)
                            self.assertGreaterEqual(self.score(changed, "bar_toggle_" + state,
                                                               [840, 640, 95, 80]), THRESHOLD)
                            other = "off" if state == "on" else "on"
                            self.assertLess(self.score(changed, "bar_toggle_" + other,
                                                       [840, 640, 95, 80]), THRESHOLD)

    def test_popup_templates_ignore_countdown_changes(self):
        for case in self.fixtures["popupCases"]:
            image = self.assemble(*case["parts"])
            for text in ("1 day", "99 days", "00:00"):
                with self.subTest(state=case["name"], countdown=text):
                    changed = image.copy()
                    changed[411:450, 775:855] = (20, 70, 110)
                    cv2.putText(changed, text, (776, 435), cv2.FONT_HERSHEY_SIMPLEX,
                                0.4, (0, 255, 255), 1)
                    self.assertGreaterEqual(self.score(changed, "toggle_" + case["name"],
                                                       [775, 365, 80, 85]), THRESHOLD)
                    other = "off" if case["name"] == "on" else "on"
                    self.assertLess(self.score(changed, "toggle_" + other,
                                               [775, 365, 80, 85]), THRESHOLD)

    def test_clear_formation_header_is_multilingual_and_rejects_overlays(self):
        for part, expected in (("header_jp", True), ("header_cn", True),
                               ("header_popup", False), ("header_equipment", False)):
            with self.subTest(part=part):
                score = self.score(self.assemble(part), "formation_clear", [780, 94, 90, 60])
                self.assertEqual(score >= THRESHOLD, expected)

    def test_popup_clicks_remain_limited_to_the_tea_item(self):
        for name in ("茶壶-弹窗开启状态并关闭", "茶壶-弹窗关闭状态并开启"):
            node = self.pipeline[name]
            target_x, target_y, *_ = node["action"]["param"]["target"]
            self.assertTrue(775 <= target_x < 855 and 365 <= target_y < 450)
            for item in node["recognition"]["param"]["all_of"]:
                param = item["recognition"]["param"]
                if Path(param["template"]).name in ("toggle_on.png", "toggle_off.png"):
                    x, y, width, height = param["roi"]
                    self.assertGreaterEqual(x, 760)
                    self.assertLessEqual(x + width, 900)
                    self.assertTrue(x <= target_x < x + width and y <= target_y < y + height)

    def test_fixture_images_are_only_small_required_regions(self):
        paths = [FIXTURE_ROOT / item["image"] for item in self.fixtures["parts"].values()]
        self.assertLess(sum(path.stat().st_size for path in paths), 250_000)
        for path in paths:
            with self.subTest(image=path.name):
                height, width = read_image(path).shape[:2]
                self.assertLessEqual(height, 85)
                self.assertLessEqual(width, 390)
                self.assertLess(width * height, 25_000)


if __name__ == "__main__":
    unittest.main()
