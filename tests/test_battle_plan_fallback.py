"""No turns use automatic combat; an explicit empty turn stays face-cards only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from formation_test_support import battle_action as entry
from battle.core.decider import RuleDecider
from battle.core.enums import CardColor, PrimitiveKind, Scene
from battle.core.models import (BattleAction, BattlePlan, BattleState, CommandCard,
                                Confidence, MasterSkillAction, NpCard,
                                ServantState, SkillState, TurnPlan)
from battle.core.plan_parser import _parse_plan
from battle.core.policy import BattlePolicy, CardPolicy, SkillPolicy, StrategyProfile
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.runtime.runtime import AutoBattleRuntime


EMPTY_ATTACK = {'type': 'attack', 'attacks': []}
ROSTER = ('505600', None, '100100', None, '1002000', None)


def share():
    return {'team': {'name': 'Only formation',
                     'onFieldSvts': [{'svtId': 505700}, None, {'svtId': 100100}],
                     'backupSvts': [None, {'svtId': 1002100}, None]},
            'future': {'untouched': [1, None, {'other': True}]}}


def main_state():
    skills = tuple(SkillState(value, Confidence(1)) for value in (False, True, None))
    return BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), (),
                       servants=(ServantState(1, skills, Confidence(1)),),
                       master_skills=skills)


def command_state():
    return BattleState(Scene.COMMAND_SELECTION, Confidence(1),
                       tuple(CommandCard(i, color, 1, Confidence(1))
                             for i, color in enumerate((CardColor.BUSTER, CardColor.ARTS,
                                                        CardColor.QUICK, CardColor.BUSTER,
                                                        CardColor.ARTS), 1)),
                       (NpCard(1, Confidence(1)),), ())


class PlanFallbackTests(unittest.TestCase):
    def test_invalid_or_unrecognized_actions_do_not_create_a_phantom_round(self):
        for actions in ({}, 'invalid', False, [None], [{'type': 'unrecognized'}]):
            with self.subTest(actions=actions):
                before = copy.deepcopy(actions)
                self.assertIsNone(convert_chaldea_actions_to_battle_plan(actions))
                self.assertEqual(actions, before)

    def test_absent_or_empty_actions_have_no_plan_and_keep_share_untouched(self):
        for action_fields in ({}, {'actions': None}, {'actions': []}):
            with self.subTest(action_fields=action_fields):
                data = {**share(), **action_fields}
                before = copy.deepcopy(data)
                plan, roster = entry._battle_setup_from_chaldea_share({'chaldea_share': data})
                self.assertIsNone(plan)
                self.assertEqual(roster, ROSTER)
                self.assertEqual(data, before)
                decider = RuleDecider(plan=plan)
                self.assertEqual([s.skill_index for s in decider.decide(main_state()).servant_skills], [2])
                self.assertEqual(decider.decide(command_state()).picks[0].kind, PrimitiveKind.SELECT_NP)

    def test_explicit_empty_attack_is_a_fixed_turn_and_keeps_roster(self):
        data = {**share(), 'actions': [copy.deepcopy(EMPTY_ATTACK)]}
        before = copy.deepcopy(data)
        plan, roster = entry._battle_setup_from_chaldea_share({'chaldea_share': data})
        self.assertEqual(roster, ROSTER)
        self.assertEqual(data, before)
        self.assertEqual(plan.turns, (TurnPlan(),))
        decider = RuleDecider(plan=plan)
        self.assertEqual(decider.decide(main_state()).servant_skills, ())
        picks = decider.decide(command_state()).picks
        self.assertEqual(len(picks), 3)
        self.assertTrue(all(p.kind is PrimitiveKind.SELECT_CARD for p in picks))

    def test_last_planned_turn_stays_fixed_then_full_automatic_policy_resumes(self):
        plan = convert_chaldea_actions_to_battle_plan([EMPTY_ATTACK, EMPTY_ATTACK])
        decider = RuleDecider(plan=plan)
        automatic = RuleDecider()
        for turn in (0, 1):
            self.assertEqual(decider.decide(main_state(), turn).servant_skills, ())
            self.assertTrue(all(p.kind is PrimitiveKind.SELECT_CARD
                                for p in decider.decide(command_state(), turn).picks))
        for turn in (2, 8):
            self.assertEqual(decider.decide(main_state(), turn), automatic.decide(main_state(), turn))
            self.assertEqual(decider.decide(command_state(), turn), automatic.decide(command_state(), turn))

    def test_planned_skills_and_np_run_before_fallback(self):
        plan = convert_chaldea_actions_to_battle_plan([
            {'type': 'skill', 'svt': 0, 'skill': 2},
            {'type': 'attack', 'attacks': [{'svt': 0, 'isTD': True}]},
        ])
        decider = RuleDecider(plan=plan)
        self.assertEqual([s.skill_index for s in decider.decide(main_state()).servant_skills], [3])
        self.assertEqual(decider.decide(command_state()).picks[0].kind, PrimitiveKind.SELECT_NP)
        self.assertEqual([s.skill_index for s in decider.decide(main_state(), 1).servant_skills], [2])

    def test_trailing_skill_still_creates_its_own_fixed_turn(self):
        plan = convert_chaldea_actions_to_battle_plan([{'type': 'skill', 'svt': 0, 'skill': 0}])
        self.assertEqual(len(plan.turns), 1)
        self.assertEqual(plan.turns[0].servant_skills[0].skill_index, 1)
        self.assertEqual(RuleDecider(plan=plan).decide(main_state(), 1),
                         RuleDecider().decide(main_state(), 1))

    def test_fallback_retains_custom_auto_skill_and_np_policy(self):
        policy = BattlePolicy(card=CardPolicy(np_first=False,
                              color_priority=(CardColor.ARTS, CardColor.QUICK, CardColor.BUSTER)),
                              skill=SkillPolicy(auto_servant_skills=False))
        decider = RuleDecider(policy, plan=BattlePlan((TurnPlan(),)))
        automatic = RuleDecider(policy)
        self.assertEqual(decider.decide(main_state(), 1), automatic.decide(main_state(), 1))
        self.assertEqual(decider.decide(command_state(), 1), automatic.decide(command_state(), 1))

    def test_explicit_zero_turn_plan_and_empty_turn_keep_distinct_parser_meanings(self):
        empty_plan = _parse_plan({'plan': {'turns': []}})
        self.assertIsNotNone(empty_plan)
        self.assertEqual(empty_plan.turns, ())
        self.assertEqual(RuleDecider(plan=empty_plan).decide(main_state()), RuleDecider().decide(main_state()))
        empty_turn = _parse_plan({'plan': {'turns': [{}]}})
        self.assertEqual(empty_turn.turns, (TurnPlan(),))
        self.assertEqual(RuleDecider(plan=empty_turn).decide(main_state()).servant_skills, ())


class PlanFallbackEntryTests(unittest.TestCase):
    def run_entry(self, param):
        context = NS(tasker=NS(stopping=False, controller=object()))
        argv = NS(custom_action_param=json.dumps(param))
        with patch.object(entry, 'context_session', return_value=(123, None)), \
             patch.object(entry, 'AutoBattleRuntime') as runtime:
            runtime.return_value.run.return_value = NS(ok=True, reason='victory', turns=1)
            self.assertTrue(entry.AutoBattleAction().run(context, argv).success)
            return runtime.call_args

    def test_inline_and_fetched_formation_only_sources_pass_no_plan_but_keep_identity(self):
        for action_fields in ({}, {'actions': None}, {'actions': []}):
            for fetched in (False, True):
                with self.subTest(fields=action_fields, fetched=fetched):
                    data = {**share(), **action_fields}
                    before = copy.deepcopy(data)
                    param = {'chaldea_import_source': 'local.json'} if fetched else {'chaldea_share': data}
                    with patch.object(entry, 'fetch_share_data', return_value=(data, None, None)) as fetch:
                        call = self.run_entry(param)
                    self.assertIsNone(call.args[2].plan)
                    self.assertEqual(call.kwargs['roster_servant_ids'], ROSTER)
                    self.assertEqual(data, before)
                    self.assertEqual(fetch.call_count, int(fetched))

    def test_explicit_zero_turn_plan_overrides_chaldea_source_without_fetching(self):
        with patch.object(entry, 'fetch_share_data') as fetch:
            call = self.run_entry({'plan': {'turns': []}, 'chaldea_import_source': 'must-not-fetch'})
        fetch.assert_not_called()
        self.assertIsNotNone(call.args[2].plan)
        self.assertEqual(call.args[2].plan.turns, ())
        self.assertEqual(call.args[2].decide(main_state()), RuleDecider().decide(main_state()))

    def test_real_local_json_loading_preserves_formation_and_empty_turn_meaning(self):
        for action_fields in ({}, {'actions': None}, {'actions': []}, {'actions': [EMPTY_ATTACK]}):
            with self.subTest(fields=action_fields), tempfile.TemporaryDirectory() as directory:
                data = {**share(), **action_fields}
                path = Path(directory) / 'formation.json'
                raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
                path.write_bytes(raw)
                call = self.run_entry({'chaldea_import_source': str(path)})
                self.assertEqual(call.kwargs['roster_servant_ids'], ROSTER)
                self.assertEqual(path.read_bytes(), raw)
                plan = call.args[2].plan
                if action_fields.get('actions'):
                    self.assertEqual(plan.turns, (TurnPlan(),))
                else:
                    self.assertIsNone(plan)


class PlanFallbackRuntimeTests(unittest.TestCase):
    def runtime(self, plan):
        context = NS(tasker=NS(stopping=False),
                     run_task=Mock(return_value=NS(status=NS(succeeded=True))))
        capture = Mock()
        capture.wait.return_value = capture
        capture.get.return_value = object()
        controller = NS(post_screencap=Mock(return_value=capture))
        return AutoBattleRuntime(context, controller, RuleDecider(plan=plan), StrategyProfile())

    def test_skill_patch_only_applies_during_real_plan_turns_and_preserves_cd_afterward(self):
        observed = main_state()
        for plan in (None, BattlePlan(), BattlePlan((TurnPlan(), TurnPlan()))):
            runtime = self.runtime(plan)
            with patch('battle.runtime.runtime.perception.build', return_value=observed):
                for turn in (0, 1, 2, 8):
                    with self.subTest(plan=plan, turn=turn):
                        runtime._turn_index = turn
                        result = runtime._observe()
                        if plan is not None and turn < len(plan.turns):
                            self.assertEqual([s.available for s in result.servants[0].skills], [True] * 3)
                            self.assertEqual([s.available for s in result.master_skills], [True] * 3)
                        else:
                            self.assertIs(result, observed)
                            self.assertEqual([s.available for s in result.servants[0].skills], [False, True, None])
                            self.assertEqual([s.available for s in result.master_skills], [False, True, None])
        self.assertEqual([s.available for s in observed.servants[0].skills], [False, True, None])

    def test_master_skill_policy_bypass_stops_at_plan_boundary(self):
        runtime = self.runtime(BattlePlan((TurnPlan(),)))
        runtime._execute_skill_cast = Mock(return_value=True)
        action = BattleAction(None, (), master_skills=(MasterSkillAction(1),))
        self.assertTrue(runtime._execute_skills(action))
        runtime._execute_skill_cast.assert_called_once()
        runtime._execute_skill_cast.reset_mock()
        runtime._turn_index = 1
        self.assertTrue(runtime._execute_skills(action))
        runtime._execute_skill_cast.assert_not_called()

    def test_actual_runtime_loop_switches_from_empty_turn_to_automatic_cd_and_np(self):
        runtime = self.runtime(convert_chaldea_actions_to_battle_plan([EMPTY_ATTACK]))
        observed = [main_state(), command_state(), main_state(), command_state(),
                    BattleState(Scene.VICTORY, Confidence(1), (), (), ())]
        runtime._wait_until = Mock(return_value=True)
        runtime._execute_skill_cast = Mock(return_value=True)
        runtime._execute_selection = Mock(return_value=True)
        runtime._wait_turn_settled = Mock(return_value=True)
        runtime._post_attack_returned_to_map = False
        runtime.executor = NS(open_command_cards=Mock(return_value=True))
        with patch('battle.runtime.runtime.perception.detect_scene', side_effect=[s.scene for s in observed]), \
             patch('battle.runtime.runtime.perception.build', side_effect=observed), \
             patch('battle.runtime.runtime.time.sleep'):
            result = runtime.run()
        self.assertTrue(result.ok)
        self.assertEqual(result.turns, 2)
        self.assertEqual(runtime._execute_skill_cast.call_count, 1)
        self.assertEqual(runtime._execute_skill_cast.call_args.args[0], 'cast_servant_skill')
        first, second = [c.args[0] for c in runtime._execute_selection.call_args_list]
        self.assertTrue(all(p.kind is PrimitiveKind.SELECT_CARD for p in first.picks))
        self.assertEqual(second.picks[0].kind, PrimitiveKind.SELECT_NP)


if __name__ == '__main__':
    unittest.main()
