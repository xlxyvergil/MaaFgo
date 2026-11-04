"""用正式礼装两态模板验证任意满破筛选，不连接设备。"""
import shutil
import sys
import tempfile
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


class SupportCEMatchingTests(unittest.TestCase):
    ce_dir = ROOT / "assets/resource/base/image/lizhuang"
    ce_name = "迦勒底午茶时光.png"
    origin = (36, 80)

    def setUp(self):
        for name in ("info", "warning", "error"):
            p = patch.object(support.mfaalog, name)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(support.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def make_frame(self, status, name=None):
        template = support._imread(str(self.ce_dir / status / (name or self.ce_name)))
        self.assertIsNotNone(template)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        x = self.origin[0] + support.CE_ANCHOR_NORMAL[0] + 3
        y = self.origin[1] + support.CE_ANCHOR_NORMAL[1] + 3
        h, w = template.shape[:2]
        frame[y:y + h, x:x + w] = template
        return frame

    def matches(self, frame, expected, ce_dir=None):
        return support.SupportAction()._match_ce(
            frame, str(ce_dir or self.ce_dir), *self.origin, self.ce_name,
            support.CE_ANCHOR_NORMAL, expected)

    def copy_template(self, directory, status):
        target = Path(directory) / status / self.ce_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.ce_dir / status / self.ce_name, target)

    def test_any_accepts_both_states_but_explicit_state_stays_strict(self):
        for actual in ("满破", "非满破"):
            frame = self.make_frame(actual)
            with self.subTest(actual=actual):
                self.assertTrue(self.matches(frame, "任意"))
                self.assertTrue(self.matches(frame, actual))
                self.assertFalse(self.matches(frame, support.CE_SUB_SWAP[actual]))

    def test_any_needs_only_one_existing_state_template(self):
        for available in ("满破", "非满破"):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as directory:
                self.copy_template(directory, available)
                for actual in ("满破", "非满破"):
                    self.assertTrue(self.matches(self.make_frame(actual), "任意", directory))

    def test_any_still_rejects_wrong_ce_or_empty_image(self):
        for actual in ("满破", "非满破"):
            with self.subTest(actual=actual):
                self.assertFalse(self.matches(self.make_frame(actual, "黑之圣杯.png"), "任意"))
        self.assertFalse(self.matches(np.zeros((720, 1280, 3), np.uint8), "任意"))

    def test_any_fails_when_no_template_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(self.matches(self.make_frame("满破"), "任意", directory))

    def run_chaldea_search(self, directory, status, actual="满破"):
        criteria = {
            "support_type": "normal", "class_name": "any", "servant_id": "",
            "ce_bond": "any", "active": [0, 0, 0], "passive": [0] * 5,
            "np_level": 0, "level": 0,
            "ce_specs": [{"name": self.ce_name, "slot": 1, "status": status}],
        }
        context = NS(tasker=NS(stopping=False, controller=object()),
                     get_node_data=Mock(return_value={"attach": {
                         "chaldea_enabled": True, "chaldea_import_source": "test-share",
                     }}))
        action = support.SupportAction()
        detector = NS(detect=Mock(return_value=[(*self.origin, 1218, 271, .99)]))
        for name, value in {
            "_get_servant_map": {}, "_get_detector": detector, "_init_scale": None,
            "_shot_base": self.make_frame(actual), "_ref_shift": (0, 0), "_tap": None,
        }.items():
            setattr(action, name, Mock(return_value=value))
        with patch.object(support, "fetch_share_data", return_value=({}, None, None)), \
             patch.object(support, "build_support_criteria", return_value=criteria), \
             patch.object(support, "_image_dir", return_value=str(directory)):
            result = action.run(context, NS(
                node_name="Chaldea助战action", custom_action_param='{"source_mode":"chaldea"}'))
        return result, action

    def test_chaldea_preflight_accepts_either_existing_state_for_any(self):
        for available in ("满破", "非满破"):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as directory:
                self.copy_template(directory, available)
                result, action = self.run_chaldea_search(directory, "任意")
                self.assertTrue(result.success)
                action._get_detector.assert_called_once()
                self.assertEqual(action._tap.call_count, 4)

    def test_chaldea_preflight_rejects_missing_any_and_missing_explicit_state(self):
        with tempfile.TemporaryDirectory() as directory:
            result, action = self.run_chaldea_search(directory, "任意")
            self.assertFalse(result.success)
            action._get_detector.assert_not_called()
            self.copy_template(directory, "非满破")
            result, action = self.run_chaldea_search(directory, "满破")
            self.assertFalse(result.success)
            action._get_detector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
