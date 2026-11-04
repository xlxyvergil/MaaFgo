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
from platform_waits import platform_timeout


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
SCROLL_THUMB_WIDTH_RANGE = (18, 30)
# 1280x720 小图标列表中的滑块高度随列表长度变化；实机从者列表曾达到
# 117px，短列表则可能接近整条轨道。高度上限过小会误判没有滚动条。
SCROLL_THUMB_HEIGHT_RANGE = (30, 550)
SCROLL_BOTTOM_CENTER_Y = 650
SCROLL_STABLE_DELTA = 1
INVENTORY_ICON_LAYOUT_NODES = {
    "servant": "个人库存-从者图标大小检测",
    "equip": "个人库存-礼装图标大小检测",
}
ICON_LAYOUT_TRANSITION_TIMEOUT_SECONDS = platform_timeout(2.0, 8.0)
ICON_LAYOUT_RECOVERY_TIMEOUT_SECONDS = platform_timeout(2.5, 10.0)

# “构建个人从者礼装库”独占小图标布局：8 列，卡片纵向节距约 149px。
# 其他任务仍使用各自的大/中图标参数，不能复用这里的坐标与缩放。
INVENTORY_ICON_SCALE = 0.75
INVENTORY_CARD_X = (83, 223, 362, 502, 642, 781, 921, 1061)
INVENTORY_CARD_WIDTH = 123
INVENTORY_ROW_PITCH = 149
RARITY_ANCHOR_TEMPLATE = "rarity1_0.png"
RARITY_SEARCH_TOP = 165
RARITY_SEARCH_BOTTOM = 708
RARITY_TO_CARD_TOP = 101
RARITY_THRESHOLD = 0.90
RARITY_MIN_COLUMNS = 4
BOND_EQUIP_FIRST_STAR_Y = 291
BOND_EQUIP_ROW_TOLERANCE = 8
INVENTORY_GRID_MAX_CANDIDATES = 6
INVENTORY_GRID_PHASE_SEPARATION = 8

# 从者身份模板在内存中按 0.75 倍 Bilinear 缩小。完整行优先使用更高的
# 眼部/面部特征区；页面底部第 4 行只露出上半张卡时，再使用短裁剪区。
SERVANT_FACE_X = INVENTORY_CARD_X
SERVANT_FACE_SIZE = 118
SERVANT_CARD_WIDTH = INVENTORY_CARD_WIDTH
SERVANT_OVERVIEW_SCALE = 1.04
SERVANT_Y_OFFSETS = (-3, 0, 3)
SERVANT_FEATURE_REGIONS = (
    (33, 33, 118, 76),
    (33, 33, 118, 64),
)
SERVANT_FEATURE_SIZE = (48, 38)
SERVANT_THRESHOLD = 0.50
SERVANT_MARGIN = 0.06

# EquipFaces/list 是礼装列表原图。小图标布局中先把 147x56 模板按 0.75 倍
# Bilinear 缩为 110x42；模板左缘相对卡片金框内缩约 11px。
# 快速特征只用于从 1000+ 张模板中筛出唯一候选；最终入库前仍会拿完整的
# 110x42 小图模板在卡面邻域直接执行 TM_CCOEFF_NORMED。这样既保留滚动后
# 1–4px 位置浮动，又不会把 team 模板或变形后的图片当作最终判断依据。
EQUIP_FACE_X = tuple(value + 11 for value in INVENTORY_CARD_X)
EQUIP_TEMPLATE_SIZE = (110, 42)
EQUIP_TEMPLATE_TOP_OFFSET = 24
EQUIP_TEMPLATE_CUT_TOP = 15
EQUIP_FEATURE_SIZE = (64, 18)
EQUIP_THRESHOLD = 0.85
EQUIP_MARGIN = 0.15
EQUIP_DIRECT_SEARCH_RADIUS = 5
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
        self.current_scan_recoveries = 0
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
            self.rarity_dirs = [
                os.path.join(root, "个人库存", "稀有度") for root in self.image_roots
            ]
            self._load_rarity_anchor()
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

    def _current_icon_layout_active(self, image=None):
        """用本任务的小图标按钮模板确认库存列表与布局都已恢复。"""
        node_name = INVENTORY_ICON_LAYOUT_NODES.get(self.current_list)
        if node_name is None:
            return False
        image = self._shot() if image is None else image
        if image is None:
            return False
        try:
            detail = self.context.run_recognition(node_name, image)
        except Exception as exc:
            mfaalog.warning(
                f"[个人库存] 小图标布局识别异常（{node_name}）: {exc}"
            )
            return False
        return bool(detail and detail.hit)

    def _ensure_current_icon_layout(self):
        """等待列表稳定；若误入卡片详情，则返回并重新确认小图标按钮。"""
        if self.current_list not in INVENTORY_ICON_LAYOUT_NODES:
            return False

        # 正常翻页后也可能仍在 1–2 秒的页面动画/截图缓存期。先等待模板出现，
        # 不因单帧漏识别立刻按返回，以免从正常仓库误退到编成菜单。
        if self._confirmed_now(self._current_icon_layout_active):
            return True
        if self._wait_for(
            self._current_icon_layout_active,
            ICON_LAYOUT_TRANSITION_TIMEOUT_SECONDS,
        ):
            return True

        mfaalog.warning(
            "[个人库存] 当前屏未检测到本任务的小图标按钮，"
            "按 Esc 尝试从卡片详情返回列表"
        )
        if not self._run_pipeline("个人库存-详情返回列表"):
            return False
        if not self._wait_for(
            self._current_icon_layout_active,
            ICON_LAYOUT_RECOVERY_TIMEOUT_SECONDS,
        ):
            mfaalog.error(
                "[个人库存] 返回后仍未检测到本任务的小图标按钮，停止扫描以保留旧文件"
            )
            return False
        self.current_scan_recoveries += 1
        mfaalog.info(
            f"[个人库存] 已从卡片详情恢复列表，累计 {self.current_scan_recoveries} 次"
        )
        return True

    def _load_rarity_anchor(self):
        """加载小图标行列定位用的单星绿幕模板。"""
        template = self._read_first_template(
            self.rarity_dirs, RARITY_ANCHOR_TEMPLATE
        )
        if template is None:
            raise RuntimeError("rarity_anchor_unavailable")
        green = (
            (template[:, :, 1] >= 250) &
            (template[:, :, 0] <= 5) &
            (template[:, :, 2] <= 5)
        )
        mask = (~green).astype(np.uint8) * 255
        if np.count_nonzero(mask) < 8:
            raise RuntimeError("rarity_anchor_mask_empty")
        self.rarity_anchor_template = template
        self.rarity_anchor_mask = mask

    def _small_icon_row_origins(self, image):
        """用星级图标求 8 列小图标布局的 149px 行相位。

        ``TM_CCORR_NORMED`` 对加色渲染的星级条比默认 CCOEFF 稳定，但也会
        产生亮色误候选。因此不接受单点命中，而是在 8 个固定卡片列中分别
        计算纵向分数，再选择能形成至少两行、间距固定为 149px 的整页相位。
        最后按相位外推底部星级条尚未露出的第 4 行。
        """
        candidates = self._small_icon_row_candidates(image)
        if not candidates:
            mfaalog.warning("[个人库存] 星级网格未形成至少两行稳定相位")
            return []
        # 保留仅靠星级条的兼容入口；正式从者/礼装扫描会按身份命中复核相位。
        best = max(
            candidates,
            key=lambda candidate: (
                len(candidate["rows"]),
                candidate["support"],
                candidate["score_sum"],
            ),
        )
        self.last_rarity_grid = best["diagnostic"]
        return best["origins"]

    def _small_icon_row_candidates(self, image):
        """枚举星级网格候选；避免仅凭疑似多一行就锁定错误相位。"""
        base = self._to_base(image)
        if base is None:
            return []
        template = self.rarity_anchor_template
        mask = self.rarity_anchor_mask
        template_height, template_width = template.shape[:2]
        curves = []
        for base_x in INVENTORY_CARD_X:
            region = base[
                RARITY_SEARCH_TOP:RARITY_SEARCH_BOTTOM,
                base_x:base_x + INVENTORY_CARD_WIDTH,
            ]
            if (
                region.shape[0] < template_height or
                region.shape[1] < template_width
            ):
                return []
            result = cv2.matchTemplate(
                region, template, cv2.TM_CCORR_NORMED, mask=mask
            )
            result = np.nan_to_num(
                result, nan=-1.0, posinf=-1.0, neginf=-1.0
            )
            curves.append(result.max(axis=1))

        last_star_y = RARITY_SEARCH_BOTTOM - template_height
        candidates = []
        for phase in range(INVENTORY_ROW_PITCH):
            first_star_y = RARITY_SEARCH_TOP + (
                (phase - RARITY_SEARCH_TOP) % INVENTORY_ROW_PITCH
            )
            rows = []
            for star_y in range(
                first_star_y, last_star_y + 1, INVENTORY_ROW_PITCH
            ):
                index = star_y - RARITY_SEARCH_TOP
                scores = [float(curve[index]) for curve in curves]
                support = sum(score >= RARITY_THRESHOLD for score in scores)
                if support >= RARITY_MIN_COLUMNS:
                    rows.append((star_y, support, float(np.mean(scores))))
            if len(rows) < 2:
                continue
            card_phase = (rows[0][0] - RARITY_TO_CARD_TOP) % INVENTORY_ROW_PITCH
            origins = list(range(card_phase, BASE_H, INVENTORY_ROW_PITCH))
            candidates.append({
                "phase": card_phase,
                "rows": rows,
                "support": sum(row[1] for row in rows),
                "score_sum": sum(row[2] for row in rows),
                "origins": origins,
                "diagnostic": {
                    "phase": phase,
                    "detected_rows": [row[0] for row in rows],
                    "supports": [row[1] for row in rows],
                    "means": [round(row[2], 4) for row in rows],
                    "card_origins": origins,
                },
            })
        return candidates

    # ---------- 从者 ----------

    def _build_servant_inventory(self):
        full_catalog = self._load_catalog("servant_list.json", "servants")
        # 个人库存只能生成有正式图鉴号的可持有从者。运行目录还保留少量
        # collection_no 为空的内部变体/NPC（供其他任务兼容），不能把它们
        # 当成独立身份候选，否则共享 face 会与其所属从者形成 0 分差。
        catalog = [
            item for item in full_catalog
            if item.get("collection_no") is not None
        ]
        excluded = len(full_catalog) - len(catalog)
        if excluded:
            mfaalog.info(
                f"[个人库存] 已排除 {excluded} 个无正式图鉴号的内部变体/NPC"
            )
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
                for x1, y1, x2, y2 in SERVANT_FEATURE_REGIONS:
                    vector = _normalized_vector(
                        template[y1:y2, x1:x2], SERVANT_FEATURE_SIZE
                    )
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
                    for x1, y1, x2, y2 in SERVANT_FEATURE_REGIONS:
                        vector = _normalized_vector(
                            template[y1:y2, x1:x2], SERVANT_FEATURE_SIZE
                        )
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

    def _visible_servant_features(self, image, row_origins=None):
        base = self._to_base(image)
        if base is None:
            return [], []
        features, centers = [], []
        if row_origins is None:
            row_origins = self._small_icon_row_origins(base)
        for base_y in row_origins:
            for base_x in SERVANT_FACE_X:
                center = self._scaled_center(
                    base_x, base_y, SERVANT_CARD_WIDTH, SERVANT_CARD_WIDTH
                )
                for x1, y1, x2, y2 in SERVANT_FEATURE_REGIONS:
                    for offset_y in SERVANT_Y_OFFSETS:
                        top = base_y + offset_y + round(y1 * SERVANT_OVERVIEW_SCALE)
                        bottom = base_y + offset_y + round(y2 * SERVANT_OVERVIEW_SCALE)
                        left = base_x + round(x1 * SERVANT_OVERVIEW_SCALE)
                        right = base_x + round(x2 * SERVANT_OVERVIEW_SCALE)
                        if (
                            top < RARITY_SEARCH_TOP or
                            bottom > RARITY_SEARCH_BOTTOM or
                            left < 0 or right > BASE_W
                        ):
                            continue
                        feature = base[top:bottom, left:right]
                        vector = _normalized_vector(feature, SERVANT_FEATURE_SIZE)
                        if vector is not None:
                            features.append(vector)
                            centers.append(center)
        return features, centers

    def _stable_servant_hits(self, catalog, matrix, variants):
        return self._stable_hits(
            catalog, matrix, variants, self._visible_servant_features,
            SERVANT_THRESHOLD, SERVANT_MARGIN,
            match_image=lambda image: self._match_servant_image(image, matrix, variants),
        )

    def _match_servant_image(self, image, matrix, variants):
        candidates = self._small_icon_row_candidates(image)
        if not candidates:
            mfaalog.warning("[个人库存] 从者页星级网格未形成至少两行稳定相位")
            return {}

        # 星级总支持数优先，但相近像素相位只取最强代表，避免把预算
        # 浪费在同一行位置的 1px 抖动上。最终仍由真实头像的唯一命中数决定。
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                candidate["support"],
                candidate["score_sum"] / len(candidate["rows"]),
                len(candidate["rows"]),
            ),
            reverse=True,
        )
        distinct = []
        for candidate in ranked:
            if all(
                min(
                    abs(candidate["phase"] - selected["phase"]),
                    INVENTORY_ROW_PITCH - abs(candidate["phase"] - selected["phase"]),
                ) >= INVENTORY_GRID_PHASE_SEPARATION
                for selected in distinct
            ):
                distinct.append(candidate)
            if len(distinct) >= INVENTORY_GRID_MAX_CANDIDATES:
                break

        evaluated = []
        for candidate in distinct:
            origins = candidate["origins"]
            hits = self._match_cells(
                image, matrix, variants,
                lambda frame, origins=origins: self._visible_servant_features(frame, origins),
                SERVANT_THRESHOLD, SERVANT_MARGIN,
            )
            evaluated.append((candidate, hits))
        selected, hits = max(
            evaluated,
            key=lambda entry: (
                len(entry[1]),
                sum(hit[0] for hit in entry[1].values()),
                entry[0]["support"],
            ),
        )
        self.last_rarity_grid = selected["diagnostic"]
        mfaalog.info(
            "[个人库存] 从者网格相位："
            + "，".join(
                f"{candidate['phase']}({len(candidate['rows'])}行/"
                f"{candidate['support']}列支持/{len(candidate_hits)}命中)"
                for candidate, candidate_hits in evaluated
            )
            + f"；选择={selected['phase']}"
        )
        return hits

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
            found = False
            # 自定义礼装的多张外观仍是同一个库存身份；普通礼装保留旧文件名回退。
            names = dict.fromkeys([
                *(item.get("images") or []), f"f_{item['id']}0.png"
            ])
            for name in names:
                template = self._read_first_template(self.equip_list_dirs, name)
                if template is None:
                    continue
                if template.shape[:2] != (EQUIP_TEMPLATE_SIZE[1], EQUIP_TEMPLATE_SIZE[0]):
                    template = cv2.resize(
                        template, EQUIP_TEMPLATE_SIZE, interpolation=cv2.INTER_LINEAR
                    )
                if template.shape[0] <= EQUIP_TEMPLATE_CUT_TOP:
                    continue
                vector = _normalized_vector(template[EQUIP_TEMPLATE_CUT_TOP:], EQUIP_FEATURE_SIZE)
                if vector is None:
                    continue
                vectors.append(vector)
                variants.append((str(item["id"]), name))
                self.equip_full_templates[name] = template
                found = True
            if not found:
                missing.append(str(item.get("id")))
            else:
                covered += 1
        return (np.stack(vectors) if vectors else None), variants, covered, missing

    def _visible_equip_features(self, image, row_origins=None):
        base = self._to_base(image)
        if base is None:
            return [], []
        features, centers = [], []
        template_width, template_height = EQUIP_TEMPLATE_SIZE
        feature_height = template_height - EQUIP_TEMPLATE_CUT_TOP
        if row_origins is None:
            row_origins = self._small_icon_row_origins(base)
        for base_y in row_origins:
            for base_x in EQUIP_FACE_X:
                template_top = base_y + EQUIP_TEMPLATE_TOP_OFFSET
                feature_y = template_top + EQUIP_TEMPLATE_CUT_TOP
                if (
                    feature_y < RARITY_SEARCH_TOP or
                    feature_y + feature_height > RARITY_SEARCH_BOTTOM
                ):
                    continue
                crop = base[
                    feature_y:feature_y + feature_height,
                    base_x:base_x + template_width,
                ]
                if crop.shape[:2] != (feature_height, template_width):
                    continue
                vector = _normalized_vector(crop, EQUIP_FEATURE_SIZE)
                if vector is not None:
                    features.append(vector)
                    centers.append(self._scaled_center(
                        base_x, feature_y, template_width, feature_height
                    ))
        return features, centers

    def _stable_equip_hits(self, catalog, matrix, variants):
        grids = []

        def match_image(frame):
            hits = self._match_equip_image(frame, matrix, variants)
            grids.append(getattr(self, "last_rarity_grid", None) or {})
            return hits

        hits, image = self._stable_hits(
            catalog, matrix, variants, self._visible_equip_features,
            EQUIP_THRESHOLD, EQUIP_MARGIN,
            match_image=match_image,
        )
        self.stable_equip_layout = len(grids) == 2 and all(
            grid.get("card_origins") for grid in grids
        ) and grids[0]["card_origins"] == grids[1]["card_origins"]
        self.stable_short_bond_equips = self.stable_equip_layout and all(
            grid.get("short_bond_equips", False) for grid in grids
        )
        return hits, image

    def _short_bond_equip_candidate(self, image):
        """用完整四星条确认顶部一至三行羁绊礼装，不向空白外推行。"""
        if not getattr(self, "bond_equips_only", False):
            return None
        base = self._to_base(image)
        if base is None:
            return None
        template = self._read_first_template(self.rarity_dirs, "rarity4_0.png")
        if template is None:
            return None
        green = (
            (template[:, :, 1] >= 250) &
            (template[:, :, 0] <= 5) &
            (template[:, :, 2] <= 5)
        )
        mask = (~green).astype(np.uint8) * 255
        curves = []
        for base_x in INVENTORY_CARD_X:
            result = cv2.matchTemplate(
                base[RARITY_SEARCH_TOP:RARITY_SEARCH_BOTTOM,
                     base_x:base_x + INVENTORY_CARD_WIDTH],
                template, cv2.TM_CCORR_NORMED, mask=mask,
            )
            curves.append(np.nan_to_num(
                result, nan=-1.0, posinf=-1.0, neginf=-1.0
            ).max(axis=1))
        curves = np.stack(curves)
        rows = []
        for row_index in range(3):
            expected = BOND_EQUIP_FIRST_STAR_Y + row_index * INVENTORY_ROW_PITCH
            first = expected - BOND_EQUIP_ROW_TOLERANCE
            last = expected + BOND_EQUIP_ROW_TOLERANCE
            scores = curves[:, first - RARITY_SEARCH_TOP:last - RARITY_SEARCH_TOP + 1]
            supports = (scores >= RARITY_THRESHOLD).sum(axis=0)
            best = max(range(scores.shape[1]), key=lambda i: (supports[i], scores[:, i].sum()))
            support = int(supports[best])
            if support == 0:
                break
            # 出现下一行时，上一行须有足够列支持；允许个别星级被标记遮挡。
            if rows and rows[-1][1] < RARITY_MIN_COLUMNS:
                return None
            rows.append((first + best, support, float(scores[:, best].mean())))
        if not rows:
            return None
        # 卡片上的“装备中”等标记可能在星级条附近形成假峰，只检查末行
        # 半个行距之外是否仍有下一行，避免把这些同一卡片内的峰当作新行。
        lower_start = rows[-1][0] + INVENTORY_ROW_PITCH // 2 - RARITY_SEARCH_TOP
        if np.any(curves[:, lower_start:] >= RARITY_THRESHOLD):
            return None
        origins = [row[0] - RARITY_TO_CARD_TOP for row in rows]
        return {
            "phase": origins[0] % INVENTORY_ROW_PITCH,
            "rows": rows,
            "support": sum(row[1] for row in rows),
            "score_sum": sum(row[2] for row in rows),
            "origins": origins,
            "diagnostic": {
                "detected_rows": [row[0] for row in rows],
                "supports": [row[1] for row in rows],
                "means": [round(row[2], 4) for row in rows],
                "card_origins": origins,
                "short_bond_equips": True,
            },
        }

    def _match_equip_image(self, image, matrix, variants):
        self.last_rarity_grid = None
        candidates = self._small_icon_row_candidates(image)
        short_list = self._short_bond_equip_candidate(image)
        if short_list is not None:
            candidates.append(short_list)
        if not candidates:
            mfaalog.warning("[个人库存] 礼装页未形成稳定多行网格或羁绊短列表")
            return {}

        ranked = sorted(
            candidates,
            key=lambda candidate: (
                candidate["diagnostic"].get("short_bond_equips", False),
                candidate["support"],
                candidate["score_sum"] / len(candidate["rows"]),
                len(candidate["rows"]),
            ),
            reverse=True,
        )
        distinct = []
        for candidate in ranked:
            if all(
                min(
                    abs(candidate["phase"] - selected["phase"]),
                    INVENTORY_ROW_PITCH - abs(candidate["phase"] - selected["phase"]),
                ) >= INVENTORY_GRID_PHASE_SEPARATION
                for selected in distinct
            ):
                distinct.append(candidate)
            if len(distinct) >= INVENTORY_GRID_MAX_CANDIDATES:
                break

        evaluated = []
        for candidate in distinct:
            origins = candidate["origins"]
            coarse = self._match_cells(
                image, matrix, variants,
                lambda frame, origins=origins: self._visible_equip_features(frame, origins),
                EQUIP_THRESHOLD, EQUIP_MARGIN,
            )
            direct = self._direct_equip_hits(coarse, image)
            evaluated.append((candidate, coarse, direct))
        selected, _coarse, hits = max(
            evaluated,
            key=lambda entry: (
                len(entry[2]),
                sum(hit[0] for hit in entry[2].values()),
                len(entry[1]),
                entry[0]["support"],
            ),
        )
        self.last_rarity_grid = selected["diagnostic"]
        mfaalog.info(
            "[个人库存] 礼装网格相位："
            + "，".join(
                f"{candidate['phase']}({len(candidate['rows'])}行/"
                f"{candidate['support']}列支持/{len(coarse)}初筛/"
                f"{len(direct)}复核)"
                for candidate, coarse, direct in evaluated
            )
            + f"；选择={selected['phase']}"
        )
        return hits

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
            feature_height = template_height - EQUIP_TEMPLATE_CUT_TOP
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

    def _stable_hits(self, catalog, matrix, variants, extractor, threshold, margin,
                     match_image=None):
        # 控制器截图作业是异步的，``_shot`` 为避免底层状态查询挂起会直接读取
        # cached_image。列表刚滑动后缓存可能仍慢一帧，因此先主动触发一帧并
        # 丢弃，再用后两帧做稳定校验；否则会错误比较“上一屏 vs 当前屏”。
        self._shot()
        time.sleep(MATCH_STABILITY_SECONDS)
        first_image = self._shot()
        time.sleep(MATCH_STABILITY_SECONDS)
        second_image = self._shot()
        if match_image is None:
            match_image = lambda frame: self._match_cells(
                frame, matrix, variants, extractor, threshold, margin
            )
        first = match_image(first_image)
        second = match_image(second_image)
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
        previous_thumb = None
        previous_content = None
        unchanged = 0
        content_unchanged = 0
        unmatched_cells = 0
        swipes = 0
        short_list_scan = label == "礼装" and getattr(self, "bond_equips_only", False)
        zero_hit_pages = 0
        self.current_scan_recoveries = 0
        for page_index in range(max_swipes + 1):
            if self.context.tasker.stopping:
                return None, None
            # 必须先命中“从者/礼装图标大小检测”使用的小图标按钮模板。
            # 详情页没有该按钮，因此异常画面不会进入身份匹配、累计结果或到底判断。
            if not self._ensure_current_icon_layout():
                mfaalog.error(f"[个人库存] {label}列表布局确认失败")
                return None, None
            hits, image = matcher(catalog, matrix, variants)
            if image is None:
                return None, None
            if not hits:
                # 先排除截图延迟；礼装身份缺图不应阻断有独立证据的到底检查。
                time.sleep(0.8)
                hits, image = matcher(catalog, matrix, variants)
                if image is None:
                    return None, None
                if not hits:
                    thumb = self._scroll_thumb_center(image)
                    if label != "礼装" or not (
                        getattr(self, "stable_equip_layout", False) or
                        self._scroll_at_bottom(image, thumb)
                    ):
                        mfaalog.error(
                            f"[个人库存] {label}第{page_index + 1}屏重复零命中，"
                            "缺少可靠的识别或列表完成证据，保留旧库存"
                        )
                        return None, None
                    zero_hit_pages += 1
                    mfaalog.warning(
                        f"[个人库存] 礼装第{page_index + 1}屏重试后仍零命中，"
                        "继续检查列表底部；未知礼装不会入库"
                    )
            for item_id, hit in hits.items():
                old = observed.get(item_id)
                if old is None or hit[0] > old[0]:
                    observed[item_id] = hit

            if label == "从者":
                selected_rows = (getattr(self, "last_rarity_grid", None) or {}).get(
                    "card_origins"
                )
                _visible_features, visible_centers = self._visible_servant_features(
                    image, selected_rows
                )
                visible_count = len(set(visible_centers))
            else:
                selected_rows = (getattr(self, "last_rarity_grid", None) or {}).get(
                    "card_origins"
                )
                visible_count = len(self._visible_equip_features(image, selected_rows)[0])
            unmatched_cells += max(0, visible_count - len(hits))
            mfaalog.info(
                f"[个人库存] {label}第{page_index + 1}屏："
                f"可见={visible_count}，命中={len(hits)}，累计={len(observed)}"
            )
            self._focus(f"正在扫描{label}：已识别 {len(observed)}")

            current_thumb = self._scroll_thumb_center(image)
            short_list_scan = (
                short_list_scan and current_thumb is None and
                getattr(self, "stable_short_bond_equips", False)
            )
            current_content = self._content_signature(image)
            content_diff = self._content_difference(previous_content, current_content)
            stable_content = content_diff is not None and content_diff <= CONTENT_DIFF_THRESHOLD
            content_unchanged = content_unchanged + 1 if stable_content else 0
            stable_page = (
                current_thumb is not None and previous_thumb is not None and
                abs(current_thumb - previous_thumb) <= SCROLL_STABLE_DELTA and
                self._scroll_at_bottom(image, current_thumb) and stable_content
            )
            unchanged = unchanged + 1 if stable_page else 0
            scrollbar_bottom = unchanged >= BOTTOM_STABLE_ROUNDS
            short_list_bottom = short_list_scan and content_unchanged >= CONTENT_STABLE_ROUNDS
            if scrollbar_bottom or short_list_bottom:
                if not observed:
                    mfaalog.error(f"[个人库存] {label}已到底，但全程无可靠身份命中，保留旧库存")
                    return None, None
                bottom_detection = "scrollbar" if scrollbar_bottom else "short_bond_list"
                metrics = {
                    "pages_scanned": page_index + 1,
                    "swipes": swipes,
                    "unmatched_candidate_cells": unmatched_cells,
                    "zero_hit_pages": zero_hit_pages,
                    "detail_recoveries": self.current_scan_recoveries,
                    "bottom_confirmed": True,
                    "bottom_detection": bottom_detection,
                    "recognition_valid": True,
                }
                mfaalog.info(
                    f"[个人库存] {label}已确认到达列表底部："
                    f"判定={bottom_detection}，滚动条={current_thumb}，"
                    f"滚动条稳定={unchanged}，画面稳定={content_unchanged}，"
                    f"画面差异={content_diff}"
                )
                if zero_hit_pages:
                    mfaalog.warning(
                        f"[个人库存] 礼装已到底，保存 {len(observed)} 种已识别礼装；"
                        f"{zero_hit_pages} 屏零命中，未识别礼装不代表未持有，请补齐模板后重新构建"
                    )
                if not self._leave_current_list():
                    mfaalog.error(f"[个人库存] {label}扫描完成，但仓库页状态异常")
                    return None, None
                return observed, metrics
            if content_unchanged >= CONTENT_STABLE_ROUNDS:
                mfaalog.error(
                    f"[个人库存] {label}列表连续静止，但没有可靠的底部滚动条证据："
                    f"滚动条={current_thumb}；停止扫描并保留旧库存"
                )
                return None, None
            if page_index >= max_swipes:
                mfaalog.error(
                    f"[个人库存] {label}达到安全上限 {max_swipes} 次下滑，"
                    "仍未确认列表底部："
                    f"滚动条={current_thumb}，上一屏滚动条={previous_thumb}，"
                    f"滚动条稳定={unchanged}，画面稳定={content_unchanged}，"
                    f"画面差异={content_diff}"
                )
                self._leave_current_list()
                return None, None
            previous_thumb = current_thumb
            previous_content = current_content
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

    def _scroll_thumb_bounds(self, image):
        """返回右侧白色滑块的上下边；短列表的滑块也可能接近整条轨道。"""
        base = self._to_base(image)
        if base is None:
            return None
        roi_y, roi_x = 165, 1210
        hsv = cv2.cvtColor(base[roi_y:715, roi_x:1275], cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 1] < 80) & (hsv[:, :, 2] > 180)).astype(np.uint8)
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
        candidates = []
        min_width, max_width = SCROLL_THUMB_WIDTH_RANGE
        min_height, max_height = SCROLL_THUMB_HEIGHT_RANGE
        for x, y, width, height, area in stats[1:]:
            if (
                min_width <= width <= max_width and
                min_height <= height <= max_height and
                area >= 300
            ):
                candidates.append((int(area), roi_y + int(y), roi_y + int(y + height)))
        return max(candidates)[1:] if candidates else None

    def _scroll_thumb_center(self, image):
        bounds = self._scroll_thumb_bounds(image)
        return int(round(sum(bounds) / 2)) if bounds else None

    def _scroll_at_bottom(self, image, center):
        if center is None:
            return False
        bounds = self._scroll_thumb_bounds(image)
        # 长滑块到底时中心可能低于 650，改看其下沿；短滑块兼容原判定。
        return bounds[1] >= 705 if bounds else center >= SCROLL_BOTTOM_CENTER_Y

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
