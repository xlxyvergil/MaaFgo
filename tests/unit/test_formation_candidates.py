"""固定槽位粗筛与原图精查的离线回归；合成图不代表实机准确率。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formation_test_support import formation as f
from battle.core.models import Confidence
from battle.core.models import FormationSlot
from battle.perception.formation_candidates import CandidatePool, FormationCandidateIndex
import test_formation_identity as identity_fixtures


class CandidateIndexTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(543)
        self.templates = {}
        for index in range(70):
            # 保留真实资源的宽高，但使用平滑随机图，避免依赖任何正式模板。
            image = cv2.resize(rng.integers(0, 255, (32, 32, 3), np.uint8), (185, 184))
            self.templates[str(index)] = ((str(index), image),)
        self.image = np.full((720, 1280, 3), (40, 60, 100), np.uint8)
        for index, (x, y, _, _) in enumerate(f.SLOT_ROIS):
            self.image[y + 30:y + 214, x + 1:x + 186] = self.templates[str(index)][0][1]

    def test_full_and_cropped_features_keep_six_distinct_identities(self):
        index = FormationCandidateIndex(self.templates, (.08, .15, .92, .82))
        pools = index.shortlists(self.image, f.SLOT_ROIS)
        for slot, pool in enumerate(pools):
            self.assertIn(str(slot), pool.ids)
            self.assertFalse(pool.fallback)
            self.assertGreaterEqual(len(pool.ids), 2)
            self.assertLessEqual(len(pool.ids), 48)

    def test_variants_are_grouped_by_identity_before_cutoff(self):
        # 几十份同 ID 图片不能挤掉第二个真实身份候选。
        self.templates["0"] *= 40
        pool = FormationCandidateIndex(self.templates).shortlists(self.image, f.SLOT_ROIS)[0]
        self.assertEqual(pool.ids.count("0"), 1)
        self.assertGreater(len(pool.ids), 1)

    def test_uniform_image_or_missing_templates_requests_fallback(self):
        for templates in (self.templates, {"0": ()}):
            pools = FormationCandidateIndex(templates).shortlists(np.zeros_like(self.image), f.SLOT_ROIS)
            self.assertTrue(all(pool.fallback for pool in pools))

    def test_crowded_boundary_requests_fallback(self):
        repeated = {sid: self.templates["0"] for sid in self.templates}
        pools = FormationCandidateIndex(repeated).shortlists(self.image, f.SLOT_ROIS)
        self.assertTrue(pools[0].fallback)

    def test_crop_features_remain_search_proposals_not_published_scores(self):
        index = FormationCandidateIndex(self.templates, (.08, .15, .92, .82))
        pools = index.shortlists(self.image, f.SLOT_ROIS)
        self.assertTrue(all(isinstance(pool, CandidatePool) for pool in pools))
        self.assertFalse(any(hasattr(pool, "servant_id") for pool in pools))


class FastReaderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = identity_fixtures.IdentityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.reader = self.fixture.reader()
        p = patch.object(f.mfaalog, "info")
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(f, "IDENTITY_COARSE_MIN_IDENTITIES", 1)
        p.start()
        self.addCleanup(p.stop)

    def fast_pool(self):
        return patch.object(f.FormationCandidateIndex, "shortlists",
                            return_value=(CandidatePool(("1000", "2000"), False),) * 6)

    def test_coarse_proposals_still_use_original_template_scores(self):
        with self.fast_pool(), patch.object(self.reader, "_class", return_value=("caster", Confidence(.99))):
            result = self.reader.read_frame(self.fixture.image)
        self.assertTrue(all(slot.servant_id == "1000" for slot in result))
        self.assertTrue(all(slot.search_scope == "coarse" for slot in result))
        self.assertTrue(all(slot.best_score > .99 for slot in result))
        self.assertTrue(all(slot.class_id is None for slot in result))

    def test_coarse_does_not_bypass_original_margin_or_score_requirements(self):
        for scores in ((.99, .98), (.94, .20)):
            with self.fast_pool(), patch.object(self.reader, "_rank", return_value=[
                (scores[0], "1000", "a"), (scores[1], "2000", "b")]):
                self.assertTrue(all(slot.status == "unknown" for slot in self.reader.read_frame(self.fixture.image)))

    def test_explicit_full_audit_bypasses_coarse_index(self):
        with patch.object(f.FormationCandidateIndex, "shortlists", side_effect=AssertionError("must bypass")):
            result = self.reader.read_frame(self.fixture.image, full_audit=True)
        self.assertTrue(all(slot.search_scope == "all_audit" for slot in result))

    def test_weak_coarse_proposals_use_original_full_matching(self):
        with patch.object(f.FormationCandidateIndex, "shortlists",
                          return_value=(CandidatePool((), True),) * 6):
            result = self.reader.read_frame(self.fixture.image)
        self.assertTrue(all(slot.servant_id == "1000" for slot in result))
        self.assertTrue(all(slot.search_scope == "all_fallback" for slot in result))

    def test_coarse_error_uses_original_matcher_instead_of_losing_snapshot(self):
        with patch.object(f.FormationCandidateIndex, "shortlists", side_effect=ValueError("feature unavailable")), \
             patch.object(f.mfaalog, "warning"):
            result = self.reader.read_frame(self.fixture.image)
        self.assertTrue(all(slot.servant_id == "1000" for slot in result))

    def test_fast_empty_slot_and_support_keep_original_semantics(self):
        x, y, width, height = f.SLOT_ROIS[5]
        self.fixture.image[y:y + height, x:x + width] = 70
        x, y, _, _ = f.SLOT_ROIS[2]
        self.fixture.image[y:y + 20, x:x + 35] = self.fixture.images["助战标记"]
        with self.fast_pool():
            result = self.reader.read_frame(self.fixture.image)
        self.assertTrue(result[2].is_support)
        self.assertEqual(result[5].status, "empty")
        self.assertEqual(self.reader.last_match_stats["slots"][5]["candidates"], 0)

    def test_same_array_can_change_identity_between_reads_without_stale_crops(self):
        with self.fast_pool():
            first = self.reader.read_frame(self.fixture.image)
            for x, y, _, _ in f.SLOT_ROIS:
                self.fixture.image[y + 65:y + 140, x + 25:x + 100] = self.fixture.other
            second = self.reader.read_frame(self.fixture.image)
        self.assertTrue(all(slot.servant_id == "1000" for slot in first))
        self.assertTrue(all(slot.servant_id == "2000" for slot in second))
        self.assertEqual(self.reader._roi_crop_cache, {})
        self.assertIsNone(self.reader._frame_match_cache)

    def test_cache_is_cleared_after_page_rejection(self):
        with self.assertRaisesRegex(ValueError, "not_on_formation"):
            self.reader.read_frame(np.zeros_like(self.fixture.image))
        self.assertEqual(self.reader._roi_crop_cache, {})
        self.assertIsNone(self.reader._frame_match_cache)

    def test_full_fallback_reuses_class_scores_only_within_current_frame(self):
        with patch.object(self.reader, "_class", return_value=("caster", Confidence(.99))), \
             patch.object(f.FormationCandidateIndex, "shortlists", return_value=(CandidatePool((), True),) * 6), \
             patch.object(self.reader, "_match_servant", wraps=self.reader._match_servant) as match:
            self.reader.read_frame(self.fixture.image)
        # 每槽 caster 先查一次；回退全库时不能重复精查相同的 caster。
        self.assertEqual(match.call_count, 12)

    def test_resized_frame_is_normalized_only_for_coarse_search(self):
        image = cv2.resize(self.fixture.image, (1920, 1080))
        with self.fast_pool() as coarse:
            self.reader.read_frame(image)
        self.assertEqual(coarse.call_args.args[0].shape, (720, 1280, 3))
        self.assertEqual(self.reader.sx, 1.5)

    def test_unverified_configuration_does_not_publish_even_fast_candidate(self):
        self.reader = self.fixture.reader(f.FormationCalibration())
        with self.fast_pool():
            result = self.reader.read_frame(self.fixture.image, diagnostics=True)
        self.assertTrue(all(slot.servant_id is None for slot in result))
        self.assertTrue(all(slot.reason == "calibration_required" for slot in result))

    def test_partial_reuse_only_coarse_and_exact_matches_unknown_slot(self):
        known = tuple(FormationSlot(i, "identified", "1000", is_support=False)
                      if i != 6 else FormationSlot(i, "unknown", is_support=False)
                      for i in range(1, 7))
        with self.fast_pool() as coarse, patch.object(self.reader, "_rank", wraps=self.reader._rank) as rank:
            result = self.reader.read_frame(self.fixture.image, known_slots=known)
        self.assertEqual(coarse.call_args.args[1], [f.SLOT_ROIS[5]])
        self.assertEqual({tuple(call.args[1]) for call in rank.call_args_list}, {f.SLOT_ROIS[5]})
        for before, after in zip(known[:5], result[:5]):
            self.assertIs(before, after)
        self.assertEqual(result[5].servant_id, "1000")
        self.assertEqual([s["scope"] for s in self.reader.last_match_stats["slots"][:5]], ["verified_reuse"] * 5)

    def test_partial_reuse_does_not_bypass_page_guard_and_audit_remains_independent(self):
        known = tuple(FormationSlot(i, "identified", "2000", is_support=False) for i in range(1, 7))
        with self.assertRaisesRegex(ValueError, "not_on_formation"):
            self.reader.read_frame(np.zeros_like(self.fixture.image), known_slots=known)
        audited = self.reader.read_frame(self.fixture.image, known_slots=known, full_audit=True)
        self.assertTrue(all(slot.servant_id == "1000" for slot in audited))


if __name__ == "__main__":
    unittest.main()
