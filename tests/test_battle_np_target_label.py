"""Replay NP evidence from the JP TARGET false positive without device input."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from battle.core.decider import RuleDecider
from battle.core.enums import PrimitiveKind
from battle.core.models import Confidence
from battle.perception import config, perception
from battle.perception.card_owner import CardOwnerDetection


def result(hit=False, *, text='', score=0):
    return NS(hit=hit, best_result=NS(text=text, score=score) if hit else None)


def owner(slot, score=.95, *, identified=True):
    return CardOwnerDetection(slot, slot if identified else None,
                              f'servant{slot}' if identified else None,
                              Confidence(score, 'command_template'), score,
                              .10, 'command.png', 'identified' if identified else 'owner_score_below_threshold')


def context(slot=1, *, text='ET', score=.999732, frame=True, blocked=False):
    records = {
        config.NP_VISUAL_NODE.format(servant_slot=slot): result(True, text=text, score=score),
        config.NP_FRAME_NODE.format(servant_slot=slot): result(frame, score=.883210),
        config.NP_BLOCKED_NODE.format(servant_slot=slot): result(blocked, score=1),
    }
    return NS(run_recognition=Mock(side_effect=lambda node, _img: records.get(node, result())))


class NpTargetLabelTests(unittest.TestCase):
    def test_native_log_et_and_left_frame_false_positive_is_rejected(self):
        # 2026-10-28 22:04:27: OCR ET=.999732 and left frame=.883210>.88.
        ctx = context()
        self.assertIsNone(perception._detect_np(ctx, object(), 1, owner(1, .51, identified=False)))
        self.assertNotIn(config.NP_FRAME_NODE.format(servant_slot=1),
                         [call.args[0] for call in ctx.run_recognition.call_args_list])

    def test_clipped_target_suffixes_cannot_become_cards_with_frame_evidence(self):
        for text in ('TARGET', 'ARGET', 'RGET', 'GET', 'ET', ' t a r g e t ', 'E.T.',
                     '[GET]', '目标', '目 標', ' -- '):
            with self.subTest(text=text):
                self.assertIsNone(perception._detect_np(context(text=text), object(), 1))

    def test_any_title_and_frame_are_insufficient_without_known_card_identity(self):
        for title in ('CE', 'ぜつぎのはっきどとう', '烈士徇名・不惜身命', 'Gate of Babylon', 'GET AWAY'):
            with self.subTest(title=title):
                ctx = context(text=title, score=.99)
                self.assertIsNone(perception._detect_np(ctx, object(), 1))
                names = [call.args[0] for call in ctx.run_recognition.call_args_list]
                self.assertNotIn(config.NP_VISUAL_NODE.format(servant_slot=1), names)
                self.assertNotIn(config.NP_FRAME_NODE.format(servant_slot=1), names)

    def test_unknown_portrait_does_not_fall_back_to_title_or_frame(self):
        ctx = context(text='烈士徇名', frame=False)
        self.assertIsNone(perception._detect_np(ctx, object(), 1))
        self.assertNotIn(config.NP_FRAME_NODE.format(servant_slot=1),
                         [call.args[0] for call in ctx.run_recognition.call_args_list])

    def test_known_real_np2_and_np3_keep_their_logged_portrait_positive_path(self):
        for slot, score in ((2, .958), (3, .966)):
            with self.subTest(slot=slot):
                ctx = context(slot, text='C', score=.126628, frame=False)
                card = perception._detect_np(ctx, object(), slot, owner(slot, score))
                self.assertTrue(card.available)
                self.assertEqual(card.owner_servant_id, f'servant{slot}')
                self.assertAlmostEqual(card.confidence.value, score)
                self.assertNotIn(config.NP_FRAME_NODE.format(servant_slot=slot),
                                 [call.args[0] for call in ctx.run_recognition.call_args_list])

    def test_restricted_card_stays_unavailable_even_with_clipped_title(self):
        card = perception._detect_np(context(blocked=True), object(), 1)
        self.assertFalse(card.available)
        self.assertEqual(card.blocked_reason, 'sealed_or_cannot_act')

    def test_low_confidence_title_or_wrong_owner_does_not_bypass_existing_gates(self):
        self.assertIsNone(perception._detect_np(context(text='烈士徇名', score=.79), object(), 1))
        self.assertIsNone(perception._detect_np(context(frame=False), object(), 1, owner(2)))

    def test_full_perception_replay_keeps_two_real_nps_then_fills_third_with_face_card(self):
        records = {config.SCENE_NODES['command_selection']: result(True, score=.99)}
        for slot in (1, 2, 3):
            records[config.NP_VISUAL_NODE.format(servant_slot=slot)] = result(
                True, text={1: 'ET', 2: 'ぜつぎのはっきどとう', 3: '烈士徇名'}[slot], score=.99)
            records[config.NP_FRAME_NODE.format(servant_slot=slot)] = result(True, score=.883210)
        for slot in range(1, 6):
            records[config.CARD_NODE.format(ui_slot=slot)] = result(True, text='Arts', score=.99)
        ctx = NS(run_recognition=Mock(side_effect=lambda node, _img: records.get(node, result())))
        recognizer = NS(detect=Mock(return_value=tuple(owner(i, .1, identified=False) for i in range(1, 6))),
                        detect_np=Mock(return_value=(owner(1, .51, identified=False), owner(2, .958), owner(3, .966))))
        state = perception.build(ctx, object(), frontline_servant_ids=('one', 'two', 'three'),
                                 owner_recognizer=recognizer)
        self.assertEqual([card.servant_slot for card in state.np_cards], [2, 3])
        picks = RuleDecider().decide(state).picks
        self.assertEqual([(pick.kind, pick.slot) for pick in picks[:2]],
                         [(PrimitiveKind.SELECT_NP, 2), (PrimitiveKind.SELECT_NP, 3)])
        self.assertEqual(len(picks), 3)
        self.assertEqual(picks[2].kind, PrimitiveKind.SELECT_CARD)


if __name__ == '__main__':
    unittest.main()
