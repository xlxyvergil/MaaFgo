"""Image OCR may fetch only the identified public team, before draft confirmation."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from agent.battle_editor.server import create_server
from agent.battle_editor.storage import EditorError


class ChaldeaImageHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Chaldea image HTTP ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        web = self.root / 'assets/web/battle-editor'
        web.mkdir(parents=True)
        (web / 'index.html').write_text('editor')
        self.server = create_server(self.root, 0)
        self.server._screenshot_worker.recognize = Mock(return_value={
            'kind': 'chaldea', 'teamId': 107574, 'source': 'https://untrusted.invalid/',
            'warnings': [], 'elapsedMs': 150})
        self.data = {'team': {'name': '保留完整数据', 'onFieldSvts': [None] * 3,
                             'backupSvts': [None] * 3},
                     'actions': [{'type': 'attack', 'attacks': [{'isAuto': True}]}],
                     'unknown': {'不应改写': [None, True, '中文']}}
        self.validation = {'valid': False, 'issues': [{'severity': 'error', 'message': '测试提示'}]}
        self.server.import_source = Mock(return_value={
            'data': self.data, 'teamId': 107574, 'validation': self.validation})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.stop()
        self.thread.join(5)
        self.server.server_close()

    def request(self, *, token=True, origin=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'image/png'}
        if token:
            headers['X-Editor-Token'] = self.server.token
        if origin:
            headers['Origin'] = origin
        conn.request('POST', '/api/import-image', b'image', headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_fetches_fixed_official_url_and_preserves_full_data_without_saving(self):
        before = copy.deepcopy(self.data)
        status, result = self.request()
        self.assertEqual(status, 200)
        self.server.import_source.assert_called_once_with(
            'https://link.chaldea.center/laplace/share?id=107574')
        self.assertEqual(result['source'], 'https://link.chaldea.center/laplace/share?id=107574')
        self.assertEqual((result['kind'], result['schema'], result['teamId']), ('chaldea', 1, 107574))
        self.assertEqual(result['data'], before)
        self.assertEqual(result['validation'], self.validation)
        self.assertEqual(self.data, before)
        self.assertEqual(list(self.server.storage.battle_dir.glob('*.json')), [])
        self.assertEqual(list(self.server.storage.draft_dir.glob('*.json')), [])

    def test_large_team_id_still_uses_team_query_instead_of_quest_number(self):
        self.server._screenshot_worker.recognize.return_value['teamId'] = 1234567
        self.server.import_source.return_value['teamId'] = 1234567
        self.assertEqual(self.request()[0], 200)
        self.server.import_source.assert_called_once_with(
            'https://link.chaldea.center/laplace/share?id=1234567')

    def test_bad_ids_rejected_before_network(self):
        for team_id in (None, 0, -1, True, '107574', 107574.0, 2**53):
            with self.subTest(team_id=team_id):
                self.server._screenshot_worker.recognize.return_value['teamId'] = team_id
                status, result = self.request()
                self.assertEqual(status, 422)
                self.assertEqual(result['error']['code'], 'chaldea_team_id')
        self.server.import_source.assert_not_called()

    def test_recognized_but_unavailable_team_has_precise_failure(self):
        for code, status in (('import_failed', 422), ('import_timeout', 504)):
            with self.subTest(code=code):
                self.server.import_source.side_effect = EditorError(code, 'upstream failure', status)
                response, body = self.request()
                self.assertEqual(response, status)
                self.assertEqual(body['error']['code'], 'chaldea_fetch_failed')
                self.assertIn('107574', body['error']['message'])
                self.assertIn('网络', body['error']['message'])
        self.assertEqual(list(self.server.storage.battle_dir.glob('*.json')), [])

    def test_mismatched_team_rejected_and_security_checked_before_ocr(self):
        self.server.import_source.return_value['teamId'] = 9
        status, result = self.request()
        self.assertEqual((status, result['error']['code']), (422, 'chaldea_team_mismatch'))
        self.server.import_source.reset_mock()
        self.server._screenshot_worker.recognize.reset_mock()
        for options in ({'token': False}, {'origin': 'https://other.invalid'}):
            self.assertEqual(self.request(**options)[0], 403)
        self.server.import_source.assert_not_called()
        self.server._screenshot_worker.recognize.assert_not_called()


if __name__ == '__main__':
    unittest.main()
