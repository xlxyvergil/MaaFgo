"""主线公告关闭必须执行叶子动作，并先于背景地图判断。"""
import unittest

from test_main_story import MAP, runner


class NoticeRoutingTests(unittest.TestCase):
    def test_notice_before_visible_map_during_settlement(self):
        name = "主线-右上公告关闭"
        r, c = runner([MAP | {name}, MAP, MAP])
        r.settle()
        self.assertEqual([n for n, _ in c.actions], [name])
        self.assertEqual(c.tasks, [])

    def test_notice_in_each_stage(self):
        name = "主线-右上公告关闭"
        for flags in ({}, {"entering": True}, {"forming": True}):
            with self.subTest(flags=flags):
                r, c = runner()
                self.assertTrue(r.interrupt({name}, **flags))
                self.assertEqual([n for n, _ in c.actions], [name])
                self.assertEqual(c.nodes[name]["action"]["type"], "Click")

    def test_ordinary_notice_uses_click_leaf_not_dispatcher(self):
        name = "进本-普通告知弹窗关闭"
        r, c = runner()
        self.assertTrue(r.interrupt({name, "进本-关闭告知弹窗"}))
        self.assertEqual([n for n, _ in c.actions], [name])
        self.assertEqual(c.nodes[name]["action"]["type"], "Click")

    def test_map_without_notice_has_no_click(self):
        r, c = runner()
        self.assertFalse(r.interrupt(MAP))
        self.assertEqual(c.actions, [])


if __name__ == "__main__":
    unittest.main()
