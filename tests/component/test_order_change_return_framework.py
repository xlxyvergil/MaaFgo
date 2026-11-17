"""真实框架在动态内存画面上识别换人返回；不连接设备。"""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

import cv2
import maa
import numpy as np
from maa.custom_action import CustomAction
from maa.define import LoggingLevelEnum
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom'), str(Path(__file__).parent)]
from battle.runtime.runtime import AutoBattleRuntime
from test_formation_framework import MemoryController

ENTRY = '战斗_换人后等待主界面'
RETURNED = '战斗_换人后主界面'


class OrderChangeReturnFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Library.open(Path(os.environ.get('MAAFW_BINARY_PATH', Path(maa.__file__).parent / 'bin')))
        Tasker.set_log_dir('')
        Tasker.set_stdout_level(LoggingLevelEnum.Off)

    def run_case(self, *, main_returns):
        source = json.loads((ROOT / 'assets/resource/base/pipeline/自动战斗_感知.json').read_text('utf-8'))
        nodes = {name: copy.deepcopy(source[name]) for name in (ENTRY, RETURNED)}
        if not main_returns:
            nodes[ENTRY]['timeout'] = 100  # 缩短负例识别上限；保留实际3秒过渡。
        nodes['test-return'] = {
            'action': {'type': 'Custom', 'param': {'custom_action': 'test_return'}},
            'pre_delay': 0, 'post_delay': 0, 'on_error': [],
        }
        template_path = ROOT / 'assets/resource/base/image/battle/scene_main_battle.png'
        template = cv2.imdecode(np.fromfile(template_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        results, errors, samples = [], [], []

        class DynamicController(MemoryController):
            def screencap(self):
                self.frames += 1
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                # 原冻结检测区域每帧随机变化，主界面按钮保持可识别。
                image[545:625, 40:930] = np.random.default_rng(self.frames).integers(
                    0, 256, (80, 890, 3), dtype=np.uint8)
                samples.append(int(image[545, 40, 0]))
                if main_returns and self.frames >= 3:
                    image[508:562, 1058:1210] = template
                return image

        class WaitReturn(CustomAction):
            def run(self, context, argv):
                try:
                    runtime = AutoBattleRuntime.__new__(AutoBattleRuntime)
                    runtime.ctx = context
                    results.append(runtime._wait_order_change_settled())
                    return results[-1]
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            (bundle / 'pipeline').mkdir()
            (bundle / 'image/battle').mkdir(parents=True)
            (bundle / 'pipeline/test.json').write_text(json.dumps(nodes, ensure_ascii=False), encoding='utf-8')
            shutil.copyfile(template_path, bundle / 'image/battle/scene_main_battle.png')
            resource = Resource()
            resource.use_cpu()
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            resource.register_custom_action('test_return', WaitReturn())
            controller = DynamicController(np.zeros((720, 1280, 3), dtype=np.uint8))
            self.assertTrue(controller.post_connection().wait().succeeded)
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            try:
                start = time.monotonic()
                job = tasker.post_task('test-return')
                deadline = start + 15
                while not job.status.done and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(job.status.done, '换人返回等待未在上限内结束')
                self.assertEqual(errors, [])
                self.assertEqual(results, [main_returns])
                self.assertEqual(job.status.succeeded, main_returns)
                self.assertGreaterEqual(time.monotonic() - start, 3)
                self.assertEqual(controller.inputs, 0)
                if main_returns:
                    self.assertGreaterEqual(len(set(samples)), 3)
            finally:
                tasker.post_stop().wait()

    def test_main_return_succeeds_while_skill_bar_keeps_changing(self):
        self.run_case(main_returns=True)

    def test_missing_main_battle_times_out_without_clicks(self):
        self.run_case(main_returns=False)


if __name__ == '__main__':
    unittest.main()
