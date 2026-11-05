"""Mash's offline form facts remain grouped without changing her Chaldea identity."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from battle_editor.catalog import _servant_forms, build_catalog, resolve_media

SPEC = importlib.util.spec_from_file_location("mash_catalog_builder", ROOT / "tools/build_battle_editor_catalog.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def nice_mash():
    """Small Atlas-shaped fixture with deliberately unrelated extra facts."""
    return {
        "id": 800100, "cost": 0, "future": {"preserve": [None, False]},
        "skills": [{"id": sid, "num": num} for num, ids in (
            (1, [1000, 236000, 459550, 744450, 2476450]),
            (2, [2000, 460250, 2550]), (3, [133000, 457000, 2162350, 133550])) for sid in ids],
        "noblePhantasms": [{"id": sid} for sid in [800101, 800104, 800105, 800106, 800107]],
        "ascensionAdd": {"rarity": {"ascension": {"2": 3, "4": 4}},
                         "overwriteRarity": {"costume": {"17": 5}},
                         "overwriteCost": {"costume": {"17": 16}}},
        "extraAssets": {"faces": {
            "ascension": {"3": "https://static.atlasacademy.io/JP/Faces/f_8001002.png",
                          "4": "https://static.atlasacademy.io/JP/Faces/f_8001003.png"},
            "costume": {"800140": "https://static.atlasacademy.io/JP/Faces/f_8001400.png",
                        "800190": "https://static.atlasacademy.io/JP/Faces/f_8001900.png"}}},
    }


class MashFormBuildTests(unittest.TestCase):
    def test_presets_preserve_historical_combinations_and_use_actual_cost_overwrites(self):
        record = nice_mash()
        before = copy.deepcopy(record)
        forms = builder.compile_mash_forms(record)
        self.assertEqual(record, before)
        by_key = {form["key"]: form for form in forms}
        self.assertEqual(len(by_key), 6)
        self.assertEqual([(by_key[key]["rarity"], by_key[key]["cost"]) for key in ["early", "normal", "paladin"]],
                         [(3, 0), (4, 0), (5, 16)])
        self.assertEqual(by_key["early"]["skillIds"], [1000, 2000, 133000])
        self.assertEqual(by_key["normal"]["skillIds"], [236000, 2000, 133000])
        self.assertEqual(by_key["ortenaus"]["skillIds"], [459550, 460250, 457000])
        self.assertEqual(by_key["black-barrel"]["skillIds"], [744450, 460250, 457000])
        self.assertEqual(by_key["ortenaus-restored"]["skillIds"], [744450, 460250, 2162350])
        self.assertEqual(by_key["paladin"]["skillIds"], [2476450, 2550, 133550])
        self.assertEqual([by_key[key]["tdId"] for key in by_key],
                         [800101, 800104, 800105, 800105, 800106, 800107])
        self.assertTrue(set([17, 800190, 19, 800200, 20, 800210]) <= set(by_key["paladin"]["limitCounts"]))
        same_limit = [form for form in forms if form["limitCount"] == 800140]
        self.assertEqual([form["key"] for form in same_limit if form["defaultForLimit"]], ["ortenaus-restored"])

    def test_incomplete_cache_does_not_invent_missing_skill_np_or_cost(self):
        for mutate in [lambda row: row["skills"].pop(), lambda row: row["noblePhantasms"].pop(),
                       lambda row: row["ascensionAdd"]["overwriteCost"]["costume"].clear()]:
            with self.subTest(mutate=mutate):
                record = nice_mash()
                mutate(record)
                with self.assertRaises(ValueError):
                    builder.compile_mash_forms(record)

    def test_incremental_update_preserves_every_other_field_and_is_idempotent(self):
        catalog = {"version": {"pinned": 1}, "servants": [
            {"id": 800100, "np": {"id": 800197}, "skillVariants": [{"id": 970660}],
             "future": [None, False, {"keep": True}]}, {"id": 100100, "skills": [{"id": 1}]}],
                   "equips": [{"id": 900}], "mysticCodes": [{"id": 210}]}
        before, source = copy.deepcopy(catalog), nice_mash()
        exports = {"JP/nice_servant": [source]}
        self.assertTrue(builder.update_mash_forms(catalog, exports))
        self.assertFalse(builder.update_mash_forms(catalog, exports))
        target = catalog["servants"][0]
        self.assertEqual(target["defaultFormKey"], "normal")
        self.assertEqual(target.pop("forms"), builder.compile_mash_forms(source))
        target.pop("defaultFormKey")
        self.assertEqual(catalog, before)

    def test_partial_update_command_needs_only_jp_cache_and_does_not_rewrite_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, output = root / "cache", root / "output"
            cache.mkdir();output.mkdir()
            (cache / "JP_nice_servant.json").write_text(json.dumps([nice_mash()]), encoding="utf-8")
            target = output / "catalog.json"
            target.write_text(json.dumps({"servants": [{"id": 800100}], "future": [None]}), encoding="utf-8")
            command = [sys.executable, str(ROOT / "tools/build_battle_editor_catalog.py"),
                       "--mash-forms-only", "--cache-dir", str(cache), "--output-dir", str(output)]
            subprocess.run(command, check=True, capture_output=True)
            before, stat = target.read_bytes(), target.stat().st_mtime_ns
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(target.stat().st_mtime_ns, stat)

    def test_runtime_drops_incomplete_presets_without_allowing_arbitrary_media_paths(self):
        record = nice_mash()
        forms = builder.compile_mash_forms(record)
        skills = {row["id"]: row for row in record["skills"]}
        nps = {row["id"]: row for row in record["noblePhantasms"]}
        original = copy.deepcopy(forms)
        forms[0]["image"] = "../../player-file.png"
        forms[1]["cost"] = True
        with tempfile.TemporaryDirectory() as directory:
            joined = _servant_forms(Path(directory), {"forms": forms}, skills, nps, {}, None, {})
        self.assertEqual(len(joined), 5)
        self.assertNotIn("normal", [row["key"] for row in joined])
        self.assertIsNone(joined[0]["image"])
        self.assertTrue(all(row["portrait"] is None for row in joined))
        self.assertEqual(forms[2:], original[2:])

    def test_full_portraits_require_exact_form_mapping_and_registered_local_media(self):
        record = nice_mash()
        forms = builder.compile_mash_forms(record)
        # A portrait supplied by the source facts cannot bypass the UI manifest.
        forms[0]["portrait"] = "formation_800190_0000000000000000.webp"
        skills = {row["id"]: row for row in record["skills"]}
        nps = {row["id"]: row for row in record["noblePhantasms"]}
        name = "formation_800100_0000000000000000.webp"
        unregistered = "formation_800190_0000000000000000.webp"
        webp = bytes.fromhex("524946461e000000574542505650384c110000002f0180000007509e6654abff8188e87f0000")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ui = root / "agent/battle_editor/data/ui"
            (ui / "media").mkdir(parents=True)
            (ui / "media" / name).write_bytes(webp)
            (ui / "media" / unregistered).write_bytes(webp)
            (ui / "manifest.json").write_text(json.dumps({"schema": 1, "files": {name: {"width": 2, "height": 3}}}), encoding="utf-8")
            joined = _servant_forms(root, {"forms": forms}, skills, nps, {}, None, {}, {
                "early": name, "normal": "../../player-file.png", "paladin": unregistered})
            by_key = {row["key"]: row for row in joined}
            self.assertEqual(by_key["early"]["portrait"], f"/media/ui/{name}")
            self.assertEqual((by_key["early"]["portraitWidth"], by_key["early"]["portraitHeight"]), (2, 3))
            self.assertIsNone(by_key["normal"]["portrait"])
            self.assertIsNone(by_key["paladin"]["portrait"])
            self.assertIsNone(by_key["ortenaus"]["portrait"])


class BundledMashFormsTests(unittest.TestCase):
    def test_offline_catalog_forms_resolve_exact_fact_ids_and_whitelisted_local_images(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Catalog must stay offline")):
            catalog = build_catalog(ROOT)
        mash = next(row for row in catalog["servants"] if row["id"] == 800100)
        self.assertEqual(mash["defaultFormKey"], "normal")
        self.assertEqual(len(mash["forms"]), 6)
        skills, nps = {row["id"]: row for row in mash["skillVariants"]}, {row["id"]: row for row in mash["npVariants"]}
        manifest = json.loads((ROOT / "agent/battle_editor/data/ui/manifest.json").read_text(encoding="utf-8"))
        portraits = manifest["servants"]["800100"].get("formPortraits", {})
        for form in mash["forms"]:
            with self.subTest(form=form["key"]):
                self.assertEqual([skills[sid]["num"] for sid in form["skillIds"]], [1, 2, 3])
                self.assertEqual(nps[form["tdId"]]["cardType"], 1)
                for field, kind in [("image", "servant"), ("classIcon", "ui")]:
                    self.assertTrue(form[field].startswith(f"/media/{kind}/"))
                    self.assertIsNotNone(resolve_media(ROOT, kind, form[field].rsplit("/", 1)[1]))
                expected = portraits.get(form["key"])
                if expected:
                    self.assertEqual(form["portrait"], f"/media/ui/{expected}")
                    self.assertIsNotNone(resolve_media(ROOT, "ui", expected))
                    size = manifest["files"][expected]
                    self.assertEqual((form["portraitWidth"], form["portraitHeight"]), (size["width"], size["height"]))
                else:
                    self.assertIsNone(form["portrait"])
        # There is still one catalog entry and no synthetic playable Mash IDs.
        self.assertEqual([row["id"] for row in catalog["servants"] if row["collectionNo"] == 1], [800100])


if __name__ == "__main__":
    unittest.main()
