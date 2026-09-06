# -*- coding: utf-8 -*-
"""构建玩家本地从者/概念礼装库。

本 Action 只浏览“灵基一览”仓库，不选择卡片，也不保存或修改队伍。识别成功且确认
滚动到列表底部后，才会原子替换 ``config/Inventory`` 中对应的 JSON 文件。
"""

from __future__ import annotations

import html
import json
import os
import tempfile
import time
import traceback
from datetime import datetime

import cv2
import numpy as np

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from formation_action import (
    BASE_H,
    BASE_W,
    AutoFormationFromChaldea,
    _read_image,
)
import mfaalog


_CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_CUSTOM_DIR)
_PROJECT_DIR = os.path.dirname(_AGENT_DIR)

OUTPUT_DIR = os.path.join(_PROJECT_DIR, "config", "Inventory")
SERVANT_OUTPUT = os.path.join(OUTPUT_DIR, "player_servants.json")
EQUIP_OUTPUT = os.path.join(OUTPUT_DIR, "player_equips.json")

LIST_ROI = (70, 165, 1160, 530)
INVENTORY_TAB_ROIS = {
    "servant": (15, 98, 167, 61),
    "equip": (202, 98, 168, 61),
}
ACTIVE_TAB_ORANGE_RATIO = 0.35
MATCH_STABILITY_SECONDS = 0.35
MATCH_CENTER_DELTA = 6
BOTTOM_STABLE_ROUNDS = 3
CONTENT_STABLE_ROUNDS = 3
CONTENT_DIFF_THRESHOLD = 0.012

# 从者小图标：仅在内存中裁剪模板，不改动原图。
SERVANT_FACE_X = (83, 270, 458, 646, 834, 1022)
SERVANT_FACE_SIZE = 158
SERVANT_CARD_WIDTH = 164
SERVANT_OVERVIEW_SCALE = 1.04
SERVANT_Y_OFFSETS = (0, 3, 6)
SERVANT_FEATURE_REGION = (44, 44, 158, 101)
SERVANT_FEATURE_SIZE = (48, 38)
SERVANT_THRESHOLD = 0.70
SERVANT_MARGIN = 0.12
SERVANT_FOOTER_OFFSET = 163

# EquipFaces/list 是礼装列表原图。灵基一览“大图标”布局中，模板左缘相对
# 卡片金框内缩约 14px；实机直接匹配 f_94020900.png 为 0.9804。此前沿用
# 礼装选择页的列起点而整体右偏 14px，会让所有礼装都落到错误裁剪区域。
# 快速特征只用于从 1000+ 张模板中筛出唯一候选；最终入库前仍会拿完整的
# 147x56 list 原图在卡面邻域直接执行 TM_CCOEFF_NORMED。这样既保留滚动后
# 1–4px 位置浮动，又不会把 team 模板或变形后的图片当作最终判断依据。
EQUIP_FACE_X = (97, 284, 472, 659, 847, 1034)
EQUIP_TEMPLATE_CUT_TOP = 20
EQUIP_FEATURE_SIZE = (64, 18)
EQUIP_THRESHOLD = 0.88
EQUIP_MARGIN = 0.15
EQUIP_BAR_TO_TEMPLATE_TOP = 131
EQUIP_FEATURE_MIN_Y = 160
EQUIP_DIRECT_SEARCH_RADIUS = 6
EQUIP_DIRECT_THRESHOLD = 0.88

# 上限按“持有目录接近全收集”计算；通常会由到底检测大幅提前结束。
DEFAULT_MAX_SERVANT_SWIPES = 55
DEFAULT_MAX_EQUIP_SWIPES = 200


def _truthy(value) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "否", "none"}


def _runs(values):
    result = []
    for value in values:
        value = int(value)
        if not result or value > result[-1][-1] + 1:
            result.append([value])
        else:
            result[-1].append(value)
    return result


def _cluster_positions(values, tolerance=8):
    groups = []
    for value in sorted(int(item) for item in values):
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [int(round(float(np.median(group)))) for group in groups]


def _normalized_vector(image, size):
    if image is None or image.size == 0:
        return None
    vector = cv2.resize(image, size).astype(np.float32).reshape(-1)
    vector -= vector.mean()
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


@AgentServer.custom_action("build_player_inventory")
class BuildPlayerInventory(AutoFormationFromChaldea):
    """扫描玩家持有对象，并生成两份可供后续自动编队复用的 JSON。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        self.context = context
        self.controller = context.tasker.controller
        self.current_list = None
        self.opened_overview = False
        ok = False
        try:
            node = context.get_node_data(argv.node_name) or {}
            attach = node.get("attach") or {}
            self.scan_servants = _truthy(attach.get("scan_servants", True))
            self.scan_equips = _truthy(attach.get("scan_equips", True))
            self.bond_equips_only = _truthy(attach.get("bond_equips_only", False))
            self.max_servant_swipes = self._positive_int(
                attach.get("max_servant_swipes"), DEFAULT_MAX_SERVANT_SWIPES
            )
            self.max_equip_swipes = self._positive_int(
                attach.get("max_equip_swipes"), DEFAULT_MAX_EQUIP_SWIPES
            )
            if not self.scan_servants and not self.scan_equips:
                return self._result(False, "没有选择要构建的库存类型")

            self._init_paths()
            self._init_scale()
            if not self._in_inventory_overview():
                return self._result(False, "未进入灵基一览仓库")
            self.opened_overview = True

            completed = []
            if self.scan_servants:
                self._focus("正在构建从者库：加载识图资源")
                document = self._build_servant_inventory()
                if document is None:
                    return self._result(False, "从者库扫描未完整到达列表底部，旧文件未改动")
                self._atomic_write_json(SERVANT_OUTPUT, document)
                completed.append(f"从者 {document['count']} 名")
                self._focus(f"从者库已写入：{document['count']} 名", "green")

            if self.scan_equips:
                self._focus("正在构建礼装库：加载识图资源")
                document = self._build_equip_inventory()
                if document is None:
                    return self._result(False, "礼装库扫描未完整到达列表底部，旧文件未改动")
                self._atomic_write_json(EQUIP_OUTPUT, document)
                completed.append(f"礼装 {document['count']} 张")
                self._focus(f"礼装库已写入：{document['count']} 张", "green")

            ok = True
            self._focus("个人库存构建完成：" + "，".join(completed), "green")
            mfaalog.info("[个人库存] 构建完成：" + "，".join(completed))
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            mfaalog.error(f"[个人库存] inventory_build_failed: {exc}\n{traceback.format_exc()}")
            self._focus("个人库存构建失败，旧 JSON 已保留", "red")
            return CustomAction.RunResult(success=False)
        finally:
            # 只关闭灵基一览，不会点击任何卡片或改动库存。
            self._restore_ui()
            if not ok:
                mfaalog.warning("[个人库存] 本次未完整成功；已有有效 JSON 不会被覆盖")

    @staticmethod
    def _positive_int(value, default):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _active_inventory_tab(self, image=None):
        image = self._shot() if image is None else image
        if image is None:
            return None
        ratios = {}
        for name, roi in INVENTORY_TAB_ROIS.items():
            x, y, width, height = self._scale_roi(roi)
            region = image[y:y + height, x:x + width]
            if region.size == 0:
                ratios[name] = 0.0
                continue
            hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
            orange = cv2.inRange(hsv, (5, 80, 80), (30, 255, 255))
            ratios[name] = float(np.count_nonzero(orange)) / orange.size
        active = max(ratios, key=ratios.get)
        if ratios[active] < ACTIVE_TAB_ORANGE_RATIO:
            return None
        return active

    def _in_inventory_overview(self):
        return self._active_inventory_tab() is not None

    # ---------- 从者 ----------

    def _build_servant_inventory(self):
        catalog = self._load_catalog("servant_list.json", "servants")
        matrix, variants, covered, missing = self._prepare_servant_features(catalog)
        if matrix is None:
            raise RuntimeError("servant_templates_unavailable")
        if not self._enter_servant_list():
            return None
        if not self._run_pipeline("个人库存-从者筛选重置"):
            return None
        if not self._run_pipeline("个人库存-准备从者列表"):
            return None
        if not self._run_pipeline("个人库存-从者列表复位顶部"):
            return None

        observed, metrics = self._scan_pages(
            "从者",
            catalog,
            matrix,
            variants,
            self._stable_servant_hits,
            "个人库存-从者列表下滑",
            self.max_servant_swipes,
        )
        if observed is None:
            return None
        return self._make_document(
            "servants", "servants", catalog, observed, covered, missing, metrics
        )

    def _prepare_servant_features(self, catalog):
        vectors, variants, covered, missing = [], [], 0, []
        for item in catalog:
            found = False
            loaded_names = set()
            for name in item.get("images") or []:
                template = self._read_first_template(self.face_dirs, name)
                if template is None:
                    continue
                if template.shape[:2] != (SERVANT_FACE_SIZE, SERVANT_FACE_SIZE):
                    template = cv2.resize(template, (SERVANT_FACE_SIZE, SERVANT_FACE_SIZE))
                x1, y1, x2, y2 = SERVANT_FEATURE_REGION
                vector = _normalized_vector(template[y1:y2, x1:x2], SERVANT_FEATURE_SIZE)
                if vector is not None:
                    vectors.append(vector)
                    variants.append((str(item["id"]), name))
                    loaded_names.add(name)
                    found = True
            # Atlas 增量条目可能已补图但尚未回填 images 数组；按从者 ID 的
            # 既有文件命名规则补充发现，确保“目录元数据缺 images”不会被误报
            # 成真正缺少识图资源。
            if not found:
                for name, template in self._load_servant_templates(item["id"], self.face_dirs):
                    if name in loaded_names:
                        continue
                    if template.shape[:2] != (SERVANT_FACE_SIZE, SERVANT_FACE_SIZE):
                        template = cv2.resize(template, (SERVANT_FACE_SIZE, SERVANT_FACE_SIZE))
                    x1, y1, x2, y2 = SERVANT_FEATURE_REGION
                    vector = _normalized_vector(template[y1:y2, x1:x2], SERVANT_FEATURE_SIZE)
                    if vector is not None:
                        vectors.append(vector)
                        variants.append((str(item["id"]), name))
                        loaded_names.add(name)
                        found = True
            if found:
                covered += 1
            else:
                missing.append(str(item.get("id")))
        return (np.stack(vectors) if vectors else None), variants, covered, missing

    def _visible_servant_features(self, image):
        base = self._to_base(image)
        if base is None:
            return [], []
        hsv = cv2.cvtColor(base, cv2.COLOR_BGR2HSV)
        origins = []
        for base_x in SERVANT_FACE_X:
            # “灵基一览”大图标底部的 Servant/Grand Servant 条横跨卡片。
            # Grand 从者使用蓝色底条，因此必须与金/银/铜边框一并检测。
            area = hsv[:, base_x:base_x + SERVANT_CARD_WIDTH]
            hue, saturation, value = area[:, :, 0], area[:, :, 1], area[:, :, 2]
            silver = (saturation < 90) & (value > 90)
            gold = (
                (hue >= 15) & (hue <= 48) &
                (saturation >= 60) & (value >= 70)
            )
            bronze = (
                (hue <= 20) & (saturation >= 50) & (value >= 50)
            )
            grand = (
                (hue >= 80) & (hue <= 115) &
                (saturation >= 80) & (value >= 70)
            )
            # 三种边框色分别找连续行；若先做并集，彩色卡面也可能被拼成
            # 一段“近乎满宽”的伪底条。
            for footer_mask in (silver, gold, bronze, grand):
                ratios = np.mean(footer_mask, axis=1)
                for run in _runs(np.where(ratios > 0.88)[0]):
                    # 仓库实测底条连续高同色区约为 14–24px；为兼容描边断点
                    # 适度放宽，但仍排除卡面中的大块同色区域。
                    if not 12 <= len(run) <= 30:
                        continue
                    base_y = run[0] - SERVANT_FOOTER_OFFSET
                    # 顶部第一行允许被标题栏遮住约 25px；用于匹配的内部头像
                    # 区域仍完整可见，不能把仓库顶部对象整体丢掉。
                    if 121 <= base_y and base_y + SERVANT_FACE_SIZE <= BASE_H:
                        origins.append((base_y, base_x))
        groups = []
        for value in sorted(origins):
            if not groups or value[0] - groups[-1][-1][0] > 24:
                groups.append([value])
            else:
                groups[-1].append(value)
        # 同一底条可能被相邻色罩切成两段，以组内最靠上的候选作为真实卡片
        # 起点；至少两列共同出现才视为一行，排除单张卡面里的伪横条。
        y_origins = [
            min(item[0] for item in group)
            for group in groups
            if len({item[1] for item in group}) >= 2
        ]

        features, centers = [], []
        for base_y in y_origins:
            # 只要该行至少一张卡的黄色底条可见，就评估整行。这样已装备遮罩
            # 不会漏卡，最后一行不足六张时多出的空位则会被高阈值自然排除。
            for base_x in SERVANT_FACE_X:
                x1, y1, x2, y2 = SERVANT_FEATURE_REGION
                center = self._scaled_center(
                    base_x, base_y, SERVANT_CARD_WIDTH, SERVANT_CARD_WIDTH
                )
                for offset_y in SERVANT_Y_OFFSETS:
                    feature = base[
                        base_y + offset_y + round(y1 * SERVANT_OVERVIEW_SCALE):
                        base_y + offset_y + round(y2 * SERVANT_OVERVIEW_SCALE),
                        base_x + round(x1 * SERVANT_OVERVIEW_SCALE):
                        base_x + round(x2 * SERVANT_OVERVIEW_SCALE),
                    ]
                    vector = _normalized_vector(feature, SERVANT_FEATURE_SIZE)
                    if vector is not None:
                        features.append(vector)
                        centers.append(center)
        return features, centers

    def _stable_servant_hits(self, catalog, matrix, variants):
        return self._stable_hits(
            catalog, matrix, variants, self._visible_servant_features,
            SERVANT_THRESHOLD, SERVANT_MARGIN,
        )

    def _enter_servant_list(self):
        if not self._run_pipeline("个人库存-切换从者仓库"):
            return False
        if not self._wait_for(lambda: self._active_inventory_tab() == "servant", 6.0):
            return False
        self.current_list = "servant"
        return True

    # ---------- 礼装 ----------

    def _build_equip_inventory(self):
        catalog = self._load_catalog("equip_list.json", "equips")
        matrix, variants, covered, missing = self._prepare_equip_features(catalog)
        if matrix is None:
            raise RuntimeError("equip_templates_unavailable")
        if not self._enter_equip_list():
            return None
        # 两种模式都从“重置筛选”开始，避免继承玩家上次使用的筛选条件。
        # 仅羁绊模式不再额外限制星级，以筛选页的“羁绊效果”结果为准。
        filter_node = (
            "个人库存-礼装筛选仅羁绊"
            if self.bond_equips_only else
            "个人库存-礼装筛选四五星"
        )
        if not self._run_pipeline(filter_node):
            return None
        if not self._run_pipeline("个人库存-准备礼装列表"):
            return None
        if not self._run_pipeline("个人库存-礼装列表复位顶部"):
            return None

        observed, metrics = self._scan_pages(
            "礼装",
            catalog,
            matrix,
            variants,
            self._stable_equip_hits,
            "个人库存-礼装列表下滑",
            self.max_equip_swipes,
        )
        if observed is None:
            return None
        return self._make_document(
            "equips", "equips", catalog, observed, covered, missing, metrics
        )

    def _prepare_equip_features(self, catalog):
        self.equip_full_templates = {}
        vectors, variants, covered, missing = [], [], 0, []
        for item in catalog:
            name = f"f_{item['id']}0.png"
            template = self._read_first_template(self.equip_list_dirs, name)
            if template is None or template.shape[0] <= EQUIP_TEMPLATE_CUT_TOP:
                missing.append(str(item.get("id")))
                continue
            vector = _normalized_vector(template[EQUIP_TEMPLATE_CUT_TOP:], EQUIP_FEATURE_SIZE)
            if vector is None:
                missing.append(str(item.get("id")))
                continue
            vectors.append(vector)
            variants.append((str(item["id"]), name))
            self.equip_full_templates[name] = template
            covered += 1
        return (np.stack(vectors) if vectors else None), variants, covered, missing

    def _visible_equip_features(self, image):
        base = self._to_base(image)
        if base is None:
            return [], []
        hsv = cv2.cvtColor(base, cv2.COLOR_BGR2HSV)
        gold = cv2.inRange(hsv, (18, 80, 80), (45, 255, 255))
        bar_starts = []
        for base_x in EQUIP_FACE_X:
            # 头像条从卡片左侧内缩 15px；金色 Craft Essence 条覆盖整张卡宽。
            card_left = max(0, base_x - 15)
            ratios = np.mean(gold[:, card_left:card_left + 167] > 0, axis=1)
            rows = np.where(ratios > 0.55)[0]
            for run in _runs(rows):
                if len(run) < 12 or float(ratios[run].max()) < 0.75:
                    continue
                bar_starts.append(run[0])
        features, centers = [], []
        for bar_start in _cluster_positions(bar_starts):
            for base_x in EQUIP_FACE_X:
                template_top = bar_start - EQUIP_BAR_TO_TEMPLATE_TOP
                feature_y = template_top + EQUIP_TEMPLATE_CUT_TOP
                height = 56 - EQUIP_TEMPLATE_CUT_TOP
                # 顶部第一行在列表复位后是完整卡片，必须纳入；旧下限 345 会让
                # 第一行永远在首次下滑前被跳过。160 仍排除了页签/筛选栏，并允许
                # 下滑后只露出模板底条的卡片由完整 list 原图做最终复核。
                if feature_y < EQUIP_FEATURE_MIN_Y or feature_y + height > BASE_H:
                    continue
                crop = base[feature_y:feature_y + height, base_x:base_x + 147]
                if crop.shape[:2] != (height, 147):
                    continue
                vector = _normalized_vector(crop, EQUIP_FEATURE_SIZE)
                if vector is not None:
                    features.append(vector)
                    centers.append(self._scaled_center(base_x, feature_y, 147, height))
        return features, centers

    def _stable_equip_hits(self, catalog, matrix, variants):
        hits, image = self._stable_hits(
            catalog, matrix, variants, self._visible_equip_features,
            EQUIP_THRESHOLD, EQUIP_MARGIN,
        )
        return self._direct_equip_hits(hits, image), image

    def _direct_equip_hits(self, hits, image):
        """用完整 ``EquipFaces/list`` 原图复核快速候选。

        列表停止后的纵向落点会有少量像素浮动，所以在快速候选的预期位置
        周围搜索，而不是把模板缩放到固定截图块。返回分数是原图直接匹配
        分数；未通过该步骤的候选不会写入玩家礼装库。
        """
        base = self._to_base(image)
        if base is None:
            return {}
        refined = {}
        radius = EQUIP_DIRECT_SEARCH_RADIUS
        for item_id, (_coarse_score, center, template_name) in hits.items():
            template = self.equip_full_templates.get(template_name)
            if template is None:
                continue
            template_height, template_width = template.shape[:2]
            center_x = center[0] / self.sx
            center_y = center[1] / self.sy
            expected_x = int(round(center_x - template_width / 2))
            feature_height = 56 - EQUIP_TEMPLATE_CUT_TOP
            expected_y = int(round(
                center_y - feature_height / 2 - EQUIP_TEMPLATE_CUT_TOP
            ))
            left = max(0, expected_x - radius)
            top = max(0, expected_y - radius)
            right = min(BASE_W, expected_x + template_width + radius)
            bottom = min(BASE_H, expected_y + template_height + radius)
            region = base[top:bottom, left:right]
            if (
                region.shape[0] < template_height or
                region.shape[1] < template_width
            ):
                continue
            result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            _minimum, score, _minimum_location, location = cv2.minMaxLoc(result)
            if not np.isfinite(score) or score < EQUIP_DIRECT_THRESHOLD:
                continue
            match_x = left + location[0]
            match_y = top + location[1]
            match_center = self._scaled_center(
                match_x, match_y, template_width, template_height
            )
            refined[item_id] = (float(score), match_center, template_name)
        return refined

    def _enter_equip_list(self):
        if not self._run_pipeline("个人库存-切换礼装仓库"):
            return False
        if not self._wait_for(lambda: self._active_inventory_tab() == "equip", 6.0):
            return False
        self.current_list = "equip"
        return True

    # ---------- 批量匹配、终止判定与输出 ----------

    def _stable_hits(self, catalog, matrix, variants, extractor, threshold, margin):
        # 控制器截图作业是异步的，``_shot`` 为避免底层状态查询挂起会直接读取
        # cached_image。列表刚滑动后缓存可能仍慢一帧，因此先主动触发一帧并
        # 丢弃，再用后两帧做稳定校验；否则会错误比较“上一屏 vs 当前屏”。
        self._shot()
        time.sleep(MATCH_STABILITY_SECONDS)
        first_image = self._shot()
        time.sleep(MATCH_STABILITY_SECONDS)
        second_image = self._shot()
        first = self._match_cells(first_image, matrix, variants, extractor, threshold, margin)
        second = self._match_cells(second_image, matrix, variants, extractor, threshold, margin)
        stable = {}
        for item_id, one in first.items():
            two = second.get(item_id)
            if two is None:
                continue
            delta = max(abs(one[1][0] - two[1][0]), abs(one[1][1] - two[1][1]))
            if delta <= MATCH_CENTER_DELTA:
                stable[item_id] = two
        if not stable and (first or second):
            mfaalog.warning(
                f"[个人库存] 双帧未形成稳定命中："
                f"前帧={len(first)}，后帧={len(second)}，交集={len(set(first) & set(second))}"
            )
        return stable, second_image

    @staticmethod
    def _match_cells(image, matrix, variants, extractor, threshold, margin):
        card_features, centers = extractor(image)
        if not card_features:
            return {}
        scores = matrix @ np.stack(card_features).T
        best_by_center = {}
        for card_index, center in enumerate(centers):
            best_by_id = {}
            for variant_index, (item_id, template_name) in enumerate(variants):
                score = float(scores[variant_index, card_index])
                previous = best_by_id.get(item_id)
                if previous is None or score > previous[0]:
                    best_by_id[item_id] = (score, template_name)
            ranked = sorted(best_by_id.items(), key=lambda item: item[1][0], reverse=True)
            if not ranked:
                continue
            item_id, (score, template_name) = ranked[0]
            second_score = ranked[1][1][0] if len(ranked) > 1 else -1.0
            if score >= threshold and score - second_score >= margin:
                old = best_by_center.get(center)
                if old is None or score > old[1][0]:
                    best_by_center[center] = (
                        item_id, (score, center, template_name)
                    )
        hits = {}
        for item_id, hit in best_by_center.values():
            old = hits.get(item_id)
            if old is None or hit[0] > old[0]:
                hits[item_id] = hit
        return hits

    def _scan_pages(self, label, catalog, matrix, variants, matcher, swipe_node, max_swipes):
        observed = {}
        previous_ids = None
        previous_thumb = None
        previous_content = None
        unchanged = 0
        content_unchanged = 0
        unmatched_cells = 0
        swipes = 0
        for page_index in range(max_swipes + 1):
            if self.context.tasker.stopping:
                return None, None
            hits, image = matcher(catalog, matrix, variants)
            if image is None:
                return None, None
            if page_index == 0 and not hits:
                # 回顶后首帧偶尔仍是列表过渡画面。先在原位重拍一次，绝不先
                # 下滑，确保顶部对象不会因为加载延迟而漏记。
                time.sleep(0.8)
                hits, image = matcher(catalog, matrix, variants)
                if image is None:
                    return None, None
            for item_id, hit in hits.items():
                old = observed.get(item_id)
                if old is None or hit[0] > old[0]:
                    observed[item_id] = hit

            if label == "从者":
                _visible_features, visible_centers = self._visible_servant_features(image)
                visible_count = len(set(visible_centers))
            else:
                visible_count = len(self._visible_equip_features(image)[0])
            unmatched_cells += max(0, visible_count - len(hits))
            mfaalog.info(
                f"[个人库存] {label}第{page_index + 1}屏："
                f"可见={visible_count}，命中={len(hits)}，累计={len(observed)}"
            )
            self._focus(f"正在扫描{label}：已识别 {len(observed)}")

            current_ids = tuple(sorted(hits))
            current_thumb = self._scroll_thumb_center(image)
            current_content = self._content_signature(image)
            content_diff = self._content_difference(previous_content, current_content)
            stable_content = content_diff is not None and content_diff <= CONTENT_DIFF_THRESHOLD
            content_unchanged = content_unchanged + 1 if stable_content else 0
            stable_page = (
                current_thumb is not None and previous_thumb is not None and
                abs(current_thumb - previous_thumb) <= 1 and
                (
                    current_thumb >= 650 or
                    (bool(current_ids) and current_ids == previous_ids)
                )
            )
            unchanged = unchanged + 1 if stable_page else 0
            scrollbar_bottom = unchanged >= BOTTOM_STABLE_ROUNDS
            content_bottom = content_unchanged >= CONTENT_STABLE_ROUNDS
            if scrollbar_bottom or content_bottom:
                bottom_detection = "scrollbar" if scrollbar_bottom else "content"
                metrics = {
                    "pages_scanned": page_index + 1,
                    "swipes": swipes,
                    "unmatched_candidate_cells": unmatched_cells,
                    "bottom_confirmed": True,
                    "bottom_detection": bottom_detection,
                }
                mfaalog.info(
                    f"[个人库存] {label}已确认到达列表底部："
                    f"判定={bottom_detection}，滚动条={current_thumb}，"
                    f"滚动条稳定={unchanged}，画面稳定={content_unchanged}，"
                    f"画面差异={content_diff}"
                )
                if not self._leave_current_list():
                    mfaalog.error(f"[个人库存] {label}扫描完成，但仓库页状态异常")
                    return None, None
                return observed, metrics
            previous_ids = current_ids
            previous_thumb = current_thumb
            previous_content = current_content
            if page_index >= max_swipes:
                mfaalog.error(
                    f"[个人库存] {label}达到安全上限 {max_swipes} 次下滑，"
                    "仍未确认列表底部"
                )
                self._leave_current_list()
                return None, None
            if not self._run_pipeline(swipe_node):
                self._leave_current_list()
                return None, None
            swipes += 1
        return None, None

    @staticmethod
    def _content_signature(image):
        """提取静态列表主体，供滑动后画面变化兜底判定使用。"""
        if image is None or image.shape[0] < 650 or image.shape[1] < 1140:
            return None
        # 避开顶部筛选栏、右侧滚动条和底部按钮，只比较卡片主体。
        roi = image[155:645, 70:1110]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (104, 49), interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(small, (5, 5), 0)

    @staticmethod
    def _content_difference(previous, current):
        if previous is None or current is None or previous.shape != current.shape:
            return None
        return round(float(cv2.absdiff(previous, current).mean()) / 255.0, 6)

    def _make_document(self, kind, list_key, catalog, observed, covered, missing, metrics):
        by_id = {str(item.get("id")): item for item in catalog}
        entries = []
        for item_id, hit in observed.items():
            source = by_id.get(item_id)
            if source is None:
                continue
            if kind == "servants":
                entry = {
                    "id": item_id,
                    "collection_no": source.get("collection_no"),
                    "name": source.get("name", ""),
                    "class": source.get("class", ""),
                    "rarity": source.get("rarity"),
                    "bond": source.get("bond", {}),
                    "matched_template": hit[2],
                    "match_score": round(float(hit[0]), 4),
                }
            else:
                entry = {
                    "id": item_id,
                    "collection_no": source.get("collection_no"),
                    "name": source.get("name", ""),
                    "rarity": source.get("rarity"),
                    "filter_tag": source.get("filter_tag", ""),
                    "bond": source.get("bond", {}),
                    "limit_break": None,
                    "limit_break_known": False,
                    "matched_template": hit[2],
                    "match_score": round(float(hit[0]), 4),
                }
            entries.append(entry)
        entries.sort(key=lambda item: (item.get("collection_no") is None, item.get("collection_no") or 0, item["id"]))
        return {
            "schema_version": 1,
            "kind": kind,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "scan_complete": True,
            "catalog_complete": not missing,
            "catalog_count": len(catalog),
            "template_covered_count": covered,
            "missing_template_ids": missing,
            "count": len(entries),
            "scan": metrics,
            list_key: entries,
        }

    @staticmethod
    def _atomic_write_json(path, document):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                json.dump(document, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _load_catalog(self, filename, key):
        path = os.path.join(_CUSTOM_DIR, filename)
        with open(path, encoding="utf-8-sig") as file:
            value = json.load(file).get(key)
        if not isinstance(value, list):
            raise RuntimeError(f"invalid_catalog: {filename}/{key}")
        return value

    @staticmethod
    def _read_first_template(directories, filename):
        for directory in directories:
            image = _read_image(os.path.join(directory, filename))
            if image is not None:
                return image
        return None

    @staticmethod
    def _to_base(image):
        if image is None:
            return None
        if image.shape[:2] == (BASE_H, BASE_W):
            return image
        return cv2.resize(image, (BASE_W, BASE_H))

    def _scaled_center(self, x, y, width, height):
        return (
            int(round((x + width / 2) * self.sx)),
            int(round((y + height / 2) * self.sy)),
        )

    def _page_crop(self, image):
        x, y, width, height = self._scale_roi(LIST_ROI)
        crop = image[y:y + height, x:x + width]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (232, 106))

    def _scroll_thumb_center(self, image):
        """返回右侧滚动条白色滑块的纵向中心（基准分辨率坐标）。"""
        base = self._to_base(image)
        if base is None:
            return None
        roi_y, roi_x = 165, 1210
        hsv = cv2.cvtColor(base[roi_y:715, roi_x:1275], cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 1] < 80) & (hsv[:, :, 2] > 180)).astype(np.uint8)
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
        candidates = []
        for x, y, width, height, area in stats[1:]:
            if 18 <= width <= 30 and 30 <= height <= 50 and area >= 300:
                candidates.append((int(area), int(round(roi_y + y + height / 2))))
        return max(candidates)[1] if candidates else None

    def _leave_current_list(self):
        if self.current_list is None:
            return True
        if not self._in_inventory_overview():
            return False
        self.current_list = None
        return True

    def _restore_ui(self):
        try:
            self._leave_current_list()
            if self.opened_overview and self._in_inventory_overview():
                self._run_pipeline("个人库存-关闭灵基一览")
                self._wait_for(lambda: not self._in_inventory_overview(), 6.0)
            self.opened_overview = False
        except Exception as exc:
            mfaalog.error(f"[个人库存] 关闭灵基一览失败: {exc}")

    def _focus(self, message, color=None):
        safe = html.escape(str(message), quote=True)
        if color:
            safe = f'<span style="color: {color};">{safe}</span>'
        try:
            self.context.override_pipeline({
                "个人库存-用户提示": {"focus": {"Node.Recognition.Starting": safe}}
            })
            self.context.run_task("个人库存-用户提示")
        except Exception as exc:
            mfaalog.warning(f"[个人库存] 用户提示输出失败: {exc}")

    def _result(self, success, message):
        mfaalog.info(f"[个人库存] {message}") if success else mfaalog.error(f"[个人库存] {message}")
        self._focus(message, "green" if success else "red")
        return CustomAction.RunResult(success=success)
