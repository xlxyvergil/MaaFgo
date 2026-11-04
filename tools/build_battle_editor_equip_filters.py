"""Compile curated CE filters from official Atlas Nice data; maintenance only.

The editor reads only the compact result. No network access occurs without
--refresh; the raw export and triggered-skill cache are never shipped.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.atlasacademy.io"
_SPEC = importlib.util.spec_from_file_location("_equip_filter_defs", ROOT / "agent/battle_editor/equip_filters.py")
_DEFS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_DEFS)
LABELS = {key: label for key, label, _group in _DEFS.EFFECTS}
TARGETS = {"self": "自身", "ptAll": "己方全体", "ptFull": "己方全队", "ptOne": "己方单体",
           "ptOther": "其他己方从者", "enemy": "敌方单体", "enemyAll": "敌方全体",
           "ptAllWithoutSelf": "除自身外的己方全体"}
CARD_TRAITS = {"cardArts": "arts", "cardBuster": "buster", "cardQuick": "quick"}
BUFF_TAGS = {
    "regainNp": ("npTurn", 100, "%"), "upDropnp": ("npGain", 10, "%"),
    "upDamagedropnp": ("npOnHit", 10, "%"), "upNpdamage": ("npDamage", 10, "%"),
    "upChagetd": ("overcharge", 1, "阶段"), "upAtk": ("attack", 10, "%"),
    "upDamageIndividuality": ("specialAttack", 10, "%"),
    "upDamageIndividualityActiveonly": ("specialAttack", 10, "%"),
    "upCriticaldamage": ("critDamage", 10, "%"), "upCriticalpoint": ("starDrop", 10, "%"),
    "upStarweight": ("starGather", 10, "%"), "regainStar": ("starTurn", 1, "个"),
    "upDefence": ("defense", 10, "%"), "subSelfdamage": ("damageCut", 1, ""),
    "avoidance": ("evade", None, ""), "invincible": ("invincible", None, ""),
    "guts": ("guts", 1, "HP"), "gutsRatio": ("guts", 10, "% HP"),
    "regainHp": ("heal", 1, "HP/回合"), "upGainHp": ("healUp", 10, "%"),
    "upGivegainHp": ("healUp", 10, "%"), "upTolerance": ("weakResist", 10, "%"),
    "upToleranceSubstate": ("buffRemoveResist", 10, "%"), "avoidState": ("weakImmune", None, ""),
    "pierceInvincible": ("invinciblePierce", None, ""), "breakAvoidance": ("sureHit", None, ""),
    "pierceDefence": ("defensePierce", None, ""),
}
FUNCTION_TAGS = {
    "servantFriendshipUp": ("bond", "RateCount", 10, "%"),
    "qpDropUp": ("qp", "RateCount", 10, "%"),
    "expUp": ("masterExp", "RateCount", 10, "%"),
    "userEquipExpUp": ("mysticExp", "RateCount", 10, "%"),
    "friendPointUpDuplicate": ("friendPoints", "AddCount", 1, "点"),
    "eventDropUp": ("eventDrop", "AddCount", 1, "个"),
    "classDropUp": ("eventDrop", "AddCount", 1, "个"),
    "eventDropRateUp": ("eventDrop", "RateCount", 10, "%"),
    "eventPointUp": ("eventPoints", "RateCount", 10, "%"),
    "eventFortificationPointUp": ("eventPoints", "RateCount", 10, "%"),
}
KNOWN_OTHER_BUFFS = {
    "reduceHp", "downDefence", "downTolerance", "downCriticaldamage", "downCriticalpoint", "downCommandall",
    "subMaxhp", "addMaxhp", "upGrantstate", "upHate", "addIndividuality", "upGrantInstantdeath",
    "upResistInstantdeath", "avoidInstantdeath", "overwriteClassRelation", "addDamage",
    "downCriticalRateDamageTaken", "upDamage", "upDamageEventPoint", "upCommandall", "upCommandatk",
}
KNOWN_OTHER_FUNCTIONS = {"enemyEncountCopyRateUp", "enemyEncountRateUp", "addBattlePoint"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def fetch(url):
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "MaaFGO/offline-equip-filters"})
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read()
        return gzip.decompress(raw) if response.headers.get("Content-Encoding") == "gzip" else raw


def value_at(function, level=1, key="svals"):
    values = function.get(key) or []
    return values[min(max(level - 1, 0), len(values) - 1)] if values else {}


def trigger_refs(skills):
    return {value_at(function).get("Value") for skill in skills for function in skill.get("functions", [])
            if any("Function" in buff.get("type", "") for buff in function.get("buffs", []))
            and type(value_at(function).get("Value")) is int}


def source_facts(equip):
    flags = set(equip.get("flags") or [equip.get("flag")])
    kind, source = "normal", "unknown"
    if equip.get("bondEquipOwner") or "svtEquipFriendShip" in flags:
        kind, source = "bond", "bondReward"
    elif equip.get("valentineEquipOwner") or "svtEquipChocolate" in flags:
        kind, source = "valentine", "valentineReward"
    elif equip.get("type") in ("svtEquipExp", "svtEquipExpCard") or "svtEquipExp" in flags:
        kind = "exp"
    else:
        source = next((name for flag, name in (
            ("svtEquipEventReward", "eventReward"), ("svtEquipEvent", "eventSummon"),
            ("svtEquipCampaign", "campaign"), ("svtEquipManaExchange", "shop"), ("normal", "normal"))
                       if flag in flags), "unknown")
    atk, hp = equip.get("atkMax"), equip.get("hpMax")
    atk_known, hp_known = type(atk) is int and atk >= 0, type(hp) is int and hp >= 0
    known = atk_known and hp_known
    return {"kind": kind, "kindLabel": dict(_DEFS.KINDS)[kind],
            "source": source, "sourceLabel": dict(_DEFS.SOURCES)[source],
            **({"bondOwner": equip["bondEquipOwner"]} if equip.get("bondEquipOwner") else {}),
            "atkMax": atk if atk_known else None, "hpMax": hp if hp_known else None,
            "statType": ("mixed" if atk and hp else "atk" if atk else "hp" if hp else "none") if known else "unknown"}


def skill_groups(equip, mlb):
    groups = {}
    for skill in equip.get("skills", []):
        bindings = [item for item in skill.get("skillSvts", []) if item.get("svtId") == equip["id"]]
        binding = bindings[0] if bindings else skill
        limit = binding.get("condLimitCount", skill.get("condLimitCount", 0))
        if type(limit) is not int or limit > (4 if mlb else 0):
            continue
        number = binding.get("num") or skill.get("num") or 1
        key = (number, binding.get("eventId", 0), binding.get("condQuestId", 0),
               binding.get("condQuestPhase", 0), json.dumps(skill.get("script") or {}, sort_keys=True),
               json.dumps(binding.get("releaseConditions") or [], sort_keys=True))
        rank = (limit, binding.get("priority", skill.get("priority", 0)), skill.get("id", 0))
        if key not in groups or rank > groups[key][0]:
            groups[key] = rank, skill, binding, number
    return [(skill, binding, number) for _rank, skill, binding, number in groups.values()]


def traits(rows):
    return [item.get("name") if item.get("name") not in (None, "unknown") else f"特性#{item.get('id')}"
            for item in rows if isinstance(item, dict)]


def conditions(equip, skill, binding, number, function, values, buff):
    result = []
    if equip.get("bondEquipOwner"):
        result.append(f"仅从者 #{equip['bondEquipOwner']} 装备时")
    events = {item for item in (binding.get("eventId"), values.get("EventId")) if item}
    if events:
        result.append("限定活动 " + "/".join(f"#{event}" for event in sorted(events)))
    quest = binding.get("condQuestId") or skill.get("condQuestId")
    if quest:
        result.append(f"需关卡 #{quest} 解锁")
    if number != 1 and not events:
        result.append("附加技能条件")
    if skill.get("script"):
        result.append("技能条件 " + json.dumps(skill["script"], ensure_ascii=False, separators=(",", ":")))
    if binding.get("releaseConditions"):
        result.append("解锁条件 " + json.dumps(binding["releaseConditions"], ensure_ascii=False, separators=(",", ":")))
    for field, label in (("functvals", "对象特性"), ("funcquestTvals", "关卡特性")):
        names = traits(function.get(field) or [])
        if names:
            result.append(label + "：" + "/".join(names))
    if function.get("script"):
        result.append("函数条件 " + json.dumps(function["script"], ensure_ascii=False, separators=(",", ":")))
    for field, label in (("ckSelfIndv", "自身/指令卡特性"), ("ckOpIndv", "敌方特性")):
        # These inspect the incoming buff or the built-in guts stacking gate,
        # rather than requiring a specific enemy or battle state.
        if field == "ckOpIndv" and buff.get("type") in ("guts", "gutsRatio", "upTolerance", "upToleranceSubstate", "avoidState"):
            continue
        intrinsic = {*CARD_TRAITS, "canBeInBattle"} if buff.get("type") in ("upCommandall", "upCommandatk") else {"canBeInBattle"}
        names = [name for name in traits(buff.get(field) or []) if name not in intrinsic]
        if names:
            result.append(label + "：" + "/".join(names))
    if values.get("OnField"):
        result.append("装备者在场时")
    if values.get("Rate", 1000) < 1000:
        result.append(f"发动概率 {values['Rate'] / 10:g}%")
    if 0 < values.get("UseRate", 1000) < 1000:
        result.append(f"效果触发概率 {values['UseRate'] / 10:g}%")
    return result


def effect_records(equip, skill, binding, number, function, nested, *, level=1, inherited=(), depth=0):
    values = value_at(function, level)
    buff = (function.get("buffs") or [{}])[0]
    buff_type, func_type = buff.get("type", ""), function.get("funcType", "")
    cond = list(dict.fromkeys([*inherited, *conditions(equip, skill, binding, number, function, values, buff)]))
    if "Function" in buff_type:
        trigger = {"entryFunction": "登场时", "deadFunction": "退场时", "gutsFunction": "毅力触发时",
                   "selfturnendFunction": "回合结束时"}.get(buff_type, "触发时（" + buff_type + "）")
        target = nested.get(values.get("Value"))
        if target and depth < 4:
            return [row for inner in target.get("functions", [])
                    for row in effect_records(equip, target, {}, 1, inner, nested,
                                              level=values.get("Value2", 1), inherited=(*cond, trigger), depth=depth + 1)]
        return [{"tags": [], "description": "触发效果（" + (skill.get("detail") or buff.get("detail") or buff_type) + "）",
                 "conditional": True, "target": TARGETS.get(function.get("funcTargetType"), function.get("funcTargetType", "未知")),
                 "condition": "；".join([*cond, trigger]), "skillId": skill.get("id"), "functionId": function.get("funcId")}]

    tags, amount, unit = [], None, ""
    if func_type == "gainNp":
        tags = ["npCharge" if inherited else "npStart"]
        amount, unit = values["Value"] / 100 if type(values.get("Value")) in (int, float) else None, "%"
    elif func_type == "gainStar":
        tags, amount, unit = ["starGain"], values.get("Value", 0), "个"
    elif func_type == "gainHp":
        tags, amount, unit = ["heal"], values.get("Value", 0), "HP"
    elif func_type in FUNCTION_TAGS:
        tag, key, divisor, unit = FUNCTION_TAGS[func_type]
        tags = [tag]
        amount = values.get(key) / divisor if type(values.get(key)) in (int, float) else None
        if func_type in ("eventDropUp", "classDropUp") and amount is None and "RateCount" in values:
            amount, unit = values["RateCount"] / 10, "%"
        if func_type == "eventDropRateUp":
            tags = ["eventDrop" if values.get("EventId") else "materialDrop"]
            raw = values.get("DropRateCount", values.get("AddCount", values.get("RateCount")))
            amount, unit = raw / 10 if type(raw) in (int, float) else None, "%"
    elif buff_type in ("upCommandall", "upCommandatk"):
        names = traits(buff.get("ckSelfIndv") or buff.get("tvals") or [])
        tags = [CARD_TRAITS[name] for name in names if name in CARD_TRAITS]
        amount, unit = values.get("Value", 0) / 10, "%"
    elif buff_type in BUFF_TAGS:
        tag, divisor, unit = BUFF_TAGS[buff_type]
        tags = [tag]
        amount = values.get("Value", 0) / divisor if divisor else None
    elif buff_type in ("upDamage", "upDamageEventPoint"):
        if (binding.get("eventId") or values.get("EventId")
                or any(row.get("eventId") for row in function.get("funcGroup", []))):
            tags = ["eventDamage"]
        elif buff.get("ckOpIndv"):
            tags = ["specialAttack"]
        amount, unit = values.get("Value", 0) / 10, "%"
    # Negative effects and unknown buffs remain readable, but never acquire a
    # positive filter tag (e.g. Black Grail's HP loss is not a healing effect).
    label = " / ".join(LABELS[tag] for tag in tags) if tags else buff.get("name") or function.get("funcPopupText") or func_type
    target = TARGETS.get(function.get("funcTargetType"), function.get("funcTargetType") or "未知")
    if func_type == "eventDropRateUp" and not values.get("EventId") and values.get("Individuality"):
        target = f"素材特性 #{values['Individuality']}"
    description = label + (f" {amount:g}{unit}" if amount is not None else "")
    if buff_type in ("upTolerance", "upToleranceSubstate", "avoidState") and buff.get("name"):
        description += "（" + buff["name"] + "）"
    if values.get("Count", -1) > 0:
        description += f"（{values['Count']}次）"
    if values.get("Turn", -1) > 0:
        description += f"（{values['Turn']}回合）"
    record = {"tags": tags, "description": description, "conditional": bool(cond), "target": target,
              "skillId": skill.get("id"), "functionId": function.get("funcId")}
    known = (bool(tags) or buff_type in KNOWN_OTHER_BUFFS or func_type in KNOWN_OTHER_FUNCTIONS)
    if not known:
        record["unmapped"] = True
        record["description"] = "未结构化效果：" + (skill.get("detail") or buff.get("detail") or description)
    elif not tags and (buff.get("detail") or skill.get("detail")):
        record["description"] += "：" + (buff.get("detail") or skill["detail"])
    if cond:
        record["condition"] = "；".join(cond)
    if amount is not None:
        record.update(value=amount, unit=unit)
    records = [record]
    follower = value_at(function, level, "followerVals")
    if follower and follower != values:
        altered = dict(function, svals=[follower], followerVals=[])
        records.extend(effect_records(equip, skill, binding, number, altered, nested,
                                      inherited=(*inherited, "作为助战时"), depth=depth))
    return records


def compile_equip(equip, nested=None):
    result = source_facts(equip)
    for basis, mlb in (("base", False), ("mlb", True)):
        records, details = [], []
        groups = skill_groups(equip, mlb)
        for skill, binding, number in groups:
            if skill.get("detail"):
                details.append(skill["detail"])
            for function in skill.get("functions", []):
                records.extend(effect_records(equip, skill, binding, number, function, nested or {}))
        records = list({json.dumps(row, ensure_ascii=False, sort_keys=True): row for row in records}.values())
        tags = list(dict.fromkeys(tag for row in records if not row["conditional"] for tag in row["tags"]))
        initial_rows = [row for row in records if not row["conditional"] and "npStart" in row["tags"]]
        initial_known = groups and not any(row.get("unmapped") and not row["conditional"] for row in records)
        if any(row.get("value") is None for row in initial_rows):
            initial_known = False
        initial = sum(row.get("value", 0) for row in initial_rows)
        result[basis] = {"tags": tags, "initialNp": initial if initial_known else None,
                         "effects": records, "details": list(dict.fromkeys(details))}
    return result


def compile_index(curated, exports, nested=None, version=None):
    by_id = {int(row["id"]): row for row in exports}
    selected = [int(row["id"]) for row in curated]
    missing = sorted(set(selected) - by_id.keys())
    if missing:
        raise ValueError(f"Atlas 缺少已收录礼装：{missing}")
    return {"schemaVersion": _DEFS.SCHEMA_VERSION, "region": "JP", "version": version or {},
            "equips": {str(eid): compile_equip(by_id[eid], nested) for eid in selected}}


def category_overrides(curated, exports):
    """Exact Atlas identity facts, independent of the editor and ID prefixes."""
    selected = {int(row["id"]) for row in curated}
    result = {}
    for equip in exports:
        if equip["id"] not in selected:
            continue
        facts = source_facts(equip)
        if facts["kind"] == "bond":
            result[str(equip["id"])] = {"acquisition_category_jp": "絆礼装", "filter_tag": "羁绊礼装",
                                        **({"bondOwner": equip["bondEquipOwner"]} if equip.get("bondEquipOwner") else {})}
        elif facts["kind"] == "valentine":
            result[str(equip["id"])] = {"acquisition_category_jp": "バレンタイン・チョコレート", "filter_tag": "巧克力"}
    return result


def apply_category_overrides(curated, overrides):
    result = []
    for row in curated:
        facts = overrides.get(str(row["id"]), {})
        result.append({**row, **{key: facts[key] for key in ("acquisition_category_jp", "filter_tag") if key in facts}})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "config/BattleEditor/equip-filter-build")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "agent/battle_editor/data")
    parser.add_argument("--sync-runtime-categories", action="store_true",
                        help="同步精确ID的Atlas类型分类；仅修改运行时两个分类字段，不修改外部CSV/Excel")
    args = parser.parse_args()
    cache = args.cache_dir
    cache.mkdir(parents=True, exist_ok=True)
    sources = []

    def cached(name, url):
        path = cache / name
        if args.refresh or not path.is_file():
            if not args.refresh:
                raise FileNotFoundError(f"缺少离线缓存 {path}，请先维护期运行 --refresh")
            raw = fetch(url)
            json.loads(raw)
            path.write_bytes(raw)
        raw = path.read_bytes()
        return json.loads(raw), {"file": name, "url": url, "sha256": digest(raw), "bytes": len(raw)}

    exports, record = cached("JP_nice_equip.json", API + "/export/JP/nice_equip.json")
    sources.append(record)
    info, record = cached("JP_info.json", API + "/export/JP/info.json")
    sources.append(record)
    curated_path = ROOT / "agent/custom/equip_list.json"
    curated = load(curated_path)["equips"]
    if args.sync_runtime_categories:
        overrides = category_overrides(curated, exports)
        categories = {"schemaVersion": 1, "source": {"url": sources[0]["url"], "sha256": sources[0]["sha256"]},
                      "equips": overrides}
        (ROOT / "agent/custom/equip_categories.json").write_text(
            json.dumps(categories, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        changed = apply_category_overrides(curated, overrides)
        if changed != curated:
            curated = changed
            curated_path.write_text(json.dumps({"equips": curated}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    selected = {int(row["id"]) for row in curated}
    pending = trigger_refs([skill for row in exports if row["id"] in selected for skill in row.get("skills", [])])
    nested = {}
    for _depth in range(4):
        pending -= nested.keys()
        if not pending:
            break
        def one(skill_id):
            data, source = cached(f"skill_{skill_id}.json", f"{API}/nice/JP/skill/{skill_id}")
            return skill_id, data, source
        with ThreadPoolExecutor(max_workers=4) as pool:
            for skill_id, data, source in pool.map(one, sorted(pending)):
                nested[skill_id] = data
                sources.append(source)
        pending = trigger_refs(nested.values())
    version = {"atlas": {key: info[key] for key in ("hash", "timestamp", "dataVer", "dateVer") if key in info},
               "curatedSha256": digest(curated_path.read_bytes()), "niceEquipSha256": sources[0]["sha256"]}
    index = compile_index(curated, exports, nested, version)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = (json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    (args.output_dir / "equip_filters.json").write_bytes(output)
    manifest = {"schemaVersion": 1, "version": version, "curatedCount": len(curated),
                "indexSha256": digest(output), "sources": sorted(sources, key=lambda row: row["file"])}
    (args.output_dir / "equip_filters_sources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"count": len(index["equips"]), "triggerSkills": len(nested), "bytes": len(output), "offline": not args.refresh}))


if __name__ == "__main__":
    main()
