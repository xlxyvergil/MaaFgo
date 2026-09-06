"""将 CSV 中已核验的 Atlas 羁绊效果同步到工程礼装 JSON。"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def number(value: str) -> int | float:
    parsed = float(value or "0")
    return int(parsed) if parsed.is_integer() else parsed


def csv_bond(row: dict[str, str]) -> dict[str, Any]:
    return {
        "bonus_type": row.get("bond_bonus_type") or "none",
        "bonus": number(row.get("bond_bonus") or "0"),
        "target": row.get("bond_target") or "",
        "effect_desc": row.get("bond_effect_desc") or "",
        "atlas_effects": json.loads(row["bond_effects_atlas"]),
    }


FILTER_TAGS = {
    "通常（ストーリー/常駐ガチャ）": "通常",
    "キャンペーン・記念配布": "纪念配布",
    "イベント報酬": "活动报酬",
    "イベントガチャ（期間限定）": "活动召唤",
    "経験値（強化素材）": "经验值礼装",
    "マナプリズム交換": "达芬奇工坊",
    "バレンタイン・チョコレート": "巧克力",
    "フレンドポイント（友情）ガチャ": "通常",
}


def new_equip(row: dict[str, str]) -> dict[str, Any]:
    return {
        "id": row["equipId"],
        "collection_no": int(row["collectionNo"]),
        "name": row.get("name_cn") or row.get("name_jp") or row["equipId"],
        "rarity": int(row["rarity"]),
        "important": int(row["important"]),
        "images": [f"f_{row['equipId']}0.png"],
        "name_jp": row.get("name_jp") or "",
        "acquisition_category_jp": row.get("入手分类(日文)") or "",
        "filter_tag": FILTER_TAGS.get(row.get("入手分类(日文)") or "", ""),
        "bond": csv_bond(row),
    }


def load(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("JSON 根节点必须是对象")
    return data, newline


def write(path: Path, data: dict[str, Any], newline: str) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if newline != "\n":
        text = text.replace("\n", newline)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("equip_csv", type=Path)
    parser.add_argument("equip_json", type=Path)
    parser.add_argument("--write", action="store_true", help="实际写入；默认仅预演")
    args = parser.parse_args()

    rows = read_csv(args.equip_csv)
    candidates = {
        str(row["collectionNo"]): row
        for row in rows
        if (row.get("bond_effects_atlas") or "").strip()
    }
    data, newline = load(args.equip_json)
    before = copy.deepcopy(data)
    changed = skipped = appended = 0
    missing: list[str] = []
    existing_collections = {str(item.get("collection_no")) for item in data.get("equips", [])}
    for item in data.get("equips", []):
        row = candidates.get(str(item.get("collection_no")))
        if row is None:
            continue
        bond = item.get("bond")
        if not isinstance(bond, dict):
            missing.append(f"{item.get('collection_no')}:{item.get('name')}")
            continue
        incoming = csv_bond(row)
        if all(bond.get(key) == value for key, value in incoming.items()):
            skipped += 1
            continue
        # bond 中的其它既有键（若未来添加）不删除；只同步此次归档的五项资料。
        bond.update(incoming)
        changed += 1

    # 历史列表按“高价值礼装”筛过，会漏掉有真实羁绊收益的纪念/工坊礼装。
    # 这里只追加明确存在常驻公式的条目；活动限定且标准公式为 none 的条目不进入自动编队候选。
    for collection_no, row in candidates.items():
        if collection_no in existing_collections or (row.get("bond_bonus_type") or "none") == "none":
            continue
        data.setdefault("equips", []).append(new_equip(row))
        existing_collections.add(collection_no)
        appended += 1

    # 严格断言：只有明确匹配的 equip.bond 子对象允许变化。
    for old, new in zip(before.get("equips", []), data.get("equips", [])):
        if old is new:
            continue
        for field, value in old.items():
            if field == "bond":
                continue
            if new.get(field) != value:
                raise AssertionError(f"礼装本体字段被意外修改：{old.get('collection_no')}.{field}")
    if len(data.get("equips", [])) != len(before.get("equips", [])) + appended:
        raise AssertionError("礼装条目数量变化不符合追加预期")

    print(f"CSV 已核验效果：{len(candidates)}；同步变更：{changed}；追加条目：{appended}；无需变更：{skipped}；缺少 bond：{len(missing)}")
    if missing:
        print("缺少 bond：" + "；".join(missing))
    if not args.write:
        print("预演完成；使用 --write 才会写入 JSON。")
        return 0
    if not changed and not appended:
        print("没有增量变更，无需写入 JSON。")
        return 0
    write(args.equip_json, data, newline)
    print("已原子写入礼装 JSON（由 Git 追踪，不创建备份文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
