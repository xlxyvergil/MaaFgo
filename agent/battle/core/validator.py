"""战斗动作的独立安全校验。纯 stdlib，不依赖设备或 MaaFramework。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

from .enums import PrimitiveKind, Scene
from .models import (BattleAction, BattleState, OrderChangeAction,
                     ServantSkillAction, is_slot)
from .policy import StrategyProfile


_ALLOWED_PICK_KINDS = {PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_NP}


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""
    fatal: bool = True


def skip_unusable_servant_skills(
    action: BattleAction,
    state: BattleState,
    profile: StrategyProfile,
) -> tuple[BattleAction, tuple[str, ...]]:
    """跳过无法确认可执行的从者技能，保留结构非法动作给 Validator 拒绝。"""
    ordered_swap = (
        any(isinstance(step, OrderChangeAction) for step in action.skill_sequence)
        and sum(isinstance(step, ServantSkillAction) for step in action.skill_sequence)
        == len(action.servant_skills)
    )
    seen: set[tuple[int, int]] = set()
    for skill in action.servant_skills:
        if (
            not is_slot(skill.servant_slot, 1, 3)
            or not is_slot(skill.skill_index, 1, 3)
            or (skill.target_ally is not None and not is_slot(skill.target_ally, 1, 3))
        ):
            return action, ()
        key = (skill.servant_slot, skill.skill_index)
        if key in seen and not ordered_swap:
            return action, ()
        seen.add(key)

    servants = {servant.slot: servant for servant in state.servants}
    if ordered_swap:
        kept = []
        sequence = []
        skipped = []
        after_swap = False
        for step in action.skill_sequence:
            if isinstance(step, OrderChangeAction):
                after_swap = True
            if isinstance(step, ServantSkillAction) and not after_swap:
                field = f"servant[{step.servant_slot}].skill[{step.skill_index}].available"
                servant = servants.get(step.servant_slot)
                skill_state = servant.skills[step.skill_index - 1] if servant else None
                reason = (
                    "state_missing" if skill_state is None else
                    "unknown" if skill_state.available is None else
                    "low_confidence" if not skill_state.confidence.passes(profile.min_skill_confidence) else
                    "cooldown" if skill_state.available is False else None
                )
                if reason:
                    skipped.append(f"{field}:{reason}")
                    continue
            if isinstance(step, ServantSkillAction):
                kept.append(step)
            sequence.append(step)
        if not skipped:
            return action, ()
        return replace(action, servant_skills=tuple(kept),
                       skill_sequence=tuple(sequence)), tuple(skipped)

    kept = []
    skipped: list[str] = []
    for skill in action.servant_skills:
        field = f"servant[{skill.servant_slot}].skill[{skill.skill_index}].available"
        servant = servants.get(skill.servant_slot)
        if servant is None:
            skipped.append(f"{field}:state_missing")
            continue

        skill_state = servant.skills[skill.skill_index - 1]
        if skill_state.available is None:
            skipped.append(f"{field}:unknown")
            continue
        if not skill_state.confidence.passes(profile.min_skill_confidence):
            skipped.append(f"{field}:low_confidence")
            continue
        if skill_state.available is False:
            skipped.append(f"{field}:cooldown")
            continue
        kept.append(skill)

    if len(kept) == len(action.servant_skills):
        return action, ()
    counts = Counter(kept)
    sequence = []
    for step in action.skill_sequence:
        if isinstance(step, ServantSkillAction):
            if counts[step] == 0:
                continue
            counts[step] -= 1
        sequence.append(step)
    return replace(action, servant_skills=tuple(kept),
                   skill_sequence=tuple(sequence)), tuple(skipped)


def skip_unusable_master_skills(
    action: BattleAction,
    state: BattleState,
    profile: StrategyProfile,
) -> tuple[BattleAction, tuple[str, ...]]:
    """跳过无法确认可执行的御主技能，保留结构非法动作给 Validator 拒绝。

    与 skip_unusable_servant_skills 对称：御主技能在冷却/未知/低置信度时
    从 action.master_skills 中剔除，避免盲放冷却中的技能。

    换人联动：若 action.order_change 依赖的御主换人技能被过滤掉（master_skills
    过滤后为空），则同时移除 order_change——换人无法触发时跳过换人，而不是
    让 validate_main_action 以 order_change_without_master_skill 中止整场战斗。
    """
    seen: set[int] = set()
    for skill in action.master_skills:
        if (
            not is_slot(skill.skill_index, 1, 3)
            or (skill.target_ally is not None and not is_slot(skill.target_ally, 1, 3))
        ):
            return action, ()
        if skill.skill_index in seen:
            return action, ()
        seen.add(skill.skill_index)

    master_states = {idx + 1: st for idx, st in enumerate(state.master_skills)}
    kept = []
    skipped: list[str] = []
    for skill in action.master_skills:
        field = f"master_skill[{skill.skill_index}].available"
        skill_state = master_states.get(skill.skill_index)
        if skill_state is None:
            skipped.append(f"{field}:state_missing")
            continue
        if skill_state.available is None:
            skipped.append(f"{field}:unknown")
            continue
        if not skill_state.confidence.passes(profile.min_skill_confidence):
            skipped.append(f"{field}:low_confidence")
            continue
        if skill_state.available is False:
            skipped.append(f"{field}:cooldown")
            continue
        kept.append(skill)

    if len(kept) == len(action.master_skills):
        return action, ()

    new_action = replace(action, master_skills=tuple(kept))
    # 换人技能被过滤 → 换人无法触发，一并跳过 order_change
    if action.order_change is not None and not kept:
        new_action = replace(new_action, order_change=None)
        skipped.append("order_change:master_skill_unavailable")
    return new_action, tuple(skipped)


def validate_main_action(
    action: BattleAction,
    state: BattleState,
    profile: StrategyProfile,
) -> Verdict:
    """校验 MAIN_BATTLE 阶段的选敌、技能与换人动作。"""
    if state.scene is not Scene.MAIN_BATTLE or not state.scene_confidence.passes(
        profile.min_scene_confidence
    ):
        return Verdict(False, "scene_not_main_battle", fatal=False)
    if action.picks:
        return Verdict(False, "main_action_contains_card_picks")

    target_verdict = _validate_enemy_target(action, state, profile)
    if not target_verdict.ok:
        return target_verdict

    servants = {servant.slot: servant for servant in state.servants}
    post_swap_flags = []
    after_swap = False
    for step in action.skill_sequence:
        if isinstance(step, OrderChangeAction):
            after_swap = True
        elif isinstance(step, ServantSkillAction):
            post_swap_flags.append(after_swap)
    if len(post_swap_flags) != len(action.servant_skills):
        post_swap_flags = [False] * len(action.servant_skills)
    for index, skill in enumerate(action.servant_skills):
        if not is_slot(skill.servant_slot, 1, 3) or not is_slot(
            skill.skill_index, 1, 3
        ):
            return Verdict(False, "invalid_servant_skill")
        # 不校验同回合重复技能: 减CD策略/部分英灵技能允许同一技能一回合多次释放
        if skill.target_ally is not None and not is_slot(skill.target_ally, 1, 3):
            return Verdict(False, "invalid_skill_target")

        # 换人后该位置属于新从者；当前帧里仍是换人前的技能状态。
        if post_swap_flags[index]:
            continue

        servant = servants.get(skill.servant_slot)
        if servant is None:
            return Verdict(False, "servant_state_missing", fatal=False)
        skill_state = servant.skills[skill.skill_index - 1]
        if skill_state.available is None:
            return Verdict(False, "skill_state_unknown", fatal=False)
        if not skill_state.confidence.passes(profile.min_skill_confidence):
            return Verdict(False, "skill_state_not_confident", fatal=False)
        if skill_state.available is False:
            return Verdict(False, "skill_not_available", fatal=False)

    seen_master_skills: set[int] = set()
    master_states = {idx + 1: st for idx, st in enumerate(state.master_skills)}
    for skill in action.master_skills:
        if not is_slot(skill.skill_index, 1, 3):
            return Verdict(False, "invalid_master_skill")
        if skill.skill_index in seen_master_skills:
            return Verdict(False, "duplicate_master_skill")
        seen_master_skills.add(skill.skill_index)
        if skill.target_ally is not None and not is_slot(skill.target_ally, 1, 3):
            return Verdict(False, "invalid_skill_target")

        skill_state = master_states.get(skill.skill_index)
        if skill_state is None:
            return Verdict(False, "master_skill_state_missing")
        if skill_state.available is None:
            return Verdict(False, "master_skill_state_unknown")
        if not skill_state.confidence.passes(profile.min_skill_confidence):
            return Verdict(False, "master_skill_state_not_confident")
        if skill_state.available is False:
            return Verdict(False, "master_skill_not_available")

    if action.order_change is not None:
        order_change = action.order_change
        if not action.master_skills:
            return Verdict(False, "order_change_without_master_skill")
        if not is_slot(order_change.starting_member_idx, 1, 3) or not is_slot(
            order_change.sub_member_idx, 4, 6
        ):
            return Verdict(False, "invalid_order_change_member")

    return Verdict(True)


def validate_card_action(
    action: BattleAction,
    state: BattleState,
    profile: StrategyProfile,
) -> Verdict:
    """校验 COMMAND_SELECTION 阶段的三张卡及目标。"""
    if not state.command_ready(profile.min_scene_confidence):
        return Verdict(False, "scene_not_command_selection", fatal=False)
    if not state.cards_ready(profile.min_card_confidence):
        return Verdict(False, "cards_not_confident", fatal=False)
    if action.servant_skills or action.master_skills or action.order_change is not None:
        return Verdict(False, "card_action_contains_main_actions")

    if len(action.picks) != 3:
        return Verdict(False, "need_exactly_3_picks")
    if len({(pick.kind, pick.slot) for pick in action.picks}) != 3:
        return Verdict(False, "duplicate_picks")

    face_slots = {card.ui_slot for card in state.cards}
    np_slots = {card.servant_slot for card in state.np_cards if card.available}
    for pick in action.picks:
        if pick.kind not in _ALLOWED_PICK_KINDS:
            return Verdict(False, f"forbidden_pick_kind:{pick.kind.value}")
        if pick.kind is PrimitiveKind.SELECT_CARD and pick.slot not in face_slots:
            return Verdict(False, "card_not_present", fatal=not is_slot(pick.slot, 1, 5))
        if pick.kind is PrimitiveKind.SELECT_NP and pick.slot not in np_slots:
            return Verdict(False, "np_not_present", fatal=not is_slot(pick.slot, 1, 3))

    target_verdict = _validate_enemy_target(action, state, profile)
    if not target_verdict.ok:
        return target_verdict
    return Verdict(True)


def validate(
    action: BattleAction,
    state: BattleState,
    profile: StrategyProfile,
) -> Verdict:
    """兼容旧调用；选卡阶段请逐步改用 validate_card_action。"""
    return validate_card_action(action, state, profile)


def _validate_enemy_target(
    action: BattleAction,
    state: BattleState,
    profile: StrategyProfile,
) -> Verdict:
    if action.target_enemy is None:
        return Verdict(True)
    if not is_slot(action.target_enemy, 1, 3):
        return Verdict(False, "invalid_enemy_target")
    enemy = next(
        (
            candidate
            for candidate in state.enemies
            if candidate.slot == action.target_enemy and candidate.alive
        ),
        None,
    )
    if enemy is None:
        return Verdict(False, "invalid_enemy_target", fatal=False)
    if not enemy.confidence.passes(profile.min_enemy_confidence):
        return Verdict(False, "enemy_target_not_confident", fatal=False)
    return Verdict(True)
