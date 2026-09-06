"""将一张 Atlas Academy 新礼装以增量方式归档到 equip_list.csv。"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import ssl
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


TARGETS = {
    "classSaber": ("saber_class", "Saber职阶"),
    "classArcher": ("archer_class", "Archer职阶"),
    "classLancer": ("lancer_class", "Lancer职阶"),
    "classRider": ("rider_class", "Rider职阶"),
    "classCaster": ("caster_class", "Caster职阶"),
    "classAssassin": ("assassin_class", "Assassin职阶"),
    "classBerserker": ("berserker_class", "Berserker职阶"),
}
ACQUISITION = {"svtEquipManaExchange": "マナプリズム交換"}


def fetch(collection_no: int) -> dict[str, Any]:
    url = f"https://api.atlasacademy.io/nice/JP/equip/{collection_no}"
    context = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Atlas 返回格式错误")
    return data


def rate(function: dict[str, Any]) -> float:
    values = function.get("svals") or []
    raw = next((item.get("RateCount") for item in values if item.get("RateCount") is not None), None)
    if raw is None:
        raise ValueError("羁绊函数缺少 RateCount")
    return float(raw) / 10


def effect(data: dict[str, Any]) -> tuple[str, int, str, str]:
    candidates: list[tuple[int, float, dict[str, Any], dict[str, Any]]] = []
    for skill in data.get("skills") or []:
        for function in skill.get("functions") or []:
            if function.get("funcType") == "servantFriendshipUp":
                candidates.append((int(skill.get("strengthStatus") or 0), rate(function), skill, function))
    if not candidates:
        raise ValueError("Atlas 未返回可解析的羁绊加成")
    _status, max_rate, _skill, function = max(candidates, key=lambda item: (item[0], item[1]))
    raw_target = next((item.get("name") for item in function.get("functvals") or [] if item.get("name")), "")
    if raw_target not in TARGETS:
        raise ValueError(f"未支持的羁绊目标：{raw_target!r}")
    target, target_label = TARGETS[raw_target]
    base_rates = [value for status, value, _skill, _function in candidates if status < 99]
    description = f"{target_label}+{max_rate:g}%"
    if base_rates:
        description += f"（满破；未满破+{max(base_rates):g}%）"
    return "percent", int(max_rate) if max_rate.is_integer() else max_rate, target, description


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("collection_no", type=int)
    parser.add_argument("--name-cn", required=True, help="人工确认的中文名称")
    parser.add_argument("--write", action="store_true", help="实际追加；默认仅预演")
    args = parser.parse_args()

    with args.csv_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if any(str(row.get("collectionNo")) == str(args.collection_no) for row in rows):
        raise ValueError(f"图鉴号 {args.collection_no} 已存在，拒绝重复追加")

    atlas = fetch(args.collection_no)
    if int(atlas.get("collectionNo") or 0) != args.collection_no:
        raise ValueError("Atlas 返回图鉴号与请求不一致")
    bonus_type, bonus, target, description = effect(atlas)
    acquisition = ACQUISITION.get(str(atlas.get("flag") or ""))
    if not acquisition:
        raise ValueError(f"未映射的 Atlas 获取标记：{atlas.get('flag')!r}")
    row = {
        "equipId": str(atlas["id"]),
        "collectionNo": str(atlas["collectionNo"]),
        "name_jp": str(atlas["name"]),
        "name_cn": args.name_cn,
        "rarity": str(atlas["rarity"]),
        "important": "1",
        "category": "获取类",
        "tags": "羁绊",
        "非高价值礼装": "0",
        "入手分类(日文)": acquisition,
        "bond_bonus_type": bonus_type,
        "bond_bonus": str(bonus),
        "bond_target": target,
        "bond_effect_desc": description,
    }
    print(json.dumps(row, ensure_ascii=False, indent=2))
    if not args.write:
        print("预演完成；使用 --write 才会追加 CSV。")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = args.csv_path.with_name(f"{args.csv_path.stem}.before-atlas-append-{timestamp}{args.csv_path.suffix}")
    shutil.copy2(args.csv_path, backup)
    # 文件头已有 BOM；追加时必须使用 utf-8，避免在中间再次写入 BOM。
    with args.csv_path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="raise")
        writer.writerow(row)
    print(f"已追加 CSV，备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
