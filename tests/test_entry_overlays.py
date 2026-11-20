"""进本战斗教程浮层与任务完成结算识别；不连接设备。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from test_main_story import Context
from main_story_action import StoryRunner
from battle.core.enums import Scene
from battle.perception import perception


def runner():
    context = Context()
    clock = [0.0]
    return StoryRunner(
        context, clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    ), context


class EntryOverlayTests(unittest.TestCase):
    def test_battle_tutorial_forward_is_clicked_during_entry(self):
        runtime, context = runner()
        self.assertTrue(runtime.interrupt({"进本-战斗教程前进"}, entering=True))
        self.assertEqual([name for name, _ in context.actions], ["进本-战斗教程前进"])

    def test_battle_tutorial_is_not_clicked_after_battle(self):
        runtime, context = runner()
        self.assertFalse(runtime.interrupt({"进本-战斗教程前进"}))
        self.assertEqual(context.actions, [])

    def test_completion_screen_is_reported_as_victory(self):
        class Probe:
            def run_recognition(self, name, image):
                return NS(hit=name == "战斗_任务完成", best_result=NS(score=1.0))

        self.assertIs(perception.detect_scene(Probe(), object(), check_order_change=False),
                      Scene.VICTORY)

    def test_unknown_screen_stays_unknown_without_completion_hint(self):
        class Probe:
            def run_recognition(self, name, image):
                return NS(hit=False, best_result=None)

        self.assertIs(perception.detect_scene(Probe(), object(), check_order_change=False),
                      Scene.UNKNOWN)


class OverlayPipelineTests(unittest.TestCase):
    def test_tutorial_leaf_is_reachable_from_battle_wait(self):
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/进本流程.json").read_text("utf8"))
        node = pipeline["进本-战斗教程前进"]
        self.assertEqual(node["action"], {"type": "Click"})
        self.assertEqual(node["next"], [])
        self.assertEqual(node["recognition"]["type"], "And")
        self.assertEqual(node["recognition"]["param"]["all_of"][0]["recognition"]["type"], "OCR")
        self.assertIn("[JumpBack]进本-战斗教程前进",
                      pipeline["进本-等待战斗主界面或剧情"]["next"])

    def test_completion_leaf_matches_both_server_prompts(self):
        pipeline = json.loads(
            (ROOT / "assets/resource/base/pipeline/自动战斗_感知.json").read_text("utf8"))
        node = pipeline["战斗_任务完成"]
        self.assertEqual(node["recognition"]["type"], "OCR")
        expected = node["recognition"]["param"]["expected"]
        self.assertIn("请点击游戏界面", expected)
        self.assertIn("画面をタッチしてください", expected)


if __name__ == "__main__":
    unittest.main()
