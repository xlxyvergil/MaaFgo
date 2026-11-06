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

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/fgo-equip-icon-pipeline/scripts"
if not all((SCRIPTS / name).is_file() for name in ("build_support_extension.py", "sync_equip_icons.py")):
    raise unittest.SkipTest("本地礼装资源生成 skill 未安装；安装该 skill 后可运行生成器回归")


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


generator = module("support_extension_generator", SCRIPTS / "build_support_extension.py")
formal = module("support_extension_formal", SCRIPTS / "sync_equip_icons.py")


class SupportExtensionGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        y, x = np.mgrid[:68, :150]
        pixels = np.stack((x % 256, (x * 3 + y * 2) % 256, y * 3 % 256,
                           np.full_like(x, 255)), axis=-1).astype("uint8")
        for eid in (101, 102):
            Image.fromarray(pixels).save(self.sources / f"f_{eid}0.png")
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
                np.testing.assert_array_equal(np.array(actual.convert("RGBA")), np.array(expected.convert("RGBA")))

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


if __name__ == "__main__":
    unittest.main()
