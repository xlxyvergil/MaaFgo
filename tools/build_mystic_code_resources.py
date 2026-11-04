"""Build native formation mystic-code templates from an existing FGO dump.

The dump's Items PNGs have opaque black atlas padding. Remove only dark pixels
connected to that padding, preserving enclosed black fabric and details. Missing
new assets can use the editor's cached Atlas images or explicitly download the
exact URLs from its pinned Nice export. No game data or source images are edited.

The default Nice export is the ignored editor build cache. Populate it with
tools/build_battle_editor_catalog.py --refresh, or pass --nice-export explicitly.
"""
from __future__ import annotations

import argparse
from collections import deque
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def remove_atlas_padding(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    pixels = list(image.getdata())
    width, height = image.size
    outside: set[int] = set()
    queue = deque([*range(width), *range((height - 1) * width, height * width),
                   *(y * width for y in range(height)),
                   *(y * width + width - 1 for y in range(height))])
    while queue:
        index = queue.popleft()
        if index in outside or max(pixels[index][:3]) > 15:
            continue
        outside.add(index)
        x, y = index % width, index // width
        if x:
            queue.append(index - 1)
        if x + 1 < width:
            queue.append(index + 1)
        if y:
            queue.append(index - width)
        if y + 1 < height:
            queue.append(index + width)
    image.putdata([(r, g, b, 0 if index in outside else 255)
                   for index, (r, g, b, _) in enumerate(pixels)])
    return image


def write_changed(path: Path, content: bytes) -> None:
    if path.is_file() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def png_bytes(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


def build(source_root: Path, *, download_missing: bool = False, proxy: str | None = None,
          nice_export: Path | None = None) -> dict:
    cache = ROOT / "config/BattleEditor/catalog-build"
    export_path = nice_export if nice_export is not None else cache / "JP_nice_mystic_code.json"
    if not export_path.is_file():
        raise FileNotFoundError(
            f"Missing Nice export: {export_path}. Pass --nice-export PATH, or populate "
            "the editor cache with python tools/build_battle_editor_catalog.py --refresh."
        )
    jp = json.loads(export_path.read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "agent/battle_editor/data/catalog.json").read_text(encoding="utf-8"))
    names = {item["id"]: item for item in catalog["mysticCodes"]}
    output = {"schemaVersion": 1, "mysticCodes": []}
    opener = build_opener(ProxyHandler({"https": proxy, "http": proxy})) if proxy else build_opener()
    for item in jp:
        record = {"id": item["id"], "name": names[item["id"]]["name"],
                  "nameJp": item["name"], "sources": {}}
        for gender in ("male", "female"):
            url = item["extraAssets"]["item"][gender]
            filename = Path(urlsplit(url).path).name
            image_id = int(filename.removeprefix("masterequip").removesuffix(".png"))
            record[gender + "ImageId"] = image_id
            jp_path = source_root / "图标拆分/Items" / filename
            cn_path = source_root / "国服图标拆分/Items" / filename
            editor_path = ROOT / "agent/battle_editor/data/media/mystic" / filename
            cached_path = cache / "mystic-native" / filename
            local_source = next((path for path in (jp_path, cn_path, editor_path, cached_path)
                                 if path.is_file()), None)
            if local_source is None:
                if not download_missing:
                    raise FileNotFoundError(f"Missing {filename}; use --download-missing to fetch {url}")
                if urlsplit(url).hostname != "static.atlasacademy.io":
                    raise ValueError(f"Untrusted source: {url}")
                with opener.open(Request(url, headers={"User-Agent": "MaaFGO/mystic-code-resources"}), timeout=30) as response:
                    raw = response.read()
                with Image.open(BytesIO(raw)) as check:
                    check.verify()
                write_changed(cached_path, raw)
                local_source = cached_path
            raw = local_source.read_bytes()
            image = Image.open(BytesIO(raw)).convert("RGBA")
            is_dump = local_source in (jp_path, cn_path)
            if is_dump:
                image = remove_atlas_padding(image)
            else:
                image = image.crop(image.getchannel("A").getbbox())
            target = ROOT / "assets/resource/base/image/MysticCodes" / filename
            output_raw = png_bytes(image)
            write_changed(target, output_raw)
            record["sources"][gender] = {"source": ("JP dump Items/" if local_source == jp_path else
                "CN dump Items/" if local_source == cn_path else url) + (filename if is_dump else ""),
                "sourceSha256": sha256(raw).hexdigest(), "sha256": sha256(output_raw).hexdigest(),
                "size": list(image.size), "padding": "exterior-dark-flood-fill" if is_dump else "alpha-bbox"}
            if cn_path.is_file():
                cn_raw = cn_path.read_bytes()
                cn_image = remove_atlas_padding(Image.open(BytesIO(cn_raw)))
                cn_output = png_bytes(cn_image)
                write_changed(ROOT / "assets/resource/cn/image/MysticCodes" / filename, cn_output)
                record["sources"][gender + "Cn"] = {"source": "CN dump Items/" + filename,
                    "sourceSha256": sha256(cn_raw).hexdigest(), "sha256": sha256(cn_output).hexdigest(),
                    "size": list(cn_image.size), "padding": "exterior-dark-flood-fill"}
        output["mysticCodes"].append(record)
    raw = (json.dumps(output, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    write_changed(ROOT / "agent/custom/mystic_code_data.json", raw)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--nice-export", type=Path,
                        help="Pinned JP Nice mystic-code JSON; defaults to the editor build cache")
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument("--proxy", help="Explicit HTTPS download proxy, only used with --download-missing")
    args = parser.parse_args()
    result = build(args.source_root, download_missing=args.download_missing, proxy=args.proxy,
                   nice_export=args.nice_export)
    print(f"Built {len(result['mysticCodes'])} mystic codes, both genders, with CN overrides")


if __name__ == "__main__":
    main()
