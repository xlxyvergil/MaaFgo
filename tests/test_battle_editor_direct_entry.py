"""Run the launcher's actual file/CMD entry without relying on package startup."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from battle_editor.launcher import service_status, stop_editor


class DirectLauncherEntryTests(unittest.TestCase):
    def test_isolated_direct_file_launch_reuse_status_and_stop_from_other_cwd(self):
        with tempfile.TemporaryDirectory(prefix='MaaFGO CMD 中文 ') as temporary:
            root = Path(temporary) / '便携安装'
            shutil.copytree(ROOT / 'agent/battle_editor', root / 'agent/battle_editor',
                            ignore=shutil.ignore_patterns('__pycache__', 'data'))
            (root / 'agent/chaldea').mkdir()
            shutil.copy2(ROOT / 'agent/chaldea/servant_aliases.py', root / 'agent/chaldea')
            (root / 'agent/battle/core').mkdir(parents=True)
            shutil.copy2(ROOT / 'agent/battle/core/special_skills.py', root / 'agent/battle/core')
            shutil.copy2(ROOT / 'agent/battle/core/advanced_strategy.py', root / 'agent/battle/core')
            static = root / 'assets/web/battle-editor'
            static.mkdir(parents=True)
            (static / 'index.html').write_text('<!doctype html><title>fixture</title>', encoding='utf-8')
            outside = Path(temporary) / '其他工作目录'
            outside.mkdir()
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            command = [sys.executable, '-I', '-X', 'utf8', str(root / 'agent/battle_editor/launcher.py'),
                       '--port', str(port), '--no-browser']
            def run(arguments, *, cwd=outside):
                result = subprocess.run(arguments, cwd=cwd, capture_output=True, encoding='utf-8',
                    timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)
            try:
                launched = run(command + ['--idle-timeout', '60'])
                self.assertFalse(launched['reused'])
                self.assertEqual(launched['project_root'], str(root.resolve()))
                self.assertIn('?project=', launched['url'])
                status = run(command + ['--status'])['health']
                self.assertEqual(status['pid'], launched['pid'])
                self.assertTrue(status['requiresProjectBinding'])
                self.assertEqual(run(command)['pid'], launched['pid'])
                self.assertTrue(run(command)['reused'])
                module_status = run([sys.executable, '-X', 'utf8', '-m', 'agent.battle_editor.launcher',
                    '--project-root', str(root), '--port', str(port), '--status'], cwd=ROOT)
                self.assertEqual(module_status['health']['pid'], launched['pid'])
                self.assertTrue(run(command + ['--stop'])['stopped'])
                self.assertIsNone(service_status(root, port))
            finally:
                if service_status(root, port):
                    stop_editor(root, port=port)

    @unittest.skipUnless(os.name == 'nt', 'CMD is a Windows entry')
    def test_real_cmd_help_from_an_unrelated_directory(self):
        with tempfile.TemporaryDirectory(prefix='MaaFGO CMD caller ') as temporary:
            result = subprocess.run([os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe'),
                '/d', '/c', str(ROOT / '打开编队编辑器.cmd'), '--help'], cwd=temporary,
                input='', capture_output=True, encoding='utf-8', timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('--project-root', result.stdout)
            self.assertIn('--no-takeover', result.stdout)
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
