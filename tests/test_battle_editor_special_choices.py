"""The editor exposes native menus without loading Maa or changing source JSON."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from battle.core.special_skills import SPECIAL_SKILL_GROUPS
from battle_editor.catalog import build_catalog
from battle_editor.bbc_import import import_bbc_document
from battle_editor.server import EditorHTTPServer
from battle_editor.special_choices import match_special_skill, special_skill_catalog
from battle_editor.storage import BattleStorage, EditorError
from battle_editor.validation import validate_battle


def battle(ids, actions, delegate=None):
    slots = [{"svtId": sid} if sid else None for sid in ids]
    slots += [None] * (6 - len(slots))
    if slots[0]:
        slots[0]["supportType"] = "friend"
    result = {"team": {"onFieldSvts": slots[:3], "backupSvts": slots[3:],
                       "mysticCode": {"mysticCodeId": 210}}, "actions": actions}
    if delegate is not None:
        result["delegate"] = delegate
    return result


def skill(svt, number):
    return {"type": "skill", "svt": svt, "skill": number - 1}


def errors(data, catalog=None):
    return [issue for issue in validate_battle(data, catalog)["issues"] if issue["severity"] == "error"]


class SpecialSkillCatalogTests(unittest.TestCase):
    def test_every_native_group_and_option_has_the_same_numbering(self):
        catalog = special_skill_catalog()
        self.assertEqual(catalog["schemaVersion"], 1)
        self.assertEqual(len(catalog["groups"]), 14)
        for actual, native in zip(catalog["groups"], SPECIAL_SKILL_GROUPS):
            with self.subTest(native.root):
                self.assertEqual(actual["id"], native.root)
                self.assertEqual(actual["servantIds"], list(native.servant_ids))
                self.assertEqual(actual["skillNumbers"], list(native.skills))
                self.assertEqual(actual["field"], native.field)
                self.assertEqual(actual["defaultValue"], native.default)
                self.assertEqual([row["value"] for row in actual["options"]],
                                 list(range(native.first_value, native.first_value + len(native.options))))
                self.assertTrue(all(row["label"] and "战斗_" not in row["label"] for row in actual["options"]))
        catalog["groups"][0]["options"][0]["label"] = "changed"
        self.assertNotEqual(special_skill_catalog(), catalog)

    def test_offline_catalog_and_capabilities_include_bounded_menus(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(build_catalog(directory)["specialSkillChoices"], special_skill_catalog())
            server = SimpleNamespace(root=Path(directory), storage=BattleStorage(directory),
                                     mode="standalone", idle_timeout=3600)
            caps = EditorHTTPServer.capabilities(server)
            self.assertIs(caps["features"]["customSkillSelections"], True)
            self.assertEqual(caps["supportedSpecialSkillFields"], ["tdTypeChanges", "skillActSelectSelections"])

    def test_both_editor_import_names_stay_free_of_maa_and_network_fallbacks(self):
        script = """
import importlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / 'agent')]
for name in ('battle_editor.catalog', 'agent.battle_editor.catalog'):
    module = importlib.import_module(name)
    assert len(module.special_skill_catalog()['groups']) == 14
assert not any(name == 'maa' or name.startswith('maa.') for name in sys.modules)
assert not any(name.endswith('chaldea') or name.endswith('game_data') for name in sys.modules)
"""
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(ROOT)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_menu_variants_match_and_current_bundled_defaults_resolve(self):
        rows = json.loads((ROOT / "agent/battle_editor/data/catalog.json").read_text(encoding="utf-8"))["servants"]
        by_id = {row["id"]: row for row in rows}
        cases = [(203300, 1, 604450, 604550), (603800, 3, 645251, 2379350),
                 (1002000, 3, 2588650, 2589650), (1002100, 3, 2588650, 2589675),
                 (1100100, 3, 199550, 458900), (1100100, 3, 458650, 458900)]
        for sid, number, old_id, current_id in cases:
            with self.subTest(sid=sid, old=old_id):
                explicit = [None] * 3
                explicit[number - 1] = old_id
                member = {"svtId": sid, "skillIds": explicit}
                self.assertIs(match_special_skill(member, number - 1, by_id[sid])[1], False)
                explicit[number - 1] = current_id
                self.assertIs(match_special_skill(member, number - 1, by_id[sid])[1], True)
                for unspecified in (None, 0):
                    explicit[number - 1] = unspecified
                    self.assertIs(match_special_skill(member, number - 1, by_id[sid])[1], True)
                for invalid in (False, True, "", "458900", -1):
                    explicit[number - 1] = invalid
                    self.assertIsNot(match_special_skill(member, number - 1, by_id[sid])[1], True)
        for sid in (2300700, 2300800):
            group, applicable = match_special_skill({"svtId": sid}, 2, by_id[sid])
            self.assertEqual(group["skillIds"], [2415451])
            self.assertIs(applicable, True)

    def test_target_metadata_does_not_confuse_np_type_with_ally_target(self):
        groups = special_skill_catalog()["groups"]
        rabbit = next(group for group in groups if "2301100" in group["servantIds"])
        self.assertEqual([option["target"] for option in rabbit["options"]], ["none", "none"])
        kuku = next(group for group in groups if "2501100" in group["servantIds"])
        self.assertTrue(all("target" not in option for option in kuku["options"]))


class SpecialSkillValidationTests(unittest.TestCase):
    def test_catalog_aliases_consume_the_canonical_special_queue(self):
        meta = {"id": 1100900, "aliases": [9100900], "skills": [{"num": 2, "id": 687450}]}
        data = battle([9100900], [skill(0, 2), skill(0, 2)], {"tdTypeChanges": [1]})
        report = validate_battle(data, {"servants": [meta]})
        exhausted = next(row for row in report["issues"] if row["code"] == "special_choice_exhausted")
        self.assertEqual((exhausted["path"], exhausted["actionIndex"]), ("delegate.tdTypeChanges", 1))
        self.assertNotIn("unused_special_choices", [row["code"] for row in report["issues"]])
        group, applicable = match_special_skill({"svtId": 1100900}, 1,
                                               {"id": 9100900, "aliases": [1100900]})
        self.assertEqual(group["field"], "tdTypeChanges")
        self.assertIs(applicable, True)

    def test_two_queues_follow_independent_global_order_across_turns_and_swap(self):
        data = battle([1100900, 2501100, None, 305600], [
            skill(0, 2), skill(1, 1), {"type": "attack", "attacks": []},
            skill(0, 1), skill(0, 2), {"type": "skill", "skill": 2},
            skill(0, 1), {"type": "attack", "attacks": []},
        ], {"tdTypeChanges": [2, "quick"], "skillActSelectSelections": [0, 2],
            "replaceMemberIndexes": [[0, 0]]})
        original = copy.deepcopy(data)
        self.assertEqual(errors(data), [])
        self.assertEqual(data, original)
        self.assertNotIn("unused_special_choices", [issue["code"] for issue in validate_battle(data)["issues"]])

    def test_all_menu_values_validate_and_two_option_menus_reject_value_two(self):
        for group in special_skill_catalog()["groups"]:
            sid, number = int(group["servantIds"][0]), group["skillNumbers"][0]
            for option in group["options"]:
                with self.subTest(group=group["id"], value=option["value"]):
                    data = battle([sid], [skill(0, number)], {group["field"]: [option["value"]]})
                    if group.get("skillIds"):
                        ids = [None] * 3
                        ids[number - 1] = group["skillIds"][0]
                        data["team"]["onFieldSvts"][0]["skillIds"] = ids
                    self.assertEqual(errors(data), [])
            if group["field"] == "skillActSelectSelections" and len(group["options"]) == 2:
                data["delegate"][group["field"]] = [2]
                self.assertIn("special_choice_out_of_range", [row["code"] for row in errors(data)])

    def test_malformed_values_exhaustion_and_unused_tail_have_actionable_paths(self):
        for field, values in (("tdTypeChanges", [True, 0, 4, 1.0, None, "Arts"]),
                              ("skillActSelectSelections", [True, -1, 3, 0.0, None, "0"])):
            for value in values:
                with self.subTest(field=field, value=value):
                    report = errors(battle([1100900], [], {field: [value]}))
                    self.assertTrue(any(row["code"] == "invalid_special_choice" and row["path"] == f"delegate.{field}[0]" for row in report))
        data = battle([1100900, 305600], [skill(0, 2), skill(1, 1), skill(0, 2)],
                      {"tdTypeChanges": [2], "skillActSelectSelections": [1, 0]})
        report = validate_battle(data)
        exhausted = next(row for row in report["issues"] if row["code"] == "special_choice_exhausted")
        self.assertEqual((exhausted["path"], exhausted["actionIndex"]), ("delegate.tdTypeChanges", 2))
        unused = next(row for row in report["issues"] if row["code"] == "unused_special_choices")
        self.assertEqual((unused["path"], unused["severity"]), ("delegate.skillActSelectSelections", "warning"))

    def test_older_variants_only_block_when_their_field_has_an_active_queue(self):
        for sid, number, old in ((203300, 1, 604450), (603800, 3, 645251),
                                  (1002000, 3, 2588650), (1100100, 3, 458650)):
            data = battle([sid], [skill(0, number)])
            data["team"]["onFieldSvts"][0]["skillIds"] = [old if i == number - 1 else None for i in range(3)]
            self.assertEqual(errors(data), [])
            data["delegate"] = {"tdTypeChanges": [1]}
            self.assertEqual(errors(data), [])
            data["delegate"] = {"skillActSelectSelections": [0]}
            self.assertIn("special_choice_variant_unresolved", [row["code"] for row in errors(data)])
        unknown = battle([1100100], [skill(0, 3)], {"skillActSelectSelections": [1]})
        self.assertIn("special_choice_variant_unresolved", [row["code"] for row in errors(unknown)])

    def test_save_read_and_validation_preserve_unknown_fields_and_original_strings(self):
        data = battle([1100900], [skill(0, 2)], {"tdTypeChanges": ["buster"],
                      "skillActSelectSelections": None, "futureChoices": {"unknown": [0, None]}})
        data["actions"][0]["futureAction"] = {"kept": True}
        original = copy.deepcopy(data)
        self.assertEqual(errors(data), [])
        with tempfile.TemporaryDirectory() as directory:
            storage = BattleStorage(directory)
            saved = storage.save_battle("特殊选择.json", data, create=True)
            self.assertEqual(saved["data"], original)
            self.assertEqual(storage.read_battle("特殊选择.json")["data"], original)
        self.assertEqual(data, original)
        data["delegate"]["actWeightSelections"] = [0]
        self.assertIn("unsupported_delegate", [row["code"] for row in errors(data)])

    def test_existing_chaldea_and_bbc_fixtures_do_not_acquire_special_choices(self):
        catalog = json.loads((ROOT / "agent/battle_editor/data/catalog.json").read_text(encoding="utf-8"))
        for path in (ROOT / "tests/fixtures/chaldea").glob("*.json"):
            raw = path.read_bytes()
            data = json.loads(raw)
            original = copy.deepcopy(data)
            with self.subTest(chaldea=path.name):
                self.assertEqual(errors(data, catalog), [])
                self.assertEqual(data, original)
                self.assertEqual(path.read_bytes(), raw)
        for path in (ROOT / "tests/fixtures/bbc").glob("*.json"):
            if path.name == "servants.json":
                continue
            raw = path.read_bytes()
            source = json.loads(raw)
            with self.subTest(bbc=path.name):
                if path.name.startswith("迪拜BB"):
                    # BBC's custom BBDubai operation has no supported native
                    # equivalent; the existing importer must keep rejecting it.
                    with self.assertRaisesRegex(EditorError, "BB迪拜.*尚无对应原生菜单"):
                        import_bbc_document(ROOT, source, path.name)
                else:
                    data = import_bbc_document(ROOT, source, path.name)["data"]
                    self.assertEqual(errors(data, catalog), [])
                    self.assertFalse((data.get("delegate") or {}).get("tdTypeChanges"))
                    self.assertFalse((data.get("delegate") or {}).get("skillActSelectSelections"))
                self.assertEqual(source, json.loads(raw))
                self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
