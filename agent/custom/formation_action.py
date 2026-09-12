# -*- coding: utf-8 -*-
"""Chaldea 自动编队与手动编队核对。

该 Action 仅从编队界面开始工作：读取 Chaldea BattleShareData 后，先拖拽调整
已有本地从者与助战的位置，再打开从者选择页替换不匹配的本地从者。原生自动
战斗相关 Action 不依赖、也不修改本模块。手动核对 Action 仅在从者身份相同的
情况下复用拖拽换位，不进入从者或礼装仓库。
"""

import glob
import json
import os
import re
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

_CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_CUSTOM_DIR)
_PROJECT_DIR = os.path.dirname(_AGENT_DIR)
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from chaldea import fetch_share_data
import mfaalog
from battle.core.models import Confidence, FormationSlot
from battle.runtime.formation_session import context_session, sessions


BASE_W, BASE_H = 1280, 720

# 用户提供的六个编队槽位（1280 x 720 基准坐标）。
SLOT_ROIS = (
    (41, 160, 187, 276),
    (241, 160, 187, 276),
    (440, 160, 187, 276),
    (654, 160, 187, 276),
    (854, 160, 187, 276),
    (1055, 160, 188, 276),
)
# 编队卡片下方的概念礼装图区域。它不包含在从者槽位 ROI 内：从者卡片正好在
# y=436 结束，礼装从 y=436 开始，因此必须使用独立 ROI 才不会把从者立绘误判为
# 礼装。
EQUIP_TEAM_ROIS = (
    (41, 436, 187, 120),
    (241, 436, 187, 120),
    (440, 436, 187, 120),
    (654, 436, 187, 120),
    (854, 436, 187, 120),
    (1055, 436, 188, 120),
)

# 真机编队验证的最低有效命中约为 0.649；取 0.62 以降低误匹配，同时保留
# 资源加载、抗锯齿和不同灵基图带来的合理余量。
FACE_THRESHOLD = 0.62
# 编队礼装资源与编队头像区域一一对应，直接用原始模板复核。真机装入
# “来自NFF的爱”后仓库列表稳定命中 0.9430，编队页连续复核为 0.8297；
# team 阈值使用 0.82，仓库列表仍保持 0.90，避免扩大选卡误匹配范围。
EQUIP_TEAM_THRESHOLD = 0.82
EQUIP_LIST_THRESHOLD = 0.90
SUPPORT_THRESHOLD = 0.75
SWAP_DRAG_DURATION = 1200  # ms；长按并拖至目标槽中心
SWAP_VERIFY_TIMEOUT_SECONDS = 6.0
SWAP_VERIFY_INTERVAL_SECONDS = 0.5
SERVANT_REPLACE_VERIFY_TIMEOUT_SECONDS = 10.0
SERVANT_REPLACE_VERIFY_INTERVAL_SECONDS = 0.5
SERVANT_LIST_STABILITY_MAX_CHECKS = 3
SERVANT_LIST_STABILITY_INTERVAL_SECONDS = 0.4
SERVANT_LIST_STABILITY_CENTER_DELTA_PX = 6
SELECT_PAGE_ENTER_TIMEOUT_SECONDS = 8.0
EMPTY_SLOT_STD_THRESHOLD = 25.0
EMPTY_SLOT_CHANNEL_DELTA_THRESHOLD = 6.0
FORMATION_CONFIRM_ROI = (724, 583, 232, 101)
FORMATION_CONFIRM_DELAY_SECONDS = 1.0
FORMATION_CONFIRM_APPEAR_TIMEOUT_SECONDS = 3.0
SELECT_PAGE_SCALE_ROI = (2, 598, 110, 122)
SELECT_PAGE_SCALE_THRESHOLD = 0.85
EQUIP_FILTER_TAG_THRESHOLD = 0.92
EQUIP_CARD_SELECT_SETTLE_SECONDS = 0.3
EQUIP_CONFIRM_SETTLE_SECONDS = 1.0
EQUIP_SELECT_RETURN_TIMEOUT_SECONDS = 5.0
EQUIP_SWIPE_SETTLE_SECONDS = 0.5
EQUIP_MATCH_STABILITY_INTERVAL_SECONDS = 0.5
EQUIP_MATCH_STABILITY_MAX_CHECKS = 3
EQUIP_MATCH_CENTER_DELTA_PX = 6
EMPTY_EQUIP_STD_MAX = 35.0
EMPTY_EQUIP_SATURATED_RATIO_MAX = 0.30
EQUIP_SLOT_VERIFY_TIMEOUT_SECONDS = 6.0
SCREENSHOT_SETTLE_SECONDS = 0.2
SLOT_LAYOUT_VERIFY_TIMEOUT_SECONDS = 5.0
SLOT_LAYOUT_VERIFY_INTERVAL_SECONDS = 0.4
TEMPLATE_APPEAR_TIMEOUT_SECONDS = 3.0
MAX_REORDER_OPS = 12
MAX_FIND_SERVANT_ROUNDS = 80
MAX_SERVANT_SELECT_ATTEMPTS = 3
MAX_FIND_EQUIP_ROUNDS = 30
SWIPE_LIST_BEGIN = (600, 560)
SWIPE_LIST_END = (600, 200)
# 从者与礼装仓库的“复位到顶部 / 单次下滑 / 等待”均维护在自动编队 pipeline
# 中；此处保留的坐标仅供礼装筛选弹层中的满破选项查找使用。
# 概念礼装显示在编队卡片下部。该纵坐标由 1280 x 720 编队截图标定，横向
# 则始终取对应从者槽中心，避免点到从者头像而进入错误的选择页。
EQUIP_SLOT_CLICK_Y = 477
GRAND_EQUIP_POPUP_CONTENT_ROI = (267, 218, 776, 171)
GRAND_EQUIP_POPUP_THRESHOLD = 0.80

SUPPORT_TYPES = {"friend", "fixed", "npc"}
CLASS_TEMPLATE = {
    "saber": "剑士",
    "archer": "弓兵",
    "lancer": "枪兵",
    "rider": "骑兵",
    "caster": "魔术师",
    "assassin": "暗杀者",
    "berserker": "狂战士",
    "ruler": "裁定者",
    "avenger": "复仇者",
    "moonCancer": "月之癌",
    "alterEgo": "他人格",
    "foreigner": "降临者",
    "pretender": "伪装者",
    "shielder": "盾兵",
    "beast": "兽",
    "unBeast": "兽",
}


def _norm_img(image):
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
    return array.astype(np.uint8, copy=True)


def _read_image(path):
    if not path or not os.path.isfile(path):
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


@AgentServer.custom_action("auto_formation_from_chaldea")
class AutoFormationFromChaldea(CustomAction):
    """将 Chaldea 队伍编入当前游戏编队。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            self.context = context
            self.controller = context.tasker.controller
            self.direct_formation_mode = False
            node = context.get_node_data(argv.node_name) or {}
            attach = node.get("attach") or {}
            # 单一输入框: 链接/ID/本地文件路径合用 chaldea_import_source,
            # 兼容旧版分离字段的 chaldea_import_source_file
            source = str(
                attach.get("chaldea_import_source")
                or attach.get("chaldea_import_source_file")
                or ""
            ).strip()
            # 兼容未配置新选项的旧任务：未传值时仍默认编入礼装。
            auto_equip_value = attach.get("auto_equip", True)
            self.auto_equip = str(auto_equip_value).strip().lower() not in {
                "0", "false", "no", "off", "否",
            }
            support_substitution_value = attach.get(
                "use_support_substitution", False
            )
            self.use_support_substitution = (
                str(support_substitution_value).strip().lower()
                not in {"0", "false", "no", "off", "否", ""}
            )
            self.equip_missing_policy = str(
                attach.get("equip_missing_policy") or "skip"
            ).strip()
            if self.equip_missing_policy not in {"skip", "allow_non_limit_break"}:
                mfaalog.warning(
                    f"[自动编队] 未知礼装缺失策略 {self.equip_missing_policy!r}，使用 skip"
                )
                self.equip_missing_policy = "skip"
            if not source:
                self._fail("invalid_chaldea_team: 未提供 Chaldea 分享链接/ID")
                return CustomAction.RunResult(success=False)

            self._init_paths()
            self._init_scale()
            share_data, _quest_id, _team_id = fetch_share_data(source)
            expected = self._build_expected(share_data)
            if expected is None:
                return CustomAction.RunResult(success=False)
            self.expected = expected

            expected_support_count = sum(item["kind"] == "SUPPORT" for item in expected)
            if expected_support_count > 1:
                self._fail("invalid_chaldea_team: 当前编队仅支持一个助战槽")
                return CustomAction.RunResult(success=False)

            if self.auto_equip:
                self._load_equip_database()
            else:
                self.equip_database = {}
            self._prepare_target_templates()
            self.list_view_prepared = False
            self.equip_list_view_prepared = False
            current = self._detect_slots_stable()
            if current is None:
                return CustomAction.RunResult(success=False)
            self._log_layout("初始", current)
            if not self._configure_support_target(current, expected_support_count):
                return CustomAction.RunResult(success=False)
            expected_support_count = sum(
                item["kind"] == "SUPPORT" for item in self.expected
            )

            # 在点击“配置变更”前判断当前队伍的复用价值。助战不参与统计；当至少
            # 一半的目标本地从者不匹配时，整队清空比逐个拖动、替换更直接。清空
            # 流程结束后游戏已处于编辑状态，不应再次点击“配置变更”。
            should_clear = self._should_clear_formation(current)
            already_editing = self._confirmed_now(self._in_formation_edit)
            if should_clear:
                if already_editing:
                    # “编队编辑”入口只存在于编队确认页。该分支仅用于测试中断或
                    # 用户已手动进入编辑页后的恢复，继续使用原有增量编队流程。
                    mfaalog.info(
                        "[自动编队] 不匹配从者达到清空阈值，但已处于编队编辑页；"
                        "跳过清空和配置变更，继续增量编队"
                    )
                else:
                    clear_result = self._try_clear_formation()
                    if clear_result == "failed":
                        self._fail("formation_clear_failed: 未能清空当前编队")
                        return CustomAction.RunResult(success=False)
                    if clear_result == "cleared" and not self._wait_for(
                        self._in_formation_edit, 5.0
                    ):
                        self._fail("formation_clear_failed: 清空编队后未进入编辑状态")
                        return CustomAction.RunResult(success=False)
                    current = self._detect_slots_stable()
                    if current is None:
                        return CustomAction.RunResult(success=False)
                    title = "清空后" if clear_result == "cleared" else "跳过清空后"
                    self._log_layout(title, current)
                    if clear_result == "skipped" and not self._confirmed_now(
                        self._in_formation_edit
                    ):
                        if not self._run_pipeline("自动编队-打开配置"):
                            self._fail("not_on_formation_page: 跳过解散后未找到配置变更按钮")
                            return CustomAction.RunResult(success=False)
                        if not self._wait_for(self._in_formation_edit, 5.0):
                            self._fail("not_on_formation_page: 跳过解散后未进入配置变更页")
                            return CustomAction.RunResult(success=False)
            elif not already_editing:
                if not self._run_pipeline("自动编队-打开配置"):
                    self._fail("not_on_formation_page: 未找到配置变更按钮")
                    return CustomAction.RunResult(success=False)
                if not self._wait_for(self._in_formation_edit, 5.0):
                    self._fail("not_on_formation_page: 点击配置变更后未进入编辑状态")
                    return CustomAction.RunResult(success=False)
            else:
                mfaalog.info("[自动编队] 已处于编队编辑页，跳过配置变更点击")

            if not self._relocate_unexpected_support(current, expected_support_count):
                return CustomAction.RunResult(success=False)
            if not self._reorder_existing():
                return CustomAction.RunResult(success=False)
            if not self._replace_local_servants():
                return CustomAction.RunResult(success=False)

            current = self._detect_slots_stable()
            if current is None:
                return CustomAction.RunResult(success=False)
            self._log_layout("最终复核", current)
            mismatch = self._first_mismatch(current)
            if mismatch is not None:
                self._fail(f"final_formation_mismatch: 槽位{mismatch + 1}未匹配")
                return CustomAction.RunResult(success=False)

            self._log_support_identity_if_possible(current)
            if self.auto_equip:
                if not self._replace_equips():
                    return CustomAction.RunResult(success=False)
                # 礼装选择不应改变从者布局；决定前再做一次从者/助战复核，防止界面
                # 加载或误触导致带着错误队伍提交。
                current = self._detect_slots_stable()
                if current is None:
                    return CustomAction.RunResult(success=False)
                mismatch = self._first_mismatch(current)
                if mismatch is not None:
                    self._fail(f"final_formation_mismatch: 礼装编队后槽位{mismatch + 1}未匹配")
                    return CustomAction.RunResult(success=False)
            else:
                mfaalog.info("[自动编队] 已关闭自动编成礼装，跳过礼装阶段")
            if not self._run_pipeline("自动编队-编队决定"):
                self._fail("formation_confirm_failed: 未能点击编队决定")
                return CustomAction.RunResult(success=False)
            self._confirm_formation_change_if_present()
            mfaalog.info("[自动编队] 编队完成，已点击编队决定")
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            mfaalog.error(f"[自动编队] 异常: {exc}\n{traceback.format_exc()}")
            return CustomAction.RunResult(success=False)

    # ---------- Chaldea 队伍规格 ----------

    def _build_expected(self, share_data):
        if not isinstance(share_data, dict) or not isinstance(share_data.get("team"), dict):
            self._fail("invalid_chaldea_team: 导入数据缺少 team")
            return None
        team = share_data["team"]
        raw_slots = list(team.get("onFieldSvts") or [])[:3] + list(team.get("backupSvts") or [])[:3]
        raw_slots += [None] * (6 - len(raw_slots))
        expected = []
        for index, item in enumerate(raw_slots[:6]):
            if item is None:
                expected.append({
                    "kind": "EMPTY", "svt_id": None, "equip_id": None,
                    "equip_limit_break": False, "grand_svt": False, "slot": index,
                })
                continue
            if not isinstance(item, dict):
                self._fail(f"invalid_chaldea_team: 槽位{index + 1}数据类型错误")
                return None
            support_type = str(item.get("supportType") or "").lower()
            svt_id = item.get("svtId")
            equip_id, equip_limit_break = self._extract_equip(item)
            grand_svt = item.get("grandSvt") is True
            if support_type in SUPPORT_TYPES:
                if not isinstance(svt_id, int) or svt_id <= 0:
                    self._fail(
                        f"invalid_chaldea_team: 助战槽位{index + 1}没有有效 svtId"
                    )
                    return None
                expected.append({
                    "kind": "SUPPORT", "svt_id": svt_id, "equip_id": equip_id,
                    "equip_limit_break": equip_limit_break, "grand_svt": grand_svt,
                    "slot": index,
                })
                continue
            if not isinstance(svt_id, int) or svt_id <= 0:
                self._fail(f"invalid_chaldea_team: 槽位{index + 1}没有有效 svtId")
                return None
            expected.append({
                "kind": "LOCAL", "svt_id": svt_id, "equip_id": equip_id,
                "equip_limit_break": equip_limit_break, "grand_svt": grand_svt,
                "slot": index,
            })
        return expected

    @staticmethod
    def _extract_equip(item):
        """兼容 Chaldea 当前的 equip1 与旧版 ceId/ceLimitBreak 字段。"""
        equip1 = item.get("equip1")
        if isinstance(equip1, dict) and equip1.get("id") is not None:
            equip_id = equip1.get("id")
            limit_break = equip1.get("limitBreak", item.get("ceLimitBreak", False))
        else:
            equip_id = item.get("ceId")
            limit_break = item.get("ceLimitBreak", False)
        try:
            equip_id = int(equip_id) if equip_id is not None else None
        except (TypeError, ValueError):
            equip_id = None
        return equip_id, bool(limit_break)

    # ---------- 路径、截图与模板 ----------

    def _init_paths(self):
        config = self.context.get_node_data("资源包配置") or {}
        package = str((config.get("attach") or {}).get("resource_package") or "base").strip()
        layer = "cn" if package == "cn" else "base"
        roots = []
        for root in (_PROJECT_DIR, os.path.dirname(_PROJECT_DIR)):
            for current_layer in (layer, "base"):
                image_root = os.path.join(root, "assets", "resource", current_layer, "image")
                if os.path.isdir(image_root) and image_root not in roots:
                    roots.append(image_root)
                packaged_root = os.path.join(root, "resource", current_layer, "image")
                if os.path.isdir(packaged_root) and packaged_root not in roots:
                    roots.append(packaged_root)
        self.image_roots = roots
        self.narrow_dirs = [os.path.join(root, "NarrowFigures") for root in roots]
        self.face_dirs = [os.path.join(root, "servant_face") for root in roots]
        self.equip_list_dirs = [os.path.join(root, "EquipFaces", "list") for root in roots]
        self.equip_team_dirs = [os.path.join(root, "EquipFaces", "team") for root in roots]

    def _init_scale(self):
        self.sx = self.sy = 1.0
        screenshot = self._shot()
        if screenshot is not None:
            height, width = screenshot.shape[:2]
            self.sx = width / BASE_W
            self.sy = height / BASE_H
            mfaalog.info(
                f"[自动编队] 实际分辨率 {width}x{height}，坐标缩放 {self.sx:.3f}x{self.sy:.3f}"
            )

    def _shot(self):
        """触发截图并读取控制器缓存，避开底层作业状态异常挂起。

        Maa 的 Agent 控制器在部分页面会出现“图像帧已经送达、但截图 job 的
        status 查询不返回”的问题。不要在这里调用 job.wait()/job.done；先发起
        截图请求，稍作等待后直接取得最新缓存帧。
        """
        try:
            self.controller.post_screencap()
            time.sleep(SCREENSHOT_SETTLE_SECONDS)
        except Exception as exc:
            mfaalog.warning(f"[自动编队] 请求截图失败: {exc}")
        try:
            cached = _norm_img(self.controller.cached_image)
        except Exception as exc:
            mfaalog.warning(f"[自动编队] 读取截图缓存失败: {exc}")
            cached = None
        if cached is not None:
            return cached
        mfaalog.warning("[自动编队] 截图缓存不可用")
        return None

    def _scale_roi(self, roi):
        x, y, width, height = roi
        return (
            int(round(x * self.sx)), int(round(y * self.sy)),
            int(round(width * self.sx)), int(round(height * self.sy)),
        )

    def _slot_center(self, index):
        x, y, width, height = SLOT_ROIS[index]
        return int(round((x + width / 2) * self.sx)), int(round((y + height / 2) * self.sy))

    def _equip_slot_center(self, index):
        x, _y, width, _height = SLOT_ROIS[index]
        return (
            int(round((x + width / 2) * self.sx)),
            int(round(EQUIP_SLOT_CLICK_Y * self.sy)),
        )

    def _template_path(self, relative):
        for root in self.image_roots:
            candidate = os.path.join(root, relative.replace("/", os.sep))
            if os.path.isfile(candidate):
                return candidate
        return ""

    def _load_named_template(self, relative):
        return _read_image(self._template_path(relative))

    def _load_servant_templates(self, svt_id, directories):
        sid = str(svt_id)
        if len(sid) <= 2:
            return []
        prefix = sid[:-2]
        matcher = re.compile(rf"^f_{re.escape(prefix)}\d{{3}}d?\.png$", re.IGNORECASE)
        result, seen = [], set()
        for directory in directories:
            for path in glob.glob(os.path.join(directory, "f_*.png")):
                name = os.path.basename(path)
                if name in seen or not matcher.match(name):
                    continue
                template = _read_image(path)
                if template is not None:
                    result.append((name, template))
                    seen.add(name)
        return result

    def _load_equip_template(self, equip_id, directories):
        filename = f"f_{equip_id}0.png"
        for directory in directories:
            path = os.path.join(directory, filename)
            template = _read_image(path)
            if template is not None:
                return filename, template
        return None

    def _load_equip_database(self):
        path = os.path.join(_CUSTOM_DIR, "equip_list.json")
        try:
            with open(path, encoding="utf-8-sig") as file:
                equips = json.load(file).get("equips", [])
        except Exception as exc:
            raise RuntimeError(f"equip_database_unavailable: {exc}") from exc
        self.equip_database = {
            str(item.get("id")): item for item in equips if str(item.get("id") or "")
        }

    def _get_equip_info(self, equip_id):
        return self.equip_database.get(str(equip_id))

    def _prepare_target_templates(self):
        self.local_templates = {}
        self.support_templates = {}
        self.equip_team_templates = {}
        self.equip_list_templates = {}
        for item in self.expected:
            svt_id = item["svt_id"]
            if item["kind"] == "LOCAL" and svt_id not in self.local_templates:
                templates = self._load_servant_templates(svt_id, self.narrow_dirs)
                if not templates:
                    templates = self._load_servant_templates(svt_id, self.face_dirs)
                if not templates:
                    raise RuntimeError(f"resource_missing: svtId={svt_id}")
                self.local_templates[svt_id] = templates
            if item["kind"] == "SUPPORT" and isinstance(svt_id, int):
                templates = self._load_servant_templates(svt_id, self.narrow_dirs)
                if not templates:
                    templates = self._load_servant_templates(svt_id, self.face_dirs)
                if not templates:
                    raise RuntimeError(f"resource_missing: 助战 svtId={svt_id}")
                self.support_templates[svt_id] = templates
            if not self.auto_equip:
                continue
            equip_id = item.get("equip_id")
            if item["kind"] != "LOCAL" or item.get("grand_svt") or not equip_id:
                continue
            equip = self._get_equip_info(equip_id)
            if equip is None:
                item["equip_status"] = "database_missing"
                mfaalog.warning(
                    f"[自动编队] 槽位{item['slot'] + 1}礼装 ceId={equip_id} 不在礼装数据库，跳过"
                )
                continue
            team_template = self._load_equip_template(equip_id, self.equip_team_dirs)
            list_template = self._load_equip_template(equip_id, self.equip_list_dirs)
            if team_template is None or list_template is None:
                item["equip_status"] = "resource_missing"
                mfaalog.warning(
                    f"[自动编队] 槽位{item['slot'] + 1}礼装 {equip['name']}({equip_id})"
                    "缺少 EquipFaces 资源，跳过"
                )
                continue
            item["equip_status"] = "ready"
            self.equip_team_templates[equip_id] = team_template
            self.equip_list_templates[equip_id] = list_template
        self.identity_templates = dict(self.local_templates)
        for svt_id, templates in self.support_templates.items():
            self.identity_templates.setdefault(svt_id, templates)
        self.support_marker = self._load_named_template("battle/助战标记.png")
        if self.support_marker is None:
            raise RuntimeError("resource_missing: battle/助战标记.png")
        self.edit_marker = self._load_named_template("battle/编队决定.png")
        if self.edit_marker is None:
            raise RuntimeError("resource_missing: battle/编队决定.png")
        # 确认页的“开始任务”和编辑页的“编队决定”外观非常接近，单靠
        # edit_marker 会把确认页误判为编辑页，最终在人数不足时直接开始任务。
        # 用确认页专有的“配置变更”按钮先行定界。
        self.config_marker = self._load_named_template("battle/配置变更.png")
        if self.config_marker is None:
            raise RuntimeError("resource_missing: battle/配置变更.png")
        self.select_page_markers = []
        for relative in (
            "整理礼物盒/图标缩放按钮大.png",
            "整理礼物盒/图标缩放按钮中.png",
            "整理礼物盒/图标缩放按钮小.png",
        ):
            marker = self._load_named_template(relative)
            if marker is None:
                raise RuntimeError(f"resource_missing: {relative}")
            self.select_page_markers.append(marker)
        self.formation_confirm_marker = self._load_named_template("决定.png")
        self.equip_confirm_marker = self._load_named_template("EquipFaces/礼装决定.png")
        self.grand_equip_popup_marker = self._load_named_template(
            "battle/冠位助战礼装编辑弹窗.png"
        )
        if self.equip_confirm_marker is None:
            raise RuntimeError("resource_missing: EquipFaces/礼装决定.png")
        if self.grand_equip_popup_marker is None:
            raise RuntimeError("resource_missing: battle/冠位助战礼装编辑弹窗.png")
        if self.formation_confirm_marker is None:
            raise RuntimeError("resource_missing: 决定.png")

    def _match_template(self, image, template, roi=None, display_size=None):
        if image is None or template is None:
            return None
        scaled = template
        if display_size is None:
            width = max(1, int(round(template.shape[1] * self.sx)))
            height = max(1, int(round(template.shape[0] * self.sy)))
        else:
            width = max(1, int(round(display_size[0] * self.sx)))
            height = max(1, int(round(display_size[1] * self.sy)))
        if width != template.shape[1] or height != template.shape[0]:
            scaled = cv2.resize(template, (width, height))
        region, offset_x, offset_y = image, 0, 0
        if roi is not None:
            x, y, roi_width, roi_height = self._scale_roi(roi)
            x, y = max(0, x), max(0, y)
            roi_width = min(roi_width, image.shape[1] - x)
            roi_height = min(roi_height, image.shape[0] - y)
            if roi_width <= 0 or roi_height <= 0:
                return None
            region = image[y:y + roi_height, x:x + roi_width]
            offset_x, offset_y = x, y
        if region.shape[0] < scaled.shape[0] or region.shape[1] < scaled.shape[1]:
            return None
        _min_value, score, _min_loc, max_loc = cv2.minMaxLoc(
            cv2.matchTemplate(region, scaled, cv2.TM_CCOEFF_NORMED)
        )
        center = (offset_x + max_loc[0] + scaled.shape[1] // 2,
                  offset_y + max_loc[1] + scaled.shape[0] // 2)
        return float(score), center

    def _match_servant(self, image, templates, roi):
        best = None
        for name, template in templates:
            result = self._match_template(image, template, roi)
            if result is not None and (best is None or result[0] > best[0]):
                best = (result[0], result[1], name)
        return best

    # ---------- 编队页识别、重排 ----------

    def _in_formation_edit(self):
        image = self._shot()
        confirm_page = self._match_template(image, self.config_marker)
        if confirm_page is not None and confirm_page[0] >= 0.80:
            return False
        result = self._match_template(image, self.edit_marker)
        return result is not None and result[0] >= 0.75

    def _on_formation_confirm_page(self):
        image = self._shot()
        result = self._match_template(image, self.config_marker)
        return result is not None and result[0] >= 0.80

    def _in_formation_work_area(self):
        """普通编辑页，或冠位限制下可直接逐槽编辑的确认页。"""
        if self._in_formation_edit():
            return True
        return bool(
            getattr(self, "direct_formation_mode", False)
            and self._on_formation_confirm_page()
        )

    def _detect_slots(self):
        image = self._shot()
        if image is None:
            self._fail("slot_unknown: 无法获取截图")
            return None
        detected = []
        for index, roi in enumerate(SLOT_ROIS):
            if self._is_empty_slot(image, roi):
                detected.append({"kind": "EMPTY", "svt_id": None, "score": 1.0})
                continue
            support = self._match_template(image, self.support_marker, roi)
            if support is not None and support[0] >= SUPPORT_THRESHOLD:
                identity = self._match_template_groups(
                    image, self.identity_templates, roi
                )
                detected.append({
                    "kind": "SUPPORT",
                    "svt_id": (
                        identity[1]
                        if identity is not None and identity[0] >= FACE_THRESHOLD
                        else None
                    ),
                    "score": support[0],
                    "identity_score": identity[0] if identity is not None else 0.0,
                    "template": identity[2] if identity is not None else None,
                })
                continue
            best = self._match_template_groups(image, self.local_templates, roi)
            if best is not None and best[0] >= FACE_THRESHOLD:
                detected.append({"kind": "LOCAL", "svt_id": best[1], "score": best[0], "template": best[2]})
            else:
                # 不在 Chaldea 目标集合内的本地从者只需标为 OTHER，后续替换即可。
                detected.append({"kind": "OTHER", "svt_id": None, "score": best[0] if best else 0.0})
        return detected

    def _detect_slots_stable(self):
        """持续读取布局，要求连续两帧的槽位类型和身份一致。"""
        deadline = time.monotonic() + SLOT_LAYOUT_VERIFY_TIMEOUT_SECONDS
        previous = None
        latest = None
        attempts = 0
        while time.monotonic() < deadline:
            if self.context.tasker.stopping:
                return None
            latest = self._detect_slots()
            attempts += 1
            if latest is not None:
                signature = tuple(
                    (item.get("kind"), item.get("svt_id")) for item in latest
                )
                if signature == previous:
                    if attempts > 2:
                        mfaalog.info(
                            f"[自动编队] 编队槽位布局经{attempts}次复核后稳定"
                        )
                    return latest
                previous = signature
            else:
                previous = None
            time.sleep(SLOT_LAYOUT_VERIFY_INTERVAL_SECONDS)
        mfaalog.warning(
            f"[自动编队] 编队槽位布局在"
            f"{SLOT_LAYOUT_VERIFY_TIMEOUT_SECONDS:.0f}秒内未稳定"
        )
        return None

    def _match_template_groups(self, image, groups, roi):
        """在多个从者 ID 的模板组中返回最高分 ``(score, id, template)``。"""
        best = None
        for svt_id, templates in groups.items():
            match = self._match_servant(image, templates, roi)
            if match is not None and (best is None or match[0] > best[0]):
                best = (match[0], svt_id, match[2])
        return best

    def _is_empty_slot(self, image, roi):
        """识别编队中灰色的 SELECT 空槽；不依赖文字 OCR。"""
        x, y, width, height = self._scale_roi(roi)
        region = image[y:y + height, x:x + width]
        if region.size == 0:
            return False
        channel_means = np.mean(region, axis=(0, 1))
        channel_std = float(np.mean(np.std(region, axis=(0, 1))))
        channel_delta = float(np.max(channel_means) - np.min(channel_means))
        return (
            channel_std <= EMPTY_SLOT_STD_THRESHOLD
            and channel_delta <= EMPTY_SLOT_CHANNEL_DELTA_THRESHOLD
        )

    def _matches(self, expected, current):
        if expected["kind"] == "LOCAL":
            return current["kind"] == "LOCAL" and current["svt_id"] == expected["svt_id"]
        if expected["kind"] == "SUPPORT":
            return (
                current["kind"] == "SUPPORT"
                and (
                    expected.get("svt_id") is None
                    or current.get("svt_id") == expected["svt_id"]
                )
            )
        # Chaldea 未提供从者的槽位不参与最终匹配：用户的已有编队可以保留
        # 这些位置的从者。但它们仍可能是后续重排的可移动来源，见
        # _can_move_from。
        return True

    def _first_mismatch(self, current):
        for index, (expected, actual) in enumerate(zip(self.expected, current)):
            if not self._matches(expected, actual):
                return index
        return None

    def _should_clear_formation(self, current):
        """目标本地从者中至少一半不在当前队伍时，选择先清空整队。

        此处只比较从者集合而不比较槽位；位置不同的同一从者可由后续拖动复用，
        不应算作需要重新选择的从者。Counter 同时兼容目标中出现重复 ID 的情况。
        """
        expected_counts = Counter(
            item["svt_id"] for item in self.expected if item["kind"] == "LOCAL"
        )
        current_counts = Counter(
            item["svt_id"] for item in current if item["kind"] == "LOCAL"
        )
        local_count = sum(expected_counts.values())
        matched_count = sum((expected_counts & current_counts).values())
        mismatch_count = local_count - matched_count
        should_clear = local_count > 0 and mismatch_count * 2 >= local_count
        mfaalog.info(
            f"[自动编队] 本地从者匹配检查：{mismatch_count}/{local_count}不匹配，"
            f"处理方式={'清空编队' if should_clear else '配置变更'}"
        )
        return should_clear

    def _try_clear_formation(self):
        """尝试解散队伍；冠位限制导致按钮无响应时降级为增量编队。"""
        try:
            detail = self.context.run_task("自动编队-执行清空编队")
        except Exception as exc:
            mfaalog.warning(f"[自动编队] 清空编队流程异常，检查是否受冠位限制：{exc}")
            detail = None

        if self.context.tasker.stopping:
            return "failed"
        detail_succeeded = (
            detail is not None
            and detail.status.succeeded
            and not detail.status.failed
        )

        # 不仅检查 TaskDetail，还要确认画面仍在编队编辑页。否则全局恢复链可能
        # 把失败子流程包装成 succeeded，并已经把游戏带离编队页。
        if detail_succeeded and self._wait_for(self._in_formation_edit, 5.0):
            return "cleared"

        # 冠位从者编队中，“编队解散”按钮可见但点击后不会弹出二次确认。
        # 此时子流程会超时失败，不过画面已经稳定停留在编辑页，可以安全地
        # 沿用现有队伍并切换到逐槽重排/替换。
        if not detail_succeeded and self._wait_for(self._in_formation_edit, 5.0):
            mfaalog.warning(
                "[自动编队] 编队解散点击后未出现确认，可能存在冠位从者；"
                "跳过解散并改用增量编队"
            )
            return "skipped"

        # Grand Duel 的 LIMITED 编队甚至可能不响应“编队编辑”入口。只要仍在
        # 编队确认页，就先跳过清空；调用方随后通过“配置变更”进入逐槽编辑。
        if not detail_succeeded and self._wait_for(
            self._on_formation_confirm_page, 5.0
        ):
            mfaalog.warning(
                "[自动编队] 编队编辑/解散点击后未进入清空流程，可能为冠位限定编队；"
                "跳过解散并改走配置变更"
            )
            return "skipped"

        mfaalog.error("[自动编队] 清空子流程失败，且无法确认仍在编队编辑页")
        return "failed"

    def _can_move_from(self, index, current):
        """判断当前位置的从者能否被移去满足其他目标位置。"""
        expected = self.expected[index]
        if expected["kind"] == "EMPTY":
            # 空目标位不受最终校验约束；其中若有目标从者，必须允许把它移走。
            return current["kind"] != "EMPTY"
        return not self._matches(expected, current)

    def _same_item(self, actual, expected):
        return self._matches(expected, actual)

    def _configure_support_target(self, current, expected_support_count):
        """校验助战身份，并在开启替代时生成最终 SUPPORT 目标。"""
        support_indices = [
            index for index, item in enumerate(current) if item["kind"] == "SUPPORT"
        ]
        if len(support_indices) > 1:
            return self._fail("support_count_invalid: 当前识别到多个助战槽")

        if expected_support_count or self.use_support_substitution:
            if not support_indices:
                reason = (
                    "Chaldea 要求助战"
                    if expected_support_count
                    else "已开启助战替代"
                )
                return self._fail(f"support_missing: {reason}，但当前编队没有助战")
            support_index = support_indices[0]
            actual = current[support_index]
            actual_id = actual.get("svt_id")
            if actual_id is None:
                return self._fail(
                    "support_identity_unknown: 无法识别"
                    f"槽位{support_index + 1}助战人物，"
                    f"最高分={actual.get('identity_score', 0.0):.3f}/"
                    f"{FACE_THRESHOLD:.2f}，template={actual.get('template') or '-'}"
                )

        if expected_support_count:
            target_index = next(
                index for index, item in enumerate(self.expected)
                if item["kind"] == "SUPPORT"
            )
            expected_id = self.expected[target_index]["svt_id"]
            if actual_id != expected_id:
                return self._fail(
                    "support_identity_mismatch: "
                    f"当前助战槽位{support_index + 1}为 svtId={actual_id}，"
                    f"Chaldea 槽位{target_index + 1}要求 svtId={expected_id}"
                )
            mfaalog.info(
                f"[自动编队] 助战身份校验通过：svtId={actual_id}，"
                f"目标槽位{target_index + 1}"
            )
            return True

        if not self.use_support_substitution:
            mfaalog.info("[自动编队] 未开启助战替代；沿用空位安置规则")
            return True

        candidate_indices = [
            index for index, item in enumerate(self.expected)
            if item["kind"] == "LOCAL" and item["svt_id"] == actual_id
        ]
        if not candidate_indices:
            expected_ids = sorted({
                item["svt_id"] for item in self.expected if item["kind"] == "LOCAL"
            })
            return self._fail(
                "support_identity_mismatch: "
                f"当前助战槽位{support_index + 1}为 svtId={actual_id}，"
                f"Chaldea 本地从者中没有同 ID 目标；候选={expected_ids}"
            )
        target_index = (
            support_index if support_index in candidate_indices else candidate_indices[0]
        )
        self.expected[target_index]["kind"] = "SUPPORT"
        mfaalog.info(
            f"[自动编队] 助战替代生效：svtId={actual_id}，"
            f"代替 Chaldea 槽位{target_index + 1}的同名本地从者"
        )
        return True

    def _relocate_unexpected_support(self, current, expected_support_count):
        """当 Chaldea 没有助战但游戏已选助战时，将其移至未指定槽位。"""
        if expected_support_count:
            return True
        support_index = next(
            (index for index, item in enumerate(current) if item["kind"] == "SUPPORT"),
            None,
        )
        if support_index is None:
            return True
        # 助战已处于 Chaldea 没有指定从者的位置时，不需要移动。
        if self.expected[support_index]["kind"] == "EMPTY":
            mfaalog.info(f"[自动编队] 助战已在未指定槽位{support_index + 1}，无需移动")
            return True
        empty_target = next(
            (index for index, expected in enumerate(self.expected)
             if expected["kind"] == "EMPTY"),
            None,
        )
        if empty_target is None:
            return self._fail("support_relocation_failed: Chaldea 无空位，无法保留当前助战")
        mfaalog.info(
            f"[自动编队] Chaldea 未指定助战；将助战槽位{support_index + 1}"
            f"移动至未指定槽位{empty_target + 1}"
        )
        self._drag_slot(support_index, empty_target)
        verified, current = self._wait_for_slot_match(
            empty_target,
            {"kind": "SUPPORT", "svt_id": None},
        )
        if not verified:
            actual = current[empty_target] if current is not None else {}
            mfaalog.warning(
                f"[自动编队] 助战移动复核超时：目标槽位{empty_target + 1}，"
                f"识别={actual.get('kind')} score={actual.get('score', 0.0):.3f}"
            )
            return self._fail("support_relocation_failed: 助战未移动到未指定槽位")
        return True

    def _reorder_existing(self):
        for _ in range(MAX_REORDER_OPS):
            current = self._detect_slots_stable()
            if current is None:
                return False
            target_index = next(
                (i for i, expected in enumerate(self.expected)
                 if expected["kind"] != "EMPTY"
                 and not self._matches(expected, current[i])
                 # 已满足其自身目标的从者不能被再次搬走；否则当 Chaldea
                 # 在多个槽位要求同一从者时，会在两个正确/待补位置之间来回拖拽。
                 and any(
                     self._same_item(current[j], expected)
                     and self._can_move_from(j, current[j])
                     for j in range(6) if j != i
                 )),
                None,
            )
            if target_index is None:
                return True
            source_index = next(
                j for j in range(6)
                if j != target_index
                and self._same_item(current[j], self.expected[target_index])
                and self._can_move_from(j, current[j])
            )
            mfaalog.info(
                f"[自动编队] 重排：槽位{source_index + 1} -> 槽位{target_index + 1}"
            )
            self._drag_slot(source_index, target_index)
            verified, _current = self._wait_for_slot_match(
                target_index,
                self.expected[target_index],
            )
            if not verified:
                return self._fail("swap_verify_failed: 拖动后目标槽位未匹配")
        return self._fail("swap_verify_failed: 重排次数达到上限")

    def _wait_for_slot_match(self, target_index, expected):
        """轮询等待拖动动画和卡片资源刷新完成，再复核目标槽位。"""
        deadline = time.monotonic() + SWAP_VERIFY_TIMEOUT_SECONDS
        latest = None
        consecutive = 0
        while time.monotonic() < deadline:
            if self.context.tasker.stopping:
                return False, latest
            latest = self._detect_slots()
            if latest is not None and self._matches(expected, latest[target_index]):
                consecutive += 1
                if consecutive >= 2:
                    return True, latest
            else:
                consecutive = 0
            time.sleep(SWAP_VERIFY_INTERVAL_SECONDS)
        return False, latest

    def _drag_slot(self, source_index, target_index):
        source_x, source_y = self._slot_center(source_index)
        target_x, target_y = self._slot_center(target_index)
        self.controller.post_swipe(source_x, source_y, target_x, target_y, SWAP_DRAG_DURATION).wait()

    # ---------- 从者选择、筛选、替换 ----------

    def _replace_local_servants(self):
        for index, expected in enumerate(self.expected):
            if expected["kind"] != "LOCAL":
                continue
            current = self._detect_slots_stable()
            if current is None:
                return False
            if self._matches(expected, current[index]):
                continue
            servant = self._get_servant_info(expected["svt_id"])
            if servant is None:
                return self._fail(f"servant_not_found: servant_list 中没有 {expected['svt_id']}")
            for select_attempt in range(MAX_SERVANT_SELECT_ATTEMPTS):
                mfaalog.info(
                    f"[自动编队] 替换槽位{index + 1}为 {servant['name']}({servant['id']})，"
                    f"第{select_attempt + 1}/{MAX_SERVANT_SELECT_ATTEMPTS}次选择"
                )
                if not self._enter_servant_select(index):
                    return self._fail(f"servant_select_failed: 槽位{index + 1}未进入从者选择界面")
                if not self._filter_servant_list(servant):
                    return self._fail(f"servant_filter_failed: {servant['name']}")
                if not self._find_and_select_servant(servant):
                    return self._fail(f"servant_not_found: {servant['name']}({servant['id']})")
                if not self._wait_for(self._in_formation_edit, 5.0):
                    return self._fail("servant_select_failed: 选择从者后未返回编队编辑页")
                verified, current = self._wait_for_servant_replace_verify(index, expected)
                if verified:
                    break
                actual = current[index] if current is not None else {}
                mfaalog.warning(
                    f"[自动编队] 槽位{index + 1}第{select_attempt + 1}次换人复核未通过："
                    f"识别={actual.get('kind')} id={actual.get('svt_id')} "
                    f"score={actual.get('score', 0.0):.3f} "
                    f"template={actual.get('template', '-') }；重新选择"
                )
            else:
                return self._fail(f"servant_replace_verify_failed: 槽位{index + 1}")
        return True

    def _wait_for_servant_replace_verify(self, index, expected):
        """等待从者卡资源加载完成，再判断本次换人是否生效。"""
        deadline = time.monotonic() + SERVANT_REPLACE_VERIFY_TIMEOUT_SECONDS
        latest = None
        consecutive = 0
        while time.monotonic() < deadline:
            if self.context.tasker.stopping:
                return False, latest
            latest = self._detect_slots()
            if latest is not None and self._matches(expected, latest[index]):
                consecutive += 1
                if consecutive >= 2:
                    score = float(latest[index].get("score", 0.0))
                    mfaalog.info(
                        f"[自动编队] 槽位{index + 1}换人连续复核通过："
                        f"{score:.4f}/{FACE_THRESHOLD:.2f}"
                    )
                    return True, latest
            else:
                consecutive = 0
            time.sleep(SERVANT_REPLACE_VERIFY_INTERVAL_SECONDS)
        mfaalog.warning(
            f"[自动编队] 槽位{index + 1}换人复核等待"
            f"{SERVANT_REPLACE_VERIFY_TIMEOUT_SECONDS:.0f}秒后仍未匹配"
        )
        return False, latest

    def _enter_servant_select(self, slot_index):
        if self._confirmed_now(self._in_servant_select):
            return self._run_pipeline("自动编队-确认从者选择界面")
        # 槽位坐标只允许点击一次：游戏切页尚未完成时再次点击同一坐标，会在
        # 从者列表中命中第一张卡片，等同于误选第一个从者。
        x, y = self._slot_center(slot_index)
        self.controller.post_click(x, y).wait()
        if not self._wait_for(self._in_servant_select, SELECT_PAGE_ENTER_TIMEOUT_SECONDS):
            return False
        return self._run_pipeline("自动编队-确认从者选择界面")

    def _in_servant_select(self):
        """通过列表页左下角的图标缩放按钮判断已进入从者选择页。"""
        return self._in_selection_page()

    def _in_selection_page(self):
        """判断是否已离开编队编辑页并进入从者或礼装选择列表。"""
        image = self._shot()
        if image is None:
            return False
        edit = self._match_template(image, self.edit_marker)
        if edit is not None and edit[0] >= 0.75:
            return False
        return any(
            result is not None and result[0] >= SELECT_PAGE_SCALE_THRESHOLD
            for marker in self.select_page_markers
            for result in [self._match_template(image, marker, SELECT_PAGE_SCALE_ROI)]
        )

    def _filter_servant_list(self, servant):
        class_name = CLASS_TEMPLATE.get(servant.get("class"))
        if not class_name:
            return self._fail(f"servant_filter_failed: 未知职阶 {servant.get('class')}")
        override = {
            "自动编队-筛选职介": {
                "recognition": {"param": {"template": f"强化从者/职介-{class_name}.png"}}
            }
        }
        rarity = int(servant.get("rarity", 0))
        if 1 <= rarity <= 5:
            override["自动编队-筛选星级"] = {
                "recognition": {"param": {"template": f"整理礼物盒/{rarity}星未选中.png"}}
            }
        else:
            # 0 星从者没有对应筛选按钮，只按职阶筛选。
            override["自动编队-筛选星级"] = {"recognition": "DirectHit"}
        self.context.override_pipeline(override)
        if not self._run_pipeline("自动编队-筛选准备"):
            return False
        if not self.list_view_prepared:
            # 缩放与活动筛选均是静态界面操作，交由 Maa pipeline 维护，脚本不再
            # 重复固定坐标点击。
            if not self._run_pipeline("自动编队-准备从者列表"):
                return False
            self.list_view_prepared = True
        return True

    def _find_and_select_servant(self, servant):
        templates = self._load_servant_templates(servant["id"], self.face_dirs)
        if not templates:
            templates = self._load_servant_templates(servant["id"], self.narrow_dirs)
        if not templates:
            return self._fail(f"resource_missing: 从者选择图 {servant['id']}")
        # 每次开始查找前都先通过右侧滚动条复位到列表顶端。筛选后的默认位置
        # 不能作为前提，否则上一名从者的滚动位置会漏掉前面的匹配项。
        if not self._run_pipeline("自动编队-从者列表复位顶部"):
            return self._fail("servant_list_reset_failed: 未能复位从者列表")
        for round_index in range(MAX_FIND_SERVANT_ROUNDS):
            if self.context.tasker.stopping:
                return False
            match = self._find_servant_in_still_list(
                templates, servant["name"], round_index
            )
            if match is not None:
                mfaalog.info(
                    f"[自动编队] 查找 {servant['name']} 第{round_index + 1}轮，"
                    f"最高分={match[0]:.3f} 模板={match[2]}"
                )
            if match is not None and match[0] >= FACE_THRESHOLD:
                self.controller.post_click(*match[1]).wait()
                if self._wait_for(self._in_formation_edit, 5.0):
                    return True
            # 单轮顺序固定为：查找未命中 → 下滑一次 → pipeline 等待 0.5 秒。
            if not self._run_pipeline("自动编队-从者列表下滑查找"):
                return self._fail("servant_list_swipe_failed: 从者列表下滑失败")
        return False

    def _find_servant_in_still_list(self, templates, servant_name, round_index):
        """只返回连续两帧中位置稳定的从者列表匹配，避免滚动残影误点。"""
        latest = None
        for check_index in range(SERVANT_LIST_STABILITY_MAX_CHECKS):
            first = self._match_servant(self._shot(), templates, None)
            time.sleep(SERVANT_LIST_STABILITY_INTERVAL_SECONDS)
            second = self._match_servant(self._shot(), templates, None)
            latest = second or first
            first_hit = first is not None and first[0] >= FACE_THRESHOLD
            second_hit = second is not None and second[0] >= FACE_THRESHOLD
            if not first_hit and not second_hit:
                return latest
            if first_hit and second_hit:
                dx = abs(first[1][0] - second[1][0])
                dy = abs(first[1][1] - second[1][1])
                if (
                    first[2] == second[2]
                    and dx <= SERVANT_LIST_STABILITY_CENTER_DELTA_PX
                    and dy <= SERVANT_LIST_STABILITY_CENTER_DELTA_PX
                ):
                    return second
            mfaalog.info(
                f"[自动编队] 从者 {servant_name} 第{round_index + 1}轮画面未稳定，"
                f"第{check_index + 1}/{SERVANT_LIST_STABILITY_MAX_CHECKS}次等待"
            )
        return None

    def _get_servant_info(self, svt_id):
        path = os.path.join(_CUSTOM_DIR, "servant_list.json")
        try:
            with open(path, encoding="utf-8") as file:
                servants = json.load(file).get("servants", [])
        except Exception as exc:
            mfaalog.error(f"[自动编队] 读取 servant_list.json 失败: {exc}")
            return None
        return next((item for item in servants if str(item.get("id")) == str(svt_id)), None)

    # ---------- 概念礼装选择、筛选、替换 ----------

    def _match_equip(self, image, equip_id, roi):
        template_data = self.equip_team_templates.get(equip_id)
        if template_data is None:
            return None
        name, template = template_data
        # 更新后的 team 资源就是编队界面使用的原始头像图；除 MaaFramework
        # 按设备分辨率进行的坐标缩放外，不做裁剪、缩放或其他格式变换。
        result = self._match_template(image, template, roi)
        if result is None:
            return None
        return result[0], result[1], name

    def _equip_matches_slot(self, slot_index, equip_id):
        image = self._shot()
        match = self._match_equip(image, equip_id, EQUIP_TEAM_ROIS[slot_index])
        return match is not None and match[0] >= EQUIP_TEAM_THRESHOLD, match

    def _equip_matches_slot_stable(self, slot_index, equip_id):
        """连续两次得到相同结论后，才决定礼装是否已经位于目标槽。"""
        previous = None
        latest_match = None
        for _ in range(EQUIP_MATCH_STABILITY_MAX_CHECKS):
            matched, latest_match = self._equip_matches_slot(slot_index, equip_id)
            if matched == previous:
                return matched, latest_match
            previous = matched
            time.sleep(EQUIP_MATCH_STABILITY_INTERVAL_SECONDS)
        return False, latest_match

    def _equip_slot_snapshot(self, image, slot_index):
        if image is None:
            return np.empty((0, 0, 3), dtype=np.uint8)
        x, y, width, height = self._scale_roi(EQUIP_TEAM_ROIS[slot_index])
        # 只取礼装卡片上部，避开 COST/HP 文本。
        height = max(1, int(height * 0.67))
        return image[y:y + height, x:x + width].copy()

    def _is_empty_equip_slot(self, image, slot_index):
        region = self._equip_slot_snapshot(image, slot_index)
        if region.size == 0:
            return False
        gray_std = float(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).std())
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        saturated_ratio = float(np.mean(hsv[:, :, 1] > 60))
        return (
            gray_std <= EMPTY_EQUIP_STD_MAX
            and saturated_ratio <= EMPTY_EQUIP_SATURATED_RATIO_MAX
        )

    def _wait_for_empty_equip_slot(self, slot_index):
        return self._wait_for(
            lambda: self._is_empty_equip_slot(self._shot(), slot_index),
            EQUIP_SLOT_VERIFY_TIMEOUT_SECONDS,
        )

    def _unequip_relocatable_equip(self, slot_index):
        """将同队其他槽位占用的目标礼装卸回仓库。"""
        if not self._enter_equip_select(slot_index):
            if getattr(self, "_last_equip_entry_state", None) == "grand":
                return "grand"
            self._fail(f"equip_relocation_failed: 槽位{slot_index + 1}未进入礼装选择界面")
            return "failed"
        if not self._run_pipeline("自动编队-卸下当前礼装"):
            self._fail(f"equip_relocation_failed: 槽位{slot_index + 1}未能卸下礼装")
            return "failed"
        if not self._wait_for(self._in_formation_edit, 6.0):
            self._fail(f"equip_relocation_failed: 槽位{slot_index + 1}卸下后未返回编队页")
            return "failed"
        if not self._wait_for_empty_equip_slot(slot_index):
            self._fail(f"equip_relocation_failed: 槽位{slot_index + 1}卸下后未识别为空")
            return "failed"
        return "released"

    def _release_equip_held_in_other_slot(
        self, target_index, equip_id, equip, correct_slots, pending_slots,
        blocked_grand_slots,
    ):
        """释放位于其他可编辑槽位的单个目标礼装，并返回原槽位。"""
        current = self._detect_slots_stable()
        if current is None:
            self._fail("equip_relocation_failed: 无法识别礼装占用槽位")
            return "failed", None
        for source_index, actual in enumerate(current):
            if (
                source_index == target_index
                or source_index in correct_slots
                or source_index in blocked_grand_slots
                or actual["kind"] in {"EMPTY", "SUPPORT"}
            ):
                continue
            source_expected = self.expected[source_index]
            if source_expected.get("grand_svt"):
                continue
            # 缺少资源而无法核对的 Chaldea 指定礼装仍属于保护内容，不能为了
            # 释放另一张礼装而擅自改动；已确认待替换的目标槽则可以参与换位。
            if (
                source_expected.get("equip_id")
                and source_index not in pending_slots
            ):
                continue
            matched, match = self._equip_matches_slot_stable(source_index, equip_id)
            if not matched:
                continue
            mfaalog.info(
                f"[自动编队] 目标礼装 {equip['name']}({equip_id}) "
                f"当前由槽位{source_index + 1}携带，先卸下再装入槽位{target_index + 1}，"
                f"score={match[0]:.4f}/{EQUIP_TEAM_THRESHOLD:.2f}"
            )
            result = self._unequip_relocatable_equip(source_index)
            if result == "failed":
                return "failed", None
            if result == "grand":
                blocked_grand_slots.add(source_index)
                mfaalog.info(
                    f"[自动编队] 槽位{source_index + 1}为冠位礼装槽，"
                    "不从该槽释放礼装"
                )
                continue
            return "released", source_index
        return "none", None

    def _restore_relocated_equip(self, source_index, target_index, equip):
        """目标槽未能装入时，将刚释放的礼装恢复到原槽。"""
        if not self._confirmed_now(self._in_formation_edit):
            if not self._confirmed_now(self._in_equip_select):
                return self._fail(
                    "equip_relocation_restore_failed: "
                    f"无法确认当前页面，原槽位{source_index + 1}"
                )
            if not self._leave_equip_select():
                return self._fail(
                    "equip_relocation_restore_failed: "
                    f"无法返回编队页，原槽位{source_index + 1}"
                )
        mfaalog.warning(
            f"[自动编队] 槽位{target_index + 1}未能装入 {equip['name']}({equip['id']})，"
            f"尝试恢复到原槽位{source_index + 1}"
        )
        result = self._select_equip_for_slot(source_index, equip, False)
        if result != "selected":
            return self._fail(
                "equip_relocation_restore_failed: "
                f"礼装 {equip['name']}({equip['id']}) 未恢复到槽位{source_index + 1}"
            )
        verified, score = self._wait_for_equip_replace_verify(
            source_index, int(equip["id"])
        )
        if not verified:
            return self._fail(
                "equip_relocation_restore_failed: "
                f"槽位{source_index + 1}恢复后复核失败"
            )
        mfaalog.warning(
            f"[自动编队] 已恢复原槽位{source_index + 1}礼装 "
            f"{equip['name']}({equip['id']})，score={score:.4f}/{EQUIP_TEAM_THRESHOLD:.2f}"
        )
        return True

    def _replace_equips(self):
        """在从者全部完成后逐槽补齐概念礼装。

        助战的礼装由助战提供，不能在本地队伍中替换；Chaldea 的空槽和未携带
        礼装的本地从者也没有任何操作目标。数据库或图片资源不覆盖的礼装按用户
        要求只输出日志并继续执行。
        """
        # 先在编队页对所有可匹配的礼装做连续帧校验。若全部已正确，绝不能为了
        # “确认”而进入任一礼装选择页：后者会重置筛选状态，也增加误选风险。
        if self._shot() is None:
            return self._fail("equip_initial_verify_failed: 无法获取编队截图")
        pending = []
        correct_slots = set()
        for index, expected in enumerate(self.expected):
            equip_id = expected.get("equip_id")
            if expected["kind"] == "SUPPORT":
                if equip_id:
                    mfaalog.info(
                        f"[自动编队] 槽位{index + 1}为助战，跳过助战礼装 ceId={equip_id}"
                    )
                continue
            if expected.get("grand_svt"):
                if equip_id:
                    mfaalog.info(
                        f"[自动编队] 槽位{index + 1}为 Chaldea 冠位从者，"
                        f"跳过全部冠位礼装配置（equip1={equip_id}）"
                    )
                continue
            if expected["kind"] != "LOCAL" or not equip_id:
                continue
            if expected.get("equip_status") != "ready":
                continue
            equip = self._get_equip_info(equip_id)
            matched, match = self._equip_matches_slot_stable(index, equip_id)
            if matched:
                correct_slots.add(index)
                mfaalog.info(
                    f"[自动编队] 礼装起始校验：槽位{index + 1}已匹配 "
                    f"{equip['name']}({equip_id})，"
                    f"{match[0]:.4f}/{EQUIP_TEAM_THRESHOLD:.2f}"
                )
                continue
            score = match[0] if match is not None else 0.0
            mfaalog.info(
                f"[自动编队] 礼装起始校验：槽位{index + 1}待替换 "
                f"{equip['name']}({equip_id})，{score:.4f}/{EQUIP_TEAM_THRESHOLD:.2f}"
            )
            pending.append((index, expected, equip_id, equip))

        if not pending:
            mfaalog.info("[自动编队] 礼装起始校验全部匹配，跳过礼装选择")
            return True

        mfaalog.info(
            "[自动编队] 礼装起始校验完成：仅处理槽位"
            + "、".join(str(index + 1) for index, _expected, _equip_id, _equip in pending)
        )
        pending_slots = {item[0] for item in pending}
        blocked_grand_slots = set()

        for index, expected, equip_id, equip in pending:
            # 前一槽的返回动画或网络刷新可能影响后续槽位；在实际编辑前再次确认，
            # 防止已经正确的礼装被重复编辑。
            matched, match = self._equip_matches_slot_stable(index, equip_id)
            if matched:
                mfaalog.info(
                    f"[自动编队] 槽位{index + 1}礼装复查已匹配："
                    f"{equip['name']}({equip_id})，{match[0]:.4f}/{EQUIP_TEAM_THRESHOLD:.2f}"
                )
                correct_slots.add(index)
                continue

            release_state, source_index = self._release_equip_held_in_other_slot(
                index, equip_id, equip, correct_slots, pending_slots,
                blocked_grand_slots,
            )
            if release_state == "failed":
                return False

            state = "满破" if expected.get("equip_limit_break") else "不限满破"
            mfaalog.info(
                f"[自动编队] 替换槽位{index + 1}礼装为 {equip['name']}({equip_id})，筛选={state}"
            )
            result = self._select_equip_for_slot(index, equip, bool(expected.get("equip_limit_break")))
            if result == "grand":
                if source_index is not None and not self._restore_relocated_equip(
                    source_index, index, equip
                ):
                    return False
                expected["grand_svt"] = True
                mfaalog.info(
                    f"[自动编队] 槽位{index + 1}出现冠位礼装弹窗，"
                    "已关闭弹窗并将礼装视为满足"
                )
                continue
            if result == "selected":
                verified, score = self._wait_for_equip_replace_verify(index, equip_id)
                if verified:
                    mfaalog.info(
                        f"[自动编队] 槽位{index + 1}礼装复核通过："
                        f"{score:.4f}/{EQUIP_TEAM_THRESHOLD:.2f}"
                    )
                    correct_slots.add(index)
                    continue
                return self._fail(f"equip_replace_verify_failed: 槽位{index + 1}")

            # 筛选后找不到指定礼装是可恢复状态：回到编队页，记录原因，并依照
            # 用户选项尝试不限制满破的兜底搜索。
            if result != "not_found":
                if source_index is not None:
                    self._restore_relocated_equip(source_index, index, equip)
                return False
            if expected.get("equip_limit_break") and self.equip_missing_policy == "allow_non_limit_break":
                mfaalog.warning(
                    f"[自动编队] 槽位{index + 1}未找到满破 {equip['name']}({equip_id})，"
                    "按选项改为查找非满破版本"
                )
                result = self._select_equip_for_slot(index, equip, False)
                if result == "grand":
                    if source_index is not None and not self._restore_relocated_equip(
                        source_index, index, equip
                    ):
                        return False
                    expected["grand_svt"] = True
                    mfaalog.info(
                        f"[自动编队] 槽位{index + 1}出现冠位礼装弹窗，"
                        "已关闭弹窗并将礼装视为满足"
                    )
                    continue
                if result == "selected":
                    verified, score = self._wait_for_equip_replace_verify(index, equip_id)
                    if verified:
                        mfaalog.info(
                            f"[自动编队] 槽位{index + 1}礼装非满破兜底复核通过："
                            f"{score:.4f}/{EQUIP_TEAM_THRESHOLD:.2f}"
                        )
                        correct_slots.add(index)
                        continue
                    return self._fail(f"equip_replace_verify_failed: 槽位{index + 1}")
                if result != "not_found":
                    if source_index is not None:
                        self._restore_relocated_equip(source_index, index, equip)
                    return False
            if source_index is not None and not self._restore_relocated_equip(
                source_index, index, equip
            ):
                return False
            mfaalog.warning(
                f"[自动编队] 槽位{index + 1}仓库未找到礼装 "
                f"{equip['name']}({equip_id})，已跳过"
            )
        return True

    def _select_equip_for_slot(self, slot_index, equip, require_limit_break):
        if not self._enter_equip_select(slot_index):
            if getattr(self, "_last_equip_entry_state", None) == "grand":
                return "grand"
            self._fail(f"equip_select_failed: 槽位{slot_index + 1}未进入礼装选择界面")
            return "failed"
        if not self._filter_equip_list(equip, require_limit_break):
            self._fail(f"equip_filter_failed: {equip['name']}")
            return "failed"
        result = self._find_and_select_equip(equip)
        if result == "selected":
            return "selected"
        if result == "failed":
            self._fail(f"equip_select_failed: 选择 {equip['name']} 后未返回编队编辑页")
            return "failed"
        if not self._leave_equip_select():
            self._fail("equip_select_failed: 未找到礼装后无法返回编队编辑页")
            return "failed"
        return "not_found"

    def _enter_equip_select(self, slot_index):
        self._last_equip_entry_state = None
        initial = self._equip_entry_state()
        if initial == "select":
            self._last_equip_entry_state = "select"
            return self._run_pipeline("自动编队-确认礼装选择界面")
        if initial == "grand":
            self._last_equip_entry_state = "grand"
            if not self._close_grand_equip_popup("自动编队-关闭冠位礼装弹窗"):
                self._last_equip_entry_state = "failed"
            return False
        # 同从者选择页：礼装列表加载期间不能对同一屏幕坐标重复点击，否则会
        # 直接选中列表中的首个礼装。
        self.controller.post_click(*self._equip_slot_center(slot_index)).wait()
        state = self._wait_for_equip_entry_state(SELECT_PAGE_ENTER_TIMEOUT_SECONDS)
        self._last_equip_entry_state = state
        if state == "grand":
            if not self._close_grand_equip_popup("自动编队-关闭冠位礼装弹窗"):
                self._last_equip_entry_state = "failed"
            return False
        if state != "select":
            return False
        return self._run_pipeline("自动编队-确认礼装选择界面")

    def _equip_entry_state(self, image=None):
        """区分普通礼装仓库与冠位礼装编辑弹窗。"""
        image = self._shot() if image is None else image
        if image is None:
            return None
        popup_marker = getattr(self, "grand_equip_popup_marker", None)
        if popup_marker is not None:
            match = self._match_template(image, popup_marker)
            if match is not None and match[0] >= GRAND_EQUIP_POPUP_THRESHOLD:
                return "grand"
        edit = self._match_template(image, self.edit_marker)
        if edit is not None and edit[0] >= 0.75:
            return None
        for marker in self.select_page_markers:
            result = self._match_template(image, marker, SELECT_PAGE_SCALE_ROI)
            if result is not None and result[0] >= SELECT_PAGE_SCALE_THRESHOLD:
                return "select"
        return None

    def _wait_for_equip_entry_state(self, timeout_seconds):
        """轮询两个目标，要求同一结果连续出现两帧后返回。"""
        deadline = time.monotonic() + timeout_seconds
        previous = None
        consecutive = 0
        while time.monotonic() < deadline:
            if self.context.tasker.stopping:
                return None
            state = self._equip_entry_state()
            if state is not None and state == previous:
                consecutive += 1
                if consecutive >= 2:
                    return state
            else:
                previous = state
                consecutive = 1 if state is not None else 0
            time.sleep(0.4)
        return None

    def _close_grand_equip_popup(self, pipeline_name):
        """通过单次 ESC/Android Back 关闭冠位礼装弹窗。"""
        if not self._run_pipeline(pipeline_name):
            return False
        return self._wait_for(self._in_formation_edit, 5.0)

    def _in_equip_select(self):
        """通过列表页左下角的图标缩放按钮判断已进入礼装选择页。"""
        return self._in_selection_page()

    def _leave_equip_select(self):
        if self._confirmed_now(self._in_formation_edit):
            return True
        if not self._run_pipeline("自动编队-礼装选择返回"):
            return False
        return self._wait_for(self._in_formation_edit, 5.0)

    def _filter_equip_list(self, equip, require_limit_break):
        rarity = int(equip.get("rarity", 0))
        filter_tag = str(equip.get("filter_tag") or "").strip()
        if not filter_tag:
            self._fail(
                f"equip_filter_tag_missing: {equip.get('name', '?')}({equip.get('id', '?')})"
            )
            return False
        override = {}
        if 1 <= rarity <= 5:
            override["自动编队-礼装筛选星级"] = {
                "recognition": {"param": {"template": f"整理礼物盒/{rarity}星未选中.png"}}
            }
        else:
            override["自动编队-礼装筛选星级"] = {"recognition": "DirectHit"}
        override["自动编队-礼装筛选星级"]["next"] = ["自动编队-礼装筛选标签"]
        tag_next = ["自动编队-礼装筛找满破"] if require_limit_break else ["自动编队-礼装筛点决定"]
        override["自动编队-礼装筛选标签"] = {
            "recognition": {
                "param": {
                    "template": f"EquipFaces/礼装类别筛选项/{filter_tag}.png",
                    "threshold": EQUIP_FILTER_TAG_THRESHOLD,
                }
            },
            "next": tag_next,
        }
        self.context.override_pipeline(override)
        if not self._run_pipeline("自动编队-礼装筛选非满破"):
            return False
        if not self.equip_list_view_prepared:
            # 使用用户维护的礼装列表准备 pipeline：依次处理活动筛选与图标大小。
            if not self._run_pipeline("自动编队-准备礼装列表"):
                return False
            self.equip_list_view_prepared = True
        return True

    def _find_and_select_equip(self, equip):
        """按“滑动→等待→查找→点击→决定→复核”严格串行查找礼装。

        本函数的任一轮一旦命中目标，就会完成该目标的点击与唯一一次决定；只有
        决定未生效、仍在礼装列表页时才继续向下滑动，绝不会在命中后直接滑动。
        """
        template_data = self.equip_list_templates.get(int(equip["id"]))
        if template_data is None:
            return "not_found"
        name, template = template_data
        # 与从者列表一致，每件礼装都从筛选结果的顶部开始扫描，避免沿用上一件
        # 礼装的滚动位置。
        if not self._run_pipeline("自动编队-礼装列表复位顶部"):
            return "failed"
        for round_index in range(MAX_FIND_EQUIP_ROUNDS):
            if self.context.tasker.stopping:
                return "failed"
            result = self._find_equip_in_still_list(template, equip["name"], round_index)
            # 高分目标仍随列表移动时，保持当前列表位置并继续取静止截图；绝不在
            # 此时执行下一次滑动，否则刚出现的目标会在点击前离开原坐标。
            if result == "moving":
                continue
            if result is not None:
                mfaalog.info(
                    f"[自动编队] 查找礼装 {equip['name']} 第{round_index + 1}轮，"
                    f"最高分={result[0]:.3f} 模板={name}"
                )
            if result is not None and result[0] >= EQUIP_LIST_THRESHOLD:
                # 命中后立即停止该轮滑动；先选中当前卡片，再尝试一次且仅一次
                # “礼装决定”。
                mfaalog.info(
                    f"[自动编队] 礼装 {equip['name']} 静止命中，点击卡片中心="
                    f"{result[1]}，{result[0]:.4f}/{EQUIP_LIST_THRESHOLD:.2f}"
                )
                self.controller.post_click(*result[1]).wait()
                time.sleep(EQUIP_CARD_SELECT_SETTLE_SECONDS)
                confirm = self._wait_for_template_match(
                    self.equip_confirm_marker,
                    0.80,
                    TEMPLATE_APPEAR_TIMEOUT_SECONDS,
                )
                if confirm is None or confirm[0] < 0.80:
                    mfaalog.warning(
                        f"[自动编队] 礼装 {equip['name']} 命中后未找到礼装决定；"
                        "本轮不重复点击，继续查找"
                    )
                else:
                    mfaalog.info(
                        f"[自动编队] 礼装 {equip['name']} 命中后点击礼装决定："
                        f"{confirm[0]:.4f}/0.80"
                    )
                    self.controller.post_click(*confirm[1]).wait()
                    # 决定只点击一次。先按约定等待完整一秒，再持续复核返回编队
                    # 编辑页；礼装资源加载偶尔会让切页晚于首个一秒检查。
                    time.sleep(EQUIP_CONFIRM_SETTLE_SECONDS)
                    if self._wait_for(
                        self._in_formation_edit,
                        EQUIP_SELECT_RETURN_TIMEOUT_SECONDS,
                    ):
                        return "selected"
                    if not self._wait_for(self._in_equip_select, 1.5):
                        return "failed"
                    mfaalog.warning(
                        f"[自动编队] 礼装 {equip['name']} 点击决定后未匹配，"
                        "仍在列表页，继续向下查找"
                    )
            # 未命中，或已点击一次决定但没有生效：现在才进入下一轮单次下滑；
            # 0.5 秒停顿由 pipeline 统一维护。
            if not self._run_pipeline("自动编队-礼装列表下滑查找"):
                return "failed"
        return "not_found"

    def _find_equip_in_still_list(self, template, equip_name, round_index):
        """仅在礼装列表停止滚动后返回可点击的匹配结果。

        连续两张截图都必须命中同一位置；若卡片位置仍变化，说明滑动惯性尚未
        结束，只等待而不执行点击或下一次滑动。
        """
        moving = False
        for check_index in range(EQUIP_MATCH_STABILITY_MAX_CHECKS):
            first = self._match_template(self._shot(), template)
            time.sleep(EQUIP_MATCH_STABILITY_INTERVAL_SECONDS)
            second = self._match_template(self._shot(), template)
            first_hit = first is not None and first[0] >= EQUIP_LIST_THRESHOLD
            second_hit = second is not None and second[0] >= EQUIP_LIST_THRESHOLD
            if not first_hit and not second_hit:
                return second
            if not first_hit or not second_hit:
                moving = True
                mfaalog.info(
                    f"[自动编队] 礼装 {equip_name} 第{round_index + 1}轮匹配画面未稳定，"
                    f"第{check_index + 1}/{EQUIP_MATCH_STABILITY_MAX_CHECKS}次等待"
                )
                time.sleep(EQUIP_SWIPE_SETTLE_SECONDS)
                continue
            delta = max(abs(first[1][0] - second[1][0]), abs(first[1][1] - second[1][1]))
            if delta <= EQUIP_MATCH_CENTER_DELTA_PX:
                return second
            mfaalog.info(
                f"[自动编队] 礼装 {equip_name} 第{round_index + 1}轮仍在滚动，"
                f"中心位移={delta}px，停止滑动并继续等待"
            )
            time.sleep(EQUIP_SWIPE_SETTLE_SECONDS)
        # 高分卡片在三次确认中仍在移动，宁可不点也不继续滑；外层会保持当前
        # 位置重新截图，直到列表真正停住后才允许点击。
        return "moving" if moving else None

    def _wait_for_equip_replace_verify(self, slot_index, equip_id):
        deadline = time.monotonic() + SERVANT_REPLACE_VERIFY_TIMEOUT_SECONDS
        latest_score = 0.0
        consecutive = 0
        while time.monotonic() < deadline:
            if self.context.tasker.stopping:
                return False, latest_score
            matched, match = self._equip_matches_slot(slot_index, equip_id)
            if match is not None:
                latest_score = float(match[0])
            if matched:
                consecutive += 1
                if consecutive >= 2:
                    return True, latest_score
            else:
                consecutive = 0
            time.sleep(SERVANT_REPLACE_VERIFY_INTERVAL_SECONDS)
        mfaalog.warning(
            f"[自动编队] 槽位{slot_index + 1}礼装复核等待"
            f"{SERVANT_REPLACE_VERIFY_TIMEOUT_SECONDS:.0f}秒后仍未匹配，"
            f"最后分数={latest_score:.4f}/{EQUIP_TEAM_THRESHOLD:.2f}"
        )
        return False, latest_score

    # ---------- 结束校验、日志 ----------

    def _log_support_identity_if_possible(self, current):
        for index, expected in enumerate(self.expected):
            if expected["kind"] != "SUPPORT" or current[index]["kind"] != "SUPPORT":
                continue
            actual = current[index]
            mfaalog.info(
                f"[自动编队] 助战最终复核通过：槽位{index + 1} "
                f"svtId={actual.get('svt_id')}，"
                f"identity_score={actual.get('identity_score', 0.0):.3f}"
            )

    def _confirm_formation_change_if_present(self):
        """点击“编队决定”后，按需确认游戏的二次确认弹窗。"""
        # 明确等待弹窗动画完成；不要依赖 pipeline 的 post_delay，以免该节点被
        # 后续配置调整后导致确认截图过早。
        time.sleep(FORMATION_CONFIRM_DELAY_SECONDS)
        result = self._wait_for_template_match(
            self.formation_confirm_marker,
            0.80,
            FORMATION_CONFIRM_APPEAR_TIMEOUT_SECONDS,
            FORMATION_CONFIRM_ROI,
        )
        if result is None:
            mfaalog.info("[自动编队] 编队决定后未出现二次确认弹窗")
            return
        mfaalog.info(f"[自动编队] 命中编队二次确认决定，分数={result[0]:.3f}")
        self.controller.post_click(*result[1]).wait()
        time.sleep(FORMATION_CONFIRM_DELAY_SECONDS)

    def _log_layout(self, title, current):
        def describe(item):
            if item["kind"] in {"LOCAL", "SUPPORT"}:
                return f"{item['kind']}({item.get('svt_id')})"
            return item["kind"]
        mfaalog.info(
            f"[自动编队] {title}："
            + ", ".join(describe(item) for item in current)
        )
        mfaalog.info(
            "[自动编队] 目标：" + ", ".join(describe(item) for item in self.expected)
        )

    def _run_pipeline(self, name):
        try:
            detail = self.context.run_task(name)
        except Exception as exc:
            mfaalog.error(f"[自动编队] pipeline {name} 异常: {exc}")
            return False
        if self.context.tasker.stopping:
            return False
        if detail is None or detail.status.failed or not detail.status.succeeded:
            mfaalog.error(f"[自动编队] pipeline {name} 失败")
            return False
        return True

    def _wait_for(self, predicate, timeout_seconds):
        """等待条件连续两次成立，过滤截图缓存和切页过程中的偶发单帧。"""
        end = time.monotonic() + timeout_seconds
        consecutive = 0
        while time.monotonic() < end:
            if self.context.tasker.stopping:
                return False
            if predicate():
                consecutive += 1
                if consecutive >= 2:
                    return True
            else:
                consecutive = 0
            time.sleep(0.4)
        return False

    def _confirmed_now(self, predicate):
        """仅当首帧命中时补拍一帧确认；首帧未命中则立即返回。"""
        if not predicate():
            return False
        time.sleep(0.25)
        return bool(predicate())

    def _wait_for_template_match(self, template, threshold, timeout_seconds, roi=None):
        """持续等待模板出现，避免截图缓存或切页动画造成单帧漏判。"""
        matched = None

        def predicate():
            nonlocal matched
            result = self._match_template(self._shot(), template, roi)
            if result is not None and result[0] >= threshold:
                matched = result
                return True
            return False

        return matched if self._wait_for(predicate, timeout_seconds) else None

    def _fail(self, message):
        mfaalog.error(f"[自动编队] {message}")
        return False


@AgentServer.custom_action("validate_formation_from_chaldea")
class ValidateFormationFromChaldea(AutoFormationFromChaldea):
    """核对当前编队；仅在从者相同而位置不同时执行换位。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            self.context = context
            self.controller = context.tasker.controller
            node = context.get_node_data(argv.node_name) or {}
            attach = node.get("attach") or {}
            source = str(
                attach.get("chaldea_import_source")
                or attach.get("chaldea_import_source_file")
                or ""
            ).strip()
            if not source:
                self._fail("invalid_chaldea_team: 未提供 Chaldea 分享链接/ID/本地文件")
                return CustomAction.RunResult(success=False)

            # 手动方式不读取或执行任何自动编队附加选项。
            self.auto_equip = False
            self.use_support_substitution = False
            self.equip_missing_policy = "skip"
            self.equip_database = {}
            self._init_paths()
            self._init_scale()
            share_data, _quest_id, _team_id = fetch_share_data(source)
            expected = self._build_expected(share_data)
            if expected is None:
                return CustomAction.RunResult(success=False)
            self.expected = expected

            expected_support_count = sum(
                item["kind"] == "SUPPORT" for item in self.expected
            )
            if expected_support_count > 1:
                self._fail("invalid_chaldea_team: 当前编队仅支持一个助战槽")
                return CustomAction.RunResult(success=False)

            self._prepare_target_templates()
            page_state = self._wait_for_formation_page()
            if page_state is None:
                self._fail("not_on_formation_page: 未稳定识别到编队确认页或编辑页")
                return CustomAction.RunResult(success=False)

            current = self._detect_slots_stable()
            if current is None:
                return CustomAction.RunResult(success=False)
            self._log_layout("手动检查初始", current)
            if not self._configure_support_target(current, expected_support_count):
                return CustomAction.RunResult(success=False)
            if not self._manual_identities_match(current):
                return CustomAction.RunResult(success=False)

            mismatch = self._first_mismatch(current)
            if mismatch is None:
                if page_state == "edit":
                    if not self._save_manual_formation():
                        return CustomAction.RunResult(success=False)
                    mfaalog.info("[手动编队] 从者身份与位置均匹配，已保存当前编队")
                else:
                    mfaalog.info("[手动编队] 从者身份与位置均匹配，直接进入战斗")
                return CustomAction.RunResult(success=True)

            mfaalog.info(
                f"[手动编队] 从者身份一致，仅位置不匹配（首个为槽位{mismatch + 1}），"
                "开始换位"
            )
            if page_state != "edit":
                if not self._run_pipeline("自动编队-打开配置"):
                    self._fail("not_on_formation_page: 未找到配置变更按钮")
                    return CustomAction.RunResult(success=False)
                if not self._wait_for(self._in_formation_edit, 5.0):
                    self._fail("not_on_formation_page: 点击配置变更后未稳定进入编辑状态")
                    return CustomAction.RunResult(success=False)

            # 进入编辑页会触发一次界面刷新，必须重新稳定识别并再次确认身份，
            # 避免拿确认页的旧截图执行拖动。
            current = self._detect_slots_stable()
            if current is None or not self._manual_identities_match(current):
                return CustomAction.RunResult(success=False)
            if not self._relocate_unexpected_support(current, expected_support_count):
                return CustomAction.RunResult(success=False)
            if not self._reorder_existing():
                return CustomAction.RunResult(success=False)

            current = self._detect_slots_stable()
            if current is None:
                return CustomAction.RunResult(success=False)
            self._log_layout("手动换位复核", current)
            if not self._manual_identities_match(current):
                return CustomAction.RunResult(success=False)
            mismatch = self._first_mismatch(current)
            if mismatch is not None:
                self._fail(
                    f"manual_formation_reorder_failed: 换位后槽位{mismatch + 1}仍不匹配"
                )
                return CustomAction.RunResult(success=False)
            if not self._save_manual_formation():
                return CustomAction.RunResult(success=False)
            mfaalog.info("[手动编队] 从者换位完成并已保存编队")
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            mfaalog.error(f"[手动编队] 异常: {exc}\n{traceback.format_exc()}")
            return CustomAction.RunResult(success=False)

    def _formation_page_state(self):
        """返回当前编队页状态；模板结果需由调用方连续复核。"""
        image = self._shot()
        confirm = self._match_template(image, self.config_marker)
        if confirm is not None and confirm[0] >= 0.80:
            return "confirm"
        edit = self._match_template(image, self.edit_marker)
        if edit is not None and edit[0] >= 0.75:
            return "edit"
        return None

    def _wait_for_formation_page(self):
        state = None

        def predicate():
            nonlocal state
            current = self._formation_page_state()
            if current is None:
                state = None
                return False
            if state == current:
                return True
            state = current
            return False

        # predicate 自身要求同一状态出现两次，_wait_for 再要求连续两次成立，
        # 因而至少使用三帧，避免确认页与编辑页切换动画造成误判。
        return state if self._wait_for(predicate, SLOT_LAYOUT_VERIFY_TIMEOUT_SECONDS) else None

    def _manual_identities_match(self, current):
        expected_counts = Counter(
            item["svt_id"] for item in self.expected if item["kind"] == "LOCAL"
        )
        current_counts = Counter(
            item["svt_id"] for item in current if item["kind"] == "LOCAL"
        )
        # Chaldea 未指定的槽位沿用自动编队既有语义：不要求为空，也不限制
        # 其中保留的其他本地从者。这里只验证所有目标本地从者（含重复数量）
        # 都确实存在于当前六槽，之后再由位置检查决定是否需要换位。
        missing_counts = expected_counts - current_counts
        if not missing_counts:
            return True
        other_slots = [
            index + 1 for index, item in enumerate(current) if item["kind"] == "OTHER"
        ]
        self._fail(
            "manual_formation_servant_mismatch: 缺少 Chaldea 指定的从者；"
            f"当前目标候选={dict(sorted(current_counts.items()))}，"
            f"Chaldea目标={dict(sorted(expected_counts.items()))}，"
            f"缺少={dict(sorted(missing_counts.items()))}，"
            f"其他/未识别槽位={other_slots or '-'}"
        )
        return False

    def _save_manual_formation(self):
        if not self._run_pipeline("自动编队-编队决定"):
            self._fail("manual_formation_reorder_failed: 未能点击编队决定")
            return False
        self._confirm_formation_change_if_present()
        return True

    def _fail(self, message):
        mfaalog.error(f"[手动编队] {message}")
        return False


# ---------- 原生战斗：只读取最终编队，不调用上方任何编成/点击方法 ----------

IDENTITY_FRAME_NODES = ("编队身份-第1帧", "编队身份-第2帧", "编队身份-第3帧")


@dataclass(frozen=True)
class FormationCalibration:
    """只接受有明确标定记录的阈值；缺少标定时保留诊断候选，身份为未知。

    page/support 沿用原编队页面判断。通用头像阈值没有默认值，不能直接
    使用 Chaldea 少候选时的 FACE_THRESHOLD。配置位于采集节点 attach，
    不新增用户配置界面；离线评估工具也使用同一配置。
    """

    calibration_id: str = "unverified"
    verified: bool = False
    template_family: str = "NarrowFigures"
    identity_threshold: float | None = None
    identity_margin: float | None = None
    class_threshold: float | None = None
    class_margin: float | None = None
    class_roi: tuple = ()          # 相对每个 SLOT_ROIS 左上角的区域
    class_templates: tuple = ()    # (视觉职阶, (相对资源图片路径, ...))
    class_groups: tuple = ()       # (视觉职阶, (目录职阶, ...))，不擅自合并特殊职阶

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            raise ValueError("formation calibration must be an object")
        fields = dict(data)
        if fields.get("template_family", "NarrowFigures") not in {"NarrowFigures", "servant_face"}:
            raise ValueError("unsupported formation template family")
        for key in ("identity_threshold", "identity_margin", "class_threshold", "class_margin"):
            value = fields.get(key)
            if value is not None and (type(value) not in {int, float} or not 0 < value <= 1):
                raise ValueError(f"invalid calibration {key}")
        for key in ("class_templates", "class_groups"):
            mapping = fields.get(key, {})
            if not isinstance(mapping, dict):
                raise ValueError(f"{key} must be an object")
            pairs = []
            for name, values in mapping.items():
                if not isinstance(name, str) or not name or not isinstance(values, list):
                    raise ValueError(f"invalid {key}")
                if not values or not all(isinstance(v, str) and v for v in values):
                    raise ValueError(f"invalid {key} values")
                if key == "class_templates" and any(
                    Path(v).is_absolute() or ".." in Path(v).parts for v in values
                ):
                    raise ValueError("class template must be a relative resource path")
                pairs.append((name, tuple(values)))
            fields[key] = tuple(pairs)
        roi = fields.get("class_roi", ())
        if roi and (len(roi) != 4 or any(type(v) is not int for v in roi)
                    or roi[0] < 0 or roi[1] < 0 or roi[2] <= 0 or roi[3] <= 0
                    or roi[0] + roi[2] > 187 or roi[1] + roi[3] > 276):
            raise ValueError("class ROI must be inside each formation slot")
        fields["class_roi"] = tuple(roi)
        if type(fields.get("verified", False)) is not bool:
            raise ValueError("verified must be boolean")
        if fields.get("verified"):
            if not fields.get("calibration_id") or fields["calibration_id"] == "unverified":
                raise ValueError("verified calibration requires evidence id")
            if fields.get("identity_threshold") is None or fields.get("identity_margin") is None:
                raise ValueError("verified calibration requires identity thresholds")
            if fields["class_templates"] and (not roi or fields.get("class_threshold") is None
                                               or fields.get("class_margin") is None):
                raise ValueError("class templates require calibrated ROI and thresholds")
        return cls(**fields)


def _identity_manifest(roots, family, catalog_path):
    """一次目录扫描；层覆盖优先。mtime/size 变更会触发新索引，不缓存队伍。"""
    paths = {}
    for root in roots:
        for path in sorted(Path(root, family).glob("f_*.png")):
            paths.setdefault(path.name, path)
    manifest = tuple((name, str(p), p.stat().st_mtime_ns, p.stat().st_size)
                     for name, p in sorted(paths.items()))
    catalog = Path(catalog_path)
    return str(catalog), catalog.stat().st_mtime_ns, catalog.stat().st_size, manifest


@lru_cache(maxsize=2)
def _cached_identity_index(catalog_path, catalog_mtime, catalog_size, manifest):
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))["servants"]
    records = {}
    explicit = {}
    prefixes = {}
    for item in catalog:
        sid = str(item["id"])
        if sid in records or not sid.isdigit() or len(sid) <= 2:
            raise ValueError(f"invalid/duplicate servant id: {sid}")
        records[sid] = {"name": item["name"], "class": item["class"]}
        prefixes.setdefault(sid[:-2], set()).add(sid)
        for name in item.get("images") or []:
            explicit.setdefault(name, set()).add(sid)
    groups = {sid: [] for sid in records}
    unmapped, conflicts, unreadable = [], [], []
    for name, path, _mtime, _size in manifest:
        owners = set(explicit.get(name, ()))
        match = re.fullmatch(r"f_(\d+)\d{3}d?\.png", name, re.IGNORECASE)
        if match:
            owners.update(prefixes.get(match[1], ()))
        if len(owners) > 1:
            conflicts.append(name)
            continue
        if not owners:
            unmapped.append(name)
            continue
        template = _read_image(path)
        if template is None:
            unreadable.append(name)
            continue
        template.setflags(write=False)
        groups[next(iter(owners))].append((name, template))
    by_class = {}
    for sid, info in records.items():
        by_class.setdefault(info["class"], []).append(sid)
    report = {
        "catalog_count": len(records), "template_count": len(manifest),
        "covered_ids": sum(bool(v) for v in groups.values()),
        "missing_ids": [sid for sid, values in groups.items() if not values],
        "unmapped_templates": unmapped, "conflicting_templates": conflicts,
        "unreadable_templates": unreadable,
    }
    return records, {sid: tuple(v) for sid, v in groups.items()}, by_class, report


class FormationIdentityReader(AutoFormationFromChaldea):
    """复用编队的 ROI、空位/助战、模板匹配；每次读取只接收显式图像。

    不调用 _shot/_prepare_target_templates/run，因而不会使用缓存截图、
    下载 Chaldea 数据、移动或更换成员。实例仅存活于单次采集回调。
    """

    def __init__(self, roots, calibration=None, catalog_path=None):
        self.image_roots = list(roots)
        self.sx = self.sy = 1.0
        self.calibration = calibration or FormationCalibration()
        self.records, self.templates, self.by_class, self.coverage = _cached_identity_index(
            *_identity_manifest(self.image_roots, self.calibration.template_family,
                                catalog_path or os.path.join(_CUSTOM_DIR, "servant_list.json"))
        )
        self.config_marker = self._load_named_template("battle/配置变更.png")
        self.start_markers = [self._load_named_template(f"battle/{name}.png")
                              for name in ("开始任务", "战斗开始")]
        self.support_marker = self._load_named_template("battle/助战标记.png")
        if self.config_marker is None or self.support_marker is None or not any(
            x is not None for x in self.start_markers
        ):
            raise ValueError("formation_page_resource_missing")
        self.class_templates = {}
        for name, paths in self.calibration.class_templates:
            values = [(p, self._load_named_template(p)) for p in paths]
            if any(t is None for _, t in values):
                raise ValueError(f"class_template_missing:{name}")
            self.class_templates[name] = values

    def _rank(self, image, roi, identities):
        scores = []
        for sid in identities:
            match = self._match_servant(image, self.templates[sid], roi)
            if match is not None and np.isfinite(match[0]):
                scores.append((match[0], sid, match[2]))
        return sorted(scores, key=lambda x: (-x[0], x[1]))

    def _accepted(self, ranked):
        c = self.calibration
        return (c.verified and len(ranked) >= 2
                and ranked[0][0] >= c.identity_threshold
                and ranked[0][0] - ranked[1][0] >= c.identity_margin)

    def _class(self, image, roi):
        c = self.calibration
        if not c.class_roi or not self.class_templates:
            return None, Confidence(0.0, "class_templates_unavailable")
        x, y, w, h = c.class_roi
        ranked = []
        for name, templates in self.class_templates.items():
            match = self._match_servant(image, templates, (roi[0] + x, roi[1] + y, w, h))
            if match is not None and np.isfinite(match[0]):
                ranked.append((match[0], name))
        ranked.sort(reverse=True)
        if (c.verified and c.class_threshold is not None and c.class_margin is not None
                and len(ranked) > 1 and ranked[0][0] >= c.class_threshold
                and ranked[0][0] - ranked[1][0] >= c.class_margin):
            return ranked[0][1], Confidence(ranked[0][0], "formation_class_template")
        return None, Confidence(0.0, "class_uncertain")

    def read_frame(self, image, *, full_audit=False, diagnostics=False):
        image = _norm_img(image)
        if image is None:
            raise ValueError("screenshot_unavailable")
        height, width = image.shape[:2]
        self.sx, self.sy = width / BASE_W, height / BASE_H
        if width < BASE_W // 2 or abs(self.sx / self.sy - 1) > 0.015:
            raise ValueError("unsupported_formation_layout")
        confirm = self._match_template(image, self.config_marker)
        starts = [self._match_template(image, marker) for marker in self.start_markers]
        if not confirm or confirm[0] < 0.8 or not any(s and s[0] >= 0.8 for s in starts):
            raise ValueError("not_on_formation_confirmation")
        slots = []
        c = self.calibration
        for index, roi in enumerate(SLOT_ROIS):
            if self._is_empty_slot(image, roi):
                slots.append(FormationSlot(index + 1, "empty", is_support=False,
                                           reason="existing_empty_slot_heuristic"))
                continue
            support = self._match_template(image, self.support_marker, roi)
            is_support = None if support is None else support[0] >= SUPPORT_THRESHOLD
            class_id, class_conf = self._class(image, roi)
            ranked, scope = [], "not_calibrated"
            if c.verified or diagnostics:
                class_names = dict(c.class_groups).get(class_id, (class_id,))
                candidates = tuple(sid for name in class_names for sid in self.by_class.get(name, ()))
                if class_id and candidates and not full_audit:
                    ranked = self._rank(image, roi, candidates)
                    scope = "class"
                # 最后一帧始终全库复核，避免高置信度职阶误判排除真正身份。
                # 其余帧只要职阶或该职阶身份不可靠，也立即回退全库。
                if full_audit or not self._accepted(ranked):
                    ranked = self._rank(image, roi, self.templates)
                    scope = "all_audit" if full_audit else "all_fallback"
            identified = self._accepted(ranked)
            best = ranked[0] if ranked else (0.0, None, "")
            margin = best[0] - ranked[1][0] if len(ranked) > 1 else 0.0
            class_conflict = bool(identified and class_id and self.records[best[1]]["class"]
                                  not in dict(c.class_groups).get(class_id, (class_id,)))
            if class_conflict:
                class_id, class_conf = None, Confidence(0.0, "class_identity_conflict")
            slots.append(FormationSlot(
                slot=index + 1, status="identified" if identified else "unknown",
                servant_id=best[1] if identified else None, class_id=class_id,
                is_support=is_support, class_confidence=class_conf,
                identity_confidence=Confidence(best[0] if identified else 0.0,
                                               "formation_template" if identified else "unconfirmed"),
                catalog_class=self.records[best[1]]["class"] if identified else None,
                best_candidate_id=best[1], best_score=best[0], identity_margin=margin,
                matched_template=best[2], search_scope=scope,
                reason=("class_identity_conflict" if class_conflict else "") if identified else ("calibration_required" if not c.verified
                                               else "identity_score_or_margin_insufficient"),
            ))
        return tuple(slots)


def merge_formation_frames(frames):
    """三次独立截图全部同意才能确认；相同 ID 的灵衣变化不制造身份分差。"""
    if len(frames) != len(IDENTITY_FRAME_NODES) or any(
        tuple(s.slot for s in f) != (1, 2, 3, 4, 5, 6) for f in frames
    ):
        raise ValueError("incomplete_formation_frames")
    merged = []
    for observations in zip(*frames):
        last = observations[-1]
        same_identity = len({(s.status, s.servant_id) for s in observations}) == 1
        same_class = len({s.class_id for s in observations}) == 1
        same_support = len({s.is_support for s in observations}) == 1
        identity_ok = same_identity and same_support
        merged.append(replace(
            last,
            status=last.status if identity_ok else "unknown",
            servant_id=last.servant_id if identity_ok else None,
            catalog_class=last.catalog_class if identity_ok else None,
            is_support=last.is_support if same_support else None,
            class_id=last.class_id if same_class else None,
            class_confidence=min((s.class_confidence for s in observations), key=lambda c: c.value)
            if same_class else Confidence(0.0, "class_frame_disagreement"),
            identity_confidence=min((s.identity_confidence for s in observations), key=lambda c: c.value)
            if identity_ok else Confidence(0.0, "identity_frame_disagreement"),
            consistent_frames=len(frames) if identity_ok else 0,
            reason=last.reason if identity_ok else "formation_frame_disagreement",
        ))
    return tuple(merged)


@AgentServer.custom_recognition("formation_identity_frame")
class FormationIdentityFrame(CustomRecognition):
    def analyze(self, context: Context, argv: CustomRecognition.AnalyzeArg):
        param = json.loads(argv.custom_recognition_param)
        root, token = context_session(context)
        revision = param["revision"]
        try:
            if token != param["session_id"] or context.tasker.stopping:
                raise ValueError("formation_session_stopped")
            # 仅复用路径选择；不创建控制器、不请求/读取 cached_image。
            helper = AutoFormationFromChaldea()
            helper.context = context
            helper._init_paths()
            calibration = FormationCalibration.parse(param.get("calibration", {}))
            reader = FormationIdentityReader(helper.image_roots, calibration)
            slots = reader.read_frame(argv.image, full_audit=param.get("full_audit", False))
            sessions.append_frame(root, token, revision, slots)
            return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={"frame_ok": True})
        except Exception as exc:
            # 命中只代表本次采样已结束，不代表身份/页面识别成功。
            # 外层采集动作检查 error，失败不得走开始任务。
            try:
                sessions.append_frame(root, token, revision, (), str(exc))
            except ValueError:
                pass
            return CustomRecognition.AnalyzeResult(
                box=(0, 0, 1, 1), detail={"frame_ok": False, "reason": str(exc)}
            )


@AgentServer.custom_action("capture_initial_formation")
class CaptureInitialFormation(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        root, token = context_session(context)
        try:
            revision = sessions.begin_capture(root, token)
            node = context.get_node_data(argv.node_name) or {}
            config = (node.get("attach") or {}).get("calibration", {})
            calibration = FormationCalibration.parse(config)
            overrides = {
                name: {"recognition": {"type": "Custom", "param": {
                    "custom_recognition": "formation_identity_frame",
                    "custom_recognition_param": {
                        "session_id": token, "revision": revision,
                        "calibration": config, "full_audit": i == len(IDENTITY_FRAME_NODES) - 1,
                    },
                }}}
                for i, name in enumerate(IDENTITY_FRAME_NODES)
            }
            detail = context.run_task("编队身份-采样", pipeline_override=overrides)
            if context.tasker.stopping or detail is None or not detail.status.succeeded:
                raise ValueError("formation_capture_pipeline_failed")
            slots = merge_formation_frames(sessions.frames(root, token, revision))
            snapshot = sessions.publish(root, token, revision, slots, calibration.calibration_id)
            names = json.loads(Path(_CUSTOM_DIR, "servant_list.json").read_text(encoding="utf-8"))
            names = {str(x["id"]): x["name"] for x in names["servants"]}
            summary = "; ".join(
                f"{s.slot}:{s.status}:{names.get(s.servant_id, s.servant_id or '?')}"
                f":support={s.is_support}:reason={s.reason}"
                for s in slots
            )
            mfaalog.info(f"[初始编队] task={root} session={token} revision={snapshot.revision} "
                         f"calibration={snapshot.calibration_id} {summary}")
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            try:
                sessions.invalidate(root, token, str(exc))
            except ValueError:
                pass
            mfaalog.error(f"[初始编队] 采集失败，旧快照已失效，不开始战斗: {exc}")
            return CustomAction.RunResult(success=False)
