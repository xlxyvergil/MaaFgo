"""Desktop launch links stay portable and never leak into Android descriptions."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("editor_local_link_android", ROOT / "tools/prepare_android.py")
android = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android)


class LocalEditorLinkTest(unittest.TestCase):
    def test_every_battle_entry_offers_portable_cmd_and_existing_service(self):
        entries = (
            ("options/Chaldea导入手动输入.json", "option", "原生自动战斗使用Chaldea队伍"),
            ("options/Chaldea导入手动输入.json", "option", "Chaldea导入手动输入"),
            ("options/chaldea_team_config.json", "option", "chaldea_team_config"),
            ("tasks/原生自动战斗.json", "task", None),
            ("tasks/Chaldea自动编队.json", "task", None),
            ("tasks/编队与战斗编辑器.json", "task", None),
        )
        for relative, kind, key in entries:
            with self.subTest(entry=relative, key=key):
                data = json.loads((ROOT / "assets" / relative).read_text(encoding="utf-8"))[kind]
                entry = data[key] if key else data[0]
                self.assertIn("](./打开编队编辑器.cmd)", entry["description"])
                self.assertIn("http://127.0.0.1:18765/", entry["description"])
        self.assertTrue((ROOT / "打开编队编辑器.cmd").is_file())

    def test_android_removes_local_only_and_legacy_links_at_nested_levels(self):
        for target in ("./打开编队编辑器.cmd", "./" + quote("打开编队编辑器.cmd"),
                       "http://127.0.0.1:18765/"):
            source = {
                "task": [{"name": "战斗", "option": ["entry"],
                          "description": f"保留任务说明\n[启动编辑器]({target})"}],
                "option": {
                    "entry": {"type": "select", "cases": [{"name": "on",
                              "description": f"保留选项说明\n[启动编辑器]({target})"}]},
                    "battle_mode": {"cases": [{"name": "native"}]},
                    "主线-战斗方式": {"cases": [{"name": "native"}]},
                },
            }
            before = copy.deepcopy(source)
            with self.subTest(target=target):
                result = android.adapt_interface(source, ROOT / "assets")
                self.assertEqual(result["task"][0]["description"], "保留任务说明")
                self.assertEqual(result["option"]["entry"]["cases"][0]["description"], "保留选项说明")
                self.assertEqual(source, before)

    def test_real_android_interface_has_no_editor_cmd_link(self):
        source = android.load_interface(ROOT / "assets")
        result = android.adapt_interface(source, ROOT / "assets")
        serialized = json.dumps(result, ensure_ascii=False)
        for marker in ("打开编队编辑器.cmd", "127.0.0.1:18765"):
            self.assertNotIn(marker, serialized)


if __name__ == "__main__":
    unittest.main()
