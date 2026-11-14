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
the observed fourth bar only, its default ``strict`` mode submits exactly three explicitly
fixed, reliably identified cards without the normal finisher/cleanse rule. No
automatic fill or Alter Ego attack NP is permitted. This can express a planned
Melusine AoE/count-and-charge turn, or three explicit ordinary charge cards.
It does not predict whether a non-AE preparation attack might kill the boss.
After trimming actions for recovery, these plan-turn numbers start at one again.
Optional ``phasePlanTurns`` maps the four observed bars to ordered one-based
plan rows, for example ``[[1], [2, 3], [4], [5, 6]]``. Each row's skills run once;
extra colour/NP collection turns do not consume later rows. A real class change
selects the new bar's rows and logs any skipped old rows. Collection waits commit
only after the normal three-input/animation contract and stop at the configured
``maxWaitTurnsPerStep`` (default three, range zero to ten).
With that mapping, explicit ``finalBarPreparationMode="charge_cleanser"`` allows
one listed non-final fourth-bar row to retain its fixed non-AE attacking NP and
fill two faces, preferring the cleanser's Arts. The following final row can wait
using three reliably identified non-AE ordinary cards, never an attack NP. This
does not predict ordinary-card lethal damage or that Scorch is absent. The
normal final row still requires the visible cleanse NP before all attacks.
Optional stage-only ``chargePriorityServantIds`` prefers those servants' Arts
after colour/NP constraints and before finisher face-card preferences. A visible
ready NP cancels that owner's charge priority unless it is selected this turn.
``useCleanseNpWhileWaiting`` defaults false; when explicitly enabled, a reliably
identified pure support cleanse NP may lead a waiting turn without sacrificing
the available colour goal. It is never counted as an Arts attack. A blocked
fixed NP instead prioritises its verified/actor-bound owner's Arts for charging;
unavailability alone does not distinguish low charge from a seal.
Stage-only ``avoidMightyChains`` defaults false and excludes every complete
B/A/Q combination, including NP colours, from normal/wait/preparation paths;
the last input gate repeats the check. In stage mode, an ordinary proposal
with an explicit auto hole may hold its attacking NP and wait with safe cards
when only Mighty completions exist. Fixed triples and the last input gate still
stop; the waiting budget and final-bar exclusion of AE faces do not change.
``selfCleanseFinishers=["1001600"]``
requires that flag plus ``requireCleanseBeforeFinish`` and verified Buster AE
metadata. Only Tiamat's reliably visible first NP may then replace external
cleansing (JP NP self-subState before Buster-up/damage). This avoids Grand AE
board 15's Mighty-triggered self-cleanse failure; other AEs cannot opt in by
changing role labels.
``repeatReadySkillServantIds`` defaults empty. On later main frames of the
same mapped row only, reliable listed identities may use truly ready skills
on themselves. Repeated rows disable the usual plan availability patch; first
visits to a new row retain ordinary planned execution. This does not infer HP,
NP percentages or skill benefit/cost, and U-Olga Marie skill 3 is never repeated.
Stage-only ``finisherFacePhases`` defaults empty. On listed bars, a submitted
group containing the permitted main finisher NP can use ordinary cards only
from verified legal breaker classes (Lancer; Caster/AE; Assassin/AE; Rider/AE).
The runtime supplies IDs from real local class data, not editable NP roles.
An illegal fixed face stops; insufficient legal automatic faces holds the
attacking NP for bounded collection/protection. Collection without a main NP
retains ordinary charging choices and makes no prediction of lethal damage.
Plans with queued special skill-menu choices are rejected in stage mode: their
global choice queues cannot be reordered or silently skipped with phase rows.
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
from itertools import permutations

import mfaalog

from ..core.decider import RuleDecider
from ..core.enums import CardColor, PrimitiveKind, Scene
from ..core.models import BattleAction, BattleState, CardPick, ServantSkillAction
from ..data.servant_identity import _servant_lookup, servant_name
from ..strategies.hellish_trial import (
    DEFAULT_NP_INFO, SELF_CLEANSE_FINISHER_COLORS, NpInfo, NpRole, TrialProgress,
    confirm_submission, propose_cards,
)
from .runtime import AutoBattleRuntime, BattleResult


TRIAL_PROFILE = "hellish_202611"
PHASE_NODE = "地狱的试炼-敌人职阶"
CLASS_PHASES = {"ARCHER": 1, "ASSASSIN": 2, "RIDER": 3, "CASTER": 4}
FINISHER_FACE_CLASSES = {1: frozenset(("lancer",)), 2: frozenset(("caster", "alterEgo")),
                         3: frozenset(("assassin", "alterEgo")), 4: frozenset(("rider", "alterEgo"))}


class TrialStrategyError(ValueError):
    def __init__(self, code: str, message: str, *, charge_servant_id: str | None = None,
                 allow_auto_wait: bool = False):
        super().__init__(message)
        self.code = code
        self.charge_servant_id = charge_servant_id
        self.allow_auto_wait = allow_auto_wait


@dataclass(frozen=True)
class TrialStrategyConfig:
    progress: TrialProgress
    require_cleanse_before_finish: bool = True
    np_info: tuple[NpInfo, ...] = DEFAULT_NP_INFO
    final_bar_preparation_turns: tuple[int, ...] = ()
    phase_plan_turns: tuple[tuple[int, ...], ...] = ()
    max_wait_turns_per_step: int = 3
    final_bar_preparation_mode: str = "strict"
    charge_priority_servant_ids: tuple[str, ...] = ()
    use_cleanse_np_while_waiting: bool = False
    avoid_mighty_chains: bool = False
    self_cleanse_finishers: tuple[str, ...] = ()
    repeat_ready_skill_servant_ids: tuple[str, ...] = ()
    finisher_face_phases: tuple[int, ...] = ()


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
        phase_turns = raw.get("phasePlanTurns", [])
        if phase_turns:
            if (not isinstance(phase_turns, list) or len(phase_turns) != 4
                    or any(not isinstance(group, list) or not group
                           or any(type(turn) is not int or turn < 1 for turn in group)
                           for group in phase_turns)):
                raise ValueError("phasePlanTurns 需要四组非空的正整数方案回合列表，依次对应弓、杀、骑、术四条血")
            flat = [turn for group in phase_turns for turn in group]
            if len(set(flat)) != len(flat) or flat != sorted(flat):
                raise ValueError("phasePlanTurns 的方案回合须严格递增且不能重复，按编辑器回合从1编号")
        elif phase_turns != []:
            raise ValueError("phasePlanTurns 未启用时需要填[]")
        max_wait = raw.get("maxWaitTurnsPerStep", 3)
        if type(max_wait) is not int or not 0 <= max_wait <= 10:
            raise ValueError("maxWaitTurnsPerStep 需要0～10的整数，表示每一步最多额外普攻等待几回合")
        preparation_mode = raw.get("finalBarPreparationMode", "strict")
        if preparation_mode not in ("strict", "charge_cleanser"):
            raise ValueError("finalBarPreparationMode 需要strict或charge_cleanser")
        if preparation_mode == "charge_cleanser" and not phase_turns:
            raise ValueError("charge_cleanser 需要同时填写phasePlanTurns，确保末条预备宝具只执行一次")
        if preparation_mode == "charge_cleanser" and (
                len(preparation_turns) != 1 or preparation_turns[0] not in phase_turns[3][:-1]):
            raise ValueError("charge_cleanser 需要在finalBarPreparationTurns中指定一个第四条血的非末尾方案回合，随后保留正常解弱化收尾步骤")
        charge_ids = raw.get("chargePriorityServantIds", [])
        if (not isinstance(charge_ids, list)
                or any(not isinstance(sid, str) or not sid.isascii() or not sid.isdigit()
                       or int(sid) <= 0 for sid in charge_ids)
                or len(set(charge_ids)) != len(charge_ids)):
            raise ValueError("chargePriorityServantIds 需要不重复的数字从者ID字符串列表，按蓄能优先顺序填写")
        waiting_cleanse = raw.get("useCleanseNpWhileWaiting", False)
        if type(waiting_cleanse) is not bool:
            raise ValueError("useCleanseNpWhileWaiting 需要true或false")
        if (charge_ids or waiting_cleanse) and not phase_turns:
            raise ValueError("蓄能优先名单与等待时辅助宝具仅支持同时填写phasePlanTurns的阶段方案")
        avoid_mighty = raw.get("avoidMightyChains", False)
        if type(avoid_mighty) is not bool:
            raise ValueError("avoidMightyChains 需要true或false")
        self_cleanses = raw.get("selfCleanseFinishers", [])
        if (not isinstance(self_cleanses, list)
                or any(not isinstance(sid, str) or sid not in SELF_CLEANSE_FINISHER_COLORS for sid in self_cleanses)
                or len(set(self_cleanses)) != len(self_cleanses)):
            raise ValueError("selfCleanseFinishers 仅支持已验证的提亚马特ID字符串1001600，不能给其他AE添加自身解弱化标签")
        if (avoid_mighty or self_cleanses) and not phase_turns:
            raise ValueError("禁止三色链与自身解弱化收尾仅支持同时填写phasePlanTurns的阶段方案")
        if self_cleanses and (not avoid_mighty or not cleanse):
            raise ValueError("selfCleanseFinishers 需要同时设置avoidMightyChains=true和requireCleanseBeforeFinish=true，不能取消解弱化守卫")
        repeat_ids = raw.get("repeatReadySkillServantIds", [])
        if (not isinstance(repeat_ids, list)
                or any(not isinstance(sid, str) or not sid.isascii() or not sid.isdigit()
                       or int(sid) <= 0 for sid in repeat_ids)
                or len(set(repeat_ids)) != len(repeat_ids)):
            raise ValueError("repeatReadySkillServantIds 需要不重复的数字从者ID字符串列表")
        if repeat_ids and not phase_turns:
            raise ValueError("按真实冷却续放技能仅支持同时填写phasePlanTurns的阶段方案")
        face_phases = raw.get("finisherFacePhases", [])
        if (not isinstance(face_phases, list) or any(type(phase) is not int or phase not in (1, 2, 3, 4)
                                                   for phase in face_phases)
                or len(set(face_phases)) != len(face_phases)):
            raise ValueError("finisherFacePhases 需要不重复的血条编号列表，支持1～4")
        if face_phases and not phase_turns:
            raise ValueError("限制破条普通卡职阶仅支持同时填写phasePlanTurns的阶段方案")
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
        for sid in self_cleanses:
            info = np_by_id.get(sid)
            if (info is None or info.role is not NpRole.ALTEREGO or not info.damaging
                    or info.color is not SELF_CLEANSE_FINISHER_COLORS[sid]):
                raise ValueError("提亚马特自身解弱化收尾需要npMetadata中已确认的B色、alterego_finisher、damaging=true资料")
        return TrialStrategyConfig(progress, cleanse, tuple(np_by_id.values()), tuple(preparation_turns),
                                   tuple(tuple(group) for group in phase_turns), max_wait, preparation_mode,
                                   tuple(charge_ids), waiting_cleanse, avoid_mighty, tuple(self_cleanses),
                                   tuple(repeat_ids), tuple(face_phases))
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
    "mighty_chain_forbidden": "本回合只能组成红、蓝、绿各一张的三色链，与本方案的禁止三色链要求冲突，已停止。请在编辑器调整固定出卡或队伍；依赖提亚马特宝具自解弱化时，不要关闭“avoidMightyChains”来绕过。",
    "fixed_finisher_face_forbidden": "方案把主攻宝具与其他职阶的固定普通卡排在同一组，可能由错误职阶抢先破条，已停止。请在编辑器该回合“宝具与色卡”中将这些普通卡改为当前血条允许的从者卡，或改成自动补位后重新导入。",
    "finisher_faces_unavailable": "当前没有足够的合法职阶普通卡与主攻宝具一起出卡，暂时不能安全破条。请等待允许的从者卡；策略会在配置的回合上限内暂扣攻击宝具，用普通卡或可用辅助宝具继续蓄能。",
}


class HellishTrialDecider:
    """Retain RuleDecider skills; strictly resolve planned Trial command cards."""

    def __init__(self, inner: RuleDecider, config: TrialStrategyConfig):
        self.inner, self.config = inner, config
        self.plan = inner.plan
        self.progress = config.progress
        self.observed = False
        self.may_advance = False
        self._phase_step = 0
        self._mapped_phase = self.progress.phase
        self._active_plan_turn = None
        self._started_skill_turns: set[int] = set()
        self._completed_plan_turns: set[int] = set()
        self._wait_counts: dict[int, int] = {}
        self._selection_mode = "normal"

    @property
    def phase_plan_enabled(self):
        return bool(self.config.phase_plan_turns)

    def plan_turn_for_phase(self, fallback):
        if not self.phase_plan_enabled:
            return fallback
        if self.plan is not None and self.plan.special_choices.configured:
            raise TrialStrategyError("trial_phase_plan_special_choices_unsupported", "该方案包含按顺序重放的技能内选项，暂时不能与按血条推进同时使用；等待或跳过阶段会让选项顺序错位。请保留必需的技能选项，删除方案JSON的maaTrialStrategy.phasePlanTurns，改用原来的逐回合方案后重新导入。")
        if not self.observed:
            raise TrialStrategyError("trial_phase_not_observed", "尚未确认敌人职阶，无法选择对应的试炼步骤。请保持主战斗或选卡页面并重试。")
        if self._mapped_phase != self.progress.phase:
            previous = self.config.phase_plan_turns[self._mapped_phase - 1]
            skipped = tuple(row for row in previous if row - 1 not in self._completed_plan_turns)
            mfaalog.info(f"[试炼阶段计划] 实际血条{self._mapped_phase}→{self.progress.phase}；跳过旧阶段未执行方案回合={skipped}，不再释放这些回合的技能")
            self._mapped_phase, self._phase_step = self.progress.phase, 0
        rows = self.config.phase_plan_turns[self.progress.phase - 1]
        index = rows[self._phase_step] - 1
        if self.plan is None or self.plan.active_turn(index) is None:
            raise TrialStrategyError("trial_phase_plan_turn_missing", f"第{self.progress.phase}条血引用的方案第{index + 1}回合不存在，无法继续。请在编队编辑器补齐该回合，或修改方案JSON的phasePlanTurns后重新导入。")
        self._active_plan_turn = index
        return index

    def _commit_plan_step(self, submission_id):
        if not self.phase_plan_enabled or self._active_plan_turn is None:
            return
        index = self._active_plan_turn
        if self._selection_mode == "opening_wait":
            mfaalog.info(f"[试炼阶段计划] 已确认开局等牌{submission_id}；保留方案回合={index + 1}，等待预算仍由开局等牌配置管理")
            return
        if self._selection_mode == "wait":
            self._wait_counts[index] = self._wait_counts.get(index, 0) + 1
            mfaalog.info(f"[试炼阶段计划] 已确认等待{submission_id}；方案回合={index + 1}，等待={self._wait_counts[index]}/{self.config.max_wait_turns_per_step}；不推进步骤")
            return
        self._completed_plan_turns.add(index)
        rows = self.config.phase_plan_turns[self._mapped_phase - 1]
        if self._phase_step + 1 < len(rows):
            self._phase_step += 1
            mfaalog.info(f"[试炼阶段计划] 已确认{submission_id}；血条={self._mapped_phase}，下个方案回合={rows[self._phase_step]}")
        else:
            mfaalog.info(f"[试炼阶段计划] 已确认{submission_id}；方案回合={index + 1}已出卡，等待真实Break后才进入下一阶段")

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
        already_confirmed = submission_id in self.progress.confirmed_submission_ids
        self.progress = confirm_submission(self.progress, state, picks, attacked_phase=phase,
                                           submission_id=submission_id, np_info=self.config.np_info)
        if not already_confirmed:
            self._commit_plan_step(submission_id)
        self.may_advance = True
        mfaalog.info(f"[试炼策略] 已确认提交 {submission_id}，血条={phase}，剩余颜色攻击={self.progress.remaining_color_attacks}")

    def _fixed_np(self, state, slot, actor=None):
        matches = [card for card in state.np_cards if card.servant_slot == slot]
        if len(matches) != 1 or not matches[0].available:
            actor_id = actor.servant_id if actor is not None else None
            visible_id = (matches[0].owner_servant_id
                          if len(matches) == 1 and matches[0].confidence.passes(.80)
                          and matches[0].owner_confidence.passes(.80) else None)
            if actor_id and visible_id and actor_id != visible_id:
                raise TrialStrategyError("trial_fixed_np_identity_unknown", f"前排{slot}的宝具归属与方案绑定的从者不一致，无法确定蓄能目标。请核对当前阵容及该回合“宝具与色卡”后重新导入。")
            charge_id = visible_id or actor_id
            if not (isinstance(charge_id, str) and charge_id.isascii() and charge_id.isdigit()
                    and int(charge_id) > 0):
                charge_id = None
            raise TrialStrategyError("trial_fixed_np_unavailable", f"本回合安排的前排 {slot} 宝具当前没有可用卡面，无法按方案出卡。请检查NP充能与封印，或调整该回合“宝具与色卡”，重新导入后再试。", charge_servant_id=charge_id)
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
        if self.config.final_bar_preparation_mode == "charge_cleanser":
            cards = turn.card_sequence
            if (len(cards) != 3 or cards[0].kind is not PrimitiveKind.SELECT_NP
                    or any(card.kind is not PrimitiveKind.SELECT_CARD or card.servant_slot not in (0, None)
                           for card in cards[1:])):
                raise TrialStrategyError("trial_preparation_charge_plan_invalid", f"{label}的蓄能预备需要第一张明确指定非AE攻击宝具，后两张设为自动补位。请在该回合“宝具与色卡”修正后重新导入；此模式只会执行一次预备宝具。")
            return
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
        if self.config.final_bar_preparation_mode == "charge_cleanser":
            planned = turn.card_sequence[0]
            pick = self._fixed_np(state, planned.servant_slot, planned.actor)
            info = self._validate_preparation_np(state, planned.servant_slot, turn_index)
            faces = self._charge_faces(state, 2, prefix_colors=(info.color,))
            if faces is None:
                raise TrialStrategyError("trial_cleanser_charge_cards_missing", "末条预备不足两张可靠的非AE普通卡，不能按安全边界蓄能。请保持现场检查发牌；本次不会改用AE普通卡或攻击宝具。")
            self._selection_mode = "preparation"
            return BattleAction(self.inner._pick_enemy_target(state, turn_index), (pick, *faces),
                                rationale_tag=f"hellish_202611:phase4:preparation_turn{turn_index + 1}:charge_cleanser")
        picks, used_faces = [], set()
        for planned in turn.card_sequence:
            if planned.kind is PrimitiveKind.SELECT_NP:
                pick = self._fixed_np(state, planned.servant_slot, planned.actor)
                self._validate_preparation_np(state, planned.servant_slot, turn_index)
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

    def _validate_preparation_np(self, state, slot, turn_index):
        card = next(card for card in state.np_cards if card.servant_slot == slot)
        info = next(info for info in self.config.np_info if info.servant_id == card.owner_servant_id)
        if self.config.final_bar_preparation_mode == "charge_cleanser" and not info.damaging:
            raise TrialStrategyError("trial_preparation_charge_np_not_attacking", "蓄能预备没有指定攻击宝具，无法执行这一步。请将该回合第一张设为已确认的非AE攻击宝具，或改回strict预备模式后重新导入。")
        servant = _servant_lookup().get(card.owner_servant_id)
        if not servant or not servant.get("class"):
            raise TrialStrategyError("trial_preparation_np_class_unknown", f"前排{slot}的宝具职阶不在本地资料中，无法确认它能用于末条预备。请更新从者资料，或将该宝具改到正常回合后重新导入。")
        # A user-editable role never overrides the verified AE class/type.
        is_ae = servant["class"] == "alterEgo"
        known_attack = "输出" in servant.get("np_type", "")
        if info.role is NpRole.ALTEREGO or is_ae and (info.damaging or known_attack):
            raise TrialStrategyError("trial_preparation_attack_np_forbidden", f"方案第{turn_index + 1}回合的末条预备安排了AE攻击宝具，不能借预备回合跳过解弱化收尾守卫。请将这次AE宝具放入正常收尾回合，或删除 finalBarPreparationTurns 中的该回合编号，再重新导入。")
        if is_ae and servant.get("np_type") != "辅助宝具":
            raise TrialStrategyError("trial_preparation_np_type_unknown", "本地资料无法确认该AE宝具是纯辅助宝具，不能用于末条预备。请更新从者资料，或将它改到正常回合后重新导入。")
        return info

    def _mighty_forbidden(self, colors):
        return (self.phase_plan_enabled and self.config.avoid_mighty_chains
                and set(colors) == {CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK})

    def _allowed_finisher_face_ids(self, state):
        if not self.phase_plan_enabled or self.progress.phase not in self.config.finisher_face_phases:
            return None
        classes = FINISHER_FACE_CLASSES[self.progress.phase]
        return tuple(sorted({card.owner_servant_id for card in state.cards
            if card.owner_servant_id and (_servant_lookup().get(card.owner_servant_id) or {}).get("class") in classes}))

    def _validate_fixed_finisher_actors(self, turn):
        if (turn is None or not self.phase_plan_enabled
                or self.progress.phase not in self.config.finisher_face_phases):
            return
        main_role = NpRole.LANCER if self.progress.phase == 1 else NpRole.ALTEREGO
        info_by_id = {info.servant_id: info for info in self.config.np_info}
        has_main = any(card.kind is PrimitiveKind.SELECT_NP and card.actor is not None
            and info_by_id.get(card.actor.servant_id) is not None
            and info_by_id[card.actor.servant_id].role is main_role for card in turn.card_sequence)
        if has_main and any(card.kind is PrimitiveKind.SELECT_CARD and card.actor is not None
                and (_servant_lookup().get(card.actor.servant_id) or {}).get("class")
                    not in FINISHER_FACE_CLASSES[self.progress.phase] for card in turn.card_sequence):
            raise TrialStrategyError("trial_fixed_finisher_face_forbidden", _SUGGESTION_ERRORS["fixed_finisher_face_forbidden"])

    def validate_chain(self, state, picks):
        if not self.phase_plan_enabled:
            return
        info_by_id = {info.servant_id: info for info in self.config.np_info}
        allowed_faces = self._allowed_finisher_face_ids(state)
        if allowed_faces is not None:
            main_role = NpRole.LANCER if self.progress.phase == 1 else NpRole.ALTEREGO
            main_slots = {card.servant_slot for card in state.np_cards
                if info_by_id.get(card.owner_servant_id) is not None
                and info_by_id[card.owner_servant_id].role is main_role}
            if any(pick.kind is PrimitiveKind.SELECT_NP and pick.slot in main_slots for pick in picks):
                faces = {card.ui_slot: card for card in state.cards}
                if any(pick.kind is PrimitiveKind.SELECT_CARD and (pick.slot not in faces
                       or faces[pick.slot].owner_servant_id not in allowed_faces) for pick in picks):
                    raise TrialStrategyError("trial_fixed_finisher_face_forbidden", _SUGGESTION_ERRORS["fixed_finisher_face_forbidden"])
        if not self.config.avoid_mighty_chains:
            return
        colors = []
        for pick in picks:
            if pick.kind is PrimitiveKind.SELECT_CARD:
                matches = [card for card in state.cards if card.ui_slot == pick.slot]
                color = matches[0].color if len(matches) == 1 else None
            else:
                matches = [card for card in state.np_cards if card.servant_slot == pick.slot]
                info = info_by_id.get(matches[0].owner_servant_id) if len(matches) == 1 else None
                color = info.color if info else None
            if color not in (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK):
                raise TrialStrategyError("trial_chain_color_unknown", "没有确认本组三张卡的完整颜色，无法执行禁止三色链检查，已停止。请保持选卡页面并检查宝具颜色资料后重试。")
            colors.append(color)
        if len(colors) != 3:
            raise TrialStrategyError("trial_chain_color_unknown", "本回合没有三张颜色明确的卡，无法执行禁止三色链检查，已停止。请检查该回合“宝具与色卡”后重试。")
        if self._mighty_forbidden(colors):
            raise TrialStrategyError("trial_mighty_chain_forbidden", _SUGGESTION_ERRORS["mighty_chain_forbidden"])

    def _charge_faces(self, state, count, *, primary_ids=(), allow_alterego=False,
                      required_color=None, spent_np_ids=(), prefix_colors=()):
        """Known faces, colour goal first, then the blocked fixed NP's Arts.

        A missing or sealed NP is merely unavailable, never proof of low NP.
        The target comes from verified NP identity or an explicit plan actor.
        Final-bar collection retains the no-AE-face boundary.
        """
        if state.scene is not Scene.COMMAND_SELECTION or not state.scene_confidence.passes(.95) or len(state.cards) != 5:
            return None
        info_by_id = {info.servant_id: info for info in self.config.np_info}
        reliable = []
        for card in state.cards:
            servant = _servant_lookup().get(card.owner_servant_id)
            info = info_by_id.get(card.owner_servant_id)
            if (type(card.ui_slot) is not int or not 1 <= card.ui_slot <= 5
                    or sum(other.ui_slot == card.ui_slot for other in state.cards) != 1
                    or type(card.owner_slot) is not int or not 1 <= card.owner_slot <= 3
                    or not card.can_attack or not card.confidence.passes(.80)
                    or not card.owner_confidence.passes(.80)
                    or card.color not in (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK)
                    or not servant or not servant.get("class")
                    or not allow_alterego and (servant["class"] == "alterEgo"
                                              or info and info.role is NpRole.ALTEREGO)):
                continue
            reliable.append(card)
        if len(reliable) < count:
            return None
        ready_ids = {card.owner_servant_id for card in state.np_cards if card.available
                     and card.confidence.passes(.80) and card.owner_confidence.passes(.80)
                     and sum(other.servant_slot == card.servant_slot for other in state.np_cards) == 1}
        primary_ids = tuple(sid for sid in primary_ids if sid not in ready_ids or sid in spent_np_ids)
        cleanse_ids = {info.servant_id for info in self.config.np_info if info.role is NpRole.CLEANSE
                       and (info.servant_id not in ready_ids or info.servant_id in spent_np_ids)}
        configured_priorities = self.config.charge_priority_servant_ids if self.phase_plan_enabled else ()
        configured_weights = {sid: len(configured_priorities) - index
                              for index, sid in enumerate(configured_priorities)
                              if sid not in ready_ids or sid in spent_np_ids}
        def score(cards):
            colour_count = sum(card.color is required_color for card in cards) if required_color else 0
            primary_arts = sum(card.owner_servant_id in primary_ids and card.color is CardColor.ARTS for card in cards)
            primary_faces = sum(card.owner_servant_id in primary_ids for card in cards)
            # Keep the available mission colour, but do not put Quick first
            # at the expense of an Arts lead and the NP owner's trailing Arts.
            return (min(colour_count, self.progress.remaining_color_attacks), primary_arts,
                    primary_faces == 3, primary_faces,
                    bool(primary_ids) and cards[0].color is CardColor.ARTS,
                    sum((index + 1) * (card.owner_servant_id in primary_ids and card.color is CardColor.ARTS)
                        for index, card in enumerate(cards)),
                    sum(configured_weights.get(card.owner_servant_id, 0)
                        for card in cards if card.color is CardColor.ARTS),
                    sum((index + 1) * configured_weights.get(card.owner_servant_id, 0)
                        for index, card in enumerate(cards) if card.color is CardColor.ARTS),
                    sum(card.owner_servant_id in cleanse_ids and card.color is CardColor.ARTS for card in cards),
                    sum(card.owner_servant_id in cleanse_ids for card in cards),
                    sum(card.color is CardColor.ARTS for card in cards),
                    sum((index + 1) * (card.owner_servant_id in cleanse_ids and card.color is CardColor.ARTS)
                        for index, card in enumerate(cards)),
                    tuple(-card.ui_slot for card in cards))
        candidates = [cards for cards in permutations(reliable, count)
                      if not self._mighty_forbidden((*prefix_colors, *(card.color for card in cards)))]
        if not candidates:
            raise TrialStrategyError("trial_mighty_chain_forbidden", _SUGGESTION_ERRORS["mighty_chain_forbidden"])
        selected = max(candidates, key=score)
        return tuple(CardPick(PrimitiveKind.SELECT_CARD, card.ui_slot) for card in selected)

    def _available_waiting_cleanse(self, state):
        if not self.phase_plan_enabled or not self.config.use_cleanse_np_while_waiting:
            return None
        cleanse_ids = {info.servant_id for info in self.config.np_info
                       if info.role is NpRole.CLEANSE and not info.damaging}
        for card in state.np_cards:
            servant = _servant_lookup().get(card.owner_servant_id)
            if (card.owner_servant_id in cleanse_ids and card.available
                    and type(card.servant_slot) is int and 1 <= card.servant_slot <= 3
                    and card.confidence.passes(.80) and card.owner_confidence.passes(.80)
                    and sum(other.servant_slot == card.servant_slot for other in state.np_cards) == 1
                    and servant and servant.get("np_type") == "辅助宝具"):
                return CardPick(PrimitiveKind.SELECT_NP, card.servant_slot)
        return None

    def _wait_for_step(self, state, turn_index, reason, *, charge_servant_id=None):
        count = self._wait_counts.get(turn_index, 0)
        if count >= self.config.max_wait_turns_per_step:
            raise TrialStrategyError("trial_step_wait_exhausted", f"方案第{turn_index + 1}回合已额外普攻等待{count}回合，仍未满足颜色或宝具条件，已停止。请检查当前手牌、NP和生存状态；需要增加等待时，在方案JSON的maxWaitTurnsPerStep中修改（0～10）后重新导入剩余方案。")
        if self.progress.phase == 4:
            picks = None
            cleanse = self._available_waiting_cleanse(state)
            if cleanse is not None:
                cleanse_id = next(card.owner_servant_id for card in state.np_cards
                                  if card.servant_slot == cleanse.slot)
                cleanse_color = next(info.color for info in self.config.np_info if info.servant_id == cleanse_id)
                try:
                    faces = self._charge_faces(state, 2,
                        primary_ids=(charge_servant_id,) if charge_servant_id else (), spent_np_ids=(cleanse_id,),
                        prefix_colors=(cleanse_color,))
                except TrialStrategyError as exc:
                    if exc.code != "trial_mighty_chain_forbidden":
                        raise
                    faces = None  # Optional protection cannot force a forbidden chain.
                if faces is not None:
                    picks = (cleanse, *faces)
            if picks is None:
                picks = self._charge_faces(state, 3, primary_ids=(charge_servant_id,) if charge_servant_id else ())
            if picks is None:
                raise TrialStrategyError("trial_cleanser_charge_cards_missing", "末条蓄能不足三张可靠的非AE普通卡，无法继续等待术呆宝具。请保持现场检查发牌；本次不会改用AE普通卡或攻击宝具。")
        else:
            result = propose_cards(state, self.progress, allow_attack_np=False, np_info=self.config.np_info,
                                   avoid_mighty_chains=self.config.avoid_mighty_chains)
            if result.waiting:
                raise TrialStrategyError(f"trial_{result.reason}", _SUGGESTION_ERRORS[result.reason])
            picks = (self._charge_faces(state, 3, primary_ids=(charge_servant_id,),
                                       allow_alterego=True, required_color=self.progress.required_color)
                     if charge_servant_id else result.picks)
            if picks is None:
                raise TrialStrategyError("trial_fewer_than_three_reliable_candidates", _SUGGESTION_ERRORS["fewer_than_three_reliable_candidates"])
            cleanse = self._available_waiting_cleanse(state)
            if cleanse is not None:
                priorities = tuple(dict.fromkeys((*( (charge_servant_id,) if charge_servant_id else ()),
                                                  *self.config.charge_priority_servant_ids)))
                protected = propose_cards(state, self.progress, fixed_prefix=(cleanse,),
                    allow_attack_np=False, np_info=self.config.np_info,
                    charge_priority_servant_ids=priorities, avoid_mighty_chains=self.config.avoid_mighty_chains)
                faces_by_slot = {card.ui_slot: card for card in state.cards}
                baseline_colours = sum(faces_by_slot[pick.slot].color is self.progress.required_color
                                       for pick in picks if pick.kind is PrimitiveKind.SELECT_CARD)
                if (not protected.waiting and min(protected.projected_color_attacks, self.progress.remaining_color_attacks)
                        >= min(baseline_colours, self.progress.remaining_color_attacks)):
                    picks = protected.picks
        self._selection_mode = "wait"
        protection = any(pick.kind is PrimitiveKind.SELECT_NP for pick in picks)
        mfaalog.info(f"[试炼阶段计划] 方案回合={turn_index + 1}等待原因={reason}，优先蓄能从者={charge_servant_id or '未指定'}；已确认等待={count}/{self.config.max_wait_turns_per_step}；辅助弱解保护={protection}，不插入攻击宝具、不推进步骤、不重复技能；宝具不可用不等于NP不足")
        self.validate_chain(state, picks)
        return BattleAction(self.inner._pick_enemy_target(state, turn_index), picks,
                            rationale_tag=f"hellish_202611:phase{self.progress.phase}:step_wait:{reason}")

    def decide(self, state: BattleState, turn_index: int = 0) -> BattleAction:
        if not self.phase_plan_enabled:
            return self._decide(state, turn_index)
        turn_index = self.plan_turn_for_phase(turn_index)
        self._validate_fixed_finisher_actors(self.plan.active_turn(turn_index))
        if state.scene is Scene.MAIN_BATTLE and turn_index in self._started_skill_turns:
            repeat = []
            for servant in state.servants:
                if (servant.servant_id not in self.config.repeat_ready_skill_servant_ids
                        or type(servant.slot) is not int or not 1 <= servant.slot <= 3
                        or sum(other.slot == servant.slot for other in state.servants) != 1
                        or not servant.confidence.passes(.80)):
                    continue
                for index, skill in enumerate(servant.skills[:3], 1):
                    if (skill.available is not True or not skill.confidence.passes(.80)
                            or servant.servant_id == "4000100" and index == 3):
                        continue
                    repeat.append(ServantSkillAction(servant.slot, index, target_ally=servant.slot))
            return BattleAction(self.inner._pick_enemy_target(state, turn_index), (),
                                servant_skills=tuple(repeat), skill_sequence=tuple(repeat),
                                rationale_tag=f"hellish_202611:step{turn_index + 1}:"
                                              + ("repeat_ready_skills" if repeat else "skills_already_executed"))
        if state.scene is Scene.COMMAND_SELECTION:
            self._selection_mode = "normal"
        try:
            action = self._decide(state, turn_index)
        except TrialStrategyError as exc:
            auto_mighty_wait = (exc.code == "trial_mighty_chain_forbidden" and exc.allow_auto_wait)
            if state.scene is Scene.COMMAND_SELECTION and (auto_mighty_wait or exc.code in (
                    "trial_fixed_np_unavailable", "trial_fixed_np_would_break_before_colour_goal",
                    "trial_cleanse_np_unavailable", "trial_finisher_faces_unavailable")):
                if auto_mighty_wait:
                    mfaalog.info("[试炼阶段计划] 当前自动补位只能与固定宝具组成三色链；暂扣攻击宝具，按原等待上限尝试安全补牌")
                return self._wait_for_step(state, turn_index, exc.code,
                                           charge_servant_id=exc.charge_servant_id)
            raise
        if state.scene is Scene.MAIN_BATTLE:
            # A failed execution aborts this runtime; no implicit retry is made.
            self._started_skill_turns.add(turn_index)
        elif state.scene is Scene.COMMAND_SELECTION:
            self.validate_chain(state, action.picks)
        return action

    def _decide(self, state: BattleState, turn_index: int = 0) -> BattleAction:
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
                               np_info=self.config.np_info,
                               charge_priority_servant_ids=(self.config.charge_priority_servant_ids
                                                            if self.phase_plan_enabled else ()),
                               avoid_mighty_chains=(self.config.avoid_mighty_chains if self.phase_plan_enabled else False),
                               self_cleanse_finishers=(self.config.self_cleanse_finishers if self.phase_plan_enabled else ()),
                               allowed_face_servant_ids=self._allowed_finisher_face_ids(state))
        if result.waiting:
            message = _SUGGESTION_ERRORS.get(result.reason, "当前出卡条件无法确认，已保留现场停止。请检查当前血条及导入方案后重试。")
            # Only this failed ordinary proposal can defer an explicit auto
            # hole. Fixed triples, preparation errors and the last input guard
            # retain their hard stop; no selected card/identity is weakened.
            auto_hole = bool(turn and any(card.kind is PrimitiveKind.SELECT_CARD
                and (card.servant_slot is None or type(card.servant_slot) is int and card.servant_slot == 0)
                and card.color is None and card.actor is None for card in turn.card_sequence))
            raise TrialStrategyError(f"trial_{result.reason}", message,
                allow_auto_wait=(self.phase_plan_enabled and self.config.avoid_mighty_chains
                                 and auto_hole and result.reason == "mighty_chain_forbidden"))
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
        self._trial_seen_cleanser_slots: dict[str, int] = {}
        self._trial_reading_real_skills = False

    def run(self) -> BattleResult:
        try:
            return super().run()
        except TrialStrategyError as exc:
            mfaalog.error(f"[试炼策略] {exc}")
            return BattleResult.fail(exc.code, self._trial_confirmed_turns)

    def _plan_turn_after_observation(self, plan_turn, state):
        if state.scene not in (Scene.MAIN_BATTLE, Scene.COMMAND_SELECTION):
            return plan_turn
        return self.decider.plan_turn_for_phase(plan_turn)

    def _has_active_plan(self):
        if self._trial_reading_real_skills:
            return False  # Repeated rows must observe real cooldowns, never patched availability.
        return super()._has_active_plan()

    def _plan_turn_after_settlement(self, plan_turn):
        if self.decider.phase_plan_enabled:
            return self.decider.plan_turn_for_phase(plan_turn)
        return super()._plan_turn_after_settlement(plan_turn)

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
        # This flag controls perception only. Fixed Master skills/order changes
        # must still belong to an active plan after decide marks a row started.
        self._trial_reading_real_skills = bool(self.decider.phase_plan_enabled
            and self.decider.config.repeat_ready_skill_servant_ids
            and self._turn_index in self.decider._started_skill_turns)
        try:
            state = super()._observe(settle_main=settle_main, enemy_slots=enemy_slots)
        finally:
            self._trial_reading_real_skills = False
        self._check_cleanser_survival(state)
        if state.scene in (Scene.MAIN_BATTLE, Scene.COMMAND_SELECTION):
            phase = self._read_phase()
            if self._trial_pending is None:
                self.decider.observe_phase(phase)
            else:
                old = self._trial_pending.phase
                if phase not in (old, old + 1) or self._trial_deferred_phase == old + 1 and phase == old:
                    raise TrialStrategyError("trial_phase_discontinuity", "出卡后的敌人职阶变化不连续，无法确认血条进度，已停止。请核对实际血条，在方案 maaTrialStrategy 中修正恢复起点后重新导入。")
                self._trial_deferred_phase = phase
            if self.decider.phase_plan_enabled and self._trial_pending is None:
                mapped = self.decider.plan_turn_for_phase(self._turn_index)
                if mapped != self._turn_index:
                    self._turn_index = mapped
                    if state.scene is Scene.MAIN_BATTLE and self._has_active_plan():
                        # A Break may only become visible on this first new
                        # main frame, after the parent's availability decision.
                        state = self._patch_skills_available(state)
        if state.scene is Scene.COMMAND_SELECTION:
            self._trial_selection_state = state
        return state

    def _check_cleanser_survival(self, state):
        """Stop on positive replacement evidence, not absence from one hand."""
        if (not self.decider.phase_plan_enabled or not self.decider.config.require_cleanse_before_finish
                or state.scene is not Scene.MAIN_BATTLE or not state.scene_confidence.passes(.95)):
            return
        if any(sid in self.roster_servant_ids for sid in self.decider.config.self_cleanse_finishers):
            return  # The explicitly verified self-cleansing finisher replaces external cleansing.
        cleanse_ids = {info.servant_id for info in self.decider.config.np_info
                       if info.role is NpRole.CLEANSE}
        visible = {servant.slot: servant for servant in state.servants
                   if servant.confidence.passes(max(.80, self.profile.min_skill_confidence))
                   and servant.servant_id and _servant_lookup().get(servant.servant_id)}
        for slot, servant in visible.items():
            if servant.servant_id in cleanse_ids:
                self._trial_seen_cleanser_slots[servant.servant_id] = slot
        for sid, previous_slot in self._trial_seen_cleanser_slots.items():
            if sid in self.roster_servant_ids:
                continue  # An explicit order change may keep the cleanser in reserve.
            replacement = visible.get(previous_slot)
            if replacement is not None and replacement.servant_id != sid:
                raise TrialStrategyError("trial_required_cleanser_replaced", f"负责解弱化的{servant_name(sid)}原位置已确认换成其他从者，当前队伍也没有这位从者，无法按本方案完成无焦热收尾，已在后续技能前停止。请撤退重试并提前安排保护技能；如要换解弱化方案，请在编辑器重新编排后再导入。")

    def _execute_selection(self, action) -> bool:
        picks = tuple(action.picks)
        blocked = {card.ui_slot for card in self._selection_cards if not card.can_attack}
        normalized = tuple(self._normalize_picks_for_execution(picks, blocked_face_slots=blocked))
        if len(picks) != 3 or normalized != picks or self._trial_selection_state is None:
            raise TrialStrategyError("trial_selection_not_exact", "本回合没有形成三张有效且顺序明确的卡，无法安全提交。请检查该回合“宝具与色卡”及卡牌封印状态后重试。")
        self.decider.validate_chain(self._trial_selection_state, picks)
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
                               np_info=self.decider.config.np_info,
                               avoid_mighty_chains=(self.decider.config.avoid_mighty_chains
                                                   if self.decider.phase_plan_enabled else False))
        if result.waiting:
            raise TrialStrategyError(f"trial_{result.reason}", _SUGGESTION_ERRORS[result.reason])
        self.decider._selection_mode = "opening_wait"
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
