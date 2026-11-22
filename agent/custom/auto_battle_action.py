"""MFW Custom Action 入口：原生自动战斗（V1b）。

与 bbc_action 并列，作为可选战斗后端；不与 bbc_* 相互 import。
默认不改变现有 pipeline，需在节点显式使用 custom_action="auto_battle"。

节点参数（custom_action_param，JSON）示例：
  {"strategy_profile":"farm-safe-v1","max_turns":20,"save_evidence":true}
  {"chaldea_import_source":"<链接/ID/压缩数据>", "max_turns":20}
"""
import copy
import json
import os
import re
import sys
from dataclasses import replace

# 让 agent/battle 可作为顶层包导入（main.py 只把 custom 目录加进了 path）
_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.event_sink import NotificationType
from maa.tasker import TaskerEventSink

import mfaalog
from battle.core.decider import RuleDecider
from battle.core.advanced_strategy import (ADVANCED_STRATEGY_FIELD,
                                          advanced_strategy_to_param,
                                          validate_advanced_strategy)
from battle.core.models import BattlePlan
from battle.core.special_skills import SpecialSkillChoiceError
from battle.core.models import CONTINUOUS_FORMATION_SOURCE
from battle.core.plan_parser import (_load_action_param, _parse_battle_policy,
                                    _parse_native_card_strategy, _parse_plan,
                                    _parse_strategy_profile)
from battle.core.policy import StrategyProfile
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.data.servant_identity import canonical_servant_id
from battle.runtime.runtime import AutoBattleRuntime, BattleResult
from battle.runtime.preset_fallback import recover_for_default_battle
from battle.runtime.hellish_trial import (HellishTrialDecider, HellishTrialRuntime,
                                         trial_config_from_source)
from battle.runtime.formation_session import (context_session, restriction_settings,
                                               session_override, sessions)
from battle.runtime.skill_confirmation_reminder import skill_confirmation_reminders
from bond_stop_action import inspect_victory_settlement, skip_bond_check_for_restricted_formation
from chaldea import fetch_share_data


@AgentServer.tasker_sink()
class FormationTaskLifecycle(TaskerEventSink):
    """与 BBC 停止监听并列，维护编队和任务提醒内存，不控制游戏/进程。"""

    def on_tasker_task(self, tasker, noti_type, detail):
        if detail.entry == "MaaTaskerPostStop":
            sessions.stop_owner(detail.uuid)
            skill_confirmation_reminders.stop_owner(detail.uuid)
        elif noti_type == NotificationType.Starting:
            sessions.task_started(detail.task_id, detail.uuid)
            skill_confirmation_reminders.task_started(detail.task_id, detail.uuid)
        elif noti_type in (NotificationType.Succeeded, NotificationType.Failed):
            sessions.finish(detail.task_id)
            skill_confirmation_reminders.finish(detail.task_id)


@AgentServer.custom_action("begin_native_formation_session")
class BeginNativeFormationSession(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        root, token = context_session(context)
        # repeat(count=1) 也走单场调度，必须继续使用外层显式创建的会话。
        if not sessions.valid(root, token, repeat=True):
            token = sessions.begin(root)
        try:
            if context.tasker.stopping or not context.override_pipeline(session_override(token, context)):
                raise ValueError("cannot install native formation session")
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            sessions.finish(root, token)
            mfaalog.error(f"[初始编队] 会话初始化失败: {exc}")
            return CustomAction.RunResult(success=False)


@AgentServer.custom_action("remember_continuous_formation")
class RememberContinuousFormation(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        root, token = context_session(context)
        if context.tasker.stopping:
            return CustomAction.RunResult(success=False)
        if sessions.valid(root, token, repeat=True):
            retained = sessions.prepare_continuation(root, token)
            mfaalog.info(f"[初始编队] 连续出击初始编队已保留={retained}；等待本次助战选择")
        return CustomAction.RunResult(success=True)


@AgentServer.custom_recognition("formation_restriction_enabled")
class FormationRestrictionEnabled(CustomRecognition):
    def analyze(self, context: Context, argv: CustomRecognition.AnalyzeArg):
        if restriction_settings(context).get("auto_handle_restrictions") is True:
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="restriction_enabled")
        return None


@AgentServer.custom_recognition("formation_unclassified_notice_allowed")
class FormationUnclassifiedNoticeAllowed(CustomRecognition):
    def analyze(self, context: Context, argv: CustomRecognition.AnalyzeArg):
        settings = restriction_settings(context)
        if (settings.get("managed_formation_restrictions") is not True
                or settings.get("auto_handle_restrictions") is True):
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="notice_allowed")
        return None


@AgentServer.custom_recognition("formation_restriction_marked")
class FormationRestrictionMarked(CustomRecognition):
    def analyze(self, context: Context, argv: CustomRecognition.AnalyzeArg):
        if restriction_settings(context).get("auto_handle_restrictions") is not True:
            return None
        root, token = context_session(context)
        if sessions.valid(root, token) and sessions.restricted(root, token):
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="restriction_marked")
        return None


@AgentServer.custom_action("mark_native_formation_restricted")
class MarkNativeFormationRestricted(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        if context.tasker.stopping:
            return CustomAction.RunResult(success=False)
        root, token = context_session(context)
        if sessions.valid(root, token):
            if not sessions.restricted(root, token):
                mfaalog.warning("[编队限制] 本场将采用游戏当前阵容和默认战斗逻辑。")
            sessions.mark_restricted(root, token)
            skip_bond_check_for_restricted_formation(context)
        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("auto_battle")
class AutoBattleAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        root, token = context_session(context)
        initial_formation, formation_battle, restricted = None, 1, False
        if token:
            try:
                if context.tasker.stopping:
                    raise ValueError("task stopping")
                initial_formation, formation_battle = sessions.read(root, token)
                restricted = sessions.restricted(root, token)
            except ValueError as exc:
                mfaalog.error(f"[auto_battle] 初始编队会话无效: {exc}")
                return CustomAction.RunResult(success=False)
        param = _load_action_param(argv.custom_action_param)
        node_name = getattr(argv, "node_name", "")
        battle_node = (context.get_node_data(node_name) or {}) if node_name else {}
        battle_attach = battle_node.get("attach") or {}
        support_fallback = bool(token and sessions.support_fallback(root, token))
        restricted = restricted or battle_attach.get("native_battle_formation_restricted") is True
        if restricted:
            skip_bond_check_for_restricted_formation(context)
        formation_reset = restricted or param.get("native_battle_formation_reset") is True
        continued_formation_reset = (formation_reset and initial_formation is not None
                                     and getattr(initial_formation, "source", "") == CONTINUOUS_FORMATION_SOURCE)
        if support_fallback or formation_reset:
            # 使用实际采集或确认可继承的编队，舍弃绑定原助战的方案。
            param = {}
            if continued_formation_reset:
                initial_formation = None
                sessions.skip_capture(root, token)
            mfaalog.warning("[auto_battle] 本场阵容有变动，使用高级策略及当前已确认的编队记录")
        plan = _parse_plan(param)
        chaldea_roster = None
        try:
            strategy_attach = _attached_strategy_settings(context, node_name)
            source_mode = strategy_attach.get("native_battle_strategy_source")
            trial_requested = (battle_attach.get("native_battle_trial_profile")
                               == "hellish_202611" and not (support_fallback or formation_reset))
            share = (_share_data_from_param(param) if plan is None or source_mode == "editor" or trial_requested
                     else None)
            trial_config = trial_config_from_source(battle_node, share) if trial_requested else None
            if share is not None:
                if plan is None:
                    plan = _build_plan_from_share(share)
                chaldea_roster = _roster_from_share(share)
            param = _battle_param_with_attached_config(context, node_name, param, share)
            # Direct Custom invocations without an MXU pointer use the same
            # defaults as all native tasks. Old explicit strategy nodes remain
            # compatible for external integrations.
            if not strategy_attach:
                param = {**param, **advanced_strategy_to_param()}
            if support_fallback or formation_reset:
                param = _without_formation_strategy(param)
            param = _canonical_native_strategy_param(param)
            profile = _parse_strategy_profile(param)
            battle_policy = _parse_battle_policy(param)
            if (battle_policy.card.native_strategy.opening_hand.enabled
                    and (plan is None or plan.active_turn(0) is None)):
                mfaalog.warning("[高级策略] 未设置有效首回合方案，跳过开局等待")
                param = copy.deepcopy(param)
                param["card_policy"]["native_strategy"]["opening_hand"]["enabled"] = False
                battle_policy = _parse_battle_policy(param)
        except (TeamFetchError, SpecialSkillChoiceError, ValueError) as e:
            # 队伍下载失败: 明确失败并提示, 由 pipeline on_error 走终止分支
            mfaalog.error(f"[auto_battle] {e}")
            if token:
                sessions.invalidate(root, token, "chaldea_plan_failed")
            return CustomAction.RunResult(success=False)
        roster_servant_ids = _merge_rosters(initial_formation, chaldea_roster)
        preferred = strategy_attach.get("native_battle_preferred_attacker")
        frontline_preferred_servant_id = (
            canonical_servant_id(preferred)
            if restricted and source_mode == "manual" and isinstance(preferred, str)
            else None
        )
        decider = RuleDecider(battle_policy, plan=plan,
                              frontline_preferred_servant_id=frontline_preferred_servant_id)
        runtime_class = AutoBattleRuntime
        if trial_config is not None:
            decider = HellishTrialDecider(decider, trial_config)
            runtime_class = HellishTrialRuntime
            mfaalog.info("[试炼策略] 已由本任务标记与导入方案共同启用202611专用策略")

        if not context.tasker.stopping and skill_confirmation_reminders.claim(root):
            mfaalog.warning(
                "[原生战斗提醒] 请在游戏内关闭“技能使用确认”（技能二次确认）；"
                "开启时可能取消或跳过技能。若已开启，请停止任务，关闭后重新运行。"
                "如已关闭弹窗可以无视。"
            )

        # Agent 控制器代理仅在当前回调内复用；重复获取会使旧代理失效，
        # 回调结束后也不能交给后续结算回调，因此只获取一次并显式传递。
        controller = context.tasker.controller

        plan_status = "loaded" if plan is not None else "none"
        plan_turns = len(plan.turns) if plan is not None else 0
        mfaalog.info(
            f"[auto_battle] start profile={profile.id} "
            f"max_turns={profile.max_turns} plan={plan_status} plan_turns={plan_turns}"
        )
        try:
            runtime = runtime_class(
                context, controller, decider, profile, battle_policy,
                initial_formation=initial_formation, formation_battle=formation_battle,
                roster_servant_ids=roster_servant_ids,
            )
            result = runtime.run()
            preset_active = (trial_config is not None or (
                isinstance(plan, BattlePlan) and bool(plan.turns) and runtime._has_active_plan()))
            result = _continue_without_preset(
                context, controller, runtime, result, profile,
                enabled=battle_attach.get("native_battle_preset_fallback") is True,
                preset_active=preset_active,
                trial_mode=trial_config is not None,
            )
        except Exception:
            if token and sessions.valid(root, token):
                sessions.invalidate(root, token, "battle_runtime_exception")
            raise
        if not result.ok and token and sessions.valid(root, token):
            sessions.invalidate(root, token, "battle_runtime_failed")
        mfaalog.info(f"[auto_battle] end ok={result.ok} reason={result.reason} turns={result.turns}")
        if not result.ok and not context.tasker.stopping:
            mfaalog.error(
                f"自动战斗未能完成，已停止本场自动战斗（已完成{result.turns}回合）。"
                "请保留当前游戏画面截图，并使用客户端“导出日志”反馈。"
                f"停止原因：{result.reason}"
            )
        elif result.reason == "defeat":
            mfaalog.warning("本场战斗失败，将按现有流程处理撤退和结算。")

        # 已确认胜利后立即交接结算，避免Pipeline默认延迟错过短暂羁绊页。
        if result.ok and result.reason == "victory":
            inspect_victory_settlement(context, result, controller)

        # TODO(save_evidence)：失败时保存截图/状态证据
        return CustomAction.RunResult(success=result.ok)


_PRESET_RECOVERABLE_ERRORS = frozenset({
    "skill_execution_failed", "card_actor_identity_mismatch",
    "unexpected_dialog", "unexpected_order_change_scene",
    "open_cards_failed", "open_cards_confirm_failed",
    "selection_confirm_failed", "card_selection_not_submitted",
    "stuck_unknown_scene",
})


def _continue_without_preset(context, controller, runtime, result, profile, *,
                             enabled: bool, preset_active: bool, trial_mode: bool = False):
    """Only explicitly opted-in story/trial battles may discard a broken plan.

    No recursive fallback: a failure of the fresh default runtime is final.
    Configuration, controller exceptions, stop requests and terminal results keep
    their existing paths. An uncertain card submission reserves one turn of the
    existing budget without claiming that it was a confirmed completed turn.
    """
    if (enabled is not True or not preset_active or result.ok or context.tasker.stopping
            or not isinstance(result.reason, str)):
        return result
    reason = result.reason
    if (reason not in _PRESET_RECOVERABLE_ERRORS and not reason.startswith((
            "trial_", "opening_hand_", "invalid_main_action:", "invalid_card_action:"))):
        return result

    default_param = advanced_strategy_to_param()
    default_profile = _parse_strategy_profile(default_param)
    reserved = int(getattr(runtime, "_selection_in_flight", False) is True)
    remaining = min(profile.max_turns - result.turns - reserved, default_profile.max_turns)
    if remaining <= 0:
        mfaalog.error("[战斗回退] 本场已用完战斗回合预算，无法再启动默认战斗。请检查战斗方案，或调整“最大战斗回合”后重新挑战。")
        return BattleResult.fail("max_turns_exceeded", result.turns)

    mfaalog.warning(
        "[战斗回退] 预设技能或出卡流程无法继续，正在关闭误开的战斗弹窗。"
        "确认返回战斗后，本场将清除预设方案，改为自动释放可用从者技能、优先使用可用宝具；下一场恢复原方案。"
    )
    if trial_mode:
        mfaalog.warning("[试炼战斗回退] 本场优先继续战斗，不再按原方案保证颜色、击破职阶或无焦热等附加任务条件；请在战后核对任务进度。")
    mfaalog.info(f"[战斗回退] reason={reason} confirmed_turns={result.turns} "
                 f"reserved_unconfirmed_turns={reserved} remaining_budget={remaining}")
    if not recover_for_default_battle(runtime):
        mfaalog.error(
            "[战斗回退] 未能确认返回可操作的战斗界面，无法切换默认战斗，已保留现场停止。"
            "请手动关闭游戏内弹窗并检查当前画面后重试。"
        )
        return BattleResult.fail("preset_fallback_recovery_failed", result.turns)
    if context.tasker.stopping:
        return result

    policy = _parse_battle_policy(default_param)
    fallback = AutoBattleRuntime(
        context, controller, RuleDecider(policy),
        replace(default_profile, max_turns=remaining), policy,
        initial_formation=None, roster_servant_ids=(None,) * 6,
    )
    # Old IDs are unordered visual candidates only. No original frontline slots,
    # retired instances, actor bindings or special-choice queues survive.
    candidates = set()
    for name in ("candidate_servant_ids", "roster_servant_ids", "_initial_servant_ids"):
        values = getattr(runtime, name, ())
        if isinstance(values, (tuple, list)):
            for value in values:
                sid = canonical_servant_id(value)
                if sid:
                    candidates.add(sid)
    fallback.candidate_servant_ids = tuple(sorted(candidates))
    resumed = fallback.run()
    return BattleResult(resumed.ok, resumed.reason, result.turns + resumed.turns)


def _canonical_native_strategy_param(param: dict) -> dict:
    """让可编辑策略 ID 与运行时识别的规范身份一致，不修改原始参数。

    先复用纯 core 解析器筛掉非法 ID/颜色，保留其现有类型和边界回退。
    数据层的别名转换仅发生在入口；目录外正数 ID 仍由 canonical 保留。
    若别名与规范 ID 同时配置了不同颜色，明确的规范 ID 配置优先。
    """
    card = param.get("card_policy")
    native = card.get("native_strategy") if isinstance(card, dict) else None
    if not isinstance(native, dict):
        return param
    has_ids = isinstance(native.get("preferred_servant_ids"), list)
    has_colors = isinstance(native.get("np_colors_by_servant_id"), dict)
    if not has_ids and not has_colors:
        return param
    parsed = _parse_native_card_strategy(native)
    result = copy.deepcopy(param)
    normalized = result["card_policy"]["native_strategy"]
    if has_ids:
        normalized["preferred_servant_ids"] = list(dict.fromkeys(
            canonical_servant_id(sid) or sid for sid in parsed.preferred_servant_ids
        ))
    if has_colors:
        colors = {}
        for sid, color in parsed.np_colors_by_servant_id:
            canonical = canonical_servant_id(sid) or sid
            if canonical == sid or canonical not in colors:
                colors[canonical] = color.value
        normalized["np_colors_by_servant_id"] = colors
    return result


def _attached_strategy_settings(context, node_name: str) -> dict:
    if not node_name:
        return {}
    node = context.get_node_data(node_name) or {}
    pointer = (node.get("attach") or {}).get("native_battle_config_node")
    if not isinstance(pointer, str) or not pointer.strip():
        return {}
    return (context.get_node_data(pointer) or {}).get("attach") or {}


def _manual_advanced_strategy_param(attach: dict) -> dict:
    """Read independent MXU fields; hidden values are only read in manual mode."""
    config = {}
    if "native_battle_fill_priority" in attach:
        config["fillPriority"] = attach["native_battle_fill_priority"]
    if "native_battle_max_turns" in attach:
        config["maxTurns"] = attach["native_battle_max_turns"]
    if "native_battle_auto_servant_skills" in attach:
        config["autoServantSkills"] = attach["native_battle_auto_servant_skills"]
    overrides = attach.get("native_strategy_overrides", {})
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("补牌策略微调必须为 JSON 对象") from exc
    config["nativeStrategyOverrides"] = overrides
    opening = {}
    for external, internal in (("native_battle_opening_hand_enabled", "enabled"),
                               ("native_battle_opening_hand_max_wait_turns", "maxWaitTurns")):
        if external in attach:
            opening[internal] = attach[external]
    # Only picture selections provide an explicit identity. Retired inherit
    # selections are empty too; the removed hand-entered ID is never read.
    raw_id = attach.get("native_battle_opening_hand_selected_servant", "")
    if raw_id == "inherit":
        raw_id = ""
    if not isinstance(raw_id, str) or (raw_id != "" and not re.fullmatch(r"[1-9][0-9]{0,11}", raw_id)):
        raise ValueError("开局等牌的目标从者无法识别，无法确认要等待谁的牌。请在「高级策略 → 手动 → 开局等待关键牌」下重新选择「目标从者职阶」和「目标从者」。")
    if raw_id is not None and str(raw_id).strip():
        opening["servantId"] = str(raw_id).strip()
    raw_slot = attach.get("native_battle_opening_hand_servant_slot")
    if raw_slot is not None and not (type(raw_slot) is int and raw_slot == 0):
        opening["servantSlot"] = raw_slot
    colors = attach.get("native_battle_opening_hand_required_colors")
    if isinstance(colors, str):
        colors = list(colors.replace(",", "").replace(" ", "").strip())
    if colors is not None:
        opening["requiredColors"] = colors
    if opening:
        config["openingHand"] = opening
    result = advanced_strategy_to_param(config)
    preset_card = attach.get("native_battle_card_policy", {})
    if not isinstance(preset_card, dict):
        raise ValueError("补牌预设必须为 JSON 对象")
    if "native_strategy" in preset_card:
        errors = validate_advanced_strategy({"nativeStrategyOverrides": preset_card["native_strategy"]})
        if errors:
            raise ValueError(errors[0]["path"] + ": " + errors[0]["message"])
    # The preset supplies weights; the editable JSON and independent opening
    # controls must have precedence over them.
    for key, value in preset_card.items():
        if key != "native_strategy":
            result["card_policy"][key] = copy.deepcopy(value)
    preset_native = preset_card.get("native_strategy", {})
    result["card_policy"]["native_strategy"] = _deep_merge(
        _deep_merge(advanced_strategy_to_param()["card_policy"]["native_strategy"], preset_native),
        {**copy.deepcopy(overrides), "opening_hand": result["card_policy"]["native_strategy"]["opening_hand"]},
    )
    if "fillPriority" in config:
        result["card_policy"]["native_strategy"]["fill_priority"] = config["fillPriority"]
    # The select cases write one scalar: an integer position or a string ID.
    # An empty selection preserves preferences from existing strategy JSON.
    preferred = attach.get("native_battle_preferred_attacker", "")
    if preferred != "":
        native = result["card_policy"]["native_strategy"]
        if type(preferred) is int and 1 <= preferred <= 3:
            native.update(preferred_slots=[preferred], preferred_servant_ids=[])
        elif isinstance(preferred, str) and re.fullmatch(r"[1-9][0-9]{0,11}", preferred):
            native.update(preferred_slots=[], preferred_servant_ids=[preferred])
        else:
            raise ValueError("主输出偏好无法识别。请在「高级策略 → 手动 → 主输出偏好」重新选择前排位置或从者。")
    return result


def _deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _without_formation_strategy(param: dict) -> dict:
    result = copy.deepcopy(param)
    native = result.get("card_policy", {}).get("native_strategy", {})
    for key in ("preferred_servant_ids", "preferred_slots"):
        native[key] = []
    for key in ("np_colors_by_servant_id", "np_colors_by_slot"):
        native[key] = {}
    native["opening_hand"] = {"enabled": False}
    return result


def _battle_param_with_attached_config(context, node_name: str, param: dict,
                                     share_data: dict | None = None) -> dict:
    """读取所有原生入口共用的策略节点，保留固定来源与逐回合动作。

    新节点按 default/editor/manual 选用独立来源，替换运行上限、技能与补牌
    设置。旧外部集成没有来源字段时继续走原白名单合并分支；没有指针时本
    helper 不改参数，实际 action 入口另外提供统一默认策略。
    """
    if not node_name:
        return param
    node = context.get_node_data(node_name) or {}
    config_node = (node.get("attach") or {}).get("native_battle_config_node")
    if not isinstance(config_node, str) or not config_node.strip():
        return param
    config_data = context.get_node_data(config_node) or {}
    attach = config_data.get("attach") or {}
    if "native_battle_strategy_source" in attach:
        mode = attach["native_battle_strategy_source"]
        if type(mode) is not str or mode not in {"default", "editor", "manual"}:
            raise ValueError("未知的高级策略来源")
        if mode == "manual":
            strategy = _manual_advanced_strategy_param(attach)
        elif mode == "editor" and isinstance(share_data, dict) and ADVANCED_STRATEGY_FIELD in share_data:
            value = share_data[ADVANCED_STRATEGY_FIELD]
            errors = validate_advanced_strategy(value)
            if errors:
                raise ValueError(errors[0]["path"] + ": " + errors[0]["message"])
            strategy = advanced_strategy_to_param(value)
        else:
            strategy = advanced_strategy_to_param()
        # Replacing these three whitelisted fields prevents old hidden values,
        # trial-specific identities, or another source from leaking in.
        return {**copy.deepcopy(param), **strategy}
    config = attach.get("native_battle_config")
    if not isinstance(config, dict):
        raise ValueError(f"{config_node} 缺少 native_battle_config 对象")

    def merge(base, override):
        result = copy.deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    allowed = {key: value for key, value in config.items()
               if key in {"strategy_profile", "max_turns", "card_policy", "skill_policy"}}
    result = merge(param, allowed)
    # 选项各写入平级 attach 键，避免多个选项覆盖同一个嵌套配置对象。
    if "native_battle_max_turns" in attach:
        result["max_turns"] = attach["native_battle_max_turns"]
    if "native_battle_card_policy" in attach:
        if not isinstance(attach["native_battle_card_policy"], dict):
            raise ValueError("补牌预设必须为 JSON 对象")
        result = merge(result, {"card_policy": attach["native_battle_card_policy"]})
    if "native_battle_auto_servant_skills" in attach:
        result = merge(result, {"skill_policy": {
            "auto_servant_skills": attach["native_battle_auto_servant_skills"],
        }})
    overrides = attach.get("native_strategy_overrides", {})
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("补牌策略微调必须为 JSON 对象") from exc
    if not isinstance(overrides, dict):
        raise ValueError("补牌策略微调必须为 JSON 对象")
    if overrides:
        result = merge(result, {"card_policy": {"native_strategy": overrides}})
    # 独立UI开关/预算最后应用；微调仍可编辑目标从者与所需卡色。
    opening_override = {}
    if "native_battle_opening_hand_enabled" in attach:
        opening_override["enabled"] = attach["native_battle_opening_hand_enabled"]
    if "native_battle_opening_hand_max_wait_turns" in attach:
        opening_override["max_wait_turns"] = attach["native_battle_opening_hand_max_wait_turns"]
    if opening_override:
        result = merge(result, {"card_policy": {"native_strategy": {
            "opening_hand": opening_override,
        }}})
    return result


@AgentServer.custom_recognition("support_fallback_active")
class SupportFallbackActive(CustomRecognition):
    def analyze(self, context: Context, argv: CustomRecognition.AnalyzeArg):
        root, token = context_session(context)
        if token and sessions.valid(root, token) and sessions.support_fallback(root, token):
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="support_fallback")
        return None


def _plan_from_chaldea_share(param: dict) -> BattlePlan | None:
    """Compatibility wrapper returning only the Chaldea battle plan."""
    return _battle_setup_from_chaldea_share(param)[0]


def _battle_setup_from_chaldea_share(
    param: dict,
) -> tuple[BattlePlan | None, tuple[str | None, ...] | None]:
    """从参数解析出 Chaldea 来源并转成 BattlePlan。

    支持两种形态（显式 ``plan`` 优先级更高，由调用方保证已在此前解析）：
    1. ``chaldea_share``：已解码的 BattleShareData dict（离线注入用）。
    2. ``chaldea_import_source``：链接/ID/压缩串或本地文件路径（MXU 文件
       选择框，路径与手填输入已合并为同一字段），复用 agent/chaldea 的
       fetch_share_data 下载/解码/本地加载。
    """
    share = _share_data_from_param(param)
    if share is not None:
        return _build_plan_from_share(share), _roster_from_share(share)
    return None, None


def _share_data_from_param(param: dict) -> dict | None:
    """Obtain one document for its actions, identities, and global strategy."""
    share = param.get("chaldea_share")
    if isinstance(share, dict):
        mfaalog.info(f"[auto_battle] 使用离线注入的 chaldea_share")
        return share

    source = param.get("chaldea_import_source") or param.get("chaldea_import_source_file")
    if isinstance(source, str) and source.strip():
        mfaalog.info(f"[auto_battle] chaldea_import_source 开始下载/解码: {source[:100]}...")
        share_data, quest_id, team_id = fetch_share_data(source.strip())
        mfaalog.info(f"[auto_battle] fetch_share_data 结果: ok={share_data is not None} quest_id={quest_id} team_id={team_id}")
        if not share_data:
            mfaalog.error(f"[auto_battle] 队伍下载/解码失败, 终止任务: source={source[:100]}")
            raise TeamFetchError(
                f"[Chaldea] 队伍接口无匹配数据: team_id={team_id}, "
                f"请检查链接/ID是否正确, 或稍后重试"
            )
        return share_data

    return None


class TeamFetchError(RuntimeError):
    """Chaldea 队伍下载/解码失败, 任务应终止并提示用户。"""
    pass


def _build_plan_from_share(share: dict) -> BattlePlan | None:
    """把 BattleShareData actions 转成 BattlePlan。"""
    actions = share.get("actions")
    team = share.get("team") or {}
    mystic_code_id = (team.get("mysticCode") or {}).get("mysticCodeId")
    if mystic_code_id is None:
        mystic_code_id = (share.get("mysticCode") or {}).get("id")
    delegate = share.get("delegate")
    mfaalog.info(f"[auto_battle] 构建 BattlePlan: actions={len(actions) if actions else 0}条 mysticCode={mystic_code_id} delegate={'有' if delegate else '无'}")
    plan = convert_chaldea_actions_to_battle_plan(
        actions,
        delegate=delegate,
        mystic_code_id=mystic_code_id,
        initial_servant_ids=_roster_from_share(share),
    )
    mfaalog.info(f"[auto_battle] chaldea_share -> plan turns={len(plan.turns) if plan is not None else 0}")
    return plan


def _roster_from_share(share: dict) -> tuple[str | None, ...] | None:
    """Extract six semantic formation slots from Chaldea BattleShareData."""
    team = share.get("team")
    if not isinstance(team, dict):
        return None
    groups = (team.get("onFieldSvts"), team.get("backupSvts"))
    if not any(isinstance(group, list) for group in groups):
        return None
    # 前排与候补各占三槽，空位不能让后续成员的位置前移。
    members = []
    for group in groups:
        slots = group[:3] if isinstance(group, list) else []
        members.extend(slots + [None] * (3 - len(slots)))
    return tuple(
        canonical_servant_id(member.get("svtId")) if isinstance(member, dict) else None
        for member in members
    )


def _merge_rosters(
    initial_formation,
    chaldea_roster: tuple[str | None, ...] | None,
) -> tuple[str | None, ...]:
    """Merge visual and Chaldea evidence without hiding identity conflicts."""
    visual = None
    if initial_formation is not None:
        visual = tuple(
            canonical_servant_id(slot.servant_id) if slot.status == "identified" else None
            for slot in initial_formation.slots
        )
    if chaldea_roster is not None:
        chaldea_roster = tuple(canonical_servant_id(sid) for sid in chaldea_roster)
    if visual is None:
        return chaldea_roster or (None,) * 6
    if chaldea_roster is None:
        return visual

    merged: list[str | None] = []
    for index, (seen, planned) in enumerate(zip(visual, chaldea_roster), start=1):
        if (getattr(initial_formation, "source", "") == CONTINUOUS_FORMATION_SOURCE
                and initial_formation.slots[index - 1].is_support and seen is None):
            # 本场未识别的助战不能从旧攻略回填；其余自有成员照常合并。
            merged.append(None)
            continue
        if seen is not None and planned is not None and seen != planned:
            mfaalog.warning(
                f"[auto_battle] roster conflict at slot {index}: "
                f"formation={seen} chaldea={planned}; owner mapping disabled for this slot"
            )
            merged.append(None)
        else:
            merged.append(seen or planned)
    return tuple(merged)
