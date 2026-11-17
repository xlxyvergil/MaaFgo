"""升级遮挡的延后资格来自真实剩余战斗预算，不模拟页面识别或设备。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.runtime.bond_stop_state import BondStopStates, bond_stops
from battle.runtime.formation_session import FormationSessions
from auto_battle_repeat_action import AutoBattleRepeatAction
from free_quest_action import FreeQuestRunner
import free_quest_action as free
import main_story_action as story


class DeferredStateTests(unittest.TestCase):
    def test_deferred_evidence_is_copied_latched_and_separate_from_stop(self):
        state = BondStopStates()
        token = state.begin(1)
        state.battle_started(1, 2, another_battle_planned=True)
        first, latest = np.full((2, 3, 3), 5, np.uint8), np.full((2, 3, 3), 8, np.uint8)
        deferred = state.defer(1, token, message="升级遮挡，下一场检查", image=first, latest_image=latest)
        first.fill(0)
        latest.fill(0)
        self.assertIsNone(state.get(1))
        self.assertIs(state.deferred(1), deferred)
        self.assertEqual((deferred.reason, deferred.battle), ("bond_upgrade_deferred", 2))
        self.assertTrue(np.all(deferred.image == 5))
        self.assertTrue(np.all(deferred.latest_image == 8))
        again = state.defer(1, token, message="不能覆盖首份证据", image=np.ones((2, 3, 3), np.uint8))
        self.assertIs(again, deferred)
        self.assertEqual(again.message, "升级遮挡，下一场检查")

    def test_unknown_budget_and_final_battle_cannot_defer(self):
        state = BondStopStates()
        self.assertFalse(state.can_defer(99))
        token = state.begin(1)
        self.assertFalse(state.can_defer(1))
        self.assertFalse(state.is_last_battle(1))
        self.assertIsNone(state.defer(1, token, message="默认不放行"))
        self.assertIsNone(state.continue_last_upgrade(1, token, message="未声明预算不能假定末场"))
        state.battle_started(1, 1, another_battle_planned=False)
        self.assertTrue(state.is_last_battle(1))
        self.assertIsNone(state.defer(1, token, message="末场不放行"))

    def test_last_upgrade_is_copied_once_and_separate_from_stop_and_deferral(self):
        state = BondStopStates()
        token = state.begin(1)
        state.battle_started(1, 2, another_battle_planned=False)
        first, latest = np.full((2, 3, 3), 5, np.uint8), np.full((2, 3, 3), 8, np.uint8)
        final = state.continue_last_upgrade(1, token, message="末场正常结算", image=first, latest_image=latest)
        first.fill(0)
        latest.fill(0)
        self.assertEqual((final.reason, final.battle), ("bond_upgrade_final", 2))
        self.assertIs(state.upgrade_continuation(1), final)
        self.assertIsNone(state.deferred(1))
        self.assertIsNone(state.get(1))
        self.assertFalse(state.can_defer(1))
        self.assertTrue(np.all(final.image == 5))
        self.assertTrue(np.all(final.latest_image == 8))
        again = state.continue_last_upgrade(1, token, message="不覆盖初次结算记录")
        self.assertIs(again, final)
        self.assertEqual(again.message, "末场正常结算")

    def test_final_continuation_isolated_and_cleared_by_next_battle_restart_and_finish(self):
        state = BondStopStates()
        first = state.begin(1, "first")
        second = state.begin(2, "second")
        for root, token in ((1, first), (2, second)):
            state.battle_started(root, 1, another_battle_planned=False)
            state.continue_last_upgrade(root, token, message=f"任务{root}末场")
        state.battle_started(1, 2)
        self.assertIsNone(state.upgrade_continuation(1))
        self.assertFalse(state.is_last_battle(1))
        self.assertIsNotNone(state.upgrade_continuation(2))
        state.battle_started(1, 3, another_battle_planned=False)
        state.continue_last_upgrade(1, first, message="新的末场")
        replacement = state.begin(1, "first")
        state.battle_started(1, 1, another_battle_planned=False)
        self.assertIsNone(state.upgrade_continuation(1))
        self.assertIsNone(state.continue_last_upgrade(1, first, message="拒绝过期回调"))
        self.assertIsNotNone(state.continue_last_upgrade(1, replacement, message="新会话末场"))
        state.stop_owner("first")
        self.assertIsNone(state.upgrade_continuation(1))
        self.assertIsNotNone(state.upgrade_continuation(2))
        state.finish(2)
        self.assertIsNone(state.upgrade_continuation(2))

    def test_pending_or_stopped_battle_cannot_be_marked_as_final_upgrade(self):
        state = BondStopStates()
        token = state.begin(1)
        state.battle_started(1, 1, another_battle_planned=True)
        self.assertIsNone(state.continue_last_upgrade(1, token, message="还有下一场"))
        state.battle_started(1, 2, another_battle_planned=False)
        stopped = state.latch(1, token, reason="bond_max", message="MAX优先停止")
        self.assertIsNone(state.continue_last_upgrade(1, token, message="不可覆盖已停止"))
        self.assertIs(state.get(1), stopped)

    def test_next_battle_clears_deferred_and_requires_new_explicit_budget(self):
        state = BondStopStates()
        token = state.begin(1)
        state.battle_started(1, 1, another_battle_planned=True)
        state.defer(1, token, message="第一场")
        state.battle_started(1, 2)
        self.assertIsNone(state.deferred(1))
        self.assertIsNone(state.upgrade_continuation(1))
        self.assertFalse(state.is_last_battle(1))
        self.assertFalse(state.can_defer(1))
        self.assertIsNone(state.defer(1, token, message="第二场未指定余战"))

    def test_root_isolation_restart_and_stale_token(self):
        state = BondStopStates()
        old = state.begin(1, "a")
        other = state.begin(2, "b")
        state.battle_started(1, 1, another_battle_planned=True)
        state.battle_started(2, 1, another_battle_planned=True)
        state.defer(2, other, message="另一个根任务")
        new = state.begin(1, "a")
        state.battle_started(1, 1, another_battle_planned=True)
        self.assertIsNone(state.defer(1, old, message="过期回调"))
        self.assertIsNone(state.deferred(1))
        self.assertIsNotNone(state.defer(1, new, message="新会话"))
        state.stop_owner("a")
        self.assertIsNone(state.deferred(1))
        self.assertEqual(state.deferred(2).message, "另一个根任务")

    def test_actual_stop_prevents_defer_and_remains_get_result(self):
        state = BondStopStates()
        token = state.begin(1)
        state.battle_started(1, 1, another_battle_planned=True)
        stopped = state.latch(1, token, reason="bond_max", message="MAX停止")
        self.assertFalse(state.can_defer(1))
        self.assertIsNone(state.defer(1, token, message="不覆盖停止"))
        self.assertIs(state.get(1), stopped)

    def test_formation_session_begin_does_not_overwrite_repeat_budget(self):
        root = 9920
        bond_stops.begin(root)
        self.addCleanup(bond_stops.finish, root)
        bond_stops.battle_started(root, 1, another_battle_planned=True)
        store = FormationSessions()
        store.begin(root)
        self.assertTrue(bond_stops.can_defer(root))


class Context:
    def __init__(self, root):
        self.root, self.nodes, self.entries = root, {}, []
        self.tasker = NS(stopping=False, controller=object())
        self.clear_hit_count = Mock()
        self.on_task = lambda: None

    def get_task_job(self):
        return NS(job_id=self.root)

    def get_node_data(self, name):
        return self.nodes.get(name, {})

    def override_pipeline(self, update):
        self.nodes.update(update)
        return True

    def run_task(self, name, pipeline_override=None):
        self.entries.append(name)
        self.on_task()
        return NS(status=NS(succeeded=True, failed=False), entry=name)


class BattleBudgetTests(unittest.TestCase):
    def test_story_only_declares_last_battle_on_the_final_progression_stage(self):
        ctx = Context(9935)
        bond_stops.begin(ctx.root)
        self.addCleanup(bond_stops.finish, ctx.root)
        runner = object.__new__(story.StoryRunner)
        runner.ctx, runner.limit, runner.completed, runner.battles = ctx, 2, 0, 0
        runner._dialogue_choices = {}
        runner.reset_restriction_state = Mock()
        runner.locate = lambda: None
        runner.settle = Mock()
        observed = []
        def fight():
            observed.append((bond_stops.can_defer(ctx.root), bond_stops.is_last_battle(ctx.root)))
        runner.fight = fight
        message = runner.run()
        self.assertEqual(observed, [(False, False), (False, True)])
        self.assertEqual((runner.completed, runner.battles), (2, 2))
        self.assertEqual(runner.settle.call_count, 2)
        self.assertIn("达到推进上限", message)

    def test_repeat_deferral_never_adds_battles_and_final_battle_cannot_defer(self):
        for count in (1, 3):
            with self.subTest(count=count):
                ctx = Context(9930)
                self.addCleanup(bond_stops.finish, ctx.root)
                observations = []
                def observe():
                    eligible = bond_stops.can_defer(ctx.root)
                    prior = bond_stops.deferred(ctx.root)
                    deferred = bond_stops.defer(ctx.root, bond_stops.ensure(ctx.root), message="本场升级遮挡")
                    observations.append((eligible, prior, deferred is not None, bond_stops.get(ctx.root)))
                ctx.on_task = observe
                result = AutoBattleRepeatAction().run(ctx, NS(custom_action_param={"battle_count": count}))
                self.assertTrue(result.success)
                self.assertEqual(len(ctx.entries), count)
                self.assertEqual([row[0] for row in observations], [True] * (count - 1) + [False])
                self.assertTrue(all(row[1] is None and row[3] is None for row in observations))
                self.assertEqual([row[2] for row in observations], [True] * (count - 1) + [False])

    def test_free_limit_uses_actual_battles_and_deferral_preserves_counting(self):
        for limit in (1, 3):
            with self.subTest(limit=limit):
                ctx = Context(9940)
                bond_stops.begin(ctx.root)
                self.addCleanup(bond_stops.finish, ctx.root)
                runner = object.__new__(FreeQuestRunner)
                runner.ctx, runner.limit, runner.battles, runner.completed = ctx, limit, 0, 0
                runner.pending_clear = False
                runner.locate = lambda: None
                runner.settle = Mock()
                runner.frame = lambda: None
                runner.panel = lambda image: False
                observed = []
                def fight():
                    self.assertIsNone(bond_stops.deferred(ctx.root))
                    eligible = bond_stops.can_defer(ctx.root)
                    result = bond_stops.defer(ctx.root, bond_stops.ensure(ctx.root), message="本场升级遮挡")
                    observed.append((eligible, result is not None))
                    self.assertIsNone(bond_stops.get(ctx.root))
                runner.fight = fight
                message = runner.run()
                self.assertEqual(observed, [(True, True)] * (limit - 1) + [(False, False)])
                self.assertEqual(runner.battles, limit)
                self.assertEqual(runner.completed, 0)
                self.assertEqual(runner.settle.call_count, limit)
                self.assertIn(f"完成战斗 {limit} 场", message)


class FreeCompletionNoticeTests(unittest.TestCase):
    def context(self):
        ctx = Context(9950)
        ctx.clone = lambda: ctx
        ctx.run_task = Mock(return_value=NS(status=NS(succeeded=True)))
        self.addCleanup(bond_stops.finish, ctx.root)
        return ctx

    def test_natural_completion_retains_success_and_reports_unverified_target_evidence(self):
        normal = "地图遍历完成，未找到未完成数字标记；完成战斗 1 场"
        for save_error in (False, True):
            with self.subTest(save_error=save_error):
                ctx = self.context()
                def natural_end():
                    bond_stops.battle_started(ctx.root, 1, another_battle_planned=True)
                    deferred = bond_stops.defer(ctx.root, bond_stops.ensure(ctx.root), message="本场升级遮挡")
                    if save_error:
                        deferred.screenshot_error = "disk full"
                    else:
                        deferred.screenshot_path = "debug/screencap/deferred.png"
                        deferred.latest_screenshot_path = "debug/screencap/deferred_latest.png"
                    return normal
                with patch.object(free, "FreeQuestRunner") as runner:
                    runner.return_value.run.side_effect = natural_end
                    result = free.FreeQuestAction().run(ctx, NS())
                    runner.return_value.run.assert_called_once()
                self.assertTrue(result.success)
                self.assertIsNone(bond_stops.get(ctx.root))
                self.assertEqual(bond_stops.battle_number(ctx.root), 1)
                ctx.run_task.assert_called_once()
                args, kwargs = ctx.run_task.call_args
                self.assertEqual(args[0], "Free-结果提示")
                message = kwargs["pipeline_override"]["Free-结果提示"]["focus"]["Node.Action.Starting"]
                self.assertIn(normal, message)
                self.assertIn("羁绊目标尚未复核；任务按原条件结束，未追加战斗", message)
                if save_error:
                    self.assertIn("截图保存失败：disk full", message)
                else:
                    self.assertIn("debug/screencap/deferred.png", message)
                    self.assertIn("debug/screencap/deferred_latest.png", message)

    def test_completion_without_deferred_keeps_original_message(self):
        ctx = self.context()
        normal = "地图遍历完成，未找到未完成数字标记；完成战斗 0 场"
        with patch.object(free, "FreeQuestRunner") as runner:
            runner.return_value.run.return_value = normal
            result = free.FreeQuestAction().run(ctx, NS())
        self.assertTrue(result.success)
        message = ctx.run_task.call_args.kwargs["pipeline_override"]["Free-结果提示"]["focus"]["Node.Action.Starting"]
        self.assertEqual(message, normal)

    def test_final_upgrade_completion_does_not_report_pending_next_battle_recheck(self):
        ctx = self.context()
        normal = "达到战斗上限：确认 CLEAR 1 个 Free 本，完成战斗 2 场"
        def completed():
            token = bond_stops.ensure(ctx.root)
            bond_stops.battle_started(ctx.root, 2, another_battle_planned=False)
            self.assertIsNotNone(bond_stops.continue_last_upgrade(ctx.root, token, message="末场正常结算"))
            return normal
        with patch.object(free, "FreeQuestRunner") as runner:
            runner.return_value.run.side_effect = completed
            result = free.FreeQuestAction().run(ctx, NS())
        self.assertTrue(result.success)
        self.assertIsNone(bond_stops.get(ctx.root))
        self.assertIsNone(bond_stops.deferred(ctx.root))
        message = ctx.run_task.call_args.kwargs["pipeline_override"]["Free-结果提示"]["focus"]["Node.Action.Starting"]
        self.assertEqual(message, normal)


if __name__ == "__main__":
    unittest.main()
