"""Trial isolation, explicit phase evidence, and commit-after-settlement contract."""
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "agent"), str(ROOT / "agent/custom")]
from formation_test_support import battle_action as entry
from battle.core.decider import RuleDecider
from battle.core.enums import CardColor as C, PrimitiveKind as K, Scene
from battle.core.models import (ActorBinding, BattleAction, BattlePlan, BattleState, CardPick,
                               CommandCard, Confidence, NpCard, PlannedCard, TurnPlan)
from battle.core.policy import StrategyProfile
from battle.runtime.hellish_trial import (HellishTrialDecider, HellishTrialRuntime,
    TrialStrategyConfig, TrialStrategyError, trial_config_from_source, PHASE_NODE)
from battle.runtime.runtime import AutoBattleRuntime
from battle.strategies.hellish_trial import DEFAULT_NP_INFO, NpInfo, NpRole, TrialProgress


TRIAL_NODE = {"attach": {"native_battle_trial_profile": "hellish_202611"}}


def snapshot(colors=(C.BUSTER, C.BUSTER, C.ARTS, C.QUICK, C.ARTS), nps=()):
    return BattleState(Scene.COMMAND_SELECTION, Confidence(1),
        tuple(CommandCard(i, color, 2, Confidence(1), Confidence(1), "1002200")
              for i, color in enumerate(colors, 1)), tuple(nps), ())


def np(slot, sid, **kw):
    return NpCard(slot, Confidence(1), owner_servant_id=sid,
                  owner_confidence=Confidence(1), **kw)


def candidate(phase=1, *, turn=None, remaining=None, cleanse=False):
    plan = BattlePlan((turn,)) if turn is not None else None
    config = TrialStrategyConfig(TrialProgress.begin(phase, remaining), cleanse)
    decider = HellishTrialDecider(RuleDecider(plan=plan), config)
    decider.observe_phase(phase)
    return decider


def runtime(decider=None, state=None):
    decider = decider or candidate()
    context = NS(run_recognition=Mock(), tasker=NS(stopping=False))
    controller = NS(cached_image=object())
    result = HellishTrialRuntime(context, controller, decider, StrategyProfile())
    state = state or snapshot()
    result._selection_cards = state.cards
    result._selection_np_cards = state.np_cards
    result._trial_selection_state = state
    return result


class TrialConfigTests(unittest.TestCase):
    def test_final_preparation_turns_are_optional_strict_one_based_plan_numbers(self):
        config = trial_config_from_source(TRIAL_NODE, {"maaTrialStrategy": {"enabled": True}})
        self.assertEqual(config.final_bar_preparation_turns, ())
        document = {"maaTrialStrategy": {"enabled": True, "finalBarPreparationTurns": [5, 7]}}
        self.assertEqual(trial_config_from_source(TRIAL_NODE, document).final_bar_preparation_turns, (5, 7))
        self.assertEqual(document["maaTrialStrategy"]["finalBarPreparationTurns"], [5, 7])
        for turns in (None, {}, (5,), [0], [-1], [True], [1.5], ["5"], [5, 5], [[]]):
            with self.subTest(turns=turns), self.assertRaises(TrialStrategyError):
                trial_config_from_source(TRIAL_NODE, {"maaTrialStrategy": {
                    "enabled": True, "finalBarPreparationTurns": turns}})
        self.assertIsNone(trial_config_from_source({}, {"maaTrialStrategy": {
            "enabled": True, "finalBarPreparationTurns": "bad"}}))

    def test_both_gates_are_required_and_other_tasks_ignore_malformed_extension(self):
        self.assertIsNone(trial_config_from_source({}, {"maaTrialStrategy": "bad"}))
        self.assertIsNone(trial_config_from_source(TRIAL_NODE, None))
        self.assertIsNone(trial_config_from_source(TRIAL_NODE, {}))
        self.assertIsNone(trial_config_from_source(TRIAL_NODE, {"maaTrialStrategy": {"enabled": False}}))
        config = trial_config_from_source(TRIAL_NODE, {"maaTrialStrategy": {"enabled": True}})
        self.assertEqual(config.progress, TrialProgress.begin(1))
        self.assertTrue(config.require_cleanse_before_finish)

    def test_recovery_seed_and_form_dependent_np_need_explicit_metadata(self):
        config = trial_config_from_source(TRIAL_NODE, {"maaTrialStrategy": {
            "enabled": True, "phase": 3, "remainingColorAttacks": 1,
            "npMetadata": [{"servantId": "304800", "color": "B",
                            "role": "lancer_finisher", "damaging": True}]}})
        self.assertEqual(config.progress, TrialProgress.begin(3, 1))
        melu = next(info for info in config.np_info if info.servant_id == "304800")
        self.assertEqual((melu.color, melu.role), (C.BUSTER, NpRole.LANCER))
        self.assertFalse(any(info.servant_id == "304800" for info in TrialStrategyConfig(TrialProgress.begin(1)).np_info))

    def test_bad_types_and_unknown_np_color_do_not_silently_default(self):
        for value in ({"enabled": "true"}, {"enabled": True, "phase": True},
                      {"enabled": True, "phase": 4, "remainingColorAttacks": 1},
                      {"enabled": True, "requireCleanseBeforeFinish": "false"},
                      {"enabled": True, "npMetadata": [{"servantId": "304800", "role": "lancer_finisher", "damaging": True}]}):
            with self.subTest(value=value), self.assertRaises(TrialStrategyError):
                trial_config_from_source(TRIAL_NODE, {"maaTrialStrategy": value})


class TrialDeciderTests(unittest.TestCase):
    def test_fixed_np_missing_or_sealed_stops_before_legacy_face_replacement(self):
        decider = candidate(turn=TurnPlan(np_order=(1,)))
        for state in (snapshot(), snapshot(nps=[np(1, "305300", available=False)])):
            with self.subTest(nps=state.np_cards), self.assertRaisesRegex(TrialStrategyError, "没有可用卡面"):
                decider.decide(state)

    def test_fixed_unknown_np_identity_and_missing_metadata_stop_separately(self):
        decider = candidate(turn=TurnPlan(np_order=(1,)))
        with self.assertRaisesRegex(TrialStrategyError, "缺少已确认的宝具颜色"):
            decider.decide(snapshot(nps=[np(1, "304800")]))
        bad = replace(np(1, "305300"), owner_confidence=Confidence(.2))
        with self.assertRaisesRegex(TrialStrategyError, "无法可靠确认"):
            decider.decide(snapshot(nps=[bad]))

    def test_manual_slots_and_auto_holes_keep_absolute_positions_without_new_nps(self):
        turn = TurnPlan(card_sequence=(PlannedCard(K.SELECT_CARD, 0),
            PlannedCard(K.SELECT_CARD, 2, C.QUICK), PlannedCard(K.SELECT_CARD, 0)))
        action = candidate(3, turn=turn, remaining=1).decide(snapshot(nps=[np(2, "1002200")]))
        self.assertEqual(action.picks[1], CardPick(K.SELECT_CARD, 4))
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in action.picks))

    def test_fixed_np_wrong_phase_does_not_become_an_ordinary_card(self):
        decider = candidate(turn=TurnPlan(np_order=(2,)))
        with self.assertRaisesRegex(TrialStrategyError, "不适合当前血条"):
            decider.decide(snapshot(nps=[np(2, "1002200")]))

    def test_final_phase_respects_cleanse_order_and_never_assumes_scorch_expired(self):
        state = snapshot(nps=[np(2, "1002200"), np(3, "504500")])
        action = candidate(4, cleanse=True).decide(state)
        self.assertEqual(action.picks[:2], (CardPick(K.SELECT_NP, 3), CardPick(K.SELECT_NP, 2)))
        with self.assertRaisesRegex(TrialStrategyError, "没有识别到可用的解弱化宝具"):
            candidate(4, cleanse=True).decide(replace(state, np_cards=state.np_cards[:1]))
        turn = TurnPlan(card_sequence=(PlannedCard(K.SELECT_NP, 2), PlannedCard(K.SELECT_NP, 3)))
        with self.assertRaisesRegex(TrialStrategyError, "攻击放在了解弱化宝具前"):
            candidate(4, turn=turn, cleanse=True).decide(state)

    def test_normal_skills_remain_owned_by_inner_decider(self):
        decider = candidate(turn=TurnPlan())
        state = replace(snapshot(), scene=Scene.MAIN_BATTLE)
        expected = BattleAction(None, (), rationale_tag="original skills")
        with patch.object(decider.inner, "decide", return_value=expected) as original:
            self.assertIs(decider.decide(state, 4), expected)
        original.assert_called_once_with(state, 4)

    def test_real_class_evidence_is_not_assumed_from_turn_or_seed(self):
        decider = candidate()
        with self.assertRaises(TrialStrategyError):
            decider.observe_phase(2)  # no confirmed attack
        decider.confirm(snapshot(), (CardPick(K.SELECT_CARD, 1), CardPick(K.SELECT_CARD, 2)), 1, "first")
        decider.observe_phase(2)
        self.assertEqual(decider.progress, TrialProgress.begin(2))
        for phase in (1, 4):
            with self.assertRaises(TrialStrategyError):
                decider.observe_phase(phase)
        fresh = HellishTrialDecider(RuleDecider(), TrialStrategyConfig(TrialProgress.begin(3)))
        with self.assertRaisesRegex(TrialStrategyError, "恢复起点"):
            fresh.observe_phase(2)


class TrialRuntimeTests(unittest.TestCase):
    def test_full_parent_loop_cannot_silently_replace_missing_planned_np(self):
        run = runtime(candidate(turn=TurnPlan(np_order=(1,))))
        with patch.object(run, "_observe", return_value=snapshot()), \
             patch.object(run, "_check_card_actor_bindings", return_value=True), \
             patch.object(run, "_replace_unavailable_np_picks") as replace_missing, \
             patch.object(run, "_execute_selection") as submit:
            result = run.run()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "trial_fixed_np_unavailable")
        replace_missing.assert_not_called()
        submit.assert_not_called()

    def test_opening_wait_uses_trial_colours_and_never_spends_an_np(self):
        state = snapshot(nps=[np(1, "305300"), np(2, "1002200")])
        run = runtime(state=state)
        run._turn_index = 0
        action = run._opening_wait_action(state, "305300", 1)
        self.assertEqual(len(action.picks), 3)
        self.assertTrue(all(pick.kind is K.SELECT_CARD for pick in action.picks))
        self.assertIn(CardPick(K.SELECT_CARD, 1), action.picks)
        self.assertIn(CardPick(K.SELECT_CARD, 2), action.picks)

    def test_ocr_uses_same_cached_frame_and_rejects_unknown_low_confidence_or_ambiguity(self):
        run = runtime()
        result = NS(text="ARCHER", score=.99)
        run.ctx.run_recognition.return_value = NS(hit=True, filtered_results=[result])
        self.assertEqual(run._read_phase(), 1)
        run.ctx.run_recognition.assert_called_once_with(PHASE_NODE, run.controller.cached_image)
        for results in ([NS(text="SABER", score=.99)], [NS(text="RIDER", score=.2)],
                        [result, NS(text="CASTER", score=.99)]):
            run.ctx.run_recognition.return_value = NS(hit=True, filtered_results=results)
            with self.assertRaisesRegex(TrialStrategyError, "无法可靠识别"):
                run._read_phase()

    def test_submission_is_only_counted_after_all_three_inputs_and_successful_settlement(self):
        run = runtime()
        action = run.decider.decide(snapshot())
        with patch.object(AutoBattleRuntime, "_execute_selection", return_value=True), \
             patch.object(AutoBattleRuntime, "_wait_turn_settled", return_value=True):
            self.assertTrue(run._execute_selection(action))
            self.assertEqual(run.decider.progress.remaining_color_attacks, 2)
            self.assertTrue(run._wait_turn_settled())
        self.assertEqual(run.decider.progress.remaining_color_attacks, 0)
        self.assertEqual(run._trial_confirmed_turns, 1)

    def test_failed_click_or_unconfirmed_animation_never_counts_projected_attacks(self):
        for clicked, settled in ((False, True), (True, False)):
            run = runtime()
            action = run.decider.decide(snapshot())
            with patch.object(AutoBattleRuntime, "_execute_selection", return_value=clicked), \
                 patch.object(AutoBattleRuntime, "_wait_turn_settled", return_value=settled):
                if run._execute_selection(action):
                    self.assertFalse(run._wait_turn_settled())
            self.assertEqual(run.decider.progress.remaining_color_attacks, 2)
            self.assertEqual(run._trial_confirmed_turns, 0)

    def test_phase_seen_during_settlement_is_applied_after_old_phase_confirmation(self):
        run = runtime()
        action = run.decider.decide(snapshot())
        def settle(instance):
            with patch.object(AutoBattleRuntime, "_observe", return_value=replace(snapshot(), scene=Scene.MAIN_BATTLE)), \
                 patch.object(instance, "_read_phase", return_value=2):
                instance._observe()
            self.assertEqual(instance.decider.progress.phase, 1)
            self.assertEqual(instance.decider.progress.remaining_color_attacks, 2)
            return True
        with patch.object(AutoBattleRuntime, "_execute_selection", return_value=True), \
             patch.object(AutoBattleRuntime, "_wait_turn_settled", settle):
            run._execute_selection(action)
            run._wait_turn_settled()
        self.assertEqual(run.decider.progress, TrialProgress.begin(2))
        self.assertEqual(run._trial_confirmed_turns, 1)

    def test_padding_duplicate_or_invalid_slots_stop_before_any_input(self):
        run = runtime()
        for picks in ((CardPick(K.SELECT_CARD, 1),), (CardPick(K.SELECT_CARD, 1),) * 3,
                      (CardPick(K.SELECT_CARD, 1), CardPick(K.SELECT_CARD, 2), CardPick(K.SELECT_NP, 9))):
            with patch.object(AutoBattleRuntime, "_execute_selection") as submit:
                with self.assertRaisesRegex(TrialStrategyError, "三张有效"):
                    run._execute_selection(BattleAction(None, picks))
                submit.assert_not_called()


class FinalBarPreparationTests(unittest.TestCase):
    def state(self):
        state = snapshot(nps=[np(1, "304800"), np(2, "1000200")])
        cards = (replace(state.cards[0], owner_slot=3, owner_servant_id="504500", color=C.ARTS),
                 replace(state.cards[1], owner_slot=3, owner_servant_id="504500", color=C.QUICK),
                 replace(state.cards[2], owner_slot=3, owner_servant_id="504500", color=C.BUSTER),
                 *state.cards[3:])
        return replace(state, cards=cards)

    def planned_cards(self):
        return (PlannedCard(K.SELECT_NP, 1, actor=ActorBinding(0, "304800")),
                PlannedCard(K.SELECT_CARD, 3, C.ARTS, ActorBinding(3, "504500")),
                PlannedCard(K.SELECT_CARD, 3, C.QUICK, ActorBinding(3, "504500")))

    def decider(self, cards=None, *, phase=4, listed=(5,)):
        turn = TurnPlan(card_sequence=self.planned_cards() if cards is None else cards)
        plan = BattlePlan((TurnPlan(),) * 4 + (turn, TurnPlan(np_order=(2,))))
        info = (*DEFAULT_NP_INFO, NpInfo("304800", C.BUSTER, NpRole.LANCER, True),
                NpInfo("1000200", C.QUICK, NpRole.ALTEREGO, True))
        config = TrialStrategyConfig(TrialProgress.begin(phase), True, info, listed)
        decider = HellishTrialDecider(RuleDecider(plan=plan), config)
        decider.observe_phase(phase)
        return decider

    def test_explicit_melusine_np_and_two_caster_faces_skip_only_preparation_guards(self):
        decider = self.decider()
        state = self.state()
        state = replace(state, np_cards=(*state.np_cards, np(3, "504500")))
        action = decider.decide(state, 4)
        self.assertEqual(action.picks, (CardPick(K.SELECT_NP, 1), CardPick(K.SELECT_CARD, 1), CardPick(K.SELECT_CARD, 2)))
        self.assertEqual(action.rationale_tag, "hellish_202611:phase4:preparation_turn5")
        # Despite an available AE NP and required cleansing, no NP is inserted.
        self.assertNotIn(CardPick(K.SELECT_NP, 2), action.picks)
        self.assertNotIn(CardPick(K.SELECT_NP, 3), action.picks)
        with self.assertRaisesRegex(TrialStrategyError, "没有识别到可用的解弱化宝具"):
            decider.decide(self.state(), 5)  # following normal finisher is still guarded

    def test_three_explicit_ordinary_cards_are_allowed_without_automatic_np(self):
        cards = (PlannedCard(K.SELECT_CARD, 3, C.QUICK),
                 PlannedCard(K.SELECT_CARD, 3, C.ARTS), PlannedCard(K.SELECT_CARD, 3, C.BUSTER))
        action = self.decider(cards).decide(self.state(), 4)
        self.assertEqual(action.picks, (CardPick(K.SELECT_CARD, 2), CardPick(K.SELECT_CARD, 1), CardPick(K.SELECT_CARD, 3)))

    def test_preparation_requires_observed_final_bar_before_any_planned_skills(self):
        decider = self.decider(phase=3)
        with patch.object(decider.inner, "decide") as original:
            for state in (self.state(), replace(self.state(), scene=Scene.MAIN_BATTLE)):
                with self.assertRaisesRegex(TrialStrategyError, "没有确认第四条血"):
                    decider.decide(state, 4)
            original.assert_not_called()

    def test_preparation_requires_an_actual_plan_and_exactly_three_explicit_cards(self):
        for cards in ((), self.planned_cards()[:2], self.planned_cards() + self.planned_cards()[:1]):
            decider = self.decider(cards)
            with self.subTest(cards=cards), self.assertRaisesRegex(TrialStrategyError, "明确安排三张卡"):
                decider.decide(self.state(), 4)
        decider = self.decider()
        decider.plan = None
        with self.assertRaisesRegex(TrialStrategyError, "没有实际回合配置"):
            decider.decide(self.state(), 4)

    def test_auto_or_unspecified_colour_cards_do_not_fall_back_to_fill(self):
        for card in (PlannedCard(K.SELECT_CARD, 0), PlannedCard(K.SELECT_CARD, 3),
                     PlannedCard(K.SELECT_CARD, True, C.ARTS)):
            decider = self.decider((self.planned_cards()[0], card, self.planned_cards()[2]))
            with self.subTest(card=card), self.assertRaisesRegex(TrialStrategyError, "自动补位或未指定颜色"):
                decider.decide(self.state(), 4)

    def test_missing_sealed_or_unknown_fixed_face_stops_instead_of_substitution(self):
        for change in ({"color": C.BUSTER}, {"can_attack": False},
                       {"owner_confidence": Confidence(.79)}, {"owner_servant_id": "unknown"},
                       {"owner_servant_id": "304800"}):
            decider, state = self.decider(), self.state()
            state = replace(state, cards=(replace(state.cards[0], **change), *state.cards[1:]))
            with self.subTest(change=change), patch.object(decider.inner, "decide") as fallback:
                with self.assertRaisesRegex(TrialStrategyError, "不能用其他卡替代"):
                    decider.decide(state, 4)
                fallback.assert_not_called()

    def test_missing_or_sealed_fixed_np_retains_existing_strict_guard(self):
        for nps in ((), (np(1, "304800", available=False),)):
            with self.subTest(nps=nps), self.assertRaisesRegex(TrialStrategyError, "没有可用卡面"):
                self.decider().decide(replace(self.state(), np_cards=nps), 4)

    def test_attack_ae_np_cannot_bypass_the_normal_finishing_guard(self):
        cards = (PlannedCard(K.SELECT_NP, 2), *self.planned_cards()[1:])
        with self.assertRaisesRegex(TrialStrategyError, "安排了AE攻击宝具"):
            self.decider(cards).decide(self.state(), 4)

    def test_known_attack_ae_cannot_be_relabelled_to_bypass_the_guard(self):
        cards = (PlannedCard(K.SELECT_NP, 2), *self.planned_cards()[1:])
        for role, damaging in ((NpRole.LANCER, True), (NpRole.PREPARATION, True), (NpRole.CLEANSE, False)):
            decider = self.decider(cards)
            info = tuple(item for item in decider.config.np_info if item.servant_id != "1000200")
            decider.config = replace(decider.config, np_info=(*info, NpInfo("1000200", C.QUICK, role, damaging)))
            with self.subTest(role=role), self.assertRaisesRegex(TrialStrategyError, "安排了AE攻击宝具"):
                decider.decide(self.state(), 4)

    def test_unknown_np_class_stops_even_with_an_explicit_preparation_role(self):
        cards = (PlannedCard(K.SELECT_NP, 1), *self.planned_cards()[1:])
        decider, state = self.decider(cards), self.state()
        decider.config = replace(decider.config, np_info=(*decider.config.np_info,
            NpInfo("9999999", C.BUSTER, NpRole.PREPARATION, True)))
        state = replace(state, np_cards=(np(1, "9999999"),))
        with self.assertRaisesRegex(TrialStrategyError, "宝具职阶不在本地资料"):
            decider.decide(state, 4)

    def test_duplicate_np_and_partial_hand_cannot_submit(self):
        cards = (self.planned_cards()[0], self.planned_cards()[0], self.planned_cards()[1])
        with self.assertRaisesRegex(TrialStrategyError, "重复安排了同一张卡"):
            self.decider(cards).decide(self.state(), 4)
        state = replace(self.state(), cards=self.state().cards[:4])
        with self.assertRaisesRegex(TrialStrategyError, "完整选卡页面"):
            self.decider().decide(state, 4)

    def test_unlisted_turn_cannot_use_preparation_even_on_the_final_bar(self):
        with self.assertRaisesRegex(TrialStrategyError, "不适合当前血条"):
            self.decider(listed=()).decide(self.state(), 4)

    def test_preparation_still_uses_three_input_then_settlement_confirmation(self):
        state, decider = self.state(), self.decider()
        run = runtime(decider, state)
        action = decider.decide(state, 4)
        with patch.object(AutoBattleRuntime, "_execute_selection", return_value=True), \
             patch.object(AutoBattleRuntime, "_wait_turn_settled", return_value=True):
            run._execute_selection(action)
            self.assertEqual(decider.progress.confirmed_submission_ids, ())
            run._wait_turn_settled()
        self.assertEqual(len(decider.progress.confirmed_submission_ids), 1)
        self.assertEqual((decider.progress.phase, decider.progress.remaining_color_attacks), (4, 0))


class TrialActionIsolationTests(unittest.TestCase):
    def test_production_action_chooses_trial_only_when_both_gates_are_present(self):
        for marked, enabled, expected in ((False, True, False), (True, False, False), (True, True, True)):
            with self.subTest(marked=marked, enabled=enabled):
                ctx = NS(get_node_data=lambda name: TRIAL_NODE if marked and name == "battle" else {},
                         tasker=NS(stopping=False, controller=object()))
                param = {"chaldea_share": {"maaTrialStrategy": {"enabled": enabled}}}
                with patch.object(entry, "context_session", return_value=(1, None)), \
                     patch.object(entry, "AutoBattleRuntime") as normal, \
                     patch.object(entry, "HellishTrialRuntime") as trial:
                    for cls in (normal, trial):
                        cls.return_value.run.return_value = NS(ok=True, reason="test", turns=0)
                    result = entry.AutoBattleAction().run(ctx, NS(node_name="battle", custom_action_param=json.dumps(param)))
                self.assertTrue(result.success)
                self.assertEqual(trial.called, expected)
                self.assertEqual(normal.called, not expected)
                if expected:
                    self.assertIsInstance(trial.call_args.args[2], HellishTrialDecider)


if __name__ == "__main__":
    unittest.main()
