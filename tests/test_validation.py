import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from validate_schema import create_validator, load_jsonc, validate_interface_references


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schemas = {}
        for path in (ROOT / "deps/tools").glob("*.schema.json"):
            document = load_jsonc(path)
            schemas[path.name] = document
            schemas[path.resolve().as_uri()] = document
        cls.validator = create_validator(schemas["interface_import.schema.json"], schemas)

    def test_mxu_extensions_are_checked_not_unconditionally_allowed(self):
        select = {"type": "scan_select", "scan_dir": "config/Battle", "scan_filter": "*.json",
                  "cases": [], "default": "", "pipeline_override": {}}
        self.assertFalse(list(self.validator.iter_errors({"option": {"test": select}})))
        select["scan_filter"] = 42
        self.assertTrue(list(self.validator.iter_errors({"option": {"test": select}})))

    def test_missing_import_cycle_and_entry_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            interface = root / "interface.json"
            interface.write_text(json.dumps({"import": ["missing.json"]}), encoding="utf-8")
            self.assertFalse(validate_interface_references(interface))
            interface.write_text(json.dumps({"import": ["interface.json"]}), encoding="utf-8")
            self.assertFalse(validate_interface_references(interface))
            interface.write_text(json.dumps({"task": [{"entry": "missing"}]}), encoding="utf-8")
            self.assertFalse(validate_interface_references(interface))
            interface.write_text('{"import":["invalid.json"]}', encoding="utf-8")
            (root / "invalid.json").write_text('[]', encoding="utf-8")
            self.assertFalse(validate_interface_references(interface))
            (root / "invalid.json").write_text('{"import":"not-a-list"}', encoding="utf-8")
            self.assertFalse(validate_interface_references(interface))

    def test_shipped_interface_task_entries_exist(self):
        self.assertTrue(validate_interface_references(ROOT / "assets/interface.json"))

    def test_local_only_import_cannot_pass_release_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "interface.json").write_text('{"import":["local.json"]}', encoding="utf-8")
            (root / "local.json").write_text('{}', encoding="utf-8")
            self.assertFalse(validate_interface_references(root / "interface.json", True))
            subprocess.run(["git", "add", "local.json"], cwd=root, check=True)
            self.assertTrue(validate_interface_references(root / "interface.json", True))

    def test_explicit_task_validation_fails_if_schema_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/validate_schema.py"), "--schema-dir", temp, "--task-dirs", temp],
                capture_output=True, env=dict(os.environ, PYTHONIOENCODING="gbk"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("interface_import.schema.json", result.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
