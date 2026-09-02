"""战斗领域枚举。纯 stdlib，禁止 import maa/cv2/socket。"""
from enum import Enum


class Scene(str, Enum):
    MAIN_BATTLE = "main_battle"           # 主界面：从者技能 + 御主技能 + 攻击钮
    COMMAND_SELECTION = "command_selection"  # 选卡界面：5 面卡 + 0~3 宝具卡
    SKILL_TARGET_SELECTION = "skill_target_selection" # 技能目标选择子屏
    ORDER_CHANGE = "order_change"           # 换人界面：首发+候补成员选择
    ANIMATION = "animation"               # 攻击/技能动画（20~40s）
    VICTORY = "victory"
    DEFEAT = "defeat"
    DIALOG = "dialog"
    UNKNOWN = "unknown"


class Subscene(str, Enum):
    """子场景：叠加在基础 Scene 上的覆盖层弹窗。

    与 Scene 的区别：
    - Scene 是互斥的全屏状态，主循环路由用
    - Subscene 是局部的覆盖层（如提示窗），只关心它的流程才检测
    - 不参与 detect_scene 全场景轮询，避免每帧多余识别
    """
    SKILL_USE_DIALOG = "skill_use_dialog"       # 技能使用弹窗（点击 CD 技能后弹出）
    SKILL_UNUSABLE_DIALOG = "skill_unusable_dialog"  # 技能无法使用弹窗（点击不可用技能后弹出）


class CardColor(str, Enum):
    BUSTER = "B"
    ARTS = "A"
    QUICK = "Q"


class PrimitiveKind(str, Enum):
    SELECT_ENEMY = "select_enemy"
    SELECT_CARD = "select_card"   # 下排面卡
    SELECT_NP = "select_np"       # 上排宝具卡
    CAST_SERVANT_SKILL = "cast_servant_skill"
    CAST_MASTER_SKILL = "cast_master_skill"
    ORDER_CHANGE = "order_change"
    ATTACK = "attack"
    STOP = "stop"
