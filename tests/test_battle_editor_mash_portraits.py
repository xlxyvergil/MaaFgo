"""Mash costume cards are exact offline sources, never guessed adjacent IDs."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, features


SPEC = importlib.util.spec_from_file_location(
    "build_mash_portraits", Path(__file__).resolve().parents[1] / "tools/build_battle_editor_mash_portraits.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@unittest.skipUnless(features.check("webp"), "Pillow with libwebp is required")
class MashPortraitBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project, self.source, self.cache = [self.base / name for name in ("project", "dump", "cache")]
        self.output = self.project / "agent/battle_editor/data/ui"
        self.media = self.output / "media"
        self.media.mkdir(parents=True)
        self.base_name = "formation_800100_0123456789abcdef.webp"
        Image.new("RGB", (358, 507), "gray").save(self.media / self.base_name, format="WEBP")
        Image.new("RGB", (9, 7), "yellow").save(self.media / "untouched.png")
        files = {name: {"sha256": MODULE.UI.sha256(self.media / name), "preserveUnknown": [None, False, 7]}
                 for name in (self.base_name, "untouched.png")}
        self.before = {
            "schema": 1, "generator": "existing generator", "sources": {"original": "keep"},
            "files": files,
            "servants": {"800100": {"formationPortrait": self.base_name, "activeSkills": [{"id": 1000}]},
                         "100100": {"name": "untouched"}},
            "equips": {"77": {"keep": True}}, "classes": {"shielder": "untouched.png"},
            "missing": {"portraits": [123]},
            "stats": {"formationPortraits": 1, "mediaFiles": 2,
                      "mediaBytes": sum(p.stat().st_size for p in self.media.iterdir()), "keep": 47},
        }
        write_json(self.output / "manifest.json", self.before)
        self.urls = {str(sid): f"https://static.atlasacademy.io/JP/CharaGraph/{sid}/{sid}a.png"
                     for sid in MODULE.COSTUME_IDS}
        self.cache_data = [{"id": 800100, "extraAssets": {"charaGraph": {"costume": self.urls}}}]
        write_json(self.cache / "JP_nice_servant.json", self.cache_data)
        for sid, color in zip(MODULE.COSTUME_IDS, ("red", "blue", "magenta")):
            path = self.source / f"从者立绘/CharaGraph/{sid}/{sid}a.png"
            path.parent.mkdir(parents=True)
            atlas = Image.new("RGBA", (1024, 1024), "green")
            # Valid RGB beneath a zero alpha channel must survive the crop.
            rgb = Image.new("RGB", (512, 724), color)
            alpha = Image.new("L", rgb.size, 0)
            art = rgb.convert("RGBA")
            art.putalpha(alpha)
            atlas.paste(art, (0, 0))
            atlas.save(path)

    def build(self):
        return MODULE.build_mash_portraits(self.project, self.source, self.cache, self.output)

    def test_exact_cards_discard_bad_alpha_and_exclude_other_frame_and_names(self):
        before = {p: MODULE.UI.sha256(p) for p in self.source.rglob("*.png")}
        updated = self.build()
        forms = updated["servants"]["800100"]["formPortraits"]
        self.assertEqual(set(forms), {"early", "normal", "ortenaus", "black-barrel", "ortenaus-restored", "paladin"})
        self.assertEqual(forms["early"], self.base_name)
        self.assertEqual(forms["normal"], self.base_name)
        self.assertEqual(forms["ortenaus"], forms["black-barrel"])
        for key, color in (("ortenaus", (255, 0, 0)), ("ortenaus-restored", (0, 0, 255)), ("paladin", (255, 0, 255))):
            name = forms[key]
            with self.subTest(key=key), Image.open(self.media / name) as image:
                self.assertEqual((image.format, image.mode, image.size), ("WEBP", "RGB", (358, 507)))
                self.assertLess(max(abs(a - b) for a, b in zip(image.getpixel((200, 300)), color)), 5)
            metadata = updated["files"][name]
            self.assertEqual(metadata["source"]["crop"], [0, 0, 512, 724])
            self.assertEqual(metadata["source"]["resize"], MODULE.UI.formation_resize_recipe())
            self.assertEqual(metadata["encoding"]["alpha"], "discardBeforeResizePreserveRGB")
            self.assertEqual(metadata["sha256"], MODULE.UI.sha256(self.media / name))
        self.assertEqual(before, {p: MODULE.UI.sha256(p) for p in before})

    def test_incremental_build_preserves_unrelated_records_bytes_and_mtimes(self):
        untouched = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.media.iterdir()}
        updated = self.build()
        remaining = copy.deepcopy(updated)
        remaining["servants"]["800100"].pop("formPortraits")
        remaining["files"] = {k: v for k, v in remaining["files"].items() if k in self.before["files"]}
        remaining["stats"] = self.before["stats"]
        self.assertEqual(remaining, self.before)
        for path, expected in untouched.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), expected)
        self.assertEqual(updated["stats"]["mediaFiles"], 5)
        self.assertEqual(updated["stats"]["formPortraitMappings"], 6)
        self.assertEqual(updated["stats"]["formPortraitImages"], 3)

    def test_second_build_is_byte_and_timestamp_stable(self):
        self.build()
        paths = [self.output / "manifest.json", *self.media.iterdir()]
        snapshots = {p: (MODULE.UI.sha256(p), p.stat().st_mtime_ns) for p in paths}
        self.build()
        self.assertEqual(snapshots, {p: (MODULE.UI.sha256(p), p.stat().st_mtime_ns) for p in paths})

    def test_different_original_pixels_fingerprint_a_new_url_without_modifying_old_media(self):
        first = self.build()
        old_name = first["servants"]["800100"]["formPortraits"]["paladin"]
        old_hash = MODULE.UI.sha256(self.media / old_name)
        source = self.source / "从者立绘/CharaGraph/800190/800190a.png"
        Image.new("RGBA", (512, 724), "cyan").save(source)
        updated = self.build()
        self.assertNotEqual(old_name, updated["servants"]["800100"]["formPortraits"]["paladin"])
        self.assertEqual(old_hash, MODULE.UI.sha256(self.media / old_name))

    def test_all_sources_are_preflighted_and_missing_declaration_has_no_manifest_or_media_changes(self):
        self.cache_data[0]["extraAssets"]["charaGraph"]["costume"].pop("800190")
        write_json(self.cache / "JP_nice_servant.json", self.cache_data)
        manifest = (self.output / "manifest.json").read_bytes()
        names = set(p.name for p in self.media.iterdir())
        with self.assertRaisesRegex(ValueError, "three declared"):
            self.build()
        self.assertEqual(manifest, (self.output / "manifest.json").read_bytes())
        self.assertEqual(names, set(p.name for p in self.media.iterdir()))

    def test_url_identity_path_and_texture_dimensions_cannot_be_guessed(self):
        invalid_urls = [
            self.urls["800190"], self.urls["800140"].replace("https:", "http:"),
            self.urls["800140"] + "?frame=1", self.urls["800140"].replace("a.png", "a@2.png"),
            self.urls["800140"].replace("static.atlasacademy.io", "example.com"),
        ]
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                MODULE.costume_source(url, self.source, 800140)
        path = self.source / "从者立绘/CharaGraph/800140/800140a.png"
        Image.new("RGB", (150, 376), "red").save(path)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            MODULE.costume_source(self.urls["800140"], self.source, 800140)


if __name__ == "__main__":
    unittest.main()
