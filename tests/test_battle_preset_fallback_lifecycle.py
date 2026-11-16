"""Preset recovery stays local to one battle and preserves defeat handoff.

Real action parsing, Chaldea conversion, deciders and battle loops run against
controlled observations. No controller input, device or consumable is used.
"""
import copy
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
from battle.core.models import (BattleState, CommandCard, Confidence, NpCard,
                               ServantState, SkillState)
from battle.runtime.runtime import AutoBattleRuntime
from test_main_story_restricted_formation import merge, runner as story_runner


ENTRY = '原生自动战斗入口'


def source_share():
    return {
        'team': {'onFieldSvts': [{'svtId': sid} for sid in (100100, 100200, 100300)],
                 'backupSvts': []},
        'actions': [
            {'type': 'skill', 'svt': 1, 'skill': 1},
            {'type': 'attack', 'attacks': [
                {'isTD': True, 'svt': 1, 'card': -1}, {'isAuto': True}, {'isAuto': True}]},
        ],
        'maaAdvancedStrategy': {'preset': 'brave', 'autoServantSkills': False, 'maxTurns': 7},
    }


def strategy_nodes():
    return {
        ENTRY: {'attach': {'native_battle_preset_fallback': True,
                           'native_battle_config_node': 'strategy'},
                'action': {'param': {'custom_action_param':
                                    {'chaldea_import_source': 'lifecycle-test-share'}}}},
        'strategy': {'attach': {'native_battle_strategy_source': 'editor'}},
    }


class BattleHarness:
    """Only replace perception and physical execution, keeping runtime.run real."""
    def __init__(self, cases):
        self.cases = iter(cases)
        self.runtimes, self.results = [], []
        ready = SkillState(True, Confidence(1))
        servants = tuple(ServantState(slot, (ready,) * 3, Confidence(1), str(sid))
                         for slot, sid in enumerate((100100, 100200, 100300), 1))
        self.main = BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), (), servants)
        cards = tuple(CommandCard(slot, color, 1, Confidence(1), Confidence(1), '100100')
                      for slot, color in enumerate((C.BUSTER, C.ARTS, C.QUICK,
                                                    C.BUSTER, C.ARTS), 1))
        self.hand = BattleState(Scene.COMMAND_SELECTION, Confidence(1), cards,
                               (NpCard(3, Confidence(1), owner_servant_id='100300'),
                                NpCard(2, Confidence(1), owner_servant_id='100200')), ())

    def create(self, *args, **kwargs):
        failure, terminal = next(self.cases)
        runtime = AutoBattleRuntime(*args, **kwargs)
        frames = [self.main] if failure else [self.main, self.hand,
            BattleState(terminal, Confidence(1), (), (), ())]
        runtime._observe = Mock(side_effect=frames)
        runtime._execute_skills = Mock(return_value=not failure)
        runtime.executor.open_command_cards = Mock(return_value=True)
        runtime._wait_until = Mock(return_value=True)
        runtime._execute_selection = Mock(return_value=True)
        runtime._wait_turn_settled = Mock(return_value=True)
        runtime._post_attack_returned_to_map = False
        real_run = runtime.run

        def run():
            result = real_run()
            self.results.append(result)
            return result

        runtime.run = run
        self.runtimes.append(runtime)
        return runtime


class PresetFallbackLifecycleTests(unittest.TestCase):
    def make_context(self):
        nodes = strategy_nodes()
        return NS(nodes=nodes, get_node_data=lambda name: nodes.get(name, {}),
                  tasker=NS(stopping=False, controller=object()))

    def invoke(self, ctx):
        param = ctx.nodes[ENTRY]['action']['param']['custom_action_param']
        return entry.AutoBattleAction().run(ctx, NS(node_name=ENTRY,
            custom_action_param=json.dumps(param)))

    def test_next_battle_reloads_original_editor_actions_and_strategy_after_fallback_victory(self):
        ctx, share = self.make_context(), source_share()
        original_nodes, original_share = copy.deepcopy(ctx.nodes), copy.deepcopy(share)
        harness = BattleHarness([(True, Scene.VICTORY), (False, Scene.VICTORY),
                                 (False, Scene.VICTORY)])
        # Return the same mutable source object on both loads, detecting source
        # pollution as well as reuse of a previous default runtime/decider.
        with patch.object(entry, 'context_session', return_value=(1, None)), \
             patch.object(entry, 'fetch_share_data', return_value=(share, None, None)) as fetch, \
             patch.object(entry, 'AutoBattleRuntime', side_effect=harness.create), \
             patch.object(entry, 'recover_for_default_battle', return_value=True) as cleanup, \
             patch.object(entry, 'inspect_victory_settlement') as settle:
            self.assertTrue(self.invoke(ctx).success)
            self.assertTrue(self.invoke(ctx).success)

        old, fallback, next_battle = harness.runtimes
        cleanup.assert_called_once_with(old)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(settle.call_count, 2)
        self.assertEqual((ctx.nodes, share), (original_nodes, original_share))
        self.assertIsNone(fallback.decider.plan)
        self.assertTrue(fallback.decider.policy.skill.auto_servant_skills)
        self.assertIsNotNone(next_battle.decider.plan)
        self.assertIsNot(next_battle.decider.plan, old.decider.plan)
        self.assertEqual(next_battle.profile.max_turns, 7)
        self.assertFalse(next_battle.decider.policy.skill.auto_servant_skills)
        self.assertEqual(next_battle.decider.policy.card.native_strategy.brave_chain_weight, 32)
        planned_skills = next_battle._execute_skills.call_args.args[0].servant_skills
        self.assertEqual([(s.servant_slot, s.skill_index) for s in planned_skills], [(2, 2)])
        planned_picks = next_battle._execute_selection.call_args.args[0].picks
        default_picks = fallback._execute_selection.call_args.args[0].picks
        self.assertEqual((planned_picks[0].kind, planned_picks[0].slot), (K.SELECT_NP, 2))
        self.assertEqual((default_picks[0].kind, default_picks[0].slot), (K.SELECT_NP, 3))

    def test_default_defeat_returns_successful_handoff_without_victory_settlement_or_retry(self):
        ctx, share = self.make_context(), source_share()
        harness = BattleHarness([(True, Scene.VICTORY), (False, Scene.DEFEAT)])
        with patch.object(entry, 'context_session', return_value=(1, None)), \
             patch.object(entry, 'fetch_share_data', return_value=(share, None, None)), \
             patch.object(entry, 'AutoBattleRuntime', side_effect=harness.create), \
             patch.object(entry, 'recover_for_default_battle', return_value=True) as cleanup, \
             patch.object(entry, 'inspect_victory_settlement') as settle:
            result = self.invoke(ctx)
        self.assertTrue(result.success)
        self.assertEqual(len(harness.runtimes), 2)
        self.assertTrue(harness.results[-1].ok)
        self.assertEqual((harness.results[-1].reason, harness.results[-1].turns), ('defeat', 1))
        cleanup.assert_called_once_with(harness.runtimes[0])
        settle.assert_not_called()

    def test_story_receives_default_defeat_then_resumes_with_explicit_default_reset(self):
        runtime, ctx = story_runner([
            {'战斗失败_不回主界面', '主线-使用灵脉石'},
            {'主线-灵脉石确认'}, {'进本-战斗主界面已出现'}, set(),
        ], {'use_spirit_stone': True})
        ctx.nodes.update(strategy_nodes())
        original_nodes, share = copy.deepcopy(ctx.nodes), source_share()
        original_share = copy.deepcopy(share)
        runtime.reach_formation = Mock()
        runtime._start_formation_battle = Mock()
        harness = BattleHarness([(True, Scene.VICTORY), (False, Scene.DEFEAT),
                                 (False, Scene.VICTORY)])
        task_results = []
        original_run_task = type(ctx).run_task

        def dispatch(context, name, pipeline_override=None):
            result = original_run_task(context, name, pipeline_override)
            if name != ENTRY:
                return result
            effective = copy.deepcopy(context.nodes)
            merge(effective, pipeline_override or {})
            action_context = copy.copy(context)
            action_context.nodes = effective
            param = effective[ENTRY]['action']['param']['custom_action_param']
            action_result = entry.AutoBattleAction().run(action_context, NS(
                node_name=ENTRY, custom_action_param=(json.dumps(param)
                    if isinstance(param, dict) else param)))
            task_results.append(action_result.success)
            return NS(status=NS(succeeded=action_result.success))

        # StoryRunner and the real CustomAction communicate through the fake
        # Context task boundary. Its recognition/actions only record names;
        # neither a simulator nor an actual spirit stone is involved.
        with patch.object(type(ctx), 'run_task', dispatch), \
             patch.object(entry, 'context_session', return_value=(1, None)), \
             patch.object(entry, 'fetch_share_data', return_value=(share, None, None)) as fetch, \
             patch.object(entry, 'AutoBattleRuntime', side_effect=harness.create), \
             patch.object(entry, 'recover_for_default_battle', return_value=True) as cleanup, \
             patch.object(entry, 'inspect_victory_settlement') as settle:
            runtime.fight()

        self.assertEqual(task_results, [True, True])
        self.assertEqual([result.reason for result in harness.results],
                         ['skill_execution_failed', 'defeat', 'victory'])
        cleanup.assert_called_once_with(harness.runtimes[0])
        self.assertIsNone(harness.runtimes[2].decider.plan)
        self.assertTrue(harness.runtimes[2].decider.policy.skill.auto_servant_skills)
        fetch.assert_called_once()
        settle.assert_called_once()
        self.assertEqual([name for name, _override in ctx.actions],
                         ['主线-使用灵脉石', '主线-灵脉石确认'])
        self.assertEqual(runtime._spirit_stones_used, 1)
        self.assertEqual(runtime.completed, 0)
        self.assertEqual((ctx.nodes, share), (original_nodes, original_share))
        self.assertIs(runtime.ctx, ctx)


if __name__ == '__main__':
    unittest.main()
