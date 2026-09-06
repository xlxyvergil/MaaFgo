"""将羁绊计算器的静态特性/礼装效果归档到从者和礼装 CSV。

该工具只解析 HTML 中的 JSON 数据块，不执行其中的 JavaScript。首次归档时可用
``--write`` 更新 CSV；更新前会在同目录创建带时间戳的备份。默认仅输出检查结果。
"""

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


SERVANT_COLUMNS = [
    "bond_tags",
    "bond_tags_by_ascension",
    "bond_tags_by_costume",
    "bond_alignment",
    "bond_attribute",
    "bond_gender",
    "bond_sub_attribute",
    "bond_tags_atlas_raw",
]
EQUIP_COLUMNS = [
    "bond_bonus_type",
    "bond_bonus",
    "bond_target",
    "bond_effect_desc",
]


def extract_json_assignment(html: str, variable: str) -> dict[str, Any]:
    """取得 ``VARIABLE = {...}`` 中的 JSON 对象，且绝不执行 HTML 脚本。"""
    marker = f"{variable} ="
    start = html.find(marker)
    if start < 0:
        raise ValueError(f"HTML 中未找到 {marker!r}")
    start = html.find("{", start + len(marker))
    if start < 0:
        raise ValueError(f"{variable} 后未找到 JSON 对象")
    try:
        value, _ = json.JSONDecoder().raw_decode(html[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"{variable} 不是有效 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{variable} 不是 JSON 对象")
    return value


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError(f"CSV 缺少表头：{path}")
        return list(reader.fieldnames), list(reader)


def json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


ATLAS_TAG_MAP = {
    "genderMale": "male",
    "genderFemale": "female",
    "alignmentLawful": "lawful",
    "alignmentNeutral": "neutral",
    "alignmentChaotic": "chaotic",
    "alignmentGood": "good",
    "alignmentEvil": "evil",
    "attributeStar": "star_power",
    "wildbeast": "beast_species",
}
ATLAS_STANDARD_CLASSES = {
    "classSaber": "saber_class",
    "classArcher": "archer_class",
    "classLancer": "lancer_class",
    "classRider": "rider_class",
    "classCaster": "caster_class",
    "classAssassin": "assassin_class",
    "classBerserker": "berserker_class",
}


def fetch_atlas_servant(collection_no: str, region: str) -> dict[str, Any]:
    """获取单个 Atlas Nice Servant；仅在计算器未收录时调用。"""
    url = f"https://api.atlasacademy.io/nice/{region}/servant/{collection_no}"
    context = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Atlas collectionNo={collection_no} 返回格式错误")
    return payload


def atlas_to_bond_tags(servant: dict[str, Any]) -> tuple[list[str], list[str], str, str, str]:
    """保留 Atlas 原始标签，并转换当前羁绊匹配器可识别的标准标签。"""
    raw_tags: list[str] = []
    converted: list[str] = []
    alignment = ""
    attribute = ""
    gender = ""
    for trait in servant.get("traits") or []:
        if not isinstance(trait, dict):
            continue
        name = str(trait.get("name") or "")
        if not name:
            continue
        raw_tags.append(name)
        mapped = ATLAS_TAG_MAP.get(name)
        if mapped:
            converted.append(mapped)
        mapped_class = ATLAS_STANDARD_CLASSES.get(name)
        if mapped_class:
            converted.append(mapped_class)
        if name.startswith("classBeast"):
            converted.append("beast_class")
        if name.startswith("alignment"):
            suffix = name.removeprefix("alignment").lower()
            if suffix in {"lawful", "neutral", "chaotic"}:
                alignment = suffix
            if suffix in {"good", "evil", "balanced", "summer"}:
                attribute = suffix
        if name.startswith("gender"):
            suffix = name.removeprefix("gender").lower()
            if suffix in {"male", "female"}:
                gender = suffix
    return list(dict.fromkeys(converted)), raw_tags, alignment, attribute, gender


def enrich_servants(
    rows: list[dict[str, str]],
    servants: dict[str, Any],
    ascension_traits: dict[str, Any],
    costume_traits: dict[str, Any],
    alignments: dict[str, Any],
) -> tuple[int, list[str]]:
    updated = 0
    missing: list[str] = []
    for row in rows:
        key = str(row["collectionNo"]).zfill(3)
        servant = servants.get(key)
        if not isinstance(servant, dict):
            # 计算器未收录的剧情/Boss 实体显式归档为空标签，避免下游把空单元格
            # 误解为“资料尚未迁移”。
            row.update(
                {
                    "bond_tags": "[]",
                    "bond_tags_by_ascension": "{}",
                    "bond_tags_by_costume": "[]",
                    "bond_alignment": "",
                    "bond_attribute": "",
                    "bond_gender": "",
                    "bond_sub_attribute": "",
                }
            )
            missing.append(row["collectionNo"])
            continue
        alignment = alignments.get(key, {})
        costume = costume_traits.get(key, {})
        row.update(
            {
                "bond_tags": json_cell(servant.get("traits") or []),
                "bond_tags_by_ascension": json_cell(ascension_traits.get(key) or {}),
                "bond_tags_by_costume": json_cell(
                    costume.get("traits", costume) if isinstance(costume, dict) else costume
                ),
                "bond_alignment": str(alignment.get("align") or ""),
                "bond_attribute": str(alignment.get("attr") or ""),
                "bond_gender": str(alignment.get("gender") or ""),
                "bond_sub_attribute": str(alignment.get("sub_attr") or ""),
                "bond_tags_atlas_raw": "[]",
            }
        )
        updated += 1
    return updated, missing


def enrich_missing_servants_from_atlas(
    rows: list[dict[str, str]], collection_numbers: set[str], region: str
) -> tuple[int, list[str]]:
    """以 Atlas 补充未被计算器收录的条目，保留原始和转换后的标签。"""
    updated = 0
    errors: list[str] = []
    for row in rows:
        if str(row.get("collectionNo")) not in collection_numbers:
            continue
        collection_no = row["collectionNo"]
        try:
            atlas_servant = fetch_atlas_servant(collection_no, region)
            tags, raw_tags, alignment, attribute, gender = atlas_to_bond_tags(atlas_servant)
        except Exception as exc:
            errors.append(f"{collection_no}: {exc}")
            continue
        row.update(
            {
                "bond_tags": json_cell(tags),
                "bond_tags_by_ascension": "{}",
                "bond_tags_by_costume": "[]",
                "bond_alignment": alignment,
                "bond_attribute": attribute,
                "bond_gender": gender,
                "bond_sub_attribute": str(atlas_servant.get("attribute") or ""),
                "bond_tags_atlas_raw": json_cell(raw_tags),
            }
        )
        updated += 1
    return updated, errors


def enrich_equips(
    rows: list[dict[str, str]], craft_essences: dict[str, Any]
) -> tuple[int, list[str]]:
    updated = 0
    bond_tag_without_formula: list[str] = []
    for row in rows:
        key = str(row["collectionNo"])
        effect = craft_essences.get(key)
        if isinstance(effect, dict):
            row.update(
                {
                    "bond_bonus_type": str(effect.get("bonus_type") or "none"),
                    "bond_bonus": str(effect.get("bonus") or 0),
                    "bond_target": str(effect.get("target") or ""),
                    "bond_effect_desc": str(effect.get("desc") or ""),
                    "bond_data_source": "FGO牵绊计算器（优化版）",
                }
            )
            updated += 1
        else:
            # Atlas 后续新增的礼装不一定存在于参考计算器的静态表中；只要 CSV
            # 已有可计算公式，就必须原样保留，不能被默认 none 覆盖。
            if (row.get("bond_bonus_type") or "none") != "none" or (row.get("bond_bonus") or "0") != "0":
                continue
            row.update(
                {
                    "bond_bonus_type": "none",
                    "bond_bonus": "0",
                    "bond_target": "",
                    "bond_effect_desc": "",
                    "bond_data_source": "",
                }
            )
            if "羁绊" in row.get("tags", ""):
                bond_tag_without_formula.append(
                    f"{row.get('collectionNo')} {row.get('name_cn')}"
                )
    return updated, bond_tag_without_formula


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.before-bond-archive-{timestamp}{path.suffix}")
    shutil.copy2(path, backup)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return backup


def ensure_writable(path: Path) -> None:
    """在修改任一 CSV 前确认全部目标文件可写，避免出现半完成更新。"""
    try:
        with path.open("r+", encoding="utf-8-sig", newline=""):
            pass
    except OSError as exc:
        raise RuntimeError(f"CSV 当前不可写：{path}（请关闭占用它的程序）") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path, help="FGO 牵绊计算器 HTML")
    parser.add_argument("servants", type=Path, help="servant_list.csv")
    parser.add_argument("equips", type=Path, help="equip_list.csv")
    parser.add_argument("--write", action="store_true", help="写入 CSV；省略时仅检查")
    parser.add_argument(
        "--atlas-region",
        choices=("JP", "NA"),
        help="以 Atlas Nice Servant 补充计算器未收录的从者（需联网）",
    )
    args = parser.parse_args()

    html = args.html.read_text(encoding="utf-8")
    servants = extract_json_assignment(html, "SERVANTS")
    craft_essences = extract_json_assignment(html, "CRAFT_ESSENCES")
    ascension_traits = extract_json_assignment(html, "ASCENSION_TRAITS")
    costume_traits = extract_json_assignment(html, "COSTUME_TRAITS")
    alignments = extract_json_assignment(html, "SERVANT_ALIGNMENTS")

    servant_fields, servant_rows = read_csv(args.servants)
    equip_fields, equip_rows = read_csv(args.equips)
    # 来源信息不参与运行时计算，也不保留在归档文件中。
    servant_fields = [field for field in servant_fields if field != "bond_data_source"]
    equip_fields = [field for field in equip_fields if field != "bond_data_source"]
    servant_fields += [field for field in SERVANT_COLUMNS if field not in servant_fields]
    equip_fields += [field for field in EQUIP_COLUMNS if field not in equip_fields]

    servant_count, missing_servants = enrich_servants(
        servant_rows, servants, ascension_traits, costume_traits, alignments
    )
    atlas_count = 0
    atlas_errors: list[str] = []
    if args.atlas_region:
        atlas_count, atlas_errors = enrich_missing_servants_from_atlas(
            servant_rows, set(missing_servants), args.atlas_region
        )
    equip_count, bond_tag_without_formula = enrich_equips(equip_rows, craft_essences)

    print(f"从者：{servant_count}/{len(servant_rows)} 条已归档")
    if args.atlas_region:
        print(f"Atlas {args.atlas_region}：补充 {atlas_count} 条从者")
    print(f"礼装：{equip_count}/{len(equip_rows)} 条已写入可计算羁绊效果")
    print(f"带‘羁绊’标签但计算器无公式：{len(bond_tag_without_formula)} 条")
    if missing_servants:
        print("计算器未收录的从者图鉴号：" + ", ".join(missing_servants))
    if atlas_errors:
        print("Atlas 补充失败：" + "；".join(atlas_errors))
    if bond_tag_without_formula:
        print("无公式礼装示例：" + "；".join(bond_tag_without_formula[:8]))

    if not args.write:
        print("检查完成；使用 --write 才会写入 CSV。")
        return 0

    ensure_writable(args.servants)
    ensure_writable(args.equips)
    servant_backup = write_csv(args.servants, servant_fields, servant_rows)
    equip_backup = write_csv(args.equips, equip_fields, equip_rows)
    print(f"已写入从者 CSV，备份：{servant_backup}")
    print(f"已写入礼装 CSV，备份：{equip_backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
