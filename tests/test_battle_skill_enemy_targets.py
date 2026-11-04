"""Per-skill enemy targets keep Chaldea order and fall back when unavailable."""
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.decider import RuleDecider
from battle.core.enums import Scene
from battle.core.models import (BattleAction, BattlePlan, BattleState, Confidence, EnemyState,
                                MasterSkillAction, OrderChangeAction,
                                ServantSkillAction, ServantState, SkillState, TurnPlan)
from battle.core.plan_parser import _parse_plan
from battle.core.policy import BattlePolicy, SkillPolicy, StrategyProfile
from battle.core.validator import (skip_unusable_master_skills,
                                   validate_main_action)
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.runtime.runtime import AutoBattleRuntime


def state(*, selected=1, scene=Scene.MAIN_BATTLE, scene_confidence=1.0,
          enemies=None, skill_available=True, skill_confidence=1.0):
    available = SkillState(skill_available, Confidence(skill_confidence))
    return BattleState(
        scene, Confidence(scene_confidence), (), (),
        enemies if enemies is not None else tuple(
            EnemyState(i, True, i == selected, Confidence(1.0)) for i in (1, 2, 3)),
        servants=(ServantState(1, (available,) * 3, Confidence(1.0)),),
        master_skills=(available,) * 3,
    )


def action_for(sequence, target=None):
    return BattleAction(
        target, (),
        servant_skills=tuple(s for s in sequence if isinstance(s, ServantSkillAction)),
        master_skills=tuple(s for s in sequence if isinstance(s, MasterSkillAction)),
        order_change=next((s for s in sequence if isinstance(s, OrderChangeAction)), None),
        skill_sequence=tuple(sequence),
    )


@contextmanager
def fast_clock():
    clock = [0.0]
    with patch("battle.runtime.runtime.time.monotonic", side_effect=lambda: clock[0]), \
            patch("battle.runtime.runtime.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
        yield clock


class SkillEnemyTargetTests(unittest.TestCase):
    def runtime(self, *, selected=1):
        runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
        runtime.ctx = NS(tasker=NS(stopping=False))
        runtime.profile = StrategyProfile()
        runtime.decider = NS(plan=BattlePlan(turns=(TurnPlan(),)))
        runtime._turn_index = 0
        runtime.battle_policy = BattlePolicy(skill=SkillPolicy(use_master_skills=True))
        runtime.roster_servant_ids = ("one", "two", "three", "four", None, None)
        runtime.events = []
        runtime.selected = selected
        runtime._mark_action = Mock()
        runtime._observe = Mock(side_effect=lambda **_kw: state(selected=runtime.selected))

        def select(slot):
            runtime.events.append(("enemy", slot))
            runtime.selected = slot
            return True

        runtime.executor = NS(
            select_enemy=Mock(side_effect=select),
            cast_servant_skill=Mock(side_effect=lambda slot, skill: runtime.events.append(("servant", slot, skill)) or True),
            cast_master_skill=Mock(side_effect=lambda skill: runtime.events.append(("master", skill)) or True),
            order_change=Mock(side_effect=lambda front, back: runtime.events.append(("swap", front, back)) or True),
        )
        runtime._execute_skill_cast = Mock(side_effect=lambda _label, cast, *_args, **_kw: cast())
        runtime._wait_until = Mock(return_value=True)
        runtime._wait_order_change_settled = Mock(return_value=True)
        return runtime

    def test_chaldea_target_mapping_preserves_each_skill_and_attack(self):
        actions = [{"type": "skill", "svt": 0, "skill": 1, "options": {"enemyTarget": value, "playerTarget": 2}}
                   for value in (2, 1, 0)]
        actions += [{"type": "skill", "svt": None, "skill": 0, "options": {"enemyTarget": 1}},
                    {"type": "attack", "attacks": [], "options": {"enemyTarget": 2}}]
        turn = convert_chaldea_actions_to_battle_plan(actions).turns[0]
        self.assertEqual([s.target_enemy for s in turn.servant_skills], [1, 2, 3])
        self.assertEqual([s.target_ally for s in turn.servant_skills], [3, 3, 3])
        self.assertEqual(turn.master_skills[0].target_enemy, 2)
        self.assertEqual(turn.target_enemy, 1)
        self.assertEqual(turn.skill_sequence, (*turn.servant_skills, *turn.master_skills))

    def test_chaldea_swap_master_target_is_retained_next_to_paired_swap(self):
        turn = convert_chaldea_actions_to_battle_plan([
            {"type": "skill", "svt": None, "skill": 2, "options": {"enemyTarget": 0}},
            {"type": "attack", "attacks": []},
        ], {"replaceMemberIndexes": [[1, 0]]}, 20).turns[0]
        self.assertEqual(turn.skill_sequence, (MasterSkillAction(3, target_enemy=3), OrderChangeAction(2, 4)))

    def test_three_identical_master_uses_survive_conversion_in_order(self):
        turn = convert_chaldea_actions_to_battle_plan([
            *[{"type": "skill", "svt": None, "skill": 0, "options": {"enemyTarget": target}}
              for target in (2, 1, 0)], {"type": "attack", "attacks": []}
        ]).turns[0]
        self.assertEqual([s.skill_index for s in turn.master_skills], [1, 1, 1])
        self.assertEqual([s.target_enemy for s in turn.skill_sequence], [1, 2, 3])

    def test_invalid_chaldea_enemy_values_do_not_become_valid_targets(self):
        for value in (True, False, -1, 3, 1.0, "1", [], {}, None):
            with self.subTest(value=value):
                turn = convert_chaldea_actions_to_battle_plan([
                    {"type": "skill", "svt": 0, "skill": 0, "options": {"enemyTarget": value}},
                    {"type": "attack", "attacks": [], "options": {"enemyTarget": value}},
                ]).turns[0]
                self.assertIsNone(turn.servant_skills[0].target_enemy)
                self.assertIsNone(turn.target_enemy)

    def test_old_skill_positions_and_positional_constructors_are_compatible(self):
        old = ServantSkillAction(1, 2, 3)
        self.assertEqual(old.target_ally, 3)
        self.assertIsNone(old.target_enemy)
        self.assertIsNone(MasterSkillAction(1, 2).target_enemy)
        turn = convert_chaldea_actions_to_battle_plan([
            {"type": "skill", "svt": 1, "skill": 2, "options": {"playerTarget": 0}},
            {"type": "skill", "svt": None, "skill": 1}, {"type": "attack", "attacks": []}
        ]).turns[0]
        self.assertEqual(turn.skill_sequence, (ServantSkillAction(2, 3, 1), MasterSkillAction(2)))

    def test_parser_accepts_optional_native_enemy_target_and_rejects_bool_or_out_of_range(self):
        for target in (1, 2, 3):
            turn = _parse_plan({"plan": {"turns": [{
                "servant_skills": [{"servant_slot": 1, "skill_index": 2, "target_enemy": target}],
                "master_skills": [{"skill_index": 3, "target_enemy": target}],
            }]}}).turns[0]
            self.assertEqual(turn.servant_skills[0].target_enemy, target)
            self.assertEqual(turn.master_skills[0].target_enemy, target)
        for target in (True, False, 0, 4, -1, "1", 1.0):
            turn = _parse_plan({"plan": {"turns": [{
                "servant_skills": [{"servant_slot": 1, "skill_index": 2, "target_enemy": target}],
                "master_skills": [{"skill_index": 3, "target_enemy": target}],
            }]}}).turns[0]
            self.assertEqual(turn.servant_skills, ())
            self.assertEqual(turn.master_skills, ())

    def test_master_duplicates_allowed_but_availability_confidence_and_range_still_checked(self):
        action = action_for([MasterSkillAction(1)] * 3)
        self.assertTrue(validate_main_action(action, state(), StrategyProfile()).ok)
        for available, confidence, expected in ((False, 1, "master_skill_not_available"),
                                                (None, 1, "master_skill_state_unknown"),
                                                (True, 0.1, "master_skill_state_not_confident")):
            verdict = validate_main_action(action, state(skill_available=available, skill_confidence=confidence), StrategyProfile())
            self.assertEqual(verdict.reason, expected)
        self.assertEqual(validate_main_action(action_for([MasterSkillAction(4)]), state(), StrategyProfile()).reason,
                         "invalid_master_skill")
        kept, skipped = skip_unusable_master_skills(action, state(skill_available=False), StrategyProfile())
        self.assertEqual(kept.master_skills, ())
        self.assertEqual(len(skipped), 3)

    def test_validator_rejects_structurally_invalid_skill_enemy_targets(self):
        for kind in (lambda target: ServantSkillAction(1, 1, target_enemy=target),
                     lambda target: MasterSkillAction(1, target_enemy=target)):
            for target in (True, False, 0, 4, -1, "1", 1.0):
                self.assertEqual(validate_main_action(action_for([kind(target)]), state(), StrategyProfile()).reason,
                                 "invalid_skill_enemy_target")

    def test_mixed_sequence_switches_before_each_cast_and_restores_attack_target(self):
        runtime = self.runtime()
        sequence = [ServantSkillAction(1, 1, target_enemy=2), MasterSkillAction(2, target_enemy=3),
                    ServantSkillAction(1, 2, target_enemy=2)]
        self.assertTrue(runtime._execute_skills(action_for(sequence, target=1)))
        self.assertEqual(runtime.events, [("enemy", 2), ("servant", 1, 1), ("enemy", 3), ("master", 2),
                                          ("enemy", 2), ("servant", 1, 2), ("enemy", 1)])

    def test_repeated_master_casts_use_each_target_and_preserve_order(self):
        runtime = self.runtime()
        self.assertTrue(runtime._execute_skills(action_for([
            MasterSkillAction(1, target_enemy=target) for target in (2, 3, 1)])))
        self.assertEqual(runtime.events, [("enemy", 2), ("master", 1), ("enemy", 3), ("master", 1),
                                          ("enemy", 1), ("master", 1)])

    def test_order_change_target_confirmed_before_master_and_swap_then_attack_restored(self):
        runtime = self.runtime()
        self.assertTrue(runtime._execute_skills(action_for([
            MasterSkillAction(3, target_enemy=2), OrderChangeAction(1, 4), ServantSkillAction(1, 1)], target=1)))
        self.assertEqual(runtime.events, [("enemy", 2), ("master", 3), ("swap", 1, 4),
                                          ("servant", 1, 1), ("enemy", 1)])
        self.assertEqual(runtime.roster_servant_ids[0], "four")

    def test_already_targeted_enemy_needs_no_extra_click(self):
        runtime = self.runtime(selected=2)
        self.assertTrue(runtime._execute_skills(action_for([ServantSkillAction(1, 1, target_enemy=2)], target=2)))
        runtime.executor.select_enemy.assert_not_called()
        self.assertEqual(runtime.events, [("servant", 1, 1)])

    def test_old_untargeted_skill_path_uses_no_new_observation_and_leaves_attack_preselection_unchanged(self):
        runtime = self.runtime()
        self.assertTrue(runtime._execute_skills(action_for([ServantSkillAction(1, 1), MasterSkillAction(1)], target=2)))
        runtime._observe.assert_not_called()
        runtime.executor.select_enemy.assert_not_called()
        self.assertEqual(runtime.events, [("servant", 1, 1), ("master", 1)])

    def test_unavailable_target_warns_and_continues_servant_and_master_skills(self):
        scenarios = [state(enemies=()), state(enemies=(EnemyState(2, False, False, Confidence(1)),)),
                     state(enemies=(EnemyState(2, True, False, Confidence(0.1)),)),
                     state(scene=Scene.COMMAND_SELECTION), state(scene_confidence=0.1)]
        for observed in scenarios:
            for skill in (ServantSkillAction(1, 1, target_enemy=2), MasterSkillAction(1, target_enemy=2)):
                with self.subTest(scene=observed.scene, enemies=observed.enemies, skill=skill):
                    runtime = self.runtime();runtime._observe = Mock(return_value=observed)
                    with fast_clock(), patch("battle.runtime.runtime.mfaalog.warning") as warning:
                        self.assertTrue(runtime._execute_skills(action_for([skill])))
                    runtime.executor.select_enemy.assert_not_called()
                    runtime._execute_skill_cast.assert_called_once()
                    warning.assert_called_once()
                    self.assertIn("敌人2", warning.call_args.args[0])
                    self.assertIn("沿用游戏当前目标继续执行", warning.call_args.args[0])

    def test_invalid_target_rejected_without_observe_or_cast(self):
        for target in (True, False, 0, 4, -1, "1", 1.0):
            runtime = self.runtime()
            self.assertFalse(runtime._execute_skills(action_for([MasterSkillAction(1, target_enemy=target)])))
            runtime._observe.assert_not_called();runtime._execute_skill_cast.assert_not_called()

    def test_click_or_confirmation_failure_warns_and_continues_cast(self):
        for click_ok in (True, False):
            for skill in (ServantSkillAction(1, 1, target_enemy=2), MasterSkillAction(1, target_enemy=2)):
                runtime = self.runtime();runtime.executor.select_enemy = Mock(return_value=click_ok)
                runtime._wait_until_enemy_targeted = Mock(return_value=False)
                with patch("battle.runtime.runtime.mfaalog.warning") as warning:
                    self.assertTrue(runtime._execute_skills(action_for([skill])))
                runtime._execute_skill_cast.assert_called_once()
                self.assertEqual(runtime._wait_until_enemy_targeted.call_count, 1 if click_ok else 0)
                warning.assert_called_once()
                self.assertIn("未能确认" if click_ok else "点击", warning.call_args.args[0])

    def test_cancellation_before_or_during_target_observation_never_casts(self):
        runtime = self.runtime();runtime.ctx.tasker.stopping = True
        self.assertFalse(runtime._execute_skills(action_for([MasterSkillAction(1, target_enemy=2)])))
        runtime._observe.assert_not_called();runtime._execute_skill_cast.assert_not_called()
        runtime = self.runtime()
        def observe(**_kwargs):
            runtime.ctx.tasker.stopping = True
            return state(selected=2)
        runtime._observe = Mock(side_effect=observe)
        self.assertFalse(runtime._execute_skills(action_for([MasterSkillAction(1, target_enemy=2)])))
        runtime._execute_skill_cast.assert_not_called()

    def test_cancellation_during_click_or_confirmation_never_casts(self):
        for stage in ("click", "confirmation"):
            runtime = self.runtime()

            def stop(*_args):
                runtime.ctx.tasker.stopping = True
                return False

            if stage == "click":
                runtime.executor.select_enemy = Mock(side_effect=stop)
            else:
                runtime._wait_until_enemy_targeted = Mock(side_effect=stop)
            with patch("battle.runtime.runtime.mfaalog.warning") as warning:
                self.assertFalse(runtime._execute_skills(action_for([ServantSkillAction(1, 1, target_enemy=2)])))
            runtime._execute_skill_cast.assert_not_called()
            warning.assert_not_called()

    def test_confirmation_requires_confident_main_battle_enemy_and_honors_timeout(self):
        runtime = self.runtime()
        clock = [0.0]
        runtime._observe = Mock(return_value=state(scene=Scene.COMMAND_SELECTION, selected=2))
        with patch("battle.runtime.runtime.time.monotonic", side_effect=lambda: clock[0]), \
                patch("battle.runtime.runtime.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            self.assertFalse(runtime._wait_until_enemy_targeted(2, timeout_s=1))
        runtime._observe = Mock(return_value=state(selected=2))
        self.assertTrue(runtime._wait_until_enemy_targeted(2, timeout_s=1))

    def test_disappeared_attack_target_keeps_ordinary_attack_fallback(self):
        runtime = self.runtime()
        runtime._observe = Mock(side_effect=[state(selected=2), state(enemies=())])
        self.assertTrue(runtime._execute_skills(action_for([ServantSkillAction(1, 1, target_enemy=2)], target=1)))
        self.assertEqual(runtime.events, [("servant", 1, 1)])

    def test_skill_waits_through_weak_animation_frames_before_casting(self):
        runtime = self.runtime()
        runtime._observe = Mock(side_effect=[state(scene_confidence=0.82), state(scene=Scene.UNKNOWN), state(selected=2)])
        with fast_clock() as clock:
            self.assertTrue(runtime._execute_skills(action_for([ServantSkillAction(1, 1, target_enemy=2)])))
        self.assertEqual(clock[0], 1.0)
        self.assertEqual(runtime._observe.call_count, 3)
        self.assertEqual(runtime.events, [("servant", 1, 1)])

    def test_missing_enemy_on_stable_main_battle_skips_selection_without_waiting(self):
        runtime = self.runtime();runtime._observe = Mock(return_value=state(enemies=()))
        with fast_clock() as clock:
            self.assertTrue(runtime._execute_skills(action_for([MasterSkillAction(1, target_enemy=2)])))
        self.assertEqual(clock[0], 0)
        runtime._observe.assert_called_once()
        self.assertEqual(runtime.events, [("master", 1)])

    def test_readiness_timeout_is_bounded_and_allows_continuation(self):
        runtime = self.runtime();runtime._observe = Mock(return_value=state(scene_confidence=0.82))
        with fast_clock() as clock, patch("battle.runtime.runtime.mfaalog.warning") as warning:
            self.assertTrue(runtime._ensure_enemy_target(2, timeout_s=1))
        self.assertEqual(clock[0], 1)
        self.assertEqual(runtime._observe.call_count, 2)
        runtime.executor.select_enemy.assert_not_called();runtime._execute_skill_cast.assert_not_called()
        warning.assert_called_once()
        self.assertIn("等待战斗主界面超时", warning.call_args.args[0])

    def test_best_effort_restore_timeout_or_click_failure_does_not_stop_attack(self):
        runtime = self.runtime();runtime._observe = Mock(return_value=state(scene_confidence=0.82))
        with fast_clock() as clock:
            self.assertTrue(runtime._ensure_enemy_target(2, label="restore_attack_enemy", timeout_s=1))
        self.assertEqual(clock[0], 1)
        for clicked in (False, True):
            runtime = self.runtime();runtime.executor.select_enemy = Mock(return_value=clicked)
            runtime._wait_until_enemy_targeted = Mock(return_value=False)
            self.assertTrue(runtime._ensure_enemy_target(2, label="restore_attack_enemy"))
            runtime._execute_skill_cast.assert_not_called()

    def test_best_effort_restore_still_honors_cancellation(self):
        runtime = self.runtime();runtime.ctx.tasker.stopping = True
        self.assertFalse(runtime._ensure_enemy_target(2, label="restore_attack_enemy"))
        runtime._observe.assert_not_called();runtime.executor.select_enemy.assert_not_called()

    def test_run_retains_old_initial_target_choice_then_restores_after_skills_before_attack(self):
        runtime = self.runtime(selected=3)
        sequence = [ServantSkillAction(1, 1, target_enemy=2), MasterSkillAction(1, target_enemy=3)]
        action = action_for(sequence, target=1)
        runtime.initial_formation = None
        runtime.initial_formation_reused = False
        runtime.candidate_servant_ids = ()
        runtime.decider = NS(plan=BattlePlan(turns=(TurnPlan(),)), decide=lambda *_args, **_kw: action)
        opened = [False]
        def observe(**_kw):
            return state(selected=runtime.selected, scene=Scene.VICTORY if opened[0] else Scene.MAIN_BATTLE)
        def open_cards():
            runtime.events.append(("attack",));opened[0] = True;return True
        runtime._observe = Mock(side_effect=observe)
        runtime.executor.open_command_cards = Mock(side_effect=open_cards)
        self.assertTrue(runtime.run().ok)
        self.assertEqual(runtime.events, [("enemy", 1), ("enemy", 2), ("servant", 1, 1),
                                          ("enemy", 3), ("master", 1), ("enemy", 1), ("attack",)])

    def test_enemy_defeated_by_first_skill_does_not_stop_remaining_skills_or_attack(self):
        runtime = self.runtime(selected=2)
        action = action_for([
            ServantSkillAction(1, 1), ServantSkillAction(1, 3, target_enemy=1),
            MasterSkillAction(1),
        ], target=2)
        runtime.initial_formation = None
        runtime.initial_formation_reused = False
        runtime.candidate_servant_ids = ()
        runtime.decider = NS(plan=BattlePlan(turns=(TurnPlan(),)), decide=lambda *_args, **_kw: action)
        opened = [False]

        def observe(**_kw):
            enemies = tuple(EnemyState(i, True, i == 2, Confidence(1))
                            for i in ((2,) if runtime.events else (1, 2)))
            return state(enemies=enemies, scene=Scene.VICTORY if opened[0] else Scene.MAIN_BATTLE)

        def open_cards():
            runtime.events.append(("attack",));opened[0] = True;return True

        runtime._observe = Mock(side_effect=observe)
        runtime.executor.open_command_cards = Mock(side_effect=open_cards)
        with patch("battle.runtime.runtime.mfaalog.warning") as warning:
            self.assertTrue(runtime.run().ok)
        self.assertEqual(runtime.events, [("servant", 1, 1), ("servant", 1, 3),
                                          ("master", 1), ("attack",)])
        runtime.executor.select_enemy.assert_not_called()
        warning.assert_called_once()
        self.assertIn("未识别到敌人1", warning.call_args.args[0])

    def test_unspecified_attack_target_restores_the_existing_decider_fallback(self):
        plan = convert_chaldea_actions_to_battle_plan([
            {"type": "skill", "svt": 0, "skill": 0, "options": {"enemyTarget": 2}},
            {"type": "attack", "attacks": []},
        ])
        self.assertIsNone(plan.turns[0].target_enemy)
        # The decider keeps the currently selected right-side enemy as before.
        action = RuleDecider(plan=plan).decide(state(selected=3), turn_index=0)
        self.assertEqual(action.target_enemy, 3)
        runtime = self.runtime(selected=3)
        self.assertTrue(runtime._execute_skills(action))
        self.assertEqual(runtime.events, [("enemy", 1), ("servant", 1, 1), ("enemy", 3)])

    def test_skill_killing_original_attack_target_does_not_block_opening_command_cards(self):
        runtime = self.runtime(selected=1)
        action = action_for([ServantSkillAction(1, 1, target_enemy=2)], target=1)
        runtime.initial_formation = None;runtime.initial_formation_reused = False
        runtime.candidate_servant_ids = ()
        runtime.decider = NS(plan=BattlePlan(turns=(TurnPlan(),)), decide=lambda *_args, **_kw: action)
        alive = {1, 2, 3};opened = [False]
        def observe(**_kw):
            return state(scene=Scene.VICTORY if opened[0] else Scene.MAIN_BATTLE,
                         enemies=tuple(EnemyState(i, i in alive, i == runtime.selected, Confidence(1.0))
                                       for i in (1, 2, 3)))
        def cast(_label, callback, *_args, **_kw):
            result = callback();alive.discard(1);return result
        def open_cards():
            runtime.events.append(("attack",));opened[0] = True;return True
        runtime._observe = Mock(side_effect=observe)
        runtime._execute_skill_cast = Mock(side_effect=cast)
        runtime.executor.open_command_cards = Mock(side_effect=open_cards)
        self.assertTrue(runtime.run().ok)
        self.assertEqual(runtime.events, [("enemy", 2), ("servant", 1, 1), ("attack",)])
        runtime.executor.open_command_cards.assert_called_once()


if __name__ == "__main__":
    unittest.main()
