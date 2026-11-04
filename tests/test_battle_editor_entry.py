"""Check the MXU entry wiring and its real success/failure reporting boundary."""
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent" / "custom")]

from battle_editor.launcher import EditorLaunchError
from battle_editor_action import OpenBattleEditor


class EntryTest(unittest.TestCase):
    def test_task_entry_and_action_registration_match(self):
        task = json.loads((ROOT / "assets/tasks/编队与战斗编辑器.json").read_text(encoding="utf-8"))["task"][0]
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/编队与战斗编辑器.json").read_text(encoding="utf-8"))
        self.assertEqual(pipeline[task["entry"]]["action"]["param"]["custom_action"], "open_battle_editor")
        self.assertEqual(pipeline[task["entry"]]["on_error"], [])
        interface = json.loads((ROOT / "assets/interface.json").read_text(encoding="utf-8"))
        self.assertIn("tasks/编队与战斗编辑器.json", interface["import"])

    def test_start_failure_is_reported_to_maa(self):
        context = SimpleNamespace(get_node_data=lambda _: {"attach": {"editor_operation": "open"}})
        with patch("battle_editor_action._mxu_owner", return_value=(None, None)), \
             patch("battle_editor_action.open_editor", side_effect=EditorLaunchError("port occupied")):
            self.assertFalse(OpenBattleEditor().run(context, SimpleNamespace(node_name="editor")).success)

    def test_stop_uses_shared_launcher_and_never_opens_browser(self):
        context = SimpleNamespace(get_node_data=lambda _: {"attach": {"editor_operation": "stop"}})
        with patch("battle_editor_action.stop_editor", return_value=True) as stop, \
             patch("battle_editor_action.open_editor") as launch:
            self.assertTrue(OpenBattleEditor().run(context, SimpleNamespace(node_name="editor")).success)
        stop.assert_called_once_with(ROOT)
        launch.assert_not_called()

    def test_android_omits_desktop_editor_and_dead_loopback_links(self):
        spec = importlib.util.spec_from_file_location("editor_android_boundary", ROOT / "tools/prepare_android.py")
        android = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(android)
        source = {"version": "v9.9.9", "task": [
            {"name": "打开编队与战斗编辑器", "option": ["editor"]},
            {"name": "战斗", "description": "保留说明\n[打开编辑器](http://127.0.0.1:18765/)"}],
            "option": {"editor": {"type": "select", "cases": [{"name": "open"}]},
                       "battle_mode": {"cases": [{"name": "native"}]},
                       "主线-战斗方式": {"cases": [{"name": "native"}]}}}
        converted = android.adapt_interface(source, ROOT / "assets")
        self.assertEqual(converted["version"], "v9.9.9")
        self.assertEqual([task["name"] for task in converted["task"]], ["战斗"])
        self.assertEqual(converted["task"][0]["description"], "保留说明")
        self.assertNotIn("editor", converted["option"])
        self.assertIn("127.0.0.1", source["task"][1]["description"])
        with tempfile.TemporaryDirectory() as folder:
            src, dest = Path(folder) / "agent", Path(folder) / "output"
            for relative in ("main.py", "custom/auto_battle_action.py", "custom/battle_editor_action.py",
                             "battle_editor/data/catalog.json", "chaldea/bbc_formatter.py"):
                path = src / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            android.copy_agent(src, dest)
            self.assertFalse((dest / "battle_editor").exists())
            self.assertFalse((dest / "custom/battle_editor_action.py").exists())
            self.assertTrue((dest / "custom/auto_battle_action.py").is_file())
            self.assertTrue((dest / "chaldea/bbc_formatter.py").is_file())


if __name__ == "__main__":
    unittest.main()
