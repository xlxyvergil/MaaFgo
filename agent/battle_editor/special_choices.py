"""Offline editor metadata for the native special-skill selection queues.

Load the dependency-free native registry by file, so both ``battle_editor`` and
``agent.battle_editor`` entry points work without importing a controller package.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_SPEC = importlib.util.spec_from_file_location(
    "_battle_editor_special_skill_core",
    Path(__file__).resolve().parents[1] / "battle" / "core" / "special_skills.py",
)
_CORE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _CORE
_SPEC.loader.exec_module(_CORE)
SPECIAL_SKILL_GROUPS = _CORE.SPECIAL_SKILL_GROUPS

# Labels describe game effects, rather than exposing recognition-node names.
_LABELS = {
    "战斗_仇凛2技能_色卡选择": ("Arts 宝具", "Buster 宝具", "Quick 宝具"),
    "战斗_库库尔坎_暴击星消耗": ("不消耗暴击星", "消耗 10 个暴击星"),
    "战斗_马嘶1技能_无敌选择": ("无敌", "毅力"),
    "战斗_梵高矿工1技能_Arts选择": ("Quick", "Arts", "Buster"),
    "战斗_莉莉丝3技能_不赋予选择": ("不附加即死状态", "附加宝具后自身即死状态"),
    "战斗_普雷拉蒂1技能_不换形态选择": ("不更换灵基", "更换灵基"),
    "战斗_夏洛特3技能_Arts选择": ("Arts 性能", "暴击威力", "宝具威力"),
    "战斗_芙萝拉3技能_通常状态选择": ("通常状态", "冥界花园状态"),
    "战斗_安哥拉曼纽3技能_50%毅力选择": ("不赋予毅力", "毅力回复 50% HP", "毅力回复 1 HP"),
    "战斗_但丁2技能_天国Arts选择": ("天国化（Arts）", "地狱化（Buster）"),
    "战斗_玉兔2技能_宝具类型选择": ("月下（全体宝具）", "日轮（单体宝具）"),
    "战斗_岸波白野3技能_天之力选择": ("天之力特攻", "地之力特攻", "人之力特攻"),
    "战斗_静希草十郎3技能_红卡选择": ("Quick", "Arts", "Buster"),
    "战斗_巴格斯特3技能_全体宝具选择": ("全体宝具", "单体宝具"),
}

# JP Atlas skill.script.SelectAddInfo, checked against the bundled catalog and
# the 2026-10-29 source snapshots. Earlier variants have no selection menu.
_MENU_SKILL_IDS = {
    "战斗_马嘶1技能_无敌选择": (604550,),
    "战斗_夏洛特3技能_Arts选择": (2379350,),
    "战斗_芙萝拉3技能_通常状态选择": (2589650, 2589675),
    "战斗_安哥拉曼纽3技能_50%毅力选择": (458900,),
    "战斗_岸波白野3技能_天之力选择": (2415451,),
}


def special_skill_catalog() -> dict:
    """Return fresh JSON values, with numbering shared with the native registry."""
    groups = []
    for native in SPECIAL_SKILL_GROUPS:
        labels = _LABELS.get(native.root)
        options = []
        for index, node in enumerate(native.options):
            label = labels[index] if labels and index < len(labels) else node.removeprefix("战斗_").replace("_", " · ")
            option = {"value": native.first_value + index, "label": label}
            # Kukulkan's shared menu covers S1/S3 (no ally target) and S2
            # (ally target), so preserve the selected skill's target metadata.
            if native.root != "战斗_库库尔坎_暴击星消耗":
                option["target"] = "none"
            options.append(option)
        group = {"id": native.root, "servantIds": list(native.servant_ids),
                 "skillNumbers": list(native.skills), "field": native.field,
                 "options": options, "defaultValue": native.default}
        if native.root in _MENU_SKILL_IDS:
            group["skillIds"] = list(_MENU_SKILL_IDS[native.root])
        groups.append(group)
    return {"schemaVersion": 1, "groups": groups}


_MATCH_GROUPS = tuple(special_skill_catalog()["groups"])


def match_special_skill(member, skill_index, servant_meta=None):
    """Return ``(group, applicable)``; None applicability needs better metadata.

    A known older skill variant does not consume a menu choice. The native
    executor currently cannot safely mix such a variant with an active queue of
    the same field, so callers can explicitly report that execution boundary.
    """
    if not isinstance(member, dict) or type(skill_index) is not int or not 0 <= skill_index <= 2:
        return None, False
    identities = {str(member.get("svtId"))}
    if isinstance(servant_meta, dict):
        identities.add(str(servant_meta.get("id")))
        aliases = servant_meta.get("aliases")
        if isinstance(aliases, list):
            identities.update(str(alias) for alias in aliases)
    group = next((row for row in _MATCH_GROUPS
                  if identities.intersection(row["servantIds"])
                  and skill_index + 1 in row["skillNumbers"]), None)
    if group is None or not group.get("skillIds"):
        return group, bool(group)
    explicit = member.get("skillIds")
    chosen = explicit[skill_index] if isinstance(explicit, list) and len(explicit) > skill_index else None
    if chosen is None or type(chosen) is int and chosen == 0:
        skills = (servant_meta or {}).get("skills", [])
        chosen = next((row.get("id") for row in skills
                       if isinstance(row, dict) and row.get("num") == skill_index + 1), None)
    if type(chosen) is not int or chosen <= 0:
        return group, None
    return group, chosen in group["skillIds"]
