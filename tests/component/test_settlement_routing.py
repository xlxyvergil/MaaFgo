"""Exercise the actual settlement next graph without device input."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

import maa
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_formation_framework import MemoryController
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker
from maa.define import LoggingLevelEnum

ROOT = Path(__file__).resolve().parents[2]


class SettlementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.source = {}
        for name in ("原生自动战斗调度", "原生自动战斗", "地狱的试炼", "战斗完成", "跳过剧情", "Dummy"):
            cls.source.update(json.loads((ROOT / f"assets/resource/base/pipeline/{name}.json").read_text("utf-8")))

    def run_scenario(self, entry, screens, *, succeeds=True, blind_limit=None,
                     bond_enabled=False, extra_nodes=None, fail_bond_save=False):
        # Include only the reachable graph, retaining production next ordering,
        # JumpBack edges and max_hit. Replace visual algorithms and clicks only.
        nodes = {}
        def include(name):
            name = name.removeprefix("[JumpBack]")
            if name in nodes:
                return
            node = copy.deepcopy((extra_nodes or {}).get(name, self.source.get(name)))
            self.assertIsNotNone(node, f"missing pipeline node {name}")
            if name == "结算-羁绊满停止":
                node["enabled"] = bond_enabled
            nodes[name] = node
            for raw in node.get("next", []) + node.get("on_error", []):
                include(raw)
        include(entry)
        state = {"index": 0, "seen": []}
        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                index = state["index"]
                return (0, 0, 1, 1) if index < len(screens) and argv.node_name in screens[index] else None
        class Record(CustomAction):
            def run(self, context, argv):
                state["seen"].append(argv.node_name)
                if state["index"] < len(screens) and argv.node_name in screens[state["index"]]:
                    state["index"] += 1
                return not (fail_bond_save and argv.node_name == "结算-羁绊满停止")
        for name, node in nodes.items():
            node.pop("focus", None)
            node.pop("post_wait_freezes", None)
            node.pop("pre_wait_freezes", None)
            node.update(pre_delay=0, post_delay=0, timeout=150, rate_limit=1)
            if node.get("recognition", {}).get("type", "DirectHit") != "DirectHit":
                node["recognition"] = {"type": "Custom", "param": {"custom_recognition": "screen"}}
                node.pop("inverse", None)
            # Keep the real StopTask semantics: replacing it with a recorder
            # would miss an accidental JumpBack return into another battle.
            if node.get("action", {}).get("type") != "StopTask":
                node["action"] = {"type": "Custom", "param": {"custom_action": "record"}}
            if blind_limit is not None and name.startswith("结算推进_点击右上角"):
                node["max_hit"] = blind_limit
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pipeline"
            path.mkdir()
            (path / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_recognition("screen", Screen())
            resource.register_custom_action("record", Record())
            controller = MemoryController(np.zeros((720, 1280, 3), dtype=np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            job = tasker.post_task(entry)
            deadline = time.monotonic() + 10
            try:
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(job.status.done, "unbounded settlement loop")
                self.assertEqual(job.status.succeeded, succeeds)
            finally:
                tasker.post_stop().wait()
            self.assertEqual(controller.inputs, 0)
        return state["seen"]

    def test_defeat_enters_retreat_without_blind_clicks(self):
        seen = self.run_scenario("结束战斗_不回主界面", [
            {"战斗失败_不回主界面"}, {"失败2_不回主界面"}, {"战斗结束关闭_不回主界面"},
            {"可能已经结束战斗_不回主界面"},
        ])
        self.assertIn("失败2_不回主界面", seen)
        self.assertNotIn("结算推进_点击右上角", seen)

    def test_final_friend_request_never_clicks_continue(self):
        seen = self.run_scenario("结束战斗_不回主界面", [
            {"好友申请界面_不回主界面"}, {"连续出击_继续", "战斗结束关闭_不回主界面"},
            {"可能已经结束战斗_不回主界面"},
        ])
        self.assertNotIn("连续出击_继续", seen)
        self.assertIn("战斗结束关闭_不回主界面", seen)

    def test_continue_friend_request_does_click_continue(self):
        seen = self.run_scenario("结束战斗_连续出击继续", [
            {"好友申请界面_连续出击继续"}, {"连续出击_继续"},
        ])
        self.assertIn("连续出击_继续", seen)

    def test_story_returns_to_settlement_then_continues(self):
        seen = self.run_scenario("结束战斗_连续出击继续", [
            {"跳过剧情-点击跳过"}, {"跳过剧情-确认跳过"}, {"连续出击_继续"},
        ])
        self.assertIn("跳过剧情-确认跳过", seen)
        self.assertIn("连续出击_继续", seen)

    def test_unknown_screen_exhausts_blind_limit(self):
        seen = self.run_scenario("结束战斗_不回主界面", [], succeeds=False, blind_limit=3)
        self.assertEqual(seen.count("结算推进_点击右上角"), 3)

    def test_activity_close_exits_once_before_generic_close_or_blind_click(self):
        for entry in ("结束战斗_不回主界面", "结束战斗_连续出击继续",
                      "结算推进_点击右上角", "结算推进_点击右上角_继续"):
            with self.subTest(entry=entry):
                seen = self.run_scenario(entry, [
                    {"结算-活动报酬返回地图", "战斗结束关闭_不回主界面"},
                    {"可能已经结束战斗_不回主界面"},
                ])
                self.assertEqual(seen.count("结算-活动报酬返回地图"), 1)
                self.assertIn("可能已经结束战斗_不回主界面", seen)
                self.assertNotIn("战斗结束关闭_不回主界面", seen)
                self.assertNotIn("连续出击_继续", seen)
                # Direct entry into the existing blind node itself clicks once;
                # its next poll must exit via the new branch without more clicks.
                blind = [n for n in seen if n.startswith("结算推进_点击右上角")]
                self.assertEqual(blind, [entry] if entry.startswith("结算推进_") else [])

    def test_activity_escape_without_map_confirmation_fails_without_extra_input(self):
        seen = self.run_scenario("结束战斗_不回主界面", [
            {"结算-活动报酬返回地图"},
        ], succeeds=False)
        self.assertEqual(seen.count("结算-活动报酬返回地图"), 1)
        self.assertNotIn("结算推进_点击右上角", seen)

    def test_reward_details_return_to_each_settlement_poll_before_other_inputs(self):
        # ESC may reveal another reward or a continue prompt rather than a map.
        # Exercise every production poll that also handles the larger close button.
        entries = [name for name, node in self.source.items()
                   if "结算-活动报酬返回地图" in node.get("next", [])]
        for entry in entries:
            with self.subTest(entry=entry):
                terminal = ("连续出击_继续" if "继续" in entry and
                            "连续出击_继续" in self.source[entry]["next"]
                            else "可能已经结束战斗_不回主界面")
                seen = self.run_scenario(entry, [
                    {entry},
                    {"结算-奖励详情返回", "剧情奖励-礼装详情关闭",
                     "结算-活动报酬返回地图"},
                    {"结算-奖励详情返回"},
                    {terminal},
                ])
                self.assertEqual(seen.count("结算-奖励详情返回"), 2)
                self.assertIn(terminal, seen)
                self.assertNotIn("剧情奖励-礼装详情关闭", seen)
                self.assertNotIn("结算-活动报酬返回地图", seen)
                blind = [n for n in seen if n.startswith("结算推进_点击右上角")]
                self.assertEqual(blind, [entry] if entry.startswith("结算推进_") else [])

    def test_reward_escape_without_return_evidence_does_not_finish_battle(self):
        for entry in ("结算-关闭后返回复核", "结算-关闭后返回复核_继续"):
            with self.subTest(entry=entry):
                seen = self.run_scenario(entry, [{"结算-奖励详情返回"}], succeeds=False)
                self.assertEqual(seen.count("结算-奖励详情返回"), 1)
                self.assertNotIn("全局空节点", seen)
                self.assertNotIn("连续出击_继续", seen)
                self.assertFalse(any(n.startswith("结算推进_") for n in seen))

    def test_generic_close_waits_for_activity_rewards_and_map_before_completion(self):
        for entry, friend, close in (
            ("结束战斗_不回主界面", "好友申请界面_不回主界面", "战斗结束关闭_不回主界面"),
            ("结束战斗_连续出击继续", "好友申请界面_连续出击继续", "战斗结束关闭_连续出击继续"),
        ):
            with self.subTest(entry=entry):
                seen = self.run_scenario(entry, [
                    {friend}, {close},
                    {"结算-活动报酬返回地图"},
                    {"可能已经结束战斗_不回主界面"},
                ])
                self.assertEqual(seen.count(close), 1)
                self.assertEqual(seen.count("结算-活动报酬返回地图"), 1)
                self.assertEqual(seen[-1], "全局空节点")
                self.assertIn("可能已经结束战斗_不回主界面", seen)
                self.assertNotIn("连续出击_继续", seen)
                self.assertFalse(any(n.startswith("结算推进_") for n in seen))

    def test_generic_close_without_return_confirmation_does_not_finish_battle(self):
        seen = self.run_scenario("结束战斗_不回主界面", [
            {"战斗结束关闭_不回主界面"},
        ], succeeds=False)
        self.assertEqual(seen.count("战斗结束关闭_不回主界面"), 1)
        self.assertNotIn("全局空节点", seen)
        self.assertFalse(any(n.startswith("结算推进_") for n in seen))

    def test_continue_after_reward_popup_close_stays_in_settlement(self):
        for entry in ("结束战斗_连续出击继续", "作战成功_结束战斗_继续",
                      "好友申请界面_连续出击继续", "结算推进_点击右上角_继续"):
            with self.subTest(entry=entry):
                seen = self.run_scenario(entry, [
                    {entry}, {"战斗结束关闭_连续出击继续"},
                    {"连续出击_继续"},
                ])
                self.assertEqual(seen.count("连续出击_继续"), 1)
                self.assertIn("结算-关闭后返回复核_继续", seen)
                self.assertNotIn("战斗结束关闭_不回主界面", seen)

    def test_final_closes_delayed_continue_then_activity_rewards(self):
        seen = self.run_scenario("结束战斗_不回主界面", [
            {"战斗结束关闭_不回主界面"},
            {"结算-最终场连续出击关闭", "连续出击_继续"},
            {"结算-活动报酬返回地图"},
            {"可能已经结束战斗_不回主界面"},
        ])
        self.assertIn("结算-最终场连续出击关闭", seen)
        self.assertNotIn("连续出击_继续", seen)
        self.assertIn("可能已经结束战斗_不回主界面", seen)

    def test_bond_max_preempts_final_continuous_and_trial_advancement(self):
        for entry in ("结束战斗_不回主界面", "结束战斗_连续出击继续",
                      "地狱的试炼-结算调度"):
            with self.subTest(entry=entry):
                seen = self.run_scenario(entry, [{
                    "结算-羁绊满停止", "作战成功_结束战斗", "作战成功_结束战斗_继续",
                    "连续出击_继续", "地狱的试炼-结算下一步",
                    "战斗结束关闭_不回主界面", "地狱的试炼-结算关闭",
                }], bond_enabled=True)
                self.assertEqual(seen, [entry, "结算-羁绊满停止"])

    def test_bond_guard_covers_every_settlement_repoll(self):
        for entry in ("作战成功_结束战斗", "作战成功_结束战斗_继续",
                      "好友申请界面_不回主界面", "好友申请界面_连续出击继续",
                      "结算推进_点击右上角", "结算推进_点击右上角_继续"):
            with self.subTest(entry=entry):
                seen = self.run_scenario(entry, [{entry}, {"结算-羁绊满停止", "连续出击_继续"}],
                                         bond_enabled=True)
                self.assertEqual(seen, [entry, "结算-羁绊满停止"])

    def test_bond_guard_disabled_preserves_original_continue_behavior(self):
        seen = self.run_scenario("结束战斗_连续出击继续", [{
            "结算-羁绊满停止", "连续出击_继续",
        }])
        self.assertNotIn("结算-羁绊满停止", seen)
        self.assertIn("连续出击_继续", seen)

    def test_bond_enabled_without_max_allows_original_next_and_continue(self):
        seen = self.run_scenario("结束战斗_连续出击继续", [
            {"作战成功_结束战斗_继续"}, {"连续出击_继续"},
        ], bond_enabled=True)
        self.assertNotIn("结算-羁绊满停止", seen)
        self.assertIn("作战成功_结束战斗_继续", seen)
        self.assertIn("连续出击_继续", seen)

    def test_bond_stop_unwinds_jumpback_without_parent_completion(self):
        extra = {
            "测试父调度": {"next": ["[JumpBack]结束战斗_连续出击继续", "测试父下一场"]},
            "测试父下一场": {},
        }
        seen = self.run_scenario("测试父调度", [{
            "结算-羁绊满停止", "连续出击_继续",
        }], bond_enabled=True, extra_nodes=extra)
        self.assertEqual(seen, ["测试父调度", "结束战斗_连续出击继续", "结算-羁绊满停止"])
        self.assertNotIn("测试父下一场", seen)

    def test_bond_screenshot_failure_still_stops_before_any_advance(self):
        seen = self.run_scenario("结束战斗_连续出击继续", [{
            "结算-羁绊满停止", "连续出击_继续",
        }], bond_enabled=True, fail_bond_save=True)
        self.assertEqual(seen, ["结束战斗_连续出击继续", "结算-羁绊满停止"])


if __name__ == "__main__":
    unittest.main()
