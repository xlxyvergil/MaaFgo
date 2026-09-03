"""战斗领域枚举。纯 stdlib，禁止 import maa/cv2/socket。"""
from enum import Enum


class Scene(str, Enum):
    MAIN_BATTLE = "main_battle"           # 主界面：从者技能 + 御主技能 + 攻击钮
    COMMAND_SELECTION = "command_selection"  # 选卡界面：5 面卡 + 0~3 宝具卡
    ORDER_CHANGE = "order_change"           # 换人界面：首发+候补成员选择
    ANIMATION = "animation"               # 攻击/技能动画（20~40s）
    VICTORY = "victory"
    DEFEAT = "defeat"
    DIALOG = "dialog"
    UNKNOWN = "unknown"


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
