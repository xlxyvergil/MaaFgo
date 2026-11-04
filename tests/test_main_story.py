"""主线外层状态机和 NEXT 合成图验证；不操作设备。"""
import copy
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from main_story_action import StoryRunner, StoryStopped, map_swipes
from main_story_recognition import load_templates, match_next
from battle.runtime.formation_session import sessions

PANEL = {"主线-副本列表信息"}
MAP = {"主线-地图菜单", "主线-地图控件"}
NEXT = "主线-列表NEXT"


class Context:
    def __init__(self, frames=(), config=None):
        self.nodes = {}
        for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            self.nodes.update(json.loads(path.read_text("utf-8")))
        self.nodes["主线-配置"]["attach"].update(config or {})
        self.frames = list(frames)
        self.last = set()
        self.actions, self.tasks, self.recognitions, self.cleared = [], [], [], []
        self.tasker = NS(stopping=False, controller=NS(post_screencap=lambda: self))

    def wait(self):
        return self

    def get(self):
        if self.frames:
            self.last = self.frames.pop(0)
        return self.last

    def get_node_data(self, name):
        return copy.deepcopy(self.nodes.get(name, {}))

    def clone(self):
        child = copy.copy(self)
        child.nodes = copy.deepcopy(self.nodes)
        return child

    def run_recognition(self, name, image):
        self.recognitions.append(name)
        return NS(hit=name in image, box=[700, 200, 1, 1])

    def run_action(self, name, box, pipeline_override=None):
        self.actions.append((name, pipeline_override))
        return NS(success=True)

    def run_task(self, name, pipeline_override=None):
        self.tasks.append((name, pipeline_override))
        return NS(status=NS(succeeded=True))

    def clear_hit_count(self, name):
        self.cleared.append(name)

    def get_task_job(self):
        return NS(job_id=12345)

    def override_pipeline(self, override):
        self.override = override
        return True


def runner(frames=(), config=None):
    ctx = Context(frames, config)
    clock = [0.0]
    def sleep(seconds):
        clock[0] += seconds
    return StoryRunner(ctx, clock=lambda: clock[0], sleep=sleep), ctx


class RoutingTests(unittest.TestCase):
    def test_panel_has_priority_even_with_visible_map_target(self):
        r, c = runner([PANEL | MAP | {NEXT, "主线-地图NEXT"}])
        self.assertIsNotNone(r.locate())
        self.assertNotIn("主线-地图NEXT", c.recognitions)
        self.assertEqual(c.actions, [])

    def test_visible_map_target_does_not_start_patrol(self):
        r, c = runner([MAP | {"主线-地图NEXT"}, PANEL, PANEL | {NEXT}])
        r.locate()
        self.assertEqual([x[0] for x in c.actions], ["主线-地图NEXT"])

    def test_patrol_checks_after_each_initialization_swipe(self):
        r, c = runner([MAP, MAP | {"主线-地图NEXT"}, PANEL, PANEL | {NEXT}])
        r.locate()
        self.assertEqual([x[0] for x in c.actions], ["巡-初始化左滑", "主线-地图NEXT"])
        self.assertEqual(c.actions[0][1], {"巡-初始化左滑": {"repeat": 1}})

    def test_unknown_or_menu_only_never_swipes(self):
        r, c = runner([{"主线-地图菜单"}], {"transition_timeout": 10})
        with self.assertRaisesRegex(StoryStopped, "无法确认"):
            r.locate()
        self.assertEqual(c.actions, [])

    def test_exhausted_patrol_stops_with_reason(self):
        r, c = runner([MAP])
        with self.assertRaisesRegex(StoryStopped, "遍历完成"):
            r.locate()
        expected = list(map_swipes(c))
        self.assertEqual([x[0] for x in c.actions], expected)
        self.assertEqual(expected.count("巡-初始化左滑"), 5)

    def test_empty_list_never_clicks_last_or_first_quest(self):
        r, c = runner([PANEL])
        with self.assertRaisesRegex(StoryStopped, "无 NEXT"):
            r.locate()
        self.assertTrue(all("列表" in name for name, _ in c.actions))
        self.assertNotIn("进本-点击上一次关卡", c.recognitions)

    def test_cn_list_remains_detectable_without_info_or_visible_next(self):
        r, _ = runner()
        empty_panel = {"主线-列表后退", "主线-地图菜单", "主线-地图任务图标"}
        self.assertTrue(r.panel(empty_panel))
        self.assertFalse(r.panel(empty_panel | {"主线-地图控件"}))
        self.assertFalse(r.panel({"主线-列表后退"}))

    def test_click_map_requires_panel_confirmation(self):
        r, c = runner([MAP | {"主线-地图NEXT"}])
        with self.assertRaisesRegex(StoryStopped, "未打开"):
            r.open_panel(NS(box=[10, 10, 1, 1]))
        self.assertEqual(len(c.actions), 3)

    def test_pure_story_returns_without_battle(self):
        r, c = runner([{"跳过剧情-点击跳过"}, {"跳过剧情-确认跳过"}, PANEL, PANEL])
        self.assertEqual(r.enter(NS(box=[1, 1, 1, 1])), "story")
        self.assertEqual([n for n, _ in c.actions],
                         ["主线-列表NEXT", "跳过剧情-点击跳过", "跳过剧情-确认跳过"])

    def test_pure_story_start_popup_uses_positive_match_before_skip(self):
        r, c = runner([{"主线-纯剧情任务开始"}, {"跳过剧情-点击跳过"},
                       {"跳过剧情-确认跳过"}, {"跳过剧情-点击跳过"}, PANEL, PANEL])
        self.assertEqual(r.wait_after_quest_click(), "story")
        self.assertEqual([n for n, _ in c.actions],
                         ["主线-纯剧情任务开始", "跳过剧情-点击跳过", "跳过剧情-确认跳过"])
        self.assertEqual(c.tasks, [])

    def test_story_skip_retries_when_confirmation_never_opens(self):
        r, c = runner([{"跳过剧情-点击跳过"}])
        self.assertTrue(r.interrupt({"跳过剧情-点击跳过"}, entering=True))
        self.assertFalse(r.interrupt({"跳过剧情-点击跳过"}, entering=True))
        r.pause(4)
        self.assertTrue(r.interrupt({"跳过剧情-点击跳过"}, entering=True))
        self.assertEqual([n for n, _ in c.actions],
                         ["跳过剧情-点击跳过", "跳过剧情-点击跳过"])

    def test_pure_story_reward_uses_existing_recognized_click(self):
        r, c = runner([{"跳过剧情-点击跳过"}, {"跳过剧情-确认跳过"},
                       {"主线-任务完成报酬"}, PANEL, PANEL])
        self.assertEqual(r.wait_after_quest_click(), "story")
        self.assertEqual([n for n, _ in c.actions],
                         ["跳过剧情-点击跳过", "跳过剧情-确认跳过", "主线-任务完成报酬"])

    def test_two_story_segments_with_animation_before_battle(self):
        frames = ([{"跳过剧情-点击跳过"}, {"跳过剧情-确认跳过"}]
                  + [set()] * 9
                  + [{"跳过剧情-点击跳过"}, {"跳过剧情-确认跳过"},
                     {"主线-助战界面"}])
        r, c = runner(frames)
        self.assertEqual(r.enter(NS(box=[1, 1, 1, 1])), "battle")
        self.assertEqual([n for n, _ in c.actions],
                         ["主线-列表NEXT", "跳过剧情-点击跳过",
                          "跳过剧情-确认跳过", "跳过剧情-点击跳过",
                          "跳过剧情-确认跳过"])

    def test_story_returns_to_map_or_list_after_skip(self):
        for screen in (MAP, PANEL):
            with self.subTest(screen=screen):
                r, c = runner([{"跳过剧情-点击跳过"}, {"跳过剧情-确认跳过"}, screen])
                self.assertEqual(r.wait_after_quest_click(), "story")
                self.assertEqual([n for n, _ in c.actions],
                                 ["跳过剧情-点击跳过", "跳过剧情-确认跳过"])

    def test_brief_list_between_two_stories_is_not_completion(self):
        frames = ([{"跳过剧情-点击跳过"}, {"跳过剧情-确认跳过"}]
                  + [PANEL] * 2 + [{"跳过剧情-点击跳过"}] * 9
                  + [{"跳过剧情-确认跳过"}, MAP])
        r, c = runner(frames)
        self.assertEqual(r.wait_after_quest_click(), "story")
        self.assertEqual([n for n, _ in c.actions],
                         ["跳过剧情-点击跳过", "跳过剧情-确认跳过",
                          "跳过剧情-点击跳过", "跳过剧情-确认跳过"])

    def test_special_formation_popup_stops_before_generic_battle_flow(self):
        r, c = runner([{"进本-选择助战"},
                       {"主线-编制限制", "进本-队伍确认"}],
                      {"auto_handle_restrictions": False})
        with self.assertRaisesRegex(StoryStopped, "需要手动调整"):
            r.fight()
        self.assertEqual([n for n, _ in c.actions], ["进本-选择助战"])
        self.assertEqual(c.tasks, [])

    def test_restriction_stops_immediately_at_start_and_during_entry(self):
        popup = {"主线-编制限制", "进本-队伍确认", "进本-关闭告知弹窗",
                 "主线-编队弹窗关闭保底"}
        for mode in ("native", "bbc"):
            for method in ("run", "wait_after_quest_click", "reach_formation"):
                with self.subTest(mode=mode, method=method):
                    r, c = runner([popup], {"battle_mode": mode, "auto_handle_restrictions": False})
                    with self.assertRaisesRegex(StoryStopped, "手动调整"):
                        getattr(r, method)()
                    self.assertLess(r.clock(), 1)
                    self.assertEqual(c.actions, [])
                    self.assertEqual(c.tasks, [])
                    self.assertEqual((r.completed, r.battles), (0, 0))

    def test_start_from_formation_uses_native_flow_and_counts_current_quest(self):
        formation = {"进本-队伍确认"}
        r, c = runner([formation] * 4 + [{"进本-战斗主界面已出现"}, MAP, MAP],
                      {"quest_limit": 1})
        r.run()
        self.assertEqual([n for n, _ in c.tasks], ["进本流程", "原生自动战斗入口"])
        self.assertEqual(c.actions, [])
        self.assertNotIn("主线-列表NEXT", c.recognitions)
        self.assertEqual((r.completed, r.battles), (1, 1))

    def test_start_from_formation_bbc_remains_single_battle(self):
        r, c = runner([{"进本-队伍确认"}, MAP, MAP],
                      {"quest_limit": 1, "battle_mode": "bbc"})
        r.run()
        self.assertEqual(c.tasks, [("bbc战斗", {
            "执行BBC任务": {"attach": {"run_count": 1, "battle_type": 0}}
        })])
        self.assertEqual(c.actions, [])
        self.assertEqual((r.completed, r.battles), (1, 1))

    def test_start_from_formation_returns_to_next_locator_after_current_quest(self):
        r, c = runner([{"进本-队伍确认"}, MAP | {"主线-地图NEXT"},
                       PANEL | {NEXT}, PANEL | {NEXT}], {"quest_limit": 2})
        with patch.object(r, "fight") as fight, patch.object(r, "settle") as settle, \
             patch.object(r, "enter", return_value="story") as enter:
            r.run()
        self.assertEqual(fight.call_count, 1)
        self.assertEqual(settle.call_count, 1)
        self.assertEqual(enter.call_count, 1)
        self.assertEqual([n for n, _ in c.actions], ["主线-地图NEXT"])
        self.assertEqual((r.completed, r.battles), (2, 1))

    def test_start_from_formation_handles_auto_formation_popup(self):
        popup, formation = "进本-自动编队确认", "进本-队伍确认"
        decide = "主线限制-编队决定"
        r, c = runner([{popup}, {decide}, {formation}], {"quest_limit": 1})
        with patch.object(r, "fight"), patch.object(r, "settle"):
            r.run()
        self.assertEqual([n for n, _ in c.actions], [popup, decide])
        self.assertEqual(c.tasks, [])
        self.assertEqual((r.completed, r.battles), (1, 1))

    def test_normal_support_reaches_formation_then_reuses_entry_flow(self):
        for support_name in ("进本-选择助战", "助战action", "Chaldea助战action"):
            with self.subTest(support_name=support_name):
                r, c = runner([{support_name}, {"进本-队伍确认"},
                               {"进本-队伍确认"}, {"进本-队伍确认"},
                               {"进本-战斗主界面已出现"}])
                c.nodes["进本流程"]["next"] = [support_name, "进本-队伍确认"]
                r.fight()
                custom = support_name in ("助战action", "Chaldea助战action")
                self.assertEqual([n for n, _ in c.actions], [] if custom else [support_name])
                self.assertEqual([n for n, _ in c.tasks],
                                 ([support_name] if custom else [])
                                 + ["进本流程", "原生自动战斗入口"])
                if custom:
                    self.assertEqual(c.tasks[0][1], {
                        support_name: {"next": [], "on_error": []}
                    })

    def test_auto_formation_popup_has_priority_over_visible_formation(self):
        popup = "进本-自动编队确认"
        formation = "进本-队伍确认"
        decide = "主线限制-编队决定"
        for support in ("进本-选择助战", "助战action", "Chaldea助战action"):
            with self.subTest(support=support):
                r, c = runner([{support}, {popup, formation}, {decide},
                               {formation}, {formation}, {formation}])
                c.nodes["进本流程"]["next"] = [support, formation]
                r.reach_formation()
                self.assertEqual([n for n, _ in c.actions].count(popup), 1)
                self.assertEqual([n for n, _ in c.actions].count(decide), 1)
                self.assertTrue(r.current_restricted)
                self.assertNotIn("进本流程", [n for n, _ in c.tasks])

    def test_auto_formation_not_handled_during_settlement(self):
        r, c = runner()
        self.assertFalse(r.interrupt({"进本-自动编队确认"}))
        self.assertEqual(c.actions, [])
        self.assertTrue(r.interrupt({"进本-自动编队确认"}, entering=True))
        self.assertTrue(r.interrupt({"主线限制-编队决定"}, entering=True))
        self.assertEqual([n for n, _ in c.actions], ["进本-自动编队确认", "主线限制-编队决定"])
        self.assertEqual(c.tasks, [])

    def test_auto_formation_decide_failure_stops_handoff(self):
        r, c = runner([{"进本-自动编队确认"}, {"主线限制-编队决定"}])
        original = c.run_action
        def action(name, *args, **kwargs):
            if name == "主线限制-编队决定":
                return NS(success=False)
            return original(name, *args, **kwargs)
        with patch.object(c, 'run_action', side_effect=action):
            with self.assertRaisesRegex(StoryStopped, "主线限制-编队决定"):
                r.reach_formation()

    def test_formation_notice_and_auto_popup_can_appear_in_either_order(self):
        notice, auto, formation = "进本-编成条件提示", "进本-自动编队确认", "进本-队伍确认"
        for order in ((notice, auto), (auto, notice), (notice,)):
            with self.subTest(order=order):
                decide = "主线限制-编队决定"
                middle = [{decide}] if auto in order else []
                r, c = runner([{n, formation} for n in order] + middle + [{formation}] * 4)
                r.reach_formation()
                self.assertEqual([n for n, _ in c.actions], list(order) + ([decide] if auto in order else []))
                self.assertEqual(c.tasks, [])

    def test_formation_notice_is_not_closed_during_settlement(self):
        r, c = runner()
        self.assertFalse(r.interrupt({"进本-编成条件提示"}))
        self.assertEqual(c.actions, [])

    def test_strategy_hint_is_closed_before_visible_formation(self):
        popup,formation="进本-攻略提示关闭","进本-队伍确认"
        r,c=runner([{popup,formation}]+[{formation}]*4)
        r.reach_formation()
        self.assertEqual([n for n,_ in c.actions],[popup])
        self.assertEqual(c.tasks,[])

    def test_strategy_hint_only_participates_in_entry_interrupts(self):
        r,c=runner()
        popup={"进本-攻略提示关闭"}
        self.assertFalse(r.interrupt(popup))
        self.assertTrue(r.interrupt(popup,entering=True))
        self.assertTrue(r.interrupt(popup,forming=True))
        self.assertEqual([n for n,_ in c.actions],["进本-攻略提示关闭"]*2)

    def test_formation_close_fallback_repeats_until_stable_formation(self):
        popup, formation = "主线-编队弹窗关闭保底", "进本-队伍确认"
        r, c = runner([{popup, formation}, set(), {popup}, {popup, formation}]
                      + [{formation}] * 4)
        r.reach_formation()
        self.assertEqual([n for n, _ in c.actions], [popup] * 3)
        self.assertEqual(c.tasks, [])

    def test_formation_close_fallback_runs_before_startup_handoff(self):
        popup, formation = "主线-编队弹窗关闭保底", "进本-队伍确认"
        r, c = runner([{popup, formation}] * 3 + [{formation}])
        self.assertIsNone(r.locate())
        self.assertEqual([n for n, _ in c.actions], [popup] * 3)
        self.assertEqual(c.tasks, [])

    def test_formation_close_fallback_is_only_used_during_entry(self):
        popup = "主线-编队弹窗关闭保底"
        r, c = runner()
        self.assertFalse(r.interrupt({popup}))
        self.assertNotIn(popup, c.recognitions)
        self.assertTrue(r.interrupt({popup}, entering=True))
        self.assertTrue(r.interrupt({popup}, forming=True))
        self.assertEqual([n for n, _ in c.actions], [popup] * 2)

    def test_formation_close_fallback_never_overrides_manual_restriction(self):
        r, c = runner(config={"auto_handle_restrictions": False})
        popup = {"主线-编制限制", "主线-编队弹窗关闭保底"}
        for flags in ({"entering": True}, {"forming": True}):
            with self.subTest(flags=flags):
                with self.assertRaisesRegex(StoryStopped, "需要手动调整"):
                    r.interrupt(popup, **flags)
        self.assertEqual(c.actions, [])

    def test_known_formation_hint_has_priority_over_close_fallback(self):
        hint, fallback, formation = "进本-攻略提示关闭", "主线-编队弹窗关闭保底", "进本-队伍确认"
        r, c = runner([{hint, fallback, formation}, {fallback, formation}]
                      + [{formation}] * 4)
        r.reach_formation()
        self.assertEqual([n for n, _ in c.actions], [hint, fallback])

    def test_close_fallback_precedes_generic_notice_dispatch(self):
        fallback = "主线-编队弹窗关闭保底"
        r, c = runner()
        self.assertTrue(r.interrupt({fallback, "进本-关闭告知弹窗"}, forming=True))
        self.assertEqual([n for n, _ in c.actions], [fallback])

    def test_ambiguous_support_configuration_stops_before_click(self):
        r, c = runner([{"进本-选择助战", "助战action"}])
        c.nodes["进本流程"]["next"] = ["进本-选择助战", "助战action"]
        with self.assertRaisesRegex(StoryStopped, "无法确定当前助战方式"):
            r.fight()
        self.assertEqual(c.actions, [])

    def test_unknown_flash_is_not_story_completion(self):
        r, c = runner([set(), MAP], {"transition_timeout": 10})
        with self.assertRaisesRegex(StoryStopped, "进本超时"):
            r.enter(NS(box=[1, 1, 1, 1]))

    def test_enter_hands_off_at_support(self):
        r, c = runner([{"主线-助战界面"}])
        self.assertEqual(r.enter(NS(box=[1, 1, 1, 1])), "battle")
        self.assertEqual(c.tasks, [])

    def test_ap_disabled_stops_without_spending(self):
        r, c = runner([{"全局-战前吃体力"}])
        with self.assertRaisesRegex(StoryStopped, "AP 不足"):
            r.enter(NS(box=[1, 1, 1, 1]))
        self.assertEqual(c.tasks, [])

    def test_ap_enabled_reuses_existing_pipeline(self):
        r, c = runner([{"全局-战前吃体力"}, {"主线-助战界面"}])
        c.nodes["全局-战前吃体力"]["next"] = ["全局-吃体力选择金"]
        self.assertEqual(r.enter(NS(box=[1, 1, 1, 1])), "battle")
        self.assertEqual(c.tasks[0][0], "全局-战前吃体力")

    def test_repeated_ineffective_quest_click_is_bounded(self):
        r, c = runner([PANEL | {NEXT}])
        with self.assertRaisesRegex(StoryStopped, "点击无效"):
            r.enter(NS(box=[1, 1, 1, 1]))
        self.assertEqual(len(c.actions), 3)

    def test_slow_list_recognition_refreshes_before_retry_at_battle_handoff(self):
        for handoff in ("主线-助战界面", "进本-队伍确认", "进本-战斗主界面已出现"):
            with self.subTest(handoff=handoff):
                old = PANEL | {NEXT}
                r, c = runner([old, {handoff}])
                original = r.reco

                def slow_reco(name, image):
                    if image is old and name == "主线-副本列表信息":
                        r.sleep(6)
                    return original(name, image)

                with patch.object(r, "reco", side_effect=slow_reco):
                    self.assertEqual(r.enter(NS(box=[1, 1, 1, 1])), "battle")
                self.assertEqual([n for n, _ in c.actions], [NEXT])
                self.assertEqual(c.tasks, [])

    def test_retry_refresh_waits_through_loading_without_clicking(self):
        old = PANEL | {NEXT}
        r, c = runner([old, set(), {"主线-助战界面"}])
        original = r.reco

        def slow_reco(name, image):
            if image is old and name == "主线-副本列表信息":
                r.sleep(6)
            return original(name, image)

        with patch.object(r, "reco", side_effect=slow_reco):
            self.assertEqual(r.enter(NS(box=[1, 1, 1, 1])), "battle")
        self.assertEqual([n for n, _ in c.actions], [NEXT])

    def test_retry_refresh_handles_ap_popup_before_visible_list(self):
        old = PANEL | {NEXT}
        r, c = runner([old, old | {"全局-战前吃体力"}])
        original = r.reco

        def slow_reco(name, image):
            if image is old and name == "主线-副本列表信息":
                r.sleep(6)
            return original(name, image)

        with patch.object(r, "reco", side_effect=slow_reco):
            with self.assertRaisesRegex(StoryStopped, "AP 不足"):
                r.enter(NS(box=[1, 1, 1, 1]))
        self.assertEqual([n for n, _ in c.actions], [NEXT])
        self.assertEqual(c.tasks, [])

    def test_retry_uses_target_coordinates_from_fresh_frame(self):
        old, fresh = PANEL | {NEXT}, PANEL | {NEXT}
        r, c = runner([old, fresh, {"主线-助战界面"}])
        original = r.reco

        def reco(name, image):
            if image is old and name == "主线-副本列表信息":
                r.sleep(6)
            if image is fresh and name == NEXT:
                return NS(hit=True, box=[900, 350, 1, 1])
            return original(name, image)

        with patch.object(r, "reco", side_effect=reco), \
             patch.object(c, "run_action", wraps=c.run_action) as action:
            self.assertEqual(r.enter(NS(box=[1, 1, 1, 1])), "battle")
        self.assertEqual([n for n, _ in c.actions], [NEXT, NEXT])
        self.assertEqual(action.call_args_list[1].args[1], [900, 350, 1, 1])

    def test_slow_fresh_confirmation_drops_retry_click(self):
        old, fresh = PANEL | {NEXT}, PANEL | {NEXT}
        r, c = runner([old, fresh, {"主线-助战界面"}])
        original = r.reco

        def slow_reco(name, image):
            if name == "主线-副本列表信息":
                r.sleep(6 if image is old else 3)
            return original(name, image)

        with patch.object(r, "reco", side_effect=slow_reco):
            self.assertEqual(r.enter(NS(box=[1, 1, 1, 1])), "battle")
        self.assertEqual([n for n, _ in c.actions], [NEXT])

    def test_settlement_returns_to_map_or_panel_without_home(self):
        for screen in [MAP, PANEL]:
            with self.subTest(screen=screen):
                r, c = runner([{"作战成功_结束战斗"}, screen, screen])
                r.settle()
                self.assertEqual([x[0] for x in c.actions], ["作战成功_结束战斗"])
                self.assertEqual(c.tasks, [])

    def test_full_screen_reward_advances_only_when_recognized(self):
        r, c = runner([{"主线-任务完成报酬"}, PANEL, PANEL])
        r.settle()
        self.assertEqual([x[0] for x in c.actions], ["主线-任务完成报酬"])
        self.assertEqual(c.tasks, [])

    def test_defeat_never_counts_as_success_or_retreats_automatically(self):
        r, c = runner([{"战斗失败_不回主界面"}])
        with self.assertRaisesRegex(StoryStopped, "战斗失败"):
            r.settle()
        self.assertEqual(c.actions, [])
        self.assertEqual(r.battles, 0)

    def test_unknown_settlement_never_blind_clicks(self):
        r, c = runner([set()], {"transition_timeout": 10})
        with self.assertRaisesRegex(StoryStopped, "结算超时"):
            r.settle()
        self.assertEqual(c.actions, [])

    def test_bbc_is_one_normal_battle(self):
        r, c = runner(config={"battle_mode": "bbc"})
        r.fight()
        self.assertEqual(c.tasks, [("bbc战斗", {"执行BBC任务": {"attach": {"run_count": 1, "battle_type": 0}}})])

    def test_native_sessions_are_fresh_and_cleaned_on_failure(self):
        r, c = runner([{"进本-战斗主界面已出现"}])
        tokens = []
        original = sessions.begin
        def begin(*args, **kwargs):
            token = original(*args, **kwargs)
            tokens.append(token)
            return token
        with patch.object(sessions, "begin", side_effect=begin):
            r.fight()
            with patch.object(r, "task", side_effect=StoryStopped("failed")):
                with self.assertRaises(StoryStopped):
                    r.fight()
        self.assertNotEqual(*tokens)
        for token in tokens:
            self.assertFalse(sessions.valid(12345, token))
        self.assertEqual([n for n, _ in c.tasks], ["进本流程", "原生自动战斗入口"])
        self.assertEqual(c.cleared.count("自动编队-礼装筛找满破"), 2)
        self.assertEqual(c.cleared.count("羁绊补齐-礼装筛找满破"), 2)

    def test_slow_custom_support_keeps_session_and_allows_formation_handoff(self):
        formation = {"进本-队伍确认"}
        r, c = runner([{"助战action"}, formation, formation, formation,
                       {"进本-战斗主界面已出现"}], {"transition_timeout": 10})
        c.nodes["进本流程"]["next"] = ["助战action", "进本-队伍确认"]
        original = r.task
        tokens = []
        def task(name, override=None):
            token = r.ctx.override["编队身份-会话"]["attach"]["session_id"]
            if name == "助战action":
                tokens.append(token)
                sessions.set_support_fallback(c.get_task_job().job_id, token, True)
                r.sleep(60)
            elif name == "原生自动战斗入口":
                self.assertEqual(token, tokens[0])
                self.assertTrue(sessions.support_fallback(c.get_task_job().job_id, token))
            return original(name, override)
        with patch.object(r, "task", side_effect=task):
            r.fight()
        self.assertFalse(sessions.valid(c.get_task_job().job_id, tokens[0]))

    def test_failed_shared_pipeline_does_not_continue(self):
        r, c = runner(config={"battle_mode": "bbc"})
        c.run_task = lambda *args, **kwargs: NS(status=NS(succeeded=False))
        with self.assertRaisesRegex(StoryStopped, "流程失败"):
            r.fight()
        self.assertEqual((r.completed, r.battles), (0, 0))

    def test_reenters_locator_between_different_quests(self):
        r, c = runner(config={"quest_limit": 3})
        with patch.object(r, "locate") as locate, \
             patch.object(r, "enter", side_effect=["battle", "story", "battle"]), \
             patch.object(r, "fight") as fight, patch.object(r, "settle") as settle:
            r.run()
        self.assertEqual(locate.call_count, 3)
        self.assertEqual(fight.call_count, 2)
        self.assertEqual(settle.call_count, 2)
        self.assertEqual((r.completed, r.battles), (3, 2))

    def test_stop_is_observed_before_actions(self):
        r, c = runner()
        c.tasker.stopping = True
        with self.assertRaisesRegex(StoryStopped, "用户已停止"):
            r.locate()
        self.assertEqual(c.actions, [])


class NextImageTests(unittest.TestCase):
    def scene(self, server, scale=1.0, panel=False):
        templ, mask = load_templates(server)
        factor = 2 / 3 * scale
        size = tuple(round(v * factor) for v in templ.shape[1::-1])
        t = cv2.resize(templ, size, interpolation=cv2.INTER_AREA)
        m = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
        image = np.full((720, 1280, 3), [40, 70, 110], np.uint8)
        x, y = (800 if panel else 350), 150
        view = image[y:y+size[1], x:x+size[0]]
        view[m > 0] = t[m > 0]
        return image, templ, mask, (x, y, *size)

    def test_both_servers_match_changed_background_and_scaled_map(self):
        for server in ("cn", "jp"):
            for scale in (0.6, 1.0, 1.5):
                with self.subTest(server=server, scale=scale):
                    image, templ, mask, box = self.scene(server, scale)
                    result = match_next(image, templ, mask)
                    self.assertIsNotNone(result)
                    self.assertEqual(result[1]["marker_box"], list(box))
                    self.assertAlmostEqual(result[1]["scale"], scale)
                    self.assertEqual(result[0][1], round(box[1]+box[3]/2+100*scale))

    def test_panel_uses_own_roi_and_click_offset(self):
        image, templ, mask, box = self.scene("cn", panel=True)
        result = match_next(image, templ, mask, panel=True)
        self.assertEqual(result[1]["marker_box"], list(box))
        self.assertEqual(result[0][1], round(box[1]+box[3]/2+100*2/3))
        image, templ, mask, _ = self.scene("cn", panel=False)
        self.assertIsNone(match_next(image, templ, mask, panel=True))

    def test_black_screen_and_cancel_do_not_produce_a_target(self):
        templ, mask = load_templates("cn")
        self.assertIsNone(match_next(np.zeros((720,1280,3),np.uint8),templ,mask))
        image, _, _, _ = self.scene("cn")
        self.assertIsNone(match_next(image,templ,mask,stopped=lambda: True))


if __name__ == "__main__":
    unittest.main()
