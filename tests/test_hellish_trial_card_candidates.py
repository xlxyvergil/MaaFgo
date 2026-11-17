"""Encounter-scoped candidates preserve phase goals without guessing visuals."""
from dataclasses import replace
import unittest

from agent.battle.core.enums import CardColor as C, PrimitiveKind as K, Scene
from agent.battle.core.models import BattleState, CardPick, CommandCard, Confidence, NpCard
from agent.battle.strategies.hellish_trial import (
    DEFAULT_NP_INFO, NpInfo, NpRole, TrialProgress, confirm_submission, propose_cards,
)


def face(index, color, sid="1002200", owner=2, *, known=True, attackable=True):
    return CommandCard(index, color, owner if known else None, Confidence(1),
                       owner_confidence=Confidence(1 if known else 0),
                       owner_servant_id=sid if known else None, can_attack=attackable)


def np(slot, sid, *, available=True, known=True):
    return NpCard(slot, Confidence(1), available=available,
                  owner_servant_id=sid if known else None,
                  owner_confidence=Confidence(1 if known else 0))


def state(colors, *, nps=(), known=True):
    return BattleState(Scene.COMMAND_SELECTION, Confidence(1),
                       tuple(face(i, color, known=known) for i, color in enumerate(colors, 1)),
                       tuple(nps), ())


def pick_np(slot):
    return CardPick(K.SELECT_NP, slot)


def pick_face(slot):
    return CardPick(K.SELECT_CARD, slot)


class HellishTrialCandidateTests(unittest.TestCase):
    def test_phase_one_holds_lancer_np_when_only_one_red_can_attack(self):
        snapshot = state([C.BUSTER, C.ARTS, C.QUICK, C.ARTS, C.QUICK],
                         nps=[np(1, "305300"), np(2, "1002200")])
        progress = TrialProgress.begin(1)
        result = propose_cards(snapshot, progress)
        self.assertFalse(result.waiting)
        self.assertTrue(result.holds_attack_np)
        self.assertTrue(all(p.kind is K.SELECT_CARD for p in result.picks))
        self.assertIn(pick_face(1), result.picks)
        self.assertEqual(result.projected_color_attacks, 1)
        self.assertEqual(progress.remaining_color_attacks, 2)

    def test_phase_one_allows_lancer_then_red_followups_not_alterego_np(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS],
                         nps=[np(1, "305300"), np(2, "1002200")])
        result = propose_cards(snapshot, TrialProgress.begin(1))
        self.assertEqual(result.picks[0], pick_np(1))
        self.assertNotIn(pick_np(2), result.picks)
        self.assertEqual(set(result.picks[1:]), {pick_face(1), pick_face(2)})
        self.assertEqual(result.projected_color_attacks, 2)

    def test_phase_one_never_automatically_uses_medea_preparation(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS],
                         nps=[np(3, "500100")])
        result = propose_cards(snapshot, TrialProgress.begin(1))
        self.assertNotIn(pick_np(3), result.picks)
        fixed = propose_cards(snapshot, TrialProgress.begin(1), fixed_prefix=[pick_np(3)])
        self.assertEqual(fixed.picks[0], pick_np(3))
        self.assertEqual(fixed.projected_color_attacks, 2)

    def test_an_explicit_alterego_np_cannot_override_phase_one_restriction(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS],
                         nps=[np(2, "1002200")])
        result = propose_cards(snapshot, TrialProgress.begin(1), fixed_prefix=[pick_np(2)])
        self.assertTrue(result.waiting)
        self.assertFalse(result.picks)
        self.assertEqual(result.reason, "fixed_prefix_np_forbidden_in_phase")

    def test_support_arts_np_does_not_complete_two_arts_attacks(self):
        snapshot = state([C.ARTS, C.BUSTER, C.QUICK, C.BUSTER, C.QUICK],
                         nps=[np(2, "1002200"), np(3, "504500")])
        result = propose_cards(snapshot, TrialProgress.begin(2),
                               fixed_prefix=[pick_np(2), pick_np(3)])
        self.assertEqual(result.picks, (pick_np(2), pick_np(3), pick_face(1)))
        self.assertEqual(result.projected_color_attacks, 2)
        # Only the support NP and face were actually confirmed in this example.
        updated = confirm_submission(TrialProgress.begin(2), snapshot,
                                     [pick_np(3), pick_face(1)],
                                     submission_id="turn2", attacked_phase=2)
        self.assertEqual(updated.remaining_color_attacks, 1)

    def test_alterego_arts_np_is_held_when_no_other_arts_attack_exists(self):
        snapshot = state([C.QUICK, C.BUSTER, C.QUICK, C.BUSTER, C.QUICK],
                         nps=[np(2, "1002200"), np(3, "504500")])
        result = propose_cards(snapshot, TrialProgress.begin(2))
        self.assertTrue(result.holds_attack_np)
        self.assertNotIn(pick_np(2), result.picks)
        self.assertNotIn(pick_np(3), result.picks)

    def test_phase_three_waits_with_faces_if_only_one_quick_exists(self):
        snapshot = state([C.QUICK, C.BUSTER, C.ARTS, C.BUSTER, C.ARTS],
                         nps=[np(1, "305300"), np(2, "1002200")])
        result = propose_cards(snapshot, TrialProgress.begin(3))
        self.assertTrue(result.holds_attack_np)
        self.assertEqual(result.projected_color_attacks, 1)
        self.assertTrue(all(p.kind is K.SELECT_CARD for p in result.picks))

    def test_phase_three_alterego_precedes_two_quick_overgauge_followups(self):
        snapshot = state([C.QUICK, C.QUICK, C.ARTS, C.BUSTER, C.ARTS],
                         nps=[np(1, "305300"), np(2, "1002200")])
        result = propose_cards(snapshot, TrialProgress.begin(3))
        self.assertEqual(result.picks[0], pick_np(2))
        self.assertEqual(set(result.picks[1:]), {pick_face(1), pick_face(2)})
        self.assertNotIn(pick_np(1), result.picks)

    def test_explicit_phase_three_np_prefix_retains_original_video_order(self):
        snapshot = state([C.QUICK, C.BUSTER, C.ARTS, C.BUSTER, C.ARTS],
                         nps=[np(1, "305300"), np(2, "1002200")])
        result = propose_cards(snapshot, TrialProgress.begin(3),
                               fixed_prefix=[pick_np(2), pick_np(1)])
        self.assertEqual(result.picks, (pick_np(2), pick_np(1), pick_face(1)))
        self.assertEqual(result.projected_color_attacks, 2)
        wrong = propose_cards(snapshot, TrialProgress.begin(3), fixed_prefix=[pick_np(1)])
        self.assertTrue(wrong.waiting)
        self.assertFalse(wrong.picks)

    def test_phase_three_prefers_ae_quick_followup_over_another_servants_quick(self):
        snapshot = state([C.QUICK, C.QUICK, C.ARTS, C.BUSTER, C.ARTS],
                         nps=[np(1, "305300"), np(2, "1002200")])
        snapshot = replace(snapshot, cards=(face(1, C.QUICK, "305300", 1), *snapshot.cards[1:]))
        result = propose_cards(snapshot, TrialProgress.begin(3),
                               fixed_prefix=[pick_np(2), pick_np(1)])
        self.assertEqual(result.picks, (pick_np(2), pick_np(1), pick_face(2)))

    def test_phase_four_requires_cleanse_np_before_alterego_np(self):
        snapshot = state([C.QUICK, C.BUSTER, C.ARTS, C.BUSTER, C.ARTS],
                         nps=[np(2, "1002200"), np(3, "504500")])
        result = propose_cards(snapshot, TrialProgress.begin(4), require_cleanse_before_finish=True)
        self.assertEqual(result.picks[:2], (pick_np(3), pick_np(2)))
        self.assertFalse(result.waiting)
        self.assertEqual(result.projected_color_attacks, 0)

    def test_phase_four_without_cleanse_np_does_not_guess_scorch_absence(self):
        snapshot = state([C.QUICK, C.BUSTER, C.ARTS, C.BUSTER, C.ARTS],
                         nps=[np(2, "1002200")])
        result = propose_cards(snapshot, TrialProgress.begin(4), require_cleanse_before_finish=True)
        self.assertTrue(result.waiting)
        self.assertEqual(result.reason, "cleanse_np_unavailable")
        self.assertFalse(result.picks)

    def test_explicit_wrong_cleanse_order_is_rejected_instead_of_reordered(self):
        snapshot = state([C.QUICK, C.BUSTER, C.ARTS, C.BUSTER, C.ARTS],
                         nps=[np(2, "1002200"), np(3, "504500")])
        result = propose_cards(snapshot, TrialProgress.begin(4),
                               fixed_prefix=[pick_np(2), pick_np(3)],
                               require_cleanse_before_finish=True)
        self.assertTrue(result.waiting)
        self.assertEqual(result.reason, "cleanse_must_precede_all_attacks")

    def test_unknown_ownership_does_not_become_a_guessed_current_slot(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS], known=False,
                         nps=[np(2, "1002200", known=False)])
        result = propose_cards(snapshot, TrialProgress.begin(1))
        self.assertTrue(result.waiting)
        self.assertFalse(result.picks)
        updated = confirm_submission(TrialProgress.begin(1), snapshot, [pick_face(1), pick_face(2)],
                                     submission_id="unknown", attacked_phase=1)
        self.assertEqual(updated.remaining_color_attacks, 2)

    def test_just_one_unknown_face_can_be_omitted_if_three_known_choices_remain(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS])
        snapshot = replace(snapshot, cards=(*snapshot.cards[:4], face(5, C.ARTS, known=False)))
        result = propose_cards(snapshot, TrialProgress.begin(1))
        self.assertFalse(result.waiting)
        self.assertNotIn(pick_face(5), result.picks)

    def test_sealed_faces_and_unavailable_np_are_not_mission_attacks(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS],
                         nps=[np(1, "305300", available=False)])
        snapshot = replace(snapshot, cards=(replace(snapshot.cards[0], can_attack=False), *snapshot.cards[1:]))
        result = propose_cards(snapshot, TrialProgress.begin(1))
        self.assertNotIn(pick_face(1), result.picks)
        self.assertNotIn(pick_np(1), result.picks)
        self.assertEqual(result.projected_color_attacks, 1)

    def test_confirmed_submission_is_external_idempotent_and_does_not_advance_phase(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS])
        start = TrialProgress.begin(1)
        suggestion = propose_cards(snapshot, start)
        self.assertEqual(start.remaining_color_attacks, 2)
        updated = confirm_submission(start, snapshot, suggestion.picks,
                                     submission_id="battle1-turn1", attacked_phase=1)
        self.assertEqual((updated.phase, updated.remaining_color_attacks), (1, 0))
        self.assertIs(confirm_submission(updated, snapshot, suggestion.picks,
                                         submission_id="battle1-turn1", attacked_phase=1), updated)
        with self.assertRaises(ValueError):
            confirm_submission(updated, snapshot, suggestion.picks,
                               submission_id="wrong-bar", attacked_phase=2)

    def test_non_command_scene_or_missing_hand_returns_no_input(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS])
        for bad in (replace(snapshot, scene=Scene.MAIN_BATTLE), replace(snapshot, cards=snapshot.cards[:4])):
            with self.subTest(scene=bad.scene, card_count=len(bad.cards)):
                result = propose_cards(bad, TrialProgress.begin(1))
                self.assertTrue(result.waiting)
                self.assertFalse(result.picks)

    def test_auxiliary_np_cannot_be_mislabelled_as_an_attack(self):
        with self.assertRaises(ValueError):
            NpInfo("504500", C.ARTS, NpRole.CLEANSE, True)
        with self.assertRaises(ValueError):
            propose_cards(state([C.BUSTER] * 5), TrialProgress.begin(1),
                          np_info=(*DEFAULT_NP_INFO, DEFAULT_NP_INFO[0]))

    def test_fixed_positions_preserve_the_middle_card_and_only_fill_holes(self):
        snapshot = state([C.QUICK, C.BUSTER, C.QUICK, C.ARTS, C.ARTS],
                         nps=[np(2, "1002200")])
        result = propose_cards(snapshot, TrialProgress.begin(3),
                               fixed_positions=[None, pick_face(3), None], allow_attack_np=False)
        self.assertEqual(result.picks[1], pick_face(3))
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in result.picks))
        self.assertEqual(result.projected_color_attacks, 2)

    def test_forbid_automatic_np_does_not_forbid_a_safe_explicit_np(self):
        snapshot = state([C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS],
                         nps=[np(1, "305300")])
        auto = propose_cards(snapshot, TrialProgress.begin(1), allow_attack_np=False)
        fixed = propose_cards(snapshot, TrialProgress.begin(1), allow_attack_np=False,
                              fixed_positions=[pick_np(1), None, None])
        self.assertNotIn(pick_np(1), auto.picks)
        self.assertEqual(fixed.picks[0], pick_np(1))

    def test_incomplete_colour_hand_prefers_support_fillers_over_primary_damage(self):
        snapshot = state([C.BUSTER, C.QUICK, C.ARTS, C.ARTS, C.QUICK])
        snapshot = replace(snapshot, cards=(face(1, C.BUSTER, "305300", 1),
            face(2, C.QUICK, "1002200", 2), face(3, C.ARTS, "504500", 3),
            face(4, C.ARTS, "504500", 3), face(5, C.QUICK, "305300", 1)))
        result = propose_cards(snapshot, TrialProgress.begin(1))
        self.assertEqual(set(result.picks), {pick_face(1), pick_face(3), pick_face(4)})
        self.assertEqual(result.projected_color_attacks, 1)

    def test_finisher_np_is_followed_by_its_own_face_before_a_different_class(self):
        info = (*DEFAULT_NP_INFO, NpInfo("1000200", C.QUICK, NpRole.ALTEREGO, True))
        snapshot = state([C.BUSTER] * 4 + [C.QUICK], nps=[np(2, "1000200")])
        snapshot = replace(snapshot, cards=tuple(face(i, C.BUSTER, "304800", 1) for i in range(1, 5))
                           + (face(5, C.QUICK, "1000200", 2),))
        for phase, remaining in ((2, 0), (3, 2)):
            with self.subTest(phase=phase):
                result = propose_cards(snapshot, TrialProgress.begin(phase, remaining), np_info=info)
                self.assertEqual(result.picks, (pick_np(2), pick_face(5), pick_face(1)))
                self.assertGreaterEqual(result.projected_color_attacks, remaining)

    def test_collect_two_arts_and_put_the_finisher_arts_before_the_other_arts(self):
        info = (*DEFAULT_NP_INFO, NpInfo("1000200", C.QUICK, NpRole.ALTEREGO, True))
        snapshot = state([C.ARTS, C.BUSTER, C.BUSTER, C.BUSTER, C.ARTS],
                         nps=[np(2, "1000200")])
        snapshot = replace(snapshot, cards=(face(1, C.ARTS, "304800", 1),
            face(2, C.BUSTER, "304800", 1), face(3, C.BUSTER, "304800", 1),
            face(4, C.BUSTER, "304800", 1), face(5, C.ARTS, "1000200", 2)))
        result = propose_cards(snapshot, TrialProgress.begin(2), np_info=info)
        self.assertEqual(result.picks, (pick_np(2), pick_face(5), pick_face(1)))
        self.assertEqual(result.projected_color_attacks, 2)

    def test_explicit_second_card_is_never_moved_by_finisher_order(self):
        info = (*DEFAULT_NP_INFO, NpInfo("1000200", C.QUICK, NpRole.ALTEREGO, True))
        snapshot = state([C.BUSTER] * 4 + [C.QUICK], nps=[np(2, "1000200")])
        snapshot = replace(snapshot, cards=tuple(face(i, C.BUSTER, "304800", 1) for i in range(1, 5))
                           + (face(5, C.QUICK, "1000200", 2),))
        result = propose_cards(snapshot, TrialProgress.begin(2, 0), np_info=info,
                               fixed_positions=[pick_np(2), pick_face(1), None])
        self.assertEqual(result.picks, (pick_np(2), pick_face(1), pick_face(5)))

    def test_finisher_order_precedes_colour_order_after_required_arts_are_covered(self):
        snapshot = state([C.ARTS, C.BUSTER, C.BUSTER, C.BUSTER, C.QUICK],
                         nps=[np(2, "1002200")])
        snapshot = replace(snapshot, cards=(face(1, C.ARTS, "304800", 1),
            face(2, C.BUSTER, "304800", 1), face(3, C.BUSTER, "304800", 1),
            face(4, C.BUSTER, "304800", 1), face(5, C.QUICK, "1002200", 2)))
        result = propose_cards(snapshot, TrialProgress.begin(2))
        self.assertEqual(result.picks, (pick_np(2), pick_face(5), pick_face(1)))
        self.assertEqual(result.projected_color_attacks, 2)

    def test_without_main_np_the_existing_stable_face_order_is_preserved(self):
        info = (*DEFAULT_NP_INFO, NpInfo("1000200", C.QUICK, NpRole.ALTEREGO, True))
        snapshot = state([C.BUSTER] * 4 + [C.QUICK])
        snapshot = replace(snapshot, cards=tuple(face(i, C.BUSTER, "304800", 1) for i in range(1, 5))
                           + (face(5, C.QUICK, "1000200", 2),))
        result = propose_cards(snapshot, TrialProgress.begin(2, 0), np_info=info)
        self.assertEqual(result.picks, (pick_face(1), pick_face(2), pick_face(5)))


if __name__ == "__main__":
    unittest.main()
