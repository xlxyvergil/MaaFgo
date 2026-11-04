"""真实 MaaFramework 内存截图回放；验证入口模板掩码，不连接设备。"""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import maa
import numpy as np
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1] / "unit")]
from test_formation_framework import MemoryController
import test_teapot_vision as vision


class TeapotEntryMaskTests(unittest.TestCase):
    def test_cn_popup_title_and_tea_guard_with_native_ocr(self):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        vision.TeapotVisionTests.setUpClass()
        image = vision.TeapotVisionTests().assemble("cn_popup_title", "cn_popup_storm_on", "cn_popup_tea_off")
        expectations = {"茶壶-识别弹窗": True, "茶壶-弹窗已关闭": True,
                        "茶壶-弹窗已开启": False, "茶壶-弹窗关闭状态并开启": True,
                        "茶壶-弹窗开启状态并关闭": False, "茶壶-识别并关闭弹窗": True}
        controller = MemoryController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        seen = {}

        class Probe(CustomAction):
            def run(self, context, argv):
                for name in expectations:
                    result = context.run_recognition(name, controller.image)
                    seen[name] = bool(result and result.hit)
                return True

        resource = Resource()
        resource.use_cpu()
        resource.register_custom_action("probe", Probe())
        self.assertTrue(resource.post_bundle(vision.ROOT / "assets/resource/base").wait().succeeded)
        self.assertTrue(resource.post_bundle(vision.ROOT / "assets/resource/cn").wait().succeeded)
        self.assertTrue(resource.override_pipeline({"茶壶测试-只读弹窗": {
            "action": {"type": "Custom", "param": {"custom_action": "probe"}},
            "next": [], "on_error": [],
        }}))
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        self.assertTrue(tasker.post_task("茶壶测试-只读弹窗").wait().succeeded)
        self.assertEqual(seen, expectations)
        for bounds in ((95, 160, 460, 820), (365, 410, 680, 735)):
            with self.subTest(missing=bounds):
                controller.image = image.copy()
                y1, y2, x1, x2 = bounds
                controller.image[y1:y2, x1:x2] = 0
                self.assertTrue(tasker.post_task("茶壶测试-只读弹窗").wait().succeeded)
                self.assertFalse(any(seen.values()))
        self.assertEqual(controller.inputs, 0)

    def test_native_green_mask_ignores_background_and_rejects_missing_icons(self):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        vision.TeapotVisionTests.setUpClass()
        original = vision.TeapotVisionTests().assemble("bar_transition")
        yy, xx = np.indices(original.shape[:2])
        foreground = ((xx - 690.5)**2 + (yy - 678.5)**2 < 16.3**2)
        foreground |= ((xx - 734.5)**2 + (yy - 678.5)**2 < 16.3**2)
        frames = []
        for color in ((0, 0, 0), (255, 255, 255)):
            image = np.full_like(original, color)
            image[foreground] = original[foreground]
            frames.append((image, True))
            for x1, x2 in ((665, 712), (713, 765)):
                missing = image.copy()
                missing[650:710, x1:x2] = color
                frames.append((missing, False))

        for region in ("base", "cn"):
            with self.subTest(region=region), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                (folder / "image/battle/teapot").mkdir(parents=True)
                shutil.copy2(vision.ROOT / f"assets/resource/{region}/image/battle/teapot/entry_icons.png",
                             folder / "image/battle/teapot/entry_icons.png")
                nodes = {"probe": {"action": {"type": "Custom", "param": {"custom_action": "probe"}}},
                         "茶壶-识别交替过渡": vision.TeapotVisionTests.pipeline["茶壶-识别交替过渡"]}
                (folder / "pipeline").mkdir()
                (folder / "pipeline/test.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf8")
                controller = MemoryController(original)
                self.assertTrue(controller.post_connection().wait().succeeded)
                seen = []

                class Probe(CustomAction):
                    def run(self, context, argv):
                        result = context.run_recognition("茶壶-识别交替过渡", controller.image)
                        seen.append(bool(result and result.hit))
                        return True

                resource = Resource()
                resource.use_cpu()
                resource.register_custom_action("probe", Probe())
                self.assertTrue(resource.post_bundle(folder).wait().succeeded)
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                for image, expected in frames:
                    controller.image = image
                    self.assertTrue(tasker.post_task("probe").wait().succeeded)
                    self.assertEqual(seen[-1], expected)
                self.assertEqual(controller.inputs, 0)


if __name__ == "__main__":
    unittest.main()
