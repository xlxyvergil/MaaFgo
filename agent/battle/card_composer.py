"""Offline, standalone command-card composition for Agent callers.

No Maa imports, Pillow, networking or game-asset decoding. The caller supplies
identity, artwork variant and card colour. NP callers must explicitly select
the language and, where necessary, the name filename and confirmed row; this module
does not infer a servant's NP, ascension, colour or current transformation.

Example::

    composer = CommandCardComposer()
    face = composer.compose(100100, 'buster', portrait_variant=1)
    noble = composer.compose(202200, 'quick', card_type='np', region='JP')
    scores = cv2.matchTemplate(screenshot, noble.bgr,
                               cv2.TM_CCOEFF_NORMED, mask=noble.mask)

The layout is an experimental 720p-calibrated profile, not a reproduction of
all runtime animations, status overlays or perspective transforms. Battle
recognition reuses it through perception.composed_card with overlay masks.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re
from threading import RLock
from typing import Iterable, Mapping

import cv2
import numpy as np

PROFILE = 'command-card-720p-v1'
COLORS = frozenset(('buster', 'arts', 'quick'))
REGIONS = frozenset(('JP', 'CN', 'NA', 'TW', 'KR'))


@dataclass(frozen=True)
class ComposedCard:
    """Independent writable arrays: BGRA uint8 plus an alpha>=128 uint8 mask.

    ``anchor`` is the group's origin relative to this tightly cropped image.
    Use ``bgr`` with ``mask`` for matching: do not compare transparent pixels.
    The caller owns the returned arrays; modifying them cannot poison cache.
    """

    bgra: np.ndarray
    mask: np.ndarray
    anchor: tuple[float, float]
    profile: str = PROFILE

    @property
    def bgr(self) -> np.ndarray:
        return np.ascontiguousarray(self.bgra[:, :, :3])


def default_asset_roots() -> tuple[Path, ...]:
    project = Path(__file__).resolve().parents[2]
    paths = []
    for root in (project, project.parent):
        for relative in ('assets/resource/base/image/CommandCards', 'resource/base/image/CommandCards'):
            candidate = root / relative
            if candidate.is_dir() and candidate not in paths:
                paths.append(candidate)
    return tuple(paths)


def _number(value: str | int, label: str) -> str:
    text = str(value)
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', text):
        raise ValueError(f'{label} must be a positive integer, got {value!r}')
    return text


def _viewport(value: tuple[int, int]) -> tuple[int, int]:
    if len(value) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise ValueError('viewport must contain two integer dimensions')
    width, height = value
    if not 160 <= width <= 7680 or not 90 <= height <= 4320 or width * 9 != height * 16:
        raise ValueError('viewport must be positive 16:9 dimensions between 160x90 and 7680x4320')
    return width, height


def _premultiplied(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Bilinear RGBA resampling without black halos at transparent edges."""
    floating = image.astype(np.float32) / 255.0
    floating[:, :, :3] *= floating[:, :, 3:4]
    return cv2.resize(floating, (width, height), interpolation=cv2.INTER_LINEAR)


class CommandCardComposer:
    """Reusable bounded-cache composer. Images never leave process memory.

    Base and regional CommandCards roots share the same relative filenames:
    ``_composition/shared/*.png`` and ``_composition/np/<id>/card_servant_np.png``.
    Regional roots override base; localized text never falls back across regions.
    Existing ``<servant_id>/card_servant_<variant>.png`` portraits stay intact.
    Clear cache explicitly after resource updates; no mtime polling is done.
    """

    def __init__(
        self,
        roots: Iterable[str | Path] | str | Path | None = None,
        *,
        region_roots: Mapping[str, Iterable[str | Path] | str | Path] | None = None,
        cache_size: int = 32,
        cache_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError('cache_size must be a non-negative integer')
        if isinstance(cache_bytes, bool) or not isinstance(cache_bytes, int) or cache_bytes < 0:
            raise ValueError('cache_bytes must be a non-negative integer')
        selected = default_asset_roots() if roots is None else roots
        if isinstance(selected, (str, Path)):
            selected = (selected,)
        self.roots = tuple(Path(p).resolve() for p in selected)
        self.region_roots: dict[str, tuple[Path, ...]] = {}
        for region in REGIONS:
            inferred = []
            for base in self.roots:
                if base.parent.name == 'image' and base.parent.parent.name == 'base':
                    candidate = base.parents[2] / region.lower() / 'image/CommandCards'
                    if candidate not in inferred:
                        inferred.append(candidate)
            self.region_roots[region] = tuple(inferred)
        for region, values in (region_roots or {}).items():
            if region not in REGIONS:
                raise ValueError(f'Unknown resource region: {region!r}')
            if isinstance(values, (str, Path)):
                values = (values,)
            self.region_roots[region] = tuple(Path(p).resolve() for p in values)
        self.cache_size = cache_size
        self.cache_bytes = cache_bytes
        self._cache: OrderedDict[tuple, ComposedCard] = OrderedDict()
        self._cache_used = 0
        self._lock = RLock()

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_used = 0

    def cache_info(self) -> dict[str, int]:
        with self._lock:
            return {'entries': len(self._cache), 'bytes': self._cache_used}

    def _read(self, relative: str, *, expected: tuple[int, int], region: str = 'JP', localized_only: bool = False) -> np.ndarray:
        roots = self.region_roots.get(region, ())
        if not localized_only:
            roots += self.roots
        for root in roots:
            path = root / relative
            if not path.is_file():
                continue
            data = np.fromfile(path, dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
            if image is None or image.ndim != 3 or image.shape[2] != 4:
                raise ValueError(f'Composition asset must decode as BGRA: {path}')
            if (image.shape[1], image.shape[0]) != expected:
                raise ValueError(f'Composition asset has unexpected size, expected {expected}: {path}')
            return image
        raise FileNotFoundError(f'Command-card composition asset missing: {relative}; region={region}; roots={roots}. Localized text never falls back to a different language.')

    @staticmethod
    def _copy(card: ComposedCard) -> ComposedCard:
        return ComposedCard(card.bgra.copy(), card.mask.copy(), card.anchor, card.profile)

    def compose(
        self,
        servant_id: str | int,
        color: str,
        *,
        portrait_variant: str | int = 1,
        card_type: str = 'face',
        region: str | None = None,
        np_name_file: str | None = None,
        np_name_row: int = 0,
        viewport: tuple[int, int] = (1280, 720),
    ) -> ComposedCard:
        """Compose one card, without inferring its NP name or colour.

        ``np_name_file`` selects a PNG basename under this servant's regional
        ``_composition/np/<id>/`` folder; omitted means ``card_servant_np.png``.
        It is not a human-readable NP name or an arbitrary filesystem path.
        """
        sid = _number(servant_id, 'servant_id')
        variant = _number(portrait_variant, 'portrait_variant')
        if color not in COLORS:
            raise ValueError(f'color must be one of {sorted(COLORS)}')
        if card_type not in ('face', 'np'):
            raise ValueError('card_type must be face or np')
        width, height = _viewport(viewport)
        if isinstance(np_name_row, bool) or not isinstance(np_name_row, int) or np_name_row not in (0, 1):
            raise ValueError('np_name_row must be 0 (top) or 1 (bottom), explicitly selected by the caller')
        if card_type == 'np' and region not in REGIONS:
            raise ValueError(f'NP composition requires an explicit region from {sorted(REGIONS)}')
        if card_type == 'face' and region not in (None, 'JP', 'CN'):
            raise ValueError('Face composition region must be JP or CN (omitted means JP)')
        if card_type == 'face' and np_name_row != 0:
            raise ValueError('np_name_row is only valid for NP cards')
        if card_type == 'face' and np_name_file is not None:
            raise ValueError('np_name_file is only valid for NP cards')
        if np_name_file is not None and (not isinstance(np_name_file, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.png', np_name_file)):
            raise ValueError('np_name_file must be a PNG basename using letters, digits, underscores or hyphens; paths are not allowed')
        name_file = 'card_servant_np.png' if np_name_file is None else np_name_file
        key = (sid, variant, color, card_type, region, name_file, np_name_row, width, height, PROFILE)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._copy(self._cache[key])
            card = self._render(sid, variant, color, card_type, region, name_file, np_name_row, height)
            used = card.bgra.nbytes + card.mask.nbytes
            if self.cache_size and used <= self.cache_bytes:
                self._cache[key] = card
                self._cache_used += used
                while len(self._cache) > self.cache_size or self._cache_used > self.cache_bytes:
                    _, removed = self._cache.popitem(last=False)
                    self._cache_used -= removed.bgra.nbytes + removed.mask.nbytes
            return self._copy(card)

    def _render(self, sid: str, variant: str, color: str, card_type: str, region: str | None, name_file: str, row: int, height: int) -> ComposedCard:
        region = region or 'JP'
        scale = 1.25 * height / 720
        canvas = np.zeros((round(280 * scale), round(256 * scale), 4), dtype=np.float32)
        # Regional bundles use exactly the base-relative filenames. CN padding
        # is restored in source preparation, not by stretching at runtime.
        shared = '_composition/shared/'

        def add(image: np.ndarray, size: tuple[float, float], position: tuple[float, float], *, bottom: bool = False):
            w, h = size
            x, y = position
            layer = _premultiplied(image, max(1, round(w * scale)), max(1, round(h * scale)))
            left = round((128 + x - w / 2) * scale)
            top = round((120 - y - (h if bottom else h / 2)) * scale)
            if left < 0 or top < 0 or left + layer.shape[1] > canvas.shape[1] or top + layer.shape[0] > canvas.shape[0]:
                raise ValueError('Composition layer exceeds canvas; unsupported layout')
            target = canvas[top:top + layer.shape[0], left:left + layer.shape[1]]
            target[:] = layer + target * (1.0 - layer[:, :, 3:4])

        bg_name = f'card_bg_np{color}' if card_type == 'np' else f'card_bg_{color}'
        background = self._read(shared + bg_name + '.png', expected=(140, 177) if card_type == 'np' else (133, 170), region=region)
        add(background, (133, 170), (0, 0))
        portrait = self._read(f'{sid}/card_servant_{variant}.png', expected=(256, 256), region=region)
        add(portrait, (191, 191), (0, 6))
        icon = self._read(shared + f'card_icon_{color}.png', expected=(185, 133), region=region)
        add(icon, (186, 134), (4, -42))
        if card_type == 'face':
            label = self._read(shared + f'card_txt_{color}.png', expected=(150, 73), region=region, localized_only=region == 'CN')
            # CN retains the saved UISprite dimensions; JP uses the separately
            # screenshot-verified native-size label profile.
            add(label, (126, 66) if region == 'CN' else (150, 73), (4, -46.997))
        else:
            names = self._read(f'_composition/np/{sid}/{name_file}', expected=(256, 256), region=region, localized_only=region != 'JP')
            label = names[row * 128:(row + 1) * 128]
            if cv2.countNonZero(np.where(label[:, :, 3] >= 128, 255, 0).astype(np.uint8)) < 32:
                raise ValueError(f'Empty NP name row {row} for {region}/{sid}/{name_file}; no name substitution is allowed')
            add(label, (179, 89), (0, -79.2), bottom=True)
        alpha = canvas[:, :, 3:4]
        colors = np.divide(canvas[:, :, :3], alpha, out=np.zeros_like(canvas[:, :, :3]), where=alpha > 0)
        rgba = np.concatenate((colors, alpha), axis=2)
        bgra = np.rint(np.clip(rgba * 255.0, 0, 255)).astype(np.uint8)
        mask = np.where(bgra[:, :, 3] >= 128, 255, 0).astype(np.uint8)
        x, y, w, h = cv2.boundingRect(mask)
        if not w or not h:
            raise ValueError('Composition has no opaque pixels')
        return ComposedCard(bgra[y:y + h, x:x + w].copy(), mask[y:y + h, x:x + w].copy(), (128 * scale - x, 120 * scale - y))
