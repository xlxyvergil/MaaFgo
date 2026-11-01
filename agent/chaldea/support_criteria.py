"""将 Chaldea 队伍中明确标记的助战转换为助战筛选条件。"""

from .game_data import get_equip_name


_SUPPORT_TYPES = {"friend", "fixed", "npc"}
_FULLWIDTH_ALNUM = str.maketrans({
    codepoint: codepoint - 0xFEE0
    for start, end in ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A))
    for codepoint in range(start, end + 1)
})
_CLASS_TABS = {
    "saber": "剑士", "archer": "弓兵", "lancer": "枪兵", "rider": "骑兵",
    "caster": "魔术师", "assassin": "暗杀者", "berserker": "狂战士",
    "ruler": "OTHER", "avenger": "OTHER", "mooncancer": "OTHER",
    "alterego": "OTHER", "foreigner": "OTHER", "pretender": "OTHER",
    "shielder": "OTHER", "beast": "OTHER", "unbeast": "OTHER",
}


def _level(value, label, maximum):
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{label} 不是有效等级: {value!r}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效等级: {value!r}") from exc
    if not 0 <= number <= maximum:
        raise ValueError(f"{label} 超出范围: {number}")
    return number


def _levels(values, count, label):
    if values is None:
        values = []
    if not isinstance(values, list):
        raise ValueError(f"{label} 不是等级数组")
    return [_level(values[i], f"{label}{i + 1}", 10) if i < len(values) else 0
            for i in range(count)]


def _equip_limit_break(item, source_slot):
    """Current fields override legacy aliases independently, including null/false."""
    equip = item.get(f"equip{source_slot}")
    if isinstance(equip, dict) and "limitBreak" in equip:
        return equip["limitBreak"]
    return item.get("ceLimitBreak") if source_slot == 1 else None


def _equip(item, source_slot, resolve_name, target_slot=None):
    """解析 Chaldea 礼装槽，并映射到 MaaFGO 可见卡面槽位。"""
    equip = item.get(f"equip{source_slot}")
    if isinstance(equip, dict) and equip.get("id") is not None:
        raw_id = equip["id"]
    elif source_slot == 1:
        raw_id = item.get("ceId")
    else:
        raw_id = None
    if raw_id in (None, "", 0):
        return None
    try:
        equip_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"助战第{source_slot}格礼装 ID 无效: {raw_id!r}") from exc
    if equip_id <= 0:
        raise ValueError(f"助战第{source_slot}格礼装 ID 无效: {equip_id}")
    limit_break = _equip_limit_break(item, source_slot)
    name = resolve_name(equip_id)
    if not name:
        raise ValueError(f"助战第{source_slot}格礼装 {equip_id} 缺少名称数据")
    # Chaldea 名称库可能使用全角英数字（如“来自ＮＦＦ的爱”），而图片资源
    # 使用半角文件名。这里只转换英数字，不能用 NFKC 整体归一化；同时把
    # 名称中的路径分隔符转成全角符号，与可落盘的模板文件名保持一致。
    normalized_name = (str(name).translate(_FULLWIDTH_ALNUM)
                       .replace("/", "／").replace("\\", "＼"))
    return {
        "slot": target_slot if target_slot is not None else source_slot,
        "source_slot": source_slot,
        "id": equip_id,
        "name": f"{normalized_name}.png",
        "status": "满破" if limit_break is True else "非满破" if limit_break is False else "任意",
    }


def _equip_group(item, source_slot, resolve_name, target_slot=None):
    """One visible CE cell is an OR group; the original object is its first choice."""
    primary = _equip(item, source_slot, resolve_name, target_slot)
    stored = item.get(f"equip{source_slot}")
    extra = stored.get("candidates") if isinstance(stored, dict) else None
    if extra is None:
        extra = []
    if not isinstance(extra, list) or len(extra) > 2:
        raise ValueError(f"助战第{source_slot}格礼装最多支持首选及两个额外候选")
    if extra and primary is None:
        raise ValueError(f"助战第{source_slot}格礼装有候选但缺少有效首选")
    if extra:
        current = stored.get("id")
        raw_id = current if current is not None else item.get("ceId") if source_slot == 1 else None
        state = _equip_limit_break(item, source_slot)
        if type(raw_id) is not int or not 0 < raw_id <= 9007199254740991:
            raise ValueError(f"助战第{source_slot}格首选礼装 ID 必须是有效正整数")
        if state is not None and type(state) is not bool:
            raise ValueError(f"助战第{source_slot}格首选礼装满破状态必须是布尔值或未指定")
    choices = [primary] if primary is not None else []
    seen = {primary["id"]} if primary is not None else set()
    for index, candidate in enumerate(extra, 2):
        label = f"助战第{source_slot}格候选礼装{index}"
        if (not isinstance(candidate, dict) or type(candidate.get("id")) is not int
                or not 0 < candidate["id"] <= 9007199254740991):
            raise ValueError(f"{label}必须是包含有效正整数 ID 的对象")
        if "candidates" in candidate:
            raise ValueError(f"{label}不能嵌套候选列表")
        if candidate.get("limitBreak") is not None and type(candidate["limitBreak"]) is not bool:
            raise ValueError(f"{label}满破状态必须是布尔值或未指定")
        _level(candidate.get("lv"), f"{label}等级", 100)
        if candidate["id"] in seen:
            raise ValueError(f"助战第{source_slot}格候选礼装 ID 重复: {candidate['id']}")
        seen.add(candidate["id"])
        choices.append(_equip({f"equip{source_slot}": candidate}, source_slot,
                              resolve_name, target_slot))
    return {"slot": target_slot if target_slot is not None else source_slot,
            "source_slot": source_slot, "candidates": choices} if choices else None


def build_support_criteria(share_data, servant_map, resolve_name=get_equip_name):
    """返回现有 SupportAction 可使用的条件；不能可靠推断助战时失败。"""
    team = share_data.get("team") if isinstance(share_data, dict) else None
    if not isinstance(team, dict):
        raise ValueError("Chaldea 数据缺少 team")
    front, backup = team.get("onFieldSvts"), team.get("backupSvts")
    if not isinstance(front, list) or not isinstance(backup, list):
        raise ValueError("Chaldea 数据缺少有效的六槽队伍")
    members = (front[:3] + backup[:3])
    supports = [item for item in members if isinstance(item, dict)
                and str(item.get("supportType") or "").lower() in _SUPPORT_TYPES]
    if not supports:
        raise ValueError(
            "导入的队伍没有标记助战，无法确定要借用哪位从者。"
            "请在 Chaldea 或编队编辑器中，将准备借用的一位从者标记为助战，重新导出并导入；"
            "也可将助战方式改为“自定义助战”，在 MAA 中设置要借用的从者。"
        )
    if len(supports) > 1:
        raise ValueError(
            f"导入的队伍标记了 {len(supports)} 位助战，但每支队伍只能借用一位。"
            "请在 Chaldea 或编队编辑器中，只保留一位从者的助战标记，重新导出并导入。"
        )
    for member in members:
        if not isinstance(member, dict):
            continue
        for slot in range(1, 4):
            stored = member.get(f"equip{slot}")
            extra = stored.get("candidates") if isinstance(stored, dict) else None
            if extra is None:
                continue
            if not isinstance(extra, list) or len(extra) > 2:
                raise ValueError(f"第{slot}格礼装 candidates 必须是最多两项的数组")
            if not extra:
                continue
            if str(member.get("supportType") or "").lower() not in _SUPPORT_TYPES:
                raise ValueError("多候选礼装仅支持助战位置，自有从者不能忽略候选执行")
            allowed = (1, 3) if member.get("grandSvt") is True else (1,)
            if slot not in allowed:
                raise ValueError("候选礼装只支持普通助战主礼装及冠位助战的普通、追加礼装槽；羁绊礼装使用效果筛选")
    support = supports[0]
    raw_id = support.get("svtId")
    any_servant = (str(support.get("supportType") or "").lower() == "friend"
                   and (raw_id is None or type(raw_id) is int and raw_id == 0))
    if any_servant:
        servant = None
        class_name = "any"
    else:
        if isinstance(raw_id, bool):
            raise ValueError("Chaldea 助战缺少有效 svtId")
        try:
            servant_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Chaldea 助战缺少有效 svtId") from exc
        if servant_id <= 0:
            raise ValueError("Chaldea 助战缺少有效 svtId")
        servant = servant_map.get(str(servant_id))
        if not servant or not servant.get("images"):
            raise ValueError(f"助战从者 {servant_id} 缺少头像资源")
        servant_class = str(servant.get("class") or "").lower()
        class_name = _CLASS_TABS.get(servant_class)
        if not class_name:
            raise ValueError(f"助战从者 {servant_id} 的职介无法识别")
    grand = support.get("grandSvt") is True
    if grand:
        # Chaldea 冠位槽定义：equip1=普通礼装、equip2=羁绊礼装、
        # equip3=冠位追加礼装。MaaFGO 的两张卡面模板对应 equip1/equip3；
        # equip2 只通过羁绊效果图标判断，不匹配礼装卡面。
        groups = [
            _equip_group(support, 1, resolve_name, target_slot=1),
            _equip_group(support, 3, resolve_name, target_slot=2),
        ]
        # This is a class-board effect filter, independent of the bond CE's ID.
        # BBC may require NP50/original while leaving its bond CE unspecified.
        class_board = support.get("classBoardData")
        bond_changed = (class_board.get("grandBondEquipSkillChange")
                        if isinstance(class_board, dict) else None)
        ce_bond = ("50np" if bond_changed else "original") if isinstance(bond_changed, bool) else "any"
    else:
        groups = [_equip_group(support, 1, resolve_name)]
        ce_bond = "any"
    groups = [group for group in groups if group is not None]
    return {
        "support_type": "grand" if grand else "normal",
        "servant_id": "" if any_servant else str(servant["id"]),
        "class_name": class_name,
        "images": [] if any_servant else servant["images"],
        # Keep the existing first-choice view for legacy callers; the executor
        # consumes ce_groups so alternatives never become cross-cell ANDs.
        "ce_specs": [group["candidates"][0] for group in groups],
        "ce_groups": groups,
        "active": [0] * 3 if any_servant else _levels(support.get("skillLvs"), 3, "主动技能"),
        "passive": [0] * 5 if any_servant else _levels(support.get("appendLvs"), 5, "追加技能"),
        "np_level": 0 if any_servant else _level(support.get("tdLv"), "宝具等级", 5),
        "level": 0 if any_servant else _level(support.get("lv"), "从者等级", 130),
        "ce_bond": ce_bond,
    }
