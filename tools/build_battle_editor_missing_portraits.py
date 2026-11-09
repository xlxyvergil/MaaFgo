"""Fill missing editor formation cards from declared, offline Atlas assets.

No downloads or full UI rebuilds occur. Existing skill/form mappings and media
remain intact. An atlas-cache root contains the exact extracted @1/@2 PNGs;
a fgo-dump root contains the original unframed two-card textures.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

from PIL import Image


_SPEC = importlib.util.spec_from_file_location(
    "missing_portrait_ui_assets", Path(__file__).with_name("build_battle_editor_ui_assets.py"))
UI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(UI)


def _write_json(path: Path, value: dict) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.is_file() and path.read_bytes() == raw:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".missing-portraits.", suffix=".tmp",
                                     dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_bytes(raw)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _formation_ready(output: Path, manifest: dict, record: dict) -> bool:
    name = record.get("formationPortrait")
    if not isinstance(name, str) or not UI.FORMATION_FILENAMES.fullmatch(name):
        return False
    entry = manifest.get("files", {}).get(name)
    path = output / "media" / name
    if not isinstance(entry, dict) or not path.is_file() or not path.resolve().is_relative_to((output / "media").resolve()):
        return False
    if entry.get("sha256") != UI.sha256(path):
        return False
    try:
        with Image.open(path) as image:
            image.load()
            recipe = UI.formation_resize_recipe()
            return image.format == "WEBP" and image.mode == "RGB" and image.size == (recipe["width"], recipe["height"])
    except (OSError, ValueError):
        return False


def _editable_ids(project: Path) -> set[int]:
    # Match the actual selector's canonical identities instead of guessing from
    # an old manifest row (for example an excluded, unnumbered NPC alias).
    repository = str(Path(__file__).resolve().parents[1])
    if repository not in sys.path:
        sys.path.insert(0, repository)
    from agent.battle_editor.catalog import build_catalog
    return {row["id"] for row in build_catalog(project)["servants"]}


def _source(sid: int, data: dict, record: dict, source: Path, source_kind: str):
    declared = data.get("extraAssets", {}).get("charaGraph", {}).get("ascension", {})
    if not isinstance(declared, dict):
        return None
    npc = data.get("type") == "enemyCollectionDetail"
    order = list(dict.fromkeys((str(record.get("portraitAscension", 3)), "3", "1", "2", "4")))
    if npc:
        order.append("0")
    for ascension in order:
        url = declared.get(ascension)
        mapped = UI.atlas_formation_source(url, source, sid, source_kind=source_kind,
                                          allow_unframed=npc and ascension == "0")
        if mapped is None or not mapped[0].is_file():
            continue
        path, crop = mapped
        if not path.resolve().is_relative_to(source):
            raise ValueError(f"Declared card source escaped the selected source root: {sid}")
        with Image.open(path) as image:
            image.load()
            if source_kind == "atlas-cache":
                valid = image.size == UI.FORMATION_RECT
            elif ascension == "0":
                valid = image.size in ((1024, 1024), (512, 1024), (512, 724))
            else:
                valid = image.size in ((1024, 1024), (1024, 724))
            if image.format != "PNG" or not valid or crop[2] > image.width or crop[3] > image.height:
                raise ValueError(f"Unrecognized declared formation card layout for {sid}: {image.size}")
        return path, crop, url, int(ascension)
    return None


def build_missing_portraits(project: Path, source: Path, cache: Path, output: Path, *,
                            servant_ids: list[int] | None = None, source_kind: str = "fgo-dump",
                            evidence: Path | None = None) -> dict:
    project, source, cache, output = (path.resolve() for path in (project, source, cache, output))
    if source_kind not in ("fgo-dump", "atlas-cache"):
        raise ValueError("Expected fgo-dump or atlas-cache image sources")
    if not source.is_dir() or output == source or output.is_relative_to(source):
        raise ValueError("Use an existing source directory and a separate output directory")
    path = output / "manifest.json"
    before = path.read_bytes()
    previous = json.loads(before)
    if previous.get("schema") != 1 or not isinstance(previous.get("servants"), dict):
        raise ValueError("An existing schema-1 editor UI manifest is required")
    eligible = _editable_ids(project)
    registered = {int(sid) for sid in previous["servants"] if str(sid).isdigit()}
    if servant_ids is not None and any(type(sid) is not int or sid <= 0 for sid in servant_ids):
        raise ValueError("Servant IDs must be positive integers")
    requested = set(servant_ids) if servant_ids is not None else registered & eligible
    unavailable = requested - (registered & eligible)
    if unavailable:
        raise ValueError(f"Requested servants are not registered editable identities: {sorted(unavailable)}")
    targets = sorted(sid for sid in requested if not _formation_ready(output, previous, previous["servants"][str(sid)]))
    if not targets:
        return previous
    cache_path = cache / "JP_nice_servant.json"
    records = {row["id"]: row for row in UI.read_json(cache_path, []) if isinstance(row, dict) and type(row.get("id")) is int}
    prepared = {sid: _source(sid, records.get(sid, {}), previous["servants"][str(sid)], source, source_kind)
                for sid in targets}
    unresolved = sorted(sid for sid, asset in prepared.items() if asset is None)
    if servant_ids is not None and unresolved:
        raise ValueError(f"Missing exact declared offline formation sources: {unresolved}")
    prepared = {sid: asset for sid, asset in prepared.items() if asset is not None}
    if not prepared:
        return previous
    original_hashes = {name: UI.sha256(output / "media" / name)
                       for name in previous.get("files", {}) if (output / "media" / name).is_file()}
    source_hashes = {sid: UI.sha256(asset[0]) for sid, asset in prepared.items()}
    builder = UI.AssetBuilder(project, source, cache, output, source_kind=source_kind)
    updated = copy.deepcopy(previous)
    if source_kind == "atlas-cache":
        updated["sourceLabel"] = "用户本地 FGO 整包及 Atlas Academy 原始图片缓存；仅供 MaaFGO 本地界面使用，版权归原权利方"
    added = {}
    for sid, (asset, crop, url, ascension) in prepared.items():
        kind, relative = builder._relative_source(asset)
        resize = UI.formation_resize_recipe()
        recipe = {"kind": kind, "path": relative, "sha256": source_hashes[sid],
                  "crop": list(crop), "resize": resize, "encoding": builder.webp_recipe()}
        fingerprint = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        name = f"formation_{sid}_{fingerprint}.webp"
        if builder.add(asset, name, crop=crop, resize=resize) != name:
            raise ValueError(f"Could not build declared formation card for {sid}")
        metadata = builder.manifest["files"][name]
        metadata["source"].update({"atlasURL": url, "ascension": ascension})
        updated["files"][name] = metadata
        updated["servants"][str(sid)].update({"formationPortrait": name, "formationPortraitAscension": ascension})
        added[sid] = name
    updated.setdefault("sources", {})[f"incremental/formationPortraits/{source_kind}/JP_nice_servant.json"] = {
        "sha256": UI.sha256(cache_path)}
    missing = updated.setdefault("missing", {})
    missing["formationPortraits"] = [sid for sid in missing.get("formationPortraits", []) if int(sid) not in added]
    updated.setdefault("stats", {}).update({
        "formationPortraits": sum("formationPortrait" in row for row in updated["servants"].values()),
        "mediaFiles": len(updated["files"]),
        "mediaBytes": sum((output / "media" / name).stat().st_size for name in updated["files"]),
    })
    if any(UI.sha256(asset[0]) != source_hashes[sid] for sid, asset in prepared.items()):
        raise RuntimeError("Formation source changed during the build")
    changed_names = set(added.values())
    if any(UI.sha256(output / "media" / name) != digest for name, digest in original_hashes.items() if name not in changed_names):
        raise RuntimeError("An unrelated existing UI image changed during the incremental build")
    if path.read_bytes() != before:
        raise RuntimeError("UI manifest changed during the build; rerun after the other build finishes")
    _write_json(path, updated)
    if evidence is not None:
        _write_json(evidence.resolve() / "missing-portraits.json", {
            "manifestBeforeSha256": hashlib.sha256(before).hexdigest(), "manifestAfterSha256": UI.sha256(path),
            "sourceKind": source_kind, "added": {str(sid): name for sid, name in added.items()},
            "unresolved": unresolved, "existingImagesChecked": len(original_hashes),
        })
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-kind", choices=("fgo-dump", "atlas-cache"), default="fgo-dump")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--servant-ids", type=int, nargs="+")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    project = args.project_root.resolve()
    manifest = build_missing_portraits(
        project, args.source_root, args.cache_dir or project / "config/BattleEditor/catalog-build",
        args.output_dir or project / "agent/battle_editor/data/ui", servant_ids=args.servant_ids,
        source_kind=args.source_kind, evidence=args.evidence_dir)
    print(json.dumps({"stats": manifest.get("stats"), "missingFormationPortraits": manifest.get("missing", {}).get("formationPortraits", [])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
