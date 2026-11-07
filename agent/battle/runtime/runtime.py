"""回合循环（按真实两屏流程 + 长动画宽容等待）。

流程：
  MAIN_BATTLE --点攻击--> COMMAND_SELECTION --选3张--> 自动发动 -> 等待攻击动画 -> 回主界面/胜利

设计要点：
- 攻击动画期间画面既非主界面也非选卡，会读成 UNKNOWN；这段是"宽容等待"（轮询到已知场景/超时），
  **不走 fail-closed 停止**，否则每回合攻击都会被误中止。
- 决策/校验失败、开卡/选卡确认失败、真正卡死超时才停止。
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, replace

from ..core.decider import Decider, RuleDecider
from ..core.special_skills import SpecialSkillChoiceError
from .special_skills import SpecialSkillDispatcher
from ..core.enums import PrimitiveKind, Scene
from ..core.models import (ActorBinding, BattlePlan, BattleState, CardPick, InitialFormation,
                           MasterSkillAction, OrderChangeAction, RetireAction,
                           ServantSkillAction, VERIFIED_FORMATION_SOURCE,
                           CONTINUOUS_FORMATION_SOURCE, is_slot)
from ..core.policy import BattlePolicy, Goal, StrategyProfile
from ..core.validator import (
    skip_unusable_servant_skills,
    validate_card_action,
    validate_main_action,
)
from ..execution.executor import Executor
from ..data.servant_identity import canonical_servant_id, servant_name
from ..perception import perception
import mfaalog
from platform_waits import platform_timeout

# 选完卡后等攻击动画结束（部分长演出可能超过 60s，留足余量）
_ANIMATION_TIMEOUT_S = 120.0
# 意外 UNKNOWN（加载等）的最大等待
_UNKNOWN_TIMEOUT_S = 30.0
# 每次轮询之间等画面静止的窗口（ms）
_POLL_FREEZE_MS = 2000
# 每次选卡后的固定间隔，等待卡牌选中态渲染
_PICK_DELAY_S = 0.3
# 主战斗界面首次出现后，等待技能 CD 数字渲染完成再重新截图感知。
_MAIN_BATTLE_SETTLE_S = 1.0
_NP_GLARE_MAX_FRAMES = 3

# —— 等待上限（秒）：安卓为慢速 OCR 留余量；识别成功立即继续 ——
_OPEN_CARDS_TIMEOUT_S = 15.0        # 手机单轮 OCR 可耗时 5～6 秒，允许至少两轮确认选卡
_OPEN_CARDS_RETRY = 6               # 攻击点击被覆盖层吞掉时的重试次数
_SKILL_TARGET_TIMEOUT_S = 5.0       # 技能目标子屏出现
_SKILL_ANIM_TIMEOUT_S = platform_timeout(15.0, 30.0)        # 施放技能后动画结束回到主界面
_MASTER_SKILL_RETURN_TIMEOUT_S = platform_timeout(10.0, 20.0)  # 御主技能后回主界面或进换人界面
_ORDER_CHANGE_OPEN_TIMEOUT_S = platform_timeout(10.0, 20.0)    # 换人界面出现
_ORDER_CHANGE_RETURN_TIMEOUT_S = platform_timeout(25.0, 45.0)  # 换人完成后回主界面
_ENEMY_TARGET_READY_TIMEOUT_S = platform_timeout(5.0, 20.0)   # 动画收尾后敌方目标可确认

_TERMINAL_OR_MAIN = (Scene.MAIN_BATTLE, Scene.VICTORY, Scene.DEFEAT)
_KNOWN_SCENES = (Scene.MAIN_BATTLE, Scene.COMMAND_SELECTION, Scene.ORDER_CHANGE, Scene.VICTORY, Scene.DEFEAT)


@dataclass(frozen=True)
class BattleResult:
    ok: bool
    reason: str = ""
    turns: int = 0

    @staticmethod
    def success(turns: int) -> "BattleResult":
        return BattleResult(True, "victory", turns)

    @staticmethod
    def fail(reason: str, turns: int = 0) -> "BattleResult":
        return BattleResult(False, reason, turns)


class AutoBattleRuntime:
    # 没有编队证据时保留通用选卡；每次更新都替换元组，不共享可变阵容。
    roster_servant_ids: tuple[str | None, ...] = (None,) * 6
    candidate_servant_ids: tuple[str | None, ...] = ()
    servant_recognizer = None

    def __init__(self, context, controller, decider: Decider, profile: StrategyProfile,
                 battle_policy: BattlePolicy | None = None, *,
                 initial_formation: InitialFormation | None = None,
                 formation_battle: int = 1,
                 roster_servant_ids: tuple[str | None, ...] | None = None) -> None:
        self.ctx = context
        self.controller = controller
        self.decider = decider
        self.profile = profile
        self.battle_policy = battle_policy or BattlePolicy()
        # 这是初始编队证据，不参与当前回合 owner_slot 或换人/退场推断。
        self.initial_formation = initial_formation
        self.initial_formation_reused = (
            initial_formation is not None and (
                initial_formation.captured_battle != formation_battle
                or initial_formation.source == CONTINUOUS_FORMATION_SOURCE
            )
        )
        if roster_servant_ids is None and initial_formation is not None:
            roster_servant_ids = tuple(
                slot.servant_id if slot.status == "identified" else None
                for slot in initial_formation.slots
            )
        if roster_servant_ids is None or len(roster_servant_ids) != 6:
            roster_servant_ids = (None,) * 6
        self.roster_servant_ids = tuple(roster_servant_ids)
        self.candidate_servant_ids = self.roster_servant_ids
        self._initial_servant_ids = self.roster_servant_ids
        self._member_order = list(range(6))
        self._retired_instances = set()
        self._special_skill_servant_ids, self._special_skill_scope_reason = self._special_skill_roster()
        self.servant_recognizer = None
        self.executor = Executor(context, controller)
        self._turn_index = 0
        self._selection_cards = ()

    def _has_active_plan(self) -> bool:
        plan = getattr(self.decider, "plan", None)
        return (isinstance(plan, BattlePlan)
                and plan.active_turn(self._turn_index) is not None)

    def run(self) -> BattleResult:
        scope = getattr(self, "_special_skill_servant_ids", None)
        mfaalog.info(
            f"[AutoBattle] 特殊技能检测范围={'完整兜底' if scope is None else ','.join(sorted(scope))} "
            f"reason={getattr(self, '_special_skill_scope_reason', 'no_actual_formation')}"
        )
        if self.initial_formation is not None:
            initial = self.initial_formation
            mfaalog.info(
                f"[AutoBattle] initial_formation task={initial.task_id} session={initial.session_id} "
                f"revision={initial.revision} captured_at={initial.captured_at} "
                f"reused={self.initial_formation_reused} source={initial.source}; "
                "初始编队仅供身份候选，当前前排仍按本场画面识别"
            )
        else:
            mfaalog.info("[AutoBattle] initial_formation=none; 使用通用策略")
        mfaalog.info(
            "[AutoBattle] roster_servant_ids="
            f"{tuple(self.roster_servant_ids)}; 卡牌归属缺证据时保持 unknown"
        )
        mfaalog.info(f"[AutoBattle] run() start, max_turns={self.profile.max_turns}")
        turns = 0
        plan_turn = 0
        opening_waits = 0
        native = self.battle_policy.card.native_strategy
        opening = native.opening_hand
        opening_pending = native.enabled and opening.enabled
        opening_used = opening_pending
        opening_preview_started = False
        opening_plan = getattr(self.decider, "plan", None)
        if opening_pending and (
            not opening.required_colors or not 0 <= opening.max_wait_turns <= 5
            or not (opening.servant_id or opening.servant_slot)
            or not isinstance(opening_plan, BattlePlan) or opening_plan.active_turn(0) is None
        ):
            return BattleResult.fail("invalid_opening_hand_config_or_missing_plan", turns)
        while turns < self.profile.max_turns:
            self._turn_index = plan_turn
            state = self._observe(settle_main=True)
            scene = state.scene
            mfaalog.info(f"[AutoBattle] Turn {turns+1} | scene={scene.name} | unknown={state.unknown_fields}")

            if scene is Scene.VICTORY:
                # 战斗已胜。结算多屏（奖励/好友申请/连续出击等）不在 Python 侧处理，
                # 由 pipeline 的 结束战斗_* 节点家族负责推进（见 原生自动战斗调度.json）。
                mfaalog.info(f"[AutoBattle] Victory! turns={turns} -> settlement handled by pipeline")
                return BattleResult.success(turns)
            if scene is Scene.DEFEAT:
                # 战斗失败同样交给 pipeline 的 战斗失败_不回主界面 撤退分支处理，
                # 避免 custom action 失败导致 auto_battle_repeat 中断整条多场链。
                mfaalog.info(f"[AutoBattle] Defeat. turns={turns} -> retreat handled by pipeline")
                return BattleResult(True, "defeat", turns)
            if scene is Scene.DIALOG:
                mfaalog.info(f"[AutoBattle] Unexpected dialog. turns={turns}")
                return BattleResult.fail("unexpected_dialog", turns)

            if scene is Scene.MAIN_BATTLE:
                if opening_used and plan_turn == 0 and not self._opening_roster_unchanged():
                    return BattleResult.fail("opening_hand_roster_changed_or_unknown", turns)
                if opening_pending and plan_turn == 0:
                    # Observe the real five cards before the first skill. The
                    # existing Executor owns Attack; the return lives in Pipeline.
                    mfaalog.info(f"[OpeningHand] 实际回合{turns+1}，释放任何计划技能前查看手牌")
                    if not self.executor.open_command_cards():
                        return BattleResult.fail("opening_hand_open_failed", turns)
                    if not self._wait_until((Scene.COMMAND_SELECTION,), _OPEN_CARDS_TIMEOUT_S):
                        return BattleResult.fail("opening_hand_open_confirm_failed", turns)
                    opening_preview_started = True
                    continue
                mfaalog.info("[AutoBattle] MAIN_BATTLE -> deciding skills...")
                action = self.decider.decide(state, turn_index=plan_turn)
                action, skipped_skills = skip_unusable_servant_skills(
                    action, state, self.profile
                )
                for reason in skipped_skills:
                    mfaalog.info(f"[AutoBattle] servant skill skipped: {reason}")
                verdict = validate_main_action(action, state, self.profile)
                if not verdict.ok:
                    if verdict.fatal:
                        mfaalog.info(f"[AutoBattle] Main action rejected: {verdict.reason}")
                        return BattleResult.fail(
                            f"invalid_main_action:{verdict.reason}", turns
                        )
                    mfaalog.info(
                        "[AutoBattle] Main action validation warning; "
                        f"continuing: {verdict.reason}"
                    )

                if action.target_enemy is not None:
                    enemy = next(
                        (
                            e for e in state.enemies
                            if e.slot == action.target_enemy and e.alive
                        ),
                        None,
                    )
                    if enemy is None:
                        mfaalog.info(
                            "[AutoBattle] target enemy "
                            f"{action.target_enemy} not detected; skip target click"
                        )
                        enemy = None

                    if enemy is None:
                        pass
                    elif enemy.targeted:
                        mfaalog.info(
                            f"[AutoBattle] enemy {action.target_enemy} already targeted, skip click"
                        )
                    else:
                        mfaalog.info(
                            f"[AutoBattle] selecting enemy target {action.target_enemy}"
                        )
                        self._mark_action("select_enemy")
                        if not self.executor.select_enemy(action.target_enemy):
                            mfaalog.info("[AutoBattle] select_enemy failed; continuing")
                        if not self._wait_until_enemy_targeted(action.target_enemy):
                            mfaalog.info(
                                "[AutoBattle] enemy target confirmation failed; continuing"
                            )

                if not self._execute_skills(action):
                    return BattleResult.fail("skill_execution_failed", turns)

                mfaalog.info("[AutoBattle] MAIN_BATTLE -> opening command cards (click attack)")
                opened = False
                for attempt in range(1, _OPEN_CARDS_RETRY + 1):
                    self._mark_action("open_command_cards")
                    if not self.executor.open_command_cards():
                        mfaalog.info(f"[AutoBattle] open_command_cards failed. turns={turns}")
                        return BattleResult.fail("open_cards_failed", turns)
                    mfaalog.info("[AutoBattle] command cards clicked, confirming command selection scene...")
                    if self._wait_until((Scene.COMMAND_SELECTION,), _OPEN_CARDS_TIMEOUT_S):
                        opened = True
                        break
                    # 竞态防护：技能覆盖层收尾时主界面模板会弱命中（如 0.82），
                    # 攻击点击被残留覆盖层吞掉。确认仍在主界面后重试点击。
                    img = self.controller.post_screencap().wait().get()
                    scene = perception.detect_scene(self.ctx, img)
                    mfaalog.info(
                        f"[AutoBattle] attack click not confirmed "
                        f"(attempt {attempt}/{_OPEN_CARDS_RETRY}, scene={scene.name}); retrying"
                    )
                    if scene is not Scene.MAIN_BATTLE:
                        break  # 场景已变化（弹窗/目标子屏等），交回主循环处理
                if not opened:
                    mfaalog.info("[AutoBattle] command selection confirmation failed; stopping safely")
                    return BattleResult.fail("open_cards_confirm_failed", turns)
                mfaalog.info("[AutoBattle] command cards opened and confirmed")
                continue

            if scene is Scene.ORDER_CHANGE:
                # 换人界面：不应在主循环顶层出现，说明 _execute_skills 没处理完
                mfaalog.info(f"[AutoBattle] Unexpected ORDER_CHANGE scene in main loop. turns={turns}")
                return BattleResult.fail("unexpected_order_change_scene", turns)

            if scene is Scene.COMMAND_SELECTION:
                waiting_action = None
                if opening_pending and plan_turn == 0:
                    if not opening_preview_started:
                        return BattleResult.fail("opening_hand_requires_main_battle_start", turns)
                    if not self._opening_roster_unchanged():
                        return BattleResult.fail("opening_hand_roster_changed_or_unknown", turns)
                    ready, reason, target_id, target_slot = self._opening_hand_matches(state, opening)
                    mfaalog.info(f"[OpeningHand] 实际回合{turns+1} 逻辑回合1 等待={opening_waits}/{opening.max_wait_turns} "
                                 f"目标={target_id or target_slot} required={opening.required_colors} ready={ready} reason={reason}")
                    if reason != "known_hand":
                        return BattleResult.fail(f"opening_hand_{reason}", turns)
                    if ready:
                        detail = self.ctx.run_task("战斗_选卡返回主界面")
                        if not (detail and detail.status.succeeded):
                            return BattleResult.fail("opening_hand_return_failed", turns)
                        opening_pending = False
                        continue
                    if opening_waits >= opening.max_wait_turns:
                        return BattleResult.fail("opening_hand_wait_exhausted", turns)
                    waiting_action = self._opening_wait_action(state, target_id, target_slot)
                if waiting_action is None and not self._check_card_actor_bindings():
                    return BattleResult.fail("card_actor_identity_mismatch", turns)
                mfaalog.info(f"[AutoBattle] ========== Turn {turns+1} ==========")
                self._selection_cards = state.cards
                cards_text = " | ".join(
                    f"卡{card.ui_slot}:{servant_name(card.owner_servant_id)}/{card.color.value}"
                    f"(前排{card.owner_slot or '?'},归属{card.owner_confidence.value:.3f},"
                    f"分差{card.owner_margin:.3f},{'可攻击' if card.can_attack else '封印/无法行动'})"
                    for card in state.cards
                )
                mfaalog.diagnostic(f"[英灵选卡] {cards_text}")
                self._selection_np_cards = state.np_cards
                mfaalog.diagnostic("[宝具选卡] " + " | ".join(
                    f"宝具{card.servant_slot}:{servant_name(card.owner_servant_id)}"
                    f"({'可用' if card.available else card.blocked_reason},卡面={card.confidence.value:.3f})"
                    for card in state.np_cards
                ))
                mfaalog.diagnostic(f"[AutoBattle] State: {state}")
                action = waiting_action or self.decider.decide(state, turn_index=plan_turn)
                mfaalog.info(f"[AutoBattle] Decided Action: {action}")
                
                verdict = validate_card_action(action, state, self.profile)
                if not verdict.ok and verdict.fatal:
                    mfaalog.info(f"[AutoBattle] Card action rejected: {verdict.reason}")
                    return BattleResult.fail(
                        f"invalid_card_action:{verdict.reason}", turns
                    )
                if not verdict.ok and verdict.reason == "np_not_present":
                    action = self._replace_unavailable_np_picks(action, state)
                    verdict = validate_card_action(action, state, self.profile)
                    mfaalog.info(
                        "[AutoBattle] unavailable NP removed; "
                        f"revalidated={verdict.ok} reason={verdict.reason}"
                    )
                if not verdict.ok:
                    if verdict.fatal:
                        mfaalog.info(f"[AutoBattle] Card action rejected: {verdict.reason}")
                        return BattleResult.fail(
                            f"invalid_card_action:{verdict.reason}", turns
                        )
                    mfaalog.info(
                        "[AutoBattle] Card action validation warning; "
                        f"continuing: {verdict.reason}"
                    )
                    
                mfaalog.info("[AutoBattle] Executing picks...")
                if not self._execute_selection(action):
                    mfaalog.info("[AutoBattle] Execution failed (confirmation error).")
                    return BattleResult.fail("selection_confirm_failed", turns)
                mfaalog.info("[AutoBattle] Picks executed, waiting for attack animation to settle...")
                # 选完第 3 张自动发动 -> 等动画结束
                if not self._wait_turn_settled():
                    reason = getattr(self, "_post_attack_failure_reason", "stuck_after_attack")
                    mfaalog.error(f"[AutoBattle] 出卡后等待失败: {reason}；保留当前画面停止。 turns={turns}")
                    return BattleResult.fail(reason, turns)
                if self._post_attack_returned_to_map:
                    # 剧情后直接返回地图的关卡没有普通结算；交回调用方复核，
                    # 不将剧情按钮本身视为胜利，也不进入下一回合。
                    return BattleResult(True, "story_returned_to_map", turns + 1)
                mfaalog.info(f"[AutoBattle] Turn {turns+1} settled, advancing to turn {turns+2}")
                turns += 1
                if waiting_action is not None:
                    opening_waits += 1
                else:
                    plan_turn += 1
                continue

            # UNKNOWN / ANIMATION（非攻击后语境，如加载）：有界等待
            mfaalog.info(f"[AutoBattle] Unknown scene, waiting up to {_UNKNOWN_TIMEOUT_S}s for known scene...")
            if not self._wait_until(_KNOWN_SCENES, _UNKNOWN_TIMEOUT_S):
                mfaalog.info(f"[AutoBattle] Stuck in unknown scene for {_UNKNOWN_TIMEOUT_S}s. turns={turns}")
                return BattleResult.fail("stuck_unknown_scene", turns)
            mfaalog.info("[AutoBattle] Recovered from unknown scene, continuing")

        mfaalog.info(f"[AutoBattle] Max turns ({self.profile.max_turns}) exceeded")
        return BattleResult.fail("max_turns_exceeded", turns)

    # ---- 内部 ----

    def _opening_roster_unchanged(self):
        self._ensure_member_tracking()
        expected = tuple(canonical_servant_id(sid) for sid in self._initial_servant_ids[:3])
        actual = tuple(canonical_servant_id(sid) for sid in self.roster_servant_ids[:3])
        return (any(expected) and expected == actual and not self._retired_instances
                and self._member_order[:3] == [0, 1, 2])

    def _opening_hand_matches(self, state, opening):
        from ..core.enums import CardColor
        target_id = canonical_servant_id(opening.servant_id) if opening.servant_id else None
        target_slot = opening.servant_slot
        roster = tuple(canonical_servant_id(sid) for sid in self.roster_servant_ids[:3])
        if target_id:
            slots = [slot for slot, sid in enumerate(roster, 1) if sid == target_id]
            if len(slots) != 1:
                return False, "target_identity_unknown", target_id, target_slot
            target_slot = slots[0]
        elif not is_slot(target_slot, 1, 3) or roster[target_slot - 1] is None:
            return False, "target_identity_unknown", target_id, target_slot
        if (not state.cards_ready(self.profile.min_card_confidence)
                or not state.card_owners_ready(.80)
                or any(card.color not in (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK) for card in state.cards)
                or target_id and any(canonical_servant_id(card.owner_servant_id) != roster[card.owner_slot - 1]
                                     for card in state.cards)):
            return False, "hand_identity_or_color_unknown", target_id, target_slot
        available = Counter(card.color for card in state.cards if card.owner_slot == target_slot and card.can_attack)
        needed = Counter(opening.required_colors)
        return all(available[color] >= count for color, count in needed.items()), "known_hand", target_id, target_slot

    def _opening_wait_action(self, state, target_id, target_slot):
        from ..core.enums import CardColor
        policy = self.battle_policy.card
        native = policy.native_strategy
        ids = tuple(dict.fromkeys(((target_id,) if target_id else ()) + native.preferred_servant_ids))
        slots = tuple(dict.fromkeys((target_slot,) + native.preferred_slots))
        recovery = replace(policy, goal=Goal.BUILD_NP, np_first=False,
                           color_priority=(CardColor.ARTS, CardColor.QUICK, CardColor.BUSTER),
                           native_strategy=replace(native, preferred_servant_ids=ids, preferred_slots=slots,
                                                   color_weights=((CardColor.ARTS, 4), (CardColor.QUICK, 2), (CardColor.BUSTER, 1))))
        action = RuleDecider(recovery).decide(state)
        return replace(action, rationale_tag="opening_hand:wait_without_skills_or_np")

    def _observe(self, *, settle_main: bool = False, enemy_slots=None):
        mfaalog.diagnostic("[AutoBattle] _observe() -> post_screencap")
        img = self.controller.post_screencap().wait().get()
        scene = perception.detect_scene(self.ctx, img) if settle_main else None
        if scene is Scene.MAIN_BATTLE:
            mfaalog.diagnostic(
                f"[AutoBattle] MAIN_BATTLE detected; waiting {_MAIN_BATTLE_SETTLE_S}s "
                "before skill CD recognition"
            )
            time.sleep(_MAIN_BATTLE_SETTLE_S)
            img = self.controller.post_screencap().wait().get()
        elif scene is Scene.COMMAND_SELECTION:
            # The upper NP cards finish appearing after the five face cards.
            # Keep the timing in the native pipeline rather than using NP bars
            # to predict cards that are not yet visible.
            detail = self.ctx.run_task("战斗_选卡展开等待")
            if not (detail and detail.status.succeeded):
                raise RuntimeError("command_card_settle_failed")
            img = self.controller.post_screencap().wait().get()
        frontline = self.roster_servant_ids[:3]
        candidates = self.candidate_servant_ids or self.roster_servant_ids
        perception_args = {}
        if any(candidates):
            if self.servant_recognizer is None:
                self.servant_recognizer = perception.FrontlineServantRecognizer.from_context(self.ctx)
            perception_args = dict(frontline_servant_ids=frontline,
                                   candidate_servant_ids=candidates,
                                   servant_recognizer=self.servant_recognizer)
        if enemy_slots is not None:
            # 目标复核仍取新截图，只省去另外两个无关敌人的 OCR。
            perception_args["enemy_slots"] = enemy_slots
        result = perception.build(self.ctx, img, **perception_args)
        for frame_index in range(1, _NP_GLARE_MAX_FRAMES):
            uncertain = tuple(field for field in getattr(result, "unknown_fields", ())
                              if field.startswith("np[") and field.endswith(".presence:glare"))
            if result.scene is not Scene.COMMAND_SELECTION or not uncertain:
                break
            if self.ctx.tasker.stopping:
                raise RuntimeError("np_glare_retry_stopped")
            mfaalog.diagnostic(f"[NPGlare] frame={frame_index}/{_NP_GLARE_MAX_FRAMES} "
                         f"uncertain={uncertain}; 保持0.80门槛，等待新相位后重拍")
            detail = self.ctx.run_task("战斗_宝具闪光重拍等待")
            if not (detail and detail.status.succeeded):
                raise RuntimeError("np_glare_retry_wait_failed")
            img = self.controller.post_screencap().wait().get()
            # Use only the latest complete frame. Do not carry an available NP
            # over a newer seal/identity change, another turn, or another battle.
            result = perception.build(self.ctx, img, **perception_args)
        unresolved = tuple(field for field in getattr(result, "unknown_fields", ())
                           if field.startswith("np[") and field.endswith(".presence:glare"))
        if result.scene is Scene.COMMAND_SELECTION and unresolved:
            mfaalog.error(f"[NPGlare] {_NP_GLARE_MAX_FRAMES}帧仍无法确认 {unresolved}，保留选卡现场停止")
            raise RuntimeError("np_glare_unresolved")
        if result.scene is Scene.MAIN_BATTLE and any(candidates):
            self._update_frontline(result.servants)

        # 当前回合有 Chaldea 计划时：技能 CD 检测无意义（计划已精确指定何时放技能），
        # 且 ColorMatch ROI 可能因分辨率/缩放偏移导致误判为 CD。
        # 将所有技能标记为可用，避免被安全门过滤掉。
        if self._has_active_plan():
            result = self._patch_skills_available(result)
            mfaalog.diagnostic("[AutoBattle] _observe() -> plan active, present servants' skills patched to available")

        mfaalog.diagnostic(f"[AutoBattle] _observe() -> perception built, scene={result.scene.name}")
        return result

    def _update_frontline(self, servants) -> None:
        identities = {servant.slot: servant.servant_id for servant in servants}
        frontline = tuple(identities.get(slot) for slot in (1, 2, 3))
        previous = self.roster_servant_ids[:3]
        if frontline == previous:
            return
        reserves = list(self.roster_servant_ids[3:])
        for before, after in zip(previous, frontline):
            if after is not None and after != before and after in reserves:
                reserves.remove(after)
        self.roster_servant_ids = frontline + tuple(reserves) + (None,) * (3 - len(reserves))
        mfaalog.info(
            "[当前前排] " + " | ".join(
                f"{slot}:{servant_name(sid) if sid else '未识别，跳过技能'}"
                for slot, sid in enumerate(frontline, start=1)
            )
        )

    def _patch_skills_available(self, state: BattleState) -> BattleState:
        """将已识别在场的从者技能和御主技能标记为可用，保留空位过滤。"""
        from dataclasses import replace as dc_replace

        # 从者技能
        patched_servants = tuple(
            dc_replace(srv, skills=tuple(
                dc_replace(sk, available=True)
                for sk in srv.skills
            ))
            for srv in state.servants
        )
        # 御主技能
        patched_master = tuple(
            dc_replace(sk, available=True)
            for sk in state.master_skills
        )
        return dc_replace(
            state,
            servants=patched_servants,
            master_skills=patched_master,
        )

    def _mark_action(self, action: str) -> None:
        mfaalog.info(f"[AutoBattle] action: {action}")

    def _execute_selection(self, action) -> bool:
        picks = self._normalize_picks_for_execution(
            action.picks,
            blocked_face_slots={card.ui_slot for card in self._selection_cards if not card.can_attack},
        )
        cards = {card.ui_slot: card for card in self._selection_cards}
        selected = []
        for pick in picks:
            if pick.kind is PrimitiveKind.SELECT_NP:
                selected.append(f"宝具{pick.slot}")
            else:
                card = cards.get(pick.slot)
                selected.append(
                    f"卡{pick.slot}:{servant_name(card.owner_servant_id)}/{card.color.value}"
                    if card else f"卡{pick.slot}"
                )
        mfaalog.info(f"[英灵选卡] 实际出卡={' → '.join(selected)}")
        mfaalog.info(f"[AutoBattle] _execute_selection() picks={picks}")
        for index, p in enumerate(picks):
            if p.kind is PrimitiveKind.SELECT_NP:
                mfaalog.info(f"[AutoBattle] select_np(slot={p.slot})")
                ok = self.executor.select_np(p.slot)
            else:
                mfaalog.info(f"[AutoBattle] select_card(slot={p.slot})")
                ok = self.executor.select_card(p.slot)
            mfaalog.info(f"[AutoBattle] pick result: ok={ok}")
            if not ok:
                return False
            # 最后一张会自动发动攻击，不需要等待
            if index < len(picks) - 1:
                time.sleep(_PICK_DELAY_S)
        return True

    def _normalize_picks_for_execution(self, picks, *, blocked_face_slots=()) -> tuple[CardPick, ...]:
        """Keep command-card execution moving when perception returns partial picks."""
        normalized: list[CardPick] = []
        selected_face_slots: set[int] = set()
        selected_np_slots: set[int] = set()

        for pick in picks:
            if len(normalized) >= 3:
                break
            if type(pick.slot) is not int:
                continue
            if pick.kind is PrimitiveKind.SELECT_NP and 1 <= pick.slot <= 3:
                if pick.slot in selected_np_slots:
                    continue
                selected_np_slots.add(pick.slot)
                normalized.append(pick)
                continue
            if pick.kind is PrimitiveKind.SELECT_CARD and 1 <= pick.slot <= 5:
                if pick.slot in selected_face_slots:
                    continue
                selected_face_slots.add(pick.slot)
                normalized.append(pick)

        if len(normalized) < 3:
            mfaalog.info(
                "[AutoBattle] command picks incomplete after validation warning; "
                "padding with face cards"
            )
        for slot in sorted(range(1, 6), key=lambda slot: slot in blocked_face_slots):
            if len(normalized) >= 3:
                break
            if slot in selected_face_slots:
                continue
            selected_face_slots.add(slot)
            normalized.append(CardPick(PrimitiveKind.SELECT_CARD, slot))

        return tuple(normalized)

    @staticmethod
    def _replace_unavailable_np_picks(action, state):
        """Replace NP picks missing from state with detected face cards."""
        available_np_slots = {card.servant_slot for card in state.np_cards if card.available}
        available_face_slots = sorted(state.cards, key=lambda card: not card.can_attack)
        picks = [
            pick
            for pick in action.picks
            if pick.kind is not PrimitiveKind.SELECT_NP
            or pick.slot in available_np_slots
        ]
        selected_face_slots = {
            pick.slot
            for pick in picks
            if pick.kind is PrimitiveKind.SELECT_CARD
        }
        for card in available_face_slots:
            slot = card.ui_slot
            if len(picks) >= 3:
                break
            if slot in selected_face_slots:
                continue
            picks.append(CardPick(PrimitiveKind.SELECT_CARD, slot))
            selected_face_slots.add(slot)
        return replace(action, picks=tuple(picks[:3]))

    def _wait_until_enemy_targeted(self, slot: int, timeout_s: float = platform_timeout(3.0, 20.0)) -> bool:
        """点击敌人后确认职介框蓝色选中态已经出现。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.ctx.tasker.stopping:
                return False
            state = self._observe(enemy_slots=(slot,))
            if self.ctx.tasker.stopping:
                return False
            enemy = next((e for e in state.enemies if e.slot == slot and e.alive), None)
            if (state.scene is Scene.MAIN_BATTLE
                    and state.scene_confidence.passes(self.profile.min_scene_confidence)
                    and enemy is not None and enemy.targeted
                    and enemy.confidence.passes(self.profile.min_enemy_confidence)):
                mfaalog.info(f"[AutoBattle] enemy {slot} target confirmed")
                return True
            time.sleep(0.5)
        mfaalog.info(f"[AutoBattle] enemy {slot} target confirmation timed out")
        return False

    def _ensure_enemy_target(self, slot: int, *, label: str = "select_skill_enemy",
                             timeout_s: float = _ENEMY_TARGET_READY_TIMEOUT_S) -> bool:
        """等待动画收尾再尽力选敌；选不到时警告并继续，仅非法目标或停止返回失败。"""
        if not is_slot(slot, 1, 3) or self.ctx.tasker.stopping:
            return False

        def skip_target(reason: str) -> bool:
            if self.ctx.tasker.stopping:
                return False
            phase = "恢复攻击目标" if label == "restore_attack_enemy" else "技能选敌"
            mfaalog.warning(
                f"[AutoBattle] {phase}：{reason}；跳过本次选敌，沿用游戏当前目标继续执行。"
            )
            return True

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.ctx.tasker.stopping:
                return False
            state = self._observe(enemy_slots=(slot,))
            if self.ctx.tasker.stopping:
                return False
            if (state.scene is Scene.MAIN_BATTLE
                    and state.scene_confidence.passes(self.profile.min_scene_confidence)):
                enemy = next((e for e in state.enemies if e.slot == slot and e.alive), None)
                if enemy is not None and enemy.confidence.passes(self.profile.min_enemy_confidence):
                    if enemy.targeted:
                        mfaalog.info(f"[AutoBattle] {label}: enemy {slot} already targeted")
                        return True
                    self._mark_action(label)
                    if not self.executor.select_enemy(slot):
                        return skip_target(f"点击敌人{slot}未成功")
                    confirmed = self._wait_until_enemy_targeted(slot)
                    if confirmed:
                        return not self.ctx.tasker.stopping
                    return skip_target(f"未能确认敌人{slot}已选中")
                # 前一个伤害/即死技能可能已经击倒目标。主界面已稳定时，
                # 不再等待该位置重新出现，也不因此跳过后续技能或攻击。
                if enemy is None:
                    return skip_target(f"未识别到敌人{slot}，可能已被击倒或尚未识别到")
                return skip_target(f"敌人{slot}的识别结果可信度不足，无法确认目标")
            # 主界面模板在技能动画收尾可能仅弱命中（如0.82）；有界等强帧。
            time.sleep(0.5)
        return skip_target(f"等待战斗主界面超时，未能选择敌人{slot}")

    def _execute_skills(self, action) -> bool:
        plan = getattr(self.decider, "plan", None)
        if (isinstance(plan, BattlePlan) and plan.special_choices.configured
                and 0 <= self._turn_index < len(plan.turns)):
            if getattr(self, "_special_choice_dispatcher", None) is None:
                self._special_choice_dispatcher = SpecialSkillDispatcher(plan.special_choices)
            try:
                self._special_choice_dispatcher.check_skipped(
                    plan.turns[self._turn_index], action, self._semantic_roster(),
                )
            except SpecialSkillChoiceError as exc:
                mfaalog.error(f"[AutoBattle] Chaldea 特殊技能选择失败: {exc}")
                return False
        # 日志：输出实际要执行的技能队列（安全门过滤后）
        sequence = action.skill_sequence or (
            *action.servant_skills, *action.master_skills,
            *((action.order_change,) if action.order_change is not None else ()),
        )
        if sequence:
            parts = []
            for step in sequence:
                if isinstance(step, ServantSkillAction):
                    target = f"->从者{step.target_ally}" if step.target_ally else ""
                    target += f"->敌人{step.target_enemy}" if step.target_enemy else ""
                    parts.append(f"从者{step.servant_slot}技能{step.skill_index}{target}")
                elif isinstance(step, MasterSkillAction):
                    target = f"->从者{step.target_ally}" if step.target_ally else ""
                    target += f"->敌人{step.target_enemy}" if step.target_enemy else ""
                    parts.append(f"御主技能{step.skill_index}{target}")
                elif isinstance(step, OrderChangeAction):
                    parts.append(f"换人: {step.starting_member_idx}↔{step.sub_member_idx}")
                elif isinstance(step, RetireAction):
                    parts.append(f"等待从者{step.servant_slot}自然退场")
            mfaalog.info(f"[AutoBattle] 执行技能队列: {' | '.join(parts)}")

        for index, step in enumerate(sequence):
            if isinstance(step, RetireAction):
                if not self._execute_retirement(step):
                    return False
                continue
            if isinstance(step, ServantSkillAction):
                sk = step
                if not self._actor_matches(sk.actor, sk.servant_slot):
                    return False
                if not (
                    is_slot(sk.servant_slot, 1, 3)
                    and is_slot(sk.skill_index, 1, 3)
                    and (sk.target_ally is None or is_slot(sk.target_ally, 1, 3))
                ):
                    mfaalog.info(f"[AutoBattle] invalid servant skill skipped: {sk}")
                    continue
                if sk.target_enemy is not None and not self._ensure_enemy_target(sk.target_enemy):
                    return False
                mfaalog.info(f"[AutoBattle] cast_servant_skill(slot={sk.servant_slot}, idx={sk.skill_index})")
                if not self._execute_skill_cast(
                    "cast_servant_skill",
                    lambda: self.executor.cast_servant_skill(sk.servant_slot, sk.skill_index),
                    sk.target_ally,
                    (Scene.MAIN_BATTLE,),
                    _SKILL_ANIM_TIMEOUT_S,
                    default_target=sk.servant_slot,
                    skill=sk,
                ):
                    return False
                continue

            if isinstance(step, MasterSkillAction):
                sk = step
                if not self._has_active_plan() and not self.battle_policy.skill.use_master_skills:
                    mfaalog.info(f"[AutoBattle] master skill skipped (use_master_skills=False): idx={sk.skill_index}")
                    continue
                if not (
                    is_slot(sk.skill_index, 1, 3)
                    and (sk.target_ally is None or is_slot(sk.target_ally, 1, 3))
                ):
                    mfaalog.info(f"[AutoBattle] invalid master skill skipped: {sk}")
                    continue
                if sk.target_enemy is not None and not self._ensure_enemy_target(sk.target_enemy):
                    return False
                next_is_swap = (
                    index + 1 < len(sequence)
                    and isinstance(sequence[index + 1], OrderChangeAction)
                )
                mfaalog.info(f"[AutoBattle] cast_master_skill(idx={sk.skill_index})")
                if not self._execute_skill_cast(
                    "cast_master_skill",
                    lambda: self.executor.cast_master_skill(sk.skill_index),
                    sk.target_ally,
                    (Scene.ORDER_CHANGE, Scene.MAIN_BATTLE) if next_is_swap else (Scene.MAIN_BATTLE,),
                    _MASTER_SKILL_RETURN_TIMEOUT_S if next_is_swap else _SKILL_ANIM_TIMEOUT_S,
                ):
                    return False
                continue

            if not isinstance(step, OrderChangeAction):
                continue
            oc = step
            if not (
                is_slot(oc.starting_member_idx, 1, 3)
                and is_slot(oc.sub_member_idx, 4, 6)
            ):
                mfaalog.info(f"[AutoBattle] invalid order change skipped: {oc}")
                continue
            self._ensure_member_tracking()
            if self._member_order[oc.sub_member_idx - 1] in self._retired_instances:
                mfaalog.error("[AutoBattle] 已退场从者不能再次换入；未点击换人")
                return False
            mfaalog.info(f"[AutoBattle] order_change(starting={oc.starting_member_idx}, sub={oc.sub_member_idx})")
            # 换人技能已由前面的 master_skills 触发（御主换人服技能）
            # 等待换人界面出现
            if not self._wait_until((Scene.ORDER_CHANGE,), _ORDER_CHANGE_OPEN_TIMEOUT_S):
                mfaalog.info("[AutoBattle] failed to see order change screen!")
                return False
            # 在换人界面选择首发成员和候补成员
            self._mark_action("order_change")
            if not self.executor.order_change(oc.starting_member_idx, oc.sub_member_idx):
                return False
            # 主界面模板可能在换人动画尚未结束时已命中；先确认技能栏稳定，
            # 再变更身份并发送新成员的技能，避免把点击发进动画。
            if not self._wait_order_change_settled():
                return False
            front = oc.starting_member_idx - 1
            back = oc.sub_member_idx - 1
            self._ensure_member_tracking()
            self._member_order[front], self._member_order[back] = self._member_order[back], self._member_order[front]
            roster = list(self.roster_servant_ids)
            roster[front], roster[back] = roster[back], roster[front]
            self.roster_servant_ids = tuple(roster)
            mfaalog.info(
                "[AutoBattle] order change roster updated: "
                f"{tuple(self.roster_servant_ids)}"
            )

        # run() retains the original pre-skill attack-target selection. A new
        # per-skill override can change it; try restoring the attack group's
        # chosen target after all skills, before opening the command cards. Old plans
        # without a per-skill target keep their original observation/click path.
        if action.target_enemy is not None and any(
            isinstance(step, (ServantSkillAction, MasterSkillAction))
            and step.target_enemy is not None for step in sequence
        ):
            return self._ensure_enemy_target(action.target_enemy, label="restore_attack_enemy")
        return True

    def _ensure_member_tracking(self):
        if not hasattr(self, "_member_order"):
            self._member_order = list(range(6))
            self._retired_instances = set()
            self._initial_servant_ids = tuple(self.roster_servant_ids)

    def _semantic_roster(self):
        self._ensure_member_tracking()
        return tuple(self._initial_servant_ids[i] if i not in self._retired_instances else None
                     for i in self._member_order)

    def _actor_matches(self, actor, slot, *, check_visible=True):
        if actor is None:
            return True  # Legacy imports describe current UI positions.
        if not isinstance(actor, ActorBinding):
            return False
        self._ensure_member_tracking()
        initial = actor.initial_slot
        expected = canonical_servant_id(actor.servant_id)
        ok = (expected is not None and is_slot(slot, 1, 3) and type(initial) is int and 0 <= initial <= 5
              and initial not in self._retired_instances and self._member_order[slot - 1] == initial
              and canonical_servant_id(self._initial_servant_ids[initial]) == expected
              and (not check_visible or canonical_servant_id(self.roster_servant_ids[slot - 1]) == expected))
        if not ok:
            mfaalog.error(f"[AutoBattle] 动作绑定从者不在当前槽位或已退场；停止，未点击技能/卡片: slot={slot}, actor={actor}")
        return ok

    def _check_card_actor_bindings(self):
        plan = getattr(self.decider, "plan", None)
        turn = plan.active_turn(self._turn_index) if isinstance(plan, BattlePlan) else None
        return turn is None or all(self._actor_matches(card.actor, card.servant_slot)
                                   for card in turn.card_sequence if card.actor is not None)

    def _execute_retirement(self, step):
        """Confirm a natural replacement without sending any game action."""
        if (not isinstance(step.actor, ActorBinding)
                or not self._actor_matches(step.actor, step.servant_slot, check_visible=False)):
            return False
        self._ensure_member_tracking()
        front = step.servant_slot - 1
        back = next((i for i in range(3, 6)
                     if self._member_order[i] not in self._retired_instances
                     and self._initial_servant_ids[self._member_order[i]] is not None), None)
        if back is None:
            mfaalog.error("[AutoBattle] 从者退场没有已确认身份的存活候补；停止")
            return False
        order = list(self._member_order)
        outgoing = order[front]
        order[front] = order.pop(back)
        order.append(outgoing)
        expected = canonical_servant_id(self._initial_servant_ids[order[front]])
        if expected == canonical_servant_id(step.actor.servant_id):
            mfaalog.error("[AutoBattle] 同名从者递补缺少可区分的视觉身份，不能确认退场；停止")
            return False
        self.servant_recognizer = None
        self._selection_cards = ()
        self._selection_np_cards = ()
        deadline = time.monotonic() + _ORDER_CHANGE_RETURN_TIMEOUT_S
        while not self.ctx.tasker.stopping and time.monotonic() < deadline:
            if not self._wait_order_change_settled(timeout=max(0.0, deadline - time.monotonic())):
                return False
            if self.ctx.tasker.stopping:
                return False
            state = self._observe()
            if self.ctx.tasker.stopping or time.monotonic() >= deadline:
                return False
            servant = next((s for s in state.servants if s.slot == step.servant_slot), None)
            if (state.scene is Scene.MAIN_BATTLE and servant is not None
                    and servant.confidence.passes(self.profile.min_skill_confidence)
                    and canonical_servant_id(servant.servant_id) == expected):
                self._member_order = order
                self._retired_instances.add(outgoing)
                # Use initial instances, not _observe's already-updated reserve;
                # this also handles a death observed during the preceding skill.
                observed = {s.slot: s for s in state.servants}
                frontline = tuple(
                    canonical_servant_id(observed[slot].servant_id)
                    if slot in observed and observed[slot].confidence.passes(self.profile.min_skill_confidence)
                    else None for slot in (1, 2, 3)
                )
                # Another member may have died during the same animation.
                # The marker only advances its declared instance: never turn
                # missing/different live identities back into plan evidence.
                self.roster_servant_ids = frontline + tuple(self._initial_servant_ids[i] for i in order[3:])
                self.candidate_servant_ids = tuple(self._initial_servant_ids[i]
                                                   for i in order if i not in self._retired_instances)
                self._selection_cards = ()
                self._selection_np_cards = ()
                self.servant_recognizer = None
                mfaalog.info(f"[AutoBattle] 自然退场已确认: initialSlot={outgoing}, incoming={expected}, order={order}")
                return True
        mfaalog.error("[AutoBattle] 未确认预期候补入场；保留画面停止，不发送后续技能")
        return False

    def _wait_order_change_settled(self, *, timeout=None) -> bool:
        """只为换人等待主界面/技能栏稳定；失败或停止时不提交新阵容。"""
        if self.ctx.tasker.stopping:
            return False
        deadline = time.monotonic() + (_ORDER_CHANGE_RETURN_TIMEOUT_S if timeout is None else timeout)
        try:
            mfaalog.diagnostic("[AutoBattle] order change waiting for stable skill bar")
            detail = self.ctx.run_task("战斗_换人后等待技能栏稳定")
            if self.ctx.tasker.stopping or not detail or not detail.status.succeeded:
                return False
            node = self.ctx.get_node_object("战斗_换人后主界面与技能栏稳定")
            param = node.post_wait_freezes
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if (self.ctx.tasker.stopping or param is None or param.time <= 0
                    or param.timeout <= 0 or remaining_ms < param.time):
                return False
            # MFW 5.14.3 的节点 post_wait_freezes 超时不会使 run_task 失败。
            # 用相同 Pipeline 参数再做有明确 bool 结果的严格确认，预算共用。
            stable = self.ctx.wait_freezes(
                wait_freezes_param=replace(param, timeout=min(param.timeout, remaining_ms)),
            )
            if not stable or self.ctx.tasker.stopping or time.monotonic() > deadline:
                return False
            mfaalog.diagnostic("[AutoBattle] order change skill bar stable")
            return True
        except Exception as exc:
            mfaalog.warning(f"[AutoBattle] order change stability check failed: {exc}")
            return False

    # 特殊技能子流水线入口与分支归类（自动战斗_特殊技能.json）
    _SPECIAL_SKILL_PIPELINE = "战斗_特殊技能处理"
    # 命中这些分支 = 技能未生效（弹窗关闭），应跳过该技能
    _SKILL_SKIP_BRANCHES = ("战斗_技能使用弹窗", "战斗_技能无法使用弹窗")
    # 命中此分支 = 技能直接释放（无任何覆盖层），无需额外处理
    _SKILL_NO_OVERLAY_BRANCH = "战斗_特殊技能_无覆盖层"
    # 命中这些分支 = 需要选目标，从 action 参数指定槽位（通过 pipeline_override 控制）
    _SKILL_TARGET_BRANCH = "战斗_技能目标子屏"

    def _special_skill_roster(self) -> tuple[frozenset[str] | None, str]:
        """只有本场完整实际快照能证明从者不在队内；空位不等于未知。"""
        initial = self.initial_formation
        if initial is None:
            return None, "no_actual_formation"
        if self.initial_formation_reused:
            return None, "formation_not_reconfirmed_this_battle"
        if (initial.source not in ("formation_confirmation", VERIFIED_FORMATION_SOURCE)
                or initial.calibration_id in ("", "unverified")):
            return None, "formation_not_calibrated"
        if any(slot.status not in {"identified", "empty"} for slot in initial.slots):
            return None, "formation_contains_unknown"
        actual = tuple(canonical_servant_id(slot.servant_id) for slot in initial.slots)
        merged = tuple(canonical_servant_id(sid) for sid in self.roster_servant_ids)
        if actual != merged or any(slot.status == "identified" and sid is None
                                   for slot, sid in zip(initial.slots, actual)):
            return None, "formation_roster_conflict"
        return frozenset(sid for sid in actual if sid is not None), "complete_actual_formation"

    def _special_skill_pipeline_override(self) -> dict:
        """只裁剪本次子任务入口，不改变节点 enabled 或 Resource 中的 next。"""
        scope = getattr(self, "_special_skill_servant_ids", None)
        if scope is None:
            return {}
        try:
            node = self.ctx.get_node_data(self._SPECIAL_SKILL_PIPELINE) or {}
        except Exception as exc:
            mfaalog.warning(f"[AutoBattle] 特殊技能范围配置读取失败，保留完整检测: {exc}")
            return {}
        owners = (node.get("attach") or {}).get("special_skill_servant_ids")
        branches = node.get("next")
        if not isinstance(owners, dict) or not isinstance(branches, list):
            return {}
        kept = []
        common = {*self._SKILL_SKIP_BRANCHES, self._SKILL_TARGET_BRANCH, self._SKILL_NO_OVERLAY_BRANCH}
        for branch in branches:
            name = branch.get("name") if isinstance(branch, dict) else branch
            ids = owners.get(name) if isinstance(name, str) else None
            # 无所属声明/非法声明时不排除未来新节点，避免更新元数据前漏检。
            if not isinstance(name, str) or name in common or not isinstance(ids, list) or not ids:
                kept.append(branch)
                continue
            normalized = tuple(canonical_servant_id(sid) for sid in ids)
            if any(sid is None for sid in normalized) or scope.intersection(normalized):
                kept.append(branch)
        return {self._SPECIAL_SKILL_PIPELINE: {"next": kept}}

    def _execute_skill_cast(self, label, cast_callable, target_ally, return_scenes, return_timeout,
                            default_target: int = 1, *, skill=None):
        """通用技能执行核：cast → sleep 0.6s → 子流水线处理特殊覆盖层 → 等回主界面。

        特殊覆盖层（弹窗/目标选择/专属技能流程）的识别与点击已下沉到
        自动战斗_特殊技能.json，next 列表按序识别自动路由。
        返回 True 表示本技能处理完成（含"触发弹窗而跳过"）；False 表示卡死/确认失败。
        """
        self._mark_action(label)
        cast_callable()
        time.sleep(0.6)
        # 子流水线：处理技能点击后可能出现的特殊覆盖层
        # 注意：选从者1/2/3 均为 DirectHit 节点（必命中），必须显式禁用
        # 未选中的两个节点，否则 next 列表按序识别永远先命中「选从者1」
        target = target_ally if target_ally is not None else default_target
        override = {
            f"{self._SKILL_TARGET_BRANCH}_选从者{slot}": (
                {} if slot == target else {"enabled": False}
            )
            for slot in (1, 2, 3)
        }
        try:
            entry = (self._run_special_skill_pipeline(override, skill=skill) if skill is not None
                     else self._run_special_skill_pipeline(override))
        except SpecialSkillChoiceError as exc:
            mfaalog.error(f"[AutoBattle] Chaldea 特殊技能选择失败: {exc}")
            return False
        if entry in self._SKILL_SKIP_BRANCHES:
            mfaalog.info(f"[AutoBattle] special dialog '{entry}' handled; skill skipped")
            return True
        if entry == self._SKILL_NO_OVERLAY_BRANCH:
            mfaalog.info("[AutoBattle] skill cast directly (no overlay); waiting main battle")
        elif entry is not None:
            mfaalog.info(f"[AutoBattle] special skill branch '{entry}' executed")
        return self._wait_until(return_scenes, return_timeout, tap_close=True)

    def _run_special_skill_pipeline(self, pipeline_override=None, *, skill=None):
        """运行特殊技能子流水线，返回命中分支节点名（entry）；异常/未命中返回 None。

        next 末尾的 DirectHit 兜底节点保证「直接释放」也在一轮识别内正常结束，
        只有识别轮全部失败（如截图异常）才会 failed。子流水线内所有分支都只
        处理覆盖层本身（关弹窗/选目标/选选项），不等待回主界面——那由外层
        _wait_until 负责。
        """
        plan = getattr(self.decider, "plan", None)
        use_choices = (isinstance(skill, ServantSkillAction) and isinstance(plan, BattlePlan)
                       and plan.special_choices.configured and 0 <= self._turn_index < len(plan.turns))
        try:
            override = self._special_skill_pipeline_override()
            override.update(pipeline_override or {})
            if use_choices:
                dispatcher = getattr(self, "_special_choice_dispatcher", None)
                if dispatcher is None:
                    dispatcher = self._special_choice_dispatcher = SpecialSkillDispatcher(plan.special_choices)
                sid = self.roster_servant_ids[skill.servant_slot - 1]
                detail = dispatcher.run(self.ctx, override, servant_id=canonical_servant_id(sid),
                                        skill_index=skill.skill_index)
            else:
                detail = self.ctx.run_task(self._SPECIAL_SKILL_PIPELINE,
                                           pipeline_override=override)
        except SpecialSkillChoiceError:
            raise
        except Exception as e:
            if use_choices:
                raise SpecialSkillChoiceError(f"特殊技能流水线异常，无法确认已配置的选项: {e}") from e
            mfaalog.error(f"[AutoBattle] special skill pipeline error: {e}")
            return None
        if detail is None:
            return None
        if detail.status.failed:
            mfaalog.info("[AutoBattle] special skill pipeline: no overlay matched")
            return None
        # detail.entry 恒为入口名（战斗_特殊技能处理），真正命中的分支
        # 要看最后执行的节点：nodes 非空取末尾节点名，否则回退 entry
        nodes = getattr(detail, "nodes", None) or []
        last = nodes[-1] if nodes else None
        return getattr(last, "name", None) or getattr(detail, "entry", None)

    def _wait_turn_settled(self) -> bool:
        self._post_attack_returned_to_map = False
        self._post_attack_failure_reason = "stuck_after_attack"
        mfaalog.diagnostic(f"[AutoBattle] _wait_turn_settled() timeout={_ANIMATION_TIMEOUT_S}s")
        getter = getattr(self.ctx, "get_node_data", None)
        guard = getter("结算-羁绊满停止") if getter else None
        preserve_bond_page = isinstance(guard, dict) and guard.get("enabled") is True
        # 羁绊守卫开启时，UNKNOWN可能正处于结算淡入；盲点会在识别前跳过羁绊页。
        # 明确的剧情仍由剧情分支处理，战后页面在羁绊检查完成后交回结算流水线。
        result = self._wait_until(_TERMINAL_OR_MAIN, _ANIMATION_TIMEOUT_S,
                                  tap_close=not preserve_bond_page, handle_story=True,
                                  command_selection_timeout_s=3.0)
        mfaalog.diagnostic(f"[AutoBattle] _wait_turn_settled() result={result}")
        return result

    def _handle_post_attack_story(self, image) -> bool | None:
        """None=不是剧情，True=已处理/按选项等待，False=处理失败。"""
        name = "跳过剧情-点击跳过"
        enabled = (self.ctx.get_node_data(name) or {}).get("enabled", True)
        # 即使用户关闭自动跳过，也识别剧情以阻止右上角盲点；仅临时启用识别。
        result = self.ctx.run_recognition(name, image,
                                         pipeline_override={name: {"enabled": True}})
        if not result or not result.hit:
            return None
        if not enabled:
            return True
        mfaalog.info("[AutoBattle] 出卡后检测到剧情，处理跳过后继续等待战斗或结算")
        detail = self.ctx.run_task("战斗-出卡后剧情处理", pipeline_override={
            name: {"next": ["跳过剧情-确认跳过", "跳过剧情-确认跳过-已消失"]},
            "跳过剧情-确认跳过": {"next": ["战斗-剧情切换等待"]},
            "跳过剧情-确认跳过-已消失": {
                "next": ["战斗-剧情切换等待"], "on_error": []
            },
        })
        ok = bool(detail and detail.status.succeeded and not self.ctx.tasker.stopping)
        if not ok:
            mfaalog.error("[AutoBattle] 出卡后剧情处理失败，保留现场停止等待")
        return ok

    def _wait_until(self, scenes, timeout_s: float, tap_close: bool = False,
                    handle_story: bool = False,
                    command_selection_timeout_s: float | None = None) -> bool:
        mfaalog.diagnostic(f"[AutoBattle] _wait_until() scenes={[s.name for s in scenes]} timeout={timeout_s}s")
        deadline = time.monotonic() + timeout_s
        story_seen = False
        map_since = None
        command_selection_since = None
        command_selection_observations = 0
        while time.monotonic() < deadline:
            if self.ctx.tasker.stopping:
                mfaalog.info("[AutoBattle] _wait_until() detected stop signal, aborting wait")
                return False
            img = self.controller.post_screencap().wait().get()
            if img is None:
                mfaalog.error("[AutoBattle] 等待界面时截图失败")
                return False
            # 换人只由明确的换人技能等待触发；普通出卡/技能返回不轮询大范围 OCR。
            scene = (perception.detect_scene(self.ctx, img) if Scene.ORDER_CHANGE in scenes
                     else perception.detect_scene(self.ctx, img, check_order_change=False))
            if scene in scenes:
                mfaalog.diagnostic(f"[AutoBattle] _wait_until() matched scene={scene.name}")
                return True
            if command_selection_timeout_s is not None and scene is Scene.COMMAND_SELECTION:
                now = time.monotonic()
                if command_selection_since is None:
                    command_selection_since = now
                command_selection_observations += 1
                if (command_selection_observations >= 2
                        and now - command_selection_since >= command_selection_timeout_s):
                    self._post_attack_failure_reason = "card_selection_not_submitted"
                    mfaalog.error(
                        "[AutoBattle] 三次出卡操作后仍持续停留在选卡界面，"
                        "出卡选择未完成；保留画面停止，不自动补卡。"
                    )
                    return False
                # 卡牌未提交时不点击关闭或重试，避免更改用户当前选择。
                time.sleep(0.5)
                continue
            command_selection_since = None
            command_selection_observations = 0
            if handle_story and scene is Scene.UNKNOWN:
                handled = self._handle_post_attack_story(img)
                if handled is False:
                    return False
                if handled is True:
                    story_seen = True
                    map_since = None
                    time.sleep(0.5)
                    continue
            if handle_story and story_seen:
                # 剧情后有些关卡直接返回地图。两种独立底栏特征均命中且
                # 稳定 3 秒才交回调度；短暂露出地图不能算完成。
                returned = all((r := self.ctx.run_recognition(name, img)) and r.hit
                               for name in ("可能已经结束战斗_不回主界面", "战斗结束-主界面"))
                if returned:
                    if map_since is None:
                        map_since = time.monotonic()
                    if time.monotonic() - map_since >= 3:
                        self._post_attack_returned_to_map = True
                        mfaalog.info("[AutoBattle] 剧情后已稳定返回地图，交回战后调度复核")
                        return True
                else:
                    map_since = None
            # 默认点击右上角推进奖励等界面；实际剧情由前面的分支按开关处理。
            if tap_close and not story_seen:
                self.executor.tap_top_right_close()
            time.sleep(0.5)
        mfaalog.info(f"[AutoBattle] _wait_until() timed out")
        return False
