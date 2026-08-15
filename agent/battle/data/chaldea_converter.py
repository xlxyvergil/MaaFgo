"""Chaldea BattleShareData actions -> auto-battle BattlePlan 转换器。

把 Chaldea 回合动作（与 BBC 转换共用同一份 actions 数组）转成
`battle.core` 的 `BattlePlan`，让原生自动战斗能按 Chaldea 攻略的顺序
执行技能 / 宝具 / 换人。

Chaldea actions 数据结构（与 bbc_formatter.convert_actions_to_bbc_rounds 同源）：
- {"type":"skill","svt":0..5|None,"skill":1..3,"options":{"playerTarget":N}}
    svt=None 表示御主技能；svt=0..2 对应前排从者槽位 1..3。
- {"type":"attack","attacks":[{"isTD":bool,"svt":0..5}, ...]}
    一次攻击组：把此前的技能累积成"一个回合"；attacks 里的 isTD 条目是宝具。

约束（诚实边界）：
- Chaldea actions **不提供显式面卡顺序**（只有宝具 isTD + 技能），
  因此 3 张面卡的选择交给 RuleDecider 用现有策略（宝具优先 + 卡色打分）补齐。
- 换人：由换人礼装（mystic_code_id 在换人服集合内）的御主技能 3 触发，
  具体首发/候补槽位取 delegate.replaceMemberIndexes。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..core.models import (BattlePlan, MasterSkillAction, OrderChangeAction,
                           ServantSkillAction, TurnPlan)

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
        return BattlePlan(turns=(TurnPlan(),))

    # 换人信息：delegate.replaceMemberIndexes -> [(front,back), ...]（0-based）
    replace_members: List[List[int]] = []
    if isinstance(delegate, dict):
        raw = delegate.get("replaceMemberIndexes")
        if isinstance(raw, list):
            for item in raw:
                if (
                    isinstance(item, (list, tuple))
                    and len(item) >= 2
                    and isinstance(item[0], int)
                    and isinstance(item[1], int)
                ):
                    replace_members.append([item[0], item[1]])
    replace_ptr = 0

    is_order_change = _is_order_change_mystic_code(mystic_code_id)

    turns: List[TurnPlan] = []
    cur_skills: List[ServantSkillAction] = []
    cur_masters: List[MasterSkillAction] = []
    cur_np: List[int] = []
    cur_order_change: Optional[OrderChangeAction] = None

    def _flush() -> None:
        """把当前累积的回合内容结算成 TurnPlan。"""
        nonlocal cur_skills, cur_masters, cur_np, cur_order_change
        turns.append(TurnPlan(
            servant_skills=tuple(cur_skills),
            master_skills=tuple(cur_masters),
            np_order=tuple(cur_np),
            order_change=cur_order_change,
        ))
        cur_skills = []
        cur_masters = []
        cur_np = []
        cur_order_change = None

    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = action.get("type", "")

        if action_type == "skill":
            skill_idx = action.get("skill")
            if not isinstance(skill_idx, int) or not (1 <= skill_idx <= 3):
                continue
            target = _parse_target(action.get("options"))

            svt_idx = action.get("svt")
            if svt_idx is None:
                # 御主技能
                if skill_idx == 3 and is_order_change and replace_ptr < len(replace_members):
                    # 换人服第 3 技能 = 换人
                    front, back = replace_members[replace_ptr]
                    replace_ptr += 1
                    cur_order_change = OrderChangeAction(
                        starting_member_idx=front + 1,
                        sub_member_idx=back + 1,
                    )
                else:
                    cur_masters.append(MasterSkillAction(skill_idx, target_ally=target))
            elif isinstance(svt_idx, int) and 0 <= svt_idx <= 2:
                cur_skills.append(ServantSkillAction(
                    servant_slot=svt_idx + 1,
                    skill_index=skill_idx,
                    target_ally=target,
                ))

        elif action_type == "attack":
            attacks = action.get("attacks")
            if isinstance(attacks, list):
                for atk in attacks:
                    if not isinstance(atk, dict):
                        continue
                    if atk.get("isTD", False):
                        svt_pos = atk.get("svt")
                        if isinstance(svt_pos, int) and 0 <= svt_pos <= 2:
                            np_slot = svt_pos + 1
                            if np_slot not in cur_np:
                                cur_np.append(np_slot)
            _flush()

    # 末尾未结算的技能 -> 补最后一回合
    if cur_skills or cur_masters or cur_np or cur_order_change is not None:
        _flush()

    if not turns:
        turns.append(TurnPlan())

    return BattlePlan(turns=tuple(turns))
