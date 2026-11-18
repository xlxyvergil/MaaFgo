import unittest
from test_main_story import MAP,PANEL,runner
from main_story_action import StoryStopped

CHOICE='主线-剧情选择-不赞同魁札尔'
GENERIC_CHOICE='跳过剧情-强制选项'
SKIP='跳过剧情-点击跳过'
CONTINUE='跳过剧情-灰色继续'

class DialogueChoicesTests(unittest.TestCase):
    def test_forced_choice_has_priority_over_gray_skip_and_old_confirm_timer(self):
        r,c=runner()
        r.skip_confirm_until=20
        self.assertTrue(r.interrupt({CHOICE,GENERIC_CHOICE,SKIP}))
        self.assertEqual([name for name,_ in c.actions],[CHOICE])
        self.assertEqual(r.skip_confirm_until,0)

    def test_ordinary_map_does_not_trigger_choice(self):
        r,c=runner()
        self.assertFalse(r.interrupt(set()))
        self.assertEqual(c.actions,[])

    def test_generic_choice_precedes_gray_skip_and_pending_confirmation(self):
        r,c=runner()
        r.skip_confirm_until=20
        self.assertTrue(r.interrupt({GENERIC_CHOICE,SKIP,'跳过剧情-确认跳过'}))
        self.assertEqual([name for name,_ in c.actions],[GENERIC_CHOICE])
        self.assertEqual(r.skip_confirm_until,0)

    def test_generic_choice_then_regular_skip_and_confirmation(self):
        r,c=runner()
        self.assertTrue(r.interrupt({GENERIC_CHOICE,SKIP}))
        r.pause(1.1)
        self.assertTrue(r.interrupt({SKIP}))
        self.assertTrue(r.interrupt({'跳过剧情-确认跳过'}))
        self.assertEqual([name for name,_ in c.actions],
                         [GENERIC_CHOICE,SKIP,'跳过剧情-确认跳过'])

    def test_skip_disabled_leaves_both_specific_and_generic_choices_untouched(self):
        r,c=runner()
        c.nodes[SKIP]['enabled']=False
        c.nodes.setdefault(GENERIC_CHOICE,{})['enabled']=False
        self.assertFalse(r.interrupt({CHOICE,GENERIC_CHOICE,SKIP}))
        self.assertEqual(c.actions,[])

    def test_choice_without_readable_skip_then_map_or_list_is_pure_story(self):
        for destination in (MAP,PANEL):
            with self.subTest(destination=destination):
                r,c=runner([{GENERIC_CHOICE},destination])
                self.assertEqual(r.wait_after_quest_click(),'story')
                self.assertEqual([name for name,_ in c.actions],[GENERIC_CHOICE])

    def test_continue_dialogue_expands_choice_then_skips_and_confirms(self):
        r,c=runner()
        r.skip_confirm_until=20
        self.assertTrue(r.interrupt({CONTINUE,SKIP}))
        self.assertEqual(r.skip_confirm_until,0)
        self.assertTrue(r.interrupt({GENERIC_CHOICE,CONTINUE,SKIP}))
        r.pause(1.1)
        self.assertTrue(r.interrupt({SKIP}))
        self.assertTrue(r.interrupt({'跳过剧情-确认跳过'}))
        self.assertEqual([name for name,_ in c.actions],
                         [CONTINUE,GENERIC_CHOICE,SKIP,'跳过剧情-确认跳过'])

    def test_disabled_skip_never_continues_dialogue(self):
        r,c=runner()
        c.nodes[SKIP]['enabled']=False
        c.nodes.setdefault(CONTINUE,{})['enabled']=False
        self.assertFalse(r.interrupt({CONTINUE,SKIP}))
        self.assertEqual(c.actions,[])

    def test_missing_continue_marker_does_not_click_dialogue(self):
        r,c=runner()
        self.assertFalse(r.interrupt(set()))
        self.assertEqual(c.actions,[])

    def test_repeated_dialogue_continue_has_bounded_retries(self):
        r,c=runner()
        r._dialogue_choices[CONTINUE]=60
        with self.assertRaisesRegex(StoryStopped,'已达60次'):
            r.interrupt({CONTINUE,SKIP})
        self.assertEqual(c.actions,[])

    def test_dialogue_continue_without_skip_then_map_is_pure_story(self):
        r,c=runner([{CONTINUE},MAP])
        self.assertEqual(r.wait_after_quest_click(),'story')
        self.assertEqual([name for name,_ in c.actions],[CONTINUE])

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
        self.assertTrue(r.interrupt({node,GENERIC_CHOICE,SKIP}))
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
        self.assertTrue(r.interrupt({node,GENERIC_CHOICE,SKIP}))
        self.assertEqual([name for name,_ in c.actions],[node])

if __name__=='__main__':unittest.main()
