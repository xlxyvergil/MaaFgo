"""固定编队槽的低分辨率批量候选检索；候选分数不能作为确定身份。

只缓存静态模板特征，每次调用使用显式图像，不保存截图/槽位身份。
最终身份仍由调用方的原尺寸模板匹配及不同 ID 分差决定。
"""
from collections import defaultdict
from dataclasses import dataclass

import cv2
import numpy as np


FEATURE_SIZE = (20, 20)
SEARCH_STRIDE = 6
CANDIDATES_PER_VIEW = 24
MIN_COARSE_SCORE = 0.50
BOUNDARY_MARGIN = 0.08


def _feature(image):
    if image.size == 0:
        return None
    vector = cv2.resize(image, FEATURE_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32).reshape(-1)
    vector -= vector.mean()
    length = float(np.linalg.norm(vector))
    return vector / length if length > 1e-6 else None


def _positions(limit):
    # 模板宽度接近槽宽时也包含中心点，容忍 1px 的边框差异。
    return sorted({0, limit, limit // 2, *range(0, limit + 1, SEARCH_STRIDE)})


@dataclass(frozen=True)
class CandidatePool:
    ids: tuple[str, ...]
    fallback: bool


class FormationCandidateIndex:
    def __init__(self, templates, crop=()):
        self.ids = tuple(templates)
        groups = defaultdict(list)
        for owner, sid in enumerate(self.ids):
            for _name, template in templates[sid]:
                height, width = template.shape[:2]
                boxes = [(False, 0, 0, width, height)]
                if crop:
                    left, top, right, bottom = crop
                    boxes.append((True, round(left * width), round(top * height),
                                  round(right * width), round(bottom * height)))
                for cropped, left, top, right, bottom in boxes:
                    vector = _feature(template[top:bottom, left:right])
                    if vector is not None:
                        groups[(cropped, height, width, left, top, right, bottom)].append((owner, vector))
        self.groups = []
        for layout, rows in groups.items():
            owners = np.asarray([owner for owner, _ in rows], dtype=np.intp)
            matrix = np.stack([vector for _, vector in rows])
            matrix.setflags(write=False)
            self.groups.append((layout, owners, matrix))

    def shortlists(self, image, rois):
        scores = np.full((2, len(rois), len(self.ids)), -1.0, dtype=np.float32)
        for (cropped, height, width, left, top, right, bottom), owners, matrix in self.groups:
            queries, slots = [], []
            for slot, (x, y, roi_width, roi_height) in enumerate(rois):
                if width > roi_width or height > roi_height:
                    continue
                # 只在固定槽中滑动。局部特征跟随完整模板的合法位置，
                # 而不是把头像局部拿到礼装/技能等任意位置搜索。
                for dy in _positions(roi_height - height):
                    for dx in _positions(roi_width - width):
                        patch = image[y + dy + top:y + dy + bottom, x + dx + left:x + dx + right]
                        if patch.shape[:2] != (bottom - top, right - left):
                            continue
                        vector = _feature(patch)
                        if vector is not None:
                            queries.append(vector)
                            slots.append(slot)
            for start in range(0, len(queries), 128):
                current_slots = np.asarray(slots[start:start + 128])
                values = matrix @ np.stack(queries[start:start + 128]).T
                for slot in np.unique(current_slots):
                    best = values[:, current_slots == slot].max(axis=1)
                    np.maximum.at(scores[int(cropped), slot], owners, best)
        pools = []
        for slot in range(len(rois)):
            chosen, fallback = set(), not bool(self.groups)
            for view in scores[:, slot]:
                order = np.argsort(-view, kind="stable")
                valid = order[view[order] > -1]
                if not len(valid):
                    continue
                chosen.update(int(index) for index in valid[:CANDIDATES_PER_VIEW])
                # 粗筛太弱或候选边界拥挤时不能以有限候选冒充全库分差。
                if (view[valid[0]] < MIN_COARSE_SCORE
                        or (len(valid) > CANDIDATES_PER_VIEW
                            and view[valid[0]] - view[valid[CANDIDATES_PER_VIEW]] < BOUNDARY_MARGIN)):
                    fallback = True
            if len(chosen) < min(2, len(self.ids)):
                fallback = True
            pools.append(CandidatePool(tuple(sid for i, sid in enumerate(self.ids) if i in chosen), fallback))
        return tuple(pools)
