"""决策层（可插拔）。V1 为 RuleDecider；以后 LLMDecider 实现同一 Protocol 即可替换。

纯逻辑，无设备/无网络/无 maa 依赖，可离线单测。
V1b 策略：宝具卡优先出，剩余槽位用面卡按卡色枚举打分补齐，共 3 张。
owner_slot 在 V1 为 None，故暂不计 Brave / 从者优先级。

V2：支持 BattlePlan（固定回合计划）。有计划时：
  - MAIN_BATTLE：按 turn_index 取 TurnPlan，输出技能序列 + 换人
  - COMMAND_SELECTION：优先按 TurnPlan.np_order 选宝具，再按卡色补面卡
无计划时退回 V1b 行为。
"""
from __future__ import annotations

from itertools import permutations
from typing import List, Optional, Protocol, Tuple

from .enums import CardColor, PrimitiveKind, Scene
from .models import (BattleAction, BattlePlan, BattleState, CardPick,
                     CommandCard, ServantSkillAction, TurnPlan, is_slot)
from .policy import BattlePolicy, CardPolicy, Goal, SkillPolicy

_GOAL_COLOR = {
    Goal.FINISH_WAVE: CardColor.BUSTER,
    Goal.BUILD_NP: CardColor.ARTS,
    Goal.BUILD_STARS: CardColor.QUICK,
}


class Decider(Protocol):
    def decide(self, state: BattleState, turn_index: int = 0) -> BattleAction: ...


class RuleDecider:
    def __init__(self, policy: BattlePolicy | CardPolicy | None = None,
                 plan: BattlePlan | None = None) -> None:
        if isinstance(policy, BattlePolicy):
            self.policy = policy
        elif isinstance(policy, CardPolicy):
            self.policy = BattlePolicy(card=policy)
        else:
            self.policy = BattlePolicy()
        self.plan = plan

    @property
    def card_policy(self) -> CardPolicy:
        return self.policy.card

    @property
    def skill_policy(self) -> SkillPolicy:
        return self.policy.skill

    def decide(self, state: BattleState, turn_index: int = 0) -> BattleAction:
        target = _pick_target(state)

        if state.scene is Scene.MAIN_BATTLE:
            return self._decide_main_battle(state, turn_index, target)

        # 选卡阶段
        return self._decide_command_selection(state, turn_index, target)

    def _decide_main_battle(self, state: BattleState, turn_index: int,
                            target) -> BattleAction:
        if self.plan is None:
            # 无计划：按 SkillPolicy 自动决策可用的从者技能
            servant_skills = _auto_servant_skills(state, self.skill_policy)
            return BattleAction(
                target_enemy=target,
                picks=(),
                servant_skills=servant_skills,
                master_skills=(),
                order_change=None,
                rationale_tag="v2:main_battle_auto_skills"
            )

        tp = self.plan.turn(turn_index)
        # 计划中指定的敌人目标优先。技能状态过滤由 Runtime 的统一安全门处理，
        # 保证 RuleDecider 与未来其他 Decider 使用同一套跳过策略。
        enemy = tp.target_enemy if tp.target_enemy is not None else target
        return BattleAction(
            target_enemy=enemy,
            picks=(),
            servant_skills=tp.servant_skills,
            master_skills=tp.master_skills,
            order_change=tp.order_change,
            rationale_tag=f"v2:turn{turn_index}_skills"
        )


    def _decide_command_selection(self, state: BattleState, turn_index: int,
                                  target) -> BattleAction:
        tp: Optional[TurnPlan] = None
        if self.plan is not None:
            tp = self.plan.turn(turn_index)

        # ---- 宝具卡选择 ----
        np_picks: List[CardPick] = []

        if tp is not None and tp.np_order:
            # 固定计划优先按配置点击宝具，不依赖 NP OCR 是否识别成功。
            for slot in tp.np_order:
                if slot not in {pick.slot for pick in np_picks} and len(np_picks) < 3:
                    np_picks.append(CardPick(PrimitiveKind.SELECT_NP, slot))
        elif self.card_policy.np_first:
            # 无计划：有宝具就优先出
            np_picks = [CardPick(PrimitiveKind.SELECT_NP, c.servant_slot)
                        for c in state.np_cards][:3]

        # ---- 面卡补齐 ----
        need = 3 - len(np_picks)
        face_picks: List[CardPick] = []
        if need > 0 and state.cards:
            slots = _best_face_order(state.cards, min(need, len(state.cards)), self.card_policy)
            face_picks = [CardPick(PrimitiveKind.SELECT_CARD, s) for s in slots]

        picks = tuple((np_picks + face_picks)[:3])

        # 计划中指定的敌人目标优先
        enemy = tp.target_enemy if (tp is not None and tp.target_enemy is not None) else target

        tag = f"v2:turn{turn_index}" if tp is not None else f"v2:{self.card_policy.goal.value}"
        return BattleAction(
            target_enemy=enemy,
            picks=picks,
            rationale_tag=tag
        )


def _pick_target(state: BattleState):
    tgt = next((e.slot for e in state.enemies if e.alive and e.targeted), None)
    if tgt is None:
        tgt = next((e.slot for e in state.enemies if e.alive), None)
    return tgt


def _auto_servant_skills(state: BattleState, skill_policy: SkillPolicy) -> Tuple[ServantSkillAction, ...]:
    """无计划时按 SkillPolicy 自动决策可用的从者技能。

    规则：
    - auto_servant_skills=False → 不自动放技能
    - servant_slots 非空 → 只放指定从者的技能
    - skip_skill_indexes → 跳过指定技能索引
    - max_skills_per_turn > 0 → 每回合最多放 N 个技能
    - available is True → 加入技能序列；False（CD）或 None（未知）→ 跳过
    技能可用性过滤由 Runtime 的统一安全门（skip_unusable_servant_skills）兜底。
    """
    if not skill_policy.auto_servant_skills:
        return ()

    slots = set(skill_policy.servant_slots) if skill_policy.servant_slots else None
    skip = set(skill_policy.skip_skill_indexes)

    skills: List[ServantSkillAction] = []
    for servant in state.servants:
        if not is_slot(servant.slot, 1, 3):
            continue
        if slots is not None and servant.slot not in slots:
            continue
        for idx, skill_state in enumerate(servant.skills, start=1):
            if idx in skip:
                continue
            if skill_state.available is True:
                skills.append(ServantSkillAction(
                    servant_slot=servant.slot,
                    skill_index=idx,
                ))
        if skill_policy.max_skills_per_turn > 0 and len(skills) >= skill_policy.max_skills_per_turn:
            break
    return tuple(skills[:skill_policy.max_skills_per_turn] if skill_policy.max_skills_per_turn > 0 else skills)


def _best_face_order(cards: Tuple[CommandCard, ...], need: int, policy: CardPolicy) -> List[int]:
    weights = {c: len(policy.color_priority) - i for i, c in enumerate(policy.color_priority)}
    goal_color = _GOAL_COLOR.get(policy.goal)

    def score(seq: Tuple[CommandCard, ...]) -> float:
        s = 0.0
        n = len(seq)
        for pos, card in enumerate(seq):
            s += weights.get(card.color, 0) * (n - pos)      # 靠前的卡权重更高
        colors = {c.color for c in seq}
        if len(colors) == 1:                                 # 同色链
            s += 5.0
        elif len(colors) == 3 and policy.prefer_mighty_chain:
            # 三色连锁（红蓝绿各一张）：全卡享受三种首卡染色加成，
            # 顺序偏好取决于目标：
            #   FINISH_WAVE → QAB（绿垫刀，红末位吃最高倍率）
            #   BUILD_NP    → BQA（红首位压血线，蓝末位吃140%）
            #   BUILD_STARS → ABQ（蓝首位，绿末位吃112%）
            s += 8.0
            if goal_color is not None:
                # 目标色卡放末位（吃最高位置加成）
                if seq[-1].color is goal_color:
                    s += 4.0
                # 根据目标推荐首位
                if goal_color is CardColor.BUSTER:
                    # FINISH_WAVE: 绿卡首位垫刀
                    if seq[0].color is CardColor.QUICK:
                        s += 3.0
                elif goal_color is CardColor.ARTS:
                    # BUILD_NP: 红卡首位压血线
                    if seq[0].color is CardColor.BUSTER:
                        s += 3.0
                elif goal_color is CardColor.QUICK:
                    # BUILD_STARS: 蓝卡首位
                    if seq[0].color is CardColor.ARTS:
                        s += 3.0
        if goal_color is not None:
            # 通用：首卡/末卡目标色加分（同色链、三色链、杂色均适用）
            if seq[0].color is goal_color:
                s += 3.0
            if seq[-1].color is goal_color:
                s += 2.0
        return s

    best = max(permutations(cards, need), key=score)
    return [c.ui_slot for c in best]
