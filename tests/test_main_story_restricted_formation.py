"""主线受限编队的本关状态、配置隔离和失败清理；不驱动设备。"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from test_main_story import Context
from main_story_action import StoryRunner, StoryStopped
from battle.runtime.formation_session import sessions

ROOT = Path(__file__).resolve().parents[1]


def merge(target, update):
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


class CloneContext(Context):
    """节点配置隔离，任务与设备状态按真实Context共享。"""

    def __init__(self, frames=(), config=None):
        super().__init__(frames, config)
        self.clones = []
        self.executions = []
        self.fail_tasks = set()

    def clone(self):
        child = copy.copy(self)
        child.nodes = copy.deepcopy(self.nodes)
        self.clones.append(child)
        return child

    def override_pipeline(self, override):
        merge(self.nodes, override)
        self.override = copy.deepcopy(override)
        return True

    def run_task(self, name, pipeline_override=None):
        effective = copy.deepcopy(self.nodes)
        merge(effective, pipeline_override or {})
        self.tasks.append((name, copy.deepcopy(pipeline_override)))
        self.executions.append((name, effective))
        return NS(status=NS(succeeded=name not in self.fail_tasks))


def runner(frames=(), config=None):
    context = CloneContext(frames, config)
    clock = [0.0]
    runtime = StoryRunner(context, clock=lambda: clock[0],
                          sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return runtime, context


class RestrictedStateTests(unittest.TestCase):
    def test_automatic_restriction_handling_defaults_enabled(self):
        runtime, _ = runner()
        self.assertTrue(runtime.auto_handle_restrictions)
        self.assertFalse(runtime.current_restricted)

    def test_disabled_restriction_stops_before_any_click(self):
        runtime, context = runner(config={"auto_handle_restrictions": False})
        with self.assertRaisesRegex(StoryStopped, "手动|限制"):
            runtime.check_formation_restriction({"主线-编制限制"})
        self.assertEqual(context.actions, [])
        self.assertEqual(context.tasks, [])

    def test_enabled_restriction_title_marks_only_current_quest(self):
        runtime, context = runner()
        runtime.check_formation_restriction({"主线-编制限制"})
        self.assertTrue(runtime.current_restricted)
        self.assertEqual(context.actions, [])
        self.assertEqual(context.tasks, [])

    def test_reset_clears_restriction_and_automatic_formation_state(self):
        runtime, _ = runner()
        runtime.current_restricted = True
        runtime._restriction_needs_auto = True
        runtime._restriction_auto_done = True
        runtime.reset_restriction_state()
        self.assertFalse(runtime.current_restricted)
        self.assertFalse(runtime._restriction_needs_auto)
        self.assertFalse(runtime._restriction_auto_done)

    def test_each_new_quest_resets_before_locating(self):
        runtime, _ = runner(config={"quest_limit": 2})
        before, fighting = [], []
        def locate():
            before.append((runtime.current_restricted, runtime._restriction_needs_auto,
                           runtime._restriction_auto_done))
            if len(before) == 1:
                runtime.current_restricted = True
                runtime._restriction_needs_auto = True
                runtime._restriction_auto_done = True
            return None
        with patch.object(runtime, "locate", side_effect=locate), \
             patch.object(runtime, "fight", side_effect=lambda: fighting.append(runtime.current_restricted)), \
             patch.object(runtime, "settle"):
            runtime.run()
        self.assertEqual(before, [(False, False, False), (False, False, False)])
        self.assertEqual(fighting, [True, False])

    def test_source_cancel_then_game_auto_formation_and_decide(self):
        runtime, context = runner()
        steps = [
            {"主线限制-取消编队来源"},
            {"进本-队伍确认", "主线限制-打开自动编队"},
            {"主线限制-自动编成确认"},
            {"主线限制-编队决定"},
        ]
        for frame in steps:
            self.assertTrue(runtime._handle_restricted_formation(frame))
        self.assertFalse(runtime._handle_restricted_formation({"进本-队伍确认"}))
        self.assertEqual([name for name, _ in context.actions], [
            "主线限制-取消编队来源", "主线限制-打开自动编队",
            "主线限制-自动编成确认", "主线限制-编队决定",
        ])
        self.assertTrue(runtime.current_restricted)
        self.assertTrue(runtime._restriction_auto_done)
        self.assertEqual(context.tasks, [])

    def test_existing_fixed_member_does_not_rebuild_formation(self):
        runtime, context = runner()
        self.assertFalse(runtime._handle_restricted_formation({
            "进本-队伍确认", "主线限制-固定成员", "主线限制-打开自动编队"}))
        self.assertTrue(runtime.current_restricted)
        self.assertFalse(runtime._restriction_needs_auto)
        self.assertEqual(context.actions, [])

    def test_empty_required_slot_opens_game_auto_formation(self):
        runtime, context = runner()
        self.assertTrue(runtime._handle_restricted_formation({
            "进本-队伍确认", "主线限制-空槽", "主线限制-打开自动编队"}))
        self.assertTrue(runtime.current_restricted)
        self.assertEqual([name for name, _ in context.actions], ["主线限制-打开自动编队"])

    def test_remaining_required_slot_after_auto_formation_stops(self):
        runtime, context = runner()
        runtime._handle_restricted_formation({"主线限制-自动编成确认"})
        context.actions.clear()
        with self.assertRaisesRegex(StoryStopped, "仍有|空槽|手动"):
            runtime._handle_restricted_formation({
                "进本-队伍确认", "主线限制-空槽", "主线限制-打开自动编队"})
        self.assertEqual(context.actions, [])

    def test_decide_has_priority_over_empty_slot_during_editing(self):
        runtime, context = runner()
        runtime.current_restricted = runtime._restriction_auto_done = True
        runtime._restriction_needs_decide = True
        self.assertTrue(runtime._handle_restricted_formation({
            "主线限制-编队决定", "进本-队伍确认", "主线限制-空槽"}))
        self.assertEqual([name for name, _ in context.actions], ["主线限制-编队决定"])

    def test_confirmation_may_return_directly_to_confirmation_page(self):
        runtime, context = runner()
        self.assertTrue(runtime._handle_restricted_formation({"主线限制-自动编成确认"}))
        self.assertFalse(runtime._handle_restricted_formation({"进本-队伍确认"}))
        self.assertTrue(runtime.current_restricted)
        self.assertFalse(runtime._restriction_needs_decide)
        self.assertEqual([name for name, _ in context.actions], ["主线限制-自动编成确认"])

    def test_disabled_setting_rejects_each_restriction_signal_before_action(self):
        for signal in ("主线限制-关闭提示", "主线限制-取消编队来源",
                       "主线限制-编成条件未满足",
                       "主线限制-自动编成确认", "进本-自动编队确认",
                       "主线限制-空槽", "主线限制-固定成员"):
            with self.subTest(signal=signal):
                runtime, context = runner(config={"auto_handle_restrictions": False})
                with self.assertRaisesRegex(StoryStopped, "手动|限制"):
                    runtime._handle_restricted_formation({signal, "进本-队伍确认"})
                self.assertEqual(context.actions, [])
                self.assertEqual(context.tasks, [])

    def test_auto_formation_can_enter_battle_without_decide_screen(self):
        runtime, context = runner()
        self.assertTrue(runtime._handle_restricted_formation({"主线限制-自动编成确认"}))
        self.assertFalse(runtime._handle_restricted_formation({"进本-战斗主界面已出现"}))
        self.assertTrue(runtime.current_restricted)
        self.assertFalse(runtime._restriction_needs_auto)
        self.assertFalse(runtime._restriction_needs_decide)
        self.assertEqual([name for name, _ in context.actions], ["主线限制-自动编成确认"])

    def test_class_affinity_notice_closes_before_visible_decide(self):
        runtime, context = runner()
        runtime.current_restricted = True
        runtime._restriction_needs_decide = True
        self.assertTrue(runtime._handle_restricted_formation({
            "主线限制-关闭职阶相性", "主线限制-编队决定"}))
        self.assertEqual([name for name, _ in context.actions], ["主线限制-关闭职阶相性"])
        self.assertTrue(runtime._restriction_needs_decide)
        self.assertTrue(runtime._handle_restricted_formation({"主线限制-编队决定"}))
        self.assertEqual([name for name, _ in context.actions], [
            "主线限制-关闭职阶相性", "主线限制-编队决定"])

    def test_bbc_restriction_stops_without_silently_switching_backend(self):
        runtime, context = runner(config={"battle_mode": "bbc"})
        with self.assertRaisesRegex(StoryStopped, "原生|手动"):
            runtime._handle_restricted_formation({"主线限制-自动编成确认"})
        self.assertEqual(context.actions, [])
        self.assertEqual(context.tasks, [])

    def test_repeated_same_popup_has_three_click_budget(self):
        runtime, context = runner()
        for _ in range(3):
            runtime._handle_restricted_formation({"主线限制-关闭提示"})
        with self.assertRaisesRegex(StoryStopped, "重复|停止"):
            runtime._handle_restricted_formation({"主线限制-关闭提示"})
        self.assertEqual(len(context.actions), 3)

    def test_combined_action_budget_is_bounded_and_reset_per_quest(self):
        runtime, context = runner()
        # Different actions can alternate; the overall guard still rejects action 21.
        runtime._restriction_action_counts = {f"earlier-{i}": 1 for i in range(20)}
        with self.assertRaisesRegex(StoryStopped, "重复|停止"):
            runtime._handle_restricted_formation({"主线限制-关闭提示"})
        self.assertEqual(context.actions, [])
        runtime.reset_restriction_state()
        self.assertTrue(runtime._handle_restricted_formation({"主线限制-关闭提示"}))
        self.assertEqual(len(context.actions), 1)

    def test_card_text_outside_formation_does_not_mark_restricted(self):
        for signal in ("主线限制-空槽", "主线限制-固定成员"):
            with self.subTest(signal=signal):
                runtime, context = runner()
                self.assertFalse(runtime._handle_restricted_formation({signal}))
                self.assertFalse(runtime.current_restricted)
                self.assertEqual(context.actions, [])


class RestrictedOptionTests(unittest.TestCase):
    def test_switch_defaults_on_and_maps_to_boolean_config(self):
        options = json.loads((ROOT / "assets/options/自动推主线.json").read_text("utf8"))["option"]
        switch = options["主线-自动处理编队限制"]
        self.assertEqual(switch["type"], "switch")
        self.assertEqual(switch["label"], "自动处理编队限制情况")
        self.assertEqual(switch["default_case"], "Yes")
        cases = {case["name"]: case for case in switch["cases"]}
        for case, expected in (("Yes", True), ("No", False)):
            value = cases[case]["pipeline_override"]["主线-配置"]["attach"]["auto_handle_restrictions"]
            self.assertIs(value, expected)

    def test_switch_only_exposed_for_native_backend(self):
        options = json.loads((ROOT / "assets/options/自动推主线.json").read_text("utf8"))["option"]
        cases = {case["name"]: case for case in options["主线-战斗方式"]["cases"]}
        self.assertIn("主线-自动处理编队限制", cases["native"]["option"])
        self.assertNotIn("主线-自动处理编队限制", cases["bbc"]["option"])


class RestrictedContextIsolationTests(unittest.TestCase):
    def setUp(self):
        sessions.finish(12345)
        self.addCleanup(sessions.finish, 12345)

    def make_runtime(self, *, restricted):
        runtime, context = runner([{"进本-战斗主界面已出现"}])
        context.nodes["进本-编队处理"]["next"] = ["执行手动编队检查"]
        context.nodes["原生自动战斗入口"]["action"]["param"]["custom_action_param"] = {
            "chaldea_import_source": "missing-team.json",
            "chaldea_share": {"actions": [{"bad": "must not read"}]},
            "plan": {"turns": [{"np_order": [1]}]},
            "max_turns": 99,
        }
        runtime.current_restricted = restricted
        return runtime, context

    def assert_session_finished(self, context):
        tokens = [child.nodes["编队身份-会话"]["attach"]["session_id"]
                  for child in context.clones
                  if child.nodes.get("编队身份-会话", {}).get("attach", {}).get("session_id")]
        self.assertTrue(tokens, "fight should initialize its own session")
        for token in tokens:
            self.assertFalse(sessions.valid(12345, token))

    def test_restricted_battle_bypasses_chaldea_and_bond_without_mutating_parent(self):
        runtime, context = self.make_runtime(restricted=True)
        before = copy.deepcopy(context.nodes)
        with patch.object(runtime, "reach_formation"):
            runtime.fight()
        self.assertIs(runtime.ctx, context)
        self.assertEqual(context.nodes, before)
        self.assertEqual([name for name, _ in context.tasks], ["进本流程", "原生自动战斗入口"])
        entry, battle = (nodes for _, nodes in context.executions)
        self.assertEqual(entry["进本-编队处理"]["next"], ["进本-点击队伍确认"])
        self.assertEqual(entry["进本-点击队伍确认"]["next"], ["原生战斗-校验茶壶"])
        self.assertEqual(entry["原生战斗-校验茶壶"]["next"], ["编队身份-采集"])
        param = battle["原生自动战斗入口"]["action"]["param"]["custom_action_param"]
        self.assertEqual(json.loads(param) if isinstance(param, str) else param,
                         {"native_battle_formation_reset": True})
        self.assert_session_finished(context)

    def test_normal_battle_keeps_original_chaldea_settings(self):
        runtime, context = self.make_runtime(restricted=False)
        before = copy.deepcopy(context.nodes)
        with patch.object(runtime, "reach_formation"):
            runtime.fight()
        self.assertIs(runtime.ctx, context)
        self.assertEqual(context.nodes, before)
        entry, battle = (nodes for _, nodes in context.executions)
        self.assertEqual(entry["进本-编队处理"]["next"], ["执行手动编队检查"])
        self.assertEqual(battle["原生自动战斗入口"]["action"], before["原生自动战斗入口"]["action"])
        self.assert_session_finished(context)

    def test_restriction_discovered_during_handoff_is_applied_before_entry(self):
        runtime, context = self.make_runtime(restricted=False)
        def discover():
            runtime.check_formation_restriction({"主线-编制限制"})
        with patch.object(runtime, "reach_formation", side_effect=discover):
            runtime.fight()
        entry = context.executions[0][1]
        self.assertEqual(entry["进本-编队处理"]["next"], ["进本-点击队伍确认"])
        self.assertTrue(runtime.current_restricted)
        self.assert_session_finished(context)

    def test_next_normal_battle_restores_original_plan_after_restricted_battle(self):
        runtime, context = self.make_runtime(restricted=True)
        before = copy.deepcopy(context.nodes)
        with patch.object(runtime, "reach_formation"):
            runtime.fight()
            runtime.reset_restriction_state()
            runtime.fight()
        self.assertEqual(context.nodes, before)
        first_entry, first_battle, next_entry, next_battle = (nodes for _, nodes in context.executions)
        self.assertEqual(first_entry["进本-编队处理"]["next"], ["进本-点击队伍确认"])
        self.assertEqual(next_entry["进本-编队处理"]["next"], ["执行手动编队检查"])
        self.assertEqual(next_battle["原生自动战斗入口"]["action"], before["原生自动战斗入口"]["action"])
        first_param = first_battle["原生自动战斗入口"]["action"]["param"]["custom_action_param"]
        self.assertEqual(json.loads(first_param) if isinstance(first_param, str) else first_param,
                         {"native_battle_formation_reset": True})
        self.assert_session_finished(context)

    def test_failure_during_formation_restores_parent_and_finishes_session(self):
        runtime, context = self.make_runtime(restricted=True)
        before = copy.deepcopy(context.nodes)
        with patch.object(runtime, "reach_formation", side_effect=StoryStopped("formation failed")):
            with self.assertRaisesRegex(StoryStopped, "formation failed"):
                runtime.fight()
        self.assertIs(runtime.ctx, context)
        self.assertEqual(context.nodes, before)
        self.assert_session_finished(context)

    def test_failed_battle_restores_parent_and_finishes_session(self):
        runtime, context = self.make_runtime(restricted=True)
        before = copy.deepcopy(context.nodes)
        context.fail_tasks.add("原生自动战斗入口")
        with patch.object(runtime, "reach_formation"):
            with self.assertRaisesRegex(StoryStopped, "流程失败"):
                runtime.fight()
        self.assertIs(runtime.ctx, context)
        self.assertEqual(context.nodes, before)
        self.assert_session_finished(context)

    def test_failed_entry_does_not_run_battle_and_restores_context(self):
        runtime, context = self.make_runtime(restricted=True)
        before = copy.deepcopy(context.nodes)
        context.fail_tasks.add("进本流程")
        with patch.object(runtime, "reach_formation"):
            with self.assertRaisesRegex(StoryStopped, "流程失败"):
                runtime.fight()
        self.assertIs(runtime.ctx, context)
        self.assertEqual(context.nodes, before)
        self.assertEqual([name for name, _ in context.tasks], ["进本流程"])
        self.assert_session_finished(context)

    def test_start_rejection_then_auto_formation_updates_next_entry_and_battle(self):
        runtime, context = self.make_runtime(restricted=False)
        context.frames[:] = [
            {"主线限制-编成条件未满足"},
            {"进本-队伍确认", "主线限制-打开自动编队"},
            {"主线限制-自动编成确认"},
            {"主线限制-编队决定"},
            *[{"进本-队伍确认"}] * 3,
            {"进本-战斗主界面已出现"},
        ]
        before = copy.deepcopy(context.nodes)
        with patch.object(runtime, "reach_formation"):
            runtime.fight()
        self.assertEqual([name for name, _ in context.tasks], [
            "进本流程", "进本流程", "原生自动战斗入口"])
        first, second, battle = (nodes for _, nodes in context.executions)
        self.assertEqual(first["进本-编队处理"]["next"], ["执行手动编队检查"])
        self.assertEqual(second["进本-编队处理"]["next"], ["进本-点击队伍确认"])
        self.assertEqual(second["进本-队伍确认"]["next"][0], "进本-助战兜底跳过编队")
        self.assertEqual(second["进本-实际点击队伍确认"]["next"], [])
        self.assertEqual(json.loads(battle["原生自动战斗入口"]["action"]["param"]["custom_action_param"]),
                         {"native_battle_formation_reset": True})
        self.assertEqual(context.nodes, before)
        self.assertEqual([name for name, _ in context.actions], [
            "主线限制-编成条件未满足", "主线限制-打开自动编队",
            "主线限制-自动编成确认", "主线限制-编队决定"])
        self.assert_session_finished(context)

    def test_fixed_marker_after_start_changes_subsequent_entry_without_rebuilding(self):
        runtime, context = self.make_runtime(restricted=False)
        context.frames[:] = [
            *[{"进本-队伍确认", "主线限制-固定成员"}] * 3,
            {"进本-战斗主界面已出现"},
        ]
        with patch.object(runtime, "reach_formation"):
            runtime.fight()
        self.assertEqual([name for name, _ in context.tasks], [
            "进本流程", "进本流程", "原生自动战斗入口"])
        second = context.executions[1][1]
        self.assertEqual(second["进本-编队处理"]["next"], ["进本-点击队伍确认"])
        self.assertEqual(context.actions, [])
        self.assert_session_finished(context)

    def test_start_retry_budget_is_three_and_failure_cleans_context(self):
        runtime, context = self.make_runtime(restricted=True)
        context.frames[:] = [{"进本-队伍确认"}]
        with patch.object(runtime, "reach_formation"):
            with self.assertRaisesRegex(StoryStopped, "重试3次"):
                runtime.fight()
        self.assertEqual([name for name, _ in context.tasks], ["进本流程"] * 3)
        self.assertIs(runtime.ctx, context)
        self.assert_session_finished(context)


if __name__ == "__main__":
    unittest.main()
