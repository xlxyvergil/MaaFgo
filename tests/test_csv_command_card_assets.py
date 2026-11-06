"""CSV/Atlas incremental workflow; fixture-only network and isolated outputs."""
import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('csv_command_assets',ROOT/'tools/sync_command_card_assets_from_csv.py')
syncer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(syncer)


def png(flip=False):
    image=Image.new('RGBA',(256,256))
    image.paste((220,40,130,255),(25,25,90,95))
    if flip:image=image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    buffer=io.BytesIO()
    image.save(buffer,'PNG')
    return buffer.getvalue()


class CsvCommandAssetsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project=Path(temp.name)/'工程'
        self.project.mkdir()
        self.csv=self.project/'从者.csv'
        with self.csv.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream)
            writer.writerow(['servantId','collectionNo','name_cn'])
            writer.writerows([['100','1','角色甲'],['200','2','敌方乙']])
        self.basic=[{'id':100,'collectionNo':1,'type':'normal','costume':{'100110':{'id':11}}},{'id':200,'collectionNo':2,'type':'enemyCollectionDetail'}]
        self.nice={'id':100,'collectionNo':1,'extraAssets':{'commands':{'ascension':{'1':syncer.STATIC+'/JP/Servants/Commands/100/card_servant_1.png','2':syncer.STATIC+'/JP/Servants/Commands/100/card_servant_2.png'},'costume':{'11':syncer.STATIC+'/JP/Servants/Commands/100/card_servant_11.png'}}}}
        self.calls=[]
        self.cn_missing=False
        self.fail_portrait=False

    def fetch(self,url):
        self.calls.append(url)
        if url.endswith('/export/JP/basic_servant.json'):return json.dumps(self.basic).encode()
        if url.endswith('/export/CN/basic_servant.json'):return json.dumps(self.basic[:1]).encode()
        if url.endswith('/nice/JP/servant/1'):return json.dumps(self.nice).encode()
        if url.endswith('card_servant_2.png') and self.fail_portrait:raise TimeoutError('test network failure')
        if '/CN/' in url and self.cn_missing:return None
        if url.endswith('.png'):return png('/CN/' in url)
        raise AssertionError(f'Unexpected request: {url}')

    def run_sync(self,**kwargs):
        return syncer.sync(self.project,self.csv,fetcher=self.fetch,**kwargs)

    def test_preview_never_downloads_images_or_changes_csv(self):
        before=self.csv.read_bytes()
        report=self.run_sync()
        self.assertEqual(len(report['planned']),5)
        self.assertEqual(report['downloaded'],[])
        self.assertEqual(report['no_commands'][0]['id'],'200')
        self.assertFalse(any(url.endswith('.png') for url in self.calls))
        self.assertFalse((self.project/'assets').exists())
        self.assertEqual(self.csv.read_bytes(),before)

    def test_download_quantize_overlay_and_idempotence(self):
        report=self.run_sync(write=True)
        self.assertEqual(len(report['downloaded']),5)
        base=self.project/'assets/resource/base/image/CommandCards'
        cn=self.project/'assets/resource/cn/image/CommandCards'
        manifest=json.loads((base/'manifest').read_text('utf8'))
        self.assertEqual(len(manifest['servants']['100']),3)
        self.assertNotIn('200',manifest['servants'])
        for entry in manifest['servants']['100']:
            with Image.open(base/entry['file']) as image:
                self.assertEqual(image.mode,'P')
                self.assertEqual(image.size,(256,256))
            self.assertIn('source_sha256',entry)
        relative='_composition/np/100/card_servant_np.png'
        self.assertTrue((base/relative).exists())
        self.assertTrue((cn/relative).exists())
        self.assertNotEqual((base/relative).read_bytes(),(cn/relative).read_bytes())
        before=(base/'manifest').read_bytes()
        self.calls.clear()
        again=self.run_sync(write=True)
        self.assertEqual(again['deployed_files'],0)
        self.assertFalse(any(url.endswith('.png') for url in self.calls))
        self.assertEqual((base/'manifest').read_bytes(),before)

    def test_network_failure_cannot_partially_deploy(self):
        self.fail_portrait=True
        with self.assertRaisesRegex(RuntimeError,'card_servant_2.png'):self.run_sync(write=True)
        self.assertFalse((self.project/'assets').exists())

    def test_identity_mismatch_is_not_silently_skipped(self):
        self.nice['id']=999
        with self.assertRaisesRegex(ValueError,'identity mismatch'):self.run_sync(write=True)
        self.assertFalse((self.project/'assets').exists())

    def test_csv_collection_conflict_is_fatal(self):
        self.basic[0]['id']=999
        with self.assertRaisesRegex(ValueError,'identity conflict'):self.run_sync()

    def test_cn_404_recorded_without_japanese_substitution(self):
        self.cn_missing=True
        report=self.run_sync(write=True)
        root=self.project/'assets/resource/cn/image/CommandCards/_composition'
        manifest=json.loads((root/'manifest').read_text('utf8'))
        self.assertIn('100',manifest['missing'])
        self.assertFalse((root/'np/100/card_servant_np.png').exists())
        self.assertEqual(len(report['downloaded']),4)

    def test_corrupt_existing_file_is_not_overwritten(self):
        self.run_sync(write=True)
        path=self.project/'assets/resource/base/image/CommandCards/100/card_servant_1.png'
        path.write_bytes(b'user changed this file')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.run_sync(write=True)
        self.assertEqual(path.read_bytes(),b'user changed this file')

    def test_reject_csv_duplicates_and_unknown_selected_ids(self):
        with self.assertRaisesRegex(ValueError,'not in CSV'):self.run_sync(selected_ids=['999'])
        with self.csv.open('a',encoding='utf8') as stream:stream.write('100,3,duplicate\n')
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.run_sync()

    def test_atlas_roles_outside_csv_are_reported_not_downloaded(self):
        self.basic.append({'id':300,'collectionNo':3,'type':'normal'})
        report=self.run_sync()
        self.assertEqual(report['atlas_not_in_csv'],['300'])
        self.assertFalse(any(job['id']=='300' for job in report['planned']))

    def test_declared_transform_adds_own_portraits_without_csv_name_fallback(self):
        self.nice['functions'] = [{'funcType': 'transformServant', 'svals': [{'Value': 300}]}]
        original_fetch = self.fetch
        form = {'id': 300, 'collectionNo': 0, 'extraAssets': {'commands': {'ascension': {'1': syncer.STATIC + '/JP/Servants/Commands/300/card_servant_1.png'}}},
                'functions': [{'funcType': 'transformServant', 'svals': [{'Value': 100}]}]}
        def fetch(url):
            if url.endswith('/nice/JP/servant/300'):
                return json.dumps(form).encode()
            return original_fetch(url)
        report = syncer.sync(self.project, self.csv, fetcher=fetch, write=True)
        self.assertEqual(report['forms']['100'], ['300'])
        manifest = json.loads((self.project / 'assets/resource/base/image/CommandCards/manifest').read_text('utf8'))
        self.assertIn('300', manifest['servants'])
        self.assertTrue((self.project / 'assets/resource/base/image/CommandCards/300/card_servant_1.png').exists())
        self.assertEqual(manifest['servants']['300'][0]['quantization_profile'], 'libimagequant-256-alpha-classes-v1')


if __name__=='__main__':unittest.main()
