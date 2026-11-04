"""MXU must contain a self-contained offline editor, without local caches."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_mxu_tests", ROOT / "tools/install-MXU.py")
mxu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mxu)


class MxuPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mxu-editor 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.output = Path(self.temp.name) / "output"
        self.data = self.source / "agent/battle_editor/data"
        self.write("assets/web/battle-editor/index.html",
                   '<script type="module" src="./assets/editor.js"></script>'
                   '<link rel="stylesheet" href="./assets/editor.css">')
        for name in ("editor.js", "editor.css"):
            self.write("assets/web/battle-editor/assets/" + name, "fixture")
        for relative in (
            "agent/battle_editor/__init__.py", "agent/battle_editor/main.py",
            "agent/battle_editor/launcher.py", "agent/battle_editor/server.py",
            "agent/battle_editor/catalog.py", "agent/battle_editor/storage.py",
            "agent/battle_editor/validation.py", "agent/battle_editor/bond_optimizer.py",
            "agent/battle_editor/special_choices.py", "agent/battle/core/special_skills.py",
            "agent/battle/core/advanced_strategy.py",
            "agent/battle_editor/equip_filters.py",
            "agent/battle_editor/np_cards.py", "agent/battle_editor/np_card_metadata.py",
            "agent/battle_editor/screenshot_import.py", "agent/battle/perception/formation_candidates.py",
            "agent/battle_editor/share_image.py",
            "agent/battle_editor/chaldea_image.py",
            "agent/battle_editor/bbc_import.py", "agent/chaldea/bbc_importer.py", "agent/chaldea/bbc_special_skills.py", "agent/chaldea/servant_types.py",
            "agent/custom/battle_editor_action.py", "agent/custom/bond_matcher.py",
            "agent/chaldea/servant_aliases.py", "agent/battle/card_composer.py",
            "agent/battle/perception/composed_card.py",
            "打开编队编辑器.cmd", "docs/HTML编队与战斗逻辑工具使用说明.md",
        ):
            self.write(relative, "fixture")
        for relative in ("agent/custom/servant_list.json", "agent/custom/equip_list.json",
                         "agent/chaldea/bbc_servant_ids.json", "agent/utils/Chaldea/equip_names_CN.json",
                         "agent/custom/bond_completion_costs.json", "agent/battle_editor/data/catalog.json"):
            self.document(relative, {})
        self.document("assets/interface.json", {"version": "source-version", "agent": {}})
        self.document("assets/tasks/编队与战斗编辑器.json", {"task": []})
        self.document("assets/options/编队与战斗编辑器.json", {"option": {}})
        self.write("agent/battle_editor/data/ui/media/formation.webp", b"webp-fixture")
        self.write("agent/battle_editor/data/media/skill/skill.png", b"skill-fixture")
        self.write("assets/resource/base/image/CommandCards/_composition/shared/frame.png", b"np-fixture")
        self.document("assets/resource/base/image/CommandCards/manifest", {
            "servants": {"100100": [{"file": "100100/card_servant_1.png"}]}})
        for pack, region in (("base", "JP"), ("cn", "CN")):
            self.document(f"assets/resource/{pack}/image/CommandCards/_composition/manifest", {
                "region": region, "shared": {"frame": {}}, "np_names": {
                    "100100": {"file": "np/100100/card_servant_np.png", "name_rows": [0]}}})
        self.document("agent/battle_editor/data/ui/manifest.json", {
            "schema": 1, "files": {"formation.webp": self.metadata(b"webp-fixture")}})
        self.document("agent/battle_editor/data/sources.json", {
            "media": [{"file": "skill/skill.png", **self.metadata(b"skill-fixture")} ]})
        self.document("agent/battle_editor/data/np_cards.json", {
            "schema": 1, "assets": {"frame": {"pack": "base", "relative": "_composition/shared/frame.png",
                                               **self.metadata(b"np-fixture")}}})
        self.document("agent/battle_editor/data/equip_filters.json", {"schemaVersion": 1, "equips": {"100": {}}})
        filter_bytes = (self.data / "equip_filters.json").read_bytes()
        self.document("agent/battle_editor/data/equip_filters_sources.json", {
            "schemaVersion": 1, "indexSha256": hashlib.sha256(filter_bytes).hexdigest()})
        self.document("agent/custom/equip_categories.json", {"schemaVersion": 1, "equips": {}})

    @staticmethod
    def metadata(raw):
        return {"sha256": hashlib.sha256(raw).hexdigest()}

    def write(self, relative, content):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def document(self, relative, content):
        self.write(relative, json.dumps(content, ensure_ascii=False))

    def test_source_and_packaged_layout_work_without_atlas_or_dump(self):
        self.write("agent/battle_editor/__pycache__/server.cpython-312.pyc", b"private-cache")
        self.write("agent/custom/local.pyo", b"private-cache")
        self.write("agent/.pytest_cache/state", "private-cache")
        with patch.object(mxu, "working_dir", self.source), patch.object(mxu, "install_path", self.output), \
             patch.object(mxu, "version", "v1.2.3-test"), patch.object(mxu, "configure_ocr_model") as ocr:
            mxu.install_resource()
            mxu.install_chores()
            mxu.install_agent()
        ocr.assert_called_once_with()
        mxu.validate_editor_bundle(self.output, packaged=True)
        interface = json.loads((self.output / "interface.json").read_text("utf-8"))
        self.assertEqual(interface["version"], "v1.2.3-test")
        self.assertEqual(interface["agent"]["child_args"], ["-u", "./agent/main.py"])
        self.assertTrue((self.output / "打开编队编辑器.cmd").is_file())
        self.assertTrue((self.output / "tasks/编队与战斗编辑器.json").is_file())
        self.assertTrue((self.output / "options/编队与战斗编辑器.json").is_file())
        self.assertFalse(any(path.name in {"__pycache__", ".pytest_cache"} or path.suffix in {".pyc", ".pyo"}
                             for path in (self.output / "agent").rglob("*")))
        for relative in ("config", "node_modules", "web/battle-editor/src"):
            self.assertFalse((self.output / relative).exists())

    def test_missing_hashed_entry_bundle_fails_before_ocr_or_copy(self):
        (self.source / "assets/web/battle-editor/assets/editor.js").unlink()
        with patch.object(mxu, "working_dir", self.source), patch.object(mxu, "install_path", self.output), \
             patch.object(mxu, "configure_ocr_model") as ocr, self.assertRaises(RuntimeError):
            mxu.install_resource()
        ocr.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_vite_source_and_external_entry_assets_are_rejected(self):
        for reference in ("https://example.invalid/editor.js", "../../outside.js"):
            with self.subTest(reference=reference):
                self.write("assets/web/battle-editor/index.html",
                           f'<script src="{reference}"></script><link rel="stylesheet" href="./assets/editor.css">')
                with self.assertRaises(RuntimeError):
                    mxu.validate_editor_bundle(self.source)
        self.write("assets/web/battle-editor/index.html",
                   '<script src="./assets/editor.js"></script><link rel="stylesheet" href="./assets/editor.css">')
        (self.source / "assets/web/battle-editor/src").mkdir()
        with self.assertRaisesRegex(RuntimeError, "静态产物"):
            mxu.validate_editor_bundle(self.source)

    def test_missing_offline_metadata_and_runtime_modules_are_rejected(self):
        for relative in ("agent/battle_editor/data/catalog.json", "agent/battle_editor/data/np_cards.json",
                         "agent/battle/card_composer.py", "agent/custom/bond_matcher.py",
                         "agent/battle_editor/screenshot_import.py", "agent/battle/perception/formation_candidates.py",
                         "agent/battle_editor/share_image.py",
                         "agent/battle_editor/chaldea_image.py",
                         "agent/battle_editor/bbc_import.py", "agent/chaldea/bbc_importer.py", "agent/chaldea/bbc_special_skills.py",
                         "agent/chaldea/servant_types.py", "agent/chaldea/bbc_servant_ids.json",
                         "agent/utils/Chaldea/equip_names_CN.json"):
            with self.subTest(relative=relative):
                path = self.source / relative
                original = path.read_bytes()
                path.unlink()
                with self.assertRaises(RuntimeError):
                    mxu.validate_editor_bundle(self.source)
                path.write_bytes(original)

    def test_all_registered_ui_skill_and_np_assets_are_hash_checked(self):
        for relative in ("agent/battle_editor/data/ui/media/formation.webp",
                         "agent/battle_editor/data/media/skill/skill.png",
                         "assets/resource/base/image/CommandCards/_composition/shared/frame.png"):
            with self.subTest(relative=relative):
                path = self.source / relative
                original = path.read_bytes()
                path.write_bytes(b"corrupted")
                with self.assertRaisesRegex(RuntimeError, "哈希"):
                    mxu.validate_editor_bundle(self.source)
                path.write_bytes(original)

    def test_manifest_traversal_cannot_read_other_install_files(self):
        self.document("agent/battle_editor/data/ui/manifest.json", {
            "schema": 1, "files": {"../../catalog.json": self.metadata(b"{}")}})
        with self.assertRaisesRegex(RuntimeError, "越界"):
            mxu.validate_editor_bundle(self.source)

    def test_real_committed_editor_resources_are_complete(self):
        # This uses checked-in metadata and layers only, never the Atlas build cache.
        mxu.validate_editor_bundle(ROOT)

    def test_runtime_requirements_cover_compositor_and_bond_process(self):
        packages = {line.split("==")[0].split(">=")[0].strip().lower()
                    for line in (ROOT / "requirements.txt").read_text("utf-8").splitlines()}
        self.assertTrue({"opencv-python-headless", "numpy", "psutil", "highspy"}.issubset(packages))


if __name__ == "__main__":
    unittest.main()
