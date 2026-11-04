"""Visual share packets survive real PNG/JPEG transport without losing fields."""
import base64
import copy
import hashlib
import json
import random
import unittest
from unittest.mock import patch
import zlib

import cv2
import numpy as np

from agent.battle_editor.share_image import (ANCHOR, CHUNK_SIZE, FORMAT, MAX_MARKERS,
    decode_share_blocks, decode_share_image, encode_share)
from agent.battle_editor.storage import EditorError, MAX_JSON_BYTES
import agent.battle_editor.share_image as share_image


def document():
    return {"team": {"name": "一图流 · 共享方案", "onFieldSvts": [
        {"svtId": 100100, "supportType": "friend", "skillLvs": [10, 10, 10],
         "equip1": {"id": 9400340, "limitBreak": True}},
        {"svtId": 604200}, {"svtId": 504500}], "backupSvts": [None, None, None],
        "mysticCode": {"mysticCodeId": 210, "level": 10}},
        "actions": [{"type": "skill", "svt": 0, "skill": 1, "options": {"playerTarget": 1}},
                    {"type": "skill", "svt": None, "skill": 2},
                    {"type": "attack", "attacks": [{"isAuto": True}, {"isTD": True, "svt": 1}],
                     "options": {"enemyTarget": 0}}],
        "delegate": {"replaceMemberIndexes": [[1, 0]]},
        "future": {"保留字段": [None, False, 0, "中文"]}}


def multirow_document():
    rng = random.Random(2)
    return dict(document(), notes="".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(2600)))


def render_packet(encoded, *, footer=800, omit_markers=()):
    sizes = [len(marker["matrix"]) for marker in encoded["markers"]]
    row_height = max(sizes) * 5 + 70
    image = np.full((footer + ((len(sizes) + 1) // 2) * row_height + 60, 1200, 3), 255, np.uint8)
    cv2.putText(image, "MaaFGO Party & Battle", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (50, 80, 100), 2)
    for index in range(6):
        left = 40 + index * 190
        cv2.rectangle(image, (left, 250), (left + 170, 490), (170 + index * 10, 210, 230), -1)
        cv2.putText(image, str(index + 1), (left + 70, 550), cv2.FONT_HERSHEY_SIMPLEX, 1, (10, 50, 80), 2)

    def paste(matrix, x, y):
        qr = np.array([[0 if cell == "1" else 255 for cell in row] for row in matrix], np.uint8)
        qr = cv2.resize(qr, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
        image[y:y + qr.shape[0], x:x + qr.shape[1]] = qr[:, :, None]

    paste(encoded["anchor"]["matrix"], 995, 25)
    for index, marker in enumerate(encoded["markers"]):
        if index in omit_markers:
            continue
        paste(marker["matrix"], 40 + index % 2 * 570 + (552 - len(marker["matrix"]) * 5) // 2,
              footer + index // 2 * row_height)
    return image


def transported(image, scale=1, *, jpeg=None, border=0):
    if border:
        image = cv2.copyMakeBorder(image, border, border, border, border, cv2.BORDER_CONSTANT, value=(35, 35, 35))
    if scale != 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    extension = ".jpg" if jpeg is not None else ".png"
    params = [cv2.IMWRITE_JPEG_QUALITY, jpeg] if jpeg is not None else []
    return cv2.imdecode(cv2.imencode(extension, image, params)[1], cv2.IMREAD_COLOR)


class SharePacketTests(unittest.TestCase):
    def test_complete_tree_roundtrip_keeps_unknown_null_false_and_action_order(self):
        data = document()
        before = copy.deepcopy(data)
        encoded = encode_share(data)
        self.assertEqual((encoded["schema"], encoded["format"]), (1, FORMAT))
        self.assertTrue(all(set(row) <= {"0", "1"} for marker in encoded["markers"] for row in marker["matrix"]))
        result = decode_share_blocks([marker["text"] for marker in reversed(encoded["markers"])] + [ANCHOR])
        self.assertEqual(result, {"kind": "share", "schema": 1, "data": data, "warnings": []})
        self.assertEqual(data, before)
        self.assertEqual(encoded["digest"], hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest())

    def test_duplicate_scanner_reads_do_not_change_the_document(self):
        encoded = encode_share(document())
        texts = [marker["text"] for marker in encoded["markers"]]
        self.assertEqual(decode_share_blocks(texts + texts)["data"], document())

    def test_unrelated_qr_data_is_not_a_share_document(self):
        for texts in ([], ["https://example.invalid"], ["FGO party"]):
            self.assertIsNone(decode_share_blocks(texts))

    def test_header_without_complete_footer_is_a_specific_error(self):
        encoded = encode_share(document())
        for texts in ([ANCHOR], [ANCHOR] + [m["text"] for m in encoded["markers"][:-1]]):
            with self.assertRaises(EditorError) as error:
                decode_share_blocks(texts)
            self.assertEqual(error.exception.code, "share_incomplete")

    def test_conflicting_duplicate_and_mixed_documents_are_rejected(self):
        encoded = encode_share(document())
        texts = [m["text"] for m in encoded["markers"]]
        duplicate = texts[0][:-1] + ("A" if texts[0][-1] != "A" else "B")
        with self.assertRaises(EditorError) as error:
            decode_share_blocks(texts + [duplicate])
        self.assertEqual(error.exception.code, "share_packet")
        other = encode_share(dict(document(), future={"different": True}))
        with self.assertRaises(EditorError) as error:
            decode_share_blocks(texts + [m["text"] for m in other["markers"]])
        self.assertEqual(error.exception.code, "share_incomplete")

    def test_schema_indexes_and_malformed_packets_are_rejected(self):
        for texts in (["MFGS-IMAGE:2"], ["MFGS2|not-supported"],
                      ["MFGS1|1234567890abcdef|24|24|AAA"],
                      ["MFGS1|1234567890abcdef|0|25|AAA"], [1]):
            with self.subTest(texts=texts), self.assertRaises(EditorError):
                decode_share_blocks(texts)

    def test_changed_payload_and_hash_never_produce_partial_json(self):
        texts = [m["text"] for m in encode_share(document())["markers"]]
        texts[0] = texts[0][:-1] + ("A" if texts[0][-1] != "A" else "B")
        with self.assertRaises(EditorError) as error:
            decode_share_blocks(texts)
        self.assertEqual(error.exception.code, "share_integrity")

    def test_decompression_limit_and_trailing_stream_are_enforced(self):
        raw = json.dumps({"team": {}, "x": "x" * MAX_JSON_BYTES}, separators=(",", ":")).encode()
        for compressed in (zlib.compress(raw, 9), zlib.compress(b'{"team":{}}') + b"trailing"):
            packet = hashlib.sha256(raw).digest() + compressed
            group = hashlib.sha256(packet).hexdigest()[:16]
            encoded = base64.urlsafe_b64encode(packet).decode().rstrip("=")
            total = (len(encoded) + CHUNK_SIZE - 1) // CHUNK_SIZE
            self.assertLessEqual(total, MAX_MARKERS)
            texts = [f"MFGS1|{group}|{i}|{total}|{encoded[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]}" for i in range(total)]
            with self.assertRaises(EditorError) as error:
                decode_share_blocks(texts)
            self.assertEqual(error.exception.code, "share_integrity")

    def test_export_bounds_and_non_json_values_are_actionable(self):
        rng = random.Random(7)
        many = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(18000))
        for data in (None, [], {"actions": []}, {"team": {}, "actions": "bad"},
                     {"team": {}, "x": float("nan")}, {"team": {}, "notes": many},
                     {"team": {}, "x": "x" * MAX_JSON_BYTES}):
            with self.subTest(type=type(data).__name__), self.assertRaises(EditorError):
                encode_share(data)

    def test_one_megabyte_of_compressible_data_is_preserved(self):
        data = {"team": {}, "actions": [], "opaque": "x" * (1024 * 1024)}
        encoded = encode_share(data)
        self.assertEqual(decode_share_blocks([m["text"] for m in encoded["markers"]])["data"], data)


class ShareImageRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = document()
        cls.encoded = encode_share(cls.data)
        cls.image = render_packet(cls.encoded)

    def test_real_png_and_recompressed_scaled_jpeg_roundtrip(self):
        for scale, quality in ((1, None), (1, 72), (.75, 72), (.6, 75)):
            with self.subTest(scale=scale, quality=quality):
                result = decode_share_image(transported(self.image, scale, jpeg=quality))
                self.assertIsNotNone(result)
                self.assertEqual(result["data"], self.data)

    def test_extra_dark_borders_do_not_change_identity_or_actions(self):
        self.assertEqual(decode_share_image(transported(self.image, .75, jpeg=75, border=32))["data"], self.data)

    def test_multirow_footer_has_enough_scan_overlap_for_whole_codes(self):
        data = multirow_document()
        encoded = encode_share(data)
        self.assertGreater(len(encoded["markers"]), 4)
        image = render_packet(encoded, footer=800)
        for scale, quality in ((1, None), (1, 72), (.75, 72), (.6, 72)):
            with self.subTest(scale=scale, quality=quality):
                self.assertEqual(decode_share_image(transported(image, scale, jpeg=quality))["data"], data)

    def test_multirow_full_width_miss_is_recovered_by_real_column_recognition(self):
        data = multirow_document()
        encoded = encode_share(data)
        self.assertEqual(len(encoded["markers"]), 8)
        missing = encoded["markers"][-1]["text"]
        original_read = share_image._read_codes
        recovered = []

        def miss_in_multi_code_view(crop):
            # Reproduce a detector finding only 7/8 in every full-width retry.
            # Narrow crops still run the real QR detector on the same pixels.
            values = original_read(crop)
            if crop.shape[1] >= 800:
                return [value for value in values if value != missing]
            if missing in values:
                recovered.append(crop.shape)
            return values

        image = transported(render_packet(encoded))
        with patch.object(share_image, "_read_codes", side_effect=miss_in_multi_code_view):
            self.assertEqual(decode_share_image(image)["data"], data)
        self.assertTrue(recovered, "缺失分块必须从真实局部二维码读回，不能接受不完整数据")

    def test_multirow_missing_visual_marker_still_rejects_the_incomplete_packet(self):
        encoded = encode_share(multirow_document())
        self.assertEqual(len(encoded["markers"]), 8)
        image = transported(render_packet(encoded, omit_markers=(7,)))
        with self.assertRaises(EditorError) as error:
            decode_share_image(image)
        self.assertEqual(error.exception.code, "share_incomplete")
        self.assertRegex(str(error.exception), r"\b[0-7]/8\b")

    def test_multirow_upright_miss_is_recovered_from_real_rotated_column(self):
        data = multirow_document()
        encoded = encode_share(data)
        missing = encoded["markers"][-1]["text"]
        original_read = share_image._read_codes
        recovered = []

        def miss_until_column_is_rotated(crop):
            values = original_read(crop)
            # A 1200-high, 720-wide scan column becomes 720x1200 after
            # a quarter turn. Every accepted value still comes from OpenCV
            # decoding these actual image pixels; no missing text is supplied.
            if crop.shape[:2] == (720, 1200) and missing in values:
                recovered.append(crop.shape)
                return values
            return [value for value in values if value != missing]

        with patch.object(share_image, "_read_codes", side_effect=miss_until_column_is_rotated):
            self.assertEqual(decode_share_image(transported(render_packet(encoded)))["data"], data)
        self.assertTrue(recovered, "必须旋转真实局部图像读回缺失码，不得接受不完整数据")

    def test_multirow_generic_cpu_path_preserves_the_complete_packet(self):
        # This CPU path reproduces the 7/8 miss seen by the Linux CI runner,
        # even on Windows. Restore globals so subsequent tests are unaffected.
        optimized, threads = cv2.useOptimized(), cv2.getNumThreads()
        try:
            cv2.setNumThreads(1)
            cv2.setUseOptimized(False)
            data = multirow_document()
            image = transported(render_packet(encode_share(data)))
            self.assertEqual(decode_share_image(image)["data"], data)
        finally:
            cv2.setUseOptimized(optimized)
            cv2.setNumThreads(threads)

    def test_truncated_footer_never_falls_back_to_an_unrelated_game_image(self):
        with self.assertRaises(EditorError) as error:
            decode_share_image(self.image[:700])
        self.assertEqual(error.exception.code, "share_incomplete")

    def test_unrelated_game_like_screenshot_and_random_pixels_are_not_share_images(self):
        image = np.full((720, 1280, 3), (210, 160, 80), np.uint8)
        for i in range(6):
            cv2.rectangle(image, (30 + i * 205, 80), (210 + i * 205, 640), (245, 240, 230), -1)
        for source in (image, np.full((720, 1280, 3), 255, np.uint8), np.random.default_rng(7).integers(0, 256, (720, 1280, 3), np.uint8)):
            self.assertIsNone(decode_share_image(source))

    def test_bad_array_and_oversized_dimensions_fail_before_detection(self):
        for image in (None, np.zeros((4, 4), np.float32), np.empty((30001, 1), np.uint8)):
            with self.assertRaises(EditorError):
                decode_share_image(image)


if __name__ == "__main__":
    unittest.main()
