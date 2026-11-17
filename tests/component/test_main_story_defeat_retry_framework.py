"""国服消费确认框的暗色背景撤退字不能被识别成退出确认。"""
import copy
import json
from pathlib import Path
import unittest

import cv2
import numpy as np
from maa.custom_action import CustomAction
from maa.tasker import Tasker
from test_main_story_framework import FrameworkResources, ScreenController

ROOT=Path(__file__).resolve().parents[2]

class DefeatRetryFrameworkTests(FrameworkResources,unittest.TestCase):
    def test_consumption_confirmation_is_rejected_despite_background_retreat(self):
        resource=self.resource('cn')
        path=ROOT/'tests/fixtures/main_story/cn_spirit_confirmation_retreat_negative.png'
        image=cv2.imdecode(np.fromfile(str(path),np.uint8),cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)
        positive_path=path.with_name('cn_retreat_confirmation_synthetic.png')
        positive=cv2.imdecode(np.fromfile(str(positive_path),np.uint8),cv2.IMREAD_COLOR)
        self.assertIsNotNone(positive)
        controller=ScreenController(image)
        self.assertTrue(controller.post_connection().wait().succeeded)
        results={}
        class Probe(CustomAction):
            def run(self,ctx,argv):
                current=ctx.run_recognition('主线-撤退确认',image)
                results['current']=bool(current and current.hit)
                source=json.loads((ROOT/'assets/resource/cn/pipeline/主线灵脉石续关.json').read_text(encoding='utf-8'))
                coarse=copy.deepcopy(source['主线-撤退确认'])
                part=coarse['recognition']['param']['all_of'][0]
                part['sub_name']='test/main_story_defeat_retry/coarse/OCR1'
                param=part['recognition']['param']
                param.update(roi=[250,180,800,230],expected=['撤退|中断任务|中止任务|放弃任务'])
                param.pop('color_filter',None)
                old=ctx.run_recognition('test_coarse_confirmation',image,pipeline_override={'test_coarse_confirmation':coarse})
                results['coarse']=bool(old and old.hit)
                allowed=ctx.run_recognition('主线-撤退确认',positive)
                results['synthetic_retreat_question']=bool(allowed and allowed.hit)
                return True
        resource.register_custom_action('probe_defeat_confirmation',Probe())
        tasker=Tasker()
        self.assertTrue(tasker.bind(resource,controller))
        try:
            self.assertTrue(tasker.post_task('probe_defeat_confirmation',{'probe_defeat_confirmation':{'action':{'type':'Custom','param':{'custom_action':'probe_defeat_confirmation'}}}}).wait().succeeded)
            self.assertEqual(results,{'current':False,'coarse':True,'synthetic_retreat_question':True})
            self.assertEqual(controller.clicks,[])
            self.assertEqual(controller.swipes,[])
        finally:
            tasker.post_stop().wait()

if __name__=='__main__':unittest.main()
