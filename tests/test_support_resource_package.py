"""助战素材层选择及参照物低分保护；不连接设备。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from maa.agent.agent_server import AgentServer

with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import support_action as support


class ResourcePackageTests(unittest.TestCase):
    def resolve(self, attach):
        return support._resolve_resource_package(NS(get_node_data=lambda name: {"attach": attach}))

    def test_loaded_layer_wins_without_mutating_task_options(self):
        for loaded, configured in (("base", "cn"), ("cn", "base")):
            with self.subTest(loaded=loaded):
                attach = {"resource_package": configured, "loaded_resource_package": loaded}
                with patch.object(support.mfaalog, "warning") as warning:
                    self.assertEqual(self.resolve(attach), loaded)
                warning.assert_called_once()
                self.assertIn("不一致", warning.call_args.args[0])
                self.assertEqual(attach["resource_package"], configured)

    def test_matching_layer_does_not_warn(self):
        for package in ("base", "cn"):
            with patch.object(support.mfaalog, "warning") as warning:
                self.assertEqual(self.resolve({"resource_package": package,
                                               "loaded_resource_package": package}), package)
                warning.assert_not_called()

    def test_old_bundles_keep_previous_resolution(self):
        for value, expected in (("cn", "cn"), ("base", "base"), ("jp", "base"),
                                (None, "base"), ("", "base")):
            with self.subTest(value=value):
                self.assertEqual(self.resolve({"resource_package": value}), expected)
        self.assertEqual(self.resolve({}), "base")

    def test_invalid_loaded_layer_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "实际资源包标记无效"):
            self.resolve({"loaded_resource_package": "invalid"})

    def test_marker_is_owned_by_resource_layers_not_options(self):
        for package, expected in (("base", "base"), ("jp", "base"), ("cn", "cn")):
            path = ROOT / "assets/resource" / package / "pipeline/资源包配置.json"
            node = json.loads(path.read_text("utf-8"))["资源包配置"]
            self.assertEqual(node["attach"]["loaded_resource_package"], expected)
        for path in (ROOT / "assets/options").rglob("*.json"):
            self.assertNotIn('"loaded_resource_package"', path.read_text("utf-8"), str(path))


class ReferenceShiftTests(unittest.TestCase):
    def setUp(self):
        self.action = support.SupportAction()
        self.action._ref_name = "助战编入确认.png"
        self.action._ref_dir = support._image_dir("base", "support")
        for name in ("info", "warning", "error"):
            p = patch.object(support.mfaalog, name)
            p.start()
            self.addCleanup(p.stop)

    def test_both_languages_recover_known_offsets(self):
        for package in ("base", "cn"):
            with self.subTest(package=package):
                self.action._ref_dir = support._image_dir(package, "support")
                template = support._imread(str(Path(self.action._ref_dir) / self.action._ref_name))
                self.assertIsNotNone(template)
                image = np.random.default_rng(1).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
                bx, by = 30, 311
                dx, dy = 6, -5
                x, y = bx + support.REF_BASE[0] + dx, by + support.REF_BASE[1] + dy
                h, w = template.shape[:2]
                image[y:y + h, x:x + w] = template
                self.assertEqual(self.action._ref_shift(image, bx, by), (dx, dy))

    def test_low_confidence_or_incomplete_search_has_no_shift(self):
        self.assertIsNone(self.action._ref_shift(np.zeros((720, 1280, 3), np.uint8), 30, 311))
        self.assertIsNone(self.action._ref_shift(np.zeros((20, 20, 3), np.uint8), 0, 0))

    def test_missing_template_has_no_shift(self):
        with patch.object(support, "_imread", return_value=None):
            self.assertIsNone(self.action._ref_shift(np.zeros((720, 1280, 3), np.uint8), 30, 311))

    def test_threshold_boundary_and_nonfinite_scores(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        for score, accepted in ((support.TH_REF, True), (support.TH_REF - .001, False),
                                (float("nan"), False), (float("inf"), False)):
            with self.subTest(score=score), \
                 patch("cv2.minMaxLoc", return_value=(0, score, (0, 0), (40, 40))):
                shift = self.action._ref_shift(image, 30, 311)
                self.assertEqual(shift is not None, accepted)

    def test_bad_anchor_skips_criteria_and_entry_click(self):
        context = NS(tasker=NS(stopping=False, controller=object()))
        attach = {"servant": "test", "class_name": "剑士", "class_all": "def",
                  "礼装状态": "满破", "普通助战-礼装-满破": "0_空.png",
                  "np_level": 1, "level": 90,
                  **{f"skill_active_{i}": 10 for i in range(1, 4)},
                  **{f"skill_passive_{i}": 0 for i in range(1, 6)}}
        context.get_node_data = lambda name: {"attach": {"resource_package": "base"}
                                             if name == "资源包配置" else attach}
        action = self.action
        mocks = {"_get_servant_map": {"test": {"images": ["test.png"]}},
                 "_get_detector": NS(detect=lambda image: [(30, 311, 1212, 502, .99)]),
                 "_init_scale": None, "_shot_base": object(), "_ref_shift": None,
                 "_match_servant": True, "_match_ce": True, "_check_active_view": True,
                 "_tap": None}
        for name, result in mocks.items():
            setattr(action, name, Mock(return_value=result))
        action._swipe = Mock(side_effect=lambda *args: setattr(context.tasker, "stopping", True))
        with patch.object(support.time, "sleep"):
            self.assertFalse(action.run(context, NS(node_name="助战action", custom_action_param="{}")).success)
        action._ref_shift.assert_called_once()
        action._match_servant.assert_not_called()
        action._match_ce.assert_not_called()
        action._check_active_view.assert_not_called()
        # 开始扫描前仍按原流程点击三次职介页签，但不能点击任何助战条目。
        self.assertEqual(action._tap.call_count, 3)
        for call in action._tap.call_args_list:
            self.assertEqual(call.args, (context.tasker.controller, *support.CLASS_TABS["剑士"]))


if __name__ == "__main__":
    unittest.main()
