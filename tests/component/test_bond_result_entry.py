"""Run the existing victory TemplateMatch with enabled/off option overrides."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import maa
import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_formation_framework import MemoryController

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class BondResultEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_enabled_header_and_switch_off_keep_one_existing_matcher(self):
        cases = read("assets/options/原生自动战斗羁绊满停止.json")["option"]["原生自动战斗羁绊满停止"]["cases"]
        overrides = {case["name"]: case["pipeline_override"]["战斗_胜利"] for case in cases}
        original = read("assets/resource/base/pipeline/自动战斗_感知.json")["战斗_胜利"]
        source = ROOT / "assets/resource/base/image"
        # Put the old next-step button below the new header ROI. Turning the
        # option back off must restore the original full-screen search too.
        button = cv2.imdecode(np.fromfile(str(source / "battle/下一步.png"), np.uint8), cv2.IMREAD_COLOR)
        old_frame = np.zeros((720, 1280, 3), np.uint8)
        height, width = button.shape[:2]
        old_frame[500:500 + height, 900:900 + width] = button
        report = {}

        class Probe(CustomAction):
            def run(self, context, argv):
                for mode, expected in (("all", False), ("off", True), ("slot6", False), ("off", True)):
                    self_outer.assertTrue(context.override_pipeline({"战斗_胜利": overrides[mode]}))
                    node = context.get_node_data("战斗_胜利")
                    self_outer.assertEqual(node["recognition"]["type"], "TemplateMatch")
                    result = context.run_recognition("战斗_胜利", old_frame)
                    report.setdefault("switches", []).append((mode, bool(result and result.hit)))
                    self_outer.assertEqual(bool(result and result.hit), expected)
                return True

        self_outer = self
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            (temp / "pipeline").mkdir()
            for relative in ("battle/下一步.png", "battle/bond/战后结果标题.png"):
                target = temp / "image" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, target)
            nodes = {"战斗_胜利": original, "probe": {"action": {
                "type": "Custom", "param": {"custom_action": "probe"}}}}
            (temp / "pipeline/test.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_action("probe", Probe())
            controller = MemoryController(old_frame)
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                self.assertTrue(tasker.post_task("probe").wait().succeeded, report)
                self.assertEqual(report["switches"], [("all", False), ("off", True), ("slot6", False), ("off", True)])
                self.assertEqual(controller.inputs, 0)
            finally:
                tasker.post_stop().wait()


if __name__ == "__main__":
    unittest.main()
