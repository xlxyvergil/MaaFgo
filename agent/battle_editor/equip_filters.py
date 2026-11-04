"""Bundled, offline CE filter facts; this module never imports Atlas clients."""
from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path


SCHEMA_VERSION = 1
EFFECTS = (
    ("npStart", "初始 NP", "NP与循环"), ("npTurn", "每回合 NP", "NP与循环"),
    ("npGain", "NP 获得量", "NP与循环"), ("npOnHit", "受击 NP", "NP与循环"),
    ("npCharge", "条件 NP 充能", "NP与循环"),
    ("npDamage", "宝具威力", "卡色与输出"), ("overcharge", "宝具充能阶段", "卡色与输出"),
    ("arts", "Arts 性能", "卡色与输出"), ("buster", "Buster 性能", "卡色与输出"),
    ("quick", "Quick 性能", "卡色与输出"), ("attack", "攻击力", "卡色与输出"),
    ("specialAttack", "特攻", "卡色与输出"), ("critDamage", "暴击威力", "暴击与星星"),
    ("starDrop", "暴击星掉落率", "暴击与星星"), ("starGather", "暴击星集中度", "暴击与星星"),
    ("starTurn", "每回合暴击星", "暴击与星星"), ("starGain", "获得暴击星", "暴击与星星"),
    ("defense", "防御力", "生存与辅助"), ("damageCut", "伤害减免", "生存与辅助"),
    ("evade", "回避", "生存与辅助"), ("invincible", "无敌", "生存与辅助"),
    ("guts", "毅力", "生存与辅助"), ("heal", "HP 回复", "生存与辅助"),
    ("healUp", "HP 回复量", "生存与辅助"), ("weakResist", "弱化耐性", "生存与辅助"),
    ("weakImmune", "弱化无效", "生存与辅助"), ("invinciblePierce", "无敌贯通", "生存与辅助"),
    ("buffRemoveResist", "强化解除耐性", "生存与辅助"),
    ("sureHit", "必中", "生存与辅助"), ("defensePierce", "防御无视", "生存与辅助"),
    ("bond", "羁绊获取", "刷本收益"), ("qp", "QP 获取", "刷本收益"),
    ("masterExp", "御主经验", "刷本收益"), ("mysticExp", "御主礼装经验", "刷本收益"),
    ("friendPoints", "友情点", "刷本收益"), ("eventDrop", "活动掉落", "刷本收益"),
    ("materialDrop", "素材掉落", "刷本收益"),
    ("eventPoints", "活动点数", "刷本收益"), ("eventDamage", "活动伤害", "刷本收益"),
)
KINDS = (("normal", "普通礼装"), ("bond", "羁绊礼装"), ("valentine", "巧克力礼装"),
         ("exp", "经验礼装"), ("unknown", "类型未知"))
SOURCES = (("normal", "通常"), ("eventSummon", "活动召唤"), ("eventReward", "活动报酬"),
           ("campaign", "纪念配布"), ("shop", "达芬奇工坊"), ("bondReward", "从者羁绊奖励"),
           ("valentineReward", "情人节赠礼"), ("unknown", "来源未知"))


def read_equip_filters(project_root: Path | str) -> dict:
    path = Path(project_root).resolve() / "agent/battle_editor/data/equip_filters.json"
    try:
        stat = path.stat()
    except OSError:
        return {}
    return _read_cached(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=8)
def _read_cached(filename: str, modified: int, size: int) -> dict:
    try:
        data = json.loads(Path(filename).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if (not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION
            or not isinstance(data.get("equips"), dict)):
        return {}
    return data


def catalog_filter_metadata(index: dict) -> dict:
    return {"schemaVersion": SCHEMA_VERSION,
            "effects": [{"id": key, "label": label, "group": group} for key, label, group in EFFECTS],
            "kinds": [{"id": key, "label": label} for key, label in KINDS],
            "sources": [{"id": key, "label": label} for key, label in SOURCES],
            "available": bool(index.get("equips")),
            "version": copy.deepcopy(index.get("version", {}))}


def equip_filter_facts(index: dict, equip_id: str) -> dict | None:
    value = index.get("equips", {}).get(str(equip_id))
    return copy.deepcopy(value) if isinstance(value, dict) else None
