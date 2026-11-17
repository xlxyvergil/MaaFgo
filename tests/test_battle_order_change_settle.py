"""换人后只等主界面返回；动态画面不阻断，失败或停止不更新身份。"""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from battle.core.enums import Scene
from battle.core.models import (BattleAction, BattlePlan, BattleState, Confidence, MasterSkillAction,
                                OrderChangeAction, ServantSkillAction, ServantState, SkillState, TurnPlan)
from battle.core.policy import BattlePolicy, SkillPolicy, StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime, _ORDER_CHANGE_RETURN_TIMEOUT_S


INITIAL = ('miner', 'flora', 'kuku', None, 'iori', 'tomoe')
SWAPPED = ('iori', 'flora', 'kuku', None, 'miner', 'tomoe')
ENTRY = '战斗_换人后等待主界面'
RETURNED = '战斗_换人后主界面'


def action():
    master, swap, following = MasterSkillAction(3), OrderChangeAction(1, 5), ServantSkillAction(1, 1)
    return BattleAction(None, (), (following,), (master,), swap,
                        skill_sequence=(master, swap, following))


class OrderChangeSettleTests(unittest.TestCase):
    def runtime(self):
        runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
        runtime.roster_servant_ids = INITIAL
        runtime.decider = NS(plan=BattlePlan(turns=(TurnPlan(),)))
        runtime._turn_index = 0
        runtime.battle_policy = BattlePolicy(skill=SkillPolicy(use_master_skills=True))
        runtime.events = []
        runtime._mark_action = Mock()
        runtime._wait_until = Mock(return_value=True)
        runtime.executor = NS(
            cast_master_skill=Mock(side_effect=lambda *_: runtime.events.append(('master', runtime.roster_servant_ids)) or True),
            order_change=Mock(side_effect=lambda *_: runtime.events.append(('swap', runtime.roster_servant_ids)) or True),
            cast_servant_skill=Mock(side_effect=lambda *_: runtime.events.append(('skill', runtime.roster_servant_ids)) or True),
        )
        runtime._execute_skill_cast = Mock(side_effect=lambda _label, cast, *_a, **_kw: cast())
        runtime.ctx = NS(
            tasker=NS(stopping=False),
            run_task=Mock(side_effect=lambda *_: runtime.events.append(('pipeline', runtime.roster_servant_ids)) or NS(status=NS(succeeded=True))),
            get_node_object=Mock(side_effect=AssertionError('must not read freeze configuration')),
            wait_freezes=Mock(return_value=False),  # 动态画面永不冻结，也应允许返回后继续。
        )
        return runtime

    def assert_no_commit(self, runtime):
        self.assertEqual(runtime.roster_servant_ids, INITIAL)
        self.assertEqual(runtime._member_order, list(range(6)))
        runtime.executor.cast_servant_skill.assert_not_called()

    def test_main_return_updates_roster_then_casts_without_any_freeze_check(self):
        runtime = self.runtime()
        self.assertTrue(runtime._execute_skills(action()))
        self.assertEqual(runtime.events, [('master', INITIAL), ('swap', INITIAL),
                                         ('pipeline', INITIAL), ('skill', SWAPPED)])
        runtime.ctx.run_task.assert_called_once_with(ENTRY)
        runtime.ctx.get_node_object.assert_not_called()
        runtime.ctx.wait_freezes.assert_not_called()
        self.assertEqual(runtime.roster_servant_ids, SWAPPED)
        self.assertEqual(runtime._member_order, [4, 1, 2, 3, 0, 5])
        # 只剩换人页轮询；主界面返回交给专用Pipeline。
        self.assertEqual(runtime._wait_until.call_count, 1)

    def test_failed_or_missing_main_return_blocks_commit(self):
        for detail in (None, NS(status=NS(succeeded=False))):
            with self.subTest(detail=detail):
                runtime = self.runtime()
                runtime.ctx.run_task.side_effect = None
                runtime.ctx.run_task.return_value = detail
                self.assertFalse(runtime._execute_skills(action()))
                self.assert_no_commit(runtime)
                runtime.ctx.wait_freezes.assert_not_called()

    def test_pipeline_exception_blocks_commit(self):
        runtime = self.runtime()
        runtime.ctx.run_task.side_effect = RuntimeError('main battle recognition failed')
        self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)

    def test_cancel_before_wait_sends_no_pipeline_and_changes_no_identity(self):
        runtime = self.runtime()
        runtime.executor.order_change.side_effect = lambda *_: setattr(runtime.ctx.tasker, 'stopping', True) or True
        self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.run_task.assert_not_called()

    def test_cancel_during_successful_pipeline_blocks_commit(self):
        runtime = self.runtime()
        runtime.ctx.run_task.side_effect = lambda *_: setattr(runtime.ctx.tasker, 'stopping', True) or NS(status=NS(succeeded=True))
        self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.wait_freezes.assert_not_called()

    def test_failed_swap_executor_does_not_wait_or_commit(self):
        runtime = self.runtime()
        runtime.executor.order_change.side_effect = None
        runtime.executor.order_change.return_value = False
        self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.run_task.assert_not_called()

    def test_shared_budget_exhaustion_blocks_pipeline_and_following_skill(self):
        runtime = self.runtime()
        with patch('battle.runtime.runtime.time.monotonic', side_effect=[100, 101 + _ORDER_CHANGE_RETURN_TIMEOUT_S]):
            self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.run_task.assert_not_called()

    def test_over_budget_success_result_still_blocks_commit(self):
        runtime = self.runtime()
        with patch('battle.runtime.runtime.time.monotonic', side_effect=[100, 101, 101 + _ORDER_CHANGE_RETURN_TIMEOUT_S]):
            self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)

    def test_expired_retirement_budget_does_not_start_wait(self):
        runtime = self.runtime()
        with patch('battle.runtime.runtime.time.monotonic', return_value=100):
            self.assertFalse(runtime._wait_order_change_settled(timeout=0))
        runtime.ctx.run_task.assert_not_called()

    def test_no_order_change_does_not_add_pipeline_or_freeze_wait(self):
        runtime = self.runtime()
        following = ServantSkillAction(1, 1)
        plain = BattleAction(None, (), (following,), skill_sequence=(following,))
        self.assertTrue(runtime._execute_skills(plain))
        runtime.ctx.run_task.assert_not_called()
        runtime.ctx.wait_freezes.assert_not_called()
        self.assertEqual(runtime.roster_servant_ids, INITIAL)

    def test_main_loop_propagates_return_failure_without_opening_attack(self):
        runtime = self.runtime()
        runtime.initial_formation = None
        runtime.profile = StrategyProfile()
        available = SkillState(True, Confidence(1.0))
        runtime._observe = Mock(return_value=BattleState(
            Scene.MAIN_BATTLE, Confidence(1.0), (), (), (),
            servants=tuple(ServantState(i, (available,) * 3, Confidence(1.0)) for i in (1, 2, 3)),
            master_skills=(available,) * 3,
        ))
        runtime.decider.decide = Mock(return_value=action())
        runtime.executor.open_command_cards = Mock()
        runtime.ctx.run_task.side_effect = None
        runtime.ctx.run_task.return_value = NS(status=NS(succeeded=False))
        result = runtime.run()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, 'skill_execution_failed')
        self.assert_no_commit(runtime)
        runtime.executor.open_command_cards.assert_not_called()

    def test_pipeline_only_waits_for_main_after_bounded_transition(self):
        pipeline = json.loads((ROOT / 'assets/resource/base/pipeline/自动战斗_感知.json').read_text('utf-8'))
        entry, returned = pipeline[ENTRY], pipeline[RETURNED]
        self.assertEqual(entry['next'], [RETURNED])
        self.assertEqual(entry['post_delay'], 3000)
        self.assertEqual(entry['timeout'], 8000)
        param = copy.deepcopy(pipeline['战斗_主界面']['recognition']['param'])
        param.setdefault('threshold', .7)
        self.assertEqual(returned['recognition']['param'], param)
        self.assertEqual(returned['post_delay'], 0)
        self.assertEqual(returned['next'], [])
        for node in (entry, returned):
            self.assertEqual(node['action']['type'], 'DoNothing')
            self.assertEqual(node['on_error'], [])
            self.assertEqual(node['pre_delay'], 0)
            for key in ('pre_wait_freezes', 'post_wait_freezes', 'repeat_wait_freezes'):
                self.assertNotIn(key, node)


if __name__ == '__main__':
    unittest.main()
