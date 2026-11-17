"""真实 MaaFramework/OCR 回放匿名国日服幕间画面；仅使用内存控制器。"""
import json
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from interlude_action import InterludeRunner
from interlude_recognition import InterludeEntry, InterludeMap, InterludeQuest, InterludeScan
from test_main_story_framework import FrameworkResources, ScreenController


def detail(result):
    value = result.best_result.detail
    return json.loads(value) if isinstance(value, str) else value


class InterludeRecognitionFrameworkTests(FrameworkResources, unittest.TestCase):
    def fixture(self, name):
        image = cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/interlude" / name, np.uint8), 1)
        self.assertIsNotNone(image)
        return image

    def probe(self, image, callback, server="cn"):
        resource = self.resource(server)
        resource.register_custom_recognition("interlude_scan", InterludeScan())
        resource.register_custom_recognition("interlude_entry", InterludeEntry())
        resource.register_custom_recognition("interlude_quest", InterludeQuest())
        resource.register_custom_recognition("interlude_map", InterludeMap())
        outcomes, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    outcomes.append(callback(context))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("interlude_recognition_probe", Probe())
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            status = tasker.post_task("interlude_recognition_probe", {
                "interlude_recognition_probe": {"action": {"type": "Custom", "param": {
                    "custom_action": "interlude_recognition_probe"
                }}, "on_error": []}
            }).wait()
            self.assertTrue(status.succeeded, errors)
            self.assertEqual(controller.clicks, [])
            self.assertEqual(controller.swipes, [])
            return outcomes[0]
        finally:
            tasker.post_stop().wait()

    def scan(self, image, map_mode=False, server="cn"):
        def callback(context):
            result = context.run_recognition("幕间-条目扫描", image, {
                "幕间-条目扫描": {"recognition": {"param": {
                    "custom_recognition_param": {"map": map_mode}
                }}}
            })
            self.assertTrue(result and result.hit)
            return detail(result)
        return self.probe(image, callback, server)

    def test_real_stage_direct_ap_zero_and_locked_lists(self):
        cases = (
            ("cn_list_stage.png", ["stage", "stage", "stage"], 0),
            ("cn_list_direct.png", ["stage", "direct", "stage"], 1),
            ("cn_list_locked.png", ["direct"], 2),
            ("cn_list_story.png", ["direct", "stage", "direct"], 0),
        )
        for name, kinds, locked_count in cases:
            with self.subTest(name=name):
                report = self.scan(self.fixture(name))
                self.assertEqual([entry["kind"] for entry in report["entries"]], kinds)
                self.assertEqual(report["locked_count"], locked_count)
                self.assertEqual(report["uncertain_count"], 0)
                self.assertTrue(all(entry["title"] for entry in report["entries"]))

    def test_real_map_selects_interlude_instead_of_green_free(self):
        for name, expected_top in (("cn_map_interlude.png", 115),
                                   ("cn_map_merged_background.png", 128)):
            with self.subTest(name=name):
                report = self.scan(self.fixture(name), True)
                self.assertEqual(len(report["entries"]), 1)
                self.assertEqual(report["entries"][0]["kind"], "map")
                self.assertEqual(report["entries"][0]["title"], "神弓的真价")
                self.assertEqual(report["entries"][0]["row_box"][1], expected_top)
                self.assertGreater(report["entries"][0]["brightness"], 170)
                self.assertLess(report["entries"][0]["box"][1], 280)
                self.assertEqual(report["uncertain_count"], 0)
                self.assertFalse(report["empty_confirmed"])

    def test_free_only_synthetic_map_is_positive_empty_evidence(self):
        for name in ("cn_map_free_only_synthetic.png", "cn_map_free_only_live.png"):
            with self.subTest(name=name):
                report = self.scan(self.fixture(name), True)
                self.assertEqual(report["entries"], [])
                self.assertEqual(report["free_count"], 1)
                self.assertEqual(report["row_count"], 0)
                self.assertEqual(report["uncertain_count"], 0)
                self.assertTrue(report["empty_confirmed"])

    def test_blank_and_brown_without_task_text_are_unknown(self):
        for color in (None, (40, 110, 180), (20, 45, 100)):
            image = np.zeros((720, 1280, 3), np.uint8)
            if color:
                image[120:264, 624:1234] = color
            for map_mode in (False, True):
                with self.subTest(color=color, map_mode=map_mode):
                    report = self.scan(image, map_mode)
                    self.assertEqual(report["entries"], [])
                    self.assertEqual(report["locked_count"], 0)
                    self.assertGreater(report["uncertain_count"], 0)
                    self.assertFalse(report["empty_confirmed"])

    def test_map_return_button_rejects_dimmed_background(self):
        image = self.fixture("cn_map_interlude.png")
        def callback(context):
            visible = context.run_recognition("幕间-返回列表", image)
            dimmed = context.run_recognition("幕间-返回列表", (image * .55).astype(np.uint8))
            return bool(visible and visible.hit), bool(dimmed and dimmed.hit)
        self.assertEqual(self.probe(image, callback), (True, False))

    def test_close_map_from_log_is_recognized_without_return_button(self):
        image = self.fixture("cn_map_without_interlude_return.png")

        def callback(context):
            runtime = InterludeRunner(context)
            self.assertIsNone(runtime.reco("幕间-返回列表", image))
            self.assertTrue(runtime.on_map(image))
            # 仅改变关闭按钮亮度；图片仍能匹配时不加独立白色像素门槛。
            dim_close = image.copy()
            dim_close[:100, :230] = (dim_close[:100, :230] * .55).astype(np.uint8)
            self.assertTrue(runtime.on_map(dim_close))
            result = runtime.reco("幕间-地图关卡", image)
            self.assertIsNotNone(result)
            self.assertEqual(detail(result)["title"], "龙之圣女，海边圣女")
            self.assertLess(result.box[1], 280)
            return True

        self.assertTrue(self.probe(image, callback))

    def test_close_map_rejects_missing_button_and_non_map_pages(self):
        image = self.fixture("cn_map_without_interlude_return.png")
        no_close = image.copy()
        no_close[:100, :230] = 0
        close_only = np.zeros_like(image)
        close_only[:100, :230] = image[:100, :230]
        no_interlude = image.copy()
        # 保留AP和亮棕卡，但去掉Interlude；同时去掉绿色Free，不能凭AP确认地图。
        no_interlude[158:183, 765:1100] = 0
        no_interlude[295:] = 0
        list_image = self.fixture("cn_list_direct.png")

        def callback(context):
            runtime = InterludeRunner(context)
            return [runtime.on_map(screenshot) for screenshot in (
                no_close, close_only,
                no_interlude, list_image,
            )]

        self.assertEqual(self.probe(image, callback), [False] * 4)

    def test_close_map_supports_completed_interlude_and_free_only_after_clear(self):
        image = self.fixture("cn_map_without_interlude_return.png")
        marker = cv2.imdecode(np.fromfile(
            ROOT / "assets/resource/cn/image/event/已完成的副本标签.png", np.uint8), 1)
        completed = image.copy()
        h, w = marker.shape[:2]
        completed[235:235 + h, 635:635 + w] = marker
        free_only = image.copy()
        free_only[100:295] = 0

        def callback(context):
            runtime = InterludeRunner(context)
            for screenshot in (completed, free_only):
                self.assertTrue(runtime.on_map(screenshot))
                self.assertIsNone(runtime.reco("幕间-地图关卡", screenshot))
            return True

        self.assertTrue(self.probe(image, callback))

    def test_jp_existing_close_template_confirms_locked_interlude_map(self):
        image = self.fixture("jp_map_locked_free.png")
        no_return = image.copy()
        no_return[90:180, :200] = 0

        def callback(context):
            runtime = InterludeRunner(context)
            self.assertIsNone(runtime.reco("幕间-返回列表", no_return))
            self.assertTrue(runtime.on_map(no_return))
            dim_close = no_return.copy()
            dim_close[:100, :230] = (dim_close[:100, :230] * .55).astype(np.uint8)
            self.assertTrue(runtime.on_map(dim_close))
            self.assertFalse(runtime.on_map(self.fixture("jp_list_direct_locked.png")))
            return True

        self.assertTrue(self.probe(image, callback, "jp"))

    def test_real_jp_direct_entries_and_locked_stage_are_separate(self):
        image = self.fixture("jp_list_direct_locked.png")
        report = self.scan(image, server="jp")
        self.assertEqual([entry["kind"] for entry in report["entries"]], ["direct", "direct"])
        self.assertGreaterEqual(report["locked_count"], 1)
        self.assertEqual(report["uncertain_count"], 0)
        self.assertTrue(all(entry["title"] for entry in report["entries"]))
        # 編成条件あり是允许进本后处理的限制，不能当作尚未开启。
        self.assertGreater(report["entries"][1]["row_box"][1], 300)

    def test_real_jp_locked_list_has_no_available_entries(self):
        report = self.scan(self.fixture("jp_list_locked.png"), server="jp")
        self.assertEqual(report["entries"], [])
        self.assertGreaterEqual(report["locked_count"], 3)
        self.assertEqual(report["uncertain_count"], 0)

    def test_real_jp_header_and_shared_exit_reject_dimmed_background(self):
        image = self.fixture("jp_list_direct_locked.png")
        def callback(context):
            outcomes = []
            for node in ("幕间-列表界面", "结算-已返回幕间列表"):
                visible = context.run_recognition(node, image)
                dimmed = context.run_recognition(node, (image * .55).astype(np.uint8))
                outcomes.append((bool(visible and visible.hit), bool(dimmed and dimmed.hit)))
            return outcomes
        self.assertEqual(self.probe(image, callback, "jp"), [(True, False), (True, False)])
        # 同一实图在国服资源下不能通过，避免把未生效的日服覆盖计作通过。
        def cn_header(context):
            result = context.run_recognition("幕间-列表界面", image)
            return bool(result and result.hit)
        self.assertFalse(self.probe(image, cn_header, "cn"))

    def test_real_jp_map_return_rejects_dimmed_background_and_other_pages(self):
        image = self.fixture("jp_map_locked_free.png")
        list_image = self.fixture("jp_list_direct_locked.png")

        def callback(context):
            results = []
            for screenshot in (image, (image * .55).astype(np.uint8), list_image):
                result = context.run_recognition("幕间-返回列表", screenshot)
                results.append(bool(result and result.hit))
            return results

        self.assertEqual(self.probe(image, callback, "jp"), [True, False, False])

        # 国服文案不能命中日服实图，确保测试确实加载了日服覆盖。
        def cn_return(context):
            result = context.run_recognition("幕间-返回列表", image)
            return bool(result and result.hit)

        self.assertFalse(self.probe(image, cn_return, "cn"))

    def test_real_jp_map_locked_interludes_and_free_are_not_available(self):
        report = self.scan(self.fixture("jp_map_locked_free.png"), True, "jp")
        self.assertEqual(report["entries"], [])
        self.assertEqual(report["locked_count"], 2)
        self.assertEqual(report["free_count"], 1)
        self.assertEqual(report["uncertain_count"], 0)
        self.assertTrue(report["empty_confirmed"])

    def test_jp_return_keeps_foreground_gate_and_click_action(self):
        resource = self.resource("jp")
        back = resource.get_node_data("幕间-返回列表")
        self.assertEqual(back["recognition"]["type"], "And")
        clauses = back["recognition"]["param"]["all_of"]
        self.assertIn("幕间-地图返回前景", clauses)
        expected = clauses[1]["param"]["expected"]
        self.assertTrue(any("幕間の物語" in value for value in expected))
        self.assertEqual(back["action"]["type"], "Click")

    def test_jp_pending_reward_keeps_base_fallback_contract(self):
        # 奖励尚无本轮日服实图；此处只核对合并契约，不计作实机覆盖。
        resource = self.resource("jp")
        reward = resource.get_node_data("剧情奖励-展示继续")
        expected = reward["recognition"]["param"]["expected"]
        self.assertTrue(any("画面をタッチ" in value for value in expected))
        self.assertTrue(any("请点击" in value for value in expected))
        self.assertEqual(reward["action"]["type"], "Click")

    def test_cn_gate_requires_title_and_close_button_together(self):
        image = self.fixture("cn_gate_interlude_tab.png")
        no_close = image.copy()
        no_close[:100, :230] = 0
        no_title = image.copy()
        no_title[:95, 950:] = 0

        def callback(context):
            results = []
            for screenshot in (image, no_close, no_title,
                               self.fixture("cn_list_direct.png")):
                result = context.run_recognition("幕间-迦勒底之门界面", screenshot)
                results.append(bool(result and result.hit))
            return results

        self.assertEqual(self.probe(image, callback), [True, False, False, False])

    def test_cn_gate_interlude_tab_matches_real_screenshot(self):
        image = self.fixture("cn_gate_interlude_tab.png")

        def callback(context):
            result = context.run_recognition("幕间-选择幕间页签", image)
            self.assertTrue(result and result.hit)
            node = context.get_node_data("幕间-选择幕间页签")
            self.assertEqual(node["recognition"]["type"], "TemplateMatch")
            return True

        self.assertTrue(self.probe(image, callback))

    def test_cn_gate_tab_ignores_four_corner_badges_and_event_caption(self):
        image = self.fixture("cn_gate_interlude_tab.png")
        # 合成角标变化，只修改页签四角及下沿说明，保留中央标题和底纹。
        variants = []
        for color in ((0, 0, 0), (40, 170, 240)):
            changed = image.copy()
            for x, y, w, h in ((650, 180, 100, 35), (1120, 165, 115, 50),
                               (650, 270, 100, 45), (1100, 270, 135, 45),
                               (850, 280, 385, 45)):
                changed[y:y+h, x:x+w] = color
            variants.append(changed)

        def callback(context):
            return [bool(result and result.hit) for result in (
                context.run_recognition("幕间-选择幕间页签", screenshot)
                for screenshot in variants
            )]

        self.assertEqual(self.probe(image, callback), [True, True])

    def test_cn_gate_tab_remains_recognizable_after_vertical_scroll(self):
        image = self.fixture("cn_gate_interlude_tab.png")
        card = image[180:320, 640:1235].copy()
        # 从原实图取页签卡片，合成不同滚动位置；不代表已经实机滚动验收。
        variants = []
        for top in (105, 390, 540):
            moved = image.copy()
            moved[95:710, 640:1235] = 0
            moved[top:top+card.shape[0], 640:1235] = card
            variants.append(moved)

        def callback(context):
            return [bool(result and result.hit) for result in (
                context.run_recognition("幕间-选择幕间页签", screenshot)
                for screenshot in variants
            )]

        self.assertEqual(self.probe(image, callback), [True, True, True])

    def test_cn_gate_rankup_and_advanced_tabs_are_not_interlude(self):
        image = self.fixture("cn_gate_interlude_tab.png")
        no_interlude = image.copy()
        no_interlude[180:325, 640:1235] = 0

        def callback(context):
            # 下方强化任务及进阶关卡保留真实画面，不能命中幕间入口。
            result = context.run_recognition("幕间-选择幕间页签", no_interlude)
            return bool(result and result.hit)

        self.assertFalse(self.probe(image, callback))

    def test_jp_gate_title_uses_japanese_ocr_and_tab_uses_template_contract(self):
        resource = self.resource("jp")
        gate = resource.get_node_data("幕间-迦勒底之门界面")
        self.assertEqual(gate["recognition"]["type"], "And")
        self.assertIn("カルデアゲート", json.dumps(gate, ensure_ascii=False))
        tab = resource.get_node_data("幕间-选择幕间页签")
        self.assertEqual(tab["recognition"]["type"], "TemplateMatch")
        self.assertEqual(tab["recognition"]["param"]["template"],
                         ["chaldea_gate/幕间物语_日服.png"])
        self.assertEqual(tab["action"]["type"], "Click")

    def test_jp_gate_requires_title_and_close_button_together(self):
        image = self.fixture("jp_gate_interlude_tab.png")
        no_close = image.copy()
        no_close[:100, :230] = 0
        no_title = image.copy()
        no_title[:95, 880:] = 0

        def callback(context):
            results = []
            for screenshot in (image, no_close, no_title,
                               self.fixture("jp_list_direct_locked.png")):
                result = context.run_recognition("幕间-迦勒底之门界面", screenshot)
                results.append(bool(result and result.hit))
            return results

        self.assertEqual(self.probe(image, callback, "jp"), [True, False, False, False])

    def test_jp_gate_interlude_tab_matches_real_screenshot(self):
        image = self.fixture("jp_gate_interlude_tab.png")

        def callback(context):
            result = context.run_recognition("幕间-选择幕间页签", image)
            self.assertTrue(result and result.hit)
            self.assertEqual(list(result.box), [750, 320, 250, 70])
            return True

        self.assertTrue(self.probe(image, callback, "jp"))

    def test_jp_gate_tab_ignores_four_corner_badges_and_event_caption(self):
        image = self.fixture("jp_gate_interlude_tab.png")
        # 修改四角角标及下沿活动说明，中央日文标题与英文条保持原样。
        variants = []
        for color in ((0, 0, 0), (40, 170, 240)):
            changed = image.copy()
            for x, y, w, h in ((650, 285, 95, 35), (1120, 280, 115, 50),
                               (650, 390, 95, 45), (1100, 390, 135, 45),
                               (750, 395, 485, 40)):
                changed[y:y+h, x:x+w] = color
            variants.append(changed)

        def callback(context):
            return [bool(result and result.hit) for result in (
                context.run_recognition("幕间-选择幕间页签", screenshot)
                for screenshot in variants
            )]

        self.assertEqual(self.probe(image, callback, "jp"), [True, True])

    def test_jp_gate_tab_remains_recognizable_after_vertical_scroll(self):
        image = self.fixture("jp_gate_interlude_tab.png")
        card = image[285:435, 640:1235].copy()
        # 由真实卡片合成三个列表位置；不代表已执行日服实机滚动。
        variants = []
        for top in (120, 325, 525):
            moved = image.copy()
            moved[95:700, 640:1235] = 0
            moved[top:top+card.shape[0], 640:1235] = card
            variants.append((top, moved))

        def callback(context):
            for top, screenshot in variants:
                result = context.run_recognition("幕间-选择幕间页签", screenshot)
                self.assertTrue(result and result.hit, top)
                self.assertEqual(list(result.box), [750, top + 35, 250, 70])
            return True

        self.assertTrue(self.probe(image, callback, "jp"))

    def test_jp_daily_and_rank_up_tabs_are_not_interlude(self):
        image = self.fixture("jp_gate_interlude_tab.png")
        no_interlude = image.copy()
        no_interlude[280:435, 640:1235] = 0

        def callback(context):
            # 上方曜日クエスト和下方強化クエスト保留实图，不能误作幕间入口。
            result = context.run_recognition("幕间-选择幕间页签", no_interlude)
            return bool(result and result.hit)

        self.assertFalse(self.probe(image, callback, "jp"))

    def test_cn_and_jp_gate_tab_layers_keep_own_boxes_and_title_gate(self):
        images = [self.fixture(f"{server}_gate_interlude_tab.png")
                  for server in ("cn", "jp")]

        for server, own_index, template, expected_box, expected in (
                ("cn", 0, "chaldea_gate/幕间物语.png", [776, 210, 200, 68], [True, False]),
                ("jp", 1, "chaldea_gate/幕间物语_日服.png", [750, 320, 250, 70], [False, True])):
            with self.subTest(server=server):
                def callback(context):
                    node = context.get_node_data("幕间-选择幕间页签")
                    self.assertEqual(node["recognition"]["param"]["template"],
                                     [template])
                    own_tab = context.run_recognition("幕间-选择幕间页签", images[own_index])
                    self.assertTrue(own_tab and own_tab.hit)
                    # 本服实图命中各自中央标题模板，尺寸不同可定位资源来源。
                    self.assertEqual(list(own_tab.box), expected_box)
                    # 实际点击叶子要求同一画面的本服门标题和页签同时命中。
                    return [bool(result and result.hit) for result in (
                        context.run_recognition("幕间-确认并选择页签", screenshot)
                        for screenshot in images
                    )]

                self.assertEqual(self.probe(images[0], callback, server), expected)

    def test_clear_template_is_limited_to_the_interlude_row(self):
        image = self.fixture("cn_map_interlude.png")
        path = ROOT / "assets/resource/cn/image/event/已完成的副本标签.png"
        marker = cv2.imdecode(np.fromfile(path, np.uint8), 1)
        self.assertIsNotNone(marker)
        h, w = marker.shape[:2]
        # 原图绿色Free已经有完成角标，本行仍可选；把同款角标放本行下沿才拒绝。
        before = self.scan(image, True)
        self.assertEqual(len(before["entries"]), 1)
        image[235:235 + h, 635:635 + w] = marker
        after = self.scan(image, True)
        self.assertEqual(after["entries"], [])
        self.assertEqual(after["completed_count"], 1)
        self.assertTrue(after["empty_confirmed"])


if __name__ == "__main__":
    unittest.main()
