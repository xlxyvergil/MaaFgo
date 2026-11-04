"""Chaldea file pickers expose desktop hints without changing import values."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest

from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chaldea_file_dialog_android", ROOT / "tools/prepare_android.py")
android = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android)


def fields():
    for filename, name in (("Chaldea导入手动输入.json", "Chaldea导入手动输入"),
                           ("chaldea_team_config.json", "chaldea_team_config")):
        document = json.loads((ROOT / "assets/options" / filename).read_text(encoding="utf-8"))
        yield filename, document["option"][name]["inputs"][0]


class ChaldeaFileDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((ROOT / "deps/tools/interface.schema.json").read_text(encoding="utf-8"))
        cls.validator = validator_for(schema)({
            "$ref": "#/definitions/inputItem", "definitions": schema["definitions"],
        })

    def test_both_import_entries_default_to_battle_json_without_replacing_input(self):
        for filename, field in fields():
            with self.subTest(entry=filename):
                self.assertEqual(field["name"], "chaldea_import_source")
                self.assertEqual(field["file_dialog_dir"], "config/Battle")
                self.assertEqual(field["file_dialog_filters"], [
                    {"name": "JSON", "extensions": ["json"]},
                    {"name": "所有文件", "extensions": ["*"]},
                ])
                self.assertEqual(field["default"], "")
                for value in ("16017", "https://link.chaldea.center/laplace/share?id=16017",
                              "local_队伍.json", r"F:\旧安装\config\Battle\队伍.json"):
                    self.assertIsNotNone(re.fullmatch(field["verify"], value))

    def test_source_inputs_match_schema_and_legacy_inputs_remain_valid(self):
        for filename, field in fields():
            with self.subTest(entry=filename):
                self.validator.validate(field)
                legacy = {key: value for key, value in field.items() if not key.startswith("file_dialog_")}
                self.validator.validate(legacy)

    def test_all_battle_tasks_reach_configured_shared_import_entries(self):
        source = android.load_interface(ROOT / "assets")
        imports = {
            name for name, option in source["option"].items()
            if any(field.get("name") == "chaldea_import_source"
                   for field in option.get("inputs", []))
        }
        self.assertEqual(imports, {"Chaldea导入手动输入", "chaldea_team_config", "地狱的试炼-队伍方案"})
        expected = {
            "日常战斗", "冠位戴冠战", "迦勒底之门", "直接bbc战斗",
            "自动推主线（beta）", "大活动", "小活动", "Chaldea 自动编队",
            "芥雏子学姐连续代打", "地狱的试炼",
        }
        covered = set()
        for task in source["task"]:
            visited, pending = set(), list(task.get("option", []))
            while pending:
                name = pending.pop()
                if name in visited:
                    continue
                visited.add(name)
                option = source["option"][name]
                pending.extend(option.get("option", []))
                for case in option.get("cases", []):
                    pending.extend(case.get("option", []))
            if visited & imports:
                covered.add(task["name"])
        self.assertTrue(expected <= covered, expected - covered)
        for name in imports:
            field = source["option"][name]["inputs"][0]
            with self.subTest(entry=name):
                self.assertEqual(field["file_dialog_dir"], "config/Battle")
                self.assertEqual(field["file_dialog_filters"][0]["extensions"], ["json"])

    def test_schema_rejects_malformed_dialog_hints(self):
        field = next(fields())[1]
        for changes in (
            {"file_dialog_dir": 123}, {"file_dialog_dir": ""},
            {"file_dialog_filters": {"name": "JSON", "extensions": ["json"]}},
            {"file_dialog_filters": []},
            {"file_dialog_filters": [{"name": "JSON", "extensions": "json"}]},
            {"file_dialog_filters": [{"name": "JSON", "extensions": []}]},
            {"file_dialog_filters": [{"name": "JSON", "extensions": [".json"]}]},
            {"file_dialog_filters": [{"name": "JSON", "extensions": ["json"], "args": "--bad"}]},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(list(self.validator.iter_errors({**field, **changes})))

    def test_android_drops_desktop_hints_and_keeps_saved_import_values(self):
        for filename, field in fields():
            for value in ("16017", "https://link.chaldea.center/laplace/share?id=16017",
                          r"F:\旧安装\config\Battle\队伍.json"):
                field = {**field, "default": value}
                source = {
                    "task": [{"name": "战斗", "option": ["entry"]}],
                    "option": {
                        "entry": {"type": "input", "inputs": [field]},
                        "battle_mode": {"cases": [{"name": "native"}]},
                        "主线-战斗方式": {"cases": [{"name": "native"}]},
                    },
                }
                before = copy.deepcopy(source)
                with self.subTest(entry=filename, value=value):
                    converted = android.adapt_interface(source, ROOT / "assets")
                    result = converted["option"]["entry"]["inputs"][0]
                    for desktop_key in ("input_type", "file_dialog_dir", "file_dialog_filters"):
                        self.assertNotIn(desktop_key, result)
                    self.assertEqual(result["default"], value)
                    self.assertEqual(result["verify"], field["verify"])
                    self.assertEqual(source, before)

    def test_real_android_interface_has_no_desktop_file_dialog_hints(self):
        source = android.load_interface(ROOT / "assets")
        converted = android.adapt_interface(source, ROOT / "assets")
        serialized = json.dumps(converted, ensure_ascii=False)
        self.assertNotIn('"file_dialog_dir"', serialized)
        self.assertNotIn('"file_dialog_filters"', serialized)


if __name__ == "__main__":
    unittest.main()
