"""Read a retained bond-results frame through Pipeline recognition nodes.

Coordinates, templates and scores belong to ``羁绊结算识别.json``. This module
neither captures a new frame nor clicks, waits, or infers identity from MAX.
Result columns exclude support servants and use the original formation order.
"""
from __future__ import annotations

from collections.abc import Mapping


BOND_PAGE_NODE = "羁绊结算-页面"
BOND_MAX_NODE = "羁绊结算-第{column}栏MAX"
BOND_ANY_MAX_NODE = "羁绊结算-任意MAX"
BOND_UPGRADE_NODE = "羁绊结算-升级展示"
SUPPORTED_RESULT_COLUMNS = (5,)


def _hit(context, node, image):
    if image is None:
        raise ValueError("bond_result_image_missing")
    try:
        detail = context.run_recognition(node, image)
    except Exception as exc:
        raise ValueError(f"bond_result_recognition_failed:{node}") from exc
    # Maa returns a detail with hit=False for a real miss. None means the
    # recognition did not start (e.g. missing node/template or empty image).
    if detail is None or not isinstance(getattr(detail, "hit", None), bool):
        raise ValueError(f"bond_result_recognition_unavailable:{node}")
    return detail.hit


def read_bond_page(context, image) -> bool:
    """Whether this same frame shows the bond-results page.

    A genuine recognition miss is False; unavailable recognition raises.
    """
    return _hit(context, BOND_PAGE_NODE, image)


def read_bond_upgrade(context, image) -> bool:
    """Whether the bond-level-up presentation currently covers the results.

    The Pipeline matches the complete extracted explanatory text on the right,
    separately from the normal bond-page text in the upper-left corner.
    This confirms only a presentation, never MAX, the current cap, or a member.
    """
    return _hit(context, BOND_UPGRADE_NODE, image)


def read_any_bond_max(context, image) -> bool:
    """Whether any MAX is visible in the bond row, without mapping a member.

    The whole-row node spans the screen width so all mode does not need to
    assume five columns or a particular support position. It cannot attribute
    the match to a formation slot; callers must not present one as confirmed.
    """
    if not read_bond_page(context, image):
        raise ValueError("bond_result_page_not_visible")
    return _hit(context, BOND_ANY_MAX_NODE, image)


def read_bond_max(context, image, columns=5) -> tuple[int, ...]:
    """Return all MAX result columns, 1-based, in a known page/layout.

    The temporary BBC calibration covers the five-own-servant layout only.
    Missing/short/no-support layouts must not be mistaken for a negative.
    """
    if type(columns) is not int or columns not in SUPPORTED_RESULT_COLUMNS:
        raise ValueError(f"bond_result_layout_not_calibrated:{columns}")
    if not read_bond_page(context, image):
        raise ValueError("bond_result_page_not_visible")
    return tuple(column for column in range(1, columns + 1)
                 if _hit(context, BOND_MAX_NODE.format(column=column), image))


def _field(slot, name, default=None):
    return slot.get(name, default) if isinstance(slot, Mapping) else getattr(slot, name, default)


def result_formation_order(slots) -> tuple[int, ...]:
    """Map result-column order to original formation slots without names.

    ``unknown`` is an unidentified occupied slot, not an empty slot. Support
    flags must still be definite, and frame disagreement cannot prove occupancy.
    This pure mapping does not certify that a short layout has calibrated ROIs;
    pass its length to ``read_bond_max`` before reading such a layout.
    """
    try:
        slots = tuple(slots)
    except TypeError as exc:
        raise ValueError("bond_result_formation_missing") from exc
    if (len(slots) != 6
            or any(type(_field(slot, "slot")) is not int for slot in slots)
            or tuple(_field(slot, "slot") for slot in slots) != (1, 2, 3, 4, 5, 6)):
        raise ValueError("bond_result_formation_incomplete")
    own_slots = []
    supports = 0
    for slot in slots:
        number, status = _field(slot, "slot"), _field(slot, "status")
        support = _field(slot, "is_support")
        if status not in {"empty", "identified", "unknown"}:
            raise ValueError(f"bond_result_occupancy_unknown:{number}")
        if type(support) is not bool:
            raise ValueError(f"bond_result_support_unknown:{number}")
        if _field(slot, "reason") == "formation_frame_disagreement":
            raise ValueError(f"bond_result_occupancy_unknown:{number}")
        if status == "empty":
            if support:
                raise ValueError(f"bond_result_empty_support:{number}")
            continue
        if support:
            supports += 1
        else:
            own_slots.append(number)
    if supports > 1:
        raise ValueError("bond_result_multiple_supports")
    if not own_slots:
        raise ValueError("bond_result_no_own_servants")
    if len(own_slots) > max(SUPPORTED_RESULT_COLUMNS):
        raise ValueError("bond_result_layout_not_calibrated:6")
    return tuple(own_slots)


def result_slot_for_formation(slots, target_slot) -> int:
    """Resolve an original formation slot to its non-support result column.

    A selected empty/support slot is an error, never another servant's column.
    """
    if type(target_slot) is not int or not 1 <= target_slot <= 6:
        raise ValueError("bond_result_target_slot_invalid")
    order = result_formation_order(slots)
    try:
        return order.index(target_slot) + 1
    except ValueError as exc:
        raise ValueError(f"bond_result_target_not_own_servant:{target_slot}") from exc
