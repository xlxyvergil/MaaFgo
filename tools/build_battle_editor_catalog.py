"""Build bundled offline editor facts from pinned Atlas Academy exports.

Run with --refresh to download exports and missing skill/mystic-code PNGs.
Without --refresh, cached files are used and no network requests are made.
The cache lives under config/BattleEditor/catalog-build (never shipped).
The compact output and image source hashes are shipped with the Agent.

    python tools/build_battle_editor_catalog.py --refresh
    python tools/build_battle_editor_catalog.py --cache-dir PATH

Atlas data: https://api.atlasacademy.io/docs . Images are FGO game assets,
consistent with the existing recognition resource collection in this project.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timezone
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.atlasacademy.io"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ORDER_CHANGE_IDS = {20, 210, 440}
DIRECT_CONNECTION = False


def complete_png(raw: bytes) -> bool:
    """Check PNG chunk boundaries and CRCs, including the terminal IEND."""
    if not raw.startswith(PNG_MAGIC):
        return False
    offset = len(PNG_MAGIC)
    first = True
    while offset + 12 <= len(raw):
        length = int.from_bytes(raw[offset:offset + 4], "big")
        end = offset + length + 12
        if end > len(raw):
            return False
        kind = raw[offset + 4:offset + 8]
        if first and (kind != b"IHDR" or length != 13):
            return False
        first = False
        expected_crc = int.from_bytes(raw[end - 4:end], "big")
        if zlib.crc32(raw[offset + 4:end - 4]) & 0xFFFFFFFF != expected_crc:
            return False
        if kind == b"IEND":
            return length == 0 and end == len(raw)
        offset = end
    return False


def _request_once(url: str) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"api.atlasacademy.io", "static.atlasacademy.io"}:
        raise ValueError(f"Untrusted Atlas source: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "MaaFGO/battle-editor-catalog",
        "Accept-Encoding": "gzip" if parsed.hostname == "api.atlasacademy.io" else "identity"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if DIRECT_CONNECTION else None
    open_request = opener.open if opener else urllib.request.urlopen
    with open_request(req, timeout=60 if parsed.hostname == "static.atlasacademy.io" else 300) as response:
        if urllib.parse.urlsplit(response.url).hostname not in {"api.atlasacademy.io", "static.atlasacademy.io"}:
            raise ValueError("Unexpected redirect host")
        chunks, received, last_report = [], 0, time.monotonic()
        while chunk := response.read(128 * 1024):
            chunks.append(chunk)
            received += len(chunk)
            if "nice_servant.json" in url and time.monotonic() - last_report > 30:
                print(f"  {url}: received {received / (1024 * 1024):.1f} MiB (transport)", flush=True)
                last_report = time.monotonic()
        raw = b"".join(chunks)
        return gzip.decompress(raw) if response.headers.get("Content-Encoding") == "gzip" else raw


def request(url: str) -> bytes:
    for attempt in range(3):
        try:
            return _request_once(url)
        except (OSError, EOFError, http.client.IncompleteRead) as exc:
            if attempt == 2:
                raise
            print(f"Retrying {url}: {type(exc).__name__}: {str(exc)[:180]}", flush=True)
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def curl_image(url: str, cache: Path, direct: bool) -> bytes:
    """Optional native TLS transport for maintenance on unstable proxy chains."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "static.atlasacademy.io":
        raise ValueError("Untrusted image source")
    executable = shutil.which("curl.exe") or shutil.which("curl")
    if not executable:
        raise FileNotFoundError("curl is unavailable; omit --curl to use Python")
    with tempfile.NamedTemporaryFile(prefix="editor-image-", suffix=".png", dir=cache, delete=False) as handle:
        target = Path(handle.name)
    try:
        command = [executable, "--ipv4", "--fail", "--silent", "--show-error", "--proto", "=https",
                   "--retry", "2", "--retry-delay", "1", "--connect-timeout", "15", "--max-time", "45",
                   "--output", str(target)]
        if direct:
            command += ["--noproxy", "*"]
        command.append(url)
        result = subprocess.run(command, capture_output=True, timeout=160,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise OSError(result.stderr.decode("utf-8", errors="replace")[:300])
        return target.read_bytes()
    finally:
        target.unlink(missing_ok=True)


def dump(path: Path, value, *, pretty: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":")) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def skill_target(skill: dict) -> tuple[str, list[str]]:
    """Classify only explicit selectable targets; uncertain functions stay unknown.

    ptRandom / ptOneHpLowest* are automatic selection, unlike ptOne/ptOneOther.
    Skill-choice branches can hide targets in referenced skills, hence unknown.
    """
    functions = skill.get("functions") or []
    targets = sorted({f.get("funcTargetType", "unknown") for f in functions})
    if any(f.get("funcType") in {"skillSelect", "skillSelectToAvoid", "changeServant"} for f in functions):
        return "unknown", targets
    if set(targets) & {"ptOne", "ptOneOther"}:
        return "ally", targets
    known_automatic = {
        "self", "ptAll", "ptFull", "ptOther", "ptOtherFull", "ptRandom",
        "ptOneAnotherRandom", "ptSelfAnotherRandom", "ptSelfAnotherFirst", "ptSelfAnotherLast",
        "ptSelfBefore", "ptSelfAfter", "ptOneHpLowestValue", "ptOneHpLowestRate",
        "enemy", "enemyAll", "enemyAnother", "enemyFull", "enemyOther", "enemyOtherFull",
        "enemyRandom", "enemyOneAnotherRandom", "enemyOneNoTargetNoAction", "enemyRange",
        "fieldOther", "fieldAll", "fieldRandom", "noTarget", "commandTypeSelfTreasureDevice",
        "handCommandcardRandomOne",
    }
    return ("none" if targets and set(targets) <= known_automatic else "unknown"), targets


def compact_skill(skill: dict, chinese: dict, num: int | None = None) -> dict:
    target, targets = skill_target(skill)
    localized = chinese.get(skill["id"], {})
    icon_url = skill.get("icon")
    return {
        "id": skill["id"], "num": num if num is not None else skill.get("num"),
        "name": localized.get("name") or skill.get("name") or f"技能 {skill['id']}",
        "nameJp": skill.get("name", ""),
        "icon": Path(urllib.parse.urlsplit(icon_url).path).name if icon_url else None,
        "target": target, "targets": targets,
        "detail": localized.get("detail") or skill.get("detail", ""),
        "coolDown": skill.get("coolDown", []),
        "priority": skill.get("priority", 0),
        "condLimitCount": skill.get("condLimitCount", 0),
    }


def compact_np(np: dict, chinese: dict) -> dict:
    localized = chinese.get(np["id"], {})
    card = np.get("card")
    # Atlas CardType is its numeric string enum, matching Chaldea integers.
    card_type = int(card) if str(card) in {"1", "2", "3"} else None
    return {"id": np["id"], "name": localized.get("name") or np.get("name", ""),
            "nameJp": np.get("name", ""), "cardType": card_type,
            "priority": np.get("priority", 0), "condLimitCount": np.get("condLimitCount", 0)}


def select_default_np(nps: list[dict], chinese: dict) -> dict | None:
    """Prefer recorded effects over placeholders, without removing any variant."""
    if not nps:
        return None
    effectful = [np for np in nps if isinstance(np.get("functions"), list)
                 and any(isinstance(function, dict) and function for function in np["functions"])]
    selected = max(effectful or nps, key=lambda np: (np.get("priority", 0), np["id"]))
    return compact_np(selected, chinese)


def update_np_defaults(catalog: dict, exports: dict) -> list[dict]:
    """Patch only existing rows' np fields from cached Nice data, preserving all other facts."""
    jp = {str(item["id"]): item for item in exports["JP/nice_servant"]}
    cn = {str(item["id"]): item for item in exports.get("CN/nice_servant", [])}
    changes = []
    for servant in catalog.get("servants", []):
        sid = str(servant["id"])
        record = jp.get(sid)
        if record is None:
            continue
        localized = {np["id"]: np for np in cn.get(sid, {}).get("noblePhantasms", [])}
        selected = select_default_np(record.get("noblePhantasms", []), localized)
        if servant.get("np") != selected:
            changes.append({"svtId": servant["id"], "nameJp": servant.get("nameJp"),
                            "previous": servant.get("np"), "next": selected})
            servant["np"] = selected
    return changes


def compile_catalog(exports: dict, local_servants: list[dict]) -> tuple[dict, dict[str, str]]:
    """Pure transformation used by tests as well as the maintenance command."""
    jp = {str(item["id"]): item for item in exports["JP/nice_servant"]}
    cn = {str(item["id"]): item for item in exports.get("CN/nice_servant", [])}
    media: dict[str, str] = {}
    servants = []
    for local in local_servants:
        sid = str(local["id"])
        record = jp.get(sid)
        if not record:
            continue
        cn_record = cn.get(sid, {})
        cn_skills = {s["id"]: s for s in cn_record.get("skills", [])}
        cn_nps = {s["id"]: s for s in cn_record.get("noblePhantasms", [])}
        skills = [s for s in record.get("skills", []) if s.get("num") in (1, 2, 3)]
        compact = [compact_skill(s, cn_skills) for s in skills]
        for skill in skills:
            if skill.get("icon"):
                media["skill/" + Path(urllib.parse.urlsplit(skill["icon"]).path).name] = skill["icon"]
        selected = []
        for num in (1, 2, 3):
            variants = [s for s in compact if s["num"] == num]
            if variants:
                selected.append(max(variants, key=lambda s: (s["priority"], s["id"])))
        raw_nps = record.get("noblePhantasms", [])
        nps = [compact_np(np, cn_nps) for np in raw_nps]
        raw_cards = record.get("cards", [])
        cards = [int(c) for c in raw_cards] if len(raw_cards) == 5 and all(str(c) in {"1", "2", "3"} for c in raw_cards) else []
        servants.append({
            "id": int(sid), "nameJp": record.get("name", ""),
            "collectionNo": record.get("collectionNo"), "cards": cards,
            "searchAliases": sorted({name for source in (record, cn_record)
                                     for field in ("name", "originalName", "battleName", "ruby")
                                     if isinstance((name := source.get(field)), str) and name}),
            "skills": selected, "skillVariants": compact,
            "np": select_default_np(raw_nps, cn_nps),
            "npVariants": nps,
        })
    equips = {}
    for region in ("JP", "CN"):
        for item in exports.get(f"{region}/basic_equip", []):
            entry = equips.setdefault(item["id"], {"id": item["id"]})
            entry.update({"collectionNo": item.get("collectionNo"), "rarity": item.get("rarity")})
            entry["nameJp" if region == "JP" else "name"] = item.get("name", "")
            if item.get("face"):
                entry["image"] = Path(urllib.parse.urlsplit(item["face"]).path).name
    mystics = []
    cn_mc = {r["id"]: r for r in exports.get("CN/nice_mystic_code", [])}
    for item in exports["JP/nice_mystic_code"]:
        translated = cn_mc.get(item["id"], {})
        cn_skills = {s["id"]: s for s in translated.get("skills", [])}
        skills = []
        for num, skill in enumerate(item.get("skills", []), 1):
            skills.append(compact_skill(skill, cn_skills, num))
            if skill.get("icon"):
                media["skill/" + Path(urllib.parse.urlsplit(skill["icon"]).path).name] = skill["icon"]
        face = (item.get("extraAssets", {}).get("item", {}) or {}).get("female")
        filename = Path(urllib.parse.urlsplit(face).path).name if face else None
        if face:
            media["mystic/" + filename] = face
        mystics.append({"id": item["id"], "name": translated.get("name") or item["name"],
                        "nameJp": item["name"], "image": filename, "skills": skills,
                        "orderChange": item["id"] in ORDER_CHANGE_IDS})
    return {"servants": servants, "equips": sorted(equips.values(), key=lambda e: e["id"]), "mysticCodes": mystics}, media


def main():
    global DIRECT_CONNECTION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Download Atlas exports and missing images")
    parser.add_argument("--download-images", action="store_true", help="Keep cached exports and download missing images only")
    parser.add_argument("--np-defaults-only", action="store_true", help="Offline: update only existing catalog np defaults from cached JP/CN Nice data")
    parser.add_argument("--image-workers", type=int, default=4, choices=range(1, 9), help="Concurrent small-image downloads (1-8)")
    parser.add_argument("--direct", action="store_true", help="Bypass proxy for this maintenance run only")
    parser.add_argument("--curl", action="store_true", help="Use native curl TLS for small images (optional)")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "config/BattleEditor/catalog-build")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "agent/battle_editor/data")
    args = parser.parse_args()
    if args.np_defaults_only and (args.refresh or args.download_images):
        parser.error("--np-defaults-only cannot download or refresh resources")
    DIRECT_CONNECTION = args.direct
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cache, output = args.cache_dir, args.output_dir
    cache.mkdir(parents=True, exist_ok=True)
    sources = []
    exports = {}
    keys = [f"{region}/{name}" for region in ("JP", "CN") for name in ("nice_servant", "basic_equip", "nice_mystic_code")]

    def load(key):
        region, name = key.split("/")
        path = cache / f"{region}_{name}.json"
        url = f"{API}/export/{region}/{name}.json"
        if args.refresh or not path.is_file():
            if not args.refresh:
                raise FileNotFoundError(f"Missing cached {path}. Run with --refresh first.")
            print(f"Fetching {url}", flush=True)
            raw = request(url)
            json.loads(raw)
            path.write_bytes(raw)
        raw = path.read_bytes()
        return key, json.loads(raw), {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    if args.np_defaults_only:
        for key in ("JP/nice_servant", "CN/nice_servant"):
            _, exports[key], _ = load(key)
        path = output / "catalog.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        changes = update_np_defaults(catalog, exports)
        if changes:
            dump(path, catalog)
        print(json.dumps({"updatedNpDefaults": len(changes), "changes": changes,
                          "offline": True, "otherFieldsPreserved": True}, ensure_ascii=False))
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for key, data, source in pool.map(load, keys):
            exports[key] = data
            sources.append(source)
    infos = {}
    for region in ("JP", "CN"):
        path = cache / f"{region}_info.json"
        if args.refresh:
            path.write_bytes(request(f"{API}/export/{region}/info.json"))
        infos[region] = json.loads(path.read_bytes())
    local_path = ROOT / "agent/custom/servant_list.json"
    local = json.loads(local_path.read_text(encoding="utf-8"))["servants"]
    catalog, images = compile_catalog(exports, local)
    catalog["version"] = {"schema": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
                          "atlas": {region: {k: v for k, v in info.items() if k in {"hash", "timestamp", "dataVer", "dateVer"}}
                                    for region, info in infos.items()}}
    # Make real card/skill facts available even if an optional PNG is slow.
    dump(output / "catalog.json", catalog)
    source_manifest = {"version": catalog["version"], "sources": sources,
                       "servantCatalogSha256": hashlib.sha256(local_path.read_bytes()).hexdigest(),
                       "missingServantIds": sorted({int(row["id"]) for row in local} - {row["id"] for row in catalog["servants"]}),
                       "noActiveSkillMetadataIds": sorted(row["id"] for row in catalog["servants"] if not row["skills"]),
                       "media": [], "missingMedia": []}

    def image_load(pair):
        relative, url = pair
        path = output / "media" / relative
        if not path.is_file() and (args.refresh or args.download_images):
            try:
                raw = curl_image(url, cache, args.direct) if args.curl else request(url)
                if not complete_png(raw):
                    raise ValueError("Asset is not a complete PNG")
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_bytes(raw)
                temporary.replace(path)
            except Exception as exc:
                return None, {"file": relative, "url": url, "error": str(exc)}
        if not path.is_file():
            return None, {"file": relative, "url": url, "error": "Not downloaded"}
        raw = path.read_bytes()
        if not complete_png(raw):
            raise ValueError(f"Invalid PNG: {path}")
        return {"file": relative, "url": url, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.image_workers) as pool:
        for image, missing in pool.map(image_load, sorted(images.items())):
            source_manifest["media" if image else "missingMedia"].append(image or missing)
    dump(output / "catalog.json", catalog)
    dump(output / "sources.json", source_manifest, pretty=True)
    print(f"Built {len(catalog['servants'])} servants, {len(catalog['equips'])} equips, {len(catalog['mysticCodes'])} mystic codes; "
          f"{len(source_manifest['media'])} PNGs, {len(source_manifest['missingMedia'])} missing", flush=True)


if __name__ == "__main__":
    main()
