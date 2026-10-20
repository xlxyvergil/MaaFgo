"""战斗领域契约（V1b）。纯 stdlib，禁止 import maa/cv2/socket。

约定：
- 坐标不出现在这里。PrimitiveAction 只携带槽位号，真实 720p 坐标只存在于 execution 层。
- 视觉不确定必须能显式表达（Confidence、unknown_fields），不允许用假默认值掩盖识别失败。
- V2 支持技能、换人及按从者/卡色描述的面卡与宝具混合顺序。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from .enums import CardColor, PrimitiveKind, Scene

SCHEMA_VERSION = 2


def is_slot(value: object, low: int, high: int) -> bool:
    """Return whether ``value`` is an integer within ``[low, high]`` (inclusive)."""
    return type(value) is int and low <= value <= high


@dataclass(frozen=True)
class Confidence:
    value: float
    source: str = ""

    def passes(self, threshold: float) -> bool:
        return self.value >= threshold


# 单帧采集：确认页为静态界面，单帧全库匹配即可给出结论。
# formation_action.IDENTITY_FRAME_NODES 与 formation_session 的 publish 校验共用此值。
REQUIRED_FRAME_COUNT = 1


@dataclass(frozen=True)
class FormationSlot:
    """编队确认页的成员；slot 为 1..6，不是战斗中动态的前排位置。

    confidence 是匹配证据分，不是正确率。class_id 来自独立的职阶识别；
    catalog_class 仅在身份确认后从本地目录解析，不能冒充视觉职阶证据。
    """

    slot: int
    status: str                       # empty / identified / unknown
    servant_id: str | None = None
    class_id: str | None = None
    is_support: bool | None = None
    identity_confidence: Confidence = field(default_factory=lambda: Confidence(0.0))
    class_confidence: Confidence = field(default_factory=lambda: Confidence(0.0))
    catalog_class: str | None = None
    best_candidate_id: str | None = None  # 诊断候选，绝不能作为已确认身份使用
    best_score: float = 0.0
    identity_margin: float = 0.0
    matched_template: str = ""
    consistent_frames: int = 0
    reason: str = ""
    search_scope: str = ""

    def __post_init__(self):
        if not is_slot(self.slot, 1, 6):
            raise ValueError("formation slot must be 1..6")
        if self.status not in {"empty", "identified", "unknown"}:
            raise ValueError("invalid formation slot status")
        if (self.status == "identified") != bool(self.servant_id):
            raise ValueError("only identified slots may carry a servant_id")
        if self.status == "empty" and self.is_support is not False:
            raise ValueError("empty slot cannot be support")


@dataclass(frozen=True)
class InitialFormation:
    """一次真实采集的不可变快照；复用时也不改写采集时间或初始位置。"""

    slots: Tuple[FormationSlot, ...]
    task_id: int
    session_id: str
    revision: int
    captured_at: str
    captured_battle: int
    source: str = "formation_confirmation"
    calibration_id: str = "unverified"

    def __post_init__(self):
        if tuple(s.slot for s in self.slots) != (1, 2, 3, 4, 5, 6):
            raise ValueError("initial formation must contain six ordered slots")
        if not self.session_id or self.task_id <= 0 or self.revision <= 0:
            raise ValueError("initial formation requires task/session/revision")

    def member_key(self, slot: int) -> tuple[str, int, int]:
        if not is_slot(slot, 1, 6):
            raise ValueError("formation slot must be 1..6")
        return self.session_id, self.revision, slot


@dataclass(frozen=True)
class CommandCard:
    """下排面卡。"""
    ui_slot: int                     # 1..5
    color: CardColor
    owner_slot: Optional[int]        # 当前前排槽位 1..3；不确定时为 None
    confidence: Confidence           # 卡色识别置信度
    owner_confidence: Confidence = field(default_factory=lambda: Confidence(0.0))
    owner_servant_id: str | None = None
    owner_margin: float = 0.0
    owner_template: str = ""
    owner_reason: str = ""
    can_attack: bool = True          # 封印/无法行动的面卡仍可选作回合占位
    blocked_reason: str = ""


@dataclass(frozen=True)
class NpCard:
    """上排实际可见的宝具卡；封印时仍记录卡面，但不允许选择。"""
    servant_slot: int                # 1..3，宝具卡的归属从者
    confidence: Confidence
    percent: int | None = None       # 兼容历史快照；当前不再读取 NP 数值
    available: bool = True
    blocked_reason: str = ""
    owner_servant_id: str | None = None
    owner_confidence: Confidence = field(default_factory=lambda: Confidence(0.0))
    owner_template: str = ""


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
    servant_id: str | None = None


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

    def card_owners_ready(self, threshold: float = 0.86) -> bool:
        return len(self.cards) == 5 and all(
            c.owner_slot is not None and c.owner_confidence.passes(threshold)
            for c in self.cards
        )


@dataclass(frozen=True)
class CardPick:
    """一次选卡：来自面卡或宝具卡。"""
    kind: PrimitiveKind              # SELECT_CARD | SELECT_NP
    slot: int                        # SELECT_CARD: ui_slot 1..5 ; SELECT_NP: servant_slot 1..3


@dataclass(frozen=True)
class PlannedCard:
    """攻略中的出卡意图；面卡位置由当前截图的从者归属与卡色解析。"""
    kind: PrimitiveKind
    servant_slot: int
    color: CardColor | None = None


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
    skill_sequence: Tuple[ServantSkillAction | MasterSkillAction | OrderChangeAction, ...] = ()
    card_sequence: Tuple[PlannedCard, ...] = ()


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
    skill_sequence: Tuple[ServantSkillAction | MasterSkillAction | OrderChangeAction, ...] = ()


@dataclass(frozen=True)
class PrimitiveAction:
    """执行层的受限原子动作，不含 x/y。"""
    kind: PrimitiveKind
    slot: Optional[int] = None
    skill_index: Optional[int] = None
    target_ally: Optional[int] = None
    starting_member_idx: Optional[int] = None
    sub_member_idx: Optional[int] = None
