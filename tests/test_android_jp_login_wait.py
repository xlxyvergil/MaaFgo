"""日服安卓登录对后继界面使用有限等待，保留原点击和失败语义。"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = "jp/pipeline/run_FGO.json"
spec = importlib.util.spec_from_file_location("prepare_android_jp_login", ROOT / "tools/prepare_android.py")
android = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android)


class AndroidJpLoginWaitTests(unittest.TestCase):
    def test_real_login_conversion_extends_wait_without_changing_click_or_failure(self):
        path = ROOT / "assets/resource" / PIPELINE
        original_bytes = path.read_bytes()
        source = json.loads(original_bytes)
        converted = copy.deepcopy(source)
        android.adapt_transition_timeouts(converted, PIPELINE)
        for name in ("自动登录", "自动登录2"):
            with self.subTest(node=name):
                self.assertEqual(converted[name]["timeout"], 60000)
                self.assertGreater(converted[name]["timeout"], 0)
                for field in ("action", "next", "post_delay", "max_hit", "on_error"):
                    self.assertEqual(converted[name][field], source[name][field], field)
                self.assertEqual(converted[name]["max_hit"], 3)
                self.assertEqual(converted[name]["on_error"], [])
        self.assertEqual(converted["直到达到主界面"], source["直到达到主界面"])
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_existing_longer_wait_is_preserved(self):
        source = android.read_json(ROOT / "assets/resource" / PIPELINE)
        source["自动登录2"]["timeout"] = 90000
        android.adapt_transition_timeouts(source, PIPELINE)
        self.assertEqual(source["自动登录2"]["timeout"], 90000)
        self.assertEqual(source["自动登录"]["timeout"], 60000)

    def test_framework_waits_on_title_node_before_home_can_match(self):
        # 缩时 100 倍：主界面在 350ms 出现，原 200ms 预算失败，安卓 600ms 预算成功。
        # 保留真实 next/JumpBack、max_hit 和 on_error，避免把 HOME.timeout 误当为前置等待。
        import maa
        import numpy as np
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.custom_recognition import CustomRecognition
        from maa.define import LoggingLevelEnum
        from maa.library import Library
        from maa.resource import Resource
        from maa.tasker import Tasker

        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return "android-jp-login-budget-memory-only"
            def get_features(self): return 0
            def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)

        source = android.read_json(ROOT / "assets/resource" / PIPELINE)
        for adapted in (False, True):
            with self.subTest(android=adapted), tempfile.TemporaryDirectory() as directory:
                nodes = copy.deepcopy(source)
                if adapted:
                    android.adapt_transition_timeouts(nodes, PIPELINE)
                started = []
                confirmed = []

                class Screen(CustomRecognition):
                    def analyze(self, context, argv):
                        if argv.node_name == "自动登录2" and not started:
                            return (0, 0, 1, 1)
                        if (argv.node_name == "直到达到主界面" and started
                                and time.monotonic() - started[0] >= .35):
                            return (0, 0, 1, 1)
                        return None

                class Record(CustomAction):
                    def run(self, context, argv):
                        if argv.node_name == "自动登录2":
                            started.append(time.monotonic())
                        else:
                            confirmed.append(argv.node_name)
                        return True

                for node in nodes.values():
                    node.pop("focus", None)
                    node.update(pre_delay=0, post_delay=0, rate_limit=5,
                                timeout=node.get("timeout", 20000) // 100)
                    node["recognition"] = {"type": "Custom", "param": {"custom_recognition": "screen"}}
                nodes["自动登录2"]["action"] = {"type": "Custom", "param": {"custom_action": "record"}}
                nodes["确认抵达主界面流程"] = {"action": {"type": "Custom", "param": {"custom_action": "record"}}}
                resource_dir = Path(directory)
                android.write_json(resource_dir / "pipeline/login.json", nodes)
                resource = Resource()
                resource.use_cpu()
                self.assertTrue(resource.post_bundle(resource_dir).wait().succeeded)
                resource.register_custom_recognition("screen", Screen())
                resource.register_custom_action("record", Record())
                controller = Controller()
                self.assertTrue(controller.post_connection().wait().succeeded)
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                try:
                    job = tasker.post_task("自动登录2").wait()
                    self.assertEqual(job.succeeded, adapted)
                    self.assertEqual(len(started), 1)
                    self.assertEqual(confirmed, ["确认抵达主界面流程"] if adapted else [])
                finally:
                    tasker.post_stop().wait()


if __name__ == "__main__":
    unittest.main()
