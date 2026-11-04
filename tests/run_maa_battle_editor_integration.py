"""Run the real Maa pipeline with a dummy controller; no game or device input."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent" / "custom")]

from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from maa.library import Library
from battle_editor.launcher import open_editor, service_status, stop_editor
# Register the identical action on the local Resource for this native callback
# test, rather than switching this process into AgentServer mode at decoration.
Library.version()
with patch("maa.agent.agent_server.AgentServer.custom_action", return_value=lambda cls: cls):
    from battle_editor_action import OpenBattleEditor


class LocalOnlyController(CustomController):
    def connect(self): return True
    def request_uuid(self): return "battle-editor-local-only"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
    def start_app(self, *args): raise AssertionError("No device operations permitted")
    stop_app = start_app
    click = start_app
    swipe = start_app
    touch_down = start_app
    touch_move = start_app
    touch_up = start_app
    click_key = start_app
    input_text = start_app
    key_down = start_app
    key_up = start_app


def main():
    with tempfile.TemporaryDirectory(prefix="maa-editor-integration-") as temporary:
        root = Path(temporary)
        shutil.copytree(ROOT / "agent/battle_editor", root / "agent/battle_editor",
                        ignore=shutil.ignore_patterns("data", "__pycache__"))
        (root / "agent/chaldea").mkdir()
        shutil.copy2(ROOT / "agent/chaldea/servant_aliases.py", root / "agent/chaldea")
        # The offline editor shares the native strategy contract and reads the
        # special-skill registry by file.
        # Keep this isolated installation complete without copying battle runtime.
        (root / "agent/battle/core").mkdir(parents=True)
        shutil.copy2(ROOT / "agent/battle/core/special_skills.py", root / "agent/battle/core")
        shutil.copy2(ROOT / "agent/battle/core/advanced_strategy.py", root / "agent/battle/core")
        (root / "web/battle-editor").mkdir(parents=True)
        (root / "web/battle-editor/index.html").write_text("<!doctype html><title>Integration</title>")
        bundle = root / "resource"
        (bundle / "pipeline").mkdir(parents=True)
        shutil.copy2(ROOT / "assets/resource/base/pipeline/编队与战斗编辑器.json", bundle / "pipeline/editor.json")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        Toolkit.init_option(str(ROOT / ".workbuddy" / "runtime" / "editor-native-integration"))
        controller, resource, tasker = LocalOnlyController(), Resource(), Tasker()
        assert controller.post_connection().wait().succeeded
        assert resource.register_custom_action("open_battle_editor", OpenBattleEditor())
        assert resource.post_bundle(str(bundle)).wait().succeeded
        assert tasker.bind(resource, controller)
        def launch(project_root, **kwargs):
            return open_editor(project_root, port=port, open_browser=False, **kwargs)
        def stop(project_root):
            return stop_editor(project_root, port=port)
        try:
            with patch("battle_editor_action._AGENT_DIR", root / "agent"), \
                 patch("battle_editor_action._mxu_owner", return_value=(None, None)), \
                 patch("battle_editor_action.open_editor", side_effect=launch), \
                 patch("battle_editor_action.stop_editor", side_effect=stop):
                assert tasker.post_task("打开编队与战斗编辑器").wait().succeeded
                first = service_status(root, port)
                assert first is not None
                assert tasker.post_task("打开编队与战斗编辑器").wait().succeeded
                assert service_status(root, port)["pid"] == first["pid"]
                override = {"打开编队与战斗编辑器": {"attach": {"editor_operation": "stop"}}}
                assert tasker.post_task("打开编队与战斗编辑器", override).wait().succeeded
                assert service_status(root, port) is None
            print(json.dumps({"ok": True, "pipeline": "real MaaFramework", "controller": "local dummy", "checks": ["start", "reuse", "task return keeps service", "stop"]}, ensure_ascii=False))
        except Exception:
            # TemporaryDirectory is removed on failure too; preserve the actual
            # child-process traceback in CI instead of only an expired log path.
            log_path = root / "config/BattleEditor/service.log"
            if log_path.is_file():
                print("\nEditor service log:\n" + log_path.read_text(encoding="utf-8", errors="replace"),
                      file=sys.stderr, flush=True)
            raise
        finally:
            if service_status(root, port) is not None:
                stop_editor(root, port=port)


if __name__ == "__main__":
    main()
