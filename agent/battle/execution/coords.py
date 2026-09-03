"""1280x720 逻辑坐标。MFW Controller 已把设备分辨率归一到此坐标系。

卡片用 ROI 框 (x, y, w, h)：点击取框中心。
其余交互点用 (x, y) 点。
"""
from __future__ import annotations

from typing import Tuple


def center(box: Tuple[int, int, int, int]) -> Tuple[int, int]:
    x, y, w, h = box
    return (x + w // 2, y + h // 2)


# 下排 5 张面卡的 ROI 框（用于 OCR 识别卡牌类型）
CARD_ROI = {
    1: (75, 529, 138, 98),
    2: (320, 529, 136, 98),
    3: (578, 529, 136, 98),
    4: (842, 529, 127, 98),
    5: (1097, 529, 129, 98),
}

# 上排最多 3 张宝具卡的 ROI 框（按从者槽位）——用于 OCR 检测 NP 数值
NP_ROI = {
    1: (222, 656, 83, 26),
    2: (540, 657, 83, 24),
    3: (865, 655, 73, 25),
}

# 宝具卡点击位置（上排卡牌位置，用于 select_np 点击）
NP_CLICK = {
    1: (410, 138),
    2: (640, 138),
    3: (875, 138),
}

# 主界面"攻击"按钮点击点（开卡）
ATTACK_BTN = (1136, 601)

# 敌方槽位 ROI 框（用于感知层识别存活/选中状态）
ENEMY_ROI = {
    1: (53, 43, 11, 11),
    2: (289, 42, 7, 9),
    3: (538, 40, 6, 4),
}

# 敌方槽位点击点（选目标）
ENEMY_POINT = {
    slot: center(roi) for slot, roi in ENEMY_ROI.items()
}

# 从者技能 ROI 框 (servant_slot 1..3, skill_index 1..3) -> (x, y, w, h)
# 用于感知层识别技能 CD/可用状态
SERVANT_SKILL_ROI = {
    (1, 1): (65, 568, 8, 10), (1, 2): (160, 574, 4, 9), (1, 3): (246, 574, 4, 4),
    (2, 1): (392, 577, 5, 3), (2, 2): (474, 574, 10, 7), (2, 3): (565, 582, 5, 2),
    (3, 1): (700, 579, 7, 5), (3, 2): (795, 572, 5, 4), (3, 3): (881, 579, 4, 8),
}

# 从者技能点击点 (servant_slot 1..3, skill_index 1..3) -> (x, y)
SERVANT_SKILL_CLICK = {
    (s, i): center(roi) for (s, i), roi in SERVANT_SKILL_ROI.items()
}

# 技能目标选择子屏（己方从者槽位）ROI 框 1..3
SKILL_TARGET_ALLY_ROI = {
    1: (324, 383, 22, 29),
    2: (626, 379, 14, 15),
    3: (944, 382, 15, 13),
}

# 技能目标选择子屏（己方从者槽位）点击点 1..3
SKILL_TARGET_ALLY = {
    slot: center(roi) for slot, roi in SKILL_TARGET_ALLY_ROI.items()
}

# 仇凛色卡/库库尔坎暴击星等专属技能流程的点击坐标已随识别一起
# 下沉到 assets/resource/base/pipeline/自动战斗_特殊技能.json（TODO 标定也在该文件）。

# 御主技能菜单按钮
MASTER_SKILL_MENU_ROI = (1187, 306, 9, 11)
MASTER_SKILL_MENU_BTN = center(MASTER_SKILL_MENU_ROI)

# 御主技能 ROI 框（展开后）——用于感知层识别
MASTER_SKILL_ROI = {
    1: (898, 303, 11, 15),
    2: (985, 309, 13, 12),
    3: (1084, 312, 8, 8),
}

# 御主技能点击点（展开后）
MASTER_SKILL_CLICK = {
    idx: center(roi) for idx, roi in MASTER_SKILL_ROI.items()
}

# 点击 CD 中技能后出现的"技能使用"提示窗。
SKILL_USE_DIALOG_TITLE_ROI = (556, 156, 146, 42)
SKILL_USE_DIALOG_CLOSE_ROI = (1088, 166, 48, 27)
SKILL_USE_DIALOG_CLOSE_BTN = center(SKILL_USE_DIALOG_CLOSE_ROI)

# 通用右上角关闭点（用于等待时持续点击以关闭可能弹出的遮挡层）
TOP_RIGHT_CLOSE = (1240, 20)

# 换人界面（Order Change）场景识别 ROI（OCR "请从首发成员和候补成员中"）
ORDER_CHANGE_SCENE_ROI = (339, 134, 625, 63)

# 换人界面（Order Change）选择从者 ROI 框 (1..6)
ORDER_CHANGE_MEMBER_ROI = {
    1: (119, 343, 21, 20),
    2: (307, 340, 26, 20),
    3: (533, 345, 12, 10),
    4: (711, 344, 10, 7),
    5: (930, 340, 25, 19),
    6: (1133, 348, 14, 14),
}

# 换人界面（Order Change）选择从者点击点 (1..6)
ORDER_CHANGE_MEMBER = {
    slot: center(roi) for slot, roi in ORDER_CHANGE_MEMBER_ROI.items()
}
ORDER_CHANGE_CONFIRM_ROI = (559, 592, 151, 58)
ORDER_CHANGE_CONFIRM_BTN = center(ORDER_CHANGE_CONFIRM_ROI)
ORDER_CHANGE_CANCEL_BTN = center((1203, 123, 30, 24))

# ---- 战斗结算流程（胜利后：掉落/羁绊/结果多屏点击直到回关卡列表）----
# 标定护栏：未真机标定前保持 False；runtime 在未标定时不会盲点，仍按"识别到胜利即返回成功"处理。
SETTLEMENT_CALIBRATED = False
# TODO(标定): 结算各屏"继续/下一步"的安全点击点（1280x720）。
#   ⚠ 标定注意：该点必须只推进结算，绝不能落在"连续出击/继续出击"的确认钮上，
#     否则会误触发下一场战斗（违背安全边界）。若结算含连续出击弹窗，需另标一个"取消连续出击"节点+坐标。
SETTLEMENT_CONTINUE = (0, 0)  # 占位，未标定
