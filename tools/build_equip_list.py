"""从 Chaldea 礼装 CSV 生成自动编队使用的礼装数据库。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


# CSV 的日文入手分类映射到游戏筛选面板中的资源文件名。友情点召唤礼装在
# 游戏的筛选器中归入“通常”；从者羁绊礼装使用独立的“羁绊礼装”筛选项。
ACQUISITION_FILTER_TAGS = {
    "通常（ストーリー/常駐ガチャ）": "通常",
    "キャンペーン・記念配布": "纪念配布",
    "イベント報酬": "活动报酬",
    "イベントガチャ（期間限定）": "活动召唤",
    "経験値（強化素材）": "经验值礼装",
    "マナプリズム交換": "达芬奇工坊",
    "バレンタイン・チョコレート": "巧克力",
    "フレンドポイント（友情）ガチャ": "通常",
    "絆礼装": "羁绊礼装",
}


def authoritative_categories(path: Path | None = None) -> dict:
    """Atlas-confirmed identities override older CSV classification only."""
    path = path or Path(__file__).resolve().parents[1] / "agent/custom/equip_categories.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(data, dict) or data.get("schemaVersion") != 1
            or not isinstance(data.get("equips"), dict)):
        raise ValueError("Atlas 礼装分类权威表格式不正确")
    return data["equips"]


def to_int(value: str, field: str, equip_id: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"equipId={equip_id} 的 {field} 不是整数: {value!r}") from exc


def build(source: Path, destination: Path, *, categories_path: Path | None = None) -> int:
    with source.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    equips = []
    unknown_categories = set()
    categories = authoritative_categories(categories_path)
    for row in rows:
        if row.get("非高价值礼装") != "0":
            continue
        equip_id = row.get("equipId", "")
        category_jp = categories.get(equip_id, {}).get("acquisition_category_jp", row.get("入手分类(日文)", ""))
        filter_tag = ACQUISITION_FILTER_TAGS.get(category_jp)
        if not filter_tag:
            unknown_categories.add(category_jp)
            continue
        equips.append({
            "id": equip_id,
            "collection_no": to_int(row.get("collectionNo", ""), "collectionNo", equip_id),
            "name": row.get("name_cn", ""),
            "rarity": to_int(row.get("rarity", ""), "rarity", equip_id),
            "important": to_int(row.get("important", ""), "important", equip_id),
            "images": [f"f_{equip_id}0.png"],
            "name_jp": row.get("name_jp", ""),
            "acquisition_category_jp": category_jp,
            "filter_tag": filter_tag,
        })
    if unknown_categories:
        raise ValueError(f"未映射的礼装入手分类: {sorted(unknown_categories)!r}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"equips": equips}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",  # Match Git's LF bytes so the editor's source hash is portable.
    )
    return len(equips)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="包含入手分类(日文)列的 equip_list.csv")
    parser.add_argument("destination", type=Path, help="生成的 equip_list.json")
    args = parser.parse_args()
    print(f"已生成 {build(args.source, args.destination)} 条礼装数据")


if __name__ == "__main__":
    main()
