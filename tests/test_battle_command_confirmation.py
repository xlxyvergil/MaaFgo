"""慢速手机识别应有足够的开卡确认时间；超时和取消仍须停止。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.enums import Scene
from battle.core.models import BattleAction, BattleState, Confidence
from battle.core.policy import StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime


class CommandConfirmationTests(unittest.TestCase):
    def run_scenario(self, observations, *, cancel_during_recognition=False):
        clock = [0.0]
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        capture = Mock()
        capture.wait.return_value = capture
        capture.get.return_value = object()
        controller = Mock()
        controller.post_screencap.return_value = capture
        decider = SimpleNamespace(
            plan=None, decide=lambda *_args, **_kwargs: BattleAction(None, ())
        )
        runtime = AutoBattleRuntime(context, controller, decider, StrategyProfile())
        runtime.executor = SimpleNamespace(open_command_cards=Mock(return_value=True))
        runtime._observe = Mock(side_effect=[
            BattleState(Scene.MAIN_BATTLE, Confidence(1.0), (), (), ()),
            BattleState(Scene.VICTORY, Confidence(1.0), (), (), ()),
        ])
        pending = iter(observations)

        def detect(_context, _image, **_kwargs):
            scene, elapsed = next(pending)
            clock[0] += elapsed
            if cancel_during_recognition:
                context.tasker.stopping = True
            return scene

        def sleep(seconds):
            clock[0] += seconds

        with patch("battle.runtime.runtime.perception.detect_scene", side_effect=detect), \
                patch("battle.runtime.runtime.time.monotonic", side_effect=lambda: clock[0]), \
                patch("battle.runtime.runtime.time.sleep", side_effect=sleep):
            result = runtime.run()
        return result, runtime, clock[0]

    def test_slow_recognition_can_confirm_without_another_attack(self):
        for seconds in (6.0, 12.0):
            with self.subTest(recognition_seconds=seconds):
                result, runtime, _ = self.run_scenario([
                    (Scene.MAIN_BATTLE, seconds), (Scene.COMMAND_SELECTION, 0.1)
                ])
                self.assertTrue(result.ok, result.reason)
                runtime.executor.open_command_cards.assert_called_once()
                self.assertEqual(runtime._observe.call_count, 2)

    def test_confirmed_scene_within_deadline_still_resumes(self):
        result, runtime, elapsed = self.run_scenario([(Scene.COMMAND_SELECTION, 0.1)])
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(elapsed, 0.1)  # 最大等待时间不是固定等待时间。
        runtime.executor.open_command_cards.assert_called_once()

    def test_stuck_main_scene_exhausts_bounded_retries(self):
        result, runtime, _ = self.run_scenario(
            [(Scene.MAIN_BATTLE, 16.0), (Scene.MAIN_BATTLE, 0.0)] * 6
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "open_cards_confirm_failed")
        self.assertEqual(runtime.executor.open_command_cards.call_count, 6)
        runtime._observe.assert_called_once()

    def test_unknown_scene_stops_without_further_attack(self):
        result, runtime, _ = self.run_scenario([
            (Scene.MAIN_BATTLE, 16.0), (Scene.UNKNOWN, 0.0)
        ])
        self.assertFalse(result.ok)
        runtime.executor.open_command_cards.assert_called_once()
        runtime._observe.assert_called_once()

    def test_cancellation_during_recognition_does_not_resume(self):
        result, runtime, _ = self.run_scenario([
            (Scene.MAIN_BATTLE, 6.0), (Scene.COMMAND_SELECTION, 0.0)
        ], cancel_during_recognition=True)
        self.assertFalse(result.ok)
        runtime.executor.open_command_cards.assert_called_once()
        runtime._observe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
