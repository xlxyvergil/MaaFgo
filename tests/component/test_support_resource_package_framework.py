"""实际资源层及任务覆盖的 MaaFramework 回归；只使用内存控制器。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import maa
import numpy as np
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom"), str(Path(__file__).parent)]
from test_formation_framework import MemoryController
from support_action import _resolve_resource_package


class ResourcePackageFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_loaded_layer_survives_stale_task_options_and_context_clone(self):
        for layers, expected, stale in ((("base", "jp"), "base", "cn"),
                                       (("base", "cn"), "cn", "base"),
                                       (("base", "cn", "jp"), "base", "cn"),
                                       (("base", "jp", "cn"), "cn", "base")):
            with self.subTest(layers=layers):
                resource = Resource()
                resource.use_cpu()
                for layer in layers:
                    self.assertTrue(resource.post_bundle(ROOT / "assets/resource" / layer).wait().succeeded)
                self.assertEqual(resource.get_node_data("资源包配置")["attach"]["loaded_resource_package"], expected)
                original_attach = dict(resource.get_node_data("资源包配置")["attach"])
                self.assertEqual(original_attach["resource_package"], "base")
                seen = []

                class Probe(CustomAction):
                    def run(self, ctx, argv):
                        for context in (ctx, ctx.clone()):
                            seen.append((_resolve_resource_package(context),
                                         context.get_node_data("资源包配置")["attach"]))
                        return True

                resource.register_custom_action("support_resource_probe", Probe())
                controller = MemoryController(np.zeros((720, 1280, 3), np.uint8))
                self.assertTrue(controller.post_connection().wait().succeeded)
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                try:
                    with patch("support_action.mfaalog.warning"):
                        self.assertTrue(tasker.post_task("support_resource_probe", {
                            "support_resource_probe": {
                                "action": {"type": "Custom", "param": {"custom_action": "support_resource_probe"}},
                                "pre_delay": 0, "post_delay": 0, "on_error": [],
                            },
                            "资源包配置": {"attach": {"resource_package": stale}},
                        }).wait().succeeded)
                    self.assertEqual(len(seen), 2)
                    for package, attach in seen:
                        self.assertEqual(package, expected)
                        self.assertEqual(attach["resource_package"], stale)
                        self.assertEqual(attach["loaded_resource_package"], expected)
                    # 任务覆盖不污染资源本体，也未发生真实/模拟输入。
                    self.assertEqual(resource.get_node_data("资源包配置")["attach"], original_attach)
                    self.assertEqual(controller.inputs, 0)
                finally:
                    tasker.post_stop().wait()


if __name__ == "__main__":
    unittest.main()
