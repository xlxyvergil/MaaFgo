"""Bounded in-memory card previews, isolated from battle execution and files."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct
import threading
from urllib.parse import parse_qsl

from .storage import EditorError

MAX_QUERY_BYTES = 1024
MAX_ASSET_BYTES = 1024 * 1024
MAX_PNG_BYTES = 2 * 1024 * 1024
_DIGITS = re.compile(r"[1-9][0-9]{0,9}\Z")
_NONNEGATIVE = re.compile(r"(?:0|[1-9][0-9]{0,9})\Z")
_BASENAME = re.compile(r"[A-Za-z0-9_-]{1,100}\.png\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class NpCardRequest:
    svt_id: int
    td_id: int | None
    region: str
    limit_count: int | None = None
    transform_val: int | None = None
    portrait_variant: int | None = None
    card_type: str = "np"
    color: str | None = None


@dataclass(frozen=True)
class NpCardPreview:
    png: bytes
    portrait_source: str


def parse_np_card_query(query: str) -> NpCardRequest:
    return _parse_card_query(query, "np")


def parse_face_card_query(query: str) -> NpCardRequest:
    return _parse_card_query(query, "face")


def _parse_card_query(query: str, card_type: str) -> NpCardRequest:
    """No arbitrary filenames, paths, dimensions or ambiguous repeated values."""
    if not isinstance(query, str) or len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise EditorError("invalid_np_card_query", "宝具预览参数过长")
    try:
        if re.search(r"%(?![0-9A-Fa-f]{2})", query):
            raise ValueError("invalid percent escape")
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict", max_num_fields=6)
    except (ValueError, UnicodeError) as exc:
        raise EditorError("invalid_np_card_query", "宝具预览参数格式无效") from exc
    selector = "tdId" if card_type == "np" else "color"
    allowed = {"svtId", selector, "region", "limitCount", "transformVal", "portraitVariant"}
    values = {}
    for key, value in pairs:
        if key not in allowed or key in values:
            raise EditorError("invalid_np_card_query", "宝具预览参数未知或重复")
        values[key] = value
    if not {"svtId", selector, "region"}.issubset(values) or values["region"] not in {"JP", "CN"}:
        raise EditorError("invalid_np_card_query", "请明确提供从者、宝具与 JP / CN 资源语言")
    if card_type == "face" and values["color"] not in {"arts", "buster", "quick"}:
        raise EditorError("invalid_np_card_query", "指令卡色必须是 arts / buster / quick")
    for key in ("svtId", "tdId", "portraitVariant"):
        if key in values and not _DIGITS.fullmatch(values[key]):
            raise EditorError("invalid_np_card_query", f"{key} 必须是最多 10 位的正整数")
    for key in ("limitCount", "transformVal"):
        if key in values and not _NONNEGATIVE.fullmatch(values[key]):
            raise EditorError("invalid_np_card_query", f"{key} 必须是最多 10 位的非负整数")
    return NpCardRequest(int(values["svtId"]), int(values["tdId"]) if card_type == "np" else None, values["region"],
                         int(values["limitCount"]) if "limitCount" in values else None,
                         int(values["transformVal"]) if "transformVal" in values else None,
                         int(values["portraitVariant"]) if "portraitVariant" in values else None,
                         card_type, values.get("color"))


class NpCardRenderer:
    """One renderer per install, at most eight active/queued cache misses.

    The compositor receives approved decoded pixels, never request-controlled
    paths. Its own array cache and this encoded-PNG cache are independently
    bounded. No generated images or player data are written to disk.
    """
    def __init__(self, root: str | Path, *, resolver=None, face_resolver=None, cache_entries: int = 32,
                 cache_bytes: int = 8 * 1024 * 1024, max_pending: int = 8):
        if any(type(value) is not int or value < 1 for value in (cache_entries, cache_bytes, max_pending)):
            raise ValueError("renderer limits must be positive integers")
        self.root = Path(root).resolve()
        self._resolver = resolver
        self._face_resolver = face_resolver
        self.cache_entries, self.cache_bytes = cache_entries, cache_bytes
        self._cache: OrderedDict[tuple, NpCardPreview] = OrderedDict()
        self._cache_used = 0
        self._cache_lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_pending)
        self._composer = None
        self._cv2 = self._np = None
        self._composer_metadata_digest = None

    def cache_info(self) -> dict:
        with self._cache_lock:
            return {"entries": len(self._cache), "bytes": self._cache_used}

    def _resolve(self, request: NpCardRequest):
        if request.card_type not in {"np", "face"}:
            raise EditorError("invalid_np_card_query", "指令卡预览类型无效")
        kwargs = {"svt_id": request.svt_id, "region": request.region,
                  "limit_count": request.limit_count, "transform_val": request.transform_val,
                  "portrait_variant": request.portrait_variant}
        kwargs["td_id" if request.card_type == "np" else "color"] = request.td_id if request.card_type == "np" else request.color
        resolver = self._resolver if request.card_type == "np" else self._face_resolver
        if resolver is None:
            try:
                from .np_card_metadata import resolve_np_card, resolve_face_card, NpMetadataError
            except ImportError as exc:
                raise EditorError("np_card_metadata_unavailable", "宝具预览映射未完整安装，请更新资源包", 503) from exc
            try:
                return (resolve_np_card if request.card_type == "np" else resolve_face_card)(self.root, **kwargs)
            except NpMetadataError as exc:
                raise EditorError(exc.code, exc.message, exc.status) from exc
        return resolver(self.root, **kwargs)

    def _binding(self, binding, request: NpCardRequest):
        """Validate the resolver contract before any resource read or decode."""
        sid, variant = str(binding.servant_id), str(binding.portrait_variant)
        is_np = request.card_type == "np"
        name_file, name_row = binding.np_name_file, binding.np_name_row
        if (not _DIGITS.fullmatch(sid) or not _DIGITS.fullmatch(variant)
                or binding.card_type != request.card_type
                or binding.color not in {"buster", "arts", "quick"}
                or binding.region != request.region or binding.region not in {"JP", "CN"}
                or (is_np and (type(binding.td_id) is not int or binding.td_id != request.td_id
                               or not isinstance(name_file, str) or not _BASENAME.fullmatch(name_file)
                               or type(name_row) is not int or name_row not in (0, 1)))
                or (not is_np and (binding.td_id is not None or binding.color != request.color
                                   or name_file is not None or name_row != 0))
                or not isinstance(binding.portrait_source, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", binding.portrait_source)
                or not isinstance(binding.metadata_digest, str) or not _SHA256.fullmatch(binding.metadata_digest)):
            raise EditorError("invalid_np_card_metadata", "宝具预览映射无效，请核对离线映射", 422)
        expected = {
            f"{sid}/card_servant_{variant}.png": (256, 256),
            f"_composition/shared/card_bg_np{binding.color}.png" if is_np else f"_composition/shared/card_bg_{binding.color}.png": (140, 177) if is_np else (133, 170),
            f"_composition/shared/card_icon_{binding.color}.png": (185, 133),
            f"_composition/np/{sid}/{name_file}" if is_np else f"_composition/shared/card_txt_{binding.color}.png": (256, 256) if is_np else (150, 73),
        }
        refs = tuple(binding.asset_refs)
        if len(refs) != len(expected) or {ref.relative for ref in refs} != set(expected):
            raise EditorError("invalid_np_card_metadata", "宝具预览图层映射不完整", 422)
        for ref in refs:
            if ref.pack not in {"base", "cn"} or not isinstance(ref.sha256, str) or not _SHA256.fullmatch(ref.sha256):
                raise EditorError("invalid_np_card_metadata", "宝具预览资源校验信息无效", 422)
            if binding.region == "JP" and ref.pack != "base":
                raise EditorError("invalid_np_card_metadata", "日服预览不读取其他语言资源", 422)
            localized = ref.relative.startswith("_composition/np/") or "/card_txt_" in ref.relative
            if localized and ref.pack != ("cn" if binding.region == "CN" else "base"):
                raise EditorError("invalid_np_card_metadata", "宝具名称资源语言不匹配，拒绝跨语言替代", 422)
        key = (request.card_type, sid, variant, binding.td_id, binding.color, binding.region, name_file,
               name_row, binding.portrait_source, binding.metadata_digest,
               tuple(sorted((ref.pack, ref.relative, ref.sha256) for ref in refs)))
        return key, expected, refs

    def _resource_root(self) -> Path:
        for candidate in (self.root / "assets/resource", self.root / "resource"):
            if (candidate / "base/image/CommandCards").is_dir():
                resolved = candidate.resolve()
                if not resolved.is_relative_to(self.root):
                    raise EditorError("unsafe_np_card_asset", "宝具预览资源目录越出本安装目录", 403)
                return candidate
        raise EditorError("np_card_assets_missing", "未安装指令卡合成资源，请更新完整资源包", 404)

    def _asset_bytes(self, refs, expected) -> dict[str, bytes]:
        resource = self._resource_root()
        approved = {}
        for ref in refs:
            directory = resource / ref.pack / "image/CommandCards"
            resolved_dir = directory.resolve()
            path = (directory / ref.relative).resolve()
            if not resolved_dir.is_relative_to(self.root) or not path.is_relative_to(resolved_dir):
                raise EditorError("unsafe_np_card_asset", "宝具预览资源路径越界", 403)
            try:
                with path.open("rb") as handle:
                    raw = handle.read(MAX_ASSET_BYTES + 1)
            except FileNotFoundError as exc:
                raise EditorError("np_card_assets_missing", "缺少已登记的宝具合成资源，请更新完整资源包", 404) from exc
            if len(raw) > MAX_ASSET_BYTES:
                raise EditorError("np_card_asset_invalid", "宝具合成资源超过尺寸限制", 422)
            if hashlib.sha256(raw).hexdigest() != ref.sha256:
                raise EditorError("np_card_asset_changed", "宝具合成资源校验不符，请重新安装完整资源包", 422)
            if (len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR"
                    or struct.unpack(">II", raw[16:24]) != expected[ref.relative]):
                raise EditorError("np_card_asset_invalid", "宝具合成资源 PNG 尺寸或格式无效", 422)
            approved[ref.relative] = raw
        return approved

    def _load_composer(self):
        if self._composer is not None:
            return
        try:
            import cv2
            import numpy as np
            if __package__ == "battle_editor":
                # CMD/MXU execute main.py with agent/ as the import root.
                from battle.card_composer import CommandCardComposer
            else:
                from ..battle.card_composer import CommandCardComposer
        except ImportError as exc:
            raise EditorError("np_card_dependency_missing", "宝具预览依赖未完整安装，请更新完整资源包", 503) from exc

        class ApprovedComposer(CommandCardComposer):
            approved = None

            def _read(self, relative, *, expected, region="JP", localized_only=False):
                if self.approved is None or relative not in self.approved:
                    raise EditorError("unapproved_np_card_asset", "合成器请求了未登记的资源", 403)
                image = cv2.imdecode(np.frombuffer(self.approved[relative], dtype=np.uint8), cv2.IMREAD_UNCHANGED)
                if (image is None or image.ndim != 3 or image.shape[2] != 4
                        or (image.shape[1], image.shape[0]) != expected):
                    raise EditorError("np_card_asset_invalid", "宝具合成资源无法解码为正确的透明图层", 422)
                return image

        self._cv2, self._np = cv2, np
        self._composer = ApprovedComposer(roots=(), cache_size=16, cache_bytes=16 * 1024 * 1024)

    def _cached(self, key):
        with self._cache_lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def render(self, request: NpCardRequest) -> NpCardPreview:
        binding = self._resolve(request)
        key, expected, refs = self._binding(binding, request)
        cached = self._cached(key)
        if cached is not None:
            return cached
        if not self._slots.acquire(blocking=False):
            raise EditorError("np_card_busy", "宝具预览请求较多，请稍后重试", 429)
        try:
            with self._render_lock:
                cached = self._cached(key)
                if cached is not None:
                    return cached
                approved = self._asset_bytes(refs, expected)
                self._load_composer()
                if self._composer_metadata_digest != binding.metadata_digest:
                    self._composer.clear_cache()
                    self._composer_metadata_digest = binding.metadata_digest
                self._composer.approved = approved
                try:
                    card = self._composer.compose(binding.servant_id, binding.color,
                                                  portrait_variant=binding.portrait_variant, card_type=request.card_type,
                                                  region=binding.region, np_name_file=binding.np_name_file,
                                                  np_name_row=binding.np_name_row, viewport=(1280, 720))
                    ok, encoded = self._cv2.imencode(".png", card.bgra, [self._cv2.IMWRITE_PNG_COMPRESSION, 3])
                except (ValueError, self._cv2.error) as exc:
                    raise EditorError("np_card_composition_failed", "宝具卡无法合成，请核对名称行及离线图层", 422) from exc
                finally:
                    self._composer.approved = None
                if not ok:
                    raise EditorError("np_card_composition_failed", "宝具卡 PNG 编码失败", 422)
                png = encoded.tobytes()
                if len(png) > MAX_PNG_BYTES:
                    raise EditorError("np_card_composition_failed", "宝具卡 PNG 超过预览限制", 422)
                result = NpCardPreview(png, binding.portrait_source)
                if len(png) <= self.cache_bytes:
                    with self._cache_lock:
                        self._cache[key] = result
                        self._cache_used += len(png)
                        while len(self._cache) > self.cache_entries or self._cache_used > self.cache_bytes:
                            _, removed = self._cache.popitem(last=False)
                            self._cache_used -= len(removed.png)
                return result
        finally:
            self._slots.release()
