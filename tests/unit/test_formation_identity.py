"""合成图像仅检验算法行为，不作为真实截图准确率证据。"""
import json
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import formation as f
from battle.core.models import Confidence, FormationSlot, InitialFormation


def image_write(path, array):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", array)[1].tofile(path)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rng = np.random.default_rng(124)
        self.images = {}
        for name in ("配置变更", "开始任务", "战斗开始", "助战标记"):
            array = self.rng.integers(0, 255, (20, 35, 3), dtype=np.uint8)
            self.images[name] = array
            image_write(self.root / "battle" / f"{name}.png", array)
        self.face = self.rng.integers(0, 255, (75, 75, 3), dtype=np.uint8)
        self.other = self.rng.integers(0, 255, (75, 75, 3), dtype=np.uint8)
        image_write(self.root / "NarrowFigures/f_10000.png", self.face)
        image_write(self.root / "NarrowFigures/f_10001.png", self.face)  # 同身份不同模板
        image_write(self.root / "NarrowFigures/f_20000.png", self.other)
        self.catalog = self.root / "servants.json"
        self.catalog.write_text(json.dumps({"servants": [
            {"id": "1000", "name": "同名", "class": "saber", "images": ["f_10000.png"]},
            {"id": "2000", "name": "同名", "class": "caster", "images": []},
        ]}), encoding="utf-8")
        self.calibration = f.FormationCalibration.parse({
            "verified": True, "calibration_id": "synthetic-test-only",
            "identity_threshold": 0.95, "identity_margin": 0.25,
        })
        self.image = np.full((720, 1280, 3), (40, 60, 100), dtype=np.uint8)
        self.image[40:60, 20:55] = self.images["配置变更"]
        self.image[620:640, 1000:1035] = self.images["开始任务"]
        for x, y, w, h in f.SLOT_ROIS:
            self.image[y + 65:y + 140, x + 25:x + 100] = self.face

    def reader(self, calibration=None):
        return f.FormationIdentityReader([self.root], calibration or self.calibration, self.catalog)

    def test_six_slots_support_empty_and_repeated_identity(self):
        x, y, w, h = f.SLOT_ROIS[2]
        self.image[y:y + 20, x:x + 35] = self.images["助战标记"]
        x, y, w, h = f.SLOT_ROIS[5]
        self.image[y:y+h, x:x+w] = 70
        result = self.reader().read_frame(self.image, full_audit=True)
        self.assertEqual([s.servant_id for s in result], ["1000"] * 5 + [None])
        self.assertTrue(result[2].is_support)
        self.assertFalse(result[0].is_support)
        self.assertEqual(result[5].status, "empty")
        snapshot = InitialFormation(result, 1, "session", 1, "now", 1)
        self.assertNotEqual(snapshot.member_key(1), snapshot.member_key(3))
        with self.assertRaises(FrozenInstanceError):
            snapshot.slots = ()

    def test_variants_are_grouped_before_margin(self):
        reader = self.reader()
        ranked = reader._rank(self.image, f.SLOT_ROIS[0], reader.templates)
        self.assertEqual(len(ranked), 2)
        self.assertGreater(ranked[0][0] - ranked[1][0], 0.25)
        self.assertEqual(reader.coverage["covered_ids"], 2)  # 空 images 的命名回填
        self.assertEqual(len(reader.templates["1000"]), 2)

    def test_unverified_profile_never_accepts_candidate(self):
        reader = self.reader(f.FormationCalibration())
        slots = reader.read_frame(self.image, diagnostics=True)
        self.assertEqual(slots[0].best_candidate_id, "1000")
        self.assertIsNone(slots[0].servant_id)
        self.assertEqual(slots[0].reason, "calibration_required")

    def test_wrong_class_falls_back_and_final_frame_audits_all(self):
        reader = self.reader()
        with patch.object(reader, "_class", return_value=("caster", Confidence(.99))):
            slots = reader.read_frame(self.image)
            self.assertEqual(slots[0].servant_id, "1000")
            self.assertEqual(slots[0].search_scope, "all_fallback")
        with patch.object(reader, "_class", return_value=("saber", Confidence(.99))):
            slots = reader.read_frame(self.image, full_audit=True)
            self.assertEqual(slots[0].search_scope, "all_audit")

    def test_high_score_wrong_class_cannot_survive_global_audit(self):
        reader = self.reader()
        # 增加同职阶竞争者，使剪枝帧确有足够候选而非因单候选触发回退。
        reader.by_class = {"caster": ["2000", "3000"]}
        reader.templates = {"1000": (), "2000": (), "3000": ()}
        def ranked(image, roi, ids):
            return [(0.999, "1000", "correct"), (.96, "2000", "similar")] if "1000" in ids else [
                (.96, "2000", "similar"), (.4, "3000", "other")]
        # 提高全局分差仍不足：可疑场景必须未知。
        with patch.object(reader, "_class", return_value=("caster", Confidence(.99))), \
             patch.object(reader, "_rank", side_effect=ranked):
            first = reader.read_frame(self.image)
            last = reader.read_frame(self.image, full_audit=True)
        merged = f.merge_formation_frames((first, first, last))
        self.assertEqual(first[0].servant_id, "2000")
        self.assertIsNone(merged[0].servant_id)

    def test_low_margin_and_multi_frame_disagreement_are_unknown(self):
        reader = self.reader()
        with patch.object(reader, "_rank", return_value=[(.99, "1000", "a"), (.98, "2000", "b")]):
            self.assertEqual(reader.read_frame(self.image)[0].status, "unknown")
        frame = reader.read_frame(self.image, full_audit=True)
        changed = (replace(frame[0], servant_id="2000"), *frame[1:])
        merged = f.merge_formation_frames((frame, frame, changed))
        self.assertIsNone(merged[0].servant_id)
        self.assertEqual(merged[1].consistent_frames, 3)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            f.merge_formation_frames((frame, frame))

    def test_page_and_screenshot_failure_are_not_empty_slots(self):
        reader = self.reader()
        for image, reason in [(None, "screenshot_unavailable"),
                              (np.zeros_like(self.image), "not_on_formation_confirmation"),
                              (self.image[:, :800], "unsupported_formation_layout")]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                reader.read_frame(image)

    def test_resource_cache_refreshes_after_catalog_change(self):
        first = self.reader()
        data = json.loads(self.catalog.read_text())
        data["servants"][0]["name"] = "新名字"
        self.catalog.write_text(json.dumps(data), encoding="utf-8")
        self.assertNotEqual(first.records["1000"]["name"], self.reader().records["1000"]["name"])

    def test_invalid_calibration_and_contract_are_rejected(self):
        for config in [{"verified": True}, {"identity_margin": -1}, {"class_roi": [0, 0, 200, 20]},
                       {"template_family": "强化从者"}, {"verified": "true"}]:
            with self.subTest(config=config), self.assertRaises((ValueError, TypeError)):
                f.FormationCalibration.parse(config)
        with self.assertRaises(ValueError):
            FormationSlot(1, "unknown", servant_id="1000")


if __name__ == "__main__":
    unittest.main()
