"""原子操作层：受限原子动作 -> 1280x720 坐标点击。

真实流程（已按游戏确认）：
  主界面(有攻击钮) --open_command_cards()--> 选卡界面 --选3张--> 选完第3张自动发动 -> 动画

安全边界（硬禁区）：本类**故意不提供** 令咒/圣晶石复活/氪金/抽卡/补 AP 入口。
"""
from __future__ import annotations

from . import coords

import time


# 两步点击之间的固定间隔；配合 controller 时序保证点击生效
_MASTER_SKILL_MENU_DELAY_S = 0.5
_ORDER_CHANGE_STEP_DELAY_S = 0.3


class Executor:
    def __init__(self, context, controller=None) -> None:
        self.ctx = context
        self.controller = controller or context.tasker.controller

    # ---- 原子动作（V1b）----

    def open_command_cards(self) -> bool:
        """主界面点攻击钮。"""
        self._click(coords.ATTACK_BTN)
        return True

    def select_card(self, ui_slot: int) -> bool:
        self._click(coords.center(coords.CARD_ROI[ui_slot]))
        return True

    def select_np(self, servant_slot: int) -> bool:
        self._click(coords.NP_CLICK[servant_slot])
        return True

    def select_enemy(self, slot: int) -> bool:
        self._click(coords.ENEMY_POINT[slot])
        return True

    def cast_servant_skill(self, servant_slot: int, skill_index: int) -> bool:
        self._click(coords.SERVANT_SKILL_CLICK[(servant_slot, skill_index)])
        return True

    def select_skill_target(self, target_ally: int) -> bool:
        self._click(coords.SKILL_TARGET_ALLY[target_ally])
        return True

    def cast_master_skill(self, skill_index: int) -> bool:
        self._click(coords.MASTER_SKILL_MENU_BTN)
        time.sleep(_MASTER_SKILL_MENU_DELAY_S)
        self._click(coords.MASTER_SKILL_CLICK[skill_index])
        return True

    def close_skill_use_dialog(self, detail=None) -> bool:
        """关闭点击 CD 技能后出现的"技能使用"提示窗（固定坐标）。"""
        self._click(coords.SKILL_USE_DIALOG_CLOSE_BTN)
        return True

    def click_recognition(self, detail) -> bool:
        """通用识别点击：点击任意识别 detail（box）的中心位置。

        原子动作级通用能力，不限于弹窗关闭——任何"识别到什么就点什么"的
        场景（模板识别按钮、OCR 定位文本等）都可以传入 RecognitionDetail 复用。
        拿不到 box 时返回 False（调用方决定回退策略）。
        """
        box = getattr(getattr(detail, "best_result", None), "box", None)
        if not box:
            return False
        x, y, w, h = box
        self._click((x + w / 2, y + h / 2))
        return True

    def close_dialog_by_detail(self, detail=None) -> bool:
        """通用弹窗关闭：点击识别 detail 的位置；拿不到 box 回退右上角关闭点。"""
        if not self.click_recognition(detail):
            self._click(coords.TOP_RIGHT_CLOSE)
        return True

    # ---- 技能特殊覆盖层：已下沉至 pipeline ----
    # 弹窗关闭/目标选择/专属技能流程（仇凛色卡、库库尔坎暴击星等）的识别与点击
    # 全部声明式定义在 assets/resource/base/pipeline/自动战斗_特殊技能.json，
    # 由 runtime._execute_skill_cast 通过 run_task 按需驱动，新增特殊技能只需加 JSON 节点。
    # executor 仅保留通用原子动作（click_recognition / close_dialog_by_detail /
    # close_skill_use_dialog / select_skill_target），供其他流程单独复用。

    def tap_top_right_close(self) -> None:
        """点击右上角关闭按钮，用于等待时持续点击以关闭可能弹出的遮挡层。"""
        self._click(coords.TOP_RIGHT_CLOSE)

    def order_change(self, starting_member_idx: int, sub_member_idx: int) -> bool:
        self._click(coords.ORDER_CHANGE_MEMBER[starting_member_idx])
        time.sleep(_ORDER_CHANGE_STEP_DELAY_S)
        self._click(coords.ORDER_CHANGE_MEMBER[sub_member_idx])
        time.sleep(_ORDER_CHANGE_STEP_DELAY_S)
        self._click(coords.ORDER_CHANGE_CONFIRM_BTN)
        return True

    def cancel_order_change(self) -> bool:
        """换人界面点取消/退出按钮。"""
        self._click(coords.ORDER_CHANGE_CANCEL_BTN)
        return True

    def tap_settlement_continue(self) -> bool:
        """结算屏点"继续/下一步"。未标定坐标时返回 False（不盲点）。"""
        if not coords.SETTLEMENT_CALIBRATED:
            return False
        self._click(coords.SETTLEMENT_CONTINUE)
        return True

    # 注意：无 attack()——选完第 3 张卡自动发动；也没有令咒/圣晶石/氪金/抽卡入口

    def _click(self, xy) -> None:
        x, y = xy
        self.controller.post_click(int(x), int(y)).wait()
