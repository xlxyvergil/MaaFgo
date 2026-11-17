"""Atlas NP discovery/transactions against isolated fixtures, no network."""
import copy
import csv
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import sync_command_card_np_assets as tool


def asset(region, sid, filename):
    return f'https://static.atlasacademy.io/{region}/Servants/Commands/{sid}/{filename}'


def png():
    image = Image.new('RGBA', (256, 256))
    image.paste((180, 80, 40, 255), (20, 20, 100, 90))
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


class NpAssetsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.csv = self.project / 'roster.csv'
        with self.csv.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['servantId', 'collectionNo'])
            writer.writerow(['100', '1'])
        self.exports = {}
        self.forms = {}
        for region in ('JP', 'CN'):
            self.exports[region] = [{
                'id': 100, 'collectionNo': 1, 'type': 'normal',
                'noblePhantasms': [{'icon': asset(region, 100, 'card_servant_np.png')},
                                  {'icon': asset(region, 100, 'card_servant_np_100102.png')}],
                'ascensionAdd': {'overWriteTDFileName': {'ascension': {'3': asset(region, 100, '100110.png'), '4': asset(region, 100, '100110.png')}}},
                'skills': [{'functions': [{'funcType': 'transformServant', 'svals': [{'Value': 300}], 'svals2': [{'Value': 300}]}]}],
            }]
            self.forms[region] = {'id': 300, 'collectionNo': 0, 'noblePhantasms': [{'icon': asset(region, 300, 'card_servant_np.png')}],
                                  'functions': [{'funcType': 'transformServant', 'svals': [{'Value': 100}]}]}
        self.calls = []
        self.missing = set()
        self.failure = None

    def fetch(self, url):
        self.calls.append(url)
        if self.failure and self.failure in url:
            raise TimeoutError('fixture network failure')
        if url in self.missing:
            return None
        for region in ('JP', 'CN'):
            if url.endswith(f'/export/{region}/basic_servant.json'):
                return json.dumps(self.exports[region]).encode()
            if url.endswith(f'/nice/{region}/servant/100'):
                return json.dumps(self.exports[region][0]).encode()
            if url.endswith(f'/nice/{region}/servant/300'):
                return json.dumps(self.forms[region]).encode()
        if url.endswith('.png'):
            return png()
        raise AssertionError(url)

    def sync(self, **kwargs):
        return tool.sync(self.project, self.csv, fetcher=self.fetch, **kwargs)

    def test_preview_discovers_icons_overrides_and_cycle_safe_transform(self):
        report = self.sync()
        self.assertEqual(len(report['planned']), 8)
        self.assertEqual(report['forms']['JP/100'], ['300'])
        self.assertFalse(any(url.endswith('.png') for url in self.calls))
        self.assertFalse((self.project / 'assets').exists())

    def test_selected_ids_limit_np_checks_but_keep_linked_forms(self):
        with self.csv.open('a', encoding='utf8', newline='') as stream:
            csv.writer(stream).writerow(['200', '2'])
        for region in self.exports:
            self.exports[region].append({'id': 200, 'collectionNo': 2, 'type': 'normal'})
        report = self.sync(selected_ids=['100'])
        self.assertEqual(report['csv_rows'], 1)
        self.assertEqual(len(report['planned']), 8)
        self.assertFalse(any(url.endswith('/servant/200') for url in self.calls))
        self.assertTrue(any(url.endswith('/servant/300') for url in self.calls))

    def test_selected_id_not_in_csv_rejected_before_network(self):
        with self.assertRaisesRegex(ValueError, 'Selected IDs not in CSV'):
            self.sync(selected_ids=['999'])
        self.assertEqual(self.calls, [])

    def test_palette_deployment_keeps_filenames_and_is_idempotent(self):
        report = self.sync(write=True)
        self.assertEqual(len(report['downloaded']), 8)
        for pack in ('base', 'cn'):
            root = self.project / f'assets/resource/{pack}/image/CommandCards/_composition'
            manifest = json.loads((root / 'manifest').read_text('utf8'))
            self.assertIn('300', manifest['np_names'])
            self.assertEqual(set(manifest['np_extra_names']['100']), {'100110.png', 'card_servant_np_100102.png'})
            for path in root.rglob('*.png'):
                with Image.open(path) as image:
                    self.assertEqual(image.mode, 'P')
                    self.assertEqual(image.size, (256, 256))
        self.calls.clear()
        again = self.sync(write=True)
        self.assertEqual(again['planned'], [])
        self.assertFalse(any(url.endswith('.png') for url in self.calls))

    def test_404_is_per_file_not_replaced_with_default_or_other_region(self):
        self.missing.add(asset('CN', 100, '100110.png'))
        report = self.sync(write=True)
        self.assertEqual(len(report['missing']), 1)
        root = self.project / 'assets/resource/cn/image/CommandCards/_composition'
        manifest = json.loads((root / 'manifest').read_text('utf8'))
        self.assertIn('np/100/100110.png', manifest['np_missing'])
        self.assertFalse((root / 'np/100/100110.png').exists())
        self.assertTrue((root / 'np/100/card_servant_np.png').exists())

    def test_network_failure_does_not_partially_deploy(self):
        self.failure = '/CN/Servants/'
        with self.assertRaisesRegex(RuntimeError, 'fixture network failure'):
            self.sync(write=True)
        self.assertFalse((self.project / 'assets').exists())

    def test_hash_mismatch_prevents_overwrite(self):
        self.sync(write=True)
        path = self.project / 'assets/resource/base/image/CommandCards/_composition/np/100/100110.png'
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.sync(write=True)
        self.assertEqual(path.read_bytes(), b'tampered')

    def test_identity_conflict_fails_before_deployment(self):
        self.exports['JP'][0]['collectionNo'] = 9
        with self.assertRaisesRegex(ValueError, 'collectionNo mismatch'):
            self.sync(write=True)
        self.assertFalse((self.project / 'assets').exists())

    def test_metadata_cache_retry_does_not_refetch_exports(self):
        preview = self.sync()
        self.calls.clear()
        result = self.sync(write=True, metadata_cache=Path(preview['backup']) / 'metadata')
        self.assertEqual(len(result['downloaded']), 8)
        self.assertTrue(all(url.endswith('.png') for url in self.calls))

    def test_untrusted_or_wrong_region_or_portrait_urls_rejected(self):
        for url in ('https://evil.example/JP/Servants/Commands/100/extra.png',
                    asset('CN', 100, 'extra.png'), asset('JP', 100, 'card_servant_1.png'),
                    asset('JP', 100, '../extra.png'), asset('JP', 100, 'extra.png') + '?q=1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                tool.name_assets({'noblePhantasms': [{'icon': url}]}, 'JP')

    def test_existing_default_and_unrelated_manifest_data_are_preserved(self):
        self.sync(write=True)
        root = self.project / 'assets/resource/base/image/CommandCards/_composition'
        path = root / 'manifest'
        manifest = json.loads(path.read_text('utf8'))
        before = copy.deepcopy(manifest['np_names']['100'])
        manifest['unrelated'] = {'keep': True}
        path.write_text(json.dumps(manifest), encoding='utf8')
        self.exports['JP'][0]['noblePhantasms'].append({'icon': asset('JP', 100, 'card_servant_np4.png')})
        self.sync(write=True)
        after = json.loads(path.read_text('utf8'))
        self.assertEqual(after['unrelated'], {'keep': True})
        self.assertEqual(after['np_names']['100'], before)

    def test_raw_cache_is_revalidated_without_downloading_images(self):
        first = self.sync(write=True)
        other = self.project / 'second-project'
        other.mkdir()
        self.calls.clear()
        second = tool.sync(other, self.csv, write=True, fetcher=self.fetch,
                           metadata_cache=Path(first['backup']) / 'metadata',
                           raw_cache=Path(first['backup']) / 'originals')
        self.assertEqual(len(second['downloaded']), 8)
        self.assertEqual(self.calls, [])

    def test_concurrently_changed_manifest_is_not_overwritten(self):
        self.sync(write=True)
        path = self.project / 'assets/resource/base/image/CommandCards/_composition/manifest'
        self.exports['JP'][0]['noblePhantasms'].append({'icon': asset('JP', 100, 'extra.png')})
        original_fetch = self.fetch

        def changed_fetch(url):
            if url.endswith('/extra.png'):
                manifest = json.loads(path.read_text('utf8'))
                manifest['concurrent'] = True
                path.write_text(json.dumps(manifest), encoding='utf8')
            return original_fetch(url)

        with self.assertRaisesRegex(RuntimeError, 'manifest changed'):
            tool.sync(self.project, self.csv, write=True, fetcher=changed_fetch)
        self.assertTrue(json.loads(path.read_text('utf8'))['concurrent'])
        self.assertFalse((path.parent / 'np/100/extra.png').exists())


class CurlTransportTests(unittest.TestCase):
    def test_curl_proxy_dns_override_retains_original_https_hostname(self):
        url = asset('JP', 100, 'extra.png')
        completed = tool.subprocess.CompletedProcess([], 0, b'png-data\n200', b'')
        with patch.object(tool.shutil, 'which', return_value='curl.exe'), patch.object(tool.subprocess, 'run', return_value=completed) as run:
            fetch = tool.downloader('http://127.0.0.1:7890', 'curl', '104.21.34.124')
            self.assertEqual(fetch(url), b'png-data')
            args = run.call_args.args[0]
            self.assertIn(url, args)
            self.assertIn('--proxy', args)
            self.assertIn('static.atlasacademy.io:443:104.21.34.124:443', args)
            self.assertNotIn('--insecure', args)
            self.assertNotIn('--location', args)
            with self.assertRaisesRegex(ValueError, 'Untrusted Atlas URL'):
                fetch('http://evil.example/extra.png')

    def test_curl_404_is_missing_not_a_transport_error(self):
        completed = tool.subprocess.CompletedProcess([], 0, b'not found\n404', b'')
        with patch.object(tool.shutil, 'which', return_value='curl.exe'), patch.object(tool.subprocess, 'run', return_value=completed):
            self.assertIsNone(tool.downloader(transport='curl')(asset('CN', 100, 'extra.png')))

    def test_curl_network_retries_are_bounded(self):
        completed = tool.subprocess.CompletedProcess([], 28, b'\n000', b'timeout')
        with patch.object(tool.shutil, 'which', return_value='curl.exe'), patch.object(tool.subprocess, 'run', return_value=completed) as run, patch.object(tool.time, 'sleep'):
            with self.assertRaisesRegex(OSError, 'timeout'):
                tool.downloader(transport='curl')(asset('JP', 100, 'extra.png'))
            self.assertEqual(run.call_count, 3)

    @unittest.skipUnless(importlib.util.find_spec('requests'), 'optional maintenance requests package not installed')
    def test_requests_transport_reuses_session_and_explicit_proxy(self):
        with patch('requests.Session') as session_type:
            session = session_type.return_value
            session.get.return_value.status_code = 200
            session.get.return_value.content = b'png-data'
            fetch = tool.downloader('http://127.0.0.1:7890', transport='requests')
            self.assertEqual(fetch(asset('JP', 100, 'extra.png')), b'png-data')
            self.assertEqual(fetch(asset('CN', 100, 'extra.png')), b'png-data')
            session_type.assert_called_once()
            self.assertEqual(session.get.call_count, 2)
            self.assertFalse(session.trust_env)
            self.assertEqual(session.proxies['https'], 'http://127.0.0.1:7890')
            self.assertNotIn('verify', session.get.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
