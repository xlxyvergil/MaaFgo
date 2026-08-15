"""严格解析多模态模型返回的视觉 JSON。"""
from __future__ import annotations
import json
import re
from typing import Any, Optional
from battle.core.enums import CardColor, Scene
from .models import VisualCard, VisualDialog, VisualEnemy, VisualObservation, VisualServant

class VisionParseError(ValueError):
    pass

def parse_observation(raw_text: str, *, evidence_id: str = "") -> VisualObservation:
    data = _load(raw_text)
    if data.get("schema_version", 1) != 1:
        raise VisionParseError("unsupported schema_version")
    return VisualObservation(
        scene=_scene(data.get("scene")),
        cards=tuple(_card(v) for v in _list(data, "cards")),
        servants=tuple(_servant(v) for v in _list(data, "servants")),
        enemies=tuple(_enemy(v) for v in _list(data, "enemies")),
        dialogs=tuple(_dialog(v) for v in _list(data, "dialogs")),
        confidence=_confidence(data.get("confidence", 0.0), "confidence"),
        unknown_fields=_unknown(data.get("unknown_fields", [])),
        evidence_id=evidence_id or str(data.get("evidence_id", "")),
    )

def _load(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisionParseError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise VisionParseError("top-level JSON must be an object")
    return value

def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise VisionParseError(f"{key} must be an array")
    return value

def _scene(value: Any) -> Optional[Scene]:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    if not isinstance(value, str):
        raise VisionParseError("scene must be a string or object")
    try:
        return Scene(value)
    except ValueError as exc:
        raise VisionParseError(f"invalid scene: {value}") from exc

def _card(value: Any) -> VisualCard:
    if not isinstance(value, dict):
        raise VisionParseError("card must be an object")
    slot = _slot(value.get("ui_slot"), 1, 5, "card.ui_slot")
    color = value.get("color")
    if color is not None:
        try:
            color = CardColor(color)
        except ValueError as exc:
            raise VisionParseError(f"invalid card color: {color}") from exc
    owner = value.get("owner_slot")
    if owner is not None:
        owner = _slot(owner, 1, 3, "card.owner_slot")
    return VisualCard(slot, color, owner, _bool(value.get("is_np", False), "card.is_np"), _confidence(value.get("confidence", 0.0), "card.confidence"))

def _servant(value: Any) -> VisualServant:
    if not isinstance(value, dict):
        raise VisionParseError("servant must be an object")
    slot = _slot(value.get("slot"), 1, 3, "servant.slot")
    skills = value.get("skill_available", [None, None, None])
    if not isinstance(skills, list) or len(skills) != 3:
        raise VisionParseError("servant.skill_available must contain 3 values")
    np_percent = value.get("np_percent")
    if np_percent is not None and (not isinstance(np_percent, int) or not 0 <= np_percent <= 300):
        raise VisionParseError("servant.np_percent must be in [0, 300]")
    return VisualServant(slot, _optional_bool(value.get("present"), "servant.present"), tuple(_optional_bool(v, "servant.skill_available") for v in skills), np_percent, _confidence(value.get("confidence", 0.0), "servant.confidence"))

def _enemy(value: Any) -> VisualEnemy:
    if not isinstance(value, dict):
        raise VisionParseError("enemy must be an object")
    slot = _slot(value.get("slot"), 1, 3, "enemy.slot")
    hp = value.get("hp_ratio")
    if hp is not None and (not isinstance(hp, (int, float)) or not 0 <= hp <= 1):
        raise VisionParseError("enemy.hp_ratio must be in [0, 1]")
    return VisualEnemy(slot, _optional_bool(value.get("alive"), "enemy.alive"), _optional_bool(value.get("targeted"), "enemy.targeted"), hp, _optional_string(value.get("break_state"), "enemy.break_state"), _confidence(value.get("confidence", 0.0), "enemy.confidence"))

def _dialog(value: Any) -> VisualDialog:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str) or not value["kind"]:
        raise VisionParseError("dialog.kind must be a non-empty string")
    return VisualDialog(value["kind"], _optional_string(value.get("text"), "dialog.text"), _bool(value.get("blocking", False), "dialog.blocking"), _confidence(value.get("confidence", 0.0), "dialog.confidence"))

def _slot(value: Any, low: int, high: int, name: str) -> int:
    if not isinstance(value, int) or not low <= value <= high:
        raise VisionParseError(f"{name} must be an integer in [{low}, {high}]")
    return value

def _confidence(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise VisionParseError(f"{name} must be in [0, 1]")
    return float(value)

def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise VisionParseError(f"{name} must be boolean")
    return value

def _optional_bool(value: Any, name: str) -> Optional[bool]:
    return None if value is None else _bool(value, name)

def _optional_string(value: Any, name: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise VisionParseError(f"{name} must be string or null")
    return value

def _unknown(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise VisionParseError("unknown_fields must be an array of strings")
    return tuple(value)
