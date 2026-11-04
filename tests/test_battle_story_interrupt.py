"""出卡后剧情中断不得被当成胜利，且必须尊重跳过开关。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.core.enums import Scene
from battle.runtime.runtime import AutoBattleRuntime


class StoryInterruptTests(unittest.TestCase):
    def run_wait(self, frames, *, enabled=True, task_ok=True, handle_story=True, timeout=3):
        runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
        pending = iter(frames)
        current = [frames[-1]]
        clock = [0.0]
        def frame():
            current[0] = next(pending, current[0])
            return current[0]
        runtime.controller = NS(post_screencap=lambda: NS(wait=lambda: NS(get=frame)))
        runtime.ctx = NS(
            tasker=NS(stopping=False),
            get_node_data=lambda name: {"enabled": enabled},
            run_recognition=Mock(side_effect=lambda name, image, **kw: NS(
                hit=image[1] if name == "跳过剧情-点击跳过" else len(image) > 2 and image[2])),
            run_task=Mock(return_value=NS(status=NS(succeeded=task_ok))),
        )
        runtime.executor = NS(tap_top_right_close=Mock())
        def sleep(seconds):
            clock[0] += seconds
        with patch("battle.runtime.runtime.time.monotonic", side_effect=lambda: clock[0]), \
                patch("battle.runtime.runtime.time.sleep", side_effect=sleep), \
                patch("battle.runtime.runtime.perception.detect_scene", side_effect=lambda ctx, im, **_kwargs: im[0]):
            ok = runtime._wait_until((Scene.MAIN_BATTLE, Scene.VICTORY, Scene.DEFEAT),
                                     timeout, tap_close=True, handle_story=handle_story)
        return ok, runtime

    def test_story_returns_to_battle_or_real_terminal(self):
        for target in (Scene.MAIN_BATTLE, Scene.VICTORY, Scene.DEFEAT):
            with self.subTest(target=target):
                ok, r = self.run_wait([(Scene.UNKNOWN, True), (target, False)])
                self.assertTrue(ok)
                r.ctx.run_task.assert_called_once()
                r.executor.tap_top_right_close.assert_not_called()

    def test_two_story_segments_are_handled_before_victory(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True), (Scene.UNKNOWN, True),
                               (Scene.VICTORY, False)])
        self.assertTrue(ok)
        self.assertEqual(r.ctx.run_task.call_count, 2)

    def test_story_alone_never_counts_as_victory_and_times_out(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True)])
        self.assertFalse(ok)
        self.assertEqual(r.ctx.run_task.call_count, 6)
        r.executor.tap_top_right_close.assert_not_called()

    def test_skip_disabled_never_clicks_even_on_transition_frames(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True), (Scene.UNKNOWN, False),
                               (Scene.MAIN_BATTLE, False)], enabled=False)
        self.assertTrue(ok)
        r.ctx.run_task.assert_not_called()
        r.executor.tap_top_right_close.assert_not_called()

    def test_failed_story_pipeline_stops_without_blind_click(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True)], task_ok=False)
        self.assertFalse(ok)
        r.ctx.run_task.assert_called_once()
        r.executor.tap_top_right_close.assert_not_called()

    def test_normal_animation_keeps_existing_wait(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, False), (Scene.MAIN_BATTLE, False)])
        self.assertTrue(ok)
        r.ctx.run_task.assert_not_called()
        r.executor.tap_top_right_close.assert_called_once()

    def test_other_waits_do_not_start_story_pipeline(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True), (Scene.MAIN_BATTLE, False)],
                               handle_story=False)
        self.assertTrue(ok)
        r.ctx.run_recognition.assert_not_called()
        r.ctx.run_task.assert_not_called()

    def test_known_scene_does_not_run_story_recognition(self):
        ok, r = self.run_wait([(Scene.MAIN_BATTLE, True)])
        self.assertTrue(ok)
        r.ctx.run_recognition.assert_not_called()

    def test_unknown_after_story_does_not_click_event_reward(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True), (Scene.UNKNOWN, False)])
        self.assertFalse(ok)
        r.executor.tap_top_right_close.assert_not_called()

    def test_brief_map_after_story_is_not_completion(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True), (Scene.UNKNOWN, False, True),
                               (Scene.UNKNOWN, False), (Scene.MAIN_BATTLE, False)])
        self.assertTrue(ok)
        self.assertFalse(getattr(r, "_post_attack_returned_to_map", False))
        r.executor.tap_top_right_close.assert_not_called()

    def test_stable_map_after_story_hands_off_without_victory_claim(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True), (Scene.UNKNOWN, False, True)], timeout=5)
        self.assertTrue(ok)
        self.assertTrue(r._post_attack_returned_to_map)
        r.executor.tap_top_right_close.assert_not_called()

    def test_map_without_story_does_not_finish_attack(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, False, True)])
        self.assertFalse(ok)
        self.assertFalse(getattr(r, "_post_attack_returned_to_map", False))


if __name__ == "__main__":
    unittest.main()
