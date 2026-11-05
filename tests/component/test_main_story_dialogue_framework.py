"""实际国服选项截图与真实识别，只向内存控制器记录落点。"""
from pathlib import Path
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker
from test_main_story_framework import FrameworkResources, ScreenController, StoryRunner

ROOT = Path(__file__).resolve().parents[2]


class DialogueFrameworkTests(FrameworkResources, unittest.TestCase):
    def test_exact_choices_reject_each_other_and_click_lower_option(self):
        resource = self.resource('cn')
        cases = (
            ('cn_quetzal_choice.png', '主线-剧情选择-不赞同魁札尔'),
            ('cn_eresh_choice.png', '主线-剧情选择-埃列什基伽勒'),
            ('cn_eresh_choice_long.png', '主线-剧情选择-埃列什基伽勒'),
        )
        for filename, expected in cases:
            with self.subTest(filename=filename):
                image = cv2.imdecode(np.fromfile(str(ROOT / 'tests/fixtures/main_story' / filename), np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)
                controller = ScreenController(image)
                self.assertTrue(controller.post_connection().wait().succeeded)
                results = {}

                class Probe(CustomAction):
                    def run(self, context, argv):
                        for _, node in cases:
                            detail = context.run_recognition(node, image)
                            results[node] = bool(detail and detail.hit)
                        results['handled'] = StoryRunner(context).interrupt(image)
                        return True

                resource.register_custom_action('probe_exact_choice', Probe())
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                try:
                    node = {'action': {'type': 'Custom', 'param': {'custom_action': 'probe_exact_choice'}}}
                    self.assertTrue(tasker.post_task('probe_exact_choice', {'probe_exact_choice': node}).wait().succeeded)
                    self.assertTrue(results['handled'])
                    for _, name in cases:
                        self.assertEqual(results[name], name == expected)
                    self.assertEqual(len(controller.clicks), 1)
                    x, y = controller.clicks[0]
                    self.assertTrue(270 <= x <= 1070 and 290 <= y <= 380)
                    self.assertEqual(controller.swipes, [])
                finally:
                    tasker.post_stop().wait()


if __name__ == '__main__':
    unittest.main()
