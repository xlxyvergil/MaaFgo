"""Offline facts, identity normalization and media containment contract."""
import importlib.util
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from urllib.parse import unquote
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from battle_editor.catalog import _dimensions, build_catalog, resolve_media

SPEC = importlib.util.spec_from_file_location("editor_catalog_build", ROOT / "tools/build_battle_editor_catalog.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

# Real 2 x 3 encoded images: lossy, lossless and extended (with an alpha chunk).
WEBP_FIXTURES = [bytes.fromhex(raw) for raw in (
    "524946463a00000057454250565038202e000000f001009d012a0200030000c01225880274ba01f800044c0000fefd2145ffcb3e7f0d9d4e7ff29545aa6dc5000000",
    "524946461e000000574542505650384c110000002f0180000007509e6654abff8188e87f0000",
    "524946465c00000057454250565038580a00000010000000010000020000414c504807000000005a5a5a5a5a5a00565038202e000000f001009d012a0200030000c01225880274ba01f800044c0000fefd2145ffcb3e7f0d9d4e7ff29545aa6dc5000000",
)]


class EditorCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("agent/custom/servant_list.json", {
            "class_cn": {"saber": "剑"}, "servants": [
                {"id": "100", "aliases": ["200"], "collection_no": 1, "name": "从者", "class": "saber", "rarity": 5, "images": ["f_1000.png"]},
                {"id": "200", "name": "旧形态"},
            ]})
        self.write("agent/custom/equip_list.json", {"equips": [{"id": "900", "name": "识别礼装", "images": ["f_9000.png"]}]})
        self.write("agent/utils/Chaldea/equip_names_CN.json", {"900": "礼装", "901": "完整名称库中的礼装"})
        self.write("config/Inventory/player_servants.json", {"servants": [{"id": "200"}], "scan_complete": True})
        self.write("agent/battle_editor/data/catalog.json", {
            "version": {"schema": 1, "atlas": {"JP": {"hash": "test"}}},
            "servants": [{"id": 100, "cards": [3, 1, 1, 2, 2], "skills": [{"id": 1, "name": "技能", "icon": "skill_1.png", "target": "ally"}]}],
            "equips": [{"id": 902, "nameJp": "未实装礼装", "rarity": 5}],
            "mysticCodes": [{"id": 20, "name": "换人服", "skills": [], "orderChange": True, "image": "master.png"}],
        })
        self.image("assets/resource/base/image/servant_face/f_1000.png")
        self.image("assets/resource/base/image/EquipFaces/team/f_9000.png")
        self.image("agent/battle_editor/data/media/skill/skill_1.png")

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def image(self, path):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(builder.PNG_MAGIC)

    def sized_image(self, path, width, height):
        self.image(path)
        (self.root / path).write_bytes(builder.PNG_MAGIC + b"\0\0\0\rIHDR" + struct.pack(">II", width, height))

    def test_complete_names_identity_inventory_and_reused_media(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Runtime must stay offline")):
            result = build_catalog(self.root)
        self.assertEqual(len(result["servants"]), 1)
        servant = result["servants"][0]
        self.assertEqual(servant["id"], 100)
        self.assertEqual(servant["cards"], [3, 1, 1, 2, 2])
        self.assertEqual(servant["image"], "/media/servant/f_1000.png")
        self.assertEqual(servant["skills"][0]["icon"], "/media/skill/skill_1.png")
        self.assertEqual(result["inventory"]["servants"], [100])
        self.assertTrue(result["inventory"]["servantsStatus"]["scanComplete"])
        self.assertFalse(result["inventory"]["equipsStatus"]["available"])
        equips = {e["id"]: e for e in result["equips"]}
        self.assertEqual(set(equips), {900, 901, 902})
        self.assertTrue(equips[900]["recognizable"])
        self.assertEqual(equips[900]["image"], "/media/equip/f_9000.png")
        self.assertFalse(equips[901]["recognizable"])
        self.assertIsNone(equips[901]["image"])
        self.assertEqual(equips[902]["name"], "未实装礼装")
        self.assertFalse(equips[902]["recognizable"])

    def test_cost_index_exposes_zero_and_canonical_facts_without_expanding_selection(self):
        self.write("agent/custom/bond_completion_costs.json", {
            "schema_version": 1, "servants": {"100": 0, "200": 16, "999": 12},
            "equips": {"900": 9, "901": 0, "902": 100, "999": 12}})
        result = build_catalog(self.root)
        self.assertEqual([(item["id"], item["cost"]) for item in result["servants"]], [(100, 0)])
        equips = {item["id"]: item for item in result["equips"]}
        self.assertEqual({eid: item["cost"] for eid, item in equips.items()}, {900: 9, 901: 0, 902: 100})
        self.assertTrue(equips[900]["recognizable"])
        self.assertFalse(equips[901]["recognizable"])
        self.assertFalse(equips[902]["recognizable"])

    def test_bundled_cost_index_covers_every_canonical_servant_and_preserves_special_costs(self):
        catalog = build_catalog(ROOT)
        servants = {item["id"]: item for item in catalog["servants"]}
        costs = json.loads((ROOT / "agent/custom/bond_completion_costs.json").read_text(encoding="utf-8"))["servants"]
        self.assertGreater(len(servants), 450)
        for sid, servant in servants.items():
            with self.subTest(servant=sid):
                self.assertIn(str(sid), costs)
                self.assertIs(type(servant["cost"]), int)
                self.assertGreaterEqual(servant["cost"], 0)
                self.assertLessEqual(servant["cost"], 100)
                self.assertEqual(servant["cost"], costs[str(sid)])
        for sid, cost in ((1700100, 16), (705400, 16), (800100, 0), (1100100, 4)):
            self.assertEqual(servants[sid]["cost"], cost)
        # The stale Hassan record is a registered Flora form, not a third gap.
        self.assertNotIn(1002100, servants)
        self.assertIn(1002100, servants[1002000]["aliases"])
        self.assertEqual(servants[1002000]["cost"], 12)

    def test_servant_cost_falls_back_to_declared_alias_only_when_canonical_fact_is_missing(self):
        for canonical in (None, True, "0", -1, 101):
            with self.subTest(canonical=canonical):
                self.write("agent/custom/bond_completion_costs.json", {
                    "servants": {"100": canonical, "200": 0}, "equips": {}})
                servant = build_catalog(self.root)["servants"][0]
                self.assertEqual(servant["cost"], 0)
        self.write("agent/custom/bond_completion_costs.json", {
            "servants": {"100": 16, "200": 0}, "equips": {}})
        self.assertEqual(build_catalog(self.root)["servants"][0]["cost"], 16)

    def test_missing_or_malformed_cost_tables_do_not_guess_from_rarity_or_other_metadata(self):
        self.write("agent/custom/servant_list.json", {"servants": [
            {"id": "100", "name": "有星级但未知COST", "rarity": 5, "cost": 16}]})
        self.write("agent/custom/equip_list.json", {"equips": [
            {"id": "900", "name": "有星级但未知COST", "rarity": 5, "cost": 12}]})
        self.write("agent/battle_editor/data/catalog.json", {
            "servants": [{"id": 100, "cost": 16}], "equips": [{"id": 902, "rarity": 5, "cost": 12}]})
        tables = [None, [], True, "bad", {}, {"servants": [], "equips": []},
                  {"servants": "bad", "equips": False},
                  {"servants": {"100": None}, "equips": {"900": None}}]
        for table in tables:
            with self.subTest(table=table):
                self.write("agent/custom/bond_completion_costs.json", table)
                result = build_catalog(self.root)
                self.assertTrue(all(item["cost"] is None for item in result["servants"]))
                self.assertTrue(all(item["cost"] is None for item in result["equips"]))
        cost_file = self.root / "agent/custom/bond_completion_costs.json"
        cost_file.write_text("{malformed", encoding="utf-8")
        self.assertIsNone(build_catalog(self.root)["servants"][0]["cost"])
        cost_file.unlink()
        self.assertIsNone(build_catalog(self.root)["servants"][0]["cost"])

    def test_cost_values_reject_boolean_string_fractional_and_out_of_bounds_facts(self):
        for invalid in (True, False, "0", "16", 1.5, -1, 101, [], {}):
            with self.subTest(invalid=invalid):
                self.write("agent/custom/bond_completion_costs.json", {
                    "servants": {"100": invalid}, "equips": {"900": invalid}})
                result = build_catalog(self.root)
                self.assertIsNone(result["servants"][0]["cost"])
                self.assertTrue(all(item["cost"] is None for item in result["equips"]))

    def test_packaged_resource_layout_and_path_rejection(self):
        self.image("resource/base/image/servant_face/packaged.png")
        self.assertEqual(resolve_media(self.root, "servant", "packaged.png"),
                         (self.root / "resource/base/image/servant_face/packaged.png").resolve())
        for kind, name in (("servant", "../f_1000.png"), ("servant", "C:\\secret.png"),
                           ("skill", "%2e%2e.png"), ("equip", "file.png:stream"),
                           ("config", "f_1000.png"), ("servant", "f_1000.json")):
            with self.subTest(kind=kind, name=name):
                self.assertIsNone(resolve_media(self.root, kind, name))

    def test_equip_reuses_team_template_and_ignores_dump_ui_overrides(self):
        self.sized_image("assets/resource/base/image/EquipFaces/team/f_9000.png", 152, 86)
        self.sized_image("assets/resource/base/image/EquipFaces/list/f_9000.png", 147, 56)
        self.sized_image("assets/resource/base/image/EquipFaces/f_9000.png", 90, 40)
        self.sized_image("agent/battle_editor/data/media/equip/f_9000.png", 95, 45)
        self.sized_image("agent/battle_editor/data/ui/media/dump-equip.png", 150, 68)
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
            "files": {"dump-equip.png": {"width": 150, "height": 68}},
            "equips": {"900": {"image": "dump-equip.png"}}})
        equip = next(item for item in build_catalog(self.root)["equips"] if item["id"] == 900)
        self.assertTrue(equip["recognizable"])
        self.assertEqual((equip["image"], equip["imageWidth"], equip["imageHeight"]),
                         ("/media/equip/f_9000.png", 152, 86))
        self.assertEqual(resolve_media(self.root, "equip", "f_9000.png"),
                         (self.root / "assets/resource/base/image/EquipFaces/team/f_9000.png").resolve())

    def test_missing_team_template_cannot_be_supplied_by_list_root_or_editor_copies(self):
        (self.root / "assets/resource/base/image/EquipFaces/team/f_9000.png").unlink()
        for directory in ("assets/resource/base/image/EquipFaces/list", "assets/resource/base/image/EquipFaces",
                          "resource/base/image/EquipFaces/list", "resource/base/image/EquipFaces",
                          "agent/battle_editor/data/media/equip"):
            self.image(directory + "/f_9000.png")
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
            "files": {"dump-equip.png": {}}, "equips": {"900": {"image": "dump-equip.png"}}})
        self.image("agent/battle_editor/data/ui/media/dump-equip.png")
        equip = next(item for item in build_catalog(self.root)["equips"] if item["id"] == 900)
        self.assertEqual(equip["name"], "识别礼装")
        self.assertFalse(equip["recognizable"])
        self.assertIsNone(equip["image"])
        self.assertEqual((equip["imageWidth"], equip["imageHeight"]), (None, None))
        self.assertIsNone(resolve_media(self.root, "equip", "f_9000.png"))

    def test_packaged_team_templates_are_used_without_editor_image_fallbacks(self):
        (self.root / "assets/resource/base/image/EquipFaces/team/f_9000.png").unlink()
        self.sized_image("resource/base/image/EquipFaces/team/f_9000.png", 152, 86)
        self.sized_image("agent/battle_editor/data/media/equip/f_9000.png", 150, 68)
        equip = next(item for item in build_catalog(self.root)["equips"] if item["id"] == 900)
        self.assertTrue(equip["recognizable"])
        self.assertEqual((equip["image"], equip["imageWidth"], equip["imageHeight"]),
                         ("/media/equip/f_9000.png", 152, 86))
        self.assertEqual(resolve_media(self.root, "equip", "f_9000.png"),
                         (self.root / "resource/base/image/EquipFaces/team/f_9000.png").resolve())

    def test_catalog_retains_old_equip_names_without_marking_unrecognized_metadata_selectable(self):
        self.write("agent/utils/Chaldea/equip_names_CN.json", {"900": "礼装", "901": "旧方案礼装"})
        self.write("agent/utils/Chaldea/equip_names_JP.json", {"903": "旧日本語礼装"})
        self.image("assets/resource/base/image/EquipFaces/team/f_9010.png")
        self.image("agent/battle_editor/data/ui/media/dump-only.png")
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
            "files": {"dump-only.png": {}}, "equips": {"902": {"image": "dump-only.png"}}})
        equips = {item["id"]: item for item in build_catalog(self.root)["equips"]}
        self.assertEqual(set(equips), {900, 901, 902, 903})
        self.assertEqual(equips[901]["name"], "旧方案礼装")
        self.assertEqual(equips[902]["name"], "未实装礼装")
        self.assertEqual(equips[903]["nameJp"], "旧日本語礼装")
        for eid in (901, 902, 903):
            with self.subTest(eid=eid):
                self.assertFalse(equips[eid]["recognizable"])
        self.assertIsNone(equips[902]["image"])
        self.assertIsNone(equips[903]["image"])

    def test_recognized_equip_uses_declared_variant_filename_without_guessing_its_id(self):
        self.write("agent/custom/equip_list.json", {"equips": [{"id": "900", "name": "变体礼装",
            "images": ["f_9000.png", "f_9000_1_2_3_1.png"]}]})
        (self.root / "assets/resource/base/image/EquipFaces/team/f_9000.png").unlink()
        self.sized_image("assets/resource/base/image/EquipFaces/team/f_9000_1_2_3_1.png", 152, 86)
        equip = next(item for item in build_catalog(self.root)["equips"] if item["id"] == 900)
        self.assertTrue(equip["recognizable"])
        self.assertEqual(equip["image"], "/media/equip/f_9000_1_2_3_1.png")
        self.assertEqual((equip["imageWidth"], equip["imageHeight"]), (152, 86))

    def test_status_portraits_reuse_exact_id_with_safe_flat_urls(self):
        self.image("assets/resource/base/image/ServantStatus/100/status_servant_1.png")
        self.image("assets/resource/base/image/ServantStatus/100/status_servant_3.png")
        self.image("assets/resource/base/image/ServantStatus/200/status_servant_3.png")
        catalog = build_catalog(self.root)
        self.assertEqual(catalog["servants"][0]["statusImage"], "/media/status/status_100_3.png")
        self.assertEqual(resolve_media(self.root, "status", "status_100_3.png"),
                         (self.root / "assets/resource/base/image/ServantStatus/100/status_servant_3.png").resolve())
        (self.root / "assets/resource/base/image/ServantStatus/100/status_servant_3.png").unlink()
        self.assertEqual(build_catalog(self.root)["servants"][0]["statusImage"], "/media/status/status_100_1.png")
        (self.root / "assets/resource/base/image/ServantStatus/100/status_servant_1.png").unlink()
        # A known alias's portrait must not silently supply another exact ID.
        self.assertIsNone(build_catalog(self.root)["servants"][0]["statusImage"])

    def test_status_portraits_support_release_layout_and_reject_paths(self):
        self.image("resource/base/image/ServantStatus/100/status_servant_2.png")
        self.assertEqual(build_catalog(self.root)["servants"][0]["statusImage"], "/media/status/status_100_2.png")
        for name in ("status_servant_2.png", "status_0100_2.png", "status_100_02.png", "status_-100_2.png",
                     "status_100_../2.png", "../100/status_servant_2.png", "status_100_2.png:stream",
                     "status_100_2%2epng", "status_100_2.PNG", "status_100_0.png"):
            with self.subTest(name=name):
                self.assertIsNone(resolve_media(self.root, "status", name))

    def test_battle_ui_assets_only_expose_manifest_listed_images(self):
        fields = {"masterSkill": "master.png", "masterFrame": "frame.png", "masterEquipFrame": "equip.png"}
        files = {name: {} for name in [*fields.values(), "arts.png", "buster.png", "quick.png"]}
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1, "files": files,
            "common": {**fields, "npFrames": {"arts": "arts.png", "buster": "buster.png", "quick": "quick.png",
                                                  "private": "private.png"}}})
        for name in files:
            self.image("agent/battle_editor/data/ui/media/" + name)
        assets = build_catalog(self.root)["uiAssets"]
        self.assertEqual(assets["npFrames"], {color: f"/media/ui/{color}.png" for color in ("arts", "buster", "quick")})
        for key, name in fields.items():
            self.assertEqual(assets[key], f"/media/ui/{name}")
        self.assertIsNone(assets["masterSkillDisabled"])

    def test_alias_forms_keep_all_skill_and_np_variants_in_one_selector_entry(self):
        path = self.root / "agent/battle_editor/data/catalog.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["servants"].append({"id": 200, "cards": [3, 1, 1, 2, 2],
            "skillVariants": [{"id": 99, "name": "形态技能", "target": "none"}],
            "npVariants": [{"id": 123, "name": "形态宝具", "cardType": 1}]})
        self.write("agent/battle_editor/data/catalog.json", data)
        result = build_catalog(self.root)
        self.assertEqual(len(result["servants"]), 1)
        servant = result["servants"][0]
        self.assertEqual(servant["id"], 100)
        self.assertEqual(servant["skills"][0]["id"], 1)
        self.assertEqual(servant["skillVariants"][0]["id"], 99)
        self.assertEqual(servant["npVariants"][0]["id"], 123)

    def test_advanced_filters_preserve_source_forms_and_np_pairs_with_independent_copies(self):
        local_path = self.root / "agent/custom/servant_list.json"
        local = json.loads(local_path.read_text(encoding="utf-8"))
        bond = {"tags": ["lawful", "female"], "alignment": "lawful", "attribute": "",
                "tags_by_ascension": {"1-2": ["lawful_good"], "3-4": ["lawful_summer"]},
                "tags_by_costume": ["beast_species"], "future": {"raw": [None, 7]}}
        variants = [
            {"treasureDeviceId": 101, "type": "辅助宝具", "card": "Arts", "conditions_jp": ["通常時"]},
            {"treasureDeviceId": 102, "type": "群体输出", "card": "Buster", "conditions_jp": ["聖剣装填時"], "future": {"raw": [1]}},
            {"treasureDeviceId": 103, "type": "群体输出", "card": "Buster"},
        ]
        local["servants"][0].update(bond=bond, np_type="单体输出", np_card="Quick", np_variants=variants)
        source_path = self.root / "agent/battle_editor/data/catalog.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["servants"][0].update(np={"id": 999, "name": "执行用宝具", "cardType": 3},
                                      npVariants=[{"id": 999, "cardType": 3}])
        from battle_editor import catalog as catalog_module
        original_read = catalog_module._read

        def shared_read(path, fallback):
            return local if path == local_path else source if path == source_path else original_read(path, fallback)

        with patch.object(catalog_module, "_read", side_effect=shared_read):
            servant = build_catalog(self.root)["servants"][0]
        self.assertEqual(servant["bond"], bond)
        self.assertEqual(servant["npFilter"], {"types": ["辅助宝具", "群体输出"], "cards": ["Arts", "Buster"], "variants": variants})
        self.assertEqual({(v["type"], v["card"]) for v in servant["npFilter"]["variants"]},
                         {("辅助宝具", "Arts"), ("群体输出", "Buster")})
        self.assertEqual(servant["np"], {"id": 999, "name": "执行用宝具", "cardType": 3})
        self.assertEqual(servant["npVariants"], [{"id": 999, "cardType": 3}])
        servant["bond"]["tags"].append("evil")
        servant["bond"]["tags_by_ascension"]["1-2"].clear()
        servant["bond"]["future"]["raw"].append(8)
        servant["npFilter"]["variants"][0]["conditions_jp"].clear()
        servant["npFilter"]["variants"][1]["future"]["raw"].append(2)
        self.assertEqual(bond["tags"], ["lawful", "female"])
        self.assertEqual(bond["tags_by_ascension"]["1-2"], ["lawful_good"])
        self.assertEqual(bond["future"]["raw"], [None, 7])
        self.assertEqual(variants[0]["conditions_jp"], ["通常時"])
        self.assertEqual(variants[1]["future"]["raw"], [1])

    def test_advanced_filters_never_borrow_alias_or_atlas_classifications(self):
        local_path = self.root / "agent/custom/servant_list.json"
        local = json.loads(local_path.read_text(encoding="utf-8"))
        local["servants"][1].update(bond={"tags": ["female"]}, np_type="群体输出", np_card="Buster",
            np_variants=[{"treasureDeviceId": 201, "type": "群体输出", "card": "Buster"}])
        self.write("agent/custom/servant_list.json", local)
        source_path = self.root / "agent/battle_editor/data/catalog.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["servants"][0].update(bond={"tags": ["male"]}, np_type="单体输出", np_card="Quick",
            np_variants=[{"treasureDeviceId": 101, "type": "单体输出", "card": "Quick"}])
        self.write("agent/battle_editor/data/catalog.json", source)
        servant = build_catalog(self.root)["servants"][0]
        self.assertIsNone(servant["bond"])
        self.assertEqual(servant["npFilter"], {"types": [], "cards": [], "variants": []})

    def test_advanced_np_summaries_are_reference_only_without_variants(self):
        local_path = self.root / "agent/custom/servant_list.json"
        local = json.loads(local_path.read_text(encoding="utf-8"))
        for fields, expected in [
            ({}, {"types": [], "cards": [], "variants": []}),
            ({"np_type": "待补充", "np_card": "", "np_variants": []},
             {"types": ["待补充"], "cards": [], "variants": []}),
            ({"np_type": " 单体输出/群体输出/单体输出 ", "np_card": "Arts/Buster/Arts", "np_variants": []},
             {"types": ["单体输出", "群体输出"], "cards": ["Arts", "Buster"], "variants": []}),
            ({"np_type": "群体输出", "np_card": "Buster", "np_variants": [{"type": "待补充", "card": ""}]},
             {"types": [], "cards": [], "variants": [{"type": "待补充", "card": ""}]}),
            ({"np_type": "群体输出", "np_card": "Buster", "np_variants": [None, {"type": [], "card": {}}]},
             {"types": [], "cards": [], "variants": [None, {"type": [], "card": {}}]}),
        ]:
            with self.subTest(fields=fields):
                canonical = local["servants"][0]
                for key in ("np_type", "np_card", "np_variants"):
                    canonical.pop(key, None)
                canonical.update(copy.deepcopy(fields))
                self.write("agent/custom/servant_list.json", local)
                self.assertEqual(build_catalog(self.root)["servants"][0]["npFilter"], expected)

    def test_missing_supplement_does_not_invent_cards_skills_or_mystics(self):
        (self.root / "agent/battle_editor/data/catalog.json").unlink()
        result = build_catalog(self.root)
        self.assertEqual(result["servants"][0]["cards"], [])
        self.assertEqual(result["servants"][0]["skills"], [])
        self.assertEqual(result["mysticCodes"], [])
        self.assertFalse(result["version"]["supplementAvailable"])

    def test_common_equips_match_both_template_forms_and_width_equivalent_names_only(self):
        self.write("agent/utils/Chaldea/equip_names_CN.json", {"900": "限制/零毁", "901": "未收录"})
        for path in ("满破/限制／零毁.png", "非满破/限制／零毁.png", "满破/未收.png", "满破/0_空.png"):
            self.image("assets/resource/base/image/lizhuang/" + path)
        result = build_catalog(self.root)
        equips = {item["id"]: item for item in result["equips"]}
        self.assertTrue(equips[900]["common"])
        self.assertEqual(len(equips[900]["commonSources"]), 2)
        self.assertFalse(equips[901]["common"])
        self.assertEqual(result["commonEquips"]["count"], 1)
        self.assertEqual(result["commonEquips"]["unmatched"], ["assets/resource/base/image/lizhuang/满破/未收.png"])

    def test_ambiguous_common_names_are_never_assigned_to_a_first_candidate(self):
        self.write("agent/utils/Chaldea/equip_names_CN.json", {"900": "同名", "901": "同名"})
        self.image("resource/base/image/lizhuang/满破/同名.png")
        result = build_catalog(self.root)
        self.assertFalse(any(item["common"] for item in result["equips"]))
        self.assertEqual(result["commonEquips"]["ambiguous"][0]["ids"], [900, 901])

    def test_support_recognition_is_independent_of_team_image_and_display_name(self):
        equip = next(e for e in build_catalog(self.root)["equips"] if e["id"] == 900)
        self.assertTrue(equip["recognizable"])
        self.assertEqual(equip["supportRecognition"], {"limitBreak": False, "nonLimitBreak": False})
        # 运行时使用 Chaldea 名称“礼装”，不是本地条目的显示名“识别礼装”。
        self.image("assets/resource/base/image/lizhuang/满破/识别礼装.png")
        equip = next(e for e in build_catalog(self.root)["equips"] if e["id"] == 900)
        self.assertFalse(equip["supportRecognition"]["limitBreak"])
        self.image("assets/resource/base/image/lizhuang/非满破/礼装.png")
        equip = next(e for e in build_catalog(self.root)["equips"] if e["id"] == 900)
        self.assertEqual(equip["supportRecognition"], {"limitBreak": False, "nonLimitBreak": True})
        self.image("assets/resource/base/image/lizhuang/满破/礼装.png")
        equip = next(e for e in build_catalog(self.root)["equips"] if e["id"] == 900)
        self.assertEqual(equip["supportRecognition"], {"limitBreak": True, "nonLimitBreak": True})

    def test_support_filenames_normalize_alnum_and_slashes_not_all_punctuation(self):
        self.write("agent/utils/Chaldea/equip_names_CN.json", {"900": "来自ＮＦＦ的爱/测试\\A"})
        self.image("assets/resource/base/image/lizhuang/满破/来自NFF的爱／测试＼A.png")
        equip = next(e for e in build_catalog(self.root)["equips"] if e["id"] == 900)
        self.assertTrue(equip["supportRecognition"]["limitBreak"])
        # 名称缓存缺失不能使用显示名猜测，即使模板恰好存在。
        self.write("agent/utils/Chaldea/equip_names_CN.json", {})
        equip = next(e for e in build_catalog(self.root)["equips"] if e["id"] == 900)
        self.assertFalse(any(equip["supportRecognition"].values()))

    def test_packaged_support_template_directory_has_same_priority_as_executor(self):
        self.image("assets/resource/base/image/lizhuang/满破/礼装.png")
        self.image("resource/base/image/lizhuang/非满破/礼装.png")
        equip = next(e for e in build_catalog(self.root)["equips"] if e["id"] == 900)
        self.assertEqual(equip["supportRecognition"], {"limitBreak": False, "nonLimitBreak": True})

    def test_ui_manifest_provides_dimensions_append_and_exact_active_skill_ids(self):
        files = {name: {"width": width, "height": height}
                 for name, width, height in (("portrait.png", 150, 376), ("equip.png", 150, 68),
                                              ("class.png", 64, 64), ("append.png", 128, 128), ("skill.png", 128, 128))}
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1, "files": files,
            "servants": {"100": {"portrait": "portrait.png", "activeSkills": [{"id": 1, "icon": "skill.png"}],
                "appendSkills": [{"id": 100 + num, "num": num, "name": f"追加技能 {num}", "icon": "append.png"}
                                 for num in range(1, 6)]}},
            "equips": {"900": {"image": "equip.png"}}, "classes": {"saber": "class.png"}})
        catalog_path = self.root / "agent/battle_editor/data/catalog.json"
        raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        raw_catalog["servants"][0]["skillVariants"] = [{"id": 2, "num": 1, "icon": "skill_1.png"}]
        self.write("agent/battle_editor/data/catalog.json", raw_catalog)
        for name, record in files.items():
            self.sized_image("agent/battle_editor/data/ui/media/" + name, record["width"], record["height"])
        result = build_catalog(self.root)
        servant = result["servants"][0]
        self.assertEqual(servant["portrait"], "/media/ui/portrait.png")
        self.assertEqual((servant["portraitWidth"], servant["portraitHeight"]), (150, 376))
        self.assertEqual(servant["skills"][0]["icon"], "/media/ui/skill.png")
        self.assertEqual(servant["skills"][0]["target"], "ally")
        self.assertEqual(servant["skillVariants"][0]["icon"], "/media/skill/skill_1.png")
        self.assertEqual(servant["appendSkills"], [{"id": 100 + num, "num": num, "name": f"追加技能 {num}", "icon": "/media/ui/append.png"}
                                                  for num in range(1, 6)])
        equip = next(item for item in result["equips"] if item["id"] == 900)
        self.assertEqual((equip["image"], equip["imageWidth"], equip["imageHeight"]),
                         ("/media/equip/f_9000.png", None, None))
        self.assertEqual(result["uiAssets"]["classes"], {"saber": "/media/ui/class.png"})

    def test_ui_media_requires_manifest_membership_and_flat_safe_filename(self):
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1, "files": {"allowed.png": {}}})
        self.image("agent/battle_editor/data/ui/media/allowed.png")
        self.image("agent/battle_editor/data/ui/media/private.png")
        self.assertEqual(resolve_media(self.root, "ui", "allowed.png"),
                         (self.root / "agent/battle_editor/data/ui/media/allowed.png").resolve())
        for name in ("private.png", "../allowed.png", "%2e%2e.png", "C:allowed.png", "sub/allowed.png"):
            with self.subTest(name=name):
                self.assertIsNone(resolve_media(self.root, "ui", name))

    def test_webp_is_only_allowed_for_manifest_listed_flat_ui_formation_portraits(self):
        allowed = "formation_100_0123456789abcdef.webp"
        rejected = ["formation_100_fedcba9876543210.webp", "other.webp", "formation_100.webp",
                    "formation_0100_0123456789abcdef.webp", "formation_0_0123456789abcdef.webp",
                    "formation_100_0123456789abcde.webp", "formation_100_0123456789ABCDEf.webp",
                    "formation_100_0123456789abcdef.WEBP", "../" + allowed, "sub/" + allowed,
                    "sub\\" + allowed, allowed + ":stream", "%2e%2e%2f" + allowed,
                    "C:" + allowed, allowed + "\x00"]
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
                   "files": {name: {} for name in [allowed, *rejected[1:8]]}})
        directory = self.root / "agent/battle_editor/data/ui/media"
        directory.mkdir(parents=True)
        for name in [allowed, *rejected[:8]]:
            (directory / name).write_bytes(WEBP_FIXTURES[0])
        self.assertEqual(resolve_media(self.root, "ui", allowed), (directory / allowed).resolve())
        for name in rejected:
            with self.subTest(name=name):
                self.assertIsNone(resolve_media(self.root, "ui", name))
        for kind in ("servant", "equip", "narrow", "skill", "mystic", "status", "unknown"):
            with self.subTest(kind=kind):
                self.assertIsNone(resolve_media(self.root, kind, allowed))

    def test_webp_dimensions_use_actual_riff_headers_for_all_three_encodings(self):
        name = "formation_100_0123456789abcdef.webp"
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
            "files": {name: {"width": 999, "height": 999}},
            "servants": {"100": {"formationPortrait": name}}})
        directory = self.root / "agent/battle_editor/data/ui/media"
        directory.mkdir(parents=True)
        for raw in WEBP_FIXTURES:
            with self.subTest(chunk=raw[12:16]):
                (directory / name).write_bytes(raw)
                servant = build_catalog(self.root)["servants"][0]
                self.assertEqual(servant["portrait"], f"/media/ui/{name}")
                self.assertEqual((servant["portraitWidth"], servant["portraitHeight"]), (2, 3))

    def test_webp_dimensions_reject_truncation_bad_headers_and_unbounded_canvas(self):
        name = "formation_100_0123456789abcdef.webp"
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1, "files": {name: {}}})
        directory = self.root / "agent/battle_editor/data/ui/media"
        directory.mkdir(parents=True)
        lossy, lossless, extended = WEBP_FIXTURES
        malformed = [b"", lossy[:24], lossy[:-1], lossy + b"trailing", b"NOPE" + lossy[4:],
                     lossy[:8] + b"NOPE" + lossy[12:], lossy[:12] + b"JUNK" + lossy[16:],
                     lossy[:16] + struct.pack("<I", 1000000) + lossy[20:],
                     lossy[:23] + b"bad" + lossy[26:],
                     lossy[:26] + b"\x00\x00" + lossy[28:],
                     lossless[:24] + b"\xe0" + lossless[25:],
                     extended[:24] + b"\xff\xff\xff" + extended[27:]]
        for raw in malformed:
            with self.subTest(raw=raw[:30].hex()):
                (directory / name).write_bytes(raw)
                self.assertEqual(_dimensions(self.root, f"/media/ui/{name}"), (None, None))

    def test_missing_webp_formation_uses_legacy_png_portrait(self):
        name = "formation_100_0123456789abcdef.webp"
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
            "files": {name: {}, "narrow.png": {}},
            "servants": {"100": {"formationPortrait": name, "portrait": "narrow.png"}}})
        self.sized_image("agent/battle_editor/data/ui/media/narrow.png", 150, 376)
        servant = build_catalog(self.root)["servants"][0]
        self.assertEqual(servant["portrait"], "/media/ui/narrow.png")
        self.assertEqual((servant["portraitWidth"], servant["portraitHeight"]), (150, 376))

    def test_manifest_listed_webp_symlink_cannot_escape_the_install(self):
        name = "formation_100_0123456789abcdef.webp"
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1, "files": {name: {}}})
        directory = self.root / "agent/battle_editor/data/ui/media"
        directory.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / name
            external.write_bytes(WEBP_FIXTURES[0])
            link = directory / name
            try:
                link.symlink_to(external)
            except OSError as exc:
                self.skipTest(f"This Windows account cannot create symlinks: {exc}")
            self.assertIsNone(resolve_media(self.root, "ui", name))

    def test_full_formation_portrait_is_preferred_and_uses_actual_image_dimensions(self):
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
            # Exposed dimensions must come from the selected PNG, not cached metadata.
            "files": {"formation.png": {"width": 1, "height": 1}, "narrow.png": {"width": 150, "height": 376}},
            "servants": {"100": {"formationPortrait": "formation.png", "portrait": "narrow.png"}}})
        self.sized_image("agent/battle_editor/data/ui/media/formation.png", 512, 724)
        self.sized_image("agent/battle_editor/data/ui/media/narrow.png", 150, 376)
        servant = build_catalog(self.root)["servants"][0]
        self.assertEqual(servant["portrait"], "/media/ui/formation.png")
        self.assertEqual((servant["portraitWidth"], servant["portraitHeight"]), (512, 724))
        self.assertEqual(servant["image"], "/media/servant/f_1000.png")

    def test_unavailable_full_formation_portrait_falls_back_to_narrow_then_unknown(self):
        self.sized_image("agent/battle_editor/data/ui/media/narrow.png", 150, 376)
        self.sized_image("agent/battle_editor/data/ui/media/unlisted.png", 512, 724)
        for candidate in ("missing.png", "unlisted.png", "../unlisted.png", None):
            with self.subTest(candidate=candidate):
                self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
                    "files": {"missing.png": {}, "narrow.png": {}},
                    "servants": {"100": {"formationPortrait": candidate, "portrait": "narrow.png"}}})
                servant = build_catalog(self.root)["servants"][0]
                self.assertEqual(servant["portrait"], "/media/ui/narrow.png")
                self.assertEqual((servant["portraitWidth"], servant["portraitHeight"]), (150, 376))
        (self.root / "agent/battle_editor/data/ui/media/narrow.png").unlink()
        servant = build_catalog(self.root)["servants"][0]
        self.assertIsNone(servant["portrait"])
        self.assertEqual((servant["portraitWidth"], servant["portraitHeight"]), (None, None))
        self.assertEqual(servant["image"], "/media/servant/f_1000.png")

    def test_missing_ui_file_falls_back_to_existing_icon_and_preserves_unknown_image(self):
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1, "files": {"missing.png": {}},
            "servants": {"100": {"portrait": "missing.png", "activeSkills": [{"id": 1, "icon": "missing.png"}]}},
            "equips": {"901": {"image": "missing.png"}}})
        result = build_catalog(self.root)
        self.assertIsNone(result["servants"][0]["portrait"])
        self.assertEqual(result["servants"][0]["skills"][0]["icon"], "/media/skill/skill_1.png")
        self.assertIsNone(next(item for item in result["equips"] if item["id"] == 901)["image"])

    def test_servant_class_color_uses_project_rarity_and_selector_remains_gold(self):
        files = {name: {} for name in ("copper.png", "silver.png", "gold.png", "zero.png")}
        variants = {"0": "zero.png", "1": "copper.png", "2": "copper.png", "3": "silver.png",
                    "4": "gold.png", "5": "gold.png"}
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1, "files": files,
            "classes": {"saber": "gold.png"}, "classVariants": {"saber": variants},
            # An export's four-star appearance must not override project rarity.
            "servants": {"100": {"rarity": 4}}})
        for filename in files:
            self.image("agent/battle_editor/data/ui/media/" + filename)
        local_path = self.root / "agent/custom/servant_list.json"
        local = json.loads(local_path.read_text(encoding="utf-8"))
        for rarity, expected in ((0, "zero"), (1, "copper"), (2, "copper"), (3, "silver"), (4, "gold"), (5, "gold")):
            with self.subTest(rarity=rarity):
                local["servants"][0]["rarity"] = rarity
                self.write("agent/custom/servant_list.json", local)
                catalog = build_catalog(self.root)
                self.assertEqual(catalog["servants"][0]["classIcon"], f"/media/ui/{expected}.png")
                self.assertEqual(catalog["uiAssets"]["classes"]["saber"], "/media/ui/gold.png")

    def test_unavailable_rarity_variant_falls_back_to_same_class_gold(self):
        self.write("agent/battle_editor/data/ui/manifest.json", {"schema": 1,
            "files": {"gold.png": {}, "missing.png": {}}, "classes": {"saber": "gold.png"},
            "classVariants": {"saber": {"5": "missing.png"}}})
        self.image("agent/battle_editor/data/ui/media/gold.png")
        result = build_catalog(self.root)
        self.assertEqual(result["servants"][0]["classIcon"], "/media/ui/gold.png")

    def test_media_symlink_cannot_escape_the_install(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "private.png"
            external.write_bytes(builder.PNG_MAGIC)
            link = self.root / "agent/battle_editor/data/media/skill/escape.png"
            try:
                link.symlink_to(external)
            except OSError as exc:
                self.skipTest(f"This Windows account cannot create symlinks: {exc}")
            self.assertIsNone(resolve_media(self.root, "skill", link.name))

    def test_target_classification_uses_function_data_not_names(self):
        self.assertEqual(builder.skill_target({"functions": [{"funcTargetType": "ptOne"} ]})[0], "ally")
        self.assertEqual(builder.skill_target({"functions": [{"funcTargetType": "ptRandom"} ]})[0], "none")
        self.assertEqual(builder.skill_target({"functions": [{"funcTargetType": "ptselectOneSub"} ]})[0], "unknown")
        self.assertEqual(builder.skill_target({"functions": [{"funcTargetType": "ptAnother"} ]})[0], "unknown")
        self.assertEqual(builder.skill_target({"functions": [{"funcTargetType": "self", "funcType": "skillSelect"} ]})[0], "unknown")
        self.assertEqual(builder.skill_target({"functions": []})[0], "unknown")

    def test_export_compilation_keeps_real_deck_and_localizes_only_matching_ids(self):
        exports = {
            "JP/nice_servant": [{"id": 100, "cards": ["3", "1", "1", "2", "2"],
                "skills": [{"id": 11, "num": 1, "priority": 1, "name": "旧技能", "functions": [{"funcTargetType": "self"}]},
                           {"id": 12, "num": 1, "priority": 2, "name": "強化技能", "functions": [{"funcTargetType": "ptOne"}]}],
                "noblePhantasms": [{"id": 21, "name": "宝具", "card": "2"}]}],
            "CN/nice_servant": [{"id": 100, "skills": [{"id": 11, "name": "中文旧技能"}]}],
            "JP/nice_mystic_code": [],
        }
        compiled, media = builder.compile_catalog(exports, [{"id": "100"}])
        servant = compiled["servants"][0]
        self.assertEqual(servant["cards"], [3, 1, 1, 2, 2])
        self.assertEqual(servant["skills"][0]["id"], 12)
        self.assertEqual(servant["skills"][0]["target"], "ally")
        self.assertEqual(servant["skillVariants"][0]["name"], "中文旧技能")
        self.assertEqual(servant["skills"][0]["name"], "強化技能")
        self.assertEqual(servant["np"]["cardType"], 2)
        self.assertEqual(media, {})

    def test_download_integrity_rejects_truncated_or_corrupted_png(self):
        def chunk(kind, data):
            return len(data).to_bytes(4, "big") + kind + data + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
        image = builder.PNG_MAGIC + chunk(b"IHDR", b"\0\0\0\1\0\0\0\1\x08\x06\0\0\0")
        image += chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0")) + chunk(b"IEND", b"")
        self.assertTrue(builder.complete_png(image))
        self.assertFalse(builder.complete_png(image[:-1]))
        self.assertFalse(builder.complete_png(image[:40] + bytes([image[40] ^ 1]) + image[41:]))

    def test_default_np_prefers_effectful_candidates_but_preserves_every_exact_variant(self):
        actual = {"id": 21, "name": "実宝具", "card": "2", "priority": 101,
                  "functions": [{"funcId": 12, "funcType": "damageNp"}]}
        placeholder = {"id": 99, "name": "？？？", "card": "2", "priority": 199, "functions": []}
        exports = {"JP/nice_servant": [{"id": 100, "noblePhantasms": [actual, placeholder]}],
                   "CN/nice_servant": [{"id": 100, "noblePhantasms": [{"id": 21, "name": "实际宝具"}]}],
                   "JP/nice_mystic_code": []}
        before = copy.deepcopy(exports)
        compiled, _ = builder.compile_catalog(exports, [{"id": "100"}])
        servant = compiled["servants"][0]
        self.assertEqual(servant["np"]["id"], 21)
        self.assertEqual(servant["np"]["name"], "实际宝具")
        self.assertEqual(servant["npVariants"], [builder.compact_np(actual, {21: {"name": "实际宝具"}}),
                                                builder.compact_np(placeholder, {})])
        self.assertEqual(exports, before)
        # The runtime join retains even an explicitly selectable placeholder.
        self.write("agent/battle_editor/data/catalog.json", compiled)
        joined = build_catalog(self.root)["servants"][0]
        self.assertEqual(joined["np"]["id"], 21)
        self.assertEqual(next(np for np in joined["npVariants"] if np["id"] == 99)["name"], "？？？")

    def test_effectful_default_np_keeps_original_priority_and_id_ties_without_name_blacklists(self):
        candidates = [{"id": 1, "name": "旧宝具", "priority": 100, "functions": [{"funcId": 1}]},
                      {"id": 2, "name": "强化宝具", "priority": 101, "functions": [{"funcId": 2}]},
                      {"id": 3, "name": "？？？", "priority": 101, "functions": [{"funcId": 3}]},
                      {"id": 99, "name": "空壳", "priority": 999, "functions": []}]
        self.assertEqual(builder.select_default_np(candidates, {})["id"], 3)
        self.assertEqual(builder.select_default_np(candidates, {})["name"], "？？？")

    def test_all_empty_np_functions_fall_back_to_old_priority_and_id_or_none(self):
        candidates = [{"id": 1, "priority": 101, "functions": []},
                      {"id": 2, "priority": 101}, {"id": 3, "priority": 100, "functions": None}]
        self.assertEqual(builder.select_default_np(candidates, {})["id"], 2)
        self.assertIsNone(builder.select_default_np([], {}))

    def test_incremental_np_patch_only_changes_default_fields_and_keeps_cached_sources_untouched(self):
        exports = {"JP/nice_servant": [{"id": 100, "noblePhantasms": [
            {"id": 21, "priority": 101, "functions": [{"funcId": 12}]},
            {"id": 99, "priority": 199, "functions": []}]}]}
        catalog = {"version": {"pinned": [1, 2]}, "servants": [
            {"id": 100, "np": {"id": 99}, "npVariants": [{"id": 21, "future": [None]}, {"id": 99}],
             "skills": [{"id": 11}], "future": {"original": True}},
            {"id": 200, "np": {"id": 201}, "future": [3]}], "equips": [{"id": 900}]}
        before, sources_before = copy.deepcopy(catalog), copy.deepcopy(exports)
        changes = builder.update_np_defaults(catalog, exports)
        self.assertEqual([item["svtId"] for item in changes], [100])
        before["servants"][0]["np"] = builder.select_default_np(exports["JP/nice_servant"][0]["noblePhantasms"], {})
        self.assertEqual(catalog, before)
        self.assertEqual(exports, sources_before)
        self.assertEqual(builder.update_np_defaults(catalog, exports), [])

    def test_np_defaults_only_command_is_offline_incremental_and_does_not_rewrite_an_unchanged_catalog(self):
        cache, output = self.root / "cache", self.root / "output"
        self.write("cache/JP_nice_servant.json", [{"id": 100, "noblePhantasms": [
            {"id": 21, "functions": [{"funcId": 12}], "priority": 101},
            {"id": 99, "functions": [], "priority": 199}]}])
        self.write("cache/CN_nice_servant.json", [])
        original = {"version": {"unchanged": True}, "servants": [{"id": 100, "np": {"id": 99},
                    "npVariants": [{"id": 21}, {"id": 99}]}], "equips": [{"future": None}]}
        self.write("output/catalog.json", original)
        cached_before = {path.name: path.read_bytes() for path in cache.iterdir()}
        command = [sys.executable, str(ROOT / "tools/build_battle_editor_catalog.py"), "--np-defaults-only",
                   "--cache-dir", str(cache), "--output-dir", str(output)]
        first = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(json.loads(first.stdout)["updatedNpDefaults"], 1)
        path = output / "catalog.json"
        saved, stamp = path.read_bytes(), path.stat().st_mtime_ns
        second = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(json.loads(second.stdout)["updatedNpDefaults"], 0)
        self.assertEqual(path.read_bytes(), saved)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual({file.name for file in output.iterdir()}, {"catalog.json"})
        self.assertEqual({file.name: file.read_bytes() for file in cache.iterdir()}, cached_before)
        changed = json.loads(saved)
        original["servants"][0]["np"] = changed["servants"][0]["np"]
        self.assertEqual(changed, original)
        for download_flag in ("--refresh", "--download-images"):
            rejected = subprocess.run([*command, download_flag], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("cannot download or refresh", rejected.stderr)
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(path.stat().st_mtime_ns, stamp)


class BundledCatalogTests(unittest.TestCase):
    def test_bundled_advanced_fields_keep_canonical_local_classifications(self):
        local = json.loads((ROOT / "agent/custom/servant_list.json").read_text(encoding="utf-8"))
        by_id = {int(item["id"]): item for item in local["servants"]}
        catalog = build_catalog(ROOT)
        self.assertGreater(len(catalog["servants"]), 450)
        for servant in catalog["servants"]:
            with self.subTest(servant=servant["id"]):
                source = by_id[servant["id"]]
                self.assertEqual(servant["bond"], source.get("bond"))
                self.assertEqual(servant["npFilter"]["variants"], source.get("np_variants", []))
                self.assertTrue(servant["bond"])
                self.assertTrue(servant["npFilter"]["variants"])
        selected = {servant["id"]: servant for servant in catalog["servants"]}
        for sid, pairs in [
            (800100, {("辅助宝具", "Arts"), ("群体输出", "Buster")}),
            (304800, {("单体输出", "Arts"), ("群体输出", "Buster")}),
            (205000, {("单体输出", "Buster"), ("群体输出", "Arts")}),
        ]:
            self.assertEqual({(variant["type"], variant["card"]) for variant in selected[sid]["npFilter"]["variants"]}, pairs)

    def test_bundled_ui_catalog_joins_exact_id_and_all_exposed_media_is_whitelisted(self):
        directory = ROOT / "agent/battle_editor/data/ui"
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        catalog = build_catalog(ROOT)
        self.assertGreater(len(manifest["files"]), 600)
        self.assertEqual(manifest["equips"], {})
        self.assertFalse(any(filename.startswith("f_") for filename in manifest["files"]))
        for filename, record in manifest["files"].items():
            path = resolve_media(ROOT, "ui", filename)
            self.assertIsNotNone(path, filename)
            raw = path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), record["sha256"], filename)
            if filename.endswith(".webp"):
                self.assertTrue(filename.startswith("formation_"), filename)
                self.assertEqual(raw[:4], b"RIFF", filename)
                self.assertEqual(raw[8:12], b"WEBP", filename)
                self.assertEqual(_dimensions(ROOT, f"/media/ui/{filename}"),
                                 (record["width"], record["height"]), filename)
            else:
                self.assertTrue(builder.complete_png(raw), filename)
                self.assertEqual(struct.unpack(">II", raw[16:24]), (record["width"], record["height"]), filename)
        for servant in catalog["servants"]:
            source = manifest["servants"].get(str(servant["id"]), {})
            self.assertEqual([(skill["id"], skill["num"]) for skill in servant["appendSkills"]],
                             [(skill["id"], skill["num"]) for skill in source.get("appendSkills", [])])
            variants = manifest.get("classVariants", {}).get(servant["classKey"], {})
            expected_class = variants.get(str(servant["rarity"])) or manifest["classes"].get(servant["classKey"])
            self.assertEqual(servant["classIcon"], f"/media/ui/{expected_class}" if expected_class else None)
            expected_portrait = next((name for name in (source.get("formationPortrait"), source.get("portrait"))
                                      if isinstance(name, str) and resolve_media(ROOT, "ui", name)), None)
            self.assertEqual(servant["portrait"], f"/media/ui/{expected_portrait}" if expected_portrait else None)
            expected_size = manifest["files"][expected_portrait] if expected_portrait else {}
            self.assertEqual((servant["portraitWidth"], servant["portraitHeight"]),
                             (expected_size.get("width"), expected_size.get("height")))
            if servant["id"] in {1700100, 9935530}:
                self.assertIsNone(servant["statusImage"])
            else:
                self.assertIsNotNone(servant["statusImage"], servant["id"])
                self.assertTrue(servant["statusImage"].startswith(f"/media/status/status_{servant['id']}_"))
        self.assertEqual(set(catalog["uiAssets"]["npFrames"]), {"arts", "buster", "quick"})
        self.assertTrue(all(catalog["uiAssets"]["npFrames"].values()))
        self.assertTrue(catalog["uiAssets"]["masterSkill"])
        self.assertFalse(catalog["commonEquips"]["unmatched"])
        self.assertFalse(catalog["commonEquips"]["ambiguous"])
        self.assertGreater(catalog["commonEquips"]["count"], 40)
        self.assertTrue(all(equip["image"] for equip in catalog["equips"]
                            if equip["common"] and equip["recognizable"]))
        self.assertEqual(sum(equip["common"] and equip["recognizable"]
                             for equip in catalog["equips"]), 47)
        recognized = json.loads((ROOT / "agent/custom/equip_list.json").read_text(encoding="utf-8"))["equips"]
        self.assertEqual(len(recognized), 1157)
        expected_equip_ids = {int(row["id"]) for row in recognized
                              if any(resolve_media(ROOT, "equip", image) for image in row.get("images", []))}
        self.assertEqual({equip["id"] for equip in catalog["equips"] if equip["recognizable"]}, expected_equip_ids)
        for equip in catalog["equips"]:
            if equip["recognizable"]:
                self.assertTrue(equip["image"].startswith("/media/equip/"))
                self.assertEqual((equip["imageWidth"], equip["imageHeight"]), (152, 86))
            else:
                self.assertIsNone(equip["image"])

        def exposed_urls(value):
            if isinstance(value, dict):
                for item in value.values():
                    yield from exposed_urls(item)
            elif isinstance(value, list):
                for item in value:
                    yield from exposed_urls(item)
            elif isinstance(value, str) and value.startswith("/media/"):
                yield value

        for url in set(exposed_urls(catalog)):
            pieces = url.split("/")
            self.assertEqual(len(pieces), 4, url)
            self.assertIsNotNone(resolve_media(ROOT, pieces[2], unquote(pieces[3])), url)

    def test_bundled_new_servant_and_high_value_equips_keep_exact_bindings(self):
        catalog = build_catalog(ROOT)
        servant = next(item for item in catalog["servants"] if item["id"] == 406000)
        self.assertEqual((servant["collectionNo"], servant["classKey"], servant["cost"]),
                         (483, "rider", 16))
        self.assertEqual((servant["portraitWidth"], servant["portraitHeight"]), (358, 507))
        self.assertEqual(len(servant["skills"]), 3)
        self.assertEqual(len(servant["appendSkills"]), 5)
        self.assertEqual((servant["np"]["id"], servant["np"]["cardType"]), (406001, 1))

        new_ids = {9407580, 9409520, 9312570, 9312620}
        selected = [item for item in catalog["equips"] if item["id"] in new_ids]
        self.assertEqual({item["id"] for item in selected}, new_ids)
        local = json.loads((ROOT / "agent/custom/equip_list.json").read_text(encoding="utf-8"))
        for item in selected:
            with self.subTest(equip=item["id"]):
                self.assertTrue(item["recognizable"])
                self.assertFalse(item["common"])
                record = next(row for row in local["equips"] if int(row["id"]) == item["id"])
                self.assertEqual(record["important"], 0)
                self.assertIsNotNone(resolve_media(ROOT, "equip", f"f_{item['id']}0.png"))
        self.assertEqual(catalog["commonEquips"]["count"], 47)
        journey = next(item for item in catalog["equips"] if item["id"] == 9400360)
        # 旅途的开始是四星特例，不能按普通四星礼装误计为9 COST。
        self.assertEqual(journey["cost"], 3)
        self.assertTrue(journey["recognizable"] and journey["common"])
        self.assertEqual(journey["supportRecognition"], {"limitBreak": True, "nonLimitBreak": True})
        record = next(row for row in local["equips"] if row["id"] == "9400360")
        self.assertEqual(record["important"], 1)
        self.assertEqual(record["bond"]["bonus_type"], "none")

    def test_bundled_servants_have_official_decks_and_media_hashes_match(self):
        directory = ROOT / "agent/battle_editor/data"
        catalog = json.loads((directory / "catalog.json").read_text(encoding="utf-8"))
        sources = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["version"], sources["version"])
        self.assertGreater(len(catalog["servants"]), 450)
        self.assertGreater(len(catalog["equips"]), 2500)
        self.assertGreaterEqual(len(catalog["mysticCodes"]), 20)
        local = json.loads((ROOT / "agent/custom/servant_list.json").read_text(encoding="utf-8"))["servants"]
        self.assertTrue({int(row["id"]) for row in local} <= {row["id"] for row in catalog["servants"]})
        for servant in catalog["servants"]:
            with self.subTest(servant=servant["id"]):
                self.assertEqual(len(servant["cards"]), 5)
                self.assertTrue(all(type(card) is int and card in {1, 2, 3} for card in servant["cards"]))
        for entry in sources["media"]:
            path = (directory / "media" / entry["file"]).resolve()
            self.assertTrue(path.is_relative_to(directory.resolve()))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
            self.assertTrue(builder.complete_png(path.read_bytes()))


if __name__ == "__main__":
    unittest.main()
