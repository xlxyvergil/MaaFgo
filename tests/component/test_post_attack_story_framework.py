"""真实框架执行既有剧情节点，验证恢复战斗/地图以及配置隔离。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_main_story_framework as framework
from battle.runtime.runtime import AutoBattleRuntime
from battle.execution.executor import Executor

CHOICE = '跳过剧情-强制选项'
SKIP = '跳过剧情-点击跳过'
CONTINUE = '跳过剧情-灰色继续'


class PostAttackStoryTests(framework.FrameworkResources, unittest.TestCase):
    def scenario(self, start, destination, *, gray_skip=False):
        resource = self.resource('jp')
        state = {'page': start, 'clicked_pages': []}
        class Controller(framework.ScreenController):
            def click(self, x, y):
                self.clicks.append((x,y))
                state['clicked_pages'].append(state['page'])
                if state['page'] == 'hidden_choice':
                    if x < 1000:
                        state['page'] = 'choice'
                elif state['page'] == 'choice':
                    # Only selecting the option unlocks the ordinary story SKIP.
                    if x < 1000:
                        state['page'] = 'story'
                else:
                    state['page'] = 'confirm' if x > 1000 else destination
                return True
        controller = Controller(np.zeros((720,1280,3),np.uint8))
        self.assertTrue(controller.post_connection().wait().succeeded)
        class Reco(CustomRecognition):
            def analyze(self, ctx, argv):
                page = state['page']
                if argv.node_name == CONTINUE and page == 'hidden_choice':
                    return (1205,650,40,50)
                if argv.node_name == CHOICE and page == 'choice':
                    return (400,200,100,40)
                if argv.node_name == SKIP and (page in ('story','confirm')
                                                or page in ('choice','hidden_choice') and gray_skip):
                    return (1143,24,79,34)
                if argv.node_name.startswith('跳过剧情-确认跳过') and page == 'confirm':
                    return (780,538,90,44)
                if argv.node_name == '战斗_主界面' and page == 'main':
                    return (1058,508,152,54)
                if argv.node_name in ('可能已经结束战斗_不回主界面','战斗结束-主界面') and page == 'map':
                    return (50,600,20,20)
                return None
        from battle.perception.config import SCENE_NODES
        names = set(SCENE_NODES.values()) | {CHOICE,CONTINUE,SKIP,'跳过剧情-确认跳过',
            '跳过剧情-确认跳过-已消失','可能已经结束战斗_不回主界面','战斗结束-主界面'}
        resource.register_custom_recognition('story_frame',Reco())
        overrides = {n:{'recognition':{'type':'Custom','param':{'custom_recognition':'story_frame'}},
                        'post_delay':0,'pre_delay':0} for n in names}
        overrides['战斗-剧情切换等待']={'post_delay':0}
        resource.override_pipeline(overrides)
        report = {}
        class Probe(CustomAction):
            def run(self, ctx, argv):
                r = AutoBattleRuntime.__new__(AutoBattleRuntime)
                r.ctx=ctx; r.controller=controller; r.executor=Executor(ctx,controller)
                before={name:ctx.get_node_data(name) for name in (CHOICE,CONTINUE,SKIP)}
                try:
                    with patch('battle.runtime.runtime._ANIMATION_TIMEOUT_S',8):
                        report['ok']=r._wait_turn_settled()
                    report['map']=r._post_attack_returned_to_map
                    report['isolated']=before=={
                        name:ctx.get_node_data(name) for name in (CHOICE,CONTINUE,SKIP)}
                    return report['ok']
                except Exception as exc:
                    report['error']=repr(exc)
                    return False
        resource.register_custom_action('story_probe',Probe())
        tasker=Tasker()
        self.assertTrue(tasker.bind(resource,controller))
        try:
            result=tasker.post_task('story_probe',{'story_probe':{'action':{
                'type':'Custom','param':{'custom_action':'story_probe'}}}}).wait()
            self.assertTrue(result.succeeded,report)
            self.assertTrue(report['isolated'])
            self.assertEqual(report['map'],destination=='map')
            expected_pages = (['hidden_choice','choice','story','confirm'] if start=='hidden_choice'
                              else ['choice','story','confirm'] if start=='choice'
                              else ['story','confirm'] if start=='story' else ['confirm'])
            self.assertEqual(state['clicked_pages'],expected_pages)
            if start in ('choice','hidden_choice'):
                self.assertLess(controller.clicks[0][0],1000)
        finally:
            tasker.post_stop().wait()

    def test_skip_and_confirm_then_resume_battle(self):
        self.scenario('story','main')

    def test_existing_confirmation_then_map_handoff(self):
        self.scenario('confirm','map')

    def test_forced_choice_precedes_gray_skip_then_resumes_story_and_battle(self):
        self.scenario('choice','main',gray_skip=True)

    def test_hidden_choice_continues_then_selects_skips_and_resumes_battle(self):
        self.scenario('hidden_choice','main',gray_skip=True)

    def test_hidden_choice_continues_then_selects_and_hands_back_map(self):
        self.scenario('hidden_choice','map',gray_skip=True)

    def test_forced_choice_without_skip_then_story_and_map_handoff(self):
        self.scenario('choice','map')

if __name__ == '__main__':
    unittest.main()
