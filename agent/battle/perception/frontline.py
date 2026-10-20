"""Identify the three battle HUD portraits among the known formation members."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from ..core.models import Confidence
from .card_owner import _load_servant_templates, _normalize_image


@dataclass(frozen=True)
class FrontlineIdentity:
    slot: int
    servant_id: str | None
    confidence: Confidence
    margin: float = 0.0
    matched_template: str = ""


@lru_cache(maxsize=1)
def _status_roots() -> tuple[str, ...]:
    project = Path(__file__).resolve().parents[3]
    return tuple(str(path.resolve()) for root in (project, project.parent)
                 for path in (root / "assets/resource/base/image/ServantStatus",
                              root / "resource/base/image/ServantStatus")
                 if path.is_dir())


@lru_cache(maxsize=64)
def _prepared_templates(roots, servant_id, scales, sx, sy):
    prepared = []
    for item in _load_servant_templates(roots, servant_id):
        for scale in scales:
            size = (round(item.bgr.shape[1] * scale * sx),
                    round(item.bgr.shape[0] * scale * sy))
            prepared.append((
                cv2.resize(item.bgr, size, interpolation=cv2.INTER_CUBIC),
                cv2.resize(item.mask, size, interpolation=cv2.INTER_NEAREST),
                item.relative_path,
            ))
    return tuple(prepared)


class FrontlineServantRecognizer:
    def __init__(self, rois, scales, min_score, min_margin, analysis_scale, *, roots=None):
        self.rois = tuple(tuple(roi) for roi in rois)
        self.scales = tuple(scales)
        self.min_score = min_score
        self.min_margin = min_margin
        self.analysis_scale = analysis_scale
        self.roots = tuple(str(Path(root).resolve()) for root in roots) if roots is not None else _status_roots()
        self._candidates = ()
        self._verified = {}
        self._unmatched = {}

    @classmethod
    def from_context(cls, context):
        node = context.get_node_data("战斗_前排身份识别配置")
        return cls(**node["attach"])

    def detect(self, image, candidate_servant_ids: Sequence[str | int | None]):
        frame = _normalize_image(image)
        candidates = tuple(dict.fromkeys(str(sid) for sid in candidate_servant_ids if sid))
        if candidates != self._candidates:
            self._candidates = candidates
            self._verified.clear()
            self._unmatched.clear()
        if frame is None or not candidates:
            return tuple(FrontlineIdentity(slot, None, Confidence(0.0, "status_template"))
                         for slot in (1, 2, 3))

        frame = cv2.resize(frame, None, fx=self.analysis_scale, fy=self.analysis_scale,
                           interpolation=cv2.INTER_AREA)

        sy, sx = frame.shape[0] / 720, frame.shape[1] / 1280
        templates = {sid: _prepared_templates(self.roots, sid, self.scales, sx, sy)
                     for sid in candidates}
        results = []
        for slot, (x, y, w, h) in enumerate(self.rois, start=1):
            region = frame[round(y * sy):round((y + h) * sy),
                           round(x * sx):round((x + w) * sx)]
            cached = self._verified.get(slot)
            if cached is not None:
                identity, (left, top, width, height), portrait, mask = cached
                current = region[top:top + height, left:left + width]
                if current.shape == portrait.shape:
                    score = cv2.minMaxLoc(cv2.matchTemplate(
                        current, portrait, cv2.TM_CCOEFF_NORMED, mask=mask,
                    ))[1]
                    if np.isfinite(score) and score >= 0.98:
                        results.append(identity)
                        continue
            if slot in self._unmatched and np.array_equal(region, self._unmatched[slot]):
                results.append(FrontlineIdentity(slot, None, Confidence(0.0, "status_template")))
                continue
            ranked = []
            matches = {}
            for sid, variants in templates.items():
                best, path = 0.0, ""
                for template, mask, relative_path in variants:
                    if template.shape[0] > region.shape[0] or template.shape[1] > region.shape[1]:
                        continue
                    response = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED, mask=mask)
                    response = np.nan_to_num(response, nan=-1.0, posinf=-1.0, neginf=-1.0)
                    _, score, _, location = cv2.minMaxLoc(response)
                    if score > best:
                        best, path = float(score), relative_path
                        matches[sid] = (location, template.shape[:2], mask)
                ranked.append((best, sid, path))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            score, sid, path = ranked[0]
            margin = score - (ranked[1][0] if len(ranked) > 1 else 0.0)
            identified = score >= self.min_score and margin >= self.min_margin
            identity = FrontlineIdentity(
                slot, sid if identified else None, Confidence(score, "status_template"), margin, path,
            )
            if identified:
                (left, top), (height, width), mask = matches[sid]
                self._verified[slot] = (
                    identity, (left, top, width, height),
                    region[top:top + height, left:left + width].copy(), mask,
                )
                self._unmatched.pop(slot, None)
            else:
                self._verified.pop(slot, None)
                self._unmatched[slot] = region.copy()
            results.append(identity)
        return tuple(results)
