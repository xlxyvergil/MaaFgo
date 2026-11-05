import unittest
from test_main_story import runner
from main_story_action import StoryStopped

CHOICE='主线-剧情选择-不赞同魁札尔'

class DialogueChoicesTests(unittest.TestCase):
    def test_forced_choice_has_priority_over_gray_skip_and_old_confirm_timer(self):
        r,c=runner()
        r.skip_confirm_until=20
        self.assertTrue(r.interrupt({CHOICE,'跳过剧情-点击跳过'}))
        self.assertEqual([name for name,_ in c.actions],[CHOICE])
        self.assertEqual(r.skip_confirm_until,0)

    def test_ordinary_map_does_not_trigger_choice(self):
        r,c=runner()
        self.assertFalse(r.interrupt(set()))
        self.assertEqual(c.actions,[])

    def test_unchanged_choices_have_bounded_retries(self):
        r,c=runner()
        for _ in range(3):
            self.assertTrue(r.interrupt({CHOICE}))
        with self.assertRaisesRegex(StoryStopped,'点击3次'):
            r.interrupt({CHOICE})
        self.assertEqual(len(c.actions),3)

if __name__=='__main__':unittest.main()
