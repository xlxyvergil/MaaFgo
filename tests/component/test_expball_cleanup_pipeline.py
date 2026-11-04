"""Exercise cleanup's production branches in MaaFramework without a device.

Only leaf visual evidence and device input are replaced by an in-memory scene.
Production next/And guards and per-call overrides remain real Framework paths.
The caller's approval boundary is modeled by invoking only preview/close until
it has decided to call selection; this is not a visual accuracy test.
"""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest

import maa
import numpy as np
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker


ROOT = Path(__file__).resolve().parents[2]
P = "搓丸子整理-"
N = "搓丸子网络-"
TERMINALS = {
    P + name for name in ("详情就绪", "列表就绪", "候选已选择", "贩卖已完成",
                          "QP已满", "保管室就绪", "存储已完成", "保管室已满",
                          "列表到底", "翻页完成", "保管室从者就绪")
} | {"整理礼物盒-导航到灵基变还完成", "整理礼物盒-已在灵基图标大小检测"}


class CleanupMemoryController(CustomController):
    def __init__(self, scene, *, green_confirm=True, qp_full=False,
                 storage_full=False, sell_confirm=True, store_context=True,
                 at_bottom=False, page_after_swipe=None,
                 storage_category="servant", storage_tab_evidence=True,
                 network_after=None, network_kind="download", network_body=True,
                 network_button=True, filter_window_visible=True,
                 filter_experience_visible=True, filter_experience_after_reposition=None,
                 filter_experience_after_swipes=None):
        self.scene = scene
        self.list_scene = "store_list" if scene in ("store_list", "store_selected") else "sell_list"
        self.green_confirm = green_confirm
        self.qp_full = qp_full
        self.storage_full = storage_full
        self.sell_confirm = sell_confirm
        self.store_context = store_context
        self.at_bottom = at_bottom
        self.page_after_swipe = page_after_swipe
        self.storage_category = storage_category
        self.storage_tab_evidence = storage_tab_evidence
        self.network_after = dict(network_after or {})
        self.network_remaining = self.network_after.pop("start", 0)
        self.network_kind = network_kind
        self.network_body = network_body
        self.network_button = network_button
        self.network_click_boxes = []
        self.steps = []
        self.action_boxes = {}
        self.confirmed_sales = self.confirmed_stores = 0
        self.unexpected_inputs = []
        self.chosen_stars = []
        self.filter_window_visible = filter_window_visible
        self.filter_experience_visible = filter_experience_visible
        self.filter_experience_after_reposition = filter_experience_after_reposition
        self.filter_experience_after_swipes = filter_experience_after_swipes
        self.filter_swipes = 0
        self.action_targets = {}
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return "expball-cleanup-synthetic-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)

    def reject(self, *args):
        self.unexpected_inputs.append(args)
        return False

    start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = reject
    click_key = input_text = key_down = key_up = reject

    def step(self, name):
        name = {P + "执行关闭详情": P + "关闭详情",
                P + "执行提交贩卖": P + "提交贩卖",
                P + "执行提交存储": P + "提交存储"}.get(name, name)
        self.steps.append(name)
        if name == N + "重试":
            if self.network_remaining <= 0:
                return False
            self.network_remaining -= 1
            return True
        if self.network_remaining:
            return False
        if name == P + "保管室选择从者":
            if self.scene not in ("store_list", "store_selected") or self.storage_category != "equip":
                return False
            self.storage_category = "servant"
        elif name == P + "滑动下一页":
            if self.scene not in ("sell_list", "store_list") or self.at_bottom:
                return False
            if self.page_after_swipe is not None:
                self.scene = self.page_after_swipe
        elif name == P + "预览候选":
            if self.scene not in ("sell_list", "store_list"):
                return False
            self.scene = "detail"
        elif name == P + "关闭详情":
            if self.scene != "detail": return False
            self.scene = self.list_scene
        elif name == P + "选择候选":
            if self.scene not in ("sell_list", "store_list"): return False
            self.scene = "selected" if self.list_scene == "sell_list" else "store_selected"
        elif name == P + "提交贩卖":
            if self.scene != "selected": return False
            self.scene = "sell_confirm" if self.sell_confirm else "unknown_confirm"
        elif name == P + "确认贩卖":
            if self.scene != "sell_confirm": return False
            self.confirmed_sales += 1
            self.scene = "qp_full" if self.qp_full else "sell_reward"
        elif name == P + "QP已满" and self.scene == "qp_full":
            # An action is installed here only by the production aqf override.
            self.scene = "sell_reward"
        elif name == P + "关闭贩卖结果":
            if self.scene != "sell_reward": return False
            self.scene = "sell_list"
        elif name == P + "提交存储":
            if self.scene != "store_selected": return False
            self.scene = "store_full" if self.storage_full else "store_confirm"
        elif name == P + "确认存储":
            if self.scene != "store_confirm": return False
            self.confirmed_stores += 1
            self.scene = "store_reward"
        elif name == P + "关闭存储结果":
            if self.scene != "store_reward": return False
            self.scene = "store_list"
        elif name == "整理礼物盒-筛卖点筛选按钮":
            self.scene = "filter"
        elif name == P + "经验筛选滚动定位":
            if self.scene != "filter" or not self.filter_window_visible:
                return False
            if self.filter_experience_after_reposition is not None:
                self.filter_experience_visible = self.filter_experience_after_reposition
        elif name == "整理礼物盒-筛卖下滑找经验值":
            if self.scene != "filter": return False
            self.filter_swipes += 1
            if self.filter_experience_after_swipes is not None:
                self.filter_experience_visible = self.filter_swipes >= self.filter_experience_after_swipes
        elif name == "整理礼物盒-筛卖点经验值":
            if self.scene != "filter" or not self.filter_experience_visible: return False
        elif name in ("整理礼物盒-筛卖点4星", "整理礼物盒-筛卖点5星", "整理礼物盒-筛卖点3星"):
            self.chosen_stars.append(int(name[-2]))
        elif name == "整理礼物盒-筛卖点决定":
            self.scene = self.list_scene
        self.network_remaining = self.network_after.pop(name, 0)
        return True


class CleanupPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.cleanup = json.loads((ROOT / "assets/resource/base/pipeline/搓丸子整理.json").read_text("utf-8"))
        cls.shared = json.loads((ROOT / "assets/resource/base/pipeline/整理礼物盒.json").read_text("utf-8"))
        cls.network = json.loads((ROOT / "assets/resource/base/pipeline/搓丸子网络.json").read_text("utf-8"))

    @contextmanager
    def harness(self, scene, **config):
        controller = CleanupMemoryController(scene, **config)
        nodes = copy.deepcopy({**self.shared, **self.cleanup, **self.network})
        # External navigation isn't under test. Unknown pages must never be
        # rescued by a DirectHit placeholder or a global recovery success.
        external = set()
        for item in nodes.values():
            for value in item.get("next", []):
                name = re.sub(r"^\[[^]]+\]", "", value if isinstance(value, str) else value["name"])
                if name not in nodes: external.add(name)
        for name in external:
            nodes[name] = {"recognition": {"type": "Custom", "param": {
                "custom_recognition": "cleanup_screen", "custom_recognition_param": {"node": name}}}, "on_error": []}
        seen = []

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                cfg = json.loads(argv.custom_recognition_param)
                name, leaf, state = cfg["node"], cfg.get("leaf", ""), controller.scene
                if name.startswith(N):
                    # Keep native Or/And/box_index. Only OCR/template leaves
                    # are simulated, with different text and button boxes.
                    if not controller.network_remaining:
                        return None
                    branch = ".0" if controller.network_kind == "download" else ".1"
                    if not leaf.startswith(branch):
                        return None
                    if leaf.endswith(".0"):
                        return (220, 230, 800, 80) if controller.network_body else None
                    return (735, 535, 205, 55) if controller.network_button else None
                if name == P + "列表到底":
                    # This leaf models only scrollEnd. The production string
                    # reference must independently recognize the list identity.
                    return (993, 675, 40, 27) if controller.at_bottom else None
                if name in (P + "保管室从者已选", P + "保管室选择从者"):
                    # Only the inline tab template is replaced here. The
                    # production And must independently verify storage identity.
                    category = "servant" if name.endswith("从者已选") else "equip"
                    return (220, 100, 130, 35) if (
                        controller.storage_tab_evidence and controller.storage_category == category
                    ) else None
                if name == P + "确认存储":
                    if state != "store_confirm": return None
                    if leaf.endswith(".1") and not controller.store_context: return None
                    # Distinct boxes prove And returns the button (index 0),
                    # rather than the full-screen OCR identity context.
                    return (0, 0, 1280, 720) if leaf.endswith(".1") else (750, 550, 130, 40)
                if name == "整理礼物盒-筛卖点经验值":
                    return (600, 250, 100, 40) if state == "filter" and controller.filter_experience_visible else None
                if name == "整理礼物盒-筛卖点决定":
                    return (800, 600, 100, 40) if state == "filter" and controller.filter_window_visible else None
                allowed = {
                    "sell_list": {P + "列表就绪", P + "贩卖已完成", "整理礼物盒-已在灵基变还", "整理礼物盒-已在灵基图标大小检测"},
                    "store_list": {P + "列表就绪", P + "保管室就绪", P + "存储已完成", "整理礼物盒-已在灵基图标大小检测"},
                    "detail": {P + "详情就绪", P + "执行关闭详情", P + "候选未锁定", P + "候选名称"},
                    "selected": {P + "候选已选择"} if controller.green_confirm else set(),
                    "store_selected": {P + "候选已选择"} if controller.green_confirm else set(),
                    "sell_confirm": {P + "确认贩卖"},
                    "sell_reward": {P + "关闭贩卖结果"},
                    "qp_full": {P + "QP已满"},
                    "store_full": {P + "保管室已满"},
                    "store_reward": {P + "关闭存储结果"},
                    "filter": {"整理礼物盒-筛卖重置筛选", "整理礼物盒-筛卖点4星", "整理礼物盒-筛卖点5星",
                               "整理礼物盒-筛卖点3星", "整理礼物盒-筛卖点经验值", "整理礼物盒-筛卖点决定"},
                }
                return (20, 20, 40, 40) if name in allowed.get(state, set()) else None

        class Step(CustomAction):
            def run(self, context, argv):
                controller.action_boxes[argv.node_name] = tuple(argv.box)
                source = json.loads(argv.custom_action_param).get("source_action", {})
                if "target" in source.get("param", {}):
                    controller.action_targets[argv.node_name] = source["param"]["target"]
                if argv.node_name == N + "重试":
                    controller.network_click_boxes.append(tuple(argv.box))
                return controller.step(argv.node_name)

        class Record(CustomAction):
            def run(self, context, argv):
                seen.append(argv.node_name)
                return True

        def visual(recognition, owner, leaf=""):
            kind = recognition.get("type", "DirectHit")
            if kind in ("And", "Or"):
                value = copy.deepcopy(recognition)
                field = "all_of" if kind == "And" else "any_of"
                for index, child in enumerate(value["param"][field]):
                    if isinstance(child, dict) and isinstance(child.get("recognition"), dict):
                        child["recognition"] = visual(child["recognition"], owner, f"{leaf}.{index}")
                return value
            if kind == "DirectHit": return copy.deepcopy(recognition)
            return {"type": "Custom", "param": {
                "custom_recognition": "cleanup_screen",
                "custom_recognition_param": {"node": owner, "leaf": leaf},
            }}

        def input_action(name, source_action):
            return {"type": "Custom", "param": {"custom_action": "cleanup_step",
                    "custom_action_param": {"source_action": copy.deepcopy(source_action)}}}

        for name, item in nodes.items():
            item.update(pre_delay=0, post_delay=0, post_wait_freezes=0, timeout=60, rate_limit=1, on_error=[])
            item["recognition"] = visual(item.get("recognition", {}), name)
            if item.get("action", {}).get("type") not in (None, "DoNothing", "StopTask"):
                item["action"] = input_action(name, item["action"])
            elif name in TERMINALS:
                item["action"] = {"type": "Custom", "param": {"custom_action": "cleanup_record"}}
            item.pop("focus", None)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "pipeline/cleanup.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(root).wait().succeeded)
            resource.register_custom_recognition("cleanup_screen", Screen())
            resource.register_custom_action("cleanup_step", Step())
            resource.register_custom_action("cleanup_record", Record())
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            monitored = ["整理礼物盒-导航到灵基变还", "整理礼物盒-筛卖选4星", "整理礼物盒-筛卖选5星",
                         "整理礼物盒-筛卖找经验值", P + "经验筛选滚动定位", P + "QP已满"]
            baseline = {name: copy.deepcopy(resource.get_node_data(name)) for name in monitored}

            def run(entry, override=None):
                patch = copy.deepcopy(override or {})
                # The only conversion is device input to its scene equivalent;
                # override next/recognition/on_error remain production behavior.
                for name, value in patch.items():
                    if value.get("action", {}).get("type") not in (None, "DoNothing", "StopTask"):
                        value["action"] = input_action(name, value["action"])
                job = tasker.post_task(entry if entry.startswith(("整理礼物盒-", N)) else P + entry, patch)
                deadline = time.monotonic() + 3
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.002)
                if not job.status.done:
                    tasker.post_stop().wait()
                    self.fail("cleanup Pipeline exceeded bounded test deadline")
                for name in monitored:
                    self.assertEqual(resource.get_node_data(name), baseline[name], name)
                return job.status.succeeded

            try:
                yield controller, seen, run
            finally:
                tasker.post_stop().wait()
                self.assertEqual(controller.unexpected_inputs, [])

    def test_reuses_shared_navigation_terminal_with_scoped_override(self):
        with self.harness("sell_list") as (c, seen, run):
            self.assertTrue(run("导航贩卖", {
                "整理礼物盒-导航到灵基变还": {"next": ["整理礼物盒-已在灵基变还"]},
            }))
            self.assertEqual(seen, ["整理礼物盒-导航到灵基变还完成"])
            self.assertEqual(c.steps, [])

    def test_filter_uses_shared_terminal_and_does_not_keep_previous_stars(self):
        with self.harness("sell_list") as (c, seen, run):
            self.assertTrue(run("筛选经验卡", {
                "整理礼物盒-筛卖选3星": {"next": ["整理礼物盒-筛卖选4星"]},
                "整理礼物盒-筛卖选4星": {"next": ["整理礼物盒-筛卖点4星"]},
                "整理礼物盒-筛卖选5星": {"next": ["整理礼物盒-筛卖找经验值"]},
            }))
            self.assertEqual(c.chosen_stars, [4])
            self.assertEqual(seen, ["整理礼物盒-已在灵基图标大小检测"])
            c.chosen_stars.clear()
            self.assertTrue(run("筛选经验卡", {
                "整理礼物盒-筛卖选4星": {"next": ["整理礼物盒-筛卖选5星"]},
                "整理礼物盒-筛卖选5星": {"next": ["整理礼物盒-筛卖点5星"]},
            }))
            self.assertEqual(c.chosen_stars, [5])

    def test_sale_filter_reposition_requires_visible_filter_window(self):
        for scene, visible in (("unknown", True), ("sell_list", True), ("detail", True), ("filter", False)):
            with self.subTest(scene=scene, visible=visible), self.harness(
                    scene, filter_window_visible=visible) as (c, seen, run):
                self.assertFalse(run("经验筛选滚动定位"))
                self.assertEqual(c.steps, [])
                self.assertEqual(seen, [])
                self.assertEqual((c.confirmed_sales, c.confirmed_stores), (0, 0))

    def test_sale_filter_uses_visible_experience_before_reposition_and_rechecks_after_it(self):
        override = self.cleanup[P + "筛选经验卡"]["attach"]["sale_filter_override"]
        for initially_visible in (True, False):
            with self.subTest(initially_visible=initially_visible), self.harness(
                    "filter", filter_experience_visible=initially_visible,
                    filter_experience_after_reposition=True) as (c, seen, run):
                self.assertTrue(run("整理礼物盒-筛卖找经验值", override))
                self.assertEqual(c.steps.count(P + "经验筛选滚动定位"), int(not initially_visible))
                self.assertEqual(c.filter_swipes, 0)
                self.assertEqual(c.steps.count("整理礼物盒-筛卖点经验值"), 1)
                self.assertEqual(c.steps.count("整理礼物盒-筛卖点决定"), 1)
                self.assertEqual(seen, ["整理礼物盒-已在灵基图标大小检测"])
                if not initially_visible:
                    self.assertEqual(c.action_targets[P + "经验筛选滚动定位"], [1135, 250, 1, 1])
                    self.assertLess(c.steps.index(P + "经验筛选滚动定位"),
                                    c.steps.index("整理礼物盒-筛卖点经验值"))
                self.assertEqual((c.confirmed_sales, c.confirmed_stores), (0, 0))

    def test_sale_filter_failed_reposition_retains_ten_swipe_limit_without_blind_confirmation(self):
        override = self.cleanup[P + "筛选经验卡"]["attach"]["sale_filter_override"]
        for reveal_after in (3, None):
            with self.subTest(reveal_after=reveal_after), self.harness(
                    "filter", filter_experience_visible=False,
                    filter_experience_after_reposition=False,
                    filter_experience_after_swipes=reveal_after) as (c, seen, run):
                self.assertEqual(run("整理礼物盒-筛卖找经验值", override), reveal_after is not None)
                self.assertEqual(c.steps.count(P + "经验筛选滚动定位"), 1)
                self.assertEqual(c.filter_swipes, 3 if reveal_after else 10)
                if reveal_after:
                    self.assertEqual(c.steps.count("整理礼物盒-筛卖点经验值"), 1)
                    self.assertEqual(seen, ["整理礼物盒-已在灵基图标大小检测"])
                else:
                    self.assertNotIn("整理礼物盒-筛卖点经验值", c.steps)
                    self.assertNotIn("整理礼物盒-筛卖点决定", c.steps)
                    self.assertEqual(seen, [])
                    self.assertEqual(c.scene, "filter")
                self.assertEqual((c.confirmed_sales, c.confirmed_stores), (0, 0))

    def test_sale_filter_override_does_not_leak_to_following_shared_filter_call(self):
        override = self.cleanup[P + "筛选经验卡"]["attach"]["sale_filter_override"]
        with self.harness("filter", filter_experience_visible=False,
                          filter_experience_after_reposition=True,
                          filter_experience_after_swipes=2) as (c, seen, run):
            self.assertTrue(run("整理礼物盒-筛卖找经验值", override))
            self.assertEqual(c.steps.count(P + "经验筛选滚动定位"), 1)
            c.scene, c.filter_experience_visible = "filter", False
            c.steps.clear()
            seen.clear()
            self.assertTrue(run("整理礼物盒-筛卖找经验值"))
            self.assertNotIn(P + "经验筛选滚动定位", c.steps)
            self.assertEqual(c.filter_swipes, 2)
            self.assertEqual(seen, ["整理礼物盒-已在灵基图标大小检测"])

    def test_storage_menu_accepts_complete_cn_jp_formation_labels_only(self):
        node = self.cleanup[P + "打开编成"]["recognition"]["param"]
        for text in ("编队", "編隊", "编成", "編成"):
            with self.subTest(text=text):
                self.assertTrue(any(re.search(pattern, text) for pattern in node["expected"]))
        for text in ("编队设置", "推荐编队", "编成中", "编队失败", ""):
            with self.subTest(text=text):
                self.assertFalse(any(re.search(pattern, text) for pattern in node["expected"]))
        menu = self.cleanup[P + "打开菜单"]["recognition"]["param"]
        self.assertEqual(menu["order_by"], "Score")
        self.assertGreaterEqual(menu["roi"][0], 1000)
        self.assertGreaterEqual(menu["roi"][1], 550)

    def test_unapproved_candidate_preview_and_close_never_select_or_submit(self):
        with self.harness("sell_list") as (c, seen, run):
            self.assertTrue(run("预览候选"))
            self.assertEqual(c.scene, "detail")
            self.assertTrue(run("关闭详情"))
            self.assertEqual(c.scene, "sell_list")
            self.assertEqual(seen, [P + "详情就绪", P + "列表就绪"])
            self.assertNotIn(P + "选择候选", c.steps)
            self.assertEqual((c.confirmed_sales, c.confirmed_stores), (0, 0))

    def test_selection_requires_green_frame_confirmation(self):
        for green in (True, False):
            with self.subTest(green=green), self.harness("sell_list", green_confirm=green) as (c, seen, run):
                self.assertEqual(run("选择候选"), green)
                self.assertEqual(seen, [P + "候选已选择"] if green else [])
                self.assertEqual(c.confirmed_sales, 0)

    def test_sell_requires_second_confirmation_and_returns_to_list(self):
        with self.harness("selected") as (c, seen, run):
            self.assertTrue(run("提交贩卖"))
            self.assertEqual(c.confirmed_sales, 1)
            self.assertEqual(seen, [P + "贩卖已完成"])
        with self.harness("selected", sell_confirm=False) as (c, seen, run):
            self.assertFalse(run("提交贩卖"))
            self.assertEqual(c.confirmed_sales, 0)
            self.assertNotIn(P + "确认贩卖", c.steps)

    def test_qp_full_stops_by_default(self):
        with self.harness("selected", qp_full=True) as (c, seen, run):
            self.assertTrue(run("提交贩卖"))
            self.assertEqual(seen, [P + "QP已满"])
            self.assertEqual(c.scene, "qp_full")
            self.assertNotIn(P + "QP已满", c.steps)
            self.assertNotIn(P + "关闭贩卖结果", c.steps)

    def test_qp_continue_is_per_call_override(self):
        with self.harness("selected", qp_full=True) as (c, seen, run):
            patch = self.cleanup[P + "提交贩卖"]["attach"]["aqf_override"]
            self.assertTrue(run("提交贩卖", patch))
            self.assertEqual(seen, [P + "贩卖已完成"])
            self.assertIn(P + "QP已满", c.steps)
            self.assertEqual(c.scene, "sell_list")

    def test_storage_full_does_not_confirm_transfer(self):
        with self.harness("store_selected", storage_full=True) as (c, seen, run):
            self.assertTrue(run("提交存储"))
            self.assertEqual(seen, [P + "保管室已满"])
            self.assertEqual(c.confirmed_stores, 0)

    def test_storage_requires_context_and_closes_success(self):
        with self.harness("store_selected") as (c, seen, run):
            self.assertTrue(run("提交存储"))
            self.assertEqual(c.confirmed_stores, 1)
            self.assertEqual(c.action_boxes[P + "确认存储"], (750, 550, 130, 40))
            self.assertEqual(seen, [P + "存储已完成"])
            self.assertEqual(c.scene, "store_list")
        with self.harness("store_selected", store_context=False) as (c, seen, run):
            self.assertFalse(run("提交存储"))
            self.assertEqual(c.confirmed_stores, 0)

    def test_download_retry_after_preview_returns_to_detail_without_reopening(self):
        with self.harness("sell_list", network_after={P + "预览候选": 1}) as (c, seen, run):
            self.assertTrue(run("预览候选"))
            self.assertEqual(c.steps, [P + "预览候选", N + "重试"])
            self.assertEqual(seen, [P + "详情就绪"])
            self.assertEqual(c.network_click_boxes, [(735, 535, 205, 55)])
            self.assertEqual((c.confirmed_sales, c.confirmed_stores), (0, 0))

    def test_detail_close_waits_for_retry_before_clicking_exposed_background(self):
        with self.harness("detail", network_after={"start": 1}) as (c, seen, run):
            self.assertTrue(run("关闭详情"))
            self.assertEqual(c.steps, [N + "重试", P + "关闭详情"])
            self.assertEqual(c.scene, "sell_list")
            self.assertEqual(seen, [P + "列表就绪"])

    def test_network_recovery_before_and_after_final_submission_never_resubmits(self):
        for scene, entry, final, terminal, counter in (
                ("selected", "提交贩卖", "确认贩卖", "贩卖已完成", "confirmed_sales"),
                ("store_selected", "提交存储", "确认存储", "存储已完成", "confirmed_stores")):
            for blocked in (entry, final):
                for kind in ("download", "normal"):
                    with self.subTest(entry=entry, blocked=blocked, kind=kind), self.harness(
                            scene, network_after={P + blocked: 1}, network_kind=kind) as (c, seen, run):
                        self.assertTrue(run(entry))
                        self.assertEqual(getattr(c, counter), 1)
                        self.assertEqual(c.steps.count(P + entry), 1)
                        self.assertEqual(c.steps.count(P + final), 1)
                        self.assertEqual(c.steps.count(N + "重试"), 1)
                        self.assertEqual(seen, [P + terminal])
                        self.assertEqual(c.network_click_boxes, [(735, 535, 205, 55)])
                        self.assertNotIn(P + "选择候选", c.steps)
                        self.assertNotIn(P + "预览候选", c.steps)

    def test_persistent_network_error_stops_after_three_retries_without_background_success(self):
        for scene, entry, action, counter in (
                ("sell_list", "预览候选", "预览候选", None),
                ("selected", "提交贩卖", "确认贩卖", "confirmed_sales"),
                ("store_selected", "提交存储", "确认存储", "confirmed_stores")):
            with self.subTest(entry=entry), self.harness(
                    scene, network_after={P + action: 4}) as (c, seen, run):
                run(entry)  # StopTask may report handled completion; no business terminal is success.
                self.assertEqual(c.steps.count(N + "重试"), 3)
                self.assertEqual(c.network_remaining, 1)
                self.assertEqual(c.steps.count(P + action), 1)
                self.assertEqual(seen, [])
                if counter:
                    self.assertEqual(getattr(c, counter), 1)
                self.assertTrue(all(x >= 700 and y >= 510 for x, y, _, _ in c.network_click_boxes))

    def test_network_retry_requires_both_dialog_body_and_right_retry_button(self):
        for kind in ("download", "normal"):
            for body, button in ((False, True), (True, False)):
                with self.subTest(kind=kind, body=body, button=button), self.harness(
                        "unknown", network_after={"start": 1}, network_kind=kind,
                        network_body=body, network_button=button) as (c, seen, run):
                    self.assertFalse(run(N + "重试"))
                    self.assertEqual(c.steps, [])
                    self.assertEqual(c.network_click_boxes, [])
                    self.assertEqual(seen, [])

    def test_unknown_page_navigation_never_submits_input(self):
        with self.harness("unknown") as (c, seen, run):
            self.assertFalse(run("导航保管室"))
            self.assertEqual(c.steps, [])
            self.assertEqual(seen, [])

    def test_storage_servant_category_already_selected_never_clicks(self):
        with self.harness("store_list", storage_category="servant") as (c, seen, run):
            self.assertTrue(run("保管室切从者"))
            self.assertEqual(c.steps, [])
            self.assertEqual(seen, [P + "保管室从者就绪"])
            self.assertEqual((c.confirmed_sales, c.confirmed_stores), (0, 0))

    def test_storage_servant_category_switches_once_and_rechecks(self):
        with self.harness("store_list", storage_category="equip") as (c, seen, run):
            self.assertTrue(run("保管室切从者"))
            self.assertEqual(c.steps, [P + "保管室选择从者"])
            self.assertEqual(c.storage_category, "servant")
            self.assertEqual(seen, [P + "保管室从者就绪"])
            self.assertTrue(run("保管室切从者"))
            self.assertEqual(c.steps, [P + "保管室选择从者"])
            self.assertEqual((c.confirmed_sales, c.confirmed_stores), (0, 0))

    def test_storage_category_switch_requires_storage_identity(self):
        for scene in ("sell_list", "unknown"):
            for category in ("servant", "equip"):
                with self.subTest(scene=scene, category=category), self.harness(
                        scene, storage_category=category) as (c, seen, run):
                    self.assertFalse(run("保管室切从者"))
                    self.assertEqual(c.steps, [])
                    self.assertEqual(seen, [])

    def test_storage_category_unknown_tab_never_clicks(self):
        with self.harness("store_list", storage_tab_evidence=False) as (c, seen, run):
            self.assertFalse(run("保管室切从者"))
            self.assertEqual(c.steps, [])
            self.assertEqual(seen, [])

    def test_storage_capacity_reads_remaining_total_at_bottom_right(self):
        node = self.cleanup[P + "保管室容量"]
        self.assertEqual(node["attach"]["capacity_semantics"], "remaining_total")
        self.assertEqual(node["recognition"]["type"], "OCR")
        self.assertEqual(node["recognition"]["param"]["roi"], [1040, 565, 225, 60])
        pattern = node["recognition"]["param"]["expected"][0]
        self.assertIsNotNone(re.fullmatch(pattern, "102/1600"))

    def test_bottom_probe_requires_list_identity_and_never_inputs(self):
        for scene in ("sell_list", "store_list", "unknown"):
            for at_bottom in (True, False):
                with self.subTest(scene=scene, at_bottom=at_bottom), self.harness(
                        scene, at_bottom=at_bottom) as (c, seen, run):
                    expected = scene != "unknown" and at_bottom
                    self.assertEqual(run("列表到底"), expected)
                    self.assertEqual(c.steps, [])
                    self.assertEqual(seen, [P + "列表到底"] if expected else [])

    def test_next_page_at_bottom_has_no_input(self):
        for scene in ("sell_list", "store_list"):
            with self.subTest(scene=scene), self.harness(scene, at_bottom=True) as (c, seen, run):
                self.assertTrue(run("下一页"))
                self.assertEqual(c.steps, [])
                self.assertEqual(seen, [P + "翻页完成"])

    def test_next_page_unknown_has_no_input_even_with_scroll_end(self):
        for at_bottom in (True, False):
            with self.subTest(at_bottom=at_bottom), self.harness(
                    "unknown", at_bottom=at_bottom) as (c, seen, run):
                self.assertFalse(run("下一页"))
                self.assertEqual(c.steps, [])
                self.assertEqual(seen, [])

    def test_next_page_non_bottom_swipes_once_and_rechecks_list(self):
        for scene in ("sell_list", "store_list"):
            with self.subTest(scene=scene), self.harness(scene) as (c, seen, run):
                self.assertTrue(run("下一页"))
                self.assertEqual(c.steps, [P + "滑动下一页"])
                self.assertEqual(seen, [P + "翻页完成"])
        with self.harness("sell_list", page_after_swipe="unknown") as (c, seen, run):
            self.assertFalse(run("下一页"))
            self.assertEqual(c.steps, [P + "滑动下一页"])
            self.assertEqual(seen, [])

    def test_production_selection_color_match_uses_rgb_for_bgr_controller_frames(self):
        node = copy.deepcopy(self.cleanup[P + "候选已选择"])
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, width, height = node["recognition"]["param"]["roi"]
        observed = []

        class BorderController(CleanupMemoryController):
            def screencap(self): return frame.copy()

        controller = BorderController("sell_list")

        class Probe(CustomAction):
            def run(self, context, argv):
                # Controller frames are BGR. Keep the production ColorMatch
                # unchanged so its RGB method/range is exercised by Framework.
                for color in ([221, 249, 110], [190, 140, 50]):
                    frame[y:y + height, x:x + width] = color
                    image = controller.post_screencap().wait().get()
                    detail = context.run_recognition(P + "候选已选择", image)
                    observed.append(bool(detail and detail.hit))
                return True

        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "pipeline").mkdir()
            (bundle / "pipeline/color_probe.json").write_text(json.dumps({
                P + "候选已选择": node,
                "cleanup_color_probe_entry": {"on_error": [], "action": {
                    "type": "Custom", "param": {"custom_action": "cleanup_color_probe"}}},
            }, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
            resource.register_custom_action("cleanup_color_probe", Probe())
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                result = tasker.post_task("cleanup_color_probe_entry").wait().get()
                self.assertTrue(result.status.succeeded)
            finally:
                tasker.post_stop().wait()
        self.assertEqual(observed, [True, False])
        self.assertEqual(controller.unexpected_inputs, [])


if __name__ == "__main__":
    unittest.main()
