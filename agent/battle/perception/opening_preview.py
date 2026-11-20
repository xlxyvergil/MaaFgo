"""Read the five face-card thumbnails shown above Attack on the main battle UI."""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from ..core.enums import CardColor
from ..data.servant_identity import servant_template_ids
from .card_owner import (
    BASE_HEIGHT,
    BASE_WIDTH,
    _load_servant_templates,
    _normalize_image,
    _read_rgba,
    default_command_card_roots,
)


# The five visible cards follow the fan from its lower left to upper right.
# CN samples expose a strip of each color's background along the left edge.
_CN_COLOR_ROIS = ((962, 558, 8, 5), (1001, 491, 10, 10), (1058, 457, 10, 10),
                  (1122, 437, 10, 10), (1200, 432, 10, 10))
# JP's longer text and modifiers cover the background strips. Match its
# localized artwork at the five independently rotated label positions.
_JP_LABEL_POSES = ((1037, 556, 64), (1065, 515, 42), (1105, 495, 19),
                   (1151, 493, -8), (1203, 509, -29))
_JP_LABEL_SEARCH = 7
_LABEL_ASSETS = ((CardColor.BUSTER, "buster"), (CardColor.ARTS, "arts"),
                 (CardColor.QUICK, "quick"))
_CARD_TOP_EDGES = (((947.2, 559.4), (978, 491)), ((982.8, 481), (1038, 434)),
                   ((1042.4, 431), (1111, 409)), ((1122.8, 409), (1197, 417)),
                   ((1206, 419.8), (1271, 454)))
_OWNER_MIN_SCORE = 0.85
_OWNER_MIN_MARGIN = 0.10
_SUPPORT_MIN_SCORE = 0.66
_MAX_OWNER_ASSETS = 64


@dataclass(frozen=True)
class PreviewCard:
    ui_slot: int
    color: CardColor | None
    color_score: float
    color_margin: float
    region: str | None
    owner_slot: int | None = None
    servant_id: str | None = None
    owner_score: float = 0.0
    owner_margin: float = 0.0
    owner_template: str = ""
    owner_reason: str = ""
    support_score: float | None = None


@dataclass(frozen=True)
class _ColorMatch:
    color: CardColor | None
    score: float
    margin: float


@dataclass(frozen=True)
class _MaskedTemplate:
    pixels: np.ndarray
    mask: np.ndarray


def _rank_colors(scores, threshold, min_margin) -> _ColorMatch:
    (color, best), (_, runner_up), *_ = sorted(scores.items(), key=lambda item: -item[1])
    margin = best - runner_up
    return _ColorMatch(color if best >= threshold and margin >= min_margin else None, best, margin)


def _cn_colors(frame: np.ndarray) -> tuple[_ColorMatch, ...]:
    matches = []
    for x, y, width, height in _CN_COLOR_ROIS:
        patch = frame[y:y + height, x:x + width]
        hue, saturation, value = cv2.split(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV))
        vivid = (saturation >= 120) & (value >= 55)
        scores = {
            CardColor.BUSTER: float(np.mean(vivid & ((hue <= 9) | (hue >= 170)))),
            CardColor.ARTS: float(np.mean(vivid & (hue >= 97) & (hue <= 135))),
            CardColor.QUICK: float(np.mean(vivid & (hue >= 43) & (hue <= 83))),
        }
        matches.append(_rank_colors(scores, 0.75, 0.35))
    return tuple(matches)


@lru_cache(maxsize=4)
def _jp_labels(roots: tuple[str, ...]) -> tuple:
    labels = {}
    for color, name in _LABEL_ASSETS:
        relative = f"_composition/shared/card_txt_{name}.png"
        for root in roots:
            label = _read_rgba(Path(root) / relative)
            if label is not None and label.shape == (73, 150, 4):
                labels[color] = label
                break
    if len(labels) != len(_LABEL_ASSETS):
        return ()

    slots = []
    for center_x, center_y, angle in _JP_LABEL_POSES:
        slot = []
        for color, label in labels.items():
            for scale in (0.60, 0.625):
                matrix = cv2.getRotationMatrix2D((75, 36.5), angle, scale)
                matrix[:, 2] += (60 - 75, 60 - 36.5)
                rotated = cv2.warpAffine(label, matrix, (120, 120))
                mask = np.uint8(rotated[:, :, 3] > 230) * 255
                x, y, w, h = cv2.boundingRect(mask)
                if not w or not h:
                    return ()
                pixels = np.ascontiguousarray(rotated[y:y + h, x:x + w, :3])
                mask = mask[y:y + h, x:x + w].copy()
                patch_x = center_x - 60 + x - _JP_LABEL_SEARCH
                patch_y = center_y - 60 + y - _JP_LABEL_SEARCH
                roi = (patch_x, patch_y, w + 2 * _JP_LABEL_SEARCH, h + 2 * _JP_LABEL_SEARCH)
                slot.append((color, _MaskedTemplate(pixels, mask), roi))
        slots.append(tuple(slot))
    return tuple(slots)


def _jp_colors(frame: np.ndarray, roots: tuple[str, ...]) -> tuple[_ColorMatch, ...]:
    matches = []
    for slot in _jp_labels(roots):
        scores = dict.fromkeys((color for color, _ in _LABEL_ASSETS), 0.0)
        for color, template, (x, y, width, height) in slot:
            region = frame[y:y + height, x:x + width]
            response = cv2.matchTemplate(
                region, template.pixels, cv2.TM_SQDIFF_NORMED, mask=template.mask,
            )
            minimum = float(np.min(np.where(np.isfinite(response), response, np.inf)))
            scores[color] = max(scores[color], 1.0 - minimum)
        matches.append(_rank_colors(scores, 0.46, 0.10))
    return tuple(matches)


def _card_transform(edge):
    left, right = np.asarray(edge, dtype=np.float32)
    direction = (right - left) / 75
    down = np.asarray((-direction[1], direction[0]), dtype=np.float32)
    source = np.stack((left, right, left + 50 * down))
    target = np.asarray(((15, 10), (90, 10), (15, 60)), dtype=np.float32)
    return cv2.getAffineTransform(source, target)


_CARD_TRANSFORMS = tuple(_card_transform(edge) for edge in _CARD_TOP_EDGES)


@lru_cache(maxsize=32)
def _owner_templates(roots: tuple[str, ...], servant_id: str):
    artwork = _load_servant_templates(roots, servant_id)
    if len(artwork) > _MAX_OWNER_ASSETS:
        return ()
    prepared = []
    for template in artwork:
        # The lower label obscures the torso. Upper buffs and the adjacent
        # card obscure the outside of the portrait on the smaller thumbnails.
        face = template.bgr[10:68, 66:150]
        mask = template.mask[10:68, 66:150]
        x, y, w, h = cv2.boundingRect(mask)
        if w == 0 or h == 0:
            continue
        for scale in (0.445, 0.47, 0.495):
            size = (round(w * scale), round(h * scale))
            if min(size) < 4:
                continue
            pixels = cv2.resize(face[y:y + h, x:x + w], size, interpolation=cv2.INTER_AREA)
            alpha = cv2.resize(mask[y:y + h, x:x + w], size, interpolation=cv2.INTER_AREA)
            opaque = np.uint8(alpha > 215) * 255
            if cv2.countNonZero(opaque) >= 40:
                prepared.append((template.relative_path, _MaskedTemplate(pixels, opaque)))
    return tuple(prepared)


def _owner_score(region: np.ndarray, templates) -> tuple[float, str]:
    best_score, best_name = 0.0, ""
    for name, template in templates:
        height, width = template.mask.shape
        if height > region.shape[0] or width > region.shape[1]:
            continue
        response = cv2.matchTemplate(
            region, template.pixels, cv2.TM_CCOEFF_NORMED, mask=template.mask,
        )
        score = float(np.max(np.where(np.isfinite(response), response, -1.0)))
        if score > best_score:
            best_score, best_name = min(score, 1.0), name
    return best_score, best_name


def _badge_pixels(image: np.ndarray) -> np.ndarray:
    blue, green, red = np.moveaxis(image.astype(np.int16), -1, 0)
    white = (np.minimum(np.minimum(blue, green), red) > 190)
    white &= (np.abs(red - green) < 37) & (np.abs(red - blue) < 37)
    red_text = (red > 120) & (red - green > 26) & (red - blue > 26)
    return np.stack((white, red_text), axis=-1).astype(np.float32)


@lru_cache(maxsize=8)
def _badge_artwork(roots: tuple[str, ...], region: str) -> np.ndarray | None:
    layer = "cn" if region == "CN" else "base"
    for root in roots:
        image_dir = Path(root).parents[2] / layer / "image"
        artwork = _read_rgba(image_dir / "battle/助战标记.png")
        if artwork is not None:
            return artwork[:, :, :3]
    return None


@lru_cache(maxsize=8)
def _badge_templates(roots: tuple[str, ...], region: str) -> tuple[np.ndarray, ...]:
    artwork = _badge_artwork(roots, region)
    if artwork is None:
        return ()
    templates = []
    for scale in (0.42, 0.48, 0.54, 0.60, 0.66):
        # The card on the right can hide the far end of the badge.
        for fraction in (0.56, 0.76, 1.0):
            width = round(artwork.shape[1] * fraction)
            badge = cv2.resize(artwork[:, :width], None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA)
            templates.append(_badge_pixels(badge))
    return tuple(templates)


def _support_score(tile: np.ndarray, templates: tuple[np.ndarray, ...]) -> float:
    region = _badge_pixels(tile[9:27, 59:98])
    matches = [cv2.matchTemplate(region, template, cv2.TM_CCORR_NORMED)
               for template in templates
               if template.shape[0] <= region.shape[0] and template.shape[1] <= region.shape[1]]
    return max((float(cv2.minMaxLoc(response)[1]) for response in matches), default=0.0)


def _hud_support_slot(frame: np.ndarray, roots: tuple[str, ...], region: str) -> int | None:
    artwork = _badge_artwork(roots, region)
    if artwork is None or artwork.shape[0] > 53 or artwork.shape[1] > 151:
        return None
    template = _badge_pixels(artwork)
    scores = []
    for index in range(3):
        x = 175 + 318 * index
        hud = _badge_pixels(frame[378:431, x:x + 151])
        response = cv2.matchTemplate(hud, template, cv2.TM_CCORR_NORMED)
        scores.append((float(cv2.minMaxLoc(response)[1]), index + 1))
    (top, slot), (runner_up, _), *_ = sorted(scores, reverse=True)
    return slot if top >= 0.80 and top - runner_up >= 0.15 else None


class OpeningPreviewRecognizer:
    """Return thumbnail evidence for an already confirmed main battle screenshot.

    These results describe color and portrait evidence. Card seals and other
    effects must be checked independently before consuming a preview result.
    """

    def __init__(self, region: str = "auto") -> None:
        if region not in ("auto", "CN", "JP"):
            raise ValueError("opening preview region must be auto, CN, or JP")
        self.region = region
        self._roots = tuple(str(path) for path in default_command_card_roots())

    @classmethod
    @lru_cache(maxsize=1)
    def default(cls) -> OpeningPreviewRecognizer:
        return cls()

    def _colors(self, frame: np.ndarray) -> tuple[str | None, tuple[_ColorMatch, ...]]:
        cn = _cn_colors(frame) if self.region != "JP" else ()
        jp = _jp_colors(frame, self._roots) if self.region != "CN" else ()
        if self.region == "CN":
            return "CN", cn
        if self.region == "JP":
            return "JP", jp

        # Positive JP text and CN background color are independent. Any
        # disagreement between accepted results leaves the hand to the full page.
        if any(left.color is not None and right.color is not None and left.color != right.color
               for left, right in zip(cn, jp)):
            return None, ()
        cn_complete = bool(cn) and all(match.color is not None for match in cn)
        jp_complete = bool(jp) and all(match.color is not None for match in jp)
        if jp_complete:
            return "JP", jp
        if cn_complete:
            return "CN", cn
        return None, ()

    def detect(
        self,
        image,
        frontline_servant_ids: Sequence[str | int | None],
    ) -> tuple[PreviewCard, ...]:
        frame = _normalize_image(image)
        if frame is None:
            return ()
        height, width = frame.shape[:2]
        if height < BASE_HEIGHT or width * BASE_HEIGHT != height * BASE_WIDTH:
            return ()
        if (width, height) != (BASE_WIDTH, BASE_HEIGHT):
            frame = cv2.resize(frame, (BASE_WIDTH, BASE_HEIGHT), interpolation=cv2.INTER_AREA)

        region, color_matches = self._colors(frame)
        if region is None or not color_matches:
            return ()
        slots_by_servant = {}
        if len(frontline_servant_ids) == 3:
            for slot, identity in enumerate(frontline_servant_ids, 1):
                identifiers = servant_template_ids(identity)
                if identifiers:
                    slots_by_servant.setdefault(identifiers[0], []).append(slot)
        # An unknown frontline entry might be an unrecognized active servant.
        # With only a shortlist we cannot rule out an omitted appearance.
        roster_known = sum(map(len, slots_by_servant.values())) == 3
        owners = {sid: _owner_templates(self._roots, sid) for sid in slots_by_servant} if roster_known else {}
        owners_ready = bool(owners) and all(owners.values())
        support_slot = None
        if owners_ready and any(len(slots) > 1 for slots in slots_by_servant.values()):
            support_slot = _hud_support_slot(frame, self._roots, region)

        cards = []
        for slot, (match, transform) in enumerate(zip(color_matches, _CARD_TRANSFORMS), 1):
            card = PreviewCard(slot, match.color, match.score, match.margin, region)
            if match.color is None or not owners_ready:
                reason = "color_unknown" if match.color is None else (
                    "candidate_assets_incomplete" if roster_known else "frontline_identity_unknown"
                )
                cards.append(replace(card, owner_reason=reason))
                continue
            tile = cv2.warpAffine(frame, transform, (105, 67))
            cards.append(self._identify_owner(card, tile, owners, slots_by_servant, support_slot))
        return tuple(cards)

    def _identify_owner(self, card, tile, owners, slots_by_servant, support_slot):
        face = tile[11:55, 23:73]
        ranked = sorted(((*_owner_score(face, templates), sid) for sid, templates in owners.items()),
                        reverse=True)
        score, template, sid = ranked[0]
        margin = score - (ranked[1][0] if len(ranked) > 1 else 0.0)
        slot, reason, support_score = None, "identified", None
        if score < _OWNER_MIN_SCORE:
            sid, reason = None, "owner_score_below_threshold"
        elif margin < _OWNER_MIN_MARGIN:
            sid, reason = None, "owner_margin_insufficient"
        else:
            slots = slots_by_servant[sid]
            if len(slots) == 1:
                slot = slots[0]
            elif support_slot in slots:
                support_score = _support_score(tile, _badge_templates(self._roots, card.region))
                if support_score >= _SUPPORT_MIN_SCORE:
                    slot = support_slot
                else:
                    reason = "duplicate_owner_badge_unconfirmed"
            else:
                reason = "duplicate_frontline_identity"
        return replace(
            card,
            owner_slot=slot,
            servant_id=sid,
            owner_score=score,
            owner_margin=margin,
            owner_template=template,
            owner_reason=reason,
            support_score=support_score,
        )
