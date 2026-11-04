"""Exercise the portable launcher against real local listeners and processes."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from battle_editor.launcher import EditorLaunchError, open_editor, service_status, stop_editor
from battle_editor.server import create_server


class LauncherTest(unittest.TestCase):
    def _listener(self, payload):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                value = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(value)))
                self.end_headers()
                self.wfile.write(value)

            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def test_foreign_port_is_rejected_without_opening_browser(self):
        port = self._listener({"service": "other-service"})
        with patch("battle_editor.launcher.webbrowser.open") as browser:
            with self.assertRaises(EditorLaunchError):
                open_editor(ROOT, port=port)
            browser.assert_not_called()

    def test_another_installation_is_not_reused(self):
        port = self._listener({
            "service": "maafgo-battle-editor", "protocolVersion": 1,
            "projectRoot": str(ROOT / "another-install"), "pid": 123,
        })
        with self.assertRaisesRegex(EditorLaunchError, "另一份"):
            service_status(ROOT, port)

    def test_existing_service_is_reused_without_spawning(self):
        port = self._listener({
            "service": "maafgo-battle-editor", "protocolVersion": 1,
            "projectRoot": str(ROOT), "pid": 123, "mode": "standalone",
        })
        with patch("battle_editor.launcher.subprocess.Popen") as spawn:
            result = open_editor(ROOT, port=port, open_browser=False)
        spawn.assert_not_called()
        self.assertTrue(result.reused)
        self.assertEqual(result.pid, 123)

    def test_same_port_restarts_after_real_http_requests(self):
        with tempfile.TemporaryDirectory(prefix="MaaFGO 服务重启 ") as temporary:
            root = Path(temporary)
            static = root / "web" / "battle-editor"
            static.mkdir(parents=True)
            (static / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
            port = 0
            for attempt in range(3):
                with self.subTest(restart=attempt):
                    server = create_server(root, port, idle_timeout=60)
                    port = server.server_port
                    worker = threading.Thread(target=server.serve_forever,
                                              kwargs={"poll_interval": 0.01}, daemon=True)
                    worker.start()
                    connection = HTTPConnection("127.0.0.1", port, timeout=3)
                    try:
                        connection.request("GET", "/health")
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        health = json.loads(response.read())
                        self.assertEqual(health["service"], "maafgo-battle-editor")
                        self.assertEqual(health["projectRoot"], str(root.resolve()))
                    finally:
                        connection.close()
                        server.shutdown()
                        worker.join(timeout=3)
                        server.server_close()
                    self.assertFalse(worker.is_alive())

    def test_active_listener_still_rejects_second_server(self):
        with tempfile.TemporaryDirectory(prefix="MaaFGO 端口占用 ") as temporary:
            root = Path(temporary)
            static = root / "web" / "battle-editor"
            static.mkdir(parents=True)
            (static / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
            server = create_server(root, 0)
            try:
                with self.assertRaises(OSError):
                    with create_server(root, server.server_port):
                        pass
            finally:
                server.server_close()

    def test_portable_unicode_install_and_both_lifecycles(self):
        with tempfile.TemporaryDirectory(prefix="MaaFGO 编队测试 ") as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "agent" / "battle_editor", root / "agent" / "battle_editor",
                            ignore=shutil.ignore_patterns("__pycache__", "data"))
            (root / "agent" / "chaldea").mkdir()
            shutil.copy2(ROOT / "agent" / "chaldea" / "servant_aliases.py", root / "agent" / "chaldea")
            (root / "agent/battle/core").mkdir(parents=True)
            shutil.copy2(ROOT / "agent/battle/core/special_skills.py", root / "agent/battle/core")
            shutil.copy2(ROOT / "agent/battle/core/advanced_strategy.py", root / "agent/battle/core")
            static = root / "web" / "battle-editor"
            static.mkdir(parents=True)
            (static / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            result = open_editor(root, port=port, open_browser=False, idle_timeout=60, timeout=10)
            try:
                self.assertFalse(result.reused)
                self.assertEqual(service_status(root, port)["pid"], result.pid)
                again = open_editor(root, port=port, open_browser=False)
                self.assertTrue(again.reused)
                self.assertEqual(result.pid, again.pid)
                self.assertTrue(stop_editor(root, port=port))
                self.assertIsNone(service_status(root, port))
                # A service owned by a host survives this call, then exits when that
                # exact host process ends. This uses no MXU/game process.
                import psutil
                owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                try:
                    created = psutil.Process(owner.pid).create_time()
                    bound = open_editor(root, port=port, open_browser=False,
                                        owner_pid=owner.pid, owner_created=created)
                    self.assertEqual(bound.mode, "mxu")
                    self.assertEqual(service_status(root, port)["ownerPid"], owner.pid)
                    owner.terminate()
                    owner.wait(timeout=5)
                    deadline = time.monotonic() + 6
                    while service_status(root, port) and time.monotonic() < deadline:
                        time.sleep(0.1)
                    self.assertIsNone(service_status(root, port))
                finally:
                    if owner.poll() is None:
                        owner.terminate()
                        owner.wait(timeout=5)
            finally:
                if service_status(root, port):
                    stop_editor(root, port=port)


if __name__ == "__main__":
    unittest.main()
