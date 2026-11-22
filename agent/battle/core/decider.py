"""决策层（可插拔）。V1 为 RuleDecider；以后 LLMDecider 实现同一 Protocol 即可替换。

纯逻辑，无设备/无网络/无 maa 依赖，可离线单测。
V1b 策略：宝具卡优先出，剩余槽位用面卡按卡色枚举打分补齐，共 3 张。
owner_slot 用于将 Chaldea 攻略中的从者和卡色映射到当前卡面。

V2：支持 BattlePlan（固定回合计划）。有计划时：
  - MAIN_BATTLE：按 turn_index 取 TurnPlan，输出技能序列 + 换人
  - COMMAND_SELECTION：按攻略的面卡/宝具顺序匹配当前卡面，未指定部分按策略补齐
无计划时退回 V1b 行为。

显式启用 native_strategy 时，只枚举尚未指定的普通卡空位，对完整三张
组合评分。既定宝具/普通卡的位置不变，宝具颜色只接受显式配置。

RuleDecider 在确定出卡后比较同一从者同色面卡的暴击率。
"""
from __future__ import annotations

from itertools import permutations
from typing import List, Protocol, Tuple

from .enums import CardColor, PrimitiveKind, Scene
from .models import (BattleAction, BattlePlan, BattleState, CardPick,
                     CommandCard, ServantSkillAction, TurnPlan, is_slot)
from .policy import BattlePolicy, CardPolicy, Goal, NativeCardStrategy, SkillPolicy

import mfaalog

_GOAL_COLOR = {
    Goal.FINISH_WAVE: CardColor.BUSTER,
    Goal.BUILD_NP: CardColor.ARTS,
    Goal.BUILD_STARS: CardColor.QUICK,
}


def _uses_native_card_fill(policy: CardPolicy) -> bool:
    native = policy.native_strategy
    return (native.enabled or native.fill_priority == "servant"
            or bool(native.preferred_servant_ids or native.preferred_slots))


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
        native = self.card_policy.native_strategy
        if native.enabled:
            mfaalog.info(
                "[NativeCardStrategy] enabled: "
                f"brave={native.brave_chain_weight if native.prefer_brave_chain else 0} "
                f"mighty={native.mighty_chain_weight if self.card_policy.prefer_mighty_chain else 0} "
                f"same_color={native.same_color_chain_weight} "
                f"fill_priority={native.fill_priority} "
                f"preferred_ids={native.preferred_servant_ids} preferred_slots={native.preferred_slots}; "
                f"configured_np_colors={native.np_colors_by_servant_id}/{native.np_colors_by_slot}; "
                f"np_color_preservation={native.np_color_preservation_weight if native.avoid_np_color_before_np else 0}; "
                "组合评分使用已识别的卡色、归属及显式配置的宝具颜色"
            )

    @property
    def card_policy(self) -> CardPolicy:
        return self.policy.card

    @property
    def skill_policy(self) -> SkillPolicy:
        return self.policy.skill

    def decide(self, state: BattleState, turn_index: int = 0) -> BattleAction:
        target = self._pick_enemy_target(state, turn_index)

        if state.scene is Scene.MAIN_BATTLE:
            return self._decide_main_battle(state, turn_index, target)

        # 选卡阶段
        return self._decide_command_selection(state, turn_index, target)

    def _pick_enemy_target(self, state: BattleState, turn_index: int) -> int | None:
        # 目标取自攻略；未指定或目标已不在场时沿用既有选怪行为。
        tp = self.plan.active_turn(turn_index) if self.plan is not None else None
        if tp is not None:
            planned = tp.target_enemy
            if planned is not None and any(
                enemy.slot == planned and enemy.alive for enemy in state.enemies
            ):
                return planned
        return _pick_target(state)

    def _decide_main_battle(self, state: BattleState, turn_index: int,
                            target) -> BattleAction:
        tp = self.plan.active_turn(turn_index) if self.plan is not None else None
        if tp is None:
            # 无计划或计划已结束：按 SkillPolicy 自动决策可用的从者技能
            servant_skills = _auto_servant_skills(
                state, self.skill_policy, self.card_policy.native_strategy,
            )
            return BattleAction(
                target_enemy=target,
                picks=(),
                servant_skills=servant_skills,
                master_skills=(),
                order_change=None,
                rationale_tag="v2:main_battle_auto_skills"
            )

        # 敌人目标已按攻略确定。技能状态过滤由 Runtime 的统一安全门处理，
        # 保证 RuleDecider 与未来其他 Decider 使用同一套跳过策略。
        # 日志：输出 Chaldea 计划的本回合技能队列
        _log_plan_turn(turn_index, tp)

        return BattleAction(
            target_enemy=target,
            picks=(),
            servant_skills=tp.servant_skills,
            master_skills=tp.master_skills,
            order_change=tp.order_change,
            skill_sequence=tp.skill_sequence,
            rationale_tag=f"v2:turn{turn_index}_skills"
        )


    def _decide_command_selection(self, state: BattleState, turn_index: int,
                                  target) -> BattleAction:
        tp = self.plan.active_turn(turn_index) if self.plan is not None else None

        if tp is not None and tp.card_sequence:
            picks = _prefer_higher_crit(_resolve_planned_cards(tp, state, self.card_policy), state.cards)
            mfaalog.info(f"[ChaldeaPlan] 选卡阶段 回合{turn_index}: 攻略出卡={tp.card_sequence} 实际={picks}")
            return BattleAction(target_enemy=target, picks=picks,
                                rationale_tag=f"v2:turn{turn_index}_cards" + (
                                    ":native_strategy" if self.card_policy.native_strategy.enabled else ""))

        # ---- 宝具卡选择 ----
        np_picks: List[CardPick] = []

        if tp is not None and tp.np_order:
            # 固定计划优先按配置点击宝具，不依赖 NP OCR 是否识别成功。
            for slot in tp.np_order:
                if not any(card.servant_slot == slot and card.available for card in state.np_cards):
                    continue
                if slot not in {pick.slot for pick in np_picks} and len(np_picks) < 3:
                    np_picks.append(CardPick(PrimitiveKind.SELECT_NP, slot))
            mfaalog.info(f"[ChaldeaPlan] 选卡阶段 回合{turn_index}: 计划宝具顺序={tp.np_order} 实际np_picks={[p.slot for p in np_picks]}")
        elif tp is None and self.card_policy.np_first:
            # 无计划：有宝具就优先出
            np_picks = [CardPick(PrimitiveKind.SELECT_NP, c.servant_slot)
                        for c in state.np_cards if c.available][:3]

        # ---- 面卡补齐 ----
        need = 3 - len(np_picks)
        face_picks: List[CardPick] = []
        if _uses_native_card_fill(self.card_policy):
            picks = _complete_native_cards(list(np_picks), state, self.card_policy)
        elif need > 0 and state.cards:
            slots = _best_face_order(
                state.cards,
                min(need, len(state.cards)),
                self.card_policy,
            )
            face_picks = [CardPick(PrimitiveKind.SELECT_CARD, s) for s in slots]
            picks = tuple((np_picks + face_picks)[:3])
        else:
            picks = tuple(np_picks[:3])

        tag = f"v2:turn{turn_index}" if tp is not None else f"v2:{self.card_policy.goal.value}"
        if self.card_policy.native_strategy.enabled:
            tag += ":native_strategy"
        return BattleAction(
            target_enemy=target,
            picks=_prefer_higher_crit(picks, state.cards),
            rationale_tag=tag
        )


def _prefer_higher_crit(
    picks: Tuple[CardPick, ...], cards: Tuple[CommandCard, ...],
) -> Tuple[CardPick, ...]:
    """在既定出卡位置内，优先选同一从者、同色且可攻击的高暴击率面卡。"""
    positions = {pick.slot: index for index, pick in enumerate(picks)
                 if pick.kind is PrimitiveKind.SELECT_CARD}
    groups: dict[tuple[int, str | None, CardColor], list[CommandCard]] = {}
    for card in cards:
        if (card.can_attack and card.color is not CardColor.UNKNOWN
                and is_slot(card.owner_slot, 1, 3) and is_slot(card.crit_percent, 0, 100)):
            groups.setdefault((card.owner_slot, card.owner_servant_id, card.color), []).append(card)

    ordered = list(picks)
    for group in groups.values():
        if len(group) < 2:
            continue
        selected_positions = sorted(positions[card.ui_slot] for card in group if card.ui_slot in positions)
        # 同暴击率时优先保留既定牌，未识别到暴击率的出卡位置不参与替换。
        group.sort(key=lambda card: (-(card.crit_percent or 0), positions.get(card.ui_slot, len(picks))))
        for index, card in zip(selected_positions, group):
            ordered[index] = CardPick(PrimitiveKind.SELECT_CARD, card.ui_slot)
    return tuple(ordered)


def _resolve_planned_cards(tp: TurnPlan, state: BattleState, policy: CardPolicy) -> Tuple[CardPick, ...]:
    """将攻略的从者/卡色映射到此回合位置，保留宝具穿插在面卡间的顺序。"""
    picks: list[CardPick | None] = []
    used_faces: set[int] = set()
    used_np: set[int] = set()
    for request in tp.card_sequence[:3]:
        pick = None
        if request.kind is PrimitiveKind.SELECT_NP:
            if request.servant_slot not in used_np and any(
                card.servant_slot == request.servant_slot and card.available
                for card in state.np_cards
            ):
                pick = CardPick(PrimitiveKind.SELECT_NP, request.servant_slot)
                used_np.add(request.servant_slot)
        else:
            card = next((card for card in state.cards
                         if card.ui_slot not in used_faces and card.can_attack
                         and card.owner_slot == request.servant_slot
                         and card.color is request.color), None)
            if card is not None:
                pick = CardPick(PrimitiveKind.SELECT_CARD, card.ui_slot)
                used_faces.add(card.ui_slot)
        picks.append(pick)

    if _uses_native_card_fill(policy):
        return _complete_native_cards(picks, state, policy)

    remaining = tuple(card for card in state.cards if card.ui_slot not in used_faces)
    need = min(3 - len(used_faces) - len(used_np), len(remaining))
    fallback = iter(CardPick(PrimitiveKind.SELECT_CARD, slot) for slot in (_best_face_order(
        remaining, need, policy,
    ) if need else ()))
    picks = [pick if pick is not None else next(fallback, None) for pick in picks]
    picks.extend(fallback)
    return tuple(pick for pick in picks if pick is not None)[:3]


def _complete_native_cards(
    fixed: list[CardPick | None], state: BattleState, policy: CardPolicy,
) -> Tuple[CardPick, ...]:
    """保留明确出卡及其位置，只枚举剩余普通卡，最多 5P3=60 种。

    NP 是否释放仍由原来的固定计划/np_first 决定。颜色来自策略中显式
    提供的已核对资料；缺失时 NP 只参与归属明确的 Brave Chain。
    """
    template = fixed[:3] + [None] * max(0, 3 - len(fixed))
    reserved = {pick.slot for pick in template if pick and pick.kind is PrimitiveKind.SELECT_CARD}
    remaining = tuple(card for card in state.cards if card.ui_slot not in reserved)
    holes = [index for index, pick in enumerate(template) if pick is None]
    need = min(len(holes), len(remaining))

    def complete(faces: Tuple[CommandCard, ...]) -> Tuple[CardPick, ...]:
        picks = list(template)
        for index, card in zip(holes, faces):
            picks[index] = CardPick(PrimitiveKind.SELECT_CARD, card.ui_slot)
        return tuple(pick for pick in picks if pick is not None)

    preferred = _native_preferred_owners(state, policy)
    native = policy.native_strategy
    if need and native.fill_priority == "servant" and not (native.preferred_servant_ids or native.preferred_slots):
        if preferred[1]:
            mfaalog.info(f"[NativeCardStrategy] 自动主输出=当前前排{preferred[1][0]}（最靠前的已确认攻击宝具从者）")
        else:
            mfaalog.warning("当前前排没有已确认的攻击宝具从者，无法自动指定主输出。本次按连携策略补牌；可在「主输出偏好」指定前排位置或从者。")
    automatic_faces = frozenset(card.ui_slot for card in remaining)

    def score(picks):
        return _native_card_score(picks, state, policy, automatic_face_slots=automatic_faces,
                                  preferred_owners=preferred)
    best = max((complete(faces) for faces in permutations(remaining, need)), key=score)
    mfaalog.info(f"[NativeCardStrategy] 优先级={native.fill_priority} 固定位置={template} 补齐={best} "
                 f"评分={score(best)}")
    return best


def _native_preferred_owners(state: BattleState, policy: CardPolicy) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """An automatic target is one actual frontline instance, never every equal ID."""
    native = policy.native_strategy
    if native.preferred_servant_ids or native.preferred_slots or native.fill_priority != "servant":
        return native.preferred_servant_ids, native.preferred_slots
    attackers = [servant for servant in state.servants if is_slot(servant.slot, 1, 3) and servant.has_attack_np]
    return ((), (min(attackers, key=lambda servant: servant.slot).slot,)) if attackers else ((), ())


def _native_card_score(
    picks: Tuple[CardPick, ...], state: BattleState, policy: CardPolicy,
    *, automatic_face_slots: frozenset[int] | None = None,
    preferred_owners: tuple[tuple[str, ...], tuple[int, ...]] | None = None,
) -> tuple[int, int, float, float]:
    """Safety first, then lexicographic servant/chain objectives in selected order."""
    native = policy.native_strategy
    preferred_ids, preferred_slots = preferred_owners or _native_preferred_owners(state, policy)
    faces = {card.ui_slot: card for card in state.cards}
    automatic = tuple(faces[pick.slot] for pick in picks if pick.kind is PrimitiveKind.SELECT_CARD
                      and (automatic_face_slots is None or pick.slot in automatic_face_slots))

    def priority(value, ordered) -> float:
        return (len(ordered) - ordered.index(value)) / len(ordered) if value in ordered else 0.0

    owner_score = sum(max(priority(card.owner_servant_id, preferred_ids),
                          priority(card.owner_slot if is_slot(card.owner_slot, 1, 3) else None,
                                   preferred_slots))
                      for card in automatic if card.can_attack)
    if not native.enabled:
        # Disabling enhanced chains preserves the original ordinary-card score,
        # while an explicitly selected servant-first ordering still applies.
        attackable, known_colors, chain_score = _face_order_score(automatic, policy)
        return ((attackable, known_colors, owner_score, chain_score) if native.fill_priority == "servant"
                else (attackable, known_colors, chain_score, owner_score))
    weights = {c: len(policy.color_priority) - i for i, c in enumerate(policy.color_priority)}
    weights.update(native.color_weights)
    nps = {card.servant_slot: card for card in state.np_cards if card.available}
    np_id_colors = dict(native.np_colors_by_servant_id)
    np_slot_colors = dict(native.np_colors_by_slot)
    colors: list[CardColor | None] = []
    owners: list[int | None] = []
    identities: list[str | None] = []
    attackable = known_colors = 0
    for pick in picks:
        if pick.kind is PrimitiveKind.SELECT_CARD:
            card = faces[pick.slot]
            colors.append(card.color if card.color is not CardColor.UNKNOWN else None)
            owners.append(card.owner_slot if is_slot(card.owner_slot, 1, 3) else None)
            identities.append(card.owner_servant_id)
            attackable += int(card.can_attack)
            known_colors += int(card.color is not CardColor.UNKNOWN)
        else:
            card = nps[pick.slot]
            # Explicit ID metadata follows the recognized servant after swaps.
            # The slot map is only a user-provided fallback, never a guessed NP color.
            colors.append(np_id_colors.get(card.owner_servant_id,
                                           np_slot_colors.get(card.servant_slot)))
            owners.append(card.servant_slot)
            identities.append(card.owner_servant_id)

    n = len(picks)
    score = sum(weights.get(color, 0) * (n - pos) for pos, color in enumerate(colors))
    goal_color = _GOAL_COLOR.get(policy.goal)
    color_set = set(colors)
    if n == 3 and None not in color_set:
        if len(color_set) == 1:
            score += native.same_color_chain_weight
        elif color_set == {CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK} and policy.prefer_mighty_chain:
            score += native.mighty_chain_weight
            if colors[-1] is goal_color:
                score += 4.0
            first_for_goal = {CardColor.BUSTER: CardColor.QUICK,
                              CardColor.ARTS: CardColor.BUSTER,
                              CardColor.QUICK: CardColor.ARTS}.get(goal_color)
            if colors[0] is first_for_goal:
                score += 3.0
    if colors and goal_color is not None:
        score += 3.0 if colors[0] is goal_color else 0.0
        score += 2.0 if colors[-1] is goal_color else 0.0
    # Equal servant IDs can be two separate party members: Brave Chain needs
    # the same actual frontline instance (slot), never only equal IDs.
    if n == 3 and native.prefer_brave_chain and owners[0] is not None and len(set(owners)) == 1:
        score += native.brave_chain_weight

    if native.avoid_np_color_before_np:
        for np_index, pick in enumerate(picks):
            if (pick.kind is not PrimitiveKind.SELECT_NP or colors[np_index] is None
                    or identities[np_index] is None or not is_slot(owners[np_index], 1, 3)):
                continue
            # Matching IDs alone are insufficient when two party instances are
            # the same servant. Both the known identity and current owner slot
            # must match. Never infer an NP colour or an unknown owner.
            for face_index, before in enumerate(picks[:np_index]):
                if (before.kind is PrimitiveKind.SELECT_CARD
                        and faces[before.slot].can_attack
                        and colors[face_index] is colors[np_index]
                        and owners[face_index] == owners[np_index]
                        and identities[face_index] == identities[np_index]):
                    score -= native.np_color_preservation_weight
    return ((attackable, known_colors, owner_score, score) if native.fill_priority == "servant"
            else (attackable, known_colors, score, owner_score))


def _pick_target(state: BattleState):
    tgt = next((e.slot for e in state.enemies if e.alive and e.targeted), None)
    if tgt is None:
        tgt = next((e.slot for e in state.enemies if e.alive), None)
    return tgt


def _auto_servant_skills(
    state: BattleState, skill_policy: SkillPolicy, native: NativeCardStrategy,
) -> Tuple[ServantSkillAction, ...]:
    """无计划时按 SkillPolicy 自动决策可用的从者技能。

    规则：
    - auto_servant_skills=False → 不自动放技能
    - servant_slots 非空 → 只放指定从者的技能
    - skip_skill_indexes → 跳过指定技能索引
    - max_skills_per_turn > 0 → 每回合最多放 N 个技能
    - available is True → 加入技能序列；False（CD）或 None（未知）→ 跳过
    - 需要选择队友时优先匹配当前前排的主输出，未匹配时优先选最靠左的攻击宝具从者，再选最靠左的前排从者
    技能可用性过滤由 Runtime 的统一安全门（skip_unusable_servant_skills）兜底。
    """
    if not skill_policy.auto_servant_skills:
        return ()

    frontline = tuple(servant for servant in state.servants if is_slot(servant.slot, 1, 3))
    if not frontline:
        return ()
    # 微调同时列出从者和位置时，先匹配从者身份，再匹配位置。
    preferred_ids = {sid: rank for rank, sid in enumerate(native.preferred_servant_ids)}
    preferred_slots = {slot: rank for rank, slot in enumerate(native.preferred_slots)}
    target_ally = min(frontline, key=lambda servant: (
        preferred_ids.get(servant.servant_id, len(preferred_ids)),
        preferred_slots.get(servant.slot, len(preferred_slots)),
        not servant.has_attack_np,
        servant.slot,
    )).slot
    slots = set(skill_policy.servant_slots) if skill_policy.servant_slots else None
    skip = set(skill_policy.skip_skill_indexes)

    skills: List[ServantSkillAction] = []
    for servant in frontline:
        if slots is not None and servant.slot not in slots:
            continue
        for idx, skill_state in enumerate(servant.skills, start=1):
            if idx in skip:
                continue
            if skill_state.available is True:
                skills.append(ServantSkillAction(
                    servant_slot=servant.slot,
                    skill_index=idx,
                    target_ally=target_ally,
                ))
        if skill_policy.max_skills_per_turn > 0 and len(skills) >= skill_policy.max_skills_per_turn:
            break
    return tuple(skills[:skill_policy.max_skills_per_turn] if skill_policy.max_skills_per_turn > 0 else skills)


def _face_order_score(seq: Tuple[CommandCard, ...], policy: CardPolicy) -> tuple[int, int, float]:
    if not seq:
        return 0, 0, 0.0
    weights = {c: len(policy.color_priority) - i for i, c in enumerate(policy.color_priority)}
    goal_color = _GOAL_COLOR.get(policy.goal)

    s = 0.0
    n = len(seq)
    for pos, card in enumerate(seq):
        s += weights.get(card.color, 0) * (n - pos)      # 靠前的卡权重更高
    colors = {c.color for c in seq}
    if len(colors) == 1 and CardColor.UNKNOWN not in colors:  # 已识别的同色链
        s += 5.0
    elif colors == {CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK} and policy.prefer_mighty_chain:
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
    return (sum(card.can_attack for card in seq),
            sum(card.color is not CardColor.UNKNOWN for card in seq), s)

def _best_face_order(
    cards: Tuple[CommandCard, ...],
    need: int,
    policy: CardPolicy,
) -> List[int]:
    best = max(permutations(cards, need), key=lambda seq: _face_order_score(seq, policy))
    return [card.ui_slot for card in best]


def _log_plan_turn(turn_index: int, tp: TurnPlan) -> None:
    """输出 Chaldea 计划的本回合技能队列日志。"""
    if not tp:
        return

    parts = []
    for sk in tp.servant_skills:
        target = f"->从者{sk.target_ally}" if sk.target_ally else ""
        parts.append(f"从者{sk.servant_slot}技能{sk.skill_index}{target}")
    for sk in tp.master_skills:
        target = f"->从者{sk.target_ally}" if sk.target_ally else ""
        parts.append(f"御主技能{sk.skill_index}{target}")
    if tp.np_order:
        parts.append(f"宝具顺序={'→'.join(str(s) for s in tp.np_order)}")
    if tp.order_change:
        parts.append(f"换人: {tp.order_change.starting_member_idx}↔{tp.order_change.sub_member_idx}")
    if tp.target_enemy is not None:
        parts.append(f"目标敌人={tp.target_enemy}")

    if parts:
        mfaalog.info(f"[ChaldeaPlan] 回合{turn_index}: {' | '.join(parts)}")
    else:
        mfaalog.info(f"[ChaldeaPlan] 回合{turn_index}: 无技能/宝具（平A）")
