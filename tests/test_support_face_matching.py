"""助战头像遮挡、残缺边界与异常相关度的视觉回归；不连接设备。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from maa.agent.agent_server import AgentServer

with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    import support_action as support


class SupportFaceMatchingTests(unittest.TestCase):
    face_dir = ROOT / "assets/resource/base/image/servant_face"
    origin = (36, 184)

    def setUp(self):
        self.action = support.SupportAction()
        self.mask = self.action._get_face_mask()
        logging = patch.object(support.mfaalog, "diagnostic")
        logging.start()
        self.addCleanup(logging.stop)

    def make_frame(self, gray):
        frame = np.zeros((720, 1280, 3), np.uint8)
        x, y = self.origin[0] + 27, self.origin[1] + 45
        h, w = gray.shape
        frame[y:y + h, x:x + w] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        return frame

    def fixture_crop(self, name):
        # 两张原始取证 ROI 均从旧顶边 y39 开始，保留原图以验证等级遮挡。
        return support._imread(str(ROOT / "tests/fixtures/support_face" / name), gray=True)[6:]

    @staticmethod
    def old_mask():
        mask = np.zeros((70, 140), np.uint8)
        poly = np.array([(0,18),(0,69),(113,69),(113,45),(139,45),(139,0),(24,0)])
        cv2.fillPoly(mask, [poly], 255)
        return mask

    def matches(self, gray, filenames):
        return self.action._match_servant(
            self.make_frame(gray), str(self.face_dir), *self.origin, filenames, "normal")

    def test_all_castoria_forms_ignore_changes_outside_the_polygon(self):
        for filename in (f"f_704700{i}.png" for i in range(5)):
            template = support._imread(str(self.face_dir / filename), gray=True)
            original = template[29:93, 12:152].copy()
            for fill in (0, 255):
                with self.subTest(template=filename, excluded_fill=fill):
                    crop = original.copy()
                    crop[self.mask == 0] = fill
                    self.assertTrue(self.matches(crop, [filename]))
                    self.assertGreater(self.action._matched_face_evidence[0], .9999)
                    np.testing.assert_array_equal(
                        self.action._face_crop(self.make_frame(crop), *self.origin), crop)

    def test_jp_gilgamesh_false_negative_is_fixed_without_lowering_threshold(self):
        crop = self.fixture_crop("jp_gilgamesh.png")
        template = support._imread(str(self.face_dir / "f_2002001.png"), gray=True)
        old_crop = support._imread(str(ROOT / "tests/fixtures/support_face/jp_gilgamesh.png"), gray=True)
        old_mask = self.old_mask()
        old_crop[old_mask == 0] = round(float(old_crop[old_mask > 0].mean()))
        old_score = float(cv2.matchTemplate(template, old_crop, cv2.TM_CCOEFF_NORMED).max())
        self.assertLess(old_score, support.TH_FACE)
        self.assertEqual(support.TH_FACE, .75)
        self.assertTrue(self.matches(crop, ["f_2002001.png"]))
        self.assertGreater(self.action._matched_face_evidence[0], .82)
        self.assertFalse(self.matches(crop, ["f_5019002.png", "f_7047003.png"]))

    def test_unrestricted_identity_uses_the_same_masked_score(self):
        crop = self.fixture_crop("jp_gilgamesh.png")
        self.action._get_servant_map = Mock(return_value={
            "200200": {"id": "200200", "images": ["f_2002001.png"]},
            "501900": {"id": "501900", "images": ["f_5019002.png"]},
            "704700": {"id": "704700", "images": ["f_7047003.png"]},
        })
        self.assertEqual(self.action._identify_unrestricted_support(
            self.make_frame(crop), str(self.face_dir), *self.origin), "200200")
        self.assertEqual(self.action._matched_face_evidence[1], "f_2002001.png")
        self.assertGreater(self.action._matched_face_evidence[0], .82)

    def test_live_castoria_level_text_is_excluded_from_the_polygon(self):
        original = support._imread(str(ROOT / "tests/fixtures/support_face/jp_castoria_level120.png"), gray=True)
        template = support._imread(str(self.face_dir / "f_7047003.png"), gray=True)
        old_mask = self.old_mask()
        score = float(cv2.matchTemplate(template, original, cv2.TM_CCOEFF_NORMED, mask=old_mask).max())
        self.assertLess(score, support.TH_FACE)  # 仅改成真正 mask 后仍只有约 0.737。
        crop = self.fixture_crop("jp_castoria_level120.png")
        self.assertTrue(self.matches(crop, ["f_7047003.png"]))
        self.assertGreater(self.action._matched_face_evidence[0], .90)
        self.assertFalse(self.matches(crop, ["f_2002001.png", "f_5019002.png", "f_7050003.png"]))

    def test_missing_small_and_constant_images_cannot_match(self):
        rng = np.random.default_rng(14)
        crop = rng.integers(0, 256, self.mask.shape, dtype=np.uint8)
        for template in (None, np.zeros((63, 140), np.uint8), np.zeros((158, 158), np.uint8)):
            with self.subTest(template_shape=None if template is None else template.shape):
                self.assertEqual(self.action._face_score(template, crop), -1.0)
        template = rng.integers(0, 256, (158, 158), dtype=np.uint8)
        self.assertEqual(self.action._face_score(template, np.zeros_like(crop)), -1.0)

    def test_invalid_locations_do_not_hide_valid_match_or_pass_as_infinity(self):
        crop = np.arange(self.mask.size, dtype=np.uint8).reshape(self.mask.shape)
        template = np.zeros((158, 158), np.uint8)
        for scores, expected in (([np.nan, np.inf, -np.inf, .83], .83),
                                 ([np.nan, np.inf, 2.0], -1.0)):
            with self.subTest(scores=scores), patch("cv2.matchTemplate", return_value=np.array([scores])):
                self.assertAlmostEqual(self.action._face_score(template, crop), expected)

    def test_clipped_faces_are_rejected_before_matching(self):
        frame = np.zeros((720, 1280, 3), np.uint8)
        for bx, by in ((-28, 0), (0, -46), (1200, 0), (0, 650)):
            with self.subTest(origin=(bx, by)), patch("cv2.matchTemplate") as match:
                self.assertFalse(self.action._match_servant(
                    frame, str(self.face_dir), bx, by, ["f_7047003.png"], "normal"))
                self.assertIsNone(self.action._identify_unrestricted_support(
                    frame, str(self.face_dir), bx, by))
                match.assert_not_called()


if __name__ == "__main__":
    unittest.main()
