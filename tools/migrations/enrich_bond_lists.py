"""以归档 CSV 增量补充自动编队 JSON 的羁绊资料。

仅在现有条目没有 ``bond`` 键时追加该键；绝不替换既有字段或覆盖已有羁绊资料。
默认只预演并输出统计，传入 ``--write`` 后才原子写入。工程文件由 Git 追踪，
因此不额外创建备份副本。
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def parse_json_cell(row: dict[str, str], field: str, default: Any) -> Any:
    value = (row.get(field) or "").strip()
    if not value:
        return copy.deepcopy(default)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"CSV 字段 {field} 不是合法 JSON：{value!r}") from exc


def parse_int_cell(row: dict[str, str], field: str) -> int:
    value = (row.get(field) or "0").strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"CSV 字段 {field} 不是整数：{value!r}") from exc


def servant_bond(row: dict[str, str]) -> dict[str, Any]:
    return {
        "tags": parse_json_cell(row, "bond_tags", []),
        "tags_by_ascension": parse_json_cell(row, "bond_tags_by_ascension", {}),
        "tags_by_costume": parse_json_cell(row, "bond_tags_by_costume", []),
        "alignment": row.get("bond_alignment") or "",
        "attribute": row.get("bond_attribute") or "",
        "gender": row.get("bond_gender") or "",
        "sub_attribute": row.get("bond_sub_attribute") or "",
        "atlas_tags_raw": parse_json_cell(row, "bond_tags_atlas_raw", []),
    }


def equip_bond(row: dict[str, str]) -> dict[str, Any]:
    return {
        "bonus_type": row.get("bond_bonus_type") or "none",
        "bonus": parse_int_cell(row, "bond_bonus"),
        "target": row.get("bond_target") or "",
        "effect_desc": row.get("bond_effect_desc") or "",
    }


def enrich_servants(data: dict[str, Any], rows: list[dict[str, str]]) -> tuple[int, int, list[str]]:
    by_collection = {str(row["collectionNo"]): row for row in rows}
    by_id = {str(row["servantId"]): row for row in rows}
    added = skipped = 0
    missing: list[str] = []
    for item in data.get("servants", []):
        if "bond" in item:
            skipped += 1
            continue
        row = by_collection.get(str(item.get("collection_no")))
        if row is None:
            # 个别旧条目没有 collection_no，以游戏 servant ID 对应 CSV 补充。
            row = by_id.get(str(item.get("id")))
        if row is None:
            missing.append(f"{item.get('id')}:{item.get('name')}")
            continue
        item["bond"] = servant_bond(row)
        added += 1
    return added, skipped, missing


def enrich_equips(data: dict[str, Any], rows: list[dict[str, str]]) -> tuple[int, int, list[str]]:
    by_collection = {str(row["collectionNo"]): row for row in rows}
    added = skipped = 0
    missing: list[str] = []
    for item in data.get("equips", []):
        if "bond" in item:
            skipped += 1
            continue
        row = by_collection.get(str(item.get("collection_no")))
        if row is None:
            missing.append(f"{item.get('id')}:{item.get('name')}")
            continue
        item["bond"] = equip_bond(row)
        added += 1
    return added, skipped, missing


def append_new_equips(data: dict[str, Any], rows: list[dict[str, str]]) -> int:
    """仅追加 CSV 中的高价值礼装，既有 JSON 条目绝不修改。"""
    existing_ids = {str(item.get("id")) for item in data.get("equips", [])}
    existing_collections = {str(item.get("collection_no")) for item in data.get("equips", [])}
    appended = 0
    for row in rows:
        if row.get("非高价值礼装") != "0":
            continue
        if row["equipId"] in existing_ids or row["collectionNo"] in existing_collections:
            continue
        data.setdefault("equips", []).append(
            {
                "id": row["equipId"],
                "collection_no": parse_int_cell(row, "collectionNo"),
                "name": row.get("name_cn") or row.get("name_jp") or row["equipId"],
                "rarity": parse_int_cell(row, "rarity"),
                "important": parse_int_cell(row, "important"),
                "images": [f"f_{row['equipId']}0.png"],
                "name_jp": row.get("name_jp") or "",
                "acquisition_category_jp": row.get("入手分类(日文)") or "",
                "filter_tag": {
                    "通常（ストーリー/常駐ガチャ）": "通常",
                    "キャンペーン・記念配布": "纪念配布",
                    "イベント報酬": "活动报酬",
                    "イベントガチャ（期間限定）": "活动召唤",
                    "経験値（強化素材）": "经验值礼装",
                    "マナプリズム交換": "达芬奇工坊",
                    "バレンタイン・チョコレート": "巧克力",
                    "フレンドポイント（友情）ガチャ": "通常",
                }.get(row.get("入手分类(日文)") or "", ""),
                "bond": equip_bond(row),
            }
        )
        existing_ids.add(row["equipId"])
        existing_collections.add(row["collectionNo"])
        appended += 1
    return appended


def drop_data_source(data: dict[str, Any], key: str) -> int:
    """删除过去版本写入的来源字段；不会触碰其他 bond 数据。"""
    removed = 0
    for item in data.get(key, []):
        bond = item.get("bond")
        if isinstance(bond, dict) and "data_source" in bond:
            del bond["data_source"]
            removed += 1
    return removed


def load_json(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点不是对象：{path}")
    return data, newline


def write_json_incrementally(path: Path, data: dict[str, Any], newline: str) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if newline != "\n":
        text = text.replace("\n", newline)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)


def assert_existing_fields_unchanged(
    before: dict[str, Any], after: dict[str, Any], key: str, allow_data_source_removal: bool, allow_append: bool
) -> None:
    before_items = before.get(key, [])
    after_items = after.get(key, [])
    if len(before_items) > len(after_items) or (not allow_append and len(before_items) != len(after_items)):
        raise AssertionError(f"{key} 条目数量发生变化")
    for index, (old, new) in enumerate(zip(before_items, after_items)):
        for field, value in old.items():
            if field == "bond" and allow_data_source_removal:
                expected = copy.deepcopy(value)
                if isinstance(expected, dict):
                    expected.pop("data_source", None)
                if new.get(field) != expected:
                    raise AssertionError(f"{key}[{index}].bond 出现非来源字段的修改")
                continue
            if new.get(field) != value:
                raise AssertionError(f"{key}[{index}].{field} 被意外修改")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("servant_csv", type=Path)
    parser.add_argument("equip_csv", type=Path)
    parser.add_argument("servant_json", type=Path)
    parser.add_argument("equip_json", type=Path)
    parser.add_argument("--write", action="store_true", help="实际写入；默认预演")
    parser.add_argument(
        "--drop-data-source",
        action="store_true",
        help="删除历史版本写入的 bond.data_source 字段",
    )
    parser.add_argument(
        "--append-new-equips",
        action="store_true",
        help="仅追加 CSV 中已有但 JSON 尚未收录的高价值礼装",
    )
    args = parser.parse_args()

    servant_data, servant_newline = load_json(args.servant_json)
    equip_data, equip_newline = load_json(args.equip_json)
    before_servant_data = copy.deepcopy(servant_data)
    before_equip_data = copy.deepcopy(equip_data)

    servant_added, servant_skipped, servant_missing = enrich_servants(
        servant_data, read_csv(args.servant_csv)
    )
    equip_rows = read_csv(args.equip_csv)
    equip_added, equip_skipped, equip_missing = enrich_equips(equip_data, equip_rows)
    appended_equips = append_new_equips(equip_data, equip_rows) if args.append_new_equips else 0
    removed_servant_sources = removed_equip_sources = 0
    if args.drop_data_source:
        removed_servant_sources = drop_data_source(servant_data, "servants")
        removed_equip_sources = drop_data_source(equip_data, "equips")
    assert_existing_fields_unchanged(
        before_servant_data, servant_data, "servants", args.drop_data_source, False
    )
    assert_existing_fields_unchanged(
        before_equip_data, equip_data, "equips", args.drop_data_source, args.append_new_equips
    )

    print(f"从者：新增 bond={servant_added}，跳过已有 bond={servant_skipped}，未匹配={len(servant_missing)}")
    print(f"礼装：新增 bond={equip_added}，跳过已有 bond={equip_skipped}，未匹配={len(equip_missing)}")
    if args.append_new_equips:
        print(f"新增礼装条目：{appended_equips}")
    if args.drop_data_source:
        print(f"已移除来源字段：从者={removed_servant_sources}，礼装={removed_equip_sources}")
    if servant_missing:
        print("未匹配从者：" + "；".join(servant_missing))
    if equip_missing:
        print("未匹配礼装：" + "；".join(equip_missing))
    if not args.write:
        print("预演完成；使用 --write 才会写入 JSON。")
        return 0

    wrote_servants = servant_added > 0 or removed_servant_sources > 0
    wrote_equips = equip_added > 0 or removed_equip_sources > 0 or appended_equips > 0
    if wrote_servants:
        write_json_incrementally(args.servant_json, servant_data, servant_newline)
        print("已原子写入从者 JSON（由 Git 追踪，不创建备份文件）")
    if wrote_equips:
        write_json_incrementally(args.equip_json, equip_data, equip_newline)
        print("已原子写入礼装 JSON（由 Git 追踪，不创建备份文件）")
    if not wrote_servants and not wrote_equips:
        print("没有增量变更，无需写入 JSON。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
