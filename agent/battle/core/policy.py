"""策略与关卡档。纯 stdlib。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple

from .enums import CardColor


class Goal(str, Enum):
    FINISH_WAVE = "finish_wave"
    BUILD_NP = "build_np"
    BUILD_STARS = "build_stars"


@dataclass(frozen=True)
class CardPolicy:
    goal: Goal = Goal.FINISH_WAVE
    color_priority: Tuple[CardColor, ...] = (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK)
    np_first: bool = True                 # 有宝具卡则优先出
    prefer_mighty_chain: bool = True      # 三色连锁（红蓝绿各一张）优先


@dataclass(frozen=True)
class SkillPolicy:
    """技能决策策略。"""
    # 无计划时是否自动使用从者技能
    auto_servant_skills: bool = True
    # 自动使用哪些从者的技能（空 = 全部从者）
    servant_slots: Tuple[int, ...] = ()
    # 每回合最多自动放几个技能（0 = 不限制）
    max_skills_per_turn: int = 0
    # 跳过指定技能索引（如 (2,) 表示跳过所有从者的技能2）
    skip_skill_indexes: Tuple[int, ...] = ()
    # 是否使用御主技能（默认 False：只使用英灵技能）
    use_master_skills: bool = False


@dataclass(frozen=True)
class BattlePolicy:
    """战斗策略（顶层）：选卡策略 + 技能策略。"""
    card: CardPolicy = field(default_factory=CardPolicy)
    skill: SkillPolicy = field(default_factory=SkillPolicy)


@dataclass(frozen=True)
class StrategyProfile:
    id: str = "farm-safe-v1"
    min_scene_confidence: float = 0.95
    min_card_confidence: float = 0.50
    min_enemy_confidence: float = 0.80
    min_skill_confidence: float = 0.80
    max_turns: int = 20
    # 高风险开关：V1 全部关闭，且执行层根本不提供入口
    allow_command_spell: bool = False
    allow_sq_revive: bool = False
    allow_ap_refill: bool = False
    fallback: str = "stop"                # stop | bbc（仅外层显式允许时）
