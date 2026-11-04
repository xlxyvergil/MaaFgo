"""Exact NP/face bindings, form boundaries, provenance and offline rebuilding."""
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from battle_editor.np_card_metadata import NpMetadataError, resolve_face_card, resolve_np_card

SPEC = importlib.util.spec_from_file_location("np_card_builder", ROOT / "tools/build_battle_editor_np_cards.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def png(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (builder.PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x80\x90\xa0\xff" * width) * height))
            + chunk(b"IEND", b""))


class NpMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "config/BattleEditor/catalog-build"
        self.target = self.root / "agent/battle_editor/data/np_cards.json"
        self.write("agent/custom/servant_list.json", {"servants": [{"id": 100, "aliases": [200]}, {"id": 300}, {"id": 400}]})
        self.write("agent/battle_editor/data/catalog.json", {"servants": []})
        self.portraits = {"region": "JP", "servants": {}}
        self.records, self.manifests = {}, {}
        for sid in (100, 200, 400):
            entries = self.portraits["servants"][str(sid)] = []
            for variant, group, key in ((1, "ascension", "1"), (2, "ascension", "2"), (3, "ascension", "3"), (11, "costume", str(sid * 1000 + 130))):
                file = f"{sid}/card_servant_{variant}.png"
                source = self.url("JP", sid, Path(file).name)
                entry = self.asset("base", file, 256, 256) | {"group": group, "key": key, "source": source}
                entries.append(entry)
        self.write("assets/resource/base/image/CommandCards/manifest", self.portraits)
        for region, pack in (("JP", "base"), ("CN", "cn")):
            manifest = self.manifests[region] = {"schema_version": 2, "region": region, "shared": {}, "np_names": {}, "np_extra_names": {}}
            for color in ("arts", "buster", "quick"):
                for stem, size in (("card_bg_" + color, (133, 170)), ("card_bg_np" + color, (140, 177)),
                                   ("card_icon_" + color, (185, 133)), ("card_txt_" + color, (150, 73))):
                    manifest["shared"][stem] = self.asset(pack, "_composition/shared/" + stem + ".png", *size) | {"file": "shared/" + stem + ".png"}
            records = self.records[region] = []
            for sid in (100, 200, 300, 400):
                if region == "CN" and sid == 400:
                    continue
                commands = {group: {e["key"]: e["source"] for e in self.portraits["servants"].get(str(sid), []) if e["group"] == group}
                            for group in ("ascension", "costume")}
                record = {"id": sid, "extraAssets": {"commands": commands}, "script": {}, "noblePhantasms": [
                    {"id": sid + 1, "name": f"NP {sid + 1}", "card": "1", "icon": self.url(region, sid, "card_servant_np.png"),
                     "npSvts": [{"svtId": sid, "imageIndex": 0}]}]}
                if sid == 100:
                    record["script"] = {"transformInfo": {"saveTransform": 200}}
                    record["noblePhantasms"] += [
                        {"id": 102, "name": "lower row", "card": "2", "icon": self.url(region, sid, "card_servant_np.png"),
                         "npSvts": [{"svtId": sid, "imageIndex": 1}]},
                        {"id": 103, "name": "extra file", "card": "3", "icon": self.url(region, sid, "card_servant_np_103.png"),
                         "npSvts": [{"svtId": sid, "imageIndex": 2}]}]
                    record["ascensionAdd"] = {"overWriteTDFileName": {
                        "ascension": {str(lim): self.url(region, sid, "100110.png") for lim in (3, 4)},
                        "costume": {"11": self.url(region, sid, "100110.png")}}}
                records.append(record)
                if sid == 300:
                    continue
                for filename in (["card_servant_np.png", "card_servant_np_103.png", "100110.png"] if sid == 100 else ["card_servant_np.png"]):
                    file = f"np/{sid}/{filename}"
                    entry = self.asset(pack, "_composition/" + file, 256, 256) | {
                        "file": file, "source": self.url(region, sid, filename), "name_rows": [0, 1] if sid == 100 else [0]}
                    if filename == "card_servant_np.png":
                        manifest["np_names"][str(sid)] = entry
                    else:
                        manifest["np_extra_names"].setdefault(str(sid), {})[filename] = entry
            self.flush_source(region)
        self.build()

    @staticmethod
    def url(region, sid, filename):
        return f"https://static.atlasacademy.io/{region}/Servants/Commands/{sid}/{filename}"

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def asset(self, pack, relative, width, height):
        path = self.root / f"assets/resource/{pack}/image/CommandCards" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = png(width, height)
        path.write_bytes(raw)
        return {"file": relative, "sha256": hashlib.sha256(raw).hexdigest(), "size": [width, height], "source": "fixture"}

    def flush_source(self, region):
        pack = "base" if region == "JP" else "cn"
        self.write(f"assets/resource/{pack}/image/CommandCards/_composition/manifest", self.manifests[region])
        self.write(f"config/BattleEditor/catalog-build/{region}_nice_servant.json", self.records[region])

    def build(self):
        self.data = builder.NpMetadataBuilder(self.root, self.cache).build()
        builder.write_atomic(self.target, self.data)

    def binding(self, **kwargs):
        return resolve_np_card(self.root, svt_id=100, td_id=101, region="JP", **kwargs)

    def assert_error(self, code, callback):
        with self.assertRaises(NpMetadataError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_exact_np_id_controls_color_file_and_image_index_row(self):
        first = self.binding()
        lower = resolve_np_card(self.root, svt_id=100, td_id=102, region="JP")
        extra = resolve_np_card(self.root, svt_id=100, td_id=103, region="JP")
        self.assertEqual((first.color, first.np_name_row, first.portrait_source), ("arts", 0, "preview-default"))
        self.assertEqual((lower.color, lower.np_name_row, lower.np_name_file), ("buster", 1, "card_servant_np.png"))
        self.assertEqual((extra.color, extra.np_name_row, extra.np_name_file), ("quick", 0, "card_servant_np_103.png"))
        self.assert_error("np_unknown_td", lambda: resolve_np_card(self.root, svt_id=100, td_id=201, region="JP"))

    def test_chaldea_limit_counts_resolve_exact_displayed_stage(self):
        for limit, variant in ((0, 1), (1, 2), (2, 2), (3, 3), (4, 3)):
            with self.subTest(limit=limit):
                binding = self.binding(limit_count=limit)
                self.assertEqual(binding.portrait_variant, variant)
                self.assertEqual(binding.portrait_source, "limit-count")
        costume = self.binding(limit_count=100130)
        self.assertEqual((costume.portrait_variant, costume.np_name_file), (11, "100110.png"))
        self.assert_error("np_portrait_unmapped", lambda: self.binding(limit_count=11))
        self.assert_error("np_portrait_unmapped", lambda: self.binding(limit_count=999999))
        self.assert_error("np_portrait_unmapped", lambda: self.binding(limit_count=999999, portrait_variant=1))

    def test_limit_name_override_wins_over_display_only_portrait_selection(self):
        self.assertEqual(self.binding().np_name_file, "card_servant_np.png")
        self.assertEqual(self.binding(limit_count=3).np_name_file, "100110.png")
        self.assertEqual(self.binding(portrait_variant=3).np_name_file, "100110.png")
        b = self.binding(limit_count=0, portrait_variant=3)
        self.assertEqual((b.np_name_file, b.portrait_variant, b.portrait_source), ("card_servant_np.png", 3, "preview-selection"))
        self.assert_error("np_portrait_unmapped", lambda: self.binding(portrait_variant=99))

    def test_ambiguous_preview_name_state_requires_explicit_limit(self):
        self.records["JP"][0]["ascensionAdd"]["overWriteTDFileName"]["ascension"].pop("4")
        self.flush_source("JP")
        self.build()
        self.assert_error("np_name_ambiguous", lambda: self.binding(portrait_variant=3))
        self.assertEqual(self.binding(limit_count=3).np_name_file, "100110.png")

    def test_transform_requires_exact_region_and_target_np(self):
        b = resolve_np_card(self.root, svt_id=100, td_id=201, region="JP", transform_val=1)
        self.assertEqual(b.servant_id, 200)
        self.assertTrue(all("/100/" not in a.relative for a in b.asset_refs))
        self.assert_error("np_unknown_td", lambda: self.binding(transform_val=1))
        self.assert_error("np_unknown_form", lambda: resolve_np_card(self.root, svt_id=200, td_id=201, region="JP", transform_val=1))

    def test_regional_metadata_and_names_never_borrow_jp(self):
        b = resolve_np_card(self.root, svt_id=100, td_id=101, region="CN")
        self.assertEqual(b.asset_refs[-1].pack, "cn")
        self.assert_error("np_region_metadata_missing", lambda: resolve_np_card(self.root, svt_id=400, td_id=401, region="CN"))
        del self.manifests["CN"]["np_names"]["100"]
        self.flush_source("CN")
        self.build()
        self.assert_error("np_name_missing", lambda: resolve_np_card(self.root, svt_id=100, td_id=101, region="CN"))
        self.assertEqual(self.binding().region, "JP")

    def test_no_name_or_portrait_substitution_for_npc(self):
        self.assert_error("np_portrait_missing", lambda: resolve_np_card(self.root, svt_id=300, td_id=301, region="JP"))
        self.assert_error("np_portrait_missing", lambda: resolve_face_card(self.root, svt_id=300, color="arts", region="JP"))

    def test_wrong_row_is_reported_without_using_another_filled_row(self):
        self.manifests["JP"]["np_names"]["100"]["name_rows"] = [0]
        self.flush_source("JP")
        self.build()
        self.assert_error("np_name_row_missing", lambda: resolve_np_card(self.root, svt_id=100, td_id=102, region="JP"))

    def test_explicit_valid_form_override_can_replace_an_absent_base_name(self):
        del self.manifests["JP"]["np_names"]["100"]
        self.flush_source("JP")
        self.build()
        self.assert_error("np_name_missing", lambda: self.binding())
        self.assertEqual(self.binding(limit_count=3).np_name_file, "100110.png")

    def test_face_reuses_portrait_and_explicit_cn_label_without_np_requirement(self):
        for color in ("arts", "buster", "quick"):
            b = resolve_face_card(self.root, svt_id=400, region="CN", color=color, limit_count=4)
            self.assertEqual((b.card_type, b.td_id, b.np_name_file, b.np_name_row), ("face", None, None, 0))
            self.assertEqual(b.portrait_variant, 3)
            self.assertEqual(b.asset_refs[-1].pack, "cn")
            self.assertEqual(b.asset_refs[-1].relative, f"_composition/shared/card_txt_{color}.png")
        self.assert_error("np_unknown_form", lambda: resolve_face_card(self.root, svt_id=400, region="CN", color="arts", transform_val=1))

    def test_strict_parameters_and_frozen_refs(self):
        for key, value in (("svt_id", True), ("svt_id", "100"), ("td_id", 1.0), ("region", "NA"),
                           ("limit_count", -1), ("limit_count", False), ("portrait_variant", 0), ("transform_val", "../1")):
            args = {"svt_id": 100, "td_id": 101, "region": "JP", key: value}
            with self.subTest(key=key, value=value):
                self.assert_error("invalid_np_card_parameter", lambda: resolve_np_card(self.root, **args))
        self.assert_error("invalid_np_card_parameter", lambda: resolve_face_card(self.root, svt_id=100, region="JP", color="red"))
        binding = self.binding()
        with self.assertRaises(FrozenInstanceError):
            binding.asset_refs[0].relative = "../secret.png"

    def test_builder_is_offline_deterministic_and_never_changes_images_or_catalog(self):
        watched = list((self.root / "assets").rglob("*.png")) + [self.root / "agent/battle_editor/data/catalog.json"]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in watched}
        metadata_before = (self.target.read_bytes(), self.target.stat().st_mtime_ns)
        with patch("urllib.request.urlopen", side_effect=AssertionError("offline only")):
            self.build()
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in watched})
        self.assertEqual(metadata_before, (self.target.read_bytes(), self.target.stat().st_mtime_ns))
        self.assertEqual(self.binding().metadata_digest, hashlib.sha256(self.target.read_bytes()).hexdigest())

    def test_builder_rejects_wrong_region_source_and_modified_png(self):
        self.records["CN"][0]["noblePhantasms"][0]["icon"] = self.url("JP", 100, "card_servant_np.png")
        self.flush_source("CN")
        self.build()
        self.assert_error("np_name_unmapped", lambda: resolve_np_card(self.root, svt_id=100, td_id=101, region="CN"))
        image = self.root / "assets/resource/base/image/CommandCards/100/card_servant_1.png"
        image.write_bytes(png(255, 256))
        with self.assertRaisesRegex(ValueError, "hash/size mismatch"):
            self.build()

    def test_missing_or_unsafe_metadata_asset_cannot_become_a_path(self):
        self.data["assets"][self.data["servants"]["100"]["portraits"][0]["asset"]]["relative"] = "../secrets.png"
        builder.write_atomic(self.target, self.data)
        self.assert_error("np_metadata_invalid", lambda: self.binding())
        self.target.write_text("not JSON", encoding="utf-8")
        self.assert_error("np_metadata_unavailable", lambda: self.binding())

    def test_tampered_cn_name_binding_cannot_point_to_jp_pack(self):
        name = self.data["servants"]["100"]["regions"]["CN"]["nps"]["101"]
        self.data["assets"][name["asset"]]["pack"] = "base"
        builder.write_atomic(self.target, self.data)
        self.assert_error("np_metadata_invalid", lambda: resolve_np_card(self.root, svt_id=100, td_id=101, region="CN"))


class BundledNpMetadataTests(unittest.TestCase):
    def test_real_rows_alternative_files_and_form_overrides(self):
        mash = resolve_np_card(ROOT, svt_id=800100, td_id=800101, region="JP")
        self.assertEqual((mash.np_name_file, mash.np_name_row), ("card_servant_np.png", 1))
        mash_new = resolve_np_card(ROOT, svt_id=800100, td_id=800107, region="JP")
        self.assertEqual((mash_new.np_name_file, mash_new.np_name_row), ("card_servant_np4.png", 0))
        melu = resolve_np_card(ROOT, svt_id=304800, td_id=304802, region="JP")
        self.assertEqual((melu.color, melu.np_name_file), ("buster", "card_servant_np_304802.png"))
        bb = resolve_np_card(ROOT, svt_id=2300600, td_id=2300698, region="CN")
        self.assertEqual(bb.np_name_file, "card_servant_np_2300610.png")
        summer_skadi = resolve_np_card(ROOT, svt_id=1001700, td_id=1001701, region="JP", limit_count=3)
        self.assertEqual((summer_skadi.portrait_variant, summer_skadi.np_name_file), (3, "1001710.png"))

    def test_all_bundled_asset_hashes_and_ready_np_bindings(self):
        data = json.loads((ROOT / "agent/battle_editor/data/np_cards.json").read_text("utf-8"))
        for key, entry in data["assets"].items():
            path = ROOT / "assets/resource" / entry["pack"] / "image/CommandCards" / entry["relative"]
            with self.subTest(asset=key):
                self.assertTrue(path.resolve().is_relative_to(ROOT))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
        for sid, servant in data["servants"].items():
            for region, facts in servant["regions"].items():
                for td, np in facts["nps"].items():
                    if np.get("missing"):
                        continue
                    with self.subTest(sid=sid, region=region, td=td):
                        b = resolve_np_card(ROOT, svt_id=int(sid), td_id=int(td), region=region)
                        self.assertEqual(len(b.asset_refs), 4)
                        self.assertEqual(b.portrait_source, "preview-default")


if __name__ == "__main__":
    unittest.main()
