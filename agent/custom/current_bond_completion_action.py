# -*- coding: utf-8 -*-
"""基于玩家当前编队执行羁绊最大化补齐。

本入口不读取 Chaldea 队伍。它只负责识别当前编队并构造羁绊优化所需的初始状态；
仓库扫描、从者与礼装选择、COST 校验、缺失重规划和失败恢复均复用
``CompleteBondFormation`` 的既有实现。
"""

from __future__ import annotations

import traceback

import cv2
import numpy as np

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from bond_completion_action import CompleteBondFormation, _truthy
from bond_matcher import equip_is_permanent_bond, rarity_order, team_bond_score
from formation_action import _norm_img
import mfaalog


CURRENT_TEAM_FEATURE_SIZE = (48, 48)
CURRENT_TEAM_FEATURE_THRESHOLD = 0.80
CURRENT_TEAM_FEATURE_MARGIN = 0.20
CURRENT_TEAM_TEMPLATE_SIZE = (185, 184)
CURRENT_TEAM_TEMPLATE_OFFSET_Y = 64


@AgentServer.custom_action("complete_current_bond_formation")
class CompleteCurrentBondFormation(CompleteBondFormation):
    """识别当前队伍后，复用现有羁绊补齐引擎进行增量优化。"""

    def _shot(self):
        """同步消费截图回包，避免长识别流程累积未领取的异步响应。

        基类为兼容旧控制器使用缓存帧，但在 MaaMCP Agent 传输中只发起请求而
        不等待会留下反向截图回包。当前任务识别五名从者耗时较长，积压回包可能
        被 AgentServer 判为 unexpected msg；本入口改为同步取得本次截图，并在
        控制器偶发异常时只回退到最后一帧缓存。
        """
        try:
            image = _norm_img(self.controller.post_screencap().wait().get())
            if image is not None:
                return image
        except Exception as exc:
            mfaalog.warning(f"[当前编队羁绊] 同步截图失败，尝试缓存帧: {exc}")
        try:
            return _norm_img(self.controller.cached_image)
        except Exception as exc:
            mfaalog.warning(f"[当前编队羁绊] 读取截图缓存失败: {exc}")
            return None

    @staticmethod
    def _current_team_feature(image):
        """生成固定对齐头像的小尺寸归一化灰度特征。"""
        if image is None or image.size == 0:
            return None
        resized = cv2.resize(
            image, CURRENT_TEAM_FEATURE_SIZE, interpolation=cv2.INTER_AREA
        )
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY).astype(np.float32)
        vector = gray.reshape(-1)
        vector -= float(vector.mean())
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else None

    def _identify_unspecified_servants(self, image, slots):
        """批量识别当前队伍已有从者，避免全库逐槽滑窗造成 Action 超时。

        编队卡中的 NarrowFigures 在 1280x720 下稳定落在槽位 ROI 的 y=64，
        x 仅有 0~1 像素偏移。先把所有候选模板与各槽位固定区域统一缩至
        48x48，再用矩阵乘法一次计算相关性；同时要求较高绝对分与候选分差。
        对齐或置信度不满足时安全失败，不回退到容易超时的全库慢扫描。
        """
        if not slots:
            return {}
        if image is None:
            return None

        excluded = {
            str(item["svt_id"])
            for item in self.expected
            if item["kind"] == "LOCAL"
        }
        template_width = max(1, int(round(CURRENT_TEAM_TEMPLATE_SIZE[0] * self.sx)))
        template_height = max(1, int(round(CURRENT_TEAM_TEMPLATE_SIZE[1] * self.sy)))
        rows = []
        for servant in self.servant_database.values():
            servant_id = str(servant.get("id") or "")
            if not servant_id or servant_id in excluded:
                continue
            if (
                self.local_servant_inventory_active
                and servant_id not in self.local_servant_ids
            ):
                continue
            for template_name, template in self._servant_templates(
                servant_id, for_list=False
            ):
                if template.shape[:2] != (
                    CURRENT_TEAM_TEMPLATE_SIZE[1],
                    CURRENT_TEAM_TEMPLATE_SIZE[0],
                ):
                    continue
                scaled = template
                if scaled.shape[:2] != (template_height, template_width):
                    scaled = cv2.resize(
                        scaled, (template_width, template_height),
                        interpolation=cv2.INTER_AREA,
                    )
                feature = self._current_team_feature(scaled)
                if feature is not None:
                    rows.append((servant_id, template_name, servant, feature))
        if not rows:
            mfaalog.error("[当前编队羁绊] 当前队伍从者识别没有可用模板")
            return None

        template_matrix = np.stack([row[3] for row in rows])
        identified = {}
        for slot in slots:
            x, y, width, height = self._scale_roi(self._slot_roi(slot))
            base_y = y + int(round(CURRENT_TEAM_TEMPLATE_OFFSET_Y * self.sy))
            slot_features = []
            max_dx = max(2, int(round(2 * self.sx)))
            max_dy = max(1, int(round(self.sy)))
            for dy in range(-max_dy, max_dy + 1):
                for dx in range(0, max_dx + 1):
                    left, top = x + dx, base_y + dy
                    right, bottom = left + template_width, top + template_height
                    if (
                        left < x or top < y or right > x + width
                        or bottom > y + height
                    ):
                        continue
                    feature = self._current_team_feature(
                        image[top:bottom, left:right]
                    )
                    if feature is not None:
                        slot_features.append(feature)
            if not slot_features:
                mfaalog.error(
                    f"[当前编队羁绊] 槽位{slot + 1}无法取得从者识别区域"
                )
                return None
            scores = np.max(
                template_matrix @ np.stack(slot_features).T, axis=1
            )
            best_by_servant = {}
            for index, score_value in enumerate(scores):
                servant_id, template_name, servant, _feature = rows[index]
                score = float(score_value)
                previous = best_by_servant.get(servant_id)
                if previous is None or score > previous[0]:
                    best_by_servant[servant_id] = (
                        score, template_name, servant
                    )
            ranked = sorted(
                (
                    (score, servant_id, template_name, servant)
                    for servant_id, (score, template_name, servant)
                    in best_by_servant.items()
                    if servant_id not in excluded
                ),
                key=lambda item: (-item[0], item[1]),
            )
            if not ranked:
                mfaalog.error(
                    f"[当前编队羁绊] 槽位{slot + 1}没有可用的从者模板候选"
                )
                return None
            best = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else -1.0
            margin = best[0] - second_score
            mfaalog.info(
                f"[当前编队羁绊] 当前从者快速识别：槽位{slot + 1} "
                f"{best[3]['name']}({best[1]}) score={best[0]:.4f}，"
                f"second={second_score:.4f}，margin={margin:.4f}，"
                f"template={best[2]}"
            )
            if (
                best[0] < CURRENT_TEAM_FEATURE_THRESHOLD
                or margin < CURRENT_TEAM_FEATURE_MARGIN
            ):
                mfaalog.error(
                    f"[当前编队羁绊] 槽位{slot + 1}当前从者识别不唯一："
                    f"{best[0]:.4f}/{CURRENT_TEAM_FEATURE_THRESHOLD:.2f}，"
                    f"margin={margin:.4f}/{CURRENT_TEAM_FEATURE_MARGIN:.2f}"
                )
                return None
            servant = dict(best[3])
            if not (servant.get("bond") or {}).get("tags"):
                mfaalog.error(
                    f"[当前编队羁绊] 槽位{slot + 1}从者 {best[1]} 缺少羁绊特性"
                )
                return None
            servant["slot"] = slot
            identified[slot] = servant
            excluded.add(best[1])
        return identified

    @staticmethod
    def _empty_expected():
        return [
            {
                "kind": "EMPTY",
                "svt_id": None,
                "equip_id": None,
                "equip_limit_break": False,
                "slot": slot,
            }
            for slot in range(6)
        ]

    @staticmethod
    def _fillable_current_slots(detected, own_count):
        available = max(0, 5 - int(own_count))
        return [
            slot for slot, item in enumerate(detected)
            if item["kind"] == "EMPTY"
        ][:available]

    def _abort_safe(self, reason):
        """复用原编队恢复流程，但独立任务未完成时仍返回失败状态。"""
        result = super()._abort_safe(reason)
        if result.success:
            mfaalog.warning(
                f"[当前编队羁绊] current_bond_aborted_safe: {reason}；原编队已恢复"
            )
            return CustomAction.RunResult(success=False)
        return result

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        self.context = context
        self.controller = context.tasker.controller
        self.opened_edit = False
        self.added_servants = {}
        self.added_equips = {}
        self.available_equips = set()
        self.unavailable_equips = set()
        self.unavailable_servants = set()
        self.owned_servants_by_rarity = {}
        self.local_servant_ids = set()
        self.local_equip_ids = set()
        self.local_servant_inventory_active = False
        self.local_equip_inventory_active = False
        try:
            node = context.get_node_data(argv.node_name) or {}
            attach = node.get("attach") or {}
            try:
                self.preferred_rarity = int(attach.get("preferred_rarity", 5))
                self.rarity_order = rarity_order(self.preferred_rarity)
                self.bond_base = int(str(attach.get("bond_base", "1200")).strip())
                self.use_local_servant_inventory = _truthy(
                    attach.get("use_local_servant_inventory", False)
                )
                self.use_local_equip_inventory = _truthy(
                    attach.get("use_local_equip_inventory", False)
                )
                self.modify_all_equips = _truthy(
                    attach.get("modify_all_equips", False)
                )
                self.debug_preserve_failure = _truthy(
                    attach.get("debug_preserve_failure", False)
                )
            except (TypeError, ValueError) as exc:
                return self._result_fail(f"current_bond_option_invalid: {exc}")
            if self.bond_base <= 0:
                return self._result_fail(
                    "current_bond_option_invalid: 羁绊基础值必须为正整数"
                )

            # 当前编队模式没有 Chaldea 保护位。已有从者始终锁定；礼装是否可改
            # 由新任务自己的 modify_all_equips 开关决定。
            self.modify_unspecified_servants = False
            self.modify_unspecified_equips = self.modify_all_equips
            self.expected = self._empty_expected()
            self._init_paths()
            self._init_scale()
            self.auto_equip = True
            self.equip_missing_policy = "skip"
            self._load_databases()
            self._prepare_target_templates()
            self._prepare_bond_resources()
            self._configure_local_inventories()

            self._focus_user("正在识别当前编队")
            mfaalog.info(
                f"[当前编队羁绊] 开始：bond_base={self.bond_base}，"
                f"优先星级={self.preferred_rarity}，顺序={self.rarity_order}，"
                f"本地从者库={self.local_servant_inventory_active}，"
                f"本地礼装库={self.local_equip_inventory_active}，"
                f"修改所有礼装={self.modify_all_equips}"
            )

            # 只接受编队确认页。这样任何未提交修改都可以通过取消配置恢复，
            # 不会误删玩家在编辑页尚未保存的手工调整。
            if not self._on_confirm_page():
                return self._result_fail(
                    "current_bond_start_page_invalid: 请从编队确认页启动任务"
                )
            if not self._run_pipeline("羁绊补齐-打开配置"):
                return self._result_fail(
                    "current_bond_start_page_invalid: 未找到配置变更按钮"
                )
            if not self._wait_for(self._in_formation_edit, 5.0):
                return self._result_fail(
                    "current_bond_start_page_invalid: 未进入编队编辑页"
                )
            self.opened_edit = True

            detected = self._detect_slots_stable()
            if detected is None:
                return self._abort_safe("current_bond_slot_invalid: 无法识别编队槽位")
            support_slots = [
                slot for slot, item in enumerate(detected)
                if item["kind"] == "SUPPORT"
            ]
            if len(support_slots) > 1:
                return self._abort_safe("current_bond_slot_invalid: 识别到多个助战槽")
            for slot in support_slots:
                self.expected[slot]["kind"] = "SUPPORT"

            occupied_slots = [
                slot for slot, item in enumerate(detected)
                if item["kind"] not in {"EMPTY", "SUPPORT"}
            ]
            self.equip_probe_slots = list(occupied_slots)
            if not occupied_slots:
                return self._abort_safe(
                    "current_bond_slot_invalid: 当前队伍至少需要一名自有从者"
                )

            image = self._shot()
            self.unspecified_servants_by_slot = self._identify_unspecified_servants(
                image, occupied_slots
            )
            if self.unspecified_servants_by_slot is None:
                return self._abort_safe(
                    "current_bond_slot_invalid: 无法唯一识别当前自有从者"
                )
            own_count = len(self.unspecified_servants_by_slot)
            if own_count > 5:
                return self._abort_safe(
                    f"current_bond_slot_invalid: 自有从者数量异常 {own_count}"
                )
            truly_empty_slots = self._fillable_current_slots(detected, own_count)
            self.truly_empty_servant_slots = set(truly_empty_slots)
            self.replaceable_slots = set()

            cost = self._read_cost_consistent()
            if cost is None:
                return self._abort_safe(
                    "current_bond_cost_ocr_failed: 初始 COST 无法稳定识别"
                )
            self.used_cost, self.max_cost = cost
            self.initial_used_cost = self.used_cost

            current_servants = self._current_known_servants()
            image = self._shot()
            initial_fixed_equips, empty_equip_slots, occupied_unknown, equip_by_slot = (
                self._classify_current_equips(image, detected)
            )
            if initial_fixed_equips is None:
                return self._abort_safe(
                    "current_bond_final_mismatch: 当前礼装识别失败"
                )
            initial_empty_equip_slots = set(empty_equip_slots)
            existing_equip_count = own_count - len([
                slot for slot in occupied_slots
                if slot in initial_empty_equip_slots
            ])
            self.locked_unspecified_equips = {}
            if not self.modify_all_equips:
                self._remember_locked_unspecified_equips(
                    image, detected, empty_equip_slots, equip_by_slot
                )

            self.initial_score = team_bond_score(
                current_servants, initial_fixed_equips, self.bond_base
            )
            if self.modify_all_equips:
                cleared = self._clear_unspecified_equips(
                    detected, empty_equip_slots, occupied_unknown, equip_by_slot
                )
                if cleared is None:
                    return self._abort_safe(
                        "current_bond_select_verify_failed: 未能清理现有礼装"
                    )
                empty_equip_slots, occupied_unknown, equip_by_slot = cleared

            self.fixed_equips = [
                equip for equip in equip_by_slot.values()
                if equip_is_permanent_bond(equip or {})
            ]
            self.empty_equip_slots = sorted(set(empty_equip_slots))
            self._focus_user(
                f"当前编队识别完成：自有从者{own_count}名、空从者位"
                f"{len(truly_empty_slots)}个、已有礼装{existing_equip_count}张、"
                f"可处理礼装位{len(self.empty_equip_slots)}个"
            )
            mfaalog.info(
                f"[当前编队羁绊] 识别完成：自有从者槽="
                f"{','.join(str(slot + 1) for slot in occupied_slots) or '无'}；"
                f"助战槽={','.join(str(slot + 1) for slot in support_slots) or '无'}；"
                f"空从者槽={','.join(str(slot + 1) for slot in truly_empty_slots) or '无'}；"
                f"空礼装槽={','.join(str(slot + 1) for slot in self.empty_equip_slots) or '无'}；"
                f"未知占用礼装槽={','.join(str(slot + 1) for slot in occupied_unknown) or '无'}；"
                f"COST={self.used_cost}/{self.max_cost}；基线羁绊={self.initial_score}"
            )

            current_servants = self._fill_servants(
                truly_empty_slots, current_servants
            )
            if current_servants is None:
                return self._abort_safe(
                    "current_bond_select_verify_failed: 补从者状态不可确认"
                )

            image = self._shot()
            for slot in self.added_servants:
                if slot not in self.equip_probe_slots:
                    self.equip_probe_slots.append(slot)
                if (
                    slot not in self.empty_equip_slots
                    and self._is_empty_equip_slot(image, slot)
                ):
                    self.empty_equip_slots.append(slot)
            self.equip_probe_slots = sorted(set(self.equip_probe_slots))
            self.empty_equip_slots = sorted(set(self.empty_equip_slots))

            if not self._fill_equips(current_servants):
                return self._abort_safe(
                    "current_bond_select_verify_failed: 补礼装状态不可确认"
                )

            final_cost = self._read_cost_consistent()
            if final_cost is None:
                return self._abort_safe(
                    "current_bond_cost_ocr_failed: 最终 COST 无法稳定识别"
                )
            self.used_cost, self.max_cost = final_cost
            if self.used_cost > self.max_cost:
                return self._abort_safe(
                    f"current_bond_cost_exceeded: {self.used_cost}/{self.max_cost}"
                )
            if not self._verify_final_state():
                return self._abort_safe(
                    "current_bond_final_mismatch: 最终槽位或模板复核失败"
                )

            all_equips = [*self.fixed_equips, *self.added_equips.values()]
            final_score = team_bond_score(
                current_servants, all_equips, self.bond_base
            )
            if final_score < self.initial_score:
                return self._abort_safe(
                    f"current_bond_score_regressed: {self.initial_score}->{final_score}"
                )
            mfaalog.info(
                f"[当前编队羁绊] 最终复算：{self.initial_score} -> {final_score} "
                f"(+{final_score - self.initial_score})，"
                f"COST={self.used_cost}/{self.max_cost}"
            )

            if not self._run_pipeline("羁绊补齐-编队决定"):
                return self._abort_safe(
                    "current_bond_final_mismatch: 未能点击编队决定"
                )
            self.opened_edit = False
            self._confirm_formation_change_if_present()
            status = (
                "current_bond_no_change"
                if not (self.added_servants or self.added_equips)
                else "current_bond_complete"
            )
            if status == "current_bond_no_change":
                self._focus_user(
                    f"羁绊优化完成：当前没有可补齐项目，"
                    f"COST {self.used_cost}/{self.max_cost}",
                    "green",
                )
            else:
                self._focus_user(
                    f"羁绊优化完成：{self.initial_score}→{final_score}，"
                    f"补齐从者{len(self.added_servants)}名、"
                    f"配置礼装{len(self.added_equips)}张，"
                    f"COST {self.used_cost}/{self.max_cost}",
                    "green",
                )
            mfaalog.info(
                f"[当前编队羁绊] {status}: 补齐从者={len(self.added_servants)}，"
                f"配置礼装={len(self.added_equips)}"
            )
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            mfaalog.error(
                f"[当前编队羁绊] 异常: {exc}\n{traceback.format_exc()}"
            )
            if getattr(self, "opened_edit", False):
                return self._abort_safe(f"current_bond_unhandled: {exc}")
            return CustomAction.RunResult(success=False)
