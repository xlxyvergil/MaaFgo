"""Native supplemental scoring preserves fixed plans and uses observed facts only."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.decider import RuleDecider, _best_face_order, _native_card_score
from battle.core.enums import CardColor as Color, PrimitiveKind as Kind, Scene
from battle.core.models import BattleState, CardPick, CommandCard, Confidence, NpCard
from battle.core.plan_parser import _parse_battle_policy
from battle.core.policy import CardPolicy, NativeCardStrategy
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan


def face(slot, color=Color.BUSTER, owner=None, sid=None, *, blocked=False):
    return CommandCard(slot, color, owner, Confidence(1),
                       owner_servant_id=sid, can_attack=not blocked)


def state(cards, nps=()):
    return BattleState(Scene.COMMAND_SELECTION, Confidence(1), tuple(cards), tuple(nps), ())


def policy(**changes):
    return CardPolicy(native_strategy=replace(NativeCardStrategy(enabled=True), **changes))


def planned(attacks):
    return convert_chaldea_actions_to_battle_plan([{"type": "attack", "attacks": attacks}])


def pairs(picks):
    return [(pick.kind, pick.slot) for pick in picks]


AUTO = {"isAuto": True}
NP1 = {"svt": 0, "isTD": True, "card": -1}


class NativeStrategyParsingTests(unittest.TestCase):
    def parse(self, config):
        return _parse_battle_policy({"card_policy": {"native_strategy": config}}).card.native_strategy

    def test_missing_or_malformed_configuration_never_enables_strategy(self):
        self.assertFalse(_parse_battle_policy({}).card.native_strategy.enabled)
        for config in (None, True, [], "enabled", {}, {"enabled": "true"}, {"enabled": 1}):
            with self.subTest(config=config):
                self.assertFalse(self.parse(config).enabled)

    def test_editable_weights_ids_slots_and_colors_parse_without_mutating_source(self):
        config = {"enabled": True, "prefer_brave_chain": False,
                  "brave_chain_weight": 20.5, "mighty_chain_weight": 7,
                  "same_color_chain_weight": 9, "servant_priority_weight": 4,
                  "preferred_servant_ids": [100100, " 304800 ", "0100100", True, None, "bad"],
                  "preferred_slots": [3, 1, 3, 0, 4, True, "2"],
                  "color_weights": {"B": 0, "A": 4.5, "Q": 2, "unknown": 99}}
        result = self.parse(config)
        self.assertTrue(result.enabled)
        self.assertFalse(result.prefer_brave_chain)
        self.assertEqual(result.brave_chain_weight, 20.5)
        self.assertEqual(result.preferred_servant_ids, ("100100", "304800"))
        self.assertEqual(result.preferred_slots, (3, 1))
        self.assertEqual(dict(result.color_weights), {Color.BUSTER: 0, Color.ARTS: 4.5, Color.QUICK: 2})
        self.assertEqual(config["preferred_slots"], [3, 1, 3, 0, 4, True, "2"])

    def test_nonfinite_boolean_and_string_weights_do_not_corrupt_scores(self):
        for invalid in (float("nan"), float("inf"), -float("inf"), True, "12", None, []):
            with self.subTest(invalid=invalid):
                result = self.parse({"enabled": True, "brave_chain_weight": invalid,
                                     "color_weights": {"A": invalid}})
                self.assertEqual(result.brave_chain_weight, NativeCardStrategy().brave_chain_weight)
                self.assertEqual(result.color_weights, ())
        result = self.parse({"enabled": True, "brave_chain_weight": -4,
                             "mighty_chain_weight": 10**1000, "color_weights": {"Q": 999}})
        self.assertEqual((result.brave_chain_weight, result.mighty_chain_weight), (0, 100))
        self.assertEqual(dict(result.color_weights), {Color.QUICK: 100})

    def test_explicit_np_color_maps_normalize_only_valid_ids_slots_and_colors(self):
        config = {"enabled": True,
                  "np_colors_by_servant_id": {" 0605300 ": "A", "100100": "B", "invalid": "Q", "0": "A", "304800": "arts"},
                  "np_colors_by_slot": {"1": "A", 3: "Q", "2": None, "4": "B", "01": "Q", False: "B"}}
        result = self.parse(config)
        self.assertEqual(dict(result.np_colors_by_servant_id), {"605300": Color.ARTS, "100100": Color.BUSTER})
        self.assertEqual(dict(result.np_colors_by_slot), {1: Color.ARTS, 3: Color.QUICK})
        for malformed in (None, [], True, "A"):
            with self.subTest(malformed=malformed):
                result = self.parse({"enabled": True, "np_colors_by_servant_id": malformed,
                                     "np_colors_by_slot": malformed})
                self.assertEqual((result.np_colors_by_servant_id, result.np_colors_by_slot), ((), ()))

    def test_np_color_preservation_is_opt_in_with_bounded_numeric_weight(self):
        self.assertFalse(self.parse({}).avoid_np_color_before_np)
        self.assertEqual(self.parse({}).np_color_preservation_weight, 20)
        self.assertTrue(self.parse({"avoid_np_color_before_np": True}).avoid_np_color_before_np)
        self.assertFalse(self.parse({"avoid_np_color_before_np": "true"}).avoid_np_color_before_np)
        for value, expected in ((-1, 0), (200, 100), (12.5, 12.5), (True, 20), ("60", 20), (float("nan"), 20)):
            with self.subTest(value=value):
                self.assertEqual(self.parse({"np_color_preservation_weight": value}).np_color_preservation_weight, expected)


class NativeStrategyDecisionTests(unittest.TestCase):
    def test_disabled_strategy_keeps_legacy_choice_even_with_extreme_preferences(self):
        cards = (face(1, Color.QUICK, 2, "200100"), face(2, Color.ARTS, 2, "200100"),
                 face(3, Color.BUSTER, 1, "100100"), face(4, Color.BUSTER, 1, "100100"),
                 face(5, Color.QUICK, 3, "300100"))
        disabled = CardPolicy(native_strategy=NativeCardStrategy(
            preferred_servant_ids=("200100",), preferred_slots=(2,), servant_priority_weight=100,
            np_colors_by_slot=((1, Color.ARTS),), mighty_chain_weight=100))
        actual = RuleDecider(disabled).decide(state(cards)).picks
        self.assertEqual([pick.slot for pick in actual], _best_face_order(cards, 3, CardPolicy()))
        attacks = [AUTO, NP1, AUTO]
        default = RuleDecider(plan=planned(attacks)).decide(state(cards, (NpCard(1, Confidence(1)),)))
        actual = RuleDecider(disabled, planned(attacks)).decide(state(cards, (NpCard(1, Confidence(1)),)))
        self.assertEqual(actual, default)

    def test_full_triple_scoring_includes_fixed_face_without_moving_it(self):
        cards = [face(1, Color.ARTS, 1), face(2, Color.ARTS, 2), face(3, Color.ARTS, 3),
                 face(4, Color.BUSTER, 2), face(5, Color.BUSTER, 3)]
        plan = planned([AUTO, {"svt": 0, "card": 0, "cardType": 1}])
        result = RuleDecider(policy(same_color_chain_weight=100), plan).decide(state(cards))
        self.assertEqual(result.picks[1], CardPick(Kind.SELECT_CARD, 1))
        self.assertEqual({pick.slot for pick in result.picks}, {1, 2, 3})
        self.assertIn("native_strategy", result.rationale_tag)

    def test_np_in_middle_forms_brave_chain_from_its_actual_owner(self):
        cards = [face(1, Color.BUSTER, 2), face(2, Color.BUSTER, 3),
                 face(3, Color.QUICK, 1), face(4, Color.ARTS, 1), face(5, Color.BUSTER, 3)]
        result = RuleDecider(policy(brave_chain_weight=100), planned([AUTO, NP1])).decide(
            state(cards, (NpCard(1, Confidence(1)),)))
        self.assertEqual(result.picks[1], CardPick(Kind.SELECT_NP, 1))
        self.assertEqual({pick.slot for pick in result.picks if pick.kind is Kind.SELECT_CARD}, {3, 4})

    def test_automatic_np_prefix_uses_same_complete_combination_scoring(self):
        cards = [face(1, owner=2), face(2, owner=3), face(3, Color.ARTS, 1),
                 face(4, Color.QUICK, 1), face(5, owner=2)]
        result = RuleDecider(policy(brave_chain_weight=100)).decide(state(cards, (NpCard(1, Confidence(1)),)))
        self.assertEqual(result.picks[0], CardPick(Kind.SELECT_NP, 1))
        self.assertEqual({pick.slot for pick in result.picks[1:]}, {3, 4})

    def test_explicit_np_and_face_take_precedence_over_servant_preferences(self):
        cards = [face(1, Color.ARTS, 2, "200100"), face(2, owner=1, sid="100100"),
                 face(3, owner=1, sid="100100"), face(4, owner=1, sid="100100"), face(5, owner=3)]
        plan = planned([AUTO, {"svt": 1, "card": 0, "cardType": 1}, NP1])
        result = RuleDecider(policy(preferred_servant_ids=("100100",), servant_priority_weight=100), plan).decide(
            state(cards, (NpCard(1, Confidence(1)),)))
        self.assertEqual(result.picks[1:], (CardPick(Kind.SELECT_CARD, 1), CardPick(Kind.SELECT_NP, 1)))
        self.assertIn(result.picks[0].slot, {2, 3, 4})

    def test_missing_explicit_face_and_unavailable_np_fill_their_original_holes(self):
        cards = [face(i, owner=2) for i in range(1, 6)]
        plan = planned([{"svt": 0, "card": 0, "cardType": 1}, AUTO, NP1])
        result = RuleDecider(policy(), plan).decide(state(cards, (NpCard(1, Confidence(1), available=False),)))
        self.assertEqual([pick.kind for pick in result.picks], [Kind.SELECT_CARD] * 3)
        self.assertEqual(len({pick.slot for pick in result.picks}), 3)

    def test_identity_priority_tracks_owner_id_after_slot_move(self):
        config = policy(preferred_servant_ids=("100100",), servant_priority_weight=100,
                        prefer_brave_chain=False, mighty_chain_weight=0)
        for owner in (1, 3):
            with self.subTest(owner=owner):
                cards = [face(1, Color.QUICK, owner, "100100"), face(2, Color.ARTS, owner, "100100"),
                         face(3, Color.QUICK, owner, "100100"), face(4, owner=2, sid="200100"),
                         face(5, owner=2, sid="200100")]
                selected = RuleDecider(config).decide(state(cards)).picks
                self.assertEqual({pick.slot for pick in selected}, {1, 2, 3})

    def test_current_frontline_slot_priority_can_select_designated_attacker(self):
        cards = [face(1, Color.QUICK, 2), face(2, Color.ARTS, 2), face(3, Color.QUICK, 2),
                 face(4, owner=1), face(5, owner=3)]
        selected = RuleDecider(policy(preferred_slots=(2,), servant_priority_weight=100)).decide(state(cards)).picks
        self.assertEqual({pick.slot for pick in selected}, {1, 2, 3})

    def test_blocked_faces_lose_to_attackable_faces_despite_high_owner_preference(self):
        cards = [face(1, owner=1, blocked=True), face(2, owner=1, blocked=True),
                 face(3, Color.QUICK, 2), face(4, Color.ARTS, 2), face(5, Color.QUICK, 3)]
        selected = RuleDecider(policy(preferred_slots=(1,), servant_priority_weight=100)).decide(state(cards)).picks
        self.assertEqual({pick.slot for pick in selected}, {3, 4, 5})

    def test_unknown_owners_cannot_create_a_fake_brave_chain(self):
        cards = [face(i, Color.BUSTER) for i in range(1, 6)]
        picks = tuple(CardPick(Kind.SELECT_CARD, i) for i in (1, 2, 3))
        base = _native_card_score(picks, state(cards), policy(brave_chain_weight=0))
        enhanced = _native_card_score(picks, state(cards), policy(brave_chain_weight=100))
        self.assertEqual(enhanced, base)

    def test_duplicate_servant_ids_in_different_slots_cannot_create_brave_chain(self):
        cards = [face(1, owner=1, sid="100100"), face(2, owner=2, sid="100100"),
                 face(3, owner=1, sid="100100")]
        picks = tuple(CardPick(Kind.SELECT_CARD, i) for i in (1, 2, 3))
        self.assertEqual(_native_card_score(picks, state(cards), policy(brave_chain_weight=0)),
                         _native_card_score(picks, state(cards), policy(brave_chain_weight=100)))

    def test_np_color_is_unknown_and_never_invents_color_chain_bonus(self):
        for colors in ((Color.ARTS, Color.ARTS), (Color.ARTS, Color.QUICK)):
            with self.subTest(colors=colors):
                snapshot = state([face(1, colors[0], 2), face(2, colors[1], 3)], (NpCard(1, Confidence(1)),))
                picks = (CardPick(Kind.SELECT_NP, 1), CardPick(Kind.SELECT_CARD, 1), CardPick(Kind.SELECT_CARD, 2))
                self.assertEqual(_native_card_score(picks, snapshot, policy(mighty_chain_weight=0, same_color_chain_weight=0)),
                                 _native_card_score(picks, snapshot, policy(mighty_chain_weight=100, same_color_chain_weight=100)))

    def test_configured_arts_np_fills_mighty_brave_chain_and_follows_recognized_id(self):
        config = _parse_battle_policy({"card_policy": {"native_strategy": {
            "enabled": True, "mighty_chain_weight": 100,
            "np_colors_by_servant_id": {"605300": "A"},
            "np_colors_by_slot": {"1": "B", "3": "B"}}}}).card
        for slot in (1, 3):
            with self.subTest(current_slot=slot):
                other = 2 if slot != 2 else 1
                cards = [face(1, Color.BUSTER, slot, "605300"), face(2, Color.QUICK, slot, "605300"),
                         face(3, Color.ARTS, other), face(4, Color.BUSTER, other), face(5, Color.ARTS, other)]
                np = {"svt": slot - 1, "isTD": True, "card": -1}
                snapshot = state(cards, (NpCard(slot, Confidence(1), owner_servant_id="605300"),))
                result = RuleDecider(config, planned([AUTO, np, AUTO])).decide(snapshot)
                self.assertEqual(pairs(result.picks), [(Kind.SELECT_CARD, 2), (Kind.SELECT_NP, slot), (Kind.SELECT_CARD, 1)])

    def test_explicit_slot_np_color_can_complete_same_color_chain_with_fixed_middle_np(self):
        config = _parse_battle_policy({"card_policy": {"native_strategy": {
            "enabled": True, "same_color_chain_weight": 100,
            "np_colors_by_slot": {"1": "A"}}}}).card
        cards = [face(1, Color.ARTS, 2), face(2, Color.ARTS, 3), face(3), face(4), face(5, Color.QUICK)]
        result = RuleDecider(config, planned([AUTO, NP1, AUTO])).decide(state(cards, (NpCard(1, Confidence(1)),)))
        self.assertEqual(result.picks[1], CardPick(Kind.SELECT_NP, 1))
        self.assertEqual({pick.slot for pick in result.picks if pick.kind is Kind.SELECT_CARD}, {1, 2})

    def test_unmatched_np_id_does_not_borrow_another_servants_configured_color(self):
        snapshot = state([face(1, Color.ARTS), face(2, Color.ARTS)],
                         (NpCard(1, Confidence(1), owner_servant_id="100100"),))
        picks = (CardPick(Kind.SELECT_CARD, 1), CardPick(Kind.SELECT_NP, 1), CardPick(Kind.SELECT_CARD, 2))
        config = policy(np_colors_by_servant_id=(("605300", Color.ARTS),), same_color_chain_weight=100)
        self.assertEqual(_native_card_score(picks, snapshot, config),
                         _native_card_score(picks, snapshot, replace(config, native_strategy=replace(
                             config.native_strategy, same_color_chain_weight=0))))

    def test_unknown_colors_fill_three_distinct_slots_deterministically(self):
        snapshot = state([face(i, Color.UNKNOWN) for i in range(1, 6)])
        result = RuleDecider(policy()).decide(snapshot).picks
        self.assertEqual([pick.slot for pick in result], [1, 2, 3])

    def test_color_weights_can_select_arts_recovery_without_changing_fixed_np_policy(self):
        cards = [face(1, Color.ARTS), face(2, Color.ARTS), face(3, Color.ARTS),
                 face(4), face(5)]
        config = replace(policy(color_weights=((Color.ARTS, 100),),
                                prefer_brave_chain=False, mighty_chain_weight=0), np_first=False)
        result = RuleDecider(config).decide(state(cards, (NpCard(1, Confidence(1)),)))
        self.assertEqual({pick.slot for pick in result.picks}, {1, 2, 3})
        self.assertTrue(all(pick.kind is Kind.SELECT_CARD for pick in result.picks))

    def test_fully_specified_mixed_plan_is_unchanged_by_extreme_preferences(self):
        cards = [face(1, Color.ARTS, 2), face(2, Color.QUICK, 3), face(3), face(4), face(5)]
        plan = planned([{"svt": 2, "card": 0, "cardType": 3}, NP1,
                        {"svt": 1, "card": 0, "cardType": 1}])
        result = RuleDecider(policy(preferred_slots=(1,), servant_priority_weight=100), plan).decide(
            state(cards, (NpCard(1, Confidence(1)),)))
        self.assertEqual(pairs(result.picks), [(Kind.SELECT_CARD, 2), (Kind.SELECT_NP, 1), (Kind.SELECT_CARD, 1)])

    def test_brave_preference_can_be_disabled_and_ordered_priorities_do_not_double_count(self):
        snapshot = state([face(i, owner=2, sid="200100") for i in range(1, 4)])
        picks = tuple(CardPick(Kind.SELECT_CARD, i) for i in range(1, 4))
        baseline = _native_card_score(picks, snapshot, policy(prefer_brave_chain=False))
        brave = _native_card_score(picks, snapshot, policy(brave_chain_weight=20))
        self.assertEqual(brave[2] - baseline[2], 20)
        # Slot 2 is second in its list (half priority), but ID is first (full).
        # One card gets the larger preference once, not full + half together.
        priority = _native_card_score(picks, snapshot, policy(
            prefer_brave_chain=False, preferred_servant_ids=("200100", "100100"),
            preferred_slots=(1, 2), servant_priority_weight=6))
        self.assertEqual(priority[2] - baseline[2], 18)

    def test_automatic_card_before_fixed_arts_np_preserves_same_owner_arts_buff(self):
        np = {"svt": 1, "isTD": True, "card": -1}
        last_arts = {"svt": 1, "card": 0, "cardType": 1}
        config = policy(avoid_np_color_before_np=True, preferred_servant_ids=("605300",),
                        np_colors_by_servant_id=(("605300", Color.ARTS),))
        # The last planned Arts is reserved first. A second main Arts is a bad
        # filler before its NP; another owner's Arts or main Quick remains valid.
        base = [face(1, Color.ARTS, 2, "605300"), face(2, Color.ARTS, 2, "605300"),
                face(3, Color.ARTS, 1, "205500"), face(4, Color.BUSTER, 3, "504500", blocked=True),
                face(5, Color.BUSTER, 3, "504500", blocked=True)]
        for alternative in (base[2], face(3, Color.QUICK, 2, "605300")):
            with self.subTest(alternative=alternative):
                cards = [*base[:2], alternative, *base[3:]]
                result = RuleDecider(config, planned([AUTO, np, last_arts])).decide(state(
                    cards, (NpCard(2, Confidence(1), owner_servant_id="605300"),)))
                self.assertEqual(pairs(result.picks), [(Kind.SELECT_CARD, 3), (Kind.SELECT_NP, 2), (Kind.SELECT_CARD, 1)])

    def test_explicit_same_owner_arts_before_np_remains_fixed_even_with_strong_penalty(self):
        cards = [face(1, Color.ARTS, 2, "605300"), face(2, Color.ARTS, 2, "605300"),
                 face(3, Color.QUICK, 2, "605300"), face(4), face(5)]
        plan = planned([{"svt": 1, "card": 0, "cardType": 1},
                        {"svt": 1, "isTD": True, "card": -1},
                        {"svt": 1, "card": 0, "cardType": 1}])
        result = RuleDecider(policy(avoid_np_color_before_np=True, np_color_preservation_weight=100,
                                   np_colors_by_servant_id=(("605300", Color.ARTS),)), plan).decide(state(
                                       cards, (NpCard(2, Confidence(1), owner_servant_id="605300"),)))
        self.assertEqual(pairs(result.picks), [(Kind.SELECT_CARD, 1), (Kind.SELECT_NP, 2), (Kind.SELECT_CARD, 2)])

    def test_np_color_penalty_requires_known_same_identity_instance_and_color_before_np(self):
        picks = (CardPick(Kind.SELECT_CARD, 1), CardPick(Kind.SELECT_NP, 2), CardPick(Kind.SELECT_CARD, 2))
        base = state([face(1, Color.ARTS, 2, "605300"), face(2, Color.ARTS, 2, "605300")],
                     (NpCard(2, Confidence(1), owner_servant_id="605300"),))
        config = policy(avoid_np_color_before_np=True, np_colors_by_servant_id=(("605300", Color.ARTS),))

        def deduction(snapshot, current=config, sequence=picks):
            ordinary = replace(current, native_strategy=replace(current.native_strategy, avoid_np_color_before_np=False))
            return _native_card_score(sequence, snapshot, ordinary)[2] - _native_card_score(sequence, snapshot, current)[2]

        self.assertEqual(deduction(base), 20)
        self.assertEqual(deduction(base, sequence=(picks[1], picks[0], picks[2])), 0)
        self.assertEqual(deduction(base, replace(config, native_strategy=replace(
            config.native_strategy, np_colors_by_servant_id=()))), 0)
        for face_card in (replace(base.cards[0], owner_slot=1, owner_servant_id="205500"),
                          replace(base.cards[0], owner_slot=1),  # same ID, other instance
                          replace(base.cards[0], owner_slot=None),
                          replace(base.cards[0], owner_servant_id=None),
                          replace(base.cards[0], can_attack=False),
                          replace(base.cards[0], color=Color.QUICK)):
            with self.subTest(face_card=face_card):
                self.assertEqual(deduction(replace(base, cards=(face_card, base.cards[1]))), 0)
        # Slot-configured colour alone does not establish an unknown NP owner.
        unknown = replace(base, np_cards=(replace(base.np_cards[0], owner_servant_id=None),))
        slot_config = replace(config, native_strategy=replace(config.native_strategy, np_colors_by_slot=((2, Color.ARTS),)))
        self.assertEqual(deduction(unknown, slot_config), 0)


if __name__ == "__main__":
    unittest.main()
