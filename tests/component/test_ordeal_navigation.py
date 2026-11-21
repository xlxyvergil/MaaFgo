"""真实 MaaFramework 验证白纸化地球有限重试、全局恢复和风暴罐不足停止。"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

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
SELECT = "冻土的试炼-2"
START = "任务开始"
WAIT = "任务开始-空节点"
SUPPORT = "OrdealCall-已到助战"
SEARCH = "寻找OrdealCall"
SCROLL = "OrdealCall-滑动"


class QuestController(CustomController):
    def __init__(self, succeed_after, scene="map", start_succeed_after=1):
        self.scene = scene
        self.succeed_after = succeed_after
        self.start_succeed_after = start_succeed_after
        self.quest_clicks = []
        self.start_clicks = []
        self.popup_clicks = []
        self.unexpected = []
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return "ordeal-navigation-memory-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), np.uint8)

    def click(self, x, y):
        if self.scene == "storm" and 578 <= x < 692 and 550 <= y < 575:
            self.popup_clicks.append((x, y))
            self.scene = "map"
        elif self.scene == "map":
            self.quest_clicks.append((x, y))
            if (600 <= x < 1000 and 100 <= y < 500
                    and self.succeed_after is not None
                    and len(self.quest_clicks) >= self.succeed_after):
                self.scene = "detail"
        elif self.scene == "detail" and 500 <= x < 600 and 500 <= y < 540:
            self.start_clicks.append((x, y))
            if self.start_succeed_after is not None and len(self.start_clicks) >= self.start_succeed_after:
                self.scene = "support"
        else:
            self.unexpected.append((x, y))
        return True

    def reject(self, *args):
        self.unexpected.append(args)
        return False
    start_app = stop_app = swipe = touch_down = touch_move = touch_up = reject
    click_key = input_text = key_down = key_up = reject


class OrdealNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.source = json.loads((ROOT / "assets/resource/base/pipeline/ordeal call.json").read_text("utf8"))
        cls.defaults = json.loads((ROOT / "assets/resource/base/default_pipeline.json").read_text("utf8"))

    def run_navigation(self, *, succeed_after, scene="map", entry=None, outer=False,
                       selector=SELECT, start_succeed_after=1, storm_recognition=None,
                       next_task=False):
        entry = entry or selector
        nodes = {name: copy.deepcopy(self.source[name]) for name in (selector, START, WAIT, SUPPORT)}
        controller = QuestController(succeed_after, scene, start_succeed_after)
        seen = []

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                if argv.node_name == selector and controller.scene == "map":
                    return (765, 123, 118, 39)  # 失败现场的关卡名称模板框。
                if argv.node_name == START and controller.scene == "detail":
                    return (500, 500, 100, 40)
                if argv.node_name == SUPPORT and controller.scene == "support":
                    return (1100, 200, 100, 40)
                if argv.node_name == storm_recognition and controller.scene == "storm":
                    return (578, 550, 114, 25)
                return None

        class Record(CustomAction):
            def run(self, context, argv):
                seen.append(argv.node_name)
                return True

        if entry == SEARCH:
            for name in (SEARCH, SCROLL, "OrdealCall-识别"):
                nodes[name] = copy.deepcopy(self.source[name])
            nodes[SCROLL]["action"] = {"type": "Custom", "param": {"custom_action": "record"}}
            nodes["奏1基准点"] = {"enabled": False, "on_error": []}
        nodes["风暴罐副本-体力不足"] = {"recognition": "DirectHit", "enabled": False}
        for name, node in nodes.items():
            node.pop("focus", None)
            node.pop("doc", None)
            node.pop("post_wait_freezes", None)
            node.update(pre_delay=0, post_delay=0, timeout=40, rate_limit=1)
            if name not in (WAIT, SEARCH, SCROLL, "风暴罐副本-体力不足"):
                node["recognition"] = {"type": "Custom", "param": {"custom_recognition": "screen"}}
        for name in ("Global_Null", "battle"):
            nodes[name] = {"action": {"type": "Custom", "param": {"custom_action": "record"}}}
        if storm_recognition:
            dummy = json.loads((ROOT / "assets/resource/base/pipeline/Dummy.json").read_text("utf8"))
            for name in ("Global_Null", "风暴罐不足", "风暴罐不足2", "风暴罐不足-结束任务"):
                nodes[name] = copy.deepcopy(dummy[name])
                nodes[name].pop("focus", None)
                nodes[name].update(pre_delay=0, post_delay=0, timeout=40, rate_limit=1)
            for name in ("风暴罐不足", "风暴罐不足2"):
                nodes[name]["recognition"] = {"type": "Custom", "param": {"custom_recognition": "screen"}}
            for name in ("全局-战前吃体力", "其他异常"):
                nodes[name] = {"enabled": False}
        nodes["next-task"] = {"action": {"type": "Custom", "param": {"custom_action": "record"}}}
        if outer:
            daily = json.loads((ROOT / "assets/resource/base/pipeline/日常战斗.json").read_text("utf8"))
            nodes["执行章节导航"] = copy.deepcopy(daily["执行章节导航"])
            nodes["执行章节导航"]["next"] = [entry]
            nodes["执行章节导航"].update(timeout=40, rate_limit=1, pre_delay=0, post_delay=0)
            nodes["outer"] = {"next": ["[JumpBack]执行章节导航", "battle"], "timeout": 40, "rate_limit": 1}
            entry = "outer"
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "pipeline").mkdir()
            (bundle / "pipeline/ordeal.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf8")
            (bundle / "default_pipeline.json").write_text(json.dumps(self.defaults), "utf8")
            resource = Resource()
            resource.use_cpu()
            assert resource.post_bundle(bundle).wait().succeeded
            resource.register_custom_recognition("screen", Screen())
            resource.register_custom_action("record", Record())
            assert controller.post_connection().wait().succeeded
            tasker = Tasker()
            assert tasker.bind(resource, controller)
            try:
                job = tasker.post_task(entry)
                deadline = time.monotonic() + 3
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(job.status.done, "关卡点击重试必须在上限内停止")
                succeeded = job.status.succeeded
                if next_task:
                    self.assertTrue(tasker.post_task("next-task").wait().succeeded)
            finally:
                tasker.post_stop().wait()
        self.assertEqual(controller.unexpected, [])
        return succeeded, controller, seen

    def test_card_click_stays_inside_body_and_reaches_start(self):
        succeeded, controller, seen = self.run_navigation(succeed_after=1)
        self.assertTrue(succeeded)
        self.assertEqual(len(controller.quest_clicks), 1)
        self.assertTrue(all(x == 929 and 177 <= y < 179 for x, y in controller.quest_clicks))
        self.assertEqual(len(controller.start_clicks), 1)
        self.assertEqual(controller.scene, "support")
        self.assertEqual(seen, [])

    def test_one_dropped_card_click_retries_before_start(self):
        succeeded, controller, seen = self.run_navigation(succeed_after=2, outer=True)
        self.assertTrue(succeeded)
        self.assertEqual(len(controller.quest_clicks), 2)
        self.assertTrue(all(x == 929 and 177 <= y < 179 for x, y in controller.quest_clicks))
        self.assertEqual(len(controller.start_clicks), 1)
        self.assertEqual(seen, ["battle"])

    def test_three_dropped_clicks_enter_global_recovery(self):
        succeeded, controller, seen = self.run_navigation(succeed_after=None, outer=True)
        self.assertTrue(succeeded)
        self.assertEqual(len(controller.quest_clicks), 3)
        self.assertEqual(controller.start_clicks, [])
        self.assertEqual(seen, ["Global_Null", "battle"])

    def test_wrong_quest_or_unknown_screen_never_receives_click(self):
        succeeded, controller, seen = self.run_navigation(succeed_after=1, scene="unknown")
        self.assertTrue(succeeded)
        self.assertEqual(controller.quest_clicks, [])
        self.assertEqual(seen, ["Global_Null"])

    def test_shared_start_wait_failure_uses_global_recovery(self):
        succeeded, controller, seen = self.run_navigation(succeed_after=1, scene="unknown", entry=WAIT, outer=True)
        self.assertTrue(succeeded)
        self.assertEqual(controller.quest_clicks, [])
        self.assertEqual(seen, ["Global_Null", "battle"])

    def test_start_button_dropped_click_retries_and_confirms_support(self):
        succeeded, controller, seen = self.run_navigation(
            succeed_after=1, start_succeed_after=2, outer=True)
        self.assertTrue(succeeded)
        self.assertEqual(len(controller.quest_clicks), 1)
        self.assertEqual(len(controller.start_clicks), 2)
        self.assertEqual(controller.scene, "support")
        self.assertEqual(seen, ["battle"])

    def test_start_button_exhaustion_uses_global_recovery(self):
        succeeded, controller, seen = self.run_navigation(
            succeed_after=1, start_succeed_after=None, outer=True)
        self.assertTrue(succeeded)
        self.assertEqual(len(controller.start_clicks), 3)
        self.assertEqual(controller.scene, "detail")
        self.assertEqual(seen, ["Global_Null", "battle"])

    def quest_selectors(self):
        return [name for name, node in self.source.items()
                if name != START and node.get("recognition", {}).get("type") == "TemplateMatch"
                and START in node.get("next", [])]

    def test_all_19_options_have_reachable_guarded_selectors_and_positive_targets(self):
        cases = json.loads((ROOT / "assets/options/quests/OrdealCall.json").read_text("utf8"))["option"]["OrdealCall"]["cases"]
        selectors = set(self.quest_selectors())
        self.assertEqual(len(cases), 19)
        self.assertEqual(len(selectors), 19)
        reached = set()
        for case in cases:
            with self.subTest(quest=case["name"]):
                pending = [case["name"]]
                visited = set()
                while pending:
                    name = pending.pop()
                    if name in visited:
                        continue
                    visited.add(name)
                    node = self.source[name]
                    self.assertNotIn("on_error", node)
                    target = node.get("action", {}).get("param", {}).get("target")
                    if isinstance(target, list):
                        self.assertEqual(len(target), 4)
                        self.assertGreater(target[2], 0)
                        self.assertGreater(target[3], 0)
                    if name in selectors:
                        reached.add(name)
                    else:
                        pending.extend(n.removeprefix("[JumpBack]") for n in node.get("next", []))
                self.assertEqual(len(visited & selectors), 1)
        self.assertEqual(reached, selectors)

    def test_all_19_quest_cards_retry_one_drop_then_confirm_support(self):
        for name in self.quest_selectors():
            with self.subTest(selector=name):
                succeeded, controller, seen = self.run_navigation(
                    succeed_after=2, selector=name, outer=True)
                self.assertTrue(succeeded)
                self.assertEqual(len(controller.quest_clicks), 2)
                self.assertEqual(len(controller.start_clicks), 1)
                self.assertEqual(controller.scene, "support")
                self.assertEqual(seen, ["battle"])

    def test_all_19_quest_cards_recover_after_three_drops(self):
        for name in self.quest_selectors():
            with self.subTest(selector=name):
                succeeded, controller, seen = self.run_navigation(
                    succeed_after=None, selector=name, outer=True)
                self.assertTrue(succeeded)
                self.assertEqual(len(controller.quest_clicks), 3)
                self.assertEqual(controller.start_clicks, [])
                self.assertEqual(seen, ["Global_Null", "battle"])

    def test_missing_ordeal_entry_stops_after_ten_scrolls(self):
        succeeded, controller, seen = self.run_navigation(
            succeed_after=1, scene="unknown", entry=SEARCH)
        self.assertTrue(succeeded)
        self.assertEqual(seen, [SCROLL] * 10 + ["Global_Null"])
        self.assertEqual(controller.quest_clicks, [])

    def test_storm_popup_closes_and_stops_current_task_without_blocking_next_task(self):
        for name in ("风暴罐不足", "风暴罐不足2"):
            with self.subTest(recognition=name):
                succeeded, controller, seen = self.run_navigation(
                    succeed_after=1, scene="storm", outer=True,
                    storm_recognition=name, next_task=True)
                self.assertTrue(succeeded)
                self.assertEqual(len(controller.popup_clicks), 1)
                self.assertEqual(controller.scene, "map")
                self.assertEqual(controller.quest_clicks, [])
                self.assertEqual(controller.start_clicks, [])
                self.assertEqual(seen, ["next-task"])


class OrdealMixedEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        # Run the production Custom Actions in-process with the same framework
        # used by the device. The AgentServer decorator can be bypassed here.
        from maa.agent.agent_server import AgentServer
        sys.path.insert(0, str(ROOT / "agent/custom"))
        try:
            with patch.object(AgentServer, "register_custom_action"), patch.object(
                AgentServer, "register_custom_recognition"
            ):
                import ordeal_call_action
        finally:
            sys.path.pop(0)
            Library._is_agent_server = False
        cls.logic = ordeal_call_action
        cls.catalog = json.loads((ROOT / "assets/resource/base/pipeline/OrdealCall图鉴进本.json").read_text("utf8"))

    def run_mixed(self, *, map_lands, map_battle_starts=False, gallery_lands=True):
        seen = []
        controller = QuestController(None)

        class Record(CustomAction):
            def run(self, context, argv):
                seen.append(argv.node_name)
                return True

        action = {"type": "Custom", "param": {"custom_action": "record"}}
        entry = self.logic.CONFIG
        nodes = {
            entry: {
                **self.catalog[entry],
                "attach": {"quest": "残留的神话", "item": 6536, "item_name": "极光之钢",
                           "mode": "mixed", "engine": "native"},
            },
            "OrdealCall": {"action": action, "next": [SUPPORT] if map_lands else ["blocked"]},
            "回主界面": {"action": action, "next": ["无弹窗"]},
            "无弹窗": {},
            SUPPORT: {},
            self.logic.BATTLE_SCREEN: {"next": ["blocked"], "enabled": map_battle_starts},
            "OrdealCall-战前界面": {},
            "blocked": {"enabled": False},
            "OrdealCall-用户提示": {},
            "战斗完成信息": {"action": action},
            "OrdealCall-图鉴-进关卡": {"action": action, "next": [SUPPORT] if gallery_lands else ["blocked"]},
        }
        for name in ("OrdealCall-图鉴-打开道具列表", "OrdealCall-图鉴-找道具", "OrdealCall-图鉴-找获取途径"):
            nodes[name] = {"action": action}
        for name in ("Global_Null", self.logic.STORM_POD_EMPTY, "全局-战前吃体力"):
            nodes[name] = {"enabled": False}
        if map_lands:
            map_battle_attempt = self.logic.map_battle_attempt

            class Battle(CustomAction):
                def run(self, context, argv):
                    seen.append(argv.node_name)
                    return map_battle_attempt(context) is None or map_battle_starts

            nodes[self.logic.NATIVE_ENGINE] = {
                "action": {"type": "Custom", "param": {"custom_action": "battle"}},
                "next": [self.logic.BATTLE_SCREEN] if map_battle_starts else [],
            }
        else:
            nodes[self.logic.NATIVE_ENGINE] = {"action": action}
        for node in nodes.values():
            node.update(timeout=40, rate_limit=1, pre_delay=0, post_delay=0, on_error=[])

        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "pipeline").mkdir()
            (bundle / "pipeline/ordeal.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
            runner = self.logic.OrdealCallEntry()
            runner._at_prebattle = lambda context: True
            resource.register_custom_action("ordeal_call_entry", runner)
            resource.register_custom_action("ordeal_call_mark_battle", self.logic.MarkOrdealCallBattle())
            resource.register_custom_action("record", Record())
            if map_lands:
                resource.register_custom_action("battle", Battle())
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                succeeded = tasker.post_task(entry).wait().succeeded
            finally:
                tasker.post_stop().wait()
        self.assertFalse(self.logic._map_attempts)
        return succeeded, seen

    def test_navigation_failure_falls_back_once_and_stops_on_gallery_failure(self):
        succeeded, seen = self.run_mixed(map_lands=False, gallery_lands=False)
        self.assertFalse(succeeded)
        self.assertEqual(seen.count("回主界面"), 1)
        self.assertEqual(seen.count("OrdealCall-图鉴-进关卡"), 1)
        self.assertNotIn(self.logic.NATIVE_ENGINE, seen)
        self.assertNotIn("战斗完成信息", seen)

    def test_source_title_recognition_tolerates_observed_japanese_ocr_variants(self):
        for expected, read in (("悪夢のカボチャ", "悪夢の力ボチャ"), ("残された神話", "残されだ神話")):
            with self.subTest(expected=expected):
                label = f"白紙化地球上のフリークエスト「{read}」で獲得"
                self.assertIn(self.logic._normalized(expected), self.logic._normalized(label))
        self.assertNotIn(self.logic._normalized("悪夢のカボチャ"), self.logic._normalized("荒野の歓楽"))

    def test_engine_failure_before_start_restarts_from_catalog(self):
        succeeded, seen = self.run_mixed(map_lands=True)
        self.assertTrue(succeeded)
        self.assertEqual(seen.count(self.logic.NATIVE_ENGINE), 2)
        self.assertEqual(seen.count("回主界面"), 1)
        self.assertEqual(seen.count("战斗完成信息"), 1)

    def test_engine_failure_after_confirmed_start_does_not_retry(self):
        succeeded, seen = self.run_mixed(map_lands=True, map_battle_starts=True)
        self.assertFalse(succeeded)
        self.assertEqual(seen, ["OrdealCall", self.logic.NATIVE_ENGINE])


if __name__ == "__main__":
    unittest.main()
