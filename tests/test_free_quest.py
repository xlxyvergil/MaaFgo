"""Free Quest 路由、完成归属与资源消耗边界；不连接设备。"""
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
from free_quest_action import FreeQuestRunner
from free_quest_recognition import badge_candidates
from main_story_action import StoryStopped
from test_main_story import Context


def runner(frames=(), config=None):
    ctx = Context(frames)
    ctx.nodes["Free-配置"]["attach"].update(config or {})
    clock = [0.0]
    def sleep(seconds):
        clock[0] += seconds
    return FreeQuestRunner(ctx, clock=lambda: clock[0], sleep=sleep), ctx


ROW = NS(box=NS(x=650, y=157, w=98, h=70))


class RoutingTests(unittest.TestCase):
    def test_four_entry_screens_and_visible_map_target(self):
        for entry in ("主线-助战界面", "进本-队伍确认", "进本-战斗主界面已出现"):
            r, c = runner([{entry}])
            self.assertIsNone(r.locate())
            self.assertEqual(c.actions, [])
        r, c = runner([{"主线-地图菜单", "主线-地图控件", "Free-地图数字"}, {"主线-列表后退", "Free-列表标记"}])
        with patch.object(r, "open_panel"), patch.object(r, "find_in_panel", return_value=ROW):
            self.assertIs(r.locate(), ROW)
        self.assertEqual(c.actions, [])

    def test_unknown_screen_never_swipes_or_clicks(self):
        r, c = runner([{"主线-地图菜单"}], {"transition_timeout": 10})
        with self.assertRaisesRegex(StoryStopped, "无法确认"):
            r.locate()
        self.assertEqual(c.actions, [])

    def test_clear_panel_returns_to_map_without_entering(self):
        r, c = runner([{"主线-列表后退", "Free-列表标记"}, {"主线-地图菜单", "主线-地图控件"}])
        with patch.object(r, "find_in_panel", return_value=None), patch.object(r, "back_to_map") as back, patch("free_quest_action.map_swipes", return_value=[]):
            self.assertIs(r.locate(), False)
            back.assert_called_once()
        self.assertEqual(c.actions, [])

    def test_map_exhaustion_is_success_and_not_an_arbitrary_quest(self):
        r, c = runner()
        with patch.object(r, "locate", return_value=False):
            message = r.run()
        self.assertIn("地图遍历完成", message)
        self.assertEqual((r.battles, r.completed), (0, 0))
        self.assertEqual(c.tasks, [])

    def test_postbattle_map_reopens_previous_before_any_new_number(self):
        r, c = runner([{"主线-地图菜单", "主线-地图控件", "Free-地图数字", "Free-地图前回"},
                       {"主线-列表后退", "Free-列表标记"}])
        r.pending_clear = True
        with patch.object(r, "open_panel") as opened, patch.object(r, "find_in_panel", return_value=ROW):
            self.assertIs(r.locate(), ROW)
        self.assertEqual(opened.call_args.args[1], "Free-地图前回")
        self.assertNotIn("Free-地图数字", c.recognitions)

    def test_postbattle_map_without_previous_does_not_claim_exhausted(self):
        r, c = runner([{"主线-地图菜单", "主线-地图控件", "Free-地图数字"}])
        r.pending_clear = True
        with patch("free_quest_action.map_swipes", return_value=[]):
            with self.assertRaisesRegex(StoryStopped, "未找到前回"):
                r.locate()
        self.assertEqual(c.actions, [])

    def test_confirmed_clear_panel_does_not_swipe_background_map(self):
        r, c = runner()
        with patch.object(r, "rows", return_value=iter(((ROW, True),))):
            self.assertIsNone(r.find_in_panel(np.zeros((720, 1280, 3), np.uint8)))
        self.assertEqual(c.actions, [])

    def test_wide_map_patrol_covers_two_thousand_pixels_per_row(self):
        r, c = runner()
        route = list(r.patrol())
        row = route[10:18]
        self.assertEqual(len(row), 8)
        # 特劳姆的剩余两点在右端；旧通用路径每行只横跨1000px。
        distance = 0
        for name in row:
            param = c.nodes[name]["action"]["param"]
            distance += param["begin"][0] - param["end"][0][0]
        self.assertEqual(distance, 2000)
        self.assertEqual(len(route), 63)

    def test_three_battles_do_not_imply_clear(self):
        r, _ = runner(config={"battle_limit": 3})
        with patch.object(r, "locate", return_value=None), patch.object(r, "fight"), patch.object(r, "settle"), patch.object(r, "panel", return_value=False):
            r.run()
        self.assertEqual((r.battles, r.completed), (3, 0))

    def test_completed_quest_confirmed_before_next_uncleared_row(self):
        r, _ = runner()
        r.pending_clear = True
        other = NS(box=NS(x=650, y=350, w=98, h=70))
        r.current_title = np.zeros((40, 290, 3), np.uint8)
        image = np.zeros((720, 1280, 3), np.uint8)
        with patch.object(r, "rows", return_value=iter(((ROW, True), (other, False)))):
            self.assertIs(r.find_in_panel(image), other)
        self.assertEqual(r.completed, 1)
        self.assertFalse(r.pending_clear)

    def test_other_quest_clear_does_not_complete_current(self):
        r, _ = runner()
        r.pending_clear = True
        r.current_title = np.zeros((40, 290, 3), np.uint8)
        other = NS(box=NS(x=650, y=350, w=98, h=70))
        image = np.zeros((720, 1280, 3), np.uint8)
        image[315:345, 775:1065] = 255
        with patch.object(r, "rows", return_value=iter(((other, True), (ROW, False)))):
            self.assertIs(r.find_in_panel(image), ROW)
        self.assertEqual(r.completed, 0)
        self.assertTrue(r.pending_clear)

    def test_ap_disabled_stops_without_consumption(self):
        r, c = runner()
        with self.assertRaisesRegex(StoryStopped, "AP 不足"):
            r.interrupt({"全局-战前吃体力"}, entering=True)
        self.assertEqual(c.tasks, [])

    def test_selected_apple_uses_existing_pipeline(self):
        r, c = runner()
        c.nodes["全局-战前吃体力"]["next"] = ["全局-吃体力选择金"]
        self.assertTrue(r.interrupt({"全局-战前吃体力"}, entering=True))
        self.assertEqual(c.tasks, [("全局-战前吃体力", {})])

    def test_no_main_story_restriction_or_stone_options(self):
        task = (ROOT / "assets/tasks/自动清free本.json").read_text("utf-8")
        options = (ROOT / "assets/options/自动清free本.json").read_text("utf-8")
        self.assertNotIn("主线-自动处理编队限制", task+options)
        self.assertNotIn("主线-是否自动使用灵脉石", task+options)
        r, c = runner()
        self.assertFalse(r.interrupt({"主线-编制限制"}, forming=True))
        self.assertFalse(any("限制" in name for name in c.recognitions))

    def test_bbc_handoff_is_one_battle_and_restores_context(self):
        r, c = runner(config={"battle_mode": "bbc"})
        r.fight()
        self.assertIs(r.ctx, c)
        self.assertEqual(c.tasks, [("bbc战斗", {"执行BBC任务": {"attach": {"run_count": 1, "battle_type": 0}}})])

    def test_native_failure_finishes_session_and_restores_context(self):
        r, c = runner()
        with patch.object(r, "reach_formation"), patch.object(r, "task", side_effect=StoryStopped("测试失败")), patch("free_quest_action.sessions.finish") as finish:
            with self.assertRaisesRegex(StoryStopped, "测试失败"):
                r.fight()
        self.assertIs(r.ctx, c)
        finish.assert_called_once()
        # 清理实际测试会话；生产 finally 已由 spy 验证。
        from battle.runtime.formation_session import sessions
        sessions.finish(*finish.call_args.args)

    def test_native_return_while_still_in_battle_stops_before_counting(self):
        r, c = runner([{"进本-战斗主界面已出现"}])
        with patch.object(r, "reach_formation"):
            with self.assertRaisesRegex(StoryStopped, "提前返回"):
                r.fight()
        self.assertEqual(r.battles, 0)
        self.assertIs(r.ctx, c)

    def test_title_match_tolerates_small_shift_and_rejects_other_title(self):
        r, _ = runner()
        image = np.zeros((720, 1280, 3), np.uint8)
        cv2.putText(image, "ALPHA QUEST", (790, 143), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 1)
        r.current_title = r.title_patch(image, ROW)
        shifted = np.zeros_like(image)
        cv2.putText(shifted, "ALPHA QUEST", (790, 146), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 1)
        self.assertTrue(r.same_quest(shifted, ROW))
        different = np.zeros_like(image)
        cv2.putText(different, "OTHER STAGE", (790, 143), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 1)
        self.assertFalse(r.same_quest(different, ROW))


class BadgeTests(unittest.TestCase):
    def test_round_badges_require_white_ring_and_exclude_bottom_controls(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        for point in ((800, 200), (450, 620), (1200, 650)):
            cv2.circle(image, point, 25, (255, 255, 255), -1)
            cv2.circle(image, point, 22, (0, 125, 255), -1)
        cv2.circle(image, (950, 350), 22, (0, 125, 255), -1)
        boxes = badge_candidates(image)
        self.assertEqual(len(boxes), 1)
        self.assertAlmostEqual(boxes[0][0]+boxes[0][2]/2, 800, delta=1)


if __name__ == "__main__":
    unittest.main()
