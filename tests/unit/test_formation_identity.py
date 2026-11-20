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
from formation_test_support import ROOT, formation as f
from battle.core.models import Confidence, FormationSlot, InitialFormation
from chaldea.servant_aliases import build_servant_lookup
from chaldea.support_criteria import build_support_criteria


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

    def restricted_formation(self, support_slots):
        image = self.image.copy()
        marker_path = ROOT / "assets/resource/base/image/battle/编队锁定空位.png"
        lock = cv2.imdecode(np.fromfile(marker_path, np.uint8), cv2.IMREAD_COLOR)
        image_write(self.root / "battle/编队锁定空位.png", lock)
        marker_height, marker_width = lock.shape[:2]
        for slot in (3, 6):
            x, y, width, height = f.SLOT_ROIS[slot - 1]
            image[y:y + height, x:x + width] = (45, 30, 15)
            image[y + 17:y + 17 + marker_height, x + 9:x + 9 + marker_width] = lock
        if 5 in support_slots:
            x, y, width, height = f.SLOT_ROIS[4]
            image[y:y + height, x:x + width] = (40, 60, 100)
        for slot in support_slots:
            x, y, _, _ = f.SLOT_ROIS[slot - 1]
            image[y:y + 20, x:x + 35] = self.images["助战标记"]
        return image

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

    def test_restricted_formation_reads_each_support_and_locked_slot(self):
        for support_slots in ((1, 4), (1, 4, 5)):
            with self.subTest(support_slots=support_slots):
                image = self.restricted_formation(support_slots)
                slots = self.reader().read_frame(image)
                self.assertEqual(tuple(s.slot for s in slots if s.is_support), support_slots)
                for slot in (3, 6):
                    empty = slots[slot - 1]
                    self.assertEqual((empty.status, empty.is_support, empty.reason),
                                     ("empty", False, "restricted_slot_locked"))
                self.assertEqual([slots[i].servant_id for i in (0, 1, 3)], ["1000"] * 3)
                self.assertEqual(slots[4].status, "unknown" if 5 in support_slots else "identified")

    def test_multiple_supports_recover_occluded_portraits_only_in_support_slots(self):
        for support_slots, expected_identity in (((1,), None), ((1, 4), "1000")):
            with self.subTest(support_slots=support_slots):
                image = self.restricted_formation(support_slots)
                # Simulate a restriction notice covering the lower portrait.
                for x, y, _, _ in f.SLOT_ROIS[:2]:
                    image[y + 111:y + 140, x + 25:x + 100] = (14, 21, 33)
                slots = self.reader().read_frame(image)
                self.assertTrue(slots[0].is_support)
                self.assertEqual(slots[0].servant_id, expected_identity)
                self.assertFalse(slots[1].is_support)
                self.assertIsNone(slots[1].servant_id)

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

    def test_missing_narrow_identity_uses_existing_face_fallback(self):
        image_write(self.root / "servant_face/f_30000.png", self.other)
        data = json.loads(self.catalog.read_text())
        data["servants"].append({"id": "3000", "name": "备用", "class": "rider", "images": []})
        self.catalog.write_text(json.dumps(data), encoding="utf-8")
        reader = self.reader()
        self.assertEqual(reader.coverage["fallback_ids"], ["3000"])
        self.assertEqual(reader.templates["3000"][0][0], "servant_face/f_30000.png")

    def test_wrong_class_falls_back_and_final_frame_audits_all(self):
        reader = self.reader()
        with patch.object(reader, "_class", return_value=("caster", Confidence(.99))):
            slots = reader.read_frame(self.image)
            self.assertEqual(slots[0].servant_id, "1000")
            self.assertEqual(slots[0].search_scope, "all_fallback")
            self.assertIsNone(slots[0].class_id)
            self.assertEqual(slots[0].class_confidence.source, "class_identity_conflict")
        with patch.object(reader, "_class", return_value=("saber", Confidence(.99))):
            slots = reader.read_frame(self.image, full_audit=True)
            self.assertEqual(slots[0].search_scope, "all_audit")

    def test_class_template_uses_slot_relative_roi_and_margin(self):
        icons = {}
        for name in ("saber", "caster"):
            icons[name] = self.rng.integers(0, 255, (12, 12, 3), dtype=np.uint8)
            image_write(self.root / f"classes/{name}.png", icons[name])
        calibration = f.FormationCalibration.parse({
            "verified": True, "calibration_id": "synthetic-test-only",
            "identity_threshold": .95, "identity_margin": .25,
            "class_threshold": .95, "class_margin": .25,
            "class_roi": [0, 0, 20, 20],
            "class_templates": {name: [f"classes/{name}.png"] for name in icons},
        })
        for x, y, _, _ in f.SLOT_ROIS:
            self.image[y+2:y+14, x+2:x+14] = icons["saber"]
        reader = self.reader(calibration)
        result = reader.read_frame(self.image, full_audit=True)
        self.assertTrue(all(s.class_id == "saber" for s in result))
        self.assertTrue(all(s.class_confidence.value > .99 for s in result))

    def test_high_score_wrong_class_cannot_survive_global_audit(self):
        reader = self.reader()
        # 增加同职阶竞争者，使剪枝帧确有足够候选而非因单候选触发回退。
        reader.by_class = {"caster": ["2000", "3000"]}
        reader.templates = {"1000": (), "2000": (), "3000": ()}
        def ranked(image, roi, ids):
            return [(0.999, "1000", "correct"), (.96, "2000", "similar")] if "1000" in ids else [
                (.96, "2000", "similar"), (.4, "3000", "other")]
        # 单帧全局复核：可疑场景必须未知。
        with patch.object(reader, "_class", return_value=("caster", Confidence(.99))), \
             patch.object(reader, "_rank", side_effect=ranked):
            last = reader.read_frame(self.image, full_audit=True)
        merged = f.merge_formation_frames((last,))
        self.assertIsNone(merged[0].servant_id)
        self.assertEqual(merged[0].search_scope, "all_audit")

    def test_low_margin_and_multi_frame_disagreement_are_unknown(self):
        reader = self.reader()
        with patch.object(reader, "_rank", return_value=[(.99, "1000", "a"), (.98, "2000", "b")]):
            self.assertEqual(reader.read_frame(self.image)[0].status, "unknown")
        frame = reader.read_frame(self.image, full_audit=True)
        merged = f.merge_formation_frames((frame,))
        self.assertEqual(merged[1].consistent_frames, 1)
        # 超出当前帧数上限（单帧）的输入必须拒绝。
        changed = (replace(frame[0], servant_id="2000"), *frame[1:])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            f.merge_formation_frames((frame, changed))

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

    def test_servant_lookup_reuses_parse_and_refreshes_without_shared_mutations(self):
        catalog = self.root / "servant_list.json"
        catalog.write_bytes(self.catalog.read_bytes())
        f._cached_servant_lookup.cache_clear()
        action = f.AutoFormationFromChaldea()
        with patch.object(f, "_CUSTOM_DIR", str(self.root)):
            first = action._get_servant_info(1000)
            first["images"].clear()
            self.assertTrue(action._get_servant_info(1000)["images"])
            self.assertEqual(f._cached_servant_lookup.cache_info().misses, 1)
            data = json.loads(catalog.read_text("utf-8"))
            data["servants"][0]["name"] = "新目录条目"
            catalog.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(action._get_servant_info(1000)["name"], "新目录条目")
            self.assertEqual(f._cached_servant_lookup.cache_info().misses, 2)

    def test_alternate_form_id_is_one_canonical_servant(self):
        data = json.loads(self.catalog.read_text())
        canonical = data["servants"][0]
        canonical["aliases"] = ["1100"]
        canonical["images"].append("f_11000.png")
        data["servants"].append({
            "id": "1100", "name": "旧错误记录", "class": "assassin",
            "images": ["f_11000.png"],
        })
        self.catalog.write_text(json.dumps(data), encoding="utf-8")
        image_write(self.root / "NarrowFigures/f_11000.png", self.other)

        lookup = build_servant_lookup(data["servants"])
        self.assertIs(lookup["1100"], canonical)
        reader = self.reader()
        self.assertNotIn("1100", reader.records)
        self.assertTrue(any(name.endswith("f_11000.png")
                            for name, _image in reader.templates["1000"]))

    def test_prelati_chaldea_id_uses_both_form_images(self):
        action = f.AutoFormationFromChaldea()
        servant = action._get_servant_info(505700)
        self.assertEqual(servant["id"], "505600")
        self.assertIn("f_5056000.png", servant["images"])
        self.assertIn("f_5057000.png", servant["images"])

        share_data = {"team": {
            "onFieldSvts": [{"svtId": 505700, "supportType": "none"}],
            "backupSvts": [],
        }}
        expected = action._build_expected(share_data)
        self.assertEqual(expected[0]["svt_id"], 505600)

        servant_map = build_servant_lookup([servant])
        share_data["team"]["onFieldSvts"][0]["supportType"] = "friend"
        criteria = build_support_criteria(share_data, servant_map)
        self.assertEqual(criteria["servant_id"], "505600")
        self.assertEqual(criteria["class_name"], "魔术师")
        self.assertIn("f_5057000.png", criteria["images"])

    def test_invalid_calibration_and_contract_are_rejected(self):
        for config in [{"verified": True}, {"identity_margin": -1}, {"class_roi": [0, 0, 200, 20]},
                       {"template_family": "强化从者"}, {"verified": "true"}]:
            with self.subTest(config=config), self.assertRaises((ValueError, TypeError)):
                f.FormationCalibration.parse(config)
        with self.assertRaises(ValueError):
            FormationSlot(1, "unknown", servant_id="1000")


class SoloSortieIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image_path = ROOT / "tests/fixtures/main_story/cn_solo_sortie_confirm.png"
        cls.image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
        resource_root = ROOT / "assets/resource"
        pipeline = json.loads((resource_root / "base/pipeline/自动编队.json").read_text("utf8"))
        calibration = f.FormationCalibration.parse(pipeline["编队身份-采集"]["attach"]["calibration"])
        cls.reader = f.FormationIdentityReader(
            [resource_root / layer / "image" for layer in ("cn", "base")], calibration,
        )

    def test_fixed_support_portrait_is_identified_in_cn_solo_sortie(self):
        support, *locked = self.reader.read_frame(self.image)
        self.assertEqual((support.status, support.is_support, support.servant_id),
                         ("identified", True, "2300300"))
        self.assertEqual(support.search_scope, "solo_support_crop")
        self.assertEqual([(slot.status, slot.reason) for slot in locked],
                         [("empty", "restricted_slot_locked")] * 5)

    def test_ordinary_empty_slot_does_not_count_as_locked(self):
        image = self.image.copy()
        x, y, width, height = f.SLOT_ROIS[-1]
        image[y:y + height, x:x + width] = 70
        slots = self.reader.read_frame(image)
        self.assertTrue(slots[0].is_support)
        self.assertIsNone(slots[0].servant_id)
        self.assertEqual(slots[-1].reason, "existing_empty_slot_heuristic")


if __name__ == "__main__":
    unittest.main()
