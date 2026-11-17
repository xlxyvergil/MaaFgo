"""仅战后结算调用的羁绊停止守卫；不接入战斗运行时。"""
from __future__ import annotations

import hashlib
import html
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

import cv2

_AGENT_DIR = str(Path(__file__).resolve().parents[1])
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.event_sink import NotificationType
from maa.tasker import TaskerEventSink

import mfaalog
from battle.perception.bond_result import (read_bond_page, read_bond_max, read_any_bond_max, read_bond_upgrade,
                                         result_formation_order, result_slot_for_formation)
from battle.runtime.bond_stop_state import BondStop, bond_stops


CONFIG_NODE = "原生自动战斗-羁绊停止配置"
GUARD_NODE = "结算-羁绊满停止"


class BondStopReached(RuntimeError):
    def __init__(self, result: BondStop):
        self.result = result
        super().__init__(result.display())


def _root(context) -> int:
    return context.get_task_job().job_id


def _digest(image) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


def _settings(context) -> tuple[str, int | tuple[int, ...]]:
    getter = getattr(context, "get_node_data", None)
    if getter is None:
        # 旧外部调用可只提供战斗参数；未提供此独立配置的调用默认关闭。
        return "off", 1
    attach = (getter(CONFIG_NODE) or {}).get("attach") or {}
    mode = attach.get("native_bond_stop_mode", "off")
    slot = attach.get("native_bond_stop_slot", 1)
    if mode not in {"off", "all", "slot", "slots"}:
        raise ValueError("无法读取羁绊停止设置。请重新选择“羁绊满时停止”开关后重试。")
    if mode == "slots":
        flags = tuple(attach.get(f"native_bond_stop_slot_{i}", False) for i in range(1, 7))
        if any(type(value) is not bool for value in flags):
            raise ValueError("无法读取检查号位。请在“羁绊满时停止”的“检查号位”中重新勾选从者后重试。")
        targets = tuple(i for i, selected in enumerate(flags, 1) if selected)
        if not targets:
            raise ValueError("尚未选择检查号位，无法判断应在哪位从者满羁绊时停止。请在“检查号位”中至少勾选一个号位，或关闭“羁绊满时停止”。")
        return ("all", 0) if len(targets) == 6 else ("slots", targets)
    if mode == "slot" and (type(slot) is not int or not 1 <= slot <= 6):
        raise ValueError("羁绊停止目标号位无效")
    return mode, slot


def _latch_error(context, token, image, exc, *, latest_image=None) -> BondStop | None:
    return bond_stops.latch(_root(context), token, reason="bond_check_unconfirmed",
                            message=f"羁绊检查无法确认，已保留结算现场并停止：{exc}",
                            image=image, latest_image=latest_image)


def _read_maxima(context, image, mode):
    return (bool(read_any_bond_max(context, image)) if mode == "all"
            else read_bond_max(context, image, columns=5))


def _positive(context, token, image, mode, maxima, order, target_column):
    columns = (target_column,) if type(target_column) is int else target_column
    selected = tuple(c for c in maxima if c in columns) if mode in {"slot", "slots"} else ()
    if not ((mode == "all" and maxima) or selected):
        return None
    positions = tuple(order[column - 1] for column in selected)
    labels = "、".join(f"{slot}号位" for slot in positions) + "从者" if positions else "结算羁绊栏从者"
    root = _root(context)
    return bond_stops.latch(root, token, reason="bond_max",
        message=f"第{bond_stops.battle_number(root)}场结算：{labels}羁绊MAX，已停止战斗",
        image=image, formation_slots=positions, result_columns=selected)


def _slot_mapping(context, target):
    formation = bond_stops.formation(_root(context))
    order = result_formation_order(formation)
    if len(order) != 5:
        raise ValueError("本场结算人数尚未适配，无法可靠判断所选号位。当前仅支持五名自有从者的号位识别；请保留截图反馈，或将“检查号位”改为全选后重试。")
    if isinstance(target, tuple):
        columns = tuple(i for i, slot in enumerate(order, 1) if slot in target)
        if not columns:
            raise ValueError("所选号位本场均为助战或空位，没有可检查的自有从者。请在“检查号位”中勾选本场自有从者所在号位后重试。")
        return order, columns
    return order, result_slot_for_formation(formation, target)


def _inspect_upgrade(context, token, image, mode, target, *, evidence_image=None):
    """明确升级可下场复核或末场正常结算；不能把升级或识别失败当作未满。"""
    if not read_bond_upgrade(context, image):
        return False, None
    root = _root(context)
    evidence = image if evidence_image is None else evidence_image
    if mode in {"slot", "slots"}:
        _slot_mapping(context, target)
    if bond_stops.can_defer(root):
        existing = bond_stops.deferred(root)
        deferred = bond_stops.defer(root, token,
            message=(f"第{bond_stops.battle_number(root)}场羁绊升级展示遮挡，"
                     "待检查从者是否MAX尚未确认；按原剩余次数在下一场结算复核"),
            image=evidence, latest_image=image if evidence_image is not None else None)
        if deferred and existing is None:
            _save_evidence(context, deferred)
        return True, None
    if bond_stops.is_last_battle(root):
        existing = bond_stops.upgrade_continuation(root)
        final = bond_stops.continue_last_upgrade(root, token,
            message=f"第{bond_stops.battle_number(root)}场已确认羁绊升级，本场为最后一场；正常完成结算，后续任务可继续",
            image=evidence, latest_image=image if evidence_image is not None else None)
        if final is not None and existing is None:
            mfaalog.info(f"[羁绊结算] {final.message}")
        return True, None
    message = "羁绊升级展示遮挡，当前无法确认本场是否为最后一场，也无法安排下场复核，已保留现场并停止。请保留当前截图和日志反馈。"
    return True, _latch_error(context, token, evidence, message,
                             latest_image=image if evidence_image is not None else None)


def _poll_config(attach, prefix):
    node = attach.get(f"{prefix}_node")
    attempts = attach.get(f"{prefix}_attempts")
    timeout = attach.get(f"{prefix}_timeout_ms")
    if (not isinstance(node, str) or not node or type(attempts) is not int
            or not 1 <= attempts <= 200 or type(timeout) is not int or not 1 <= timeout <= 60000):
        raise ValueError(f"羁绊快速轮询配置无效：{prefix}")
    return node, attempts, timeout


def _poll_wait(context, token, node):
    root = _root(context)
    if context.tasker.stopping or not bond_stops.valid(root, token):
        return False
    detail = context.run_task(node)
    if context.tasker.stopping or not bond_stops.valid(root, token):
        return False
    if detail is None or not detail.status.succeeded:
        raise ValueError(f"羁绊快速轮询等待失败：{node}")
    return True


def inspect_bond_stop(context, image, *, controller=None) -> BondStop | None:
    """首次羁绊页起共用观察窗口，阳性立即处理，到点以新帧确认阴性。"""
    root = _root(context)
    existing = bond_stops.get(root)
    if existing:
        return existing
    if context.tasker.stopping:
        return None
    if bond_stops.upgrade_continuation(root) is not None or bond_stops.observation_complete(root):
        return None
    token = bond_stops.ensure(root)
    evidence_image, current_image = image, None
    try:
        mode, target = _settings(context)
        if mode == "off":
            return None
        if not read_bond_page(context, image):
            _, stopped = _inspect_upgrade(context, token, image, mode, target)
            return stopped
        started_at = bond_stops.start_observation(root, token, time.monotonic())
        if started_at is None:
            return None
        # 羁绊页可能自动被升级展示覆盖，先保留这一帧，不能等动画后再找证据。
        evidence_image = image.copy()
        original_digest = _digest(image)
        order, target_column = _slot_mapping(context, target) if mode in {"slot", "slots"} else ((), None)
        maxima = _read_maxima(context, evidence_image, mode)
        stopped = _positive(context, token, evidence_image, mode, maxima, order, target_column)
        if stopped:
            return stopped
        recognized, stopped = _inspect_upgrade(context, token, image, mode, target,
                                               evidence_image=evidence_image)
        if recognized:
            return stopped
        guard = (context.get_node_data(GUARD_NODE) or {}).get("attach") or {}
        wait_node, attempts, timeout = _poll_config(guard, "observation")
        deadline = started_at + timeout / 1000
        # 胜利立即交接时复用调用方的同回调代理；Pipeline 后续回调须自行
        # 获取一次，不能从根任务状态复用已失效的 Agent 控制器句柄。
        if controller is None:
            controller = context.tasker.controller
        # 不再串联快速轮询、冻结及升级等待；动画中途消失的文字同样共用此截止时间。
        for _ in range(attempts):
            if context.tasker.stopping or not bond_stops.valid(root, token):
                return None
            if bond_stops.observation_complete(root):
                return None
            if time.monotonic() < deadline and not _poll_wait(context, token, wait_node):
                return None
            if context.tasker.stopping or not bond_stops.valid(root, token):
                return None
            # 以取图开始时间判定末帧，不能拿4秒前的旧截图在到点后宣告阴性。
            captured_at = time.monotonic()
            captured = controller.post_screencap().wait().get()
            if captured is None:
                raise ValueError("羁绊观察截图失败，无法确认当前结算画面。请保留日志反馈后重试。")
            current_image = captured
            page_visible = read_bond_page(context, current_image)
            if page_visible:
                maxima = _read_maxima(context, current_image, mode)
                stopped = _positive(context, token, current_image, mode, maxima, order, target_column)
                if stopped:
                    return stopped
            recognized, stopped = _inspect_upgrade(context, token, current_image, mode, target,
                                                   evidence_image=evidence_image)
            if recognized:
                return stopped
            if not page_visible:
                _reject_missed_bond_page(context, current_image)
            if context.tasker.stopping or not bond_stops.valid(root, token):
                return None
            if captured_at >= deadline:
                bond_stops.complete_observation(root, token)
                bond_stops.mark_checked(root, token, _digest(current_image))
                bond_stops.mark_checked(root, token, original_digest)
                return None
        raise ValueError("羁绊观察未覆盖完整时限，无法完成本场检查。请更新资源包后重试；若仍出现此提示，请保留截图和日志反馈。")
    except Exception as exc:
        if context.tasker.stopping or not bond_stops.valid(root, token):
            return None
        return _latch_error(context, token, evidence_image, exc, latest_image=current_image)


def _save_evidence(context, result: BondStop | None) -> BondStop | None:
    if result is None or result.evidence_attempted:
        return result
    result.evidence_attempted = True
    try:
        if result.image is None:
            raise ValueError("没有可保存的命中截图")
        android = sys.platform == "android" or os.environ.get("MAAFGO_PLATFORM") == "android"
        directory = Path("log" if android else "debug") / "screencap"
        directory.mkdir(parents=True, exist_ok=True)
        slots = "-".join(map(str, result.formation_slots)) or "unconfirmed"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        prefix = "bond_deferred" if result.reason == "bond_upgrade_deferred" else "bond_stop"
        target = directory / f"{prefix}_{timestamp}_task{_root(context)}_battle{result.battle}_slot{slots}.png"
        ok, encoded = cv2.imencode(".png", result.image)
        if not ok:
            raise ValueError("PNG编码失败")
        encoded.tofile(str(target))
        result.screenshot_path = str(target.resolve())
        if result.latest_image is not None:
            latest_target = target.with_name(target.stem + "_latest.png")
            ok, encoded = cv2.imencode(".png", result.latest_image)
            if not ok:
                raise ValueError("最新现场图PNG编码失败")
            encoded.tofile(str(latest_target))
            result.latest_screenshot_path = str(latest_target.resolve())
    except Exception as exc:
        result.screenshot_error = str(exc)
    label = "羁绊待复核" if result.reason == "bond_upgrade_deferred" else "羁绊停止"
    mfaalog.warning(f"[{label}] {result.display()}")
    return result


def save_bond_stop(context) -> BondStop | None:
    return _save_evidence(context, bond_stops.get(_root(context)))


def check_settlement_bond_stop(context, image, *, controller=None) -> None:
    result = inspect_bond_stop(context, image, controller=controller)
    if result:
        save_bond_stop(context)
        raise BondStopReached(result)


def _reject_missed_bond_page(context, image) -> None:
    # 正常顺序是羁绊 → 经验 → 战利品。到达后两页不能再靠点击补查羁绊。
    for name, label in (("战斗_经验结算页", "经验结算页"), ("战斗_战利品弹窗", "战利品页")):
        recognized = context.run_recognition(name, image)
        if context.tasker.stopping:
            return
        if recognized and recognized.hit:
            raise ValueError(f"已进入{label}，之前的羁绊页未完成检查，无法确认是否满羁绊，本场无法补查。请保留当前截图和日志反馈；已停止，不会继续点击或开始下一场。")


def inspect_victory_settlement(context, battle_result, controller) -> BondStop | None:
    """战斗已结束后的立即交接；关闭、战败和失败均不增加截图或识别。"""
    if not battle_result.ok or battle_result.reason != "victory" or context.tasker.stopping:
        return None
    settings_error = None
    try:
        mode, _ = _settings(context)
    except Exception as exc:
        mode, settings_error = None, exc
    if mode == "off":
        return None
    root = _root(context)
    token = bond_stops.ensure(root)
    if bond_stops.upgrade_continuation(root) is not None or bond_stops.observation_complete(root):
        return None
    evidence_image, image = None, None
    try:
        if settings_error is not None:
            raise settings_error
        image = controller.post_screencap().wait().get()
        if image is None:
            raise ValueError("胜利后的羁绊结算交接截图失败")
        evidence_image = image.copy()
        if read_bond_page(context, image) or read_bond_upgrade(context, image):
            result = inspect_bond_stop(context, image, controller=controller)
        else:
            guard = (context.get_node_data(GUARD_NODE) or {}).get("attach") or {}
            wait_node, attempts, timeout = _poll_config(guard, "page_wait")
            _reject_missed_bond_page(context, image)
            if context.tasker.stopping:
                return None
            deadline = time.monotonic() + timeout / 1000
            result = None
            found_page = False
            for _ in range(attempts):
                if time.monotonic() >= deadline:
                    break
                if not _poll_wait(context, token, wait_node):
                    return None
                image = controller.post_screencap().wait().get()
                if image is None:
                    raise ValueError("等待羁绊结算页时截图失败")
                if read_bond_page(context, image) or read_bond_upgrade(context, image):
                    found_page = True
                    result = inspect_bond_stop(context, image, controller=controller)
                    break
                _reject_missed_bond_page(context, image)
            if not found_page:
                raise ValueError("已识别战斗结束，但未在配置时限内捕获羁绊结算页或羁绊升级展示。无法确认是否满羁绊，已停止；请保留当前截图反馈。")
    except Exception as exc:
        result = _latch_error(context, token, evidence_image, exc, latest_image=image)
    if result:
        save_bond_stop(context)
    return result


@AgentServer.custom_recognition("native_bond_max")
class NativeBondMax(CustomRecognition):
    def analyze(self, context, argv):
        result = inspect_bond_stop(context, argv.image)
        if result:
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"reason": result.reason})
        return None


@AgentServer.custom_recognition("native_bond_upgrade_continue")
class NativeBondUpgradeContinue(CustomRecognition):
    def analyze(self, context, argv):
        if context.tasker.stopping:
            return None
        mode, _ = _settings(context)
        root = _root(context)
        if bond_stops.get(root) is not None:
            return None
        if (mode == "off" or bond_stops.upgrade_continuation(root) is not None
                or bond_stops.observation_complete(root)):
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"bond_upgrade_continue": True})
        return None


@AgentServer.custom_action("save_native_bond_stop")
class SaveNativeBondStop(CustomAction):
    def run(self, context, argv):
        result = save_bond_stop(context)
        if result is None:
            return CustomAction.RunResult(success=False)
        context.override_pipeline({"结算-羁绊停止出口": {
            "focus": {"Node.Action.Starting": html.escape(result.display())}}})
        return CustomAction.RunResult(success=True)


@AgentServer.tasker_sink()
class BondStopLifecycle(TaskerEventSink):
    def on_tasker_task(self, tasker, noti_type, detail):
        if detail.entry == "MaaTaskerPostStop":
            bond_stops.stop_owner(detail.uuid)
        elif noti_type == NotificationType.Starting:
            bond_stops.begin(detail.task_id, detail.uuid)
        elif noti_type in (NotificationType.Succeeded, NotificationType.Failed):
            bond_stops.finish(detail.task_id)
