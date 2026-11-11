"""幕间任务配置、跨幕间小关计数与中断边界；不连接设备。"""
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.runtime.bond_stop_state import bond_stops
from interlude_action import InterludeRunner, recognition_detail
from main_story_action import StoryStopped
from test_main_story_restricted_formation import CloneContext


def load(relative):
    return json.loads((ROOT / relative).read_text("utf-8"))


LIST = {"幕间-列表界面"}
MAP = {"幕间-返回列表"}


class InterludeContext(CloneContext):
    def __init__(self, frames=(), config=None):
        super().__init__(frames, config)
        self.scan_detail = {"row_count": 1, "locked_count": 1, "uncertain_count": 0}

    def run_recognition(self, name, image, pipeline_override=None):
        if name == "幕间-条目扫描":
            self.recognitions.append(name)
            return NS(hit=True, best_result=NS(detail=json.dumps(self.scan_detail)))
        return super().run_recognition(name, image)

    def run_action(self, name, box, pipeline_override=None):
        if name not in self.nodes:
            raise AssertionError(f"动作引用了未定义的 Pipeline 节点：{name}")
        return super().run_action(name, box, pipeline_override)


def runner(frames=None, config=None, main_config=None):
    context = InterludeContext([LIST] if frames is None else frames, main_config)
    context.nodes["幕间-配置"]["attach"].update(config or {})
    clock = [0.0]
    runtime = InterludeRunner(context, clock=lambda: clock[0],
                             sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return runtime, context


TARGET = ("幕间-测试条目", object())


class ConfigurationTests(unittest.TestCase):
    def test_task_is_imported_for_current_cn_resource_and_android(self):
        interface = load("assets/interface.json")
        task = load("assets/tasks/自动幕间副本.json")["task"][0]
        self.assertIn("tasks/自动幕间副本.json", interface["import"])
        self.assertIn("options/自动幕间副本.json", interface["import"])
        self.assertEqual(task["name"], "自动幕间副本")
        self.assertEqual(task["entry"], "自动幕间副本调度")
        self.assertEqual(task["controller"], ["安卓设备"])
        cn_resources = {item["name"] for item in interface["resource"]
                        if "resource/cn" in item["path"]}
        self.assertEqual(set(task["resource"]), cn_resources)

    def test_basic_battle_options_are_available(self):
        task = load("assets/tasks/自动幕间副本.json")["task"][0]
        self.assertTrue({"幕间-挑战次数上限", "吃苹果", "原生自动战斗跳过剧情",
                         "幕间-自动处理编队限制", "原生自动战斗使用Chaldea队伍",
                         "原生自动战斗助战方式", "原生自动战斗高级策略"}.issubset(task["option"]))

    def test_interlude_options_write_only_interlude_configuration(self):
        options = load("assets/options/自动幕间副本.json")["option"]
        overrides = []
        for option in options.values():
            if "pipeline_override" in option:
                overrides.append(option["pipeline_override"])
            overrides.extend(case["pipeline_override"] for case in option.get("cases", [])
                             if "pipeline_override" in case)
        self.assertTrue(overrides)
        self.assertTrue(all(set(value) == {"幕间-配置"} for value in overrides))
        self.assertNotIn("主线-战斗方式", options)

    def test_default_limit_is_999_and_restrictions_are_enabled(self):
        options = load("assets/options/自动幕间副本.json")["option"]
        self.assertEqual(options["幕间-挑战次数上限"]["inputs"][0]["default"], "999")
        self.assertEqual(options["幕间-自动处理编队限制"]["default_case"], "Yes")
        runtime, _ = runner()
        self.assertEqual(runtime.limit, 999)
        self.assertEqual(runtime.mode, "native")
        self.assertTrue(runtime.auto_handle_restrictions)

    def test_main_story_limit_and_restriction_settings_are_isolated(self):
        runtime, context = runner(config={"quest_limit": 3, "auto_handle_restrictions": False},
                                  main_config={"quest_limit": 7, "auto_handle_restrictions": True,
                                               "battle_mode": "bbc"})
        self.assertEqual(runtime.limit, 3)
        self.assertEqual(runtime.mode, "native")
        self.assertFalse(runtime.auto_handle_restrictions)
        self.assertEqual(context.nodes["主线-配置"]["attach"]["quest_limit"], 7)
        self.assertTrue(context.nodes["主线-配置"]["attach"]["auto_handle_restrictions"])

    def test_limit_accepts_boundaries_and_rejects_out_of_range(self):
        for value in (1, 999):
            with self.subTest(value=value):
                runtime, _ = runner(config={"quest_limit": value})
                self.assertEqual(runtime.limit, value)
        for value in (0, 1000, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner(config={"quest_limit": value})

    def test_main_story_revival_settings_do_not_apply_to_interludes(self):
        runtime, context = runner(main_config={"use_spirit_stone": True,
                                              "retry_without_spirit_stone": True})
        self.assertFalse(runtime.use_spirit_stone)
        self.assertFalse(runtime.retry_without_spirit_stone)
        self.assertTrue(context.nodes["主线-配置"]["attach"]["use_spirit_stone"])
        self.assertTrue(context.nodes["主线-配置"]["attach"]["retry_without_spirit_stone"])

    def test_scan_report_uses_sdk_best_result_detail(self):
        report = {"row_count": 2, "uncertain_count": 1}
        response = NS(best_result=NS(detail=json.dumps(report)), detail={"uncertain_count": 0})
        self.assertEqual(recognition_detail(response), report)


class ProgressTests(unittest.TestCase):
    def tearDown(self):
        bond_stops.finish(12345)

    def test_different_interludes_continue_until_small_quest_limit(self):
        runtime, _ = runner(config={"quest_limit": 3})
        targets = [("幕间-第一项", object()), ("幕间-第一项", object()),
                   ("幕间-第二项", object())]
        with patch.object(runtime, "locate", side_effect=targets) as locate, \
             patch.object(runtime, "enter", return_value="battle") as enter, \
             patch.object(runtime, "fight") as fight, patch.object(runtime, "settle") as settle:
            runtime.run()
        self.assertEqual((runtime.completed, runtime.battles), (3, 3))
        self.assertEqual(locate.call_count, 3)
        self.assertEqual([call.args[0] for call in enter.call_args_list], targets)
        self.assertEqual((fight.call_count, settle.call_count), (3, 3))

    def test_pure_story_counts_toward_limit_without_starting_battle(self):
        runtime, _ = runner(config={"quest_limit": 2})
        with patch.object(runtime, "locate", return_value=TARGET), \
             patch.object(runtime, "enter", side_effect=("story", "battle")), \
             patch.object(runtime, "fight") as fight, patch.object(runtime, "settle"):
            runtime.run()
        self.assertEqual((runtime.completed, runtime.battles), (2, 1))
        fight.assert_called_once()

    def test_entering_story_stage_map_does_not_consume_challenge_count(self):
        runtime, _ = runner(config={"quest_limit": 1})
        with patch.object(runtime, "locate", return_value=TARGET) as locate, \
             patch.object(runtime, "enter", side_effect=("map", "battle")), \
             patch.object(runtime, "fight") as fight, patch.object(runtime, "settle"):
            runtime.run()
        self.assertEqual(locate.call_count, 2)
        self.assertEqual((runtime.completed, runtime.battles), (1, 1))
        fight.assert_called_once()

    def test_no_available_entry_finishes_without_battle_or_progress(self):
        runtime, context = runner()
        with patch.object(runtime, "locate", return_value=None), \
             patch.object(runtime, "enter") as enter, patch.object(runtime, "fight") as fight:
            runtime.run()
        self.assertEqual((runtime.completed, runtime.battles), (0, 0))
        enter.assert_not_called()
        fight.assert_not_called()
        self.assertEqual(context.actions, [])

    def test_failed_settlement_never_counts_or_starts_another_quest(self):
        runtime, _ = runner(config={"quest_limit": 2})
        with patch.object(runtime, "locate", return_value=TARGET) as locate, \
             patch.object(runtime, "enter", return_value="battle"), \
             patch.object(runtime, "fight"), \
             patch.object(runtime, "settle", side_effect=StoryStopped("未确认返回幕间列表")), \
             self.assertRaises(StoryStopped):
            runtime.run()
        self.assertEqual((runtime.completed, runtime.battles), (0, 0))
        self.assertEqual(locate.call_count, 1)

    def test_restriction_applies_to_current_small_quest_then_resets(self):
        runtime, _ = runner(config={"quest_limit": 2})
        before, fighting = [], []

        def locate():
            before.append(runtime.current_restricted)
            if len(before) == 1:
                runtime.current_restricted = True
            return TARGET

        with patch.object(runtime, "locate", side_effect=locate), \
             patch.object(runtime, "enter", return_value="battle"), \
             patch.object(runtime, "fight", side_effect=lambda: fighting.append(runtime.current_restricted)), \
             patch.object(runtime, "settle"):
            runtime.run()
        self.assertEqual(before, [False, False])
        self.assertEqual(fighting, [True, False])


class NavigationSafetyTests(unittest.TestCase):
    def test_only_total_list_or_interlude_stage_is_a_valid_entry(self):
        runtime, _ = runner()
        self.assertTrue(runtime.panel(LIST))
        self.assertTrue(runtime.panel(MAP))
        self.assertTrue(runtime.on_map(MAP))
        self.assertFalse(runtime.on_map({"主线-地图菜单", "主线-地图控件"}))
        self.assertFalse(runtime.panel({"主线-副本列表信息"}))

    def test_unknown_start_stops_before_any_device_action(self):
        runtime, context = runner([set()], {"transition_timeout": 10})
        with self.assertRaises(StoryStopped):
            runtime.run()
        self.assertEqual((runtime.completed, runtime.battles), (0, 0))
        self.assertEqual(context.actions, [])
        self.assertEqual(context.tasks, [])

    def test_unknown_navigation_times_out_without_recovery_clicks(self):
        runtime, context = runner([set()], {"transition_timeout": 10})
        with self.assertRaises(StoryStopped):
            runtime.locate()
        self.assertEqual(context.actions, [])
        self.assertEqual(context.tasks, [])

    def test_unknown_screen_after_entry_is_not_clicked_as_a_loading_page(self):
        runtime, context = runner([set()], {"transition_timeout": 10})
        target = ("幕间-可挑战条目", NS(box=[950, 200, 1, 1]))
        with self.assertRaises(StoryStopped):
            runtime.enter(target)
        self.assertEqual([name for name, _ in context.actions], ["幕间-可挑战条目"])
        self.assertEqual(context.tasks, [])
        self.assertEqual((runtime.completed, runtime.battles), (0, 0))

    def test_empty_newly_opened_stage_stops_instead_of_reopening_same_interlude(self):
        runtime, context = runner([MAP])
        runtime._map_opened_at = runtime.completed
        with patch.object(runtime, "scan_entries", return_value=None), \
             patch.object(runtime, "return_to_list") as back, self.assertRaises(StoryStopped):
            runtime.locate()
        back.assert_not_called()
        self.assertEqual(context.actions, [])
        self.assertEqual((runtime.completed, runtime.battles), (0, 0))

    def test_completed_stage_returns_to_total_list_before_selecting_next(self):
        runtime, context = runner([MAP, LIST])
        runtime._map_opened_at = 0
        runtime.completed = 1
        with patch.object(runtime, "scan_entries", side_effect=(None, TARGET)) as scan, \
             patch.object(runtime, "return_to_list") as back:
            self.assertIs(runtime.locate(), TARGET)
        back.assert_called_once()
        self.assertEqual(scan.call_args_list[0].kwargs, {"map_mode": True})
        self.assertEqual(scan.call_args_list[1].kwargs, {})
        self.assertEqual(context.actions, [])

    def test_list_scan_resets_to_top_before_selecting_visible_entry(self):
        runtime, context = runner([LIST | {"幕间-可挑战条目"}])
        with patch("interlude_action.same_list", return_value=True):
            name, _ = runtime.scan_entries(LIST)
        self.assertEqual(name, "幕间-可挑战条目")
        self.assertEqual([name for name, _ in context.actions], ["幕间-列表复位"])

    def test_uncertain_entries_are_not_reported_as_all_finished(self):
        runtime, context = runner([LIST])
        context.scan_detail = {"uncertain_count": 1}
        # 克隆 Context 与真实框架一致，保留本次识别响应，配置仍独立。
        runtime.ctx.scan_detail = context.scan_detail
        with patch("interlude_action.same_list", return_value=True), self.assertRaises(StoryStopped):
            runtime.scan_entries(LIST)
        self.assertTrue(any(name.startswith("幕间-列表向下") for name, _ in context.actions))
        self.assertFalse(any(name == "幕间-可挑战条目" for name, _ in context.actions))

    def test_known_locked_list_stops_at_bottom_using_defined_pipeline_actions(self):
        for image, map_mode in ((LIST, False), (MAP, True)):
            with self.subTest(map_mode=map_mode):
                runtime, context = runner([image])
                with patch("interlude_action.same_list", return_value=True):
                    self.assertIsNone(runtime.scan_entries(image, map_mode=map_mode))
                self.assertEqual(len(context.actions), 2)
                for name, _ in context.actions:
                    self.assertEqual(context.nodes[name]["action"]["type"], "Swipe")

    def test_unrecognized_empty_total_list_is_not_reported_as_all_finished(self):
        runtime, context = runner([LIST])
        context.scan_detail = {"row_count": 0, "locked_count": 0, "uncertain_count": 0}
        runtime.ctx.scan_detail = context.scan_detail
        with patch("interlude_action.same_list", return_value=True), self.assertRaises(StoryStopped):
            runtime.scan_entries(LIST)

    def test_stage_without_rows_or_completion_evidence_is_not_reported_empty(self):
        runtime, _ = runner([MAP])
        runtime.ctx.scan_detail = {"row_count": 0, "uncertain_count": 0, "empty_confirmed": False}
        with patch("interlude_action.same_list", return_value=True), self.assertRaises(StoryStopped):
            runtime.scan_entries(MAP, map_mode=True)

    def test_stage_with_only_confirmed_free_rows_allows_return_to_interlude_list(self):
        runtime, _ = runner([MAP])
        runtime.ctx.scan_detail = {"row_count": 0, "uncertain_count": 0,
                                   "empty_confirmed": True, "free_count": 1}
        with patch("interlude_action.same_list", return_value=True):
            self.assertIsNone(runtime.scan_entries(MAP, map_mode=True))

    def test_disabled_restriction_stops_without_clicking_or_starting_battle(self):
        runtime, context = runner(config={"auto_handle_restrictions": False})
        with self.assertRaises(StoryStopped):
            runtime.check_formation_restriction({"主线-编制限制"})
        self.assertEqual(context.actions, [])
        self.assertEqual(context.tasks, [])

    def test_no_apple_setting_stops_at_ap_popup_without_consumption(self):
        runtime, context = runner()
        runtime.ctx.nodes["全局-战前吃体力"]["next"] = ["全局-体力不足"]
        with self.assertRaisesRegex(StoryStopped, "AP"):
            runtime.interrupt({"全局-战前吃体力"}, entering=True)
        self.assertEqual(context.tasks, [])


if __name__ == "__main__":
    unittest.main()
