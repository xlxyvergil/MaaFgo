"""4399 登录弹窗离线回放：真实 MaaFramework 识别，不连接安卓设备。

覆盖 6 张现场截图（已遮盖顶部账号欢迎语）、三个特征按钮的互斥识别、
固定关闭坐标、点击后复查直到特征消失，以及 max_hit 有界重试。
"""
import json
import shutil
import tempfile
import time
from pathlib import Path
import unittest

import cv2
import maa
import numpy as np
from maa.controller import CustomController
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/4399"
FEATURES = {
    "small": {
        "node": "4399-关闭弹窗-前往游戏盒领取-小",
        "template": "4399-前往游戏盒领取-小.png",
        "target": (770, 219, 12, 14),
    },
    "large": {
        "node": "4399-关闭弹窗-前往游戏盒领取-大",
        "template": "4399-前往游戏盒领取-大.png",
        "target": (815, 170, 16, 20),
    },
    "notice": {
        "node": "4399-关闭弹窗-我知道了前往查看",
        "template": "4399-我知道了前往查看.png",
        "target": (769, 252, 15, 11),
    },
}
EXPECTED = {1: "notice", 2: "large", 3: "small", 4: "small", 5: "small", 6: "small"}


def read_image(path):
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


class PopupController(CustomController):
    def __init__(self, image, disappear_after=None):
        self.image = image
        self.blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.disappear_after = disappear_after
        self.clicks = []
        super().__init__()

    def connect(self):
        return True

    def request_uuid(self):
        return "4399-popup-memory"

    def get_features(self):
        return 0

    def screencap(self):
        return self.image

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.disappear_after is not None and len(self.clicks) >= self.disappear_after:
            self.image = self.blank
        return True

    def reject_input(self, *args):
        return False

    start_app = stop_app = swipe = touch_down = touch_move = touch_up = reject_input
    click_key = input_text = key_down = key_up = reject_input


class PopupReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.snapshot = json.loads(
            (ROOT / "assets/resource/cn/pipeline/4399登录弹窗.json").read_text("utf-8"))
        cls.temp = tempfile.TemporaryDirectory()
        bundle = Path(cls.temp.name)
        (bundle / "pipeline").mkdir()
        (bundle / "image/4399").mkdir(parents=True)
        for feature in FEATURES.values():
            shutil.copy2(ROOT / "assets/resource/cn/image/4399" / feature["template"],
                         bundle / "image/4399" / feature["template"])

        (bundle / "default_pipeline.json").write_text(
            json.dumps({"Default": {"on_error": ["Global_Null"]}}), "utf-8")
        nodes = {"Global_Null": {}}
        for key, feature in FEATURES.items():
            node = json.loads(json.dumps(cls.snapshot[feature["node"]], ensure_ascii=False))
            node.update(enabled=True, pre_delay=0, post_delay=0, post_wait_freezes=0,
                        rate_limit=1, on_error=[], next=["TEST_PARENT"])
            nodes[feature["node"]] = node
        nodes["TEST_PARENT"] = {
            "timeout": 15000,
            "rate_limit": 1,
            "on_error": [],
            "next": [feature["node"] for feature in FEATURES.values()] + ["TEST_END"],
        }
        nodes["TEST_END"] = {"rate_limit": 1, "on_error": []}
        for key, feature in FEATURES.items():
            isolated = json.loads(json.dumps(nodes[feature["node"]], ensure_ascii=False))
            isolated.update(max_hit=1, next=[f"TEST_ONLY_{key}"])
            nodes[f"TEST_ONLY_NODE_{key}"] = isolated
            nodes[f"TEST_ONLY_{key}"] = {
                "timeout": 2000,
                "rate_limit": 1,
                "on_error": [],
                "next": [f"TEST_ONLY_NODE_{key}", "TEST_END"],
            }
        (bundle / "pipeline/t.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
        cls.resource = Resource()
        cls.resource.use_cpu()
        assert cls.resource.post_bundle(bundle).wait().succeeded

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_popup(self, image, disappear_after=None, entry="TEST_PARENT"):
        controller = PopupController(image, disappear_after)
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(self.resource, controller))
        job = tasker.post_task(entry)
        deadline = time.monotonic() + 20
        while not job.status.done and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            self.assertTrue(job.status.done, "4399 弹窗流程未在上限内结束")
            return job.status.succeeded, controller.clicks
        finally:
            tasker.post_stop().wait()

    def assert_inside(self, clicks, target):
        x, y, w, h = target
        for click_x, click_y in clicks:
            self.assertGreaterEqual(click_x, x)
            self.assertLessEqual(click_x, x + w)
            self.assertGreaterEqual(click_y, y)
            self.assertLessEqual(click_y, y + h)

    def test_each_screenshot_matches_only_its_feature(self):
        for index, expected in EXPECTED.items():
            image = read_image(FIXTURES / f"test_{index}.png")
            for key, feature in FEATURES.items():
                with self.subTest(screenshot=index, feature=key):
                    succeeded, clicks = self.run_popup(image, entry=f"TEST_ONLY_{key}")
                    self.assertTrue(succeeded)
                    if key == expected:
                        self.assertEqual(len(clicks), 1)
                        self.assert_inside(clicks, feature["target"])
                    else:
                        self.assertEqual(clicks, [])

    def test_click_repeats_until_feature_disappears(self):
        for index, disappear_after in ((1, 1), (2, 2), (4, 3), (4, 4)):
            with self.subTest(screenshot=index, disappear_after=disappear_after):
                succeeded, clicks = self.run_popup(
                    read_image(FIXTURES / f"test_{index}.png"), disappear_after)
                self.assertTrue(succeeded)
                self.assertEqual(len(clicks), disappear_after)
                self.assert_inside(clicks, FEATURES[EXPECTED[index]]["target"])

    def test_no_feature_never_clicks(self):
        succeeded, clicks = self.run_popup(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertTrue(succeeded)
        self.assertEqual(clicks, [])

    def test_persistent_feature_is_bounded_by_max_hit(self):
        succeeded, clicks = self.run_popup(read_image(FIXTURES / "test_4.png"))
        self.assertTrue(succeeded)
        self.assertEqual(len(clicks), self.snapshot[FEATURES["small"]["node"]]["max_hit"])
        self.assert_inside(clicks, FEATURES["small"]["target"])


if __name__ == "__main__":
    unittest.main()
