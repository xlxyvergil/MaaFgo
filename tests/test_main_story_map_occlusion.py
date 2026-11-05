"""波斯湾图标只用于打开列表，不能替代列表 NEXT 的进关证据。"""
import unittest
from types import SimpleNamespace as NS

from test_main_story import MAP, PANEL, NEXT, runner
from main_story_action import StoryStopped

ICON = '主线-地图提亚马特'
NORMAL = '主线-地图NEXT'


class MapOcclusionTests(unittest.TestCase):
    def test_icon_can_locate_current_list_when_next_arrow_is_hidden(self):
        r, c = runner([MAP | {ICON}, PANEL | {NEXT}])
        self.assertIsNotNone(r.locate())
        self.assertEqual([name for name, _ in c.actions], [ICON])
        self.assertNotIn(NEXT, [name for name, _ in c.actions])

    def test_visible_next_has_priority_over_icon(self):
        r, c = runner([MAP | {ICON, NORMAL}, PANEL | {NEXT}])
        self.assertIsNotNone(r.locate())
        self.assertEqual([name for name, _ in c.actions], [NORMAL])
        self.assertNotIn(ICON, c.recognitions)

    def test_icon_without_current_list_next_never_starts_quest(self):
        r, c = runner([MAP | {ICON}, PANEL | {"主线-列表后退"}])
        with self.assertRaisesRegex(StoryStopped, '没有可推进的 NEXT'):
            r.locate()
        actions = [name for name, _ in c.actions]
        self.assertEqual(actions[0], ICON)
        self.assertNotIn(NEXT, actions)
        self.assertNotIn('进本-点击任务开始', actions)
        self.assertNotIn('进本-点击上一次关卡', actions)

    def test_panel_retry_refreshes_the_original_icon_instead_of_normal_next(self):
        r, c = runner([MAP | {ICON}])
        with self.assertRaisesRegex(StoryStopped, '未打开副本列表'):
            r.open_panel(NS(box=[100, 200, 1, 1]), ICON)
        self.assertEqual([name for name, _ in c.actions], [ICON] * 3)
        self.assertNotIn(NORMAL, c.recognitions)


if __name__ == '__main__':
    unittest.main()
