"""
FGO 游戏数据类型定义与常量映射表
"""

from typing import Optional, Dict, List, Any, Union
from dataclasses import dataclass, field
from enum import Enum


class SupportType(Enum):
    """助战类型枚举"""
    NONE = "none"
    FRIEND = "friend"
    FIXED = "fixed"
    NPC = "npc"


@dataclass
class ServantInfo:
    """从者信息数据结构"""
    svt_id: Optional[int] = None
    name: Optional[str] = None
    lv: int = 1
    skill_lvs: List[int] = field(default_factory=lambda: [10, 10, 10])
    append_lvs: List[int] = field(default_factory=list)
    td_lv: int = 1
    atk_fou: int = 1000
    hp_fou: int = 1000
    ce_id: Optional[int] = None
    ce_limit_break: bool = False
    ce_lv: int = 0
    support_type: SupportType = SupportType.NONE
    is_on_field: bool = True
    position: int = 0  # 0-5


@dataclass
class MysticCodeInfo:
    """魔术礼装信息"""
    mystic_code_id: int = 0
    level: int = 10


@dataclass
class TeamFormation:
    """队伍编成信息"""
    on_field: List[Optional[ServantInfo]] = field(default_factory=lambda: [None, None, None])
    backup: List[Optional[ServantInfo]] = field(default_factory=lambda: [None, None, None])
    mystic_code: MysticCodeInfo = field(default_factory=MysticCodeInfo)


@dataclass
class ConvertedConfig:
    """转换后的 BBC 配置"""
    servant_names: List[Optional[str]] = field(default_factory=lambda: [None] * 6)
    assist_idx: int = 2
    assist_mode: str = "从者礼装"
    assist_equip: Union[str, List[Optional[str]]] = field(default_factory=lambda: [None, None, None])
    master_equip: int = 0
    used_servant: List[int] = field(default_factory=list)
    rounds: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


# Chaldea mysticCodeId → BBC master_equip SN 映射表
# Chaldea/Atlas Academy 使用的是游戏内部 ID (1, 20, 30, ...)
# BBC 使用的是自增序号 (0, 1, 2, ...)
MYSTIC_CODE_ID_TO_BBC_SN: Dict[int, int] = {
    1:   7,   # 魔术礼装·迦勒底
    20:  6,   # 魔术礼装·迦勒底战斗服 (换人服)
    30:  9,   # 魔术礼装·魔术协会制服
    40:  1,   # 魔术礼装·阿特拉斯院制服
    50:  3,   # 金色庆典
    60:  12,  # 王室品牌
    70:  11,  # 明亮夏日
    80:  14,  # 月之海的记忆
    90:  15,  # 月之背面的记忆
    100: 0,   # 2004年的碎片
    110: 8,   # 魔术礼装·极地用迦勒底制服
    120: 10,  # 热带夏日
    130: 13,  # 华美的新年
    150: 4,   # 迦勒底船长
    160: 2,   # 第五真说要素环境用迦勒底制服
    170: 5,   # 迦勒底开拓者
    190: 16,  # 万圣夜王室装
    210: 17,  # 决战用迦勒底制服
    240: 18,  # 总耶高校学生服
    260: 19,  # 新春装束
    330: 20,  # 夏日街头
    340: 21,  # 白色圣诞
    360: 22,  # 三咲高校学生服
    410: 23,  # 冬日便装
    430: 24,  # 浅葱的队服
    440: 25,  # 标准·迦勒底制服
    460: 26,  # 二十八怪物（クラン・カラティン）
    470: 27,  # 迦勒底学园制服
}

# 换人服的 mysticCodeId 列表
ORDER_CHANGE_MYSTIC_CODE_IDS: set = {20, 210, 440}

# 默认常规指令卡优先级策略 (从 BBC 配置文件中提取的经典保底策略)
DEFAULT_STRATEGY: List[Dict[str, Any]] = [
    {
        "card1": {
            "type": 0,
            "cards": [1],
            "criticalStar": 0,
            "more_or_less": True
        },
        "card2": {
            "type": 1,
            "cards": ["1A", "1B", "1Q", "2B", "3B", "2A", "3A", "2Q", "3Q"],
            "criticalStar": 0,
            "more_or_less": True
        },
        "card3": {
            "type": 1,
            "cards": ["1A", "1B", "1Q", "2B", "3B", "2A", "3A", "2Q", "3Q"],
            "criticalStar": 0,
            "more_or_less": True
        },
        "breakpoint": [False, False],
        "colorFirst": True
    }
]
