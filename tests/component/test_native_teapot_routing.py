"""实际 MaaFramework 路由验证：茶壶在御主礼装之后，失败时不点击开始。"""
import copy
import json
from pathlib import Path
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


sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from test_formation_framework import MemoryController
from battle.runtime.formation_session import session_override


ROOT = Path(__file__).resolve().parents[2]
CHECK = "原生战斗-校验茶壶"
FALLBACK_CHECK = "原生战斗-兜底校验茶壶"


class NativeTeapotRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        cls.production = {}
        for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            cls.production.update(json.loads(path.read_text(encoding="utf-8")))

    def execute(self, *, mode="manual", use_teapot=False, teapot_ok=True, formation_ok=True):
        seen = []
        nodes = {name: copy.deepcopy(self.production[name]) for name in (
            "进本-点击队伍确认", CHECK, FALLBACK_CHECK, "原生战斗-茶壶配置",
        )}
        nodes["原生战斗-茶壶配置"]["attach"]["use_teapot"] = use_teapot
        for name, next_nodes in (
            ("执行手动编队检查", ["手动编队检查完成"]),
            ("手动编队检查完成", ["进本-点击队伍确认"]),
            ("执行自动编队", ["执行羁绊补齐自动编队"]),
            ("执行羁绊补齐自动编队", ["自动编队完成"]),
            ("自动编队完成", ["进本-点击队伍确认"]),
            ("编队身份-采集", ["进本-实际点击队伍确认"]),
            ("进本-实际点击队伍确认", []),
            ("进本-助战兜底跳过编队", ["进本-实际点击队伍确认"]),
        ):
            nodes[name] = {"action": {"type": "Custom", "param": {"custom_action": "record"}},
                           "next": next_nodes, "on_error": []}
        entry = {
            "manual": "执行手动编队检查", "auto": "执行自动编队",
            "plain": "进本-点击队伍确认", "fallback": "进本-助战兜底跳过编队",
            "bbc": "进本-点击队伍确认",
        }[mode]
        nodes["root"] = {"action": {"type": "Custom", "param": {"custom_action": "init"}},
                         "next": [entry], "on_error": []}
        for node in nodes.values():
            node.update(pre_delay=0, post_delay=0, timeout=100, rate_limit=1)

        class Init(CustomAction):
            def run(self, context, argv):
                return True if mode == "bbc" else context.override_pipeline(session_override("test-native"))

        class Record(CustomAction):
            def run(self, context, argv):
                seen.append(argv.node_name)
                if argv.node_name in {"执行手动编队检查", "执行自动编队"}:
                    return formation_ok
                return True

        class Teapot(CustomAction):
            def run(self, context, argv):
                value = context.get_node_data("原生战斗-茶壶配置")["attach"]["use_teapot"]
                seen.append((argv.node_name, value))
                return teapot_ok

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp, "pipeline")
            folder.mkdir()
            (folder / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), "utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_action("init", Init())
            resource.register_custom_action("record", Record())
            resource.register_custom_action("ensure_native_teapot", Teapot())
            controller = MemoryController(np.zeros((720, 1280, 3), dtype=np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            succeeded = tasker.post_task("root").wait().succeeded
            self.assertEqual(controller.inputs, 0)
        return succeeded, seen

    def test_manual_and_automatic_formation_check_before_capture_and_start(self):
        for mode in ("manual", "auto"):
            for desired in (False, True):
                with self.subTest(mode=mode, desired=desired):
                    ok, seen = self.execute(mode=mode, use_teapot=desired)
                    self.assertTrue(ok)
                    self.assertLess(seen.index((CHECK, desired)), seen.index("编队身份-采集"))
                    self.assertLess(seen.index("编队身份-采集"), seen.index("进本-实际点击队伍确认"))
                    endpoint = "手动编队检查完成" if mode == "manual" else "自动编队完成"
                    self.assertLess(seen.index(endpoint), seen.index((CHECK, desired)))

    def test_disabled_chaldea_still_checks_default_off(self):
        ok, seen = self.execute(mode="plain")
        self.assertTrue(ok)
        self.assertEqual(seen, [(CHECK, False), "编队身份-采集", "进本-实际点击队伍确认"])

    def test_support_fallback_checks_without_capturing_identities(self):
        for desired in (False, True):
            with self.subTest(desired=desired):
                ok, seen = self.execute(mode="fallback", use_teapot=desired)
                self.assertTrue(ok)
                self.assertEqual(seen, ["进本-助战兜底跳过编队", (FALLBACK_CHECK, desired),
                                        "进本-实际点击队伍确认"])

    def test_failed_formation_does_not_check_teapot_or_start(self):
        for mode in ("manual", "auto"):
            with self.subTest(mode=mode):
                ok, seen = self.execute(mode=mode, formation_ok=False)
                self.assertFalse(ok)
                self.assertEqual(seen, ["执行手动编队检查" if mode == "manual" else "执行自动编队"])

    def test_failed_teapot_check_does_not_start(self):
        for mode in ("manual", "auto", "plain", "fallback"):
            with self.subTest(mode=mode):
                ok, seen = self.execute(mode=mode, teapot_ok=False)
                self.assertFalse(ok)
                self.assertNotIn("进本-实际点击队伍确认", seen)
                self.assertNotIn("编队身份-采集", seen)

    def test_bbc_does_not_run_teapot_detection(self):
        ok, seen = self.execute(mode="bbc")
        self.assertTrue(ok)
        self.assertEqual(seen, ["进本-实际点击队伍确认"])


if __name__ == "__main__":
    unittest.main()
