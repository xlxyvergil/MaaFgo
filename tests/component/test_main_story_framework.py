"""真实 MaaFramework 资源、Context 克隆及动作调用；仅使用内存控制器。"""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

import maa
import cv2
import numpy as np
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from main_story_action import MainStoryAction, StoryRunner, StoryStopped, map_swipes, next_names
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


class FrameworkResources:
    """Share native resource setup without inheriting unrelated test methods."""
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


class FrameworkTests(FrameworkResources, unittest.TestCase):
    def test_both_bundles_and_normalized_route(self):
        for server in ("cn", "jp"):
            with self.subTest(server=server):
                resource = self.resource(server)
                self.assertEqual(resource.get_node_data("主线-配置")["attach"]["server"], server)
                route = list(map_swipes(resource))
                self.assertEqual(route[:5], ["巡-初始化左滑"] * 5)
                self.assertEqual(route[-1], "巡-左4c")
                self.assertEqual(next_names(resource.get_node_data("全局-战前吃体力")), ["全局-体力不足"])

    def test_formation_start_uses_existing_start_button_templates(self):
        for server in ("cn", "jp"):
            with self.subTest(server=server):
                resource = self.resource(server)
                node = resource.get_node_data("进本-队伍确认")
                templates = node["recognition"]["param"]["template"]
                for template in templates:
                    with self.subTest(template=template):
                        path = ROOT / "assets/resource" / server / "image" / template
                        if not path.is_file():
                            path = ROOT / "assets/resource/base/image" / template
                        button = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                        self.assertIsNotNone(button)
                        image = np.zeros((720, 1280, 3), np.uint8)
                        h, w = button.shape[:2]
                        image[720-h:720, 1280-w:1280] = button
                        controller = ScreenController(image)
                        self.assertTrue(controller.post_connection().wait().succeeded)
                        outcomes, errors = [], []

                        class Probe(CustomAction):
                            def run(self, ctx, argv):
                                try:
                                    runner = StoryRunner(ctx.clone())
                                    outcomes.append(runner.locate())
                                    return True
                                except Exception as exc:
                                    errors.append(repr(exc))
                                    return False

                        resource.register_custom_action("formation_start_probe", Probe())
                        tasker = Tasker()
                        self.assertTrue(tasker.bind(resource, controller))
                        status = tasker.post_task("formation_start_probe", {
                            "formation_start_probe": {"action": {"type": "Custom", "param": {
                                "custom_action": "formation_start_probe"
                            }}}
                        }).wait()
                        self.assertTrue(status.succeeded, errors)
                        self.assertEqual(outcomes, [None])
                        self.assertEqual(controller.clicks, [])
                        self.assertEqual(controller.swipes, [])

    def test_multiple_support_markers_require_two_in_formation_cards(self):
        for server in ("cn", "jp"):
            resource = self.resource(server)
            folder = "cn" if server == "cn" else "base"
            path = ROOT / "assets/resource" / folder / "image/battle/助战标记.png"
            marker = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            h, w = marker.shape[:2]
            for count, y in ((0, 190), (1, 190), (2, 190), (3, 190), (6, 190), (2, 80)):
                with self.subTest(server=server, count=count, y=y):
                    image = np.zeros((720, 1280, 3), np.uint8)
                    for x in (60, 260, 460, 675, 875, 1075)[:count]:
                        image[y:y+h, x:x+w] = marker
                    controller = ScreenController(image)
                    self.assertTrue(controller.post_connection().wait().succeeded)
                    hits = []

                    class Probe(CustomAction):
                        def run(self, ctx, argv):
                            hits.append(bool(StoryRunner(ctx.clone()).reco("主线限制-多个助战", image)))
                            return True

                    resource.register_custom_action("multiple_support_probe", Probe())
                    tasker = Tasker()
                    self.assertTrue(tasker.bind(resource, controller))
                    status = tasker.post_task("multiple_support_probe", {
                        "multiple_support_probe": {"action": {"type": "Custom", "param": {
                            "custom_action": "multiple_support_probe"
                        }}}
                    }).wait()
                    self.assertTrue(status.succeeded)
                    self.assertEqual(hits, [count >= 2 and y == 190])
                    self.assertEqual(controller.clicks, [])
                    self.assertEqual(controller.swipes, [])

    def test_close_fallback_recognizes_only_button_in_bottom_region(self):
        node_name = "主线-编队弹窗关闭保底"
        for server in ("cn", "jp"):
            resource = self.resource(server)
            roi = resource.get_node_data(node_name)["recognition"]["param"]["roi"]
            folder = "cn" if server == "cn" else "base"
            path = ROOT / "assets/resource" / folder / "image/close_botton.png"
            button = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            h, w = button.shape[:2]
            for x, y, expected in ((600, 570, True), (100, 100, False)):
                with self.subTest(server=server, position=(x, y)):
                    image = np.zeros((720, 1280, 3), np.uint8)
                    image[y:y+h, x:x+w] = button
                    controller = ScreenController(image)
                    self.assertTrue(controller.post_connection().wait().succeeded)
                    hits, errors, boxes, ocr_details = [], [], [], []

                    class Probe(CustomAction):
                        def run(self, ctx, argv):
                            try:
                                runner = StoryRunner(ctx.clone())
                                result = runner.reco(node_name, image)
                                hits.append(bool(result))
                                if result:
                                    box = result.box
                                    boxes.append((box.x, box.y, box.w, box.h))
                                    ocr_details.append(result.raw_detail)
                                    runner.action(node_name, result)
                                return True
                            except Exception as exc:
                                errors.append(repr(exc))
                                return False

                    resource.register_custom_action("close_fallback_probe", Probe())
                    tasker = Tasker()
                    self.assertTrue(tasker.bind(resource, controller))
                    status = tasker.post_task("close_fallback_probe", {
                        "close_fallback_probe": {"action": {"type": "Custom", "param": {
                            "custom_action": "close_fallback_probe"
                        }}}
                    }).wait()
                    self.assertTrue(status.succeeded, errors)
                    diagnostic = {"server": server, "patch": (x, y, w, h), "roi": roi,
                                  "ocr_boxes": boxes, "clicks": controller.clicks, "ocr": ocr_details}
                    self.assertEqual(hits, [expected], diagnostic)
                    self.assertEqual(len(controller.clicks), int(expected), diagnostic)
                    if expected:
                        self.assertEqual(len(boxes), 1, diagnostic)
                        bx, by, bw, bh = boxes[0]
                        rx, ry, rw, rh = roi
                        self.assertGreater(bw, 0, diagnostic)
                        self.assertGreater(bh, 0, diagnostic)
                        self.assertTrue(rx <= bx and ry <= by and bx+bw <= rx+rw and by+bh <= ry+rh,
                                        diagnostic)
                        # OCR can expand around tightly cropped text. Require a
                        # localized box, rejecting an entire-ROI fallback, rather
                        # than treating the PNG crop as the action's target box.
                        intersection = (max(0, min(x+w, bx+bw)-max(x, bx))
                                        * max(0, min(y+h, by+bh)-max(y, by)))
                        union = w*h + bw*bh - intersection
                        self.assertGreaterEqual(intersection / union, 0.5, diagnostic)
                        click_x, click_y = controller.clicks[0]
                        self.assertTrue(bx <= click_x < bx+bw and by <= click_y < by+bh, diagnostic)
                        self.assertTrue(rx <= click_x < rx+rw and ry <= click_y < ry+rh, diagnostic)
                    else:
                        self.assertEqual(boxes, [], diagnostic)
                    self.assertEqual(controller.swipes, [])

    def test_main_story_abort_fails_without_global_recovery_or_inputs(self):
        reason = "已进入编队，但关卡要求指定首发队员；请按编制限制弹窗手动调整"
        for server in ("cn", "jp"):
            for outcome in (StoryStopped(reason), RuntimeError("截图失败"), None):
                with self.subTest(server=server, outcome=outcome):
                    resource = self.resource(server)
                    self.assertEqual(resource.get_node_data("自动推主线调度")["on_error"], [])
                    controller = ScreenController(np.zeros((720, 1280, 3), np.uint8))
                    self.assertTrue(controller.post_connection().wait().succeeded)
                    visited, messages = [], []

                    class FakeRunner:
                        completed, battles = 3, 0

                        def run(self):
                            if outcome is not None:
                                raise outcome
                            return "达到推进上限：已推进 3 关，完成战斗 0 场"

                    class Record(CustomAction):
                        def run(self, ctx, argv):
                            visited.append(argv.node_name)
                            if argv.node_name == "主线-结果提示":
                                messages.append(ctx.get_node_data(argv.node_name)["focus"]["Node.Action.Starting"])
                            return True

                    resource.register_custom_action("auto_main_story", MainStoryAction())
                    resource.register_custom_action("record_story_exit", Record())
                    # 保留全局恢复的入口和链路，但用无输入记录器代替设备操作。
                    # 若入口再次遗漏 on_error: []，会访问这些节点并误变成成功。
                    overrides = {}
                    chain = ("Global_Null", "其他异常", "回主界面")
                    for index, name in enumerate(chain):
                        overrides[name] = {
                            "recognition": {"type": "DirectHit"},
                            "action": {"type": "Custom", "param": {"custom_action": "record_story_exit"}},
                            "next": [chain[index + 1]] if index + 1 < len(chain) else [],
                            "on_error": [], "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
                        }
                    overrides["主线-结果提示"] = {
                        "action": {"type": "Custom", "param": {"custom_action": "record_story_exit"}},
                        "pre_delay": 0, "post_delay": 0,
                    }
                    tasker = Tasker()
                    self.assertTrue(tasker.bind(resource, controller))
                    try:
                        with patch("main_story_action.StoryRunner", return_value=FakeRunner()):
                            job = tasker.post_task("自动推主线调度", overrides)
                            deadline = time.monotonic() + 10
                            while not job.status.done and time.monotonic() < deadline:
                                time.sleep(0.01)
                            self.assertTrue(job.status.done, "主线中断后没有及时结束任务")
                            self.assertEqual(job.status.succeeded, outcome is None)
                        self.assertEqual(visited, ["主线-结果提示"])
                        self.assertEqual(len(messages), 1)
                        if outcome is not None:
                            self.assertIn(f"主线中断：{outcome}", messages[0])
                            self.assertIn("已推进 3 关，完成战斗 0 场", messages[0])
                        self.assertEqual(controller.clicks, [])
                        self.assertEqual(controller.swipes, [])
                    finally:
                        tasker.post_stop().wait()

    def test_cloned_context_does_not_leak_and_swipe_is_one_step(self):
        resource = self.resource("cn")
        controller = ScreenController(np.zeros((720,1280,3),np.uint8))
        self.assertTrue(controller.post_connection().wait().succeeded)
        checks, errors = [], []
        class Probe(CustomAction):
            def run(self, ctx, argv):
                try:
                    ctx.override_pipeline({"进本流程": {"next": ["助战action", "进本-队伍确认"]}})
                    local = ctx.clone()
                    checks.append(next_names(local.get_node_data("进本流程")) ==
                                  ["助战action", "进本-队伍确认"])
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
        self.assertEqual(checks, [True, True, True])
        self.assertEqual(len(controller.swipes), 1)
        self.assertEqual(controller.clicks, [])

    def test_custom_support_runs_with_recognition_detail(self):
        resource = self.resource("cn")
        controller = ScreenController(np.zeros((720, 1280, 3), np.uint8))
        self.assertTrue(controller.post_connection().wait().succeeded)
        seen, errors = [], []

        class Inner(CustomAction):
            def run(self, ctx, argv):
                seen.append((argv.node_name, argv.reco_detail.reco_id))
                return True

        class Outer(CustomAction):
            def run(self, ctx, argv):
                try:
                    runner = StoryRunner(ctx.clone())
                    for name in ("助战action", "Chaldea助战action"):
                        runner.task(name, {name: {
                            "recognition": {"type": "DirectHit"},
                            "action": {"type": "Custom", "param": {
                                "custom_action": "story_support_probe"
                            }},
                            "next": [],
                            "on_error": [],
                        }})
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action("story_support_probe", Inner())
        resource.register_custom_action("story_outer_probe", Outer())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        status = tasker.post_task("story_outer_probe", {"story_outer_probe": {
            "action": {"type": "Custom", "param": {
                "custom_action": "story_outer_probe"
            }}
        }}).wait()
        self.assertTrue(status.succeeded, errors)
        self.assertEqual([name for name, _ in seen],
                         ["助战action", "Chaldea助战action"])
        self.assertTrue(all(reco_id > 0 for _, reco_id in seen))
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
