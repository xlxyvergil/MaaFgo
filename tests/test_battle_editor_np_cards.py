"""Synthetic card pixels, bounded rendering and guarded loopback HTTP contracts."""
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import cv2
import numpy as np

from agent.battle_editor.np_cards import (MAX_ASSET_BYTES, NpCardRenderer,
                                         parse_np_card_query, parse_face_card_query)
from agent.battle_editor.np_card_metadata import NpAssetRef, NpCardBinding, FaceCardBinding
from agent.battle_editor.server import create_server
from agent.battle_editor.storage import EditorError


class CardFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="card-editor 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resource = self.root / "assets/resource"
        self.files = {}
        self.digest = "a" * 64
        self.row = 0
        for pack, marker in (("base", 30), ("cn", 190)):
            for color, offset in (("arts", 0), ("buster", 20), ("quick", 40)):
                for filename, size in ((f"card_bg_np{color}.png", (140, 177)),
                                       (f"card_bg_{color}.png", (133, 170)),
                                       (f"card_icon_{color}.png", (185, 133)),
                                       (f"card_txt_{color}.png", (150, 73))):
                    self.image(pack, "_composition/shared/" + filename, size, (marker, 70 + offset, 100, 170))
            name = np.zeros((256, 256, 4), dtype=np.uint8)
            name[:128] = (marker, 210, 70, 255)
            self.write(pack, "_composition/np/100/card_servant_np.png", cv2.imencode(".png", name)[1].tobytes())
        self.image("base", "100/card_servant_1.png", (256, 256), (70, 90, 120, 230))
        self.image("base", "100/card_servant_2.png", (256, 256), (170, 90, 20, 230))
        self.renderer = NpCardRenderer(self.root, resolver=self.np_binding, face_resolver=self.face_binding)

    def path(self, pack, relative):
        return self.resource / pack / "image/CommandCards" / relative

    def write(self, pack, relative, raw):
        path = self.path(pack, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.files[pack, relative] = raw

    def image(self, pack, relative, size, color):
        image = np.empty((size[1], size[0], 4), dtype=np.uint8)
        image[:] = color
        self.write(pack, relative, cv2.imencode(".png", image)[1].tobytes())

    def ref(self, pack, relative):
        return NpAssetRef(pack, relative, hashlib.sha256(self.files[pack, relative]).hexdigest())

    def facts(self, *, svt_id, region, limit_count=None, transform_val=None, portrait_variant=None):
        if svt_id != 100 or transform_val not in (None, 0):
            raise EditorError("np_unknown_servant", "没有已确认的当前形态", 404)
        variant = portrait_variant or (2 if limit_count == 3 else 1)
        source = "preview-selection" if portrait_variant is not None else "limit-count" if limit_count is not None else "preview-default"
        return variant, source, "base" if region == "JP" else "cn"

    def np_binding(self, _root, *, td_id, **kwargs):
        if td_id not in (1001, 1002, 1003):
            raise EditorError("np_unknown_td", "没有已确认的宝具", 422)
        variant, source, pack = self.facts(**kwargs)
        color = {1001: "buster", 1002: "arts", 1003: "quick"}[td_id]
        refs = (self.ref(pack, f"_composition/shared/card_bg_np{color}.png"),
                self.ref("base", f"100/card_servant_{variant}.png"),
                self.ref(pack, f"_composition/shared/card_icon_{color}.png"),
                self.ref(pack, "_composition/np/100/card_servant_np.png"))
        return NpCardBinding(100, td_id, color, kwargs["region"], variant, "card_servant_np.png", self.row, source, refs, self.digest)

    def face_binding(self, _root, *, color, **kwargs):
        variant, source, pack = self.facts(**kwargs)
        refs = (self.ref(pack, f"_composition/shared/card_bg_{color}.png"),
                self.ref("base", f"100/card_servant_{variant}.png"),
                self.ref(pack, f"_composition/shared/card_icon_{color}.png"),
                self.ref(pack, f"_composition/shared/card_txt_{color}.png"))
        return FaceCardBinding(100, color, kwargs["region"], variant, source, refs, self.digest)

    def request(self, region="JP", td=1001, extra=""):
        return parse_np_card_query(f"svtId=100&tdId={td}&region={region}" + extra)


class QueryTests(unittest.TestCase):
    def test_exact_parameters_and_optional_values(self):
        request = parse_np_card_query("svtId=100&tdId=1001&region=CN&limitCount=0&transformVal=0&portraitVariant=11")
        self.assertEqual((request.svt_id, request.td_id, request.region, request.limit_count, request.transform_val, request.portrait_variant),
                         (100, 1001, "CN", 0, 0, 11))
        self.assertEqual(parse_face_card_query("svtId=100&region=JP&color=quick").card_type, "face")

    def test_unknown_duplicate_paths_unbounded_dimensions_and_bad_encodings_rejected(self):
        prefix = "svtId=100&tdId=1001&region=JP"
        for extra in ("&svtId=100", "&color=arts", "&root=C:/", "&viewport=7680x4320", "&filename=../name.png",
                      "&row=1", "&npNameFile=x.png", "&bad=%FF", "&bad=%ZZ", "&bad", "&limitCount=1&limitCount=2"):
            with self.subTest(extra=extra), self.assertRaises(EditorError):
                parse_np_card_query(prefix + extra)
        for key in ("svtId", "tdId"):
            for value in ("0", "-1", "true", "False", "1.0", "1e3", "01", "99999999999", "../1", "C:1", "1%2F2", "1%252F2", ""):
                with self.subTest(key=key, value=value), self.assertRaises(EditorError):
                    parse_np_card_query(prefix.replace(f"{key}=" + ("100" if key == "svtId" else "1001"), f"{key}={value}"))
        for query in ("", "svtId=100&tdId=1001", prefix.replace("JP", "NA"), prefix + "&x=" + "a" * 1024):
            with self.assertRaises(EditorError):
                parse_np_card_query(query)
        for query in ("svtId=100&region=JP&color=Buster", "svtId=100&region=JP&color=arts&tdId=1001"):
            with self.assertRaises(EditorError):
                parse_face_card_query(query)


class RendererTests(CardFixture):
    def test_np_and_face_png_pixels_regions_alpha_and_no_disk_output(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        images = []
        for region in ("JP", "CN"):
            for request in (self.request(region), parse_face_card_query(f"svtId=100&region={region}&color=buster")):
                result = self.renderer.render(request)
                image = cv2.imdecode(np.frombuffer(result.png, np.uint8), cv2.IMREAD_UNCHANGED)
                self.assertEqual(image.shape[2], 4)
                self.assertGreater(image.shape[0], 150)
                self.assertLess(image.shape[0], 400)
                self.assertTrue(np.any(image[:, :, 3] == 0))
                self.assertTrue(np.any(image[:, :, 3] >= 128))
                images.append(result.png)
        self.assertEqual(len(set(images)), 4)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_encoded_cache_hit_needs_no_asset_read_or_composition(self):
        first = self.renderer.render(self.request())
        with patch.object(self.renderer, "_asset_bytes", side_effect=AssertionError("unexpected read")):
            second = self.renderer.render(self.request())
        self.assertIs(first, second)
        self.assertEqual(self.renderer.cache_info()["entries"], 1)

    def test_portrait_source_is_not_borrowed_from_another_request_cache_entry(self):
        self.assertEqual(self.renderer.render(self.request()).portrait_source, "preview-default")
        self.assertEqual(self.renderer.render(self.request(extra="&limitCount=0")).portrait_source, "limit-count")
        self.assertEqual(self.renderer.render(self.request(extra="&portraitVariant=1")).portrait_source, "preview-selection")

    def test_metadata_change_invalidates_both_encoded_and_compositor_caches(self):
        before = self.renderer.render(self.request()).png
        self.image("base", "100/card_servant_1.png", (256, 256), (200, 20, 30, 255))
        self.digest = "b" * 64
        after = self.renderer.render(self.request()).png
        self.assertNotEqual(before, after)

    def test_unregistered_cn_portrait_override_is_never_read(self):
        expected = self.renderer.render(self.request("CN")).png
        self.renderer = NpCardRenderer(self.root, resolver=self.np_binding, face_resolver=self.face_binding)
        self.image("cn", "100/card_servant_1.png", (256, 256), (255, 0, 0, 255))
        self.assertEqual(self.renderer.render(self.request("CN")).png, expected)

    def test_packaged_resource_layout_is_kept_within_this_install(self):
        packed = self.root / "resource"
        self.resource.rename(packed)
        self.resource = packed
        self.assertTrue(self.renderer.render(self.request("CN")).png.startswith(b"\x89PNG"))
        self.assertEqual(self.renderer._resource_root(), packed)

    def test_missing_localized_names_and_text_never_fall_back_to_jp(self):
        for card_type, relative in (("np", "_composition/np/100/card_servant_np.png"),
                                    ("face", "_composition/shared/card_txt_buster.png")):
            with self.subTest(card_type=card_type):
                self.path("cn", relative).unlink()
                request = self.request("CN") if card_type == "np" else parse_face_card_query("svtId=100&region=CN&color=buster")
                with self.assertRaises(EditorError) as error:
                    self.renderer.render(request)
                self.assertEqual(error.exception.status, 404)

    def test_empty_name_row_is_an_error_without_name_substitution(self):
        self.row = 1
        with self.assertRaises(EditorError) as error:
            self.renderer.render(self.request())
        self.assertEqual(error.exception.code, "np_card_composition_failed")

    def test_asset_digest_size_and_png_header_are_checked_before_decode(self):
        relative = "100/card_servant_1.png"
        self.path("base", relative).write_bytes(b"corrupt")
        with self.assertRaises(EditorError) as error:
            self.renderer.render(self.request())
        self.assertEqual(error.exception.code, "np_card_asset_changed")
        for raw in (b"x" * (MAX_ASSET_BYTES + 1), b"not a PNG"):
            self.write("base", relative, raw)
            with self.assertRaises(EditorError) as error:
                self.renderer.render(self.request())
            self.assertEqual(error.exception.code, "np_card_asset_invalid")

    def test_unsafe_or_unapproved_metadata_paths_are_rejected_before_reads(self):
        original = self.np_binding(self.root, svt_id=100, td_id=1001, region="JP")
        for change in ({"np_name_file": "../other.png"}, {"np_name_row": True}, {"metadata_digest": "x"},
                       {"portrait_source": "default\r\nInjected: yes"}, {"asset_refs": original.asset_refs[:-1]}):
            self.renderer = NpCardRenderer(self.root, resolver=lambda *_args, **_kwargs: replace(original, **change))
            with patch.object(self.renderer, "_asset_bytes", side_effect=AssertionError("unapproved read")):
                with self.assertRaises(EditorError):
                    self.renderer.render(self.request())

    def test_localized_names_cannot_be_bound_to_a_different_language(self):
        original = self.np_binding(self.root, svt_id=100, td_id=1001, region="CN")
        refs = (*original.asset_refs[:-1], replace(original.asset_refs[-1], pack="base"))
        self.renderer = NpCardRenderer(self.root, resolver=lambda *_args, **_kwargs: replace(original, asset_refs=refs))
        with patch.object(self.renderer, "_asset_bytes", side_effect=AssertionError("unapproved read")):
            with self.assertRaises(EditorError) as error:
                self.renderer.render(self.request("CN"))
        self.assertEqual(error.exception.code, "invalid_np_card_metadata")

    def test_asset_symlink_cannot_escape_the_current_install(self):
        path = self.path("base", "100/card_servant_1.png")
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "portrait.png"
            target.write_bytes(path.read_bytes())
            path.unlink()
            try:
                path.symlink_to(target)
            except OSError:
                self.skipTest("当前 Windows 账户没有创建符号链接权限")
            with self.assertRaises(EditorError) as error:
                self.renderer.render(self.request())
            self.assertEqual(error.exception.status, 403)

    def test_encoded_cache_has_entry_and_byte_bounds(self):
        renderer = NpCardRenderer(self.root, resolver=self.np_binding, face_resolver=self.face_binding, cache_entries=2)
        with patch.object(renderer, "_asset_bytes", wraps=renderer._asset_bytes) as reads:
            for td in (1001, 1002, 1003, 1001):
                renderer.render(self.request(td=td))
            self.assertEqual(reads.call_count, 4)
        self.assertEqual(renderer.cache_info()["entries"], 2)
        tiny = NpCardRenderer(self.root, resolver=self.np_binding, cache_bytes=1)
        tiny.render(self.request())
        self.assertEqual(tiny.cache_info(), {"entries": 0, "bytes": 0})

    def test_three_different_np_cards_can_be_requested_concurrently(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.renderer.render, self.request(td=td)) for td in (1001, 1002, 1003)]
            results = [future.result(3).png for future in futures]
        self.assertEqual(len(set(results)), 3)
        self.assertEqual(self.renderer.cache_info()["entries"], 3)

    def test_identical_simultaneous_cards_share_one_render_and_queue_is_bounded(self):
        entered, release = threading.Event(), threading.Event()
        original = self.renderer._asset_bytes
        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(4))
            return original(*args)
        with patch.object(self.renderer, "_asset_bytes", side_effect=blocked) as reads, ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self.renderer.render, self.request()) for _ in range(8)]
            try:
                self.assertTrue(entered.wait(2))
                deadline = time.monotonic() + 2
                while self.renderer._slots._value and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(self.renderer._slots._value, 0)
                with self.assertRaises(EditorError) as error:
                    self.renderer.render(self.request())
                self.assertEqual(error.exception.status, 429)
            finally:
                release.set()
            results = [future.result(3).png for future in futures]
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(reads.call_count, 1)

    def test_missing_image_dependency_is_a_readable_error(self):
        original = __import__
        def import_without_cv2(name, *args, **kwargs):
            if name == "cv2":
                raise ImportError("synthetic unavailable dependency")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=import_without_cv2):
            with self.assertRaises(EditorError) as error:
                self.renderer.render(self.request())
        self.assertEqual(error.exception.status, 503)
        self.assertIsNone(self.renderer._composer)

    def test_direct_cmd_import_root_can_load_composer_without_maa(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "from battle_editor.np_cards import NpCardRenderer; "
                "renderer = NpCardRenderer(sys.argv[2]); renderer._load_composer(); "
                "assert renderer._composer is not None; "
                "assert not any(n == 'maa' or n.startswith('maa.') for n in sys.modules)")
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", code,
                                 str(Path(__file__).resolve().parents[1] / "agent"), str(self.root)],
                                cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_sidecar_is_translated_to_a_readable_metadata_error(self):
        with self.assertRaises(EditorError) as error:
            NpCardRenderer(self.root).render(self.request())
        self.assertEqual(error.exception.code, "np_metadata_unavailable")
        self.assertEqual(error.exception.status, 503)


class HTTPTests(CardFixture):
    def setUp(self):
        super().setUp()
        web = self.root / "assets/web/battle-editor"
        web.mkdir(parents=True)
        (web / "index.html").write_text("<!doctype html><title>cards</title>", encoding="utf-8")
        self.server = create_server(self.root, 0)
        self.server._card_renderer = self.renderer
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.stop();self.thread.join(5);self.server.server_close()

    def http(self, path, *, method="GET", headers=None, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        final_headers = {"X-Editor-Token": self.server.token} if token else {}
        final_headers.update(headers or {})
        connection.request(method, path, headers=final_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_get_head_png_headers_and_shared_capability_contract(self):
        path = "/api/np-card?svtId=100&tdId=1001&region=JP"
        status, headers, raw = self.http(path)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(int(headers["Content-Length"]), len(raw))
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(headers["X-Editor-Card-Portrait-Source"], "preview-default")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertIn("img-src 'self' data: blob:", headers["Content-Security-Policy"])
        status, head, body = self.http(path, method="HEAD")
        self.assertEqual(status, 200);self.assertEqual(body, b"")
        self.assertEqual(head["Content-Length"], headers["Content-Length"])
        status, _, raw = self.http("/api/command-card?svtId=100&region=CN&color=arts")
        self.assertEqual(status, 200);self.assertTrue(raw.startswith(b"\x89PNG"))
        status, _, raw = self.http("/api/capabilities")
        self.assertEqual(json.loads(raw)["npCardPreview"], {"enabled": True, "regions": ["JP", "CN"],
                         "defaultRegion": "JP", "endpoint": "/api/np-card", "faceEndpoint": "/api/command-card"})
        self.assertEqual(list(self.server.storage.battle_dir.iterdir()), [])

    def test_security_guards_reject_before_renderer_work(self):
        path = "/api/np-card?svtId=100&tdId=1001&region=JP"
        with patch.object(self.renderer, "render", side_effect=AssertionError("untrusted render")):
            self.assertEqual(self.http(path, token=False)[0], 403)
            for headers in ({"Origin": "https://untrusted.example"}, {"Origin": "null"},
                            {"Host": "untrusted.example"}, {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(self.http(path, headers=headers)[0], 403)

    def test_query_errors_and_wrong_method_do_not_reach_renderer(self):
        with patch.object(self.renderer, "render", side_effect=AssertionError("invalid render")):
            for path in ("/api/np-card", "/api/np-card?svtId=1&tdId=2&region=JP&tdId=3",
                         "/api/command-card?svtId=1&region=JP&color=EXTRA",
                         "/api/np-card?svtId=1&tdId=2&region=JP&root=C:/private"):
                status, headers, raw = self.http(path)
                self.assertEqual(status, 400);self.assertIn("application/json", headers["Content-Type"])
                self.assertIn("error", json.loads(raw))
            self.assertEqual(self.http("/api/np-card?svtId=100&tdId=1001&region=JP", method="DELETE")[0], 404)

    def test_unknown_binding_or_missing_assets_are_json_errors_for_text_fallback(self):
        status, headers, raw = self.http("/api/np-card?svtId=100&tdId=999&region=JP")
        self.assertEqual(status, 422);self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(raw)["error"]["code"], "np_unknown_td")
        self.path("cn", "_composition/np/100/card_servant_np.png").unlink()
        status, headers, raw = self.http("/api/np-card?svtId=100&tdId=1001&region=CN")
        self.assertEqual(status, 404);self.assertIn("error", json.loads(raw))


if __name__ == "__main__":
    unittest.main()
