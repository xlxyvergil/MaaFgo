"""Real HTTP, conflict, atomic-write and unsupported-data regression checks."""
import base64
import copy
import gzip
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from agent.battle_editor.server import EditorHandler, create_server
from agent.battle_editor.storage import BattleStorage, EditorError, MAX_JSON_BYTES, decode_json, digest
from agent.battle_editor.validation import DEFAULTS, validate_battle

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_FIXTURES = ROOT / "tests" / "fixtures" / "battle_editor"


def official_golden():
    return json.loads((OFFICIAL_FIXTURES / "official_contract_golden.json").read_text(encoding="utf-8"))


def battle():
    return {"team": {"name": "中文队伍", "onFieldSvts": [{"svtId": 100100}, {"svtId": 100200, "supportType": "friend"}, {"svtId": 100300}], "backupSvts": [{"svtId": 100400}, None, None], "mysticCode": {"mysticCodeId": 20}}, "actions": [{"type": "attack", "attacks": []}]}


class ValidationTests(unittest.TestCase):
    def codes(self, result, severity="error"):
        return {i["code"] for i in result["issues"] if i["severity"] == severity}

    def test_duplicate_owned_servants_report_identity_and_both_slots_without_mutation(self):
        catalog = {"servants": [{"id": 100100, "name": "阿尔托莉雅·潘德拉贡"}]}
        for support_type in (..., None, "none"):
            with self.subTest(support_type=support_type):
                data = battle()
                duplicate = {"svtId": 100100, "lv": 120, "limitCount": 3, "skillIds": [1, 2, 3], "future": {"keep": [None]}}
                if support_type is not ...:
                    duplicate["supportType"] = support_type
                data["team"]["backupSvts"][0] = duplicate
                before = copy.deepcopy(data)
                report = validate_battle(data, catalog)
                self.assertFalse(report["valid"])
                issues = [i for i in report["issues"] if i["code"] == "duplicate_servant"]
                self.assertEqual(len(issues), 1)
                self.assertEqual((issues[0]["severity"], issues[0]["path"], issues[0]["slot"]), ("error", "team.backupSvts[0].svtId", 3))
                for text in ("阿尔托莉雅·潘德拉贡", "槽位 1", "槽位 4"):
                    self.assertIn(text, issues[0]["message"])
                self.assertEqual(data, before)

    def test_owned_and_support_same_servant_are_allowed_in_either_slot_order(self):
        for support_type in ("friend", "npc", "fixed"):
            for support_first in (False, True):
                with self.subTest(support_type=support_type, support_first=support_first):
                    data = battle()
                    own, support = {"svtId": 100100}, {"svtId": 100100, "supportType": support_type}
                    data["team"]["onFieldSvts"][:2] = [support, own] if support_first else [own, support]
                    report = validate_battle(data)
                    self.assertTrue(report["valid"], report)
                    self.assertNotIn("duplicate_servant", self.codes(report))
                    self.assertNotIn("missing_support", self.codes(report, "warning"))

    def test_support_does_not_allow_two_other_owned_copies_of_the_same_servant(self):
        data = battle()
        data["team"]["onFieldSvts"][1]["svtId"] = 100100
        data["team"]["backupSvts"][0] = {"svtId": 100100, "supportType": "none"}
        report = validate_battle(data)
        self.assertFalse(report["valid"])
        self.assertIn("duplicate_servant", self.codes(report))
        self.assertNotIn("multiple_supports", self.codes(report))

    def test_alias_forms_share_owned_identity_without_rewriting_imported_ids(self):
        catalog = {"servants": [{"id": 1002000, "aliases": [1002100, 1002200], "name": "芙萝拉"}]}
        for first, second in ((1002000, 1002100), (1002100, 1002000), (1002100, 1002200)):
            with self.subTest(first=first, second=second):
                data = battle()
                data["team"]["onFieldSvts"][0]["svtId"] = first
                data["team"]["backupSvts"][0]["svtId"] = second
                before = copy.deepcopy(data)
                report = validate_battle(data, catalog)
                self.assertIn("duplicate_servant", self.codes(report))
                self.assertIn("芙萝拉", next(i["message"] for i in report["issues"] if i["code"] == "duplicate_servant"))
                self.assertEqual(data, before)
        data["team"]["backupSvts"][0]["supportType"] = "friend"
        data["team"]["onFieldSvts"][1]["supportType"] = "none"
        self.assertTrue(validate_battle(data, catalog)["valid"])

    def test_same_display_name_does_not_merge_different_servant_ids(self):
        data = battle()
        catalog = {"servants": [{"id": 100100, "name": "同名从者"}, {"id": 100400, "name": "同名从者"}]}
        self.assertTrue(validate_battle(data, catalog)["valid"])

    def test_unknown_positive_ids_are_checked_but_invalid_ids_do_not_form_identities(self):
        data = battle()
        for slot in (data["team"]["onFieldSvts"][0], data["team"]["backupSvts"][0]):
            slot["svtId"] = 987654321
        report = validate_battle(data)
        self.assertIn("duplicate_servant", self.codes(report))
        self.assertIn("#987654321", next(i["message"] for i in report["issues"] if i["code"] == "duplicate_servant"))
        for invalid in (None, True, False, 0, -1, 1.0, "1", [], {}, {"bad": True}):
            with self.subTest(invalid=invalid):
                data = battle()
                data["team"]["onFieldSvts"][0]["svtId"] = invalid
                data["team"]["backupSvts"][0]["svtId"] = invalid
                report = validate_battle(data)
                self.assertFalse(report["valid"])
                self.assertNotIn("duplicate_servant", self.codes(report))
        data["team"]["onFieldSvts"][0]["svtId"] = 1
        data["team"]["backupSvts"][0]["svtId"] = True
        self.assertNotIn("duplicate_servant", self.codes(validate_battle(data)))

    def test_missing_support_is_an_optional_team_warning_for_missing_null_or_none_type(self):
        for support_type in (..., None, "none"):
            with self.subTest(support_type=support_type):
                data = battle()
                if support_type is ...:
                    del data["team"]["onFieldSvts"][1]["supportType"]
                else:
                    data["team"]["onFieldSvts"][1]["supportType"] = support_type
                before = copy.deepcopy(data)
                report = validate_battle(data)
                self.assertTrue(report["valid"], report)
                issues = [i for i in report["issues"] if i["code"] == "missing_support"]
                self.assertEqual(len(issues), 1)
                self.assertEqual((issues[0]["severity"], issues[0]["path"]), ("warning", "team"))
                self.assertNotIn("slot", issues[0])
                self.assertIn("可忽略", issues[0]["message"])
                self.assertIn("允许保存", issues[0]["message"])
                self.assertEqual(data, before)

    def test_unknown_support_id_and_backup_support_count_without_catalog_metadata(self):
        for support_type in ("friend", "npc", "fixed"):
            with self.subTest(support_type=support_type):
                data = battle()
                data["team"]["onFieldSvts"][1]["supportType"] = "none"
                data["team"]["backupSvts"][2] = {"svtId": 987654321, "supportType": support_type}
                report = validate_battle(data, {"servants": []})
                self.assertTrue(report["valid"], report)
                self.assertNotIn("missing_support", self.codes(report, "warning"))

    def test_invalid_support_does_not_count_or_participate_in_owned_duplicates(self):
        for invalid in ("other", False, {}, []):
            with self.subTest(support_type=invalid):
                data = battle()
                data["team"]["onFieldSvts"][1] = {"svtId": 100100, "supportType": invalid}
                report = validate_battle(data)
                self.assertIn("invalid_support", self.codes(report))
                self.assertIn("missing_support", self.codes(report, "warning"))
                self.assertNotIn("duplicate_servant", self.codes(report))

    def test_multiple_supports_and_empty_frontline_keep_existing_checks(self):
        data = battle()
        data["team"]["backupSvts"][0]["supportType"] = "npc"
        report = validate_battle(data)
        self.assertIn("multiple_supports", self.codes(report))
        self.assertNotIn("missing_support", self.codes(report, "warning"))
        data["team"]["onFieldSvts"] = [None, None, None]
        data["team"]["backupSvts"] = [None, None, None]
        report = validate_battle(data)
        self.assertIn("empty_frontline", self.codes(report))
        self.assertIn("missing_support", self.codes(report, "warning"))
        self.assertNotIn("duplicate_servant", self.codes(report))

    def test_per_skill_enemy_targets_and_repeated_casts_keep_the_raw_plan(self):
        data = battle()
        data["actions"] = [
            {"type": "skill", "svt": 0, "skill": 0, "options": {"enemyTarget": 2}},
            {"type": "skill", "svt": 0, "skill": 0, "options": {"enemyTarget": 0}},
            {"type": "skill", "skill": 1, "options": {"enemyTarget": 1}},
            {"type": "skill", "skill": 1},
            {"type": "attack", "attacks": [], "options": {"enemyTarget": 2}},
        ]
        original = copy.deepcopy(data)
        report = validate_battle(data)
        self.assertTrue(report["valid"], report)
        self.assertNotIn("skill_enemy_target", self.codes(report, "warning"))
        self.assertEqual(data, original)
        data["actions"][0]["options"]["enemyTarget"] = True
        self.assertIn("invalid_integer", self.codes(validate_battle(data)))

    def test_official_optional_fields_unknown_fields_and_three_or_five_appends_roundtrip(self):
        for appends in ([], [0, 10, 0], [0, 10, 0, 0, 0]):
            data = battle()
            svt = data["team"]["onFieldSvts"][0]
            svt.update({"appendLvs": appends, "skillIds": [], "commandCodeIds": [], "cardStrengthens": [], "equip1": {"id": None, "limitBreak": False}, "extra": {"keep": [None, 0, "值"]}})
            data["unknownTop"] = {"deep": [{"z": None}]}
            before = copy.deepcopy(data)
            report = validate_battle(data)
            self.assertTrue(report["valid"], report)
            self.assertIn("preserved_field", self.codes(report, "warning"))
            self.assertEqual(data, before)

    def test_skill_swap_and_mixed_cards_follow_current_lineup(self):
        data = battle()
        data["delegate"] = {"replaceMemberIndexes": [[0, 0]]}
        data["actions"] = [{"type": "skill", "svt": 0, "skill": 0}, {"type": "skill", "skill": 2}, {"type": "skill", "svt": 0, "skill": 1, "options": {"playerTarget": 2}}, {"type": "attack", "options": {"enemyTarget": 2}, "attacks": [{"svt": 0, "card": 0, "cardType": 1}, {"svt": 1, "isTD": True, "card": -1, "cardType": 2}, {"svt": 0, "card": 2, "cardType": "quick"}]}]
        report = validate_battle(data, {"servants": [{"id": 100400, "cards": [1, 1, 3, 2, 2]}]})
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["summary"]["orderChanges"], 1)
        self.assertEqual(report["summary"]["noblePhantasms"], 1)
        data["actions"][-1]["attacks"][0]["card"] = 4
        changed_color = validate_battle(data, {"servants": [{"id": 100400, "cards": [1, 1, 3, 2, 2]}]})
        self.assertTrue(changed_color["valid"])
        self.assertIn("card_deck_mismatch", self.codes(changed_color, "warning"))

    def test_unsupported_actions_special_options_card_types_and_targets_block_formal_save(self):
        for modify, code in (
            (lambda d: d["actions"].append({"type": "wait", "opaque": {"a": 1}}), "unsupported_action"),
            (lambda d: d.update(delegate={"actWeightSelections": [1]}), "unsupported_delegate"),
            (lambda d: d["actions"][0].update(options={"enemyTarget": 3}), "invalid_integer"),
            (lambda d: d["actions"][0].update(attacks=[{"svt": 0, "card": 0, "cardType": 5}]), "unsupported_card_color"),
            (lambda d: d["actions"][0].update(attacks=[{"svt": 0, "cardType": 1}]), "invalid_deck_index"),
            (lambda d: d["actions"].insert(0, {"type": "skill", "svt": 3, "skill": 0}), "invalid_front_slot"),
        ):
            with self.subTest(code=code):
                data = battle()
                modify(data)
                before = copy.deepcopy(data)
                report = validate_battle(data)
                self.assertFalse(report["valid"])
                self.assertIn(code, self.codes(report))
                self.assertEqual(data, before)

    def test_falsey_non_lists_are_not_empty_attacks(self):
        for value in ({}, "", 0, False):
            data = battle()
            data["actions"][0]["attacks"] = value
            self.assertIn("invalid_cards", self.codes(validate_battle(data)))
        data["actions"][0]["attacks"] = None
        self.assertTrue(validate_battle(data)["valid"])

    def test_empty_slot_after_order_change_is_checked_at_action_time(self):
        data = battle()
        data["delegate"] = {"replaceMemberIndexes": [[0, 1]]}
        data["actions"].insert(0, {"type": "skill", "skill": 2})
        self.assertIn("empty_swap", self.codes(validate_battle(data)))

    def test_same_deck_card_cannot_be_selected_twice_in_one_attack(self):
        data = battle()
        data["actions"][0]["attacks"] = [{"svt": 0, "card": 0, "cardType": 1}, {"svt": 0, "card": 0, "cardType": 1}]
        self.assertIn("duplicate_deck_card", self.codes(validate_battle(data)))
        data["actions"][0]["attacks"][1]["card"] = 1
        self.assertTrue(validate_battle(data)["valid"])

    def test_actual_existing_formation_fixtures_pass_without_rewriting_defaults(self):
        for file in (ROOT / "tests" / "fixtures" / "chaldea").glob("*.json"):
            data = json.loads(file.read_text(encoding="utf-8"))
            before = copy.deepcopy(data)
            self.assertTrue(validate_battle(data)["valid"], file.name)
            self.assertEqual(before, data)

    def test_malformed_nested_json_returns_issues_instead_of_crashing_validation(self):
        for value in ({"wrong": True}, [], "bad", False, 1.5):
            data = battle()
            data["team"]["onFieldSvts"][0]["svtId"] = value
            data["actions"][0]["attacks"] = [{"svt": 0, "card": 0, "cardType": 1}]
            report = validate_battle(data, {"servants": [{"id": 100100, "cards": [1, 1, 3, 2, 2]}]})
            self.assertFalse(report["valid"])

    def test_target_is_required_only_when_metadata_is_unambiguous(self):
        data = battle()
        data["actions"].insert(0, {"type": "skill", "svt": 0, "skill": 1})
        ally_skill = {"id": 101, "num": 2, "target": "ally"}
        catalog = {"servants": [{"id": 100100, "skills": [ally_skill]}]}
        self.assertIn("required_ally_target", self.codes(validate_battle(data, catalog)))
        catalog["servants"][0]["skillVariants"] = [ally_skill, {"id": 102, "num": 2, "target": "unknown"}]
        self.assertNotIn("required_ally_target", self.codes(validate_battle(data, catalog)))
        data["team"]["onFieldSvts"][0]["skillIds"] = [None, 101, None]
        self.assertIn("required_ally_target", self.codes(validate_battle(data, catalog)))
        data["actions"][0]["options"] = {"playerTarget": 1}
        self.assertTrue(validate_battle(data, catalog)["valid"])

    def test_historical_alias_uses_canonical_metadata_without_rewriting_original_id(self):
        data = battle()
        data["team"]["onFieldSvts"][0]["svtId"] = 1002100
        data["actions"][0]["attacks"] = [{"svt": 0, "card": 0, "cardType": 2}]
        catalog = {"servants": [{"id": 1002000, "aliases": [1002100], "cards": [1, 1, 3, 2, 2]}]}
        self.assertIn("card_deck_mismatch", self.codes(validate_battle(data, catalog), "warning"))
        self.assertEqual(data["team"]["onFieldSvts"][0]["svtId"], 1002100)

    def test_order_change_then_dynamic_buster_cards_warn_without_blocking(self):
        data = json.loads((OFFICIAL_FIXTURES / "dynamic_card_color_golden.json").read_text(encoding="utf-8"))
        original = copy.deepcopy(data)
        catalog = {"servants": [{"id": 100100, "cards": [3, 1, 1, 2, 2], "skills": [{"id": 100101, "num": 2, "target": "none"}]}]}
        report = validate_battle(data, catalog)
        self.assertTrue(report["valid"], report)
        warnings = [i for i in report["issues"] if i["code"] == "card_deck_mismatch"]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all(i["severity"] == "warning" and i["actionIndex"] == 2 and "技能改色" in i["message"] for i in warnings))
        self.assertEqual(report["summary"]["orderChanges"], 1)
        self.assertEqual(data, original)
        data["actions"][-1]["attacks"][2]["card"] = 1
        self.assertIn("duplicate_deck_card", self.codes(validate_battle(data, catalog)))
        data["actions"][-1]["attacks"][2] = {"svt": 0, "isTD": True, "card": -1, "cardType": 2}
        self.assertIn("duplicate_np", self.codes(validate_battle(data, catalog)))
        data["actions"][-1]["attacks"][2] = {"svt": 0, "isTD": False, "card": 5, "cardType": 2}
        self.assertIn("invalid_deck_index", self.codes(validate_battle(data, catalog)))

    def test_pinned_official_generated_serializer_field_sample(self):
        """Assert the hand-checked contract, without pretending to execute Dart."""
        manifest = json.loads((OFFICIAL_FIXTURES / "official_source_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["commit"], "e656fa4b3e23228805efdec851358e0ab319aeeb")
        self.assertEqual({s["path"] for s in manifest["sources"]}, {"lib/models/userdata/battle.dart", "lib/generated/models/userdata/battle.g.dart", "lib/models/gamedata/common.dart"})
        self.assertTrue(all(len(s["sha256"]) == 64 and manifest["commit"] in s["url"] for s in manifest["sources"]))
        data = official_golden()
        before = copy.deepcopy(data)
        self.assertEqual(set(data), {"minBuild", "appBuild", "options", "team", "delegate", "actions", "isCritTeam"})
        self.assertEqual(set(data["team"]), {"name", "mysticCode", "onFieldSvts", "backupSvts"})
        self.assertEqual(data["team"]["mysticCode"], {"mysticCodeId": 20, "level": 0})
        self.assertEqual(len(data["team"]["onFieldSvts"]), 3)
        self.assertEqual(data["team"]["backupSvts"][1:], [None, None])
        for member in data["team"]["onFieldSvts"] + data["team"]["backupSvts"]:
            if member is None:
                continue
            self.assertIn(member["supportType"], ("none", "friend", "npc"))
            self.assertTrue(all(type(v) is int for v in member["skillLvs"] + member["appendLvs"]))
            self.assertEqual(member["skillIds"], [None, None, None])
            self.assertEqual(set(member["equip1"]), {"id", "limitBreak", "lv"})
        colors = set()
        for action in data["actions"]:
            self.assertIn(action["type"], ("skill", "attack"))
            self.assertEqual(set(action["options"]), {"playerTarget", "enemyTarget", "random", "threshold"})
            self.assertTrue(all(type(v) is int for v in action["options"].values()))
            if action["type"] == "attack":
                self.assertNotIn("svt", action)
                self.assertNotIn("skill", action)
                for card in action["attacks"]:
                    self.assertEqual(set(card), {"svt", "card", "isTD", "critical", "cardType"})
                    self.assertIs(type(card["cardType"]), int)
                    colors.add(card["cardType"])
                    if card["isTD"]:
                        self.assertEqual(card["card"], -1)
                    else:
                        self.assertIn(card["card"], range(5))
        self.assertEqual(colors, {1, 2, 3})
        self.assertEqual(data["delegate"], {"replaceMemberIndexes": [[0, 0]]})
        self.assertNotIn("svt", data["actions"][3])
        report = validate_battle(data)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["summary"]["turns"], 3)
        self.assertEqual(report["summary"]["orderChanges"], 1)
        self.assertEqual(data, before)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="编队 空格 ")
        self.root = Path(self.temp.name)
        self.store = BattleStorage(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_update_conflict_original_tree_and_backup(self):
        data = battle()
        data["custom"] = {"list": [None, {"legacy": 1}]}
        created = self.store.save_battle("local_中文队伍", data, create=True)
        self.assertEqual(created["data"], data)
        self.assertEqual(created["name"], "local_中文队伍.json")
        old_bytes = Path(created["path"]).read_bytes()
        edited = copy.deepcopy(data)
        edited["team"]["name"] = "改名"
        saved = self.store.save_battle(created["name"], edited, created["hash"])
        self.assertEqual(saved["data"], edited)
        backups = list(self.store.backup_dir.glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), old_bytes)
        with self.assertRaises(EditorError) as conflict:
            self.store.save_battle(created["name"], data, created["hash"])
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(conflict.exception.details["currentHash"], saved["hash"])
        self.assertEqual(Path(saved["path"]).read_bytes(), json.dumps(edited, ensure_ascii=False, indent=2).encode() + b"\n")
        self.assertEqual(len(list(self.store.backup_dir.glob("*.json"))), 1)
        with self.assertRaises(EditorError):
            self.store.save_battle(created["name"], data, create=True)

    def test_replace_failure_keeps_original_and_complete_backup_without_temp_residue(self):
        original = self.store.save_battle("local_failure.json", battle(), create=True)
        original_bytes = Path(original["path"]).read_bytes()
        changed = battle()
        changed["team"]["name"] = "不能覆盖"
        with patch("agent.battle_editor.storage.os.replace", side_effect=PermissionError("模拟文件锁")):
            with self.assertRaises(PermissionError):
                self.store.save_battle(original["name"], changed, original["hash"])
        self.assertEqual(Path(original["path"]).read_bytes(), original_bytes)
        self.assertEqual(next(self.store.backup_dir.glob("*.json")).read_bytes(), original_bytes)
        self.assertEqual(list(self.store.battle_dir.glob(".editor-*")), [])

    def test_malformed_old_file_can_be_preserved_as_backup_when_repaired(self):
        file = self.store.battle_dir / "broken.json"
        file.write_bytes(b"not JSON")
        result = self.store.save_battle(file.name, battle(), digest(file.read_bytes()))
        self.assertEqual(result["data"], battle())
        self.assertEqual(next(self.store.backup_dir.glob("*.json")).read_bytes(), b"not JSON")

    def test_two_independent_drafts_and_stale_revision_do_not_clobber(self):
        payload = {"data": {"unknown": [None, {"type": "future"}]}, "editorState": {"tab": "turns"}, "revision": 0, "sourceName": "同一个正式文件.json"}
        a = self.store.save_draft("window-a", payload)
        b = self.store.save_draft("window-b", payload)
        self.assertEqual(a["revision"], 1)
        self.assertEqual(b["revision"], 1)
        with self.assertRaises(EditorError) as conflict:
            self.store.save_draft("window-a", payload)
        self.assertEqual(conflict.exception.code, "draft_conflict")
        self.assertEqual(self.store.read_draft("window-a")["data"], payload["data"])
        a2 = self.store.save_draft("window-a", payload | {"revision": 1, "data": {"changed": True}})
        self.assertEqual(a2["revision"], 2)
        self.assertEqual(self.store.read_draft("window-b")["data"], payload["data"])
        self.assertEqual(len(self.store.list_drafts()["drafts"]), 2)
        self.assertEqual(list(self.store.battle_dir.iterdir()), [])

    def test_path_validation_reserved_names_and_invalid_numeric_json(self):
        for name in ("../outside.json", "..\\outside.json", "F:\\outside.json", "CON.json", "NUL", "LPT1.txt.json", "blank .", ".hidden.json", "bad:name.json", "x/../../z.json"):
            with self.subTest(name=name), self.assertRaises(EditorError):
                self.store.save_battle(name, battle(), create=True)
        with self.assertRaises(EditorError):
            self.store.save_battle("123456", battle(), create=True)
        for raw in (b'{"value": NaN}', b'{"value": Infinity}', b'\xff'):
            with self.assertRaises(EditorError):
                decode_json(raw)

    def test_damaged_file_is_listed_without_hiding_valid_ones(self):
        self.store.save_battle("local_valid", battle(), create=True)
        (self.store.battle_dir / "damaged.json").write_text("broken", encoding="utf-8")
        records = {row["name"]: row for row in self.store.list_battles()["battles"]}
        self.assertIn("error", records["damaged.json"])
        self.assertEqual(records["local_valid.json"]["turnCount"], 1)

    def test_malformed_title_cannot_crash_scheme_or_draft_library(self):
        data = battle()
        data["team"]["name"] = {"bad": "not text"}
        (self.store.battle_dir / "malformed-title.json").write_text(json.dumps(data), encoding="utf-8")
        record = self.store.list_battles()["battles"][0]
        self.assertEqual(record["title"], "malformed-title")
        self.assertIn("error", record)
        self.store.save_draft("unfinished-malformed", {"revision": 0, "data": data})
        draft = self.store.list_drafts()["drafts"][0]
        self.assertIsInstance(draft["title"], str)
        with self.assertRaises(EditorError):
            self.store.save_draft("bad-metadata", {"revision": 0, "data": data, "sourceName": {"bad": True}})

    def test_directory_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.store.battle_dir / "escape.json"
            target = Path(outside) / "secret.json"
            target.write_text("{}", encoding="utf-8")
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("当前 Windows 账户没有创建符号链接权限")
            with self.assertRaises(EditorError) as result:
                self.store.read_battle(link.name)
            self.assertEqual(result.exception.code, "unsafe_path")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="HTTP 中文 ")
        self.root = Path(self.temp.name)
        self.web = self.root / "assets" / "web" / "battle-editor"
        self.web.mkdir(parents=True)
        (self.web / "index.html").write_text("<!doctype html><title>编队</title>", encoding="utf-8")
        self.server = create_server(self.root, 0)
        self.server._catalog = {"servants": []}
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.token = self.request("GET", "/api/session")[1]["token"]

    def tearDown(self):
        self.server.stop()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, *, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        final_headers = {"X-Editor-Token": self.server.token} if token else {}
        if payload is not None:
            final_headers["Content-Type"] = "application/json"
        final_headers.update(headers or {})
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body=body, headers=final_headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        content_type = response.getheader("Content-Type", "")
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        connection.close()
        return status, json.loads(raw) if "application/json" in content_type else raw

    def test_public_health_and_same_origin_session_token_gate(self):
        status, health = self.request("GET", "/health", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(health["service"], "maafgo-battle-editor")
        self.assertEqual(health["protocolVersion"], 1)
        self.assertEqual(health["projectRoot"], str(self.root.resolve()))
        self.assertEqual(health["mode"], "standalone")
        self.assertEqual(len(health["projectId"]), 64)
        for headers in ({"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Host": "attacker.example"}, {"Origin": "null"}):
            self.assertEqual(self.request("GET", "/api/session", headers=headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/battles", token=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/capabilities")[1]["battleDirectory"], str(self.root / "config" / "Battle"))

    def test_ui_media_route_serves_manifest_files_without_exposing_neighbors(self):
        directory = self.root / "agent/battle_editor/data/ui"
        media = directory / "media"
        media.mkdir(parents=True)
        (directory / "manifest.json").write_text(json.dumps({"schema": 1, "files": {"class_saber.png": {}}}), encoding="utf-8")
        image = b"\x89PNG\r\n\x1a\nfixture"
        (media / "class_saber.png").write_bytes(image)
        (media / "unlisted.png").write_bytes(b"not public")
        self.assertEqual(self.request("GET", "/media/ui/class_saber.png", token=False), (200, image))
        self.assertEqual(self.request("GET", "/media/ui/unlisted.png", token=False)[0], 404)
        self.assertEqual(self.request("GET", "/media/ui/%2e%2e%2fmanifest.png", token=False)[0], 403)
        self.assertEqual(self.request("GET", "/media/ui/class_saber.png", headers={"Origin": "https://attacker.example"})[0], 403)

    def test_formation_webp_get_and_head_have_explicit_mime_and_keep_media_boundaries(self):
        directory = self.root / "agent/battle_editor/data/ui"
        media = directory / "media"
        media.mkdir(parents=True)
        name = "formation_100100_0123456789abcdef.webp"
        unlisted = "formation_100100_fedcba9876543210.webp"
        (directory / "manifest.json").write_text(json.dumps({"schema": 1,
            "files": {name: {}, "other.webp": {}, "class_saber.png": {}}}), encoding="utf-8")
        # An actual small lossy WebP, rather than a renamed PNG fixture.
        image = bytes.fromhex("524946463a00000057454250565038202e000000f001009d012a0200030000c01225880274ba01f800044c0000fefd2145ffcb3e7f0d9d4e7ff29545aa6dc5000000")
        for filename in (name, unlisted, "other.webp"):
            (media / filename).write_bytes(image)
        png = b"\x89PNG\r\n\x1a\nfixture"
        (media / "class_saber.png").write_bytes(png)
        with patch("agent.battle_editor.server.mimetypes.guess_type", return_value=(None, None)):
            for method in ("GET", "HEAD"):
                with self.subTest(method=method):
                    connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                    try:
                        connection.request(method, f"/media/ui/{name}")
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.getheader("Content-Type"), "image/webp")
                        self.assertEqual(response.getheader("Content-Length"), str(len(image)))
                        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
                        self.assertEqual(response.read(), image if method == "GET" else b"")
                    finally:
                        connection.close()
        self.assertEqual(self.request("GET", "/media/ui/class_saber.png", token=False), (200, png))
        for path, status in ((f"/media/ui/{unlisted}", 404), ("/media/ui/other.webp", 404),
                             (f"/media/servant/{name}", 404), (f"/media/equip/{name}", 404),
                             (f"/media/status/{name}", 404), (f"/media/ui/{name}:stream", 404),
                             (f"/media/ui/%252e%252e%252f{name}", 404),
                             (f"/media/ui/%2e%2e%2f{name}", 403)):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, token=False)[0], status)

    def test_http_save_update_conflict_and_unknown_data_draft(self):
        data = battle()
        status, first = self.request("POST", "/api/battles", {"name": "local_http.json", "data": data})
        self.assertEqual(status, 201, first)
        data["team"]["name"] = "第二版"
        status, second = self.request("PUT", "/api/battles/local_http.json", {"data": data, "expectedHash": first["hash"]})
        self.assertEqual(status, 200, second)
        self.assertNotEqual(second["hash"], first["hash"])
        status, conflict = self.request("PUT", "/api/battles/local_http.json", {"data": data, "expectedHash": first["hash"]})
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"]["details"]["currentHash"], second["hash"])
        data["actions"].append({"type": "future", "unrecognized": {"tree": [None, True]}})
        self.assertEqual(self.request("POST", "/api/battles", {"name": "local_blocked", "data": data})[0], 422)
        status, draft = self.request("PUT", "/api/drafts/independent-window", {"data": data, "editorState": {"tab": "turns"}, "revision": 0})
        self.assertEqual(status, 200)
        self.assertEqual(draft["data"], data)
        self.assertEqual(self.request("GET", "/api/drafts/independent-window")[1]["data"], data)
        self.assertFalse((self.root / "config" / "Battle" / "local_blocked.json").exists())

    def test_duplicate_owned_servants_block_formal_create_and_update_but_preserve_drafts(self):
        data = battle()
        status, created = self.request("POST", "/api/battles", {"name": "local_original.json", "data": data})
        self.assertEqual(status, 201, created)
        data["team"]["backupSvts"][0] = {"svtId": 100100, "supportType": "none", "unknown": {"keep": [None, "原值"]}}
        data["unknownTop"] = {"untouched": [1, False, None]}
        before = copy.deepcopy(data)
        status, checked = self.request("POST", "/api/validate", {"data": data})
        self.assertEqual(status, 200, checked)
        self.assertFalse(checked["valid"])
        self.assertTrue(any(i["code"] == "duplicate_servant" and i["slot"] == 3 for i in checked["issues"]))
        status, rejected = self.request("POST", "/api/battles", {"name": "local_duplicate.json", "data": data})
        self.assertEqual(status, 422, rejected)
        self.assertEqual(rejected["error"]["code"], "validation_failed")
        self.assertFalse((self.server.storage.battle_dir / "local_duplicate.json").exists())
        status, rejected = self.request("PUT", "/api/battles/local_original.json", {"data": data, "expectedHash": created["hash"]})
        self.assertEqual(status, 422, rejected)
        original = self.request("GET", "/api/battles/local_original.json")[1]
        self.assertEqual(original["hash"], created["hash"])
        self.assertEqual(original["data"], created["data"])
        status, drafted = self.request("PUT", "/api/drafts/duplicate-owned", {"revision": 0, "data": data, "editorState": {"tab": "check"}})
        self.assertEqual(status, 200, drafted)
        self.assertEqual(drafted["data"], before)
        self.assertEqual(self.request("GET", "/api/drafts/duplicate-owned")[1]["data"], before)
        self.assertEqual(data, before)

    def test_missing_support_warning_still_allows_formal_save_and_roundtrip(self):
        data = battle()
        del data["team"]["onFieldSvts"][1]["supportType"]
        before = copy.deepcopy(data)
        status, saved = self.request("POST", "/api/battles", {"name": "local_no_support.json", "data": data})
        self.assertEqual(status, 201, saved)
        self.assertTrue(saved["validation"]["valid"])
        warnings = [i for i in saved["validation"]["issues"] if i["code"] == "missing_support"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual((warnings[0]["severity"], warnings[0]["path"]), ("warning", "team"))
        self.assertNotIn("slot", warnings[0])
        self.assertEqual(saved["data"], before)
        self.assertEqual(self.request("GET", "/api/battles/local_no_support.json")[1]["data"], before)
        self.assertEqual(data, before)

    def test_default_face_slots_formally_roundtrip_and_conflicting_slots_are_rejected(self):
        data = battle()
        data["actions"][0]["attacks"] = [{"isAuto": True, "future": {"keep": [None]}},
                                          {"isAuto": True}, {"svt": 0, "isTD": True, "card": -1}]
        before = copy.deepcopy(data)
        status, saved = self.request("POST", "/api/battles", {"name": "local_auto_cards.json", "data": data})
        self.assertEqual(status, 201, saved)
        self.assertTrue(saved["validation"]["valid"])
        restored = self.request("GET", "/api/battles/local_auto_cards.json")[1]
        self.assertEqual(restored["data"], before)
        data["actions"][0]["attacks"][0]["svt"] = None
        status, rejected = self.request("PUT", "/api/battles/local_auto_cards.json", {
            "data": data, "expectedHash": saved["hash"]})
        self.assertEqual(status, 422, rejected)
        self.assertTrue(any(item["code"] == "conflicting_auto_card"
                            for item in rejected["error"]["details"]["issues"]))
        self.assertEqual(self.request("GET", "/api/battles/local_auto_cards.json")[1]["data"], before)

    def test_same_servant_owned_and_support_can_be_formally_saved_with_alias_identity(self):
        self.server._catalog = {"servants": [{"id": 1002000, "aliases": [1002100], "name": "芙萝拉"}]}
        for support_type in ("friend", "npc", "fixed"):
            with self.subTest(support_type=support_type):
                data = battle()
                data["team"]["onFieldSvts"][0] = {"svtId": 1002000, "supportType": "none"}
                data["team"]["onFieldSvts"][1] = {"svtId": 1002100, "supportType": support_type}
                before = copy.deepcopy(data)
                status, saved = self.request("POST", "/api/battles", {"name": f"local_same_{support_type}.json", "data": data})
                self.assertEqual(status, 201, saved)
                self.assertTrue(saved["validation"]["valid"])
                self.assertFalse(any(i["code"] in ("duplicate_servant", "missing_support") for i in saved["validation"]["issues"]))
                self.assertEqual(saved["data"], before)
                self.assertEqual(data, before)

    def test_official_golden_http_and_draft_roundtrip_preserves_tree_and_single_field_edit(self):
        data = official_golden()
        untouched = copy.deepcopy(data)
        status, created = self.request("POST", "/api/battles", {"name": "local_official_golden.json", "data": data})
        self.assertEqual(status, 201, created)
        self.assertEqual(data, untouched)
        self.assertEqual(created["data"], untouched)
        self.assertEqual(self.request("GET", "/api/battles/local_official_golden.json")[1]["data"], untouched)
        status, draft = self.request("PUT", "/api/drafts/official-golden", {"revision": 0, "data": data, "editorState": {"tab": "turns", "turn": 1}, "sourceName": created["name"], "sourceHash": created["hash"]})
        self.assertEqual(status, 200, draft)
        self.assertEqual(self.request("GET", "/api/drafts/official-golden")[1]["data"], untouched)
        edited = copy.deepcopy(data)
        edited["team"]["name"] = "只修改队名"
        status, saved = self.request("PUT", "/api/battles/local_official_golden.json", {"data": edited, "expectedHash": created["hash"]})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["data"], edited)
        saved["data"]["team"]["name"] = untouched["team"]["name"]
        self.assertEqual(saved["data"], untouched)
        self.assertEqual(json.loads(next(self.server.storage.backup_dir.glob("*.json")).read_text(encoding="utf-8")), untouched)

    def test_editor_new_task_skill_presets_roundtrip_without_other_simulator_defaults(self):
        status, capabilities = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200, capabilities)
        self.assertEqual(capabilities["defaults"]["servant"], {
            "supportType": "none", "skillLvs": [10, 10, 10], "appendLvs": [0, 0, 0, 0, 0],
        })
        self.assertIn("导入", capabilities["defaults"]["description"]["skillLvs"])
        self.assertIn("0 表示不要求", capabilities["defaults"]["description"]["appendLvs"])
        data = copy.deepcopy(DEFAULTS["newBattle"])
        data["team"]["onFieldSvts"] = [copy.deepcopy(DEFAULTS["servant"]) | {"svtId": sid} for sid in (100100, 505300, 402700)]
        data["team"]["mysticCode"] = {"mysticCodeId": 20}
        data["actions"] = [{"type": "skill", "svt": 0, "skill": 0}, {"type": "skill", "skill": 0}, {"type": "attack", "attacks": [{"svt": 0, "isTD": False, "card": 1, "cardType": 1}, {"svt": 1, "isTD": True, "card": -1}, {"svt": 2, "isTD": False, "card": 0, "cardType": 3}]}]
        before = copy.deepcopy(data)
        status, result = self.request("POST", "/api/battles", {"name": "local_editor_minimal.json", "data": data})
        self.assertEqual(status, 201, result)
        self.assertEqual(result["data"], before)
        for member in result["data"]["team"]["onFieldSvts"]:
            self.assertNotIn("lv", member)
            self.assertNotIn("tdLv", member)
            self.assertEqual(member["skillLvs"], [10, 10, 10])
            self.assertEqual(member["appendLvs"], [0, 0, 0, 0, 0])
        self.assertNotIn("level", result["data"]["team"]["mysticCode"])
        self.assertNotIn("options", result["data"]["actions"][0])

    def test_import_save_and_draft_do_not_backfill_existing_skill_fields(self):
        levels = ({}, {"skillLvs": None, "appendLvs": None},
                  {"skillLvs": [], "appendLvs": []},
                  {"skillLvs": [0, 6, 10], "appendLvs": [0, 10, 0]},
                  {"skillLvs": [5, 0, 10], "appendLvs": [0, 10, 0, 2, 0]})
        for support_type in ("none", "friend", "npc"):
            for index, fields in enumerate(levels):
                with self.subTest(support_type=support_type, fields=fields):
                    data = battle()
                    data["team"]["onFieldSvts"][0] = {
                        "svtId": 100100, "supportType": support_type,
                        "extra": {"keep": None}, **copy.deepcopy(fields),
                    }
                    data["team"]["onFieldSvts"][1]["supportType"] = "none"
                    original = copy.deepcopy(data)
                    name = f"skill_{support_type}_{index}"
                    self.server.storage.save_battle(name, data, create=True)
                    status, imported = self.request("POST", "/api/import", {"source": name})
                    self.assertEqual(status, 200, imported)
                    self.assertEqual(imported["data"], original)
                    status, saved = self.request("POST", "/api/battles", {
                        "name": "roundtrip_" + name, "data": imported["data"],
                    })
                    self.assertEqual(status, 201, saved)
                    self.assertEqual(saved["data"], original)
                    self.assertEqual(data, original)
                    if index == 0:
                        status, draft = self.request("PUT", "/api/drafts/" + name, {
                            "revision": 0, "data": imported["data"], "editorState": {"tab": "team"},
                        })
                        self.assertEqual(status, 200, draft)
                        restored = self.request("GET", "/api/drafts/" + name)[1]
                        self.assertEqual(restored["data"], original)
                        member = restored["data"]["team"]["onFieldSvts"][0]
                        self.assertNotIn("skillLvs", member)
                        self.assertNotIn("appendLvs", member)

    def test_new_default_mystic_code_does_not_backfill_existing_imports(self):
        status, capabilities = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200, capabilities)
        self.assertEqual(capabilities["defaults"]["newBattle"]["team"]["mysticCode"], {"mysticCodeId": 210, "level": 10})
        for label, value in (("missing", ...), ("null", None), ("empty", {}), ("custom", {"mysticCodeId": 20})):
            with self.subTest(mystic=label):
                data = battle()
                if value is ...:
                    del data["team"]["mysticCode"]
                else:
                    data["team"]["mysticCode"] = value
                self.server.storage.save_battle("mystic_" + label, data, create=True)
                status, imported = self.request("POST", "/api/import", {"source": "mystic_" + label})
                self.assertEqual(status, 200, imported)
                self.assertEqual(imported["data"], data)

    def test_dynamic_card_color_can_be_formally_saved_with_warning(self):
        data = json.loads((OFFICIAL_FIXTURES / "dynamic_card_color_golden.json").read_text(encoding="utf-8"))
        self.server._catalog = {"servants": [{"id": 100100, "cards": [3, 1, 1, 2, 2]}]}
        status, saved = self.request("POST", "/api/battles", {"name": "local_dynamic_color.json", "data": data})
        self.assertEqual(status, 201, saved)
        self.assertEqual(saved["data"], data)
        self.assertTrue(saved["validation"]["valid"])
        self.assertEqual(len([i for i in saved["validation"]["issues"] if i["code"] == "card_deck_mismatch" and i["severity"] == "warning"]), 2)
        self.assertEqual(self.request("GET", "/api/battles/local_dynamic_color.json")[1]["data"], data)

    def test_static_and_formal_routes_cannot_escape(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        for path in ("/../outside.txt", "/%2e%2e/outside.txt", "/api/battles/%2e%2e%2foutside.json", "/api/drafts/%2e%2e%2foutside", "/media/servant/../secret"):
            with self.subTest(path=path):
                self.assertIn(self.request("GET", path)[0], (400, 403, 404))
        self.assertEqual(self.request("POST", "/api/validate", {"data": battle()}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/import", {"source": "https://example.com/?id=1"})[0], 400)
        self.assertEqual(self.request("POST", "/api/import", {"source": "C:/arbitrary/private.json"})[0], 400)

    def test_rejected_finite_request_bodies_return_http_errors_without_tcp_reset(self):
        # Bodies exceed the header buffer, exercising the Windows unread-data RST
        # failure instead of relying on a tiny request fitting into one read.
        payload = {"name": "local_forbidden.json", "data": battle(), "padding": "x" * 65536}
        for iteration in range(20):
            with self.subTest(iteration=iteration, rejection="content_type"):
                self.assertEqual(self.request("POST", "/api/validate", payload, {"Content-Type": "text/plain"})[0], 415)
            with self.subTest(iteration=iteration, rejection="token"):
                self.assertEqual(self.request("POST", "/api/battles", payload, token=False)[0], 403)
            with self.subTest(iteration=iteration, rejection="origin"):
                self.assertEqual(self.request("POST", "/api/battles", payload, {"Origin": "https://untrusted.example"})[0], 403)
        self.assertEqual(list(self.server.storage.battle_dir.iterdir()), [])

    def test_local_import_does_not_write_a_second_file_or_drop_unknown_fields(self):
        data = battle()
        data["opaque"] = {"a": [None]}
        self.server.storage.save_battle("Grand 中文", data, create=True)
        status, imported = self.request("POST", "/api/import", {"source": "Grand 中文"})
        self.assertEqual(status, 200, imported)
        self.assertEqual(imported["data"], data)
        self.assertEqual(len(list(self.server.storage.battle_dir.iterdir())), 1)

    def test_shutdown_finishes_received_drafts(self):
        data = {"unfinished": [None, {"unknown": "保留"}]}
        self.assertEqual(self.request("PUT", "/api/drafts/shutdown", {"revision": 0, "data": data})[0], 200)
        self.assertEqual(self.request("POST", "/api/shutdown", {})[0], 200)
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        persisted = json.loads((self.root / "config" / "BattleEditor" / "drafts" / "shutdown.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["data"], data)

    def test_offline_chaldea_share_reuses_importer_without_cwd_or_cache_mutation(self):
        shutil.copytree(ROOT / "agent" / "chaldea", self.root / "agent" / "chaldea", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "agent" / "custom").mkdir()
        shutil.copy2(ROOT / "agent" / "custom" / "mfaalog.py", self.root / "agent" / "custom" / "mfaalog.py")
        # bbc_formatter imports servant_aliases, which is inside chaldea itself.
        data = battle()
        data["unknown"] = {"deep": [0, None, "中文"]}
        encoded = "G" + base64.urlsafe_b64encode(gzip.compress(json.dumps(data, ensure_ascii=False).encode())).decode()
        result = self.server.import_source("https://link.chaldea.center/laplace/share?data=" + encoded)
        self.assertEqual(result["data"], data)
        self.assertEqual(list(self.server.storage.battle_dir.iterdir()), [])
        self.assertEqual(Path.cwd(), ROOT)


class RejectedBodyCleanupTests(unittest.TestCase):
    def handler(self, length, body, already_read=0, transfer_encoding=None):
        handler = EditorHandler.__new__(EditorHandler)
        handler.headers = Message()
        if length is not None:
            handler.headers["Content-Length"] = str(length)
        if transfer_encoding:
            handler.headers["Transfer-Encoding"] = transfer_encoding
        handler._body_read = already_read
        handler.rfile = io.BytesIO(body)
        from unittest.mock import Mock
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = 15
        return handler

    def test_only_unread_declared_remainder_is_consumed(self):
        handler = self.handler(9, b"456789NEXT", already_read=3)
        handler._discard_unread_body()
        self.assertEqual(handler._body_read, 9)
        self.assertEqual(handler.rfile.read(), b"NEXT")
        self.assertEqual(handler.connection.settimeout.call_args.args, (15,))

    def test_fully_read_invalid_json_is_not_read_again(self):
        handler = self.handler(4, b"NEXT", already_read=4)
        handler._discard_unread_body()
        self.assertEqual(handler.rfile.read(), b"NEXT")
        handler.connection.settimeout.assert_not_called()

    def test_unknown_oversized_or_chunked_framing_is_never_drained(self):
        for length, transfer in ((None, None), ("invalid", None), (-1, None), (MAX_JSON_BYTES + 1, None), (12, "chunked")):
            with self.subTest(length=length, transfer=transfer):
                handler = self.handler(length, b"UNREAD", transfer_encoding=transfer)
                handler._discard_unread_body()
                self.assertEqual(handler.rfile.read(), b"UNREAD")
                handler.connection.settimeout.assert_not_called()

    def test_slow_sender_only_gets_short_drain_deadline(self):
        handler = self.handler(100, b"")
        from unittest.mock import Mock
        handler.rfile = Mock()
        handler.rfile.read1.side_effect = TimeoutError("slow sender")
        handler._discard_unread_body()
        initial_timeout = handler.connection.settimeout.call_args_list[0].args[0]
        self.assertGreater(initial_timeout, 0)
        self.assertLessEqual(initial_timeout, 0.2)
        self.assertEqual(handler.connection.settimeout.call_args.args, (15,))


class LifecycleTests(unittest.TestCase):
    def static_page(self, directory):
        static = Path(directory) / "web" / "battle-editor"
        static.mkdir(parents=True)
        (static / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")

    def test_vite_source_is_not_mistaken_for_a_built_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            self.static_page(directory)
            (Path(directory) / "web" / "battle-editor" / "src").mkdir()
            with self.assertRaises(FileNotFoundError):
                create_server(directory, 0)

    def test_standalone_idle_exit_and_heartbeat_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            self.static_page(directory)
            server = create_server(directory, 0, idle_timeout=1)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                # Repeated accepted activity extends lifetime past the original deadline.
                for _ in range(4):
                    time.sleep(0.35)
                    server.last_activity = time.monotonic()
                self.assertTrue(thread.is_alive())
                thread.join(3)
                self.assertFalse(thread.is_alive())
            finally:
                server.stop()
                thread.join(3)
                server.server_close()

    def test_owner_creation_time_prevents_pid_reuse(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            self.static_page(directory)
            created = psutil.Process().create_time()
            with self.assertRaises(ValueError):
                create_server(directory, 0, owner_pid=os.getpid(), owner_created=created - 5)
            with self.assertRaises(ValueError):
                create_server(directory, 0, owner_pid=os.getpid())
            server = create_server(directory, 0, owner_pid=os.getpid(), owner_created=created, idle_timeout=1)
            self.assertEqual(server.mode, "mxu")
            self.assertEqual(server.health()["ownerCreated"], created)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
