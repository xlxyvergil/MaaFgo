"""Optional ZIP packs never select their installation root or modify built-ins."""
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from agent.support_ce import (MAX_PACK_BYTES, SupportCEError, extension_entries,
                              install_extension, read_manifest, resolve_external_templates)
from agent.battle_editor.server import create_server


def png(size=(158, 44), fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", size, "blue").save(stream, fmt)
    return stream.getvalue()


def pack(*, version="1", images=None, mutate=None, extras=()):
    images = images or {"满破/f_94000100.png": png(), "非满破/f_94000100.png": png()}
    manifest = {"schemaVersion": 1, "type": "maafgo-support-ce", "name": "测试礼装扩展",
                "version": version, "equips": [{"id": 9400010, "name": "来自ＮＦＦ的爱",
                "templates": {"满破": "满破/f_94000100.png", "非满破": "非满破/f_94000100.png"}}],
                "sha256": {path: hashlib.sha256(raw).hexdigest() for path, raw in images.items()}}
    if mutate:
        mutate(manifest)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for path, raw in images.items():
            archive.writestr(path, raw)
        for path, raw in extras:
            archive.writestr(path, raw)
    return stream.getvalue()


class SupportCEPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_installs_only_into_bound_root_and_resolves_id_normalized_name(self):
        untouched = self.root / "config/Battle/keep.json"
        untouched.parent.mkdir(parents=True)
        untouched.write_text("keep")
        builtin = self.root / "resource/base/image/lizhuang/满破/keep.png"
        builtin.parent.mkdir(parents=True)
        builtin.write_bytes(b"keep")
        result = install_extension(self.root, pack())
        self.assertEqual((result["installed"], result["images"]), (1, 2))
        for name, eid in (("来自NFF的爱.png", None), ("ignored.png", 9400010), ("support-ce:9400010", None)):
            paths = resolve_external_templates(self.root, name, "任意", eid)
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(path.is_relative_to(self.root / "config/SupportCE") for path in paths))
        self.assertEqual(untouched.read_text(), "keep")
        self.assertEqual(builtin.read_bytes(), b"keep")

    def test_replacement_keeps_previous_pack_and_reloads_cached_manifest(self):
        install_extension(self.root, pack(version="1"))
        self.assertEqual(read_manifest(self.root)["version"], "1")
        install_extension(self.root, pack(version="2"))
        self.assertEqual(read_manifest(self.root)["version"], "2")
        backup = json.loads((self.root / "config/SupportCE.backup/manifest.json").read_text())
        self.assertEqual(backup["version"], "1")

    def test_invalid_pack_keeps_previous_install(self):
        install_extension(self.root, pack(version="1"))
        before = (self.root / "config/SupportCE/manifest.json").read_bytes()
        for payload in (b"", b"not a zip", pack(mutate=lambda m: m["sha256"].update({"满破/f_94000100.png": "0" * 64})),
                        pack(images={"满破/f_94000100.png": png((159, 44)), "非满破/f_94000100.png": png()}),
                        pack(images={"满破/f_94000100.png": png(fmt="JPEG"), "非满破/f_94000100.png": png()})):
            with self.subTest(length=len(payload)), self.assertRaises(SupportCEError):
                install_extension(self.root, payload)
            self.assertEqual((self.root / "config/SupportCE/manifest.json").read_bytes(), before)

    def test_traversal_absolute_special_and_case_colliding_members_are_rejected(self):
        for path in ("../outside.png", "/absolute.png", "C:/outside.png", "满破/../outside.png",
                     "满破\\outside.png", "满破/CON.png", "满破/f_94000100.PNG", "run.py"):
            with self.subTest(path=path), self.assertRaises(SupportCEError):
                install_extension(self.root, pack(extras=[(path, png())]))
        link = zipfile.ZipInfo("满破/link.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(SupportCEError):
            install_extension(self.root, pack(extras=[(link, b"../../outside")]))
        self.assertFalse((self.root / "config/SupportCE").exists())

    def test_declared_manifest_duplicates_and_missing_images_rejected(self):
        for mutate in (lambda m: m.update(schemaVersion=True),
                       lambda m: m["equips"].append(m["equips"][0]),
                       lambda m: m["equips"][0]["templates"].update({"满破": "../outside.png"}),
                       lambda m: m["equips"][0]["templates"].update({"满破": "满破/missing.png"})):
            with self.assertRaises(SupportCEError):
                install_extension(self.root, pack(mutate=mutate))

    def test_missing_or_corrupt_installed_manifest_fails_closed(self):
        self.assertEqual(read_manifest(self.root), {})
        install_extension(self.root, pack())
        path = self.root / "config/SupportCE/manifest.json"
        path.write_text('{"broken":true}')
        self.assertEqual(extension_entries(self.root), [])
        self.assertEqual(resolve_external_templates(self.root, "来自NFF的爱.png", "满破"), [])

    def test_missing_installed_image_is_not_advertised_as_available(self):
        install_extension(self.root, pack())
        (self.root / "config/SupportCE/满破/f_94000100.png").unlink()
        self.assertEqual(resolve_external_templates(self.root, "ignored.png", "满破", 9400010), [])
        self.assertEqual(len(resolve_external_templates(self.root, "ignored.png", "非满破", 9400010)), 1)

    def test_failed_final_rename_restores_previous_install(self):
        install_extension(self.root, pack(version="1"))
        original = Path.replace

        def fail_staging(path, target):
            if path.name.startswith(".SupportCE-"):
                raise OSError("simulated write failure")
            return original(path, target)

        with patch.object(Path, "replace", fail_staging), self.assertRaises(SupportCEError):
            install_extension(self.root, pack(version="2"))
        self.assertEqual(read_manifest(self.root)["version"], "1")
        self.assertEqual(list((self.root / "config").glob(".SupportCE-*")), [])

    def test_destination_symlink_outside_installation_rejected(self):
        install_extension(self.root, pack())
        target = self.root / "config/SupportCE"
        target.rename(self.root / "real-templates")
        try:
            target.symlink_to(self.root / "real-templates", target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(SupportCEError):
            install_extension(self.root, pack())
        self.assertEqual(read_manifest(self.root), {})


class SupportCEHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        web = self.root / "assets/web/battle-editor"
        web.mkdir(parents=True)
        (web / "index.html").write_text("test")
        self.server = create_server(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.server.stop()
        self.thread.join(5)
        self.server.server_close()

    def request(self, raw=None, mime="application/zip", token=True, origin=None, project=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        headers = {"Content-Type": mime}
        if token:
            headers["X-Editor-Token"] = self.server.token if token is True else token
        if origin:
            headers["Origin"] = origin
        if project:
            headers["X-Editor-Project"] = project
        connection.request("POST", "/api/support-ce/install", body=raw if raw is not None else pack(), headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_authenticated_install_clears_catalog_for_immediate_refresh(self):
        self.server._catalog = {"stale": True}
        status, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body["installed"], 1)
        self.assertIsNone(self.server._catalog)
        self.assertTrue((self.root / "config/SupportCE/manifest.json").is_file())
        refreshed = self.server.catalog()
        self.assertEqual(refreshed["supportExtension"]["installed"], 1)
        self.assertTrue(all(refreshed["equips"][0]["supportRecognition"].values()))

    def test_catalog_detects_manual_install_replacement_and_removal_without_restart(self):
        def read_catalog():
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
            connection.request("GET", "/api/catalog", headers={"X-Editor-Token": self.server.token})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            result = json.loads(response.read())
            connection.close()
            return result

        from agent.battle_editor.catalog import build_catalog
        with patch("agent.battle_editor.catalog.build_catalog", wraps=build_catalog) as build:
            empty = read_catalog()
            self.assertEqual(empty["supportExtension"]["installed"], 0)
            self.assertEqual(read_catalog(), empty)
            self.assertEqual(build.call_count, 1)

            # A manual extraction never calls install_support_ce or its invalidation.
            with zipfile.ZipFile(io.BytesIO(pack())) as archive:
                archive.extractall(self.root / "config/SupportCE")
            installed = read_catalog()
            self.assertEqual(installed["supportExtension"]["installed"], 1)
            self.assertEqual({row["id"] for row in installed["equips"]}, {9400010})
            self.assertTrue(all(installed["equips"][0]["supportRecognition"].values()))
            self.assertEqual(read_catalog(), installed)
            self.assertEqual(build.call_count, 2)

            # PNGs can arrive after the manifest during manual extraction, or be
            # removed separately. Their directory metadata changes the cache key.
            image = self.root / "config/SupportCE/满破/f_94000100.png"
            original_image = image.read_bytes()
            image.unlink()
            without_max = read_catalog()
            self.assertEqual(without_max["equips"][0]["supportRecognition"], {
                "limitBreak": False, "nonLimitBreak": True})
            self.assertEqual(build.call_count, 3)
            image.write_bytes(original_image)
            restored = read_catalog()
            self.assertTrue(all(restored["equips"][0]["supportRecognition"].values()))
            self.assertEqual(read_catalog(), restored)
            self.assertEqual(build.call_count, 4)

            # Replacing a same-sized manifest while preserving mtime must invalidate
            # both the editor catalog and the shared parsed-manifest cache.
            manifest = self.root / "config/SupportCE/manifest.json"
            old_stat = manifest.stat()
            replacement = manifest.with_suffix(".replacement")
            with zipfile.ZipFile(io.BytesIO(pack(mutate=lambda value: value["equips"][0].update(id=9400020)))) as archive:
                replacement.write_bytes(archive.read("manifest.json"))
            self.assertEqual(replacement.stat().st_size, old_stat.st_size)
            os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
            replacement.replace(manifest)
            replaced = read_catalog()
            self.assertEqual({row["id"] for row in replaced["equips"]}, {9400020})
            self.assertEqual(build.call_count, 5)

            manifest.unlink()
            removed = read_catalog()
            self.assertEqual(removed["supportExtension"]["installed"], 0)
            self.assertEqual(removed["equips"], [])
            self.assertEqual(read_catalog(), removed)
            self.assertEqual(build.call_count, 6)

    def test_auth_origin_project_type_and_empty_payload_rejected_before_install(self):
        for kwargs, expected in (({"token": False}, 403), ({"origin": "https://other.invalid"}, 403),
                                 ({"project": "another-installation"}, 409), ({"mime": "application/json"}, 415),
                                 ({"raw": b""}, 413)):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.request(**kwargs)[0], expected)
        self.assertFalse((self.root / "config/SupportCE").exists())

    def test_rejected_large_upload_returns_actionable_error_without_tcp_reset(self):
        # Real packs exceed JSON's 4 MiB limit. Sending the entire body exposes
        # Windows' unread-data TCP reset instead of only testing headers.
        payload = b"x" * (16 * 1024 * 1024 + 1)
        cases = (({"token": "expired-token"}, 403, "invalid_token"),
                 ({"project": "another-installation"}, 409, "project_mismatch"),
                 ({"origin": "https://other.invalid"}, 403, "invalid_origin"))
        with patch("agent.battle_editor.server.install_extension") as install:
            for kwargs, expected_status, expected_code in cases:
                with self.subTest(kwargs=kwargs):
                    status, body = self.request(raw=payload, **kwargs)
                    self.assertEqual(status, expected_status)
                    self.assertEqual(body["error"]["code"], expected_code)
                    if expected_code == "invalid_token":
                        self.assertIn("会话已失效", body["error"]["message"])
                        self.assertIn("重新连接", body["error"]["message"])
                        self.assertNotIn("刷新页面", body["error"]["message"])
            install.assert_not_called()
        self.assertFalse((self.root / "config/SupportCE").exists())

    def test_oversized_declared_zip_rejected_without_waiting_for_payload(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        connection.putrequest("POST", "/api/support-ce/install")
        for name, value in (("X-Editor-Token", self.server.token), ("Content-Type", "application/zip"),
                            ("Content-Length", str(MAX_PACK_BYTES + 1))):
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()


if __name__ == "__main__":
    unittest.main()
