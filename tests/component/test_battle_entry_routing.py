"""真实 MaaFramework 的顶层战斗完成交接和有界进本；不连接设备。"""
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
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_formation_framework import MemoryController

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent/custom"))
from auto_battle_repeat_action import _DEFAULT_RESET_HIT_NODES
from main_story_action import _BATTLE_RESET

GUARD = "进本-盲点跳过剧情"
BLIND = "跳过剧情-直接点击跳过"
REPEAT = "原生自动战斗_多次入口"
MAIN = "进本-战斗主界面已出现"


class EntryRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.source = {}
        for name in ("原生自动战斗", "日常战斗", "进本流程", "跳过剧情"):
            cls.source.update(json.loads((ROOT / f"assets/resource/base/pipeline/{name}.json").read_text("utf-8")))

    def run_graph(self, nodes, entry, screens=(), *, succeeds=True, runs=1):
        nodes = copy.deepcopy(nodes)
        state = {"index": 0, "seen": []}

        class Screen(CustomRecognition):
            def analyze(self, context, argv):
                index = state["index"]
                current = screens[index] if index < len(screens) else set()
                hit = argv.node_name in current
                if argv.node_name == "跳过剧情-无确认弹窗":
                    hit = "跳过剧情-确认跳过" not in current
                return (0, 0, 1, 1) if hit else None

        class Record(CustomAction):
            def run(self, context, argv):
                state["seen"].append(argv.node_name)
                index = state["index"]
                current = screens[index] if index < len(screens) else set()
                if argv.node_name in current or argv.node_name == BLIND:
                    state["index"] += 1
                return True

        for name, node in nodes.items():
            node.pop("focus", None)
            node.pop("post_wait_freezes", None)
            node.update(pre_delay=0, post_delay=0, timeout=20, rate_limit=1)
            if node.get("recognition", {}).get("type", "DirectHit") != "DirectHit":
                node["recognition"] = {"type": "Custom", "param": {"custom_recognition": "screen"}}
                node.pop("inverse", None)
            node["action"] = {"type": "Custom", "param": {"custom_action": "record"}}
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
            try:
                for _ in range(runs):
                    job = tasker.post_task(entry)
                    deadline = time.monotonic() + 5
                    while not job.status.done and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(job.status.done, "进本或顶层战斗出现无界循环")
                    self.assertEqual(job.status.succeeded, succeeds)
            finally:
                tasker.post_stop().wait()
            self.assertEqual(controller.inputs, 0)
        return state["seen"]

    def outer_graph(self):
        names = ("通用战斗调度", "执行回主界面", "执行队伍选择", "执行章节导航",
                 REPEAT, "战斗完成信息", "bbc弹窗信息输出")
        nodes = {name: copy.deepcopy(self.source[name]) for name in names}
        option = json.loads((ROOT / "assets/options/战斗方式.json").read_text("utf-8"))
        case = next(case for case in option["option"]["battle_mode"]["cases"] if case["name"] == "native")
        nodes["通用战斗调度"].update(case["pipeline_override"]["通用战斗调度"])
        # 内部导航与战斗由成功记录动作替代；保留顶层生产 JumpBack 和次数限制。
        for name in ("执行回主界面", "执行队伍选择", "执行章节导航"):
            nodes[name]["next"] = []
        return nodes

    def entry_graph(self, support="进本-选择助战", *, limit=3):
        names = ("进本流程", "进本流程-超时重试", "进本-等待战斗主界面或剧情",
                 "进本-关闭告知弹窗", "进本-选择助战", "助战action", "Chaldea助战action",
                 "进本-队伍确认", MAIN, GUARD, BLIND,
                 "跳过剧情-确认跳过", "跳过剧情-无确认弹窗", "跳过剧情-点击跳过",
                 "跳过剧情-确认跳过-已消失", "跳过-可能在主界面", "完成跳过剧情")
        nodes = {name: copy.deepcopy(self.source[name]) for name in names}
        nodes[GUARD]["max_hit"] = limit  # 缩小生产60次上限，快速验证真实框架限制。
        for name in ("进本-队伍确认", MAIN):
            nodes[name]["next"] = []
        option = json.loads((ROOT / "assets/options/原生自动战斗助战方式.json").read_text("utf-8"))
        case = next(case for case in option["option"]["原生自动战斗助战方式"]["cases"]
                    if case["pipeline_override"]["进本流程"]["next"][1] == support)
        nodes["进本流程"].update(case["pipeline_override"]["进本流程"])
        return nodes

    def test_top_level_reaches_completion_once(self):
        seen = self.run_graph(self.outer_graph(), "通用战斗调度")
        self.assertEqual(seen.count(REPEAT), 1)
        self.assertEqual(seen[-2:], ["战斗完成信息", "bbc弹窗信息输出"])

    def test_new_top_level_task_can_run_again(self):
        seen = self.run_graph(self.outer_graph(), "通用战斗调度", runs=2)
        self.assertEqual(seen.count(REPEAT), 2)
        self.assertEqual(seen.count("战斗完成信息"), 2)

    def test_all_support_options_use_bounded_fallback(self):
        for support in ("进本-选择助战", "助战action", "Chaldea助战action"):
            with self.subTest(support=support):
                seen = self.run_graph(self.entry_graph(support), "进本流程",
                                      [set(), {support}, set(), {MAIN}])
                self.assertEqual(seen.count(BLIND), 2)
                self.assertIn(support, seen)
                self.assertEqual(seen[-1], MAIN)

    def test_unknown_screen_fails_after_budget_and_retries(self):
        seen = self.run_graph(self.entry_graph(), "进本流程", succeeds=False)
        self.assertEqual(seen.count(BLIND), 3)
        self.assertEqual(seen.count("进本流程-超时重试"), 2)

    def test_positive_screen_is_allowed_after_budget_exhaustion(self):
        seen = self.run_graph(self.entry_graph(), "进本流程", [set()] * 3 + [{MAIN}])
        self.assertEqual(seen.count(BLIND), 3)
        self.assertEqual(seen[-1], MAIN)

    def test_post_start_wait_is_bounded(self):
        seen = self.run_graph(self.entry_graph(), "进本-等待战斗主界面或剧情", succeeds=False)
        self.assertEqual(seen.count(BLIND), 3)

    def test_story_confirmation_returns_to_entry(self):
        seen = self.run_graph(self.entry_graph(), "进本流程",
                              [set(), {"跳过剧情-确认跳过"}, {MAIN}])
        self.assertIn("跳过剧情-确认跳过", seen)
        self.assertEqual(seen[-1], MAIN)

    def test_skip_disabled_never_blind_clicks(self):
        nodes = self.entry_graph()
        option = json.loads((ROOT / "assets/options/原生自动战斗跳过剧情.json").read_text("utf-8"))
        off = next(case for case in option["option"]["原生自动战斗跳过剧情"]["cases"] if case["name"] == "No")
        for name, override in off["pipeline_override"].items():
            nodes[name].update(override)
        seen = self.run_graph(nodes, "进本流程", succeeds=False)
        self.assertNotIn(GUARD, seen)
        self.assertNotIn(BLIND, seen)

    def test_per_battle_and_story_reset_only_blind_budget(self):
        self.assertEqual(self.source[GUARD]["max_hit"], 60)
        self.assertIn(GUARD, _DEFAULT_RESET_HIT_NODES)
        self.assertIn(GUARD, _BATTLE_RESET)
        self.assertNotIn(REPEAT, _DEFAULT_RESET_HIT_NODES)
        self.assertNotIn(REPEAT, _BATTLE_RESET)


if __name__ == "__main__":
    unittest.main()
