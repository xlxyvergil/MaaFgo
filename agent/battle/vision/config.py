"""多模态视觉服务配置。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping

@dataclass(frozen=True)
class VisionConfig:
    enabled: bool = False
    provider: str = "openai_compatible"
    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    timeout_s: float = 30.0
    max_calls_per_turn: int = 2
    min_confidence: float = 0.8

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "VisionConfig":
        if not isinstance(value, Mapping):
            return cls()
        return cls(
            enabled=_bool(value.get("enabled", False), "enabled"),
            provider=_string(value.get("provider", "openai_compatible"), "provider"),
            endpoint=_string(value.get("endpoint", ""), "endpoint"),
            api_key=_string(value.get("api_key", ""), "api_key"),
            model=_string(value.get("model", ""), "model"),
            timeout_s=_number(value.get("timeout_s", 30.0), "timeout_s", 0.1),
            max_calls_per_turn=_integer(value.get("max_calls_per_turn", 2), "max_calls_per_turn", 0),
            min_confidence=_number(value.get("min_confidence", 0.8), "min_confidence", 0.0, 1.0),
        )

def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"vision.{name} must be boolean")
    return value

def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"vision.{name} must be string")
    return value

def _number(value: Any, name: str, low: float, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < low or (high is not None and value > high):
        raise ValueError(f"vision.{name} is out of range")
    return float(value)

def _integer(value: Any, name: str, low: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise ValueError(f"vision.{name} must be an integer >= {low}")
    return value
