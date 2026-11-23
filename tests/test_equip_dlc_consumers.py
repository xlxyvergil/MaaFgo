"""Installed CE packs supply real team/list images without changing local priority."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from formation_test_support import formation as formation
from maa.agent.agent_server import AgentServer
with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls):
    from inventory_scan_action import BuildPlayerInventory
    from bond_completion_action import CompleteBondFormation
from battle_editor.catalog import build_catalog, resolve_media
from battle_editor.bond_optimizer import BondPlanner
from battle_editor import screenshot_import
from support_ce import install_extension


class EquipDLCConsumerTests(unittest.TestCase):
    eid = 9408540
    filename = "f_94085400.png"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "assets/resource/base/image"
        self.base.mkdir(parents=True)
        self.files = {}
        self.pixels = {}
        for index, (kind, size) in enumerate((("list", (147, 56)), ("team", (152, 86)),
                                             ("非满破", (158, 44)), ("满破", (158, 44)))):
            image = Image.fromarray(np.random.default_rng(510 + index).integers(
                0, 255, (size[1], size[0], 3), dtype=np.uint8)).convert("RGBA")
            if kind != "满破":
                image = image.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
            output = io.BytesIO()
            image.save(output, "PNG")
            self.files[f"{kind}/{self.filename}"] = output.getvalue()
            self.pixels[kind] = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        self.install()
        custom = self.root / "agent/custom"
        custom.mkdir(parents=True)
        (custom / "equip_list.json").write_text(json.dumps({"equips": [{
            "id": str(self.eid), "name": "冠位奖励", "images": [self.filename],
            "important": 0, "resource": "dlc"}]}), encoding="utf-8")

    def install(self, kinds=("list", "team", "非满破", "满破")):
        files = {name: raw for name, raw in self.files.items() if name.split("/")[0] in kinds}
        entry = {"id": self.eid, "name": "冠位奖励", "templates": {
            state: f"{state}/{self.filename}" for state in ("满破", "非满破") if state in kinds}}
        images = {kind: f"{kind}/{self.filename}" for kind in ("list", "team") if kind in kinds}
        if images:
            entry["images"] = images
        manifest = {"schemaVersion": 1, "type": "maafgo-support-ce", "name": "礼装扩展",
                    "version": "test", "equips": [entry],
                    "sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            for name, raw in files.items():
                archive.writestr(name, raw)
        install_extension(self.root, output.getvalue())

    def helper(self, cls=formation.AutoFormationFromChaldea):
        helper = cls()
        helper.context = NS(get_node_data=lambda _name: {"attach": {"loaded_resource_package": "base"}})
        with patch.object(formation, "_PROJECT_DIR", str(self.root)):
            helper._init_paths()
        return helper

    def local_image(self, kind, pixels):
        path = self.base / "EquipFaces" / kind / self.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".png", pixels)
        self.assertTrue(ok)
        path.write_bytes(encoded.tobytes())
        return path

    def test_formation_loads_both_dlc_kinds_from_the_bound_installation(self):
        helper = self.helper()
        for kind in ("list", "team"):
            filename, template = helper._load_equip_template(
                self.eid, getattr(helper, f"equip_{kind}_dirs"), kind)
            self.assertEqual(filename, self.filename)
            np.testing.assert_array_equal(template, self.pixels[kind])
        # A missing local team image must not make us substitute the list crop.
        self.assertNotEqual(self.pixels["list"].shape, self.pixels["team"].shape)

    def test_local_files_win_independently_for_each_recognition_kind(self):
        helper = self.helper()
        local = np.full_like(self.pixels["team"], 43)
        self.local_image("team", local)
        team = helper._load_equip_template(self.eid, helper.equip_team_dirs, "team")[1]
        warehouse = helper._load_equip_template(self.eid, helper.equip_list_dirs, "list")[1]
        np.testing.assert_array_equal(team, local)
        np.testing.assert_array_equal(warehouse, self.pixels["list"])
        self.assertEqual(resolve_media(self.root, "equip", self.filename),
                         self.base / "EquipFaces/team" / self.filename)

    def test_warehouse_indexes_dlc_and_refreshes_after_pack_replacement(self):
        scan = self.helper(BuildPlayerInventory)
        catalog = [{"id": str(self.eid), "images": [self.filename]}]
        matrix, variants, covered, missing = scan._prepare_equip_features(catalog)
        self.assertEqual((covered, missing), (1, []))
        self.assertEqual(variants, [(str(self.eid), self.filename)])
        self.assertEqual(matrix.shape[0], 1)
        self.assertIn(self.filename, scan.equip_full_templates)
        # Reinstalling an old support-only pack must not leave a stale list index.
        self.install(("满破", "非满破"))
        matrix, variants, covered, missing = scan._prepare_equip_features(catalog)
        self.assertIsNone(matrix)
        self.assertEqual((variants, covered, missing), ([], 0, [str(self.eid)]))
        self.assertEqual(scan.equip_full_templates, {})

    def test_editor_serves_dlc_team_and_preserves_common_and_support_flags(self):
        catalog = build_catalog(self.root)
        equip = next(row for row in catalog["equips"] if row["id"] == self.eid)
        self.assertTrue(equip["recognizable"])
        self.assertEqual(equip["image"], f"/media/equip/{self.filename}")
        self.assertEqual((equip["imageWidth"], equip["imageHeight"]), (152, 86))
        self.assertFalse(equip["common"])
        self.assertTrue(all(equip["supportRecognition"].values()))
        self.assertTrue(equip["supportExtension"])
        self.assertEqual(resolve_media(self.root, "equip", self.filename),
                         self.root / "config/SupportCE/team" / self.filename)

    def test_old_support_only_pack_does_not_enable_formation(self):
        self.install(("满破", "非满破"))
        equip = next(row for row in build_catalog(self.root)["equips"] if row["id"] == self.eid)
        self.assertFalse(equip["recognizable"])
        self.assertIsNone(equip["image"])
        self.assertTrue(all(equip["supportRecognition"].values()))
        helper = self.helper()
        self.assertIsNone(helper._load_equip_template(self.eid, helper.equip_team_dirs, "team"))
        self.assertIsNone(helper._load_equip_template(self.eid, helper.equip_list_dirs, "list"))

    def test_media_fallback_does_not_guess_variant_ids_or_accept_paths(self):
        for name in ("f_94085400_1_1_1_1.png", "../team/f_94085400.png", "%2e%2e.png",
                     "f_94085410.png", "f_94085400.png:other"):
            with self.subTest(name=name):
                self.assertIsNone(resolve_media(self.root, "equip", name))

    def test_bond_planner_accepts_each_dlc_purpose_without_guessing_variants(self):
        planner = object.__new__(BondPlanner)
        planner.root = self.root
        item = {"id": str(self.eid), "images": [self.filename]}
        self.assertTrue(planner._templates(item, "equip"))
        self.assertFalse(planner._templates({**item, "images": ["f_94085400_1_1_1_1.png"]}, "equip"))
        self.local_image("list", self.pixels["list"])
        self.install(("team", "满破", "非满破"))
        self.assertTrue(planner._templates(item, "equip"))
        self.install(("满破", "非满破"))
        self.assertFalse(planner._templates(item, "equip"))

    def test_native_bond_completion_prepares_both_dlc_templates(self):
        helper = self.helper(CompleteBondFormation)
        helper.equip_database = {str(self.eid): {
            "id": str(self.eid), "cost": 12, "bond": {"bonus_type": "percent", "bonus": 10}}}
        helper._prepare_bond_resources()
        self.assertEqual([row["id"] for row in helper.bond_equips], [str(self.eid)])
        np.testing.assert_array_equal(helper.equip_list_templates[self.eid][1], self.pixels["list"])
        np.testing.assert_array_equal(helper.equip_team_templates[self.eid][1], self.pixels["team"])

    def screenshot_index(self):
        custom = self.root / "agent/custom"
        (custom / "servant_list.json").write_text(json.dumps({"servants": [
            {"id": "100100", "images": ["f_1001001.png"]}]}), encoding="utf-8")
        for relative, size in (("NarrowFigures/f_1001001.png", (185, 184)),
                               ("battle/助战标记.png", (40, 25))):
            path = self.base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.random.default_rng(914).integers(
                0, 255, (size[1], size[0], 3), dtype=np.uint8)).save(path, "PNG")
        return screenshot_import.ScreenshotIndex(self.root)

    def test_screenshot_import_indexes_dlc_with_local_media_priority(self):
        index = self.screenshot_index()
        np.testing.assert_array_equal(index.equips[str(self.eid)][0][1], self.pixels["team"])
        local = np.full_like(self.pixels["team"], 57)
        self.local_image("team", local)
        index = screenshot_import.ScreenshotIndex(self.root)
        np.testing.assert_array_equal(index.equips[str(self.eid)][0][1], local)

    def test_screenshot_cached_index_refreshes_after_dlc_replacement(self):
        index = self.screenshot_index()
        self.install(("满破", "非满破"))
        replacement = Mock()
        replacement.recognize.return_value = {"kind": "screenshot"}
        with patch.object(screenshot_import, "decode_image", return_value=np.zeros((720, 1280, 3), np.uint8)), \
                patch("battle_editor.share_image.decode_share_image", return_value=None), \
                patch("battle_editor.chaldea_image.recognize_chaldea_image", return_value=None), \
                patch.object(screenshot_import, "ScreenshotIndex", return_value=replacement) as factory:
            _, current = screenshot_import.recognize_image(b"png", self.root, index)
        self.assertIs(current, replacement)
        factory.assert_called_once_with(self.root)


if __name__ == "__main__":
    unittest.main()
