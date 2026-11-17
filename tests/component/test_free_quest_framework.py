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
    def test_all_daily_free_quest_templates_have_cn_coverage(self):
        resource = self.resource("cn")
        controller = ScreenController(np.zeros((720, 1280, 3), np.uint8))
        self.assertTrue(controller.post_connection().wait().succeeded)
        checks, errors = [], []

        class Probe(CustomAction):
            def run(self, ctx, argv):
                try:
                    runner = FreeQuestRunner(ctx.clone())
                    for server in ("base", "cn"):
                        folder = ROOT / "assets/resource" / server / "image/map"
                        for path in sorted(folder.glob("关卡归位*.png")):
                            label = cv2.imdecode(np.fromfile(path, np.uint8), 1)
                            h, w = label.shape[:2]
                            image = np.zeros((720, 1280, 3), np.uint8)
                            image[120:120+h, 641:641+w] = label
                            rows = list(runner.rows(image))
                            checks.append((server, path.name, len(rows),
                                           bool(rows and not rows[0][1])))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("free_daily_coverage_probe", Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("free_daily_coverage_probe", {"free_daily_coverage_probe": {
                "action": {"type": "Custom", "param": {"custom_action": "free_daily_coverage_probe"}}, "on_error": []
            }}).wait()
            self.assertTrue(status.succeeded, errors)
            self.assertEqual(len(checks), 8)
            self.assertTrue(all(count == 1 and uncleared for _, _, count, uncleared in checks), checks)
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
        finally:
            tasker.post_stop().wait()

    def test_cn_daily_legacy_templates_preserve_short_rows_clear_and_titles(self):
        resource = self.resource("cn")
        image = np.zeros((720, 1280, 3), np.uint8)
        expected_titles = []
        # 来源模板中的文字锚点分别为(11,23)、(9,19)、(8,19)，
        # 对应标准文字约0.89倍；用128px行距复现固定跳140px漏行。
        layouts = ((2, 120, 122, 10), (3, 248, 120, -12), (4, 376, 119, 6))
        for (index, y, dx, dy), title in zip(layouts, ("ALPHA QUEST", "BETA QUEST", "GAMMA QUEST")):
            source = ROOT / f"assets/resource/cn/image/map/关卡归位{index}.png"
            label = cv2.imdecode(np.fromfile(source, np.uint8), 1)
            h, w = label.shape[:2]
            image[y:y+h, 641:641+w] = label
            title_image = np.zeros((40, 290, 3), np.uint8)
            cv2.putText(title_image, title, (3, 28), cv2.FONT_HERSHEY_SIMPLEX, .7, (255, 255, 255), 1)
            small_title = cv2.resize(title_image, (258, 36))
            image[y+dy:y+dy+36, 641+dx:641+dx+258] = small_title
            expected_titles.append(cv2.resize(small_title, (290, 40)))
        clear = cv2.imdecode(np.fromfile(
            ROOT / "assets/resource/cn/image/event/已完成的副本标签.png", np.uint8), 1)
        # 只有第三行完成；缩小后的角标也须在该行自己的区域内复核。
        small_clear = cv2.resize(clear, (103, 27))
        image[475:502, 626:729] = small_clear
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        checks, errors = [], []

        class Probe(CustomAction):
            def run(self, ctx, argv):
                try:
                    runner = FreeQuestRunner(ctx.clone())
                    for _ in range(2):
                        rows = list(runner.rows(image))
                        checks.append([(row.name, row.box.y, done) for row, done in rows])
                    checks.append(all(np.array_equal(runner.title_patch(image, row), title)
                                      for (row, _), title in zip(rows, expected_titles)))
                    runner.current_title = runner.title_patch(image, rows[0][0])
                    runner.pending_clear = True
                    checks.append(runner.same_quest(image, rows[0][0]))
                    checks.append(runner.same_quest(image, rows[2][0]))
                    checks.append(runner.find_in_panel(image).name)
                    checks.append(runner.completed)
                    unknown = np.zeros_like(image)
                    unknown[120:264, 624:1234] = (60, 150, 40)
                    checks.append(list(runner.rows(unknown)))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("free_cn_legacy_probe", Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("free_cn_legacy_probe", {"free_cn_legacy_probe": {
                "action": {"type": "Custom", "param": {"custom_action": "free_cn_legacy_probe"}}, "on_error": []
            }}).wait()
            self.assertTrue(status.succeeded, (errors, checks))
            expected_rows = [("Free-列表旧版中文2", 120, False),
                             ("Free-列表旧版英文3", 248, False),
                             ("Free-列表旧版中文4", 376, True)]
            self.assertEqual(checks, [expected_rows, expected_rows, True, True, False,
                                      "Free-列表旧版中文2", 0, []])
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
        finally:
            tasker.post_stop().wait()

    def test_cn_ordeal_english_label_uses_english_title_offset(self):
        resource = self.resource("cn")
        card = cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/free_quest_cn_ordeal_card.png", np.uint8), 1)
        image = np.zeros((720, 1280, 3), np.uint8)
        image[110:290, 620:1240] = card
        # 在真实卡面下方加入另一行中文标记及完成角标，验证混合样式
        # 按屏幕顺序读取，且不能把下一行的完成状态归给奏章目标。
        def template(path):
            return cv2.imdecode(np.fromfile(ROOT / path, np.uint8), 1)
        label = template("assets/resource/cn/image/free_quest/free_label.png")
        clear = template("assets/resource/cn/image/event/已完成的副本标签.png")
        image[357:357+label.shape[0], 650:650+label.shape[1]] = label
        image[451:451+clear.shape[0], 642:642+clear.shape[1]] = clear
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        checks, errors = [], []

        class Probe(CustomAction):
            def run(self, ctx, argv):
                try:
                    runner = FreeQuestRunner(ctx.clone())
                    rows = list(runner.rows(image))
                    checks.append([(row.name, row.box.y, cleared) for row, cleared in rows])
                    english, chinese = rows[0][0], rows[1][0]
                    checks.append(np.array_equal(runner.title_patch(image, english), image[122:162, 775:1065]))
                    checks.append(np.array_equal(runner.title_patch(image, chinese), image[342:382, 775:1065]))
                    runner.current_title = runner.title_patch(image, english)
                    checks.append(runner.same_quest(image, english))
                    changed = image.copy()
                    changed[122:162, 775:1065] = 0
                    checks.append(runner.same_quest(changed, english))
                    checks.append(runner.find_in_panel(image).name)
                    # 无信息按钮时，英文标记也应可独立配合关闭按钮确认详情。
                    image[200:250, 1180:1230] = 0
                    back = template("assets/resource/cn/image/后退按钮.png")
                    image[27:27+back.shape[0], 29:29+back.shape[1]] = back
                    checks.append(runner.panel(image))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("free_cn_ordeal_probe", Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("free_cn_ordeal_probe", {"free_cn_ordeal_probe": {
                "action": {"type": "Custom", "param": {"custom_action": "free_cn_ordeal_probe"}}, "on_error": []
            }}).wait()
            self.assertTrue(status.succeeded, errors)
            self.assertEqual(checks, [
                [("Free-列表英文标记", 157, False), ("Free-列表标记", 357, True)],
                True, True, True, False, "Free-列表英文标记", True,
            ])
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
        finally:
            tasker.post_stop().wait()

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
