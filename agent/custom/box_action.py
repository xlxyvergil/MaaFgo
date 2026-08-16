# -*- coding: utf-8 -*-
"""
整理礼物盒（理盒盒）—— 一键领取 + 按数量保留 + 贩卖狗粮

架构分工：
- pipeline（整理礼物盒.json）负责「导航」「一键领取循环」等纯
  模板匹配+点击的流程（识别更稳、坐标随分辨率自适应）。
- 本 action 负责必须用 cv2 的判断类逻辑：筛选取色、OCR 数字识别、
  勾选态判断、贩卖循环（长按选中/销毁/QP 满处理），以及整体编排。

坐标自适应：启动时截一帧拿实际分辨率，算缩放系数，统一应用到
post_click / post_swipe / 模板匹配 / 取色 ROI（基准 1280x720）。

attach 参数（pipeline 节点「执行整理礼物盒」定义默认值，option 覆盖）：
    one_key3/4/5  bool  一键领取 3/4/5 星狗粮（不管数量，全部领走）
    keep3/4/5     bool  是否按数量保留 3/4/5 星狗粮
    keep3_num / keep4_num / keep5_num  int  保留数量（>= 该值则保留，< 该值则领取）
    sell3/4/5     bool  是否贩卖 3/4/5 星狗粮（去灵基变还卖掉换 QP）
    aqf           bool  QP 满时是否自动点掉继续（True 继续 / False 停止贩卖）

资源目录：通过「资源包配置」节点的 attach.resource_package 区分
    "cn"   -> resource/cn/image/整理礼物盒/   (B服)
    "base" -> resource/base/image/整理礼物盒/ (日服)
"""
import os
import re
import time

import numpy as np
import cv2

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
from maa.pipeline import JOCR

import mfaalog

BASE_W, BASE_H = 1280, 720

# ==================== 模板图（中文命名，resource/{cn|base}/image/整理礼物盒/）====================
TPL_IN_BOX = "礼物盒界面标志.png"          # 礼物盒界面标题栏，判断已进入礼物盒
TPL_GIFT_FULL = "礼物盒已满提示.png"        # 礼物盒已满弹窗按钮
TPL_DECIDE = "筛选面板决定按钮.png"         # 筛选面板底部「决定」
TPL_RECEIVE = "领取确认按钮.png"            # 领取确认弹窗「领取」按钮
TPL_DF = ["三星狗粮图标.png", "四星狗粮图标.png", "五星狗粮图标.png"]
TPL_CHOSEN = "勾选框已选中.png"
TPL_UNCHOSEN = "勾选框未选中.png"
# 贩卖狗粮（灵基变还）
TPL_LJBH = "灵基变还界面标志.png"         # 判断已进入灵基变还界面
TPL_QP_FULL = "QP已满提示.png"            # QP 已满弹窗

# ==================== 坐标（基准 720p，运行时 × scale 自适应）====================
# 筛选固定动作（点筛选、重置、从者经验值、下滑）已移到 pipeline「整理礼物盒-筛选准备」，
# 这里只保留必须用 cv2 取色判断的星级相关坐标：
STAR_ROI = [(580, 467, 33, 33), (393, 467, 33, 33), (205, 467, 33, 33)]  # 3/4/5 星取色区
STAR_TAP = [(640, 483), (453, 483), (265, 483)]                          # 3/4/5 星点击位
# 按数量保留
ROI_DF_COL = (100, 183, 107, 553)          # 狗粮图标列（左侧竖列）
TAP_GET_CHOSEN = (1150, 372)               # 「领取选中」按钮
# 数字 OCR（相对狗粮图标中心 p 的偏移）
NUM_ROI_DX = 179                           # 数字区左偏移
NUM_ROI_DY = -51                           # 数字区上偏移
NUM_ROI_W = 79                             # 数字区宽
NUM_ROI_H = 38                             # 数字区高
# 勾选框（相对狗粮图标中心 p 的偏移，图标右侧偏下）
CHOSEN_DX = 667                            # 勾选框检测区左偏移
CHOSEN_DY = 29                             # 勾选框检测区上偏移
CHOSEN_W = 60                              # 检测区宽
CHOSEN_H = 60                              # 检测区高
CHOSEN_TAP_DX = 697                        # 勾选框点击位 x 偏移
CHOSEN_TAP_DY = 29                         # 勾选框点击位 y 偏移

# 取色参考（FGO 筛选面板：蓝色=未选中，灰色=选中）
RGB_UNSELECTED = (61, 112, 196)   # 蓝
RGB_SELECTED = (215, 215, 215)    # 灰

TH_TEMPLATE = 0.65     # 模板匹配阈值
MAX_FILTER_GET_ROUND = 60  # 按数量保留最大滚动轮数

# ==================== 贩卖狗粮（灵基变还）坐标（基准 720p，待实测校准）====================
# 滑动全选轨迹：长按第一张卡片 → 右滑到最右 → 下滑到最下（框选整屏宫格）
TAP_CARD1 = (136, 248)             # 第一张卡片中心（长按起点，待实测）
SWIPE_SELECT_RIGHT = 964           # 右滑终点 x（宫格最右）
SWIPE_SELECT_BOTTOM = 700          # 下滑终点 y（贴近屏幕底，触发列表滚动）
SWIPE_SCROLL_TIMES = 3             # 停在底部触发滚动后，再补滑的次数
TAP_SELL_JD = (1153, 671)          # 「决定」按钮（多选后点它，判断是否选中）
MAX_SELL_ROUND = 300               # 贩卖最大循环轮数


# ==================== 模块级原语 ====================

def _norm_img(img):
    """把 Maa screencap 返回的 ndarray/RGBA/Image 统一为 BGR uint8 ndarray"""
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
    return arr


def _read_tpl(path):
    """读模板图（支持中文路径），返回 BGR ndarray"""
    if not os.path.isfile(path):
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def _ocr_number(context, roi, image):
    """OCR 识别 ROI 内数字，返回 int 或 None。"""
    x, y, w, h = roi
    x, y = max(0, int(x)), max(0, int(y))
    w = min(int(w), image.shape[1] - x)
    h = min(int(h), image.shape[0] - y)
    if w <= 0 or h <= 0:
        return None
    try:
        detail = context.run_recognition_direct("OCR", JOCR(roi=(x, y, w, h)), image)
    except Exception as e:
        mfaalog.warning(f"[整理礼物盒] OCR 调用异常: {e}")
        return None
    if detail is None:
        return None
    for r in detail.all_results:
        text = (getattr(r, "text", "") or "").strip()
        m = re.search(r"\d[\d,]*", text)
        if m:
            digits = m.group().replace(",", "")
            if digits.isdigit():
                return int(digits)
    return None


# ==================== 核心 Action ====================

@AgentServer.custom_action("execute_boxtask")
class ExecuteBoxTask(CustomAction):
    """整理礼物盒：一键领取 + 按数量保留 + 贩卖狗粮"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            self._context = context
            self._controller = context.tasker.controller

            node = context.get_node_data(argv.node_name) or {}
            attach = node.get("attach") or {}

            one_key3 = bool(attach.get("one_key3", True))
            one_key4 = bool(attach.get("one_key4", True))
            one_key5 = bool(attach.get("one_key5", True))
            keep3 = bool(attach.get("keep3", False))
            keep4 = bool(attach.get("keep4", False))
            keep5 = bool(attach.get("keep5", False))
            keep3_num = int(attach.get("keep3_num", 5))
            keep4_num = int(attach.get("keep4_num", 5))
            keep5_num = int(attach.get("keep5_num", 5))
            sell3 = bool(attach.get("sell3", False))
            sell4 = bool(attach.get("sell4", False))
            sell5 = bool(attach.get("sell5", False))
            aqf = bool(attach.get("aqf", True))

            if not (one_key3 or one_key4 or one_key5) and not (keep3 or keep4 or keep5) \
                    and not (sell3 or sell4 or sell5):
                mfaalog.error("[整理礼物盒] 未选择任何操作（一键领取/按数量保留/贩卖均为关）")
                return CustomAction.RunResult(success=False)

            # 资源包区分服务器 -> 模板目录
            cfg = context.get_node_data("资源包配置") or {}
            pkg = str((cfg.get("attach") or {}).get("resource_package") or "base").strip()
            layer = "cn" if pkg == "cn" else "base"
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

            def _resolve(l):
                for c in [
                    os.path.join(root_dir, "assets", "resource", l, "image", "整理礼物盒"),
                    os.path.join(root_dir, "resource", l, "image", "整理礼物盒"),
                ]:
                    if os.path.isdir(c):
                        return c
                return None

            self.tpl_dir = _resolve(layer) or ""
            # base 作为兜底：cn 缺图时回退到 base（与 pipeline 资源分层行为一致）
            self.tpl_base_dir = _resolve("base") or self.tpl_dir
            mfaalog.info(f"[整理礼物盒] 资源包={pkg} 模板目录={self.tpl_dir}")

            # 坐标自适应：截图拿实际分辨率算缩放系数
            self._init_scale()

            # 推断是否整理、是否贩卖
            do_tidy = one_key3 or one_key4 or one_key5 or keep3 or keep4 or keep5
            do_sell = sell3 or sell4 or sell5

            # 整理（一键领取 + 按数量保留）：需先导航到礼物盒
            if do_tidy:
                mfaalog.info("[整理礼物盒] 导航到礼物盒...")
                try:
                    context.run_task("整理礼物盒-导航到礼物盒")
                except Exception as e:
                    mfaalog.warning(f"[整理礼物盒] 导航 pipeline 异常: {e}")
                if context.tasker.stopping:
                    return CustomAction.RunResult(success=False)
                if not self._in_giftbox():
                    mfaalog.error("[整理礼物盒] 导航失败，未到达礼物盒界面")
                    return CustomAction.RunResult(success=False)

                # 一键领取：筛选勾选的星级 -> pipeline 循环领空
                if one_key3 or one_key4 or one_key5:
                    mfaalog.info("[整理礼物盒] ===== 一键领取 =====")
                    ok_flags = [one_key3, one_key4, one_key5]
                    self._filter_giftbox([1 if f else 0 for f in ok_flags])
                    if self._in_giftbox():
                        context.run_task("整理礼物盒-一键领取循环")

                # 按数量保留：筛选目标星级 -> 数量 < 阈值才领取
                if keep3 or keep4 or keep5:
                    mfaalog.info("[整理礼物盒] ===== 按数量保留 =====")
                    keep_flags = [keep3, keep4, keep5]
                    nums = [keep3_num, keep4_num, keep5_num]
                    self._filter_giftbox([1 if f else 0 for f in keep_flags])
                    self._filter_get(keep_flags, nums)

            # 贩卖狗粮：直接去灵基变还（不依赖礼物盒）
            if do_sell:
                mfaalog.info("[整理礼物盒] ===== 贩卖狗粮 =====")
                self._sell_dogfood(sell3, sell4, sell5, aqf)

            mfaalog.info("[整理礼物盒] 完成")
            return CustomAction.RunResult(success=True)

        except Exception as e:
            mfaalog.error(f"[整理礼物盒] 异常: {e}")
            return CustomAction.RunResult(success=False)

    # ---------- 坐标自适应 ----------

    def _init_scale(self):
        """截图获取实际分辨率，计算缩放系数（基准 1280x720）。"""
        self.sx = self.sy = 1.0
        img = _norm_img(self._controller.post_screencap().wait().get())
        if img is None:
            mfaalog.warning("[整理礼物盒] 截图失败，缩放系数保持 1.0")
            return
        h, w = img.shape[:2]
        self.sx = w / float(BASE_W)
        self.sy = h / float(BASE_H)
        mfaalog.info(f"[整理礼物盒] 实际分辨率 {w}x{h}，缩放 {self.sx:.3f}x{self.sy:.3f}")

    def _px(self, x):
        return int(round(x * self.sx))

    def _py(self, y):
        return int(round(y * self.sy))

    # ---------- 基础封装 ----------

    def _shot(self):
        img = _norm_img(self._controller.post_screencap().wait().get())
        if img is None:
            mfaalog.warning("[整理礼物盒] 截图失败")
        return img

    def _tap(self, x, y, delay=0.3):
        self._controller.post_click(self._px(x), self._py(y)).wait()
        time.sleep(delay)

    def _tpl(self, name):
        """模板图路径：优先当前服，缺图时回退 base（与 pipeline 资源分层一致）"""
        p = os.path.join(self.tpl_dir, name)
        if os.path.isfile(p):
            return p
        pb = os.path.join(self.tpl_base_dir, name)
        if pb != p and os.path.isfile(pb):
            return pb
        return p

    def _match(self, name, roi=None, threshold=TH_TEMPLATE):
        """模板匹配（模板图 + ROI 均按 scale 缩放）。返回 (score, cx, cy) 或 None"""
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
        _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(res)
        if max_v < threshold:
            return None
        cx = ox + max_l[0] + tpl.shape[1] // 2
        cy = oy + max_l[1] + tpl.shape[0] // 2
        return float(max_v), int(cx), int(cy)

    def _match_many(self, name, roi=None, threshold=TH_TEMPLATE, max_count=50):
        """findAll：找 ROI 内所有匹配位置，返回 [(cx, cy, score)] 从高到低"""
        tpl = _read_tpl(self._tpl(name))
        if tpl is None:
            return []
        img = self._shot()
        if img is None:
            return []
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
                return []
            region = img[y:y + h, x:x + w]
            ox, oy = x, y
        if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
            return []
        res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
        th, tw = tpl.shape[:2]
        out = []
        while len(out) < max_count:
            _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(res)
            if max_v < threshold:
                break
            out.append((ox + max_l[0] + tw // 2, oy + max_l[1] + th // 2, float(max_v)))
            cv2.rectangle(res, (max(0, max_l[0] - tw // 2), max(0, max_l[1] - th // 2)),
                          (min(res.shape[1], max_l[0] + tw // 2), min(res.shape[0], max_l[1] + th // 2)),
                          (0, 0, 0), -1)
        return out

    def _pick_color(self, roi, rgb_list):
        """取 ROI 平均色，返回与 rgb_list 中最接近的下标"""
        img = self._shot()
        if img is None:
            return 0
        x, y, w, h = roi
        x, y = self._px(x), self._py(y)
        w, h = self._px(w), self._py(h)
        x, y = max(0, x), max(0, y)
        w = min(w, img.shape[1] - x)
        h = min(h, img.shape[0] - y)
        if w <= 0 or h <= 0:
            return 0
        region = img[y:y + h, x:x + w]
        b, g, r = (float(np.average(region[:, :, i])) for i in range(3))
        dists = [(b - rb) ** 2 + (g - rg) ** 2 + (r - rr) ** 2 for (rr, rg, rb) in rgb_list]
        return int(np.argmin(dists))

    def _in_giftbox(self):
        """判断当前是否在礼物盒界面"""
        return self._match(TPL_IN_BOX, roi=(0, 0, 1280, 200)) is not None

    # ---------- 筛选 ----------

    def _filter_giftbox(self, star_lst):
        """设置礼物盒筛选：star_lst=[3星,4星,5星] 是否选中（1/0）"""
        mfaalog.info(f"[整理礼物盒] 筛选星级 {star_lst}")
        # pipeline 做固定动作：点筛选按钮、等决定、重置 3 次、点从者经验值、下滑
        try:
            self._context.run_task("整理礼物盒-筛选准备")
        except Exception as e:
            mfaalog.warning(f"[整理礼物盒] 筛选准备 pipeline 异常: {e}")
        time.sleep(0.5)
        # 稀有度 3/4/5 星：取色判断选中态，点未选中的（XOR 逻辑需 cv2，保留在脚本）
        # _pick_color 返回下标：0=蓝(未选中)、1=灰(选中)
        for _ in range(3):
            states = [self._pick_color(STAR_ROI[i], [RGB_UNSELECTED, RGB_SELECTED]) == 1
                      for i in range(3)]
            mfaalog.info(f"[整理礼物盒] 星级选中态 {states} 期望 {[bool(s) for s in star_lst]}")
            if all((states[i] == bool(star_lst[i])) for i in range(3)):
                break
            for i in range(3):
                if states[i] != bool(star_lst[i]):
                    self._tap(*STAR_TAP[i], delay=0.5)
            time.sleep(0.5)
        # 点决定
        m = self._match(TPL_DECIDE)
        if m is not None:
            self._controller.post_click(m[1], m[2]).wait()
            time.sleep(1.0)

    # ---------- 按数量保留 ----------

    def _filter_get(self, keep_flags, keep_nums):
        """逐行读狗粮数量，数量 < 保留阈值则勾选领取，滚动遍历"""
        active = [i for i in range(3) if keep_flags[i]]
        if not active:
            return
        no_pick_rounds = 0
        for _round in range(MAX_FILTER_GET_ROUND):
            if self._context.tasker.stopping:
                return
            img = self._shot()
            if img is None:
                continue
            if self._match(TPL_GIFT_FULL) is not None:
                mfaalog.info("[整理礼物盒] 礼物盒已满，停止")
                return
            picked = 0
            for i in active:
                pts = self._match_many(TPL_DF[i], ROI_DF_COL)
                for (cx, cy, _sc) in pts:
                    roi = (cx + self._px(NUM_ROI_DX), cy + self._py(NUM_ROI_DY),
                           self._px(NUM_ROI_W), self._py(NUM_ROI_H))
                    num = _ocr_number(self._context, roi, img)
                    if num is None:
                        mfaalog.warning(f"[整理礼物盒] {i + 3}星狗粮({cx},{cy}) 数字识别失败")
                        continue
                    if num >= keep_nums[i]:
                        continue
                    if self._is_chosen(img, cx, cy):
                        continue
                    # 点击勾选（cx/cy 是实际坐标，勾选框偏移再 ×scale）
                    self._controller.post_click(cx + self._px(CHOSEN_TAP_DX),
                                                cy + self._py(CHOSEN_TAP_DY)).wait()
                    time.sleep(0.1)
                    picked += 1
            if picked > 0:
                no_pick_rounds = 0
                self._tap(*TAP_GET_CHOSEN, delay=1.0)
                for _ in range(3):
                    if self._context.tasker.stopping:
                        return
                    m = self._match(TPL_RECEIVE)
                    if m is not None:
                        self._controller.post_click(m[1], m[2]).wait()
                        time.sleep(1.0)
                        break
                    time.sleep(0.5)
                continue
            no_pick_rounds += 1
            if no_pick_rounds >= 5:
                mfaalog.info("[整理礼物盒] 连续多轮无待领取狗粮，结束")
                return
            # 滚动（手指上滑，列表向下翻）
            self._controller.post_swipe(self._px(600), self._py(560),
                                        self._px(600), self._py(200), 400).wait()
            time.sleep(1.0)
        mfaalog.warning("[整理礼物盒] 按数量保留达到最大轮数")

    def _is_chosen(self, img, cx, cy):
        """判断狗粮勾选框是否已选中：比对 chosen/unchosen 模板的归一化距离"""
        bx = cx + self._px(CHOSEN_DX)
        by = cy + self._py(CHOSEN_DY)
        w = self._px(CHOSEN_W)
        h = self._py(CHOSEN_H)
        if bx < 0 or by < 0 or bx + w > img.shape[1] or by + h > img.shape[0]:
            return False
        region = img[by:by + h, bx:bx + w]
        chosen = _read_tpl(self._tpl(TPL_CHOSEN))
        unchosen = _read_tpl(self._tpl(TPL_UNCHOSEN))
        if chosen is None or unchosen is None:
            return False
        chosen = cv2.resize(chosen, (region.shape[1], region.shape[0]))
        unchosen = cv2.resize(unchosen, (region.shape[1], region.shape[0]))

        def _norm(a, b):
            return float(np.linalg.norm(a.astype(np.int16) - b.astype(np.int16)))

        d_chosen = _norm(chosen, region)
        d_unchosen = _norm(unchosen, region)
        return d_chosen < d_unchosen

    # ---------- 贩卖狗粮 ----------

    def _sell_dogfood(self, sell3, sell4, sell5, aqf):
        """贩卖狗粮：导航到灵基变还 → 筛选狗粮+星级 → 循环变还"""
        mfaalog.info(f"[整理礼物盒] ===== 贩卖狗粮 3/4/5星={sell3}/{sell4}/{sell5} QP自动={aqf} =====")
        # 导航到灵基变还（pipeline）
        try:
            self._context.run_task("整理礼物盒-导航到灵基变还")
        except Exception as e:
            mfaalog.warning(f"[整理礼物盒] 导航到灵基变还 pipeline 异常: {e}")
        if self._context.tasker.stopping:
            return
        if not self._in_ljbh():
            mfaalog.error("[整理礼物盒] 导航失败，未到达灵基变还界面")
            return
        # 筛选（pipeline：点筛选按钮→点顶部→选星级(option override 控制)→下滑找经验值→决定）
        try:
            self._context.run_task("整理礼物盒-筛卖准备")
        except Exception as e:
            mfaalog.warning(f"[整理礼物盒] 筛卖准备 pipeline 异常: {e}")
        if self._context.tasker.stopping:
            return
        # 贩卖循环
        self._sell_loop(aqf, [sell3, sell4, sell5])

    def _in_ljbh(self):
        """判断当前是否在灵基变还界面"""
        return self._match(TPL_LJBH, roi=(0, 0, 1280, 200)) is not None

    def _sell_loop(self, aqf, target_stars):
        """判断有无目标狗粮 → 滑动全选 → 点决定 → pipeline(贩卖→QP满轮巡→关闭) → 循环"""
        for _round in range(MAX_SELL_ROUND):
            if self._context.tasker.stopping:
                return
            # 1. 判断画面里有没有目标星级的狗粮
            if not self._has_target_dogfood(target_stars):
                mfaalog.info("[整理礼物盒] 画面无目标狗粮，贩卖结束")
                return
            # 2. 长按第一张 + 右滑 + 下滑到底（滑动轨迹框选整屏宫格）
            self._swipe_select_all()
            # 3. 点「决定」
            self._tap(*TAP_SELL_JD, delay=1.0)
            # 4. pipeline：点贩卖 → QP满轮巡处理 → 关闭奖励弹窗
            try:
                self._context.run_task("整理礼物盒-贩点贩卖")
            except Exception as e:
                mfaalog.warning(f"[整理礼物盒] 贩点贩卖 pipeline 异常: {e}")
            # 5. aqf=false 时 pipeline 对 QP 满 DoNothing，这里检查并停止
            if not aqf and self._match(TPL_QP_FULL) is not None:
                mfaalog.info("[整理礼物盒] QP 已满，按设置停止贩卖")
                return
        mfaalog.warning("[整理礼物盒] 贩卖达到最大轮数")

    def _has_target_dogfood(self, target_stars):
        """判断画面里有没有目标星级的狗粮（用狗粮图标匹配）"""
        for i, star in enumerate(target_stars):
            if star and self._match(TPL_DF[i]) is not None:
                return True
        return False

    def _swipe_select_all(self):
        """长按第一张卡片 → 右滑到最右 → 下滑到底，滑动轨迹框选整屏宫格"""
        x1, y1 = self._px(TAP_CARD1[0]), self._py(TAP_CARD1[1])
        x2 = self._px(SWIPE_SELECT_RIGHT)
        y3 = self._py(SWIPE_SELECT_BOTTOM)
        self._controller.post_touch_down(x1, y1).wait()
        time.sleep(0.6)  # 长按触发多选模式
        self._controller.post_touch_move(x2, y1).wait()  # 右滑过第一行
        time.sleep(0.2)
        self._controller.post_touch_move(x2, y3).wait()  # 下滑到屏幕底部
        time.sleep(0.8)  # 停在底部，触发列表自动滚动
        self._controller.post_touch_up().wait()
        time.sleep(0.5)
