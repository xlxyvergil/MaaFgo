"""Bounded UI cleanup before discarding an interrupted preset battle plan.

Only the caller decides whether a task may use this recovery.  This helper never
casts skills, chooses cards, retreats or continues a defeated battle.  In
particular it first cancels card selection so a partially selected old chain
cannot be completed accidentally by the default battle runner.
"""
from __future__ import annotations

import time

import mfaalog

from ..core.enums import Scene
from ..perception import perception
from ..perception.config import SCENE_NODES
from .special_skills import NP_IDENTITY_GUARD, UNKNOWN_MENU


# A third card may already have submitted a long NP animation when execution
# reported a failure.  Match the normal attack-animation wait without blind taps.
_RECOVERY_TIMEOUT_S = 120.0
_POLL_DELAY_S = 0.5
_MAX_CLOSE_ACTIONS = 8
_MAX_SELECTION_RETURNS = 2
_MAIN_CONFIRMATIONS = 2
_CARD_RETURN = "战斗_选卡返回主界面"
_SKILL_USE_DIALOG = "战斗_技能使用弹窗"
_UNUSABLE_DIALOG = "战斗_技能无法使用弹窗"
_OVERLAY_NODES = (_SKILL_USE_DIALOG, _UNUSABLE_DIALOG, "战斗_技能目标子屏",
                  NP_IDENTITY_GUARD, UNKNOWN_MENU)


def _hit(context, name, image):
    result = context.run_recognition(name, image)
    return result if result and result.hit else None


def recover_for_default_battle(runtime) -> bool:
    """Dismiss preset leftovers and confirm a clean main screen or a terminal.

    ``runtime`` supplies the already selected controller, context and Executor.
    Every action consumes a new screenshot.  Repeated confirmations concern the
    battle UI state, never pixel similarity or a frozen animation.
    """
    context, controller, executor = runtime.ctx, runtime.controller, runtime.executor
    deadline = time.monotonic() + _RECOVERY_TIMEOUT_S
    initial_close_done = False
    close_actions = selection_returns = main_confirmations = 0
    try:
        while time.monotonic() < deadline:
            if context.tasker.stopping:
                return False
            image = controller.post_screencap().wait().get()
            if image is None:
                mfaalog.error("[预设战斗回退] 无法取得当前战斗画面，不能确认弹窗是否关闭，已停止。请检查模拟器连接后重试。")
                return False
            # Do not dismiss a victory/defeat screen or consume any revive UI.
            if any(_hit(context, SCENE_NODES[name], image) for name in ("victory", "defeat")):
                return not context.tasker.stopping
            scene, confidence = perception._detect_scene(context, image)
            if context.tasker.stopping:
                return False

            # The user-requested first cleanup is the existing harmless battle
            # close point.  Do not assume this click actually closed anything.
            if not initial_close_done:
                executor.tap_top_right_close()
                initial_close_done = True
                close_actions += 1
                time.sleep(_POLL_DELAY_S)
                continue

            if scene is Scene.ORDER_CHANGE:
                main_confirmations = 0
                if close_actions >= _MAX_CLOSE_ACTIONS:
                    break
                if executor.cancel_order_change() is False:
                    return False
                close_actions += 1
                time.sleep(_POLL_DELAY_S)
                continue
            if scene is Scene.COMMAND_SELECTION:
                main_confirmations = 0
                if selection_returns >= _MAX_SELECTION_RETURNS:
                    break
                # A recognition-guarded leaf cancels every old card selection.
                # Verify the main screen from our next actual frame, not from
                # the old plan or a Pipeline success result alone.
                context.run_task(_CARD_RETURN, pipeline_override={
                    _CARD_RETURN: {"next": [], "on_error": [], "timeout": 1000},
                })
                selection_returns += 1
                time.sleep(_POLL_DELAY_S)
                continue

            overlay = next(((name, detail) for name in _OVERLAY_NODES
                            if (detail := _hit(context, name, image))), None)
            if context.tasker.stopping:
                return False
            if overlay is not None:
                main_confirmations = 0
                if close_actions >= _MAX_CLOSE_ACTIONS:
                    break
                name, detail = overlay
                if name == _SKILL_USE_DIALOG:
                    ok = executor.close_skill_use_dialog(detail)
                elif name == _UNUSABLE_DIALOG:
                    ok = executor.close_dialog_by_detail(detail)
                else:
                    ok = executor.tap_top_right_close()
                if ok is False:
                    return False
                close_actions += 1
            elif scene is Scene.MAIN_BATTLE and confidence >= .95:
                main_confirmations += 1
                if main_confirmations >= _MAIN_CONFIRMATIONS:
                    mfaalog.info("[预设战斗回退] 已关闭残留界面并确认回到战斗主界面，交给无预设自动战斗。")
                    return True
            else:
                # Unknown may be a submitted attack/NP animation.  One initial
                # close is enough; never keep tapping through an unknown screen.
                main_confirmations = 0
            time.sleep(_POLL_DELAY_S)
    except Exception as exc:
        mfaalog.error(f"[预设战斗回退] 关闭残留界面时发生异常，无法确认可以继续战斗，已保留现场停止。请检查游戏画面并导出日志。原因：{exc}")
        return False
    mfaalog.error("[预设战斗回退] 未能确认残留弹窗已关闭并返回战斗主界面，无法切换默认战斗，已保留现场停止。请手动关闭弹窗或退出选卡界面后重试。")
    return False
