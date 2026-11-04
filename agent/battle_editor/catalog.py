"""Offline editor catalog and a deliberately small media allowlist.

Atlas exports are processed by tools/build_battle_editor_catalog.py at maintenance
time. Opening the editor never imports the online Chaldea game-data fallback.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import re
import struct
import unicodedata
from urllib.parse import quote

from .special_choices import special_skill_catalog
from .equip_filters import catalog_filter_metadata, equip_filter_facts, read_equip_filters


_MODULE_DIR = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "_battle_editor_servant_aliases", _MODULE_DIR.parent / "chaldea" / "servant_aliases.py"
)
_ALIASES = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ALIASES)


def _read(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return copy.deepcopy(fallback)


def _int(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


def _costs(document, kind: str) -> dict:
    """Expose only authoritative, bounded integer COST facts, including zero."""
    rows = document.get(kind) if isinstance(document, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {key: value for key, value in rows.items()
            if type(value) is int and 0 <= value <= 100}


def _data_dir(root: Path) -> Path:
    return root / "agent" / "battle_editor" / "data"


def _resource_roots(root: Path) -> list[Path]:
    return [root / "assets" / "resource" / "base" / "image", root / "resource" / "base" / "image"]


def _media_filename(filename, kind: str) -> bool:
    return (isinstance(filename, str) and 0 < len(filename) <= 160
            and not any(c in filename for c in ("/", "\\", ":", "%", "\x00"))
            and (Path(filename).suffix.lower() == ".png"
                 or kind == "ui" and re.fullmatch(r"formation_[1-9][0-9]{0,9}_[0-9a-f]{16}\.webp", filename) is not None)
            and filename not in {".", ".."})


def _ui_manifest(root: Path) -> dict:
    path = _data_dir(root) / "ui" / "manifest.json"
    if not path.resolve().is_relative_to(root):
        return {}
    try:
        stat = path.stat()
    except OSError:
        return {}
    return _cached_ui_manifest(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=16)
def _cached_ui_manifest(path: str, modified: int, size: int) -> dict:
    # Keyed by file identity so hundreds of portrait resolutions read one JSON.
    value = _read(Path(path), {})
    return value if isinstance(value, dict) and value.get("schema") == 1 else {}


def resolve_media(project_root: Path | str, kind: str, filename: str) -> Path | None:
    """Resolve PNG resources and manifest-listed WebP formation portraits.

    Paths, drive letters, URL escapes, ADS names and symlinks escaping a listed
    directory are rejected. No caller supplied path ever becomes a root.
    """
    if not _media_filename(filename, kind):
        return None
    root = Path(project_root).resolve()
    subdirs = {
        "servant": ("servant_face",),
        "equip": ("EquipFaces/team",),
        "narrow": ("NarrowFigures",),
        "skill": (),
        "mystic": (),
        "ui": (),
        "status": (),
    }
    if kind not in subdirs:
        return None
    directories = [base / sub for base in _resource_roots(root) for sub in subdirs[kind]]
    status_id = None
    if kind == "status":
        # A flat, generated URL never accepts caller-provided directories. Both
        # directory and source basename are reconstructed from decimal IDs.
        match = re.fullmatch(r"status_([1-9][0-9]{0,9})_([1-9][0-9]{0,2})\.png", filename)
        if match is None:
            return None
        status_id, variant = match.groups()
        filename = f"status_servant_{variant}.png"
        directories = [base / "ServantStatus" / status_id for base in _resource_roots(root)]
    if kind == "ui":
        # UI atlases are cropped at maintenance time. Only the generated manifest
        # can expose an output, even if another image happens to be in this folder.
        files = _ui_manifest(root).get("files", {})
        if not isinstance(files, dict) or not isinstance(files.get(filename), dict):
            return None
        directories.append(_data_dir(root) / "ui" / "media")
    elif kind not in {"status", "equip"}:
        directories.append(_data_dir(root) / "media" / kind)
    for directory in directories:
        unresolved = directory / filename
        if not unresolved.is_file():
            continue
        # Even the whitelisted base directory must stay inside this install.
        resolved_dir = directory.resolve()
        if not resolved_dir.is_relative_to(root):
            continue
        candidate = unresolved.resolve()
        if status_id is not None and (unresolved.is_symlink() or directory.is_symlink()
                                      or candidate.parent.name != status_id):
            continue
        if candidate.is_relative_to(resolved_dir) and candidate.is_file():
            return candidate
    return None


def _image(root: Path, kind: str, filenames, cache: dict | None = None) -> str | None:
    for filename in filenames:
        if not isinstance(filename, str):
            continue
        key = (kind, filename)
        if cache is not None and key in cache:
            found = cache[key]
        else:
            found = bool(resolve_media(root, kind, filename))
            if cache is not None:
                cache[key] = found
        if found:
            return f"/media/{kind}/{quote(filename, safe='')}"
    return None


def _skill(root: Path, item: dict, cache: dict | None = None, overrides: dict | None = None) -> dict:
    result = copy.deepcopy(item)
    override = (overrides or {}).get(_int(item.get("id")))
    result["icon"] = (_image(root, "ui", [override], cache)
                      or _image(root, "skill", [item.get("icon")], cache))
    return result


def _webp_dimensions(handle) -> tuple[int | None, int | None]:
    """Read only the bounded RIFF/image header; never decode pixels at runtime."""
    size = handle.seek(0, 2)
    handle.seek(0)
    header = handle.read(30)
    if (len(header) < 25 or header[:4] != b"RIFF" or header[8:12] != b"WEBP"
            or struct.unpack("<I", header[4:8])[0] + 8 != size):
        return None, None
    chunk, length = header[12:16], struct.unpack("<I", header[16:20])[0]
    if 20 + length + (length & 1) > size:
        return None, None
    if chunk == b"VP8X" and length == 10 and len(header) == 30:
        # The extended canvas is stored as two 24-bit, little-endian minus-one values.
        width = int.from_bytes(header[24:27], "little") + 1
        height = int.from_bytes(header[27:30], "little") + 1
    elif chunk == b"VP8 " and length >= 10 and len(header) == 30:
        if header[20] & 1 or header[23:26] != b"\x9d\x01\x2a":
            return None, None
        width, height = (value & 0x3fff for value in struct.unpack("<HH", header[26:30]))
    elif chunk == b"VP8L" and length >= 5 and header[20] == 0x2f:
        packed = int.from_bytes(header[21:25], "little")
        if packed >> 29:  # Only lossless bitstream version zero is defined.
            return None, None
        width, height = (packed & 0x3fff) + 1, ((packed >> 14) & 0x3fff) + 1
    else:
        return None, None
    return (width, height) if 0 < width <= 32768 and 0 < height <= 32768 else (None, None)


def _dimensions(root: Path, url: str | None) -> tuple[int | None, int | None]:
    if not url:
        return None, None
    from urllib.parse import unquote
    pieces = url.split("/")
    path = resolve_media(root, pieces[2], unquote(pieces[3])) if len(pieces) == 4 else None
    if path is None:
        return None, None
    try:
        with path.open("rb") as handle:
            if path.suffix == ".webp":
                return _webp_dimensions(handle)
            header = handle.read(24)
        if len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
            width, height = struct.unpack(">II", header[16:24])
            if 0 < width <= 32768 and 0 < height <= 32768:
                return width, height
    except OSError:
        pass
    return None, None


def _common_equips(root: Path, equips: list[dict], raw_records: list[dict]) -> tuple[dict[int, list[str]], dict]:
    """Map actual support CE templates by exact, width-normalized official name.

    Fullwidth punctuation (for example 限制／零毁) is Unicode NFKC-equivalent
    to its database spelling. No substring, fuzzy or first-candidate matching is
    allowed: ambiguous and absent names remain reported and unselected.
    """
    index: dict[str, set[int]] = {}
    known_ids = {item["id"] for item in equips}
    for item in equips + raw_records:
        eid = _int(item.get("id"))
        if eid not in known_ids:
            continue
        for field in ("name", "nameJp", "name_jp"):
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                index.setdefault(unicodedata.normalize("NFKC", value.strip()), set()).add(eid)
    mapped: dict[int, list[str]] = {}
    unmatched, ambiguous = [], []
    for base in _resource_roots(root):
        directory = base / "lizhuang"
        if not directory.is_dir() or not directory.resolve().is_relative_to(root):
            continue
        for path in sorted(directory.rglob("*.png")):
            if not path.resolve().is_relative_to(directory.resolve()) or path.stem == "0_空":
                continue
            relative = path.relative_to(root).as_posix()
            ids = index.get(unicodedata.normalize("NFKC", path.stem.strip()), set())
            if len(ids) == 1:
                mapped.setdefault(next(iter(ids)), []).append(relative)
            elif ids:
                ambiguous.append({"source": relative, "ids": sorted(ids)})
            else:
                unmatched.append(relative)
    return mapped, {"count": len(mapped), "unmatched": unmatched, "ambiguous": ambiguous}


def _support_equip_recognition(root: Path, name) -> dict[str, bool]:
    """Mirror Chaldea's filename normalization, without importing its online clients.

    Team portraits and common/NFKC-name matches cannot prove support recognition.
    Directory priority mirrors SupportAction._image_dir('base', 'lizhuang').
    """
    result = {"limitBreak": False, "nonLimitBreak": False}
    if not isinstance(name, str) or not name.strip():
        return result
    fullwidth_alnum = {code: code - 0xFEE0 for start, end in
                       ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A))
                       for code in range(start, end + 1)}
    filename = name.translate(fullwidth_alnum).replace("/", "／").replace("\\", "＼") + ".png"
    directories = [root / "resource/base/image/lizhuang", root / "assets/resource/base/image/lizhuang"]
    directory = next((path for path in directories if path.is_dir()), None)
    if directory is None or not directory.resolve().is_relative_to(root):
        return result
    for key, state in (("limitBreak", "满破"), ("nonLimitBreak", "非满破")):
        path = directory / state / filename
        if path.is_file() and path.resolve().is_relative_to(directory.resolve()):
            result[key] = True
    return result


def _inventory(root: Path, kind: str, aliases: dict[int, int]) -> tuple[list[int], dict]:
    path = root / "config" / "Inventory" / f"player_{kind}.json"
    data = _read(path, {})
    if not isinstance(data, dict):
        data = {}
    records = data.get(kind, []) if isinstance(data, dict) else []
    found = set()
    for item in records if isinstance(records, list) else []:
        value = _int(item.get("id")) if isinstance(item, dict) else _int(item)
        if value is not None:
            found.add(aliases.get(value, value))
    status = {
        "available": path.is_file() and isinstance(data, dict) and isinstance(data.get(kind), list),
        "scanComplete": data.get("scan_complete") is True,
        "catalogComplete": data.get("catalog_complete") is True,
        "generatedAt": data.get("generated_at"),
        "recordCount": len(found),
        "catalogCount": _int(data.get("catalog_count")),
        "limitBreakKnown": False,
    }
    return sorted(found), status


def _np_filter(item: dict) -> dict:
    """Keep selector classifications separate from executable Atlas NP facts.

    A type and card only form a selectable pair when they coexist in one source
    variant. Summary strings are references for records without variants, never
    a source for manufacturing combinations or borrowing an alias's metadata.
    """
    raw_variants = item.get("np_variants")
    variants = copy.deepcopy(raw_variants) if isinstance(raw_variants, list) else []
    types, cards = [], []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        np_type, card = variant.get("type"), variant.get("card")
        if (not isinstance(np_type, str) or not isinstance(card, str)
                or np_type not in {"群体输出", "单体输出", "辅助宝具"}
                or card not in {"Arts", "Buster", "Quick"}):
            continue
        if np_type not in types:
            types.append(np_type)
        if card not in cards:
            cards.append(card)
    if not variants:
        for field, values in (("np_type", types), ("np_card", cards)):
            summary = item.get(field)
            if isinstance(summary, str):
                for value in summary.split("/"):
                    value = value.strip()
                    if value and value not in values:
                        values.append(value)
    return {"types": types, "cards": cards, "variants": variants}


def build_catalog(project_root: Path | str) -> dict:
    """Merge bundled facts, complete names, recognition assets and local inventory.

    Full CE names remain readable for imported plans; selection requires the
    project's curated recognition entry and an actual team template.
    """
    root = Path(project_root).resolve()
    media_cache = {}
    custom = root / "agent" / "custom"
    names_dir = root / "agent" / "utils" / "Chaldea"
    local = _read(custom / "servant_list.json", {})
    source = _read(_data_dir(root) / "catalog.json", {})
    cost_index = _read(custom / "bond_completion_costs.json", {})
    servant_costs, equip_costs = _costs(cost_index, "servants"), _costs(cost_index, "equips")
    ui = _ui_manifest(root)
    ui_servants = ui.get("servants", {})
    ui_classes = ui.get("classes", {})
    ui_class_variants = ui.get("classVariants", {})
    ui_rarities = ui.get("rarities", {})
    ui_common = ui.get("common", {})
    ui_servants = ui_servants if isinstance(ui_servants, dict) else {}
    ui_classes = ui_classes if isinstance(ui_classes, dict) else {}
    ui_class_variants = ui_class_variants if isinstance(ui_class_variants, dict) else {}
    ui_rarities = ui_rarities if isinstance(ui_rarities, dict) else {}
    ui_common = ui_common if isinstance(ui_common, dict) else {}
    extras = {str(item["id"]): item for item in source.get("servants", []) if isinstance(item, dict) and "id" in item}
    classes = local.get("class_cn", {})
    servants, aliases = [], {}
    for item in _ALIASES.canonical_servants(local.get("servants", [])):
        sid = _int(item.get("id"))
        if sid is None:
            continue
        extra = extras.get(str(sid), {})
        alt_ids = sorted({_int(alias) for alias in item.get("aliases", []) if _int(alias) is not None})
        aliases.update({alias: sid for alias in alt_ids})
        cost = next((servant_costs[str(identity)] for identity in [sid, *alt_ids]
                     if str(identity) in servant_costs), None)
        # Alias forms share one selector entry, but their imported skillIds / tdId
        # must still resolve. Keep canonical defaults while retaining every fact.
        variant_sources = [extra] + [extras.get(str(alias), {}) for alias in alt_ids]
        appearance = ui_servants.get(str(sid), {})
        appearance = appearance if isinstance(appearance, dict) else {}
        icon_overrides = {}
        for form_id in [sid] + alt_ids:
            form = ui_servants.get(str(form_id), {})
            if not isinstance(form, dict):
                continue
            for skill in form.get("activeSkills", []):
                if isinstance(skill, dict) and _int(skill.get("id")) is not None:
                    icon_overrides[_int(skill["id"])] = skill.get("icon")
        skill_variants, np_variants = {}, {}
        for variant_source in variant_sources:
            for skill in variant_source.get("skillVariants", []):
                skill_variants.setdefault(skill["id"], skill)
            for np in variant_source.get("npVariants", []):
                np_variants.setdefault(np["id"], np)
        cards = extra.get("cards", [])
        if len(cards) != 5 or any(type(card) is not int or card not in (1, 2, 3) for card in cards):
            cards = []
        portrait = _image(root, "ui", [appearance.get("formationPortrait"), appearance.get("portrait")], media_cache)
        portrait_width, portrait_height = _dimensions(root, portrait)
        class_variants = ui_class_variants.get(item.get("class"), {})
        class_variants = class_variants if isinstance(class_variants, dict) else {}
        # The editable catalog follows this project's rarity, which can differ
        # from a JP export (notably Mash). The selector's classes stay gold.
        class_icon = _image(root, "ui", [class_variants.get(str(_int(item.get("rarity")))),
                                        ui_classes.get(item.get("class"))], media_cache)
        append_skills = []
        for skill in appearance.get("appendSkills", []):
            if not isinstance(skill, dict) or _int(skill.get("num")) not in range(1, 6) or _int(skill.get("id")) is None:
                continue
            append_skills.append({"id": _int(skill["id"]), "num": _int(skill["num"]),
                                  "name": skill.get("name") or skill.get("nameJp") or "追加技能",
                                  "icon": _image(root, "ui", [skill.get("icon")], media_cache)})
        servants.append({
            "id": sid, "name": item.get("name") or extra.get("nameJp") or f"从者 {sid}",
            "nameJp": item.get("name_jp") or extra.get("nameJp", ""),
            "collectionNo": item.get("collection_no", extra.get("collectionNo")),
            "classKey": item.get("class", "unknown"),
            "className": classes.get(item.get("class"), item.get("class", "未知")),
            "rarity": item.get("rarity"), "aliases": alt_ids, "cost": cost,
            "searchAliases": item.get("search_aliases", []) + extra.get("searchAliases", []),
            "image": _image(root, "servant", item.get("images", []) + [extra.get("image")], media_cache),
            "statusImage": _image(root, "status", [f"status_{sid}_{stage}.png" for stage in (3, 1, 2)], media_cache),
            "portrait": portrait, "portraitWidth": portrait_width, "portraitHeight": portrait_height,
            "classIcon": class_icon,
            "cards": cards, "skills": [_skill(root, skill, media_cache, icon_overrides) for skill in extra.get("skills", [])],
            "skillVariants": [_skill(root, skill, media_cache, icon_overrides) for skill in skill_variants.values()],
            "appendSkills": sorted(append_skills, key=lambda skill: skill["num"]),
            "np": copy.deepcopy(extra.get("np")),
            "npVariants": copy.deepcopy(list(np_variants.values())),
            "bond": copy.deepcopy(item.get("bond")),
            "npFilter": _np_filter(item),
        })
    servants.sort(key=lambda item: (item["collectionNo"] or 0, item["id"]))

    recognized = {str(item["id"]): item for item in _read(custom / "equip_list.json", {}).get("equips", [])}
    equip_meta = {str(item["id"]): item for item in source.get("equips", [])}
    equip_filter_index = read_equip_filters(root)
    cn_names = _read(names_dir / "equip_names_CN.json", {})
    jp_names = _read(names_dir / "equip_names_JP.json", {})
    equips = []
    for key in sorted(set(recognized) | set(equip_meta) | set(cn_names) | set(jp_names), key=str):
        eid = _int(key)
        if eid is None:
            continue
        item, extra = recognized.get(key, {}), equip_meta.get(key, {})
        # Only the recognition list supplies filenames; old editor/dump images
        # and complete metadata must not expand the executable selection set.
        image = _image(root, "equip", item.get("images", []), media_cache) if key in recognized else None
        image_width, image_height = _dimensions(root, image)
        equips.append({
            "id": eid, "name": item.get("name") or cn_names.get(key) or extra.get("name") or extra.get("nameJp") or jp_names.get(key) or f"礼装 {eid}",
            "nameJp": item.get("name_jp") or extra.get("nameJp") or jp_names.get(key, ""),
            "collectionNo": item.get("collection_no", extra.get("collectionNo")),
            "rarity": item.get("rarity", extra.get("rarity")), "cost": equip_costs.get(key),
            "recognizable": key in recognized and image is not None,
            "supportRecognition": _support_equip_recognition(root, cn_names.get(key)),
            "image": image, "imageWidth": image_width, "imageHeight": image_height,
            "filters": equip_filter_facts(equip_filter_index, key) if key in recognized else None,
        })
    equips.sort(key=lambda item: (item["collectionNo"] or 0, item["id"]))
    raw_names = ([{"id": key, "name": value} for key, value in cn_names.items()]
                 + [{"id": key, "nameJp": value} for key, value in jp_names.items()]
                 + list(recognized.values()) + list(equip_meta.values()))
    common_equips, common_status = _common_equips(root, equips, raw_names)
    for equip in equips:
        equip["common"] = equip["id"] in common_equips
        equip["commonSources"] = common_equips.get(equip["id"], [])
    mystics = []
    for item in source.get("mysticCodes", []):
        code = copy.deepcopy(item)
        code["image"] = _image(root, "mystic", [item.get("image")], media_cache)
        code["skills"] = [_skill(root, skill, media_cache) for skill in item.get("skills", [])]
        mystics.append(code)
    owned_servants, servant_status = _inventory(root, "servants", aliases)
    owned_equips, equip_status = _inventory(root, "equips", {})
    return {
        "servants": servants, "equips": equips, "mysticCodes": mystics,
        "specialSkillChoices": special_skill_catalog(),
        "equipFilters": catalog_filter_metadata(equip_filter_index),
        "inventory": {"servants": owned_servants, "equips": owned_equips,
                      "servantsStatus": servant_status, "equipsStatus": equip_status},
        "commonEquips": common_status,
        "uiAssets": {
            "classes": {key: url for key, filename in ui_classes.items()
                        if (url := _image(root, "ui", [filename], media_cache))},
            "classVariants": {
                key: {rarity: url for rarity, filename in variants.items()
                      if (url := _image(root, "ui", [filename], media_cache))}
                for key, variants in ui_class_variants.items() if isinstance(variants, dict)
            },
            "rarities": {key: url for key, filename in ui_rarities.items()
                         if (url := _image(root, "ui", [filename], media_cache))},
            "support": _image(root, "ui", [ui_common.get("support")], media_cache),
            "background": _image(root, "ui", [ui_common.get("background")], media_cache),
            "npFrames": {color: _image(root, "ui", [np_frames.get(color)], media_cache)
                         for color in ("arts", "buster", "quick")}
                        if isinstance(np_frames := ui_common.get("npFrames"), dict) else {},
            "masterSkill": _image(root, "ui", [ui_common.get("masterSkill")], media_cache),
            "masterSkillDisabled": _image(root, "ui", [ui_common.get("masterSkillDisabled")], media_cache),
            "masterMenu": _image(root, "ui", [ui_common.get("masterMenu")], media_cache),
            "masterFrame": _image(root, "ui", [ui_common.get("masterFrame")], media_cache),
            "masterEquipFrame": _image(root, "ui", [ui_common.get("masterEquipFrame")], media_cache),
        },
        "version": source.get("version", {"schema": 1, "supplementAvailable": False}),
    }
