"""使用真实 MaaFramework 合并多选 case；内存控制器不执行游戏操作。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

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
from support_action import _custom_ce_candidates


class CandidateOverrideFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(maa.__file__).parent / "bin")
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_selected_cases_merge_and_next_task_does_not_keep_unchecked_candidates(self):
        options = {}
        for slot in ("普通助战-礼装", "冠位助战-1号礼装", "冠位助战-2号礼装"):
            options.update(json.loads((ROOT / "assets/options/support" / f"{slot}.json").read_text("utf8"))["option"])
        keys = [f"{slot}-{status}" for slot in ("普通助战-礼装", "冠位助战-1号礼装", "冠位助战-2号礼装")
                for status in ("满破", "非满破")]
        baseline = {key: "旧单选.png" for key in keys}
        seen = []

        class Probe(CustomAction):
            def run(self, ctx, argv):
                attach = ctx.get_node_data("助战action")["attach"]
                seen.append({key: _custom_ce_candidates(attach, key) for key in keys})
                return True

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "pipeline")
            folder.mkdir()
            (folder / "test.json").write_text(json.dumps({
                "助战action": {"attach": baseline},
                "probe": {"action": {"type": "Custom", "param": {"custom_action": "ce_candidate_probe"}},
                          "pre_delay": 0, "post_delay": 0},
            }, ensure_ascii=False), "utf8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(tmp).wait().succeeded)
            resource.register_custom_action("ce_candidate_probe", Probe())
            controller = MemoryController(np.zeros((720, 1280, 3), np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                multiple = []
                empty = []
                single = []
                expected = {}
                for key in keys:
                    modes = options[f"{key}-选择方式"]["cases"]
                    single.append(modes[0]["pipeline_override"])
                    empty.append(modes[1]["pipeline_override"])
                    multiple.append(modes[1]["pipeline_override"])
                    cases = options[f"{key}-候选"]["cases"][:2]
                    multiple.extend(case["pipeline_override"] for case in cases)
                    expected[key] = [case["name"] for case in cases]
                self.assertTrue(tasker.post_task("probe", multiple).wait().succeeded)
                self.assertEqual(seen[-1], expected)
                self.assertTrue(tasker.post_task("probe", single).wait().succeeded)
                self.assertEqual(seen[-1], {key: ["旧单选.png"] for key in keys})
                self.assertTrue(tasker.post_task("probe", empty).wait().succeeded)
                self.assertEqual(seen[-1], {key: ["0_空.png"] for key in keys})
                self.assertEqual(resource.get_node_data("助战action")["attach"], baseline)
                self.assertEqual(controller.inputs, 0)
            finally:
                tasker.post_stop().wait()


if __name__ == "__main__":
    unittest.main()
