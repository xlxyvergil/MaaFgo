"""Native servant profile/clear routing with in-memory states and no device."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
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
P, N = "搓丸子-", "搓丸子网络-"


class MemoryController(CustomController):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def connect(self): return True
    def request_uuid(self): return "expball-servant-profile-memory-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
    def reject(self, *args):
        self.inputs.append(args)
        return False
    start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = reject
    click_key = input_text = key_down = key_up = reject


class ExpballServantPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        folder = ROOT / "assets/resource/base/pipeline"
        cls.production = {}
        for file in ("强化从者.json", "搓丸子从者.json", "搓丸子网络.json"):
            cls.production.update(json.loads((folder / file).read_text("utf-8")))

    def scenario(self, *, clear=False, scene="selected", name="大卫", missing_tabs=(),
                 empty=False, empty_slots=None, cost="0", popup_after=None, retries=2,
                 clear_works=True):
        nodes = deepcopy(self.production)
        state = {"scene": scene, "popup": 0, "empty": empty, "cost": cost, "auto_on": True}
        slots = list(empty_slots) if empty_slots is not None else [empty] * 20
        seen, actions, boxes = [], [], []
        nodes[P + "资料从者名称"]["recognition"]["param"]["expected"] = ["^大卫$", "^ダビデ$"]
        if not clear:
            # Match the business boundary: inspect exact identity in the open
            # profile before allowing the production close route to start.
            nodes[P + "从者资料就绪"]["next"] = ["test_exact_profile_name"]
            nodes["test_exact_profile_name"] = {
                "recognition": {"type": "And", "param": {"all_of": [P + "从者资料页面", P + "资料从者名称"]}},
                "next": [P + "关闭当前从者资料"], "on_error": [],
            }

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                cfg = json.loads(argv.custom_recognition_param)
                node, source = cfg["node"], cfg["source"]
                param = source.get("param", {})
                if node.startswith(N):
                    return (740, 510, 180, 100) if state["popup"] else None
                if state["popup"]:
                    # Background predicates deliberately remain exposed. The
                    # production network-first routing must still win.
                    pass
                template = param.get("template")
                if template == "强化从者/强化完成界面.png":
                    return (399, 672, 98, 31) if state["scene"] == "selected" else None
                if template == "友情召唤/详情关闭按钮.png":
                    return (8, 11, 69, 59) if state["scene"] == "profile" else None
                if template == ["友情召唤/详情已锁定.png", "友情召唤/详情未锁定.png"]:
                    return (13, 151, 40, 37) if state["scene"] == "profile" else None
                if template == "强化从者/槽位标志.png":
                    roi = param["roi"]
                    index = ((roi[1] - 187) // 98) * 10 + (roi[0] - 391) // 87
                    return tuple(roi) if state["scene"] == "selected" and slots[index] else None
                text = ""
                if node == P + "从者强化标题" and state["scene"] == "selected":
                    text = "从者强化"
                elif node == P + "资料从者名称" and state["scene"] == "profile":
                    text = name
                elif node == P + "测试从者素材费用零" and state["scene"] == "selected":
                    text = state["cost"]
                elif node == P + "执行清除测试从者素材" and state["scene"] == "selected":
                    text = "清除"
                elif state["scene"] == "profile":
                    texts = {P + "从者资料资料页签": "资料", P + "从者资料战斗页签": "战斗形象",
                             P + "从者资料语音页签": "语音"}
                    text = "" if node in {P + item for item in missing_tabs} else texts.get(node, "")
                if text and any(re.search(pattern, text) for pattern in param.get("expected", [])):
                    return (1180, 153, 92, 33) if text == "清除" else tuple(param.get("roi", [0, 0, 1, 1]))
                return None

        class Action(CustomAction):
            def run(self, context, argv):
                cfg = json.loads(argv.custom_action_param)
                seen.append(argv.node_name)
                if cfg["type"] != "DoNothing":
                    actions.append((argv.node_name, cfg["type"]))
                    boxes.append((argv.node_name, list(argv.box)))
                if argv.node_name == N + "重试":
                    state["popup"] -= 1
                elif argv.node_name == P + "长按当前从者资料":
                    state["scene"] = "profile"
                elif argv.node_name == P + "执行关闭从者资料":
                    state["scene"] = "selected"
                elif argv.node_name == P + "执行清除测试从者素材" and clear_works:
                    slots[:] = [True] * 20
                    state["empty"], state["cost"] = True, "0"
                if argv.node_name == popup_after:
                    state["popup"] = retries
                return True

        def convert(recognition, name):
            result = deepcopy(recognition)
            if result.get("type") in ("And", "Or"):
                key = "all_of" if result["type"] == "And" else "any_of"
                for child in result.get("param", {}).get(key, []):
                    if isinstance(child, dict):
                        child["recognition"] = convert(child["recognition"], name)
                return result
            return {"type": "Custom", "param": {"custom_recognition": "servant_screen",
                    "custom_recognition_param": {"node": name, "source": result}}}

        for node, data in nodes.items():
            data.update(pre_delay=0, post_delay=0, timeout=60, rate_limit=1)
            data.pop("focus", None)
            if "recognition" in data:
                data["recognition"] = convert(data["recognition"], node)
            action = data.get("action", {"type": "DoNothing"})
            if action.get("type") != "StopTask":
                data["action"] = {"type": "Custom", "param": {"custom_action": "servant_action",
                                  "custom_action_param": {"type": action.get("type", "DoNothing")}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "pipeline/servant.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(root).wait().succeeded)
            resource.register_custom_recognition("servant_screen", Screen())
            resource.register_custom_action("servant_action", Action())
            controller = MemoryController()
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                result = tasker.post_task(P + ("清除测试从者素材" if clear else "打开当前从者资料")).wait()
                succeeded = result.succeeded
            finally:
                tasker.post_stop().wait()
            self.assertEqual(controller.inputs, [])
        return succeeded, state, seen, actions, boxes

    def test_profile_round_trip_requires_exact_large_name_and_returns_to_selected_page(self):
        for name in ("大卫", "ダビデ"):
            ok, state, seen, actions, boxes = self.scenario(name=name)
            self.assertTrue(ok)
            self.assertEqual(state["scene"], "selected")
            self.assertIn(P + "从者资料已关闭", seen)
            self.assertEqual(actions, [(P + "长按当前从者资料", "LongPress"), (P + "执行关闭从者资料", "Click")])
            self.assertEqual(boxes[-1], (P + "执行关闭从者资料", [8, 11, 69, 59]))

    def test_current_servant_remove_label_accepts_only_complete_observed_words(self):
        patterns = self.production[P + "候选已是当前从者"]["recognition"]["param"]["expected"]
        for label in ("卸除", "卸下", "移除", "はずす"):
            self.assertTrue(any(re.search(pattern, label) for pattern in patterns), label)
        for label in ("请卸除", "卸除中", "卸除该从者", "卸下X", "はずすX", ""):
            self.assertFalse(any(re.search(pattern, label) for pattern in patterns), label)

    def test_wrong_name_or_missing_profile_features_leave_profile_without_closing(self):
        for kwargs in ({"name": "大卫EX"}, {"name": "其他从者"}, {"missing_tabs": ("从者资料语音页签",)},
                       {"missing_tabs": ("从者资料资料页签", "从者资料战斗页签")}):
            with self.subTest(kwargs=kwargs):
                _, state, seen, actions, _ = self.scenario(**kwargs)
                self.assertEqual(state["scene"], "profile")
                self.assertEqual(actions, [(P + "长按当前从者资料", "LongPress")])
                self.assertNotIn(P + "从者资料已关闭", seen)
        _, _, _, actions, _ = self.scenario(scene="unknown")
        self.assertEqual(actions, [])

    def test_profile_network_waits_never_repeat_long_press_or_close(self):
        for stage in (P + "长按当前从者资料", P + "执行关闭从者资料"):
            ok, state, seen, actions, _ = self.scenario(popup_after=stage)
            self.assertTrue(ok)
            self.assertEqual(state["scene"], "selected")
            self.assertIn(P + "从者资料已关闭", seen)
            self.assertEqual(actions.count((P + "长按当前从者资料", "LongPress")), 1)
            self.assertEqual(actions.count((P + "执行关闭从者资料", "Click")), 1)
            self.assertEqual(actions.count((N + "重试", "Click")), 2)

    def test_profile_retry_exhaustion_never_reports_closed_or_reissues_input(self):
        for stage in (P + "长按当前从者资料", P + "执行关闭从者资料"):
            _, _, seen, actions, _ = self.scenario(popup_after=stage, retries=4)
            self.assertNotIn(P + "从者资料已关闭", seen)
            self.assertEqual(actions.count((N + "重试", "Click")), 3)
            self.assertLessEqual(actions.count((P + "长按当前从者资料", "LongPress")), 1)
            self.assertLessEqual(actions.count((P + "执行关闭从者资料", "Click")), 1)

    def test_clear_requires_all_twenty_empty_slots_and_zero_cost_and_preserves_auto_on(self):
        ok, state, seen, actions, boxes = self.scenario(clear=True, cost="62,800")
        self.assertTrue(ok)
        self.assertIn(P + "测试从者素材已清空", seen)
        self.assertTrue(state["auto_on"])
        self.assertEqual(actions, [(P + "执行清除测试从者素材", "Click")])
        self.assertEqual(boxes, [(P + "执行清除测试从者素材", [1180, 153, 92, 33])])
        _, _, seen, actions, _ = self.scenario(clear=True, empty=True)
        self.assertIn(P + "测试从者素材已清空", seen)
        self.assertEqual(actions, [])

    def test_zero_cost_with_one_occupied_slot_cannot_prove_empty_or_repeat_clear(self):
        for index in (0, 9, 10, 19):
            slots = [True] * 20
            slots[index] = False
            _, _, seen, actions, _ = self.scenario(clear=True, empty_slots=slots, cost="0", clear_works=False)
            self.assertNotIn(P + "测试从者素材已清空", seen)
            self.assertEqual(actions, [(P + "执行清除测试从者素材", "Click")])
        _, _, seen, actions, _ = self.scenario(clear=True, scene="unknown")
        self.assertNotIn(P + "测试从者素材已清空", seen)
        self.assertEqual(actions, [])
        _, _, seen, actions, _ = self.scenario(clear=True, empty=True, cost="1", clear_works=False)
        self.assertNotIn(P + "测试从者素材已清空", seen)
        self.assertEqual(actions, [(P + "执行清除测试从者素材", "Click")])

    def test_clear_network_retry_does_not_repeat_clear_or_submit_upgrade(self):
        ok, _, seen, actions, _ = self.scenario(clear=True, popup_after=P + "执行清除测试从者素材")
        self.assertTrue(ok)
        self.assertIn(P + "测试从者素材已清空", seen)
        self.assertEqual(actions.count((P + "执行清除测试从者素材", "Click")), 1)
        self.assertEqual(actions.count((N + "重试", "Click")), 2)
        self.assertTrue(all(name in {P + "执行清除测试从者素材", N + "重试"} for name, _ in actions))
        _, _, seen, actions, _ = self.scenario(clear=True, popup_after=P + "执行清除测试从者素材", retries=4)
        self.assertNotIn(P + "测试从者素材已清空", seen)
        self.assertEqual(actions.count((P + "执行清除测试从者素材", "Click")), 1)
        self.assertEqual(actions.count((N + "重试", "Click")), 3)


if __name__ == "__main__":
    unittest.main()
