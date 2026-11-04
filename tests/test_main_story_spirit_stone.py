import unittest
from unittest.mock import Mock
from test_main_story import runner
from main_story_action import StoryStopped

OFFER='主线-使用灵脉石'
CONFIRM='主线-灵脉石确认'
BATTLE='进本-战斗主界面已出现'
DEFEAT='战斗失败_不回主界面'

class SpiritStoneTests(unittest.TestCase):
    def test_default_off_never_clicks(self):
        r,c=runner()
        self.assertFalse(r.use_spirit_stone)
        with self.assertRaisesRegex(StoryStopped,'未开启'):
            r.continue_with_spirit_stone({OFFER})
        self.assertEqual(c.actions,[])

    def test_missing_item_or_zero_stock_stops_without_alternative_currency(self):
        r,c=runner(config={'use_spirit_stone':True})
        with self.assertRaisesRegex(StoryStopped,'正数库存'):
            r.continue_with_spirit_stone({DEFEAT})
        self.assertEqual(c.actions,[])

    def test_clicks_only_offer_then_verified_confirmation_then_waits_for_battle(self):
        r,c=runner([set(),{CONFIRM},set(),{BATTLE}],{'use_spirit_stone':True})
        r.continue_with_spirit_stone({OFFER,DEFEAT})
        self.assertEqual([n for n,_ in c.actions],[OFFER,CONFIRM])
        self.assertEqual(r._spirit_stones_used,1)
        self.assertEqual(r.completed,0)

    def test_confirm_timeout_does_not_repeat_offer_or_click_generic_decide(self):
        r,c=runner([{'普通决定'}],{'use_spirit_stone':True,'transition_timeout':10})
        with self.assertRaisesRegex(StoryStopped,'消费弹窗'):
            r.continue_with_spirit_stone({OFFER})
        self.assertEqual([n for n,_ in c.actions],[OFFER])
        self.assertEqual(r._spirit_stones_used,0)

    def test_loading_timeout_does_not_consume_again(self):
        r,c=runner([{CONFIRM}],{'use_spirit_stone':True,'transition_timeout':10})
        with self.assertRaisesRegex(StoryStopped,'未返回战斗'):
            r.continue_with_spirit_stone({CONFIRM})
        self.assertEqual([n for n,_ in c.actions],[CONFIRM])
        self.assertEqual(r._spirit_stones_used,1)

    def test_per_quest_limit_preserves_screen(self):
        r,c=runner(config={'use_spirit_stone':True})
        r._spirit_stones_used=3
        with self.assertRaisesRegex(StoryStopped,'3次'):
            r.continue_with_spirit_stone({OFFER})
        self.assertEqual(c.actions,[])

    def test_bbc_never_uses_stone(self):
        r,c=runner(config={'use_spirit_stone':True,'battle_mode':'bbc'})
        with self.assertRaises(StoryStopped):r.continue_with_spirit_stone({OFFER})
        self.assertEqual(c.actions,[])

    def test_battle_restarts_inside_same_session_with_default_plan(self):
        r,c=runner([{DEFEAT},{CONFIRM},{BATTLE},set()],{'use_spirit_stone':True})
        r.reach_formation=Mock();r._start_formation_battle=Mock()
        r.continue_with_spirit_stone=Mock()
        r._fight_current()
        battles=[o for n,o in c.tasks if n=='原生自动战斗入口']
        self.assertEqual(len(battles),2)
        self.assertEqual(battles[0],{})
        self.assertEqual(battles[1]['原生自动战斗入口']['action']['param']['custom_action_param'],
                         '{"native_battle_formation_reset":true}')
        r.reach_formation.assert_called_once()
        r._start_formation_battle.assert_called_once()
        self.assertEqual(r.completed,0)

    def test_disabled_fight_preserves_original_single_battle_path(self):
        r,c=runner()
        r.reach_formation=Mock();r._start_formation_battle=Mock();r.frame=Mock()
        r._fight_current()
        r.frame.assert_not_called()
        self.assertEqual(sum(n=='原生自动战斗入口' for n,_ in c.tasks),1)

if __name__=='__main__':unittest.main()
