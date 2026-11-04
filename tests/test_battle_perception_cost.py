"""按需识别保留场景和目标安全门，不复用跨帧敌人状态。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.core.enums import Scene
from battle.perception import config, perception
from battle.runtime.runtime import AutoBattleRuntime


class PerceptionCostTests(unittest.TestCase):
    def test_non_combat_scenes_do_not_read_enemy_text(self):
        for scene in (Scene.VICTORY, Scene.DEFEAT, Scene.ORDER_CHANGE, Scene.UNKNOWN):
            with self.subTest(scene=scene), patch.object(perception, "_detect_scene", return_value=(scene, 1)), \
                    patch.object(perception, "_detect_enemies") as enemies:
                state = perception.build(Mock(), object())
                self.assertEqual(state.enemies, ())
                enemies.assert_not_called()

    def test_target_check_reads_only_requested_enemy_on_current_frame(self):
        frames = [object(), object()]
        ctx = Mock()
        def reco(name, image):
            # 第二帧目标已消失，不能沿用上一帧的存活/选中态。
            return NS(hit=image is frames[0], best_result=NS(score=.99))
        ctx.run_recognition.side_effect = reco
        first = perception._detect_enemies(ctx, frames[0], slots=(2,))
        second = perception._detect_enemies(ctx, frames[1], slots=(2,))
        self.assertEqual([(e.slot, e.targeted) for e in first], [(2, True)])
        self.assertEqual(second, ())
        self.assertEqual([c.args[0] for c in ctx.run_recognition.call_args_list],
                         [config.ENEMY_NODE.format(slot=2), config.ENEMY_TARGET_NODE.format(slot=2),
                          config.ENEMY_NODE.format(slot=2)])

    def test_normal_decision_keeps_all_enemies(self):
        ctx = Mock()
        ctx.run_recognition.return_value = NS(hit=True, best_result=NS(score=.99))
        self.assertEqual([e.slot for e in perception._detect_enemies(ctx, object())], [1, 2, 3])

    def test_order_change_remains_default_and_precedes_background_main(self):
        ctx = Mock()
        ctx.run_recognition.side_effect = lambda name, image: NS(
            hit=name in ("战斗_换人界面", "战斗_主界面"), best_result=NS(score=.99))
        self.assertIs(perception.detect_scene(ctx, object()), Scene.ORDER_CHANGE)
        self.assertNotIn("战斗_主界面", [c.args[0] for c in ctx.run_recognition.call_args_list])

    def test_non_swap_poll_skips_order_ocr_and_still_finds_victory(self):
        ctx = Mock()
        ctx.run_recognition.side_effect = lambda name, image: NS(
            hit=name == "战斗_胜利", best_result=NS(score=.99))
        self.assertIs(perception.detect_scene(ctx, object(), check_order_change=False), Scene.VICTORY)
        self.assertNotIn("战斗_换人界面", [c.args[0] for c in ctx.run_recognition.call_args_list])

    def test_runtime_only_disables_order_ocr_outside_swap_wait(self):
        for scene in (Scene.MAIN_BATTLE, Scene.ORDER_CHANGE, Scene.VICTORY):
            with self.subTest(scene=scene):
                runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
                runtime.ctx = NS(tasker=NS(stopping=False))
                frame = object()
                runtime.controller = Mock()
                runtime.controller.post_screencap.return_value.wait.return_value.get.return_value = frame
                with patch("battle.runtime.runtime.perception.detect_scene", return_value=scene) as detect:
                    self.assertTrue(runtime._wait_until((scene,), 1))
                if scene is Scene.ORDER_CHANGE:
                    detect.assert_called_once_with(runtime.ctx, frame)
                else:
                    detect.assert_called_once_with(runtime.ctx, frame, check_order_change=False)


if __name__ == "__main__":
    unittest.main()
