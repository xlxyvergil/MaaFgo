"""多模态视觉触发规则与 Runtime 上下文追踪。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Optional, Tuple

from battle.core.enums import Scene


@dataclass(frozen=True)
class SceneTriggerContext:
    current_scene: Scene
    previous_scene: Optional[Scene] = None
    elapsed_since_action_ms: int = 0
    consecutive_unknown: int = 0
    frame_stable: bool = False
    last_action: Optional[str] = None
    unknown_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TriggerDecision:
    should_call: bool
    reasons: Tuple[str, ...] = ()
    requested_fields: Tuple[str, ...] = ()


class VisionTrigger:
    def __init__(self, *, transition_guard_ms: int = 2500, min_unknown_samples: int = 3) -> None:
        self.transition_guard_ms = transition_guard_ms
        self.min_unknown_samples = min_unknown_samples

    def evaluate(self, context: SceneTriggerContext) -> TriggerDecision:
        if context.current_scene is Scene.UNKNOWN:
            if context.elapsed_since_action_ms < self.transition_guard_ms:
                return TriggerDecision(False, ("transition_guard",), ())
            if not context.frame_stable:
                return TriggerDecision(False, ("frame_unstable",), ())
            if context.consecutive_unknown < self.min_unknown_samples:
                return TriggerDecision(False, ("unknown_not_persistent",), ())
            return TriggerDecision(True, ("stable_unknown_scene",), ("scene", "dialogs"))

        requested = self._missing_fields(context)
        if requested:
            return TriggerDecision(True, ("requested_fields_missing",), requested)
        return TriggerDecision(False, (), ())

    @staticmethod
    def _missing_fields(context: SceneTriggerContext) -> Tuple[str, ...]:
        fields: list[str] = []
        for field in context.unknown_fields:
            if field.startswith("card[") and field.endswith("].owner_slot"):
                normalized = "cards.owner_slot"
            elif field.startswith("enemy[") and field.endswith("].hp_ratio"):
                normalized = "enemies.hp_ratio"
            elif field.startswith("enemy[") and field.endswith("].break_state"):
                normalized = "enemies.break_state"
            else:
                normalized = field
            if normalized not in fields:
                fields.append(normalized)
        return tuple(fields)


@dataclass(frozen=True)
class RuntimeObservation:
    context: SceneTriggerContext
    screenshot_id: str


class VisionRuntimeTracker:
    def __init__(self, *, clock=monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._previous_scene: Optional[Scene] = None
        self._last_scene: Optional[Scene] = None
        self._scene_since = self._started_at
        self._unknown_count = 0
        self._last_frame_hash: Optional[str] = None
        self._stable_count = 0
        self._last_action_at: Optional[float] = None
        self._last_action: Optional[str] = None

    def mark_action(self, action: str) -> None:
        self._last_action = action
        self._last_action_at = self._clock()
        self._stable_count = 0

    def observe(self, scene: Scene, image: bytes, *, unknown_fields: tuple[str, ...] = ()) -> RuntimeObservation:
        now = self._clock()
        frame_hash = sha256(image).hexdigest()
        if frame_hash == self._last_frame_hash:
            self._stable_count += 1
        else:
            self._stable_count = 1
            self._last_frame_hash = frame_hash

        self._previous_scene = self._last_scene
        if scene is not self._last_scene:
            self._scene_since = now
        self._last_scene = scene
        if scene is Scene.UNKNOWN:
            self._unknown_count += 1
        else:
            self._unknown_count = 0

        reference = self._last_action_at if self._last_action_at is not None else self._scene_since
        elapsed = max(0, int((now - reference) * 1000))
        context = SceneTriggerContext(
            current_scene=scene,
            previous_scene=self._previous_scene,
            elapsed_since_action_ms=elapsed,
            consecutive_unknown=self._unknown_count,
            frame_stable=self._stable_count >= 2,
            last_action=self._last_action,
            unknown_fields=unknown_fields,
        )
        return RuntimeObservation(context, frame_hash)

    def reset(self) -> None:
        self.__init__(clock=self._clock)