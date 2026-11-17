"""冠位羁绊效果的低分、同分及阈值边界回归，不连接设备。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from maa.agent.agent_server import AgentServer

with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import support_action as support


class SupportBondMatchingTests(unittest.TestCase):
    def test_jp_screenshot_effects_with_real_templates(self):
        for effect, fixture in (("50np", "jp_np50.png"), ("original", "jp_original.png")):
            with self.subTest(effect=effect):
                crop = support._imread(str(ROOT / "tests/fixtures/support_bond" / fixture))
                self.assertIsNotNone(crop)
                frame = np.zeros((160, 250, 3), dtype=np.uint8)
                frame[70:130, 170:230] = crop
                action = support.SupportAction()
                self.assertTrue(action._match_bond(frame, 0, 0, effect))
                other = "original" if effect == "50np" else "50np"
                self.assertFalse(action._match_bond(frame, 0, 0, other))

    def test_flat_image_does_not_confirm_an_effect(self):
        frame = np.full((160, 250, 3), 255, dtype=np.uint8)
        for effect in ("50np", "original"):
            self.assertFalse(support.SupportAction()._match_bond(frame, 0, 0, effect))

    def test_clipped_screen_does_not_raise_or_confirm(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        self.assertFalse(support.SupportAction()._match_bond(frame, 0, 0, "50np"))

    def matches(self, np_score, original_score, expected):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        template = np.zeros((10, 10, 3), dtype=np.uint8)
        results = [np.array([[score]], dtype=np.float64)
                   for score in (np_score, original_score)]
        with patch.object(support, "_imread", return_value=template), \
                patch("cv2.matchTemplate", side_effect=results), \
                patch.object(support.mfaalog, "info"):
            return support.SupportAction()._match_bond(frame, 39, 189, expected)

    def test_logged_low_scores_do_not_select_either_effect(self):
        for scores in ((0.110, 0.041), (0.109, 0.041), (0.110, 0.045)):
            for expected in ("50np", "original"):
                with self.subTest(scores=scores, expected=expected):
                    self.assertFalse(self.matches(*scores, expected))

    def test_threshold_is_strict_for_both_effects(self):
        for high in (0.3999, 0.4, 0.4001):
            for expected, scores in (("50np", (high, 0.1)),
                                     ("original", (0.1, high))):
                with self.subTest(high=high, expected=expected):
                    self.assertEqual(self.matches(*scores, expected), high > 0.4)

    def test_only_the_higher_effect_matches_when_both_exceed_threshold(self):
        for scores, winner in (((0.7, 0.6), "50np"), ((0.6, 0.7), "original")):
            for expected in ("50np", "original"):
                with self.subTest(scores=scores, expected=expected):
                    self.assertEqual(self.matches(*scores, expected), expected == winner)

    def test_ties_are_not_recognized(self):
        for score in (0.1, 0.4, 0.8):
            for expected in ("50np", "original"):
                with self.subTest(score=score, expected=expected):
                    self.assertFalse(self.matches(score, score, expected))

    def test_logged_high_score_candidate_still_matches(self):
        self.assertTrue(self.matches(0.776, 0.193, "50np"))

    def test_nonfinite_scores_are_not_recognized(self):
        for invalid in (float("nan"), float("inf")):
            for scores in ((invalid, 0.7), (0.7, invalid)):
                with self.subTest(scores=scores):
                    self.assertFalse(self.matches(*scores, "50np"))

    def test_any_effect_keeps_skipping_image_recognition(self):
        with patch.object(support, "_roi") as crop:
            self.assertTrue(support.SupportAction()._match_bond(None, 0, 0, "any"))
        crop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
