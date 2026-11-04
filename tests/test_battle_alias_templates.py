"""形态别名共用身份，战斗模板仍须搜索实际形态目录；不连接设备。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from battle.perception import card_owner, frontline


# Existing catalog contract: 505700 is a visual form of canonical 505600.
CANONICAL = "505600"
ALIAS = "505700"
OTHER = "505300"


class BattleAliasTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.roots = (str(self.root),)
        self.first = self._texture(1)
        self.second = self._texture(2)
        self.third = self._texture(3)
        self._clear_caches()

    def tearDown(self):
        self._clear_caches()
        self.temp.cleanup()

    @staticmethod
    def _clear_caches():
        card_owner._load_servant_templates.cache_clear()
        frontline._prepared_templates.cache_clear()

    @staticmethod
    def _texture(seed):
        rng = np.random.default_rng(seed)
        rgba = rng.integers(0, 256, size=(256, 128, 4), dtype=np.uint8)
        rgba[:, :, 3] = 255
        return rgba

    def _write(self, servant_id, image, name="portrait.png"):
        directory = self.root / servant_id
        directory.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".png", image)
        self.assertTrue(ok)
        path = directory / name
        path.write_bytes(encoded.tobytes())
        return path

    @staticmethod
    def _band(image):
        return image[card_owner.VISIBLE_TEMPLATE_TOP:card_owner.VISIBLE_TEMPLATE_BOTTOM, :, :3]

    @staticmethod
    def _paste(frame, band, x, y):
        frame[y:y + band.shape[0], x:x + band.shape[1]] = band

    def test_canonical_loads_own_and_declared_alias_directories(self):
        self._write(CANONICAL, self.first, "canonical.png")
        self._write(ALIAS, self.second, "female.png")
        self._write(OTHER, self.third, "unrelated.png")
        templates = card_owner._load_servant_templates(self.roots, CANONICAL)
        self.assertEqual({item.relative_path for item in templates},
                         {f"{CANONICAL}/canonical.png", f"{ALIAS}/female.png"})

    def test_repeated_root_decodes_each_physical_alias_template_once(self):
        self._write(CANONICAL, self.first, "canonical.png")
        self._write(ALIAS, self.second, "female.png")
        repeated = (str(self.root), str(self.root / "."), str(self.root))
        with patch.object(card_owner, "_read_rgba", wraps=card_owner._read_rgba) as read:
            templates = card_owner._load_servant_templates(repeated, CANONICAL)
        self.assertEqual(len(templates), 2)
        self.assertEqual(read.call_count, 2)

    def test_unknown_positive_id_still_loads_its_own_directory(self):
        unknown = "99999999"
        self._write(unknown, self.first)
        templates = card_owner._load_servant_templates(self.roots, unknown)
        self.assertEqual([item.relative_path for item in templates], [f"{unknown}/portrait.png"])

    def test_np_lower_face_band_can_load_an_alias_only_directory(self):
        self._write(ALIAS, self.second, "female.png")
        templates = card_owner._load_servant_templates(self.roots, CANONICAL, visible_top=65)
        self.assertEqual([item.relative_path for item in templates], [f"{ALIAS}/female.png"])
        self.assertEqual(templates[0].bgr.shape, (50, 128, 3))

    def test_face_card_alias_art_returns_canonical_candidate_and_actual_path(self):
        self._write(ALIAS, self.second, "female.png")
        self._write(OTHER, self.third)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for x, y, _width, _height in card_owner.CARD_OWNER_ROIS.values():
            self._paste(frame, self._band(self.second), x + 16, y + 24)
        recognizer = card_owner.CommandCardOwnerRecognizer(
            roots=[self.root], min_score=0.98, min_margin=0.05, scales=(1.0,))
        results = recognizer.detect(frame, (CANONICAL, OTHER, None))
        self.assertEqual(len(results), 5)
        for result in results:
            self.assertEqual((result.owner_slot, result.servant_id, result.reason),
                             (1, CANONICAL, "identified"))
            self.assertEqual(result.matched_template, f"{ALIAS}/female.png")

    def test_hud_alias_art_returns_canonical_without_competing_with_its_form(self):
        self._write(CANONICAL, self.first, "male.png")
        self._write(ALIAS, self.second, "female.png")
        self._write(OTHER, self.third)
        rois = ((0, 385, 318, 160), (318, 385, 318, 160), (636, 385, 318, 160))
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self._paste(frame, self._band(self.second), 16, 409)
        self._paste(frame, self._band(self.third), 334, 409)
        recognizer = frontline.FrontlineServantRecognizer(
            rois=rois, scales=(1.0,), min_score=0.98, min_margin=0.05,
            analysis_scale=1.0, roots=[self.root])
        results = recognizer.detect(frame, (CANONICAL, OTHER))
        self.assertEqual(results[0].servant_id, CANONICAL)
        self.assertEqual(results[0].matched_template, f"{ALIAS}/female.png")
        self.assertEqual(results[1].servant_id, OTHER)
        self.assertIsNone(results[2].servant_id)

    def test_unlisted_servant_alias_is_never_admitted_as_an_extra_candidate(self):
        self._write(ALIAS, self.second, "female.png")
        self._write(OTHER, self.third)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for x, y, _width, _height in card_owner.CARD_OWNER_ROIS.values():
            self._paste(frame, self._band(self.second), x + 16, y + 24)
        recognizer = card_owner.CommandCardOwnerRecognizer(
            roots=[self.root], min_score=0.98, min_margin=0.05, scales=(1.0,))
        results = recognizer.detect(frame, (OTHER, None, None))
        for result in results:
            self.assertIsNone(result.owner_slot)
            self.assertIsNone(result.servant_id)
            self.assertEqual(result.reason, "owner_score_below_threshold")


if __name__ == "__main__":
    unittest.main()
