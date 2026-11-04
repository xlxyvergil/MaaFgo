"""Build exact, offline NP-card bindings without touching the existing PNGs.

    python tools/build_battle_editor_np_cards.py
    python tools/build_battle_editor_np_cards.py --project F:/.../MaaFgo_NEW --cache F:/.../catalog-build

Only cached Atlas metadata and the two installed CommandCards manifests are
read. No network requests, base catalog regeneration or image writes occur.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import tempfile
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
STAGES = {0: 1, 1: 2, 2: 2, 3: 3, 4: 3}
COLORS = {1: "arts", 2: "buster", 3: "quick"}
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def positive(value) -> int | None:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return int(value)
    return None


class NpMetadataBuilder:
    def __init__(self, project: Path, cache: Path):
        self.project, self.cache = project.resolve(), cache.resolve()
        development = self.project / "assets/resource"
        self.resources = development if (development / "base/image/CommandCards").is_dir() else self.project / "resource"
        if not self.resources.resolve().is_relative_to(self.project):
            raise ValueError("CommandCards resources must stay in this install")
        self.data = {"schema": 1, "generator": "tools/build_battle_editor_np_cards.py",
                     "rules": {"limitCountToDisplayStage": STAGES,
                               "nameRow": "matching npSvts.svtId imageIndex modulo 2",
                               "costume": "exact commands.costume battle character ID; no numeric inference",
                               "unknownPortrait": "first registered portrait, display-only preview-default",
                               "region": "exact regional NP metadata and name texture; no JP substitution"},
                     "sources": {}, "assets": {}, "shared": {}, "servants": {}, "missing": []}
        self.manifests = {}
        self._files = {}

    def source(self, key: str, path: Path):
        raw = path.read_bytes()
        resolved = path.resolve()
        label = resolved.relative_to(self.project).as_posix() if resolved.is_relative_to(self.project) else str(resolved)
        self.data["sources"][key] = {"path": label, "sha256": digest(raw), "bytes": len(raw)}
        return json.loads(raw.decode("utf-8-sig"))

    def asset(self, pack: str, relative: str, entry: dict, *, expected=(256, 256)) -> str:
        key = pack + ":" + relative
        if key in self.data["assets"]:
            return key
        if pack not in ("base", "cn") or relative.startswith("/") or any(p in ("", ".", "..") for p in relative.split("/")):
            raise ValueError(f"Unsafe manifest asset: {key}")
        boundary = self.resources / pack / "image/CommandCards"
        path = boundary / relative
        resolved = path.resolve()
        if not boundary.resolve().is_relative_to(self.project) or not resolved.is_relative_to(boundary.resolve()):
            raise ValueError(f"Asset escapes its CommandCards pack: {key}")
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str) or not SHA256.fullmatch(entry["sha256"]):
            raise ValueError(f"Missing manifest hash: {key}")
        if resolved not in self._files:
            raw = resolved.read_bytes()
            if (not raw.startswith(PNG_MAGIC) or len(raw) < 33 or raw[12:16] != b"IHDR"
                    or raw[-8:] != b"IEND\xaeB`\x82"):
                raise ValueError(f"Incomplete manifest PNG: {key}")
            self._files[resolved] = (digest(raw), len(raw), struct.unpack(">II", raw[16:24]))
        actual, size, dimensions = self._files[resolved]
        if actual != entry["sha256"] or dimensions != tuple(expected):
            raise ValueError(f"Manifest PNG hash/size mismatch: {key}")
        self.data["assets"][key] = {"pack": pack, "relative": relative, "sha256": actual,
                                    "bytes": size, "size": list(dimensions), "source": entry.get("source")}
        return key

    def name(self, region: str, sid: int, url) -> dict:
        """Resolve only a declared regional NP texture owned by this exact ID."""
        if not isinstance(url, str):
            return {"missing": "np_name_unmapped"}
        parsed = urlsplit(url)
        match = re.fullmatch(r"/(JP|CN)/Servants/Commands/([1-9][0-9]*)/([A-Za-z0-9_-]+\.png)", parsed.path)
        if (parsed.scheme != "https" or parsed.netloc != "static.atlasacademy.io" or parsed.query or parsed.fragment
                or not match or match[1] != region or int(match[2]) != sid
                or re.fullmatch(r"card_servant_[0-9]+\.png", match[3])):
            return {"missing": "np_name_unmapped"}
        filename = match[3]
        manifest = self.manifests[region]
        entry = (manifest.get("np_names", {}).get(str(sid)) if filename == "card_servant_np.png"
                 else manifest.get("np_extra_names", {}).get(str(sid), {}).get(filename))
        result = {"file": filename}
        if not isinstance(entry, dict):
            return result | {"missing": "np_name_missing"}
        relative = f"np/{sid}/{filename}"
        if entry.get("file") != relative or entry.get("source") != url:
            raise ValueError(f"NP source disagrees with manifest: {region}/{sid}/{filename}")
        rows = entry.get("name_rows", [])
        if not isinstance(rows, list) or any(type(v) is not int or v not in (0, 1) for v in rows):
            raise ValueError(f"Invalid NP name rows: {region}/{sid}/{filename}")
        pack = "base" if region == "JP" else "cn"
        return result | {"rows": rows, "asset": self.asset(pack, "_composition/" + relative, entry)}

    def build(self) -> dict:
        local = self.source("roster", self.project / "agent/custom/servant_list.json")
        self.source("baseCatalog", self.project / "agent/battle_editor/data/catalog.json")
        ids = sorted({v for row in local.get("servants", []) for x in [row.get("id"), *row.get("aliases", [])]
                      if (v := positive(x)) is not None})
        portrait_manifest = self.source("portraits", self.resources / "base/image/CommandCards/manifest")
        if portrait_manifest.get("region") != "JP":
            raise ValueError("Base portrait manifest must declare JP provenance")
        nice = {}
        for region, pack in (("JP", "base"), ("CN", "cn")):
            records = self.source(region + "Metadata", self.cache / f"{region}_nice_servant.json")
            nice[region] = {row["id"]: row for row in records}
            if len(nice[region]) != len(records):
                raise ValueError(f"Duplicate {region} NiceServant IDs")
            manifest = self.source(region + "Composition", self.resources / pack / "image/CommandCards/_composition/manifest")
            if manifest.get("schema_version") != 2 or manifest.get("region") != region:
                raise ValueError(f"Wrong NP composition manifest: {region}")
            self.manifests[region] = manifest
            shared = self.data["shared"][region] = {}
            for color in COLORS.values():
                shared[color] = {}
                for field, filename, expected in (("background", f"card_bg_np{color}.png", (140, 177)),
                                                  ("icon", f"card_icon_{color}.png", (185, 133)),
                                                  ("faceBackground", f"card_bg_{color}.png", (133, 170)),
                                                  ("faceText", f"card_txt_{color}.png", (150, 73))):
                    entry = manifest.get("shared", {}).get(Path(filename).stem)
                    if not isinstance(entry, dict):
                        raise ValueError(f"Missing registered shared layer: {region}/{filename}")
                    if entry.get("file") != "shared/" + filename:
                        raise ValueError(f"Wrong shared layer path: {region}/{filename}")
                    shared[color][field] = self.asset(pack, "_composition/shared/" + filename, entry, expected=expected)
        for sid in ids:
            servant = {"portraits": [], "regions": {}}
            self.data["servants"][str(sid)] = servant
            commands = nice["JP"].get(sid, {}).get("extraAssets", {}).get("commands", {})
            for entry in portrait_manifest.get("servants", {}).get(str(sid), []):
                group, key = entry.get("group"), entry.get("key")
                match = re.fullmatch(rf"{sid}/card_servant_([1-9][0-9]*)\.png", entry.get("file", ""))
                if (group not in ("ascension", "costume") or not isinstance(key, str) or not match
                        or commands.get(group, {}).get(key) != entry.get("source")):
                    raise ValueError(f"Portrait identity/source mismatch: {sid}/{entry.get('file')}")
                servant["portraits"].append({"variant": int(match[1]), "group": group, "key": key,
                                              "asset": self.asset("base", entry["file"], entry)})
            servant["portraits"].sort(key=lambda p: p["variant"])
            if not servant["portraits"]:
                self.data["missing"].append({"svtId": sid, "code": "np_portrait_missing"})
            for region in ("JP", "CN"):
                record = nice[region].get(sid)
                if record is None:
                    self.data["missing"].append({"svtId": sid, "region": region, "code": "np_region_metadata_missing"})
                    continue
                rdata = {"nps": {}, "nameOverrides": {"ascension": {}, "costume": {}},
                         "costumeOverridesByChara": {}, "previewNameOverrides": {}}
                servant["regions"][region] = rdata
                transform = record.get("script", {}).get("transformInfo", {}).get("saveTransform")
                if positive(transform) in ids:
                    rdata["transformId"] = transform
                for np in record.get("noblePhantasms", []):
                    td_id = positive(np.get("id"))
                    if td_id is None:
                        raise ValueError(f"Invalid NP ID for {region}/{sid}")
                    entry = self.name(region, sid, np.get("icon"))
                    card = int(np["card"]) if str(np.get("card")) in ("1", "2", "3") else None
                    indices = sorted({s["imageIndex"] for s in np.get("npSvts", []) if s.get("svtId") == sid
                                      and type(s.get("imageIndex")) is int and s["imageIndex"] >= 0})
                    rows = {v % 2 for v in indices}
                    entry.update({"id": td_id, "cardType": card, "name": np.get("name", ""), "imageIndices": indices})
                    if len(rows) != 1 or card is None:
                        entry["missing"] = "np_name_row_unknown" if len(rows) != 1 else "np_card_unknown"
                    else:
                        entry["row"] = next(iter(rows))
                        if not entry.get("missing") and entry["row"] not in entry["rows"]:
                            entry["missing"] = "np_name_row_missing"
                    previous = rdata["nps"].setdefault(str(td_id), entry)
                    if previous != entry:
                        raise ValueError(f"Conflicting NP binding: {region}/{sid}/{td_id}")
                    if entry.get("missing"):
                        self.data["missing"].append({"svtId": sid, "tdId": td_id, "region": region, "code": entry["missing"]})
                overrides = record.get("ascensionAdd", {}).get("overWriteTDFileName", {})
                for group in ("ascension", "costume"):
                    for key, url in overrides.get(group, {}).items():
                        rdata["nameOverrides"][group][str(key)] = self.name(region, sid, url)
                for portrait in servant["portraits"]:
                    variant = str(portrait["variant"])
                    if portrait["group"] == "costume":
                        override = rdata["nameOverrides"]["costume"].get(variant)
                        if override:
                            rdata["costumeOverridesByChara"][portrait["key"]] = override
                            rdata["previewNameOverrides"][variant] = override
                    else:
                        stage = int(portrait["key"])
                        options = [rdata["nameOverrides"]["ascension"].get(str(lim)) for lim, st in STAGES.items() if st == stage]
                        if options and any(o != options[0] for o in options):
                            rdata["previewNameOverrides"][variant] = {"ambiguous": True}
                        elif options and options[0]:
                            rdata["previewNameOverrides"][variant] = options[0]
        self.data["coverage"] = {
            "servants": len(ids), "portraits": sum(bool(s["portraits"]) for s in self.data["servants"].values()),
            "assets": len(self.data["assets"]),
            "regions": {region: {"servants": sum(region in s["regions"] for s in self.data["servants"].values()),
                                  "nps": sum(len(s["regions"].get(region, {}).get("nps", {})) for s in self.data["servants"].values()),
                                  "readyNps": sum(not n.get("missing") for s in self.data["servants"].values()
                                                  for n in s["regions"].get(region, {}).get("nps", {}).values())}
                        for region in ("JP", "CN")},
        }
        return self.data


def write_atomic(path: Path, value: dict) -> str:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if path.is_file() and path.read_bytes() == raw:
        return digest(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return digest(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--cache", type=Path, help="Existing JP/CN_nice_servant.json cache; no downloads")
    parser.add_argument("--output", type=Path, help="Default: <project>/agent/battle_editor/data/np_cards.json")
    args = parser.parse_args()
    project = args.project.resolve()
    result = NpMetadataBuilder(project, args.cache or project / "config/BattleEditor/catalog-build").build()
    target = args.output or project / "agent/battle_editor/data/np_cards.json"
    sha = write_atomic(target, result)
    print(json.dumps({"output": str(target), "sha256": sha, "coverage": result["coverage"],
                      "missing": len(result["missing"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
