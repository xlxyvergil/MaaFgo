"""Pure card candidates for the November 2026 Hellish Trial, explicitly enabled.

The dedicated Trial runtime calls this policy only when both the task profile
and the imported source explicitly enable it. Other battle tasks never call it.
The caller supplies the *observed* HP-bar phase and confirmed attack progress;
this module does not read enemy class/HP, detect Break, infer Scorch, execute a
skill, or submit a card. It must not be used as a generic automatic card policy.

Attack NPs are withheld until the chosen three cards can complete the remaining
colour objective. Their first damage can Break the bar and subsequent cards can
still hit that same bar's Overgauge; no damage spill across bars is predicted.
Only an explicit prefix permits Medea's preparation NP or Britomart's Quick NP
after an Alter Ego NP in phase 3. Unknown card ownership is never filled from
slot position, and Castoria's support NP never counts as an Arts *attack*.

Colour counts are projections until ``confirm_submission`` is called by an
external observer after the actual attacks were confirmed. An observed phase
change must be supplied separately via ``TrialProgress.begin``; proposing cards
does not change progress. This intentionally has no Runtime/Decider side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from itertools import permutations
from typing import Sequence

from ..core.enums import CardColor, PrimitiveKind, Scene
from ..core.models import BattleState, CardPick


class NpRole(str, Enum):
    LANCER = "lancer_finisher"
    ALTEREGO = "alterego_finisher"
    CLEANSE = "support_cleanse"
    PREPARATION = "explicit_preparation"


@dataclass(frozen=True)
class NpInfo:
    """Verified NP facts supplied as data, never inferred from screen position."""

    servant_id: str
    color: CardColor
    role: NpRole
    damaging: bool

    def __post_init__(self):
        if _identity(self.servant_id) != self.servant_id:
            raise ValueError("宝具资料需要规范的数字从者ID字符串")
        if self.color not in (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK):
            raise ValueError("宝具资料需要已确认的红、蓝或绿颜色")
        if not isinstance(self.role, NpRole) or type(self.damaging) is not bool:
            raise ValueError("宝具资料需要明确的用途和是否攻击")
        if self.damaging == (self.role is NpRole.CLEANSE):
            raise ValueError("解弱化辅助宝具不能标记为攻击，攻击宝具必须明确标记")


def _identity(value) -> str | None:
    if type(value) is str and value.isascii() and value.isdigit() and int(value) > 0:
        return value
    return None


DEFAULT_NP_INFO = (
    NpInfo("305300", CardColor.QUICK, NpRole.LANCER, True),
    NpInfo("1002200", CardColor.ARTS, NpRole.ALTEREGO, True),
    NpInfo("504500", CardColor.ARTS, NpRole.CLEANSE, False),
    NpInfo("500100", CardColor.ARTS, NpRole.PREPARATION, True),
)
# Verified JP Tiamat NP: self subState before Buster-up/damageNp. The identity
# and colour are facts, not configurable roles that another AE may imitate.
SELF_CLEANSE_FINISHER_COLORS = {"1001600": CardColor.BUSTER}
_GOAL_COLOR = {1: CardColor.BUSTER, 2: CardColor.ARTS, 3: CardColor.QUICK, 4: None}


@dataclass(frozen=True)
class TrialProgress:
    phase: int
    remaining_color_attacks: int
    confirmed_submission_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.phase) is not int or self.phase not in _GOAL_COLOR:
            raise ValueError("试炼血条必须由外部确认后填写1～4")
        if (type(self.remaining_color_attacks) is not int
                or not 0 <= self.remaining_color_attacks <= 2
                or self.phase == 4 and self.remaining_color_attacks != 0):
            raise ValueError("前三条剩余色卡攻击次数应为0～2，第四条应为0")
        if (len(set(self.confirmed_submission_ids)) != len(self.confirmed_submission_ids)
                or any(type(value) is not str or not value for value in self.confirmed_submission_ids)):
            raise ValueError("已确认提交记录需要互不重复的非空编号")

    @classmethod
    def begin(cls, phase: int, remaining_color_attacks: int | None = None):
        return cls(phase, (0 if phase == 4 else 2)
                   if remaining_color_attacks is None else remaining_color_attacks)

    @property
    def required_color(self) -> CardColor | None:
        return _GOAL_COLOR[self.phase]


@dataclass(frozen=True)
class TrialCardSuggestion:
    picks: tuple[CardPick, ...] = ()
    waiting: bool = False
    reason: str = ""
    projected_color_attacks: int = 0
    holds_attack_np: bool = False


def _known_cards(state: BattleState, np_info: Sequence[NpInfo]):
    info_by_id = {info.servant_id: info for info in np_info}
    if len(info_by_id) != len(np_info):
        raise ValueError("同一从者不能配置两份相互冲突的宝具资料")
    face_counts = {card.ui_slot: sum(c.ui_slot == card.ui_slot for c in state.cards) for card in state.cards}
    faces = {
        CardPick(PrimitiveKind.SELECT_CARD, card.ui_slot): card
        for card in state.cards
        if (type(card.ui_slot) is int and 1 <= card.ui_slot <= 5
            and face_counts[card.ui_slot] == 1 and card.can_attack
            and card.confidence.passes(0.80) and card.owner_confidence.passes(0.80)
            and type(card.owner_slot) is int and 1 <= card.owner_slot <= 3
            and _identity(card.owner_servant_id) is not None
            and card.color in (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK))
    }
    np_counts = {card.servant_slot: sum(c.servant_slot == card.servant_slot for c in state.np_cards)
                 for card in state.np_cards}
    nps = {
        CardPick(PrimitiveKind.SELECT_NP, card.servant_slot): info_by_id[card.owner_servant_id]
        for card in state.np_cards
        if (type(card.servant_slot) is int and 1 <= card.servant_slot <= 3
            and np_counts[card.servant_slot] == 1 and card.available
            and card.confidence.passes(0.80) and card.owner_confidence.passes(0.80)
            and _identity(card.owner_servant_id) is not None and card.owner_servant_id in info_by_id)
    }
    return faces, nps


def _attacks_of_color(picks, faces, nps, color) -> int:
    if color is None:
        return 0
    return sum(
        int(faces[pick].color is color) if pick in faces else
        int(nps[pick].damaging and nps[pick].color is color) if pick in nps else 0
        for pick in picks
    )


def propose_cards(
    state: BattleState,
    progress: TrialProgress,
    *,
    fixed_prefix: Sequence[CardPick] = (),
    fixed_positions: Sequence[CardPick | None] = (),
    allow_attack_np: bool = True,
    require_cleanse_before_finish: bool = False,
    np_info: Sequence[NpInfo] = DEFAULT_NP_INFO,
    charge_priority_servant_ids: Sequence[str] = (),
    avoid_mighty_chains: bool = False,
    self_cleanse_finishers: Sequence[str] = (),
    allowed_face_servant_ids: Sequence[str] | None = None,
) -> TrialCardSuggestion:
    """Return three candidates or an explicit no-input wait recommendation.

    ``fixed_prefix`` retains relative order; ``fixed_positions`` additionally
    fixes absolute positions and uses None for ordinary-card fill holes. Only
    one of these may be supplied. ``allow_attack_np=False`` prevents unplanned
    attack NPs, while explicitly fixed NPs remain subject to phase safety.
    In phase 3 an explicitly requested
    Lancer NP is permitted only after an Alter Ego NP in that same prefix.
    Phase 4 with ``require_cleanse_before_finish=True`` requires a visible,
    available cleanse NP first; without one the policy returns no picks. It
    never guesses that Scorch has already expired or was resisted.

    ``charge_priority_servant_ids`` is an optional encounter caller preference:
    after legal NPs and colour goals, prefer those servants' Arts and later
    positions. A verified ready NP cancels its owner's priority unless that NP
    is selected in the same combination. Empty retains the original scoring.
    ``avoid_mighty_chains`` is a hard filter over all three colours, including
    verified NP colours. With that filter and final cleansing still required,
    ``self_cleanse_finishers`` can explicitly whitelist only verified Tiamat:
    her first Buster attacking NP cleanses before its own damage. Other AEs and
    a Tiamat NP placed after an ordinary attack get no exception.
    ``allowed_face_servant_ids`` defaults None. When supplied by the encounter
    runtime, every ordinary face in a group containing a main finisher NP must
    belong to that whitelist. Illegal explicitly fixed faces stop; unavailable
    automatic faces withhold the NP. Pure ordinary collection stays unrestricted.

    Reliable ordinary cards may be proposed to cycle an insufficient hand and
    accumulate colours. This is not a damage forecast: ordinary attacks can
    also kill a low-HP bar. The caller remains responsible for stage observation.
    """
    if type(require_cleanse_before_finish) is not bool or type(allow_attack_np) is not bool:
        raise ValueError("解弱化与自动攻击宝具要求必须明确设置为布尔值")
    if type(avoid_mighty_chains) is not bool:
        raise ValueError("禁止三色链要求必须明确设置为布尔值")
    if (any(sid not in SELF_CLEANSE_FINISHER_COLORS for sid in self_cleanse_finishers)
            or len(set(self_cleanse_finishers)) != len(self_cleanse_finishers)
            or self_cleanse_finishers and (not avoid_mighty_chains or not require_cleanse_before_finish)):
        raise ValueError("自身解弱化收尾仅支持已验证的提亚马特，且必须同时禁止三色链并保留收尾解弱化要求")
    if (any(_identity(sid) != sid for sid in charge_priority_servant_ids)
            or len(set(charge_priority_servant_ids)) != len(charge_priority_servant_ids)):
        raise ValueError("蓄能优先名单需要不重复的数字从者ID字符串")
    if allowed_face_servant_ids is not None and (
            any(_identity(sid) != sid for sid in allowed_face_servant_ids)
            or len(set(allowed_face_servant_ids)) != len(allowed_face_servant_ids)):
        raise ValueError("破条普通卡名单需要不重复的数字从者ID字符串")
    if state.scene is not Scene.COMMAND_SELECTION or not state.scene_confidence.passes(0.95):
        return TrialCardSuggestion(waiting=True, reason="command_scene_not_confirmed")
    if len(state.cards) != 5:
        return TrialCardSuggestion(waiting=True, reason="five_card_hand_not_confirmed")
    faces, nps = _known_cards(state, np_info)
    prefix, positions = tuple(fixed_prefix), tuple(fixed_positions)
    if prefix and positions:
        raise ValueError("固定前置与固定位置不能同时设置")
    if len(prefix) > 3 or len(positions) > 3:
        raise ValueError("固定前置最多三张，不能重复选择同一张卡")
    fixed = list(positions or prefix)
    fixed.extend([None] * (3 - len(fixed)))
    requested = tuple(pick for pick in fixed if pick is not None)
    if any(not isinstance(pick, CardPick) for pick in requested) or len(set(requested)) != len(requested):
        raise ValueError("固定卡片需要有效且不重复的选择，空位使用None")
    if any(pick not in faces and pick not in nps for pick in requested):
        return TrialCardSuggestion(waiting=True, reason="fixed_prefix_not_reliably_available")

    main_role = NpRole.LANCER if progress.phase == 1 else NpRole.ALTEREGO
    for index, pick in enumerate(fixed):
        info = nps.get(pick)
        if info is None or info.role in (main_role, NpRole.CLEANSE):
            continue
        preparation = progress.phase == 1 and index == 0 and info.role is NpRole.PREPARATION
        after_ae = (progress.phase == 3 and info.role is NpRole.LANCER
                    and any(nps.get(previous) and nps[previous].role is NpRole.ALTEREGO
                            for previous in fixed[:index]))
        if not (preparation or after_ae):
            return TrialCardSuggestion(waiting=True, reason="fixed_prefix_np_forbidden_in_phase")

    if progress.phase == 4 and require_cleanse_before_finish:
        cleanses = tuple(pick for pick, info in nps.items() if info.role is NpRole.CLEANSE)
        self_cleanses = tuple(pick for pick, info in nps.items()
            if info.servant_id in self_cleanse_finishers and info.role is NpRole.ALTEREGO
            and info.damaging and info.color is SELF_CLEANSE_FINISHER_COLORS[info.servant_id])
        if fixed[0] not in self_cleanses:
            # An attacking self-cleanse NP is never inserted into a plan that
            # forbids unplanned attacking NPs. Explicit Tiamat-first is allowed.
            if not requested and allow_attack_np and self_cleanses:
                fixed[0] = self_cleanses[0]
            else:
                if not cleanses:
                    return TrialCardSuggestion(waiting=True, reason="cleanse_np_unavailable")
                if nps.get(fixed[0], None) is None or nps[fixed[0]].role is not NpRole.CLEANSE:
                    if any(pick in fixed for pick in cleanses) or positions and fixed[0] is not None:
                        return TrialCardSuggestion(waiting=True, reason="cleanse_must_precede_all_attacks")
                    if len(prefix) == 3:
                        return TrialCardSuggestion(waiting=True, reason="no_slot_for_required_cleanse")
                    if prefix:
                        fixed = [cleanses[0], *prefix, *([None] * (2 - len(prefix)))]
                    else:
                        fixed[0] = cleanses[0]

    mains = tuple(pick for pick, info in nps.items() if info.role is main_role)
    if (allowed_face_servant_ids is not None and any(pick in mains for pick in requested)
            and any(pick in faces and faces[pick].owner_servant_id not in allowed_face_servant_ids
                    for pick in requested)):
        return TrialCardSuggestion(waiting=True, reason="fixed_finisher_face_forbidden")
    # Other attack/support NPs are never inserted automatically. A caller may
    # explicitly reserve Castoria's NP for final cleansing by omitting its prefix.
    pool = tuple(dict.fromkeys((*faces, *(mains if allow_attack_np else ()))))
    available = tuple(pick for pick in pool if pick not in fixed)
    holes = tuple(index for index, pick in enumerate(fixed) if pick is None)
    missing = len(holes)
    if len(available) < missing:
        return TrialCardSuggestion(waiting=True, reason="fewer_than_three_reliable_candidates")
    candidates = []
    mighty_blocked = False
    finisher_face_blocked = False
    for tail in permutations(available, missing):
        picks = list(fixed)
        for index, pick in zip(holes, tail):
            picks[index] = pick
        picks = tuple(picks)
        count = _attacks_of_color(picks, faces, nps, progress.required_color)
        has_main = any(pick in mains for pick in picks)
        if has_main and count < progress.remaining_color_attacks:
            continue
        if (has_main and allowed_face_servant_ids is not None
                and any(pick in faces and faces[pick].owner_servant_id not in allowed_face_servant_ids
                        for pick in picks)):
            finisher_face_blocked = True
            continue
        colors = {faces[pick].color if pick in faces else nps[pick].color for pick in picks}
        if avoid_mighty_chains and colors == {CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK}:
            mighty_blocked = True
            continue
        candidates.append((picks, count))
    if not candidates:
        if mighty_blocked:
            return TrialCardSuggestion(waiting=True, reason="mighty_chain_forbidden")
        if finisher_face_blocked:
            return TrialCardSuggestion(waiting=True, reason="finisher_faces_unavailable", holds_attack_np=True)
        return TrialCardSuggestion(waiting=True, reason="fixed_np_would_break_before_colour_goal",
                                   holds_attack_np=bool(mains))

    def score(candidate):
        picks, count = candidate
        main_positions = tuple(index for index, pick in enumerate(picks) if pick in mains)
        # A permitted finisher NP goes first when the colour objective fits,
        # preserving the required breaker class before Overgauge follow-ups.
        first_main = -main_positions[0] if main_positions else -4
        colour_order = sum((3 - index) for index, pick in enumerate(picks)
                           if _attacks_of_color((pick,), faces, nps, progress.required_color))
        finisher_ids = {info.servant_id for info in np_info if info.role is main_role}
        finisher_faces = sum(card.owner_servant_id in finisher_ids
                             for pick, card in faces.items() if pick in picks)
        arts = sum(card.color is CardColor.ARTS for pick, card in faces.items() if pick in picks)
        deterministic = tuple(-(pick.slot + (0 if pick.kind is PrimitiveKind.SELECT_NP else 10)) for pick in picks)
        # If this hand cannot finish the colour objective, avoid rewarding the
        # phase's primary damage dealer (or AE damage) merely to cycle cards.
        # This reduces risk; it is not an HP/critical-hit damage prediction.
        collecting = count < progress.remaining_color_attacks
        risky_ids = finisher_ids | {info.servant_id for info in np_info if info.role is NpRole.ALTEREGO}
        risky_faces = sum(card.owner_servant_id in risky_ids
                          for pick, card in faces.items() if pick in picks)
        owner_score = -risky_faces if collecting else finisher_faces
        # Once this NP combination meets the colour goal, let the permitted
        # finisher's own faces follow it before a different class's faces.
        # This only lowers accidental-break risk; it predicts no damage/HP.
        finisher_order = (
            sum(3 - index for index, pick in enumerate(picks)
                if pick in faces and faces[pick].owner_servant_id in finisher_ids)
            if not collecting and main_positions else 0
        )
        ready_ids = {info.servant_id for info in nps.values()}
        spent_ids = {nps[pick].servant_id for pick in picks if pick in nps}
        charge_weights = {sid: len(charge_priority_servant_ids) - index
                          for index, sid in enumerate(charge_priority_servant_ids)
                          if sid not in ready_ids or sid in spent_ids}
        charge_arts = sum(charge_weights.get(faces[pick].owner_servant_id, 0)
                          for pick in picks if pick in faces and faces[pick].color is CardColor.ARTS)
        charge_order = sum((index + 1) * charge_weights.get(faces[pick].owner_servant_id, 0)
                           for index, pick in enumerate(picks)
                           if pick in faces and faces[pick].color is CardColor.ARTS)
        return (min(count, progress.remaining_color_attacks), bool(main_positions), first_main,
                charge_arts, charge_order, owner_score, finisher_order, colour_order, arts, deterministic)

    picks, count = max(candidates, key=score)
    holds = bool(mains) and not any(pick in mains for pick in picks)
    return TrialCardSuggestion(
        picks=tuple(picks), reason="collect_required_colour" if holds else "phase_candidates",
        projected_color_attacks=count, holds_attack_np=holds,
    )


def confirm_submission(
    progress: TrialProgress,
    state: BattleState,
    executed_picks: Sequence[CardPick],
    *,
    submission_id: str,
    attacked_phase: int,
    np_info: Sequence[NpInfo] = DEFAULT_NP_INFO,
) -> TrialProgress:
    """Apply only externally confirmed attacks from the stated pre-Break bar.

    Pass the original selection snapshot and only the picks whose execution was
    actually confirmed. A click/proposal is insufficient. The unique submission
    ID makes re-observing the same attack idempotent. This function does not
    advance phases or prove an in-game mission completed after victory.
    """
    if type(submission_id) is not str or not submission_id:
        raise ValueError("确认攻击后需要填写本回合唯一提交编号")
    if type(attacked_phase) is not int or attacked_phase != progress.phase:
        raise ValueError("确认攻击的血条与当前记录不一致，请先核对实际阶段")
    if submission_id in progress.confirmed_submission_ids:
        return progress
    picks = tuple(executed_picks)
    if len(picks) > 3 or len(set(picks)) != len(picks):
        raise ValueError("单次提交最多确认三张不同的卡，不能重复累计")
    faces, nps = _known_cards(state, np_info)
    count = _attacks_of_color(picks, faces, nps, progress.required_color)
    return replace(progress, remaining_color_attacks=max(0, progress.remaining_color_attacks - count),
                   confirmed_submission_ids=(*progress.confirmed_submission_ids, submission_id))
