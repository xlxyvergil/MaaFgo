"""Command-card owner recognition from the current three-servant roster.

The game uses dedicated transparent ``extraAssets.commands`` portraits for the
five face cards.  Formation/list portraits are different artwork and must not
be used as substitutes.  This module deliberately searches only the current
frontline candidates: it is both faster and safer than classifying against the
whole servant catalog.

No MaaFramework type is imported here.  The caller supplies the current
screenshot and the semantic ``slot -> servant_id`` roster.  Missing assets,
duplicate servant identities, low scores, or a small best/runner-up margin are
reported as unknown instead of being guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np

from ..core.models import Confidence

BASE_WIDTH = 1280
BASE_HEIGHT = 720

# Exclude the battlefield above the cards: unrelated scenery can correlate
# with a rival portrait and hide the margin of the actual card owner.
CARD_OWNER_ROIS = {
    1: (0, 400, 256, 190),
    2: (240, 400, 280, 190),
    3: (500, 400, 280, 190),
    4: (760, 400, 280, 190),
    5: (1020, 400, 260, 190),
}

# NP portraits extend beyond the card's title panel. Include both edges so the
# full-width masked command template can slide into its actual position.
NP_OWNER_ROIS = {slot: (270 + 230 * (slot - 1), 70, 280, 255) for slot in (1, 2, 3)}

# Only compare the face band. Card labels cover the torso and buffs cover the
# upper hairline; including either overlay lowers otherwise exact matches.
VISIBLE_TEMPLATE_TOP = 35
VISIBLE_TEMPLATE_BOTTOM = 115
TEMPLATE_SCALES = (0.925, 0.9375, 0.95)


@dataclass(frozen=True)
class CardOwnerDetection:
    ui_slot: int
    owner_slot: int | None
    servant_id: str | None
    confidence: Confidence
    best_score: float = 0.0
    margin: float = 0.0
    matched_template: str = ""
    reason: str = ""


@dataclass(frozen=True)
class _Template:
    relative_path: str
    bgr: np.ndarray
    mask: np.ndarray


def default_command_card_roots() -> tuple[Path, ...]:
    """Return source-tree and packaged command-card resource directories."""
    project = Path(__file__).resolve().parents[3]
    roots: list[Path] = []
    for root in (project, project.parent):
        for candidate in (
            root / "assets" / "resource" / "base" / "image" / "CommandCards",
            root / "resource" / "base" / "image" / "CommandCards",
        ):
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
    return tuple(roots)


def _normalize_image(image) -> np.ndarray | None:
    if image is None:
        return None
    if hasattr(image, "to_numpy"):
        image = image.to_numpy()
    array = np.asarray(image)
    if array.size == 0:
        return None
    if array.ndim == 2:
        array = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
    elif array.ndim == 3 and array.shape[2] == 4:
        array = array[:, :, :3]
    if array.ndim != 3 or array.shape[2] != 3:
        return None
    return array.astype(np.uint8, copy=False)


def _read_rgba(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3:
        return None
    if image.shape[2] == 3:
        alpha = np.full(image.shape[:2], 255, dtype=np.uint8)
        image = np.dstack((image, alpha))
    if image.shape[2] != 4:
        return None
    return image


@lru_cache(maxsize=1024)
def _load_servant_templates(
    roots: tuple[str, ...], servant_id: str, visible_top: int = VISIBLE_TEMPLATE_TOP
) -> tuple[_Template, ...]:
    templates: list[_Template] = []
    seen: set[Path] = set()
    for root_text in roots:
        root = Path(root_text)
        directory = root / servant_id
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.png")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            image = _read_rgba(path)
            if image is None:
                continue
            top = round(image.shape[0] * visible_top / 256)
            bottom = round(image.shape[0] * VISIBLE_TEMPLATE_BOTTOM / 256)
            bgr = image[top:bottom, :, :3]
            alpha = image[top:bottom, :, 3]
            mask = np.where(alpha >= 128, 255, 0).astype(np.uint8)
            if cv2.countNonZero(mask) < 100:
                continue
            templates.append(
                _Template(
                    relative_path=f"{servant_id}/{path.name}",
                    bgr=bgr,
                    mask=mask,
                )
            )
    return tuple(templates)


class CommandCardOwnerRecognizer:
    """Recognize face-card and NP portraits among the current frontline servants."""

    def __init__(
        self,
        roots: Iterable[str | Path] | None = None,
        *,
        min_score: float = 0.86,
        min_margin: float = 0.05,
        scales: Sequence[float] = TEMPLATE_SCALES,
    ) -> None:
        selected = tuple(Path(root) for root in (roots or default_command_card_roots()))
        self.roots = tuple(str(root.resolve()) for root in selected if root.is_dir())
        self.min_score = float(min_score)
        self.min_margin = float(min_margin)
        self.scales = tuple(float(scale) for scale in scales if scale > 0)

    @classmethod
    @lru_cache(maxsize=1)
    def default(cls) -> "CommandCardOwnerRecognizer":
        return cls()

    def detect(
        self,
        image,
        frontline_servant_ids: Sequence[str | int | None],
    ) -> tuple[CardOwnerDetection, ...]:
        return self._detect(image, frontline_servant_ids, CARD_OWNER_ROIS, self.min_margin)

    def detect_np(self, image, frontline_servant_ids) -> tuple[CardOwnerDetection, ...]:
        # Subtract the mean for NP portraits: bright battle sprites/backgrounds
        # can score highly under raw correlation even when no upper card exists.
        # NP buff icons cover the upper hairline. Compare the lower face band
        # as well as the original band, since different overlays hide either.
        frame = _normalize_image(image)
        if frame is not None:
            frame = cv2.resize(frame, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        return self._detect(frame, frontline_servant_ids, NP_OWNER_ROIS, 0.05,
                            min_score=0.80, method=cv2.TM_CCOEFF_NORMED,
                            visible_top=65)

    def _detect(self, image, frontline_servant_ids, rois, min_margin, *,
                min_score=None, method=cv2.TM_CCORR_NORMED,
                visible_top=VISIBLE_TEMPLATE_TOP):
        min_score = self.min_score if min_score is None else min_score
        frame = _normalize_image(image)
        if frame is None:
            return tuple(self._unknown(slot, "image_unreadable") for slot in rois)
        if len(frontline_servant_ids) != 3:
            return tuple(self._unknown(slot, "frontline_roster_invalid") for slot in rois)

        roster = tuple(
            str(value).strip() if value is not None and str(value).strip() else None
            for value in frontline_servant_ids
        )
        if not any(roster):
            return tuple(self._unknown(slot, "frontline_identity_unknown") for slot in rois)
        if not self.roots:
            return tuple(self._unknown(slot, "command_assets_missing") for slot in rois)

        slots_by_servant: dict[str, list[int]] = {}
        templates_by_servant: dict[str, tuple[_Template, ...]] = {}
        for slot, servant_id in enumerate(roster, start=1):
            if servant_id is None:
                continue
            slots_by_servant.setdefault(servant_id, []).append(slot)
            templates_by_servant.setdefault(
                servant_id,
                _load_servant_templates(self.roots, servant_id, visible_top)
                + (_load_servant_templates(self.roots, servant_id)
                   if visible_top != VISIBLE_TEMPLATE_TOP else ()),
            )
        # A missing candidate is unsafe: its cards could otherwise be forced to
        # one of the remaining identities with an apparently strong score.
        if any(not values for values in templates_by_servant.values()):
            return tuple(self._unknown(slot, "candidate_assets_incomplete") for slot in rois)

        height, width = frame.shape[:2]
        sx = width / BASE_WIDTH
        sy = height / BASE_HEIGHT
        results: list[CardOwnerDetection] = []
        for ui_slot, base_roi in rois.items():
            region = self._crop(frame, base_roi, sx, sy)
            if region is None:
                results.append(self._unknown(ui_slot, "card_roi_invalid"))
                continue

            ranked: list[tuple[float, str, str]] = []
            for servant_id, templates in templates_by_servant.items():
                score, template = self._best_template(region, templates, sx, sy, method=method)
                ranked.append((score, servant_id, template))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            best_score, best_id, best_template = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0.0
            margin = best_score - second_score
            owner_slots = slots_by_servant[best_id]

            if best_score < min_score:
                reason = "owner_score_below_threshold"
                owner_slot = None
                servant_id = None
            elif margin < min_margin:
                reason = "owner_margin_insufficient"
                owner_slot = None
                servant_id = None
            elif len(owner_slots) != 1:
                reason = "duplicate_frontline_identity"
                owner_slot = None
                servant_id = None
            else:
                reason = "identified"
                owner_slot = owner_slots[0]
                servant_id = best_id

            results.append(
                CardOwnerDetection(
                    ui_slot=ui_slot,
                    owner_slot=owner_slot,
                    servant_id=servant_id,
                    confidence=Confidence(best_score, "command_template"),
                    best_score=best_score,
                    margin=margin,
                    matched_template=best_template,
                    reason=reason,
                )
            )
        return tuple(results)

    @staticmethod
    def _crop(
        frame: np.ndarray,
        roi: tuple[int, int, int, int],
        sx: float,
        sy: float,
    ) -> np.ndarray | None:
        x, y, width, height = roi
        x1 = max(0, round(x * sx))
        y1 = max(0, round(y * sy))
        x2 = min(frame.shape[1], round((x + width) * sx))
        y2 = min(frame.shape[0], round((y + height) * sy))
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    def _best_template(
        self,
        region: np.ndarray,
        templates: Sequence[_Template],
        sx: float,
        sy: float,
        *,
        method: int = cv2.TM_CCORR_NORMED,
    ) -> tuple[float, str]:
        best_score = 0.0
        best_path = ""
        for item in templates:
            for scale in self.scales:
                width = max(8, round(item.bgr.shape[1] * scale * sx))
                height = max(8, round(item.bgr.shape[0] * scale * sy))
                if width > region.shape[1] or height > region.shape[0]:
                    continue
                interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
                template = cv2.resize(item.bgr, (width, height), interpolation=interpolation)
                mask = cv2.resize(item.mask, (width, height), interpolation=cv2.INTER_NEAREST)
                response = cv2.matchTemplate(
                    region,
                    template,
                    method,
                    mask=mask,
                )
                response = np.nan_to_num(response, nan=-1.0, posinf=-1.0, neginf=-1.0)
                _, score, _, _ = cv2.minMaxLoc(response)
                if score > best_score:
                    best_score = float(score)
                    best_path = item.relative_path
        return best_score, best_path

    @staticmethod
    def _unknown(ui_slot: int, reason: str) -> CardOwnerDetection:
        return CardOwnerDetection(
            ui_slot=ui_slot,
            owner_slot=None,
            servant_id=None,
            confidence=Confidence(0.0, "command_template"),
            reason=reason,
        )

    @classmethod
    def _unknown_all(cls, reason: str) -> tuple[CardOwnerDetection, ...]:
        return tuple(cls._unknown(slot, reason) for slot in range(1, 6))
