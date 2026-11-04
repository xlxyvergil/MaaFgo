"""Offline sprite extraction and skill mapping must not guess identities."""
from __future__ import annotations

import hashlib
import importlib.util
from importlib.metadata import version as package_version
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, features


SPEC = importlib.util.spec_from_file_location(
    "build_battle_editor_ui_assets",
    Path(__file__).resolve().parents[1] / "tools" / "build_battle_editor_ui_assets.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")


def image(path, size, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


class UiAssetBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project, self.source, self.cache = [self.base / name for name in ("project", "dump", "cache")]
        self.output = self.project / "agent/battle_editor/data/ui"
        self.catalog = self.project / "agent/battle_editor/data/catalog.json"
        write_json(self.catalog, {
            "servants": [
                {"id": 100100, "skillVariants": [{"id": 12345, "num": 1, "name": "技能", "icon": "skill_00300.png"}]},
                {"id": 200100, "skills": []},
            ],
            "equips": [{"id": 9400010, "image": "f_94000100.png"}],
        })
        write_json(self.cache / "JP_nice_servant.json", [
            {"id": 100100, "extraAssets": {"narrowFigure": {"ascension": {
                "3": "https://static.atlasacademy.io/JP/NarrowFigure/100100/100100@2.png",
            }}}, "appendPassive": [{"num": 102, "priority": 1, "skill": {
                "id": 3006000, "name": "対ライダー攻撃適性", "icon": "https://static.atlasacademy.io/JP/SkillIcons/skill_00300.png",
            }}]},
            {"id": 200100, "appendPassive": [{"num": 102, "priority": 1, "skill": {
                "id": 3012000, "name": "別の適性", "icon": "https://static.atlasacademy.io/JP/SkillIcons/skill_00999.png",
            }}]},
        ])
        write_json(self.cache / "CN_nice_servant.json", [{"id": 100100, "appendPassive": [{"num": 102, "skill": {
            "id": 3006000, "name": "对Rider攻击适性",
        }}]}])
        atlas = Image.new("RGB", (512, 512), "white")
        atlas.paste("red", (0, 0, 150, 376))
        atlas.paste("green", (155, 0, 305, 376))
        atlas.paste("blue", (310, 0, 460, 376))
        self.source_portrait = self.source / "从者立绘/NarrowFigure/100100/100100.png"
        self.source_portrait.parent.mkdir(parents=True)
        atlas.save(self.source_portrait)
        # A nearby numeric ID must never be used as another character's fallback.
        image(self.source / "从者立绘/NarrowFigure/200101/200101.png", (512, 512), "red")
        image(self.source / "图标拆分/SkillIcons/skill_00300.png", (110, 110), "yellow")
        image(self.source / "图标拆分/EquipFaces/f_94000100.png", (150, 68), "magenta")

    def build(self):
        builder = MODULE.AssetBuilder(self.project, self.source, self.cache, self.output)
        return builder, builder.build()

    def formation_fixture(self, *, frame=1):
        path = self.cache / "JP_nice_servant.json"
        records = json.loads(path.read_text("utf8"))
        records[0]["extraAssets"]["charaGraph"] = {"ascension": {
            "1": "https://static.atlasacademy.io/JP/CharaGraph/100100/100100a@1.png",
            "3": f"https://static.atlasacademy.io/JP/CharaGraph/100100/100100b@{frame}.png",
        }}
        write_json(path, records)
        directory = self.source / "从者立绘/CharaGraph/100100"
        directory.mkdir(parents=True, exist_ok=True)
        for suffix, left, right in (("a", "yellow", "magenta"), ("b", "red", "blue")):
            atlas = Image.new("RGBA", (1024, 1024), "white")
            atlas.paste(left, (0, 0, 512, 724))
            atlas.paste(right, (512, 0, 1024, 724))
            atlas.paste((13, 29, 47, 73), (54, 54, 75, 75))
            atlas.paste((13, 29, 47, 73), (566, 54, 587, 75))
            # Simulate the name text band; it must never enter the card output.
            atlas.paste("black", (0, 800, 1024, 1024))
            atlas.save(directory / ("100100" + suffix + ".png"))
        return directory

    def test_formation_native_rgb_crop_resized_before_webp_and_mapping_provenance(self):
        directory = self.formation_fixture(frame=2)
        original_save = Image.Image.save
        inputs = []
        def save(image, destination, format=None, **options):
            if format == "WEBP":
                inputs.append((image.copy(), options))
            return original_save(image, destination, format=format, **options)
        with patch.object(Image.Image, "save", new=save), \
                patch.object(MODULE.palette_tools(), "quantize_image", side_effect=AssertionError("Formation must not use palette quantization")):
            _, manifest = self.build()
        servant = manifest["servants"]["100100"]
        filename = servant["formationPortrait"]
        self.assertRegex(filename, r"^formation_100100_[a-f0-9]{16}\.webp$")
        self.assertEqual(servant["formationPortraitAscension"], servant["portraitAscension"])
        with Image.open(self.output / "media" / filename) as result:
            self.assertEqual(result.size, (358, 507))
            self.assertEqual(result.format, "WEBP")
            self.assertEqual(result.mode, "RGB")
            rgba = result.convert("RGBA")
            for point in ((10, 10), (357, 506)):
                self.assertLessEqual(max(abs(a - b) for a, b in zip(rgba.getpixel(point), (0, 0, 255, 255))), 3)
            self.assertEqual(rgba.getpixel((45, 45))[3], 255)
        source = manifest["files"][filename]["source"]
        self.assertEqual(source["crop"], [512, 0, 1024, 724])
        self.assertEqual(source["sha256"], hashlib.sha256((directory / "100100b.png").read_bytes()).hexdigest())
        self.assertEqual(source["atlasURL"], "https://static.atlasacademy.io/JP/CharaGraph/100100/100100b@2.png")
        self.assertEqual(source["ascension"], 3)
        self.assertEqual((source["width"], source["height"]), (1024, 1024))
        self.assertEqual(source["resize"], {"width": 358, "height": 507, "scale": 0.7,
                                            "resample": "LANCZOS", "mode": "RGB"})
        with Image.open(directory / "100100b.png") as original:
            expected = original.crop((512, 0, 1024, 724)).convert("RGB").resize((358, 507), Image.Resampling.LANCZOS)
        self.assertEqual(len(inputs), 1)
        actual, options = inputs[0]
        self.assertEqual(actual.mode, "RGB")
        self.assertEqual(actual.size, expected.size)
        self.assertEqual(actual.tobytes(), expected.tobytes())
        self.assertEqual(options, {"lossless": False, "quality": 90, "method": 6})
        self.assertEqual(manifest["files"][filename]["encoding"], {
            "format": "WEBP", "lossless": False, "quality": 90, "method": 6,
            "libwebp_version": features.version("webp"), "input_mode": "RGB",
            "alpha": "discardBeforeResizePreserveRGB"})
        self.assertNotIn("quantization", manifest["files"][filename])

    def test_formation_prefers_existing_narrow_stage_and_only_exact_fallbacks(self):
        directory = self.formation_fixture()
        path = self.cache / "JP_nice_servant.json"
        records = json.loads(path.read_text("utf8"))
        records[0]["extraAssets"]["narrowFigure"]["ascension"] = {
            "1": "https://static.atlasacademy.io/JP/NarrowFigure/100100/100100@0.png"}
        write_json(path, records)
        _, manifest = self.build()
        self.assertEqual(manifest["servants"]["100100"]["formationPortraitAscension"], 1)
        # If the preferred source disappears, use the next explicitly mapped
        # form of this same character. Never search by neighboring ID or prefix.
        (directory / "100100a.png").unlink()
        _, fallback = self.build()
        self.assertEqual(fallback["servants"]["100100"]["formationPortraitAscension"], 3)
        records[0]["extraAssets"]["charaGraph"]["ascension"]["3"] = "https://static.atlasacademy.io/JP/CharaGraph/200100/200100b@1.png"
        write_json(path, records)
        image(self.source / "从者立绘/CharaGraph/200100/200100b.png", (1024, 1024), "red")
        _, missing = self.build()
        self.assertNotIn("formationPortrait", missing["servants"]["100100"])
        self.assertNotIn("formationPortrait", missing["servants"]["200100"])
        self.assertEqual(missing["missing"]["formationPortraits"], [100100, 200100])

    def test_formation_url_rejects_unsafe_or_unproven_assets(self):
        prefix = "https://static.atlasacademy.io/JP/CharaGraph/100100/"
        for url in ("file:///100100b@1.png", "https://evil.test/JP/CharaGraph/100100/100100b@1.png",
                    prefix + "100100b@0.png", prefix + "100100b@3.png", prefix + "100100_full.png",
                    prefix + "100100name@1.png", prefix + "100100b@1.png?q=1", prefix + "100100b@1.png#fragment",
                    prefix + "../100100b@1.png", prefix + "100101b@1.png",
                    "https://static.atlasacademy.io/JP/CharaGraph/100101/100100b@1.png"):
            with self.subTest(url=url):
                self.assertIsNone(MODULE.atlas_formation_source(url, self.source, 100100))

    def test_adding_formation_preserves_old_png_bytes_and_reuses_new_ones(self):
        _, original = self.build()
        before = {name: ((self.output / "media" / name).read_bytes(), (self.output / "media" / name).stat().st_mtime_ns)
                  for name in original["files"]}
        directory = self.formation_fixture()
        source_before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.glob("*.png")}
        _, updated = self.build()
        self.assertEqual(before, {name: ((self.output / "media" / name).read_bytes(), (self.output / "media" / name).stat().st_mtime_ns)
                                  for name in before})
        self.assertEqual(source_before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in source_before})
        name = updated["servants"]["100100"]["formationPortrait"]
        created = self.output / "media" / name
        timestamp = created.stat().st_mtime_ns
        _, repeated = self.build()
        self.assertEqual(updated, repeated)
        self.assertEqual(timestamp, created.stat().st_mtime_ns)
        # A changed source receives a new URL, while the earlier file survives
        # for any browser which still has its old catalog open.
        image(directory / "100100b.png", (1024, 1024), "green")
        _, changed = self.build()
        self.assertNotEqual(name, changed["servants"]["100100"]["formationPortrait"])
        self.assertEqual(timestamp, created.stat().st_mtime_ns)

    def test_exact_frame_and_source_hash_are_retained(self):
        _, manifest = self.build()
        record = manifest["servants"]["100100"]
        filename = record["portrait"]
        with Image.open(self.output / "media" / filename) as portrait:
            self.assertEqual(portrait.size, (150, 376))
            self.assertEqual(portrait.convert("RGBA").getpixel((10, 10)), (0, 0, 255, 255))
        source = manifest["files"][filename]["source"]
        self.assertEqual(source["crop"], [310, 0, 460, 376])
        self.assertEqual(source["sha256"], hashlib.sha256(self.source_portrait.read_bytes()).hexdigest())
        self.assertNotIn("portrait", manifest["servants"]["200100"])
        self.assertEqual(manifest["missing"]["portraits"], [200100])

    def test_append_ids_names_and_missing_icons_are_preserved(self):
        _, manifest = self.build()
        first = manifest["servants"]["100100"]
        self.assertEqual(first["activeSkills"][0]["id"], 12345)
        append = first["appendSkills"][0]
        self.assertEqual((append["id"], append["num"], append["name"]), (3006000, 3, "对Rider攻击适性"))
        other = manifest["servants"]["200100"]["appendSkills"][0]
        self.assertEqual(other["id"], 3012000)
        self.assertIsNone(other["icon"])
        self.assertEqual(manifest["missing"]["skillIcons"], ["skill_00999.png"])

    def test_equip_dump_is_never_copied_even_when_project_template_is_missing(self):
        _, manifest = self.build()
        self.assertEqual(manifest["equips"], {})
        self.assertEqual(manifest["missing"]["equipImages"], [])
        self.assertEqual(manifest["stats"]["addedEquipImages"], 0)
        self.assertNotIn("f_94000100.png", manifest["files"])
        self.assertFalse((self.output / "media/f_94000100.png").exists())
        shipped = self.project / "assets/resource/base/image/EquipFaces/team/f_94000100.png"
        shipped.parent.mkdir(parents=True, exist_ok=True)
        Image.new("P", (152, 86)).save(shipped)
        before = (shipped.read_bytes(), shipped.stat().st_mtime_ns)
        _, reused = self.build()
        self.assertEqual(reused["equips"], {})
        self.assertEqual(before, (shipped.read_bytes(), shipped.stat().st_mtime_ns))

    def test_mapping_rejects_arbitrary_paths_and_outputs_are_deterministic(self):
        self.assertIsNone(MODULE.atlas_narrow_source("file:///etc/passwd", self.source))
        self.assertIsNone(MODULE.atlas_narrow_source("https://example.test/JP/NarrowFigure/../../100100@0.png", self.source))
        self.assertIsNone(MODULE.atlas_narrow_source("https://example.test/JP/NarrowFigure/100100/100100@3.png", self.source))
        _, first = self.build()
        _, second = self.build()
        self.assertEqual(first, second)
        for filename, metadata in second["files"].items():
            self.assertEqual(metadata["sha256"], hashlib.sha256((self.output / "media" / filename).read_bytes()).hexdigest())

    def test_alpha_repair_keeps_enclosed_black_details(self):
        source = self.source / "图标拆分/PartyOrganization/icon_support_01.png"
        source.parent.mkdir(parents=True)
        flag = Image.new("RGB", (7, 7), "black")
        for x in range(1, 6):
            for y in range(1, 6):
                flag.putpixel((x, y), (240, 210, 40))
        flag.putpixel((3, 3), (0, 0, 0))
        flag.save(source)
        _, manifest = self.build()
        filename = manifest["common"]["support"]
        with Image.open(self.output / "media" / filename) as result:
            self.assertEqual(result.getpixel((0, 0))[3], 0)
            self.assertEqual(result.getpixel((3, 3))[3], 255)
        self.assertEqual(manifest["files"][filename]["source"]["alphaRepair"]["method"], "edgeConnectedBlack")

    def test_class_filters_use_gold_and_rarity_uses_exact_native_colors(self):
        directory = self.source / "图标_游戏内显示/ClassIcons_纯图标透明"
        for style, color in [(0, "black"), (1, "brown"), (2, "silver"), (3, "gold")]:
            image(directory / f"class{style}_1.png", (80, 80), color)
        # Only the same class's gold may be used when its requested style is
        # absent; an unrelated silver icon may never supply the missing art.
        image(directory / "class3_17.png", (80, 80), "gold")
        _, manifest = self.build()
        selected = manifest["classes"]["saber"]
        variants = manifest["classVariants"]["saber"]
        self.assertEqual(selected, variants["4"])
        self.assertEqual(selected, variants["5"])
        self.assertEqual(variants["1"], variants["2"])
        for rarity, style in [(0, 0), (1, 1), (2, 1), (3, 2), (4, 3), (5, 3)]:
            source = manifest["files"][variants[str(rarity)]]["source"]["path"]
            self.assertEqual(Path(source).name, f"class{style}_1.png")
        grand = manifest["classes"]["loreGrandCaster"]
        self.assertEqual(manifest["classVariants"]["loreGrandCaster"]["3"], grand)
        self.assertIn({"classKey": "loreGrandCaster", "rarity": 3, "requestedStyle": 2, "fallback": "sameClassGold"},
                      manifest["missing"]["classVariants"])

    def test_unchanged_media_is_reused_and_corruption_is_rebuilt(self):
        self.formation_fixture()
        _, first = self.build()
        paths = [self.output / "media" / name for name in first["files"]] + [self.output / "manifest.json"]
        metadata = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        with patch.object(MODULE.palette_tools(), "quantize_image", side_effect=AssertionError("Cached outputs must not be requantized")):
            _, second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(metadata, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})
        damaged = self.output / "media" / first["servants"]["100100"]["formationPortrait"]
        damaged.write_bytes(b"incomplete WebP")
        _, rebuilt = self.build()
        self.assertEqual(hashlib.sha256(damaged.read_bytes()).hexdigest(), first["files"][damaged.name]["sha256"])
        self.assertEqual(rebuilt, first)

    def test_palette_targets_preserve_expected_sizes_alpha_masks_and_source_bytes(self):
        directory = self.formation_fixture(frame=2)
        narrow_fallback = self.source / "从者立绘/NarrowFigure/200100/200100.png"
        image(narrow_fallback, (512,512), "blue")
        originals = [self.source_portrait, narrow_fallback, directory / "100100b.png",
                     self.source / "图标拆分/EquipFaces/f_94000100.png"]
        for source in originals:
            with Image.open(source) as original:
                width, height = original.size
            y, x = np.indices((height, width))
            rgba = np.stack((x % 256, y % 256, (x + y) % 256,
                             np.array([0, 1, 127, 128, 254, 255])[x % 6]), axis=-1).astype(np.uint8)
            Image.fromarray(rgba).save(source)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in originals}
        _, manifest = self.build()
        names = [manifest["servants"]["200100"]["portrait"]]
        for name in names:
            metadata = manifest["files"][name]
            source = self.source / metadata["source"]["path"]
            with Image.open(source) as original:
                cropped = original.crop(metadata["source"]["crop"]) if "crop" in metadata["source"] else original.copy()
                reference = cropped.convert("RGBA")
                self.assertNotIn("resize", metadata["source"])
                original_alpha = np.asarray(reference)[:, :, 3]
            with Image.open(self.output / "media" / name) as result:
                self.assertEqual(result.mode, "P")
                self.assertLessEqual(len(result.getcolors()), 256)
                self.assertEqual(result.size, reference.size)
                alpha = np.asarray(result.convert("RGBA"))[:, :, 3]
            np.testing.assert_array_equal(alpha == 0, original_alpha == 0)
            np.testing.assert_array_equal(alpha >= 128, original_alpha >= 128)
            self.assertEqual(metadata["mode"], "P")
            self.assertEqual(metadata["quantization"]["imagequant_version"], package_version("imagequant"))
            self.assertEqual(metadata["quantization"]["colors"], 256)
            self.assertEqual(metadata["quantization"]["dither"], "NONE")
            self.assertEqual(metadata["quantization"]["compress_level"], 9)
            self.assertEqual(metadata["quantizationMetrics"]["alpha_mask_changed_pixels"], 0)
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in originals})

    def test_old_rgba_manifest_upgrades_only_target_images_from_originals(self):
        image(self.source / "图标拆分/UISkin/img_common_bg01_skin01.png", (40, 30), "navy")
        image(self.source / "图标拆分/ClassIcons/class3_1.png", (80, 80), "gold")
        _, legacy = self.build()
        untouched = {}
        provenance = {}
        for name, entry in legacy["files"].items():
            path = self.output / "media" / name
            if not MODULE.PALETTE_FILENAMES.fullmatch(name):
                untouched[name] = (path.read_bytes(), path.stat().st_mtime_ns, entry.copy())
                continue
            provenance[name] = entry["source"].copy()
            with Image.open(self.source / entry["source"]["path"]) as original:
                output = original.crop(entry["source"]["crop"]) if "crop" in entry["source"] else original.copy()
                output.convert("RGBA").save(path, "PNG")
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            for key in ("mode", "quantization", "quantizationMetrics"):
                entry.pop(key)
        write_json(self.output / "manifest.json", legacy)
        _, upgraded = self.build()
        for name, source in provenance.items():
            self.assertEqual(upgraded["files"][name]["source"], source)
            self.assertEqual(upgraded["files"][name]["mode"], "P")
            with Image.open(self.output / "media" / name) as output:
                self.assertEqual(output.mode, "P")
        for name, expected in untouched.items():
            path = self.output / "media" / name
            self.assertEqual(expected, (path.read_bytes(), path.stat().st_mtime_ns, upgraded["files"][name]))

    def test_recipe_upgrade_changes_formation_url_and_preserves_old_file(self):
        self.formation_fixture()
        _, first = self.build()
        name = first["servants"]["100100"]["formationPortrait"]
        path = self.output / "media" / name
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        builder = MODULE.AssetBuilder(self.project, self.source, self.cache, self.output)
        recipe = {**builder.webp_recipe(), "quality": 95, "libwebp_version": "actual-test-version"}
        with patch.object(MODULE.AssetBuilder, "webp_recipe", return_value=recipe), \
                patch.object(MODULE.palette_tools(), "quantize_image", wraps=MODULE.palette_tools().quantize_image) as quantize:
            _, changed = self.build()
        replacement = changed["servants"]["100100"]["formationPortrait"]
        self.assertNotEqual(name, replacement)
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertEqual(quantize.call_count, 0)
        self.assertEqual(changed["files"][replacement]["source"], first["files"][name]["source"])
        self.assertEqual(changed["files"][replacement]["encoding"], recipe)
        self.assertEqual(changed["servants"]["100100"]["formationPortraitAscension"], 3)

    def test_resize_recipe_changes_formation_fingerprint_without_touching_other_assets(self):
        self.formation_fixture()
        image(self.source / "图标拆分/UISkin/img_common_bg01_skin01.png", (40, 30), "navy")
        _, first = self.build()
        old_name = first["servants"]["100100"]["formationPortrait"]
        before = {name: ((self.output / "media" / name).read_bytes(),
                         (self.output / "media" / name).stat().st_mtime_ns)
                  for name in first["files"]}
        changed_recipe = {**MODULE.formation_resize_recipe(), "width": 256, "height": 362, "scale": 0.5}
        with patch.object(MODULE, "formation_resize_recipe", return_value=changed_recipe), \
                patch.object(MODULE.palette_tools(), "quantize_image", wraps=MODULE.palette_tools().quantize_image) as quantize:
            _, changed = self.build()
        new_name = changed["servants"]["100100"]["formationPortrait"]
        self.assertNotEqual(old_name, new_name)
        self.assertEqual(quantize.call_count, 0)
        self.assertEqual(changed["files"][new_name]["source"]["resize"], changed_recipe)
        self.assertEqual(changed["files"][new_name]["source"]["crop"], first["files"][old_name]["source"]["crop"])
        with Image.open(self.output / "media" / new_name) as result:
            self.assertEqual(result.size, (256, 362))
        for name, expected in before.items():
            path = self.output / "media" / name
            self.assertEqual(expected, (path.read_bytes(), path.stat().st_mtime_ns))
            if name != old_name:
                self.assertEqual(changed["files"][name], first["files"][name])

    def test_resize_recipe_is_checked_before_reusing_cached_formation(self):
        self.formation_fixture()
        _, first = self.build()
        filename = first["servants"]["100100"]["formationPortrait"]
        stale = json.loads(json.dumps(first))
        stale["files"][filename]["source"].pop("resize")
        write_json(self.output / "manifest.json", stale)
        original_save = Image.Image.save
        writes = []
        def save(image, destination, format=None, **options):
            writes.append(format)
            return original_save(image, destination, format=format, **options)
        with patch.object(Image.Image, "save", new=save):
            _, rebuilt = self.build()
        self.assertEqual(writes, ["WEBP"])
        self.assertEqual(rebuilt, first)

    def test_formation_recovers_hidden_rgb_before_resize_without_touching_ui_alpha(self):
        directory = self.formation_fixture()
        source = directory / "100100b.png"
        with Image.open(source) as original:
            art = original.copy()
        # Fully transparent source pixels retain the real dark hair color.
        art.paste((4, 6, 8, 0), (0, 0, 512, 724))
        art.save(source)
        ui_source = self.source / "图标拆分/SkillIcons/skill_00300.png"
        ui = Image.new("RGBA", (110, 110), (4, 6, 8, 0))
        ui.putpixel((55, 55), (40, 60, 80, 73))
        ui.save(ui_source)
        originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (source, ui_source)}
        _, manifest = self.build()
        filename = manifest["servants"]["100100"]["formationPortrait"]
        with Image.open(self.output / "media" / filename) as result:
            self.assertEqual(result.mode, "RGB")
            self.assertEqual(result.size, (358, 507))
            self.assertLessEqual(max(abs(a - b) for a, b in zip(result.getpixel((179, 253)), (4, 6, 8))), 3)
            self.assertEqual(set(result.convert("RGBA").getchannel("A").getdata()), {255})
        with Image.open(self.output / "media/skill_00300.png") as result:
            self.assertEqual(result.format, "PNG")
            self.assertEqual(result.getpixel((0, 0)), (4, 6, 8, 0))
            self.assertEqual(result.getpixel((55, 55)), (40, 60, 80, 73))
        self.assertEqual(originals, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals})

    def test_webp_recipe_fields_each_invalidate_formation_fingerprint(self):
        self.formation_fixture()
        builder, first = self.build()
        old_name = first["servants"]["100100"]["formationPortrait"]
        old_path = self.output / "media" / old_name
        before = (old_path.read_bytes(), old_path.stat().st_mtime_ns)
        recipe = builder.webp_recipe()
        for field, value in (("quality", 95), ("method", 5), ("libwebp_version", "future-version"),
                             ("alpha", "updated-opaque-source-recipe")):
            with self.subTest(field=field), patch.object(MODULE.AssetBuilder, "webp_recipe", return_value={**recipe, field: value}):
                _, updated = self.build()
                self.assertNotEqual(updated["servants"]["100100"]["formationPortrait"], old_name)
                self.assertEqual((old_path.read_bytes(), old_path.stat().st_mtime_ns), before)

    def test_missing_webp_support_rejects_cached_outputs_without_fallback(self):
        self.formation_fixture()
        _, first = self.build()
        paths = [self.output / "media" / name for name in first["files"]] + [self.output / "manifest.json"]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        with patch.object(MODULE.features, "check", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "dependency missing.*no WebP fallback"):
                self.build()
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})

    def test_webp_only_accepts_exact_formation_names_and_original_graph_sources(self):
        self.formation_fixture()
        _, first = self.build()
        builder = MODULE.AssetBuilder(self.project, self.source, self.cache, self.output)
        crop = (0, 0, 512, 724)
        name = "formation_100100_0123456789abcdef.webp"
        for source in (self.output / "media" / first["servants"]["100100"]["formationPortrait"],
                       self.source_portrait):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "original"):
                builder.add(source, name, crop=crop, resize=MODULE.formation_resize_recipe())
        source = self.source / "从者立绘/CharaGraph/100100/100100b.png"
        for filename in ("servant_100100.webp", "battle_frame.webp", "formation_100100_0123456789abcdef.png"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                builder.add(source, filename, crop=crop, resize=MODULE.formation_resize_recipe())
        # Even a valid-looking renamed old palette output is not an original
        # source: use a source root fixture with a generated-media location.
        generated = self.output / "media/servant_100100.png"
        Image.new("P", (512, 724)).save(generated)
        with self.assertRaisesRegex(ValueError, "original"):
            builder.add(generated, name, crop=crop, resize=MODULE.formation_resize_recipe())

    def test_missing_palette_dependency_rejects_even_cached_outputs_without_fallback(self):
        _, first = self.build()
        paths = [self.output / "media" / name for name in first["files"]] + [self.output / "manifest.json"]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        with patch.dict(sys.modules, {"imagequant": None}):
            with self.assertRaisesRegex(RuntimeError, "dependency missing.*no palette fallback"):
                self.build()
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})
        # Non-target UI extraction itself does not require a quantization dependency.
        with patch.dict(sys.modules, {"imagequant": None}):
            builder = MODULE.AssetBuilder(self.project, self.source, self.cache, self.output)
            self.assertEqual(builder.add(self.source / "图标拆分/SkillIcons/skill_00300.png", "skill_00300.png"), "skill_00300.png")

    def test_complete_cards_stop_registering_redundant_narrow_outputs(self):
        _, first = self.build()
        old = self.output / 'media' / first['servants']['100100']['portrait']
        before = (old.read_bytes(),old.stat().st_mtime_ns)
        directory = self.formation_fixture()
        _, complete = self.build()
        record = complete['servants']['100100']
        self.assertNotIn('portrait',record)
        self.assertNotIn(old.name,complete['files'])
        self.assertEqual(record['portraitAscension'],3)
        self.assertEqual(record['formationPortraitAscension'],3)
        self.assertEqual((old.read_bytes(),old.stat().st_mtime_ns),before)
        # Retired files are only archived by an explicitly audited deployment;
        # removal of a complete source restores the exact legitimate fallback.
        for source in directory.glob('*.png'): source.unlink()
        _, fallback = self.build()
        self.assertEqual(fallback['servants']['100100']['portrait'],old.name)
        self.assertNotIn('formationPortrait',fallback['servants']['100100'])
        self.assertEqual(old.read_bytes(),before[0])

    def test_generated_media_cannot_become_another_palette_source(self):
        _, manifest = self.build()
        builder = MODULE.AssetBuilder(self.project, self.source, self.cache, self.output)
        with self.assertRaisesRegex(ValueError, "original sources"):
            builder.add(self.output / "media" / manifest["servants"]["100100"]["portrait"], "servant_200100.png")

    def test_contact_sheet_uses_palette_transparency(self):
        with Image.open(self.source_portrait) as original:
            rgba = original.convert("RGBA")
        rgba.putpixel((310, 0), (0, 0, 0, 0))
        rgba.save(self.source_portrait)
        builder, _ = self.build()
        sheet = self.base / "contact.png"
        builder.contact_sheet(sheet)
        with Image.open(sheet) as result:
            self.assertEqual(result.getpixel((15, 25)), (233, 244, 251))

    def test_battle_frames_keep_native_ratio_and_small_whitelisted_outputs(self):
        for color in ("arts", "buster", "quick"):
            image(self.source / f"图标_游戏内显示/Battle/card_bg_np{color}.png", (140, 177), "gold")
        image(self.source / "图标_游戏内显示/Battle/btn_master_skill.png", (111, 116), "blue")
        _, manifest = self.build()
        for color, name in manifest["common"]["npFrames"].items():
            self.assertEqual(name, f"battle_np_{color}.png")
            self.assertEqual((manifest["files"][name]["width"], manifest["files"][name]["height"]), (140, 177))
        master = manifest["common"]["masterSkill"]
        self.assertEqual((manifest["files"][master]["width"], manifest["files"][master]["height"]), (111, 116))


if __name__ == "__main__":
    unittest.main()
