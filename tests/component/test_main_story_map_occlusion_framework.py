"""真实国服地图裁图：立绘遮住箭头时，图标和地点须共同命中。"""
from pathlib import Path
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker
from test_main_story_framework import FrameworkResources, ScreenController, StoryRunner
from main_story_recognition import MainStoryNext

ROOT = Path(__file__).resolve().parents[2]


class MapOcclusionFrameworkTests(FrameworkResources, unittest.TestCase):
    def test_actual_animation_phases_and_empty_map(self):
        resource = self.resource('cn')
        resource.register_custom_recognition('main_story_next', MainStoryNext())
        for filename, hit in (
            ('cn_tiamat_map_phase2.png', True),
            ('cn_tiamat_map_phase8.png', True),
            ('cn_tiamat_map_absent.png', False),
        ):
            with self.subTest(filename=filename):
                path = ROOT / 'tests/fixtures/main_story' / filename
                image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)
                controller = ScreenController(image)
                self.assertTrue(controller.post_connection().wait().succeeded)
                results = {}

                class Probe(CustomAction):
                    def run(self, context, argv):
                        full = context.run_recognition('主线-地图NEXT', image)
                        detail = context.run_recognition('主线-地图提亚马特', image)
                        results['normal'] = bool(full and full.hit)
                        results['icon'] = bool(detail and detail.hit)
                        if detail and detail.hit:
                            StoryRunner(context).action('主线-地图提亚马特', detail)
                        return True

                resource.register_custom_action('probe_map_icon', Probe())
                tasker = Tasker()
                self.assertTrue(tasker.bind(resource, controller))
                try:
                    node = {'action': {'type': 'Custom', 'param': {'custom_action': 'probe_map_icon'}}}
                    self.assertTrue(tasker.post_task('probe_map_icon', {'probe_map_icon': node}).wait().succeeded)
                    self.assertEqual(results, {'normal': False, 'icon': hit})
                    self.assertEqual(len(controller.clicks), int(hit))
                    for x, y in controller.clicks:
                        self.assertTrue(1020 <= x <= 1040 and 480 <= y <= 530)
                    self.assertEqual(controller.swipes, [])
                finally:
                    tasker.post_stop().wait()


if __name__ == '__main__':
    unittest.main()
