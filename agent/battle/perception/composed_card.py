"""Full composed-card matching among the current three frontline identities.

No device, OCR, NP-gauge, or catalog-wide identity search is performed here.
All artwork/name states come from the local composition metadata. Unknown
identities or incomplete assets remain non-authoritative so callers can retain
their existing fallback; a complete negative NP result can veto scenery OCR.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from threading import RLock
from typing import Sequence

import cv2
import numpy as np

from ..card_composer import CommandCardComposer
from ..core.enums import CardColor
from ..core.models import Confidence
from ..data.servant_identity import servant_template_ids


_COLORS = {'arts': CardColor.ARTS, 'buster': CardColor.BUSTER, 'quick': CardColor.QUICK}
_WORK_SCALE = .5
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_PREPARED_TEMPLATES = 256  # Per region; JP+CN auto remains bounded by 512.
# These only request a new screenshot; neither value accepts an NP card.
# 2026-10-31 JP: peak gold flash gave real NP scores .709/.786 and neutral
# saturation .130/.133; clear phases passed the unchanged .80 positive gate.
_NP_GLARE_SCORE_WINDOW = .20
_NP_GLARE_MIN_SATURATION = .10
_NP_GLARE_WHITE_LEVEL = 230


@dataclass(frozen=True)
class ComposedCardDetection:
    ui_slot: int
    owner_slot: int | None = None
    servant_id: str | None = None
    color: CardColor | None = None
    score: float = 0.0
    margin: float = 0.0
    template: str = ''
    reason: str = 'unknown'
    visual_present: bool = False
    authoritative: bool = False
    glare_fraction: float = 0.0

    @property
    def best_score(self) -> float:
        return self.score

    @property
    def confidence(self) -> Confidence:
        return Confidence(self.score, 'composed_card')

    @property
    def matched_template(self) -> str:
        return self.template


@dataclass(frozen=True)
class _Template:
    color: CardColor
    name: str
    pixels: np.ndarray
    mask: np.ndarray

    @property
    def nbytes(self):
        return self.pixels.nbytes + self.mask.nbytes


@dataclass(frozen=True)
class _Prepared:
    templates: tuple[_Template, ...]
    complete: bool
    reason: str

    @property
    def nbytes(self):
        return sum(t.nbytes for t in self.templates)


def _identity(value) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return str(int(text)) if text.isascii() and text.isdigit() and int(text) > 0 else None


class ComposedCardRecognizer:
    """Bounded reusable JP/CN template banks; clear after updating resources.

    ``auto`` compares explicitly registered JP and CN text independently; it
    never substitutes JP text for missing CN assets. All three face colours are
    tested, including colours changed by skills. No-identity generic recognition
    is deliberately left to the caller's existing fallback in this version.
    """

    def __init__(self, project_root: str | Path | None = None, *, region='auto',
                 regions: Sequence[str] | None = None, roots=None,
                 min_face_score=.82, min_np_score=.80, min_margin=.07,
                 cache_size=32, cache_bytes=64 * 1024 * 1024):
        self.root = Path(project_root or Path(__file__).resolve().parents[3]).resolve()
        self.regions = tuple(regions) if regions is not None else (('JP', 'CN') if region == 'auto' else (region,))
        if not self.regions or any(r not in ('JP', 'CN') for r in self.regions):
            raise ValueError('regions must contain JP and/or CN')
        self.regions = tuple(dict.fromkeys(self.regions))
        self.min_face_score = float(min_face_score)
        self.min_np_score = float(min_np_score)
        self.min_margin = float(min_margin)
        if cache_size < 0 or cache_bytes < 0:
            raise ValueError('cache limits must be non-negative')
        self.cache_size, self.cache_bytes = int(cache_size), int(cache_bytes)
        if roots is None:
            roots = [p for p in (self.root / 'assets/resource/base/image/CommandCards',
                                  self.root / 'resource/base/image/CommandCards') if p.is_dir()]
        self.composer = CommandCardComposer(roots, cache_size=256, cache_bytes=64 * 1024 * 1024)
        self._cache = OrderedDict()
        self._cache_used = 0
        self._lock = RLock()
        self._metadata = self._read_metadata()
        self._load_manifests()

    @classmethod
    @lru_cache(maxsize=1)
    def default(cls) -> 'ComposedCardRecognizer':
        return cls()

    def _read_metadata(self):
        path = self.root / 'agent/battle_editor/data/np_cards.json'
        try:
            if path.stat().st_size > _MAX_METADATA_BYTES:
                return {}
            data = json.loads(path.read_text('utf-8'))
            return data if data.get('schema') == 1 and isinstance(data.get('servants'), dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    @staticmethod
    def _json(path):
        try:
            if path.stat().st_size > _MAX_METADATA_BYTES:
                return {}
            data = json.loads(path.read_text('utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _load_manifests(self):
        self._portraits = {}
        for root in self.composer.roots:
            servants = self._json(root / 'manifest').get('servants', {})
            if not isinstance(servants, dict):
                continue
            for sid, entries in servants.items():
                if not isinstance(sid, str) or not isinstance(entries, list):
                    continue
                variants = self._portraits.setdefault(sid, set())
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    match = re.fullmatch(rf'{re.escape(sid)}/card_servant_([1-9][0-9]*)\.png', entry.get('file', ''))
                    if match:
                        variants.add(int(match[1]))
        self._np_manifests = {}
        for region in self.regions:
            roots = self.composer.region_roots.get(region, ()) + (self.composer.roots if region == 'JP' else ())
            for root in roots:
                manifest = self._json(root / '_composition/manifest')
                if manifest.get('region') == region:
                    self._np_manifests[region] = manifest
                    break

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
            self._cache_used = 0
            self.composer.clear_cache()
            self._metadata = self._read_metadata()
            self._load_manifests()

    def cache_info(self):
        with self._lock:
            return {'entries': len(self._cache), 'bytes': self._cache_used,
                    'templates': sum(len(v.templates) for v in self._cache.values())}

    def _forms(self, sid, region):
        # Aliases are explicit native catalog facts, also available in Android
        # packages without the optional editor sidecar. Missing alias directories
        # do not invalidate an otherwise complete canonical template bank.
        aliases = servant_template_ids(sid)
        available = lambda alias: any(
            (root / f'{alias}/card_servant_{variant}.png').is_file()
            for root in self.composer.roots for variant in self._portraits.get(alias, ()))
        visited, pending = set(), [alias for alias in aliases if alias == sid or available(alias)]
        if sid not in pending:
            pending.append(sid)
        while pending and len(visited) < 8:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            servant = self._metadata.get('servants', {}).get(current, {})
            if not isinstance(servant, dict):
                servant = {}
            yield current, servant
            regions = servant.get('regions', {})
            regional = regions.get(region, {}) if isinstance(regions, dict) else {}
            if not isinstance(regional, dict):
                continue
            transformed = _identity(regional.get('transformId'))
            if transformed and transformed not in visited:
                pending.append(transformed)

    def _prepare(self, sid, kind):
        key = sid, kind, self.regions
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            prepared = self._build(sid, kind)
            if self.cache_size and prepared.nbytes <= self.cache_bytes:
                self._cache[key] = prepared
                self._cache_used += prepared.nbytes
                while len(self._cache) > self.cache_size or self._cache_used > self.cache_bytes:
                    _, removed = self._cache.popitem(last=False)
                    self._cache_used -= removed.nbytes
            return prepared

    def _build(self, sid, kind):
        templates, seen = [], set()
        complete = True
        for region in self.regions:
            region_candidates = 0
            forms = list(self._forms(sid, region))
            if not forms:
                complete = False
            for render_sid, servant in forms:
                variants = tuple(sorted(self._portraits.get(render_sid, ())))
                if not variants or len(variants) > 24:
                    complete = False
                    continue
                if kind == 'face':
                    specifications = [(c, None, 0) for c in _COLORS]
                else:
                    manifest = self._np_manifests.get(region, {})
                    names, extras = manifest.get('np_names', {}), manifest.get('np_extra_names', {})
                    extra_names = extras.get(render_sid, {}) if isinstance(extras, dict) else {}
                    entries = ([names.get(render_sid)] if isinstance(names, dict) else [])
                    if isinstance(extra_names, dict):
                        entries += list(extra_names.values())
                    entries = [entry for entry in entries if isinstance(entry, dict)]
                    if not entries or len(entries) > 32:
                        complete = False
                        continue
                    specifications = set()
                    for entry in entries:
                        match = re.fullmatch(rf'np/{re.escape(render_sid)}/([A-Za-z0-9_-]+\.png)', entry.get('file', ''))
                        rows = entry.get('name_rows')
                        if not match or not isinstance(rows, list) or not rows or any(type(row) is not int or row not in (0, 1) for row in rows):
                            complete = False
                            continue
                        for row in rows:
                            # Names/rows are explicitly registered in the regional
                            # asset manifest. Try every colour for NP colour changes.
                            for color in _COLORS:
                                specifications.add((color, match[1], row))
                for variant in variants:
                    for color, name_file, row in sorted(specifications):
                        template_key = render_sid, variant, color, region, name_file, row
                        if template_key in seen:
                            continue
                        seen.add(template_key)
                        region_candidates += 1
                        if region_candidates > _MAX_PREPARED_TEMPLATES:
                            return _Prepared((), False, 'candidate_limit_exceeded')
                        try:
                            card = self.composer.compose(render_sid, color, portrait_variant=variant,
                                                         card_type=kind, region=region,
                                                         np_name_file=name_file, np_name_row=row)
                        except (OSError, ValueError, cv2.error):
                            complete = False
                            continue
                        mask = card.mask.copy()
                        # Top-edge skill/buff/support markers and bottom HUD
                        # pixels vary independently of card identity/colour.
                        mask[:45] = 0
                        mask[230:] = 0
                        if kind == 'np':
                            # The seal overlay crosses this central band. Keep
                            # the separate portrait, coloured rays and NP title.
                            mask[80:140] = 0
                        bgr = cv2.resize(card.bgr, None, fx=_WORK_SCALE, fy=_WORK_SCALE,
                                         interpolation=cv2.INTER_AREA)
                        mask = cv2.resize(mask, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)
                        if cv2.countNonZero(mask) < 100:
                            complete = False
                            continue
                        name = f'{region}/{render_sid}/v{variant}/{kind}/{color}/{name_file or "face"}:row{row}'
                        # Face-card labels/rays have distinct shapes. NP name
                        # art is shared across colour changes, so keep RGB there
                        # to distinguish similarly shaped NP-colour backgrounds.
                        pixels = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if kind == 'face' else bgr
                        templates.append(_Template(_COLORS[color], name, pixels, mask))
        return _Prepared(tuple(templates), complete and bool(templates),
                         'ready' if complete and templates else 'candidate_assets_incomplete')

    @staticmethod
    def _image(image):
        if hasattr(image, 'to_numpy'):
            image = image.to_numpy()
        array = np.asarray(image)
        if array.ndim != 3 or array.shape[2] not in (3, 4) or min(array.shape[:2]) < 90:
            return None
        if abs(array.shape[1] / array.shape[0] - 16 / 9) > .04:
            return None
        reduced = cv2.resize(np.ascontiguousarray(array[:, :, :3], dtype=np.uint8),
                             (round(1280 * _WORK_SCALE), round(720 * _WORK_SCALE)), interpolation=cv2.INTER_AREA)
        return reduced

    def detect_all(self, image, frontline_servant_ids):
        frame = self._image(image)
        return (self._detect(frame, frontline_servant_ids, 'face'),
                self._detect(frame, frontline_servant_ids, 'np'))

    def detect(self, image, frontline_servant_ids):
        return self._detect(self._image(image), frontline_servant_ids, 'face')

    def detect_np(self, image, frontline_servant_ids):
        return self._detect(self._image(image), frontline_servant_ids, 'np')

    def _detect(self, frame, identities, kind):
        count = 5 if kind == 'face' else 3
        if frame is None:
            return tuple(ComposedCardDetection(i, reason='image_unreadable') for i in range(1, count + 1))
        if identities is None or len(identities) != 3:
            return tuple(ComposedCardDetection(i, reason='frontline_roster_invalid') for i in range(1, count + 1))
        roster = tuple(_identity(v) for v in identities)
        if kind == 'face':
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        candidates = {sid: self._prepare(sid, kind) for sid in dict.fromkeys(roster) if sid}
        if not candidates:
            return tuple(ComposedCardDetection(i, reason='frontline_identity_unknown') for i in range(1, count + 1))
        outputs = []
        for slot in range(1, count + 1):
            center = 128 + 256 * (slot - 1) if kind == 'face' else 410 + 230 * (slot - 1)
            radius = 138 if kind == 'face' else 145
            x = max(0, round((center - radius) * _WORK_SCALE))
            right = min(frame.shape[1], round((center + radius) * _WORK_SCALE))
            # Buster's composed rays extend below the Arts NP's tight crop;
            # stopping at y=350 prevents the correct template from aligning.
            y, bottom = (375, 655) if kind == 'face' else (70, 370)
            crop = frame[round(y * _WORK_SCALE):round(bottom * _WORK_SCALE), x:right]
            ranked = {}
            for sid, prepared in candidates.items():
                if kind == 'np' and sid != roster[slot - 1]:
                    continue
                for template in prepared.templates:
                    if template.pixels.shape[0] > crop.shape[0] or template.pixels.shape[1] > crop.shape[1]:
                        continue
                    result = cv2.matchTemplate(crop, template.pixels, cv2.TM_CCOEFF_NORMED, mask=template.mask)
                    clean = np.nan_to_num(result, nan=-1, posinf=-1, neginf=-1)
                    _, score, _, location = cv2.minMaxLoc(clean)
                    key = sid, template.color
                    if key not in ranked or score > ranked[key][0]:
                        ranked[key] = score, template.name, template, location
            own = candidates.get(roster[slot - 1]) if kind == 'np' else None
            authoritative = own.complete if own is not None else (kind == 'face' and all(roster)
                              and all(p.complete for p in candidates.values()))
            if not ranked:
                outputs.append(ComposedCardDetection(slot, reason='candidate_assets_incomplete'))
                continue
            ranking = sorted(ranked.items(), key=lambda item: item[1][0], reverse=True)
            (sid, color), (score, name, template, location) = ranking[0]
            runner_up = next((value[0] for (other_sid, _), value in ranking[1:]
                              if kind == 'face' or other_sid != sid), 0.0)
            margin = score - runner_up
            threshold = self.min_face_score if kind == 'face' else self.min_np_score
            present = score >= threshold and margin >= self.min_margin
            owner_slots = [i for i, value in enumerate(roster, 1) if value == sid]
            owner_slot = owner_slots[0] if len(owner_slots) == 1 else None
            reason = 'identified'
            glare_fraction = 0.0
            if not present:
                reason = 'card_not_present' if score < threshold else 'card_margin_insufficient'
                if kind == 'np' and threshold - _NP_GLARE_SCORE_WINDOW <= score < threshold:
                    left, top = location
                    height, width = template.mask.shape
                    actual = crop[top:top + height, left:left + width]
                    supported = template.mask > 0
                    saturated = np.min(actual, axis=2) >= _NP_GLARE_WHITE_LEVEL
                    glare_fraction = float(np.count_nonzero(supported & saturated)) / max(1, np.count_nonzero(supported))
                    if glare_fraction >= _NP_GLARE_MIN_SATURATION:
                        # A bright transient is uncertain, not authoritative
                        # absence or availability. Runtime must resample.
                        reason, authoritative = 'np_glare_uncertain', False
                owner_slot, sid, color = None, None, None
            elif kind == 'np':
                if roster[slot - 1] == sid:
                    owner_slot = slot
                else:
                    owner_slot = None
                    reason = 'unexpected_np_owner'
                    present = False
            elif owner_slot is None:
                reason = 'duplicate_frontline_identity'
            outputs.append(ComposedCardDetection(slot, owner_slot, sid, color, score, margin, name,
                                                 reason, present, bool(authoritative), glare_fraction))
        return tuple(outputs)
