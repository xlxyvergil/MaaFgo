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


class PostAttackStoryTests(framework.FrameworkResources, unittest.TestCase):
    def scenario(self, start, destination):
        resource = self.resource('jp')
        state = {'page': start}
        class Controller(framework.ScreenController):
            def click(self, x, y):
                self.clicks.append((x,y))
                state['page'] = 'confirm' if x > 1000 else destination
                return True
        controller = Controller(np.zeros((720,1280,3),np.uint8))
        self.assertTrue(controller.post_connection().wait().succeeded)
        class Reco(CustomRecognition):
            def analyze(self, ctx, argv):
                page = state['page']
                if argv.node_name == '跳过剧情-点击跳过' and page in ('story','confirm'):
                    return (1143,24,79,34)
                if argv.node_name.startswith('跳过剧情-确认跳过') and page == 'confirm':
                    return (780,538,90,44)
                if argv.node_name == '战斗_主界面' and page == 'main':
                    return (1058,508,152,54)
                if argv.node_name in ('可能已经结束战斗_不回主界面','战斗结束-主界面') and page == 'map':
                    return (50,600,20,20)
                return None
        from battle.perception.config import SCENE_NODES
        names = set(SCENE_NODES.values()) | {'跳过剧情-点击跳过','跳过剧情-确认跳过',
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
                before=ctx.get_node_data('跳过剧情-点击跳过')
                try:
                    with patch('battle.runtime.runtime._ANIMATION_TIMEOUT_S',6):
                        report['ok']=r._wait_turn_settled()
                    report['map']=r._post_attack_returned_to_map
                    report['isolated']=before==ctx.get_node_data('跳过剧情-点击跳过')
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
            self.assertEqual(len(controller.clicks),2 if start=='story' else 1)
        finally:
            tasker.post_stop().wait()

    def test_skip_and_confirm_then_resume_battle(self):
        self.scenario('story','main')

    def test_existing_confirmation_then_map_handoff(self):
        self.scenario('confirm','map')

if __name__ == '__main__':
    unittest.main()
