"""Non-mutating checks for Chaldea's JSON contract and the current MaaFGO executor.

The reference is chaldea-center/chaldea e656fa4b3e23228805efdec851358e0ab319aeeb,
lib/models/userdata/battle.dart and its generated battle.g.dart. Optional fields
remain optional; this module never materializes Chaldea's simulation defaults.
"""
from __future__ import annotations

from typing import Any

from .special_choices import match_special_skill
try:  # Both packaged ``battle_editor`` and repository ``agent.battle_editor``.
    from ..battle.core.advanced_strategy import default_advanced_strategy, validate_advanced_strategy
except ImportError:
    from battle.core.advanced_strategy import default_advanced_strategy, validate_advanced_strategy

ORDER_CHANGE_IDS = {20, 210, 440}
MAX_VALIDATION_ISSUES = 200
CARD_TYPES = {1: "arts", 2: "buster", 3: "quick", "1": "arts", "2": "buster", "3": "quick", "arts": "arts", "buster": "buster", "quick": "quick"}
DEFAULTS = {
    "newBattle": {"team": {"name": "未命名方案", "onFieldSvts": [None, None, None], "backupSvts": [None, None, None], "mysticCode": {"mysticCodeId": 210, "level": 10}}, "actions": [], "maaAdvancedStrategy": default_advanced_strategy()},
    "servant": {"supportType": "none", "skillLvs": [10, 10, 10], "appendLvs": [0, 0, 0, 0, 0]}, "equip": {}, "mysticCode": {},
    "description": {"lv": "未指定，不新增等级要求", "tdLv": "未指定，不新增宝具要求", "skillLvs": "空槽新选从者预设 10/10/10；导入未指定时保持缺失，不新增技能要求", "appendLvs": "空槽新选从者预设 0/0/0/0/0，0 表示不要求；导入保留原字段与数组长度", "taskSkillDefaults": "任务技能预设只在新选空槽或显式补齐空项时使用；更换已有从者及切换助战类型不自动补值，预设不是游戏内实际等级", "mysticCode.level": "未指定，仅记录属性", "playerTarget": "未指定，省略该字段", "emptyAttack": "完全空的回合保留一条 attack（attacks: []）：不释放技能或宝具，仅按通用规则选普通卡；同回合已安排的技能仍执行。actions 为空表示未设置回合，使用通用自动战斗", "autoAttackCard": "Maa 扩展 isAuto: true，按当前位置使用默认普通卡策略补齐；不属于上游 Chaldea 标准，不能混入具体卡片字段"},
}


def card_color(value: Any) -> str | None:
    if type(value) not in (str, int):
        return None
    return CARD_TYPES.get(value)


def _unspecified_friend(member: Any) -> bool:
    if not isinstance(member, dict) or member.get("supportType") != "friend":
        return False
    sid = member.get("svtId")
    return sid is None or type(sid) is int and sid == 0


def _identified_member(member: Any) -> bool:
    return (isinstance(member, dict) and type(member.get("svtId")) is int
            and member["svtId"] > 0)


def _has_support_equip_filter(member: dict) -> bool:
    equip1 = member.get("equip1")
    first_id = equip1.get("id") if isinstance(equip1, dict) else None
    if first_id is None:
        first_id = member.get("ceId")
    if type(first_id) is int and first_id > 0:
        return True
    if member.get("grandSvt") is True:
        third = member.get("equip3")
        if isinstance(third, dict) and type(third.get("id")) is int and third["id"] > 0:
            return True
        board = member.get("classBoardData")
        return isinstance(board, dict) and type(board.get("grandBondEquipSkillChange")) is bool
    return False


def validate_battle(data: Any, catalog: dict | None = None) -> dict:
    """Return an actionable report, preserving every field and JSON value supplied."""
    issues: list[dict] = []
    summary = {"turns": 0, "actions": 0, "skills": 0, "masterSkills": 0, "orderChanges": 0, "retirements": 0, "noblePhantasms": 0, "cards": 0, "servants": 0}

    def issue(severity, path, code, message, **location):
        if len(issues) < MAX_VALIDATION_ISSUES:
            issues.append({"severity": severity, "path": path, "code": code, "message": message, **location})
            return
        if len(issues) == MAX_VALIDATION_ISSUES:
            issues.append({"severity": severity, "path": "$", "code": "issues_truncated", "message": ""})
        if severity == "error":
            issues[-1]["severity"] = "error"
        summary["omittedIssues"] = summary.get("omittedIssues", 0) + 1

    def error(path, code, message, **location):
        issue("error", path, code, message, **location)

    def warning(path, code, message, **location):
        issue("warning", path, code, message, **location)

    def unknown(node, known, path, **location):
        for key in node:
            if key not in known:
                warning(f"{path}.{key}" if path else str(key), "preserved_field", "未识别的字段将原样保留，当前执行器可能不使用它", **location)

    def integer(node, key, path, minimum=0, maximum=None, nullable=True, **location):
        if key not in node or (node[key] is None and nullable):
            return
        value = node[key]
        if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
            bound = f"{minimum}–{maximum}" if maximum is not None else f"≥ {minimum}"
            error(f"{path}.{key}", "invalid_integer", f"{key} 必须是 {bound} 的整数", **location)

    def boolean(node, key, path, **location):
        if node.get(key) is not None and type(node[key]) is not bool:
            error(f"{path}.{key}", "invalid_boolean", f"{key} 必须是布尔值", **location)

    def result():
        if summary.get("omittedIssues"):
            suffix = "其中包含阻断问题，请分批修正后重新检查。" if issues[-1]["severity"] == "error" else "方案数据仍完整保留，提醒不阻止保存。"
            issues[-1]["message"] = f"问题较多，仅展开前 {MAX_VALIDATION_ISSUES} 项；另有 {summary['omittedIssues']} 项提示未展开。{suffix}"
        return {"valid": not any(i["severity"] == "error" for i in issues), "issues": issues, "summary": summary}

    if not isinstance(data, dict):
        error("$", "invalid_root", "需要 Chaldea BattleShareData JSON 对象")
        return result()
    known_root = {"minBuild", "appBuild", "quest", "options", "team", "delegate", "actions", "isCritTeam", "maaAdvancedStrategy"}
    if "maaAdvancedStrategy" in data:
        for item in validate_advanced_strategy(data["maaAdvancedStrategy"]):
            issue(item["severity"], item["path"], item["code"], item["message"])
    if data.get("_source") == "bbc":
        known_root.update({"_source", "_bbcSource", "_bbcWaveTurns", "_conversionWarnings"})
        conversion_warnings = data.get("_conversionWarnings")
        if isinstance(conversion_warnings, list):
            for index, message in enumerate(conversion_warnings):
                if isinstance(message, str) and message.strip():
                    warning(f"_conversionWarnings[{index}]", "bbc_conversion_warning", message)
    unknown(data, known_root, "")
    for key in ("minBuild", "appBuild"):
        integer(data, key, "$", 0)
    boolean(data, "isCritTeam", "$")
    if data.get("quest") is not None:
        quest = data["quest"]
        if not isinstance(quest, dict):
            error("quest", "invalid_object", "quest 必须是对象或 null")
        else:
            for key in ("id", "phase"):
                if key not in quest:
                    error(f"quest.{key}", "required_field", "关卡信息需要 id 和 phase")
                integer(quest, key, "quest", 1, nullable=False)
            if quest.get("enemyHash") is not None and not isinstance(quest["enemyHash"], str):
                error("quest.enemyHash", "invalid_string", "enemyHash 必须是字符串或 null")
            if quest.get("region") is not None and quest["region"] not in ("JP", "CN", "NA", "TW", "KR", "jp", "cn", "na", "tw", "kr"):
                error("quest.region", "invalid_region", "未知的 Chaldea 区服")
            unknown(quest, {"id", "phase", "enemyHash", "region"}, "quest")
    if data.get("options") is not None:
        options = data["options"]
        if not isinstance(options, dict):
            error("options", "invalid_object", "顶层 options 必须是对象或 null")
        else:
            for key in ("mightyChain", "disableEvent", "simulateAi"):
                boolean(options, key, "options")
            unknown(options, {"mightyChain", "disableEvent", "pointBuffs", "simulateAi", "enemyRateUp"}, "options")
            if options:
                warning("options", "simulation_only", "顶层 options 是 Chaldea 模拟参数，保存保留；它们不会改变 MaaFGO 选卡策略")

    team = data.get("team")
    no_formation = team is None and ("maaAdvancedStrategy" in data or isinstance(data.get("actions"), list))
    if no_formation:
        # A private projection allows strategy-only documents to pass the same
        # validation flow without inventing or writing a recorded formation.
        team = {"onFieldSvts": [None, None, None], "backupSvts": [None, None, None]}
        warning("team", "no_formation", "方案未指定编队，原生战斗将使用当前场上的阵容；自动编队不适用。")
    if not isinstance(team, dict):
        error("team", "required_team", "缺少 Chaldea team 对象")
        return result()
    unknown(team, {"name", "onFieldSvts", "backupSvts", "mysticCode", "svts"}, "team")
    if team.get("name") is not None and not isinstance(team["name"], str):
        error("team.name", "invalid_string", "队名必须是字符串或 null")
    if team.get("svts") is not None and not isinstance(team["svts"], list):
        error("team.svts", "invalid_array", "svts 必须是数组或 null")
    elif team.get("svts"):
        error("team.svts", "unsupported_formation", "当前 MaaFGO 使用 onFieldSvts / backupSvts；新版 svts 会覆盖官方阵容解释，请保留草稿并先明确转换")
    catalog_rows = (catalog or {}).get("servants", [])
    by_id = {row.get("id", row.get("svtId")): row for row in catalog_rows if isinstance(row, dict)} if isinstance(catalog_rows, list) else {}
    for row in catalog_rows if isinstance(catalog_rows, list) else []:
        if not isinstance(row, dict):
            continue
        for alias in row.get("aliases", []):
            if type(alias) is int:
                by_id.setdefault(alias, row)
    members = []
    owned_servants: dict[int, int] = {}
    supports = 0
    servant_keys = {"svtId", "limitCount", "transformVal", "skillLvs", "skillIds", "appendLvs", "tdId", "tdLv", "lv", "atkFou", "hpFou", "fixedAtk", "fixedHp", "ceId", "ceLimitBreak", "ceLv", "equip1", "equip2", "equip3", "supportType", "cardStrengthens", "commandCodeIds", "disabledExtraSkills", "allowedExtraSkills", "customPassives", "customPassiveLvs", "grandSvt", "classBoardData"}
    for group in ("onFieldSvts", "backupSvts"):
        raw_slots = team.get(group)
        if not isinstance(raw_slots, list) or len(raw_slots) != 3:
            error(f"team.{group}", "invalid_slots", "首发和候补都必须保留三槽数组，空位使用 null")
        slots = raw_slots if isinstance(raw_slots, list) else []
        for index in range(3):
            slot = index + (0 if group == "onFieldSvts" else 3)
            member = slots[index] if index < len(slots) else None
            members.append(member if isinstance(member, dict) else None)
            path = f"team.{group}[{index}]"
            loc = {"slot": slot}
            if member is None:
                continue
            if not isinstance(member, dict):
                error(path, "invalid_servant", "从者槽位必须是对象或 null", **loc)
                continue
            summary["servants"] += 1
            unspecified_friend = _unspecified_friend(member)
            if not unspecified_friend:
                if member.get("svtId") is None:
                    error(path + ".svtId", "missing_servant", "请选择从者或将空位写为 null；好友助战可只配置礼装", **loc)
                integer(member, "svtId", path, 1, **loc)
            for key, lo, hi in (("lv", 1, 120), ("tdLv", 1, 5), ("tdId", 0, None), ("limitCount", 0, None), ("transformVal", 0, None), ("atkFou", 0, None), ("hpFou", 0, None), ("fixedAtk", 0, None), ("fixedHp", 0, None), ("ceId", 0, None), ("ceLv", 0, 100)):
                integer(member, key, path, lo, hi, **loc)
            for key in ("ceLimitBreak", "grandSvt"):
                boolean(member, key, path, **loc)
            for key, lengths, lo, hi, nullable_items in (("skillLvs", (3,), 0, 10, False), ("appendLvs", (3, 5), 0, 10, False), ("skillIds", (3,), 0, None, True), ("cardStrengthens", (5,), 0, None, False), ("commandCodeIds", (5,), 0, None, True)):
                values = member.get(key)
                if values is None:
                    continue
                if not isinstance(values, list):
                    error(path + "." + key, "invalid_array", f"{key} 必须是数组或 null", **loc)
                    continue
                if values and len(values) not in lengths:
                    warning(path + "." + key, "legacy_array_length", f"保留 {key} 的原数组长度；Chaldea 会补齐或截取，当前 MaaFGO 仅使用对应的技能/卡槽", **loc)
                for vi, value in enumerate(values):
                    if value is None and nullable_items:
                        continue
                    if type(value) is not int or value < lo or (hi is not None and value > hi):
                        error(f"{path}.{key}[{vi}]", "invalid_level", "该值必须是范围内的整数", **loc)
            support_type = member.get("supportType", "none")
            if support_type not in (None, "none", "friend", "npc", "fixed"):
                error(path + ".supportType", "invalid_support", "助战类型应为 none / friend / npc", **loc)
            if support_type in ("friend", "npc", "fixed"):
                supports += 1
            if support_type == "fixed":
                warning(path + ".supportType", "legacy_support", "保留历史 fixed 助战类型；Chaldea 官方类型为 none / friend / npc", **loc)
            if unspecified_friend:
                identity_fields = ("lv", "tdLv", "tdId", "skillLvs", "appendLvs", "skillIds",
                                   "limitCount", "transformVal", "atkFou", "hpFou", "fixedAtk", "fixedHp",
                                   "cardStrengthens", "commandCodeIds", "disabledExtraSkills",
                                   "allowedExtraSkills", "customPassives", "customPassiveLvs")
                if any(any(member[key]) if isinstance(member.get(key), list) else bool(member.get(key))
                       for key in identity_fields):
                    warning(path, "unspecified_support_attributes",
                            "此助战未指定从者，等级、技能和宝具等从者条件不参与筛选；仅按礼装要求选择。请核对保留的属性。", **loc)
                if not _has_support_equip_filter(member):
                    warning(path, "unrestricted_support", "此好友助战未指定从者或礼装筛选，可能选择任意助战；请确认是否符合预期。", **loc)
            sid = member.get("svtId")
            if support_type in (None, "none") and type(sid) is int and sid > 0:
                meta = by_id.get(sid, {})
                identity = meta.get("id", meta.get("svtId", sid))
                if type(identity) is not int or identity <= 0:
                    identity = sid
                if identity in owned_servants:
                    name = meta.get("name") or meta.get("nameJp")
                    if not isinstance(name, str):
                        name = f"从者 #{sid}"
                    first_slot = owned_servants[identity]
                    error(path + ".svtId", "duplicate_servant", f"槽位 {slot + 1} 的「{name}」与槽位 {first_slot + 1} 为同一名自有从者；请更换从者，或将其中一名正确标记为助战（队伍最多一名助战）", **loc)
                else:
                    owned_servants[identity] = slot
            for key in ("equip1", "equip2", "equip3"):
                equip = member.get(key)
                if equip is None:
                    continue
                ep = path + "." + key
                if not isinstance(equip, dict):
                    error(ep, "invalid_equip", "礼装必须是对象或 null", **loc)
                    continue
                integer(equip, "id", ep, 0, **loc)
                integer(equip, "lv", ep, 0, 100, **loc)
                boolean(equip, "limitBreak", ep, **loc)
                unknown(equip, {"id", "lv", "limitBreak", "candidates"}, ep, **loc)
                candidates = equip.get("candidates")
                if candidates is None:
                    continue
                cp = ep + ".candidates"
                if not isinstance(candidates, list):
                    error(cp, "invalid_equip_candidates", "额外候选礼装必须是对象数组或 null", **loc)
                    continue
                if len(candidates) > 2:
                    error(cp, "too_many_equip_candidates", "每格礼装最多保留首选与两个额外候选；请减少候选后保存，数据不会被截断", **loc)
                    continue
                if not candidates:
                    continue
                if member.get("supportType") not in ("friend", "fixed", "npc"):
                    error(cp, "equip_candidates_support_only", "多候选礼装仅用于助战筛选；自有从者自动编队仍需指定单张礼装", **loc)
                elif key not in (("equip1", "equip3") if member.get("grandSvt") is True else ("equip1",)):
                    error(cp, "unsupported_equip_candidates_slot", "普通助战仅主礼装支持候选；冠位助战仅普通与追加礼装支持卡面候选，羁绊礼装使用效果筛选", **loc)
                else:
                    warning(cp, "equip_candidates_extension", "MaaFGO 按任一候选匹配助战礼装；外部 Chaldea 标准仅识别首选，候选字段会完整保存", **loc)
                primary_id = equip.get("id")
                if primary_id is None and key == "equip1":
                    primary_id = member.get("ceId")
                if type(primary_id) is not int or not 0 < primary_id <= 9007199254740991:
                    error(ep + ".id", "missing_equip_candidate_primary", "请先设置有效的首选礼装，再添加额外候选", **loc)
                seen_ids = {primary_id} if type(primary_id) is int and primary_id > 0 else set()
                for ci, candidate in enumerate(candidates):
                    item_path = f"{cp}[{ci}]"
                    if not isinstance(candidate, dict):
                        error(item_path, "invalid_equip_candidate", "候选礼装必须是包含 ID 的对象", **loc)
                        continue
                    if "id" not in candidate:
                        error(item_path + ".id", "missing_equip_candidate_id", "候选礼装缺少 ID", **loc)
                    integer(candidate, "id", item_path, 1, 9007199254740991, nullable=False, **loc)
                    integer(candidate, "lv", item_path, 0, 100, **loc)
                    boolean(candidate, "limitBreak", item_path, **loc)
                    if "candidates" in candidate:
                        error(item_path + ".candidates", "nested_equip_candidates", "候选礼装不能再嵌套候选列表", **loc)
                    unknown(candidate, {"id", "lv", "limitBreak", "candidates"}, item_path, **loc)
                    candidate_id = candidate.get("id")
                    if type(candidate_id) is int and candidate_id > 0:
                        if candidate_id in seen_ids:
                            error(item_path + ".id", "duplicate_equip_candidate", "同一格的候选礼装 ID 不能重复；同一礼装的任意满破状态请使用未指定", **loc)
                        seen_ids.add(candidate_id)
            if member.get("grandSvt"):
                warning(path, "grand_equip_boundary", "冠位羁绊礼装按效果筛选，无需指定卡面；当前自动编队仍跳过冠位从者的礼装修改", **loc)
            if member.get("classBoardData") is not None and not isinstance(member["classBoardData"], dict):
                error(path + ".classBoardData", "invalid_object", "classBoardData 必须是对象或 null", **loc)
            unknown(member, servant_keys, path, **loc)
    if supports > 1:
        error("team", "multiple_supports", "当前自动编队只支持一个助战槽位")
    if supports == 0:
        warning("team", "missing_support", "队伍中未设置助战，请核对成员类型；无助战关卡可忽略此提醒，仍允许保存方案")
    if not any(members[:3]) and not (no_formation or isinstance(data.get("maaAdvancedStrategy"), dict) and not any(members)):
        error("team.onFieldSvts", "empty_frontline", "至少选择一位前排从者")

    mystic = team.get("mysticCode")
    mystic_id = None
    if mystic is not None:
        if not isinstance(mystic, dict):
            error("team.mysticCode", "invalid_object", "御主礼装必须是对象或 null")
        else:
            integer(mystic, "mysticCodeId", "team.mysticCode", 0)
            integer(mystic, "level", "team.mysticCode", 0, 10)
            mystic_id = mystic.get("mysticCodeId")
            unknown(mystic, {"mysticCodeId", "level"}, "team.mysticCode")

    delegate = data.get("delegate")
    swaps = []
    if delegate is not None:
        if not isinstance(delegate, dict):
            error("delegate", "invalid_object", "delegate 必须是对象或 null")
        else:
            known = {"actWeightSelections", "skillActSelectSelections", "tdTypeChanges", "ptRandomIndexes", "canActivateDecisions", "damageSelections", "replaceMemberIndexes"}
            unknown(delegate, known, "delegate")
            for key in ("tdTypeChanges", "skillActSelectSelections"):
                values = delegate.get(key)
                if values is None:
                    continue
                if not isinstance(values, list):
                    error("delegate." + key, "invalid_array", "特殊选项必须是数组或 null")
                    continue
                allowed = (1, 2, 3) if key == "tdTypeChanges" else (0, 1, 2)
                for index, value in enumerate(values):
                    if key == "tdTypeChanges" and isinstance(value, str):
                        value = {"arts": 1, "buster": 2, "quick": 3}.get(value)
                    if type(value) is not int or value not in allowed:
                        error(f"delegate.{key}[{index}]", "invalid_special_choice",
                              f"无法对应游戏选项；支持 {allowed}，不支持模拟器的空选择")
            for key in known - {"replaceMemberIndexes", "tdTypeChanges", "skillActSelectSelections"}:
                if delegate.get(key) is not None and not isinstance(delegate[key], list):
                    error("delegate." + key, "invalid_array", "特殊选项必须是数组或 null")
                elif delegate.get(key):
                    # Chaldea uses -1 to skip random ActSet effects in simulation;
                    # this is not a game dialog choice or an execution instruction.
                    if key == "actWeightSelections" and all(type(value) is int and value == -1 for value in delegate[key]):
                        warning("delegate." + key, "simulation_random_skip", "Chaldea 中跳过了随机效果的模拟；游戏内仍按实际随机结果生效，请核对方案是否依赖这些效果")
                    else:
                        error("delegate." + key, "unsupported_delegate", "方案指定了当前不支持的模拟选择，无法直接执行。请在 Chaldea 中取消对应的模拟选择，重新导出并导入；当前内容仍可保存草稿或下载保真副本")
            raw_swaps = delegate.get("replaceMemberIndexes")
            if raw_swaps is not None and not isinstance(raw_swaps, list):
                error("delegate.replaceMemberIndexes", "invalid_array", "换人配置必须是数组")
            elif isinstance(raw_swaps, list):
                for si, swap in enumerate(raw_swaps):
                    path = f"delegate.replaceMemberIndexes[{si}]"
                    if not isinstance(swap, list) or len(swap) != 2 or any(type(v) is not int for v in swap) or not 0 <= swap[0] <= 2 or not 0 <= swap[1] <= 5:
                        error(path, "invalid_swap", "换人为 [前排0–2, 候补0–2]；兼容历史绝对候补3–5")
                        swaps.append(None)
                    else:
                        swaps.append((swap[0], swap[1] + 3 if swap[1] <= 2 else swap[1]))
                        if swap[1] >= 3:
                            warning(path, "legacy_swap", "保留历史绝对候补索引；新建方案使用相对候补 0–2")

    mystic_rows = (catalog or {}).get("mysticCodes", [])
    mystic_meta = next((row for row in mystic_rows if row.get("id") == mystic_id), {}) if isinstance(mystic_rows, list) else {}

    def requires_target(member, skill_index, master=False):
        if type(skill_index) is not int or not 0 <= skill_index <= 2:
            return False
        if master:
            candidates = [row for row in mystic_meta.get("skills", []) if row.get("num") == skill_index + 1]
        else:
            sid = member.get("svtId") if isinstance(member, dict) else None
            meta = by_id.get(sid, {}) if type(sid) is int else {}
            explicit = member.get("skillIds") if isinstance(member, dict) else None
            chosen_id = explicit[skill_index] if isinstance(explicit, list) and len(explicit) > skill_index else None
            variants = meta.get("skillVariants") or meta.get("skills", [])
            if chosen_id:
                candidates = [row for row in variants if row.get("id") == chosen_id]
            else:
                # Requiring a target must be valid for all possible skill strengthenings.
                candidates = [row for row in variants if row.get("num") == skill_index + 1]
        return bool(candidates) and all(row.get("target") == "ally" for row in candidates)

    actions = data.get("actions", [])
    if actions is None:  # accepted by the official optional-list deserializer
        actions = []
    if not isinstance(actions, list):
        error("actions", "invalid_actions", "actions 必须是数组")
        return result()
    if len(actions) > 5000:
        error("actions", "too_many_actions", "动作超过 5000 条，请拆分方案")
        return result()
    summary["actions"] = len(actions)
    swap_pointer = 0
    initial_members = tuple(members)
    member_order = list(range(6))
    retired = set()
    special_pointers = {"tdTypeChanges": 0, "skillActSelectSelections": 0}
    ambiguous_special_fields = set()

    def canonical_id(sid):
        meta = by_id.get(sid, {}) if type(sid) is int else {}
        return meta.get("id", sid)

    def check_actor(raw, slot, path, loc, required=False):
        if raw is None and not required:
            return True
        if (not isinstance(raw, dict) or type(raw.get("initialSlot")) is not int
                or not 0 <= raw["initialSlot"] <= 5 or type(raw.get("svtId")) is not int
                or raw["svtId"] <= 0):
            error(path + ".actor", "invalid_actor_binding", "从者实例需要初始槽位 initialSlot 0–5 和正整数 svtId", **loc)
            return False
        unknown(raw, {"initialSlot", "svtId"}, path + ".actor", **loc)
        initial = raw["initialSlot"]
        if initial in retired:
            error(path + ".actor", "retired_actor", "该从者已经退场，不能继续使用其技能或出卡；请删除原动作或为递补者重新添加。", **loc)
            return False
        if member_order[slot] != initial:
            error(path + ".actor", "actor_position_mismatch", "动作仍绑定原从者，当前槽位已由其他成员占用；请为当前从者重新添加动作。", **loc)
            return False
        member = initial_members[initial]
        if not _identified_member(member) or canonical_id(raw["svtId"]) != canonical_id(member["svtId"]):
            error(path + ".actor", "actor_identity_mismatch", "动作绑定身份与初始编队不一致，请重新添加该动作。", **loc)
            return False
        return True

    def check_special_choice(member, skill, path, loc):
        sid = member.get("svtId") if isinstance(member, dict) else None
        group, applicable = match_special_skill(member, skill, by_id.get(sid, {}))
        if group is None or not isinstance(delegate, dict):
            return
        field = group["field"]
        values = delegate.get(field)
        # Missing/null/empty queues retain the native menu's existing default.
        if not isinstance(values, list) or not values:
            return
        if applicable is not True:
            ambiguous_special_fields.add(field)
            error(path + ".skill", "special_choice_variant_unresolved",
                  "此技能的强化版本没有已确认的选项菜单，但方案配置了同类特殊选择队列；"
                  "当前执行器无法安全跳过该动作，请选择支持菜单的强化技能，或移除该类队列。", **loc)
            return
        if field in ambiguous_special_fields:
            return
        index = special_pointers[field]
        special_pointers[field] += 1
        if index >= len(values):
            error("delegate." + field, "special_choice_exhausted",
                  f"特殊选择队列已耗尽：此动作为 {field} 的第 {index + 1} 次选择，请补齐选项。", **loc)
            return
        value = values[index]
        if field == "tdTypeChanges" and isinstance(value, str):
            value = {"arts": 1, "buster": 2, "quick": 3}.get(value)
        if type(value) is int and value not in [option["value"] for option in group["options"]]:
            error(f"delegate.{field}[{index}]", "special_choice_out_of_range",
                  "该选择值不在当前技能的菜单中，请重新选择该动作的技能效果。", **loc)

    trailing_skill = False
    trailing_skill_start = None
    no_np_turns: list[tuple[int, int, str]] = []
    for ai, action in enumerate(actions):
        path, loc = f"actions[{ai}]", {"actionIndex": ai}
        if not isinstance(action, dict):
            error(path, "invalid_action", "动作必须是对象", **loc)
            continue
        unknown(action, {"type", "svt", "skill", "attacks", "options", "actor"}, path, **loc)
        kind = action.get("type")
        options = action.get("options")
        if options is not None and not isinstance(options, dict):
            error(path + ".options", "invalid_object", "动作 options 必须是对象或 null", **loc)
            options = {}
        options = options or {}
        for key in ("playerTarget", "enemyTarget"):
            integer(options, key, path + ".options", 0, 2, **loc)
        for key in ("random", "threshold"):
            if options.get(key) is not None and type(options[key]) not in (int, float):
                error(path + ".options." + key, "invalid_number", f"{key} 必须是数值或 null", **loc)
        unknown(options, {"playerTarget", "enemyTarget", "random", "threshold"}, path + ".options", **loc)
        if kind == "skill":
            if not trailing_skill:
                trailing_skill_start = ai
            trailing_skill = True
            skill, svt = action.get("skill"), action.get("svt")
            if type(skill) is not int or not 0 <= skill <= 2:
                error(path + ".skill", "invalid_skill", "技能索引必须是 0–2", **loc)
            if svt is not None and (type(svt) is not int or not 0 <= svt <= 2):
                error(path + ".svt", "invalid_front_slot", "技能执行者必须是当前前排 0–2，御主技能省略 svt", **loc)
            elif svt is not None:
                summary["skills"] += 1
                check_actor(action.get("actor"), svt, path, loc)
                if not members[svt]:
                    error(path + ".svt", "empty_actor", "该动作时点的前排槽位为空", **loc)
                elif not _identified_member(members[svt]):
                    error(path + ".svt", "unspecified_support_actor", "该动作时点的助战未指定从者，不能安排从者技能；请先在编队中选择从者。", **loc)
                elif requires_target(members[svt], skill) and options.get("playerTarget") is None:
                    error(path + ".options.playerTarget", "required_ally_target", "该技能的已知元数据要求选择我方目标", **loc)
                check_special_choice(members[svt], skill, path, loc)
            else:
                summary["masterSkills"] += 1
                if not mystic_id:
                    error(path + ".svt", "missing_mystic_code", "御主技能需要明确选择御主礼装", **loc)
                if requires_target(None, skill, master=True) and options.get("playerTarget") is None:
                    error(path + ".options.playerTarget", "required_ally_target", "该御主技能的已知元数据要求选择我方目标", **loc)
                if skill == 2 and type(mystic_id) is int and mystic_id in ORDER_CHANGE_IDS:
                    if swap_pointer >= len(swaps):
                        error(path, "missing_swap", "换人服技能 3 缺少对应的换人配置", **loc)
                    else:
                        swap = swaps[swap_pointer]
                        swap_pointer += 1
                        if swap:
                            front, back = swap
                            if member_order[back] in retired:
                                error(path, "retired_swap", "已退场的从者不能通过换人重新入场", **loc)
                                continue
                            if not members[front] or not members[back]:
                                error(path, "empty_swap", "换人双方必须在此动作时点都有从者", **loc)
                            members[front], members[back] = members[back], members[front]
                            member_order[front], member_order[back] = member_order[back], member_order[front]
                            summary["orderChanges"] += 1
            target = options.get("playerTarget")
            if type(target) is int and 0 <= target <= 2 and not members[target]:
                error(path + ".options.playerTarget", "empty_target", "该动作时点的我方目标槽位为空", **loc)
        elif kind == "retire":
            svt = action.get("svt")
            if type(svt) is not int or not 0 <= svt <= 2:
                error(path + ".svt", "invalid_front_slot", "退场者必须是当前前排 0–2", **loc)
                continue
            if not check_actor(action.get("actor"), svt, path, loc, required=True):
                continue
            if not _identified_member(members[svt]):
                error(path, "empty_retirement_actor", "退场标记需要明确的当前前排从者", **loc)
                continue
            incoming = next((i for i in range(3, 6) if members[i] and member_order[i] not in retired), None)
            if incoming is None:
                error(path, "no_retirement_replacement", "退场后没有存活候补可以递补，请先配置候补从者。", **loc)
                continue
            if not _identified_member(members[incoming]):
                error(path, "unknown_retirement_replacement", "递补者未指定从者，无法确认自然退场完成，请先指定候补身份。", **loc)
            elif canonical_id(members[incoming]["svtId"]) == canonical_id(members[svt]["svtId"]):
                error(path, "ambiguous_retirement_replacement", "退场者与递补者为同名从者，当前识别无法安全确认递补，暂不支持此退场标记。", **loc)
            outgoing, outgoing_member = member_order[svt], members[svt]
            member_order[svt], members[svt] = member_order.pop(incoming), members.pop(incoming)
            member_order.append(outgoing)
            members.append(outgoing_member)
            retired.add(outgoing)
            summary["retirements"] += 1
            if not trailing_skill:
                trailing_skill_start = ai
            trailing_skill = True
        elif kind == "attack":
            summary["turns"] += 1
            trailing_skill = False
            trailing_skill_start = None
            attacks = action.get("attacks")
            if attacks is None:
                attacks = []
            if not isinstance(attacks, list):
                error(path + ".attacks", "invalid_cards", "attacks 必须是数组或 null", **loc)
                continue
            if summary["turns"] <= 3 and not any(
                isinstance(card, dict) and card.get("isTD") is True
                and card.get("isAuto") is not True for card in attacks
            ):
                no_np_turns.append((summary["turns"], ai, path + ".attacks"))
            if len(attacks) > 3:
                error(path + ".attacks", "too_many_cards", "当前执行器每回合最多支持三张出卡意图", **loc)
            seen_np = set()
            seen_cards = set()
            for ci, card in enumerate(attacks):
                cp = f"{path}.attacks[{ci}]"
                if not isinstance(card, dict):
                    error(cp, "invalid_card", "出卡意图必须是对象", **loc)
                    continue
                unknown(card, {"svt", "card", "isTD", "critical", "cardType", "isAuto", "actor"}, cp, **loc)
                if "isAuto" in card and type(card["isAuto"]) is not bool:
                    error(cp + ".isAuto", "invalid_boolean", "isAuto 必须是布尔值", **loc)
                    continue
                if card.get("isAuto") is True:
                    if any(key in card for key in ("svt", "card", "isTD", "cardType", "critical")):
                        error(cp, "conflicting_auto_card", "默认普通卡占位不能同时指定 svt、card、isTD、cardType 或 critical", **loc)
                    else:
                        summary["cards"] += 1
                    continue
                svt = card.get("svt")
                boolean(card, "isTD", cp, **loc)
                boolean(card, "critical", cp, **loc)
                if type(svt) is not int or not 0 <= svt <= 2:
                    error(cp + ".svt", "invalid_front_slot", "出卡必须指向当前前排 0–2", **loc)
                elif not members[svt]:
                    error(cp + ".svt", "empty_actor", "出卡时的前排槽位为空", **loc)
                elif not _identified_member(members[svt]):
                    error(cp + ".svt", "unspecified_support_actor", "出卡时的助战未指定从者，不能安排宝具或指定普通卡；请先在编队中选择从者。", **loc)
                if type(svt) is int and 0 <= svt <= 2:
                    check_actor(card.get("actor"), svt, cp, loc)
                if card.get("isTD") is True:
                    summary["noblePhantasms"] += 1
                    if type(svt) is int:
                        if svt in seen_np:
                            error(cp, "duplicate_np", "同一回合不能重复指定同一槽位的宝具", **loc)
                        seen_np.add(svt)
                    if card.get("card") is not None and (type(card["card"]) is not int or card["card"] != -1):
                        error(cp + ".card", "invalid_np_card", "标准宝具 card 为 -1", **loc)
                    elif card.get("card") is None:
                        warning(cp + ".card", "legacy_np_card", "保留历史缺省宝具索引；新建宝具应写 card: -1", **loc)
                else:
                    summary["cards"] += 1
                    index = card.get("card")
                    if type(index) is not int or not 0 <= index <= 4:
                        error(cp + ".card", "invalid_deck_index", "面卡 card 必须是从者五张牌组的索引 0–4，不能用屏幕位置代替", **loc)
                    elif type(svt) is int and 0 <= svt <= 2:
                        card_key = (svt, index)
                        if card_key in seen_cards:
                            error(cp + ".card", "duplicate_deck_card", "同一攻击组不能重复指定同一从者牌组中的同一张面卡", **loc)
                        seen_cards.add(card_key)
                    color = card_color(card.get("cardType"))
                    if color is None:
                        error(cp + ".cardType", "unsupported_card_color", "当前执行器只支持 Arts=1、Buster=2、Quick=3 及历史对应字符串", **loc)
                    if type(svt) is int and 0 <= svt <= 2 and members[svt]:
                        member_id = members[svt].get("svtId")
                        meta = by_id.get(member_id, {}) if type(member_id) is int else {}
                        deck = meta.get("cards")
                        if isinstance(deck, list) and len(deck) == 5 and type(index) is int and 0 <= index <= 4 and color and card_color(deck[index]) != color:
                            warning(cp + ".card", "card_deck_mismatch", "记录的面卡颜色与基础牌组不同，可能由技能改色；请核对当回合游戏状态。当前执行器按指定角色和目标颜色选卡", **loc)
                        elif not isinstance(deck, list) or len(deck) != 5:
                            warning(cp + ".card", "unknown_deck", "本地缺少该从者完整牌组，保留导入索引但无法核验色卡对应；新建面卡需补齐目录数据", **loc)
                if card.get("critical"):
                    warning(cp + ".critical", "critical_simulation_only", "暴击为模拟记录，当前实战无法保证暴击", **loc)
        else:
            error(path + ".type", "unsupported_action", f"当前执行器不支持动作 {kind!r}；保留原值，可存草稿或下载保真副本", **loc)
    if swap_pointer < len(swaps):
        error("delegate.replaceMemberIndexes", "unused_swap", "存在没有对应换人服技能 3 的换人配置，请检查御主礼装和动作")
    if isinstance(delegate, dict):
        for field, consumed in special_pointers.items():
            values = delegate.get(field)
            if isinstance(values, list) and len(values) > consumed and field not in ambiguous_special_fields:
                warning("delegate." + field, "unused_special_choices",
                        f"还有 {len(values) - consumed} 个特殊选择未能对应到已知技能动作；原值保留，请核对强化版本与动作顺序。")
    if trailing_skill:
        warning("actions", "trailing_skills", "末尾技能没有攻击边界，当前转换器将补一个回合并使用默认出卡")
        summary["turns"] += 1
        if summary["turns"] <= 3 and trailing_skill_start is not None:
            no_np_turns.append((summary["turns"], trailing_skill_start, f"actions[{trailing_skill_start}]"))
    if summary["turns"] >= 2:
        for turn_number, action_index, path in no_np_turns:
            warning(path, "early_turn_no_np",
                    f"第{turn_number}回合未安排宝具，将按配置或默认策略出普通卡。请确认是否符合预期；此提醒不影响保存。",
                    actionIndex=action_index)
    if not actions:
        warning("actions", "default_battle", "方案没有固定回合，实战使用当前默认战斗策略")
    return result()


# A short alias is useful to scripts without creating a second validation policy.
validate = validate_battle
