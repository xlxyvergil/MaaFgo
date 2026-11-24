"""可选礼装包的源图筛选、正式模板一致性和可复现归档验证。"""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "tools"
FORMAL = ROOT / "skills/fgo-equip-icon-pipeline/scripts/sync_equip_icons.py"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


generator = module("support_extension_generator", SCRIPTS / "build_support_extension.py")
formal = module("support_extension_formal", FORMAL) if FORMAL.is_file() else None


class SupportExtensionGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        image = Image.new("RGBA", (150, 68))
        image.putdata([(x % 256, (x * 3 + y * 2) % 256, y * 3 % 256, 255)
                       for y in range(68) for x in range(150)])
        for eid in (101, 102):
            image.save(self.sources / f"f_{eid}0.png")
        self.star = self.root / "star.png"
        Image.new("RGBA", (20, 20), (50, 255, 100, 180)).save(self.star)
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps({"equips": [
            {"id": str(eid), "name": f"礼装{eid}", "images": [f"f_{eid}0.png"]}
            for eid in (101, 102, 103)]}), encoding="utf8")
        self.builtin = self.root / "builtin.json"
        self.builtin.write_text(json.dumps({"已有礼装.png": "f_1010.png"}), encoding="utf8")

    def plan(self, **kwargs):
        return generator.plan(self.catalog, self.sources, self.builtin, **kwargs)

    def test_catalog_ids_control_selection_and_missing_is_explicit(self):
        result = self.plan()
        self.assertEqual([row["id"] for row in result["selected"]], [102])
        self.assertEqual(result["excluded_builtin"], [101])
        self.assertEqual(result["missing"], [{"id": 103, "name": "礼装103", "expected": "f_1030.png"}])
        self.assertEqual([row["id"] for row in self.plan(ids=[101], include_builtin=True)["selected"]], [101])
        with self.assertRaisesRegex(ValueError, "999"):
            self.plan(ids=[999])

    @unittest.skipUnless(formal, "未安装本地正式模板生成 skill；其余发布测试照常执行")
    def test_produced_images_match_formal_support_builder(self):
        source = self.sources / "f_1020.png"
        normal, maximum = self.root / "normal.png", self.root / "maximum.png"
        formal.build_support_normal_icon(source, normal)
        formal.build_support_max_icon(normal, self.star, maximum)
        with Image.open(self.star) as star:
            generated = generator.build_images(source, star.copy())
        for state, reference in (("非满破", normal), ("满破", maximum)):
            with Image.open(reference) as expected, Image.open(io.BytesIO(generated[state])) as actual:
                self.assertEqual(actual.mode, expected.mode)
                self.assertEqual(actual.convert("RGBA").tobytes(), expected.convert("RGBA").tobytes())

    def test_archive_reproducible_and_manifest_hashes_protect_images(self):
        selected = self.plan()["selected"]
        first, second = self.root / "one.zip", self.root / "two.zip"
        generator.build_archive(first, selected, self.sources, self.star, version="20261106")
        generator.build_archive(second, selected, self.sources, self.star, version="20261106")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(generator.verify_archive(first), {"equips": 1, "images": 2})
        with zipfile.ZipFile(first) as archive:
            payloads = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(payloads["manifest.json"])
        manifest["sha256"]["满破/f_1020.png"] = "0" * 64
        payloads["manifest.json"] = json.dumps(manifest).encode("utf8")
        with zipfile.ZipFile(second, "w") as archive:
            for name, payload in payloads.items():
                archive.writestr(name, payload)
        with self.assertRaisesRegex(ValueError, "哈希"):
            generator.verify_archive(second)

    def test_cli_dry_run_and_missing_strict_do_not_create_zip(self):
        output = self.root / "never-created" / "extension.zip"
        args = [sys.executable, "-X", "utf8", str(SCRIPTS / "build_support_extension.py"),
                "--catalog", str(self.catalog), "--source-dir", str(self.sources),
                "--star", str(self.star), "--builtin-manifest", str(self.builtin),
                "--output", str(output)]
        dry_run = subprocess.run(args + ["--dry-run"], capture_output=True, encoding="utf8")
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertEqual(json.loads(dry_run.stdout)["selected"], 1)
        self.assertFalse(output.parent.exists())
        strict = subprocess.run(args + ["--strict"], capture_output=True, encoding="utf8")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("缺失", strict.stderr)
        self.assertFalse(output.parent.exists())

    def test_wrong_source_is_not_replaced_with_faces_or_placeholder(self):
        Image.new("RGBA", (128, 128)).save(self.sources / "f_1020.png")
        with self.assertRaisesRegex(ValueError, "150×68"):
            self.plan()

    def test_reviewed_missing_list_rejects_new_and_resolved_gaps(self):
        selection = self.plan()
        expected = self.root / "missing.json"
        expected.write_text(json.dumps(selection["missing"]), encoding="utf8")
        generator.check_missing(selection, expected)
        for missing in ([], selection["missing"] + [{"id": 104, "name": "新礼装"}]):
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "缺失项"):
                generator.check_missing({"missing": missing}, expected)

    def test_cli_missing_check_stops_before_output(self):
        expected = self.root / "missing.json"
        expected.write_text("[]", encoding="utf8")
        output = self.root / "not-created/extension.zip"
        result = subprocess.run([
            sys.executable, "-X", "utf8", str(SCRIPTS / "build_support_extension.py"),
            "--catalog", str(self.catalog), "--source-dir", str(self.sources),
            "--star", str(self.star), "--builtin-manifest", str(self.builtin),
            "--check-missing", str(expected), "--output", str(output),
        ], capture_output=True, encoding="utf8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺失项", result.stderr)
        self.assertFalse(output.parent.exists())

    def test_archive_installs_with_runtime_and_preserves_chinese_names(self):
        from agent.support_ce import install_extension, read_manifest, resolve_external_templates

        output = self.root / "MaaFGO-SupportCE-DLC-v1.2.3-beta.1.zip"
        generator.build_archive(output, self.plan()["selected"], self.sources, self.star,
                                version="v1.2.3-beta.1")
        installation = self.root / "MaaFGO 中文目录"
        result = install_extension(installation, output.read_bytes())
        self.assertEqual((result["installed"], result["images"]), (1, 2))
        manifest = read_manifest(installation)
        self.assertEqual(manifest["version"], "v1.2.3-beta.1")
        self.assertEqual(manifest["equips"][0]["name"], "礼装102")
        self.assertEqual(len(resolve_external_templates(installation, "support-ce:102", "任意")), 2)

    def catalog_sources(self):
        faces, team = self.root / "faces", self.root / "team"
        faces.mkdir()
        team.mkdir()
        for size, target in (((128, 128), faces), ((188, 86), team)):
            image = Image.new("RGBA", size)
            image.putdata([(x % 256, (x * 3 + y * 2) % 256, y * 3 % 256, 255)
                           for y in range(size[1]) for x in range(size[0])])
            image.save(target / "f_1020.png")
        catalog = json.loads(self.catalog.read_text("utf8"))
        catalog["equips"][1]["resource"] = "dlc"
        self.catalog.write_text(json.dumps(catalog), encoding="utf8")
        return faces, team

    def test_catalog_dlc_roundtrip_includes_all_four_images_and_reproduces(self):
        from agent.support_ce import install_extension, resolve_external_image

        faces, team = self.catalog_sources()
        selected = self.plan(faces_dir=faces, team_source_dir=team)["selected"]
        first, second = self.root / "catalog-one.zip", self.root / "catalog-two.zip"
        for output in (first, second):
            report = generator.build_archive(output, selected, self.sources, self.star)
            self.assertEqual(report["images"], 4)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(generator.verify_archive(first), {"equips": 1, "images": 4})
        installation = self.root / "installation"
        self.assertEqual(install_extension(installation, first.read_bytes())["images"], 4)
        for kind, size in (("list", (147, 56)), ("team", (152, 86))):
            with Image.open(resolve_external_image(installation, 102, kind)) as image:
                self.assertEqual((image.size, image.mode), (size, "P"))

    @unittest.skipUnless(formal, "未安装本地正式模板生成 skill")
    def test_catalog_images_match_formal_list_and_team_builders(self):
        faces, team = self.catalog_sources()
        for kind, directory, builder in (("list", faces, formal.build_list_icon),
                                         ("team", team, formal.build_team_icon)):
            target = self.root / f"formal-{kind}.png"
            source = directory / "f_1020.png"
            builder(source, target)
            generated = generator.build_catalog_image(source, kind)
            with Image.open(target) as expected, Image.open(io.BytesIO(generated)) as actual:
                self.assertEqual((actual.size, actual.mode), (expected.size, expected.mode))
                self.assertEqual(actual.convert("RGBA").tobytes(), expected.convert("RGBA").tobytes())

    def test_catalog_dlc_missing_any_required_source_stops(self):
        faces, team = self.catalog_sources()
        for source in (faces / "f_1020.png", team / "f_1020.png", self.sources / "f_1020.png"):
            original = source.read_bytes()
            source.unlink()
            try:
                with self.assertRaisesRegex(ValueError, "DLC礼装.*缺少"):
                    self.plan(faces_dir=faces, team_source_dir=team)
            finally:
                source.write_bytes(original)

    def test_catalog_dlc_rejects_wrong_source_dimensions(self):
        faces, team = self.catalog_sources()
        Image.new("RGBA", (150, 68)).save(team / "f_1020.png")
        with self.assertRaisesRegex(ValueError, "188"):
            self.plan(faces_dir=faces, team_source_dir=team)

    @unittest.skipUnless(formal, "未安装本地正式模板生成 skill")
    def test_formal_selection_excludes_dlc_from_excel_and_runtime_but_keeps_variants(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["equipId", "非高价值礼装", "resource", "important"])
        sheet.append([101, 0, None, 1])
        sheet.append([102, 0, "dlc", 0])
        sheet.append([103, 0, None, 0])
        sheet.append([104, 1, None, 0])
        source = self.root / "equip_list.xlsx"
        workbook.save(source)
        runtime = self.root / "runtime.json"
        runtime.write_text(json.dumps({"equips": [
            {"id": "101", "important": 1, "images": ["f_1010.png", "f_1011.png"]},
            {"id": "102", "images": ["f_1020.png", "f_1021.png"]},
            {"id": "103", "resource": "dlc", "images": ["f_1030.png", "f_1031.png"]},
            {"id": "104", "images": ["f_1040.png", "f_1041.png"]},
        ]}), encoding="utf8")
        self.assertEqual(formal.selected_filenames(source, runtime), {
            "f_1010.png", "f_1011.png", "f_1040.png", "f_1041.png"})


class CheckedInReleaseInputsTests(unittest.TestCase):
    def test_catalog_has_no_unreviewed_missing_source(self):
        selection = generator.plan(ROOT / "agent/custom/equip_list.json",
                                   generator.DEFAULT_SOURCES, generator.INPUTS / "builtin.json")
        generator.check_missing(selection, generator.INPUTS / "missing.json")
        self.assertTrue(selection["selected"])
        selected = {row["source"] for row in selection["selected"]}
        self.assertEqual(selected, {path.name for path in generator.DEFAULT_SOURCES.glob("*.png")})

    def test_builtin_exclusions_match_shipped_templates(self):
        mapping = json.loads((generator.INPUTS / "builtin.json").read_text("utf8"))
        self.assertEqual(len(mapping.values()), len(set(mapping.values())))
        for state in generator.STATUSES:
            directory = ROOT / "assets/resource/base/image/lizhuang" / state
            self.assertEqual(set(mapping), {path.name for path in directory.glob("*.png")} - {"0_空.png"})

    def test_release_waits_for_dlc_and_uploads_separate_zip(self):
        workflow = (ROOT / ".github/workflows/install.yml").read_text("utf8")
        job = workflow.split("  build-support-ce:\n", 1)[1].split("\n  build-android:", 1)[0]
        self.assertIn('--version "$RELEASE_TAG" --check-missing extras/support-ce/missing.json', job)
        self.assertIn("if-no-files-found: error", job)
        release = workflow.split("\n  release:\n", 1)[1]
        self.assertIn("needs: [meta, build-mxu, build-support-ce, build-android, changelog]", release)
        self.assertIn("name: MaaFGO-SupportCE-DLC-${{ needs.meta.outputs.tag }}", release)
        self.assertIn("            assets/support-ce/*.zip", release)
        self.assertIn("fail_on_unmatched_files: true", release)


if __name__ == "__main__":
    unittest.main()
