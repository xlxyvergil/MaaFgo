"""用真实 MaaFramework 验证地图缩放重试的次数上限与提前命中出口。"""
import copy
import json
import os
from pathlib import Path
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
ZOOM = "巡-缩小地图后重试"
FINAL = "巡-最终未找到"


class MapController(CustomController):
    def __init__(self):
        self.zoom_calls, self.clicks = 0, []
        self.swipes_after_zoom = 0
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return "event-map-memory-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), np.uint8)
    def swipe(self, *args):
        if self.zoom_calls:
            self.swipes_after_zoom += 1
        return True
    def click(self, x, y):
        self.clicks.append((x, y))
        return True
    def reject(self, *args): return False
    start_app = stop_app = click_key = input_text = key_down = key_up = reject
    touch_down = touch_move = touch_up = reject


class EventMapRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.source = json.loads((ROOT / "assets/resource/base/pipeline/地图轮巡.json").read_text("utf8"))

    def run_map(self, found, outer_mode=None):
        nodes = copy.deepcopy(self.source)
        entry = "巡-初始化左滑"
        if outer_mode:
            daily = json.loads((ROOT / "assets/resource/base/pipeline/日常战斗.json").read_text("utf8"))
            modes = json.loads((ROOT / "assets/options/战斗方式.json").read_text("utf8"))
            option = next(case for case in modes["option"]["battle_mode"]["cases"]
                          if case["name"] == outer_mode)
            nodes.update(daily)
            nodes.update(copy.deepcopy(option["pipeline_override"]))
            # 隔离导航返回后的外层行为；战斗入口只保留命中上限，不实际执行战斗。
            for name in ("执行回主界面", "执行队伍选择", "执行战斗结束"):
                nodes[name] = {"enabled": False}
            nodes["执行章节导航"]["next"] = [entry]
            nodes["bbc战斗"] = {}
            native = json.loads((ROOT / "assets/resource/base/pipeline/原生自动战斗.json").read_text("utf8"))
            nodes["原生自动战斗_多次入口"] = {
                "max_hit": native["原生自动战斗_多次入口"]["max_hit"]
            }
            nodes["地图测试-后续任务"] = {}
            entry = "通用战斗调度"
        controller = MapController()

        class Target(CustomRecognition):
            def analyze(self, context, argv):
                visible = (found == "first"
                           or (found == "retry" and controller.zoom_calls)
                           or (found == "retry_after_scan" and controller.zoom_calls
                               and controller.swipes_after_zoom))
                return (600, 350, 30, 20) if visible else None

        class Zoom(CustomAction):
            def run(self, context, argv):
                controller.zoom_calls += 1
                return True

        for node in nodes.values():
            node.update(pre_delay=0, post_delay=0, repeat_delay=0, timeout=100, rate_limit=1, on_error=[])
            if "recognition" in node:
                node["recognition"] = {"type": "Custom", "param": {"custom_recognition": "target"}}
            action = node.get("action", {})
            if action.get("type") == "Swipe":
                action["param"]["duration"] = 1
        # 双指手势另经MaaMCP实机验证；内存测试只替换其副作用，保留真实next/max_hit。
        nodes[ZOOM]["action"] = {"type": "Custom", "param": {"custom_action": "zoom"}}
        resource = Resource()
        resource.register_custom_recognition("target", Target())
        resource.register_custom_action("zoom", Zoom())
        self.assertTrue(resource.override_pipeline(nodes))
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            job = tasker.post_task(entry)
            deadline = time.monotonic() + 10
            while not job.status.done and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(job.status.done, "地图测试未能在10秒内结束")
            detail = job.get()
            self.assertTrue(job.status.succeeded, [(node.name, node.action) for node in detail.nodes][-3:])
            seen = [node.name for node in detail.nodes]
            if outer_mode:
                following = tasker.post_task("地图测试-后续任务").wait().get()
                self.assertTrue(following.status.succeeded)
                self.assertEqual([node.name for node in following.nodes], ["地图测试-后续任务"])
            return controller, seen
        finally:
            tasker.post_stop().wait()

    def test_first_pass_found_skips_zoom(self):
        controller, seen = self.run_map("first")
        self.assertEqual(controller.zoom_calls, 0)
        self.assertNotIn(ZOOM, seen)
        self.assertEqual(seen.count("巡-初始化左滑"), 1)
        self.assertEqual(seen[-1], "地图轮巡-成功")
        self.assertNotIn(FINAL, seen)

    def test_target_visible_after_zoom_skips_second_initialization(self):
        controller, seen = self.run_map("retry")
        self.assertEqual(controller.zoom_calls, 1)
        self.assertEqual(seen.count(ZOOM), 1)
        self.assertEqual(seen.count("巡-初始化左滑"), 1)
        self.assertEqual(seen.count("巡-初始化上滑"), 1)
        self.assertEqual(controller.swipes_after_zoom, 1, "only the existing target-centering swipe is needed")
        self.assertEqual(seen[seen.index(ZOOM) + 1], "巡-找到目标-设为中心")
        self.assertEqual(seen[-1], "地图轮巡-成功")
        self.assertNotIn(FINAL, seen)

    def test_target_not_visible_after_zoom_falls_back_to_second_scan(self):
        controller, seen = self.run_map("retry_after_scan")
        self.assertEqual(controller.zoom_calls, 1)
        self.assertEqual(seen.count(ZOOM), 1)
        self.assertEqual(seen.count("巡-初始化左滑"), 2)
        self.assertEqual(seen.count("巡-初始化上滑"), 2)
        self.assertEqual(seen[-1], "地图轮巡-成功")
        self.assertNotIn(FINAL, seen)

    def test_both_passes_missing_stop_after_one_zoom(self):
        controller, seen = self.run_map("missing")
        self.assertEqual(controller.zoom_calls, 1)
        self.assertEqual(controller.clicks, [])
        self.assertEqual(seen.count(ZOOM), 1)
        self.assertEqual(seen.count("巡-初始化左滑"), 2)
        self.assertEqual(seen.count("巡-初始化上滑"), 2)
        self.assertEqual(seen.count("巡-左4c"), 2)
        self.assertEqual(seen[-1], FINAL)

    def test_missing_target_stops_outer_battle_and_completion(self):
        for mode in ("bbc", "native"):
            with self.subTest(mode=mode):
                controller, seen = self.run_map("missing", outer_mode=mode)
                self.assertEqual(controller.zoom_calls, 1)
                self.assertEqual(controller.clicks, [])
                self.assertEqual(seen.count("巡-左4c"), 2)
                self.assertEqual(seen[-1], FINAL)
                for forbidden in ("执行bbc战斗", "bbc战斗", "原生自动战斗_多次入口",
                                  "战斗完成信息", "bbc弹窗信息输出"):
                    self.assertNotIn(forbidden, seen)

    def test_found_target_still_reaches_outer_battle(self):
        for mode in ("bbc", "native"):
            for found in ("first", "retry", "retry_after_scan"):
                with self.subTest(mode=mode, found=found):
                    _, seen = self.run_map(found, outer_mode=mode)
                    self.assertNotIn(FINAL, seen)
                    battle = "bbc战斗" if mode == "bbc" else "原生自动战斗_多次入口"
                    self.assertIn(battle, seen)
                    self.assertLess(seen.index("地图轮巡-成功"), seen.index(battle))
                    self.assertEqual(seen[-1], "bbc弹窗信息输出")


if __name__ == "__main__":
    unittest.main()
