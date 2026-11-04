"""Three successful clicks must leave card selection before the turn can settle."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]

from battle.core.enums import CardColor, PrimitiveKind, Scene
from battle.core.models import BattleAction, BattleState, CardPick, CommandCard, Confidence
from battle.core.policy import StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime


class CardSubmissionTests(unittest.TestCase):
    def runtime(self):
        context = NS(tasker=NS(stopping=False))
        image = object()
        controller = NS(post_screencap=lambda: NS(wait=lambda: NS(get=lambda: image)))
        action = BattleAction(None, tuple(CardPick(PrimitiveKind.SELECT_CARD, i) for i in (1, 2, 3)))
        decider = NS(plan=None, decide=lambda *_args, **_kwargs: action)
        runtime = AutoBattleRuntime(context, controller, decider, StrategyProfile())
        runtime.executor = NS(tap_top_right_close=Mock())
        runtime._handle_post_attack_story = Mock(return_value=None)
        return runtime

    def wait(self, frames, *, run=False, stop_on_frame=None):
        runtime = self.runtime()
        clock = [0.0]
        pending = iter(frames)
        current = [frames[-1]]
        observations = [0]

        def detect(_context, _image, **_kwargs):
            current[0] = next(pending, current[0])
            scene, elapsed = current[0]
            clock[0] += elapsed
            observations[0] += 1
            if observations[0] == stop_on_frame:
                runtime.ctx.tasker.stopping = True
            return scene

        def sleep(seconds):
            clock[0] += seconds

        if run:
            state = BattleState(Scene.COMMAND_SELECTION, Confidence(1),
                tuple(CommandCard(i, CardColor.BUSTER, None, Confidence(1)) for i in range(1, 6)),
                (), ())
            runtime._observe = Mock(return_value=state)
            runtime._execute_selection = Mock(return_value=True)
        with patch('battle.runtime.runtime.perception.detect_scene', side_effect=detect), \
             patch('battle.runtime.runtime.time.monotonic', side_effect=lambda: clock[0]), \
             patch('battle.runtime.runtime.time.sleep', side_effect=sleep):
            result = runtime.run() if run else runtime._wait_turn_settled()
        return result, runtime, clock[0], observations[0]

    def test_stable_selection_fails_quickly_without_touching_existing_choices(self):
        ok, runtime, elapsed, observations = self.wait([(Scene.COMMAND_SELECTION, 0)])
        self.assertFalse(ok)
        self.assertEqual(runtime._post_attack_failure_reason, 'card_selection_not_submitted')
        self.assertEqual(elapsed, 3.0)
        self.assertGreaterEqual(observations, 2)
        runtime.executor.tap_top_right_close.assert_not_called()
        runtime._handle_post_attack_story.assert_not_called()

    def test_run_preserves_failure_reason_and_does_not_advance_or_reselect(self):
        result, runtime, elapsed, _ = self.wait([(Scene.COMMAND_SELECTION, 0)], run=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, 'card_selection_not_submitted')
        self.assertEqual(result.turns, 0)
        self.assertEqual(elapsed, 3.0)
        runtime._execute_selection.assert_called_once()
        runtime.executor.tap_top_right_close.assert_not_called()

    def test_transient_selection_can_start_animation_and_finish_normally(self):
        ok, runtime, _, _ = self.wait([(Scene.COMMAND_SELECTION, 0)] * 3
            + [(Scene.UNKNOWN, 0), (Scene.MAIN_BATTLE, 0)])
        self.assertTrue(ok)
        self.assertEqual(runtime._post_attack_failure_reason, 'stuck_after_attack')

    def test_unknown_animation_does_not_accumulate_selection_failure_time(self):
        ok, runtime, elapsed, _ = self.wait([(Scene.COMMAND_SELECTION, 0)]
            + [(Scene.UNKNOWN, 90), (Scene.MAIN_BATTLE, 0)])
        self.assertTrue(ok)
        self.assertGreater(elapsed, 90)
        self.assertEqual(runtime._post_attack_failure_reason, 'stuck_after_attack')

    def test_non_selection_frame_resets_the_stability_window(self):
        ok, runtime, _, _ = self.wait([(Scene.COMMAND_SELECTION, 0)] * 5
            + [(Scene.UNKNOWN, 0)] + [(Scene.COMMAND_SELECTION, 0)] * 5
            + [(Scene.VICTORY, 0)])
        self.assertTrue(ok)
        self.assertEqual(runtime._post_attack_failure_reason, 'stuck_after_attack')

    def test_stop_signal_during_submission_wait_leaves_selection_untouched(self):
        ok, runtime, elapsed, _ = self.wait([(Scene.COMMAND_SELECTION, 0)], stop_on_frame=1)
        self.assertFalse(ok)
        self.assertLess(elapsed, 3)
        runtime.executor.tap_top_right_close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
