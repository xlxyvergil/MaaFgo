"""Resource CI validates preset data without exempting real pipeline files."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from validate_schema import create_validator, load_jsonc, validate_resource_file


class BattlePresetValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schemas = {}
        for path in (ROOT / "deps/tools").glob("*.schema.json"):
            document = load_jsonc(path)
            schemas[path.name] = document
            schemas["./" + path.name] = document
            schemas["/" + path.name] = document
            schemas[path.resolve().as_uri()] = document
        cls.pipeline_validator = create_validator(schemas["pipeline.schema.json"], schemas)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.valid = {"team": {"onFieldSvts": [{"svtId": 100100}, None, None],
                               "backupSvts": [None, None, None]},
                      "actions": [{"type": "attack", "attacks": [{"isAuto": True}]}]}

    def check(self, path):
        with redirect_stdout(io.StringIO()):
            return validate_resource_file(path, self.pipeline_validator)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_two_actual_shipped_presets_pass_battle_contract(self):
        paths = sorted((ROOT / "assets/resource/base/battle_presets").glob("*.json"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(self.check(path))

    def test_malformed_json_is_rejected_in_preset_directory(self):
        path = self.write("resource/base/battle_presets/broken.json", self.valid)
        path.write_text('{"team":', encoding="utf-8")
        self.assertFalse(self.check(path))

    def test_bad_formation_and_bad_action_are_rejected(self):
        bad_action = {**self.valid, "actions": [{"type": "skill", "svt": 0, "skill": 99}]}
        for bad in ([], {}, {"team": {"onFieldSvts": []}}, bad_action):
            with self.subTest(value=bad):
                path = self.write("resource/base/battle_presets/bad.json", bad)
                self.assertFalse(self.check(path))

    def test_preset_metadata_warnings_do_not_block_valid_plans(self):
        data = {**self.valid, "_guideNotes": {"status": "example"}}
        self.assertTrue(self.check(self.write("resource/base/battle_presets/valid.json", data)))

    def test_real_pipeline_files_still_use_pipeline_schema(self):
        good = self.write("resource/base/pipeline/good.json", {"node": {}})
        bad = self.write("resource/base/pipeline/bad.json", {"node": {"action": {"type": "InvalidAction"}}})
        disguised_preset = self.write("resource/base/pipeline/formation.json", self.valid)
        self.assertTrue(self.check(good))
        self.assertFalse(self.check(bad))
        self.assertFalse(self.check(disguised_preset))

    def test_a_nested_pipeline_folder_named_battle_presets_is_not_exempt(self):
        path = self.write("resource/base/pipeline/battle_presets/invalid.json", self.valid)
        self.assertFalse(self.check(path))

    def test_jsonc_preset_uses_same_data_validation(self):
        path = self.write("resource/base/battle_presets/example.jsonc", self.valid)
        path.write_text("// valid JSONC example\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertTrue(self.check(path))


if __name__ == "__main__":
    unittest.main()
