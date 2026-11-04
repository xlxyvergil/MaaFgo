"""Only disposable installs and ephemeral ports are used for launcher handover."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))

from battle_editor import launcher
from battle_editor.launcher import EditorLaunchError, open_editor, service_status, stop_editor
from battle_editor.server import create_server
from battle_editor.storage import encode_json, project_identity


class EditorTakeoverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='MaaFGO 接管验收 ')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def install(self, name, *, portable=False):
        root = self.folder / name
        static = root / 'web/battle-editor'
        static.mkdir(parents=True)
        (static / 'index.html').write_text('<!doctype html><title>fixture</title>', encoding='utf-8')
        if portable:
            shutil.copytree(ROOT / 'agent/battle_editor', root / 'agent/battle_editor',
                            ignore=shutil.ignore_patterns('__pycache__', 'data'))
            (root / 'agent/chaldea').mkdir()
            shutil.copy2(ROOT / 'agent/chaldea/servant_aliases.py', root / 'agent/chaldea')
            (root / 'agent/battle/core').mkdir(parents=True)
            shutil.copy2(ROOT / 'agent/battle/core/special_skills.py', root / 'agent/battle/core')
            shutil.copy2(ROOT / 'agent/battle/core/advanced_strategy.py', root / 'agent/battle/core')
        else:
            entry = root / 'agent/battle_editor/main.py'
            entry.parent.mkdir(parents=True)
            entry.write_text('# preflight fixture only\n', encoding='utf-8')
        return root

    def free_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            return listener.getsockname()[1]

    def request(self, port, method, path, data=None, *, project=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if project is not None:
            headers['X-Editor-Project'] = project
        if token is not None:
            headers['X-Editor-Token'] = token
        connection = HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request(method, path, None if data is None else json.dumps(data).encode(), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def server(self, root, *, binding=False):
        server = create_server(root, 0, idle_timeout=60, require_project_binding=binding)
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        worker.start()
        def cleanup():
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()
        self.addCleanup(cleanup)
        return server

    def listener(self, payload):
        posts = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def do_POST(self):
                posts.append(self.path)
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port, posts

    def health(self, root):
        return {'service': 'maafgo-battle-editor', 'protocolVersion': 1,
                'projectRoot': str(root), 'projectId': project_identity(root),
                'pid': os.getpid(), 'startedAt': 'fixture'}

    def test_project_binding_blocks_legacy_and_other_install_pages_before_any_write(self):
        root = self.install('已接管安装')
        server = self.server(root, binding=True)
        port, project = server.server_port, project_identity(root)
        status, health = self.request(port, 'GET', '/health')
        self.assertEqual(status, 200)
        self.assertEqual(health['projectId'], project)
        self.assertTrue(health['requiresProjectBinding'])
        status, missing = self.request(port, 'GET', '/api/session')
        self.assertEqual((status, missing['error']['code']), (403, 'project_required'))
        status, mismatch = self.request(port, 'GET', '/api/session', project='0' * 64)
        self.assertEqual((status, mismatch['error']['code']), (409, 'project_mismatch'))
        status, session = self.request(port, 'GET', '/api/session', project=project)
        self.assertEqual(status, 200)
        self.assertEqual(session['projectId'], project)
        payload = {'revision': 0, 'data': {'team': {'name': '待保存草稿'}, 'actions': []}}
        status, denied = self.request(port, 'PUT', '/api/drafts/old-page', payload,
                                      project='0' * 64, token=session['token'])
        self.assertEqual((status, denied['error']['code']), (409, 'project_mismatch'))
        self.assertFalse((root / 'config/BattleEditor/drafts/old-page.json').exists())
        status, saved = self.request(port, 'PUT', '/api/drafts/new-page', payload,
                                     project=project, token=session['token'])
        self.assertEqual(status, 200)
        self.assertEqual(saved['data'], payload['data'])

    def test_direct_server_keeps_legacy_session_compatibility_unless_binding_requested(self):
        root = self.install('旧协议兼容')
        server = self.server(root)
        status, session = self.request(server.server_port, 'GET', '/api/session')
        self.assertEqual(status, 200)
        self.assertTrue(session['token'])

    def test_unknown_service_is_never_stopped_or_replaced(self):
        root = self.install('新安装')
        port, posts = self.listener({'service': 'some-unrelated-service'})
        with patch.object(launcher.subprocess, 'Popen') as spawn, \
                patch.object(launcher.webbrowser, 'open') as browser:
            with self.assertRaises(EditorLaunchError):
                open_editor(root, port=port)
        self.assertFalse(posts)
        spawn.assert_not_called()
        browser.assert_not_called()

    def test_editor_health_claim_with_unrelated_process_is_not_enough_to_stop_it(self):
        root, other = self.install('请求安装'), self.install('伪装安装')
        port, posts = self.listener(self.health(other))
        with patch.object(launcher.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(EditorLaunchError, '启动参数|核实|身份'):
                open_editor(root, port=port, open_browser=False)
        self.assertFalse(posts)
        spawn.assert_not_called()

    def test_missing_target_installation_does_not_shutdown_the_existing_listener(self):
        other = self.install('仍可用安装')
        port, posts = self.listener(self.health(other))
        with self.assertRaisesRegex(EditorLaunchError, '缺失'):
            open_editor(self.folder / '缺少资源安装', port=port, open_browser=False)
        self.assertFalse(posts)

    def test_retirement_timeout_does_not_force_terminate_or_kill_the_old_process(self):
        root = self.install('仍在保存')
        health, process, command = self.health(root), Mock(), ['fixture']
        process.create_time.return_value = 10.0
        process.cmdline.return_value = command
        process.is_running.return_value = True
        with patch.object(launcher, '_verify_backend_process', return_value=(process, 10.0, command)), \
                patch.object(launcher, '_service_health', return_value=health), \
                patch.object(launcher, '_request', side_effect=[{'token': 'fixture'}, {'ok': True}]) as request, \
                patch.object(launcher.time, 'monotonic', side_effect=[0, 1]):
            with self.assertRaisesRegex(EditorLaunchError, '未强制终止'):
                launcher._retire_service(health, self.free_port(), .1)
        self.assertEqual(len(request.call_args_list), 2)
        self.assertTrue(request.call_args_list[-1].args[0].endswith('/api/shutdown'))
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_identity_change_before_shutdown_aborts_without_sending_the_shutdown(self):
        root = self.install('发生变化')
        health, process, command = self.health(root), Mock(), ['fixture']
        process.create_time.return_value = 10.0
        process.cmdline.return_value = command
        process.is_running.return_value = True
        with patch.object(launcher, '_verify_backend_process', return_value=(process, 10.0, command)), \
                patch.object(launcher, '_service_health', return_value={**health, 'pid': health['pid'] + 1}), \
                patch.object(launcher, '_request', return_value={'token': 'fixture'}) as request:
            with self.assertRaisesRegex(EditorLaunchError, '身份.*变化'):
                launcher._retire_service(health, self.free_port(), .1)
        self.assertEqual(len(request.call_args_list), 1)
        self.assertTrue(request.call_args.args[0].endswith('/api/session'))
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_process_disappearing_during_identity_check_is_reported_without_shutdown(self):
        import psutil
        root = self.install('身份检查时已退出')
        health, process, command = self.health(root), Mock(), ['fixture']
        process.is_running.return_value = True
        process.create_time.side_effect = psutil.NoSuchProcess(health['pid'])
        with patch.object(launcher, '_verify_backend_process', return_value=(process, 10.0, command)), \
                patch.object(launcher, '_service_health', return_value=health), \
                patch.object(launcher, '_request', return_value={'token': 'fixture'}) as request:
            with self.assertRaises(EditorLaunchError):
                launcher._retire_service(health, self.free_port(), .1)
        self.assertEqual(len(request.call_args_list), 1)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_exited_zombie_does_not_wait_for_timeout_or_get_force_killed(self):
        import psutil
        root = self.install('已退出等待回收')
        health, process, command = self.health(root), Mock(), ['fixture']
        process.create_time.return_value = 10.0
        process.cmdline.return_value = command
        process.is_running.return_value = True
        process.status.return_value = psutil.STATUS_ZOMBIE
        with patch.object(launcher, '_verify_backend_process', return_value=(process, 10.0, command)), \
                patch.object(launcher, '_service_health', return_value=health), \
                patch.object(launcher, '_request', side_effect=[{'token': 'fixture'}, {'ok': True}]), \
                patch.object(launcher, '_port_in_use', return_value=False), \
                patch.object(launcher.time, 'monotonic', return_value=0):
            launcher._retire_service(health, self.free_port(), .1)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_real_legacy_install_handover_preserves_both_roots_and_blocks_old_page(self):
        old, new = self.install('旧中文安装', portable=True), self.install('新中文安装', portable=True)
        # Delay one already accepted save, and expose when graceful shutdown starts.
        # This patch lives only in the disposable old installation.
        entry = old / 'agent/battle_editor/main.py'
        entry.write_text(entry.read_text(encoding='utf-8').replace(
            'from __future__ import annotations',
            '''from __future__ import annotations
import sys as _sys, time as _time
from pathlib import Path as _Path
_root = _Path(__file__).resolve().parents[2]
_sys.path.insert(0, str(_root / 'agent'))
from battle_editor.storage import BattleStorage as _Storage
from battle_editor.server import EditorHTTPServer as _Server
_original_atomic, _original_stop = _Storage._atomic, _Server.stop
def _delayed_atomic(self, path, raw, *, create=False):
    if path.name == 'pending-page.json':
        (_root / '.write-accepted').touch()
        deadline = _time.monotonic() + 8
        while not (_root / '.release-write').exists() and _time.monotonic() < deadline:
            _time.sleep(.01)
        if not (_root / '.release-write').exists():
            raise RuntimeError('test did not release accepted write')
    return _original_atomic(self, path, raw, create=create)
def _observed_stop(self):
    (_root / '.shutdown-requested').touch()
    return _original_stop(self)
_Storage._atomic, _Server.stop = _delayed_atomic, _observed_stop
'''), encoding='utf-8')
        for root, label in ((old, 'old'), (new, 'new')):
            directory = root / 'config/Battle'
            directory.mkdir(parents=True)
            (directory / '既有方案.json').write_text(json.dumps({'team': {'name': label}}, ensure_ascii=False), encoding='utf-8')
        before = {root: (root / 'config/Battle/既有方案.json').read_bytes() for root in (old, new)}
        port = self.free_port()
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        child = subprocess.Popen([sys.executable, '-X', 'utf8', str(old / 'agent/battle_editor/main.py'),
                                  '--project-root', str(old), '--port', str(port), '--idle-timeout', '60'],
                                 cwd=old, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags)
        # Reap the disposable server on POSIX too; otherwise a zombie still has a PID.
        reaper = threading.Thread(target=child.wait, daemon=True)
        reaper.start()
        def cleanup():
            try:
                if service_status(new, port):
                    stop_editor(new, port=port)
            except EditorLaunchError:
                pass
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.assertIsNone(child.poll(), 'disposable legacy editor exited early')
            try:
                health = service_status(old, port)
                if health is not None:
                    break
            except EditorLaunchError:
                pass
            time.sleep(.05)
        else:
            self.fail('disposable legacy editor did not become ready')
        self.assertEqual(health['pid'], child.pid)
        _, session = self.request(port, 'GET', '/api/session')
        payload = {'revision': 0, 'data': {'team': {'name': '旧页面已同步草稿'}, 'actions': []}}
        self.assertEqual(self.request(port, 'PUT', '/api/drafts/legacy-page', payload, token=session['token'])[0], 200)
        old_draft = old / 'config/BattleEditor/drafts/legacy-page.json'
        draft_before = old_draft.read_bytes()

        with self.assertRaisesRegex(EditorLaunchError, '另一份'):
            service_status(new, port)
        with self.assertRaisesRegex(EditorLaunchError, '禁用'):
            open_editor(new, port=port, open_browser=False, replace_existing=False)
        self.assertIsNone(child.poll())
        with ThreadPoolExecutor(max_workers=2) as pool:
            saving = pool.submit(self.request, port, 'PUT', '/api/drafts/pending-page', payload,
                                 token=session['token'])
            deadline = time.monotonic() + 2
            while not (old / '.write-accepted').exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((old / '.write-accepted').exists())
            replacing = pool.submit(open_editor, new, port=port, open_browser=False,
                                    idle_timeout=60, timeout=10)
            try:
                deadline = time.monotonic() + 2
                while not (old / '.shutdown-requested').exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((old / '.shutdown-requested').exists())
                self.assertFalse(replacing.done(), 'replacement must wait for accepted saves to finish')
                self.assertIsNone(child.poll())
                (old / '.release-write').touch()
                saved_status, saved_draft = saving.result(timeout=5)
                self.assertEqual(saved_status, 200)
                result = replacing.result(timeout=15)
            finally:
                (old / '.release-write').touch()
        self.assertFalse(result.reused)
        self.assertEqual(result.replaced_pid, child.pid)
        self.assertEqual(result.replaced_project_root, str(old.resolve()))
        self.assertNotEqual(result.pid, child.pid)
        reaper.join(timeout=3)
        self.assertIsNotNone(child.poll())
        self.assertEqual(result.url, f'http://127.0.0.1:{port}/?project={project_identity(new)}')
        self.assertEqual(service_status(new, port)['pid'], result.pid)
        again = open_editor(new, port=port, open_browser=False)
        self.assertTrue(again.reused)
        self.assertEqual(again.pid, result.pid)
        self.assertIsNone(again.replaced_pid)

        # Even a manually retried session from the old page cannot acquire a new token.
        self.assertEqual(self.request(port, 'GET', '/api/session')[0], 403)
        status, mismatch = self.request(port, 'GET', '/api/session', project=project_identity(old))
        self.assertEqual((status, mismatch['error']['code']), (409, 'project_mismatch'))
        self.assertEqual(self.request(port, 'PUT', '/api/drafts/legacy-page', payload,
                                     project=project_identity(old), token=session['token'])[0], 409)
        self.assertFalse((new / 'config/BattleEditor/drafts/legacy-page.json').exists())
        pending = old / 'config/BattleEditor/drafts/pending-page.json'
        self.assertEqual(json.loads(pending.read_text(encoding='utf-8'))['data'], payload['data'])
        self.assertEqual(hashlib.sha256(pending.read_bytes()).digest(),
                         hashlib.sha256(encode_json(saved_draft)).digest())
        self.assertEqual(old_draft.read_bytes(), draft_before)
        for root in (old, new):
            self.assertEqual((root / 'config/Battle/既有方案.json').read_bytes(), before[root])
        self.assertTrue(stop_editor(new, port=port))
        self.assertIsNone(service_status(new, port))

    def test_simultaneous_launchers_share_one_backend_instead_of_racing_the_listener(self):
        root, port = self.install('并发启动安装', portable=True), self.free_port()
        code = '''import json,sys
from dataclasses import asdict
sys.path.insert(0,sys.argv[1]+'/agent')
from battle_editor.launcher import open_editor
print(json.dumps(asdict(open_editor(sys.argv[1],port=int(sys.argv[2]),open_browser=False,idle_timeout=60,timeout=10))))
'''
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        children = []
        def cleanup():
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    child.communicate(timeout=5)
            if service_status(root, port):
                stop_editor(root, port=port)
        self.addCleanup(cleanup)
        for _ in range(2):
            children.append(subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code, str(root), str(port)],
                            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, encoding='utf-8', creationflags=flags))
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(results[0]['pid'], results[1]['pid'])
        self.assertEqual(sorted(result['reused'] for result in results), [False, True])
        self.assertEqual(service_status(root, port)['pid'], results[0]['pid'])
        self.assertTrue(stop_editor(root, port=port))


if __name__ == '__main__':
    unittest.main()
