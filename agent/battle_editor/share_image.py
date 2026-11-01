"""Offline, versioned visual packets for MaaFGO formation/turn share images.

The picture contains human-readable content plus standard QR blocks. The
decoder never infers actions from decorative text or accepts a partial packet.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import zlib

from .storage import EditorError, MAX_JSON_BYTES, decode_json


SCHEMA = 1
FORMAT = "maafgo-share-image"
ANCHOR = "MFGS-IMAGE:1"
CHUNK_SIZE = 384
MAX_MARKERS = 24
_BLOCK = re.compile(r"MFGS1\|([0-9a-f]{16})\|([0-9]{1,2})\|([0-9]{1,2})\|([A-Za-z0-9_-]{1,384})\Z")


def _matrix(text: str) -> list[str]:
    import cv2
    if not all(hasattr(cv2, name) for name in ("QRCodeEncoder_Params", "QRCodeEncoder_create", "QRCodeEncoder_CORRECT_LEVEL_M")):
        raise EditorError("share_encoder_unavailable", "当前 OpenCV 版本缺少分享图片编码能力，请更新完整桌面资源包。", 503)
    params = cv2.QRCodeEncoder_Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    # OpenCV's automatic mode handles ASCII byte blocks reliably; setting its
    # MODE_BYTE explicitly can produce malformed ECI bitstreams in some wheels.
    try:
        code = cv2.QRCodeEncoder_create(params).encode(text)
    except cv2.error as exc:
        raise EditorError("share_encode", "分享图片的数据标记生成失败，请重试。", 422) from exc
    # The encoder already includes two white modules; four more give a six
    # module quiet zone that survives modest JPEG resampling and page borders.
    code = cv2.copyMakeBorder(code, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    return ["".join("1" if value == 0 else "0" for value in row) for row in code]


def _document(raw: object) -> dict:
    if not isinstance(raw, dict) or not isinstance(raw.get("team"), dict):
        raise EditorError("share_document", "分享图片需要包含编队的 Chaldea 方案对象。", 422)
    if raw.get("actions") is not None and not isinstance(raw["actions"], list):
        raise EditorError("share_document", "方案 actions 必须是数组，无法导出分享图片。", 422)
    return raw


def encode_share(data: object) -> dict:
    """Return integer-module matrices for the browser's 1200px canvas renderer."""
    _document(data)
    try:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                         allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise EditorError("share_document", "方案包含无法导出的 JSON 数据。", 422) from exc
    if len(raw) > MAX_JSON_BYTES:
        raise EditorError("share_size", "方案超过 4 MiB，无法导出分享图片。", 413)
    digest = hashlib.sha256(raw).digest()
    packet = digest + zlib.compress(raw, level=9)
    encoded = base64.urlsafe_b64encode(packet).decode("ascii").rstrip("=")
    count = (len(encoded) + CHUNK_SIZE - 1) // CHUNK_SIZE
    if count > MAX_MARKERS:
        raise EditorError("share_capacity", "方案内容过多，超过单张分享图片的数据容量；请缩短方案或分享 JSON 文件。", 413)
    group = hashlib.sha256(packet).hexdigest()[:16]
    markers = []
    for index in range(count):
        text = f"MFGS1|{group}|{index}|{count}|{encoded[index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]}"
        markers.append({"index": index, "total": count, "matrix": _matrix(text), "text": text})
    return {"schema": SCHEMA, "format": FORMAT, "digest": digest.hex(), "groupId": group,
            "anchor": {"matrix": _matrix(ANCHOR), "text": ANCHOR}, "markers": markers,
            "modulePixels": 5, "maxMarkers": MAX_MARKERS}


def decode_share_blocks(texts: list[str]) -> dict | None:
    """Validate all visual chunks before decompressing a complete document.

    Duplicate reads of the same QR (overlapping scan tiles) are harmless;
    conflicting chunks, mixed documents and unsupported versions are rejected.
    """
    if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
        raise EditorError("share_packet", "分享图片的数据标记格式无效。", 422)
    seen = set(texts)
    identified = ANCHOR in seen
    groups: dict[str, dict[int, str]] = {}
    totals: dict[str, int] = {}
    for text in seen:
        if text.startswith("MFGS-IMAGE:"):
            if text != ANCHOR:
                raise EditorError("share_version", "这张分享图片使用了尚不支持的格式版本，请更新编辑器。", 422)
            identified = True
            continue
        if not text.startswith("MFGS"):
            continue
        identified = True
        match = _BLOCK.fullmatch(text)
        if match is None:
            raise EditorError("share_packet", "分享图片的数据标记损坏或版本不受支持，请使用完整原图。", 422)
        group, index, total, chunk = match.groups()
        index, total = int(index), int(total)
        if not 1 <= total <= MAX_MARKERS or not 0 <= index < total:
            raise EditorError("share_packet", "分享图片的数据分块编号无效，请使用完整原图。", 422)
        if group in totals and totals[group] != total:
            raise EditorError("share_packet", "分享图片的数据分块数量不一致，请使用完整原图。", 422)
        totals[group] = total
        pieces = groups.setdefault(group, {})
        if index in pieces and pieces[index] != chunk:
            raise EditorError("share_packet", "分享图片包含互相冲突的数据分块，请使用原始分享图片。", 422)
        pieces[index] = chunk
    if not identified:
        return None
    if len(groups) != 1:
        raise EditorError("share_incomplete", "分享图片缺少回导数据或混入了多份方案，请上传单张完整原图。", 422)
    group, pieces = next(iter(groups.items()))
    if len(pieces) != totals[group]:
        raise EditorError("share_incomplete", f"分享图片只识别到 {len(pieces)}/{totals[group]} 个回导标记，请上传完整清晰原图。", 422)
    encoded = "".join(pieces[index] for index in range(totals[group]))
    try:
        packet = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        if len(packet) <= 32 or hashlib.sha256(packet).hexdigest()[:16] != group:
            raise ValueError("packet checksum")
        expected, compressed = packet[:32], packet[32:]
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES or decompressor.unconsumed_tail or not decompressor.eof or decompressor.unused_data:
            raise ValueError("bounded compression stream")
        if hashlib.sha256(raw).digest() != expected:
            raise ValueError("document checksum")
        data = _document(decode_json(raw))
    except (ValueError, binascii.Error, zlib.error, EditorError) as exc:
        raise EditorError("share_integrity", "分享图片的数据校验失败，请使用未经裁切的清晰原图重新导入。", 422) from exc
    return {"kind": "share", "schema": SCHEMA, "data": data, "warnings": []}


def _read_codes(image) -> list[str]:
    import cv2
    detector = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else cv2.QRCodeDetector()
    try:
        _, decoded, _, _ = detector.detectAndDecodeMulti(image)
        return [value for value in decoded if value]
    except (cv2.error, UnicodeError):
        return []


def decode_share_image(image) -> dict | None:
    """Decode the visual share protocol, or return None for an ordinary image."""
    import cv2
    import numpy as np
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim not in (2, 3):
        raise EditorError("share_image", "无法读取分享图片。", 422)
    height, width = image.shape[:2]
    if not width or not height or width * height > 20_000_000 or max(width, height) > 30000:
        raise EditorError("image_dimensions", "图片不能超过2000万像素或单边30000像素。", 413)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    # Exports have a fixed 1200px page width. Normalize a reduced copy before
    # recognition so resampling does not leave fractional 2-3px QR modules.
    if width != 1200:
        scale = min(1200 / width, 12000 / height)
        gray = cv2.resize(gray, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_CUBIC)
    height = gray.shape[0]
    found: set[str] = set()
    # Include a usual two-row footer and the branding panel in one scan. Dense
    # packets can decode together while the same codes split across tiles fail.
    regions = [(0, min(height, 650)), (max(0, height - 1600), height)]
    scanned = set()
    for top, bottom in regions:
        if (top, bottom) not in scanned:
            found.update(_read_codes(gray[top:bottom])); scanned.add((top, bottom))
    if not any(value.startswith("MFGS") for value in found):
        return None
    # A readable header identifies a damaged/truncated share image even when
    # the data footer has been removed; it must never fall through to game OCR.
    # At least 600px overlap keeps a whole <=500px data code inside one tile.
    for bottom in range(height, 0, -600):
        top = max(0, bottom - 1200)
        if (top, bottom) not in scanned:
            found.update(_read_codes(gray[top:bottom])); scanned.add((top, bottom))
    try:
        return decode_share_blocks(list(found))
    except EditorError as error:
        if error.code != "share_incomplete":
            raise
        # A multi-code detector can miss one footer code even when a full-width
        # tile contains all its pixels. Isolate the two footer columns with
        # overlapping margins, starting at the bottom. Keep packet validation
        # strict and stop as soon as every chunk has actually been decoded.
        for top, bottom in sorted(scanned, reverse=True):
            crop = gray[top:bottom]
            if crop.shape[1] <= 720:
                continue
            for left in (0, crop.shape[1] - 720):
                column = crop[:, left:left + 720]
                # OpenCV's CPU paths can miss a finder pattern in one
                # orientation. Rotate the same local pixels without resampling;
                # only already-identified incomplete shares pay for these reads.
                for rotation in (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                    view = column if rotation is None else cv2.rotate(column, rotation)
                    found.update(_read_codes(view))
                    try:
                        return decode_share_blocks(list(found))
                    except EditorError as retry_error:
                        if retry_error.code != "share_incomplete":
                            raise
        # Mild screenshot/JPEG resampling can hinder a detector at one scale.
        # Retry only a known share image, never a whole unrelated screenshot.
        for top, bottom in sorted(scanned):
            crop = gray[top:bottom]
            _, crisp = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            found.update(_read_codes(crisp))
            reduced = cv2.resize(crop, None, fx=.75, fy=.75, interpolation=cv2.INTER_AREA)
            found.update(_read_codes(reduced))
            enlarged = cv2.resize(crop, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            found.update(_read_codes(enlarged))
        return decode_share_blocks(list(found))
