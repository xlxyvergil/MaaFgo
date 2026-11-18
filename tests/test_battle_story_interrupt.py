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

CHOICE = "跳过剧情-强制选项"
SKIP = "跳过剧情-点击跳过"
CONTINUE = "跳过剧情-灰色继续"
MAP_NODES = {"可能已经结束战斗_不回主界面", "战斗结束-主界面"}


class StoryInterruptTests(unittest.TestCase):
    def run_wait(self, frames, *, enabled=True, task_ok=True, handle_story=True, timeout=3):
        runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
        pending = iter(frames)
        current = [frames[-1]]
        clock = [0.0]
        def frame():
            current[0] = next(pending, current[0])
            return current[0]
        def recognize(name, image, **kwargs):
            # Frame fields are scene, SKIP, map, choice, dialogue-continue. Keep each recognizer
            # independent so a returned map cannot masquerade as a choice.
            index = {SKIP: 1, CHOICE: 3, CONTINUE: 4}.get(name, 2 if name in MAP_NODES else None)
            return NS(hit=index is not None and len(image) > index and image[index])
        runtime.controller = NS(post_screencap=lambda: NS(wait=lambda: NS(get=frame)))
        runtime.ctx = NS(
            tasker=NS(stopping=False),
            get_node_data=lambda name: {"enabled": enabled},
            run_recognition=Mock(side_effect=recognize),
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

    def test_choice_precedes_gray_skip_and_then_resumes_story_and_battle(self):
        choice_frame = (Scene.UNKNOWN, True, False, True)
        ok, r = self.run_wait([choice_frame, (Scene.UNKNOWN, True), (Scene.MAIN_BATTLE, False)])
        self.assertTrue(ok)
        self.assertEqual([call.args[0] for call in r.ctx.run_task.call_args_list],
                         [CHOICE, "战斗-出卡后剧情处理"])
        choice_frame_probes = [call.args[0] for call in r.ctx.run_recognition.call_args_list
                               if call.args[1] is choice_frame]
        self.assertEqual(choice_frame_probes, [CHOICE])
        r.executor.tap_top_right_close.assert_not_called()

    def test_hidden_choice_continues_dialogue_before_choice_skip_and_battle(self):
        hidden = (Scene.UNKNOWN, True, False, False, True)
        choice = (Scene.UNKNOWN, True, False, True, True)
        ok, r = self.run_wait([hidden, choice, (Scene.UNKNOWN, True),
                               (Scene.MAIN_BATTLE, False)])
        self.assertTrue(ok)
        self.assertEqual([call.args[0] for call in r.ctx.run_task.call_args_list],
                         [CONTINUE, CHOICE, "战斗-出卡后剧情处理"])
        hidden_probes = [call.args[0] for call in r.ctx.run_recognition.call_args_list
                         if call.args[1] is hidden]
        self.assertEqual(hidden_probes, [CHOICE, CONTINUE])
        r.executor.tap_top_right_close.assert_not_called()

    def test_skip_disabled_only_observes_hidden_choice_without_continuing(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True, False, False, True),
                               (Scene.MAIN_BATTLE, False)], enabled=False)
        self.assertTrue(ok)
        r.ctx.run_task.assert_not_called()
        r.executor.tap_top_right_close.assert_not_called()
        probe = next(call for call in r.ctx.run_recognition.call_args_list
                     if call.args[0] == CONTINUE)
        self.assertEqual(probe.kwargs["pipeline_override"], {CONTINUE: {"enabled": True}})

    def test_missing_continue_marker_does_not_start_dialogue_continue(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True, False, False, False),
                               (Scene.MAIN_BATTLE, False)])
        self.assertTrue(ok)
        self.assertEqual([call.args[0] for call in r.ctx.run_task.call_args_list],
                         ["战斗-出卡后剧情处理"])

    def test_failed_dialogue_continue_stops_without_blind_click(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True, False, False, True)], task_ok=False)
        self.assertFalse(ok)
        self.assertEqual([call.args[0] for call in r.ctx.run_task.call_args_list], [CONTINUE])
        r.executor.tap_top_right_close.assert_not_called()

    def test_choice_alone_never_counts_as_victory(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, False, False, True)])
        self.assertFalse(ok)
        self.assertGreater(r.ctx.run_task.call_count, 0)
        self.assertTrue(all(call.args[0] == CHOICE for call in r.ctx.run_task.call_args_list))
        self.assertFalse(getattr(r, "_post_attack_returned_to_map", False))
        r.executor.tap_top_right_close.assert_not_called()

    def test_skip_disabled_only_recognizes_choice_without_clicking(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True, False, True),
                               (Scene.MAIN_BATTLE, False)], enabled=False)
        self.assertTrue(ok)
        r.ctx.run_task.assert_not_called()
        r.executor.tap_top_right_close.assert_not_called()
        self.assertEqual(r.ctx.run_recognition.call_args_list[0].args[0], CHOICE)
        self.assertEqual(r.ctx.run_recognition.call_args_list[0].kwargs["pipeline_override"],
                         {CHOICE: {"enabled": True}})

    def test_failed_choice_stops_without_blind_click(self):
        ok, r = self.run_wait([(Scene.UNKNOWN, True, False, True)], task_ok=False)
        self.assertFalse(ok)
        self.assertEqual(r.ctx.run_task.call_count, 1)
        self.assertEqual(r.ctx.run_task.call_args.args[0], CHOICE)
        r.executor.tap_top_right_close.assert_not_called()

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
