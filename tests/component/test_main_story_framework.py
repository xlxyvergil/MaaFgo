"""真实 MaaFramework 资源、Context 克隆及动作调用；仅使用内存控制器。"""
from pathlib import Path
import sys
import unittest

import maa
import numpy as np
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from main_story_action import StoryRunner, StoryStopped, map_swipes, next_names
from main_story_recognition import MainStoryNext


class ScreenController(CustomController):
    def __init__(self, image):
        self.image, self.clicks, self.swipes = image, [], []
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return "story-memory-only"
    def get_features(self): return 0
    def screencap(self): return self.image.copy()
    def click(self, x, y):
        self.clicks.append((x, y))
        return True
    def swipe(self, x1, y1, x2, y2, duration):
        self.swipes.append((x1, y1, x2, y2))
        return True
    def reject(self, *args): return False
    start_app = stop_app = touch_down = touch_move = touch_up = reject
    click_key = input_text = key_down = key_up = reject


class FrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def resource(self, server):
        resource = Resource()
        resource.use_cpu()
        for folder in ("base", server):
            self.assertTrue(resource.post_bundle(ROOT / "assets/resource" / folder).wait().succeeded)
        return resource

    def test_both_bundles_and_normalized_route(self):
        for server in ("cn", "jp"):
            with self.subTest(server=server):
                resource = self.resource(server)
                self.assertEqual(resource.get_node_data("主线-配置")["attach"]["server"], server)
                route = list(map_swipes(resource))
                self.assertEqual(route[:5], ["巡-初始化左滑"] * 5)
                self.assertEqual(route[-1], "巡-左4c")
                self.assertEqual(next_names(resource.get_node_data("全局-战前吃体力")), ["全局-体力不足"])

    def test_cloned_context_does_not_leak_and_swipe_is_one_step(self):
        resource = self.resource("cn")
        controller = ScreenController(np.zeros((720,1280,3),np.uint8))
        self.assertTrue(controller.post_connection().wait().succeeded)
        checks, errors = [], []
        class Probe(CustomAction):
            def run(self, ctx, argv):
                try:
                    local = ctx.clone()
                    local.override_pipeline({"主线-配置": {"attach": {"quest_limit": 1}}})
                    checks.append(ctx.get_node_data("主线-配置")["attach"]["quest_limit"] == 10)
                    runner = StoryRunner(local)
                    runner.action("巡-初始化左滑", override={"巡-初始化左滑": {"repeat": 1}})
                    # 规范化 next 下仍正确阻止默认 AP 分支，避免进入 StopTask。
                    runner.reco = lambda name, image: name == "全局-战前吃体力"
                    try:
                        runner.interrupt(None, entering=True)
                    except StoryStopped as exc:
                        checks.append("AP 不足" in str(exc))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False
        resource.register_custom_action("story_probe", Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        status = tasker.post_task("story_probe", {"story_probe": {
            "action": {"type":"Custom","param":{"custom_action":"story_probe"}}
        }}).wait()
        self.assertTrue(status.succeeded, errors)
        self.assertEqual(checks, [True, True])
        self.assertEqual(len(controller.swipes), 1)
        self.assertEqual(controller.clicks, [])

    def test_custom_next_returns_click_point_through_framework(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_main_story import NextImageTests
        fixture = NextImageTests()
        resource = self.resource("cn")
        image, _, _, box = fixture.scene("cn", panel=True)
        controller = ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        resource.register_custom_recognition("main_story_next", MainStoryNext())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource,controller))
        self.assertTrue(tasker.post_task("主线-列表NEXT").wait().succeeded)
        self.assertEqual(controller.clicks, [(round(box[0]+box[2]/2), round(box[1]+box[3]/2+100*2/3))])


if __name__ == "__main__":
    unittest.main()
