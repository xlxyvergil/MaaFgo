"""多模态视觉层的纯数据契约。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple
from battle.core.enums import CardColor, Scene
from battle.core.models import BattleState

@dataclass(frozen=True)
class VisualCard:
    ui_slot: int
    color: Optional[CardColor] = None
    owner_slot: Optional[int] = None
    is_np: bool = False
    confidence: float = 0.0

@dataclass(frozen=True)
class VisualServant:
    slot: int
    present: Optional[bool] = None
    skill_available: Tuple[Optional[bool], Optional[bool], Optional[bool]] = (None, None, None)
    np_percent: Optional[int] = None
    confidence: float = 0.0

@dataclass(frozen=True)
class VisualEnemy:
    slot: int
    alive: Optional[bool] = None
    targeted: Optional[bool] = None
    hp_ratio: Optional[float] = None
    break_state: Optional[str] = None
    confidence: float = 0.0

@dataclass(frozen=True)
class VisualDialog:
    kind: str
    text: Optional[str] = None
    blocking: bool = False
    confidence: float = 0.0

@dataclass(frozen=True)
class VisualObservation:
    scene: Optional[Scene] = None
    cards: Tuple[VisualCard, ...] = ()
    servants: Tuple[VisualServant, ...] = ()
    enemies: Tuple[VisualEnemy, ...] = ()
    dialogs: Tuple[VisualDialog, ...] = ()
    confidence: float = 0.0
    unknown_fields: Tuple[str, ...] = ()
    evidence_id: str = ""
    schema_version: int = 1

@dataclass(frozen=True)
class VisionRequest:
    image: bytes
    image_format: str = "png"
    state: Optional[BattleState] = None
    turn_index: int = 0
    requested_fields: Tuple[str, ...] = ()
    recent_actions: Tuple[str, ...] = ()
    evidence_id: str = ""

@dataclass(frozen=True)
class VisionResponse:
    raw_text: str
    observation: Optional[VisualObservation]
    model: str = ""
    latency_ms: int = 0
    usage: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
