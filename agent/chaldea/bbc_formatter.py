"""
Chaldea BattleShareData → BBchannel settings JSON 转换逻辑

纯数据转换，无 IO 无网络调用。
"""

import logging
from typing import Optional, Dict, List, Tuple, Any, Union

from .servant_types import (
    SupportType, ServantInfo, MysticCodeInfo, TeamFormation,
    ORDER_CHANGE_MYSTIC_CODE_IDS,
)
from .game_data import get_servant_name, get_equip_name, get_master_equip_sn

logger = logging.getLogger(__name__)


def _parse_support_type(support_type_str: Optional[str]) -> SupportType:
    """解析助战类型字符串"""
    if not support_type_str:
        return SupportType.NONE
    try:
        return SupportType(support_type_str.lower())
    except ValueError:
        return SupportType.NONE


def _extract_ce_id(svt_info: dict) -> Optional[int]:
    """从从者信息中提取概念礼装 ID（兼容新旧格式）"""
    if not isinstance(svt_info, dict):
        return None

    equip1 = svt_info.get("equip1")
    if isinstance(equip1, dict) and equip1.get("id"):
        return equip1["id"]

    ce_id = svt_info.get("ceId")
    if ce_id is not None:
        return ce_id

    return None


def _candidate_assist_settings(team_data: dict) -> Optional[dict]:
    """Export explicit Grand support and Maa CE candidates BBC can express.

    Ordinary BBC assistEquip's outer groups are OR choices; nested candidates
    inside a normal group are not executed by BBC. Grand support instead uses
    three actual slots, each with its own list. BBC's fullEquip flag applies to
    every option, so mixed/non-MLB constraints must not be silently weakened.
    Ordinary single-CE exports retain their prior output shape. Grand support
    always needs its mode and bond-effect filter, even without named CE slots.
    """
    front = list(team_data.get("onFieldSvts") or [])[:3]
    backup = list(team_data.get("backupSvts") or [])[:3]
    members = front + [None] * (3 - len(front)) + backup + [None] * (3 - len(backup))
    for member in members:
        if not isinstance(member, dict):
            continue
        for key in ("equip1", "equip2", "equip3"):
            requirement = member.get(key)
            if (isinstance(requirement, dict) and "candidates" in requirement
                    and requirement["candidates"] is not None
                    and not isinstance(requirement["candidates"], list)):
                raise ValueError(f"BBC 导出的 {key}.candidates 必须是候选数组")
            if (member.get("grandSvt") is True and key == "equip2"
                    and isinstance(requirement, dict) and requirement.get("candidates")):
                raise ValueError("冠位羁绊礼装仅支持效果筛选，不能导出 equip2 礼装候选；请先修正候选要求。")
    has_extension = any(isinstance(member, dict) and any(
        isinstance(member.get(key), dict) and bool(member[key].get("candidates"))
        for key in ("equip1", "equip2", "equip3")) for member in members)
    supports = [(index, member) for index, member in enumerate(members)
                if isinstance(member, dict) and member.get("supportType") in ("friend", "fixed", "npc")]
    has_grand_support = any(member.get("grandSvt") is True for _, member in supports)
    if not has_extension and not has_grand_support:
        return None
    if len(supports) != 1:
        raise ValueError("BBC 冠位或多候选礼装导出必须恰有一个助战位置")
    index, support = supports[0]
    if support.get("supportType") == "npc":
        raise ValueError("BBC 无法保留仅 NPC 助战限制，请使用原始 Chaldea JSON")
    if any(isinstance(member, dict) and member is not support and any(
            isinstance(member.get(key), dict) and member[key].get("candidates")
            for key in ("equip1", "equip2", "equip3")) for member in members):
        raise ValueError("BBC 不支持自有从者的多候选礼装要求，请使用原始 Chaldea JSON")
    grand = support.get("grandSvt") is True
    groups, statuses = [], []
    for number in range(1, 4):
        # The unique bond CE is not a card-face filter and does not share the
        # visible CEs' global MLB constraint. Old imported metadata stays intact.
        if grand and number == 2:
            groups.append([])
            continue
        key = f"equip{number}"
        raw = support.get(key)
        if raw is None and number == 1 and support.get("ceId") is not None:
            raw = {}
        if raw is None:
            groups.append([])
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"BBC 导出的 {key} 必须是礼装对象")
        alternatives = raw.get("candidates") or []
        if not isinstance(alternatives, list) or len(alternatives) > 2:
            raise ValueError(f"BBC 导出的 {key} 最多支持 3 个礼装候选")
        primary = dict(raw)
        if number == 1:
            # Resolve aliases per field, as native support does. Null IDs may
            # inherit ceId; explicit zero IDs and null/false MLB never fall back.
            if primary.get("id") is None:
                primary["id"] = support.get("ceId")
            if "limitBreak" not in primary:
                primary["limitBreak"] = support.get("ceLimitBreak")
        primary_id = primary.get("id")
        if not alternatives and (primary_id is None or type(primary_id) is int and primary_id == 0):
            groups.append([])
            continue
        names = []
        for candidate_index, candidate in enumerate([primary, *alternatives]):
            if not isinstance(candidate, dict) or type(candidate.get("id")) is not int or candidate["id"] <= 0:
                raise ValueError(f"BBC 导出的 {key} 礼装候选必须包含有效 ID")
            if candidate_index and candidate.get("candidates"):
                raise ValueError("BBC 不支持嵌套的礼装候选")
            status = candidate.get("limitBreak")
            if status is not None and type(status) is not bool:
                raise ValueError("BBC 礼装满破状态必须是布尔值或未指定")
            if status is False:
                raise ValueError("BBC 只能要求满破或不限制，不能要求某个候选为非满破；请使用原始 Chaldea JSON")
            name = get_equip_name(candidate["id"])
            if not name:
                raise ValueError(f"BBC 礼装候选 {candidate['id']} 缺少名称，无法导出")
            names.append(name)
            statuses.append(status)
        groups.append(names)
    if len(set(statuses)) > 1:
        raise ValueError("BBC 的 fullEquip 是全局设置，无法表达各候选不同的满破要求；请使用原始 Chaldea JSON")
    ce_only = support.get("svtId") in (None, 0)
    if not grand and (groups[1] or groups[2]):
        raise ValueError("普通助战只有一个礼装位，不能将额外礼装槽自动当作同槽候选")
    if grand:
        equipment = [None if not names else names[0] if len(names) == 1 else names for names in groups]
        mode = "冠位助战仅礼装" if ce_only else "冠位助战"
    else:
        equipment = groups[0][0] if len(groups[0]) == 1 else groups[0] + [None] * (3 - len(groups[0]))
        mode = "仅礼装" if ce_only else "从者礼装"
    settings = {"assistIdx": index, "assistMode": mode, "assistEquip": equipment,
                "fullEquip": 1 if statuses and statuses[0] is True else 0}
    if grand:
        board = support.get("classBoardData") or {}
        changed = board.get("grandBondEquipSkillChange") if isinstance(board, dict) else None
        if ce_only and not any(groups) and type(changed) is not bool:
            raise ValueError("BBC 仅礼装冠位助战至少需要一个礼装或明确的羁绊效果要求；不能导出完全不限制的空要求。")
        settings["jbType"] = "NP50" if changed is True else "初始" if changed is False else "任意"
    return settings


def _convert_svt_info(svt_data: Optional[dict], position: int, is_on_field: bool) -> Optional[ServantInfo]:
    """将 Chaldea 从者数据转换为内部 ServantInfo"""
    if svt_data is None:
        return None
    if not isinstance(svt_data, dict):
        logger.warning(f"[Chaldea] 无效的从者数据类型: {type(svt_data)}")
        return None

    skill_lvs = svt_data.get("skillLvs", [])
    if not isinstance(skill_lvs, list) or len(skill_lvs) < 3:
        skill_lvs = [10, 10, 10]

    append_lvs = svt_data.get("appendLvs", [])
    if not isinstance(append_lvs, list):
        append_lvs = []

    return ServantInfo(
        svt_id=svt_data.get("svtId"),
        name=None,
        lv=svt_data.get("lv", 1),
        skill_lvs=skill_lvs[:3],
        append_lvs=append_lvs,
        td_lv=svt_data.get("tdLv", 1),
        atk_fou=svt_data.get("atkFou", 1000),
        hp_fou=svt_data.get("hpFou", 1000),
        ce_id=_extract_ce_id(svt_data),
        ce_limit_break=svt_data.get("ceLimitBreak", False),
        ce_lv=svt_data.get("ceLv", 0),
        support_type=_parse_support_type(svt_data.get("supportType")),
        is_on_field=is_on_field,
        position=position
    )


def _convert_formation(team_data: dict) -> TeamFormation:
    """转换队伍编成数据"""
    formation = TeamFormation()

    on_field_svts = team_data.get("onFieldSvts", [])
    if isinstance(on_field_svts, list):
        for i in range(3):
            svt_data = on_field_svts[i] if i < len(on_field_svts) else None
            formation.on_field[i] = _convert_svt_info(svt_data, i, True)

    backup_svts = team_data.get("backupSvts", [])
    if isinstance(backup_svts, list):
        for i in range(3):
            svt_data = backup_svts[i] if i < len(backup_svts) else None
            formation.backup[i] = _convert_svt_info(svt_data, i + 3, False)

    mystic_code = team_data.get("mysticCode", {})
    if isinstance(mystic_code, dict):
        formation.mystic_code = MysticCodeInfo(
            mystic_code_id=mystic_code.get("mysticCodeId", 0),
            level=mystic_code.get("level", 10)
        )

    return formation


def _determine_assist_info(formation: TeamFormation) -> Tuple[int, str, Union[str, List[Optional[str]]], List[int]]:
    """确定助战相关信息，返回 (assist_idx, assist_mode, assist_equip, used_servant)"""
    assist_idx = None
    assist_mode = "从者礼装"
    assist_equip: Union[str, List[Optional[str]]] = "迦勒底午茶时光"
    used_servant: List[int] = []

    for i, svt in enumerate(formation.on_field):
        if svt is None:
            continue
        if svt.support_type in (SupportType.FRIEND, SupportType.FIXED):
            assist_idx = i
            if svt.ce_id is not None:
                ce_name = get_equip_name(svt.ce_id)
                if ce_name:
                    assist_equip = ce_name
                    assist_mode = "从者礼装"
        else:
            used_servant.append(i)

    if assist_idx is None:
        assist_idx = 2
        used_servant = [i for i in range(3) if formation.on_field[i] is not None and i != assist_idx]
        if not used_servant:
            used_servant = [0, 1]

    return assist_idx, assist_mode, assist_equip, used_servant


def _reject_special_skill_choices(delegate) -> None:
    if not isinstance(delegate, dict):
        return
    fields = [key for key in ("tdTypeChanges", "skillActSelectSelections") if delegate.get(key)]
    if fields:
        raise ValueError("当前 BBC 导出无法保留 Chaldea 特殊技能选项（" + "、".join(fields)
                         + "）；请使用原生自动战斗，不能忽略选项后转换。")


def _reject_native_actor_guards(actions) -> None:
    """BBC positions cannot enforce original-instance identity after editing."""
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        if action.get("type") == "retire":
            raise ValueError("BBC 无法无损执行从者退场标记及其身份校验；请使用原生自动战斗。")
        cards = action.get("attacks")
        guarded_card = isinstance(cards, list) and any(isinstance(card, dict) and "actor" in card for card in cards)
        if "actor" in action or guarded_card:
            raise ValueError(f"actions[{index}]：BBC 无法保留 actor 从者实例身份校验；请使用原生自动战斗，不能移除护栏后转换。")


def _reject_native_card_order(actions) -> None:
    """The legacy BBC output only expresses leading NP order, not mixed cards."""
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("type") != "attack":
            continue
        cards = action.get("attacks")
        if cards is None:
            continue
        if not isinstance(cards, list):
            raise ValueError(f"actions[{index}].attacks：出卡必须是数组，不能忽略后导出 BBC")
        for card in cards:
            if (not isinstance(card, dict) or card.get("isAuto") is True
                    or card.get("isTD") is not True
                    or "isAuto" in card and type(card["isAuto"]) is not bool):
                raise ValueError(f"actions[{index}]：BBC 反向导出无法保留普通卡、isAuto 占位或延后宝具的混排顺序；请使用原生自动战斗。")


def convert_actions_to_bbc_rounds(
    actions: List[dict],
    delegate: Optional[dict] = None,
    mystic_code_id: int = 0
) -> Dict[str, Any]:
    """
    将 Chaldea actions 转换为 BBC 回合配置

    技能编号映射:
    - 1-3: 从者0的技能1-3
    - 4-6: 从者1的技能1-3
    - 7-9: 从者2的技能1-3
    - 10-12: 御主技能1-3
    """
    _reject_special_skill_choices(delegate)
    _reject_native_actor_guards(actions)
    _reject_native_card_order(actions)
    rounds_config: Dict[str, Any] = {}
    current_skills: List[Union[int, List]] = []
    current_nps: List[int] = []
    round_idx = 1
    turn_idx = 0

    replace_members: List[List[int]] = []
    if isinstance(delegate, dict):
        replace_members = delegate.get("replaceMemberIndexes", [])
    replace_ptr = 0

    is_order_change = (mystic_code_id in ORDER_CHANGE_MYSTIC_CODE_IDS)

    def flush_round():
        nonlocal current_skills, current_nps, round_idx
        rounds_config[f"round{round_idx}_turns"] = 1
        rounds_config[f"round{round_idx}_extraSkill"] = []
        rounds_config[f"round{round_idx}_extraStrategy"] = None
        rounds_config[f"round{round_idx}_turn{turn_idx}_skill"] = current_skills.copy()
        rounds_config[f"round{round_idx}_turn{turn_idx}_np"] = current_nps.copy()
        rounds_config[f"round{round_idx}_turn{turn_idx}_strategy"] = None
        rounds_config[f"round{round_idx}_turn{turn_idx}_condition"] = None
        current_skills = []
        current_nps = []
        round_idx += 1

    for action in actions:
        if not isinstance(action, dict):
            continue

        action_type = action.get("type", "")

        if action_type == "retire":
            raise ValueError("BBC 无法无损执行从者退场标记及其身份校验；请使用原生自动战斗。")

        if action_type == "skill":
            svt_idx = action.get("svt")
            skill_idx = action.get("skill", 0)
            options = action.get("options", {}) or {}

            if svt_idx is None:
                bbc_skill_idx = 10 + skill_idx

                if bbc_skill_idx == 12 and is_order_change:
                    if not isinstance(replace_members, list) or replace_ptr >= len(replace_members):
                        raise ValueError("BBC 换人技能缺少 delegate.replaceMemberIndexes 配对，不能猜测换人位置")
                    pair = replace_members[replace_ptr]
                    if (not isinstance(pair, list) or len(pair) != 2
                            or type(pair[0]) is not int or not 0 <= pair[0] <= 2
                            or type(pair[1]) is not int or not 0 <= pair[1] <= 5):
                        raise ValueError("BBC 换人配对必须指定前排 0–2 与候补相对 0–2／旧绝对 3–5 槽位")
                    front, backup = pair
                    backup = backup + 4 if backup < 3 else backup + 1
                    # BBC's negative skill numbers select special servant menus;
                    # Order Change is [master skill 12, front 1..3, back 4..6].
                    current_skills.append([12, front + 1, backup])
                    replace_ptr += 1
                    continue
            else:
                if not isinstance(svt_idx, int) or not (0 <= svt_idx <= 5):
                    logger.warning(f"[Chaldea] 无效的从者索引: {svt_idx}")
                    continue
                bbc_skill_idx = svt_idx * 3 + skill_idx + 1

            player_target = options.get("playerTarget")
            if player_target is not None and isinstance(player_target, int) and player_target >= 0:
                current_skills.append([bbc_skill_idx, player_target + 1])
            else:
                current_skills.append(bbc_skill_idx)

        elif action_type == "attack":
            attacks = action.get("attacks", [])
            if (attacks is None or attacks == []) and not current_skills:
                # BBC drops a wholly empty configured turn, which would enable
                # its free skill/NP policy instead of preserving a normal-card turn.
                raise ValueError(
                    "BBC 无法无损表达不使用技能和宝具的纯空回合；"
                    "请使用原生战斗保留该回合的默认普通卡选取规则。"
                )
            if isinstance(attacks, list):
                for atk in attacks:
                    if not isinstance(atk, dict):
                        continue
                    if atk.get("isTD", False):
                        svt_pos = atk.get("svt", 0)
                        if isinstance(svt_pos, int) and 0 <= svt_pos <= 5:
                            np_pos = svt_pos + 1
                            if np_pos not in current_nps:
                                current_nps.append(np_pos)

            flush_round()

    if current_skills:
        # Native plans finish a trailing skill-only turn with ordinary cards.
        # BBC keeps this row because its skill list is nonempty (getRealTurns).
        flush_round()

    return rounds_config


def chaldea_to_bbc(share_data: dict) -> dict:
    """主转换函数: Chaldea BattleShareData → BBchannel 配置 dict"""
    if not isinstance(share_data, dict):
        logger.error("[Chaldea] share_data 类型错误，期望 dict")
        return {}

    advanced = share_data.get("maaAdvancedStrategy")
    if advanced is not None and (not isinstance(advanced, dict) or advanced):
        raise ValueError("BBC 无法等价执行 maaAdvancedStrategy 全局高级策略；请使用原生自动战斗，不能忽略策略后转换。")

    team_data = share_data.get("team", {})
    actions = share_data.get("actions", [])
    if actions is None:
        actions = []
    delegate = share_data.get("delegate") or {}
    _reject_special_skill_choices(delegate)

    if not isinstance(team_data, dict):
        logger.error("[Chaldea] team 字段类型错误")
        return {}

    formation = _convert_formation(team_data)
    mystic_code_id = formation.mystic_code.mystic_code_id

    assist_idx, assist_mode, assist_equip, used_servant = _determine_assist_info(formation)
    candidate_settings = _candidate_assist_settings(team_data)
    if candidate_settings is not None:
        assist_idx = candidate_settings["assistIdx"]
        assist_mode = candidate_settings["assistMode"]
        assist_equip = candidate_settings["assistEquip"]
        used_servant = [i for i, servant in enumerate(formation.on_field)
                        if servant is not None and i != assist_idx]

    result: Dict[str, Any] = {
        "_source": "chaldea",
        "_questId": (share_data.get("quest") or {}).get("id"),
        "_appBuild": share_data.get("appBuild"),
    }

    all_svts = list(formation.on_field) + list(formation.backup)
    for i in range(6):
        svt = all_svts[i] if i < len(all_svts) else None
        if svt is not None and svt.svt_id is not None:
            result[f"servant_{i}_name"] = get_servant_name(svt.svt_id)
        else:
            result[f"servant_{i}_name"] = None

    result["assistMode"] = assist_mode
    result["assistIdx"] = assist_idx
    result["assistEquip"] = assist_equip
    result["master_equip"] = get_master_equip_sn(mystic_code_id)
    result["usedServant"] = used_servant
    if candidate_settings is not None:
        result.update(candidate_settings)

    result["connectMode"] = "ADB方式"
    result["snapshotDevice"] = ["normal", "127.0.0.1:7555"]
    result["operateDevice"] = ["normal", "127.0.0.1:7555"]
    result["specialKeys"] = []

    if isinstance(actions, list):
        bbc_actions = convert_actions_to_bbc_rounds(actions, delegate, mystic_code_id)
        result.update(bbc_actions)
    else:
        logger.warning("[Chaldea] actions 字段不是列表，跳过战斗逻辑转换")

    return result
