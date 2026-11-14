"""Encounter-only stage plans consume actions after real submitted turns."""
from dataclasses import replace
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "agent"), str(ROOT / "agent/custom")]
from formation_test_support import battle_action as entry  # installs isolated Maa stubs
from battle.core.decider import RuleDecider
from battle.core.enums import CardColor as C, PrimitiveKind as K, Scene
from battle.core.models import (ActorBinding, BattleAction, BattlePlan, BattleState, CardPick,
    CommandCard, Confidence, MasterSkillAction, NpCard, OrderChangeAction, PlannedCard,
    ServantSkillAction, ServantState, SkillState, TurnPlan)
from battle.core.policy import StrategyProfile
from battle.core.special_skills import SpecialSkillChoices
from battle.runtime.hellish_trial import (HellishTrialDecider, HellishTrialRuntime,
    TrialStrategyConfig, TrialStrategyError, trial_config_from_source)
from battle.runtime.runtime import AutoBattleRuntime
from battle.strategies.hellish_trial import DEFAULT_NP_INFO, NpInfo, NpRole, TrialProgress, propose_cards


NODE = {"attach": {"native_battle_trial_profile": "hellish_202611"}}
MAPPING = ((1,), (2, 3), (4,), (5, 6))
INFO = (*DEFAULT_NP_INFO,
    NpInfo("304800", C.BUSTER, NpRole.LANCER, True),
    NpInfo("1000200", C.QUICK, NpRole.ALTEREGO, True))


def face(slot, color, sid="504500", owner=3, **changes):
    card = CommandCard(slot, color, owner, Confidence(1), Confidence(1), sid)
    return replace(card, **changes)


def np(slot, sid, **changes):
    return replace(NpCard(slot, Confidence(1), owner_servant_id=sid,
                          owner_confidence=Confidence(1)), **changes)


def hand(colors=(C.ARTS, C.BUSTER, C.ARTS, C.QUICK, C.ARTS), nps=()):
    return BattleState(Scene.COMMAND_SELECTION, Confidence(1),
        tuple(face(index, color) for index, color in enumerate(colors, 1)), tuple(nps), ())


def decider(phase=1, *, turns=None, max_wait=3, mode="strict", preparation=()):
    turns = turns or (TurnPlan(np_order=(1,)), TurnPlan(np_order=(3,)),
                     TurnPlan(np_order=(2,)), TurnPlan(np_order=(2,)),
                     TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 1),
                        PlannedCard(K.SELECT_CARD, 0), PlannedCard(K.SELECT_CARD, 0))),
                     TurnPlan(np_order=(3, 2)))
    config = TrialStrategyConfig(TrialProgress.begin(phase), True, INFO, preparation,
                                MAPPING, max_wait, mode)
    result = HellishTrialDecider(RuleDecider(plan=BattlePlan(turns)), config)
    result.observe_phase(phase)
    return result


def confirm(candidate, state, action, sequence="turn1"):
    candidate.confirm(state, action.picks, candidate.progress.phase, sequence)


class TrialPhasePlanConfigTests(unittest.TestCase):
    def test_mapping_is_optional_and_other_tasks_do_not_parse_it(self):
        plain = trial_config_from_source(NODE, {"maaTrialStrategy": {"enabled": True}})
        self.assertEqual(plain.phase_plan_turns, ())
        self.assertEqual(plain.final_bar_preparation_mode, "strict")
        self.assertIsNone(trial_config_from_source({}, {"maaTrialStrategy": {
            "enabled": True, "phasePlanTurns": "invalid"}}))
        value = {"enabled": True, "phasePlanTurns": [list(group) for group in MAPPING],
                 "maxWaitTurnsPerStep": 3, "finalBarPreparationMode": "charge_cleanser",
                 "finalBarPreparationTurns": [5]}
        config = trial_config_from_source(NODE, {"maaTrialStrategy": value})
        self.assertEqual(config.phase_plan_turns, MAPPING)
        self.assertEqual(config.max_wait_turns_per_step, 3)

    def test_invalid_mapping_and_wait_limits_fail_separately(self):
        for value in (None, {}, (), [[1]], [[1], [], [3], [4]],
                      [[1], [2, 2], [3], [4]], [[2], [1], [3], [4]],
                      [[True], [2], [3], [4]], [[1], ["2"], [3], [4]]):
            with self.subTest(value=value), self.assertRaises(TrialStrategyError):
                trial_config_from_source(NODE, {"maaTrialStrategy": {
                    "enabled": True, "phasePlanTurns": value}})
        for value in (-1, 11, True, "3", None):
            with self.subTest(value=value), self.assertRaises(TrialStrategyError):
                trial_config_from_source(NODE, {"maaTrialStrategy": {
                    "enabled": True, "maxWaitTurnsPerStep": value}})
        with self.assertRaises(TrialStrategyError):
            trial_config_from_source(NODE, {"maaTrialStrategy": {
                "enabled": True, "finalBarPreparationMode": "charge_cleanser"}})
        for preparation in ([], [4], [6], [5, 6]):
            with self.subTest(preparation=preparation), self.assertRaises(TrialStrategyError):
                trial_config_from_source(NODE, {"maaTrialStrategy": {
                    "enabled": True, "phasePlanTurns": [list(group) for group in MAPPING],
                    "finalBarPreparationMode": "charge_cleanser",
                    "finalBarPreparationTurns": preparation}})

    def test_optional_charge_priorities_and_waiting_cleanse_require_stage_mode(self):
        raw = {"enabled": True, "phasePlanTurns": [list(group) for group in MAPPING],
               "chargePriorityServantIds": ["504500"], "useCleanseNpWhileWaiting": True}
        config = trial_config_from_source(NODE, {"maaTrialStrategy": raw})
        self.assertEqual(config.charge_priority_servant_ids, ("504500",))
        self.assertTrue(config.use_cleanse_np_while_waiting)
        for extra in ({"chargePriorityServantIds": ["504500"]}, {"useCleanseNpWhileWaiting": True}):
            with self.subTest(extra=extra), self.assertRaises(TrialStrategyError):
                trial_config_from_source(NODE, {"maaTrialStrategy": {"enabled": True, **extra}})
        for ids in (None, "504500", [504500], ["0"], ["504500", "504500"]):
            with self.subTest(ids=ids), self.assertRaises(TrialStrategyError):
                trial_config_from_source(NODE, {"maaTrialStrategy": {**raw, "chargePriorityServantIds": ids}})
        with self.assertRaises(TrialStrategyError):
            trial_config_from_source(NODE, {"maaTrialStrategy": {**raw, "useCleanseNpWhileWaiting": "true"}})


class TrialPhaseStepTests(unittest.TestCase):
    def test_observed_phase_selects_real_row_and_missing_row_stops(self):
        candidate = decider(2)
        self.assertEqual(candidate.plan_turn_for_phase(99), 1)
        candidate.plan = BattlePlan((TurnPlan(),))
        with self.assertRaisesRegex(TrialStrategyError, "第2回合不存在"):
            candidate.plan_turn_for_phase(0)

    def test_special_menu_replay_queues_are_rejected_before_any_phase_skills(self):
        candidate = decider()
        candidate.plan = replace(candidate.plan, special_choices=SpecialSkillChoices((1,)))
        with patch.object(candidate.inner, "decide") as inner:
            with self.assertRaisesRegex(TrialStrategyError, "技能内选项"):
                candidate.decide(replace(hand(), scene=Scene.MAIN_BATTLE))
        inner.assert_not_called()

    def test_each_rows_skills_are_only_decided_once(self):
        candidate = decider(2)
        state = replace(hand(), scene=Scene.MAIN_BATTLE)
        expected = BattleAction(None, (), rationale_tag="first skill batch")
        with patch.object(candidate.inner, "decide", return_value=expected) as inner:
            self.assertIs(candidate.decide(state, 0), expected)
            repeated = candidate.decide(state, 0)
        inner.assert_called_once_with(state, 1)
        self.assertEqual(repeated.skill_sequence, ())
        self.assertIn("skills_already_executed", repeated.rationale_tag)

    def test_colour_wait_does_not_consume_next_step_or_repeat_skills(self):
        candidate = decider(2)
        state = hand((C.BUSTER, C.BUSTER, C.QUICK, C.BUSTER, C.QUICK),
                     nps=(np(3, "504500"),))
        action = candidate.decide(state)
        confirm(candidate, state, action)
        self.assertEqual(candidate.plan_turn_for_phase(20), 2)
        # The finisher's fixed Quick NP has no Arts follow-up this turn.
        state = replace(state, np_cards=(np(2, "1000200"),))
        waiting = candidate.decide(state)
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in waiting.picks))
        self.assertEqual(candidate.progress.remaining_color_attacks, 2)
        confirm(candidate, state, waiting, "turn2")
        self.assertEqual(candidate.plan_turn_for_phase(20), 2)
        self.assertEqual(candidate._wait_counts, {2: 1})
        ready = hand(nps=(np(2, "1000200"),))
        finish = candidate.decide(ready)
        self.assertEqual(finish.picks[0], CardPick(K.SELECT_NP, 2))
        self.assertEqual(sum(ready.cards[pick.slot - 1].color is C.ARTS
                             for pick in finish.picks if pick.kind is K.SELECT_CARD), 2)

    def test_wait_budget_is_only_committed_after_confirmed_submission(self):
        candidate = decider(max_wait=1)
        state = hand(nps=())
        action = candidate.decide(state)
        candidate.decide(state)  # mere re-observation does not spend the budget
        self.assertEqual(candidate._wait_counts, {})
        confirm(candidate, state, action)
        self.assertEqual(candidate._wait_counts, {0: 1})
        confirm(candidate, state, action)  # same animation evidence is idempotent
        self.assertEqual(candidate._wait_counts, {0: 1})
        with self.assertRaisesRegex(TrialStrategyError, "额外普攻等待1回合"):
            candidate.decide(state)

    def test_unknown_np_identity_never_becomes_a_waiting_substitution(self):
        candidate = decider()
        state = hand(nps=(np(1, "304800", owner_confidence=Confidence(.79)),))
        with self.assertRaisesRegex(TrialStrategyError, "无法可靠确认"):
            candidate.decide(state)

    def test_break_moves_to_actual_phase_and_skips_unconsumed_old_rows(self):
        candidate = decider(2)
        state = hand(nps=(np(3, "504500"),))
        action = candidate.decide(state)
        confirm(candidate, state, action)
        candidate.observe_phase(3)
        self.assertEqual(candidate.plan_turn_for_phase(99), 3)
        self.assertEqual(candidate._started_skill_turns, set())
        with self.assertRaises(TrialStrategyError):
            candidate.observe_phase(1)


class TrialCleanserChargeTests(unittest.TestCase):
    def state(self, *, cleanser_np=False, ae_faces=2):
        cards = [face(1, C.BUSTER, "304800", 1), face(2, C.ARTS), face(3, C.QUICK),
                 face(4, C.ARTS, "1000200", 2), face(5, C.BUSTER, "1000200", 2)]
        if ae_faces < 2:
            cards[4] = face(5, C.ARTS)
        if ae_faces > 2:
            cards[2] = face(3, C.QUICK, "1000200", 2)
        nps = (np(1, "304800"), np(2, "1000200"))
        if cleanser_np:
            nps += (np(3, "504500"),)
        return replace(hand(), cards=tuple(cards), np_cards=nps)

    def test_single_preparation_keeps_non_ae_np_and_prioritises_cleanser_arts(self):
        candidate = decider(4, mode="charge_cleanser", preparation=(5,))
        state = self.state(ae_faces=1)
        action = candidate.decide(state)
        self.assertEqual(action.picks[0], CardPick(K.SELECT_NP, 1))
        self.assertEqual(set(action.picks[1:]), {CardPick(K.SELECT_CARD, 2), CardPick(K.SELECT_CARD, 5)})
        self.assertEqual(candidate.plan_turn_for_phase(0), 4)
        confirm(candidate, state, action)
        self.assertEqual(candidate.plan_turn_for_phase(0), 5)
        # Missing final cleanse NP now yields only non-AE faces, never Melusine NP again.
        waiting = candidate.decide(state)
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in waiting.picks))
        self.assertNotIn(CardPick(K.SELECT_CARD, 4), waiting.picks)
        confirm(candidate, state, waiting, "turn2")
        self.assertEqual(candidate.plan_turn_for_phase(0), 5)

    def test_final_wait_never_fills_with_ae_faces_and_recovery_restores_cleanse_first(self):
        candidate = decider(4, mode="charge_cleanser", preparation=(5,))
        state = self.state()
        action = candidate.decide(state)
        confirm(candidate, state, action)
        waiting = candidate.decide(state)
        self.assertEqual(set(waiting.picks), {CardPick(K.SELECT_CARD, 1),
            CardPick(K.SELECT_CARD, 2), CardPick(K.SELECT_CARD, 3)})
        with self.assertRaisesRegex(TrialStrategyError, "不足三张可靠的非AE普通卡"):
            candidate.decide(self.state(ae_faces=3))
        finish = candidate.decide(self.state(cleanser_np=True))
        self.assertEqual(finish.picks[:2], (CardPick(K.SELECT_NP, 3), CardPick(K.SELECT_NP, 2)))

    def test_ae_attack_np_cannot_be_relabelled_as_preparation(self):
        candidate = decider(4, mode="charge_cleanser", preparation=(5,))
        turns = list(candidate.plan.turns)
        turns[4] = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 2),
            PlannedCard(K.SELECT_CARD, 0), PlannedCard(K.SELECT_CARD, 0)))
        candidate.plan = BattlePlan(tuple(turns))
        with self.assertRaisesRegex(TrialStrategyError, "安排了AE攻击宝具"):
            candidate.decide(self.state())

    def test_low_confidence_faces_are_not_used_for_charging(self):
        candidate = decider(4, mode="charge_cleanser", preparation=(5,))
        state = self.state()
        action = candidate.decide(state)
        confirm(candidate, state, action)
        state = replace(state, cards=(replace(state.cards[0], owner_confidence=Confidence(.79)), *state.cards[1:]))
        with self.assertRaisesRegex(TrialStrategyError, "不足三张可靠的非AE普通卡"):
            candidate.decide(state)


class TrialPrimaryChargeTests(unittest.TestCase):
    def test_real_t3_hand_can_charge_castoria_instead_of_full_np_melusine(self):
        # recognition-32968.log 02:15:07.262, phase2 colour goal already complete.
        candidate = decider(2)
        candidate._phase_step = 1
        candidate.progress = TrialProgress.begin(2, 0)
        state = replace(hand(), cards=(face(1, C.ARTS, "304800", 1), face(2, C.ARTS),
            face(3, C.ARTS), face(4, C.BUSTER), face(5, C.BUSTER, "1000200", 2)),
            np_cards=(np(1, "304800"), np(2, "1000200")))
        original = candidate.decide(state)
        self.assertEqual(original.picks, (CardPick(K.SELECT_NP, 2),
            CardPick(K.SELECT_CARD, 5), CardPick(K.SELECT_CARD, 1)))
        candidate.config = replace(candidate.config, charge_priority_servant_ids=("504500",))
        charged = candidate.decide(state)
        self.assertEqual(charged.picks[0], CardPick(K.SELECT_NP, 2))
        self.assertEqual(set(charged.picks[1:]), {CardPick(K.SELECT_CARD, 2), CardPick(K.SELECT_CARD, 3)})
        # Castoria's already-ready NP is not selected here, so her charge bias is cancelled.
        ready = candidate.decide(replace(state, np_cards=(*state.np_cards, np(3, "504500"))))
        self.assertEqual(ready.picks, original.picks)

    def test_waiting_support_np_protects_without_consuming_attack_np_or_arts_count(self):
        candidate = decider(3)
        candidate.config = replace(candidate.config, use_cleanse_np_while_waiting=True)
        turns = list(candidate.plan.turns)
        turns[3] = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 2,
            actor=ActorBinding(1, "1000200")), PlannedCard(K.SELECT_CARD, 0), PlannedCard(K.SELECT_CARD, 0)))
        candidate.plan = BattlePlan(tuple(turns))
        state = replace(hand(), cards=(face(1, C.ARTS, "304800", 1), face(2, C.QUICK),
            face(3, C.ARTS), face(4, C.ARTS, "1000200", 2), face(5, C.BUSTER)),
            np_cards=(np(1, "304800"), np(3, "504500")))
        action = candidate.decide(state)
        self.assertEqual(action.picks, (CardPick(K.SELECT_NP, 3),
            CardPick(K.SELECT_CARD, 2), CardPick(K.SELECT_CARD, 4)))
        confirm(candidate, state, action)
        self.assertEqual(candidate.progress.remaining_color_attacks, 1)
        self.assertEqual(candidate.plan_turn_for_phase(0), 3)
        low = replace(state, np_cards=(np(3, "504500", owner_confidence=Confidence(.79)),))
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in candidate.decide(low).picks))

    def test_real_t4_hand_keeps_quick_but_uses_melt_arts_last_with_arts_lead(self):
        fixture = json.loads((ROOT / "tests/fixtures/battle_trial/20261114_t4_melt_charge.json").read_text("utf-8"))
        candidate = decider(fixture["phase"])
        turns = list(candidate.plan.turns)
        fixed = fixture["fixedNp"]
        turns[3] = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, fixed["slot"],
            actor=ActorBinding(fixed["actorInitialSlot"], fixed["servantId"])),
            PlannedCard(K.SELECT_CARD, 0), PlannedCard(K.SELECT_CARD, 0)))
        candidate.plan = BattlePlan(tuple(turns))
        cards = tuple(face(item["slot"], C(item["color"]), item["servantId"], item["ownerSlot"],
                           confidence=Confidence(item["confidence"]), owner_confidence=Confidence(item["confidence"]))
                      for item in fixture["cards"])
        nps = tuple(np(item["slot"], item["servantId"], confidence=Confidence(item["confidence"]))
                    for item in fixture["npCards"])
        state = replace(hand(), scene_confidence=Confidence(fixture["sceneConfidence"]), cards=cards, np_cards=nps)
        action = candidate.decide(state)
        self.assertEqual([pick.slot for pick in action.picks], fixture["expectedSelectedSlots"])
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in action.picks))
        self.assertEqual(candidate.progress.remaining_color_attacks, 2)
        confirm(candidate, state, action)
        self.assertEqual(candidate.progress.remaining_color_attacks, 1)
        self.assertEqual(candidate.plan_turn_for_phase(0), 3)

    def test_preparation_missing_melusine_np_charges_melusine_before_cleanser(self):
        candidate = decider(4, mode="charge_cleanser", preparation=(5,))
        turns = list(candidate.plan.turns)
        turns[4] = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 1, actor=ActorBinding(0, "304800")),
            PlannedCard(K.SELECT_CARD, 0), PlannedCard(K.SELECT_CARD, 0)))
        candidate.plan = BattlePlan(tuple(turns))
        state = replace(hand(), cards=(face(1, C.ARTS, "304800", 1), face(2, C.ARTS),
            face(3, C.QUICK), face(4, C.ARTS), face(5, C.BUSTER)))
        action = candidate.decide(state)
        self.assertIn(CardPick(K.SELECT_CARD, 1), action.picks)
        self.assertEqual(action.picks[-1], CardPick(K.SELECT_CARD, 1))
        self.assertEqual(state.cards[action.picks[0].slot - 1].color, C.ARTS)

    def test_sealed_verified_np_owner_can_have_safe_faces_without_claiming_low_np(self):
        candidate = decider(3)
        state = hand(nps=(np(2, "1000200", available=False, blocked_reason="宝具封印"),))
        state = replace(state, cards=(face(1, C.ARTS, "1000200", 2), *state.cards[1:]))
        action = candidate.decide(state)
        self.assertIn(CardPick(K.SELECT_CARD, 1), action.picks)
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in action.picks))

    def test_unavailable_np_actor_and_visible_identity_conflict_is_not_a_charge_guess(self):
        candidate = decider(3)
        turns = list(candidate.plan.turns)
        turns[3] = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 2,
            actor=ActorBinding(1, "1000200")),))
        candidate.plan = BattlePlan(tuple(turns))
        with self.assertRaisesRegex(TrialStrategyError, "归属与方案绑定的从者不一致"):
            candidate.decide(hand(nps=(np(2, "1002200", available=False),)))


class TrialNoMightyAndSelfCleanseTests(unittest.TestCase):
    def tia(self):
        turns = (TurnPlan(np_order=(1,)), TurnPlan(np_order=(2,)),
                 TurnPlan(np_order=(2,)), TurnPlan(np_order=(2,)))
        candidate = decider(4, turns=turns)
        candidate.config = replace(candidate.config, phase_plan_turns=((1,), (2,), (3,), (4,)),
            avoid_mighty_chains=True, self_cleanse_finishers=("1001600",),
            np_info=(*INFO, NpInfo("1001600", C.BUSTER, NpRole.ALTEREGO, True)))
        return candidate

    def test_self_cleanse_config_is_hard_identity_and_requires_all_safety_flags(self):
        base = {"enabled": True, "phasePlanTurns": [[1], [2], [3], [4]],
            "avoidMightyChains": True, "requireCleanseBeforeFinish": True,
            "selfCleanseFinishers": ["1001600"], "npMetadata": [{"servantId": "1001600",
                "color": "B", "role": "alterego_finisher", "damaging": True}]}
        config = trial_config_from_source(NODE, {"maaTrialStrategy": base})
        self.assertTrue(config.avoid_mighty_chains)
        self.assertEqual(config.self_cleanse_finishers, ("1001600",))
        for changes in ({"phasePlanTurns": []}, {"avoidMightyChains": False},
                        {"avoidMightyChains": "true"}, {"requireCleanseBeforeFinish": False},
                        {"selfCleanseFinishers": ["1000200"]}, {"npMetadata": []},
                        {"npMetadata": [{"servantId": "1001600", "color": "A",
                            "role": "alterego_finisher", "damaging": True}]}):
            with self.subTest(changes=changes), self.assertRaises(TrialStrategyError):
                trial_config_from_source(NODE, {"maaTrialStrategy": {**base, **changes}})

    def test_candidate_mighty_is_excluded_and_fully_fixed_mighty_stops(self):
        state = replace(hand(nps=(np(2, "1002200"),)), cards=(face(1, C.BUSTER),
            face(2, C.QUICK), face(3, C.BUSTER), face(4, C.QUICK), face(5, C.BUSTER)))
        result = propose_cards(state, TrialProgress.begin(2, 0), avoid_mighty_chains=True)
        self.assertFalse(result.waiting)
        self.assertEqual(result.picks[0], CardPick(K.SELECT_NP, 2))
        self.assertEqual({state.cards[pick.slot - 1].color for pick in result.picks[1:]}, {C.BUSTER})
        fixed = (CardPick(K.SELECT_NP, 2), CardPick(K.SELECT_CARD, 1), CardPick(K.SELECT_CARD, 2))
        blocked = propose_cards(state, TrialProgress.begin(2, 0), fixed_prefix=fixed, avoid_mighty_chains=True)
        self.assertEqual(blocked.reason, "mighty_chain_forbidden")
        self.assertTrue(blocked.waiting)
        self.assertEqual(propose_cards(state, TrialProgress.begin(2, 0), fixed_prefix=fixed).picks, fixed)

    def test_verified_tiamat_first_replaces_external_cleanse_but_not_another_ae(self):
        candidate = self.tia()
        state = hand(nps=(np(2, "1001600"),))
        action = candidate.decide(state)
        self.assertEqual(action.picks[0], CardPick(K.SELECT_NP, 2))
        self.assertNotIn(CardPick(K.SELECT_NP, 3), action.picks)
        candidate.validate_chain(state, action.picks)
        with self.assertRaises(ValueError):
            propose_cards(hand(nps=(np(2, "1000200"),)), TrialProgress.begin(4),
                require_cleanse_before_finish=True, avoid_mighty_chains=True,
                self_cleanse_finishers=("1000200",))
        # A safe list never grants another AE the same privilege.
        candidate.config = replace(candidate.config, self_cleanse_finishers=())
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in candidate.decide(state).picks))

    def test_unavailable_tiamat_waits_without_ae_faces_and_unknown_identity_stops(self):
        candidate = self.tia()
        state = replace(hand(), cards=(face(1, C.ARTS, "304800", 1), face(2, C.ARTS),
            face(3, C.QUICK), face(4, C.BUSTER), face(5, C.BUSTER, "1001600", 2)))
        action = candidate.decide(state)
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in action.picks))
        self.assertNotIn(CardPick(K.SELECT_CARD, 5), action.picks)
        candidate.validate_chain(state, action.picks)
        with self.assertRaisesRegex(TrialStrategyError, "无法可靠确认"):
            candidate.decide(replace(state, np_cards=(np(2, "1001600", owner_confidence=Confidence(.79)),)))

    def test_tiamat_fixed_after_an_attack_does_not_replace_first_cleanse(self):
        candidate = self.tia()
        state = hand(nps=(np(2, "1001600"),))
        result = propose_cards(state, TrialProgress.begin(4),
            fixed_prefix=(CardPick(K.SELECT_CARD, 1), CardPick(K.SELECT_NP, 2)),
            require_cleanse_before_finish=True, avoid_mighty_chains=True,
            self_cleanse_finishers=("1001600",), np_info=candidate.config.np_info)
        self.assertTrue(result.waiting)
        self.assertEqual(result.picks, ())

    def test_charge_preparation_filters_full_chain_including_fixed_np_colour(self):
        candidate = decider(4, mode="charge_cleanser", preparation=(5,))
        candidate.config = replace(candidate.config, avoid_mighty_chains=True)
        state = replace(hand(nps=(np(1, "304800"),)), cards=(face(1, C.ARTS),
            face(2, C.QUICK), face(3, C.ARTS, "304800", 1),
            face(4, C.BUSTER, "1000200", 2), face(5, C.QUICK, "1000200", 2)))
        action = candidate.decide(state)
        self.assertEqual(action.picks, (CardPick(K.SELECT_NP, 1),
            CardPick(K.SELECT_CARD, 3), CardPick(K.SELECT_CARD, 1)))
        # Strict fixed cards cannot be silently changed to evade the ban.
        candidate.config = replace(candidate.config, final_bar_preparation_mode="strict")
        turns = list(candidate.plan.turns)
        turns[4] = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 1),
            PlannedCard(K.SELECT_CARD, 3, C.ARTS), PlannedCard(K.SELECT_CARD, 3, C.QUICK)))
        candidate.plan = BattlePlan(tuple(turns))
        with self.assertRaisesRegex(TrialStrategyError, "三色链"):
            candidate.decide(state)

    def test_optional_waiting_cleanse_also_filters_its_np_colour(self):
        candidate = self.tia()
        candidate.config = replace(candidate.config, use_cleanse_np_while_waiting=True)
        state = replace(hand(nps=(np(3, "504500"),)), cards=(face(1, C.BUSTER, "304800", 1),
            face(2, C.QUICK), face(3, C.BUSTER),
            face(4, C.ARTS, "1001600", 2), face(5, C.QUICK, "1001600", 2)))
        action = candidate.decide(state)
        self.assertEqual(action.picks[0], CardPick(K.SELECT_NP, 3))
        self.assertEqual(set(action.picks[1:]), {CardPick(K.SELECT_CARD, 1), CardPick(K.SELECT_CARD, 3)})
        candidate.validate_chain(state, action.picks)

    def test_execute_selection_guard_catches_mighty_before_any_input(self):
        candidate = self.tia()
        state = hand((C.BUSTER, C.ARTS, C.QUICK, C.ARTS, C.ARTS))
        context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
        run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile())
        run._selection_cards = state.cards
        run._trial_selection_state = state
        action = BattleAction(None, tuple(CardPick(K.SELECT_CARD, slot) for slot in (1, 2, 3)))
        with patch.object(AutoBattleRuntime, "_execute_selection") as execute:
            with self.assertRaisesRegex(TrialStrategyError, "三色链"):
                run._execute_selection(action)
        execute.assert_not_called()
        self.assertIsNone(run._trial_pending)


class TrialRepeatReadySkillTests(unittest.TestCase):
    def test_repeat_whitelist_is_optional_and_requires_phase_mode(self):
        raw = {"enabled": True, "phasePlanTurns": [list(group) for group in MAPPING],
               "repeatReadySkillServantIds": ["1001600", "504500", "304800"]}
        config = trial_config_from_source(NODE, {"maaTrialStrategy": raw})
        self.assertEqual(config.repeat_ready_skill_servant_ids, ("1001600", "504500", "304800"))
        with self.assertRaises(TrialStrategyError):
            trial_config_from_source(NODE, {"maaTrialStrategy": {**raw, "phasePlanTurns": []}})
        for ids in (None, "1001600", [1001600], ["0"], ["1001600", "1001600"]):
            with self.subTest(ids=ids), self.assertRaises(TrialStrategyError):
                trial_config_from_source(NODE, {"maaTrialStrategy": {**raw, "repeatReadySkillServantIds": ids}})

    def test_repeat_only_real_ready_skills_of_reliable_whitelist_targets_self(self):
        candidate = decider(3)
        candidate.config = replace(candidate.config, repeat_ready_skill_servant_ids=("1001600", "504500"))
        true, false, unknown = SkillState(True, Confidence(1)), SkillState(False, Confidence(1)), SkillState(None, Confidence(1))
        state = replace(hand(), scene=Scene.MAIN_BATTLE, servants=(
            ServantState(1, (true, false, unknown), Confidence(1), "1001600"),
            ServantState(2, (false, SkillState(True, Confidence(.79)), true), Confidence(1), "504500"),
            ServantState(3, (true, true, true), Confidence(1), "4000100")))
        candidate.decide(state)  # First visit still uses the fixed row, not automatic skills.
        repeated = candidate.decide(state)
        self.assertEqual([(skill.servant_slot, skill.skill_index, skill.target_ally)
                          for skill in repeated.servant_skills], [(1, 1, 1), (2, 3, 2)])
        self.assertEqual(repeated.master_skills, ())
        candidate.config = replace(candidate.config, repeat_ready_skill_servant_ids=("4000100",))
        only_u = candidate.decide(state)
        self.assertEqual([(skill.servant_slot, skill.skill_index) for skill in only_u.servant_skills], [(3, 1), (3, 2)])

    def test_unknown_identity_and_duplicate_current_slot_do_not_auto_cast(self):
        candidate = decider(3)
        candidate.config = replace(candidate.config, repeat_ready_skill_servant_ids=("1001600",))
        candidate._started_skill_turns.add(3)
        skills = (SkillState(True, Confidence(1)),) * 3
        state = replace(hand(), scene=Scene.MAIN_BATTLE,
            servants=(ServantState(1, skills, Confidence(.79), "1001600"),))
        self.assertEqual(candidate.decide(state).servant_skills, ())
        state = replace(state, servants=(ServantState(1, skills, Confidence(1), "1001600"),
            ServantState(1, skills, Confidence(1), "1001600")))
        self.assertEqual(candidate.decide(state).servant_skills, ())

    def test_repeated_rows_disable_plan_availability_patching_but_new_phase_restores_it(self):
        candidate = decider(1)
        candidate.config = replace(candidate.config, repeat_ready_skill_servant_ids=("504500",))
        context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
        run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile())
        self.assertTrue(run._has_active_plan())
        candidate._started_skill_turns.add(0)
        self.assertTrue(run._has_active_plan())  # Executing a fixed row remains planned.
        state = hand()
        action = candidate.decide(state)
        confirm(candidate, state, action)
        skills = (SkillState(False, Confidence(1)),) * 3
        main = replace(state, scene=Scene.MAIN_BATTLE,
            servants=(ServantState(3, skills, Confidence(1), "504500"),))
        observed_plan_flags = []
        def parent_observe(**_):
            observed_plan_flags.append(run._has_active_plan())
            return main
        with patch.object(AutoBattleRuntime, "_observe", side_effect=parent_observe), \
             patch.object(run, "_read_phase", return_value=2):
            observed = run._observe()
        self.assertEqual(observed_plan_flags, [False])
        self.assertEqual(run._turn_index, 1)
        self.assertTrue(run._has_active_plan())
        self.assertTrue(all(skill.available for skill in observed.servants[0].skills))

    def test_first_fixed_master_sequence_and_swap_execute_with_auto_master_disabled(self):
        swap = OrderChangeAction(3, 4)
        master_swap, master_charge = MasterSkillAction(3), MasterSkillAction(2, target_ally=3)
        castoria = ServantSkillAction(3, 1, actor=ActorBinding(3, "504500"))
        turn = TurnPlan(servant_skills=(castoria,), master_skills=(master_swap, master_charge),
            order_change=swap, skill_sequence=(master_swap, swap, castoria, master_charge))
        candidate = decider(2, turns=(TurnPlan(), turn, TurnPlan(), TurnPlan(), TurnPlan(), TurnPlan()))
        candidate.config = replace(candidate.config, repeat_ready_skill_servant_ids=("1001600", "504500"))
        context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
        run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile(),
            roster_servant_ids=("304800", "1001600", "4000100", "504500", None, None))
        run._turn_index = 1
        self.assertFalse(run.battle_policy.skill.use_master_skills)
        action = candidate.decide(replace(hand(), scene=Scene.MAIN_BATTLE))
        self.assertIn(1, candidate._started_skill_turns)
        def cast(_kind, callback, *_args, **_kwargs):
            return callback()
        with patch.object(run, "_execute_skill_cast", side_effect=cast), \
             patch.object(run, "_wait_until", return_value=True), \
             patch.object(run, "_wait_order_change_settled", return_value=True), \
             patch.object(run.executor, "cast_master_skill", return_value=True) as masters, \
             patch.object(run.executor, "cast_servant_skill", return_value=True) as servants, \
             patch.object(run.executor, "order_change", return_value=True) as order:
            self.assertTrue(run._execute_skills(action))
            self.assertEqual([call.args[0] for call in masters.call_args_list], [3, 2])
            servants.assert_called_once_with(3, 1)
            order.assert_called_once_with(3, 4)
            self.assertEqual(run.roster_servant_ids[:4], ("304800", "1001600", "504500", "4000100"))
            # The next main frame does not replay either fixed Master action.
            repeat_action = candidate.decide(replace(hand(), scene=Scene.MAIN_BATTLE))
            self.assertTrue(run._execute_skills(repeat_action))
            self.assertEqual(masters.call_count, 2)
            self.assertEqual(order.call_count, 1)


class TrialFinisherFaceTests(unittest.TestCase):
    def test_optional_phase_configuration_requires_stage_mapping(self):
        raw = {"enabled": True, "phasePlanTurns": [list(group) for group in MAPPING],
               "finisherFacePhases": [2, 3, 4]}
        config = trial_config_from_source(NODE, {"maaTrialStrategy": raw})
        self.assertEqual(config.finisher_face_phases, (2, 3, 4))
        for changes in ({"phasePlanTurns": []}, {"finisherFacePhases": None},
                        {"finisherFacePhases": [True]}, {"finisherFacePhases": [0]},
                        {"finisherFacePhases": [3, 3]}):
            with self.subTest(changes=changes), self.assertRaises(TrialStrategyError):
                trial_config_from_source(NODE, {"maaTrialStrategy": {**raw, **changes}})

    def test_pure_fixed_illegal_face_stops_and_auto_unavailable_holds_np(self):
        state = replace(hand(nps=(np(2, "1000200"),)), cards=(face(1, C.ARTS, "304800", 1),
            face(2, C.QUICK), face(3, C.ARTS), face(4, C.QUICK, "1000200", 2), face(5, C.BUSTER)))
        fixed = (CardPick(K.SELECT_NP, 2), CardPick(K.SELECT_CARD, 1))
        blocked = propose_cards(state, TrialProgress.begin(3, 0), fixed_prefix=fixed, np_info=INFO,
                                allowed_face_servant_ids=("1000200",))
        self.assertEqual(blocked.reason, "fixed_finisher_face_forbidden")
        self.assertEqual(blocked.picks, ())
        unavailable = propose_cards(state, TrialProgress.begin(3, 0), fixed_prefix=(fixed[0],),
                                    np_info=INFO, allowed_face_servant_ids=("1000200",))
        self.assertEqual(unavailable.reason, "finisher_faces_unavailable")
        self.assertTrue(unavailable.holds_attack_np)
        # Default None keeps the prior unrestricted face selection.
        self.assertFalse(propose_cards(state, TrialProgress.begin(3, 0), fixed_prefix=(fixed[0],), np_info=INFO).waiting)

    def test_castoria_then_melt_np_and_own_quick_face_is_valid_rider_break(self):
        state = replace(hand(nps=(np(2, "1000200"), np(3, "504500"))),
            cards=(face(1, C.QUICK), face(2, C.ARTS), face(3, C.BUSTER),
                   face(4, C.QUICK, "1000200", 2), face(5, C.ARTS, "304800", 1)))
        result = propose_cards(state, TrialProgress.begin(3), np_info=INFO,
            fixed_prefix=(CardPick(K.SELECT_NP, 3), CardPick(K.SELECT_NP, 2)),
            allowed_face_servant_ids=("1000200",))
        self.assertEqual(result.picks, (CardPick(K.SELECT_NP, 3), CardPick(K.SELECT_NP, 2), CardPick(K.SELECT_CARD, 4)))
        self.assertEqual(result.projected_color_attacks, 2)

    def test_runtime_uses_real_classes_and_not_editable_np_role_labels(self):
        state = replace(hand(), cards=(face(1, C.ARTS), face(2, C.QUICK, "600100", 2),
            face(3, C.BUSTER, "400100", 2), face(4, C.ARTS, "1000200", 2),
            face(5, C.BUSTER, "304800", 1)))
        for phase, expected in ((2, {"504500", "1000200"}), (3, {"600100", "1000200"}),
                                (4, {"400100", "1000200"})):
            candidate = decider(phase)
            candidate.config = replace(candidate.config, finisher_face_phases=(2, 3, 4))
            with self.subTest(phase=phase):
                self.assertEqual(set(candidate._allowed_finisher_face_ids(state)), expected)

    def test_bound_illegal_fixed_actor_is_rejected_before_skills_even_if_not_drawn(self):
        candidate = decider(3)
        candidate.config = replace(candidate.config, finisher_face_phases=(3,))
        turns = list(candidate.plan.turns)
        turns[3] = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 2, actor=ActorBinding(1, "1000200")),
            PlannedCard(K.SELECT_CARD, 1, C.BUSTER, ActorBinding(0, "304800")), PlannedCard(K.SELECT_CARD, 0)))
        candidate.plan = BattlePlan(tuple(turns))
        with patch.object(candidate.inner, "decide") as inner:
            with self.assertRaisesRegex(TrialStrategyError, "错误职阶"):
                candidate.decide(replace(hand(), scene=Scene.MAIN_BATTLE))
        inner.assert_not_called()

    def test_auto_class_wait_can_use_support_protection_without_consuming_main_np(self):
        candidate = decider(3)
        candidate.config = replace(candidate.config, finisher_face_phases=(3,), use_cleanse_np_while_waiting=True)
        turns = list(candidate.plan.turns)
        turns[3] = TurnPlan(np_order=(3, 2))
        candidate.plan = BattlePlan(tuple(turns))
        state = replace(hand(nps=(np(2, "1000200"), np(3, "504500"))), cards=(face(1, C.QUICK),
            face(2, C.ARTS), face(3, C.ARTS, "304800", 1), face(4, C.QUICK, "304800", 1),
            face(5, C.BUSTER, "4000100", 3)))
        action = candidate.decide(state)
        self.assertEqual(action.picks[0], CardPick(K.SELECT_NP, 3))
        self.assertNotIn(CardPick(K.SELECT_NP, 2), action.picks)
        confirm(candidate, state, action)
        self.assertEqual(candidate.plan_turn_for_phase(99), 3)
        self.assertEqual(candidate.progress.remaining_color_attacks, 0)
        self.assertEqual(candidate._wait_counts, {3: 1})

    def test_last_input_guard_rejects_wrong_face_class_even_without_mighty_filter(self):
        candidate = decider(3)
        candidate.config = replace(candidate.config, finisher_face_phases=(3,))
        state = hand(nps=(np(2, "1000200"),))
        context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
        run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile())
        run._selection_cards, run._trial_selection_state = state.cards, state
        action = BattleAction(None, (CardPick(K.SELECT_NP, 2), CardPick(K.SELECT_CARD, 1), CardPick(K.SELECT_CARD, 2)))
        with patch.object(AutoBattleRuntime, "_execute_selection") as execute:
            with self.assertRaisesRegex(TrialStrategyError, "错误职阶"):
                run._execute_selection(action)
        execute.assert_not_called()


class TrialAutoMightyWaitTests(unittest.TestCase):
    def real_case(self, *, max_wait=8):
        fixture = json.loads((ROOT / "tests/fixtures/battle_trial/20261114_t7_auto_mighty_wait.json").read_text("utf-8"))
        turn = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 1, actor=ActorBinding(3, "504500")),
            PlannedCard(K.SELECT_NP, 2, actor=ActorBinding(1, "1000200")), PlannedCard(K.SELECT_CARD, 0)))
        candidate = decider(4, turns=(TurnPlan(),) * 4 + (turn,), max_wait=max_wait)
        candidate.config = replace(candidate.config, phase_plan_turns=((1,), (2, 3), (4,), (5,)),
            avoid_mighty_chains=True, finisher_face_phases=(2, 4),
            charge_priority_servant_ids=("504500", "1000200"),
            np_info=(*INFO, NpInfo("4000100", C.BUSTER, NpRole.PREPARATION, True)))
        cards = tuple(face(item["slot"], C(item["color"]), item["servantId"], item["ownerSlot"],
                           confidence=Confidence(item["confidence"]), owner_confidence=Confidence(item["confidence"]))
                      for item in fixture["cards"])
        nps = tuple(np(item["slot"], item["servantId"], confidence=Confidence(item["confidence"]))
                    for item in fixture["npCards"])
        state = replace(hand(), scene_confidence=Confidence(fixture["sceneConfidence"]), cards=cards, np_cards=nps)
        return fixture, candidate, state

    def test_real_t7_auto_conflict_waits_with_safe_aaa_and_holds_all_attack_nps(self):
        fixture, candidate, state = self.real_case()
        action = candidate.decide(state)
        self.assertEqual([pick.slot for pick in action.picks], fixture["expectedWaitFaceSlots"])
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in action.picks))
        self.assertNotIn(CardPick(K.SELECT_CARD, 2), action.picks)  # AE face stays excluded.
        self.assertIn("step_wait:trial_mighty_chain_forbidden", action.rationale_tag)
        self.assertEqual(candidate._wait_counts, {})
        confirm(candidate, state, action)
        self.assertEqual(candidate._wait_counts, {4: 1})
        self.assertEqual(candidate.plan_turn_for_phase(99), 4)

    def test_fully_fixed_mighty_still_stops_without_turning_into_a_wait(self):
        _, candidate, state = self.real_case()
        turns = list(candidate.plan.turns)
        fixed = turns[4].card_sequence[:2] + (PlannedCard(K.SELECT_CARD, 2, C.BUSTER, ActorBinding(1, "1000200")),)
        turns[4] = TurnPlan(card_sequence=fixed)
        candidate.plan = BattlePlan(tuple(turns))
        with self.assertRaises(TrialStrategyError) as error:
            candidate.decide(state)
        self.assertEqual(error.exception.code, "trial_mighty_chain_forbidden")
        self.assertFalse(error.exception.allow_auto_wait)
        self.assertEqual(candidate._wait_counts, {})

    def test_implicit_legacy_hole_is_not_treated_as_an_explicit_auto_request(self):
        _, candidate, state = self.real_case()
        turns = list(candidate.plan.turns)
        turns[4] = TurnPlan(np_order=(1, 2))
        candidate.plan = BattlePlan(tuple(turns))
        with self.assertRaises(TrialStrategyError) as error:
            candidate.decide(state)
        self.assertEqual(error.exception.code, "trial_mighty_chain_forbidden")
        self.assertFalse(error.exception.allow_auto_wait)

    def test_auto_mighty_wait_obeys_unchanged_wait_budget(self):
        _, candidate, state = self.real_case(max_wait=1)
        action = candidate.decide(state)
        candidate.decide(state)  # A repeated proposal cannot consume the budget.
        self.assertEqual(candidate._wait_counts, {})
        confirm(candidate, state, action)
        with self.assertRaises(TrialStrategyError) as error:
            candidate.decide(state)
        self.assertEqual(error.exception.code, "trial_step_wait_exhausted")

    def test_auto_candidate_wait_never_bypasses_last_chain_validation(self):
        _, candidate, state = self.real_case()
        forbidden = BattleAction(None, (CardPick(K.SELECT_NP, 1), CardPick(K.SELECT_NP, 2), CardPick(K.SELECT_CARD, 2)))
        with patch.object(candidate, "_decide", return_value=forbidden):
            with self.assertRaises(TrialStrategyError) as error:
                candidate.decide(state)
        self.assertEqual(error.exception.code, "trial_mighty_chain_forbidden")
        self.assertFalse(error.exception.allow_auto_wait)

    def test_auto_conflict_without_three_safe_non_ae_faces_still_stops(self):
        _, candidate, state = self.real_case()
        state = replace(state, cards=(*state.cards[:3],
            face(4, C.ARTS, "1000200", 2, can_attack=False),
            face(5, C.QUICK, "1000200", 2, can_attack=False)))
        with self.assertRaises(TrialStrategyError) as error:
            candidate.decide(state)
        self.assertEqual(error.exception.code, "trial_cleanser_charge_cards_missing")


class TrialPhaseRuntimeTests(unittest.TestCase):
    def test_positive_replacement_of_seen_cleanser_stops_before_following_skills(self):
        candidate = decider(3)
        context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
        run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile(),
            roster_servant_ids=("304800", "1000200", "504500", "4000100", None, None))
        skills = (SkillState(True, Confidence(1)),) * 3
        def main(sid, confidence=1):
            return replace(hand(), scene=Scene.MAIN_BATTLE,
                servants=(ServantState(3, skills, Confidence(confidence), sid),))
        run._check_cleanser_survival(main("504500"))
        # An ordinary five-card hand without Castoria is never retirement proof.
        run._check_cleanser_survival(hand())
        run.roster_servant_ids = ("304800", "1000200", "4000100", None, None, None)
        run._check_cleanser_survival(main("4000100", .79))
        with self.assertRaisesRegex(TrialStrategyError, "后续技能前停止"):
            run._check_cleanser_survival(main("4000100"))

    def test_explicit_swap_of_cleanser_to_reserve_does_not_count_as_retirement(self):
        candidate = decider(3)
        context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
        run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile(),
            roster_servant_ids=("304800", "1000200", "504500", "4000100", None, None))
        run._trial_seen_cleanser_slots = {"504500": 3}
        run.roster_servant_ids = ("304800", "1000200", "4000100", "504500", None, None)
        skills = (SkillState(True, Confidence(1)),) * 3
        run._check_cleanser_survival(replace(hand(), scene=Scene.MAIN_BATTLE,
            servants=(ServantState(3, skills, Confidence(1), "4000100"),)))

    def test_opening_hand_wait_does_not_consume_phase_step_or_its_separate_wait_budget(self):
        candidate = decider()
        candidate.config = replace(candidate.config, phase_plan_turns=((1, 2), (3,), (4,), (5, 6)))
        candidate.plan_turn_for_phase(0)
        state = hand()
        context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
        run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile())
        action = run._opening_wait_action(state, "304800", 1)
        confirm(candidate, state, action)
        self.assertEqual(candidate.plan_turn_for_phase(99), 0)
        self.assertEqual(candidate._wait_counts, {})
        self.assertEqual(candidate._started_skill_turns, set())

    def test_parent_loop_uses_phase_rows_instead_of_its_default_turn_counter(self):
        candidate = decider(2)
        first = hand(nps=(np(3, "504500"),))
        second = hand(nps=(np(2, "1000200"),))
        context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
        run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile())
        run._post_attack_returned_to_map = False
        scenes = [replace(first, scene=Scene.MAIN_BATTLE), first,
                  replace(second, scene=Scene.MAIN_BATTLE), second,
                  replace(second, scene=Scene.VICTORY)]
        def observe(**_):
            state = scenes.pop(0)
            if state.scene is Scene.COMMAND_SELECTION:
                run._trial_selection_state = state
            return state
        with patch.object(run, "_observe", side_effect=observe), \
             patch.object(run, "_check_card_actor_bindings", return_value=True), \
             patch.object(run, "_execute_skills", return_value=True) as skills, \
             patch.object(run, "_wait_until", return_value=True), \
             patch.object(run.executor, "open_command_cards", return_value=True), \
             patch.object(AutoBattleRuntime, "_execute_selection", return_value=True), \
             patch.object(AutoBattleRuntime, "_wait_turn_settled", return_value=True):
            result = run.run()
        self.assertTrue(result.ok)
        self.assertEqual(result.turns, 2)
        self.assertEqual([call.args[0].rationale_tag for call in skills.call_args_list],
                         ["v2:turn1_skills", "v2:turn2_skills"])

    def test_stage_and_wait_commit_still_require_all_inputs_and_animation_settlement(self):
        for clicked, settled in ((False, True), (True, False), (True, True)):
            with self.subTest(clicked=clicked, settled=settled):
                candidate = decider(2)
                state = hand(nps=(np(3, "504500"),))
                context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
                run = HellishTrialRuntime(context, NS(cached_image=object()), candidate, StrategyProfile())
                run._selection_cards = state.cards
                run._selection_np_cards = state.np_cards
                run._trial_selection_state = state
                action = candidate.decide(state)
                with patch.object(AutoBattleRuntime, "_execute_selection", return_value=clicked), \
                     patch.object(AutoBattleRuntime, "_wait_turn_settled", return_value=settled):
                    if run._execute_selection(action):
                        run._wait_turn_settled()
                self.assertEqual(candidate.plan_turn_for_phase(99), 2 if clicked and settled else 1)

    def test_base_runtime_hooks_keep_ordinary_turn_semantics(self):
        run = object.__new__(AutoBattleRuntime)
        self.assertEqual(run._plan_turn_after_observation(4, hand()), 4)
        self.assertEqual(run._plan_turn_after_settlement(4), 5)


if __name__ == "__main__":
    unittest.main()
