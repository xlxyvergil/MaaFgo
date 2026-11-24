# -*- coding: utf-8 -*-
"""强化从者：指定从者 + 目标等级，自动喂狗粮升级（含突破）

架构分工：
- pipeline（强化从者.json）负责「导航」「强化一次」这两段纯模板匹配+点击
  的流程（识别更稳、坐标随分辨率自适应）。
- 本 action 负责必须动态判断的逻辑：从者头像多阶段定位（cv2）、
  等级 OCR 判断、循环编排。

attach 参数（pipeline 节点「执行强化从者」定义默认值，option 覆盖）：
    servant_id    str  从者 id（联动选择 / 职介列表选择时由 option 覆盖）
    target_level  int  目标等级（>= 该等级则停止）
    use_current_servant  bool  直接强化游戏界面里已选中的从者
    exp_bonus     bool  在列表中识别带闪烁经验加成字样的头像
"""
import os
import re
import time
import json
import traceback

import numpy as np
import cv2

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
from maa.pipeline import JOCR

import mfaalog
from resource_package import resolve_resource_package
from box_action import ExecuteBoxTask

BASE_W, BASE_H = 1280, 720

# ==================== 模板图 ====================
# 强化从者目录（resource/{cn|base}/image/强化从者/）
TPL_LEVELUP = "强化完成界面.png"       # 判断已进入从者强化详情页
TPL_SERVANT_SEL = "强化界面判定.png"   # 全图判定已在从者列表

# 详情页标志（点从者头像后应出现，用于确认已进入从者详情页）
LEVELUP_ROI = (375, 652, 144, 63)              # 强化完成界面标志（详情页底部固定元素）

# 结束条件判断图（道具不足 / 狗粮用完）
TPL_NO_MATERIAL = ["从者技能强化/无法强化.png", "从者技能强化/道具不足.png"]
TPL_EMPTY_SLOT = "强化从者/槽位标志.png"   # 狗粮槽空 = 狗粮用完
SLOT_ROI = (394, 194, 80, 84)              # 槽位标志 ROI（与 pipeline 一致）
# pipeline 在狗粮耗尽后会先退出强化界面；必须读取此终止节点的命中结果，
# 不能等回到主界面后再依赖空槽模板判断。
DOGFOOD_DEPLETED_END_NODE = "强化从者-狗粮消耗完毕结束"

# 从者头像目录（f_{servantId}{stage}.png，stage 0-3）
SERVANT_FACE_DIR = "servant_face"

# ==================== 坐标（基准 720p，待实测校准）====================
TAP_LEVELUP_BTN = (197, 157)   # 强化界面「强化」按钮（进入从者选择）
# 从者列表滚动（手指上滑，列表下翻）
SWIPE_LIST_BEGIN = (600, 560)
SWIPE_LIST_END = (600, 200)
# 等级 OCR ROI（从者详情页等级显示区，待校准）
LEVEL_ROI = (396, 450, 200, 47)

TH_TEMPLATE = 0.65
FACE_THRESHOLD = 0.55         # 从者头像匹配阈值（列表头像缩放/变形，比通用模板更宽松）
EXP_BONUS_THRESHOLD = 0.90
EXP_BONUS_LIST_ROI = (60, 171, 935, 549)  # 小图标列表，不含两侧按钮、滚动条或材料统计
EXP_BONUS_SAMPLE_DELAYS = (0.0, 0.42, 0.57, 0.43, 0.58)
MAX_FIND_SERVANT_ROUND = 80   # 头像定位最大滚动轮数
MAX_LEVELUP_ROUND = 120       # 强化最大循环轮数


# ==================== 模块级原语 ====================

def _norm_img(img):
    if img is None:
        return None
    if hasattr(img, "to_numpy"):
        img = img.to_numpy()
    elif not isinstance(img, np.ndarray):
        try:
            from PIL import Image
            if isinstance(img, Image.Image):
                img = np.array(img)
        except Exception:
            pass
    arr = np.asarray(img)
    if arr is None or arr.size == 0:
        return None
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    elif arr.ndim == 3:
        if arr.shape[2] == 4:
            arr = arr[:, :, :3]
        if arr.shape[2] != 3:
            return None
    else:
        return None
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    # Maa 截图对象的 native 缓冲区可能在当前调用返回后被复用或释放。
    # 头像匹配会在同一帧上连续进行多次 cv2 运算，必须持有独立副本。
    return arr.copy()


def _read_tpl(path):
    if not os.path.isfile(path):
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def _compose_exp_portraits(face, badge, sx, sy):
    """按小图标布局合成活动头像，同时保留字样熄灭时的普通版本。"""
    # 使用 BBC 的区服字样及 1920×1080 小图标拼图布局，国服列表实图已核对。
    face = cv2.resize(face, (170, 170), interpolation=cv2.INTER_LANCZOS4)
    boosted = face.copy()
    h, w = badge.shape[:2]
    dst = boosted[60:60 + h, 30:30 + w]
    alpha = badge[:, :, 3:4].astype(np.float32) / 255
    dst[:] = np.rint(dst * (1 - alpha) + badge[:, :, :3] * alpha).astype(np.uint8)
    size = (max(1, round(125 * BASE_W / 1920 * sx)), max(1, round(80 * BASE_H / 1080 * sy)))
    plain, combined = (cv2.resize(img[30:110, 35:160], size, interpolation=cv2.INTER_AREA)
                       for img in (face, boosted))
    # 两行字样的上半行相同。只排除下半行的倍率区，2 倍、3 倍等共用模板。
    mask = np.full((80, 125), 255, dtype=np.uint8)
    mask[55:, 16:87] = 0
    return plain, combined, cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)


@AgentServer.custom_action("execute_servant_up")
class ExecuteServantUp(CustomAction):
    """强化从者：选从者 -> 喂狗粮升级到目标等级"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            self._context = context
            self._controller = context.tasker.controller

            node = context.get_node_data(argv.node_name) or {}
            attach = node.get("attach") or {}
            servant_id = str(attach.get("servant_id") or "").strip()
            target_level = int(attach.get("target_level", 0))
            tidy_giftbox = bool(attach.get("tidy_giftbox", False))
            self._exp_bonus = bool(attach.get("exp_bonus", False))

            if target_level <= 0:
                mfaalog.error("[强化从者] 未设置目标等级")
                return CustomAction.RunResult(success=False)

            use_current_servant = bool(attach.get("use_current_servant", False))
            # 资源包区分服务器 -> 模板目录
            self._init_dirs()

            # 坐标自适应
            self._init_scale()

            if use_current_servant:
                if tidy_giftbox:
                    mfaalog.error("[强化从者] 从礼物盒返回后需要重新定位从者。要自动领取经验，请将"
                                 "「从者选择方式」改为「星级/职介/从者名联动选择」或「搜索全从者」。")
                    return CustomAction.RunResult(success=False)
                result = self._levelup_current_servant(target_level)
                return CustomAction.RunResult(
                    success=result in ("target_reached", "dogfood_empty", "no_material")
                )

            # 解析从者（联动选择 / 职介列表选择都传 servant_id，按 id 查表）
            servant = self._resolve_servant(servant_id)
            if servant is None:
                mfaalog.error("[强化从者] 未能确定目标从者")
                return CustomAction.RunResult(success=False)
            mfaalog.info(f"[强化从者] 目标从者 {servant['name']}({servant['id']}) "
                         f"{servant['rarity']}星 {servant['class']} 目标等级 {target_level}")

            if tidy_giftbox:
                ok = self._tidy_and_levelup_loop(servant, target_level)
                return CustomAction.RunResult(success=ok)

            # 单独执行强化时，先由稳定的主界面入口开始导航；
            # 整理礼物盒循环的页面衔接则完全交给 pipeline 处理。
            if not self._return_to_main():
                return CustomAction.RunResult(success=False)
            result = self._levelup_servant(servant, target_level)
            if result == "failed":
                return CustomAction.RunResult(success=False)
            if result == "dogfood_empty":
                mfaalog.warning("[强化从者] 狗粮已用完，未启用整理礼物盒循环，任务完成")

            mfaalog.info("[强化从者] 完成")
            return CustomAction.RunResult(success=True)

        except Exception as e:
            mfaalog.error(f"[强化从者] 异常: {e}\n{traceback.format_exc()}")
            return CustomAction.RunResult(success=False)

    def _levelup_current_servant(self, target_level):
        image = self._shot()
        if self._context.tasker.stopping:
            return "stopped"
        if image is None:
            mfaalog.error("[强化从者] 未能读取游戏画面。请确认模拟器仍在运行，再重试。")
            return "failed"
        page = self._context.run_recognition("搓丸子-从者强化已选页面", image)
        if page is None or not page.hit:
            mfaalog.error("[强化从者] 未识别到已选从者的强化页面，无法确定要强化的对象。"
                         "请在游戏中打开「从者强化」，选中要升级的从者后重试。")
            return "failed"
        empty_slots = self._context.run_recognition("搓丸子-测试从者素材已清空", image)
        if empty_slots is None or not empty_slots.hit:
            mfaalog.error("[强化从者] 未能确认素材槽全部为空。为避免消耗当前选中的卡牌，"
                         "请在游戏中点「清除」，确认素材槽已清空后重试。")
            return "failed"
        mfaalog.info(f"[强化从者] 从当前已选从者的强化页面开始，目标等级 {target_level}")
        return self._levelup_loop(target_level)

    def _tidy_and_levelup_loop(self, servant, target_level):
        """循环执行「整理礼物盒 -> 强化从者」，页面衔接由 pipeline 负责。"""
        box_node = self._context.get_node_data("执行整理礼物盒") or {}
        box_attach = dict(box_node.get("attach") or {})
        # 强化循环只复用整理阶段；即使礼物盒任务保留了贩卖配置，也绝不在此执行贩卖。
        for key in ("sell3", "sell4", "sell5"):
            box_attach[key] = False

        cycle = 0
        while True:
            if self._context.tasker.stopping:
                return False
            cycle += 1
            mfaalog.info(f"[强化从者] ===== 第 {cycle} 轮：整理 -> 强化 =====")

            box_task = ExecuteBoxTask()
            tidy_ok, _giftbox_exhausted, received_any = box_task.run_tidy_once(
                self._context, box_attach, reset_hit_counts=True, controller=self._controller
            )
            if not tidy_ok:
                mfaalog.error("[强化从者] 礼物盒整理失败")
                return False
            # 停止条件以本次实际领取为准（「全部领取」或「领取选中」均可），而不是
            # 后续筛选列表是否被扫空：同一次进入中已领过一次后再扫空，仍要继续强化。
            if not received_any:
                mfaalog.warning(
                    "[强化从者] 本次进入礼物盒未实际领取任何东西，任务完成"
                )
                return True
            result = self._levelup_servant(servant, target_level)
            if result == "target_reached":
                mfaalog.info(f"[强化从者] 已达到目标等级 {target_level}，任务完成")
                return True
            if result == "no_material":
                mfaalog.warning("[强化从者] 道具不足，任务完成")
                return True
            if result == "dogfood_empty":
                mfaalog.info("[强化从者] 狗粮已用完，进入下一轮礼物盒整理")
                continue
            else:
                mfaalog.error(f"[强化从者] 强化阶段异常结束：{result}")
                return False

    def _levelup_servant(self, servant, target_level):
        """完成一次从导航、选从者到喂狗粮的强化过程，返回结束原因。"""
        # 导航到强化界面 + 进入从者选择
        try:
            self._context.run_task("强化从者-导航到强化界面")
        except Exception as e:
            mfaalog.warning(f"[强化从者] 导航 pipeline 异常: {e}")
        if self._context.tasker.stopping:
            return "stopped"
        if not self._in_levelup() and not self._in_servant_select():
            mfaalog.error("[强化从者] 导航失败，未到达强化界面")
            return "failed"

        # 进入从者选择界面
        self._enter_servant_select()

        # 筛选星级职介 + 头像定位
        if not self._select_servant(servant):
            if not self._context.tasker.stopping and not getattr(self, "_exp_bonus", False):
                mfaalog.error("[强化从者] 定位从者失败")
            return "failed"

        return self._levelup_loop(target_level)

    def _return_to_main(self):
        """每轮强化用完狗粮后回主界面，确保下一轮能重新导航。"""
        try:
            detail = self._context.run_task("回主界面")
        except Exception as e:
            mfaalog.warning(f"[强化从者] 回主界面 pipeline 异常: {e}")
            return False
        if self._context.tasker.stopping:
            return False
        if detail is None or detail.status.failed or not detail.status.succeeded:
            mfaalog.error("[强化从者] 回主界面失败")
            return False
        return True

    # ---------- 初始化 ----------

    def _init_dirs(self):
        layer = resolve_resource_package(self._context, caller="强化从者")
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        def _resolve(l, sub):
            for c in [
                os.path.join(root_dir, "assets", "resource", l, "image", sub),
                os.path.join(root_dir, "resource", l, "image", sub),
            ]:
                if os.path.isdir(c):
                    return c
            return None

        self.tpl_dir = _resolve(layer, "强化从者") or ""
        self.tpl_base_dir = _resolve("base", "强化从者") or self.tpl_dir
        self.face_base_dir = _resolve("base", SERVANT_FACE_DIR) or ""
        self.face_dir = _resolve(layer, SERVANT_FACE_DIR) or self.face_base_dir
        # 整理礼物盒（复用星级图标 + 决定/重置按钮）
        self.box_dir = _resolve(layer, "整理礼物盒") or _resolve("base", "整理礼物盒") or ""

    def _init_scale(self):
        self.sx = self.sy = 1.0
        img = _norm_img(self._controller.post_screencap().wait().get())
        if img is None:
            return
        h, w = img.shape[:2]
        self.sx = w / float(BASE_W)
        self.sy = h / float(BASE_H)

    def _px(self, x):
        return int(round(x * self.sx))

    def _py(self, y):
        return int(round(y * self.sy))

    # ---------- 基础封装 ----------

    def _shot(self):
        return _norm_img(self._controller.post_screencap().wait().get())

    def _tap(self, x, y, delay=0.4):
        self._controller.post_click(self._px(x), self._py(y)).wait()
        time.sleep(delay)

    def _tpl(self, name):
        """模板路径：优先「强化从者」目录，支持跨目录引用（整理礼物盒/、servant_face/）"""
        if name.startswith("整理礼物盒/"):
            p = os.path.join(self.box_dir, name.split("/", 1)[1])
            return p if os.path.isfile(p) else name
        if name.startswith("servant_face/"):
            filename = name.split("/", 1)[1]
            p = os.path.join(self.face_dir, filename)
            if os.path.isfile(p):
                return p
            # CN 可只覆盖某个灵基头像，其余文件仍逐张继承 base。
            pb = os.path.join(getattr(self, "face_base_dir", self.face_dir), filename)
            return pb if os.path.isfile(pb) else name
        p = os.path.join(self.tpl_dir, name)
        if os.path.isfile(p):
            return p
        pb = os.path.join(self.tpl_base_dir, name)
        return pb if os.path.isfile(pb) else p

    def _match(self, name, roi=None, threshold=TH_TEMPLATE):
        tpl = _read_tpl(self._tpl(name))
        if tpl is None:
            return None
        img = self._shot()
        if img is None:
            return None
        tw = max(1, int(tpl.shape[1] * self.sx))
        th = max(1, int(tpl.shape[0] * self.sy))
        if tw != tpl.shape[1] or th != tpl.shape[0]:
            tpl = cv2.resize(tpl, (tw, th))
        region = img
        ox = oy = 0
        if roi is not None:
            x, y, w, h = roi
            x, y = self._px(x), self._py(y)
            w, h = self._px(w), self._py(h)
            x, y = max(0, x), max(0, y)
            w = min(w, img.shape[1] - x)
            h = min(h, img.shape[0] - y)
            if w <= 0 or h <= 0:
                return None
            region = img[y:y + h, x:x + w]
            ox, oy = x, y
        if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
            return None
        res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
        _mi, max_v, _ml, max_l = cv2.minMaxLoc(res)
        if max_v < threshold:
            return None
        cx = ox + max_l[0] + tpl.shape[1] // 2
        cy = oy + max_l[1] + tpl.shape[0] // 2
        return float(max_v), int(cx), int(cy)

    # ---------- 从者解析 ----------

    def _load_servant_data(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servant_list.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            mfaalog.error(f"[强化从者] 读取 servant_list.json 失败: {e}")
            return {"class_cn": {}, "servants": []}

    def _resolve_servant(self, servant_id):
        data = self._load_servant_data()
        servants = data["servants"]
        if servant_id:
            for s in servants:
                if s["id"] == servant_id or servant_id in (s.get("aliases") or []):
                    return s
            mfaalog.warning(f"[强化从者] 未找到 id={servant_id} 的从者")
        return None

    # ---------- 界面判断 ----------

    def _in_levelup(self):
        # 详情页判断：强化完成界面标志（详情页底部固定元素）
        return self._match(TPL_LEVELUP, roi=LEVELUP_ROI) is not None

    def _in_servant_select(self):
        # 全图判定「强化界面判定.png」是否已在从者列表
        return self._match(TPL_SERVANT_SEL) is not None

    # ---------- 进入从者选择 + 选从者 ----------

    def _enter_servant_select(self):
        """强化界面点「强化」按钮进入从者选择列表"""
        enter = getattr(self, "_enter_select", None)
        if enter is not None:
            return enter()
        for _ in range(5):
            self._check_network_if_configured()
            if self._in_servant_select():
                return
            self._tap(*TAP_LEVELUP_BTN, delay=1.0)

    def _select_servant(self, servant):
        """筛选星级职介（option 静态注入模板，走 pipeline）+ 头像定位"""
        try:
            self._context.run_task("强化从者-筛选准备")
        except Exception as e:
            mfaalog.warning(f"[强化从者] 筛选 pipeline 异常: {e}")
        self._check_network_if_configured()
        reset = getattr(self, "_list_reset", None)
        if reset is not None:
            reset()
        # 头像定位
        return self._find_servant(servant)

    def _check_network_if_configured(self):
        callback = getattr(self, "_network_check", None)
        if callback is not None:
            callback()

    def _exp_portrait_templates(self, faces):
        path = self._tpl("经验加成字样.png")
        badge = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED) if os.path.isfile(path) else None
        if badge is None or badge.shape != (60, 110, 4):
            return []
        templates = []
        for name in faces:
            face = _read_tpl(self._tpl(name))
            if face is None or face.shape[0] != face.shape[1]:
                # 国服可能覆盖成仓库专用局部图，拼图需要完整方形头像。
                face = _read_tpl(os.path.join(self.face_base_dir, os.path.basename(name)))
            if face is None or face.shape[0] != face.shape[1]:
                continue
            plain, combined, mask = _compose_exp_portraits(face, badge, self.sx, self.sy)
            templates.extend(((name, plain, None), (f"{name}（EXP加成）", combined, mask)))
        return templates

    def _best_exp_portrait(self, image, templates):
        x, y, w, h = EXP_BONUS_LIST_ROI
        x, y, w, h = self._px(x), self._py(y), self._px(w), self._py(h)
        region = image[y:y + h, x:x + w]
        best = None
        for name, template, mask in templates:
            th, tw = template.shape[:2]
            if region.shape[0] < th or region.shape[1] < tw:
                continue
            scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED, mask=mask)
            scores[~np.isfinite(scores)] = -1
            _, score, _, (left, top) = cv2.minMaxLoc(scores)
            if best is None or score > best[0]:
                best = (score, x + left + tw // 2, y + top + th // 2, name)
        return best

    def _sample_exp_portrait(self, servant, templates, round_index):
        # 单页在不同的闪烁相位取样，仅使用当前帧仍然命中的位置。
        hits = []
        for frame, delay in enumerate(EXP_BONUS_SAMPLE_DELAYS, 1):
            if delay:
                time.sleep(delay)
            if self._context.tasker.stopping:
                return None
            image = self._shot()
            best = self._best_exp_portrait(image, templates) if image is not None else None
            if best is None:
                continue
            score, x, y, template = best
            mfaalog.info(f"[强化从者] {servant['name']} 第{round_index}轮第{frame}帧 "
                         f"经验加成头像最高分 {score:.3f}/{EXP_BONUS_THRESHOLD:.2f} @ ({x},{y}) {template}")
            if score < EXP_BONUS_THRESHOLD:
                continue
            if any(abs(x - px) <= self._px(8) and abs(y - py) <= self._py(8) for px, py in hits):
                return best
            hits.append((x, y))
        return None

    def _run_exp_selection_step(self, node, terminal, reset=(), override=None):
        for name in reset:
            self._context.clear_hit_count(name)
        detail = self._context.run_task(node, pipeline_override=override or {})
        return (not self._context.tasker.stopping and detail is not None and detail.status.succeeded
                and any(step.name == terminal and step.completed for step in detail.nodes))

    def _select_exp_candidate(self, servant, candidate):
        _, x, y, _ = candidate
        image = self._shot()
        if self._context.tasker.stopping:
            return False
        if image is None:
            mfaalog.error("[强化从者] 未能读取当前从者列表，已停止选人。请确认模拟器仍在运行，再重试。")
            return False
        current = "搓丸子-候选已是当前从者"
        # 已选卡片在列表中显示「卸除」，应关闭列表，避免重复点击反而解除选择。
        label_left = max(self._px(60), x - self._px(100))
        label_top = min(self._py(610), y + self._py(30))
        reco = self._context.run_recognition(current, image, pipeline_override={
            current: {"recognition": {"param": {
                "roi": [label_left, label_top, self._px(200), min(self._py(110), image.shape[0] - label_top)]
            }}}
        })
        if reco and reco.hit:
            node = "搓丸子-关闭从者选择"
            terminal = "搓丸子-关闭从者选择执行"
            selected = self._run_exp_selection_step(node, terminal, (terminal,))
        else:
            node = "搓丸子-点击升级从者候选"
            terminal = "搓丸子-点击升级从者候选执行"
            target = {terminal: {"action": {"param": {"target": [x, y, 1, 1]}}}}
            selected = self._run_exp_selection_step(node, terminal, (terminal,), target)
        if selected and self._verify_exp_servant(servant):
            mfaalog.info(f"[强化从者] 已核对经验加成从者 {servant['name']}")
            return True
        if not self._context.tasker.stopping:
            mfaalog.error(f"[强化从者] 未能确认当前选中的是{servant['name']}，已停止强化。"
                         "请在游戏中选中目标并清空素材槽，再将「从者选择方式」改为「当前界面已选从者」重试。")
        return False

    def _verify_exp_servant(self, servant):
        expected = [f"^{re.escape(name)}$" for name in (servant.get("name"), servant.get("name_jp")) if name]
        if not expected:
            return False

        def name_matches(node, image):
            result = self._context.run_recognition(node, image, pipeline_override={
                node: {"recognition": {"param": {"expected": expected}}}
            })
            return result is not None and result.hit

        for _ in range(12):
            if self._context.tasker.stopping:
                return False
            image = self._shot()
            if image is not None:
                page = self._context.run_recognition("搓丸子-从者强化已选页面", image)
                if page and page.hit:
                    break
            time.sleep(0.5)
        else:
            return False
        if name_matches("搓丸子-已选从者名称", image):
            return True

        # 卡面小字不可读时用现有资料页流程读大字，确认姓名后才关闭。
        if not self._run_exp_selection_step("搓丸子-打开当前从者资料", "搓丸子-从者资料就绪",
                                            ("搓丸子-长按当前从者资料",)):
            return False
        image = self._shot()
        if image is None or not name_matches("搓丸子-资料从者名称", image):
            return False
        return self._run_exp_selection_step("搓丸子-关闭当前从者资料", "搓丸子-从者资料已关闭",
                                            ("搓丸子-执行关闭从者资料",))

    def _find_servant(self, servant):
        """头像多阶段匹配 + 滚动查找，找到点击进入"""
        sid = servant["id"]
        # 头像文件结构：f_{基础ID}{形象类型2位}{灵基阶段1位}[d].png
        #   基础ID = servantId 去掉末尾「00」
        #   形象类型：00=标准、30~90=灵衣、01/02/10/51 等=特殊形态（杰基尔海德、玛修剧情形态等）
        #   灵基阶段：0-4；d = 特殊立绘标记（如奥德修斯 ascension1 的 f_4038001d.png）
        # 动态探测形象类型 00~99，覆盖所有可能，os.path.isfile 过滤不存在的
        faces = [
            f"{SERVANT_FACE_DIR}/{name}"
            for name in servant.get("images") or []
        ]
        # images 不完整时仍按规范 ID 和形态别名回填探测；普勒拉蒂的 505600
        # 与 505700 等多形态从者因此都会加载到同一选择目标中。
        for identity_id in (sid, *(servant.get("aliases") or [])):
            prefix = identity_id[:-2] if len(identity_id) > 2 else identity_id
            for ct in range(100):
                ct_s = f"{ct:02d}"
                for stage in range(5):
                    base = f"{SERVANT_FACE_DIR}/f_{prefix}{ct_s}{stage}"
                    faces.append(f"{base}.png")
                    faces.append(f"{base}d.png")
        # 过滤实际存在的头像
        faces = list(dict.fromkeys(
            f for f in faces if os.path.isfile(self._tpl(f))
        ))
        if not faces:
            mfaalog.error(f"[强化从者] 无头像资源: {sid}")
            return False
        exp_bonus = getattr(self, "_exp_bonus", False)
        exp_templates = self._exp_portrait_templates(faces) if exp_bonus else []
        if exp_bonus and not exp_templates:
            mfaalog.error("[强化从者] 经验加成识别图片缺失或无法读取。请更新 MaaFGO 后重试，"
                         "也可在游戏中选好目标和清空素材槽后使用「当前界面已选从者」。")
            return False
        if exp_bonus:
            for name in ("搓丸子网络-重试", "搓丸子网络-重试耗尽"):
                self._context.clear_hit_count(name)
        max_rounds = getattr(self, "_max_find_rounds", MAX_FIND_SERVANT_ROUND)
        for _round in range(max_rounds):
            self._check_network_if_configured()
            if self._context.tasker.stopping:
                return False
            if exp_bonus:
                candidate = self._sample_exp_portrait(servant, exp_templates, _round + 1)
                if candidate is not None:
                    return self._select_exp_candidate(servant, candidate)
                if self._context.tasker.stopping:
                    return False
                if _round + 1 < max_rounds:
                    swipe = "强化从者-经验加成列表下滑"
                    if not self._run_exp_selection_step(swipe, swipe):
                        if not self._context.tasker.stopping:
                            mfaalog.error("[强化从者] 未确认小图标从者列表，无法继续查找。"
                                         "请在游戏的「从者强化」列表切换到小图标后重试。")
                        return False
                continue
            # 先拿到本轮所有头像的最高匹配分（threshold=0 不过滤），输出日志方便排查
            best = None  # (score, cx, cy, face)
            locator = getattr(self, "_candidate_locator", None)
            if locator is not None:
                best = locator(servant, faces)
            else:
                for f in faces:
                    roi = getattr(self, "_face_roi", None)
                    m = self._match(f, threshold=0.0, roi=roi) if roi is not None else self._match(f, threshold=0.0)
                    if m is not None and (best is None or m[0] > best[0]):
                        best = (m[0], m[1], m[2], f)
            if best is not None:
                mfaalog.info(f"[强化从者] 第{_round}轮 头像最高分 {best[0]:.3f} "
                             f"@ ({best[1]},{best[2]}) {best[3]}")
            if best is not None and best[0] >= getattr(self, "_face_threshold", FACE_THRESHOLD):
                select = getattr(self, "_candidate_selector", None)
                if select is not None:
                    select(servant, best)
                else:
                    self._controller.post_click(best[1], best[2]).wait()
                # 详情页加载需要时间，循环等待判断（最多 6 秒）
                entered = False
                for _ in range(12):
                    time.sleep(0.5)
                    self._check_network_if_configured()
                    if self._in_levelup():
                        entered = True
                        break
                if entered:
                    verify = getattr(self, "_verify_selected", None)
                    if verify is not None:
                        verify(servant)
                    mfaalog.info(f"[强化从者] 已定位从者 {servant['name']}")
                    return True
                mfaalog.warning(f"[强化从者] 点头像({best[0]:.3f})后未进入详情页，继续查找")
            # 未命中，滚动
            self._controller.post_swipe(self._px(SWIPE_LIST_BEGIN[0]), self._py(SWIPE_LIST_BEGIN[1]),
                                        self._px(SWIPE_LIST_END[0]), self._py(SWIPE_LIST_END[1]), 400).wait()
            time.sleep(0.8)
        if exp_bonus:
            mfaalog.error(f"[强化从者] 未能在列表中确认{servant['name']}的头像。请核对该从者是否在列表中，"
                         "以及头像上是否显示「获得EXP」加成。也可选好目标并清空素材槽，使用「当前界面已选从者」。")
        else:
            mfaalog.warning("[强化从者] 头像定位达到最大轮数，未找到从者")
        return False

    # ---------- 强化循环 ----------

    def _levelup_loop(self, target_level):
        for _round in range(MAX_LEVELUP_ROUND):
            self._check_network_if_configured()
            if self._context.tasker.stopping:
                return "stopped"
            level = self._read_level()
            if level is None:
                mfaalog.warning("[强化从者] 等级识别失败，继续尝试")
                time.sleep(1.0)
                continue
            mfaalog.info(f"[强化从者] 当前等级 {level} / 目标 {target_level}")
            if level >= target_level:
                mfaalog.info(f"[强化从者] 已达到目标等级 {target_level}")
                return "target_reached"
            # 走 pipeline 强化一次
            # 命中计数在整个 task 内共享；每次强化前清零，确保只读取本次的终止原因。
            self._context.clear_hit_count(DOGFOOD_DEPLETED_END_NODE)
            try:
                self._context.run_task("强化从者-强化一次")
            except Exception as e:
                mfaalog.warning(f"[强化从者] 强化一次 pipeline 异常: {e}")
            self._check_network_if_configured()
            if self._context.get_hit_count(DOGFOOD_DEPLETED_END_NODE) > 0:
                mfaalog.info("[强化从者] pipeline 已确认狗粮消耗完毕")
                return "dogfood_empty"
            # 道具不足 / 狗粮用完，结束强化
            if any(self._match(t) is not None for t in TPL_NO_MATERIAL):
                mfaalog.warning("[强化从者] 道具不足，结束强化")
                return "no_material"
            if self._match(TPL_EMPTY_SLOT, roi=SLOT_ROI) is not None:
                mfaalog.info("[强化从者] 狗粮用完，结束强化")
                return "dogfood_empty"
        mfaalog.warning("[强化从者] 强化达到最大轮数")
        return "max_round"

    def _read_level(self):
        """OCR 识别从者当前等级"""
        img = self._shot()
        if img is None:
            return None
        x, y, w, h = LEVEL_ROI
        x, y = self._px(x), self._py(y)
        w, h = self._px(w), self._py(h)
        x, y = max(0, x), max(0, y)
        w = min(w, img.shape[1] - x)
        h = min(h, img.shape[0] - y)
        if w <= 0 or h <= 0:
            return None
        try:
            detail = self._context.run_recognition_direct("OCR", JOCR(roi=(x, y, w, h)), img)
        except Exception as e:
            mfaalog.warning(f"[强化从者] OCR 调用异常: {e}")
            return None
        if detail is None:
            return None
        for r in detail.all_results:
            text = (getattr(r, "text", "") or "").strip()
            mfaalog.info(f"[强化从者] OCR 等级原文: {text!r}")
            m = re.search(r"\d+", text)
            if m and m.group().isdigit():
                return int(m.group())
        return None
