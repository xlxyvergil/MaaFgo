"""主线缺石撤退重进：仅重试当前进度，禁止改用其他续关资源。"""
import unittest
import json
from pathlib import Path
from unittest.mock import Mock
from test_main_story import runner, MAP
from main_story_action import StoryQuestRetry, StoryStopped

EMPTY = '主线-灵脉石不足'
OFFER = '主线-使用灵脉石'
RETREAT = '战斗失败_不回主界面'
CONFIRM = '主线-撤退确认'

class DefeatRetryTests(unittest.TestCase):
    def test_native_ui_exposes_opt_in_and_independent_retry_limit(self):
        root=Path(__file__).resolve().parents[1]
        options=json.loads((root/'assets/options/自动推主线.json').read_text(encoding='utf-8'))['option']
        native=next(case for case in options['主线-战斗方式']['cases'] if case['name']=='native')
        self.assertIn('主线-灵脉石不足时退出重进',native['option'])
        switch=options['主线-灵脉石不足时退出重进']
        self.assertEqual(switch['default_case'],'No')
        enabled=next(case for case in switch['cases'] if case['name']=='Yes')
        self.assertTrue(enabled['pipeline_override']['主线-配置']['attach']['retry_without_spirit_stone'])
        self.assertIn('主线-战败重进次数上限',enabled['option'])

    def test_default_off_preserves_failure_without_input(self):
        r,c = runner()
        self.assertFalse(r.retry_without_spirit_stone)
        with self.assertRaises(StoryStopped):
            r.continue_with_spirit_stone({EMPTY,RETREAT})
        self.assertEqual(c.actions,[])

    def test_missing_recognition_is_not_zero_stock(self):
        r,c = runner(config={'use_spirit_stone':True,'retry_without_spirit_stone':True})
        with self.assertRaisesRegex(StoryStopped,'正数库存'):
            r.continue_with_spirit_stone({RETREAT})
        self.assertEqual(c.actions,[])

    def test_positive_stone_still_uses_verified_stone_confirmation(self):
        r,c = runner(config={'use_spirit_stone':True,'retry_without_spirit_stone':True})
        r.retreat_and_retry = Mock()
        with self.assertRaisesRegex(StoryStopped,'消费弹窗'):
            r.continue_with_spirit_stone({OFFER,RETREAT})
        r.retreat_and_retry.assert_not_called()
        self.assertEqual([name for name,_ in c.actions],[OFFER])

    def test_zero_stock_can_retry_even_when_stone_use_off(self):
        r,c = runner([{CONFIRM},MAP,MAP],{'retry_without_spirit_stone':True})
        with self.assertRaises(StoryQuestRetry):
            r.continue_with_spirit_stone({EMPTY,RETREAT})
        self.assertEqual([name for name,_ in c.actions],[RETREAT,CONFIRM])
        self.assertEqual(r._defeat_retries,1)
        self.assertEqual((r.completed,r.battles,r._spirit_stones_used),(0,0,0))

    def test_limit_stops_before_any_retreat_click(self):
        r,c = runner(config={'retry_without_spirit_stone':True,'defeat_retry_limit':2})
        r._defeat_retries=2
        with self.assertRaisesRegex(StoryStopped,'重进2次'):
            r.retreat_and_retry({EMPTY,RETREAT})
        self.assertEqual(c.actions,[])

    def test_unknown_confirmation_never_clicks_generic_decide_or_consumes(self):
        r,c = runner([{'普通决定','主线-灵脉石确认'}],{'retry_without_spirit_stone':True,'transition_timeout':10})
        with self.assertRaisesRegex(StoryStopped,'未确认退出提示'):
            r.retreat_and_retry({EMPTY,RETREAT})
        self.assertEqual([name for name,_ in c.actions],[RETREAT])
        self.assertEqual(r._defeat_retries,0)

    def test_confirmed_retreat_without_map_does_not_start_a_new_battle(self):
        r,c = runner([{CONFIRM},set()],{'retry_without_spirit_stone':True,'transition_timeout':10})
        with self.assertRaisesRegex(StoryStopped,'未返回主线地图'):
            r.retreat_and_retry({EMPTY,RETREAT})
        self.assertEqual(c.tasks,[])
        self.assertEqual([name for name,_ in c.actions],[RETREAT,CONFIRM])

    def test_user_stop_before_retreat_has_zero_input(self):
        r,c = runner(config={'retry_without_spirit_stone':True})
        c.tasker.stopping=True
        with self.assertRaises(StoryStopped):
            r.retreat_and_retry({EMPTY,RETREAT})
        self.assertEqual(c.actions,[])

    def test_retry_does_not_consume_progress_limit_or_skip_to_next_quest(self):
        r,c=runner(config={'retry_without_spirit_stone':True,'quest_limit':1})
        r.locate=Mock(return_value=None)
        r.fight=Mock(side_effect=[StoryQuestRetry('retry'),None])
        r.settle=Mock()
        r._defeat_retries=1
        r.run()
        self.assertEqual(r.locate.call_count,2)
        self.assertEqual(r.fight.call_count,2)
        r.settle.assert_called_once()
        self.assertEqual((r.completed,r.battles,r._defeat_retries),(1,1,0))

    def test_retry_limit_rejects_boolean_and_out_of_range(self):
        for value in (0,10,True,'3'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'1至9'):
                runner(config={'retry_without_spirit_stone':True,'defeat_retry_limit':value})

    def test_bbc_never_retreats_automatically(self):
        r,c=runner(config={'retry_without_spirit_stone':True,'battle_mode':'bbc'})
        with self.assertRaises(StoryStopped):
            r.continue_with_spirit_stone({EMPTY,RETREAT})
        self.assertEqual(c.actions,[])

if __name__=='__main__':unittest.main()
