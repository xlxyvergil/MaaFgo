"""Library favourites and creation dates live beside, never inside, battle scripts."""
import concurrent.futures
import copy
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.battle_editor.server import create_server
from agent.battle_editor.storage import BattleStorage, EditorError, _birth_time, digest


def battle():
    return {"team": {"name": "中文方案", "onFieldSvts": [{"svtId": 100100}], "backupSvts": []},
            "actions": [{"type": "attack", "attacks": []}], "future": [None, {"keep": True}]}


class LibraryStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="library 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.store = BattleStorage(self.temp.name)

    def fixture(self, name="方案.json", *, raw=None):
        path = self.store.battle_dir / name
        path.write_bytes(raw if raw is not None else json.dumps(battle(), ensure_ascii=False).encode("utf-8"))
        return path

    def rows(self, store=None):
        return {row["name"]: row for row in (store or self.store).list_battles()["battles"]}

    def test_favorite_survives_reopening_and_cancels_without_modifying_script(self):
        path = self.fixture()
        original = path.read_bytes(), path.stat().st_mtime_ns
        self.assertEqual(self.store.set_favorite(path.name, True), {"name": path.name, "favorite": True})
        reopened = BattleStorage(self.temp.name)
        self.assertTrue(self.rows(reopened)[path.name]["favorite"])
        self.assertEqual(reopened.set_favorite(path.name, False), {"name": path.name, "favorite": False})
        self.assertFalse(self.rows()[path.name]["favorite"])
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)
        metadata = json.loads((self.store.editor_dir / "library.json").read_text(encoding="utf-8"))
        self.assertFalse(metadata["files"][os.path.normcase(path.name)]["favorite"])
        self.assertNotIn("favorite", json.loads(path.read_bytes()))

    def test_damaged_script_can_be_favorited_and_stays_visible_as_damaged(self):
        path = self.fixture("损坏.json", raw=b"not JSON")
        self.store.set_favorite(path.name, True)
        row = self.rows()[path.name]
        self.assertTrue(row["favorite"])
        self.assertIn("error", row)
        self.assertEqual(path.read_bytes(), b"not JSON")

    def test_bad_names_missing_files_and_non_boolean_updates_do_not_write_metadata(self):
        self.fixture()
        for name in ("../outside.json", "..\\outside.json", "/outside.json", "a/b.json", "CON.json", ".private.json"):
            with self.subTest(name=name), self.assertRaises(EditorError):
                self.store.set_favorite(name, True)
        for value in (None, 1, 0, "true", [], {}):
            with self.subTest(value=value), self.assertRaises(EditorError):
                self.store.set_favorite("方案.json", value)
        with self.assertRaises(EditorError) as missing:
            self.store.set_favorite("不存在.json", True)
        self.assertEqual(missing.exception.status, 404)
        self.assertFalse((self.store.editor_dir / "library.json").exists())

    def test_parallel_updates_from_two_storage_objects_preserve_every_favorite(self):
        files = [self.fixture(f"方案{index}.json") for index in range(18)]
        stores = (self.store, BattleStorage(self.temp.name))
        original = {file.name: digest(file.read_bytes()) for file in files}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda index: stores[index % 2].set_favorite(files[index].name, True), range(len(files))))
        self.assertEqual({row["name"] for row in self.rows().values() if row["favorite"]}, set(original))
        self.assertEqual({file.name: digest(file.read_bytes()) for file in files}, original)
        self.assertEqual(list(self.store.editor_dir.glob(".editor-*")), [])

    def test_creation_uses_birthtime_and_never_posix_change_time(self):
        expected = datetime.fromtimestamp(1_000_000_000, timezone.utc).isoformat()
        self.assertEqual(_birth_time(SimpleNamespace(st_birthtime=1_000_000_000, st_ctime=2_000_000_000)), expected)
        self.assertIsNone(_birth_time(SimpleNamespace(st_ctime=2_000_000_000)))
        for value in (True, "1000000000", float("nan"), float("inf"), -1, 0):
            with self.subTest(value=value):
                self.assertIsNone(_birth_time(SimpleNamespace(st_birthtime=value)))

    def test_first_seen_fallback_is_persisted_and_stable_after_metadata_changes(self):
        path = self.fixture()
        first = "2020-01-02T03:04:05.000+00:00"
        with patch("agent.battle_editor.storage._birth_time", return_value=None), \
             patch("agent.battle_editor.storage.utc_now", return_value=first):
            row = self.rows()[path.name]
        self.assertEqual((row["createdAt"], row["createdAtSource"]), (first, "firstSeen"))
        os.utime(path, (1_500_000_000, 1_500_000_000))
        with patch("agent.battle_editor.storage._birth_time", return_value=None), \
             patch("agent.battle_editor.storage.utc_now", return_value="2030-01-01T00:00:00+00:00"):
            next_row = self.rows(BattleStorage(self.temp.name))[path.name]
        self.assertEqual(next_row["createdAt"], first)
        self.assertEqual(next_row["modifiedAt"], datetime.fromtimestamp(1_500_000_000, timezone.utc).isoformat())

    def test_overwrite_keeps_original_creation_time_even_when_filesystem_birth_changes(self):
        path = self.fixture()
        first = "2001-09-09T01:46:40+00:00"
        with patch("agent.battle_editor.storage._birth_time", return_value=first):
            self.assertEqual(self.rows()[path.name]["createdAt"], first)
        original = self.store.read_battle(path.name)
        edited = copy.deepcopy(original["data"])
        edited["team"]["name"] = "修改后"
        with patch("agent.battle_editor.storage._birth_time", return_value="2030-01-01T00:00:00+00:00"):
            self.store.save_battle(path.name, edited, original["hash"])
            row = self.rows()[path.name]
        self.assertEqual((row["createdAt"], row["createdAtSource"]), (first, "birthtime"))
        self.assertEqual(json.loads(path.read_bytes()), edited)

    def test_direct_overwrite_records_creation_before_first_library_listing(self):
        path = self.fixture()
        original = self.store.read_battle(path.name)
        first = "2001-09-09T01:46:40+00:00"
        with patch("agent.battle_editor.storage._birth_time", return_value=first):
            self.store.save_battle(path.name, battle(), original["hash"])
        with patch("agent.battle_editor.storage._birth_time", return_value="2030-01-01T00:00:00+00:00"):
            self.assertEqual(self.rows()[path.name]["createdAt"], first)

    def test_corrupt_metadata_does_not_break_listing_or_get_silently_replaced(self):
        path = self.fixture()
        meta = self.store.editor_dir / "library.json"
        for raw in (b"broken", b'{"version": true, "files": {}}', b'{"version": 1, "files": {"../x.json": {}}}'):
            with self.subTest(raw=raw):
                meta.write_bytes(raw)
                result = self.store.list_battles()
                self.assertEqual(result["battles"][0]["name"], path.name)
                self.assertIn("libraryWarning", result)
                self.assertNotIn("error", result["battles"][0])
                with self.assertRaises(EditorError):
                    self.store.set_favorite(path.name, True)
                self.assertEqual(meta.read_bytes(), raw)

    def test_unwritable_metadata_preserves_read_only_browsing_and_unknown_birth_dates(self):
        path = self.fixture()
        original = path.read_bytes(), path.stat().st_mtime_ns
        with patch("agent.battle_editor.storage._birth_time", return_value=None), \
             patch.object(self.store, "_atomic", side_effect=PermissionError("read only")):
            result = self.store.list_battles()
            self.assertIn("libraryWarning", result)
            row = result["battles"][0]
            self.assertIsNone(row["createdAt"])
            self.assertEqual(row["createdAtSource"], "unknown")
            with self.assertRaises(EditorError) as error:
                self.store.set_favorite(path.name, True)
            self.assertEqual(error.exception.code, "library_metadata_write")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)

    def test_unreadable_existing_metadata_is_not_mistaken_for_an_empty_library(self):
        path = self.fixture()
        self.store.set_favorite(path.name, True)
        meta = self.store.editor_dir / "library.json"
        original = meta.read_bytes()
        read = self.store._read
        def denied(target):
            if target == meta:
                raise PermissionError("metadata is unreadable")
            return read(target)
        with patch.object(self.store, "_read", side_effect=denied):
            result = self.store.list_battles()
            self.assertIn("libraryWarning", result)
            self.assertNotIn("error", result["battles"][0])
            with self.assertRaises(EditorError) as error:
                self.store.set_favorite(path.name, False)
            self.assertEqual(error.exception.code, "library_metadata_read")
        self.assertEqual(meta.read_bytes(), original)
        self.assertTrue(self.rows()[path.name]["favorite"])


class LibraryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="library HTTP 中文 ")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        static = root / "assets/web/battle-editor"
        static.mkdir(parents=True)
        (static / "index.html").write_text("editor", encoding="utf-8")
        self.server = create_server(root, 0)
        self.name = "方案 测试.json"
        self.path = self.server.storage.battle_dir / self.name
        self.path.write_text(json.dumps(battle(), ensure_ascii=False), encoding="utf-8")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.stop()
        self.thread.join(5)
        self.server.server_close()

    def request(self, path="/api/library/favorite", *, method="PUT", data=None, token=True, origin=None, project=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Editor-Token"] = self.server.token
        if origin:
            headers["Origin"] = origin
        if project:
            headers["X-Editor-Project"] = project
        connection.request(method, path, None if data is None else json.dumps(data), headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_favorite_route_and_listing_keep_existing_script_and_hash(self):
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        self.assertEqual(self.request(data={"name": self.name, "favorite": True}),
                         (200, {"name": self.name, "favorite": True}))
        status, result = self.request("/api/battles", method="GET")
        self.assertEqual(status, 200)
        row = result["battles"][0]
        self.assertTrue(row["favorite"])
        self.assertIsInstance(row["createdAt"], str)
        self.assertEqual(row["hash"], digest(before[0]))
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)

    def test_security_is_checked_before_changing_favorites(self):
        for options in ({"token": False}, {"origin": "https://untrusted.invalid"}, {"project": "f" * 64}):
            with self.subTest(options=options):
                status, _ = self.request(data={"name": self.name, "favorite": True}, **options)
                self.assertIn(status, (403, 409))
        self.assertFalse((self.server.storage.editor_dir / "library.json").exists())

    def test_bad_favorite_and_paths_are_rejected_without_rewriting_the_battle(self):
        before = self.path.read_bytes()
        for payload in ({"name": "../outside.json", "favorite": True}, {"name": self.name, "favorite": 1}, {"name": self.name}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request(data=payload)[0], 400)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_sidecar_is_a_listing_warning_and_a_precise_write_failure(self):
        meta = self.server.storage.editor_dir / "library.json"
        meta.write_bytes(b"broken")
        status, listing = self.request("/api/battles", method="GET")
        self.assertEqual(status, 200)
        self.assertIn("libraryWarning", listing)
        status, result = self.request(data={"name": self.name, "favorite": True})
        self.assertEqual((status, result["error"]["code"]), (500, "library_metadata_invalid"))
        self.assertEqual(meta.read_bytes(), b"broken")


if __name__ == "__main__":
    unittest.main()
