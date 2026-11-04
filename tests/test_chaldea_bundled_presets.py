"""Built-in trial examples load offline after user files, including portable builds."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent/custom"))
from agent import chaldea


PRESET_NAMES = (
    "地狱的试炼_202610_阿兹赖尔.json",
    "地狱的试炼_202610_补贯通.json",
)


class BundledBattlePresetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cache = self.root / "config/Battle"
        for name, value in (("CACHE_DIR", str(self.cache)),
                            ("__file__", str(self.root / "agent/chaldea/__init__.py"))):
            patcher = patch.object(chaldea, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.share = {"team": {"name": "built-in", "onFieldSvts": [], "backupSvts": []},
                      "actions": [{"type": "attack", "attacks": []}]}

    def write(self, path, data=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.share if data is None else data, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def preset(self, name=PRESET_NAMES[0], *, portable=False):
        base = self.root / ("resource/base" if portable else "assets/resource/base")
        return self.write(base / "battle_presets" / name)

    def fetch_without_network(self, source):
        with patch.object(chaldea, "fetch_team_by_id") as fetch_team, \
             patch.object(chaldea, "fetch_teams_by_quest") as fetch_quest:
            result = chaldea.fetch_share_data(source)
        fetch_team.assert_not_called()
        fetch_quest.assert_not_called()
        return result

    def test_development_preset_loads_without_creating_or_changing_user_files(self):
        preset = self.preset()
        raw = preset.read_bytes()
        data, _, team_id = self.fetch_without_network(PRESET_NAMES[0])
        self.assertEqual(data, self.share)
        self.assertEqual(team_id, "local")
        self.assertEqual(preset.read_bytes(), raw)
        self.assertFalse(self.cache.exists())

    def test_optional_json_suffix_also_loads_builtin(self):
        self.preset()
        self.assertEqual(self.fetch_without_network(PRESET_NAMES[0][:-5])[0], self.share)

    def test_portable_resource_layout_resolves_from_module_location(self):
        self.preset(portable=True)
        self.assertEqual(self.fetch_without_network(PRESET_NAMES[0])[0], self.share)

    def test_all_local_filename_candidates_take_precedence_over_builtin(self):
        for local_name, source in ((PRESET_NAMES[0], PRESET_NAMES[0]),
                                   (PRESET_NAMES[0], PRESET_NAMES[0][:-5]),
                                   (PRESET_NAMES[0][:-5], PRESET_NAMES[0][:-5])):
            with self.subTest(local_name=local_name, source=source):
                shutil.rmtree(self.cache, ignore_errors=True)
                self.preset()
                local = {"team": {"name": "user-edited"}, "actions": []}
                path = self.write(self.cache / local_name, local)
                raw = path.read_bytes()
                self.assertEqual(self.fetch_without_network(source)[0], local)
                self.assertEqual(path.read_bytes(), raw)

    def test_invalid_user_file_is_not_silently_replaced_with_builtin(self):
        self.preset()
        path = self.cache / PRESET_NAMES[0]
        path.parent.mkdir(parents=True)
        for invalid in ("{broken", "[]", '{"team":null}'):
            with self.subTest(invalid=invalid):
                path.write_text(invalid, encoding="utf-8")
                self.assertIsNone(chaldea._load_local_file(PRESET_NAMES[0]))
                self.assertEqual(path.read_text(encoding="utf-8"), invalid)

    def test_explicit_paths_keep_their_original_meaning(self):
        self.preset()
        missing = self.root / "chosen" / PRESET_NAMES[0]
        self.assertIsNone(chaldea._load_local_file(str(missing)))
        self.assertIsNone(chaldea._load_local_file("./" + PRESET_NAMES[0]))
        explicit = self.write(self.root / "chosen" / "custom.json",
                              {"team": {"name": "explicit"}})
        self.assertEqual(chaldea._load_local_file(str(explicit))['team']['name'], "explicit")
        self.assertEqual(chaldea._load_local_file(str(explicit)[:-5])['team']['name'], "explicit")

    def test_unknown_filenames_do_not_search_builtin_directory(self):
        name = "another-team.json"
        self.write(self.root / "assets/resource/base/battle_presets" / name)
        self.assertIsNone(chaldea._load_local_file(name))

    def test_shipped_presets_survive_relocation_to_a_portable_build(self):
        for name in PRESET_NAMES:
            with self.subTest(name=name):
                source = ROOT / "assets/resource/base/battle_presets" / name
                data = json.loads(source.read_text(encoding="utf-8"))
                destination = self.root / "resource/base/battle_presets" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                loaded, _, _ = self.fetch_without_network(name)
                self.assertEqual(loaded, data)
                self.assertIsInstance(loaded.get("team"), dict)
                self.assertTrue(loaded.get("actions"))
                self.assertEqual(destination.read_bytes(), source.read_bytes())
        self.assertFalse(self.cache.exists())


if __name__ == "__main__":
    unittest.main()
