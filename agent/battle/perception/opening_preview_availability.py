"""Confirm that the opening-hand target's HUD still shows a clear status row."""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from ..core.models import is_slot
from ..data.servant_identity import servant_template_ids
from .card_owner import _normalize_image, _read_rgba
from .frontline import _status_roots


_PORTRAIT_X = (16, 333, 651)
_PORTRAIT_Y = 364
_PORTRAIT_SIZES = (291, 292, 293, 294, 295)
_ANCHOR_X = 79
_ANCHOR_Y = 55
_ANCHOR_HEIGHT = 89
_POSITION_TOLERANCE = 2
_FIRST_ICON_X = (32, 350, 668)
_ICON_INTERVAL = 26
_ICON_COLUMNS = 10
_BOTTOM_ROW_Y = 518
_TOP_ROW_Y = 492


@lru_cache(maxsize=16)
def _portraits(roots, servant_id):
    variants = []
    for directory in map(Path, roots):
        for identity in servant_template_ids(servant_id):
            for path in sorted((directory / identity).glob("*.png")):
                source = _read_rgba(path)
                if source is None or source.shape != (256, 256, 4):
                    continue
                for size in _PORTRAIT_SIZES:
                    portrait = cv2.resize(source, (size, size), interpolation=cv2.INTER_LINEAR)
                    face = portrait[_ANCHOR_Y:_ANCHOR_Y + _ANCHOR_HEIGHT, _ANCHOR_X:-60]
                    mask = np.uint8(face[:, :, 3] > 247) * 255
                    if cv2.countNonZero(mask) >= 100:
                        variants.append((source, size, np.ascontiguousarray(face[:, :, :3]), mask))
    return tuple(variants)


def _find_portrait(frame, slot, servant_id):
    """Align against the face, independently of what occupies the status cells."""
    pad = _POSITION_TOLERANCE
    x0 = _PORTRAIT_X[slot - 1] + _ANCHOR_X - pad
    y0 = _PORTRAIT_Y + _ANCHOR_Y - pad
    best = None
    best_score = 0.90
    for source, size, face, mask in _portraits(_status_roots(), servant_id):
        height, width = face.shape[:2]
        area = frame[y0:y0 + height + 2 * pad, x0:x0 + width + 2 * pad]
        scores = cv2.matchTemplate(area, face, cv2.TM_CCOEFF_NORMED, mask=mask)
        scores = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, score, _, (left, top) = cv2.minMaxLoc(scores)
        if score > best_score:
            best_score = score
            best = source, size, x0 + left - _ANCHOR_X, y0 + top - _ANCHOR_Y
    if best is None:
        return None
    source, size, x, y = best
    return cv2.resize(source, (size, size), interpolation=cv2.INTER_LINEAR), x, y


def _icon_band_error(frame, portrait, origin_x, origin_y, x, y):
    # The top edge of the first status icon crosses this band. Its full cell
    # can extend further into transparent parts of the portrait.
    width, height = 19, 8
    offset_x, offset_y = x - origin_x, y - origin_y
    source = portrait[offset_y:offset_y + height, offset_x:offset_x + width]
    opaque = source[:, :, 3] > 247
    if source.shape[:2] != (height, width) or np.count_nonzero(opaque) < 0.9 * width * height:
        return None
    actual = frame[y:y + height, x:x + width, :3]
    error = np.linalg.norm(source[:, :, :3].astype(np.float32) - actual, axis=-1)
    return float(np.median(error[opaque])), float(np.percentile(error[opaque], 95))


def preview_target_has_clear_status(image, slot: int, servant_id: str) -> bool:
    """Require intact opaque portrait pixels where the first HUD icon would overlay.

    Use only for the already-identified target on a confirmed battle main frame.
    Status icons start in the leftmost bottom cell before filling further
    columns and the overflow row. Transparent art, uncertain alignment, and
    an unexpected overlay all leave availability unconfirmed.
    """
    frame = _normalize_image(image)
    if (frame is None or frame.shape[:2] != (720, 1280)
            or not is_slot(slot, 1, 3) or not servant_id):
        return False
    match = _find_portrait(frame, slot, servant_id)
    if match is None:
        return False

    x = _FIRST_ICON_X[slot - 1] + 4
    first = _icon_band_error(frame, *match, x, _BOTTOM_ROW_Y)
    if first is None or first[0] > 15 or first[1] > 35:
        return False

    # A conflicting covered cell is also grounds for falling back. Ignore
    # transparent pixels that are part of the animated battlefield.
    for y, start in ((_BOTTOM_ROW_Y, 1), (_TOP_ROW_Y, 0)):
        for column in range(start, _ICON_COLUMNS):
            error = _icon_band_error(frame, *match, x + _ICON_INTERVAL * column, y)
            if error is not None and error[0] > 38:
                return False
    return True
