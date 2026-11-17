#!/usr/bin/env python
"""从真实 EquipFaces 源图构建可选的 config/SupportCE 助战礼装扩展 ZIP。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "extras/support-ce"
DEFAULT_SOURCES = INPUTS / "sources"
DEFAULT_STAR = INPUTS / "limit-star.png"
STATUSES = ("满破", "非满破")
TYPE = "maafgo-support-ce"


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _image(path, size):
    with Image.open(path) as image:
        image.load()
        if image.format != "PNG" or image.size != size or image.mode != "RGBA":
            raise ValueError(f"源图格式不符：{path}；需要 {size[0]}×{size[1]} RGBA PNG，"
                             f"实际为 {image.size} {image.mode}。请重新解包正确的游戏资源。")
        return image.copy()


def plan(catalog, source_dir, builtin_manifest=None, *, ids=None, include_builtin=False):
    """Select by stable ID; alternate existing source names never come from a name guess."""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise ValueError(f"找不到助战源图目录：{source_dir}。请将 --source-dir 指向解包后的 150×68 EquipFaces 目录。")
    rows = _json(catalog).get("equips")
    if not isinstance(rows, list):
        raise ValueError("礼装运行清单缺少 equips 列表，无法确定扩展包内容。请先重新生成 equip_list.json。")
    builtin_ids = set()
    if not include_builtin and builtin_manifest:
        for filename in _json(builtin_manifest).values():
            match = re.fullmatch(r"f_([1-9][0-9]*)0\.png", filename)
            if match:
                builtin_ids.add(int(match[1]))
    requested = set(ids) if ids is not None else None
    known, selected, missing, excluded = set(), [], [], []
    for row in rows:
        try:
            eid = int(row["id"])
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError("礼装运行清单包含无法读取的 ID。请修正 equip_list.json 后重新生成。") from error
        if eid <= 0 or eid in known or not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ValueError(f"礼装运行清单中的 ID {eid} 重复、非正数或缺少名称。请修正清单后重新生成。")
        known.add(eid)
        if requested is not None and eid not in requested:
            continue
        if eid in builtin_ids:
            excluded.append(eid)
            continue
        candidates = [f"f_{eid}0.png", *row.get("images", [])]
        filename = next((name for name in candidates
                         if isinstance(name, str) and re.fullmatch(r"f_[1-9][0-9]*\.png", name)
                         and (source_dir / name).is_file()), None)
        if filename is None:
            missing.append({"id": eid, "name": row["name"], "expected": f"f_{eid}0.png"})
            continue
        path = source_dir / filename
        if not path.resolve().is_relative_to(source_dir.resolve()):
            raise ValueError(f"礼装源图路径越过解包目录：{filename}。请移除指向目录外的链接后重试。")
        _image(path, (150, 68))
        selected.append({"id": eid, "name": row["name"], "source": filename})
    if requested is not None and requested - known:
        raise ValueError(f"运行清单中没有礼装 ID：{', '.join(map(str, sorted(requested - known)))}。请先补齐运行时资料。")
    return {"selected": sorted(selected, key=lambda item: item["id"]),
            "missing": sorted(missing, key=lambda item: item["id"]),
            "excluded_builtin": sorted(excluded)}


def _png_bytes(image):
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True, compress_level=9)
    return output.getvalue()


def check_missing(selection, expected_path):
    """Only explicitly reviewed gaps may be omitted from a release."""
    expected = _json(expected_path)
    actual = selection["missing"]
    if actual != expected:
        raise ValueError(
            f"助战源图缺失项与 {expected_path} 不一致，无法确认扩展包是否完整。"
            "请先查看上方缺图列表，补齐真实 EquipFaces 源图；"
            "确实暂缺源图时，核对礼装 ID 和名称后更新该清单，再重新打包。")


def build_images(source, star):
    """Same 150×68 -> 158×44 conversion and alpha compositing as formal support."""
    image = _image(source, (150, 68))
    resized = image.resize((158, 72), Image.Resampling.BILINEAR)
    normal = resized.crop((0, 14, 158, 58)).quantize(
        colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    maximum = normal.convert("RGBA")
    maximum.paste(star, (131, 22), star)
    return {"非满破": _png_bytes(normal), "满破": _png_bytes(maximum)}


def _zip_info(path):
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build_archive(output, selected, source_dir, star_path, *, name="MaaFGO 助战礼装扩展", version="1"):
    if not selected:
        raise ValueError("没有可打包的礼装。请检查 ID 筛选、解包源图，或使用 --include-builtin 包含已有礼装。")
    star = _image(star_path, (20, 20))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schemaVersion": 1, "type": TYPE, "name": name, "version": version,
                "equips": [], "sha256": {}}
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".zip.tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for item in sorted(selected, key=lambda row: row["id"]):
                images = build_images(Path(source_dir) / item["source"], star)
                templates = {}
                for status in STATUSES:
                    path = f"{status}/f_{item['id']}0.png"
                    payload = images[status]
                    templates[status] = path
                    manifest["sha256"][path] = hashlib.sha256(payload).hexdigest()
                    archive.writestr(_zip_info(path), payload)
                manifest["equips"].append({"id": item["id"], "name": item["name"], "templates": templates})
            content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
            archive.writestr(_zip_info("manifest.json"), content)
        verify_archive(temporary_path)
        temporary_path.replace(output)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"output": str(output.resolve()), "equips": len(selected), "images": len(selected) * 2,
            "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def verify_archive(path):
    """Verify exact inventory, CRC, PNG properties and each template SHA-256."""
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"压缩包文件校验失败：{bad}。请重新生成扩展包。")
        names = archive.namelist()
        if len(names) != len(set(name.casefold() for name in names)):
            raise ValueError("压缩包含重复文件名。请重新生成扩展包。")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("schemaVersion") != 1 or manifest.get("type") != TYPE:
            raise ValueError("压缩包不是受支持的助战礼装扩展格式。请重新生成。")
        expected, ids = {"manifest.json"}, set()
        for row in manifest["equips"]:
            if type(row["id"]) is not int or row["id"] <= 0 or row["id"] in ids:
                raise ValueError("压缩包礼装 ID 重复或无效。请重新生成。")
            ids.add(row["id"])
            if set(row["templates"]) != set(STATUSES):
                raise ValueError("压缩包中的礼装缺少满破或非满破模板。请重新生成。")
            for status, filename in row["templates"].items():
                parsed = PurePosixPath(filename)
                if (parsed.is_absolute() or ".." in parsed.parts or len(parsed.parts) != 2
                        or parsed.parts[0] != status or "\\" in filename or ":" in filename):
                    raise ValueError("压缩包中的图片路径越过礼装目录。请重新生成。")
                expected.add(filename)
                payload = archive.read(filename)
                if hashlib.sha256(payload).hexdigest() != manifest["sha256"].get(filename):
                    raise ValueError(f"压缩包图片哈希不一致：{filename}。请重新生成。")
                with Image.open(io.BytesIO(payload)) as image:
                    image.load()
                    expected_mode = "P" if status == "非满破" else "RGBA"
                    if image.format != "PNG" or image.size != (158, 44) or image.mode != expected_mode:
                        raise ValueError(f"压缩包图片规格不符：{filename}。请重新生成。")
        if set(names) != expected or set(manifest["sha256"]) != expected - {"manifest.json"}:
            raise ValueError("压缩包清单与文件集合不一致。请重新生成。")
    return {"equips": len(ids), "images": len(expected) - 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "agent/custom/equip_list.json")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--star", type=Path, default=DEFAULT_STAR)
    parser.add_argument("--builtin-manifest", type=Path, default=INPUTS / "builtin.json")
    parser.add_argument("--output", type=Path, default=ROOT / "build/support-ce/MaaFGO-SupportCE-DLC.zip")
    parser.add_argument("--name", default="MaaFGO 助战礼装扩展")
    parser.add_argument("--version", default="1")
    parser.add_argument("--ids", type=int, nargs="+", help="只生成列出的礼装 ID")
    parser.add_argument("--include-builtin", action="store_true", help="同时包含默认内置的助战礼装")
    parser.add_argument("--strict", action="store_true", help="有缺失源图时停止，不输出部分扩展包")
    parser.add_argument("--check-missing", type=Path, help="仅允许清单中已确认的缺图；新增或已补齐的缺图均须更新清单")
    parser.add_argument("--dry-run", action="store_true", help="只检查源图及差异，不创建目录或 ZIP")
    parser.add_argument("--verify", type=Path, metavar="ZIP", help="只校验已生成的扩展 ZIP")
    args = parser.parse_args()
    try:
        if args.verify:
            print(json.dumps(verify_archive(args.verify), ensure_ascii=False))
            return 0
        selection = plan(args.catalog, args.source_dir, args.builtin_manifest,
                         ids=args.ids, include_builtin=args.include_builtin)
        _image(args.star, (20, 20))
        summary = {"selected": len(selection["selected"]), "excluded_builtin": len(selection["excluded_builtin"]),
                   "missing": selection["missing"], "dry_run": args.dry_run}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.check_missing:
            check_missing(selection, args.check_missing)
        if selection["missing"] and args.strict:
            parser.error("存在上面列出的缺失助战源图，已停止生成。请重新解包对应 EquipFaces 后重试，或不加 --strict 跳过这些礼装。")
        if not args.dry_run:
            report = build_archive(args.output, selection["selected"], args.source_dir, args.star,
                                   name=args.name, version=args.version)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"生成助战礼装扩展失败：{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
