"""Download offline FGO card or battle-status portraits from Atlas Academy.

The runtime never uses the network.  This maintenance tool archives the
``NiceServant.extraAssets.commands`` PNGs under the normal Maa resource tree:

    assets/resource/base/image/CommandCards/<servant_id>/*.png

Examples:
    python tools/update_command_card_assets.py --servant-id 202000 --servant-id 4000100
    python tools/update_command_card_assets.py
    python tools/update_command_card_assets.py --family status

Atlas Academy exposes the game data and asset URLs; the images themselves are
Fate/Grand Order game assets and are handled consistently with the other FGO
recognition images already stored in this repository.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ATLAS_API = "https://api.atlasacademy.io"
USER_AGENT = "MaaFgo/command-card-assets"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "agent" / "custom" / "servant_list.json"
DEFAULT_OUTPUT = ROOT / "assets" / "resource" / "base" / "image" / "CommandCards"
STATUS_OUTPUT = ROOT / "assets" / "resource" / "base" / "image" / "ServantStatus"


def _request(url: str, *, timeout: int = 180) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _fetch_json(url: str, *, timeout: int = 180) -> Any:
    return json.loads(_request(url, timeout=timeout).decode("utf-8"))


def _load_catalog(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    servants = payload.get("servants")
    if not isinstance(servants, list):
        raise ValueError("catalog does not contain a servants list")
    return servants


def _load_atlas_records(
    catalog: list[dict],
    region: str,
    selected_ids: set[str],
) -> dict[str, dict]:
    if selected_ids:
        by_id: dict[str, dict] = {}
        by_catalog = {str(item.get("id")): item for item in catalog}
        for servant_id in sorted(selected_ids, key=int):
            item = by_catalog.get(servant_id)
            if item is None:
                raise ValueError(f"servant_id not found in local catalog: {servant_id}")
            collection_no = item.get("collection_no")
            if not isinstance(collection_no, int) or collection_no <= 0:
                raise ValueError(f"servant_id has no collection_no: {servant_id}")
            record = _fetch_json(
                f"{ATLAS_API}/nice/{region}/servant/{collection_no}", timeout=60
            )
            if str(record.get("id")) != servant_id:
                raise ValueError(
                    f"Atlas id mismatch for collectionNo={collection_no}: "
                    f"expected {servant_id}, got {record.get('id')}"
                )
            by_id[servant_id] = record
        return by_id

    wanted = {str(item.get("id")) for item in catalog if item.get("id") is not None}
    print(f"Fetching Atlas {region} nice_servant export (about 70 MB)...")
    records = _fetch_json(f"{ATLAS_API}/export/{region}/nice_servant.json", timeout=300)
    return {
        str(item["id"]): item
        for item in records
        if isinstance(item, dict) and str(item.get("id")) in wanted
    }


def _portrait_assets(record: dict, family: str) -> list[dict[str, str]]:
    portraits = (record.get("extraAssets") or {}).get(family) or {}
    result: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for group, values in portraits.items():
        if not isinstance(values, dict):
            continue
        for key, url in values.items():
            if not isinstance(url, str) or not url.startswith("https://") or url in seen_urls:
                continue
            seen_urls.add(url)
            filename = Path(urllib.parse.urlparse(url).path).name
            if not filename.lower().endswith(".png"):
                continue
            result.append(
                {
                    "group": str(group),
                    "key": str(key),
                    "url": url,
                    "filename": filename,
                }
            )
    return result


def _download_one(
    servant_id: str,
    asset: dict[str, str],
    output: Path,
    force: bool,
) -> dict[str, str]:
    directory = output / servant_id
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / asset["filename"]
    if destination.is_file() and not force:
        data = destination.read_bytes()
    else:
        data = _request(asset["url"], timeout=60)
        if not data.startswith(PNG_MAGIC):
            raise ValueError(f"not a PNG: {asset['url']}")
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(data)
        os.replace(temporary, destination)
    if not data.startswith(PNG_MAGIC):
        raise ValueError(f"invalid cached PNG: {destination}")
    return {
        "group": asset["group"],
        "key": asset["key"],
        "file": f"{servant_id}/{destination.name}",
        "source": asset["url"],
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def update(
    catalog_path: Path,
    output: Path,
    *,
    region: str,
    selected_ids: set[str],
    workers: int,
    force: bool,
    family: str = "commands",
) -> tuple[int, int]:
    catalog = _load_catalog(catalog_path)
    records = _load_atlas_records(catalog, region, selected_ids)
    output.mkdir(parents=True, exist_ok=True)
    # Maa treats JSON files under a resource bundle as pipeline definitions.
    # Keep maintenance metadata extensionless so it is not loaded as a node.
    manifest_path = output / "manifest"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"schema_version": 1, "source": f"Atlas Academy NiceServant.extraAssets.{family}", "servants": {}}
    manifest.setdefault("servants", {})

    jobs: list[tuple[str, dict[str, str]]] = []
    for servant_id, record in sorted(records.items(), key=lambda item: int(item[0])):
        assets = _portrait_assets(record, family)
        if assets:
            jobs.extend((servant_id, asset) for asset in assets)

    downloaded: dict[str, list[dict[str, str]]] = {}
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {
            pool.submit(_download_one, servant_id, asset, output, force): (servant_id, asset)
            for servant_id, asset in jobs
        }
        for future in concurrent.futures.as_completed(future_map):
            servant_id, asset = future_map[future]
            try:
                downloaded.setdefault(servant_id, []).append(future.result())
            except Exception as exc:  # keep the complete failure list for maintenance runs
                errors.append(f"{servant_id}/{asset['filename']}: {exc}")

    for servant_id, assets in downloaded.items():
        manifest["servants"][servant_id] = sorted(assets, key=lambda item: item["file"])
    manifest["region"] = region
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if errors:
        raise RuntimeError(f"{family} asset download failures:\n" + "\n".join(errors))
    return len(downloaded), sum(len(values) for values in downloaded.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--family", choices=("commands", "status"), default="commands")
    parser.add_argument("--region", default="JP", choices=("JP", "CN", "NA", "TW", "KR"))
    parser.add_argument("--servant-id", action="append", default=[])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    selected = {str(int(value)) for value in args.servant_id}
    output = args.output or (STATUS_OUTPUT if args.family == "status" else DEFAULT_OUTPUT)
    servants, assets = update(
        args.catalog,
        output,
        region=args.region,
        selected_ids=selected,
        workers=args.workers,
        force=args.force,
        family=args.family,
    )
    print(f"Updated {assets} {args.family} portraits for {servants} servants -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
