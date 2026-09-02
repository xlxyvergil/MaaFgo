"""回合循环（按真实两屏流程 + 长动画宽容等待）。

流程：
  MAIN_BATTLE --点攻击--> COMMAND_SELECTION --选3张--> 自动发动 -> 20~40s 动画 -> 回主界面/胜利

设计要点：
- 攻击动画期间画面既非主界面也非选卡，会读成 UNKNOWN；这段是"宽容等待"（轮询到已知场景/超时），
  **不走 fail-closed 停止**，否则每回合攻击都会被误中止。
- 决策/校验失败、开卡/选卡确认失败、真正卡死超时才停止。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from ..core.decider import Decider
from ..core.enums import PrimitiveKind, Scene
from ..core.models import BattleState, CardPick, is_slot
from ..core.policy import BattlePolicy, StrategyProfile
from ..core.validator import (
    skip_unusable_servant_skills,
    validate_card_action,
    validate_main_action,
)
from ..execution.executor import Executor
from ..perception import perception
import mfaalog

# 选完卡后等攻击动画结束（20~40s，留足余量）
_ANIMATION_TIMEOUT_S = 60.0
# 意外 UNKNOWN（加载等）的最大等待
_UNKNOWN_TIMEOUT_S = 30.0
# 每次轮询之间等画面静止的窗口（ms）
_POLL_FREEZE_MS = 2000
# 胜利后结算点击流（掉落/羁绊/结果多屏）的最大耗时
_SETTLEMENT_TIMEOUT_S = 90.0
# 每次选卡后的固定间隔，等待卡牌选中态渲染
_PICK_DELAY_S = 0.3

# —— 等待超时（秒）：均为真机验证过的正常时序；异常才用这些上限判定卡死 ——
_OPEN_CARDS_TIMEOUT_S = 5.0         # 点击攻击后确认进入选卡界面
_SKILL_TARGET_TIMEOUT_S = 5.0       # 技能目标子屏出现
_SKILL_ANIM_TIMEOUT_S = 15.0        # 施放技能后动画结束回到主界面
_MASTER_SKILL_RETURN_TIMEOUT_S = 10.0  # 御主技能后回主界面或进换人界面
_ORDER_CHANGE_OPEN_TIMEOUT_S = 10.0    # 换人界面出现
_ORDER_CHANGE_RETURN_TIMEOUT_S = 25.0  # 换人完成后回主界面

_TERMINAL_OR_MAIN = (Scene.MAIN_BATTLE, Scene.VICTORY, Scene.DEFEAT)
_KNOWN_SCENES = (Scene.MAIN_BATTLE, Scene.COMMAND_SELECTION, Scene.SKILL_TARGET_SELECTION, Scene.ORDER_CHANGE, Scene.VICTORY, Scene.DEFEAT)

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
    def __init__(self, context, controller, decider: Decider, profile: StrategyProfile,
                 battle_policy: BattlePolicy | None = None) -> None:
        self.ctx = context
        self.controller = controller
        self.decider = decider
        self.profile = profile
        self.battle_policy = battle_policy or BattlePolicy()
        self.executor = Executor(context, controller)
        self._turn_index = 0

    def run(self) -> BattleResult:
        mfaalog.info(f"[AutoBattle] run() start, max_turns={self.profile.max_turns}")
        turns = 0
        while turns < self.profile.max_turns:
            self._turn_index = turns
            state = self._observe()
            scene = state.scene
            mfaalog.info(f"[AutoBattle] Turn {turns+1} | scene={scene.name} | unknown={state.unknown_fields}")

            if scene is Scene.VICTORY:
                mfaalog.info(f"[AutoBattle] Victory! turns={turns} -> driving settlement")
                return self._drive_settlement(turns)
            if scene is Scene.DEFEAT:
                mfaalog.info(f"[AutoBattle] Defeat. turns={turns}")
                return BattleResult.fail("defeat", turns)
            if scene is Scene.DIALOG:
                mfaalog.info(f"[AutoBattle] Unexpected dialog. turns={turns}")
                return BattleResult.fail("unexpected_dialog", turns)

            if scene is Scene.MAIN_BATTLE:
                mfaalog.info("[AutoBattle] MAIN_BATTLE -> deciding skills...")
                action = self.decider.decide(state, turn_index=turns)
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
                self._mark_action("open_command_cards")
                if not self.executor.open_command_cards():
                    mfaalog.info(f"[AutoBattle] open_command_cards failed. turns={turns}")
                    return BattleResult.fail("open_cards_failed", turns)
                mfaalog.info("[AutoBattle] command cards clicked, confirming command selection scene...")
                if not self._wait_until((Scene.COMMAND_SELECTION,), _OPEN_CARDS_TIMEOUT_S):
                    mfaalog.info("[AutoBattle] command selection confirmation failed; stopping safely")
                    return BattleResult.fail("open_cards_confirm_failed", turns)
                mfaalog.info("[AutoBattle] command cards opened and confirmed")
                continue

            if scene is Scene.ORDER_CHANGE:
                # 换人界面：不应在主循环顶层出现，说明 _execute_skills 没处理完
                mfaalog.info(f"[AutoBattle] Unexpected ORDER_CHANGE scene in main loop. turns={turns}")
                return BattleResult.fail("unexpected_order_change_scene", turns)

            if scene is Scene.COMMAND_SELECTION:
                mfaalog.info(f"[AutoBattle] ========== Turn {turns+1} ==========")
                mfaalog.info(f"[AutoBattle] State: {state}")
                action = self.decider.decide(state, turn_index=turns)
                mfaalog.info(f"[AutoBattle] Decided Action: {action}")
                
                verdict = validate_card_action(action, state, self.profile)
                if not verdict.ok and verdict.fatal:
                    mfaalog.info(f"[AutoBattle] Card action rejected: {verdict.reason}")
                    return BattleResult.fail(
                        f"invalid_card_action:{verdict.reason}", turns
                    )
                has_plan = hasattr(self.decider, 'plan') and self.decider.plan is not None
                if not verdict.ok and verdict.reason == "np_not_present" and not has_plan:
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
                    mfaalog.info(f"[AutoBattle] Stuck after attack (no scene change within {_ANIMATION_TIMEOUT_S}s). turns={turns}")
                    return BattleResult.fail("stuck_after_attack", turns)
                mfaalog.info(f"[AutoBattle] Turn {turns+1} settled, advancing to turn {turns+2}")
                turns += 1
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

    def _observe(self):
        mfaalog.info("[AutoBattle] _observe() -> post_screencap")
        img = self.controller.post_screencap().wait().get()
        result = perception.build(self.ctx, img)

        # 有 Chaldea 计划时：技能 CD 检测无意义（计划已精确指定何时放技能），
        # 且 ColorMatch ROI 可能因分辨率/缩放偏移导致误判为 CD。
        # 将所有技能标记为可用，避免被安全门过滤掉。
        has_plan = hasattr(self.decider, 'plan') and self.decider.plan is not None
        if has_plan:
            result = self._patch_skills_available(result)
            mfaalog.info("[AutoBattle] _observe() -> plan active, all skills patched to available")

        mfaalog.info(f"[AutoBattle] _observe() -> perception built, scene={result.scene.name}")
        return result

    def _patch_skills_available(self, state: BattleState) -> BattleState:
        """将所有从者技能和御主技能标记为可用。"""
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
        picks = self._normalize_picks_for_execution(action.picks)
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

    def _normalize_picks_for_execution(self, picks) -> tuple[CardPick, ...]:
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
        for slot in range(1, 6):
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
        available_np_slots = {card.servant_slot for card in state.np_cards}
        available_face_slots = {card.ui_slot for card in state.cards}
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
        for slot in sorted(available_face_slots):
            if len(picks) >= 3:
                break
            if slot in selected_face_slots:
                continue
            picks.append(CardPick(PrimitiveKind.SELECT_CARD, slot))
            selected_face_slots.add(slot)
        return replace(action, picks=tuple(picks[:3]))

    def _wait_until_enemy_targeted(self, slot: int, timeout_s: float = 3.0) -> bool:
        """点击敌人后确认职介框蓝色选中态已经出现。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = self._observe()
            enemy = next((e for e in state.enemies if e.slot == slot and e.alive), None)
            if enemy is not None and enemy.targeted:
                mfaalog.info(f"[AutoBattle] enemy {slot} target confirmed")
                return True
            time.sleep(0.5)
        mfaalog.info(f"[AutoBattle] enemy {slot} target confirmation timed out")
        return False

    def _execute_skills(self, action) -> bool:
        # 日志：输出实际要执行的技能队列（安全门过滤后）
        if action.servant_skills or action.master_skills or action.order_change:
            parts = []
            for sk in action.servant_skills:
                target = f"->从者{sk.target_ally}" if sk.target_ally else ""
                parts.append(f"从者{sk.servant_slot}技能{sk.skill_index}{target}")
            for sk in action.master_skills:
                target = f"->从者{sk.target_ally}" if sk.target_ally else ""
                parts.append(f"御主技能{sk.skill_index}{target}")
            if action.order_change:
                parts.append(f"换人: {action.order_change.starting_member_idx}↔{action.order_change.sub_member_idx}")
            mfaalog.info(f"[AutoBattle] 执行技能队列: {' | '.join(parts)}")

        for sk in action.servant_skills:
            if not (
                is_slot(sk.servant_slot, 1, 3)
                and is_slot(sk.skill_index, 1, 3)
                and (sk.target_ally is None or is_slot(sk.target_ally, 1, 3))
            ):
                mfaalog.info(f"[AutoBattle] invalid servant skill skipped: {sk}")
                continue
            mfaalog.info(f"[AutoBattle] cast_servant_skill(slot={sk.servant_slot}, idx={sk.skill_index})")
            if not self._execute_skill_cast(
                "cast_servant_skill",
                lambda: self.executor.cast_servant_skill(sk.servant_slot, sk.skill_index),
                sk.target_ally,
                (Scene.MAIN_BATTLE,),
                _SKILL_ANIM_TIMEOUT_S,
                default_target=sk.servant_slot,  # 选自己
            ):
                return False

        for sk in action.master_skills:
            has_plan = hasattr(self.decider, 'plan') and self.decider.plan is not None
            if not has_plan and not self.battle_policy.skill.use_master_skills:
                mfaalog.info(
                    f"[AutoBattle] master skill skipped (use_master_skills=False): "
                    f"idx={sk.skill_index}"
                )
                continue
            if not (
                is_slot(sk.skill_index, 1, 3)
                and (sk.target_ally is None or is_slot(sk.target_ally, 1, 3))
            ):
                mfaalog.info(f"[AutoBattle] invalid master skill skipped: {sk}")
                continue
            mfaalog.info(f"[AutoBattle] cast_master_skill(idx={sk.skill_index})")
            # 御主技能可能是换人技能 → 之后进 ORDER_CHANGE；也可能是普通技能直接回 MAIN_BATTLE
            return_scenes = (
                (Scene.ORDER_CHANGE, Scene.MAIN_BATTLE)
                if action.order_change is not None
                else (Scene.MAIN_BATTLE,)
            )
            return_timeout = (
                _MASTER_SKILL_RETURN_TIMEOUT_S
                if action.order_change is not None
                else _SKILL_ANIM_TIMEOUT_S
            )
            if not self._execute_skill_cast(
                "cast_master_skill",
                lambda: self.executor.cast_master_skill(sk.skill_index),
                sk.target_ally,
                return_scenes,
                return_timeout,
            ):
                return False

        if action.order_change is not None:
            oc = action.order_change
            if not (
                is_slot(oc.starting_member_idx, 1, 3)
                and is_slot(oc.sub_member_idx, 4, 6)
            ):
                mfaalog.info(f"[AutoBattle] invalid order change skipped: {oc}")
                return True
            mfaalog.info(f"[AutoBattle] order_change(starting={oc.starting_member_idx}, sub={oc.sub_member_idx})")
            # 换人技能已由前面的 master_skills 触发（御主换人服技能）
            # 等待换人界面出现
            if not self._wait_until((Scene.ORDER_CHANGE,), _ORDER_CHANGE_OPEN_TIMEOUT_S):
                mfaalog.info("[AutoBattle] failed to see order change screen!")
                return False
            # 在换人界面选择首发成员和候补成员
            self._mark_action("order_change")
            self.executor.order_change(oc.starting_member_idx, oc.sub_member_idx)
            # 等待回到主界面
            if not self._wait_until((Scene.MAIN_BATTLE,), _ORDER_CHANGE_RETURN_TIMEOUT_S):
                return False

        return True

    def _execute_skill_cast(self, label, cast_callable, target_ally, return_scenes, return_timeout,
                            default_target: int = 1):
        """通用技能执行核：cast → sleep 0.2s → 检查子界面（技能使用弹窗/目标选择/回主界面）。

        返回 True 表示本技能处理完成（含"触发 CD 提示窗而跳过后续"）；False 表示卡死/确认失败。
        """
        self._mark_action(label)
        cast_callable()
        time.sleep(0.2)
        img = self.controller.post_screencap().wait().get()
        # 先查覆盖层弹窗（subscene，按需检测），再判基础场景（目标子屏/主界面）
        sub = perception.detect_subscene(self.ctx, img)
        if sub is not None:
            subscene, detail = sub
            if self.executor.dismiss_special_dialog(subscene, detail):
                mfaalog.info(f"[AutoBattle] special dialog '{subscene.value}' handled; skill skipped")
                return True
        post_scene = perception.detect_scene(self.ctx, img)
        if post_scene is Scene.SKILL_TARGET_SELECTION:
            mfaalog.info("[AutoBattle] skill target sub-screen detected")
            target = target_ally if target_ally is not None else default_target
            mfaalog.info(f"[AutoBattle] selecting skill target ally={target}")
            self.executor.select_skill_target(target)
        return self._wait_until(return_scenes, return_timeout, tap_close=True)

    def _drive_settlement(self, turns: int) -> BattleResult:
        """胜利后点击穿过结算多屏（掉落/羁绊/结果）直到回关卡列表/主界面。

        标定护栏：坐标未标定时（executor.tap_settlement_continue 返回 False），
        不盲点，直接按现有行为返回胜利（战斗已赢，只是暂不能自动点回主界面）。
        """
        mfaalog.info(f"[AutoBattle] _drive_settlement() timeout={_SETTLEMENT_TIMEOUT_S}s")
        deadline = time.monotonic() + _SETTLEMENT_TIMEOUT_S
        while time.monotonic() < deadline:
            img = self.controller.post_screencap().wait().get()
            if perception.reached_post_battle(self.ctx, img):
                mfaalog.info("[AutoBattle] settlement done -> back to quest list")
                return BattleResult.success(turns)
            if not self.executor.tap_settlement_continue():
                mfaalog.info("[AutoBattle] settlement not calibrated -> reporting victory without click-through")
                return BattleResult.success(turns)
            time.sleep(0.5)
        mfaalog.info(f"[AutoBattle] settlement did not finish within {_SETTLEMENT_TIMEOUT_S}s")
        return BattleResult.fail("settlement_timeout", turns)

    def _wait_turn_settled(self) -> bool:
        mfaalog.info(f"[AutoBattle] _wait_turn_settled() timeout={_ANIMATION_TIMEOUT_S}s")
        result = self._wait_until(_TERMINAL_OR_MAIN, _ANIMATION_TIMEOUT_S, tap_close=True)
        mfaalog.info(f"[AutoBattle] _wait_turn_settled() result={result}")
        return result

    def _wait_until(self, scenes, timeout_s: float, tap_close: bool = False) -> bool:
        mfaalog.info(f"[AutoBattle] _wait_until() scenes={[s.name for s in scenes]} timeout={timeout_s}s")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.ctx.tasker.stopping:
                mfaalog.info("[AutoBattle] _wait_until() detected stop signal, aborting wait")
                return False
            img = self.controller.post_screencap().wait().get()
            scene = perception.detect_scene(self.ctx, img)
            if scene in scenes:
                mfaalog.info(f"[AutoBattle] _wait_until() matched scene={scene.name}")
                return True
            if tap_close:
                self.executor.tap_top_right_close()
            time.sleep(0.5)
        mfaalog.info(f"[AutoBattle] _wait_until() timed out")
        return False