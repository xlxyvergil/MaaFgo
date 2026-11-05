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

    def test_eresh_choice_precedes_skip_confirmation(self):
        r,c=runner()
        node='主线-剧情选择-埃列什基伽勒'
        r.skip_confirm_until=20
        self.assertTrue(r.interrupt({node,'跳过剧情-点击跳过'}))
        self.assertEqual([name for name,_ in c.actions],[node])
        self.assertEqual(r.skip_confirm_until,0)

    def test_choice_budget_resets_after_completed_quest(self):
        r,c=runner()
        node='主线-剧情选择-埃列什基伽勒'
        r.limit=2
        r.locate=lambda: object()
        def enter(target):
            for _ in range(3):
                self.assertTrue(r.interrupt({node}))
            return 'story'
        r.enter=enter
        r.run()
        self.assertEqual(r.completed,2)
        self.assertEqual(len(c.actions),6)

    def test_eresh_patterns_reject_mixed_or_reversed_identity(self):
        import json,re
        from pathlib import Path
        node=json.loads((Path(__file__).resolve().parents[1]/'assets/resource/base/pipeline/主线剧情选项.json').read_text(encoding='utf-8'))['主线-剧情选择-埃列什基伽勒']
        rows=node['recognition']['param']['all_of']
        upper=rows[1]['recognition']['param']['expected'][0]
        lower=rows[2]['recognition']['param']['expected'][0]
        for text in ('伊什塔尔。','不用想也知道是伊什塔尔小姐……！'):
            self.assertIsNotNone(re.fullmatch(upper,text))
            self.assertIsNone(re.fullmatch(lower,text))
        for text in ('埃列什基伽勒。','经过深思熟虑决定是埃列什基伽勒……！'):
            self.assertIsNotNone(re.fullmatch(lower,text))
            self.assertIsNone(re.fullmatch(upper,text))
        for text in ('伊什塔尔和埃列什基伽勒','与两位女神都无关的其他选项'):
            self.assertIsNone(re.fullmatch(upper,text))
            self.assertIsNone(re.fullmatch(lower,text))

    def test_eresh_right_choice_dispatches_specific_upper_option_node(self):
        r,c=runner()
        node='主线-剧情选择-埃列什基伽勒没有错'
        self.assertTrue(r.interrupt({node,'跳过剧情-点击跳过'}))
        self.assertEqual([name for name,_ in c.actions],[node])

if __name__=='__main__':unittest.main()
