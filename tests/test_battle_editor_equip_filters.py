"""Offline CE facts preserve real Atlas effect/condition and MLB semantics."""
import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from battle_editor.catalog import build_catalog
from battle_editor.equip_filters import EFFECTS, equip_filter_facts, read_equip_filters


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


builder = module("build_ce_filter_tests", "tools/build_battle_editor_equip_filters.py")
list_builder = module("build_ce_list_tests", "tools/build_equip_list.py")


def function(kind="gainNp", value=8000, buff=None, **changes):
    return {"funcId": 1, "funcType": kind, "funcTargetType": "self", "funcTargetTeam": "player",
            "functvals": [], "funcquestTvals": [], "funcGroup": [], "script": {},
            "buffs": [{"type": buff, "name": buff, "ckSelfIndv": [], "ckOpIndv": []}] if buff else [],
            "svals": [{"Rate": 1000, "Value": value}], **changes}


def skill(identity, functions, limit=0, status=1, num=1, event=0):
    return {"id": identity, "condLimitCount": limit, "strengthStatus": status, "functions": functions,
            "skillSvts": [{"svtId": 9400340, "num": num, "condLimitCount": limit,
                           "priority": 1 if limit == 0 else 2, "eventId": event}]}


def equip(*skills, **changes):
    return {"id": 9400340, "flags": [], "flag": "normal", "atkMax": 2000, "hpMax": 0,
            "skills": list(skills), **changes}


class EquipFilterCompilerTests(unittest.TestCase):
    def test_initial_np_and_mlb_are_separate_and_source_is_unchanged(self):
        data = equip(skill(1, [function(value=8000)]), skill(2, [function(value=10000)], limit=4, status=99))
        original = copy.deepcopy(data)
        result = builder.compile_equip(data)
        self.assertEqual((result["base"]["initialNp"], result["mlb"]["initialNp"]), (80, 100))
        self.assertEqual(result["base"]["tags"], ["npStart"])
        self.assertEqual(data, original)

    def test_old_event_status_two_is_mlb_and_events_stay_conditional(self):
        raw = equip(skill(1, [function(value=3000)]), skill(2, [function(value=5000)], limit=4, status=99),
                    skill(3, [function("addState", 1000, "upDamage")], num=2, event=99, status=3),
                    skill(4, [function("addState", 2000, "upDamage")], num=2, event=99, limit=4, status=2))
        result = builder.compile_equip(raw)
        for basis, expected in (("base", 100), ("mlb", 200)):
            event = next(row for row in result[basis]["effects"] if "eventDamage" in row["tags"])
            self.assertEqual(event["value"], expected)
            self.assertTrue(event["conditional"])
            self.assertIn("#99", event["condition"])
            self.assertNotIn("eventDamage", result[basis]["tags"])

    def test_np_per_turn_and_acquisition_are_not_initial_np(self):
        result = builder.compile_equip(equip(skill(1, [function("addState", 800, "regainNp"),
                                                       function("addState", 250, "upDropnp")])))
        self.assertEqual(result["base"]["initialNp"], 0)
        self.assertEqual(result["base"]["tags"], ["npTurn", "npGain"])
        self.assertEqual([row["value"] for row in result["base"]["effects"]], [8, 25])

    def test_triggered_np_is_conditional_and_never_used_for_starting_threshold(self):
        trigger = function("addState", 123, "deadFunction", svals=[{"Rate": 1000, "Value": 123, "Value2": 2}])
        nested = {123: {"id": 123, "functions": [function(svals=[{"Value": 1000}, {"Value": 2000}])]}}
        result = builder.compile_equip(equip(skill(1, [trigger])), nested)["base"]
        self.assertEqual(result["initialNp"], 0)
        self.assertEqual(result["tags"], [])
        self.assertEqual(result["effects"][0]["tags"], ["npCharge"])
        self.assertEqual(result["effects"][0]["value"], 20)
        self.assertIn("退场", result["effects"][0]["condition"])

    def test_card_traits_are_intrinsic_and_enemy_traits_are_real_conditions(self):
        card = function("addState", 150, "upCommandall")
        card["buffs"][0]["ckSelfIndv"] = [{"id": 4002, "name": "cardBuster"}]
        trait = function("addState", 200, "upDamage")
        trait["buffs"][0]["ckOpIndv"] = [{"id": 103, "name": "classRider"}]
        result = builder.compile_equip(equip(skill(1, [card, trait])))["base"]
        self.assertIn("buster", result["tags"])
        effect = next(row for row in result["effects"] if "specialAttack" in row["tags"])
        self.assertTrue(effect["conditional"])
        self.assertIn("classRider", effect["condition"])
        self.assertNotIn("eventDamage", effect["tags"])

    def test_technical_guts_and_debuff_gates_do_not_hide_normal_effects(self):
        guts = function("addState", 500, "guts", svals=[{"Rate": 1000, "Count": 1, "Value": 500}])
        guts["buffs"][0]["ckOpIndv"] = [{"id": 3139, "name": "unknown", "negative": True}]
        resist = function("addState", 50, "upTolerance")
        resist["buffs"][0]["ckOpIndv"] = [{"id": 3005, "name": "buffNegativeEffect"}]
        result = builder.compile_equip(equip(skill(1, [guts, resist])))["base"]
        self.assertEqual(result["tags"], ["guts", "weakResist"])
        guts["svals"][0]["UseRate"] = 500
        result = builder.compile_equip(equip(skill(1, [guts])))["base"]
        self.assertEqual(result["tags"], [])
        self.assertIn("50%", result["effects"][0]["condition"])

    def test_negative_hp_and_support_only_bonus_do_not_become_unconditional_benefits(self):
        hp = function("addState", 500, "reduceHp")
        bond = function("servantFriendshipUp", svals=[{"RateCount": 10}], followerVals=[{"RateCount": 30}])
        result = builder.compile_equip(equip(skill(1, [hp, bond])))["base"]
        self.assertNotIn("heal", result["tags"])
        records = [row for row in result["effects"] if "bond" in row["tags"]]
        self.assertEqual([(row["value"], row["conditional"]) for row in records], [(1, False), (3, True)])
        self.assertIn("作为助战", records[1]["condition"])

    def test_star_amounts_are_plain_integers_and_unknowns_are_not_zero(self):
        result = builder.compile_equip(equip(skill(1, [function("addState", 8, "regainStar"), function("gainStar", 20)])))["base"]
        self.assertEqual([row["value"] for row in result["effects"]], [8, 20])
        missing = builder.compile_equip(equip(atkMax=None, hpMax=None))
        self.assertEqual(missing["statType"], "unknown")
        self.assertIsNone(missing["base"]["initialNp"])
        self.assertIsNone(missing["atkMax"])
        future = builder.compile_equip(equip(skill(1, [function("futureUnknown", 1)])))
        self.assertIsNone(future["base"]["initialNp"])

    def test_permanent_material_drop_is_not_an_event_bonus(self):
        drop = function("eventDropRateUp", svals=[{"EventId": 0, "Individuality": 20045, "DropRateCount": 50}])
        result = builder.compile_equip(equip(skill(1, [drop])))["base"]
        self.assertEqual(result["tags"], ["materialDrop"])
        self.assertEqual(result["effects"][0]["value"], 5)
        self.assertIn("#20045", result["effects"][0]["target"])
        drop["svals"] = [{"EventId": 99, "Individuality": 10, "AddCount": 200}]
        result = builder.compile_equip(equip(skill(1, [drop])))["base"]
        self.assertEqual(result["tags"], [])
        self.assertEqual(result["effects"][0]["tags"], ["eventDrop"])
        self.assertEqual(result["effects"][0]["value"], 20)

    def test_authoritative_bond_owner_and_scope_never_use_id_prefixes(self):
        data = equip(skill(1, [function("addState", 500, "guts")]), bondEquipOwner=700100)
        original = [{"id": "9400340", "important": 1, "images": ["same.png"], "bond": {"bonus_type": "none"},
                     "acquisition_category_jp": "フレンドポイント（友情）ガチャ", "filter_tag": "通常"}]
        overrides = builder.category_overrides(original, [data])
        adjusted = builder.apply_category_overrides(original, overrides)
        self.assertEqual(adjusted[0]["filter_tag"], "羁绊礼装")
        self.assertEqual({k: v for k, v in adjusted[0].items() if k not in ("filter_tag", "acquisition_category_jp")},
                         {k: v for k, v in original[0].items() if k not in ("filter_tag", "acquisition_category_jp")})
        index = builder.compile_index(original, [data, equip(id=123)])
        self.assertEqual(list(index["equips"]), ["9400340"])
        self.assertEqual(index["equips"]["9400340"]["kind"], "bond")
        self.assertNotIn("bond", index["equips"]["9400340"]["base"]["tags"])
        self.assertEqual(builder.category_overrides([{"id": "9301230"}], [equip(id=9301230)]), {})

    def test_csv_builder_uses_exact_id_authority_before_old_source_category(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            authority = folder / "categories.json"
            authority.write_text(json.dumps({"schemaVersion": 1, "equips": {"9300070": {"acquisition_category_jp": "絆礼装"}}}), encoding="utf-8")
            source, target = folder / "source.csv", folder / "target.json"
            columns = ["equipId", "collectionNo", "name_cn", "name_jp", "rarity", "important", "非高价值礼装", "入手分类(日文)"]
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                for identity in ("9300070", "9300071"):
                    writer.writerow(dict(zip(columns, [identity, "197", "test", "test", "4", "0", "0", "フレンドポイント（友情）ガチャ"])))
            list_builder.build(source, target, categories_path=authority)
            rows = json.loads(target.read_text(encoding="utf-8"))["equips"]
            self.assertEqual([row["filter_tag"] for row in rows], ["羁绊礼装", "通常"])
            # The recorded source hash must survive Git's LF normalization on CI.
            self.assertNotIn(b"\r\n", target.read_bytes())


class BundledEquipFilterTests(unittest.TestCase):
    def test_complete_curated_scope_sources_hash_and_known_real_effects(self):
        index = read_equip_filters(ROOT)
        curated_path = ROOT / "agent/custom/equip_list.json"
        curated = json.loads(curated_path.read_text(encoding="utf-8"))["equips"]
        self.assertEqual(set(index["equips"]), {row["id"] for row in curated})
        self.assertEqual(len(index["equips"]), 1157)
        self.assertEqual(index["version"]["curatedSha256"], hashlib.sha256(curated_path.read_bytes()).hexdigest())
        manifest = json.loads((ROOT / "agent/battle_editor/data/equip_filters_sources.json").read_text(encoding="utf-8"))
        self.assertNotIn(b"\r\n", curated_path.read_bytes())
        self.assertEqual(manifest["version"], index["version"])
        self.assertEqual(manifest["indexSha256"], hashlib.sha256((ROOT / "agent/battle_editor/data/equip_filters.json").read_bytes()).hexdigest())
        for sid, base, mlb in (("9400340", 80, 100), ("9400290", 0, 0), ("9400400", 0, 0)):
            self.assertEqual((index["equips"][sid]["base"]["initialNp"], index["equips"][sid]["mlb"]["initialNp"]), (base, mlb))
        valid_tags = {row[0] for row in EFFECTS}
        for row in index["equips"].values():
            for basis in ("base", "mlb"):
                self.assertLessEqual(set(row[basis]["tags"]), valid_tags)
                self.assertTrue(all(not effect["conditional"] for effect in row[basis]["effects"] if "npStart" in effect["tags"] and effect.get("value") == row[basis]["initialNp"] and effect.get("condition") is None))
        castle = index["equips"]["9300070"]
        self.assertEqual((castle["kind"], castle["bondOwner"]), ("bond", 700100))
        self.assertNotIn("bond", castle["base"]["tags"])
        self.assertNotIn("heal", index["equips"]["9400480"]["base"]["tags"])
        self.assertIn("guts", index["equips"]["9400410"]["base"]["tags"])

    def test_catalog_attaches_only_curated_facts_without_mutating_cached_index(self):
        index = read_equip_filters(ROOT)
        original = copy.deepcopy(index)
        catalog = build_catalog(ROOT)
        self.assertTrue(catalog["equipFilters"]["available"])
        for equip_row in catalog["equips"]:
            if str(equip_row["id"]) in index["equips"]:
                self.assertEqual(equip_row["filters"], index["equips"][str(equip_row["id"])] )
            else:
                self.assertIsNone(equip_row["filters"])
        facts = equip_filter_facts(index, "9400340")
        facts["base"]["tags"].clear()
        self.assertEqual(index, original)


if __name__ == "__main__":
    unittest.main()
