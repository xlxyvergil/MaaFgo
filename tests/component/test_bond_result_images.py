"""Real retained JP frames through Maa TemplateMatch; no device connection."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import cv2
import maa
import numpy as np
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(Path(__file__).resolve().parent)]
from battle.perception.bond_result import (
    BOND_MAX_NODE, read_any_bond_max, read_bond_max, read_bond_page, read_bond_upgrade,
)
from test_formation_framework import MemoryController


def load_image(name):
    path = ROOT / "tests/fixtures/bond_stop" / name
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)


class BondResultImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get("MAAFW_BINARY_PATH", Path(maa.__file__).parent / "bin")))
        Tasker.set_log_dir("")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def test_real_slot5_max_and_upgrade_overlay_use_production_matchers(self):
        positive = load_image("jp_bond_slot5_max.png")
        overlay = load_image("jp_bond_level_up_overlay.png")
        self.assertEqual(positive.shape, (720, 1280, 3))
        self.assertEqual(overlay.shape, positive.shape)
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/羁绊结算识别.json").read_text(encoding="utf-8"))
        options = json.loads((ROOT / "assets/options/原生自动战斗羁绊满停止.json").read_text(encoding="utf-8"))
        enabled = next(case for case in options["option"]["原生自动战斗羁绊满停止"]["cases"]
                       if case["name"] == "Yes")
        pipeline["战斗_胜利"] = enabled["pipeline_override"]["战斗_胜利"]
        pipeline["probe"] = {"action": {"type": "Custom", "param": {"custom_action": "probe"}}}
        observed = {}
        test = self

        class Probe(CustomAction):
            def run(self, context, argv):
                test.assertTrue(read_bond_page(context, positive))
                test.assertFalse(read_bond_upgrade(context, positive))
                test.assertTrue(read_any_bond_max(context, positive))
                test.assertEqual(read_bond_max(context, positive), (5,))
                for column in range(1, 6):
                    detail = context.run_recognition(BOND_MAX_NODE.format(column=column), positive)
                    test.assertIsNotNone(detail)
                    test.assertEqual(detail.hit, column == 5)
                    observed[f"column{column}"] = detail.best_result.score if detail.best_result else None
                test.assertFalse(read_bond_page(context, overlay))
                test.assertTrue(read_bond_upgrade(context, overlay))
                with test.assertRaisesRegex(ValueError, "page_not_visible"):
                    read_any_bond_max(context, overlay)
                with test.assertRaisesRegex(ValueError, "page_not_visible"):
                    read_bond_max(context, overlay)
                for image, expected in ((positive, True), (overlay, False)):
                    detail = context.run_recognition("战斗_胜利", image)
                    test.assertIsNotNone(detail)
                    test.assertEqual(detail.hit, expected)
                observed["checked"] = True
                return True

        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            (temp / "pipeline").mkdir()
            (temp / "pipeline/test.json").write_text(json.dumps(pipeline, ensure_ascii=False), encoding="utf-8")
            for template in {node["recognition"]["param"]["template"] for node in pipeline.values()
                             if node.get("recognition", {}).get("type") == "TemplateMatch"}:
                target = temp / "image" / template
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "assets/resource/base/image" / template, target)
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_action("probe", Probe())
            controller = MemoryController(positive)
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                self.assertTrue(tasker.post_task("probe").wait().succeeded, observed)
                self.assertTrue(observed.get("checked"), observed)
                self.assertEqual(controller.inputs, 0)
            finally:
                tasker.post_stop().wait()


if __name__ == "__main__":
    unittest.main()
