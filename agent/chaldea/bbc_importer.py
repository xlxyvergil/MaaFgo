"""将 BBchannel/settings 的队伍配置导入为本地 BattleShareData。

转换可确定含义的队伍、回合、选敌和出卡意图。
资料只从本地加载；无法表达的动态条件与专属技能明确拒绝。
"""

from __future__ import annotations

import hashlib
from itertools import permutations
import json
import re
from pathlib import Path

from .servant_types import MYSTIC_CODE_ID_TO_BBC_SN, ORDER_CHANGE_MYSTIC_CODE_IDS


ROOT = Path(__file__).resolve().parents[2]
_ROUND_KEY = re.compile(r"round(\d+)_turns$")
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_WAVES = 30
_MAX_TURNS_PER_WAVE = 100
_MAX_TOTAL_TURNS = 1000
_MAX_ACTIONS = 5000


class BbcImportError(ValueError):
    """BBC 配置无法可靠转换。"""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BbcImportError(f"读取 JSON 失败: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BbcImportError(f"JSON 顶层必须是对象: {path}")
    return data


def load_bbc_catalogs(root: Path, server: str = "CH") -> tuple[dict, dict]:
    """Load exact local IDs/decks without requiring a BBC install or networking.

    Actual BBC data wins over the bundled name map. Missing names still use the
    bundled map so installing an older BBC never removes known identities.
    """
    root = Path(root)
    if not isinstance(server, str):
        raise BbcImportError("BBC server 必须是字符串")
    server = {"CH": "CH", "CN": "CH", "JP": "JP", "CNTW": "CNTW"}.get(server.upper(), "CH")
    bundled = root / "agent/chaldea/bbc_servant_ids.json"
    servants = {}
    if bundled.is_file():
        document = _read_json(bundled)
        if document.get("schema") != 1 or not isinstance(document.get("servants"), dict):
            raise BbcImportError("BBC 离线从者映射格式无效")
        servants.update(document["servants"])
    servant_file = root / "BBchannel" / f"servant_info_{server}.json"
    if servant_file.is_file():
        servants.update(_read_json(servant_file))
    names_file = root / "agent/utils/Chaldea/servant_names_CN.json"
    if names_file.is_file():
        local_names: dict[str, set[int]] = {}
        for sid, name in _read_json(names_file).items():
            if str(sid).isascii() and str(sid).isdigit() and int(sid) > 0 and isinstance(name, str):
                local_names.setdefault(name, set()).add(int(sid))
        for name, ids in local_names.items():
            if len(ids) == 1:
                servants.setdefault(name, {"SN": next(iter(ids))})
    editor_file = root / "agent/battle_editor/data/catalog.json"
    decks = {}
    editor_names: dict[str, set[int]] = {}
    if editor_file.is_file():
        records = _read_json(editor_file).get("servants", [])
        if isinstance(records, list):
            for item in records:
                if not isinstance(item, dict) or type(item.get("id")) is not int:
                    continue
                sid = item["id"]
                cards = item.get("cards")
                if isinstance(cards, list) and len(cards) == 5 and all(type(n) is int and n in (1, 2, 3) for n in cards):
                    for identity in [sid] + (item.get("aliases") or []):
                        decks[str(identity)] = cards
                for name in [item.get("name"), item.get("nameJp")] + (item.get("searchAliases") or []):
                    if isinstance(name, str) and name:
                        editor_names.setdefault(name, set()).add(sid)
    for name, ids in editor_names.items():
        if len(ids) == 1:
            servants.setdefault(name, {"SN": next(iter(ids))})
    if not servants:
        raise BbcImportError("缺少 BBC 离线从者资料，请使用包含编队编辑器数据的完整安装包")
    # Do not mutate objects obtained from the source documents.
    servants = {name: dict(item, _cards=decks.get(str(item.get("SN"))))
                if isinstance(item, dict) else item for name, item in servants.items()}
    equip_file = root / "agent" / "utils" / "Chaldea" / "equip_names_CN.json"
    equips = _read_json(equip_file) if equip_file.is_file() else {}
    return servants, equips


# Kept for existing batch callers and older external scripts.
_catalogs = load_bbc_catalogs


def _servant_id(name: str, catalog: dict, slot: int) -> int:
    item = catalog.get(name)
    if not isinstance(item, dict):
        aliases = {str(value.get("SN")): value for value in catalog.values()
                   if isinstance(value, dict) and name in (value.get("other_name") or [])}
        if len(aliases) == 1:
            item = next(iter(aliases.values()))
    sn = item.get("SN") if isinstance(item, dict) else None
    if (type(sn) not in (str, int) or not str(sn).isascii()
            or not str(sn).isdigit() or int(sn) <= 0):
        raise BbcImportError(f"槽位 {slot} 的从者“{name}”无法唯一匹配 BBC 从者 ID")
    return int(sn)


def _equip_ids(value: object, equips: dict, *, grand: bool = False) -> tuple[list[list[int]], list[str]]:
    """Read BBC's executable CE choices, which differ by support mode.

    Grand support uses three real cells with OR choices inside each cell. In
    ordinary mode BBC checkMoreEquip ORs the three groups but only reads the
    first template in each group; preserve that behavior and report ignored UI
    choices rather than quietly widening the imported filter.
    """
    warnings: list[str] = []
    by_name: dict[str, list[int]] = {}
    for key, name in equips.items():
        if str(key).isascii() and str(key).isdigit() and int(key) > 0 and isinstance(name, str):
            by_name.setdefault(name, []).append(int(key))
    if isinstance(value, str):
        names: list[object] = [value]
    elif isinstance(value, list):
        if len(value) > 3:
            raise BbcImportError("助战礼装最多只能有 3 个槽位")
        names = value
    else:
        return [[], [], []], warnings
    result: list[list[int]] = [[], [], []]
    for index, raw in enumerate(names):
        if isinstance(raw, list):
            if not raw:
                continue
            if grand and len(raw) > 3:
                raise BbcImportError(f"助战礼装第 {index + 1} 格超过 3 个候选，无法完整导入；请先减少候选")
            if not grand and len(raw) > 1:
                warnings.append(f"BBC 普通助战第 {index + 1} 组在实战仅检测首个礼装，已按其实际逻辑导入；该组后续 {len(raw) - 1} 个候选不会在 BBC 中执行")
            choices = raw if grand else raw[:1]
        else:
            choices = [raw]
        target = index if grand else 0
        for name in choices:
            if not isinstance(name, str) or not name.strip():
                continue
            ids = by_name.get(name.strip(), [])
            if len(ids) == 1:
                if ids[0] not in result[target]:
                    result[target].append(ids[0])
            else:
                warnings.append(f"助战礼装“{name}”无法唯一匹配 Chaldea 礼装 ID，未导入")
        if len(result[target]) > 3:
            raise BbcImportError(f"助战礼装第 {target + 1} 格超过 3 个可执行候选，无法完整导入；请先减少候选")
    return result, warnings


def _support_levels(config: dict, support: dict, warnings: list[str]) -> None:
    # BBC Battle.__init__ uses 10/10/10 when absent, and accepts the old
    # numeric switches 1 (10/10/10) and 0 (no skill thresholds).
    skills = config.get("skillsLevel", 1)
    if type(skills) is int and skills in (0, 1):
        skills = [10 if skills else 0] * 3 + [0] * 5
    if skills is not None:
        if (isinstance(skills, list) and len(skills) <= 8
                and all(type(value) is int and 0 <= value <= 10 for value in skills)):
            padded = skills + [0] * (8 - len(skills))
            # All zero means unrestricted, including the old numeric 0.
            if any(padded):
                support["skillLvs"] = padded[:3]
                support["appendLvs"] = padded[3:]
        else:
            warnings.append("助战技能等级筛选值无效，未导入")
    for source_key, target_key, maximum in (
        ("NPlevel", "tdLv", 5),
        ("servantLevel", "lv", 130),
    ):
        value = config.get(source_key)
        if value is None:
            continue
        if type(value) is int and 0 <= value <= maximum:
            if value:
                support[target_key] = value
        else:
            warnings.append(f"助战 {source_key} 筛选值无效，未导入")


def _skill_action(raw: object, mystic_code_id: int | None, swaps: list[list[int]],
                  location: str) -> dict:
    target = None
    number = raw
    if isinstance(raw, list):
        if len(raw) == 3 and raw[0] == 12 and all(type(n) is int for n in raw[1:]):
            if mystic_code_id not in ORDER_CHANGE_MYSTIC_CODE_IDS:
                raise BbcImportError(f"{location}: 配置了换人，但魔术礼装不是换人服")
            a, b = raw[1:]
            front, back = (a, b) if 1 <= a <= 3 and 4 <= b <= 6 else (b, a)
            if not (1 <= front <= 3 and 4 <= back <= 6):
                raise BbcImportError(f"{location}: 换人槽位无效: {raw}")
            swaps.append([front - 1, back - 4])
            return {"type": "skill", "svt": None, "skill": 2}
        if len(raw) != 2 or not all(type(n) is int for n in raw):
            raise BbcImportError(f"{location}: 不支持的 BBC 特殊技能 {raw}")
        number, target = raw
        if not 1 <= target <= 3:
            raise BbcImportError(f"{location}: 技能目标必须是前排 1～3 号位: {raw}")
    if type(number) is not int or not 1 <= number <= 12:
        raise BbcImportError(f"{location}: 不支持的 BBC 技能 {raw}")
    if number >= 10 and mystic_code_id is None:
        raise BbcImportError(f"{location}: 使用了御主技能，但没有指定御主礼装 master_equip")
    if number == 12 and mystic_code_id in ORDER_CHANGE_MYSTIC_CODE_IDS:
        raise BbcImportError(f"{location}: 换人技能缺少首发和候补槽位")
    action = {
        "type": "skill",
        "svt": (number - 1) // 3 if number <= 9 else None,
        "skill": (number - 1) % 3 if number <= 9 else number - 10,
    }
    if target is not None:
        action["options"] = {"playerTarget": target - 1}
    return action


def _np_attacks(raw: object, location: str) -> list[dict]:
    if (not isinstance(raw, list) or len(raw) > 3
            or any(type(n) is not int or not 1 <= n <= 3 for n in raw)
            or len(set(raw)) != len(raw)):
        raise BbcImportError(f"{location}: 宝具槽位必须是不重复的前排 1～3 号位列表")
    return [{"isTD": True, "svt": n - 1, "card": -1} for n in raw]


def _retirement_mapping(raw: object, bbc_order: list[int | None],
                        native_order: list[int], retired: set[int],
                        slots: list[dict | None], location: str):
    """Accept only one natural-replacement sequence equal to BBC's snapshot map.

    BBC applies the map simultaneously after the attack. Native plans keep dead
    instances at the tail; comparing their live projection prevents arbitrary
    copy/delete/swap mappings from being mistaken for natural departures.
    """
    if not isinstance(raw, dict):
        raise BbcImportError(f"{location}: replace 必须是攻击后的槽位映射对象")
    target = list(bbc_order)
    for key, value in raw.items():
        if type(key) is not str or key not in {str(i) for i in range(1, 7)}:
            raise BbcImportError(f"{location}: replace 目标必须是 1～6 号槽位")
        if value is not None and (type(value) is not int or not 1 <= value <= 6):
            raise BbcImportError(f"{location}: replace 来源必须是 1～6 号槽位或 null")
        target[int(key) - 1] = bbc_order[value - 1] if value is not None else None
    live = [token for token in target if token is not None]
    if len(live) != len(set(live)):
        raise BbcImportError(f"{location}: replace 复制了同一从者，不能等价转换为自然退场")

    matches = []
    for count in range(1, 4):
        for fronts in permutations(range(3), count):
            order, dead, actions = list(native_order), set(retired), []
            for front in fronts:
                outgoing = order[front]
                back = next((i for i in range(3, 6) if order[i] not in dead and slots[order[i]] is not None), None)
                actor = slots[outgoing]
                if outgoing in dead or not isinstance(actor, dict) or not actor.get("svtId") or back is None:
                    break
                incoming = slots[order[back]]
                if (not isinstance(incoming, dict) or not incoming.get("svtId")
                        or actor["svtId"] == incoming["svtId"]):
                    break
                actions.append({"type": "retire", "svt": front,
                                "actor": {"initialSlot": outgoing, "svtId": actor["svtId"]}})
                order[front] = order.pop(back)
                order.append(outgoing)
                dead.add(outgoing)
            else:
                projection = [token if token not in dead and slots[token] is not None else None for token in order]
                if projection == target:
                    matches.append((order, dead, actions))
    if len(matches) != 1:
        raise BbcImportError(f"{location}: replace 不能唯一等价为已知从者的自然退场递补；不支持任意换位、删除候补、未知或同名递补")
    order, dead, actions = matches[0]
    return target, order, dead, actions


def _strategy_attacks(strategy: object, nps: list[dict], roster: list[dict | None],
                      decks: dict[int, list[int]], location: str,
                      warnings: list[str]) -> list[dict]:
    """Keep fixed NP/card positions; disclose dynamic priority degradation.

    BBC can choose among entire strategies and card priority lists. The native
    fixed-card format can express a singleton/color intention and default-card
    placeholders, but not the conditional search over those alternatives.
    """
    if strategy is None or strategy == []:
        return nps
    if not isinstance(strategy, list) or not strategy or not all(isinstance(row, dict) for row in strategy):
        raise BbcImportError(f"{location}: 选卡策略必须是对象数组或 null")
    if len(strategy) > 1:
        warnings.append(f"{location}: BBC 多套选卡优先策略无法完整表示，已按首套保留固定卡位，其余候选未导入")
    row = strategy[0]
    if any(row.get("breakpoint") or []):
        raise BbcImportError(f"{location}: 选卡中断点无法映射到固定出卡顺序")
    result = []
    used_cards: dict[int, set[int]] = {}
    used_nps = set()
    for index in range(1, 4):
        card = row.get(f"card{index}")
        if not isinstance(card, dict):
            raise BbcImportError(f"{location}: 选卡策略缺少 card{index}")
        kind, choices = card.get("type"), card.get("cards")
        if type(kind) is not int or kind not in (0, 1, 2) or not isinstance(choices, list):
            raise BbcImportError(f"{location}.card{index}: 不支持的选卡类型或候选列表")
        if card.get("criticalStar", 0) != 0 or card.get("more_or_less", True) is not True:
            warnings.append(f"{location}.card{index}: 暴击星阈值无法表示，已保留卡位并取消该筛选")
        if kind == 2:
            if choices:
                raise BbcImportError(f"{location}.card{index}: 默认普通卡槽不应带具体候选")
            warnings.append(f"{location}.card{index}: 默认普通卡位置已保留，实际补卡使用当前 MaaFGO 策略（BBC 默认红蓝绿排序未导入）")
            result.append({"isAuto": True})
        elif kind == 0:
            if (not choices or any(type(value) is not int or not 1 <= value <= 3 for value in choices)):
                raise BbcImportError(f"{location}.card{index}: 宝具候选槽位无效")
            # BBC itself reads only cards[0] for a type-0 NP slot.
            svt = choices[0] - 1
            if svt in used_nps:
                raise BbcImportError(f"{location}: 同一宝具不能在一回合内重复出卡")
            used_nps.add(svt)
            result.append({"isTD": True, "svt": svt, "card": -1})
        elif not choices:
            raise BbcImportError(f"{location}.card{index}: 空普通卡候选会使 BBC 整套策略失败，无法当作默认卡导入")
        elif len(choices) != 1:
            warnings.append(f"{location}.card{index}: 普通卡候选优先级无法表示，已改为默认普通卡补齐")
            result.append({"isAuto": True})
        else:
            match = re.fullmatch(r"([1-3])([ABQ])", str(choices[0]))
            if match is None:
                raise BbcImportError(f"{location}.card{index}: 普通卡候选无效: {choices!r}")
            svt = int(match[1]) - 1
            member = roster[svt]
            sid = member.get("svtId") if isinstance(member, dict) else None
            deck = decks.get(sid)
            color = {"A": 1, "B": 2, "Q": 3}[match[2]]
            candidates = [i for i, value in enumerate(deck or [])
                          if value == color and i not in used_cards.get(svt, set())]
            if not candidates:
                raise BbcImportError(f"{location}.card{index}: 无法从当前前排 {svt + 1} 的资料确定 {match[2]} 卡（可能已换人、卡数不足或缺少从者资料）")
            chosen = candidates[0]
            used_cards.setdefault(svt, set()).add(chosen)
            result.append({"isTD": False, "svt": svt, "card": chosen, "cardType": color})
    # BBC's strategy branch ignores the separate *_np list entirely.
    while result and result[-1] == {"isAuto": True}:
        result.pop()
    return result


def convert_bbc_config(config: dict, catalog: dict, equips: dict, *, root: Path = ROOT) -> tuple[dict, list[str]]:
    """转换 BBC 配置；无法精确表达的动作直接失败。"""
    if not isinstance(config, dict):
        raise BbcImportError("BBC 配置顶层必须是对象")
    if any(not isinstance(key, str) for key in config):
        raise BbcImportError("BBC 配置字段名必须是字符串")
    from .bbc_special_skills import BbcSpecialSkillChoices

    special_choices = BbcSpecialSkillChoices(root=root)
    warnings: list[str] = ["BBC 按波次执行；已按每波有效回合数展开为原生固定回合。实战提前或延后清场时，后续动作不会自动等待对应波次，请检查回合安排。"]
    reverse_codes = {sn: code for code, sn in MYSTIC_CODE_ID_TO_BBC_SN.items()}
    sn = config.get("master_equip")
    if sn is not None and (type(sn) is not int or sn not in reverse_codes):
        raise BbcImportError(f"无法识别魔术礼装 master_equip={sn!r}")
    mystic_code_id = reverse_codes.get(sn)

    slots: list[dict | None] = []
    for index in range(6):
        name = config.get(f"servant_{index}_name")
        if name is None or name == "":
            slots.append(None)
        elif isinstance(name, str) and name.strip():
            slots.append({"svtId": _servant_id(name.strip(), catalog, index + 1),
                          "supportType": "none"})
        else:
            raise BbcImportError(f"槽位 {index + 1} 的从者名无效: {name!r}")
    assist = config.get("assistIdx")
    assist_mode = config.get("assistMode", "从者礼装")
    if assist_mode not in ("仅从者", "仅礼装", "从者礼装", "冠位助战", "冠位助战仅礼装"):
        raise BbcImportError(f"不支持的助战模式 assistMode={assist_mode!r}")
    ce_only = assist_mode in ("仅礼装", "冠位助战仅礼装")
    grand_support = assist_mode in ("冠位助战", "冠位助战仅礼装")
    if assist is not None and (type(assist) is not int or not 0 <= assist < 6):
        raise BbcImportError(f"助战索引 assistIdx={assist!r} 无效，应为 0～5")
    if assist is not None and (slots[assist] is not None or ce_only):
        if ce_only:
            slots[assist] = {"supportType": "friend"}
        slots[assist]["supportType"] = "friend"
        if not ce_only:
            _support_levels(config, slots[assist], warnings)
        if grand_support:
            slots[assist]["grandSvt"] = True
            bond_type = config.get("jbType")
            if bond_type in ("NP50", "初始"):
                slots[assist]["classBoardData"] = {"grandBondEquipSkillChange": bond_type == "NP50"}
            elif bond_type not in (None, "", "任意"):
                warnings.append(f"BBC 冠位羁绊礼装效果 {bond_type!r} 无法识别，未导入该筛选")
        if assist_mode in ("仅礼装", "从者礼装", "冠位助战", "冠位助战仅礼装"):
            equip_ids, equip_warnings = _equip_ids(config.get("assistEquip"), equips,
                                                   grand=grand_support)
            warnings.extend(equip_warnings)
            if grand_support and (equip_ids[0] or equip_ids[2]):
                warnings.append("BBC 冠位助战允许普通与报酬礼装互换；已按原配置保留固定槽位，当前原生助战不会自动交换，请核对礼装位置")
            for index, ids in enumerate(equip_ids, 1):
                if ids:
                    choices = [{"id": equip_id, **({"limitBreak": True} if config.get("fullEquip", 1) else {})}
                               for equip_id in ids]
                    equipment = choices[0]
                    if len(choices) > 1:
                        equipment["candidates"] = choices[1:]
                    slots[assist][f"equip{index}"] = equipment
            board = slots[assist].get("classBoardData")
            has_bond_effect = (grand_support and isinstance(board, dict)
                               and type(board.get("grandBondEquipSkillChange")) is bool)
            card_slots = (1, 3) if grand_support else (1,)
            has_ce_filter = any(slots[assist].get(f"equip{i}") for i in card_slots)
            if ce_only and not has_ce_filter and not has_bond_effect:
                raise BbcImportError("仅礼装助战没有能够导入的礼装筛选，无法生成可执行编队")
    elif assist is not None:
        warnings.append(f"助战索引 {assist!r} 没有对应从者，未标记助战")
    if not any(slots[:3]):
        raise BbcImportError("BBC 配置缺少前排从者或仅礼装助战")
    if config.get("onlyFriendAssist") == 1:
        warnings.append("BBC 的仅好友助战限制当前无法保存在编队方案中，请在运行任务的助战选项中设置")

    round_numbers = [match.group(1) for key in config
                     if (match := _ROUND_KEY.fullmatch(key))]
    if any(len(number) > 2 or not number.isascii() for number in round_numbers):
        raise BbcImportError(f"BBC 波次数必须为 1～{_MAX_WAVES}")
    rounds = sorted(int(number) for number in round_numbers)
    if len(rounds) > _MAX_WAVES or (rounds and max(rounds) > _MAX_WAVES):
        raise BbcImportError(f"BBC 波次数必须为 1～{_MAX_WAVES}")
    if rounds and rounds != list(range(1, max(rounds) + 1)):
        raise BbcImportError("BBC 波次 roundN_turns 缺失或不连续")
    if not rounds:
        warnings.append("BBC 没有固定波次配置，已保留为空 actions；原生战斗使用通用自动战斗及当前高级策略。")
    actions: list[dict] = []
    swaps: list[list[int]] = []
    roster = slots.copy()
    bbc_order = [index if member is not None else None for index, member in enumerate(slots)]
    native_order, retired = list(range(6)), set()
    binds_actors = any(key.endswith("_replace") and value for key, value in config.items())

    def bind_actor(action, slot, location):
        if not binds_actors:
            return
        token = bbc_order[slot]
        if token is None or native_order[slot] != token or token in retired:
            raise BbcImportError(f"{location}: BBC 与原生当前从者实例不一致，不能安全转化后续动作")
        action["actor"] = {"initialSlot": token, "svtId": slots[token]["svtId"]}
    decks = {int(item["SN"]): item["_cards"] for item in catalog.values()
             if isinstance(item, dict) and type(item.get("SN")) in (str, int)
             and str(item["SN"]).isascii() and str(item["SN"]).isdigit()
             and isinstance(item.get("_cards"), list)}
    total_turns = 0
    wave_turns = []
    for round_no in rounds:
        turns = config[f"round{round_no}_turns"]
        if type(turns) is not int or not 0 <= turns <= _MAX_TURNS_PER_WAVE:
            raise BbcImportError(f"第 {round_no} 波回合数无效: {turns!r}")
        total_turns += turns
        if total_turns > _MAX_TOTAL_TURNS:
            raise BbcImportError(f"BBC 总回合数不能超过 {_MAX_TOTAL_TURNS}")
        # BBC getRealTurns removes one final blank editor row exactly once.
        last = f"round{round_no}_turn{turns - 1}"
        last_empty = not any(config.get(f"{last}_{key}") for key in ("skill", "np", "strategy", "condition"))
        if turns == 1 and last_empty and not (config.get(f"round{round_no}_extraSkill") or config.get(f"round{round_no}_extraStrategy")):
            turns = 0
        elif turns > 1 and last_empty:
            turns -= 1
        wave_turns.append(turns)
        enemy_target = None
        extra_skills = config.get(f"round{round_no}_extraSkill")
        if extra_skills:
            raise BbcImportError(f"第 {round_no} 波配置了额外技能，无法映射到固定回合")
        extra_strategy = config.get(f"round{round_no}_extraStrategy")
        if extra_strategy:
            warnings.append(f"第 {round_no} 波额外选卡策略未导入")
        for turn_no in range(turns):
            prefix = f"round{round_no}_turn{turn_no}"
            condition = config.get(f"{prefix}_condition")
            if condition is not None and not isinstance(condition, list):
                raise BbcImportError(f"{prefix}: 条件必须是策略数组或 null")
            if condition:
                raise BbcImportError(f"{prefix}: 条件分支无法映射到固定回合")
            strategy = config.get(f"{prefix}_strategy")
            skills = config.get(f"{prefix}_skill", [])
            nps = config.get(f"{prefix}_np") or []
            if not isinstance(skills, list):
                raise BbcImportError(f"{prefix}: 技能必须是数组")
            for raw in skills:
                if isinstance(raw, list) and len(raw) == 2 and type(raw[0]) is int and raw[0] == 13:
                    if type(raw[1]) is not int or not 1 <= raw[1] <= 3:
                        raise BbcImportError(f"{prefix}: 仅支持前排敌方 1～3 号位，选敌 {raw!r} 无效")
                    # BBC numbers left-to-right; Chaldea numbers right-to-left.
                    enemy_target = 3 - raw[1]
                    continue
                before = len(swaps)
                try:
                    action = special_choices.convert(raw, roster, prefix)
                    if action is None:
                        action = _skill_action(raw, mystic_code_id, swaps, prefix)
                        special_choices.observe(action, roster, prefix)
                except BbcImportError:
                    raise
                except ValueError as exc:
                    raise BbcImportError(f"特殊技能转换失败: {exc}") from exc
                actor = action["svt"]
                if actor is not None and not (roster[actor] or {}).get("svtId"):
                    raise BbcImportError(f"{prefix}: 前排 {actor + 1} 没有确定的从者，不能配置技能 {raw!r}")
                if actor is not None:
                    bind_actor(action, actor, prefix)
                target = action.get("options", {}).get("playerTarget")
                if target is not None and roster[target] is None:
                    raise BbcImportError(f"{prefix}: 技能目标前排 {target + 1} 是空位")
                if enemy_target is not None:
                    action.setdefault("options", {})["enemyTarget"] = enemy_target
                actions.append(action)
                if len(swaps) > before:
                    front, back = swaps[-1][0], swaps[-1][1] + 3
                    if roster[front] is None or roster[back] is None:
                        raise BbcImportError(f"{prefix}: 换人必须选择两个非空槽位")
                    roster[front], roster[back] = roster[back], roster[front]
                    bbc_order[front], bbc_order[back] = bbc_order[back], bbc_order[front]
                    native_order[front], native_order[back] = native_order[back], native_order[front]
                if len(actions) >= _MAX_ACTIONS:
                    raise BbcImportError(f"BBC 转换后动作数不能超过 {_MAX_ACTIONS}")
            attacks = _strategy_attacks(strategy, [] if strategy else _np_attacks(nps, prefix),
                                       roster, decks, prefix, warnings)
            for attack in attacks:
                if attack.get("isTD") and not (roster[attack["svt"]] or {}).get("svtId"):
                    raise BbcImportError(f"{prefix}: 未知从者或空位不能配置宝具")
                if "svt" in attack:
                    bind_actor(attack, attack["svt"], prefix)
            action = {"type": "attack", "attacks": attacks}
            if enemy_target is not None:
                action["options"] = {"enemyTarget": enemy_target}
            actions.append(action)
            replacement = config.get(f"{prefix}_replace")
            if replacement:
                bbc_order, native_order, retired, retire_actions = _retirement_mapping(
                    replacement, bbc_order, native_order, retired, slots, prefix)
                roster = [slots[token] if token is not None else None for token in bbc_order]
                actions.extend(retire_actions)
            if len(actions) > _MAX_ACTIONS:
                raise BbcImportError(f"BBC 转换后动作数不能超过 {_MAX_ACTIONS}")

    try:
        special_delegate = special_choices.delegate()
    except ValueError as exc:
        raise BbcImportError(str(exc)) from exc
    share = {
        "_source": "bbc",
        "team": {
            "mysticCode": {"mysticCodeId": mystic_code_id} if mystic_code_id is not None else {},
            "onFieldSvts": slots[:3],
            "backupSvts": slots[3:],
        },
        "actions": actions,
        "delegate": {"replaceMemberIndexes": swaps, **special_delegate},
        "_bbcWaveTurns": wave_turns,
    }
    if any(count == 0 and any(wave_turns[i + 1:]) for i, count in enumerate(wave_turns)):
        raise BbcImportError("BBC 中间波次没有固定回合、后面仍有动作；当前固定回合计划无法等待该波清场，不能直接导入")
    if actions and actions[-1].get("type") == "retire":
        raise BbcImportError("BBC 最后一次攻击后的退场映射没有后续回合，无法安全安排原生退场确认；请移除末尾无后续动作的 replace")
    return share, warnings


def convert_bbc_document(config: dict, *, root: Path = ROOT) -> tuple[dict, list[str]]:
    """Convert an uploaded object in memory; never write config/Battle or network."""
    if not isinstance(config, dict):
        raise BbcImportError("BBC 配置顶层必须是对象")
    server = config.get("server")
    if server is not None and not isinstance(server, str):
        raise BbcImportError("BBC server 必须是字符串")
    root = Path(root)
    catalog, equips = load_bbc_catalogs(root, server or "CH")
    share, warnings = convert_bbc_config(config, catalog, equips, root=root)
    # IDs alone do not make a support CE recognizable. Keep the requested ID,
    # disclose missing project templates, and never download/expand resources.
    fullwidth_alnum = str.maketrans({n: n - 0xFEE0 for start, end in
                                   ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A))
                                   for n in range(start, end + 1)})
    bases = [root / "assets/resource/base/image/lizhuang", root / "resource/base/image/lizhuang"]
    for member in share["team"]["onFieldSvts"] + share["team"]["backupSvts"]:
        if not isinstance(member, dict) or member.get("supportType") != "friend":
            continue
        for slot in (1, 3) if member.get("grandSvt") else (1,):
            equipment = member.get(f"equip{slot}")
            if not isinstance(equipment, dict):
                continue
            for candidate in [equipment, *(equipment.get("candidates") or [])]:
                name = equips.get(str(candidate["id"]))
                if not isinstance(name, str):
                    continue
                filename = name.translate(fullwidth_alnum).replace("/", "／").replace("\\", "＼") + ".png"
                states = ("满破",) if candidate.get("limitBreak") is True else ("满破", "非满破")
                if not any((base / state / filename).is_file() for base in bases for state in states):
                    warnings.append(f"助战礼装“{name}”已保留 ID {candidate['id']}，但工程缺少对应助战识别模板，实战可能无法选中，请更换为工程已有礼装")
    return share, warnings


def import_bbc_file(source: str, *, root: Path = ROOT) -> tuple[Path, list[str]]:
    """读取 settings 中的配置并生成 config/Battle 下可供现有流程读取的 JSON。"""
    settings = (root / "BBchannel" / "settings").resolve()
    if not source or not source.strip():
        raise BbcImportError("请选择 BBC 队伍配置")
    name = Path(source.strip()).name
    if name != source.strip() or name in (".", ".."):
        raise BbcImportError("BBC 配置只能选择 BBchannel/settings 下的文件名")
    if not name.lower().endswith(".json"):
        name += ".json"
    source_path = settings / name
    if not source_path.is_file():
        raise BbcImportError(f"BBC 配置不存在: {source_path}")
    if source_path.resolve().parent != settings:
        raise BbcImportError("BBC 配置必须位于 BBchannel/settings 内")
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    config = _read_json(source_path)
    share, warnings = convert_bbc_document(config, root=root)
    share["_bbcSource"] = {"file": name, "sha256": digest}
    if warnings:
        share["_conversionWarnings"] = warnings

    safe_stem = _INVALID_FILENAME.sub("_", source_path.stem).rstrip(" .")[:48] or "team"
    result_digest = hashlib.sha256(
        json.dumps(share, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    output = root / "config" / "Battle" / f"bbc_{safe_stem}_{result_digest[:12]}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if _read_json(output) != share:
            raise BbcImportError(f"目标文件已存在且内容不同，请先处理: {output}")
        return output, warnings
    created = False
    try:
        with output.open("x", encoding="utf-8") as stream:
            created = True
            json.dump(share, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise
    return output, warnings


def import_bbc_all(*, root: Path = ROOT) -> tuple[
    list[tuple[str, Path, list[str]]], list[tuple[str, str]]
]:
    """逐个导入 settings 中的 JSON，单个配置失败时继续处理其余文件。"""
    settings = root / "BBchannel" / "settings"
    sources = sorted(settings.glob("*.json"), key=lambda path: path.name.casefold())
    if not sources:
        raise BbcImportError(f"未找到 BBC 队伍配置: {settings}")

    converted: list[tuple[str, Path, list[str]]] = []
    skipped: list[tuple[str, str]] = []
    for path in sources:
        try:
            output, warnings = import_bbc_file(path.name, root=root)
        except (BbcImportError, OSError) as exc:
            skipped.append((path.name, str(exc)))
        else:
            converted.append((path.name, output, warnings))
    return converted, skipped
