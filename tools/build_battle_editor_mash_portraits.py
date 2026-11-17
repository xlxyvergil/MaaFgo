"""Incrementally add Mash's declared costume cards to the offline editor.

This tool neither downloads nor rebuilds the other UI assets. The cached Atlas
CharaGraph costume URLs name an unframed a.png texture: the complete artwork is
its first native 512x724 card, above the separate name rows. The three local
textures were inspected before this mapping was added. Their RGB artwork is
processed with the established AssetBuilder formation WebP recipe, preserving
RGB beneath the dump's unreliable alpha channel.

Usage:
    python tools/build_battle_editor_mash_portraits.py --source-root F:/.../FGO整包
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from PIL import Image


_SPEC = importlib.util.spec_from_file_location(
    "mash_portrait_ui_assets", Path(__file__).with_name("build_battle_editor_ui_assets.py"))
UI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(UI)

MASH_ID = "800100"
COSTUME_IDS = (800140, 800150, 800190)
FORM_COSTUMES = {"ortenaus": 800140, "black-barrel": 800140,
                 "ortenaus-restored": 800150, "paladin": 800190}
NATIVE_CARD = (0, 0, 512, 724)


def costume_source(url: str, source: Path, costume_id: int) -> Path:
    """Accept only the exact, declared single-card Mash costume URL."""
    if costume_id not in COSTUME_IDS or not isinstance(url, str):
        raise ValueError("Expected one of the three declared Mash costume cards")
    parsed = urlsplit(url)
    expected = rf"/(?:JP|CN)/CharaGraph/{costume_id}/{costume_id}a\.png"
    if (parsed.scheme != "https" or parsed.netloc != "static.atlasacademy.io"
            or parsed.query or parsed.fragment or not re.fullmatch(expected, parsed.path)):
        raise ValueError(f"Cached Atlas URL does not identify Mash costume {costume_id}")
    path = source / "从者立绘/CharaGraph" / str(costume_id) / f"{costume_id}a.png"
    if not path.is_file():
        raise ValueError(f"Missing declared costume source: {path}")
    with Image.open(path) as image:
        # The dump has 1024x1024 textures; Atlas may already extract a 512px
        # single card. Reject other layouts rather than guessing a crop.
        if image.size not in ((1024, 1024), (512, 1024), (512, 724)):
            raise ValueError(f"Unrecognized costume card texture dimensions: {path}: {image.size}")
    return path


def _write_json(path: Path, data: dict) -> None:
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.is_file() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".mash-portraits.", suffix=".tmp",
                                     dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_mash_portraits(project: Path, source: Path, cache: Path, output: Path,
                        evidence: Path | None = None) -> dict:
    """Preserve all existing records and bytes; add only three cards and aliases."""
    project, source, cache, output = (path.resolve() for path in (project, source, cache, output))
    manifest_path = output / "manifest.json"
    original_bytes = manifest_path.read_bytes()
    previous = json.loads(original_bytes)
    if previous.get("schema") != 1:
        raise ValueError("Expected the existing schema-1 UI manifest")
    base_name = previous.get("servants", {}).get(MASH_ID, {}).get("formationPortrait")
    if not isinstance(base_name, str) or Path(base_name).name != base_name:
        raise ValueError("The base Mash formation card must already be registered")
    if base_name not in previous.get("files", {}) or not (output / "media" / base_name).is_file():
        raise ValueError("The base Mash formation card is missing from the existing UI assets")
    cache_path = cache / "JP_nice_servant.json"
    records = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    mash = next((row for row in records if row.get("id") == int(MASH_ID)), None)
    if not isinstance(mash, dict):
        raise ValueError("Cached JP servant data does not include Mash 800100")
    declared = mash.get("extraAssets", {}).get("charaGraph", {}).get("costume", {})
    # Validate every source before emitting anything; there is no adjacent-ID
    # fallback, network fallback, or generated-media input.
    sources = {sid: costume_source(declared.get(str(sid)), source, sid) for sid in COSTUME_IDS}
    source_hashes = {sid: UI.sha256(path) for sid, path in sources.items()}
    old_media = {path.name: UI.sha256(path) for path in (output / "media").iterdir() if path.is_file()}
    builder = UI.AssetBuilder(project, source, cache, output)
    updated = copy.deepcopy(previous)
    added = {}
    for sid, path in sources.items():
        kind, relative = builder._relative_source(path)
        resize = UI.formation_resize_recipe()
        recipe = {"kind": kind, "path": relative, "sha256": source_hashes[sid],
                  "crop": list(NATIVE_CARD), "resize": resize, "encoding": builder.webp_recipe()}
        fingerprint = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        name = f"formation_{sid}_{fingerprint}.webp"
        result = builder.add(path, name, crop=NATIVE_CARD, resize=resize)
        if result != name:
            raise ValueError(f"Could not build declared costume {sid}")
        metadata = builder.manifest["files"][name]
        metadata["source"].update({"atlasURL": declared[str(sid)], "costume": sid,
                                   "ownerServantId": int(MASH_ID)})
        updated["files"][name] = metadata
        added[sid] = name
    form_portraits = {"early": base_name, "normal": base_name}
    form_portraits.update({key: added[sid] for key, sid in FORM_COSTUMES.items()})
    updated["servants"][MASH_ID]["formPortraits"] = form_portraits
    all_forms = [item.get("formPortraits", {}) for item in updated["servants"].values()]
    base_portraits = {item.get("formationPortrait") for item in updated["servants"].values()}
    form_images = {name for forms in all_forms for name in forms.values()} - base_portraits
    updated["stats"].update({
        "formPortraitMappings": sum(len(forms) for forms in all_forms),
        "formPortraitImages": len(form_images),
        "mediaFiles": len(updated["files"]),
        "mediaBytes": sum((output / "media" / name).stat().st_size for name in updated["files"]),
    })
    if any(UI.sha256(path) != source_hashes[sid] for sid, path in sources.items()):
        raise RuntimeError("Source dump changed during the portrait build")
    if any(not (output / "media" / name).is_file() or UI.sha256(output / "media" / name) != digest
           for name, digest in old_media.items()):
        raise RuntimeError("An existing UI image changed during the incremental portrait build")
    # Do not clobber a concurrently updated manifest.
    if manifest_path.read_bytes() != original_bytes:
        raise RuntimeError("UI manifest changed during the portrait build; rerun after the other build finishes")
    _write_json(manifest_path, updated)
    if evidence is not None:
        evidence = evidence.resolve()
        report = {
            "generator": "tools/build_battle_editor_mash_portraits.py",
            "manifestBeforeSha256": hashlib.sha256(original_bytes).hexdigest(),
            "manifestAfterSha256": UI.sha256(manifest_path),
            "cacheSha256": UI.sha256(cache_path),
            "existingImagesUnchanged": len(old_media),
            "formPortraits": form_portraits,
            "cards": {str(sid): {"file": added[sid], **updated["files"][added[sid]]} for sid in COSTUME_IDS},
            "stats": updated["stats"],
        }
        first = evidence / "first-build.json"
        if not first.exists():
            _write_json(first, report)
        _write_json(evidence / "latest-build.json", report)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    project = args.project_root.resolve()
    manifest = build_mash_portraits(
        project, args.source_root, args.cache_dir or project / "config/BattleEditor/catalog-build",
        args.output_dir or project / "agent/battle_editor/data/ui", args.evidence_dir)
    print(json.dumps(manifest["servants"][MASH_ID]["formPortraits"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
