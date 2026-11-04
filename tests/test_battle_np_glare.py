"""Real JP NP glow phases request bounded observation, never a lower match gate."""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.core.enums import Scene
from battle.core.models import BattleState, Confidence, NpCard
from battle.perception import perception
from battle.perception.composed_card import ComposedCardRecognizer
from battle.runtime.runtime import AutoBattleRuntime

ROSTER = ("205500", "605300", "504500")
GLARE = ("np[2].presence:glare", "np[3].presence:glare")


def fixture(name):
    strip = cv2.imdecode(np.fromfile(ROOT / "tests/fixtures/battle_np" / name, dtype=np.uint8), cv2.IMREAD_COLOR)
    image = np.zeros((720, 1280, 3), np.uint8)
    image[70:370, 265:1016] = strip
    return image


def state(*, unknown=(), nps=()):
    return BattleState(Scene.COMMAND_SELECTION, Confidence(1), (), tuple(nps), (), unknown_fields=unknown)


def context(*, blocked=()):
    def recognize(node, _):
        hit = node == "战斗_选卡场景" or node in {f"战斗_宝具卡{s}_封印" for s in blocked}
        return NS(hit=hit, best_result=NS(score=.99, text="") if hit else None)
    return NS(run_recognition=Mock(side_effect=recognize),
              run_task=Mock(return_value=NS(status=NS(succeeded=True))),
              tasker=NS(stopping=False))


def runtime_for(frames, ctx=None):
    runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
    runtime.ctx = ctx or context()
    runtime.decider = NS(plan=None)
    runtime.controller = NS(post_screencap=Mock(side_effect=[
        NS(wait=lambda frame=frame: NS(get=lambda: frame)) for frame in frames]))
    runtime.roster_servant_ids = ROSTER + (None,) * 3
    runtime.candidate_servant_ids = runtime.roster_servant_ids
    runtime.servant_recognizer = object()
    return runtime


class NpGlareRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glare = fixture("jp_hellish_glare_20261031.png")
        cls.clear = fixture("jp_hellish_clear_20261031.png")
        cls.recognizer = ComposedCardRecognizer()

    def test_real_peak_glare_is_uncertain_without_accepting_subthreshold_np(self):
        detected = self.recognizer.detect_np(self.glare, ROSTER)
        self.assertEqual(self.recognizer.min_np_score, .80)
        self.assertEqual([item.visual_present for item in detected], [False, False, False])
        self.assertEqual(detected[0].reason, "card_not_present")
        for item in detected[1:]:
            self.assertEqual(item.reason, "np_glare_uncertain")
            self.assertFalse(item.authoritative)
            self.assertIsNone(item.owner_slot)
            self.assertGreaterEqual(item.score, .60)
            self.assertLess(item.score, .80)
            self.assertGreaterEqual(item.glare_fraction, .10)

    def test_real_clear_phase_identifies_all_three_np_and_owner_ids(self):
        detected = self.recognizer.detect_np(self.clear, ROSTER)
        self.assertEqual([(item.owner_slot, item.servant_id) for item in detected],
                         list(enumerate(ROSTER, 1)))
        self.assertTrue(all(item.visual_present and item.score >= .80 for item in detected))

    def test_flat_overexposed_background_without_card_texture_does_not_request_retry(self):
        detected = self.recognizer.detect_np(np.full_like(self.clear, 255), ROSTER)
        self.assertTrue(all(not item.visual_present for item in detected))
        self.assertFalse(any(item.reason == "np_glare_uncertain" for item in detected))

    def test_perception_exposes_glare_as_unknown_and_preserves_real_seal_overlay(self):
        with patch.object(ComposedCardRecognizer, "default", return_value=self.recognizer):
            glared = perception.build(context(), self.glare, frontline_servant_ids=ROSTER)
            sealed = perception.build(context(blocked=(2,)), self.clear, frontline_servant_ids=ROSTER)
        self.assertTrue(set(GLARE) <= set(glared.unknown_fields))
        self.assertEqual(glared.np_cards, ())
        self.assertEqual([card.servant_slot for card in sealed.np_cards], [1, 2, 3])
        self.assertFalse(sealed.np_cards[1].available)
        self.assertEqual(sealed.np_cards[1].blocked_reason, "sealed_or_cannot_act")

    def test_real_glare_then_clear_uses_newest_complete_frame_without_lowering_threshold(self):
        runtime = runtime_for([self.glare, self.clear])
        with patch.object(ComposedCardRecognizer, "default", return_value=self.recognizer):
            result = runtime._observe()
        self.assertEqual([card.servant_slot for card in result.np_cards], [1, 2, 3])
        self.assertTrue(all(card.available and card.confidence.value >= .80 for card in result.np_cards))
        self.assertEqual(runtime.controller.post_screencap.call_count, 2)
        runtime.ctx.run_task.assert_called_once_with("战斗_宝具闪光重拍等待")


class NpGlareRetryTests(unittest.TestCase):
    def test_persistent_glare_stops_after_three_frames_without_guessing_np_availability(self):
        runtime = runtime_for([object(), object(), object()])
        with patch("battle.runtime.runtime.perception.build", return_value=state(unknown=GLARE)):
            with self.assertRaisesRegex(RuntimeError, "np_glare_unresolved"):
                runtime._observe()
        self.assertEqual(runtime.controller.post_screencap.call_count, 3)
        self.assertEqual(runtime.ctx.run_task.call_count, 2)

    def test_low_score_negative_has_no_retry(self):
        runtime = runtime_for([object()])
        negative = state()
        with patch("battle.runtime.runtime.perception.build", return_value=negative):
            self.assertIs(runtime._observe(), negative)
        runtime.ctx.run_task.assert_not_called()

    def test_latest_seal_and_identity_replace_earlier_available_cards(self):
        first = state(unknown=(GLARE[1],), nps=(NpCard(2, Confidence(.95), owner_servant_id="605300"),))
        latest = state(nps=(NpCard(2, Confidence(.96), available=False,
                                  blocked_reason="sealed_or_cannot_act", owner_servant_id="504500"),))
        runtime = runtime_for([object(), object()])
        with patch("battle.runtime.runtime.perception.build", side_effect=[first, latest]):
            result = runtime._observe()
        self.assertIs(result, latest)
        self.assertFalse(result.np_cards[0].available)
        self.assertEqual(result.np_cards[0].owner_servant_id, "504500")

    def test_observation_does_not_cache_available_np_across_turns(self):
        first = state(nps=(NpCard(2, Confidence(.95)),))
        second = state()
        runtime = runtime_for([object(), object()])
        with patch("battle.runtime.runtime.perception.build", side_effect=[first, second]):
            self.assertIs(runtime._observe(), first)
            self.assertIs(runtime._observe(), second)
        runtime.ctx.run_task.assert_not_called()

    def test_failed_wait_and_user_stop_do_not_capture_or_act_again(self):
        for stopping in (False, True):
            with self.subTest(stopping=stopping):
                runtime = runtime_for([object()])
                runtime.ctx.tasker.stopping = stopping
                runtime.ctx.run_task.return_value.status.succeeded = False
                with patch("battle.runtime.runtime.perception.build", return_value=state(unknown=GLARE)):
                    with self.assertRaisesRegex(RuntimeError, "np_glare_retry_(stopped|wait_failed)"):
                        runtime._observe()
                self.assertEqual(runtime.controller.post_screencap.call_count, 1)


if __name__ == "__main__":
    unittest.main()
