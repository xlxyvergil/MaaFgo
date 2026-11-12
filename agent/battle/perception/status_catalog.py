"""Reusable, screenshot-only search of the battle HUD ServantStatus catalog.

The caller decides when a stable battle-main screenshot is available. This module
does not control a device, change a battle roster, or retain identities between
frames. Reuse one recognizer to amortize static template/index preparation.

Fast mode retrieves a small catalog shortlist with masked 20x20 features, then
checks the original cropped templates. Its margin is only a shortlist margin;
``full_audit=True`` explicitly compares every catalog identity instead.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import math
import time

import cv2
import numpy as np

from ..data.servant_identity import servant_template_ids
from .frontline import _status_roots


BASE_SIZE = (1280, 720)
ANALYSIS_SCALE = 0.5
STATUS_ROIS = tuple((28 + 318 * index, 427, 279, 88) for index in range(3))
TEMPLATE_CROP = (16, 55, 240, 130)  # left, top, right, bottom in a 256x256 asset
TEMPLATE_SCALES = (1.1125, 1.125, 1.1375)
FEATURE_SIZE = (20, 20)
SEARCH_STRIDE = 4
CANDIDATES_PER_SLOT = 24
MIN_COARSE_SCORE = 0.50
COARSE_BOUNDARY_MARGIN = 0.08


@dataclass(frozen=True)
class StatusCatalogMatch:
    slot: int
    servant_id: str | None
    best_candidate_id: str | None
    best_score: float
    runner_up_id: str | None
    runner_up_score: float
    margin: float
    margin_scope: str
    matched_template: str
    reason: str
    candidate_count: int
    roi: tuple[int, int, int, int]


@dataclass(frozen=True)
class StatusCatalogResult:
    slots: tuple[StatusCatalogMatch, ...]
    elapsed_seconds: float
    coarse_seconds: float
    full_audit: bool
    image_size: tuple[int, int]


def _feature(image):
    return cv2.resize(image, FEATURE_SIZE, interpolation=cv2.INTER_AREA).astype(
        np.float32
    ).reshape(-1)


def _positions(limit):
    return sorted({0, limit, limit // 2, *range(0, limit + 1, SEARCH_STRIDE)})


def _readonly(array):
    array.setflags(write=False)
    return array


def _threshold(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是 0 至 1 之间的数值，请修改识别调用参数后重试。")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} 超出 0 至 1 的范围，请修改识别调用参数后重试。")
    return value


class StatusCatalogRecognizer:
    """Search a complete ServantStatus library without a known formation.

    ``roots`` contains ServantStatus directories whose numeric children contain
    256x256 RGBA PNG assets. Screenshots are uint8 BGR/BGRA arrays (or provide
    ``to_numpy()``), in landscape 16:9. Returned ROIs always use 1280x720 units.
    The class deliberately does not change OpenCV/BLAS process-wide threading.
    """

    def __init__(self, roots=None, *, min_score=0.90, min_margin=0.05):
        started = time.perf_counter()
        self.min_score = _threshold(min_score, "最低匹配分数")
        self.min_margin = _threshold(min_margin, "最低身份分差")
        if roots is None:
            roots = _status_roots()
        elif isinstance(roots, (str, Path)):
            roots = (roots,)
        self.roots = tuple(dict.fromkeys(Path(root).resolve() for root in roots))
        if not self.roots:
            raise ValueError(
                "没有找到 ServantStatus 资源，无法识别战斗头像。"
                "请使用完整资源包，或通过 --status-root 指定 ServantStatus 目录后重试。"
            )
        for root in self.roots:
            if not root.is_dir():
                raise ValueError(
                    f"ServantStatus 目录不存在：{root}，无法加载头像。"
                    "请检查 --status-root 是否直接指向 ServantStatus，而不是上一级 image 目录。"
                )

        templates = defaultdict(list)
        seen = set()
        directory_ids = {}
        source_count = 0
        for root in self.roots:
            for path in sorted(root.glob("*/*.png")):
                # This helper reads only the local catalog and applies explicit
                # aliases before old duplicate records. canonical_servant_id
                # would import the wider Chaldea package (and its GUI/network
                # dependencies), which a screenshot-only utility does not need.
                if path.parent.name not in directory_ids:
                    identities = servant_template_ids(path.parent.name)
                    directory_ids[path.parent.name] = identities[0] if identities else None
                sid = directory_ids[path.parent.name]
                if sid is None or path.resolve() in seen:
                    continue
                seen.add(path.resolve())
                try:
                    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
                except (OSError, cv2.error) as exc:
                    raise self._bad_template(path) from exc
                if image is None or image.shape != (256, 256, 4):
                    raise self._bad_template(path)
                left, top, right, bottom = TEMPLATE_CROP
                crop = image[top:bottom, left:right]
                mask = np.where(crop[:, :, 3] >= 128, 255, 0).astype(np.uint8)
                if cv2.countNonZero(mask) < 100:
                    raise self._bad_template(path)
                name = f"{path.parent.name}/{path.name}"
                for scale in TEMPLATE_SCALES:
                    size = (round(crop.shape[1] * scale * ANALYSIS_SCALE),
                            round(crop.shape[0] * scale * ANALYSIS_SCALE))
                    pixels = cv2.resize(crop[:, :, :3], size, interpolation=cv2.INTER_CUBIC)
                    scaled_mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
                    templates[sid].append((_readonly(pixels), _readonly(scaled_mask), name))
                source_count += 1
        if not templates:
            raise ValueError(
                "ServantStatus 目录中没有可用的头像模板，无法开始识别。"
                "请检查 --status-root 是否直接指向 ServantStatus，"
                "并补齐数字从者目录下的 256×256 RGBA PNG 资源后重试。"
            )
        self._ids = tuple(sorted(templates))
        self._templates = {sid: tuple(templates[sid]) for sid in self._ids}
        self._groups = self._build_index()
        if not self._groups:
            raise ValueError(
                "头像模板没有可区分的图像特征，无法建立识别索引。"
                "请检查 ServantStatus 资源是否损坏或被替换为空白图片后重试。"
            )
        self.index_stats = {
            "identity_count": len(self._ids),
            "template_count": source_count,
            "prepared_template_count": source_count * len(TEMPLATE_SCALES),
            "index_group_count": len(self._groups),
            "build_seconds": time.perf_counter() - started,
        }

    @staticmethod
    def _bad_template(path):
        return ValueError(
            f"头像模板无法读取或格式不正确：{path}，无法完成资源加载。"
            "请将该文件恢复为原始 256×256 RGBA PNG 资源后重试。"
        )

    def _build_index(self):
        groups = defaultdict(list)
        for owner, sid in enumerate(self._ids):
            for image, mask, _ in self._templates[sid]:
                values = _feature(image)
                weights = cv2.resize(mask, FEATURE_SIZE, interpolation=cv2.INTER_NEAREST)
                weights = np.repeat((weights.reshape(-1) > 0).astype(np.float32), 3)
                count = weights.sum()
                if count < 100:
                    continue
                centered = (values - (weights * values).sum() / count) * weights
                length = float(np.linalg.norm(centered))
                if length < 1e-6:
                    continue
                groups[image.shape[:2]].append((owner, centered / length, weights, count))
        return tuple((shape,
                      _readonly(np.asarray([row[0] for row in rows], dtype=np.intp)),
                      _readonly(np.stack([row[1] for row in rows])),
                      _readonly(np.stack([row[2] for row in rows])),
                      _readonly(np.asarray([row[3] for row in rows], np.float32)))
                     for shape, rows in groups.items())

    @staticmethod
    def _normalize(image):
        if hasattr(image, "to_numpy"):
            image = image.to_numpy()
        array = np.asarray(image)
        if (array.ndim != 3 or array.shape[2] not in (3, 4) or array.dtype != np.uint8
                or not array.shape[0] or not array.shape[1]):
            raise ValueError(
                "截图不是可用的 BGR/BGRA 图像，无法读取战斗头像。"
                "请传入完整游戏截图的 uint8 三通道或四通道数组后重试。"
            )
        height, width = array.shape[:2]
        # Permit sub-pixel/one-pixel capture rounding without accepting a
        # noticeably different aspect ratio at small resolutions.
        if abs(width * 9 - height * 16) / (height * 16) > 0.001:
            raise ValueError(
                f"截图比例不是横屏 16:9（当前 {width}×{height}），无法定位头像区域。"
                "请使用完整的横屏 16:9 游戏截图，不要先裁剪截图后再识别。"
            )
        frame = array[:, :, :3]
        if (width, height) != BASE_SIZE:
            frame = cv2.resize(frame, BASE_SIZE, interpolation=cv2.INTER_AREA)
        return cv2.resize(frame, None, fx=ANALYSIS_SCALE, fy=ANALYSIS_SCALE,
                          interpolation=cv2.INTER_AREA), (width, height)

    @staticmethod
    def _regions(frame):
        return tuple(frame[round(y * ANALYSIS_SCALE):round((y + height) * ANALYSIS_SCALE),
                           round(x * ANALYSIS_SCALE):round((x + width) * ANALYSIS_SCALE)]
                     for x, y, width, height in STATUS_ROIS)

    def _shortlists(self, regions):
        scores = np.full((3, len(self._ids)), -1.0, np.float32)
        for (height, width), owners, centered, weights, counts in self._groups:
            queries, slots = [], []
            for slot, region in enumerate(regions):
                if height > region.shape[0] or width > region.shape[1]:
                    continue
                for y in _positions(region.shape[0] - height):
                    for x in _positions(region.shape[1] - width):
                        queries.append(_feature(region[y:y + height, x:x + width]))
                        slots.append(slot)
            for offset in range(0, len(queries), 128):
                query = np.stack(queries[offset:offset + 128]).T
                sums = weights @ query
                variance = np.maximum(weights @ (query * query)
                                      - sums * sums / counts[:, None], 0)
                values = (centered @ query) / np.sqrt(np.maximum(variance, 1e-12))
                values[variance < 1e-6] = -1
                current_slots = np.asarray(slots[offset:offset + 128])
                for slot in np.unique(current_slots):
                    best = values[:, current_slots == slot].max(axis=1)
                    np.maximum.at(scores[slot], owners, best)
        pools = []
        for view in scores:
            order = np.argsort(-view, kind="stable")
            valid = order[view[order] > -1]
            chosen = valid[:CANDIDATES_PER_SLOT]
            weak = not len(valid) or float(view[valid[0]]) < MIN_COARSE_SCORE
            if len(valid) > CANDIDATES_PER_SLOT:
                weak = weak or (float(view[valid[0]] - view[valid[CANDIDATES_PER_SLOT]])
                                < COARSE_BOUNDARY_MARGIN)
            weak = weak or len(chosen) < min(2, len(self._ids))
            pools.append((tuple(self._ids[index] for index in chosen), bool(weak)))
        return tuple(pools)

    def _rank(self, region, candidates):
        ranked = []
        for sid in candidates:
            best_score, best_name = 0.0, ""
            for pixels, mask, name in self._templates[sid]:
                if pixels.shape[0] > region.shape[0] or pixels.shape[1] > region.shape[1]:
                    continue
                response = cv2.matchTemplate(region, pixels, cv2.TM_CCOEFF_NORMED, mask=mask)
                response = np.nan_to_num(response, nan=-1.0, posinf=-1.0, neginf=-1.0)
                score = float(cv2.minMaxLoc(response)[1])
                if score > best_score:
                    best_score, best_name = score, name
            ranked.append((best_score, sid, best_name))
        return sorted(ranked, key=lambda row: (-row[0], row[1]))

    def detect(self, image, *, full_audit=False):
        """Return fresh per-slot identities; weak/ambiguous results stay unknown.

        ``full_audit`` is an explicit slow path that certifies the best and runner
        among all loaded identities. Fast mode never silently starts that scan.
        ``elapsed_seconds`` excludes construction of the reusable static index.
        """
        started = time.perf_counter()
        frame, image_size = self._normalize(image)
        regions = self._regions(frame)
        coarse_started = time.perf_counter()
        pools = (tuple((self._ids, False) for _ in regions) if full_audit
                 else self._shortlists(regions))
        coarse_seconds = 0.0 if full_audit else time.perf_counter() - coarse_started
        matches = []
        for index, (region, (candidates, weak)) in enumerate(zip(regions, pools)):
            ranked = self._rank(region, candidates)
            best = ranked[0] if ranked else (0.0, None, "")
            runner = ranked[1] if len(ranked) > 1 else (0.0, None, "")
            margin = float(best[0] - runner[0])
            if weak:
                reason = "coarse_unreliable"
            elif not ranked:
                reason = "no_candidates"
            elif not best[2]:
                reason = "no_template_evidence"
            elif best[0] < self.min_score:
                reason = "score_below_threshold"
            elif margin < self.min_margin:
                reason = "margin_too_small"
            else:
                reason = "identified"
            matches.append(StatusCatalogMatch(
                slot=index + 1, servant_id=best[1] if reason == "identified" else None,
                best_candidate_id=best[1], best_score=float(best[0]),
                runner_up_id=runner[1], runner_up_score=float(runner[0]), margin=margin,
                margin_scope="catalog" if full_audit else "shortlist",
                matched_template=best[2], reason=reason, candidate_count=len(candidates),
                roi=STATUS_ROIS[index],
            ))
        return StatusCatalogResult(tuple(matches), time.perf_counter() - started,
                                   coarse_seconds, bool(full_audit), image_size)
