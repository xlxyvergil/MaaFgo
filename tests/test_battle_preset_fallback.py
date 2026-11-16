"""Story/trial abandon only an interrupted preset, once per current battle."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from formation_test_support import battle_action as entry
from battle.core.enums import CardColor as C, PrimitiveKind as K, Scene
from battle.core.models import (ActorBinding, BattlePlan, BattleState, CommandCard,
                               Confidence, NpCard, ServantSkillAction, ServantState,
                               SkillState, TurnPlan)
from battle.core.policy import StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime, BattleResult


def context(flag=True):
    nodes = {'battle': {'attach': {'native_battle_preset_fallback': flag}}}
    return NS(get_node_data=lambda name: nodes.get(name, {}),
              tasker=NS(stopping=False, controller=object()))


class PresetFallbackHandoffTests(unittest.TestCase):
    def invoke(self, *, reason='skill_execution_failed', ok=False, enabled=True,
               preset=True, stopping=False, recovered=True, used=3, maximum=40,
               pending=False, resumed=None):
        ctx = context()
        ctx.tasker.stopping = stopping
        old = NS(candidate_servant_ids=('504500', '1000200'),
                 roster_servant_ids=('1000200', None, '4000100', None, None, None),
                 _initial_servant_ids=('304800', '1000200', '4000100', '504500', None, None),
                 _selection_in_flight=pending)
        original = BattleResult(ok, reason, used)
        with patch.object(entry, 'recover_for_default_battle', return_value=recovered) as recover, \
             patch.object(entry, 'AutoBattleRuntime') as factory:
            factory.return_value.run.return_value = resumed or BattleResult.success(2)
            result = entry._continue_without_preset(
                ctx, ctx.tasker.controller, old, original, StrategyProfile(max_turns=maximum),
                enabled=enabled, preset_active=preset)
        return result, original, factory, recover, old, ctx

    def test_fresh_default_drops_plan_positions_and_uses_only_unordered_identity_candidates(self):
        result, _original, factory, recover, old, ctx = self.invoke()
        self.assertEqual(result, BattleResult.success(5))
        recover.assert_called_once_with(old)
        ctx_arg, controller, decider, profile, policy = factory.call_args.args
        self.assertIs(ctx_arg, ctx)
        self.assertIs(controller, ctx.tasker.controller)
        self.assertIsNone(decider.plan)
        self.assertTrue(policy.skill.auto_servant_skills)
        self.assertTrue(policy.card.np_first)
        self.assertFalse(policy.skill.use_master_skills)
        self.assertFalse(policy.card.native_strategy.opening_hand.enabled)
        self.assertEqual(policy.card.native_strategy.preferred_servant_ids, ())
        self.assertEqual(policy.card.native_strategy.np_colors_by_servant_id, ())
        self.assertEqual(profile.max_turns, 37)
        self.assertIsNone(factory.call_args.kwargs['initial_formation'])
        self.assertEqual(factory.call_args.kwargs['roster_servant_ids'], (None,) * 6)
        self.assertEqual(set(factory.return_value.candidate_servant_ids),
                         {'304800', '1000200', '4000100', '504500'})

    def test_normal_tasks_default_only_and_stop_requests_never_clean_or_restart(self):
        for changes in ({'enabled': False}, {'preset': False}, {'stopping': True},
                        {'ok': True, 'reason': 'victory'}, {'ok': True, 'reason': 'defeat'}):
            with self.subTest(changes=changes):
                result, original, factory, recover, *_ = self.invoke(**changes)
                self.assertIs(result, original)
                factory.assert_not_called()
                recover.assert_not_called()

    def test_only_preset_execution_errors_are_recoverable(self):
        for reason in ('skill_execution_failed', 'card_actor_identity_mismatch',
                       'unexpected_dialog', 'unexpected_order_change_scene',
                       'open_cards_failed', 'open_cards_confirm_failed',
                       'selection_confirm_failed', 'card_selection_not_submitted',
                       'stuck_unknown_scene',
                       'opening_hand_roster_changed_or_unknown',
                       'invalid_main_action:actor_missing', 'invalid_card_action:bad_slot',
                       'trial_required_cleanser_replaced', 'trial_wait_exhausted'):
            with self.subTest(reason=reason):
                result, _original, factory, recover, *_ = self.invoke(reason=reason)
                self.assertTrue(result.ok)
                recover.assert_called_once()
                factory.assert_called_once()
        for reason in ('max_turns_exceeded', 'stuck_after_attack',
                       'post_attack_story_failed', 'np_glare_unresolved',
                       'controller_disconnected', 'chaldea_plan_failed', 'stopped', ''):
            with self.subTest(reason=reason):
                result, original, factory, recover, *_ = self.invoke(reason=reason)
                self.assertIs(result, original)
                factory.assert_not_called()
                recover.assert_not_called()

    def test_recovery_failure_stops_and_default_failure_is_not_recursively_retried(self):
        result, _original, factory, recover, *_ = self.invoke(recovered=False)
        self.assertEqual(result.reason, 'preset_fallback_recovery_failed')
        self.assertFalse(result.ok)
        recover.assert_called_once()
        factory.assert_not_called()
        result, _original, factory, recover, *_ = self.invoke(
            resumed=BattleResult.fail('skill_execution_failed', 1))
        self.assertEqual(result, BattleResult.fail('skill_execution_failed', 4))
        recover.assert_called_once()
        factory.assert_called_once()
        factory.return_value.run.assert_called_once()

    def test_existing_turn_budget_is_preserved_and_unknown_submission_is_reserved(self):
        result, _original, factory, *_ = self.invoke(used=38, pending=True,
                                                   resumed=BattleResult.success(1))
        self.assertEqual(factory.call_args.args[3].max_turns, 1)
        self.assertEqual(result.turns, 39)  # Only confirmed completions are reported.
        for used, maximum, pending in ((40, 40, False), (39, 40, True), (1, 1, False)):
            with self.subTest(used=used, maximum=maximum, pending=pending):
                result, _original, factory, recover, *_ = self.invoke(
                    used=used, maximum=maximum, pending=pending)
                self.assertEqual(result.reason, 'max_turns_exceeded')
                factory.assert_not_called()
                recover.assert_not_called()
        result, _original, factory, *_ = self.invoke(used=2, maximum=100)
        self.assertEqual(factory.call_args.args[3].max_turns, 40)

    def test_stop_during_cleanup_never_constructs_default_runtime(self):
        ctx = context()
        old = NS(_selection_in_flight=False)
        original = BattleResult.fail('skill_execution_failed', 1)
        def stop(_runtime):
            ctx.tasker.stopping = True
            return True
        with patch.object(entry, 'recover_for_default_battle', side_effect=stop), \
             patch.object(entry, 'AutoBattleRuntime') as factory:
            result = entry._continue_without_preset(ctx, ctx.tasker.controller, old, original,
                StrategyProfile(max_turns=40), enabled=True, preset_active=True)
        self.assertIs(result, original)
        factory.assert_not_called()


class PresetFallbackActionIntegrationTests(unittest.TestCase):
    def run_entry(self, flag, *, fail_default=False, param=None, first_failure='skill'):
        ctx = context(flag)
        made = []
        servants = tuple(ServantState(slot=slot, confidence=Confidence(1), skills=(
            SkillState(True, Confidence(1)), SkillState(False, Confidence(1)),
            SkillState(False, Confidence(1)))) for slot in (1, 2, 3))
        main = BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), (), servants)
        cards = tuple(CommandCard(slot, color, 1, Confidence(1), Confidence(1), '100100')
                      for slot, color in enumerate((C.BUSTER, C.ARTS, C.QUICK, C.BUSTER, C.ARTS), 1))
        hand = BattleState(Scene.COMMAND_SELECTION, Confidence(1), cards,
                          (NpCard(3, Confidence(1), owner_servant_id='4000100'),), ())
        victory = BattleState(Scene.VICTORY, Confidence(1), (), (), ())
        def create(*args, **kwargs):
            runtime = AutoBattleRuntime(*args, **kwargs)
            first = not made
            frames = ([main, hand, main] if first_failure == 'after_plan' else
                      [main, hand] if first_failure == 'selection' else [main]) if first else (
                [main] if fail_default else [main, hand, victory])
            runtime._observe = Mock(side_effect=frames)
            runtime._execute_skills = Mock(return_value=(not first or first_failure == 'selection') and not fail_default)
            if first and first_failure == 'actor':
                runtime._execute_skills = AutoBattleRuntime._execute_skills.__get__(runtime)
                runtime._retired_instances.add(0)
            elif first and first_failure == 'after_plan':
                runtime._execute_skills = Mock(side_effect=[True, False])
            runtime.executor.open_command_cards = Mock(return_value=True)
            runtime._wait_until = Mock(return_value=True)
            runtime._execute_selection = Mock(return_value=not first or first_failure == 'after_plan')
            runtime._wait_turn_settled = Mock(return_value=True)
            runtime._post_attack_returned_to_map = False
            made.append(runtime)
            return runtime
        param = param or {'plan': {'turns': [{'servant_skills': [
            {'servant_slot': 1, 'skill_index': 1}], 'np_order': [2]}]}}
        with patch.object(entry, 'context_session', return_value=(1, None)), \
             patch.object(entry, 'AutoBattleRuntime', side_effect=create), \
             patch.object(entry, 'recover_for_default_battle', return_value=True) as cleanup, \
             patch.object(entry, 'inspect_victory_settlement') as settle:
            result = entry.AutoBattleAction().run(ctx, NS(node_name='battle',
                custom_action_param=json.dumps(param)))
        return result, made, cleanup, settle

    def test_real_runtime_skill_failure_recovers_to_real_default_skill_and_np_decisions(self):
        result, made, cleanup, settle = self.run_entry(True)
        self.assertTrue(result.success)
        self.assertEqual(len(made), 2)
        cleanup.assert_called_once_with(made[0])
        skills = made[1]._execute_skills.call_args.args[0]
        self.assertEqual([s.servant_slot for s in skills.servant_skills], [1, 2, 3])
        self.assertTrue(all(s.actor is None for s in skills.servant_skills))
        selected = made[1]._execute_selection.call_args.args[0]
        self.assertEqual((selected.picks[0].kind, selected.picks[0].slot), (K.SELECT_NP, 3))
        self.assertIsNone(made[1].decider.plan)
        settle.assert_called_once()

    def test_ordinary_task_and_nonboolean_scope_values_still_stop_at_original_failure(self):
        for flag in (False, None, 'true', 1, {}):
            with self.subTest(flag=flag):
                result, made, cleanup, settle = self.run_entry(flag)
                self.assertFalse(result.success)
                self.assertEqual(len(made), 1)
                cleanup.assert_not_called()
                settle.assert_not_called()

    def test_failed_default_runtime_stops_after_one_fallback(self):
        result, made, cleanup, settle = self.run_entry(True, fail_default=True)
        self.assertFalse(result.success)
        self.assertEqual(len(made), 2)
        cleanup.assert_called_once()
        settle.assert_not_called()

    def test_partial_card_failure_reserves_budget_and_default_clears_in_flight_after_settlement(self):
        result, made, cleanup, _settle = self.run_entry(True, first_failure='selection')
        self.assertTrue(result.success)
        self.assertTrue(made[0]._selection_in_flight)
        self.assertFalse(made[1]._selection_in_flight)
        self.assertEqual(made[1].profile.max_turns, 39)
        cleanup.assert_called_once_with(made[0])

    def test_real_bound_servant_retirement_triggers_fallback_before_any_skill_click(self):
        plan = BattlePlan((TurnPlan(servant_skills=(ServantSkillAction(
            1, 1, actor=ActorBinding(0, '100100')),)),))
        with patch.object(entry, '_parse_plan', return_value=plan), \
             patch('battle.execution.executor.Executor.cast_servant_skill') as click_skill:
            result, made, cleanup, _settle = self.run_entry(True, first_failure='actor')
        self.assertTrue(result.success)
        self.assertEqual(len(made), 2)
        cleanup.assert_called_once_with(made[0])
        click_skill.assert_not_called()

    def test_error_after_the_preset_has_finished_does_not_restart_default_battle(self):
        result, made, cleanup, _settle = self.run_entry(True, first_failure='after_plan')
        self.assertFalse(result.success)
        self.assertEqual(len(made), 1)
        self.assertFalse(made[0]._has_active_plan())
        cleanup.assert_not_called()

    def test_trial_guard_failure_removes_the_entire_trial_decider(self):
        share = json.loads((ROOT / 'assets/resource/base/battle_presets/地狱的试炼_202611_天魔轰临_阶段续航.json').read_text('utf-8'))
        nodes = {'battle': {'attach': {'native_battle_preset_fallback': True,
                                      'native_battle_trial_profile': 'hellish_202611'}}}
        ctx = context()
        ctx.get_node_data = lambda name: nodes.get(name, {})
        with patch.object(entry, 'context_session', return_value=(1, None)), \
             patch.object(entry, 'HellishTrialRuntime') as trial, \
             patch.object(entry, 'AutoBattleRuntime') as default, \
             patch.object(entry, 'recover_for_default_battle', return_value=True) as cleanup, \
             patch.object(entry, 'inspect_victory_settlement'):
            trial.return_value.run.return_value = BattleResult.fail('trial_required_cleanser_replaced', 4)
            default.return_value.run.return_value = BattleResult.success(2)
            result = entry.AutoBattleAction().run(ctx, NS(node_name='battle',
                custom_action_param=json.dumps({'chaldea_share': share})))
        self.assertTrue(result.success)
        self.assertIsInstance(trial.call_args.args[2], entry.HellishTrialDecider)
        self.assertIs(type(default.call_args.args[2]), entry.RuleDecider)
        self.assertIsNone(default.call_args.args[2].plan)
        self.assertEqual(default.call_args.args[3].max_turns, 36)
        cleanup.assert_called_once_with(trial.return_value)

    def test_source_fetch_failure_never_cleans_or_enters_battle(self):
        ctx = context(True)
        with patch.object(entry, 'context_session', return_value=(1, None)), \
             patch.object(entry, 'fetch_share_data', side_effect=entry.TeamFetchError('无法读取方案')), \
             patch.object(entry, 'AutoBattleRuntime') as factory, \
             patch.object(entry, 'recover_for_default_battle') as cleanup:
            result = entry.AutoBattleAction().run(ctx, NS(node_name='battle',
                custom_action_param=json.dumps({'chaldea_import_source': 'missing.json'})))
        self.assertFalse(result.success)
        factory.assert_not_called()
        cleanup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
