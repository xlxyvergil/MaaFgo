"""November 2026 Trial runtime, enabled only by both explicit entry/source flags.

Enemy-class OCR observes ARCHER/ASSASSIN/RIDER/CASTER as bars 1/2/3/4. A turn
number is never treated as a bar number. This adapter retains normal skill and
formation execution, replacing only command-card decisions for this Trial.
It predicts neither HP/damage nor Scorch resistance. In particular, a manually
fixed phase-3 Britomart NP after an Alter Ego NP is safe only if the latter
actually Breaks the bar; card ordering alone cannot guarantee that damage.

Source example (top level of the imported editor/Chaldea JSON)::

  "maaTrialStrategy": {"enabled": true, "phase": 1,
    "remainingColorAttacks": 2, "requireCleanseBeforeFinish": true,
    "finalBarPreparationTurns": [5],
    "npMetadata": [{"servantId": "304800", "color": "B",
      "role": "lancer_finisher", "damaging": true}]}

``phase`` and ``remainingColorAttacks`` are explicit recovery seeds, not an
instruction to override OCR. A resumed task must match its first observed bar.
They do not seek a skill-plan turn: when resuming, supply only the remaining
``actions`` (the normal plan still starts at index zero), or omit the turn plan.
``finalBarPreparationTurns`` is an optional list of one-based *plan* turns. On
the observed fourth bar only, such a turn may submit exactly three explicitly
fixed, reliably identified cards without the normal finisher/cleanse rule. No
automatic fill or Alter Ego attack NP is permitted. This can express a planned
Melusine AoE/count-and-charge turn, or three explicit ordinary charge cards.
It does not predict whether a non-AE preparation attack might kill the boss.
After trimming actions for recovery, these plan-turn numbers start at one again.
An ordinary card fixes its current frontline slot and colour; a supplied actor
binding additionally fixes the servant identity. No missing actor is invented.
NP metadata overrides verified defaults by servant ID; unknown NPs (including
form-dependent Melusine) are never assigned a colour from their screen slot.
Only three fully executed selections followed by a successful return from the
parent animation wait commit projected colour counts. This is not proof that
an in-game mission has completed; that still requires the activity mission UI.
"""
from __future__ import annotations

from dataclasses import dataclass

import mfaalog

from ..core.decider import RuleDecider
from ..core.enums import CardColor, PrimitiveKind, Scene
from ..core.models import BattleAction, BattleState, CardPick
from ..data.servant_identity import _servant_lookup
from ..strategies.hellish_trial import (
    DEFAULT_NP_INFO, NpInfo, NpRole, TrialProgress, confirm_submission, propose_cards,
)
from .runtime import AutoBattleRuntime, BattleResult


TRIAL_PROFILE = "hellish_202611"
PHASE_NODE = "地狱的试炼-敌人职阶"
CLASS_PHASES = {"ARCHER": 1, "ASSASSIN": 2, "RIDER": 3, "CASTER": 4}


class TrialStrategyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TrialStrategyConfig:
    progress: TrialProgress
    require_cleanse_before_finish: bool = True
    np_info: tuple[NpInfo, ...] = DEFAULT_NP_INFO
    final_bar_preparation_turns: tuple[int, ...] = ()


def trial_config_from_source(node: dict, source: dict | None) -> TrialStrategyConfig | None:
    """Double gate; unrelated tasks do not parse or use Trial source fields."""
    attach = node.get("attach") or {}
    if attach.get("native_battle_trial_profile") != TRIAL_PROFILE:
        return None
    raw = source.get("maaTrialStrategy") if isinstance(source, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TrialStrategyError("trial_config_type", "导入方案的 maaTrialStrategy 不是对象，无法读取试炼策略。请在方案 JSON 中修正这一栏目后重新导入。")
    enabled = raw.get("enabled", False)
    if type(enabled) is not bool:
        raise TrialStrategyError("trial_enabled_type", "试炼策略的启用值不是 true 或 false，无法确定是否启用。请在方案 JSON 的 maaTrialStrategy.enabled 中填写 true 或 false，重新导入。")
    if not enabled:
        return None
    try:
        progress = TrialProgress.begin(raw.get("phase", 1), raw.get("remainingColorAttacks"))
        cleanse = raw.get("requireCleanseBeforeFinish", True)
        if type(cleanse) is not bool:
            raise ValueError("requireCleanseBeforeFinish 需要 true 或 false")
        preparation_turns = raw.get("finalBarPreparationTurns", [])
        if (not isinstance(preparation_turns, list)
                or any(type(turn) is not int or turn < 1 for turn in preparation_turns)
                or len(set(preparation_turns)) != len(preparation_turns)):
            raise ValueError("finalBarPreparationTurns 需要不重复的正整数列表，按方案回合从1开始编号；没有预备回合时填[]")
        overrides = raw.get("npMetadata", [])
        if not isinstance(overrides, list):
            raise ValueError("npMetadata 需要宝具资料列表")
        np_by_id = {info.servant_id: info for info in DEFAULT_NP_INFO}
        seen = set()
        for item in overrides:
            if not isinstance(item, dict):
                raise ValueError("每份宝具资料需要包含 servantId、color、role 和 damaging")
            sid = item.get("servantId")
            if not isinstance(sid, str) or sid in seen:
                raise ValueError("宝具资料需要不重复的数字 servantId 字符串")
            info = NpInfo(sid, CardColor(item.get("color")), NpRole(item.get("role")), item.get("damaging"))
            seen.add(sid)
            np_by_id[sid] = info
        return TrialStrategyConfig(progress, cleanse, tuple(np_by_id.values()), tuple(preparation_turns))
    except (ValueError, TypeError) as exc:
        raise TrialStrategyError("trial_config_invalid", f"无法读取本次试炼策略：{exc}。请修正导入方案 JSON 的 maaTrialStrategy，确认实际血条、剩余颜色攻击次数和宝具资料后重新导入。") from exc


_SUGGESTION_ERRORS = {
    "command_scene_not_confirmed": "尚未确认完整的选卡界面，不能安全出卡。请保持选卡页面并重试。",
    "five_card_hand_not_confirmed": "没有识别到完整的五张指令卡，不能计算颜色任务。请保持选卡页面并重试。",
    "fixed_prefix_not_reliably_available": "方案中固定的卡片没有可靠的颜色或归属识别，不能确认出卡顺序。请检查该回合“宝具与色卡”及当前阵容，修正后重新导入。",
    "fixed_prefix_np_forbidden_in_phase": "方案安排的宝具不适合当前血条，可能由错误职阶击破。请检查当前血条及该回合“宝具与色卡”，修正方案后重新导入。",
    "cleanse_np_unavailable": "末条需要先清除弱化，但没有识别到可用的解弱化宝具，无法确认无焦热收尾。请手动确认弱化状态并补足术呆NP，或确认确实不需要解弱化后修改方案的 requireCleanseBeforeFinish，再重新导入。",
    "cleanse_must_precede_all_attacks": "末条方案把攻击放在了解弱化宝具前，无法保证无焦热收尾。请在该回合“宝具与色卡”中将术呆宝具放在第一位，重新导入。",
    "no_slot_for_required_cleanse": "末条三张卡均已固定，没有位置先释放解弱化宝具。请在该回合“宝具与色卡”中为第一位术呆宝具留出位置，重新导入。",
    "fewer_than_three_reliable_candidates": "当前不足三张可靠识别且可以行动的卡，无法安全补齐出卡。请检查封印状态与卡牌归属，保持选卡页面后重试。",
    "fixed_np_would_break_before_colour_goal": "本回合固定宝具组合不足以完成当前剩余的颜色攻击次数，直接释放可能漏掉任务。请先手动用对应颜色普攻累计，随后在方案 maaTrialStrategy 中填写实际 phase 和 remainingColorAttacks，再重新导入；也可调整该回合“宝具与色卡”。",
}


class HellishTrialDecider:
    """Retain RuleDecider skills; strictly resolve planned Trial command cards."""

    def __init__(self, inner: RuleDecider, config: TrialStrategyConfig):
        self.inner, self.config = inner, config
        self.plan = inner.plan
        self.progress = config.progress
        self.observed = False
        self.may_advance = False

    @property
    def card_policy(self):
        return self.inner.card_policy

    @property
    def skill_policy(self):
        return self.inner.skill_policy

    def observe_phase(self, phase: int) -> None:
        if not self.observed:
            if phase != self.progress.phase:
                raise TrialStrategyError("trial_initial_phase_mismatch", f"实际敌人职阶对应第 {phase} 条血，与方案恢复起点第 {self.progress.phase} 条不一致，已停止。请在导入方案 maaTrialStrategy 中按当前画面填写 phase 和 remainingColorAttacks，再重新导入。")
            self.observed = True
        elif phase != self.progress.phase:
            if phase != self.progress.phase + 1 or not self.may_advance:
                raise TrialStrategyError("trial_phase_discontinuity", f"敌人职阶从第 {self.progress.phase} 条变为第 {phase} 条，无法确认连续血条进度，已停止。请核对当前血条，在导入方案 maaTrialStrategy 中填写实际 phase 和 remainingColorAttacks，再重新导入。")
            previous = self.progress
            self.progress = TrialProgress.begin(phase)
            self.may_advance = False
            mfaalog.info(f"[试炼策略] 职阶确认血条 {previous.phase}→{phase}，上一条剩余颜色攻击={previous.remaining_color_attacks}；新条不继承旧计数")

    def confirm(self, state: BattleState, picks: tuple[CardPick, ...], phase: int, submission_id: str):
        self.progress = confirm_submission(self.progress, state, picks, attacked_phase=phase,
                                           submission_id=submission_id, np_info=self.config.np_info)
        self.may_advance = True
        mfaalog.info(f"[试炼策略] 已确认提交 {submission_id}，血条={phase}，剩余颜色攻击={self.progress.remaining_color_attacks}")

    def _fixed_np(self, state, slot, actor=None):
        matches = [card for card in state.np_cards if card.servant_slot == slot]
        if len(matches) != 1 or not matches[0].available:
            raise TrialStrategyError("trial_fixed_np_unavailable", f"本回合安排的前排 {slot} 宝具当前没有可用卡面，无法按方案出卡。请检查NP充能与封印，或调整该回合“宝具与色卡”，重新导入后再试。")
        card = matches[0]
        if (not card.confidence.passes(.80) or not card.owner_confidence.passes(.80)
                or not card.owner_servant_id or actor and actor.servant_id != card.owner_servant_id):
            raise TrialStrategyError("trial_fixed_np_identity_unknown", f"无法可靠确认前排 {slot} 宝具属于方案中的从者，不能用普通牌代替。请检查当前阵容与该回合“宝具与色卡”，修正后重新导入。")
        if card.owner_servant_id not in {info.servant_id for info in self.config.np_info}:
            raise TrialStrategyError("trial_np_metadata_missing", f"前排 {slot} 从者 {card.owner_servant_id} 缺少已确认的宝具颜色和用途，无法计算颜色任务。请在方案 maaTrialStrategy.npMetadata 中补充该形态的宝具资料后重新导入。")
        return CardPick(PrimitiveKind.SELECT_NP, slot)

    def _validate_final_preparation_plan(self, turn, turn_index):
        label = f"方案第{turn_index + 1}回合"
        if not self.observed or self.progress.phase != 4:
            raise TrialStrategyError("trial_preparation_requires_final_bar", f"{label}被标记为末条预备回合，但当前没有确认第四条血，不能跳过收尾守卫。请核对实际血条及 maaTrialStrategy.finalBarPreparationTurns；它按方案回合从1编号，不是游戏TURN，中途裁剪方案后须重新编号并导入。")
        if turn is None:
            raise TrialStrategyError("trial_preparation_plan_missing", f"{label}没有实际回合配置，无法作为末条预备回合。请在编辑器补齐该回合，或从 maaTrialStrategy.finalBarPreparationTurns 删除此编号后重新导入。")
        if len(turn.card_sequence) != 3:
            raise TrialStrategyError("trial_preparation_requires_three_fixed_cards", f"{label}的末条预备没有明确安排三张卡，不能自动补牌。请在该回合“宝具与色卡”中固定三项及顺序，再重新导入。")
        for card in turn.card_sequence:
            if (type(card.servant_slot) is not int or not 1 <= card.servant_slot <= 3
                    or not isinstance(card.kind, PrimitiveKind)
                    or card.kind not in (PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_NP)
                    or card.kind is PrimitiveKind.SELECT_CARD
                    and card.color not in (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK)):
                raise TrialStrategyError("trial_preparation_auto_card_forbidden", f"{label}的末条预备含自动补位或未指定颜色的普通卡，无法严格确认三张卡。请在“宝具与色卡”中明确每张普通卡的从者、颜色与顺序，或从 finalBarPreparationTurns 删除该回合后重新导入。")

    def _decide_final_preparation(self, state, turn, turn_index):
        if not state.scene_confidence.passes(.95) or len(state.cards) != 5:
            raise TrialStrategyError("trial_preparation_hand_unknown", "末条预备没有识别到完整选卡页面和五张指令卡，无法验证固定出卡。请保持选卡页面并重试。")
        picks, used_faces = [], set()
        info_by_id = {info.servant_id: info for info in self.config.np_info}
        for planned in turn.card_sequence:
            if planned.kind is PrimitiveKind.SELECT_NP:
                pick = self._fixed_np(state, planned.servant_slot, planned.actor)
                card = next(card for card in state.np_cards if card.servant_slot == planned.servant_slot)
                info = info_by_id[card.owner_servant_id]
                servant = _servant_lookup().get(card.owner_servant_id)
                if not servant or not servant.get("class"):
                    raise TrialStrategyError("trial_preparation_np_class_unknown", f"前排{planned.servant_slot}的宝具职阶不在本地资料中，无法确认它能用于末条预备。请更新从者资料，或将该宝具改到正常回合后重新导入。")
                # Editable role data must not relabel a known attacking AE as a
                # Lancer/preparation/support NP to bypass the finishing guard.
                is_ae = servant["class"] == "alterEgo"
                known_attack = "输出" in servant.get("np_type", "")
                if info.role is NpRole.ALTEREGO or is_ae and (info.damaging or known_attack):
                    raise TrialStrategyError("trial_preparation_attack_np_forbidden", f"方案第{turn_index + 1}回合的末条预备安排了AE攻击宝具，不能借预备回合跳过解弱化收尾守卫。请将这次AE宝具放入正常收尾回合，或删除 finalBarPreparationTurns 中的该回合编号，再重新导入。")
                if is_ae and servant.get("np_type") != "辅助宝具":
                    raise TrialStrategyError("trial_preparation_np_type_unknown", "本地资料无法确认该AE宝具是纯辅助宝具，不能用于末条预备。请更新从者资料，或将它改到正常回合后重新导入。")
            else:
                candidates = [card for card in state.cards
                    if type(card.ui_slot) is int and 1 <= card.ui_slot <= 5
                    and sum(other.ui_slot == card.ui_slot for other in state.cards) == 1
                    and card.ui_slot not in used_faces and card.can_attack
                    and card.confidence.passes(.80) and card.owner_confidence.passes(.80)
                    and type(card.owner_servant_id) is str and card.owner_servant_id.isascii()
                    and card.owner_servant_id.isdigit() and int(card.owner_servant_id) > 0
                    and card.owner_slot == planned.servant_slot and card.color is planned.color
                    and (planned.actor is None or card.owner_servant_id == planned.actor.servant_id)]
                if not candidates:
                    raise TrialStrategyError("trial_preparation_fixed_card_missing", f"方案第{turn_index + 1}回合的末条预备没有可靠识别到前排{planned.servant_slot}的指定{planned.color.value}色普通卡，不能用其他卡替代。请核对发牌、归属与封印状态，或调整该回合“宝具与色卡”后重新导入。")
                pick = CardPick(PrimitiveKind.SELECT_CARD, candidates[0].ui_slot)
                used_faces.add(pick.slot)
            picks.append(pick)
        if len(set(picks)) != 3:
            raise TrialStrategyError("trial_preparation_duplicate_card", "末条预备重复安排了同一张卡，无法提交三张不同的卡。请在该回合“宝具与色卡”中删除重复项，固定三张可用卡后重新导入。")
        mfaalog.info(f"[试炼策略] 明确末条预备回合={turn_index + 1}，仅按固定三牌执行，不自动补牌或插入宝具：{tuple(picks)}")
        return BattleAction(self.inner._pick_enemy_target(state, turn_index), tuple(picks),
                            rationale_tag=f"hellish_202611:phase4:preparation_turn{turn_index + 1}")

    def decide(self, state: BattleState, turn_index: int = 0) -> BattleAction:
        turn = self.plan.active_turn(turn_index) if self.plan else None
        preparation = turn_index + 1 in self.config.final_bar_preparation_turns
        if preparation and state.scene in (Scene.MAIN_BATTLE, Scene.COMMAND_SELECTION):
            # Reject a wrong phase/partial plan before releasing that turn's
            # skills. NP availability is checked later on the real command page.
            self._validate_final_preparation_plan(turn, turn_index)
            if state.scene is Scene.COMMAND_SELECTION:
                return self._decide_final_preparation(state, turn, turn_index)
        if state.scene is not Scene.COMMAND_SELECTION:
            return self.inner.decide(state, turn_index)
        if not self.observed:
            raise TrialStrategyError("trial_phase_not_observed", "尚未识别当前敌人职阶，无法确定血条策略。请保持主战斗或选卡页面，确认无遮挡后重试。")
        fixed_positions, fixed_prefix = [], []
        if turn and turn.card_sequence:
            if len(turn.card_sequence) > 3:
                raise TrialStrategyError("trial_too_many_fixed_cards", "该回合安排了超过三张卡，无法提交。请在“宝具与色卡”中保留最多三项后重新导入。")
            used = set()
            for planned in turn.card_sequence:
                if planned.kind is PrimitiveKind.SELECT_NP:
                    pick = self._fixed_np(state, planned.servant_slot, planned.actor)
                else:
                    candidates = [card for card in state.cards
                                  if card.ui_slot not in used and card.can_attack
                                  and card.confidence.passes(.80) and card.owner_confidence.passes(.80)
                                  and card.owner_servant_id and card.owner_slot == planned.servant_slot
                                  and (planned.color is None or card.color is planned.color)
                                  and (planned.actor is None or card.owner_servant_id == planned.actor.servant_id)]
                    pick = CardPick(PrimitiveKind.SELECT_CARD, candidates[0].ui_slot) if candidates else None
                    if pick:
                        used.add(pick.slot)
                fixed_positions.append(pick)
        elif turn:
            if len(turn.np_order) > 3:
                raise TrialStrategyError("trial_too_many_fixed_cards", "该回合安排了超过三张宝具，无法提交。请在“宝具与色卡”中保留最多三项后重新导入。")
            fixed_prefix = [self._fixed_np(state, slot) for slot in turn.np_order]
        requested = [pick for pick in (fixed_positions or fixed_prefix) if pick is not None]
        if len(set(requested)) != len(requested):
            raise TrialStrategyError("trial_duplicate_fixed_card", "该回合重复安排了同一张宝具卡，无法提交。请在“宝具与色卡”中删除重复项后重新导入。")
        result = propose_cards(state, self.progress, fixed_positions=fixed_positions,
                               fixed_prefix=fixed_prefix, allow_attack_np=turn is None,
                               require_cleanse_before_finish=self.config.require_cleanse_before_finish,
                               np_info=self.config.np_info)
        if result.waiting:
            message = _SUGGESTION_ERRORS.get(result.reason, "当前出卡条件无法确认，已保留现场停止。请检查当前血条及导入方案后重试。")
            raise TrialStrategyError(f"trial_{result.reason}", message)
        mfaalog.info(f"[试炼策略] phase={self.progress.phase} remaining={self.progress.remaining_color_attacks} projected={result.projected_color_attacks} hold_np={result.holds_attack_np} picks={result.picks}")
        return BattleAction(self.inner._pick_enemy_target(state, turn_index), result.picks,
                            rationale_tag=f"hellish_202611:phase{self.progress.phase}:{result.reason}")


@dataclass(frozen=True)
class _Submission:
    state: BattleState
    picks: tuple[CardPick, ...]
    phase: int
    submission_id: str


class HellishTrialRuntime(AutoBattleRuntime):
    """Observe phase on cached frames, confirm old-bar progress after settlement."""

    def __init__(self, context, controller, decider: HellishTrialDecider, *args, **kwargs):
        if not isinstance(decider, HellishTrialDecider):
            raise TypeError("HellishTrialRuntime requires an explicitly configured Trial decider")
        super().__init__(context, controller, decider, *args, **kwargs)
        self._trial_pending: _Submission | None = None
        self._trial_deferred_phase: int | None = None
        self._trial_selection_state: BattleState | None = None
        self._trial_confirmed_turns = 0

    def run(self) -> BattleResult:
        try:
            return super().run()
        except TrialStrategyError as exc:
            mfaalog.error(f"[试炼策略] {exc}")
            return BattleResult.fail(exc.code, self._trial_confirmed_turns)

    def _read_phase(self) -> int:
        image = self.controller.cached_image
        if image is None:
            raise TrialStrategyError("trial_phase_image_missing", "当前战斗截图不可用，无法确认敌人职阶。请保持主战斗或选卡页面并重试。")
        detail = self.ctx.run_recognition(PHASE_NODE, image)
        hits = []
        if detail and detail.hit:
            results = getattr(detail, "filtered_results", None) or [getattr(detail, "best_result", None)]
            for result in results:
                text = str(getattr(result, "text", "")).strip().upper()
                score = getattr(result, "score", 0)
                if text in CLASS_PHASES and isinstance(score, (int, float)) and score >= .85:
                    hits.append(CLASS_PHASES[text])
        if len(set(hits)) != 1:
            raise TrialStrategyError("trial_phase_unknown", "无法可靠识别当前敌人职阶，不能确认血条进度，已停止。请保持主战斗或选卡界面，确认敌人职阶无遮挡后重试。")
        return hits[0]

    def _observe(self, *, settle_main=False, enemy_slots=None):
        state = super()._observe(settle_main=settle_main, enemy_slots=enemy_slots)
        if state.scene in (Scene.MAIN_BATTLE, Scene.COMMAND_SELECTION):
            phase = self._read_phase()
            if self._trial_pending is None:
                self.decider.observe_phase(phase)
            else:
                old = self._trial_pending.phase
                if phase not in (old, old + 1) or self._trial_deferred_phase == old + 1 and phase == old:
                    raise TrialStrategyError("trial_phase_discontinuity", "出卡后的敌人职阶变化不连续，无法确认血条进度，已停止。请核对实际血条，在方案 maaTrialStrategy 中修正恢复起点后重新导入。")
                self._trial_deferred_phase = phase
        if state.scene is Scene.COMMAND_SELECTION:
            self._trial_selection_state = state
        return state

    def _execute_selection(self, action) -> bool:
        picks = tuple(action.picks)
        blocked = {card.ui_slot for card in self._selection_cards if not card.can_attack}
        normalized = tuple(self._normalize_picks_for_execution(picks, blocked_face_slots=blocked))
        if len(picks) != 3 or normalized != picks or self._trial_selection_state is None:
            raise TrialStrategyError("trial_selection_not_exact", "本回合没有形成三张有效且顺序明确的卡，无法安全提交。请检查该回合“宝具与色卡”及卡牌封印状态后重试。")
        pending = _Submission(self._trial_selection_state, picks, self.decider.progress.phase,
                              f"hellish-turn-{self._trial_confirmed_turns + 1}")
        if not super()._execute_selection(action):
            return False
        self._trial_pending = pending
        self._trial_deferred_phase = None
        return True

    def _opening_wait_action(self, state, target_id, target_slot):
        # Opening waits freeze the skill-plan turn, but still accumulate the
        # actually submitted colour attacks on the observed Trial bar.
        result = propose_cards(state, self.decider.progress, allow_attack_np=False,
                               np_info=self.decider.config.np_info)
        if result.waiting:
            raise TrialStrategyError(f"trial_{result.reason}", _SUGGESTION_ERRORS[result.reason])
        return BattleAction(self.decider.inner._pick_enemy_target(state, self._turn_index),
                            result.picks, rationale_tag="hellish_202611:opening_wait_without_np")

    def _wait_turn_settled(self) -> bool:
        settled = super()._wait_turn_settled()
        pending, observed = self._trial_pending, self._trial_deferred_phase
        self._trial_pending, self._trial_deferred_phase = None, None
        if settled and pending is not None:
            self.decider.confirm(pending.state, pending.picks, pending.phase, pending.submission_id)
            self._trial_confirmed_turns += 1
            if observed is not None:
                self.decider.observe_phase(observed)
        return settled
