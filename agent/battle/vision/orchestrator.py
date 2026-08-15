"""Vision 调用编排：触发、限流、作用域过滤与 BattleState 补丁。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Optional

from battle.core.models import BattleState, Confidence, EnemyState, ServantState, SkillState

from .config import VisionConfig
from .models import VisionRequest, VisionResponse, VisualObservation
from .provider import VisionProvider
from .trigger import SceneTriggerContext, TriggerDecision, VisionTrigger


@dataclass(frozen=True)
class VisionCallResult:
    response: Optional[VisionResponse]
    skipped: bool = False
    reason: str = ""


@dataclass(frozen=True)
class VisionAnalysisResult:
    decision: TriggerDecision
    call: VisionCallResult
    effective_state: Optional[BattleState] = None
    conflicts: tuple[str, ...] = ()


class VisionOrchestrator:
    def __init__(
        self,
        provider: VisionProvider,
        config: VisionConfig | None = None,
        trigger: VisionTrigger | None = None,
    ) -> None:
        self.provider = provider
        self.config = config or VisionConfig()
        self.trigger = trigger or VisionTrigger()
        self._turn_index: Optional[int] = None
        self._calls = 0
        self._evidence_keys: set[str] = set()

    def analyze_state_if_needed(
        self,
        image: bytes,
        state: BattleState,
        context: SceneTriggerContext,
        *,
        image_format: str = "png",
        evidence_id: str = "",
        recent_actions: tuple[str, ...] = (),
        turn_index: int = 0,
    ) -> VisionAnalysisResult:
        actions = recent_actions or ((context.last_action,) if context.last_action else ())
        request = VisionRequest(
            image=image,
            image_format=image_format,
            state=state,
            turn_index=turn_index,
            recent_actions=actions,
            evidence_id=evidence_id or state.screenshot_id,
        )
        return self.analyze_if_needed(request, context)

    def analyze_if_needed(
        self,
        request: VisionRequest,
        context: SceneTriggerContext,
    ) -> VisionAnalysisResult:
        decision = self.trigger.evaluate(context)
        if not decision.should_call:
            reason = decision.reasons[0] if decision.reasons else "not_needed"
            return VisionAnalysisResult(decision, VisionCallResult(None, skipped=True, reason=reason))

        requested_fields = request.requested_fields or decision.requested_fields
        if request.requested_fields != requested_fields:
            request = replace(request, requested_fields=requested_fields)
        if not request.recent_actions and context.last_action:
            request = replace(request, recent_actions=(context.last_action,))

        call = self._call(request)
        if call.response is None or call.response.observation is None or request.state is None:
            return VisionAnalysisResult(decision, call)

        effective_state, conflicts = apply_visual_patch(
            request.state,
            call.response.observation,
            requested_fields=requested_fields,
            min_confidence=self.config.min_confidence,
        )
        return VisionAnalysisResult(decision, call, effective_state, conflicts)

    def _call(self, request: VisionRequest) -> VisionCallResult:
        self._reset_turn(request.turn_index)
        if not self.config.enabled:
            return VisionCallResult(None, skipped=True, reason="disabled")
        key = request.evidence_id or sha256(request.image).hexdigest()
        if key in self._evidence_keys:
            return VisionCallResult(None, skipped=True, reason="duplicate_evidence")
        if self._calls >= self.config.max_calls_per_turn:
            return VisionCallResult(None, skipped=True, reason="max_calls_per_turn")

        self._evidence_keys.add(key)
        self._calls += 1
        try:
            return VisionCallResult(self.provider.analyze(request), reason="called")
        except Exception as exc:  # Provider 边界必须 fail-safe。
            return VisionCallResult(
                VisionResponse("", None, error=str(exc)),
                reason="provider_exception",
            )

    def _reset_turn(self, turn_index: int) -> None:
        if self._turn_index != turn_index:
            self._turn_index = turn_index
            self._calls = 0
            self._evidence_keys.clear()


def encode_png(image) -> bytes:
    import cv2

    if image is None:
        raise ValueError("vision image is required")
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("failed to encode vision image as PNG")
    return encoded.tobytes()


def apply_visual_patch(
    state: BattleState,
    observation: VisualObservation,
    *,
    requested_fields: tuple[str, ...],
    min_confidence: float = 0.8,
) -> tuple[BattleState, tuple[str, ...]]:
    """只把明确请求且达到门槛的字段应用到现有 BattleState。"""
    requested = set(requested_fields)
    conflicts: list[str] = []
    resolved: set[str] = set()

    scene = state.scene
    scene_confidence = state.scene_confidence
    if "scene" in requested and observation.scene is not None and observation.confidence >= min_confidence:
        if state.scene_confidence.value >= min_confidence and state.scene is not observation.scene:
            conflicts.append("scene")
        scene = observation.scene
        scene_confidence = Confidence(observation.confidence, "vision")
        resolved.add("scene")

    visual_cards = {card.ui_slot: card for card in observation.cards}
    cards = []
    for card in state.cards:
        visual = visual_cards.get(card.ui_slot)
        if visual is None or visual.confidence < min_confidence:
            cards.append(card)
            continue
        prefix = f"card[{card.ui_slot}]"
        color = card.color
        owner_slot = card.owner_slot
        confidence = card.confidence
        wants_color = _wants(requested, "cards.color", prefix, f"{prefix}.color")
        wants_owner = _wants(requested, "cards.owner_slot", f"{prefix}.owner_slot")
        if wants_color and visual.color is not None:
            if card.confidence.value >= min_confidence and card.color is not visual.color:
                conflicts.append(f"{prefix}.color")
            color = visual.color
            confidence = Confidence(visual.confidence, "vision")
            resolved.update((prefix, f"{prefix}.color"))
        if wants_owner and visual.owner_slot is not None:
            if card.owner_slot is not None and card.owner_slot != visual.owner_slot:
                conflicts.append(f"{prefix}.owner_slot")
            owner_slot = visual.owner_slot
            resolved.add(f"{prefix}.owner_slot")
        cards.append(replace(card, color=color, owner_slot=owner_slot, confidence=confidence))

    visual_servants = {servant.slot: servant for servant in observation.servants}
    servants = tuple(
        _patch_servant(servant, visual_servants.get(servant.slot), requested, min_confidence, conflicts, resolved)
        for servant in state.servants
    )
    visual_enemies = {enemy.slot: enemy for enemy in observation.enemies}
    enemies = tuple(
        _patch_enemy(enemy, visual_enemies.get(enemy.slot), requested, min_confidence, conflicts, resolved)
        for enemy in state.enemies
    )

    unknown_fields = tuple(field for field in state.unknown_fields if field not in resolved)
    patched = replace(
        state,
        scene=scene,
        scene_confidence=scene_confidence,
        cards=tuple(cards),
        servants=servants,
        enemies=enemies,
        unknown_fields=unknown_fields,
    )
    return patched, tuple(conflicts)


def _patch_servant(state: ServantState, visual, requested, threshold, conflicts, resolved) -> ServantState:
    if visual is None or visual.confidence < threshold:
        return state
    skills = list(state.skills)
    changed = False
    for index, value in enumerate(visual.skill_available, start=1):
        field = f"servant[{state.slot}].skill[{index}].available"
        if value is None or not _wants(requested, "servants.skill_available", field):
            continue
        old = skills[index - 1]
        if old.available is not None and old.available != value and old.confidence.value >= threshold:
            conflicts.append(field)
        skills[index - 1] = SkillState(value, Confidence(visual.confidence, "vision"))
        resolved.add(field)
        changed = True
    return replace(state, skills=tuple(skills), confidence=Confidence(visual.confidence, "vision")) if changed else state


def _patch_enemy(state: EnemyState, visual, requested, threshold, conflicts, resolved) -> EnemyState:
    if visual is None or visual.confidence < threshold:
        return state
    prefix = f"enemy[{state.slot}]"
    alive = state.alive
    targeted = state.targeted
    changed = False
    if visual.alive is not None and _wants(requested, "enemies.alive", f"{prefix}.alive"):
        if state.alive != visual.alive and state.confidence.value >= threshold:
            conflicts.append(f"{prefix}.alive")
        alive = visual.alive
        resolved.add(f"{prefix}.alive")
        changed = True
    if visual.targeted is not None and _wants(requested, "enemies.targeted", f"{prefix}.targeted"):
        if state.targeted != visual.targeted and state.confidence.value >= threshold:
            conflicts.append(f"{prefix}.targeted")
        targeted = visual.targeted
        resolved.add(f"{prefix}.targeted")
        changed = True
    return replace(state, alive=alive, targeted=targeted, confidence=Confidence(visual.confidence, "vision")) if changed else state


def _wants(requested: set[str], *names: str) -> bool:
    return any(name in requested for name in names)