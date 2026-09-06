"""审计指定礼装中 Atlas 标注的羁绊加成。

默认仅输出审计结果；传入 ``--write`` 时会先备份、再更新 CSV。
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fetch(row: dict[str, str]) -> tuple[dict[str, str], dict[str, Any] | None, str | None]:
    collection_no = row["collectionNo"]
    url = f"https://api.atlasacademy.io/nice/JP/equip/{collection_no}"
    request = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        return row, data, None
    except Exception as exc:  # 审计应尽量报告其它条目的结果。
        return row, None, str(exc)


def friendship_effects(data: dict[str, Any]) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for skill in data.get("skills") or []:
        for function in skill.get("functions") or []:
            if function.get("funcType") != "servantFriendshipUp":
                continue
            effects.append(
                {
                    "strength_status": skill.get("strengthStatus"),
                    "svals": function.get("svals") or [],
                    "functvals": function.get("functvals") or [],
                }
            )
    return effects


TARGETS = {
    "classSaber": ("saber_class", "Saber职阶"),
    "classArcher": ("archer_class", "Archer职阶"),
    "classLancer": ("lancer_class", "Lancer职阶"),
    "classRider": ("rider_class", "Rider职阶"),
    "classCaster": ("caster_class", "Caster职阶"),
    "classAssassin": ("assassin_class", "Assassin职阶"),
    "classBerserker": ("berserker_class", "Berserker职阶"),
    "alignmentNeutral": ("neutral", "中立特性"),
    "havingAnimalsCharacteristics": ("beast_species", "兽科从者"),
    "FSNServant": ("fsn", "FSN从者"),
    "livingHuman": ("living_human", "活在当下的人类"),
    "hasCostume": ("has_sprite", "拥有灵衣之人"),
}
SPECIAL_TARGETS = {
    frozenset(("alignmentEvil", "attributeStar")): ("star_or_evil", "星之力或恶"),
}


def rate_from(effect: dict[str, Any]) -> float | None:
    for value in effect["svals"]:
        if value.get("RateCount") is not None:
            return float(value["RateCount"]) / 10
    return None


def unconditional_formula(effects: list[dict[str, Any]]) -> dict[str, Any] | None:
    """解析不受活动或未映射特性限制的常驻羁绊效果。"""
    candidates: list[tuple[int, float, str, str, str]] = []
    for effect in effects:
        if any(value.get("EventId") for value in effect["svals"]):
            continue
        if any(value.get("Individuality") not in (None, 0) for value in effect["svals"]):
            continue
        targets = [value.get("name") for value in effect["functvals"] if value.get("name")]
        if not targets:
            target, label = "all", "全员"
        elif len(targets) == 1:
            target, label = TARGETS.get(targets[0], ("", ""))
        else:
            target, label = SPECIAL_TARGETS.get(frozenset(targets), ("", ""))
        if not target:
            continue
        for value in effect["svals"]:
            if value.get("AddCount") is not None and target == "all":
                candidates.append((int(effect["strength_status"] or 0), float(value["AddCount"]), "flat_per_servant", target, "全队非助战从者各"))
            elif value.get("RateCount") is not None:
                candidates.append((int(effect["strength_status"] or 0), float(value["RateCount"]) / 10, "percent", target, label))
    if not candidates:
        return None
    status, bonus, kind, target, label = max(candidates, key=lambda item: (item[0], item[1]))
    base = [item[1] for item in candidates if item[0] < status and item[2:] == (kind, target, label)]
    shown = f"{bonus:g}"
    if kind == "flat_per_servant":
        desc = f"{label}+{shown}"
    else:
        desc = f"{label}+{shown}%"
    if base and max(base) != bonus:
        desc += f"（满破；未满破+{max(base):g}{'' if kind == 'flat_per_servant' else '%'}）"
    return {"bonus_type": kind, "bonus": shown, "target": target, "effect_desc": desc}


def normalized_effects(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """以 CSV 可审计的精简结构保留活动 ID、目标和加成值。"""
    result: list[dict[str, Any]] = []
    for effect in effects:
        statuses = int(effect["strength_status"] or 0)
        for value in effect["svals"]:
            entry: dict[str, Any] = {"strength_status": statuses}
            if value.get("AddCount") is not None:
                entry["flat_bonus"] = value["AddCount"]
            if value.get("RateCount") is not None:
                entry["percent_bonus"] = float(value["RateCount"]) / 10
            if value.get("EventId"):
                entry["event_id"] = value["EventId"]
            if value.get("Individuality") not in (None, 0):
                entry["target_trait_id"] = value["Individuality"]
            if value.get("ApplySupportSvt") is not None:
                entry["apply_support_servant"] = bool(value["ApplySupportSvt"])
            names = [item.get("name") for item in effect["functvals"] if item.get("name")]
            if names:
                entry["target_traits"] = names
            result.append(entry)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--write", action="store_true", help="将缺失的常驻公式与 Atlas 条件归档写入 CSV")
    args = parser.parse_args()
    with args.csv_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    selected = [
        row
        for row in rows
        if row.get("入手分类(日文)") == "マナプリズム交換" or "羁绊" in row.get("tags", "")
    ]
    print(f"候选：{len(selected)} 张")
    found: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(fetch, row) for row in selected]
        for future in as_completed(futures):
            row, data, error = future.result()
            if error:
                errors.append(f"{row['collectionNo']}: {error}")
                continue
            assert data is not None
            effects = friendship_effects(data)
            if effects:
                found.append(
                    {
                        "collectionNo": row["collectionNo"],
                        "equipId": row["equipId"],
                        "name_jp": row["name_jp"],
                        "name_cn": row["name_cn"],
                        "flag": data.get("flag"),
                        "type": data.get("type"),
                        "effects": effects,
                        "archived": {
                            key: row.get(key, "")
                            for key in (
                                "bond_bonus_type",
                                "bond_bonus",
                                "bond_target",
                                "bond_effect_desc",
                            )
                        },
                    }
                )
    found.sort(key=lambda item: int(item["collectionNo"]))
    missing_permanent: list[dict[str, Any]] = []
    unresolved_permanent: list[dict[str, Any]] = []
    event_only = 0
    for item in found:
        formula = unconditional_formula(item["effects"])
        has_event = any(
            value.get("EventId")
            for effect in item["effects"]
            for value in effect["svals"]
        )
        if formula and item["archived"]["bond_bonus_type"] == "none":
            missing_permanent.append({**item, "formula": formula})
        elif not formula and not has_event:
            unresolved_permanent.append(item)
        if has_event and not formula:
            event_only += 1

    print(f"Atlas 实际羁绊效果：{len(found)} 张")
    print(f"待补常驻公式：{len(missing_permanent)} 张；仅活动效果：{event_only} 张；待人工映射的常驻特性：{len(unresolved_permanent)} 张")
    for item in missing_permanent:
        print(f"补充 {item['collectionNo']} {item['name_cn']}：{item['formula']['effect_desc']}")
    for item in unresolved_permanent:
        print(f"未映射 {item['collectionNo']} {item['name_cn']}：{json.dumps(normalized_effects(item['effects']), ensure_ascii=False)}")
    if errors:
        print("读取失败：" + "；".join(errors))
    if not args.write:
        print("检查完成；使用 --write 才会写入 CSV。")
        return 0 if not errors else 2

    fields = list(rows[0]) if rows else []
    if "bond_effects_atlas" not in fields:
        fields.append("bond_effects_atlas")
    by_collection = {item["collectionNo"]: item for item in found}
    updated_formulas = updated_effect_archives = 0
    for row in rows:
        item = by_collection.get(row["collectionNo"])
        if item is None:
            continue
        formula = unconditional_formula(item["effects"])
        if formula and (row.get("bond_bonus_type") or "none") == "none":
            row.update(
                {
                    "bond_bonus_type": formula["bonus_type"],
                    "bond_bonus": formula["bonus"],
                    "bond_target": formula["target"],
                    "bond_effect_desc": formula["effect_desc"],
                }
            )
            updated_formulas += 1
        normalized = json.dumps(normalized_effects(item["effects"]), ensure_ascii=False, separators=(",", ":"))
        if not row.get("bond_effects_atlas"):
            row["bond_effects_atlas"] = normalized
            updated_effect_archives += 1
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = args.csv_path.with_name(f"{args.csv_path.stem}.before-atlas-bond-sync-{timestamp}{args.csv_path.suffix}")
    shutil.copy2(args.csv_path, backup)
    with args.csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"已补充常驻公式：{updated_formulas} 张；已归档 Atlas 条件：{updated_effect_archives} 张；备份：{backup}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
