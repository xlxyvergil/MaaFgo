"""通用原生战斗的出卡后下载失败恢复；真实模板识别与内存控制器点击。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from battle.runtime.runtime import AutoBattleRuntime
from test_main_story_framework import FrameworkResources, ScreenController


class PostAttackDownloadTests(FrameworkResources, unittest.TestCase):
    def fixture(self, name):
        image = cv2.imdecode(np.fromfile(ROOT / 'tests/fixtures' / name, np.uint8), 1)
        self.assertIsNotNone(image)
        return image

    def probe(self, image, callback, *, click_destination=None):
        resource = self.resource('cn')
        class Controller(ScreenController):
            def click(self, x, y):
                self.clicks.append((x, y))
                if click_destination is not None:
                    self.image = click_destination
                return True
        controller = Controller(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        outcomes, errors = [], []
        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    outcomes.append(callback(context, controller))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False
        resource.register_custom_action('post_attack_download_probe', Probe())
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        try:
            result = tasker.post_task('post_attack_download_probe', {
                'post_attack_download_probe': {'action': {'type': 'Custom', 'param': {
                    'custom_action': 'post_attack_download_probe'}}, 'on_error': []}
            }).wait()
            self.assertTrue(result.succeeded, errors)
            self.assertEqual(controller.swipes, [])
            return outcomes[0], controller.clicks
        finally:
            tasker.post_stop().wait()

    def test_actual_download_retry_clicks_right_button_and_waits_for_main(self):
        error = self.fixture('strengthening/cn_download_error.png')
        main = self.fixture('battle_preset_fallback_cn/main_after_cancel.png')
        def check(ctx, controller):
            runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
            runtime.ctx, runtime.controller = ctx, controller
            runtime.executor = NS(tap_top_right_close=Mock())
            with patch('battle.runtime.runtime._ANIMATION_TIMEOUT_S', 5):
                ok = runtime._wait_turn_settled()
            runtime.executor.tap_top_right_close.assert_not_called()
            return ok
        ok, clicks = self.probe(error, check, click_destination=main)
        self.assertTrue(ok)
        self.assertEqual(len(clicks), 1)
        x, y = clicks[0]
        self.assertTrue(707 <= x <= 970 and 530 <= y <= 600, clicks)

    def test_absent_retry_button_and_normal_battle_are_not_clicked(self):
        error = self.fixture('strengthening/cn_download_error.png')
        # 中央报错文字仍在，移除按钮：不能因文字或弹窗背景点击固定位置。
        error[500:620, 280:1000] = 0
        main = self.fixture('battle_preset_fallback_cn/main_after_cancel.png')
        def check(ctx, controller):
            return [bool((result := ctx.run_recognition('战斗-出卡后下载重试', image)) and result.hit)
                    for image in (error, main)]
        hits, clicks = self.probe(error, check)
        self.assertEqual(hits, [False, False])
        self.assertEqual(clicks, [])
