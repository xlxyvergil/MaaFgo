"""真实 MaaFramework 子任务范围验证；内存画面，不连接设备、不点击游戏。"""
import copy
from dataclasses import replace
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

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from test_battle_special_skill_scope import (ASH, COMMON, ENTRY, LILITH, PIPELINE,
                                            runtime, snapshot)
from test_formation_framework import MemoryController


class SpecialSkillScopeFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_actual_next_objects_route_and_do_not_leak_across_nested_calls_or_tasks(self):
        attempts, selected, checked, errors = [], [], [], []
        complete = snapshot()
        incomplete = replace(complete, slots=(*complete.slots[:3],
                             replace(complete.slots[3], status="unknown"), *complete.slots[4:]))
        cases = [
            (complete, None, 1, LILITH),
            (None, ("405900", "100500", "200100", None, None, None), 1, ASH),
            (incomplete, None, 1, ASH),
            (complete, None, 2, ASH),
            (complete, (None, "100500", "200100", None, None, None), 1, ASH),
            (snapshot(("100100", "200100", "300100", None, None, None)), None, 1, COMMON[-1]),
            (snapshot(("203300", "200100", "300100", None, None, None)), None, 1, ASH),
        ]

        class Probe(CustomRecognition):
            def analyze(self, context, argv):
                name = json.loads(argv.custom_recognition_param)["branch"]
                attempts.append(name)
                if name in (ASH, LILITH):
                    return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail=name)
                return None

        class Record(CustomAction):
            def run(self, context, argv):
                selected.append(argv.node_name)
                return True

        class Dispatch(CustomAction):
            def run(self, context, argv):
                try:
                    for initial, roster, battle, expected in cases:
                        before = len(attempts)
                        r = runtime(initial, roster=roster, battle=battle)
                        r.ctx = context
                        got = r._run_special_skill_pipeline()
                        checked.append((expected, got, attempts[before:]))
                        if got != expected:
                            errors.append(f"expected={expected} got={got}")
                            return False
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        nodes = {ENTRY: copy.deepcopy(PIPELINE[ENTRY])}
        for name in PIPELINE[ENTRY]["next"]:
            nodes[name] = {"action": {"type": "Custom", "param": {"custom_action": "scope_record"}},
                           "next": []}
            if name != COMMON[-1]:
                nodes[name]["recognition"] = {"type": "Custom", "param": {
                    "custom_recognition": "scope_probe", "custom_recognition_param": {"branch": name}}}
        nodes["root"] = {"action": {"type": "Custom", "param": {"custom_action": "scope_dispatch"}}}
        for node in nodes.values():
            node.update(pre_delay=0, post_delay=0, timeout=200, rate_limit=1, on_error=[])
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp, "pipeline")
            folder.mkdir()
            (folder / "test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_recognition("scope_probe", Probe())
            resource.register_custom_action("scope_record", Record())
            resource.register_custom_action("scope_dispatch", Dispatch())
            controller = MemoryController(np.zeros((720, 1280, 3), dtype=np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            for _ in range(2):
                job = tasker.post_task("root")
                deadline = time.monotonic() + 15
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.01)
                if not job.status.done:
                    tasker.post_stop().wait()
                    self.fail("scope routing timed out")
                self.assertTrue(job.status.succeeded, str(errors))
            self.assertEqual(selected, [case[3] for case in cases] * 2)
            self.assertEqual(checked[0][2], [*COMMON[:2], LILITH])
            self.assertEqual(checked[5][2], list(COMMON[:3]))
            self.assertEqual(controller.inputs, 0)
            actual = resource.get_node_data(ENTRY)["next"]
            self.assertEqual([n["name"] if isinstance(n, dict) else n for n in actual], PIPELINE[ENTRY]["next"])


if __name__ == "__main__":
    unittest.main()
