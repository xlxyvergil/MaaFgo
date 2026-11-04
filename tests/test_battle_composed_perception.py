"""The composed-card path controls identity/colour and can prove NP absence."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from battle.core.enums import CardColor
from battle.core.models import Confidence
from battle.perception import config, perception


def result(hit=False, text=''):
    return NS(hit=hit, best_result=NS(text=text, score=.99) if hit else None)


def detection(slot, *, present=True, authoritative=True, color=CardColor.ARTS):
    return NS(ui_slot=slot, owner_slot=2, servant_id='105900',
              visual_present=present, authoritative=authoritative, color=color,
              confidence=Confidence(.96, 'composed_card'), best_score=.96,
              margin=.2, matched_template='composed/105900/arts', reason='identified')


def harness(*, faces=None, nps=None, blocked=()):
    records = {config.SCENE_NODES['command_selection']: result(True)}
    for slot in range(1, 6):
        records[config.CARD_NODE.format(ui_slot=slot)] = result(True, 'uick')
    for slot in (1, 2, 3):
        records[config.NP_VISUAL_NODE.format(servant_slot=slot)] = result(True, 'ET')
        records[config.NP_FRAME_NODE.format(servant_slot=slot)] = result(True)
        records[config.NP_BLOCKED_NODE.format(servant_slot=slot)] = result(slot in blocked)
    ctx = NS(run_recognition=Mock(side_effect=lambda node, _: records.get(node, result())))
    recognizer = NS(
        detect=Mock(return_value=tuple(faces if faces is not None else
                                      [detection(i, color=CardColor.QUICK) for i in range(1, 6)])),
        detect_np=Mock(return_value=tuple(nps if nps is not None else
                                         [detection(i, present=False) for i in (1, 2, 3)])))
    state = perception.build(ctx, object(), frontline_servant_ids=('504500', '105900', '505400'),
                             composed_recognizer=recognizer)
    return state, ctx


class ComposedPerceptionTests(unittest.TestCase):
    def test_complete_composed_match_replaces_clipped_colour_ocr_and_portrait(self):
        state, ctx = harness()
        self.assertEqual([c.color for c in state.cards], [CardColor.QUICK] * 5)
        self.assertEqual([c.owner_slot for c in state.cards], [2] * 5)
        self.assertTrue(all(c.confidence.source == 'composed_card' for c in state.cards))
        self.assertFalse(any(call.args[0] == config.CARD_NODE.format(ui_slot=1)
                             for call in ctx.run_recognition.call_args_list))

    def test_authoritative_np_absence_cannot_be_overridden_by_target_or_border(self):
        state, ctx = harness()
        self.assertEqual(state.np_cards, ())
        for call in ctx.run_recognition.call_args_list:
            self.assertNotIn(call.args[0], [config.NP_VISUAL_NODE.format(servant_slot=s) for s in (1, 2, 3)])

    def test_real_composed_np_keeps_restriction_overlay(self):
        state, _ = harness(nps=[detection(1, present=False), detection(2), detection(3)], blocked=(2,))
        self.assertEqual([c.servant_slot for c in state.np_cards], [2, 3])
        self.assertFalse(state.np_cards[0].available)
        self.assertEqual(state.np_cards[0].blocked_reason, 'sealed_or_cannot_act')
        self.assertTrue(state.np_cards[1].available)

    def test_positive_card_needs_no_ocr_even_if_other_regional_assets_are_missing(self):
        state, ctx = harness(nps=[detection(1, present=False), detection(2, authoritative=False),
                                 detection(3, present=False)])
        self.assertEqual([c.servant_slot for c in state.np_cards], [2])
        self.assertTrue(state.np_cards[0].available)
        self.assertFalse(any(call.args[0] == config.NP_VISUAL_NODE.format(servant_slot=2)
                             for call in ctx.run_recognition.call_args_list))

    def test_missing_composition_assets_use_legacy_safeguards_and_unknown_colour(self):
        state, ctx = harness(faces=[detection(i, present=False, authoritative=False) for i in range(1, 6)],
                             nps=[detection(i, present=False, authoritative=False) for i in (1, 2, 3)])
        self.assertEqual([c.color for c in state.cards], [CardColor.UNKNOWN] * 5)
        self.assertEqual(state.np_cards, ())
        ctx.run_recognition.assert_any_call(config.CARD_NODE.format(ui_slot=1), unittest.mock.ANY)


if __name__ == '__main__':
    unittest.main()
