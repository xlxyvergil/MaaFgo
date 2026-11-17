"""Replay real CN MCP screenshots through production recovery and MaaFramework.

The two PNGs are original, unedited 1280x720 game screenshots from
.workbuddy/runtime/maa-mcp-data/evidence/story_fallback_cn_retry2_20261116/:
partial_cards.png = frame_00003_partial_cards_fault.png (two selected cards),
main_after_cancel.png = frame_00009_recovery_after.png (after cancelling them).
They are not synthetic scenes. Only an in-memory controller is used; its return
click changes the supplied screenshot. Recognition results are never mocked.
"""
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_main_story_framework import FrameworkResources, ScreenController

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.execution.executor import Executor
from battle.runtime import preset_fallback as recovery

FIXTURES = ROOT / "tests/fixtures/battle_preset_fallback_cn"
RETURN = (1194, 682)


def fixture(name):
    image = cv2.imdecode(np.fromfile(FIXTURES / name, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape != (720, 1280, 3):
        raise AssertionError(f"原始MCP战斗截图无法读取：{name}")
    return image


class CancelCardsController(ScreenController):
    def __init__(self, *, return_works=True):
        super().__init__(fixture("partial_cards.png"))
        self.return_works = return_works
        self.view = "cards"
        self.captures = 0

    def screencap(self):
        self.captures += 1
        return super().screencap()

    def click(self, x, y):
        super().click(x, y)
        if (x, y) == RETURN and self.return_works:
            self.image = fixture("main_after_cancel.png")
            self.view = "main"
        return True


class RecordingContext:
    """Observe real Context results; do not replace recognition or Pipeline work."""
    def __init__(self, context, controller):
        self.context, self.controller = context, controller
        self.recognitions, self.tasks = [], []

    def __getattr__(self, name):
        return getattr(self.context, name)

    def run_recognition(self, name, image):
        result = self.context.run_recognition(name, image)
        score = getattr(getattr(result, "best_result", None), "score", None)
        self.recognitions.append((name, self.controller.view, self.controller.captures,
                                  bool(result and result.hit), score))
        return result

    def run_task(self, name, **kwargs):
        self.tasks.append(name)
        return self.context.run_task(name, **kwargs)


class PresetFallbackCnSceneFrameworkTests(FrameworkResources, unittest.TestCase):
    def recover(self, *, return_works=True):
        resource = self.resource("cn")  # Base first, then CN image/Pipeline overrides.
        controller = CancelCardsController(return_works=return_works)
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        outcomes, contexts, errors = [], [], []

        class Probe(CustomAction):
            def run(self, ctx, argv):
                try:
                    observed = RecordingContext(ctx, controller)
                    contexts.append(observed)
                    # The real controller proxy is obtained once, as in the
                    # production CustomAction, and used by the real Executor.
                    proxy = ctx.tasker.controller
                    runtime = NS(ctx=observed, controller=proxy,
                                 executor=Executor(ctx, proxy))
                    outcomes.append(recovery.recover_for_default_battle(runtime))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        name = "本地国服预设回退画面验证"
        self.assertTrue(resource.register_custom_action(name, Probe()))
        try:
            job = tasker.post_task(name, {name: {
                "action": {"type": "Custom", "param": {"custom_action": name}},
                "next": [], "on_error": [], "pre_delay": 0, "post_delay": 0,
            }})
            deadline = time.monotonic() + 30
            while not job.status.done and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(job.status.done, "原始截图回放的界面恢复未在有界时间内结束")
            self.assertTrue(job.status.succeeded, errors)
        finally:
            tasker.post_stop().wait()
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(len(contexts), 1)
        return outcomes[0], controller, contexts[0]

    def test_two_selected_cards_cancel_before_two_fresh_main_confirmations(self):
        ok, controller, context = self.recover()
        self.assertTrue(ok)
        self.assertEqual(controller.clicks, [(1240, 20), RETURN])
        self.assertEqual(controller.swipes, [])
        self.assertEqual(context.tasks, [recovery._CARD_RETURN])
        cards = [item for item in context.recognitions
                 if item[0] == "战斗_选卡场景" and item[1] == "cards" and item[3]]
        self.assertGreaterEqual(len(cards), 2)
        main = [item for item in context.recognitions
                if item[0] == "战斗_主界面" and item[1] == "main" and item[3]]
        self.assertEqual(len(main), 2)
        self.assertTrue(all(item[4] >= .95 for item in main))
        self.assertGreater(main[1][2], main[0][2], "两次主界面确认必须来自新截图")

    def test_return_click_without_main_transition_cannot_claim_recovery(self):
        ok, controller, context = self.recover(return_works=False)
        self.assertFalse(ok)
        self.assertEqual(controller.view, "cards")
        self.assertEqual(controller.clicks, [(1240, 20), RETURN, RETURN])
        self.assertEqual(context.tasks, [recovery._CARD_RETURN] * 2)
        self.assertFalse(any(item[0] == "战斗_主界面" and item[3]
                             for item in context.recognitions))

    def test_wrong_region_return_template_cannot_recognize_chinese_cards(self):
        for server in ("base", "jp"):
            with self.subTest(server=server):
                if server == "base":
                    resource = Resource()
                    resource.use_cpu()
                    self.assertTrue(resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded)
                else:
                    resource = self.resource(server)
                controller = CancelCardsController()
                self.assertTrue(controller.post_connection().wait().succeeded)
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                hits = []

                class Probe(CustomAction):
                    def run(self, ctx, argv):
                        detail = ctx.run_recognition(recovery._CARD_RETURN, controller.image)
                        hits.append(bool(detail and detail.hit))
                        return True

                name = "本地区服选卡返回识别验证"
                resource.register_custom_action(name, Probe())
                try:
                    self.assertTrue(tasker.post_task(name, {name: {
                        "action": {"type": "Custom", "param": {"custom_action": name}},
                        "next": [], "on_error": [],
                    }}).wait().succeeded)
                finally:
                    tasker.post_stop().wait()
                self.assertEqual(hits, [False])
                self.assertEqual(controller.clicks, [])


if __name__ == "__main__":
    unittest.main()
