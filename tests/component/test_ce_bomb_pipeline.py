"""Production CE Pipeline routing in real MaaFramework, without game input.

Visual predicates and physical actions are controlled doubles. Production
next/on_error/enabled/max_hit branches remain intact; these are framework and
safety-boundary checks, not game screenshot calibration.
"""
from copy import deepcopy
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import time
import unittest

import maa
import cv2
import numpy as np
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.define import LoggingLevelEnum, MaaControllerFeatureEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker


ROOT = Path(__file__).resolve().parents[2]
PREFIX = "搓丸子-"
NETWORK_PREFIX = "搓丸子网络-"


def merge(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
    return result


class NoDeviceController(CustomController):
    def __init__(self):
        super().__init__()
        self.inputs = 0

    def connect(self):
        return True

    def request_uuid(self):
        return "ce-bomb-memory-controller"

    def get_features(self):
        return 0

    def screencap(self):
        return np.zeros((720, 1280, 3), dtype=np.uint8)

    def reject_input(self, *args):
        self.inputs += 1
        return False

    click = swipe = touch_down = touch_move = touch_up = reject_input
    start_app = stop_app = click_key = input_text = key_down = key_up = reject_input


class CeBombPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.ce = json.loads((ROOT / "assets/resource/base/pipeline/搓丸子.json").read_text("utf-8"))
        cls.shared = json.loads((ROOT / "assets/resource/base/pipeline/强化从者.json").read_text("utf-8"))
        cls.shared.update(json.loads((ROOT / "assets/resource/base/pipeline/搓丸子网络.json").read_text("utf-8")))

    def test_qp_shortage_requires_an_explicit_negative_phrase(self):
        patterns = self.ce[PREFIX + "QP不足提示"]["recognition"]["param"]["expected"]
        for text, expected in (
            ("所持QP：2,882,907,116", False),
            ("強化に必要なQP：90", False),
            ("QPは足りています", False),
            ("QPが不足しています", True),
            ("QPが足りません", True),
            ("强化所需量子不足", True),
        ):
            with self.subTest(text=text):
                self.assertEqual(any(re.search(pattern, text) for pattern in patterns), expected)

    def run_graph(self, entry, hits=(), *, overrides=None, on_action=None,
                  state=None, cancel=False, nested_override=None):
        production = deepcopy({**self.shared, **self.ce})
        for name, override in (overrides or {}).items():
            production[name] = merge(production[name], override)
        seen, actions, inspected = [], [], []
        state = state if state is not None else {}
        recognition_started = threading.Event()
        nested_results = []
        hit_set = {name if name.startswith((PREFIX, NETWORK_PREFIX, "强化从者-")) else PREFIX + name for name in hits} if not callable(hits) else None

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                recognition_started.set()
                payload = json.loads(argv.custom_recognition_param)
                inspected.append((argv.node_name, payload["source"]))
                matched = hits(argv.node_name, state, payload["source"]) if callable(hits) else argv.node_name in hit_set or (argv.node_name.endswith("执行") and argv.node_name[:-2] in hit_set)
                return (0, 0, 1, 1) if matched else None

        class Record(CustomAction):
            def run(self, context, argv):
                seen.append(argv.node_name)
                payload = json.loads(argv.custom_action_param)
                if payload["kind"] not in ("DoNothing", "Custom"):
                    actions.append((argv.node_name, payload["kind"]))
                if on_action:
                    on_action(argv.node_name, state)
                return True

        def convert(name, data):
            node = deepcopy(data)
            node.pop("focus", None)
            for key in ("pre_wait_freezes", "post_wait_freezes", "repeat_wait_freezes"):
                node.pop(key, None)
            node.update(pre_delay=0, post_delay=0, rate_limit=1, timeout=30000 if cancel else 35)
            rec = node.get("recognition", {"type": "DirectHit"})
            if rec.get("type", "DirectHit") != "DirectHit":
                node["recognition"] = {"type": "Custom", "param": {
                    "custom_recognition": "ce_screen", "custom_recognition_param": {"source": rec}}}
                node.pop("inverse", None)
            kind = node.get("action", {}).get("type", "DoNothing")
            if kind != "StopTask":
                node["action"] = {"type": "Custom", "param": {
                    "custom_action": "ce_record", "custom_action_param": {"kind": kind}}}
            return node

        nodes = {name: convert(name, data) for name, data in production.items()}
        if nested_override is not None:
            converted_override = {}
            for name, override in nested_override.items():
                converted_override[name] = convert(name, merge(production[name], override))

            class Dispatch(CustomAction):
                def run(self, context, argv):
                    before = deepcopy(context.get_node_data("强化从者-点从者强化条"))
                    result = context.run_task(entry, pipeline_override=converted_override)
                    after = context.get_node_data("强化从者-点从者强化条")
                    nested_results.append((result.status.succeeded, before, after))
                    return bool(result.status.succeeded)

            nodes["ce_test_dispatch"] = {"action": {"type": "Custom", "param": {"custom_action": "ce_dispatch"}}}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "pipeline/ce.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(root).wait().succeeded)
            resource.register_custom_recognition("ce_screen", Screen())
            resource.register_custom_action("ce_record", Record())
            if nested_override is not None:
                resource.register_custom_action("ce_dispatch", Dispatch())
            controller = NoDeviceController()
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            job = tasker.post_task("ce_test_dispatch" if nested_override is not None else entry)
            try:
                if cancel:
                    self.assertTrue(recognition_started.wait(2), "没有进入可取消的识别等待")
                    tasker.post_stop().wait()
                    prior = list(actions)
                    time.sleep(.05)
                    self.assertEqual(actions, prior, "停止后仍出现新动作")
                deadline = time.monotonic() + 5
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertTrue(job.status.done, "生产流程未有界结束")
                succeeded = job.status.succeeded
            finally:
                tasker.post_stop().wait()
            self.assertEqual(controller.inputs, 0, "测试不得向任何真实设备发起输入")
        return succeeded, seen, actions, inspected, nested_results

    def test_preview_never_selects_base_or_submits_materials(self):
        for suffix, end in (("预览底卡", "底卡详情就绪"), ("预览素材", "素材详情就绪")):
            with self.subTest(entry=suffix):
                ready = "底卡详情显示" if suffix == "预览底卡" else "素材详情显示"
                ok, seen, actions, _, _ = self.run_graph(PREFIX + suffix, {suffix, ready})
                self.assertTrue(ok)
                self.assertIn(PREFIX + end, seen)
                self.assertEqual(actions, [(PREFIX + suffix, "LongPress")])
                self.assertNotIn(PREFIX + "提交强化", seen)
                self.assertNotIn(PREFIX + "选定底卡", seen)

    def test_preview_targets_use_native_centre_offset_with_runtime_card_boxes(self):
        entries = (PREFIX + "预览底卡", PREFIX + "预览素材", PREFIX + "预览当前底卡")
        for name in entries:
            self.assertEqual(self.ce[name]["action"]["type"], "LongPress")
            self.assertEqual(self.ce[name]["action"]["param"]["duration"], 1500)
        for name in entries[:2]:
            self.assertEqual(self.ce[name]["action"]["param"]["target"], [74, 190, 117, 128])
            self.assertEqual(self.ce[name]["action"]["param"]["target_offset"], [58, 64, -116, -127])
        self.assertEqual(self.ce[entries[2]]["action"]["param"]["target"], [190, 320, 1, 1])
        observed, down_at = [], []

        class TouchController(NoDeviceController):
            def get_features(self):
                return MaaControllerFeatureEnum.UseMouseDownAndUpInsteadOfClick

            def touch_down(self, contact, x, y, pressure):
                down_at.append(time.monotonic())
                observed.append(("down", contact, x, y))
                return True

            def touch_up(self, contact):
                observed.append(("up", contact, time.monotonic() - down_at[-1]))
                return True

        cases = [(entries[0], None, [74, 190, 117, 128]),
                 (entries[0], [208, 323, 123, 134], [208, 323, 123, 134]),
                 (entries[1], None, [74, 190, 117, 128]),
                 (entries[1], [741, 465, 123, 134], [741, 465, 123, 134]),
                 (entries[2], None, [190, 320, 1, 1])]
        completed = []

        class Probe(CustomAction):
            def run(self, context, argv):
                for name, target, _box in cases:
                    patch = {name: {"action": {"param": {"target": target}}}} if target else {}
                    result = context.run_action(name, pipeline_override=patch)
                    completed.append(bool(result and result.success))
                return all(completed)

        nodes = {name: merge(self.ce[name], {"next": [], "on_error": [], "pre_delay": 0, "post_delay": 0})
                 for name in entries}
        nodes["ce_preview_touch_probe"] = {"on_error": [], "action": {"type": "Custom", "param": {
            "custom_action": "ce_preview_touch_probe"}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "pipeline/preview.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(root).wait().succeeded)
            resource.register_custom_action("ce_preview_touch_probe", Probe())
            controller = TouchController()
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(controller.post_screencap().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                self.assertTrue(tasker.post_task("ce_preview_touch_probe").wait().succeeded,
                                (completed, observed))
            finally:
                tasker.post_stop().wait()
            self.assertEqual(controller.inputs, 0)
        self.assertEqual(completed, [True] * len(cases))
        self.assertEqual(len(observed), 2 * len(cases))
        for index, (name, _target, box) in enumerate(cases):
            down, up = observed[2 * index:2 * index + 2]
            self.assertEqual((down[0], up[0], down[1]), ("down", "up", up[1]))
            self.assertGreaterEqual(up[2], 1.45)
            x, y = down[2:4]
            left, top, width, height = box
            self.assertTrue(left <= x < left + width and top <= y < top + height, (name, box, down))
            if name != entries[2]:
                self.assertTrue(left + 58 <= x < left + width - 58, (name, box, down))
                self.assertTrue(top + 64 <= y < top + height - 63, (name, box, down))
            else:
                self.assertEqual((x, y), (190, 320))

    def test_missing_preview_detail_never_repeats_long_press(self):
        for suffix in ("预览底卡", "预览素材", "预览当前底卡"):
            with self.subTest(entry=suffix):
                ok, seen, actions, _, _ = self.run_graph(PREFIX + suffix, {suffix})
                self.assertTrue(ok)
                self.assertIn(PREFIX + "识别失败", seen)
                self.assertEqual(actions, [(PREFIX + suffix, "LongPress")])
                self.assertNotIn(PREFIX + "选定底卡", seen)
                self.assertNotIn(PREFIX + "提交强化", seen)

    def test_detail_level_uses_only_rec_in_the_fixed_level_row_without_lower_threshold(self):
        for suffix in ("底卡等级", "材料等级"):
            with self.subTest(node=suffix):
                param = self.ce[PREFIX + suffix]["recognition"]["param"]
                self.assertEqual(param["roi"], [790, 240, 160, 70])
                self.assertTrue(param["only_rec"])
                self.assertEqual(param["threshold"], .8)

    def test_all_ce_grid_modes_converge_to_small_icons_from_each_known_density(self):
        for mode, end in (("底卡", "底卡打开排序"), ("素材", "素材打开排序"),
                          ("成品", "成品打开排序"), ("保管室", "保管室打开排序")):
            normal = PREFIX + mode + "图标正常"
            adjust = {PREFIX + mode + "调整大图标": "大", PREFIX + mode + "调整小图标": "中"}
            self.assertEqual(self.ce[normal]["recognition"]["param"]["template"],
                             "整理礼物盒/图标缩放按钮小.png")
            for name, density in adjust.items():
                self.assertEqual(self.ce[name]["recognition"]["param"]["template"],
                                 f"整理礼物盒/图标缩放按钮{density}.png")
                self.assertEqual(self.ce[name]["max_hit"], 3)
            # Either rotation direction must be safe: only known wrong states
            # are clicked, and the small-icon state ends the loop immediately.
            for direction in (1, -1):
                for initial in ("大", "中", "小"):
                    with self.subTest(mode=mode, initial=initial, direction=direction):
                        state = {"density": initial}

                        def hit(name, state, source):
                            if name == normal or name in adjust:
                                return source["param"]["template"] == (
                                    f"整理礼物盒/图标缩放按钮{state['density']}.png")
                            return False

                        def acted(name, state):
                            if name in adjust:
                                values = ("大", "中", "小")
                                state["density"] = values[(values.index(state["density"]) + direction) % 3]

                        ok, seen, actions, _, _ = self.run_graph(PREFIX + mode + "图标分派", hit,
                            overrides={normal: {"next": []}}, on_action=acted, state=state)
                        self.assertTrue(ok)
                        self.assertEqual(state["density"], "小")
                        self.assertIn(normal, seen)
                        self.assertNotIn(PREFIX + "识别失败", seen)
                        self.assertTrue(all(name in adjust and kind == "Click" for name, kind in actions))
                        self.assertEqual(len(actions), 0 if initial == "小" else
                                         ((2 - ("大", "中", "小").index(initial)) * direction) % 3)
                        self.assertLessEqual(len(actions), 2)
            self.assertEqual(self.ce[normal]["next"], [PREFIX + end])

    def test_ce_density_unknown_state_never_clicks_and_unresponsive_buttons_are_bounded(self):
        for mode in ("底卡", "素材", "成品", "保管室"):
            with self.subTest(mode=mode, density="unknown"):
                ok, seen, actions, _, _ = self.run_graph(PREFIX + mode + "图标分派")
                self.assertTrue(ok)
                self.assertIn(PREFIX + "识别失败", seen)
                self.assertEqual(actions, [])
            for suffix in ("调整大图标", "调整小图标"):
                with self.subTest(mode=mode, density=suffix):
                    name = PREFIX + mode + suffix
                    ok, seen, actions, _, _ = self.run_graph(PREFIX + mode + "图标分派", {name})
                    self.assertTrue(ok)
                    self.assertIn(PREFIX + "识别失败", seen)
                    self.assertEqual(actions, [(name, "Click")] * 3)
                    self.assertNotIn(PREFIX + mode + "图标正常", seen)

    def test_ce_seven_column_grid_and_preview_dynamic_target_contract_are_preserved(self):
        grid = self.ce[PREFIX + "制作一轮"]["attach"]["grid"]
        self.assertEqual(grid["x"], [74, 207, 341, 474, 607, 741, 874])
        self.assertEqual(grid["y"], [190, 332, 474])
        self.assertEqual((grid["w"], grid["h"], grid["columns"], grid["rows"]), (117, 128, 7, 3))
        for suffix in ("预览底卡", "预览素材"):
            param = self.ce[PREFIX + suffix]["action"]["param"]
            self.assertEqual(param["target"], [74, 190, 117, 128])
            self.assertEqual(param["target_offset"], [58, 64, -116, -127])
            self.assertEqual(param["duration"], 1500)

    def test_star_selection_is_mutually_exclusive_and_fixed_sort_finishes(self):
        for star in (1, 2):
            with self.subTest(star=star):
                state = {"selected": False}
                always = {PREFIX + x for x in ("底卡只选丸子星级", "底卡筛选确认", "底卡图标正常",
                         "底卡打开排序", "底卡确认排序", "底卡列表固定完成")}

                def hit(name, state, source):
                    return state["selected"] if name == PREFIX + "底卡星级已选" else name in always

                def acted(name, state):
                    if name == PREFIX + "底卡只选丸子星级":
                        state["selected"] = True

                override = {PREFIX + "底卡只选丸子星级": {"recognition": {"param": {
                                "template": f"整理礼物盒/{star}星未选中.png"}}},
                            PREFIX + "底卡星级已选": {"recognition": {"param": {
                                "template": f"搓丸子/{star}星已选中.png"}}}}
                ok, seen, actions, inspected, _ = self.run_graph(PREFIX + "底卡筛选分派", hit,
                                  overrides=override, on_action=acted, state=state)
                self.assertTrue(ok)
                self.assertIn(PREFIX + "底卡候选就绪", seen)
                selected = [source["param"]["template"] for name, source in inspected
                            if name == PREFIX + "底卡只选丸子星级"]
                self.assertEqual(set(selected), {f"整理礼物盒/{star}星未选中.png"})
                self.assertEqual(sum(name == PREFIX + "底卡只选丸子星级" for name, _ in actions), 1)

    def test_close_details_requires_visible_list_or_selected_base_page(self):
        for destination in ("关闭回列表", "关闭回强化页", None):
            with self.subTest(destination=destination):
                hits = {"关闭详情"} | ({destination} if destination else set())
                ok, seen, _, _, _ = self.run_graph(PREFIX + "关闭详情", hits)
                self.assertTrue(ok)  # Unknown pages end through the explicit failure terminal.
                self.assertIn(PREFIX + ("详情已关闭" if destination else "识别失败"), seen)
                if destination is None:
                    self.assertNotIn(PREFIX + "详情已关闭", seen)

    def test_material_selection_cannot_complete_without_selected_frame(self):
        ok, seen, actions, _, _ = self.run_graph(PREFIX + "选择素材", {"选择素材"})
        self.assertTrue(ok)
        self.assertEqual(actions, [(PREFIX + "选择素材", "Click")])
        self.assertIn(PREFIX + "识别失败", seen)
        self.assertNotIn(PREFIX + "选择素材完成", seen)

    def test_submit_requires_second_confirmation_and_returns_batch_only(self):
        ok, seen, actions, _, _ = self.run_graph(PREFIX + "提交强化",
                        {"提交强化", "强化二次确认", "强化结果页"})
        self.assertTrue(ok)
        self.assertIn(PREFIX + "强化一批完成", seen)
        self.assertNotIn(PREFIX + "成品完成", seen)
        self.assertLess(seen.index(PREFIX + "提交强化"), seen.index(PREFIX + "强化二次确认"))
        self.assertEqual(actions, [(PREFIX + "提交强化执行", "Click"), (PREFIX + "强化二次确认", "Click")])

    def test_qp_shortage_never_confirms_or_counts_completion(self):
        ok, seen, actions, _, _ = self.run_graph(PREFIX + "提交强化", {"提交强化", "QP不足提示"})
        self.assertTrue(ok)
        self.assertIn(PREFIX + "QP不足", seen)
        self.assertEqual(actions, [(PREFIX + "提交强化执行", "Click")])
        self.assertNotIn(PREFIX + "强化二次确认", seen)
        self.assertNotIn(PREFIX + "强化一批完成", seen)

    def test_next_and_error_routes_are_distinct_and_high_star_rejection_keeps_one_failure_terminal(self):
        # maa-tools 1.1.3 treats next/on_error as a shared set of routes.
        for name, node in self.ce.items():
            with self.subTest(node=name):
                routes = [target.removeprefix("[JumpBack]")
                          for key in ("next", "on_error") for target in node.get(key, [])]
                self.assertEqual(len(routes), len(set(routes)), routes)
        reject = PREFIX + "拒绝未授权高星材料"
        for warning in ("高星素材决定确认", "高星材料确认"):
            node = self.ce[PREFIX + warning]
            self.assertEqual(node["next"], [reject])
            self.assertEqual(node["on_error"], [PREFIX + "识别失败"])
        self.assertEqual(self.ce[reject]["next"], [PREFIX + "识别失败"])
        self.assertEqual(self.ce[reject]["on_error"], [])
        self.assertEqual(self.ce[reject].get("action", {}).get("type", "DoNothing"), "DoNothing")
        self.assertEqual(self.ce[PREFIX + "识别失败"].get("next", []), [])

    def test_high_star_warning_stops_by_default_at_both_stages(self):
        for entry, warning in (("确认素材", "高星素材决定确认"), ("提交强化", "高星材料确认")):
            with self.subTest(entry=entry):
                ok, seen, actions, _, _ = self.run_graph(PREFIX + entry, {entry, warning})
                self.assertTrue(ok)
                self.assertEqual(seen.count(PREFIX + "拒绝未授权高星材料"), 1)
                self.assertEqual(seen.count(PREFIX + "识别失败"), 1)
                self.assertEqual(actions, [(PREFIX + entry + "执行", "Click")])
                self.assertNotIn(PREFIX + "素材已选择", seen)
                self.assertNotIn(PREFIX + "强化一批完成", seen)
                self.assertNotIn(PREFIX + "成品完成", seen)

    def test_high_star_local_authorization_requires_button_and_normal_followup(self):
        for material_stage in (True, False):
            for button in (True, False):
                with self.subTest(material_stage=material_stage, button=button):
                    entry = "确认素材" if material_stage else "提交强化"
                    warning = "高星素材决定确认" if material_stage else "高星材料确认"
                    authorized = "高星素材决定授权确认" if material_stage else "高星材料授权确认"
                    state = {"warning": True, "button": button}

                    def hit(name, state, source):
                        if name == PREFIX + entry + "执行":
                            return True
                        if name == PREFIX + warning:
                            return state["warning"]
                        if name == PREFIX + authorized:
                            guards = source["param"]["all_of"]
                            self.assertEqual(guards[0]["recognition"]["type"], "OCR")
                            self.assertEqual(guards[1]["recognition"]["param"]["template"],
                                             "搓丸子/高星素材确认决定.png")
                            return state["warning"] and state["button"]
                        return not state["warning"] and name in {PREFIX + "素材回到强化页",
                                      PREFIX + "强化二次确认", PREFIX + "强化结果页"}

                    def acted(name, state):
                        if name == PREFIX + authorized:
                            state["warning"] = False

                    ok, seen, actions, _, _ = self.run_graph(PREFIX + entry, hit,
                        overrides={PREFIX + warning: {"next": [PREFIX + authorized]}},
                        state=state, on_action=acted)
                    self.assertTrue(ok)
                    self.assertEqual(sum(name == PREFIX + authorized for name, _ in actions), int(button))
                    if not button:
                        self.assertIn(PREFIX + "识别失败", seen)
                        self.assertEqual(actions, [(PREFIX + entry + "执行", "Click")])
                    elif material_stage:
                        self.assertIn(PREFIX + "素材已选择", seen)
                        self.assertNotIn(PREFIX + "提交强化", seen)
                    else:
                        self.assertIn(PREFIX + "强化二次确认", seen)
                        self.assertIn(PREFIX + "强化一批完成", seen)
                        self.assertNotIn(PREFIX + "成品完成", seen)

    def test_unknown_page_has_no_input(self):
        for entry in ("关闭详情", "确认素材", "提交强化", "强化结果等待",
                      "锁定当前礼装", "标记当前候选", "存入成品"):
            with self.subTest(entry=entry):
                ok, seen, actions, _, _ = self.run_graph(PREFIX + entry)
                self.assertTrue(ok)
                self.assertIn(PREFIX + "识别失败", seen)
                self.assertEqual(actions, [])

    def test_network_retries_resume_material_submit_and_storage_waits_once(self):
        cases = (
            ("确认素材", ("确认素材执行",), {"素材回到强化页"}, "素材已选择"),
            ("提交强化", ("提交强化执行", "强化二次确认"), {"强化结果页"}, "强化一批完成"),
            ("存入成品", ("存入成品执行", "确认存入", "存储完成提示"), set(), "存储一批完成"),
        )
        for entry, inputs, final_guards, terminal in cases:
            with self.subTest(entry=entry):
                state = {"popup": False, "retries": 0}
                guards = {PREFIX + name for name in (*inputs, *final_guards)}

                def hit(name, state, source):
                    if name.startswith(NETWORK_PREFIX):
                        return state["popup"]
                    # Normal controls may remain visible behind the dialog.
                    return name in guards

                def acted(name, state):
                    if name == NETWORK_PREFIX + "重试":
                        state["popup"] = False
                        state["retries"] += 1
                    elif name in {PREFIX + item for item in inputs}:
                        state["popup"] = True

                ok, seen, actions, _, _ = self.run_graph(PREFIX + entry, hit,
                                                       state=state, on_action=acted)
                self.assertTrue(ok)
                self.assertIn(PREFIX + terminal, seen)
                self.assertEqual(state["retries"], len(inputs))
                for name in inputs:
                    self.assertEqual(actions.count((PREFIX + name, "Click")), 1)
                self.assertTrue(all(name == NETWORK_PREFIX + "重试" or name in guards
                                    for name, _ in actions))

    def test_network_exhaustion_stops_before_background_confirmation(self):
        state = {"popup": False}

        def hit(name, state, source):
            if name.startswith(NETWORK_PREFIX):
                return state["popup"]
            return name in {PREFIX + "提交强化执行", PREFIX + "强化二次确认",
                            PREFIX + "强化结果页"}

        def acted(name, state):
            if name == PREFIX + "提交强化执行":
                state["popup"] = True

        ok, seen, actions, inspected, _ = self.run_graph(PREFIX + "提交强化", hit,
                                                       state=state, on_action=acted)
        self.assertTrue(ok)  # StopTask terminates successfully without a business terminal.
        self.assertEqual(self.shared[NETWORK_PREFIX + "重试耗尽"]["action"]["type"], "StopTask")
        self.assertIn(NETWORK_PREFIX + "重试耗尽", {name for name, _ in inspected})
        self.assertNotIn(PREFIX + "识别失败", seen)
        self.assertEqual(actions, [(PREFIX + "提交强化执行", "Click")]
                         + [(NETWORK_PREFIX + "重试", "Click")] * 3)
        self.assertNotIn(PREFIX + "强化二次确认", seen)
        self.assertNotIn(PREFIX + "强化一批完成", seen)

    def test_detail_download_retries_never_repeat_long_press_or_accept_background(self):
        for entry, ready, terminal in (
            ("预览底卡", "底卡详情显示", "底卡详情就绪"),
            ("预览素材", "素材详情显示", "素材详情就绪"),
            ("预览当前底卡", "底卡详情显示", "底卡详情就绪"),
        ):
            for persistent in (False, True):
                with self.subTest(entry=entry, persistent=persistent):
                    state = {"popup": 0}

                    def hit(name, state, source):
                        if name.startswith(NETWORK_PREFIX):
                            return bool(state["popup"])
                        return name in {PREFIX + entry, PREFIX + ready}

                    def acted(name, state):
                        if name == PREFIX + entry:
                            state["popup"] = 2
                        elif name == NETWORK_PREFIX + "重试" and not persistent:
                            state["popup"] -= 1

                    ok, seen, actions, _, _ = self.run_graph(PREFIX + entry, hit,
                                                           state=state, on_action=acted)
                    self.assertTrue(ok)  # Maa StopTask also reports a successful task status.
                    self.assertEqual(actions.count((PREFIX + entry, "LongPress")), 1)
                    self.assertEqual(actions.count((NETWORK_PREFIX + "重试", "Click")),
                                     3 if persistent else 2)
                    self.assertEqual(PREFIX + terminal in seen, not persistent)
                    self.assertTrue(all(name in {PREFIX + entry, NETWORK_PREFIX + "重试"}
                                        for name, _ in actions))

    def test_detail_close_checks_network_before_and_after_one_close(self):
        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                state = {"popup": 2}

                def hit(name, state, source):
                    if name.startswith(NETWORK_PREFIX):
                        return bool(state["popup"])
                    return name in {PREFIX + "关闭详情执行", PREFIX + "关闭回列表"}

                def acted(name, state):
                    if name == NETWORK_PREFIX + "重试" and not persistent:
                        state["popup"] -= 1
                    elif name == PREFIX + "关闭详情执行":
                        state["popup"] = 1

                ok, seen, actions, _, _ = self.run_graph(PREFIX + "关闭详情", hit,
                                                       state=state, on_action=acted)
                self.assertTrue(ok)  # Maa StopTask also reports a successful task status.
                self.assertEqual(actions.count((PREFIX + "关闭详情执行", "Click")),
                                 0 if persistent else 1)
                self.assertEqual(PREFIX + "详情已关闭" in seen, not persistent)
                self.assertEqual(actions[:2], [(NETWORK_PREFIX + "重试", "Click")] * 2)

    def test_lock_retry_rechecks_state_without_toggling_again(self):
        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                state = {"popup": True, "locked": False}

                def hit(name, state, source):
                    if name.startswith(NETWORK_PREFIX):
                        return state["popup"]
                    if name == PREFIX + "锁定详情已开":
                        return True
                    if name == PREFIX + "详情锁已开":
                        return state["locked"]
                    if name == PREFIX + "点击详情锁定":
                        return not state["locked"]
                    return False

                def acted(name, state):
                    if name == NETWORK_PREFIX + "重试" and not persistent:
                        state["popup"] = False
                    elif name == PREFIX + "点击详情锁定":
                        state.update(locked=True, popup=True)

                ok, seen, actions, _, _ = self.run_graph(PREFIX + "锁定当前礼装", hit,
                                                       state=state, on_action=acted)
                self.assertTrue(ok)  # Maa StopTask also reports a successful task status.
                self.assertEqual(actions.count((PREFIX + "点击详情锁定", "Click")),
                                 0 if persistent else 1)
                self.assertEqual(PREFIX + "半成品保护完成" in seen, not persistent)

    def test_mark_retry_returns_to_verification_without_toggling_again(self):
        state = {"popup": True, "marked": False}

        def hit(name, state, source):
            if name.startswith(NETWORK_PREFIX):
                return state["popup"]
            if name == PREFIX + "候选已有标记":
                return state["marked"]
            if name == PREFIX + "给候选补标记":
                return not state["marked"]
            return name == PREFIX + "候选标记后复查" and state["marked"]

        def acted(name, state):
            if name == NETWORK_PREFIX + "重试":
                state["popup"] = False
            elif name == PREFIX + "给候选补标记":
                state.update(marked=True, popup=True)

        ok, seen, actions, _, _ = self.run_graph(PREFIX + "标记当前候选", hit,
                                               state=state, on_action=acted)
        self.assertTrue(ok)
        self.assertIn(PREFIX + "候选标记完成", seen)
        self.assertEqual(actions.count((PREFIX + "给候选补标记", "Click")), 1)
        self.assertEqual(actions.count((NETWORK_PREFIX + "重试", "Click")), 2)

    def test_composite_clicks_use_button_box_in_real_framework(self):
        # Keep real And/Or evaluation, including nested identity guards. A full
        # recognition double would conceal Maa's default box_index=0 behaviour.
        targets = {
            "关闭详情执行": "友情召唤/详情关闭按钮.png",
            "提交强化执行": "从者技能强化/强化按钮.png",
            "保护半成品退出素材列表": "强化从者/返回强化菜单按钮.png",
            "保护半成品关闭详情执行": "友情召唤/详情关闭按钮.png",
            "保护成品关闭详情执行": "友情召唤/详情关闭按钮.png",
        }
        composite_clicks = {name for name, node in self.ce.items()
            if node.get("action", {}).get("type") == "Click"
            and "target" not in node.get("action", {}).get("param", {})
            and node.get("recognition", {}).get("type") in ("And", "Or")}
        self.assertEqual(composite_clicks, {PREFIX + name for name in targets})
        expected_box, guard_box = [1000, 610, 100, 40], [10, 150, 45, 35]
        received = []

        class Box(CustomRecognition):
            def analyze(self, context, argv):
                return tuple(json.loads(argv.custom_recognition_param)["box"])

        class Capture(CustomAction):
            def run(self, context, argv):
                received.append((argv.node_name, list(argv.box)))
                return True

        def preserve_composites(recognition, wanted_template):
            source = deepcopy(recognition)
            kind = source["type"]
            if kind in ("And", "Or"):
                field = "all_of" if kind == "And" else "any_of"
                for child in source["param"][field]:
                    self.assertIsInstance(child, dict)
                    child["recognition"] = preserve_composites(child["recognition"], wanted_template)
                return source
            templates = source.get("param", {}).get("template", [])
            if isinstance(templates, str):
                templates = [templates]
            box = expected_box if wanted_template in templates else guard_box
            return {"type": "Custom", "param": {"custom_recognition": "ce_box",
                    "custom_recognition_param": {"box": box}}}

        nodes = {}
        for suffix, wanted in targets.items():
            name = PREFIX + suffix
            original = self.ce[name]
            nodes[name] = {"recognition": preserve_composites(original["recognition"], wanted),
                           "action": {"type": "Custom", "param": {"custom_action": "ce_capture"}},
                           "next": [], "timeout": 100, "pre_delay": 0, "post_delay": 0}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "pipeline/boxes.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(root).wait().succeeded)
            resource.register_custom_recognition("ce_box", Box())
            resource.register_custom_action("ce_capture", Capture())
            controller = NoDeviceController()
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                for name in nodes:
                    self.assertTrue(tasker.post_task(name).wait().succeeded)
            finally:
                tasker.post_stop().wait()
            self.assertEqual(controller.inputs, 0)
        self.assertEqual(received, [(name, expected_box) for name in nodes])

    def test_protect_existing_locked_half_product_does_not_toggle_lock(self):
        ok, seen, actions, _, _ = self.run_graph(PREFIX + "保护半成品", {
            "保护半成品详情已开", "保护半成品锁已开", "保护半成品关闭详情", "保护半成品关闭复查"})
        self.assertTrue(ok)
        self.assertIn(PREFIX + "半成品保护完成", seen)
        self.assertNotIn(PREFIX + "保护半成品点击锁", seen)
        self.assertEqual(actions, [(PREFIX + "保护半成品关闭详情执行", "Click")])

    def test_existing_mark_is_not_toggled_off(self):
        ok, seen, actions, _, _ = self.run_graph(PREFIX + "标记当前候选", {"候选已有标记"})
        self.assertTrue(ok)
        self.assertIn(PREFIX + "候选标记完成", seen)
        self.assertNotIn(PREFIX + "给候选补标记", seen)
        self.assertEqual(actions, [])

    def test_product_protection_never_marks_unknown_first_cell(self):
        ok, seen, actions, _, _ = self.run_graph(PREFIX + "保护成品", {
            "保护成品详情已开", "保护成品锁已开", "保护成品关闭详情", "保护成品打开列表", "保护成品列表就绪"})
        self.assertTrue(ok)
        self.assertIn(PREFIX + "成品待标记", seen)
        self.assertNotIn(PREFIX + "标记当前候选", seen)
        self.assertNotIn(PREFIX + "成品完成", seen)
        self.assertNotIn((PREFIX + "给候选补标记", "Click"), actions)

    def test_completed_cards_are_enumerated_only_after_smart_filter_is_off(self):
        state = {"smart_filter": True}
        always = {PREFIX + x for x in ("成品列表准备", "成品图标正常", "成品打开排序")}

        def hit(name, state, source):
            if name == PREFIX + "成品关闭智能筛选":
                self.assertEqual(source["param"]["template"], "强化从者/自动加载已选中.png")
                return state["smart_filter"]
            if name == PREFIX + "成品确认排序":
                smart = source["param"]["all_of"][2]["recognition"]["param"]["template"]
                self.assertEqual(smart, "强化从者/自动加载未选中.png")
                return not state["smart_filter"]
            if name == PREFIX + "成品列表固定完成":
                return not state["smart_filter"]
            return name in always

        def acted(name, state):
            if name == PREFIX + "成品关闭智能筛选":
                state["smart_filter"] = False

        ok, seen, actions, _, _ = self.run_graph(PREFIX + "成品列表准备", hit,
                                               on_action=acted, state=state)
        self.assertTrue(ok)
        self.assertFalse(state["smart_filter"])
        self.assertIn(PREFIX + "成品列表就绪", seen)
        self.assertEqual(sum(name == PREFIX + "成品关闭智能筛选" for name, _ in actions), 1)

    def test_next_page_is_bounded_and_never_swipes_unknown_or_completed_lists(self):
        cases = [
            ({"翻页前已到底"}, [], "翻页完成"),
            ({"滑动下一页", "翻页后列表复查"}, [(PREFIX + "滑动下一页", "Swipe")], "翻页完成"),
            (set(), [], "识别失败"),
        ]
        for hits, expected_actions, terminal in cases:
            with self.subTest(hits=hits):
                ok, seen, actions, _, _ = self.run_graph(PREFIX + "下一页", hits)
                self.assertTrue(ok)
                self.assertEqual(actions, expected_actions)
                self.assertIn(PREFIX + terminal, seen)
                self.assertLessEqual(sum(kind == "Swipe" for _, kind in actions), 1)

    def test_previous_page_requires_list_and_performs_only_one_swipe(self):
        for hits, expected in (({"上一页", "翻页后列表复查"}, [(PREFIX + "上一页", "Swipe")]), (set(), [])):
            with self.subTest(hits=hits):
                ok, seen, actions, _, _ = self.run_graph(PREFIX + "上一页", hits)
                self.assertTrue(ok)
                self.assertEqual(actions, expected)
                self.assertIn(PREFIX + ("翻页完成" if hits else "识别失败"), seen)

    def test_local_ce_navigation_override_does_not_change_original_servant_flow(self):
        state = {"selected": False}
        hits = {"强化从者-点从者强化条"} | {PREFIX + x for x in (
            "详情判定", "打开底卡列表", "底卡列表已打开", "底卡筛选准备", "底卡筛选重置",
            "底卡星级区就绪", "底卡只选丸子星级", "底卡筛选确认", "底卡图标正常", "底卡打开排序", "底卡确认排序", "底卡列表固定完成")}

        def hit(name, state, source):
            return state["selected"] if name == PREFIX + "底卡星级已选" else name in hits

        def acted(name, state):
            if name == PREFIX + "底卡只选丸子星级":
                state["selected"] = True

        override = self.ce[PREFIX + "制作一轮"]["attach"]["ce_navigation_override"]
        ok, seen, _, _, nested = self.run_graph("强化从者-点从者强化条", hit,
                            on_action=acted, state=state, nested_override=override)
        self.assertTrue(ok)
        self.assertIn(PREFIX + "底卡候选就绪", seen)
        self.assertEqual(len(nested), 1)
        succeeded, before, after = nested[0]
        self.assertTrue(succeeded)
        self.assertEqual(before["next"], after["next"])
        self.assertEqual(before["recognition"], after["recognition"])
        self.assertNotEqual(before["next"], override["强化从者-点从者强化条"]["next"])

    def test_filter_reset_is_bounded_and_never_swipes_an_unknown_modal(self):
        for initial_ready, modal_known, expected_swipes, terminal in (
            (True, True, 0, "底卡星级区就绪"),
            (False, True, 1, "底卡星级区就绪"),
            (False, False, 0, "识别失败"),
        ):
            with self.subTest(initial_ready=initial_ready, modal_known=modal_known):
                state = {"ready": initial_ready}

                def hit(name, state, source):
                    if name == PREFIX + "底卡星级区就绪":
                        return modal_known and state["ready"]
                    return name == PREFIX + "底卡筛选向上复位" and modal_known

                def acted(name, state):
                    if name == PREFIX + "底卡筛选向上复位":
                        state["ready"] = True

                ok, seen, actions, _, _ = self.run_graph(PREFIX + "底卡筛选复位", hit,
                    overrides={PREFIX + "底卡星级区就绪": {"next": []}},
                    on_action=acted, state=state)
                self.assertTrue(ok)
                self.assertIn(PREFIX + terminal, seen)
                self.assertEqual(sum(kind == "Swipe" for _, kind in actions), expected_swipes)

        ok, seen, actions, _, _ = self.run_graph(PREFIX + "底卡筛选复位", {"底卡筛选向上复位"})
        self.assertTrue(ok)
        self.assertIn(PREFIX + "识别失败", seen)
        self.assertEqual(actions, [(PREFIX + "底卡筛选向上复位", "Swipe")] * 9)

    def test_no_matching_base_cancels_filter_without_selecting_or_submitting(self):
        for hits, expected in (
            ({"底卡筛选无对象", "底卡无对象取消筛选", "底卡无对象返回列表"}, "无底卡"),
            (set(), "识别失败"),
        ):
            with self.subTest(hits=hits):
                ok, seen, actions, _, _ = self.run_graph(PREFIX + "底卡筛选待确认", hits)
                self.assertTrue(ok)
                self.assertIn(PREFIX + expected, seen)
                self.assertEqual(actions, [(PREFIX + "底卡无对象取消筛选", "Click")] if hits else [])
                self.assertNotIn(PREFIX + "选定底卡", seen)
                self.assertNotIn(PREFIX + "提交强化", seen)

    def test_positive_empty_probe_in_real_context_distinguishes_template_and_card(self):
        node = deepcopy(self.ce[PREFIX + "候选为空"])
        self.assertNotIn("inverse", node)
        template = cv2.imdecode(np.fromfile(
            ROOT / "assets/resource/base/image/搓丸子/卡片空位.png", np.uint8), cv2.IMREAD_COLOR)
        h, w = template.shape[:2]
        image = np.random.default_rng(81).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
        empty_image = image.copy()
        empty_image[190:190+h, 74:74+w] = template
        observed = []

        class Probe(CustomAction):
            def run(self, context, argv):
                for frame in (empty_image, image):
                    detail = context.run_recognition(PREFIX + "候选为空", frame)
                    observed.append(bool(detail and detail.hit))
                return True

        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "pipeline").mkdir()
            (bundle / "image/搓丸子").mkdir(parents=True)
            for filename in node["recognition"]["param"]["template"]:
                source = ROOT / "assets/resource/base/image" / filename
                (bundle / "image" / filename).write_bytes(source.read_bytes())
            (bundle / "pipeline/probe.json").write_text(json.dumps({
                PREFIX + "候选为空": node,
                "ce_empty_probe_entry": {"on_error": [], "action": {"type": "Custom", "param": {
                    "custom_action": "ce_empty_probe"}}},
            }, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
            resource.register_custom_action("ce_empty_probe", Probe())
            controller = NoDeviceController()
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                detail = tasker.post_task("ce_empty_probe_entry").wait().get()
                self.assertTrue(detail.status.succeeded)
            finally:
                tasker.post_stop().wait()
            self.assertEqual(controller.inputs, 0)
        self.assertEqual(observed, [True, False])

    def test_cancel_wait_submits_no_input_and_has_no_later_actions(self):
        _, seen, actions, _, _ = self.run_graph(PREFIX + "提交强化", cancel=True)
        self.assertEqual(actions, [])
        self.assertNotIn(PREFIX + "强化二次确认", seen)
        self.assertNotIn(PREFIX + "强化一批完成", seen)


if __name__ == "__main__":
    unittest.main()
