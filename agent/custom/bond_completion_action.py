# -*- coding: utf-8 -*-
"""Chaldea 自动编队后的羁绊最大化补齐 Action。

原自动编队 Action 仍负责严格还原 Chaldea 队伍；本模块只在其确认成功后重新进入
配置页，向真实空位追加本地从者和常驻羁绊礼装，并按选项优化 Chaldea 未指定位置
已有的对象。所有固定界面操作均调用专用的 ``羁绊补齐-*`` pipeline。
"""

from __future__ import annotations

import glob
import html
import json
import os
import re
import time
import traceback

import cv2
import numpy as np

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JOCR

from bond_matcher import (
    equip_is_permanent_bond,
    optimize_equips,
    rank_servants,
    rarity_order,
    team_bond_score,
)
from formation_action import (
    EQUIP_SLOT_CLICK_Y,
    EQUIP_TEAM_ROIS,
    AutoFormationFromChaldea,
)
from chaldea import fetch_share_data
import mfaalog


_CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(os.path.dirname(_CUSTOM_DIR))
_PLAYER_INVENTORY_DIR = os.path.join(_PROJECT_DIR, "config", "Inventory")
_PLAYER_SERVANTS_PATH = os.path.join(_PLAYER_INVENTORY_DIR, "player_servants.json")
_PLAYER_EQUIPS_PATH = os.path.join(_PLAYER_INVENTORY_DIR, "player_equips.json")

COST_ROI = (915, 671, 154, 43)
LIST_ROI = (70, 165, 1160, 445)
# 真机 1280x720 编队页对 NarrowFigures 的稳定分数为 0.8111/0.8730，次高误匹配
# 仅 0.3752/0.3491；因此编队卡复核使用 0.78。库存列表与礼装仍坚持 0.90。
SERVANT_VERIFY_THRESHOLD = 0.78
OTHER_SERVANT_VERIFY_MARGIN = 0.10
# 真机装入“秘密任务”后的 team 模板稳定命中为 0.8970；列表命中仍为
# 0.9623。另一次真机回填复核为 0.8365；编队礼装复核据此使用 0.82，
# 仓库列表继续使用 0.90，避免扩大列表选卡的误匹配范围。
EQUIP_VERIFY_THRESHOLD = 0.82
LOCKED_EQUIP_VERIFY_THRESHOLD = 0.90
LIST_MATCH_THRESHOLD = 0.90
MAX_SCAN_SWIPES = 15
MAX_EQUIP_SWIPES = 30
MATCH_STABILITY_SECONDS = 0.35
MATCH_CENTER_DELTA = 6
# 小图标模式下，从者列表每屏稳定显示 6 x 2 张完整头像。直接在整个列表 ROI
# 对数百张 158px 模板逐一滑窗会非常慢；按固定卡位批量比较图像特征后，再用
# 两帧一致性与候选间分差消除误识别。对 158px 原图仅在内存中裁掉左 44、
# 上 44、下 57（不改写模板文件），真机首屏 12 张正确候选实测为
# 0.948–0.992，扩展到顶部/中段/底部 30 名从者后最低为 0.930；
# 因此阈值取 0.88，候选间仍保留 0.12 分差。
SERVANT_LIST_FACE_X = (97, 284, 472, 659, 847, 1035)
SERVANT_LIST_FALLBACK_Y = (206, 406)
SERVANT_FACE_SIZE = 158
SERVANT_FEATURE_REGION = (44, 44, 158, 101)
SERVANT_FEATURE_SIZE = (48, 38)
SERVANT_FEATURE_THRESHOLD = 0.88
SERVANT_FEATURE_MARGIN = 0.12
SLOT_STABILITY_SECONDS = 0.60
EMPTY_EQUIP_STD_MAX = 35.0
# 真机空礼装槽受从者卡边缘、选中框与背景渐变影响，饱和像素比例可到 0.270；
# 仍同时要求灰度标准差不超过 35，避免把正常礼装仅凭颜色偏淡判为空槽。
EMPTY_EQUIP_SATURATED_RATIO_MAX = 0.30
SUPPORT_TYPES = {"friend", "fixed", "npc"}
SHORT_PARTY_CONFIRM_ROI = (650, 540, 350, 120)
SHORT_PARTY_POLL_SECONDS = 5.0


def _truthy(value) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "否", "none"}


@AgentServer.custom_action("complete_bond_formation")
class CompleteBondFormation(AutoFormationFromChaldea):
    """在第一阶段已确认的 Chaldea 队伍上执行安全的增量补齐。"""

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
            if not _truthy(attach.get("bond_completion_enabled", False)):
                mfaalog.info("[羁绊补齐] 功能未开启，保持原自动编队结果")
                return CustomAction.RunResult(success=True)

            source = str(
                attach.get("chaldea_import_source")
                or attach.get("chaldea_import_source_file")
                or ""
            ).strip()
            if not source:
                return self._result_fail("bond_completion_slot_invalid: 未提供 Chaldea 队伍来源")
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
                self.modify_unspecified_servants = _truthy(
                    attach.get("modify_unspecified_servants", True)
                )
                self.modify_unspecified_equips = _truthy(
                    attach.get("modify_unspecified_equips", True)
                )
                self.debug_preserve_failure = _truthy(
                    attach.get("debug_preserve_failure", False)
                )
            except (TypeError, ValueError) as exc:
                return self._result_fail(f"bond_completion_option_invalid: {exc}")
            if self.bond_base <= 0:
                return self._result_fail("bond_completion_option_invalid: 羁绊基础值必须为正整数")

            share_data, _quest_id, _team_id = fetch_share_data(source)
            self.expected = self._build_expected(share_data)
            if self.expected is None:
                return CustomAction.RunResult(success=False)
            self._init_paths()
            self._init_scale()
            self.auto_equip = True
            self.equip_missing_policy = "skip"
            self._load_databases()
            self._prepare_target_templates()
            self._prepare_bond_resources()
            self._configure_local_inventories()
            self.config_marker = self._load_named_template("battle/配置变更.png")
            if self.config_marker is None:
                return self._result_fail("bond_completion_resource_missing: battle/配置变更.png")

            self._focus_user(
                f"开始羁绊优化：优先{self.preferred_rarity}星，"
                f"从者{'本地快速' if self.local_servant_inventory_active else '实时扫描'}，"
                f"礼装{'本地快速' if self.local_equip_inventory_active else '实时扫描'}，"
                f"其他从者{'可调整' if self.modify_unspecified_servants else '保留'}，"
                f"其他礼装{'可调整' if self.modify_unspecified_equips else '保留'}"
            )
            mfaalog.info(
                f"[羁绊补齐] 开始：bond_base={self.bond_base}，"
                f"优先星级={self.preferred_rarity}，顺序={self.rarity_order}，"
                f"本地从者库={self.local_servant_inventory_active}，"
                f"本地礼装库={self.local_equip_inventory_active}，"
                f"修改其他从者={self.modify_unspecified_servants}，"
                f"修改其他礼装={self.modify_unspecified_equips}"
            )
            if not self._resolve_short_party_prompt():
                return self._result_fail("bond_completion_slot_invalid: 人数不足弹窗确认后未回到编队页")
            # 编队确认页的蓝色“开始任务”按钮与“编队决定”外观接近，单独用
            # edit_marker 可能误判。先用确认页专有的“配置变更”按钮定界。
            if self._on_confirm_page():
                mfaalog.info("[羁绊补齐] 位于编队确认页，进入配置变更")
                if not self._run_pipeline("羁绊补齐-打开配置"):
                    return self._result_fail("bond_completion_slot_invalid: 未找到配置变更按钮")
                if not self._wait_for(self._in_formation_edit, 5.0):
                    return self._result_fail("bond_completion_slot_invalid: 未进入配置变更页")
                self.opened_edit = True
            elif self._in_formation_edit():
                mfaalog.info("[羁绊补齐] 已位于编队编辑页")
                self.opened_edit = True
            else:
                if not self._run_pipeline("羁绊补齐-打开配置"):
                    return self._result_fail("bond_completion_slot_invalid: 未找到配置变更按钮")
                if not self._wait_for(self._in_formation_edit, 5.0):
                    return self._result_fail("bond_completion_slot_invalid: 未进入配置编辑页")
                self.opened_edit = True

            detected = self._detect_slots_stable()
            if detected is None:
                return self._abort_safe("bond_completion_slot_invalid: 无法识别编队槽位")
            support_slots = [i for i, item in enumerate(detected) if item["kind"] == "SUPPORT"]
            if len(support_slots) > 1:
                return self._abort_safe("bond_completion_slot_invalid: 识别到多个助战槽")
            self.equip_probe_slots = [
                i for i, item in enumerate(detected)
                if item["kind"] not in {"EMPTY", "SUPPORT"}
            ]

            # Chaldea 未指定位置上的残留从者不能简单丢出评分模型。先识别其身份：
            # 允许修改时把对应槽位列为替换候选；禁止修改时把它锁定为固定成员。
            image = self._shot()
            other_slots = [
                i for i, item in enumerate(detected)
                if self.expected[i]["kind"] == "EMPTY" and item["kind"] == "OTHER"
            ]
            self.unspecified_servants_by_slot = self._identify_unspecified_servants(
                image, other_slots
            )
            if self.unspecified_servants_by_slot is None:
                return self._abort_safe(
                    "bond_completion_slot_invalid: 无法识别 Chaldea 未指定位置的现有从者"
                )
            truly_empty_slots = [
                i for i, item in enumerate(detected)
                if self.expected[i]["kind"] == "EMPTY" and item["kind"] == "EMPTY"
            ]
            actual_own_count = (
                sum(item["kind"] == "LOCAL" for item in self.expected)
                + len(self.unspecified_servants_by_slot)
            )
            self.replaceable_slots = (
                set(other_slots) if self.modify_unspecified_servants else set()
            )
            available_empty_count = max(0, 5 - actual_own_count)
            fillable = sorted([
                *self.replaceable_slots,
                *truly_empty_slots[:available_empty_count],
            ])
            self.truly_empty_servant_slots = set(truly_empty_slots)
            cost = self._read_cost_consistent()
            if cost is None:
                return self._abort_safe("bond_completion_cost_ocr_failed: 初始 COST 无法稳定识别")
            self.used_cost, self.max_cost = cost
            self.initial_used_cost = self.used_cost

            current_servants = self._current_known_servants()
            image = self._shot()
            initial_fixed_equips, empty_equip_slots, occupied_unknown, equip_by_slot = self._classify_current_equips(
                image, detected
            )
            if initial_fixed_equips is None:
                return self._abort_safe("bond_completion_final_mismatch: Chaldea 保护礼装不匹配")
            self.locked_unspecified_equips = {}
            if not self.modify_unspecified_equips:
                self._remember_locked_unspecified_equips(
                    image, detected, empty_equip_slots, equip_by_slot
                )
            self.initial_score = team_bond_score(
                current_servants, initial_fixed_equips, self.bond_base
            )
            if self.modify_unspecified_equips:
                cleared = self._clear_unspecified_equips(
                    detected, empty_equip_slots, occupied_unknown, equip_by_slot
                )
                if cleared is None:
                    return self._abort_safe(
                        "bond_completion_select_verify_failed: 未能清理可修改的其他位置礼装"
                    )
                empty_equip_slots, occupied_unknown, equip_by_slot = cleared
            self.fixed_equips = [
                equip for equip in equip_by_slot.values()
                if equip_is_permanent_bond(equip or {})
            ]
            self.empty_equip_slots = empty_equip_slots
            self._focus_user(
                f"队伍分析完成：COST {self.used_cost}/{self.max_cost}，"
                f"可处理从者{len(fillable)}位、礼装{len(empty_equip_slots)}位"
            )
            mfaalog.info(
                f"[羁绊补齐] 初始 COST={self.initial_used_cost}/{self.max_cost}；"
                f"规划起始 COST={self.used_cost}/{self.max_cost}；"
                f"本地从者={actual_own_count}，可补/替换从者槽={','.join(str(i + 1) for i in fillable) or '无'}；"
                f"锁定其他从者槽={','.join(str(i + 1) for i in other_slots if i not in self.replaceable_slots) or '无'}；"
                f"空礼装槽={','.join(str(i + 1) for i in empty_equip_slots) or '无'}；"
                f"未知占用礼装槽={','.join(str(i + 1) for i in occupied_unknown) or '无'}；"
                f"基线羁绊={self.initial_score}"
            )

            current_servants = self._fill_servants(fillable, current_servants)
            if current_servants is None:
                return self._abort_safe("bond_completion_select_verify_failed: 补从者状态不可确认")

            # 新从者的礼装位在选人后为空；加入前再次用编队截图验证。
            image = self._shot()
            for slot in self.added_servants:
                if slot not in self.empty_equip_slots and self._is_empty_equip_slot(image, slot):
                    self.empty_equip_slots.append(slot)
            self.empty_equip_slots = sorted(set(self.empty_equip_slots))

            if not self._fill_equips(current_servants):
                return self._abort_safe("bond_completion_select_verify_failed: 补礼装状态不可确认")

            final_cost = self._read_cost_consistent()
            if final_cost is None:
                return self._abort_safe("bond_completion_cost_ocr_failed: 最终 COST 无法稳定识别")
            self.used_cost, self.max_cost = final_cost
            if self.used_cost > self.max_cost:
                return self._abort_safe(
                    f"bond_completion_cost_exceeded: {self.used_cost}/{self.max_cost}"
                )
            if not self._verify_final_state():
                return self._abort_safe("bond_completion_final_mismatch: 最终槽位或模板复核失败")

            all_equips = [*self.fixed_equips, *self.added_equips.values()]
            final_score = team_bond_score(current_servants, all_equips, self.bond_base)
            mfaalog.info(
                f"[羁绊补齐] 最终复算：{self.initial_score} -> {final_score} "
                f"(+{final_score - self.initial_score})，COST={self.used_cost}/{self.max_cost}，"
                f"剩余={self.max_cost - self.used_cost}"
            )
            if not self._run_pipeline("羁绊补齐-编队决定"):
                return self._abort_safe("bond_completion_final_mismatch: 未能点击编队决定")
            self.opened_edit = False
            self._confirm_formation_change_if_present()
            status = "bond_completion_no_change" if not (self.added_servants or self.added_equips) else "bond_completion_complete"
            if status == "bond_completion_no_change":
                self._focus_user(
                    f"羁绊优化完成：当前没有可提升项，COST {self.used_cost}/{self.max_cost}",
                    "green",
                )
            else:
                self._focus_user(
                    f"羁绊优化完成：{self.initial_score}→{final_score}，"
                    f"调整从者{len(self.added_servants)}位、礼装{len(self.added_equips)}张，"
                    f"COST {self.used_cost}/{self.max_cost}",
                    "green",
                )
            mfaalog.info(
                f"[羁绊补齐] {status}: 新增从者={len(self.added_servants)}，"
                f"新增礼装={len(self.added_equips)}"
            )
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            mfaalog.error(f"[羁绊补齐] 异常: {exc}\n{traceback.format_exc()}")
            if getattr(self, "opened_edit", False):
                return self._abort_safe(f"bond_completion_unhandled: {exc}")
            return CustomAction.RunResult(success=False)

    # ---------- 数据与资源 ----------

    def _load_databases(self):
        with open(os.path.join(_CUSTOM_DIR, "servant_list.json"), encoding="utf-8-sig") as file:
            servants = json.load(file).get("servants", [])
        with open(os.path.join(_CUSTOM_DIR, "equip_list.json"), encoding="utf-8-sig") as file:
            equips = json.load(file).get("equips", [])
        with open(os.path.join(_CUSTOM_DIR, "bond_completion_costs.json"), encoding="utf-8-sig") as file:
            costs = json.load(file)
        servant_costs = costs.get("servants") or {}
        equip_costs = costs.get("equips") or {}
        self.servant_database = {}
        for raw in servants:
            item = dict(raw)
            sid = str(item.get("id") or "")
            if sid in servant_costs:
                item["cost"] = int(servant_costs[sid])
            self.servant_database[sid] = item
        self.equip_database = {}
        for raw in equips:
            item = dict(raw)
            eid = str(item.get("id") or "")
            if eid in equip_costs:
                item["cost"] = int(equip_costs[eid])
            self.equip_database[eid] = item

    @staticmethod
    def _read_player_inventory(path, expected_kind, item_key):
        """读取一次构建完成的 schema v1 玩家库存，只返回去重 ID。"""
        with open(path, encoding="utf-8-sig") as file:
            document = json.load(file)
        if not isinstance(document, dict):
            raise ValueError("顶层不是对象")
        if document.get("schema_version") != 1:
            raise ValueError("schema_version 不是 1")
        if document.get("kind") != expected_kind:
            raise ValueError(f"kind 不是 {expected_kind}")
        if document.get("scan_complete") is not True:
            raise ValueError("scan_complete 不是 true")
        items = document.get(item_key)
        if not isinstance(items, list):
            raise ValueError(f"缺少 {item_key} 数组")
        ids = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"{item_key}[{index}] 不是对象")
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                raise ValueError(f"{item_key}[{index}] 缺少 ID")
            ids.append(item_id)
        unique_ids = set(ids)
        if len(unique_ids) != len(ids):
            raise ValueError(f"{item_key} 包含重复 ID")
        try:
            declared_count = int(document.get("count"))
        except (TypeError, ValueError) as exc:
            raise ValueError("count 不是整数") from exc
        if declared_count != len(unique_ids):
            raise ValueError(f"count={declared_count} 与实际 {len(unique_ids)} 不一致")
        return unique_ids, document

    def _configure_local_inventories(self):
        """分别启用本地从者/礼装白名单；无效类别独立回退实时扫描。"""
        if self.use_local_servant_inventory:
            try:
                raw_ids, document = self._read_player_inventory(
                    _PLAYER_SERVANTS_PATH, "servants", "servants"
                )
                self.local_servant_ids = raw_ids & set(self.servant_database)
                unknown = raw_ids - self.local_servant_ids
                self.owned_servants_by_rarity = {rarity: set() for rarity in range(6)}
                for servant_id in self.local_servant_ids:
                    servant = self.servant_database[servant_id]
                    rarity = int(servant.get("rarity", -1))
                    if rarity in self.owned_servants_by_rarity:
                        self.owned_servants_by_rarity[rarity].add(servant_id)
                self.local_servant_inventory_active = True
                mfaalog.info(
                    f"[羁绊补齐] 本地从者库已启用：{len(self.local_servant_ids)}名，"
                    f"generated_at={document.get('generated_at') or '未知'}，"
                    f"忽略目录外ID={len(unknown)}"
                )
                self._focus_user(f"已载入本地从者库：{len(self.local_servant_ids)}名")
                if document.get("catalog_complete") is False:
                    self._focus_user("本地从者库缺少部分模板，优化范围可能不完整", "orange")
            except Exception as exc:
                mfaalog.warning(
                    f"[羁绊补齐] 本地从者库不可用，回退实时扫描：{exc}"
                )
                self._focus_user("本地从者库不可用，已回退实时扫描", "orange")

        if self.use_local_equip_inventory:
            try:
                raw_ids, document = self._read_player_inventory(
                    _PLAYER_EQUIPS_PATH, "equips", "equips"
                )
                self.local_equip_ids = raw_ids & set(self.equip_database)
                unknown = raw_ids - self.local_equip_ids
                self.available_equips.update(self.local_equip_ids)
                self.local_equip_inventory_active = True
                mfaalog.info(
                    f"[羁绊补齐] 本地礼装库已启用：{len(self.local_equip_ids)}种，"
                    f"generated_at={document.get('generated_at') or '未知'}，"
                    f"忽略目录外ID={len(unknown)}；满破仍在选择时确认"
                )
                self._focus_user(f"已载入本地礼装库：{len(self.local_equip_ids)}种")
                if document.get("catalog_complete") is False:
                    self._focus_user("本地礼装库缺少部分模板，优化范围可能不完整", "orange")
            except Exception as exc:
                mfaalog.warning(
                    f"[羁绊补齐] 本地礼装库不可用，回退实时扫描：{exc}"
                )
                self._focus_user("本地礼装库不可用，已回退实时扫描", "orange")

    def _candidate_bond_equips(self):
        candidates = [
            item for item in self.bond_equips
            if str(item["id"]) not in self.unavailable_equips
        ]
        if self.local_equip_inventory_active:
            candidates = [
                item for item in candidates
                if str(item["id"]) in self.local_equip_ids
            ]
        return candidates

    def _prepare_bond_resources(self):
        self.bond_equips = []
        self.equip_list_templates = getattr(self, "equip_list_templates", {})
        self.equip_team_templates = getattr(self, "equip_team_templates", {})
        skipped = []
        for equip in self.equip_database.values():
            if not equip_is_permanent_bond(equip) or "cost" not in equip:
                continue
            eid = int(equip["id"])
            team = self._load_equip_template(eid, self.equip_team_dirs)
            listing = self._load_equip_template(eid, self.equip_list_dirs)
            if team is None or listing is None:
                skipped.append(str(eid))
                continue
            self.equip_team_templates[eid] = team
            self.equip_list_templates[eid] = listing
            self.bond_equips.append(equip)
        self.bond_equips.sort(key=lambda item: int(item["id"]))
        if skipped:
            mfaalog.warning(
                "[羁绊补齐] bond_completion_resource_missing: 礼装缺 list/team 模板="
                + ",".join(skipped)
            )
        mfaalog.info(f"[羁绊补齐] 可用常驻羁绊礼装资源={len(self.bond_equips)}")
        self._servant_template_cache = {}
        self._face_paths = []
        for directory in [*self.face_dirs, *self.narrow_dirs]:
            self._face_paths.extend(glob.glob(os.path.join(directory, "f_*.png")))

    def _servant_templates(self, servant_id, for_list=True):
        key = (str(servant_id), bool(for_list))
        if key in self._servant_template_cache:
            return self._servant_template_cache[key]
        directories = self.face_dirs if for_list else self.narrow_dirs
        templates = self._load_servant_templates(servant_id, directories)
        if not templates:
            fallback = self.narrow_dirs if for_list else self.face_dirs
            templates = self._load_servant_templates(servant_id, fallback)
        self._servant_template_cache[key] = templates
        return templates

    def _servant_candidates(self, rarity):
        selected = {
            str(item["svt_id"]) for item in self.expected if item["kind"] == "LOCAL"
        } | {
            str(item["id"]) for item in self.unspecified_servants_by_slot.values()
        } | {str(item["id"]) for item in self.added_servants.values()}
        result = []
        for item in self.servant_database.values():
            item_id = str(item.get("id"))
            if (
                int(item.get("rarity", -1)) != rarity
                or item_id in selected
                or item_id in self.unavailable_servants
            ):
                continue
            if "cost" not in item or not (item.get("bond") or {}).get("tags"):
                continue
            if not self._servant_templates(item["id"], for_list=True):
                continue
            result.append(item)
        return result

    # ---------- COST 与编队状态 ----------

    def _resolve_short_party_prompt(self):
        """轮询第一阶段决定后可能出现的“队伍人数不足”弹窗。"""
        deadline = time.monotonic() + SHORT_PARTY_POLL_SECONDS
        while time.monotonic() < deadline:
            image = self._shot()
            match = self._match_template(
                image,
                self.formation_confirm_marker,
                SHORT_PARTY_CONFIRM_ROI,
            )
            if match is not None and match[0] >= 0.80:
                mfaalog.info(
                    f"[羁绊补齐] 命中队伍人数不足弹窗决定，分数={match[0]:.4f}"
                )
                if not self._run_pipeline("羁绊补齐-人数不足决定"):
                    return False
                return self._wait_for(
                    lambda: self._on_confirm_page() or self._in_formation_edit(),
                    5.0,
                )
            # 已经落在正常编队页时无需把完整 5 秒等待用完。
            if self._on_confirm_page() or self._in_formation_edit():
                return True
            time.sleep(0.4)
        mfaalog.info("[羁绊补齐] 决定后未出现队伍人数不足弹窗")
        return True

    def _detect_slots_stable(self):
        """等待编队切页动画结束，要求连续两帧均为可判定槽位且类型一致。"""
        previous = None
        for attempt in range(8):
            detected = self._detect_slots()
            if detected is None:
                return None
            signature = tuple(item["kind"] for item in detected)
            # 切页遮罩有时会连续数帧稳定地返回 OTHER；它不是可接受的稳定态。
            # 自有从者区最终只能是 LOCAL/EMPTY，助战位另允许 SUPPORT。
            resolved = all(
                kind != "OTHER" or self.expected[index]["kind"] == "EMPTY"
                for index, kind in enumerate(signature)
            )
            if resolved and signature == previous:
                mfaalog.info(f"[羁绊补齐] 编队槽位稳定：{', '.join(signature)}")
                return detected
            if not resolved:
                mfaalog.info(
                    f"[羁绊补齐] 编队槽位仍有未知状态：{signature}，"
                    f"重试 {attempt + 1}/8"
                )
            elif previous is not None:
                mfaalog.info(
                    f"[羁绊补齐] 编队槽位仍在刷新：{previous} -> {signature}，"
                    f"重试 {attempt + 1}/8"
                )
            previous = signature if resolved else None
            time.sleep(SLOT_STABILITY_SECONDS)
        mfaalog.warning(f"[羁绊补齐] 编队槽位连续帧不一致：最后={previous}")
        return None

    def _ocr_cost_once(self):
        image = self._shot()
        if image is None:
            return None
        roi = self._scale_roi(COST_ROI)
        try:
            detail = self.context.run_recognition_direct("OCR", JOCR(roi=roi), image)
        except Exception as exc:
            mfaalog.warning(f"[羁绊补齐] COST OCR 调用异常: {exc}")
            return None
        texts = []
        if detail is not None:
            for result in detail.all_results:
                text = str(getattr(result, "text", "") or "").strip()
                if text:
                    texts.append(text)
        raw = " ".join(texts)
        matches = re.findall(r"([0-9][0-9,]*)\s*/\s*([0-9][0-9,]*)", raw)
        if len(matches) != 1:
            mfaalog.warning(f"[羁绊补齐] COST OCR 格式不合法: {raw!r}")
            return None
        used, maximum = (int(value.replace(",", "")) for value in matches[0])
        mfaalog.info(f"[羁绊补齐] COST OCR: {raw!r} -> {used}/{maximum}")
        if used < 0 or maximum <= 0:
            return None
        return used, maximum

    def _read_cost_consistent(self):
        first = self._ocr_cost_once()
        time.sleep(0.35)
        second = self._ocr_cost_once()
        if first is None or second is None or first != second:
            mfaalog.warning(f"[羁绊补齐] COST 连续结果不一致: {first} / {second}")
            return None
        return first

    def _current_known_servants(self):
        result = []
        for item in self.expected:
            if item["kind"] != "LOCAL":
                continue
            servant = self.servant_database.get(str(item["svt_id"]))
            if servant is None:
                mfaalog.warning(f"[羁绊补齐] 从者资料缺失，评分排除 svtId={item['svt_id']}")
                continue
            servant = dict(servant)
            servant["slot"] = item["slot"]
            result.append(servant)
        result.extend(
            dict(item) for item in self.unspecified_servants_by_slot.values()
        )
        result.extend(dict(item) for item in self.added_servants.values())
        return result

    def _identify_unspecified_servants(self, image, slots):
        """识别 Chaldea 未指定、但当前队伍仍占用的本地从者槽。"""
        if not slots:
            return {}
        if image is None:
            return None
        excluded = {
            str(item["svt_id"])
            for item in self.expected
            if item["kind"] == "LOCAL"
        }
        identified = {}
        for slot in slots:
            ranked = []
            for servant in self.servant_database.values():
                servant_id = str(servant.get("id") or "")
                if not servant_id or servant_id in excluded:
                    continue
                templates = self._servant_templates(servant_id, for_list=False)
                if not templates:
                    continue
                match = self._match_servant(image, templates, self._slot_roi(slot))
                if match is not None:
                    ranked.append((float(match[0]), servant_id, match[2], servant))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            if not ranked:
                mfaalog.error(f"[羁绊补齐] 槽位{slot + 1}没有可用的从者模板候选")
                return None
            best = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else -1.0
            mfaalog.info(
                f"[羁绊补齐] 其他位置从者识别：槽位{slot + 1} "
                f"{best[3]['name']}({best[1]}) score={best[0]:.4f}，"
                f"second={second_score:.4f}，template={best[2]}"
            )
            if (
                best[0] < SERVANT_VERIFY_THRESHOLD
                or best[0] - second_score < OTHER_SERVANT_VERIFY_MARGIN
            ):
                mfaalog.error(
                    f"[羁绊补齐] 槽位{slot + 1}其他位置从者识别不唯一："
                    f"{best[0]:.4f}/{SERVANT_VERIFY_THRESHOLD:.2f}，"
                    f"margin={best[0] - second_score:.4f}/"
                    f"{OTHER_SERVANT_VERIFY_MARGIN:.2f}"
                )
                return None
            servant = dict(best[3])
            if not (servant.get("bond") or {}).get("tags"):
                mfaalog.error(
                    f"[羁绊补齐] 槽位{slot + 1}从者 {best[1]} 缺少羁绊特性"
                )
                return None
            if self.modify_unspecified_servants and "cost" not in servant:
                mfaalog.error(
                    f"[羁绊补齐] 槽位{slot + 1}从者 {best[1]} 缺少替换所需 COST"
                )
                return None
            servant["slot"] = slot
            identified[slot] = servant
            excluded.add(best[1])
        return identified

    def _is_empty_equip_slot(self, image, slot):
        if image is None:
            return False
        region = self._equip_slot_snapshot(image, slot)
        if region.size == 0:
            return False
        gray_std = float(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).std())
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        saturated_ratio = float(np.mean(hsv[:, :, 1] > 60))
        empty = gray_std <= EMPTY_EQUIP_STD_MAX and saturated_ratio <= EMPTY_EQUIP_SATURATED_RATIO_MAX
        mfaalog.info(
            f"[羁绊补齐] 槽位{slot + 1}礼装空位判定：std={gray_std:.2f}，"
            f"sat_ratio={saturated_ratio:.3f}，empty={empty}"
        )
        return empty

    def _equip_slot_snapshot(self, image, slot):
        if image is None:
            return np.empty((0, 0, 3), dtype=np.uint8)
        x, y, width, height = self._scale_roi(EQUIP_TEAM_ROIS[slot])
        # 只取礼装卡片上部，避免 COST/HP 文本和从者替换后的队伍数值变化。
        height = max(1, int(height * 0.67))
        return image[y:y + height, x:x + width].copy()

    def _remember_locked_unspecified_equips(
        self, image, detected, empty_slots, equip_by_slot
    ):
        empty = set(empty_slots)
        for slot, state in enumerate(detected):
            if (
                state["kind"] in {"EMPTY", "SUPPORT"}
                or self.expected[slot].get("equip_id")
                or slot in empty
            ):
                continue
            self.locked_unspecified_equips[slot] = {
                "equip": equip_by_slot.get(slot),
                "snapshot": self._equip_slot_snapshot(image, slot),
            }
        if self.locked_unspecified_equips:
            mfaalog.info(
                "[羁绊补齐] 按选项锁定其他位置礼装槽="
                + ",".join(str(slot + 1) for slot in self.locked_unspecified_equips)
            )

    def _match_equip_id(self, image, equip_id, slot):
        data = self.equip_team_templates.get(int(equip_id))
        if data is None:
            return None
        _name, template = data
        return self._match_template(image, template, EQUIP_TEAM_ROIS[slot])

    def _classify_current_equips(self, image, detected):
        fixed, empty, unknown, equip_by_slot = [], [], [], {}
        for slot, state in enumerate(detected):
            if state["kind"] in {"EMPTY", "SUPPORT"}:
                continue
            protected_id = self.expected[slot].get("equip_id")
            if protected_id:
                match = self._match_equip_id(image, protected_id, slot)
                if match is None or match[0] < EQUIP_VERIFY_THRESHOLD:
                    score = match[0] if match else 0.0
                    mfaalog.error(
                        f"[羁绊补齐] 保护礼装复核失败：槽位{slot + 1} ceId={protected_id} "
                        f"score={score:.4f}/{EQUIP_VERIFY_THRESHOLD:.2f}"
                    )
                    return None, [], [], {}
                equip = self.equip_database.get(str(protected_id))
                if equip is not None:
                    equip_by_slot[slot] = equip
                if equip_is_permanent_bond(equip or {}):
                    fixed.append(equip)
                continue
            best = None
            for equip in self.bond_equips:
                match = self._match_equip_id(image, equip["id"], slot)
                if match is not None and (best is None or match[0] > best[0]):
                    best = (match[0], equip)
            if best is not None and best[0] >= EQUIP_VERIFY_THRESHOLD:
                fixed.append(best[1])
                equip_by_slot[slot] = best[1]
                mfaalog.info(
                    f"[羁绊补齐] 槽位{slot + 1}识别已有羁绊礼装 "
                    f"{best[1]['name']}({best[1]['id']}) score={best[0]:.4f}"
                )
            elif self._is_empty_equip_slot(image, slot):
                empty.append(slot)
            else:
                unknown.append(slot)
        return fixed, empty, unknown, equip_by_slot

    def _clear_unspecified_equips(
        self, detected, empty_slots, unknown_slots, equip_by_slot
    ):
        """在首次规划前卸下 Chaldea 未指定且当前已占用的礼装。"""
        empty = set(empty_slots)
        unknown = set(unknown_slots)
        by_slot = dict(equip_by_slot)
        modifiable = [
            slot for slot, state in enumerate(detected)
            if state["kind"] not in {"EMPTY", "SUPPORT"}
            and not self.expected[slot].get("equip_id")
            and slot not in empty
        ]
        for slot in modifiable:
            old = by_slot.get(slot)
            label = (
                f"{old['name']}({old['id']})" if old is not None
                else "未识别的现有礼装"
            )
            previous_cost = self.used_cost
            if not self._unequip_slot(slot):
                return None
            by_slot.pop(slot, None)
            unknown.discard(slot)
            empty.add(slot)
            mfaalog.info(
                f"[羁绊补齐] 已按选项卸下其他位置礼装：槽位{slot + 1} {label}，"
                f"COST {previous_cost}->{self.used_cost}"
            )
        return sorted(empty), sorted(unknown), by_slot

    # ---------- 从者库存扫描与选择 ----------

    @staticmethod
    def _normalized_feature(image):
        if image is None or image.size == 0:
            return None
        x1, y1, x2, y2 = SERVANT_FEATURE_REGION
        if image.shape[0] < y2 or image.shape[1] < x2:
            image = cv2.resize(image, (SERVANT_FACE_SIZE, SERVANT_FACE_SIZE))
        region = image[y1:y2, x1:x2]
        feature = cv2.resize(region, SERVANT_FEATURE_SIZE).astype(np.float32).reshape(-1)
        feature -= feature.mean()
        norm = float(np.linalg.norm(feature))
        return feature / norm if norm > 0 else None

    def _visible_servant_features(self, image):
        features = []
        centers = []
        # 下滑距离不一定恰好是 200px，卡片行会在垂直方向漂移。先把截图归一到
        # 1280x720，再从每张普通从者卡底部的黄色 Servant 条带反推出头像 y。
        base_image = image
        if image.shape[:2] != (720, 1280):
            base_image = cv2.resize(image, (1280, 720))
        hsv = cv2.cvtColor(base_image, cv2.COLOR_BGR2HSV)
        yellow = cv2.inRange(hsv, (18, 100, 100), (40, 255, 255))
        row_ratio = np.mean(yellow[:, 70:1230] > 0, axis=1)
        rows = np.where(row_ratio > 0.25)[0]
        runs = []
        for row in rows:
            row = int(row)
            if not runs or row > runs[-1][-1] + 1:
                runs.append([row])
            else:
                runs[-1].append(row)
        y_origins = []
        for run in runs:
            if len(run) < 8 or float(row_ratio[run].max()) < 0.35:
                continue
            origin = run[0] - SERVANT_FACE_SIZE
            if 165 <= origin and origin + SERVANT_FACE_SIZE <= 720:
                if not y_origins or abs(origin - y_origins[-1]) > 20:
                    y_origins.append(origin)
        if not y_origins:
            y_origins = list(SERVANT_LIST_FALLBACK_Y)

        for base_y in y_origins:
            for base_x in SERVANT_LIST_FACE_X:
                card = base_image[
                    base_y:base_y + SERVANT_FACE_SIZE,
                    base_x:base_x + SERVANT_FACE_SIZE,
                ]
                if card.shape[:2] != (SERVANT_FACE_SIZE, SERVANT_FACE_SIZE):
                    continue
                feature = self._normalized_feature(card)
                if feature is not None:
                    features.append(feature)
                    centers.append((
                        int(round((base_x + SERVANT_FACE_SIZE / 2) * self.sx)),
                        int(round((base_y + SERVANT_FACE_SIZE / 2) * self.sy)),
                    ))
        return features, centers

    def _fast_servant_hits(self, image, candidates):
        card_features, centers = self._visible_servant_features(image)
        if not card_features:
            return {}
        variant_features = []
        variants = []
        for servant in candidates:
            for template_name, template in self._servant_templates(servant["id"], for_list=True):
                feature = self._normalized_feature(template)
                if feature is not None:
                    variant_features.append(feature)
                    variants.append((str(servant["id"]), template_name))
        if not variant_features:
            return {}

        scores = np.stack(variant_features) @ np.stack(card_features).T
        hits = {}
        for card_index, center in enumerate(centers):
            best_by_servant = {}
            for variant_index, (servant_id, template_name) in enumerate(variants):
                score = float(scores[variant_index, card_index])
                old = best_by_servant.get(servant_id)
                if old is None or score > old[0]:
                    best_by_servant[servant_id] = (score, template_name)
            ranked = sorted(best_by_servant.items(), key=lambda item: item[1][0], reverse=True)
            if not ranked:
                continue
            servant_id, (best_score, template_name) = ranked[0]
            second_score = ranked[1][1][0] if len(ranked) > 1 else -1.0
            if (
                best_score >= SERVANT_FEATURE_THRESHOLD
                and best_score - second_score >= SERVANT_FEATURE_MARGIN
            ):
                hits[servant_id] = (best_score, center, template_name)
        return hits

    def _enter_servant_select_new(self, slot):
        if self._in_servant_select():
            return True
        self.controller.post_click(*self._slot_center(slot)).wait()
        if not self._wait_for(self._in_servant_select, 8.0):
            return False
        return self._run_pipeline("羁绊补齐-确认从者选择界面")

    def _leave_servant_select(self):
        if self._in_formation_edit():
            return True
        if not self._run_pipeline("羁绊补齐-从者选择返回"):
            return False
        return self._wait_for(self._in_formation_edit, 5.0)

    def _filter_servant_rarity(self, rarity):
        if 1 <= rarity <= 5:
            recognition = {
                "param": {"template": f"整理礼物盒/{rarity}星未选中.png", "threshold": LIST_MATCH_THRESHOLD}
            }
        else:
            recognition = "DirectHit"
        self.context.override_pipeline({
            "羁绊补齐-从者筛选星级": {
                "recognition": recognition,
                "next": ["羁绊补齐-从者筛点决定"],
            }
        })
        if not self._run_pipeline("羁绊补齐-从者筛选准备"):
            return False
        if not getattr(self, "servant_list_prepared", False):
            if not self._run_pipeline("羁绊补齐-准备从者列表"):
                return False
            self.servant_list_prepared = True
        return True

    def _stable_servant_hits(self, candidates):
        first_image = self._shot()
        time.sleep(MATCH_STABILITY_SECONDS)
        second_image = self._shot()
        first_hits = self._fast_servant_hits(first_image, candidates)
        second_hits = self._fast_servant_hits(second_image, candidates)
        hits = {}
        for servant_id, first in first_hits.items():
            second = second_hits.get(servant_id)
            if second is None:
                continue
            delta = max(abs(first[1][0] - second[1][0]), abs(first[1][1] - second[1][1]))
            if delta <= MATCH_CENTER_DELTA:
                hits[servant_id] = second
        return hits, second_image

    def _scan_owned_servants(self, rarity, candidates):
        self._focus_user(f"正在扫描{rarity}星从者，请稍候")
        if not self._run_pipeline("羁绊补齐-从者列表复位顶部"):
            return None
        mfaalog.info(f"[羁绊补齐] {rarity}星列表已回顶，先扫描首屏（未下滑）")
        owned = {}
        previous_crop = None
        unchanged = 0
        for round_index in range(MAX_SCAN_SWIPES + 1):
            if self.context.tasker.stopping:
                return None
            hits, image = self._stable_servant_hits(candidates)
            # 回顶跳转偶尔会在首次取帧时仍处于列表过渡帧。
            # 首屏零命中时再执行一次回顶并重拍，仍然先于任何
            # 下滑，避免把最顶部候选漏掉。
            if round_index == 0 and not hits:
                mfaalog.info(f"[羁绊补齐] {rarity}星首屏零命中，再次回顶后重拍")
                if not self._run_pipeline("羁绊补齐-从者列表复位顶部"):
                    return None
                hits, image = self._stable_servant_hits(candidates)
            for sid, hit in hits.items():
                owned[sid] = hit
            mfaalog.info(
                f"[羁绊补齐] {rarity}星库存扫描 {round_index + 1}/{MAX_SCAN_SWIPES + 1}："
                f"本屏命中={len(hits)}，累计={len(owned)}"
            )
            if round_index == MAX_SCAN_SWIPES:
                break
            x, y, width, height = self._scale_roi(LIST_ROI)
            crop = cv2.cvtColor(image[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY)
            if previous_crop is not None and previous_crop.shape == crop.shape:
                diff = float(cv2.absdiff(previous_crop, crop).mean())
                unchanged = unchanged + 1 if diff < 0.8 else 0
                if unchanged >= 2:
                    mfaalog.info(f"[羁绊补齐] {rarity}星列表已到底，提前结束扫描")
                    break
            previous_crop = crop
            if not self._run_pipeline("羁绊补齐-从者列表下滑扫描"):
                return None
        return owned

    def _select_scanned_servant(self, servant, rarity_candidates):
        # 记录目标定位过程中同星级全部稳定命中，供本地库目标缺失时
        # 一次性刷新候选，避免对每个过期 ID 都重新滑到底。
        self.last_servant_search_seen = set()
        if not self._run_pipeline("羁绊补齐-从者列表复位顶部"):
            return "failed"
        for round_index in range(MAX_SCAN_SWIPES + 1):
            # 最终回找时仍与同星级全部候选做横向区分。若只传入
            # 单个目标，一张外观相似的卡面只要越过绝对阈值就会被
            # 误当成目标，无法再利用 second-best margin 排除。
            hits, _image = self._stable_servant_hits(rarity_candidates)
            self.last_servant_search_seen.update(str(item_id) for item_id in hits)
            if round_index == 0 and not hits:
                if not self._run_pipeline("羁绊补齐-从者列表复位顶部"):
                    return "failed"
                hits, _image = self._stable_servant_hits(rarity_candidates)
                self.last_servant_search_seen.update(str(item_id) for item_id in hits)
            hit = hits.get(str(servant["id"]))
            if hit is not None:
                mfaalog.info(
                    f"[羁绊补齐] 最终选择 {servant['name']}({servant['id']})，"
                    f"feature={hit[0]:.4f}/{SERVANT_FEATURE_THRESHOLD:.2f}，"
                    f"轮次={round_index + 1}"
                )
                self.controller.post_click(*hit[1]).wait()
                return (
                    "selected"
                    if self._wait_for(self._in_formation_edit, 6.0)
                    else "failed"
                )
            if round_index < MAX_SCAN_SWIPES and not self._run_pipeline("羁绊补齐-从者列表下滑扫描"):
                return "failed"
        return "not_found"

    def _verify_servant_slot(self, slot, servant):
        templates = self._servant_templates(servant["id"], for_list=False)
        match = self._match_servant(self._shot(), templates, self._slot_roi(slot))
        score = match[0] if match else 0.0
        mfaalog.info(
            f"[羁绊补齐] 新从者复核：槽位{slot + 1} {servant['name']}({servant['id']}) "
            f"score={score:.4f}/{SERVANT_VERIFY_THRESHOLD:.2f}"
        )
        return match is not None and match[0] >= SERVANT_VERIFY_THRESHOLD

    @staticmethod
    def _slot_roi(slot):
        from formation_action import SLOT_ROIS
        return SLOT_ROIS[slot]

    def _preflight_servant_plan_equips(self, equips, slot, rarity):
        """从者落地前验证其礼装依赖，并恢复到同星级从者列表。"""
        if any(str(equip["id"]) in self.unavailable_equips for equip in equips):
            return "replan"
        pending = [
            equip for equip in equips
            if str(equip["id"]) not in self.available_equips
        ]
        if not pending:
            return "available"
        if not self._leave_servant_select():
            return "failed"
        result = self._ensure_plan_equips_available(pending)
        if result == "failed":
            return "failed"
        if not self._enter_servant_select_new(slot):
            return "failed"
        if not self._filter_servant_rarity(rarity):
            return "failed"
        return result

    def _fill_servants(self, fillable, current_servants):
        self.servant_list_prepared = False
        for slot in fillable:
            replacing_existing = slot in self.replaceable_slots
            old_servant = self.unspecified_servants_by_slot.get(slot)
            if not self._enter_servant_select_new(slot):
                return None
            chosen = None
            for rarity in self.rarity_order:
                candidates = self._servant_candidates(rarity)
                if not candidates:
                    mfaalog.info(f"[羁绊补齐] {rarity}星没有资料与图片完整的候选")
                    continue
                owned_ids = self.owned_servants_by_rarity.get(rarity)
                if owned_ids is not None:
                    source = "本地从者库" if self.local_servant_inventory_active else "复用库存扫描"
                    mfaalog.info(f"[羁绊补齐] {source}{rarity}星候选：{len(owned_ids)}名")
                    if not any(str(item["id"]) in owned_ids for item in candidates):
                        mfaalog.info(f"[羁绊补齐] {source}{rarity}星无匹配，切换下一星级")
                        continue
                if not self._filter_servant_rarity(rarity):
                    return None
                if owned_ids is None:
                    owned_hits = self._scan_owned_servants(rarity, candidates)
                    if owned_hits is None:
                        return None
                    owned_ids = set(owned_hits)
                    self.owned_servants_by_rarity[rarity] = owned_ids
                owned = [item for item in candidates if str(item["id"]) in owned_ids]
                if not owned:
                    source = "本地从者库" if self.local_servant_inventory_active else "完整扫描"
                    mfaalog.info(f"[羁绊补齐] {source}{rarity}星无匹配，切换下一星级")
                    continue
                for item in owned:
                    item["slot"] = slot
                ranking_servants = [
                    item for item in current_servants
                    if int(item.get("slot", -1)) != slot
                ]
                released_cost = (
                    int(old_servant.get("cost", 0))
                    if replacing_existing and old_servant is not None else 0
                )
                rank_budget = self.max_cost - self.used_cost + released_cost
                future_equip_slots = len(self.empty_equip_slots)
                if (
                    slot in self.truly_empty_servant_slots
                    and slot not in self.empty_equip_slots
                ):
                    future_equip_slots += 1
                while True:
                    ranked = rank_servants(
                        owned,
                        ranking_servants,
                        self.fixed_equips,
                        self._candidate_bond_equips(),
                        future_equip_slots,
                        rank_budget,
                        self.bond_base,
                    )
                    current_score = team_bond_score(
                        current_servants, self.fixed_equips, self.bond_base
                    )
                    should_replan = False
                    for row in ranked:
                        candidate = row["servant"]
                        if (
                            self.used_cost - released_cost + int(candidate["cost"])
                            > self.max_cost
                        ):
                            continue
                        net_gain = row["plan"].score - current_score
                        if replacing_existing and net_gain <= 0:
                            mfaalog.info(
                                f"[羁绊补齐] 保留槽位{slot + 1}原从者："
                                f"候选 {candidate['name']}({candidate['id']}) "
                                f"预估净羁绊增量={net_gain}"
                            )
                            continue
                        mfaalog.info(
                            f"[羁绊补齐] 候选 {candidate['name']}({candidate['id']})："
                            f"COST={candidate['cost']}，释放旧COST={released_cost}，"
                            f"预估净羁绊增量={net_gain}，"
                            f"匹配计划礼装={row['matched_equips']}"
                        )
                        availability = self._preflight_servant_plan_equips(
                            row["plan"].equips, slot, rarity
                        )
                        if availability == "failed":
                            return None
                        if availability == "replan":
                            should_replan = True
                            break
                        if self.local_servant_inventory_active:
                            self._focus_user(f"正在定位本地库从者：{candidate['name']}")
                        select_result = self._select_scanned_servant(candidate, candidates)
                        if select_result == "selected":
                            chosen = dict(candidate)
                            break
                        if select_result == "failed":
                            return None
                        if self.local_servant_inventory_active:
                            candidate_id = str(candidate["id"])
                            visible_ids = (
                                set(getattr(self, "last_servant_search_seen", set()))
                                & set(owned_ids)
                            )
                            removed_ids = set(owned_ids) - visible_ids
                            self.unavailable_servants.update(removed_ids)
                            owned_ids.intersection_update(visible_ids)
                            owned = [
                                item for item in candidates
                                if str(item["id"]) in owned_ids
                            ]
                            self._focus_user(
                                f"本地库与当前仓库不一致，已刷新{rarity}星候选："
                                f"{len(owned_ids)}名",
                                "orange",
                            )
                            mfaalog.warning(
                                f"[羁绊补齐] 本地从者目标未找到："
                                f"{candidate['name']}({candidate_id})；"
                                f"复用本次完整查找批量刷新{rarity}星候选，"
                                f"当前可见={len(owned_ids)}，排除={len(removed_ids)}"
                            )
                            should_replan = True
                            break
                    if should_replan:
                        continue
                    break
                if chosen is not None:
                    break
            if chosen is None:
                if not self._leave_servant_select():
                    return None
                mfaalog.warning(f"[羁绊补齐] bond_completion_partial: 槽位{slot + 1}无可用从者")
                if replacing_existing:
                    continue
                break
            if not self._verify_servant_slot(slot, chosen):
                return None
            previous_used_cost = self.used_cost
            cost = self._read_cost_consistent()
            if cost is None or cost[0] > cost[1]:
                return None
            expected_delta = int(chosen["cost"]) - (
                int(old_servant.get("cost", 0)) if old_servant is not None else 0
            )
            actual_delta = cost[0] - previous_used_cost
            if actual_delta != expected_delta:
                mfaalog.warning(
                    f"[羁绊补齐] 从者 COST 与索引不同：{chosen['name']} "
                    f"预计+{expected_delta}，实测+{actual_delta}；以 UI 为准"
                )
            self.used_cost, self.max_cost = cost
            self.added_servants[slot] = chosen
            self.local_templates[int(chosen["id"])] = self._servant_templates(chosen["id"], for_list=False)
            # 新补入的从者会新增一个可装礼装的空位。立即纳入后续候选评分，
            # 这样连续补多个从者时，第二个从者的排序也能看到第一个新增空位。
            if self._is_empty_equip_slot(self._shot(), slot):
                self.empty_equip_slots = sorted({*self.empty_equip_slots, slot})
            current_servants = [
                item for item in current_servants
                if int(item.get("slot", -1)) != slot
            ]
            current_servants.append(chosen)
            mfaalog.info(
                f"[羁绊补齐] 槽位{slot + 1}已补 {chosen['name']}({chosen['id']})，"
                f"COST={self.used_cost}/{self.max_cost}"
            )
            self._focus_user(
                f"已选择从者：{chosen['name']}，COST {self.used_cost}/{self.max_cost}"
            )
        return current_servants

    # ---------- 礼装规划与选择 ----------

    def _enter_equip_select_new(self, slot):
        if self._in_equip_select():
            return True
        x, _y, width, _height = self._slot_roi(slot)
        point = (int(round((x + width / 2) * self.sx)), int(round(EQUIP_SLOT_CLICK_Y * self.sy)))
        self.controller.post_click(*point).wait()
        if not self._wait_for(self._in_equip_select, 8.0):
            return False
        return self._run_pipeline("羁绊补齐-确认礼装选择界面")

    def _leave_equip_select_new(self):
        if self._in_formation_edit():
            return True
        if not self._run_pipeline("羁绊补齐-礼装选择返回"):
            return False
        return self._wait_for(self._in_formation_edit, 5.0)

    @staticmethod
    def _requires_limit_break(equip):
        return any(
            int(effect.get("strength_status", 0) or 0) == 99
            for effect in (equip.get("bond") or {}).get("atlas_effects") or []
            if isinstance(effect, dict)
        )

    def _filter_equip(self, equip):
        rarity = int(equip.get("rarity", 0))
        tag = str(equip.get("filter_tag") or "").strip()
        if not tag:
            return False
        if 1 <= rarity <= 5:
            star_recognition = {
                "param": {"template": f"整理礼物盒/{rarity}星未选中.png", "threshold": LIST_MATCH_THRESHOLD}
            }
        else:
            star_recognition = "DirectHit"
        tag_next = ["羁绊补齐-礼装筛找满破"] if self._requires_limit_break(equip) else ["羁绊补齐-礼装筛点决定"]
        self.context.override_pipeline({
            "羁绊补齐-礼装筛选星级": {
                "recognition": star_recognition,
                "next": ["羁绊补齐-礼装筛选标签"],
            },
            "羁绊补齐-礼装筛选标签": {
                "recognition": {
                    "param": {
                        "template": f"EquipFaces/礼装类别筛选项/{tag}.png",
                        "threshold": 0.92,
                    }
                },
                "next": tag_next,
            },
        })
        if not self._run_pipeline("羁绊补齐-礼装筛选准备"):
            return False
        if not getattr(self, "equip_list_prepared", False):
            if not self._run_pipeline("羁绊补齐-准备礼装列表"):
                return False
            self.equip_list_prepared = True
        return True

    def _stable_equip_match(self, equip):
        data = self.equip_list_templates.get(int(equip["id"]))
        if data is None:
            return None
        _name, template = data
        first = self._match_template(self._shot(), template, LIST_ROI)
        time.sleep(MATCH_STABILITY_SECONDS)
        second = self._match_template(self._shot(), template, LIST_ROI)
        if first is None or second is None:
            return None
        if first[0] < LIST_MATCH_THRESHOLD or second[0] < LIST_MATCH_THRESHOLD:
            return second
        delta = max(abs(first[1][0] - second[1][0]), abs(first[1][1] - second[1][1]))
        return second if delta <= MATCH_CENTER_DELTA else None

    def _scan_equip_owned(self, equip):
        """完整扫描筛选后的礼装列表，只判断库存，不点击礼装。"""
        if not self._run_pipeline("羁绊补齐-礼装列表复位顶部"):
            mfaalog.error(
                f"[羁绊补齐] bond_completion_equip_preflight_failed: "
                f"列表回顶失败 {equip['name']}({equip['id']})"
            )
            return "failed"
        best_score = 0.0
        best_round = 0
        for round_index in range(MAX_EQUIP_SWIPES + 1):
            match = self._stable_equip_match(equip)
            if match is not None and match[0] > best_score:
                best_score = float(match[0])
                best_round = round_index + 1
            if match is not None and match[0] >= LIST_MATCH_THRESHOLD:
                mfaalog.info(
                    f"[羁绊补齐] 礼装库存预检存在：{equip['name']}({equip['id']}) "
                    f"score={match[0]:.4f}，轮次={round_index + 1}"
                )
                return "available"
            if (
                round_index < MAX_EQUIP_SWIPES
                and not self._run_pipeline("羁绊补齐-礼装列表下滑查找")
            ):
                mfaalog.error(
                    f"[羁绊补齐] bond_completion_equip_preflight_failed: "
                    f"下滑扫描失败 {equip['name']}({equip['id']})，"
                    f"轮次={round_index + 1}"
                )
                return "failed"
        mfaalog.info(
            f"[羁绊补齐] 礼装库存预检不存在：{equip['name']}({equip['id']})，"
            f"完整轮次={MAX_EQUIP_SWIPES + 1}，最高分={best_score:.4f}，"
            f"最高分轮次={best_round or '无'}"
        )
        return "not_found"

    def _preflight_equip(self, equip):
        """使用任一本地从者礼装位预检一张礼装，返回后不改变编队。"""
        equip_id = str(equip["id"])
        if equip_id in self.available_equips:
            return "available"
        if equip_id in self.unavailable_equips:
            return "not_found"
        self._focus_user(f"正在确认礼装库存：{equip['name']}")
        if not self.equip_probe_slots:
            mfaalog.error(
                f"[羁绊补齐] bond_completion_equip_preflight_failed: "
                f"无本地从者礼装位可预检 {equip['name']}({equip_id})"
            )
            return "failed"
        probe_slot = self.equip_probe_slots[0]
        if not self._enter_equip_select_new(probe_slot):
            mfaalog.error(
                f"[羁绊补齐] bond_completion_equip_preflight_failed: "
                f"未进入礼装列表 {equip['name']}({equip_id})"
            )
            return "failed"
        if not self._filter_equip(equip):
            mfaalog.error(
                f"[羁绊补齐] bond_completion_equip_preflight_failed: "
                f"筛选失败 {equip['name']}({equip_id})"
            )
            return "failed"
        result = self._scan_equip_owned(equip)
        if not self._leave_equip_select_new():
            mfaalog.error(
                f"[羁绊补齐] bond_completion_equip_preflight_failed: "
                f"预检后未返回编队页 {equip['name']}({equip_id})"
            )
            return "failed"
        if result == "available":
            self.available_equips.add(equip_id)
        elif result == "not_found":
            self.unavailable_equips.add(equip_id)
        return result

    def _ensure_plan_equips_available(self, equips):
        """确认方案依赖礼装；新缺失项要求调用者重新计算整个当前方案。"""
        for equip in equips:
            equip_id = str(equip["id"])
            if equip_id in self.available_equips:
                continue
            result = self._preflight_equip(equip)
            if result == "failed":
                return "failed"
            if result == "not_found":
                self._focus_user(
                    f"未找到礼装“{equip['name']}”，正在重新规划",
                    "orange",
                )
                mfaalog.warning(
                    f"[羁绊补齐] 礼装不可用，触发联合重规划："
                    f"{equip['name']}({equip_id})"
                )
                return "replan"
        return "available"

    def _find_select_equip(self, equip):
        if not self._run_pipeline("羁绊补齐-礼装列表复位顶部"):
            return "failed"
        for round_index in range(MAX_EQUIP_SWIPES + 1):
            match = self._stable_equip_match(equip)
            if match is not None and match[0] >= LIST_MATCH_THRESHOLD:
                mfaalog.info(
                    f"[羁绊补齐] 礼装命中 {equip['name']}({equip['id']}) "
                    f"score={match[0]:.4f}，轮次={round_index + 1}"
                )
                self.controller.post_click(*match[1]).wait()
                time.sleep(0.3)
                confirm = self._match_template(self._shot(), self.equip_confirm_marker)
                if confirm is not None and confirm[0] >= 0.80:
                    self.controller.post_click(*confirm[1]).wait()
                    if self._wait_for(self._in_formation_edit, 6.0):
                        return "selected"
            if round_index < MAX_EQUIP_SWIPES and not self._run_pipeline("羁绊补齐-礼装列表下滑查找"):
                return "failed"
        return "not_found"

    def _select_equip(self, slot, equip):
        if not self._enter_equip_select_new(slot):
            return "failed"
        if not self._filter_equip(equip):
            return "failed"
        result = self._find_select_equip(equip)
        if result == "not_found" and not self._leave_equip_select_new():
            return "failed"
        return result

    def _unequip_slot(self, slot):
        """卸下指定本地槽位礼装，并以 UI COST 与空槽状态复核。"""
        if not self._enter_equip_select_new(slot):
            return False
        if not self._run_pipeline("羁绊补齐-卸下当前礼装"):
            return False
        if not self._wait_for(self._in_formation_edit, 6.0):
            return False
        if not self._is_empty_equip_slot(self._shot(), slot):
            return False
        cost = self._read_cost_consistent()
        if cost is None:
            return False
        self.used_cost, self.max_cost = cost
        return True

    def _unequip_added_equip(self, slot):
        """只回退本 Action 新增的礼装。"""
        equip = self.added_equips.get(slot)
        if equip is None or not self._unequip_slot(slot):
            return False
        self.added_equips.pop(slot, None)
        self.empty_equip_slots = sorted({*self.empty_equip_slots, slot})
        mfaalog.warning(
            f"[羁绊补齐] 已卸下本阶段新增礼装 {equip['name']}({equip['id']})，"
            f"COST={self.used_cost}/{self.max_cost}"
        )
        return True

    def _fill_equips(self, current_servants):
        self.equip_list_prepared = False
        slots = [slot for slot in self.empty_equip_slots if slot not in self.added_equips]
        while slots:
            used_ids = {str(item.get("id")) for item in self.fixed_equips}
            used_ids.update(str(item.get("id")) for item in self.added_equips.values())
            candidates = [
                item for item in self._candidate_bond_equips()
                if str(item["id"]) not in used_ids
            ]
            plan = optimize_equips(
                current_servants,
                [*self.fixed_equips, *self.added_equips.values()],
                candidates,
                len(slots),
                self.max_cost - self.used_cost,
                self.bond_base,
            )
            if not plan.equips:
                break
            availability = self._ensure_plan_equips_available(plan.equips)
            if availability == "failed":
                return False
            if availability == "replan":
                continue
            equip = dict(plan.equips[0])
            slot = slots[0]
            if self.used_cost + int(equip["cost"]) > self.max_cost:
                self.unavailable_equips.add(str(equip["id"]))
                continue
            if self.local_equip_inventory_active:
                self._focus_user(f"正在定位本地库礼装：{equip['name']}")
            result = self._select_equip(slot, equip)
            if result == "not_found":
                if self.local_equip_inventory_active:
                    equip_id = str(equip["id"])
                    self.available_equips.discard(equip_id)
                    self.unavailable_equips.add(equip_id)
                    self._focus_user(
                        f"本地记录的礼装“{equip['name']}”当前不可用，正在重新规划",
                        "orange",
                    )
                    mfaalog.warning(
                        f"[羁绊补齐] 本地礼装记录未在当前筛选中找到："
                        f"{equip['name']}({equip_id})；可能未满破或库存已变化"
                    )
                    continue
                mfaalog.error(
                    f"[羁绊补齐] bond_completion_select_verify_failed: "
                    f"预检存在但装备阶段未找到 {equip['name']}({equip['id']})"
                )
                return False
            if result != "selected":
                return False
            match = self._match_equip_id(self._shot(), equip["id"], slot)
            if match is None or match[0] < EQUIP_VERIFY_THRESHOLD:
                score = match[0] if match else 0.0
                mfaalog.error(
                    f"[羁绊补齐] 礼装复核失败：槽位{slot + 1} {equip['name']}({equip['id']}) "
                    f"score={score:.4f}/{EQUIP_VERIFY_THRESHOLD:.2f}"
                )
                return False
            previous_used_cost = self.used_cost
            cost = self._read_cost_consistent()
            if cost is None:
                return False
            self.used_cost, self.max_cost = cost
            if self.used_cost > self.max_cost:
                # Atlas COST 仅是点击前预算；若 UI 显示仍然超限，就撤销刚加入的
                # 礼装并将其列入本轮不可用集合，再规划更低 COST 的候选。
                self.added_equips[slot] = equip
                self.unavailable_equips.add(str(equip["id"]))
                if not self._unequip_added_equip(slot):
                    return False
                continue
            actual_delta = cost[0] - previous_used_cost
            if actual_delta != int(equip["cost"]):
                mfaalog.warning(
                    f"[羁绊补齐] 礼装 COST 与索引不同：{equip['name']} "
                    f"预计+{equip['cost']}，实测+{actual_delta}；以 UI 为准"
                )
            self.added_equips[slot] = equip
            slots.pop(0)
            mfaalog.info(
                f"[羁绊补齐] 槽位{slot + 1}已补礼装 {equip['name']}({equip['id']})，"
                f"COST={self.used_cost}/{self.max_cost}"
            )
            self._focus_user(
                f"已装备礼装：{equip['name']}，COST {self.used_cost}/{self.max_cost}"
            )
        return True

    # ---------- 最终复核与安全退出 ----------

    def _verify_final_state(self):
        image = self._shot()
        for item in self.expected:
            slot = item["slot"]
            if item["kind"] == "LOCAL":
                templates = self.local_templates.get(item["svt_id"], [])
                match = self._match_servant(image, templates, self._slot_roi(slot))
                if match is None or match[0] < SERVANT_VERIFY_THRESHOLD:
                    return False
            elif item["kind"] == "SUPPORT":
                match = self._match_template(image, self.support_marker, self._slot_roi(slot))
                if match is None or match[0] < 0.75:
                    return False
        for slot, servant in self.added_servants.items():
            templates = self._servant_templates(servant["id"], for_list=False)
            match = self._match_servant(image, templates, self._slot_roi(slot))
            if match is None or match[0] < SERVANT_VERIFY_THRESHOLD:
                return False
        for slot, servant in self.unspecified_servants_by_slot.items():
            if slot in self.added_servants:
                continue
            templates = self._servant_templates(servant["id"], for_list=False)
            match = self._match_servant(image, templates, self._slot_roi(slot))
            if match is None or match[0] < SERVANT_VERIFY_THRESHOLD:
                return False
        for slot, locked in self.locked_unspecified_equips.items():
            before = locked["snapshot"]
            current = self._equip_slot_snapshot(image, slot)
            if before.size == 0 or current.shape != before.shape:
                return False
            before_gray = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
            current_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
            score = float(cv2.matchTemplate(
                current_gray, before_gray, cv2.TM_CCOEFF_NORMED
            )[0, 0])
            mfaalog.info(
                f"[羁绊补齐] 锁定其他位置礼装复核：槽位{slot + 1} "
                f"score={score:.4f}/{LOCKED_EQUIP_VERIFY_THRESHOLD:.2f}"
            )
            if not np.isfinite(score) or score < LOCKED_EQUIP_VERIFY_THRESHOLD:
                return False
        for item in self.expected:
            if item["kind"] == "LOCAL" and item.get("equip_id"):
                match = self._match_equip_id(image, item["equip_id"], item["slot"])
                if match is None or match[0] < EQUIP_VERIFY_THRESHOLD:
                    return False
        for slot, equip in self.added_equips.items():
            match = self._match_equip_id(image, equip["id"], slot)
            if match is None or match[0] < EQUIP_VERIFY_THRESHOLD:
                return False
        return True

    def _on_confirm_page(self):
        match = self._match_template(self._shot(), self.config_marker)
        return match is not None and match[0] >= 0.80

    def _focus_user(self, message, color=None):
        """通过专用 DirectHit 节点输出少量用户可见的阶段信息。"""
        context = getattr(self, "context", None)
        if context is None:
            return
        safe_message = html.escape(str(message), quote=True)
        if color:
            safe_message = f'<span style="color: {color};">{safe_message}</span>'
        try:
            context.override_pipeline({
                "羁绊补齐-用户提示": {
                    "focus": {"Node.Recognition.Starting": safe_message}
                }
            })
            context.run_task("羁绊补齐-用户提示")
        except Exception as exc:
            # 用户提示不得影响编队流程本身；异常仍保留在普通日志中供排查。
            mfaalog.warning(f"[羁绊补齐] 用户提示输出失败: {exc}")

    def _abort_safe(self, reason):
        if getattr(self, "debug_preserve_failure", False):
            self._focus_user("羁绊优化遇到错误，已保留现场", "red")
            mfaalog.error(
                f"[羁绊补齐] {reason}；测试模式保留故障现场，不执行返回或取消操作"
            )
            return CustomAction.RunResult(success=False)
        self._focus_user("羁绊优化未完成，正在恢复原编队", "orange")
        mfaalog.warning(f"[羁绊补齐] {reason}；尝试取消本阶段并保留第一阶段编队")
        if self._in_equip_select():
            self._leave_equip_select_new()
        elif self._in_servant_select():
            self._leave_servant_select()
        if self._in_formation_edit() and self._run_pipeline("羁绊补齐-取消配置"):
            # 队伍发生过变化时，游戏会询问是否放弃当前改动。右侧“决定”才会
            # 恢复进入本 Action 前的第一阶段队伍；左侧“取消”会留在编辑页。
            self._run_pipeline("羁绊补齐-取消变更确认")
            self.opened_edit = False
            if self._wait_for(self._on_confirm_page, 6.0):
                self._focus_user("羁绊优化已取消，原编队已恢复", "orange")
                mfaalog.warning("[羁绊补齐] bond_completion_aborted_safe: 已恢复第一阶段编队")
                return CustomAction.RunResult(success=True)
        self._focus_user("羁绊优化失败，且无法确认原编队已恢复", "red")
        mfaalog.error("[羁绊补齐] bond_completion_restore_failed: 无法确认已恢复第一阶段编队")
        return CustomAction.RunResult(success=False)

    def _result_fail(self, message):
        self._focus_user("无法开始羁绊优化，请查看错误日志", "red")
        mfaalog.error(f"[羁绊补齐] {message}")
        return CustomAction.RunResult(success=False)
