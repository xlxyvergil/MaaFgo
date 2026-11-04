"""Run production friend-summon branching in MaaFramework, with synthetic screen states.

No device is connected. Leaf image/OCR recognition is supplied by the in-memory
state model; production And/Or guards, actions, quotas, terminal branches and
per-call overrides remain real Framework behavior. This is not visual device QA.
"""
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest

import maa
import numpy as np
from maa.controller import CustomController
from maa.context import ContextEventSink
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.define import LoggingLevelEnum
from maa.event_sink import NotificationType
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent/custom"))
import expball_action as runtime

P = "友情召唤-"
TERMINALS = {
    "已到友情池", "结果就绪", "从者仓满", "礼装仓满", "纹章仓满", "友情点不足",
    "识别失败", "离开结果完成", "锁定完成", "原已锁定",
}


class FriendScreenController(CustomController):
    def __init__(self, scene, *, confirm_count="10", correct_currency=True, forbidden_currency=False,
                 confirm_cost="2,000", name="测试从者", originally_locked=False, network_plan=None,
                 candidate_box=None, confirm_kind="paid_jp", free_texts=None,
                 auto_return=True, confirm_decide=True):
        self.scene = scene
        self.confirm_count = confirm_count
        self.correct_currency = correct_currency
        self.forbidden_currency = forbidden_currency
        self.confirm_cost = confirm_cost
        self.confirm_kind = confirm_kind
        self.free_texts = {
            "免费每日次数正文": "每日可免费召唤1次。",
            "免费开始问句": "是否开始召唤？",
            "免费重置提示": "※AM0：00过后，将可以再次免费召唤。",
            **(free_texts or {}),
        }
        self.auto_return = auto_return
        self.confirm_decide = confirm_decide
        self.name = name
        self.originally_locked = originally_locked
        self.inputs = []
        self.unexpected = []
        self.confirmations = 0
        self.confirmation_button_boxes = []
        self.lock_toggles = 0
        self.stop_requested = False
        self.network_plan = dict(network_plan or {})
        self.network_kind = None
        self.network_remaining = 0
        self.network_retries = 0
        self.draws = 0
        self.openings = 0
        self.detail_closes = 0
        self.result_closes = 0
        self.executed_actions = []
        self.candidate_box = candidate_box or [100, 100, 150, 200]
        super().__init__()

    def advance(self, scene, operation):
        self.scene = scene
        if operation in self.network_plan:
            self.network_kind, self.network_remaining = self.network_plan.pop(operation)

    def confirmation_text(self):
        if self.confirm_kind == "free_cn":
            return "\n".join(self.free_texts.values())
        if self.confirm_kind == "paid_cn":
            return f"使用{self.confirm_cost}友情点进行{self.confirm_count}次召唤。"
        if self.confirm_kind == "paid_cn_actual":
            return f"使用{self.confirm_cost}友情点数进行{self.confirm_count}召唤。"
        return f"フレンドポイントを{self.confirm_cost}pt使用して{self.confirm_count}回召喚します。"

    def connect(self): return True
    def request_uuid(self): return "friend-pipeline-synthetic-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)

    def click(self, x, y):
        self.inputs.append(("click", x, y))
        left, top, width, height = self.candidate_box
        if self.network_kind:
            if 740 <= x <= 920 and 510 <= y <= 620:
                self.network_retries += 1
                self.network_remaining -= 1
                if self.network_remaining <= 0:
                    self.network_kind = None
                return True
            self.unexpected.append(("network", self.scene, x, y))
            return False
        if self.scene in ("pool", "free_pool") and 580 <= x <= 970 and 513 <= y <= 553:
            self.draws += 1
            self.advance("confirm", "draw")
        elif self.scene in ("event_pool", "event_cn_pool", "legacy_pool") and x < 50 and 329 <= y <= 395:
            self.scene = "pool"
        elif self.scene == "confirm" and 740 <= x <= 920 and 510 <= y <= 620:
            self.confirmations += 1
            self.advance("animation", "confirm")
        elif self.scene == "animation":
            self.scene = "result"
        elif self.scene == "result" and left <= x < left + width and top <= y < top + height:
            self.openings += 1
            self.advance("original_red" if self.originally_locked else "detail_blue", "open")
        elif self.scene == "detail_blue" and x < 100 and y >= 100:
            self.lock_toggles += 1
            self.advance("detail_red", "lock")
        elif self.scene in ("detail_red", "original_red") and y < 90:
            self.detail_closes += 1
            self.advance("result", "close")
        elif self.scene == "home" and x >= 1000 and y >= 600:
            self.scene = "menu"
        elif self.scene == "menu" and 400 <= x <= 700 and y >= 580:
            self.scene = "pool"
        else:
            self.unexpected.append((self.scene, x, y))
            return False
        return True

    def click_key(self, key):
        self.inputs.append(("key", key))
        if self.scene == "result" and key == 111 and not self.network_kind:
            self.result_closes += 1
            self.advance("pool", "leave")
            return True
        self.unexpected.append((self.scene, "key", key))
        return False

    def reject(self, *args):
        self.unexpected.append(args)
        return False

    start_app = stop_app = swipe = touch_down = touch_move = touch_up = reject
    input_text = key_down = key_up = reject


class FriendSummonPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.production = json.loads((ROOT / "assets/resource/base/pipeline/友情召唤.json").read_text("utf-8"))
        cls.daily = json.loads((ROOT / "assets/resource/base/pipeline/每日免费友情.json").read_text("utf-8"))
        cls.network = json.loads((ROOT / "assets/resource/base/pipeline/搓丸子网络.json").read_text("utf-8"))

    def run_scenario(self, scene, entry="抽取一批", *, batch=10, confirm_count="10", correct_currency=True,
                     name="测试从者", originally_locked=False, stop_on_name=False, navigation=False,
                     provide_name_guard=True, forbidden_currency=False, confirm_cost=None, network_plan=None,
                     runtime_candidate_box=None, free_authorized=False, confirm_kind="paid_jp", free_texts=None,
                     auto_return=True, confirm_decide=True):
        controller = FriendScreenController(scene, confirm_count=confirm_count, correct_currency=correct_currency,
                                            name=name, originally_locked=originally_locked,
                                            forbidden_currency=forbidden_currency,
                                            confirm_cost=confirm_cost or ("20,000" if confirm_count == "100" else "2,000"),
                                            network_plan=network_plan, candidate_box=runtime_candidate_box,
                                            confirm_kind=confirm_kind, free_texts=free_texts,
                                            auto_return=auto_return, confirm_decide=confirm_decide)
        nodes = copy.deepcopy(self.production)
        nodes.update(copy.deepcopy(self.network))
        for item in ("每日免费友情-菜单", "每日免费友情-召唤"):
            nodes[item] = copy.deepcopy(self.daily[item])
        # Preserve the daily summon endpoint as a different terminal, so accidental
        # leakage to its original next would be observable rather than a false pass.
        nodes["每日免费友情-召唤开始"] = {"action": {"type": "Custom", "param": {"custom_action": "record"}}}
        nodes["回主界面"] = {"on_error": []}
        if provide_name_guard:
            nodes[P + "候选名称"]["recognition"]["param"]["expected"] = ["^测试从者$"]
        if runtime_candidate_box is None:
            nodes[P + "打开候选"]["enabled"] = True
            nodes[P + "打开候选"]["action"]["param"]["target"] = [100, 100, 150, 200]
        if batch == 100:
            nodes[P + "点击抽取"]["action"]["param"]["target"] = [793, 513, 174, 20]
            for name, patch in self.production[P + "导航到友情池"]["attach"]["batch_100_override"].items():
                if "recognition" in patch:
                    nodes[name]["recognition"]["param"].update(patch["recognition"]["param"])
        if free_authorized:
            for name, patch in self.production[P + "导航到友情池"]["attach"]["free_10_override"].items():
                nodes[name] = runtime.merge(nodes[name], patch)
        seen, references = [], {}
        cancel_waiting, cancel_release = threading.Event(), threading.Event()

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                cfg = json.loads(argv.custom_recognition_param)
                short = cfg["node"].removeprefix(P)
                state = controller.scene
                if cfg["node"].startswith("搓丸子网络-"):
                    if not controller.network_kind:
                        return None
                    if cfg.get("template"):
                        return (740, 510, 180, 100) if controller.network_kind == "ordinary" else None
                    text = ("ダウンロードサーバーとの通信が切れました" if controller.network_kind == "download"
                            else "サーバーとの通信が切れました")
                    if cfg.get("roi", [0])[0] >= 700:
                        text = "やり直す" if controller.network_kind == "download" else "リトライ"
                    if any(re.search(p, text) for p in cfg.get("expected", [])):
                        return (740, 510, 180, 100)
                    return None
                if short == "候选名称":
                    if stop_on_name:
                        cancel_waiting.set()
                        cancel_release.wait(timeout=1)
                        return None
                    if state in ("detail_blue", "detail_red", "original_red") and any(
                        re.search(pattern, controller.name) for pattern in cfg.get("expected", [])
                    ):
                        return (300, 30, 500, 80)
                    return None
                if short == "抽取数量确认":
                    text = controller.confirmation_text()
                    if state == "confirm" and any(re.search(p, text) for p in cfg.get("expected", [])):
                        return (450, 250, 100, 30)
                    return None
                if short == "确认使用友情点":
                    text = controller.confirmation_text()
                    if (state == "confirm" and controller.correct_currency
                            and any(re.search(p, text) for p in cfg.get("expected", []))):
                        return (250, 180, 770, 35)
                    return None
                if short == "确认出现圣晶石或呼符":
                    return (250, 180, 770, 35) if state == "confirm" and controller.forbidden_currency else None
                if short in controller.free_texts:
                    if (state == "confirm" and controller.confirm_kind == "free_cn"
                            and any(re.search(pattern, controller.free_texts[short])
                                    for pattern in cfg.get("expected", []))):
                        return (480, 200, 400, 40)
                    return None
                if short == "自动变还设置特征" and not controller.auto_return:
                    return None
                if short == "确认决定按钮" and not controller.confirm_decide:
                    return None
                if short == "确认决定按钮":
                    if state != "confirm":
                        return None
                    candidates = [(798, 543, 75, 44, .865896), (799, 542, 75, 44, .978470)]
                    left, top, width, height = cfg.get("roi") or [0, 0, 1280, 720]
                    candidates = [box for box in candidates if
                                  left <= box[0] and top <= box[1]
                                  and box[0] + box[2] <= left + width
                                  and box[1] + box[3] <= top + height]
                    if not candidates:
                        return None
                    candidates.sort(key=(lambda box: -box[4]) if cfg.get("order_by") == "Score"
                                    else (lambda box: box[0]))
                    controller.confirmation_button_boxes.append(candidates[0][:4])
                    return candidates[0][:4]
                if short == "每日免费友情-菜单":
                    if state != "home":
                        return None
                    # CN035: Horizontal picked the upper background false match
                    # before the real menu. Keep both candidates in the model.
                    candidates = [(507, 83, 149, 61, .6738), (1109, 616, 149, 61, .9407)]
                    left, top, width, height = cfg.get("roi") or [0, 0, 1280, 720]
                    candidates = [box for box in candidates if
                                  left <= box[0] and top <= box[1]
                                  and box[0] + box[2] <= left + width
                                  and box[1] + box[3] <= top + height]
                    if not candidates:
                        return None
                    candidates.sort(key=(lambda box: -box[4]) if cfg.get("order_by") == "Score"
                                    else (lambda box: box[0]))
                    return candidates[0][:4]
                navigation_texts = {
                    "event_pool": ("霊基召喚", "11回召喚"),
                    "event_without_header": ("", "11回召喚"),
                    "event_without_button": ("霊基召喚", ""),
                    "event_cn_pool": ("灵基召唤", "召唤×11次"),
                    "event_cn_without_header": ("", "召唤×11次"),
                    "event_cn_without_button": ("灵基召唤", ""),
                }
                if short in ("一般召唤页顶栏文字", "一般召唤页按钮文字") and state in navigation_texts:
                    text = navigation_texts[state][short == "一般召唤页按钮文字"]
                    if any(re.search(pattern, text) for pattern in cfg.get("expected", [])):
                        return (100, 100, 40, 40)
                    return None
                groups = {
                    "pool": {"友情池标题文字", "友情点消耗文字", "十连或免费按钮", "一般召唤页", "已在召唤界面"},
                    "free_pool": {"友情池标题文字", "免费十连按钮", "十连或免费按钮"},
                    "free_pool_without_title": {"免费十连按钮", "十连或免费按钮"},
                    "ordinary_pool": {"十连或免费按钮", "一般召唤页", "已在召唤界面"},
                    "pool_without_currency": {"友情池标题文字", "十连或免费按钮"},
                    "pool_without_title": {"友情点消耗文字", "十连或免费按钮"},
                    "event_pool": {"一般召唤页顶栏文字", "一般召唤页按钮文字"},
                    "legacy_pool": {"一般召唤页旧图特征"},
                    "event_without_header": {"一般召唤页按钮文字"},
                    "event_without_button": {"一般召唤页顶栏文字"},
                    "confirm": {"自动变还设置特征", "确认使用友情点", "确认决定按钮"},
                    "animation": {"跳过动画"},
                    "result": {"结果按钮特征", "结果页面已到", "关闭结果", "打开候选", "原锁返回结果", "新锁返回结果"},
                    "detail_blue": {"详情身份", "详情锁定蓝色"},
                    "detail_red": {"详情身份", "详情锁定红色", "关闭新锁详情"},
                    "original_red": {"详情身份", "详情锁定红色", "关闭原锁详情"},
                    "full_servant": {"从者容量类别", "从者容量已满文字"},
                    "full_equip": {"礼装容量类别", "礼装容量已满文字"},
                    "full_command": {"纹章容量类别", "纹章容量已满文字"},
                    "insufficient": {"不足提示"},
                    "home": {"每日免费友情-菜单"},
                    "menu": {"每日免费友情-召唤"},
                }
                if short not in groups.get(state, set()): return None
                if short == "确认决定按钮": return (740, 510, 180, 100)
                if short == "详情锁定蓝色": return (10, 150, 50, 50)
                if short.startswith("关闭") and "详情" in short: return (10, 10, 60, 60)
                if short == "每日免费友情-菜单": return (1100, 640, 60, 30)
                if short == "每日免费友情-召唤": return (500, 620, 80, 40)
                return (100, 100, 40, 40)

        class Record(CustomAction):
            def run(self, context, argv):
                seen.append(argv.node_name)
                return True

        class Actions(ContextEventSink):
            def on_node_action(self, context, noti_type, detail):
                if noti_type == NotificationType.Starting:
                    controller.executed_actions.append(detail.name)

        def instrument_recognition(recognition, name):
            kind = recognition.get("type")
            if kind in ("And", "Or"):
                for child in recognition.get("param", {}).get("all_of" if kind == "And" else "any_of", []):
                    if isinstance(child, dict) and "recognition" in child:
                        child["recognition"] = instrument_recognition(child["recognition"], name)
                return recognition
            if kind is None:
                return recognition
            param = recognition.get("param", {})
            return {"type": "Custom", "param": {
                "custom_recognition": "friend_screen", "custom_recognition_param": {
                    "node": name, "expected": param.get("expected", []),
                    "template": param.get("template"), "roi": param.get("roi", []),
                    "order_by": param.get("order_by"),
                    "only_rec": param.get("only_rec"),
                },
            }}

        original_recognitions = {name: copy.deepcopy(item.get("recognition", {})) for name, item in nodes.items()}
        for name, item in nodes.items():
            item.update(pre_delay=0, post_delay=0, timeout=80, rate_limit=1)
            recognition = item.get("recognition", {})
            if recognition:
                item["recognition"] = instrument_recognition(recognition, name)
            if name.removeprefix(P) in TERMINALS:
                item["action"] = {"type": "Custom", "param": {"custom_action": "record"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "pipeline/friend.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(root).wait().succeeded)
            resource.register_custom_recognition("friend_screen", Screen())
            resource.register_custom_action("record", Record())
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            tasker.add_context_sink(Actions())
            references["tasker"] = tasker
            self.assertTrue(tasker.bind(resource, controller))
            original_next = copy.deepcopy(resource.get_node_data("每日免费友情-召唤")["next"])
            override = {}
            if navigation:
                override = copy.deepcopy(self.production[P + "导航到友情池"]["attach"]["navigation_override"])
                for name, patch in override.items():
                    if "recognition" in patch:
                        recognition = runtime.merge(original_recognitions[name], patch["recognition"])
                        patch["recognition"] = instrument_recognition(recognition, name)
            if runtime_candidate_box is not None:
                # This is the exact dynamic action patch used by lock_targets:
                # exercise Framework's native post_task merge, not fixture setup.
                override[P + "打开候选"] = {
                    "enabled": True, "action": {"param": {"target": runtime_candidate_box}},
                }
            job = tasker.post_task(P + entry, override)
            deadline = time.monotonic() + 5
            stop_sent = False
            while not job.status.done and time.monotonic() < deadline:
                if cancel_waiting.is_set() and not stop_sent:
                    tasker.post_stop()
                    stop_sent = True
                    controller.stop_requested = True
                    cancel_release.set()
                time.sleep(.005)
            if not job.status.done:
                tasker.post_stop().wait()
                self.fail("friend Pipeline did not finish within test deadline")
            succeeded = job.status.succeeded
            self.assertEqual(resource.get_node_data("每日免费友情-召唤")["next"], original_next)
            tasker.post_stop().wait()
        self.assertEqual(controller.unexpected, [])
        return seen, controller, succeeded

    def test_unknown_page_never_submits_input(self):
        seen, c, _ = self.run_scenario("unknown")
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.inputs, [])

    def test_next_and_error_routes_are_distinct_and_rejection_keeps_one_failure_terminal(self):
        # maa-tools 1.1.3 checks next and on_error together, after stripping
        # JumpBack attributes. Repeating a target across the two is also an error.
        for name, node in self.production.items():
            with self.subTest(node=name):
                routes = [target.removeprefix("[JumpBack]")
                          for key in ("next", "on_error") for target in node.get(key, [])]
                self.assertEqual(len(routes), len(set(routes)), routes)
        reject = P + "拒绝非友情确认"
        guard = self.production[P + "确认出现圣晶石或呼符"]
        self.assertEqual(guard["next"], [reject])
        self.assertEqual(guard["on_error"], [P + "识别失败"])
        self.assertEqual(self.production[reject]["next"], [P + "识别失败"])
        self.assertEqual(self.production[reject]["on_error"], [])
        self.assertEqual(self.production[reject].get("action", {}).get("type", "DoNothing"), "DoNothing")
        self.assertEqual(self.production[P + "识别失败"].get("next", []), [])

    def test_pool_title_and_friend_currency_are_both_required_before_input(self):
        for scene in ("ordinary_pool", "pool_without_currency", "pool_without_title"):
            with self.subTest(scene=scene):
                seen, c, _ = self.run_scenario(scene)
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.inputs, [])

    def test_cn_free_ten_requires_authorized_friend_pool_and_three_free_lines(self):
        for batch in (10, 100):
            with self.subTest(batch=batch):
                seen, c, _ = self.run_scenario("free_pool", batch=batch, free_authorized=True,
                                             confirm_kind="free_cn")
                self.assertEqual(seen, [P + "结果就绪"])
                self.assertEqual((c.draws, c.confirmations), (1, 1))
                self.assertTrue(580 <= c.inputs[0][1] <= 700, c.inputs)
                self.assertIn(P + "已验证免费十连授权", c.executed_actions)
                self.assertIn(P + "免费确认安全后提交", c.executed_actions)
                self.assertNotIn(P + "友情确认", c.executed_actions)
                self.assertNotIn(P + "确认安全后提交", c.executed_actions)

    def test_free_override_requires_friend_title_and_free_ten_button_before_input(self):
        for scene in ("ordinary_pool", "pool", "free_pool_without_title"):
            with self.subTest(scene=scene):
                seen, c, _ = self.run_scenario(scene, free_authorized=True, confirm_kind="free_cn")
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.inputs, [])

    def test_free_confirm_branch_is_disabled_without_local_authorization(self):
        for entry in ("已验证免费十连授权", "免费确认安全后提交"):
            with self.subTest(entry=entry):
                seen, c, _ = self.run_scenario("confirm", entry, confirm_kind="free_cn")
                self.assertEqual(c.inputs, [])
                self.assertNotIn(P + "结果就绪", seen)
        seen, c, _ = self.run_scenario("free_pool", confirm_kind="free_cn")
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.confirmations, 0)

    def test_free_confirmation_rejects_missing_or_changed_each_line(self):
        invalid = {
            "免费每日次数正文": ("", "每日可免费召唤10次。", "每日可召唤1次。", "每日可免费召唤1次。圣晶石"),
            "免费开始问句": ("", "是否开始强化？"),
            "免费重置提示": ("", "※AM1：00过后，将可以再次免费召唤。", "※AM0：00过后，将可以再次召唤。"),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    seen, c, _ = self.run_scenario("free_pool", free_authorized=True, confirm_kind="free_cn",
                                                 free_texts={field: value})
                    self.assertEqual(seen, [P + "识别失败"])
                    self.assertEqual(c.confirmations, 0)

    def test_free_reset_prompt_accepts_observed_ocr_missing_only_leading_symbol(self):
        text = "AM0：00过后，将可以再次免费召唤。"
        seen, c, _ = self.run_scenario("free_pool", free_authorized=True, confirm_kind="free_cn",
                                     free_texts={"免费重置提示": text})
        self.assertEqual(seen, [P + "结果就绪"])
        self.assertEqual(c.confirmations, 1)
        param = self.production[P + "免费重置提示"]["recognition"]["param"]
        for invalid in ("AM1：00过后，将可以再次免费召唤。", "AM0：00过后，将可以再次召唤。",
                        "※※AM0：00过后，将可以再次免费召唤。"):
            self.assertFalse(any(re.search(pattern, invalid) for pattern in param["expected"]), invalid)

    def test_confirmation_decide_uses_highest_score_in_right_button_roi(self):
        param = self.production[P + "确认决定按钮"]["recognition"]["param"]
        self.assertEqual(param["roi"], [700, 520, 330, 115])
        self.assertEqual(param["order_by"], "Score")
        self.assertEqual(param["threshold"], .85)
        seen, c, _ = self.run_scenario("free_pool", free_authorized=True, confirm_kind="free_cn")
        self.assertEqual(seen, [P + "结果就绪"])
        self.assertTrue(c.confirmation_button_boxes)
        self.assertTrue(all(box == (799, 542, 75, 44) for box in c.confirmation_button_boxes))
        _, x, y = c.inputs[1]
        self.assertTrue(799 <= x < 874 and 542 <= y < 586, c.inputs)

    def test_free_confirmation_still_requires_auto_return_and_decide_button(self):
        for missing in ("auto_return", "confirm_decide"):
            with self.subTest(missing=missing):
                seen, c, _ = self.run_scenario("free_pool", free_authorized=True, confirm_kind="free_cn",
                                             **{missing: False})
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.confirmations, 0)

    def test_free_confirmation_rejects_stones_or_tickets_before_submit(self):
        seen, c, _ = self.run_scenario("free_pool", free_authorized=True, confirm_kind="free_cn",
                                     forbidden_currency=True)
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.confirmations, 0)
        self.assertEqual(c.executed_actions.count(P + "拒绝非友情确认"), 1)
        self.assertNotIn(P + "免费确认安全后提交", c.executed_actions)

    def test_free_override_never_falls_back_to_paid_ten_or_hundred_confirmation(self):
        for batch in (10, 100):
            with self.subTest(batch=batch):
                seen, c, _ = self.run_scenario("free_pool", batch=batch, free_authorized=True,
                                             confirm_kind="paid_cn", confirm_count=str(batch))
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.confirmations, 0)

    def test_paid_cn_ten_and_hundred_keep_currency_cost_and_quantity_guards(self):
        for batch in (10, 100):
            with self.subTest(batch=batch):
                seen, c, _ = self.run_scenario("pool", batch=batch, confirm_kind="paid_cn", confirm_count=str(batch))
                self.assertEqual(seen, [P + "结果就绪"])
                self.assertEqual(c.confirmations, 1)
                self.assertNotIn(P + "已验证免费十连授权", c.executed_actions)
                self.assertNotIn(P + "免费确认安全后提交", c.executed_actions)
        for batch, count, cost in ((10, "1", "2,000"), (10, "10", "20,000"), (100, "10", "20,000"),
                                   (100, "100", "2,000")):
            with self.subTest(batch=batch, count=count, cost=cost):
                seen, c, _ = self.run_scenario("pool", batch=batch, confirm_kind="paid_cn",
                                             confirm_count=count, confirm_cost=cost)
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.confirmations, 0)

    def test_paid_cn_actual_no_ci_word_requires_exact_cost_and_count(self):
        for batch in (10, 100):
            with self.subTest(batch=batch):
                seen, c, _ = self.run_scenario("pool", batch=batch, confirm_kind="paid_cn_actual",
                                             confirm_count=str(batch))
                self.assertEqual(seen, [P + "结果就绪"])
                self.assertEqual((c.draws, c.confirmations), (1, 1))
                self.assertNotIn(P + "免费确认安全后提交", c.executed_actions)
        invalid = ((10, "100", "2,000"), (100, "10", "20,000"),
                   (10, "10", "20,000"), (100, "100", "2,000"),
                   (10, "110", "2,000"), (10, "12310", "2,000"),
                   (100, "1100", "20,000"), (100, "1000", "20,000"),
                   (100, "100", "120,000"), (10, "10", "12,000"))
        for batch, count, cost in invalid:
            with self.subTest(batch=batch, count=count, cost=cost):
                seen, c, _ = self.run_scenario("pool", batch=batch, confirm_kind="paid_cn_actual",
                                             confirm_count=count, confirm_cost=cost)
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.confirmations, 0)

    def test_paid_cn_actual_patterns_do_not_match_balances_or_partial_amounts(self):
        for batch, amount in ((10, "2,000"), (100, "20,000")):
            configured = (self.production if batch == 10 else
                          self.production[P + "导航到友情池"]["attach"]["batch_100_override"])
            currency = configured[P + "确认使用友情点"]["recognition"]["param"]["expected"]
            quantity = configured[P + "抽取数量确认"]["recognition"]["param"]["expected"]
            text = f"使用{amount}友情点数进行{batch}召唤。"
            self.assertTrue(any(re.search(pattern, text) for pattern in currency), text)
            self.assertTrue(any(re.search(pattern, text) for pattern in quantity), text)
            for invalid in (f"持有{amount}友情点数进行{batch}召唤。", f"余额{amount}，进行{batch}召唤。",
                            f"使用{amount}圣晶石进行{batch}召唤。", f"使用{amount}友情点数进行1{batch}召唤。",
                            f"使用{amount}友情点数进行{batch}0召唤。", text + "另有余额100"):
                with self.subTest(batch=batch, invalid=invalid):
                    self.assertFalse(any(re.search(pattern, invalid) for pattern in currency), invalid)
                    self.assertFalse(any(re.search(pattern, invalid) for pattern in quantity), invalid)

    def test_paid_cn_actual_stones_and_free_route_mismatch_still_reject_submit(self):
        seen, c, _ = self.run_scenario("pool", batch=100, confirm_kind="paid_cn_actual", confirm_count="100",
                                     forbidden_currency=True)
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.confirmations, 0)
        self.assertEqual(c.executed_actions.count(P + "拒绝非友情确认"), 1)
        self.assertNotIn(P + "确认安全后提交", c.executed_actions)
        seen, c, _ = self.run_scenario("free_pool", batch=100, free_authorized=True,
                                     confirm_kind="paid_cn_actual", confirm_count="100")
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.confirmations, 0)

    def test_free_confirmation_network_retry_never_repeats_free_submit(self):
        for operation in ("draw", "confirm"):
            with self.subTest(operation=operation):
                seen, c, _ = self.run_scenario("free_pool", free_authorized=True, confirm_kind="free_cn",
                                             network_plan={operation: ("ordinary", 2)})
                self.assertEqual(seen, [P + "结果就绪"])
                self.assertEqual((c.draws, c.confirmations, c.network_retries), (1, 1, 2))

    def test_free_confirmation_patterns_are_strict_cn_and_keep_scoped_rois(self):
        cases = {
            "免费每日次数正文": ("每日可免费召唤1次。", [450, 195, 400, 45]),
            "免费开始问句": ("是否开始召唤？", [510, 233, 260, 42]),
            "免费重置提示": ("※AM0：00过后，将可以再次免费召唤。", [310, 290, 660, 65]),
        }
        for short, (text, roi) in cases.items():
            with self.subTest(node=short):
                param = self.production[P + short]["recognition"]["param"]
                self.assertTrue(any(re.search(pattern, text) for pattern in param["expected"]))
                self.assertFalse(any(re.search(pattern, "前缀" + text) for pattern in param["expected"]))
                self.assertFalse(any(re.search(pattern, "フレンドポイントを2,000pt使用して10回召喚します。")
                                     for pattern in param["expected"]))
                self.assertEqual(param["roi"], roi)
                if short == "免费开始问句":
                    self.assertTrue(param["only_rec"])
                    self.assertEqual(param["threshold"], .8)
                    self.assertFalse(any(re.search(pattern, "是否开始召喚7") for pattern in param["expected"]))
        for short in ("已验证免费十连授权", "免费确认安全后提交"):
            self.assertFalse(self.production[P + short]["enabled"])
            self.assertEqual(self.production[P + short]["max_hit"], 1)
        self.assertEqual(self.production[P + "已验证免费十连授权"]["next"][-2:],
                         [P + "确认出现圣晶石或呼符", P + "免费确认安全后提交"])

    def test_event_navigation_reaches_friend_pool_using_both_text_guards(self):
        for scene in ("event_pool", "event_cn_pool", "legacy_pool"):
            with self.subTest(scene=scene):
                seen, c, _ = self.run_scenario(scene, "寻找友情池")
                self.assertEqual(seen, [P + "已到友情池"])
                self.assertEqual(len(c.inputs), 1)
                self.assertLess(c.inputs[0][1], 50)
                self.assertEqual(c.confirmations, 0)

    def test_general_navigation_guard_cannot_authorize_friend_draw(self):
        for scene in ("event_pool", "event_cn_pool"):
            with self.subTest(scene=scene):
                seen, c, _ = self.run_scenario(scene)
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.inputs, [])
                self.assertEqual((c.draws, c.confirmations), (0, 0))

    def test_general_navigation_requires_header_and_button_before_swiping_pool(self):
        for scene in ("event_without_header", "event_without_button",
                      "event_cn_without_header", "event_cn_without_button"):
            with self.subTest(scene=scene):
                seen, c, _ = self.run_scenario(scene, "寻找友情池")
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.inputs, [])

    def test_general_navigation_text_patterns_support_cn_and_jp_with_fixed_rois(self):
        header = self.production[P + "一般召唤页顶栏文字"]["recognition"]["param"]
        button = self.production[P + "一般召唤页按钮文字"]["recognition"]["param"]
        for text in ("霊基召喚", "靈基召喚", "灵基召唤"):
            self.assertTrue(any(re.search(pattern, text) for pattern in header["expected"]), text)
        for text in ("1回召喚", "11回召喚", "100回召喚", "1次召唤", "10 次 召唤", "召唤11次", "召唤 100 次",
                     "召唤×1次", "召唤×11次", "召唤x10次", "召唤X100次", "召唤 × 11 次"):
            self.assertTrue(any(re.search(pattern, text) for pattern in button["expected"]), text)
        for text in ("友情点", "圣晶石", "召唤", "2次召唤", "21次召唤", "111回召喚", "召唤111次",
                     "召唤×2次", "召唤×21次", "召唤×111次", "召唤×1231次", "召唤x1000次", "1231次召唤"):
            self.assertFalse(any(re.search(pattern, text) for pattern in button["expected"]), text)
        self.assertEqual(header["roi"], [1000, 0, 280, 100])
        self.assertEqual(button["roi"], [300, 480, 700, 110])
        self.assertEqual(self.production[P + "一般召唤页文字特征"]["recognition"]["param"]["all_of"],
                         [P + "一般召唤页顶栏文字", P + "一般召唤页按钮文字"])

    def test_title_pattern_requires_complete_summon_title_and_balance_uses_top_roi(self):
        title = self.production[P + "友情池标题文字"]["recognition"]["param"]
        for text in ("フレンドポイント召喚", "フレンド ポイント 召喚", "フレンドボイント召喚", "友情点召唤"):
            self.assertTrue(any(re.search(pattern, text) for pattern in title["expected"]), text)
        for text in ("フレンドポイント", "消費フレンドP 2,000", "聖晶石召喚", "友情点召唤の情報"):
            self.assertFalse(any(re.search(pattern, text) for pattern in title["expected"]), text)
        self.assertEqual(title["roi"], [340, 345, 610, 100])
        self.assertEqual(self.production[P + "友情点余额"]["recognition"]["param"]["roi"], [815, 18, 155, 55])

    def test_confirmation_requires_friend_currency(self):
        seen, c, _ = self.run_scenario("pool", correct_currency=False)
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.confirmations, 0)

    def test_confirmation_rejects_stone_or_ticket_even_with_positive_friend_predicates(self):
        seen, c, _ = self.run_scenario("pool", forbidden_currency=True)
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.confirmations, 0)

    def test_confirmation_links_cost_and_count_to_friend_currency_line(self):
        for count, cost in (("10", "200"), ("10", "20,000"), ("100", "2,000")):
            with self.subTest(count=count, cost=cost):
                seen, c, _ = self.run_scenario("pool", batch=int(count), confirm_count=count, confirm_cost=cost)
                self.assertEqual(seen, [P + "识别失败"])
                self.assertEqual(c.confirmations, 0)

    def test_ten_draw_waits_for_result(self):
        seen, c, _ = self.run_scenario("pool")
        self.assertEqual(seen, [P + "结果就绪"])
        self.assertEqual(c.confirmations, 1)

    def test_network_retry_during_confirmation_and_result_never_resubmits(self):
        for kind in ("ordinary", "download"):
            for operation in ("draw", "confirm"):
                with self.subTest(kind=kind, operation=operation):
                    seen, c, _ = self.run_scenario("pool", network_plan={operation: (kind, 2)})
                    self.assertEqual(seen, [P + "结果就绪"])
                    self.assertEqual((c.draws, c.confirmations, c.network_retries), (1, 1, 2))

    def test_network_retry_during_detail_lock_and_close_never_repeats_actions(self):
        for kind in ("ordinary", "download"):
            for operation in ("open", "lock", "close"):
                with self.subTest(kind=kind, operation=operation):
                    seen, c, _ = self.run_scenario("result", "锁定候选", network_plan={operation: (kind, 1)})
                    self.assertEqual(seen, [P + "锁定完成"])
                    self.assertEqual((c.openings, c.lock_toggles, c.detail_closes, c.network_retries), (1, 1, 1, 1))

    def test_original_lock_download_retries_before_detail_and_after_close(self):
        for operation in ("open", "close"):
            with self.subTest(operation=operation):
                seen, c, _ = self.run_scenario("result", "锁定候选", originally_locked=True,
                                             network_plan={operation: ("download", 1)})
                self.assertEqual(seen, [P + "原已锁定"])
                self.assertEqual((c.openings, c.lock_toggles, c.detail_closes, c.network_retries), (1, 0, 1, 1))

    def test_network_retry_after_result_close_does_not_send_back_again(self):
        for kind in ("ordinary", "download"):
            with self.subTest(kind=kind):
                seen, c, _ = self.run_scenario("result", "离开结果", network_plan={"leave": (kind, 1)})
                self.assertEqual(seen, [P + "离开结果完成"])
                self.assertEqual((c.result_closes, c.network_retries), (1, 1))

    def test_three_retries_exhaust_and_stop_without_background_success(self):
        for kind in ("ordinary", "download"):
            for operation, entry, initial in (("draw", "抽取一批", "pool"),
                                              ("confirm", "抽取一批", "pool"),
                                              ("lock", "锁定候选", "result"),
                                              ("close", "锁定候选", "result"),
                                              ("leave", "离开结果", "result")):
                with self.subTest(kind=kind, operation=operation):
                    seen, c, _ = self.run_scenario(initial, entry, network_plan={operation: (kind, 4)})
                    self.assertEqual(c.network_retries, 3)
                    self.assertEqual(seen, [])
                    # Framework treats a deliberate StopTask as a handled task;
                    # its business outcome is the action and absent success terminal.
                    self.assertIn("搓丸子网络-重试耗尽", c.executed_actions)
                    self.assertLessEqual(c.draws, 1)
                    self.assertLessEqual(c.confirmations, 1)
                    self.assertLessEqual(c.lock_toggles, 1)
                    self.assertLessEqual(c.detail_closes, 1)
                    self.assertLessEqual(c.result_closes, 1)

    def test_network_dispatch_precedes_exposed_background_predicates(self):
        prefix = ["[JumpBack]搓丸子网络-重试", "搓丸子网络-重试耗尽"]
        for short in ("导航到友情池", "寻找友情池", "抽取一批", "等待抽取确认", "友情确认", "等待结果",
                      "离开结果", "离开结果复查", "锁定候选", "检查候选详情", "候选原本已锁定",
                      "等待新锁定", "确认新锁定", "关闭原锁详情", "关闭新锁详情"):
            with self.subTest(node=short):
                self.assertEqual(self.production[P + short]["next"][:2], prefix)
        self.assertEqual(self.production[P + "友情确认"]["max_hit"], 1)
        self.assertEqual(self.production[P + "确认安全后提交"]["max_hit"], 1)
        self.assertEqual(self.production[P + "友情确认"]["next"][2], P + "确认出现圣晶石或呼符")

    def test_hundred_draw_requires_matching_quantity(self):
        seen, c, _ = self.run_scenario("pool", batch=100, confirm_count="100")
        self.assertEqual(seen, [P + "结果就绪"])
        self.assertEqual(c.confirmations, 1)
        seen, c, _ = self.run_scenario("pool", batch=100, confirm_count="10")
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.confirmations, 0)

    def test_capacity_and_insufficient_endpoints_do_not_click(self):
        for state, terminal in [("full_servant", "从者仓满"), ("full_equip", "礼装仓满"),
                                ("full_command", "纹章仓满"), ("insufficient", "友情点不足")]:
            with self.subTest(state=state):
                seen, c, _ = self.run_scenario(state)
                self.assertEqual(seen, [P + terminal])
                self.assertEqual(c.inputs, [])

    def test_existing_lock_is_never_toggled(self):
        seen, c, _ = self.run_scenario("result", "锁定候选", originally_locked=True)
        self.assertEqual(seen, [P + "原已锁定"])
        self.assertEqual(c.lock_toggles, 0)
        self.assertEqual(c.scene, "result")

    def test_new_lock_requires_name_then_confirms_and_closes(self):
        seen, c, _ = self.run_scenario("result", "锁定候选")
        self.assertEqual(seen, [P + "锁定完成"])
        self.assertEqual(c.lock_toggles, 1)
        seen, c, _ = self.run_scenario("result", "锁定候选", name="其他对象")
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.lock_toggles, 0)

    def test_and_click_outputs_use_button_boxes_separate_from_identity_guards(self):
        # The identity, quantity, name and actionable button all expose different
        # boxes. Native And must forward the configured button's box to Click.
        seen, c, _ = self.run_scenario("pool")
        self.assertEqual(seen, [P + "结果就绪"])
        _, x, y = c.inputs[1]
        self.assertTrue(740 <= x < 920 and 510 <= y < 610, c.inputs)
        seen, c, _ = self.run_scenario("result", "锁定候选")
        self.assertEqual(seen, [P + "锁定完成"])
        _, x, y = c.inputs[1]
        self.assertTrue(10 <= x < 60 and 150 <= y < 200, c.inputs)
        _, close_x, close_y = c.inputs[2]
        self.assertTrue(10 <= close_x < 70 and 10 <= close_y < 70, c.inputs)

    def test_missing_runtime_name_guard_fails_without_locking(self):
        seen, c, _ = self.run_scenario("result", "锁定候选", provide_name_guard=False)
        self.assertEqual(seen, [P + "识别失败"])
        self.assertEqual(c.lock_toggles, 0)

    def test_runtime_candidate_target_override_still_controls_original_action_node(self):
        original = self.production[P + "打开候选"]
        self.assertFalse(original["enabled"])
        self.assertEqual(original["action"]["type"], "Click")
        self.assertEqual(original["action"]["param"]["target"], [0, 0, 1, 1])
        box = [850, 230, 45, 60]
        seen, c, _ = self.run_scenario("result", "锁定候选", runtime_candidate_box=box,
                                     network_plan={"open": ("download", 1)})
        self.assertEqual(seen, [P + "锁定完成"])
        self.assertEqual((c.openings, c.lock_toggles, c.detail_closes, c.network_retries), (1, 1, 1, 1))
        _, x, y = c.inputs[0]
        self.assertTrue(box[0] <= x < box[0] + box[2], c.inputs)
        self.assertTrue(box[1] <= y < box[1] + box[3], c.inputs)

    def test_cancel_during_identity_check_never_toggles_lock(self):
        seen, c, _ = self.run_scenario("result", "锁定候选", stop_on_name=True)
        # A handled failure terminal can still make the nested Framework task
        # successful; business success must use the terminal and stop state.
        self.assertTrue(c.stop_requested)
        self.assertEqual(c.lock_toggles, 0)
        self.assertNotIn(P + "锁定完成", seen)

    def test_close_result_reaches_confirmed_friend_pool(self):
        seen, c, _ = self.run_scenario("result", "离开结果")
        self.assertEqual(seen, [P + "离开结果完成"])
        self.assertEqual(c.scene, "pool")

    def test_navigation_override_reuses_daily_chain_without_mutation(self):
        seen, c, _ = self.run_scenario("home", "导航到友情池", navigation=True)
        self.assertEqual(seen, [P + "已到友情池"])
        self.assertEqual(c.scene, "pool")
        self.assertEqual(len(c.inputs), 2)
        _, x, y = c.inputs[0]
        self.assertTrue(1109 <= x < 1258 and 616 <= y < 677, c.inputs)

    def test_runtime_navigation_merge_preserves_local_menu_roi_and_original_match_parameters(self):
        nodes = copy.deepcopy(self.production)
        nodes.update(copy.deepcopy(self.network))
        nodes.update(copy.deepcopy(self.daily))
        nodes["回主界面"] = {}
        hits, calls = {}, []
        terminal = P + "已到友情池"

        def run_task(entry, pipeline_override=None):
            calls.append((entry, copy.deepcopy(pipeline_override)))
            hits[terminal] = 1
            return NS(status=NS(succeeded=True))

        runner = object.__new__(runtime.ExpballRunner)
        runner.ctx = NS(tasker=NS(stopping=False),
                        get_node_data=lambda name: copy.deepcopy(nodes.get(name)),
                        get_hit_count=lambda name: hits.get(name, 0),
                        clear_hit_count=lambda name: hits.update({name: 0}),
                        run_task=run_task)
        runner.config = NS(direction="left", draw_batch=10)
        original = copy.deepcopy(nodes["每日免费友情-菜单"])
        self.assertEqual(runner.task(P + "导航到友情池", [terminal], runner._friend_overrides()), terminal)
        menu_patch = calls[0][1]["每日免费友情-菜单"]
        merged = runtime.merge(original, menu_patch)
        param = merged["recognition"]["param"]
        self.assertEqual(param["roi"], [1070, 590, 210, 130])
        self.assertEqual(param["order_by"], "Score")
        for key in ("template", "green_mask", "threshold"):
            self.assertEqual(param[key], original["recognition"]["param"][key])
        self.assertEqual(merged["next"], ["每日免费友情-召唤"])
        self.assertEqual(nodes["每日免费友情-菜单"], original)


if __name__ == "__main__":
    unittest.main()
