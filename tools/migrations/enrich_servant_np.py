"""按 servantId 将 CSV 宝具资料增量写入工程从者目录；默认仅预演。

只补充 np_type、np_card、np_variants，不新增从者、不按名称或图鉴号借用数据，
不覆盖已有的不同值。CSV 的 np_variants JSON 字符串转为真正的 JSON 数组。
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Any


NP_FIELDS = ("np_type", "np_card", "np_variants")
NP_TYPES = {"单体输出", "群体输出", "辅助宝具"}
NP_CARDS = {"Quick", "Arts", "Buster"}
DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "agent/custom/servant_list.json"


def parse_np_fields(row: dict[str, str]) -> dict[str, Any]:
    sid = row["servantId"]
    np_type = (row.get("np_type") or "").strip()
    np_card = (row.get("np_card") or "").strip()
    try:
        variants = json.loads(row.get("np_variants") or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{sid}: np_variants 不是合法 JSON") from exc
    if not isinstance(variants, list):
        raise ValueError(f"{sid}: np_variants 必须是数组")
    for variant in variants:
        if not isinstance(variant, dict):
            raise ValueError(f"{sid}: 宝具变体必须是对象")
        np_id = variant.get("treasureDeviceId")
        if type(np_id) is not int or np_id <= 0 or np_id == 100:
            raise ValueError(f"{sid}: 宝具 ID 非法或为普通攻击占位 ID")
        if (not isinstance(variant.get("type"), str) or variant["type"] not in NP_TYPES
                or not isinstance(variant.get("card"), str) or variant["card"] not in NP_CARDS):
            raise ValueError(f"{sid}: 宝具类型或色卡非法")
        if not isinstance(variant.get("name_jp"), str) or not variant["name_jp"]:
            raise ValueError(f"{sid}: 缺少宝具名称")
    if variants:
        if set(np_type.split("/")) != {v["type"] for v in variants}:
            raise ValueError(f"{sid}: np_type 与 np_variants 不一致")
        if set(np_card.split("/")) != {v["card"] for v in variants}:
            raise ValueError(f"{sid}: np_card 与 np_variants 不一致")
    elif np_type != "待补充" or np_card:
        raise ValueError(f"{sid}: 无宝具数据必须标记为待补充，且色卡留空")
    return {"np_type": np_type, "np_card": np_card, "np_variants": variants}


def read_np_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"servantId", "collectionNo", *NP_FIELDS}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError("CSV 缺少字段：" + ", ".join(sorted(missing)))
        return list(reader)


def enrich_catalog(data: dict[str, Any], rows: list[dict[str, str]]) -> tuple[dict[str, Any], dict]:
    by_id = {}
    for row in rows:
        sid = (row.get("servantId") or "").strip()
        if not sid.isascii() or not sid.isdecimal() or int(sid) <= 0 or sid in by_id:
            raise ValueError(f"CSV 从者 ID 非法或重复：{sid!r}")
        by_id[sid] = (row, parse_np_fields(row))
    result = copy.deepcopy(data)
    items = result.get("servants")
    if not isinstance(items, list):
        raise ValueError("从者目录的 servants 必须是数组")
    seen = set()
    report = {"added": 0, "unchanged": 0, "unmatched_catalog_ids": [], "unused_csv_ids": []}
    for item in items:
        sid = str(item["id"])
        if sid in seen:
            raise ValueError(f"从者目录 ID 重复：{sid}")
        seen.add(sid)
        if sid not in by_id:
            report["unmatched_catalog_ids"].append(sid)
            continue
        row, fields = by_id[sid]
        if item.get("collection_no") is not None and int(item["collection_no"]) != int(row["collectionNo"]):
            raise ValueError(f"{sid}: 图鉴号不一致，拒绝合并")
        changed = False
        for field, value in fields.items():
            if field in item:
                if item[field] != value:
                    raise ValueError(f"{sid}: 已有 {field} 与 CSV 冲突，拒绝覆盖")
            else:
                item[field] = copy.deepcopy(value)
                changed = True
        report["added" if changed else "unchanged"] += 1
    report["unused_csv_ids"] = sorted(set(by_id).difference(seen), key=int)
    # 整体比对：除了新增的三个字段，既有条目、顺序及顶层数据必须完全一致。
    stripped = copy.deepcopy(result)
    for old, new in zip(data["servants"], stripped["servants"]):
        for field in NP_FIELDS:
            if field not in old:
                new.pop(field, None)
    if stripped != data:
        raise AssertionError("发生了宝具新增字段以外的修改")
    return result, report


def migrate(csv_path: Path, catalog_path: Path, *, write: bool = False) -> dict:
    raw = catalog_path.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    result, report = enrich_catalog(data, read_np_csv(csv_path))
    if write and report["added"]:
        newline = "\r\n" if b"\r\n" in raw else "\n"
        text = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").replace("\n", newline)
        payload = text.encode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=catalog_path.parent, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            if catalog_path.read_bytes() != raw:
                raise RuntimeError("从者目录在预演期间已变化，拒绝覆盖")
            os.replace(temporary, catalog_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("servant_csv", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--write", action="store_true", help="实际写入；默认仅预演")
    args = parser.parse_args()
    try:
        report = migrate(args.servant_csv, args.catalog, write=args.write)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("增量写入完成。" if args.write else "预演完成；加 --write 才写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
