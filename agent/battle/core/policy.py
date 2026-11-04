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
class OpeningHandPolicy:
    """Delay the whole first planned turn until observed face cards match."""
    enabled: bool = False
    servant_id: str | None = None
    servant_slot: int | None = None
    required_colors: Tuple[CardColor, ...] = ()
    max_wait_turns: int = 2


@dataclass(frozen=True)
class NativeCardStrategy:
    """可选的完整三张卡组合评分；默认关闭，不影响现有选卡。

    权重是相对偏好，不是伤害/NP收益的模拟值。只使用已识别的卡色、
    归属与行动状态，以及显式配置的宝具颜色；没有信息时不能推断。
    从者 ID 优先级随实际归属移动；preferred_slots 指当前前排位置 1..3。
    两个优先列表均按先后递减，同一张卡取两者较高值，不重复加分。
    """
    enabled: bool = False
    prefer_brave_chain: bool = True
    brave_chain_weight: float = 12.0
    mighty_chain_weight: float = 8.0
    same_color_chain_weight: float = 5.0
    preferred_servant_ids: Tuple[str, ...] = ()
    preferred_slots: Tuple[int, ...] = ()
    servant_priority_weight: float = 6.0
    # 空配置沿用 CardPolicy.color_priority；JSON 使用 {"B":3,"A":2,"Q":1}。
    color_weights: Tuple[Tuple[CardColor, float], ...] = ()
    # 必须由已核对的技能/宝具资料显式提供；ID 配置优先，缺失才用当前槽位。
    # 可变色宝具应由使用者同步配置，不能把本表当作实战颜色识别结果。
    np_colors_by_servant_id: Tuple[Tuple[str, CardColor], ...] = ()
    np_colors_by_slot: Tuple[Tuple[int, CardColor], ...] = ()
    opening_hand: OpeningHandPolicy = field(default_factory=OpeningHandPolicy)
    # Optional preference for preserving finite same-colour buffs for the NP.
    # It only scores fill candidates; explicit plan cards always stay fixed.
    avoid_np_color_before_np: bool = False
    np_color_preservation_weight: float = 20.0


@dataclass(frozen=True)
class CardPolicy:
    goal: Goal = Goal.FINISH_WAVE
    color_priority: Tuple[CardColor, ...] = (CardColor.BUSTER, CardColor.ARTS, CardColor.QUICK)
    np_first: bool = True                 # 有宝具卡则优先出
    prefer_mighty_chain: bool = True      # 三色连锁（红蓝绿各一张）优先
    native_strategy: NativeCardStrategy = field(default_factory=NativeCardStrategy)


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
