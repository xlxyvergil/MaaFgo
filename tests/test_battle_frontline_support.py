"""A support ribbon must not hide a real HUD identity or weaken its score gate."""
from pathlib import Path
import json
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.core.enums import Scene
from battle.core.models import BattleAction, ServantSkillAction
from battle.core.policy import StrategyProfile
from battle.core.validator import skip_unusable_servant_skills
from battle.perception import frontline, perception
from battle.runtime.runtime import AutoBattleRuntime

ROSTER = ("205500", "605300", "504500", "300500", None, None)
RIVALS = ("605000", "600200", "600900", "100800", "1001000", "1002000")
CONFIG = json.loads((ROOT / "assets/resource/base/pipeline/自动战斗_前排身份.json").read_text(encoding="utf-8"))["战斗_前排身份识别配置"]["attach"]


def image(name="jp_support_hud_20261031.png"):
    strip = cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/battle_frontline" / name, dtype=np.uint8), cv2.IMREAD_COLOR)
    output = np.zeros((720, 1280, 3), np.uint8)
    output[385:545, :954] = strip
    return output


class FrontlineSupportTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = frontline.FrontlineServantRecognizer(**CONFIG)
        self.image = image()

    def test_full_band_remains_primary_and_support_crop_recovers_target_at_original_threshold(self):
        result = self.recognizer.detect(self.image, ROSTER)
        self.assertEqual([item.servant_id for item in result], list(ROSTER[:3]))
        self.assertEqual([item.visible_top for item in result], [35, 45, 35])
        self.assertEqual(self.recognizer.min_score, .8)
        self.assertEqual(self.recognizer.min_margin, .05)
        self.assertGreater(result[1].confidence.value, .93)
        self.assertEqual(result[1].matched_template, "605300/status_servant_2.png")

    def test_similar_wrong_candidates_do_not_take_target_identity_or_pass_when_target_is_missing(self):
        result = self.recognizer.detect(self.image, (*ROSTER, *RIVALS))
        self.assertEqual([item.servant_id for item in result], list(ROSTER[:3]))
        without_target = frontline.FrontlineServantRecognizer(**CONFIG).detect(
            self.image, tuple(sid for sid in (*ROSTER, *RIVALS) if sid != "605300"))
        self.assertIsNone(without_target[1].servant_id)
        self.assertLess(without_target[1].confidence.value, .8)

    def test_np_animation_and_blank_rois_remain_unidentified(self):
        for frame in (image("jp_np_animation_no_hud_20261031.png"), np.zeros_like(self.image)):
            with self.subTest(empty=not np.any(frame)):
                result = self.recognizer.detect(frame, (*ROSTER, *RIVALS))
                self.assertTrue(all(item.servant_id is None for item in result))

    def test_crop_still_applies_original_margin_against_all_candidates(self):
        original = frontline._prepared_templates

        def identical(roots, sid, scales, sx, sy, visible_top=35):
            return original(roots, "605300", scales, sx, sy, visible_top)

        with patch.object(frontline, "_prepared_templates", side_effect=identical):
            result = self.recognizer.detect(self.image, ("1", "2"))
        self.assertIsNone(result[1].servant_id)
        self.assertEqual(result[1].margin, 0)

    def test_cached_cropped_identity_does_not_survive_real_portrait_replacement(self):
        self.assertEqual(self.recognizer.detect(self.image, ROSTER)[1].servant_id, "605300")
        changed = self.image.copy()
        changed[385:545, 318:636] = self.image[385:545, 636:954]
        result = self.recognizer.detect(changed, ROSTER)
        self.assertEqual(result[1].servant_id, "504500")

    def test_skill_state_collection_no_longer_skips_the_recognized_support(self):
        context = NS(run_recognition=Mock(return_value=NS(hit=False)))
        servants = perception._detect_servants(context, self.image, ROSTER, recognizer=self.recognizer)
        self.assertEqual([servant.slot for servant in servants], [1, 2, 3])
        self.assertEqual(servants[1].servant_id, "605300")
        self.assertEqual([skill.available for skill in servants[1].skills], [True, True, True])

    def test_runtime_keeps_support_frontline_and_required_skills_are_not_state_missing(self):
        runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
        runtime.roster_servant_ids = ROSTER
        runtime.candidate_servant_ids = ROSTER
        runtime.servant_recognizer = self.recognizer
        runtime.decider = NS(plan=None)
        runtime.ctx = NS(run_recognition=Mock(side_effect=lambda node, _: NS(
            hit=node == "战斗_主界面", best_result=NS(score=.99))))
        runtime.controller = NS(post_screencap=Mock(return_value=NS(
            wait=lambda: NS(get=lambda: self.image))))
        observed = runtime._observe()
        self.assertEqual(observed.scene, Scene.MAIN_BATTLE)
        self.assertEqual(runtime.roster_servant_ids[1], "605300")
        planned = BattleAction(None, (), servant_skills=tuple(ServantSkillAction(2, i) for i in (1, 2, 3)))
        filtered, skipped = skip_unusable_servant_skills(planned, observed, StrategyProfile())
        self.assertEqual(skipped, ())
        self.assertEqual(filtered.servant_skills, planned.servant_skills)


if __name__ == "__main__":
    unittest.main()
