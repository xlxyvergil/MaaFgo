"""Chaldea BattleShareData actions -> auto-battle BattlePlan 转换器。

把 Chaldea 回合动作（与 BBC 转换共用同一份 actions 数组）转成
`battle.core` 的 `BattlePlan`，让原生自动战斗能按 Chaldea 攻略的顺序
执行技能 / 面卡与宝具 / 换人。

Chaldea actions 数据结构（与 bbc_formatter.convert_actions_to_bbc_rounds 同源）：
- {"type":"skill","svt":0..5|None,"skill":0..2,"options":{"playerTarget":N}}
    svt=None 表示御主技能；svt=0..2 对应前排从者槽位 1..3。
- {"type":"attack","attacks":[{"isTD":bool,"svt":0..5}, ...]}
    一次攻击组：把此前的技能累积成"一个回合"；attacks 里的 isTD 条目是宝具。

约束（诚实边界）：
- attacks 按原顺序保留从者、卡色和宝具；card 是从者牌组索引，不能当作屏幕位置。
- 同一从者的同色面卡按当前画面从左到右选择；未指定的卡由普通选卡策略补齐。
- 换人：由换人礼装（mystic_code_id 在换人服集合内）的御主技能 3 触发，
  具体首发/候补槽位取 delegate.replaceMemberIndexes。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import mfaalog

from ..core.enums import CardColor, PrimitiveKind
from ..core.models import (BattlePlan, MasterSkillAction, OrderChangeAction,
                           PlannedCard, ServantSkillAction, TurnPlan)

# 换人服 mysticCodeId（与 agent/chaldea/servant_types.py 保持一致）
_ORDER_CHANGE_MYSTIC_CODE_IDS = {20, 210, 440}


def _is_order_change_mystic_code(mystic_code_id: Optional[int]) -> bool:
    """是否是换人礼装。"""
    return (
        isinstance(mystic_code_id, int)
        and mystic_code_id in _ORDER_CHANGE_MYSTIC_CODE_IDS
    )


def _parse_target(options: Optional[dict]) -> Optional[int]:
    """从 options 解析技能目标（playerTarget, 0-based -> 1-based）。"""
    if not isinstance(options, dict):
        return None
    target = options.get("playerTarget")
    if isinstance(target, int) and 0 <= target <= 5:
        return target + 1
    return None


def _normalize_replace_member(item: object) -> Optional[Tuple[int, int]]:
    """把换人索引统一为战斗界面的 1-based 槽位。

    Chaldea 分享数据把首发与候补分别编号，因此候补索引为 0..2；
    项目内 BBC 导入的兼容数据则沿用整队 0-based 槽位 3..5。
    """
    if not (
        isinstance(item, (list, tuple))
        and len(item) >= 2
        and type(item[0]) is int
        and type(item[1]) is int
    ):
        return None

    front, back = item[0], item[1]
    if not 0 <= front <= 2:
        return None
    if 0 <= back <= 2:
        sub_member_idx = back + 4
    elif 3 <= back <= 5:
        sub_member_idx = back + 1
    else:
        return None
    return front + 1, sub_member_idx


def convert_chaldea_actions_to_battle_plan(
    actions: List[dict],
    delegate: Optional[dict] = None,
    mystic_code_id: Optional[int] = None,
) -> BattlePlan:
    """把 Chaldea actions 转成 BattlePlan。

    每个 ``attack`` 动作把之前累积的技能/宝具结算成 1 个 TurnPlan；
    收到新的 ``skill`` 动作则累积到下一个回合。末尾残留的未结算技能
    会补成最后一个回合。

    Returns:
        永远返回非空 BattlePlan；无任何回合时返回 1 个空回合。
    """
    if not isinstance(actions, list):
        mfaalog.warning(f"[chaldea_converter] actions 不是列表, 类型={type(actions).__name__}")
        return BattlePlan(turns=(TurnPlan(),))

    mfaalog.info(f"[chaldea_converter] 开始转换: actions={len(actions)}条, "
                 f"mystic_code_id={mystic_code_id}, delegate={'有' if delegate else '无'}")

    # 换人信息统一为战斗界面的实际槽位：首发 1..3，候补 4..6。
    replace_members: List[Tuple[int, int]] = []
    if isinstance(delegate, dict):
        raw = delegate.get("replaceMemberIndexes")
        if isinstance(raw, list):
            for item in raw:
                normalized = _normalize_replace_member(item)
                if normalized is not None:
                    replace_members.append(normalized)
                else:
                    mfaalog.warning(
                        f"[chaldea_converter] 忽略无效换人槽位: {item!r}"
                    )
    replace_ptr = 0

    is_order_change = _is_order_change_mystic_code(mystic_code_id)

    turns: List[TurnPlan] = []
    cur_skills: List[ServantSkillAction] = []
    cur_masters: List[MasterSkillAction] = []
    cur_np: List[int] = []
    cur_cards: List[PlannedCard] = []
    cur_enemy: Optional[int] = None
    cur_order_change: Optional[OrderChangeAction] = None
    cur_sequence: list[ServantSkillAction | MasterSkillAction | OrderChangeAction] = []

    def _flush() -> None:
        """把当前累积的回合内容结算成 TurnPlan。"""
        nonlocal cur_skills, cur_masters, cur_np, cur_order_change, cur_sequence, cur_cards, cur_enemy
        turn = TurnPlan(
            servant_skills=tuple(cur_skills),
            master_skills=tuple(cur_masters),
            np_order=tuple(cur_np),
            order_change=cur_order_change,
            skill_sequence=tuple(cur_sequence),
            card_sequence=tuple(cur_cards),
            target_enemy=cur_enemy,
        )
        mfaalog.info(f"[chaldea_converter] 回合{turns.__len__() + 1}: "
                     f"servant_skills={len(cur_skills)} master_skills={len(cur_masters)} "
                     f"np_order={cur_np} cards={cur_cards} target_enemy={cur_enemy} "
                     f"order_change={'有' if cur_order_change else '无'}")
        turns.append(turn)
        cur_skills = []
        cur_masters = []
        cur_np = []
        cur_cards = []
        cur_enemy = None
        cur_order_change = None
        cur_sequence = []

    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = action.get("type", "")

        if action_type == "skill":
            skill_idx = action.get("skill")
            if not isinstance(skill_idx, int) or not (0 <= skill_idx <= 2):
                continue
            target = _parse_target(action.get("options"))

            svt_idx = action.get("svt")
            if svt_idx is None:
                # 御主技能
                if skill_idx == 2 and is_order_change and replace_ptr < len(replace_members):
                    # 换人服第 3 技能 = 换人（Chaldea 0-based: skill=2）
                    front_slot, sub_slot = replace_members[replace_ptr]
                    replace_ptr += 1
                    cur_order_change = OrderChangeAction(
                        starting_member_idx=front_slot,
                        sub_member_idx=sub_slot,
                    )
                    master = MasterSkillAction(skill_idx + 1)
                    cur_masters.append(master)
                    cur_sequence.extend((master, cur_order_change))
                else:
                    master = MasterSkillAction(skill_idx + 1, target_ally=target)
                    cur_masters.append(master)
                    cur_sequence.append(master)
            elif isinstance(svt_idx, int) and 0 <= svt_idx <= 2:
                servant = ServantSkillAction(
                    servant_slot=svt_idx + 1,
                    skill_index=skill_idx + 1,
                    target_ally=target,
                )
                cur_skills.append(servant)
                cur_sequence.append(servant)

        elif action_type == "attack":
            options = action.get("options") or {}
            enemy = options.get("enemyTarget")
            if type(enemy) is int and 0 <= enemy <= 2:
                # Chaldea 敌方索引从右往左，原生 ROI 则从左往右编号。
                cur_enemy = 3 - enemy
            attacks = action.get("attacks")
            if isinstance(attacks, list):
                for atk in attacks:
                    if not isinstance(atk, dict):
                        continue
                    svt_pos = atk.get("svt")
                    if type(svt_pos) is not int or not 0 <= svt_pos <= 2:
                        continue
                    if atk.get("isTD", False):
                        np_slot = svt_pos + 1
                        if np_slot not in cur_np:
                            cur_np.append(np_slot)
                            cur_cards.append(PlannedCard(PrimitiveKind.SELECT_NP, np_slot))
                    else:
                        color = {"buster": CardColor.BUSTER, "arts": CardColor.ARTS,
                                 "quick": CardColor.QUICK}.get(atk.get("cardType"))
                        if color is not None:
                            cur_cards.append(PlannedCard(PrimitiveKind.SELECT_CARD, svt_pos + 1, color))
            _flush()

    # 末尾未结算的技能 -> 补最后一回合
    if cur_skills or cur_masters or cur_np or cur_order_change is not None:
        _flush()

    if not turns:
        turns.append(TurnPlan())

    return BattlePlan(turns=tuple(turns))
