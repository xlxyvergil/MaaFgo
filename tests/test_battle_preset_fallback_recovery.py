"""An interrupted preset is cancelled before the default runner may act."""
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.enums import Scene
from battle.runtime import preset_fallback as recovery


class PresetFallbackRecoveryTests(unittest.TestCase):
    def run_recovery(self, frames, *, stop_on_frame=None, screenshot_failed=False,
                     cancel_ok=True, task_ok=True, timeout=10):
        clock = [0.0]
        captures = [0]
        index = [-1]
        events = []
        context = NS(tasker=NS(stopping=False))

        def frame():
            captures[0] += 1
            index[0] = min(index[0] + 1, len(frames) - 1)
            if captures[0] == stop_on_frame:
                context.tasker.stopping = True
            return None if screenshot_failed else index[0]

        def recognition(name, _image):
            scene, overlays, *_ = frames[index[0]]
            hit = (name == recovery.SCENE_NODES["victory"] and scene is Scene.VICTORY
                   or name == recovery.SCENE_NODES["defeat"] and scene is Scene.DEFEAT
                   or name in overlays)
            return NS(hit=hit, best_result=NS(box=(10, 20, 30, 40)))

        def task(name, **kwargs):
            events.append(("return_cards", name, kwargs))
            return NS(status=NS(succeeded=task_ok))

        context.run_recognition = Mock(side_effect=recognition)
        context.run_task = Mock(side_effect=task)
        controller = NS(post_screencap=lambda: NS(wait=lambda: NS(get=frame)))
        executor = NS(
            tap_top_right_close=Mock(side_effect=lambda: events.append("top_close")),
            close_skill_use_dialog=Mock(side_effect=lambda _d: events.append("skill_close")),
            close_dialog_by_detail=Mock(side_effect=lambda _d: events.append("recognized_close")),
            cancel_order_change=Mock(side_effect=lambda: events.append("cancel_swap") or cancel_ok),
        )
        runtime = NS(ctx=context, controller=controller, executor=executor)
        def detect(_context, _image):
            values = frames[index[0]]
            return values[0], values[2] if len(values) > 2 else 1.0
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(recovery, "_RECOVERY_TIMEOUT_S", timeout), \
             patch.object(recovery.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(recovery.time, "sleep", side_effect=sleep), \
             patch.object(recovery.perception, "_detect_scene", side_effect=detect):
            result = recovery.recover_for_default_battle(runtime)
        return result, runtime, events, captures[0]

    def test_no_overlay_still_closes_once_then_confirms_concrete_main_state_twice(self):
        ok, runtime, events, captures = self.run_recovery([(Scene.MAIN_BATTLE, ())])
        self.assertTrue(ok)
        self.assertEqual(events, ["top_close"])
        self.assertEqual(captures, 3)
        runtime.ctx.run_task.assert_not_called()

    def test_victory_and_defeat_are_handed_back_without_dismissing_terminal(self):
        for scene in (Scene.VICTORY, Scene.DEFEAT):
            with self.subTest(scene=scene):
                ok, _runtime, events, captures = self.run_recovery([(scene, ())])
                self.assertTrue(ok)
                self.assertEqual((events, captures), ([], 1))

    def test_multiple_recognized_layers_are_dismissed_with_fresh_frames(self):
        ok, _runtime, events, captures = self.run_recovery([
            (Scene.UNKNOWN, ("战斗_技能目标子屏",)),
            (Scene.MAIN_BATTLE, ("战斗_技能使用弹窗",)),
            (Scene.MAIN_BATTLE, ("战斗_技能无法使用弹窗",)),
            (Scene.MAIN_BATTLE, ()),
            (Scene.MAIN_BATTLE, ()),
        ])
        self.assertTrue(ok)
        self.assertEqual(events, ["top_close", "skill_close", "recognized_close"])
        self.assertEqual(captures, 5)

    def test_visible_main_behind_target_or_np_overlay_does_not_release_default_runner(self):
        for overlay in ("战斗_技能目标子屏", recovery.NP_IDENTITY_GUARD, recovery.UNKNOWN_MENU):
            with self.subTest(overlay=overlay):
                ok, _runtime, events, _captures = self.run_recovery([
                    (Scene.MAIN_BATTLE, ()),
                    (Scene.MAIN_BATTLE, (overlay,)),
                    (Scene.MAIN_BATTLE, ()),
                    (Scene.MAIN_BATTLE, ()),
                ])
                self.assertTrue(ok)
                self.assertEqual(events, ["top_close", "top_close"])

    def test_command_selection_must_cancel_old_partial_chain_before_main_confirmation(self):
        ok, runtime, events, _captures = self.run_recovery([
            (Scene.COMMAND_SELECTION, ()),
            (Scene.COMMAND_SELECTION, ()),
            (Scene.MAIN_BATTLE, ()),
            (Scene.MAIN_BATTLE, ()),
        ])
        self.assertTrue(ok)
        self.assertEqual(events[0], "top_close")
        self.assertEqual(events[1][0:2], ("return_cards", recovery._CARD_RETURN))
        self.assertEqual(runtime.ctx.run_task.call_args.kwargs["pipeline_override"], {
            recovery._CARD_RETURN: {"next": [], "on_error": [], "timeout": 1000},
        })
        self.assertFalse(hasattr(runtime.executor, "select_card"))

    def test_return_pipeline_success_alone_cannot_approve_stuck_card_selection(self):
        ok, runtime, events, _captures = self.run_recovery([(Scene.COMMAND_SELECTION, ())])
        self.assertFalse(ok)
        self.assertEqual(runtime.ctx.run_task.call_count, recovery._MAX_SELECTION_RETURNS)
        self.assertEqual(events[0], "top_close")

    def test_cancelled_or_late_swap_returns_to_verified_main(self):
        ok, _runtime, events, _captures = self.run_recovery([
            (Scene.ORDER_CHANGE, ()), (Scene.ORDER_CHANGE, ()),
            (Scene.UNKNOWN, ()), (Scene.MAIN_BATTLE, ()), (Scene.MAIN_BATTLE, ()),
        ])
        self.assertTrue(ok)
        self.assertEqual(events, ["top_close", "cancel_swap"])

    def test_attack_animation_waits_without_repeated_blind_close_or_duplicate_card_submission(self):
        ok, _runtime, events, _captures = self.run_recovery([
            (Scene.UNKNOWN, ()), (Scene.UNKNOWN, ()), (Scene.ANIMATION, ()),
            (Scene.UNKNOWN, ()), (Scene.MAIN_BATTLE, ()), (Scene.MAIN_BATTLE, ()),
        ])
        self.assertTrue(ok)
        self.assertEqual(events, ["top_close"])

    def test_unknown_timeout_is_bounded_and_stops_after_one_blind_close(self):
        ok, _runtime, events, captures = self.run_recovery([(Scene.UNKNOWN, ())], timeout=2)
        self.assertFalse(ok)
        self.assertEqual(events, ["top_close"])
        self.assertEqual(captures, 4)

    def test_positive_terminal_during_animation_returns_to_existing_settlement(self):
        ok, _runtime, events, _captures = self.run_recovery([
            (Scene.UNKNOWN, ()), (Scene.UNKNOWN, ()), (Scene.VICTORY, ()),
        ])
        self.assertTrue(ok)
        self.assertEqual(events, ["top_close"])

    def test_stop_signal_never_allows_more_cleanup_clicks(self):
        for stop_on_frame in (1, 2):
            with self.subTest(stop_on_frame=stop_on_frame):
                ok, _runtime, events, _captures = self.run_recovery(
                    [(Scene.UNKNOWN, ())], stop_on_frame=stop_on_frame)
                self.assertFalse(ok)
                self.assertEqual(events, [] if stop_on_frame == 1 else ["top_close"])

    def test_screenshot_failure_does_not_click(self):
        ok, _runtime, events, _captures = self.run_recovery(
            [(Scene.UNKNOWN, ())], screenshot_failed=True)
        self.assertFalse(ok)
        self.assertEqual(events, [])

    def test_stubborn_known_overlay_stops_after_close_budget(self):
        ok, _runtime, events, _captures = self.run_recovery(
            [(Scene.MAIN_BATTLE, (recovery.NP_IDENTITY_GUARD,))])
        self.assertFalse(ok)
        self.assertEqual(len(events), recovery._MAX_CLOSE_ACTIONS)

    def test_low_confidence_main_never_releases_default_runner(self):
        ok, _runtime, events, _captures = self.run_recovery(
            [(Scene.MAIN_BATTLE, (), .8)], timeout=2)
        self.assertFalse(ok)
        self.assertEqual(events, ["top_close"])

    def test_failed_swap_cancel_is_not_reported_as_success(self):
        ok, _runtime, events, _captures = self.run_recovery(
            [(Scene.ORDER_CHANGE, ())], cancel_ok=False)
        self.assertFalse(ok)
        self.assertEqual(events, ["top_close", "cancel_swap"])

    def test_recovery_does_not_use_image_freeze_or_revive_retreat_actions(self):
        source = inspect.getsource(recovery)
        for forbidden in ("pre_wait_freezes", "post_wait_freezes", "wait_freezes", "post_key",
                          "select_card(", "select_np(", "cast_servant_skill(", "order_change("):
            self.assertNotIn(forbidden, source.replace("cancel_order_change(", "cancel_swap("))


if __name__ == "__main__":
    unittest.main()
