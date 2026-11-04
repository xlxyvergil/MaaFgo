"""换人稳定门禁：未稳定不更新身份、不发送新前排技能，不连接设备。"""
import copy
from dataclasses import dataclass
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


@dataclass
class Freeze:
    time: int = 1000
    target: tuple = (40, 545, 890, 80)
    threshold: float = .95
    method: int = 5
    rate_limit: int = 200
    timeout: int = 5000


INITIAL = ('miner', 'flora', 'kuku', None, 'iori', 'tomoe')
SWAPPED = ('iori', 'flora', 'kuku', None, 'miner', 'tomoe')
ENTRY = '战斗_换人后等待技能栏稳定'
STABLE = '战斗_换人后主界面与技能栏稳定'


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
            get_node_object=Mock(return_value=NS(post_wait_freezes=Freeze())),
            wait_freezes=Mock(side_effect=lambda **_: runtime.events.append(('freeze', runtime.roster_servant_ids)) or True),
        )
        return runtime

    def assert_no_commit(self, runtime):
        self.assertEqual(runtime.roster_servant_ids, INITIAL)
        runtime.executor.cast_servant_skill.assert_not_called()

    def test_success_waits_for_pipeline_and_strict_freeze_before_roster_or_skill(self):
        runtime = self.runtime()
        self.assertTrue(runtime._execute_skills(action()))
        self.assertEqual(runtime.events, [('master', INITIAL), ('swap', INITIAL),
                                         ('pipeline', INITIAL), ('freeze', INITIAL), ('skill', SWAPPED)])
        runtime.ctx.run_task.assert_called_once_with(ENTRY)
        runtime.ctx.get_node_object.assert_called_once_with(STABLE)
        self.assertEqual(runtime.roster_servant_ids, SWAPPED)
        # 旧的宽松MAIN返回轮询已由专用Pipeline替代；只剩换人页识别。
        self.assertEqual(runtime._wait_until.call_count, 1)

    def test_failed_or_missing_pipeline_result_blocks_commit_and_strict_freeze(self):
        for detail in (None, NS(status=NS(succeeded=False))):
            with self.subTest(detail=detail):
                runtime = self.runtime()
                runtime.ctx.run_task.side_effect = None
                runtime.ctx.run_task.return_value = detail
                self.assertFalse(runtime._execute_skills(action()))
                self.assert_no_commit(runtime)
                runtime.ctx.wait_freezes.assert_not_called()

    def test_freeze_false_blocks_commit_even_when_pipeline_succeeded(self):
        runtime = self.runtime()
        runtime.ctx.wait_freezes.side_effect = None
        runtime.ctx.wait_freezes.return_value = False
        self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.run_task.assert_called_once()

    def test_exceptions_at_pipeline_configuration_or_strict_freeze_propagate_failure(self):
        for method in ('run_task', 'get_node_object', 'wait_freezes'):
            with self.subTest(method=method):
                runtime = self.runtime()
                getattr(runtime.ctx, method).side_effect = RuntimeError('broken local wait')
                self.assertFalse(runtime._execute_skills(action()))
                self.assert_no_commit(runtime)

    def test_missing_or_disabled_freeze_configuration_is_rejected(self):
        for node in (None, NS(), NS(post_wait_freezes=None),
                     NS(post_wait_freezes=Freeze(time=0)), NS(post_wait_freezes=Freeze(timeout=0))):
            with self.subTest(node=node):
                runtime = self.runtime()
                runtime.ctx.get_node_object.return_value = node
                self.assertFalse(runtime._execute_skills(action()))
                self.assert_no_commit(runtime)
                runtime.ctx.wait_freezes.assert_not_called()

    def test_cancel_before_wait_sends_no_pipeline_and_changes_no_identity(self):
        runtime = self.runtime()
        runtime.executor.order_change.side_effect = lambda *_: setattr(runtime.ctx.tasker, 'stopping', True) or True
        self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.run_task.assert_not_called()

    def test_cancel_during_successful_pipeline_skips_strict_freeze_and_commit(self):
        runtime = self.runtime()
        runtime.ctx.run_task.side_effect = lambda *_: setattr(runtime.ctx.tasker, 'stopping', True) or NS(status=NS(succeeded=True))
        self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.wait_freezes.assert_not_called()

    def test_cancel_during_config_read_or_successful_strict_freeze_blocks_commit(self):
        for phase in ('get_node_object', 'wait_freezes'):
            with self.subTest(phase=phase):
                runtime = self.runtime()
                result = NS(post_wait_freezes=Freeze()) if phase == 'get_node_object' else True
                getattr(runtime.ctx, phase).side_effect = lambda *_, **__: setattr(runtime.ctx.tasker, 'stopping', True) or result
                self.assertFalse(runtime._execute_skills(action()))
                self.assert_no_commit(runtime)

    def test_failed_swap_executor_does_not_wait_or_commit(self):
        runtime = self.runtime()
        runtime.executor.order_change.side_effect = None
        runtime.executor.order_change.return_value = False
        self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.run_task.assert_not_called()

    def test_shared_budget_exhaustion_blocks_following_skill(self):
        runtime = self.runtime()
        with patch('battle.runtime.runtime.time.monotonic', side_effect=[100, 101 + _ORDER_CHANGE_RETURN_TIMEOUT_S]):
            self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)
        runtime.ctx.wait_freezes.assert_not_called()

    def test_strict_freeze_timeout_is_capped_to_remaining_shared_budget(self):
        runtime = self.runtime()
        original = runtime.ctx.get_node_object.return_value.post_wait_freezes
        with patch('battle.runtime.runtime.time.monotonic', side_effect=[100, 100 + _ORDER_CHANGE_RETURN_TIMEOUT_S - 3, 100 + _ORDER_CHANGE_RETURN_TIMEOUT_S - 1]):
            self.assertTrue(runtime._execute_skills(action()))
        passed = runtime.ctx.wait_freezes.call_args.kwargs['wait_freezes_param']
        self.assertEqual(passed.timeout, 3000)
        self.assertEqual(original.timeout, 5000)  # 不回写已解析Pipeline配置。

    def test_over_budget_success_result_still_blocks_commit(self):
        runtime = self.runtime()
        with patch('battle.runtime.runtime.time.monotonic', side_effect=[100, 101, 101 + _ORDER_CHANGE_RETURN_TIMEOUT_S]):
            self.assertFalse(runtime._execute_skills(action()))
        self.assert_no_commit(runtime)

    def test_no_order_change_does_not_add_any_pipeline_or_freeze_wait(self):
        runtime = self.runtime()
        following = ServantSkillAction(1, 1)
        plain = BattleAction(None, (), (following,), skill_sequence=(following,))
        self.assertTrue(runtime._execute_skills(plain))
        runtime.ctx.run_task.assert_not_called()
        runtime.ctx.wait_freezes.assert_not_called()
        self.assertEqual(runtime.roster_servant_ids, INITIAL)

    def test_main_loop_propagates_stability_failure_without_opening_attack(self):
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
        runtime.ctx.wait_freezes.side_effect = None
        runtime.ctx.wait_freezes.return_value = False
        result = runtime.run()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, 'skill_execution_failed')
        self.assert_no_commit(runtime)
        runtime.executor.open_command_cards.assert_not_called()

    def test_pipeline_uses_explicit_skill_bar_region_and_bounded_delay_before_recognition(self):
        pipeline = json.loads((ROOT / 'assets/resource/base/pipeline/自动战斗_感知.json').read_text('utf-8'))
        entry, stable = pipeline[ENTRY], pipeline[STABLE]
        self.assertEqual(entry['action']['type'], 'DoNothing')
        self.assertEqual(entry['next'], [STABLE])
        self.assertEqual(entry['on_error'], [])
        self.assertGreaterEqual(entry['post_delay'], 3000)
        param = copy.deepcopy(pipeline['战斗_主界面']['recognition']['param'])
        param.setdefault('threshold', .7)
        self.assertEqual(stable['recognition']['param'], param)
        freeze = stable['post_wait_freezes']
        self.assertEqual(freeze['target'], [40, 545, 890, 80])
        self.assertEqual(freeze['time'], 1000)
        self.assertEqual(stable['action']['type'], 'DoNothing')
        self.assertEqual(stable['next'], [])
        self.assertEqual(stable['on_error'], [])
        budget = entry['post_delay'] + entry['timeout'] + 2 * freeze['timeout'] + stable['post_delay']
        self.assertLess(budget, 25000)
        self.assertEqual(entry['pre_delay'], 0)
        self.assertEqual(stable['pre_delay'], 0)


if __name__ == '__main__':
    unittest.main()
