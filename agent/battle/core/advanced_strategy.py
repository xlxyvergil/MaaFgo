"""Shared, device-independent contract for global native battle strategies."""
from __future__ import annotations

import copy
import math
from typing import Any

ADVANCED_STRATEGY_FIELD = "maaAdvancedStrategy"
PRESET_NAMES = ("balanced", "mighty", "brave", "np", "legacy")
_DEFAULT = {
    "preset": "balanced",
    "openingHand": {"enabled": False, "servantId": None, "servantSlot": None,
                    "requiredColors": ["B", "Q"], "maxWaitTurns": 2},
    "nativeStrategyOverrides": {}, "autoServantSkills": True, "maxTurns": 40,
}


def default_advanced_strategy() -> dict:
    return copy.deepcopy(_DEFAULT)


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def validate_advanced_strategy(value: Any, path: str = ADVANCED_STRATEGY_FIELD) -> list[dict]:
    """Validate known fields without modifying or discarding future extensions."""
    issues: list[dict] = []

    def error(at, message):
        issues.append({"severity": "error", "path": at,
                       "code": "invalid_advanced_strategy", "message": message})

    def integer(raw, at, low, high, nullable=False):
        if nullable and raw is None:
            return
        if type(raw) is not int or not low <= raw <= high:
            error(at, f"需要 {low}–{high} 的整数")

    def boolean(raw, at):
        if type(raw) is not bool:
            error(at, "需要布尔值")

    def identity(raw, at, nullable=True):
        if nullable and raw is None:
            return
        valid = type(raw) is int and 0 < raw < 10**12
        if isinstance(raw, str):
            text = raw.strip()
            valid = (0 < len(text) <= 12 and text.isascii() and text.isdigit()
                     and int(text) > 0)
        if not valid:
            error(at, "从者 ID 必须是正整数或数字字符串")

    def colors(raw, at):
        if (not isinstance(raw, list) or not 1 <= len(raw) <= 3
                or any(type(c) is not str or c not in {"B", "A", "Q"} for c in raw)):
            error(at, "需要 1–3 张 B/A/Q 牌色列表")

    def opening(raw, at, camel):
        if not isinstance(raw, dict):
            error(at, "开局等待设置必须是对象")
            return
        names = (("servantId", "servantSlot", "requiredColors", "maxWaitTurns") if camel
                 else ("servant_id", "servant_slot", "required_colors", "max_wait_turns"))
        sid, slot, required, wait = names
        if "enabled" in raw:
            boolean(raw["enabled"], at + ".enabled")
        if sid in raw:
            identity(raw[sid], at + "." + sid)
        if slot in raw:
            integer(raw[slot], at + "." + slot, 1, 3, nullable=True)
        if required in raw:
            colors(raw[required], at + "." + required)
        if wait in raw:
            integer(raw[wait], at + "." + wait, 0, 5)

    def weight(raw, at):
        if (type(raw) not in (int, float) or not 0 <= raw <= 100
                or not math.isfinite(raw)):
            error(at, "权重必须是 0–100 的有限数值")

    def native(raw, at):
        if not isinstance(raw, dict):
            error(at, "补牌策略微调必须是 JSON 对象")
            return
        for key in ("enabled", "prefer_brave_chain", "avoid_np_color_before_np"):
            if key in raw:
                boolean(raw[key], at + "." + key)
        for key in ("brave_chain_weight", "mighty_chain_weight", "same_color_chain_weight",
                    "servant_priority_weight", "np_color_preservation_weight"):
            if key in raw:
                weight(raw[key], at + "." + key)
        for key in ("preferred_servant_ids", "preferred_slots"):
            if key not in raw:
                continue
            values = raw[key]
            if not isinstance(values, list):
                error(at + "." + key, "需要列表")
                continue
            for index, item in enumerate(values):
                location = f"{at}.{key}[{index}]"
                if key == "preferred_servant_ids":
                    identity(item, location, nullable=False)
                else:
                    integer(item, location, 1, 3)
        for key in ("color_weights", "np_colors_by_servant_id", "np_colors_by_slot"):
            if key not in raw:
                continue
            mapping = raw[key]
            if not isinstance(mapping, dict):
                error(at + "." + key, "需要对象")
                continue
            for map_key, item in mapping.items():
                location = f"{at}.{key}.{map_key}"
                if key == "color_weights":
                    if map_key not in {"B", "A", "Q"}:
                        error(location, "牌色仅支持 B/A/Q")
                    weight(item, location)
                else:
                    if key == "np_colors_by_servant_id":
                        identity(map_key, location, nullable=False)
                    elif str(map_key) not in {"1", "2", "3"}:
                        error(location, "宝具槽位需要 1–3")
                    if type(item) is not str or item not in {"B", "A", "Q"}:
                        error(location, "宝具牌色仅支持 B/A/Q")
        if "opening_hand" in raw:
            opening(raw["opening_hand"], at + ".opening_hand", False)

    if not isinstance(value, dict):
        error(path, "全局高级策略必须是 JSON 对象")
        return issues
    if "preset" in value and (type(value["preset"]) is not str or value["preset"] not in PRESET_NAMES):
        error(path + ".preset", "未知的补牌预设")
    if "openingHand" in value:
        opening(value["openingHand"], path + ".openingHand", True)
    if "nativeStrategyOverrides" in value:
        native(value["nativeStrategyOverrides"], path + ".nativeStrategyOverrides")
    if "autoServantSkills" in value:
        boolean(value["autoServantSkills"], path + ".autoServantSkills")
    if "maxTurns" in value:
        integer(value["maxTurns"], path + ".maxTurns", 1, 100)
    # Independent switches win over JSON; an empty dedicated identity must not
    # discard an explicitly configured identity in the override object.
    if not issues:
        overrides = value.get("nativeStrategyOverrides", {})
        opening_values = overrides.get("opening_hand", {})
        explicit = value.get("openingHand", {})
        enabled = explicit.get("enabled", False)
        if enabled and not (explicit.get("servantId") or explicit.get("servantSlot")
                            or opening_values.get("servant_id") or opening_values.get("servant_slot")):
            error(path + ".openingHand", "开启开局等待时必须指定目标从者或当前前排槽位")
    return issues


def normalize_advanced_strategy(value: dict | None = None) -> dict:
    if value is None:
        return default_advanced_strategy()
    issues = validate_advanced_strategy(value)
    if issues:
        raise ValueError("；".join(f"{item['path']}: {item['message']}" for item in issues))
    return _merge(_DEFAULT, value)


def advanced_strategy_to_param(value: dict | None = None) -> dict:
    """Convert the portable document extension into whitelisted runtime fields."""
    config = normalize_advanced_strategy(value)
    preset = config["preset"]
    brave, mighty, same, owner = {
        "balanced": (8, 12, 5, 4), "mighty": (4, 32, 2, 4),
        "brave": (32, 8, 4, 4), "np": (6, 10, 12, 4),
        "legacy": (8, 12, 5, 4),
    }[preset]
    native = {"enabled": preset != "legacy", "prefer_brave_chain": True,
              "brave_chain_weight": brave, "mighty_chain_weight": mighty,
              "same_color_chain_weight": same, "servant_priority_weight": owner,
              "preferred_servant_ids": [], "preferred_slots": [],
              "np_colors_by_servant_id": {}, "np_colors_by_slot": {},
              "avoid_np_color_before_np": False,
              "color_weights": {"B": 1, "A": 4, "Q": 2} if preset == "np"
              else {"B": 3, "A": 2, "Q": 1}}
    native = _merge(native, config["nativeStrategyOverrides"])
    opening = native.get("opening_hand", {}).copy()
    dedicated = config["openingHand"]
    opening.update(enabled=dedicated["enabled"], max_wait_turns=dedicated["maxWaitTurns"])
    for external, internal in (("servantId", "servant_id"), ("servantSlot", "servant_slot")):
        if dedicated.get(external) is not None:
            opening[internal] = dedicated[external]
    # A default, unedited color field does not replace a targeted override.
    explicit = value.get("openingHand", {}) if isinstance(value, dict) else {}
    if "requiredColors" in explicit or "required_colors" not in opening:
        opening["required_colors"] = dedicated["requiredColors"]
    native["opening_hand"] = opening
    return {"max_turns": config["maxTurns"],
            "skill_policy": {"auto_servant_skills": config["autoServantSkills"],
                             "use_master_skills": False},
            "card_policy": {"goal": "build_np" if preset == "np" else "finish_wave",
                            "color_priority": ["A", "Q", "B"] if preset == "np" else ["B", "A", "Q"],
                            "np_first": True, "prefer_mighty_chain": True,
                            "native_strategy": native}}
