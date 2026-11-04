"""Share image export/import uses the authenticated, read-only editor routes."""
import copy
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from agent.battle_editor.server import create_server


def battle():
    return {"team": {"name": "一图流回导测试", "onFieldSvts": [
        {"svtId": 100100, "skillLvs": [10, 10, 10]},
        {"svtId": 504500, "supportType": "friend", "equip1": {"id": 9400340}}, None],
        "backupSvts": [None, None, None], "mysticCode": {"mysticCodeId": 210}},
        "actions": [{"type": "skill", "svt": 0, "skill": 0, "options": {"playerTarget": 1}},
                    {"type": "attack", "attacks": [{"isAuto": True}, {"isTD": True, "svt": 0, "card": -1}]}],
        "delegate": {"replaceMemberIndexes": []}, "unknown": {"保留": [None, True, "中文备注"]}}


def marker_image(encoded, *, header_only=False, fmt="PNG"):
    markers = [] if header_only else encoded["markers"]
    max_modules = max((len(marker["matrix"]) for marker in markers), default=40)
    stride = max_modules * 5 + 54
    image = Image.new("RGB", (1200, max(320, 200 + ((len(markers) + 1) // 2) * stride)), "white")
    draw = ImageDraw.Draw(image)
    def paste(matrix, left, top, scale):
        for y, row in enumerate(matrix):
            for x, cell in enumerate(row):
                if cell == "1":
                    draw.rectangle((left+x*scale, top+y*scale, left+(x+1)*scale-1, top+(y+1)*scale-1), fill="black")
    paste(encoded["anchor"]["matrix"], 1000, 16, 4)
    for index, marker in enumerate(markers):
        paste(marker["matrix"], 40 + (index % 2) * 580, 200 + (index // 2) * stride, 5)
    stream = io.BytesIO()
    image.save(stream, fmt)
    return stream.getvalue()


class ShareImageHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="share-image-http 中文 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        static = self.root / "assets/web/battle-editor"
        static.mkdir(parents=True)
        (static / "index.html").write_text("offline editor", encoding="utf-8")
        self.server = create_server(self.root, 0)
        self.server._catalog = {"servants": [], "equips": [], "mysticCodes": []}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.stop()
        self.thread.join(5)
        self.server.server_close()

    def request(self, path, body, *, mime="application/json", token=True, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        headers = {"Content-Type": mime}
        if token: headers["X-Editor-Token"] = self.server.token
        if origin: headers["Origin"] = origin
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if mime == "application/json" else body
        connection.request("POST", path, payload, headers)
        response = connection.getresponse()
        status, result = response.status, json.loads(response.read())
        connection.close()
        return status, result

    def encode(self, data):
        status, result = self.request("/api/share-image/encode", {"data": data})
        self.assertEqual(status, 200, result)
        self.assertEqual((result["schema"], result["format"]), (1, "maafgo-share-image"))
        return result

    def test_real_roundtrip_needs_no_device_or_recognition_resource_pack_and_never_saves(self):
        original = battle()
        before = copy.deepcopy(original)
        encoded = self.encode(original)
        for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg")):
            with self.subTest(fmt=fmt):
                status, result = self.request("/api/import-image", marker_image(encoded, fmt=fmt), mime=mime)
                self.assertEqual(status, 200, result)
                self.assertEqual((result["kind"], result["schema"]), ("share", 1))
                self.assertEqual(result["data"], original)
                self.assertTrue(result["validation"]["valid"], result["validation"])
        self.assertEqual(original, before)
        self.assertEqual(list(self.server.storage.battle_dir.iterdir()), [])
        self.assertEqual(list(self.server.storage.draft_dir.iterdir()), [])

    def test_encode_requires_valid_json_same_origin_and_token(self):
        for invalid in (None, [], "not a battle"):
            status, result = self.request("/api/share-image/encode", {"data": invalid})
            self.assertIn(status, (400, 422), result)
        for options in ({"token": False}, {"origin": "https://untrusted.invalid"}):
            self.assertEqual(self.request("/api/share-image/encode", {"data": battle()}, **options)[0], 403)

    def test_missing_opencv_encoder_returns_an_actionable_capability_error(self):
        with patch.dict(sys.modules, {"cv2": SimpleNamespace()}):
            status, result = self.request("/api/share-image/encode", {"data": battle()})
        self.assertEqual(status, 503, result)
        self.assertEqual(result["error"]["code"], "share_encoder_unavailable")

    def test_missing_data_region_is_reported_as_damaged_share_instead_of_game_screenshot(self):
        encoded = self.encode(battle())
        status, result = self.request("/api/import-image", marker_image(encoded, header_only=True), mime="image/png")
        self.assertEqual(status, 422, result)
        self.assertTrue(result["error"]["code"].startswith("share_"), result)
        self.assertEqual(list(self.server.storage.battle_dir.iterdir()), [])

    def test_worker_accepts_full_json_response_larger_than_old_one_mib_pipe_limit(self):
        data = battle()
        data["unknown"]["long"] = "x" * (1024 * 1024 + 100)
        encoded = self.encode(data)
        status, result = self.request("/api/import-image", marker_image(encoded), mime="image/png")
        self.assertEqual(status, 200, result.get("error"))
        self.assertEqual(result["data"], data)


if __name__ == "__main__":
    unittest.main()
