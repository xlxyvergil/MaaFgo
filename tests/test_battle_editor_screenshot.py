"""Screenshot input bounds, geometric normalization, real matching and HTTP boundary."""
import http.client
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image, ImageOps

from agent.battle_editor.screenshot_import import (decode_image, locate_panel, ScreenshotIndex,
    ScreenshotWorker, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS)
from agent.battle_editor.server import create_server, EditorHandler
from agent.battle_editor.storage import EditorError


def encode(image, fmt='PNG'):
    stream=io.BytesIO();image.save(stream,fmt);return stream.getvalue()


class ScreenshotInputTests(unittest.TestCase):
    def test_supported_actual_formats_and_nonimage_are_checked(self):
        for fmt in ('PNG','JPEG','WEBP'):
            decoded=decode_image(encode(Image.new('RGB',(640,360),'blue'),fmt))
            self.assertEqual(decoded.shape,(360,640,3));self.assertGreater(decoded[0,0,0],200)
        for raw in (b'',b'not an image',encode(Image.new('RGB',(640,360)),'GIF')):
            with self.assertRaises(EditorError):decode_image(raw)

    def test_small_oversized_animated_and_transparent_input(self):
        with self.assertRaises(EditorError):decode_image(encode(Image.new('RGB',(100,100))))
        with self.assertRaises(EditorError):decode_image(b'x'*(MAX_IMAGE_BYTES+1))
        decoded=decode_image(encode(Image.new('RGBA',(640,360),(0,0,0,0))))
        self.assertTrue(np.all(decoded==255))
        stream=io.BytesIO();Image.new('RGB',(640,360),'blue').save(stream,'PNG',save_all=True,append_images=[Image.new('RGB',(640,360),'red')],duration=50)
        with self.assertRaises(EditorError):decode_image(stream.getvalue())
        fake=Mock();fake.__enter__=Mock(return_value=fake);fake.__exit__=Mock(return_value=False)
        fake.format='PNG';fake.n_frames=1;fake.size=(12000,12000)
        with patch('PIL.Image.open',return_value=fake),self.assertRaises(EditorError):decode_image(b'fake')

    def test_unrelated_image_is_not_treated_as_party(self):
        for color in ('white','black','blue'):
            with self.assertRaises(EditorError):locate_panel(decode_image(encode(Image.new('RGB',(1280,720),color))))


class ScreenshotRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='截图识别测试 ');cls.root=Path(cls.temp.name)
        custom=cls.root/'agent/custom';custom.mkdir(parents=True)
        images=cls.root/'assets/resource/base/image'
        for name in ('NarrowFigures','EquipFaces/team','battle'):(images/name).mkdir(parents=True)
        rng=np.random.default_rng(20261026)
        cls.templates=[rng.integers(20,155,(184,185,3),dtype=np.uint8) for _ in range(3)]
        cls.ce=rng.integers(20,155,(86,152,3),dtype=np.uint8)
        def write(path,array):cv2.imencode('.png',array)[1].tofile(str(path))
        for i,template in enumerate(cls.templates):write(images/'NarrowFigures'/f'f_100{i+1}001.png',template)
        write(images/'EquipFaces/team/f_94000100.png',cls.ce)
        write(images/'EquipFaces/team/f_94000200.png',rng.integers(0,255,(86,152,3),dtype=np.uint8))
        write(images/'battle/助战标记.png',rng.integers(0,255,(25,40,3),dtype=np.uint8))
        (custom/'servant_list.json').write_text(json.dumps({'servants':[{'id':str(100100+i*100),'name':f'从者{i}'} for i in range(3)]}),encoding='utf-8')
        (custom/'equip_list.json').write_text(json.dumps({'equips':[{'id':'9400010','name':'测试礼装'},{'id':'9400020','name':'其他礼装'}]}),encoding='utf-8')
        cls.index=ScreenshotIndex(cls.root)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def screenshot(self):
        image=np.full((720,1280,3),(190,100,15),dtype=np.uint8)
        cv2.rectangle(image,(22,90),(1267,589),(235,235,235),-1)
        for x in (41,241,440,654,854,1055):cv2.rectangle(image,(x,120),(x+187,585),(55,55,55),-1)
        for i,x in enumerate((41,241,440)):
            image[175:359,x+1:x+186]=self.templates[i]
        image[411:497,258:410]=self.ce
        # Deliberately unknown 4th portrait, not an empty gray slot.
        image[175:359,655:840]=np.random.default_rng(9).integers(0,255,(184,185,3),dtype=np.uint8)
        return Image.fromarray(image[:,:,::-1])

    def test_normal_scaled_and_bordered_screens_keep_slots_unknown_and_ce(self):
        source=self.screenshot()
        for image in (source,source.resize((1920,1080)),ImageOps.expand(source,border=(100,60,200,80),fill='black')):
            with self.subTest(size=image.size):
                result=self.index.recognize(encode(image))
                self.assertEqual([s['servantId'] for s in result['slots'][:3]],[100100,100200,100300])
                self.assertEqual(result['slots'][1]['equipId'],9400010)
                self.assertEqual(result['slots'][3]['status'],'unknown')
                self.assertIsNone(result['slots'][3]['servantId'])
                self.assertEqual(result['slots'][5]['status'],'empty')

    def test_worker_is_lazy_and_rejects_concurrent_request(self):
        worker=ScreenshotWorker(self.root)
        self.assertIsNone(worker.process)
        worker.lock.acquire()
        try:
            with self.assertRaises(EditorError) as error:worker.recognize(b'xx')
            self.assertEqual(error.exception.status,409)
        finally:worker.lock.release();worker.close()

    def test_no_equip_anchor_keeps_unlocated_band_unknown(self):
        image=self.screenshot()
        image.paste((55,55,55),(258,411,410,497))
        result=self.index.recognize(encode(image))
        self.assertEqual(result['slots'][0]['equipStatus'],'unknown')
        self.assertNotIn('equipId',result['slots'][0])

    def test_real_worker_reuses_templates_and_exits_without_device_or_files(self):
        worker=ScreenshotWorker(self.root)
        try:
            first=worker.recognize(encode(self.screenshot()))
            process=worker.process
            self.assertEqual(first['slots'][0]['servantId'],100100)
            second=worker.recognize(encode(self.screenshot()))
            self.assertIs(worker.process,process)
            self.assertEqual(second['slots'][1]['equipId'],9400010)
        finally:worker.close()
        self.assertIsNotNone(process.poll())
        self.assertFalse((self.root/'config').exists())

    def test_worker_timeout_terminates_owned_process_and_allows_retry(self):
        worker=ScreenshotWorker(self.root)
        try:
            with self.assertRaises(EditorError) as failure:
                worker.recognize(encode(self.screenshot()),timeout=0)
            self.assertEqual(failure.exception.code,'image_timeout')
            self.assertIsNone(worker.process)
            result=worker.recognize(encode(self.screenshot()))
            self.assertEqual(result['slots'][0]['servantId'],100100)
        finally:worker.close()

    def test_worker_startup_exit_preserves_friendly_error_cleans_pipes_and_retries(self):
        # A failed worker import can exit before reading stdin. Closing its
        # buffered stdin may then raise again; both pipes must still be released.
        failed=subprocess.Popen([sys.executable,'-c','pass'],
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        failed.wait(timeout=5)
        worker=ScreenshotWorker(self.root)
        try:
            with patch('agent.battle_editor.screenshot_import.subprocess.Popen',return_value=failed):
                with self.assertRaises(EditorError) as failure:
                    worker.recognize(b'abc')
            self.assertEqual((failure.exception.code,failure.exception.status),('image_worker',503))
            self.assertIsNone(worker.process)
            self.assertIsNone(worker.responses)
            self.assertTrue(failed.stdin.closed)
            self.assertTrue(failed.stdout.closed)
            result=worker.recognize(encode(self.screenshot()))
            self.assertEqual(result['slots'][0]['servantId'],100100)
        finally:
            worker.close()
            for stream in (failed.stdin,failed.stdout):
                try:stream.close()
                except OSError:pass


class ScreenshotMysticCodeTests(unittest.TestCase):
    """Real saved clothing crops on an explicit synthetic full-game canvas."""
    @classmethod
    def setUpClass(cls):
        from agent.custom.mystic_code import match_icon, THRESHOLD
        cls.repository = Path(__file__).resolve().parents[1]
        cls.fixtures = cls.repository / 'tests/fixtures/mystic_code'
        cls.index = ScreenshotIndex.__new__(ScreenshotIndex)
        cls.index.match_mystic_icon = match_icon
        cls.index.mystic_threshold = THRESHOLD
        cls.index.mystic_icons = {}
        data = json.loads((cls.repository / 'agent/custom/mystic_code_data.json').read_text(encoding='utf-8'))
        for row in data['mysticCodes']:
            icons, seen = [], set()
            for key in ('maleImageId', 'femaleImageId'):
                for pack in ('base', 'cn'):
                    path = cls.repository / f"assets/resource/{pack}/image/MysticCodes/masterequip{row[key]:05d}.png"
                    if not path.is_file():
                        continue
                    raw = path.read_bytes()
                    if raw in seen:
                        continue
                    seen.add(raw)
                    icons.append((str(path), cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)))
            cls.index.mystic_icons[row['id']] = icons

    def canvas(self, name):
        # The repository intentionally stores only each real icon's crop, not
        # a full account screenshot. Preserve original crop pixels and geometry.
        image = Image.new('RGB', (1280, 720), (15, 100, 190))
        image.paste((235, 235, 235), (12, 108, 1268, 616))
        with Image.open(self.fixtures / name) as icon:
            image.paste(icon.convert('RGB'), (10, 640))
        return image

    def recognize(self, image):
        original = decode_image(encode(image))
        _, layout = locate_panel(original)
        return self.index.recognize_mystic_code(original, layout)

    def test_real_jp_cn_both_genders_compete_by_outfit_identity(self):
        for name, expected in (
            ('team_1.png', 1), ('male_team_1.png', 1),
            ('female_team_20.png', 20), ('male_team_20.png', 20),
            ('female_team_210.png', 210), ('male_team_210.png', 210),
            ('cn_female_team_20.png', 20),
            ('synthetic_male_team_240.png', 240),
        ):
            with self.subTest(name=name):
                result = self.recognize(self.canvas(name))
                self.assertEqual(result['id'], expected, result)
                self.assertEqual(result['status'], 'identified')
                self.assertGreaterEqual(result['score'], .90)
                self.assertNotIn('level', result)

    def test_scaling_and_black_borders_use_panel_not_total_image_dimensions(self):
        for name, expected in (('team_1.png', 1), ('female_team_20.png', 20)):
            source = self.canvas(name)
            for image in (source.resize((960, 540)), source.resize((1920, 1080)),
                          ImageOps.expand(source, border=(170, 80, 250, 110), fill='black')):
                with self.subTest(name=name, size=image.size):
                    self.assertEqual(self.recognize(image)['id'], expected)

    def test_cropped_or_obscured_outfit_is_unknown_without_lowering_threshold(self):
        source = self.canvas('female_team_20.png')
        obscured = source.copy()
        obscured.paste('black', (0, 625, 120, 720))
        for image in (source.crop((0, 0, 1280, 620)), obscured):
            with self.subTest(size=image.size):
                result = self.recognize(image)
                self.assertIsNone(result['id'])
                self.assertEqual(result['status'], 'unknown')

    def test_two_different_ids_with_equal_visual_evidence_are_not_guessed(self):
        icons = self.index.mystic_icons[1]
        with patch.object(self.index, 'mystic_icons', {1: icons, 999: icons}):
            result = self.recognize(self.canvas('team_1.png'))
        self.assertGreaterEqual(result['score'], .90)
        self.assertIsNone(result['id'])
        self.assertEqual(result['status'], 'unknown')


class ScreenshotHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);web=root/'assets/web/battle-editor';web.mkdir(parents=True);(web/'index.html').write_text('test')
        self.server=create_server(root,0);self.server._screenshot_worker.recognize=Mock(return_value={'slots':[]})
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):self.server.stop();self.thread.join(5);self.server.server_close()

    def request(self,raw=b'png',mime='image/png',token=True,origin=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=10)
        headers={'Content-Type':mime}
        if token:headers['X-Editor-Token']=self.server.token
        if origin:headers['Origin']=origin
        connection.request('POST','/api/import-image',body=raw,headers=headers)
        response=connection.getresponse();result=(response.status,json.loads(response.read()));connection.close();return result

    def test_authenticated_raw_image_is_forwarded_without_writing_battle(self):
        status,body=self.request();self.assertEqual(status,200)
        self.server._screenshot_worker.recognize.assert_called_once_with(b'png')
        self.assertEqual(list(self.server.storage.battle_dir.glob('*.json')),[])

    def test_auth_origin_content_type_and_empty_input_fail_before_recognition(self):
        for args,status in [({'token':False},403),({'origin':'https://other.invalid'},403),({'mime':'image/gif'},415),({'raw':b''},413)]:
            with self.subTest(args=args):self.assertEqual(self.request(**args)[0],status)
        self.server._screenshot_worker.recognize.assert_not_called()

    def test_oversized_declared_image_rejected_without_reading_payload(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=10)
        connection.putrequest('POST','/api/import-image')
        for name,value in [('X-Editor-Token',self.server.token),('Content-Type','image/png'),('Content-Length',str(MAX_IMAGE_BYTES+1))]:connection.putheader(name,value)
        connection.endheaders();response=connection.getresponse()
        self.assertEqual(response.status,413);response.read();connection.close()
        self.server._screenshot_worker.recognize.assert_not_called()

    def test_cancelled_browser_connection_is_not_answered_again(self):
        handler=object.__new__(EditorHandler)
        handler._dispatch=Mock(side_effect=ConnectionAbortedError('browser cancelled'))
        handler._json=Mock()
        handler._handle()
        handler._json.assert_not_called()


if __name__=='__main__':unittest.main()
