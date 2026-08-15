"""战斗领域契约（V1b）。纯 stdlib，禁止 import maa/cv2/socket。

约定：
- 坐标不出现在这里。PrimitiveAction 只携带槽位号，真实 720p 坐标只存在于 execution 层。
- 视觉不确定必须能显式表达（Confidence、unknown_fields），不允许用假默认值掩盖识别失败。
- V1b 只用 选目标 + 选 3 张卡；技能/NP 顺序字段暂不建模，等 V2 再加。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from .enums import CardColor, PrimitiveKind, Scene

SCHEMA_VERSION = 1


def is_slot(value: object, low: int, high: int) -> bool:
    """Return whether ``value`` is an integer within ``[low, high]`` (inclusive)."""
    return type(value) is int and low <= value <= high


@dataclass(frozen=True)
class Confidence:
    value: float
    source: str = ""

    def passes(self, threshold: float) -> bool:
        return self.value >= threshold


@dataclass(frozen=True)
class CommandCard:
    """下排面卡。"""
    ui_slot: int                     # 1..5
    color: CardColor
    owner_slot: Optional[int]        # 1..3；V1 先为 None
    confidence: Confidence


@dataclass(frozen=True)
class NpCard:
    """上排宝具卡（某从者 NP 满时出现）。"""
    servant_slot: int                # 1..3，宝具卡的归属从者
    confidence: Confidence
    percent: int | None = None       # OCR 读到的 NP 百分比；调试/审计用


@dataclass(frozen=True)
class SkillState:
    # True=明确可用，False=明确 CD，None=无法确认。
    available: bool | None
    confidence: Confidence


@dataclass(frozen=True)
class ServantState:
    slot: int
    skills: Tuple[SkillState, SkillState, SkillState]
    confidence: Confidence


@dataclass(frozen=True)
class EnemyState:
    slot: int
    alive: bool
    targeted: bool
    confidence: Confidence


@dataclass(frozen=True)
class BattleState:
    scene: Scene
    scene_confidence: Confidence
    cards: Tuple[CommandCard, ...]           # 期望 5 张
    np_cards: Tuple[NpCard, ...]             # 0..3 张
    enemies: Tuple[EnemyState, ...]
    servants: Tuple[ServantState, ...] = ()
    master_skills: Tuple[SkillState, ...] = ()   # 御主技能 1..3 的可用性
    np_slots_full: Tuple[int, ...] = ()      # 战斗界面检测到 NP 槽金色（NP 高/满）的从者槽位
    screenshot_id: str = ""
    schema_version: int = SCHEMA_VERSION
    unknown_fields: Tuple[str, ...] = ()

    def command_ready(self, threshold: float = 0.95) -> bool:
        return self.scene is Scene.COMMAND_SELECTION and self.scene_confidence.passes(threshold)

    def cards_ready(self, threshold: float = 0.90) -> bool:
        return len(self.cards) == 5 and all(c.confidence.passes(threshold) for c in self.cards)


@dataclass(frozen=True)
class CardPick:
    """一次选卡：来自面卡或宝具卡。"""
    kind: PrimitiveKind              # SELECT_CARD | SELECT_NP
    slot: int                        # SELECT_CARD: ui_slot 1..5 ; SELECT_NP: servant_slot 1..3


@dataclass(frozen=True)
class ServantSkillAction:
    servant_slot: int             # 1..3
    skill_index: int              # 1..3
    target_ally: Optional[int] = None   # 1..3


@dataclass(frozen=True)
class MasterSkillAction:
    skill_index: int              # 1..3
    target_ally: Optional[int] = None   # 1..3


@dataclass(frozen=True)
class OrderChangeAction:
    starting_member_idx: int      # 1..3
    sub_member_idx: int           # 4..6


@dataclass(frozen=True)
class TurnPlan:
    """单个回合的固定计划。"""
    servant_skills: Tuple[ServantSkillAction, ...] = ()
    master_skills: Tuple[MasterSkillAction, ...] = ()
    order_change: Optional[OrderChangeAction] = None
    np_order: Tuple[int, ...] = ()       # 希望释放的宝具（从者槽位，1..3）
    target_enemy: Optional[int] = None   # 需要选中的敌人目标


@dataclass(frozen=True)
class BattlePlan:
    """整场战斗的固定计划：按回合索引取 TurnPlan。

    turns 为 0-based 索引列表；超出长度时返回空 TurnPlan（平A/宝具兜底）。
    """
    turns: Tuple[TurnPlan, ...] = ()

    def turn(self, index: int) -> TurnPlan:
        """获取第 index 回合（0-based）的计划；越界返回空计划。"""
        if 0 <= index < len(self.turns):
            return self.turns[index]
        return TurnPlan()


@dataclass(frozen=True)
class BattleAction:
    target_enemy: Optional[int]
    picks: Tuple[CardPick, ...]      # 恰 3 个，含出卡顺序
    servant_skills: Tuple[ServantSkillAction, ...] = ()
    master_skills: Tuple[MasterSkillAction, ...] = ()
    order_change: Optional[OrderChangeAction] = None
    rationale_tag: str = ""


@dataclass(frozen=True)
class PrimitiveAction:
    """执行层的受限原子动作，不含 x/y。"""
    kind: PrimitiveKind
    slot: Optional[int] = None
    skill_index: Optional[int] = None
    target_ally: Optional[int] = None
    starting_member_idx: Optional[int] = None
    sub_member_idx: Optional[int] = None
