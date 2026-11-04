"""Offline, exact NP-card bindings for the editor's display-only renderer.

Only the maintenance sidecar supplies image names, colors and name rows. Request
parameters identify a servant/NP/form; they never select a path or render layer.
This module intentionally has no image, Maa or network dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re


_STAGES = {0: 1, 1: 2, 2: 2, 3: 3, 4: 3}
_COLORS = {1: "arts", 2: "buster", 3: "quick"}
_MAX_METADATA_BYTES = 4 * 1024 * 1024


class NpMetadataError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class NpAssetRef:
    pack: str
    relative: str
    sha256: str


@dataclass(frozen=True)
class NpCardBinding:
    servant_id: int
    td_id: int
    color: str
    region: str
    portrait_variant: int
    np_name_file: str
    np_name_row: int
    portrait_source: str
    asset_refs: tuple[NpAssetRef, ...]
    metadata_digest: str

    @property
    def card_type(self) -> str:
        return "np"


@dataclass(frozen=True)
class FaceCardBinding:
    servant_id: int
    color: str
    region: str
    portrait_variant: int
    portrait_source: str
    asset_refs: tuple[NpAssetRef, ...]
    metadata_digest: str

    @property
    def card_type(self) -> str:
        return "face"

    @property
    def td_id(self):
        return None

    @property
    def np_name_file(self):
        return None

    @property
    def np_name_row(self) -> int:
        return 0


def _integer(value, label: str, *, minimum: int = 1, optional: bool = False):
    if value is None and optional:
        return None
    if type(value) is not int or not minimum <= value <= 9_999_999_999:
        raise NpMetadataError("invalid_np_card_parameter", f"{label} 必须是有效整数", 400)
    return value


@lru_cache(maxsize=8)
def _load(path: str, modified: int, size: int) -> tuple[dict, str]:
    del modified
    if size > _MAX_METADATA_BYTES:
        raise NpMetadataError("np_metadata_unavailable", "宝具预览元数据过大", 503)
    try:
        raw = Path(path).read_bytes()
        if len(raw) > _MAX_METADATA_BYTES:
            raise ValueError("metadata too large")
        data = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ValueError("unsupported metadata schema")
        if (not isinstance(data.get("servants"), dict) or not isinstance(data.get("assets"), dict)
                or not isinstance(data.get("shared"), dict)):
            raise ValueError("missing metadata maps")
        return data, hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise NpMetadataError("np_metadata_unavailable", "宝具预览元数据无法读取", 503) from exc


def _metadata(root: Path) -> tuple[dict, str]:
    path = root / "agent/battle_editor/data/np_cards.json"
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise OSError("metadata outside install")
        stat = resolved.stat()
        return _load(str(resolved), stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        raise NpMetadataError("np_metadata_unavailable", "缺少本地宝具预览元数据", 503) from exc


def _asset(data: dict, key, relative: str, packs: set[str]) -> NpAssetRef:
    entry = data["assets"].get(key) if isinstance(key, str) else None
    if not isinstance(entry, dict):
        raise NpMetadataError("np_asset_missing", "所选宝具预览缺少已登记资源", 404)
    pack, actual, digest = entry.get("pack"), entry.get("relative"), entry.get("sha256")
    if (not isinstance(pack, str) or pack not in packs or actual != relative or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
        raise NpMetadataError("np_metadata_invalid", "宝具预览资源绑定不合法", 503)
    return NpAssetRef(pack, actual, digest)


def _shared(data: dict, region: str, color: str) -> dict:
    regional = data["shared"].get(region)
    entry = regional.get(color) if isinstance(regional, dict) else None
    if not isinstance(entry, dict):
        raise NpMetadataError("np_asset_missing", "缺少本地区的指令卡公共资源", 404)
    return entry


def _portrait(servant: dict, variant: int | None, limit: int | None) -> tuple[dict, str]:
    portraits = servant.get("portraits", [])
    portraits = [p for p in portraits if isinstance(p, dict) and type(p.get("variant")) is int and p["variant"] > 0] if isinstance(portraits, list) else []
    if not portraits:
        raise NpMetadataError("np_portrait_missing", "该从者缺少本地指令卡头像", 404)
    if limit is not None:
        group, key = ("ascension", str(_STAGES[limit])) if limit in _STAGES else ("costume", str(limit))
        if not any(p.get("group") == group and p.get("key") == key for p in portraits):
            raise NpMetadataError("np_portrait_unmapped", "指定灵基或灵衣没有精确对应的本地头像")
    if variant is not None:
        found = next((p for p in portraits if p["variant"] == variant), None)
        source = "preview-selection"
    elif limit is not None:
        group, key = ("ascension", str(_STAGES[limit])) if limit in _STAGES else ("costume", str(limit))
        found = next((p for p in portraits if p.get("group") == group and p.get("key") == key), None)
        source = "limit-count"
    else:
        found, source = min(portraits, key=lambda p: p["variant"]), "preview-default"
    if found is None:
        raise NpMetadataError("np_portrait_unmapped", "指定灵基或灵衣没有精确对应的本地头像")
    return found, source


def _name_override(region_data: dict, portrait: dict, limit: int | None, explicit_variant: bool):
    overrides = region_data.get("nameOverrides", {})
    overrides = overrides if isinstance(overrides, dict) else {}
    if limit is not None:
        if limit in _STAGES:
            ascension = overrides.get("ascension", {})
            return ascension.get(str(limit)) if isinstance(ascension, dict) else None
        # Atlas's name-overwrite costume keys use the registered image variant
        # (11,12,...), while commands.costume uses the battle character ID.
        costumes = region_data.get("costumeOverridesByChara", {})
        return costumes.get(str(limit)) if isinstance(costumes, dict) else None
    if explicit_variant:
        # The builder verifies every limit count in a displayed stage agrees.
        choices = region_data.get("previewNameOverrides", {})
        entry = choices.get(str(portrait["variant"])) if isinstance(choices, dict) else None
        if isinstance(entry, dict) and entry.get("ambiguous"):
            raise NpMetadataError("np_name_ambiguous", "该头像对应多个宝具名称状态，请指定灵基")
        return entry
    return None


def _servant(data: dict, sid: int, region: str, transform: int | None, *, require_region: bool):
    servant = data["servants"].get(str(sid))
    if not isinstance(servant, dict):
        raise NpMetadataError("np_unknown_servant", "该从者没有指令卡预览元数据", 404)
    regions = servant.get("regions", {})
    regional = regions.get(region) if isinstance(regions, dict) else None
    if transform:
        transformed = regional.get("transformId") if isinstance(regional, dict) else None
        if type(transformed) is not int or transformed <= 0:
            raise NpMetadataError("np_unknown_form", "该从者没有已确认的同地区变身映射")
        sid = transformed
        servant = data["servants"].get(str(sid))
        regions = servant.get("regions", {}) if isinstance(servant, dict) else {}
        regional = regions.get(region) if isinstance(regions, dict) else None
        if not isinstance(servant, dict) or not isinstance(regional, dict):
            raise NpMetadataError("np_unknown_form", "缺少同地区变身形态的元数据")
    if require_region and not isinstance(regional, dict):
        raise NpMetadataError("np_region_metadata_missing", "该地区缺少此从者的宝具元数据", 404)
    return sid, servant, regional


def resolve_np_card(project_root: Path | str, *, svt_id: int, td_id: int, region: str,
                    limit_count: int | None = None, transform_val: int | None = None,
                    portrait_variant: int | None = None) -> NpCardBinding:
    """Resolve exact regional facts; never infer ownership from canonical aliases.

    A missing limit count selects the first registered portrait only as an
    explicitly marked preview. An explicit unknown form/TD fails without falling
    back to the default NP, another servant, another row or another language.
    """
    sid = _integer(svt_id, "svtId")
    td = _integer(td_id, "tdId")
    limit = _integer(limit_count, "limitCount", minimum=0, optional=True)
    transform = _integer(transform_val, "transformVal", minimum=0, optional=True)
    variant = _integer(portrait_variant, "portraitVariant", optional=True)
    if region not in ("JP", "CN"):
        raise NpMetadataError("invalid_np_card_parameter", "region 只支持 JP 或 CN", 400)
    data, metadata_digest = _metadata(Path(project_root).resolve())
    sid, servant, regional = _servant(data, sid, region, transform, require_region=True)
    nps = regional.get("nps", {})
    np = nps.get(str(td)) if isinstance(nps, dict) else None
    if not isinstance(np, dict):
        raise NpMetadataError("np_unknown_td", "所选宝具不属于该地区的当前从者形态")
    color = _COLORS.get(np.get("cardType")) if type(np.get("cardType")) is int else None
    row = np.get("row")
    if color is None or type(row) is not int or row not in (0, 1):
        raise NpMetadataError(np.get("missing") or "np_metadata_invalid", "宝具卡色或名称行缺少精确映射", 503)
    portrait, portrait_source = _portrait(servant, variant, limit)
    override = _name_override(regional, portrait, limit, variant is not None)
    name = override if isinstance(override, dict) else np
    if name.get("missing"):
        raise NpMetadataError(name["missing"], "指定形态缺少同地区宝具名称图片", 404)
    filename = name.get("file")
    if not isinstance(filename, str) or re.fullmatch(r"[A-Za-z0-9_-]+\.png", filename) is None:
        raise NpMetadataError("np_metadata_invalid", "宝具名称文件绑定不合法", 503)
    name_rows = name.get("rows", [])
    if override is not None and (not isinstance(name_rows, list) or row not in name_rows):
        raise NpMetadataError("np_name_row_missing", "指定形态的宝具名称行不存在", 404)
    shared = _shared(data, region, color)
    pack = "base" if region == "JP" else "cn"
    refs = (
        _asset(data, shared.get("background"), f"_composition/shared/card_bg_np{color}.png", {pack, "base"}),
        _asset(data, portrait.get("asset"), f"{sid}/card_servant_{portrait['variant']}.png", {"base"}),
        _asset(data, shared.get("icon"), f"_composition/shared/card_icon_{color}.png", {pack, "base"}),
        _asset(data, name.get("asset"), f"_composition/np/{sid}/{filename}", {pack}),
    )
    return NpCardBinding(sid, td, color, region, portrait["variant"], filename, row,
                         portrait_source, refs, metadata_digest)


def resolve_face_card(project_root: Path | str, *, svt_id: int, region: str, color: str,
                      limit_count: int | None = None, transform_val: int | None = None,
                      portrait_variant: int | None = None) -> FaceCardBinding:
    """Resolve the same exact portrait with one of three ordinary card colors."""
    sid = _integer(svt_id, "svtId")
    limit = _integer(limit_count, "limitCount", minimum=0, optional=True)
    transform = _integer(transform_val, "transformVal", minimum=0, optional=True)
    variant = _integer(portrait_variant, "portraitVariant", optional=True)
    if region not in ("JP", "CN") or color not in ("arts", "buster", "quick"):
        raise NpMetadataError("invalid_np_card_parameter", "指令卡地区或颜色不受支持", 400)
    data, metadata_digest = _metadata(Path(project_root).resolve())
    sid, servant, _ = _servant(data, sid, region, transform, require_region=False)
    portrait, source = _portrait(servant, variant, limit)
    shared = _shared(data, region, color)
    pack = "base" if region == "JP" else "cn"
    refs = (
        _asset(data, shared.get("faceBackground"), f"_composition/shared/card_bg_{color}.png", {pack, "base"}),
        _asset(data, portrait.get("asset"), f"{sid}/card_servant_{portrait['variant']}.png", {"base"}),
        _asset(data, shared.get("icon"), f"_composition/shared/card_icon_{color}.png", {pack, "base"}),
        _asset(data, shared.get("faceText"), f"_composition/shared/card_txt_{color}.png", {pack}),
    )
    return FaceCardBinding(sid, color, region, portrait["variant"], source, refs, metadata_digest)
