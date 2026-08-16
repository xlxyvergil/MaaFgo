# -*- coding: utf-8 -*-
"""
坐标反推定向导航 Action
地月后事

【雷夫衔枝之年】

天上永恒的王座到来，人类之理为之焕然一新。

然后真王，原初的那一位——咕哒——开始和旧世界的统治者们，七位冠位之座的大王开战。

那恐怖的大王们是法则的具象，是超越了英灵顶点的存在。

原初的那一位造出了自己发着光的影子。

而影子的数量是七。它们被称为Beast。


【咕哒，或者原初的那一位】

原初的那一位，或许是最后的御主。

它手持令咒，身负虚数之锚，从迦勒底亚斯的结晶中诞生，难以分辨其承载的是拯救还是毁灭。

但是世界如果要被创造，虚数之树必须被贯穿。

咕哒——原初的那一位——却用“空想树”的根系隔绝了「剪定事象」和「泛人类史」。


【衔枝后四十余年】

四十个特异点埋葬了火，四十个异闻带沸腾了海。

七位冠位大王全部被打败，七个试炼全部对御主俯首称臣。

原初的那一位开始了“人理”的再定义。

为了“我们”——它最可怜的人儿将出现在这片大地。

【箱舟开门之年】

原初的那一位对人类有一套神圣的规划——“人理定础”。

人只要幸福，它便欢欣。

【箱舟开门的次年】

人们耕耘，第一次收获羁绊。

人们开掘，第一次收获圣晶石。

人们聚集，第一次写就终局特异点的诗篇。

【狂欢节之年】

如果有饥馑，天上就落下魔力与英灵的加护。

如果有贫瘠，那大地就会生出虚数宝藏。

如果有忧郁蔓延，那么迦勒底亚斯就会以声音回应。

唯一的禁止之事，就是输给诱惑。但是冠位时间的神殿已然倒塌。

【葬火之年】

天上的第二个王座到来——那是“天理”的降临，仿佛创世之初的大战再开。

那一天，虚数之树倾颓，英灵之座崩裂。

我们剪定事象之民的先祖，和他们世代栖居的土地，落入了此处。

黑暗的年代由此开始。

【黑暗的元年】

七位大王的子民被虚数之海接纳，深海的隐匿者曾经统治这里。

我们的先祖与它们发生了征战。

先祖使用千灯将它们逐入影子，它们则在影子里狩猎人类。

此处唯有剪定，所以无处不是它们的猎场。

人们的祈祷汇成哀歌，原初的那一位和其他七位发光的影子并不能听见。

【太阳的比喻】

黑暗的洞窟里，有一群未曾见过光的人们在生活。

有一位见过太阳的贤人，对着洞窟的众人描绘着光之下的生活与太阳的伟大。

他见众人无法理解，于是点起了火。

人们于是开始崇拜火，以为这个是太阳，甚至开始习惯了黑暗与火光的生活。

贤人死后，有人霸占了火，通过火，投下了自己巨大的影子。

进图即识别(不做缩放/归位): 截图 -> YOLO检测(imgsz=256) -> 以YOLO框中心为锚、按地图内最大模板
尺寸+冗余画ROI -> 多尺度 matchTemplate 独占匹配(阈值0.9) -> 关卡列表(名称+屏幕中心)
用识别关卡 + 归一化 coords.json(已统一到 2.0 基准) 反推当前视角平移 t:
    t = median(屏幕中心 - SCALE * coords[name]),  SCALE = 2.0
目标屏幕预测位置 P = SCALE * coords[target] + t:
    识别到目标 -> 点击进入
    名称条完整在屏 + 中心在可视区(距边界>=40px) -> 判定到位, 停止滑动:
        小窗口YOLO补检(220x60) + 局部模板定位确认(阈值0.9) -> 点击进入
        确认失败(附近有YOLO框但模板未命中 / 无框) -> 原地重试, 超 RECOG_WAIT_LIMIT -> 失败
    P 在屏外 -> 按 (屏幕中心 - P) 方向定向滑动(地图跟手), 滑动后重新识别循环

失败终止(无兜底): 连续空屏 / 滑动无进展 / 超轮 / 目标无坐标 -> 返回失败

识别方法说明:
- YOLO imgsz 用 256(640 下舞会会场类受周围元素干扰的名称条漏检, 256 整图直接检出;
  7 张截图全量覆盖率 640=89% -> 256=100%)
- 主识别: 中心锚点 ROI + 多尺度 matchTemplate(TM_CCOEFF_NORMED, 阈值 0.9), 替代 SIFT/greedy
  (局部框场景实测 MT+中心ROI 全量 19/19 且分数 0.95+, SIFT 已弃用)
- 目标无坐标(素材名与 coords 键不一致) -> 直接失败, 不再委托兜底

滑动约定: 地图跟手, 手指沿"目标预测位置 -> 屏幕中心"方向滑动固定 SWIPE_DIST(100px):
    起点 = 屏幕中心朝目标方向 100px 处, 终点 = 屏幕中心, 每轮固定 100px 逼近
    若实测滑动方向相反(目标越滑越远), 调整 SWIPE_SIGN = -1 取反。

流程:
1. 读 nav_test.json 目标关卡 + template 推导素材目录 + 归一化 coords
2. 主循环(最多 MAX_ROUNDS 轮):
   a. 截图 -> QuestDetector.detect -> 配对有效名称条框
   b. 中心ROI matchTemplate 独占匹配(阈值0.9) -> 识别关卡列表(名称+屏幕中心+分数)
   c. 目标在屏 -> 点击进入 -> 成功
   d. 识别列表非空 -> 反推 t(中位数, 只统计 coords 中存在的关卡)
   e. 计算目标预测 P: 名称条完整在屏且在可视区 -> 到位(小窗口补检+局部模板确认点击/原地重试);
      否则定向滑动逼近
   f. 空屏 -> 沿上轮方向盲滑; 连续空屏 -> 失败
   g. 滑动无进展(目标距中心不再缩小) -> 失败
3. 超轮数/异常 -> 失败
"""
import os
import json
import time
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
import mfaalog

# 方案B常量
SCALE = 2.0              # 恒定缩放比: 全部地图进图缩放统一 2.0(coords 已归一化)
SCREEN_W, SCREEN_H = 1280, 720
SCREEN_CENTER = np.array([SCREEN_W / 2.0, SCREEN_H / 2.0])
SWIPE_SIGN = 1.0         # 滑动方向符号: 地图跟手=+1; 若实测方向相反改为 -1
SWIPE_DIST = 100         # 固定滑动距离(px): 起点=中心朝目标方向100px处, 向中心滑动
SWIPE_DURATION = 300     # 滑动持续时间(ms)
NO_PROGRESS_LIMIT = 3    # 目标距中心连续不缩小次数 -> 判定滑动无进展 -> 失败
EMPTY_SCREEN_LIMIT = 8   # 连续空屏(未识别到任何关卡) -> 失败(雾区画面逐帧变化, 提高轮数等轻雾帧)
ROUND_INTERVAL = 1.5     # 轮间间隔秒(识别失败等场景每轮之间等待, 雾气飘动后下一帧画面不同)
MAX_ROUNDS = 40          # 最大识别-滑动轮数

# matchTemplate 识别参数(中心锚点 ROI, 替代原 SIFT/greedy)
MT_SCALES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 1.8, 2.0)
MT_RECOG_TH = 0.8        # 主识别阈值(中心ROI matchTemplate top1 达标才算识别; 雾区画面干扰, 0.9 会误判空屏)
ROI_PAD = 10             # 中心锚点ROI 四周冗余px(基准=地图内最大模板尺寸)
# 孤框小窗口重检参数(YOLO 检测管线内, 全部推理统一 imgsz=256)
WIN_W = 220              # 小窗口宽度(px)
WIN_H = 60               # 小窗口高度(px)
RECOG_WAIT_LIMIT = 5     # 预测到位后停止滑动, 原地重试识别上限轮数

# 可视区域多边形: 内部基本无 UI 遮挡,
# 目标预测屏幕位置落入此区域且距边界足够远才认为"可点击/稳定识别"
VISIBLE_POLY = np.array([
    [0, 171], [0, 493], [112, 493], [193, 565], [599, 565], [600, 719],
    [1030, 719], [1031, 608], [1279, 608], [1279, 85], [736, 85],
    [735, 0], [179, 0], [178, 84], [88, 84], [87, 171],
], dtype=np.int32)

# 局部模板定位(YOLO漏检兜底)参数
MT_MIN_SCORE = 0.8       # 局部 matchTemplate 命中阈值
CLICK_X_OFFSET = 5       # 点击点相对识别中心左偏px, 防名称条中心点击误触相邻关卡(如格洛斯特)
LOCAL_RADIUS = 250       # 局部匹配窗口半径(px, 以预测位置为中心)
NEAR_RADIUS = 300        # 预测位置附近 YOLO 框(tag/nametag)判定半径(px)

# 小窗口 YOLO 补检(整图漏检兜底)参数
# 整图推理时名称条受周围元素干扰漏检; 在预测位置开约名称条大小的窗口裁剪推理, 干扰被去除后模型可正常识别
WIN_CONF = 0.2           # 小窗口推理置信阈值
WIN_MIN_MT = 0.8         # 小窗口检出框的目标模板确认阈值(与主识别一致)


def _in_visible_area(px, py, margin=40):
    """目标屏幕位置是否在可视区域内且距边界至少 margin px
    (中心需远离可视区边界 >=40px 才能点击/稳定识别, 靠边时继续滑动让目标居中)"""
    import cv2
    return cv2.pointPolygonTest(VISIBLE_POLY, (float(px), float(py)), True) >= margin


def _icon_in_visible(px, py, tw, th, margin=20):
    """名称条图标(模板尺寸 tw x th, 中心 px,py)是否整体进入可视区域且距边界至少 margin px
    中心点进入可视区不代表整条不被UI遮挡: 名称条较宽, 右下角/左下斜线/顶部角落的 UI 会盖住部分。
    四角都必须远离 UI 边界才能点击, 否则继续滑动把图标拉进安全区
    (右下偏低->上拉, 左下斜线附近->右拉, 左低->上拉, 顶部角落->下拉, 由滑动朝向中心自然完成)"""
    import cv2
    hw, hh = tw / 2, th / 2
    pts = [(px - hw, py - hh), (px + hw, py - hh), (px - hw, py + hh), (px + hw, py + hh)]
    return all(cv2.pointPolygonTest(VISIBLE_POLY, (float(pxx), float(pyy)), True) >= margin
               for pxx, pyy in pts)


def locate_quest_near(img, tpl_bgr, px, py, radius=LOCAL_RADIUS, min_score=MT_MIN_SCORE):
    """在 (px,py) 附近局部窗口内用目标素材多尺度 matchTemplate 定位名称条
    (处理 YOLO 漏检: 预测到位但识别列表无目标)
    返回 (bx, by, bw, bh, score) 名称条左上角+尺寸+分数; 未命中返回 None"""
    import cv2
    H, W = img.shape[:2]
    x0 = max(0, int(px - radius))
    x1 = min(W, int(px + radius))
    y0 = max(0, int(py - radius))
    y1 = min(H, int(py + radius))
    win = img[y0:y1, x0:x1]
    if win.size == 0:
        return None
    gray = cv2.cvtColor(win, cv2.COLOR_BGR2GRAY)
    gtpl = cv2.cvtColor(tpl_bgr, cv2.COLOR_BGR2GRAY)
    tw0, th0 = gtpl.shape[1], gtpl.shape[0]
    best = (0.0, None, 1.0)
    for s in MT_SCALES:
        tw, th = max(12, int(tw0 * s)), max(6, int(th0 * s))
        if tw > win.shape[1] or th > win.shape[0]:
            continue
        rtpl = cv2.resize(gtpl, (tw, th))
        r = cv2.matchTemplate(gray, rtpl, cv2.TM_CCOEFF_NORMED)
        _mv, mvv, _ml, mvl = cv2.minMaxLoc(r)
        if mvv > best[0]:
            best = (float(mvv), (mvl[0], mvl[1]), s)
    sc, bpos, bs = best
    if bpos is None or sc < min_score:
        return None
    bxw, byw = bpos
    bx, by = x0 + bxw, y0 + byw
    tw, th = max(12, int(tw0 * bs)), max(6, int(th0 * bs))
    return bx, by, tw, th, sc


# ---- 内联自 fallback_navigation(该模块已删除): 素材/坐标加载 + YOLO 检测 + 防误触 ----
def resolve_quest_dir(root_dir, resource_package, template_path):
    """从运行时 template(map/{英文}/{关卡}.png) 推导导航素材目录
    素材约定: 日服放 base, 国服放 cn → resource/{base|cn}/image/map/{英文}/"""
    pkg = "cn" if resource_package == "cn" else "base"
    folder = os.path.dirname(template_path).replace("\\", "/").strip("/")
    return os.path.join(root_dir, "resource", pkg, "image", *folder.split("/"))


def load_quest_templates(quest_dir):
    """加载素材目录下所有名称条截图 -> {关卡名: BGR图}"""
    import cv2
    templates = {}
    if not os.path.isdir(quest_dir):
        return templates
    for f in sorted(os.listdir(quest_dir)):
        if f.endswith(".png") and f != "special.json":
            img = cv2.imdecode(np.fromfile(os.path.join(quest_dir, f), dtype=np.uint8),
                               cv2.IMREAD_COLOR)
            if img is not None:
                templates[f[:-4]] = img
    return templates


def load_quest_coords(root_dir, folder):
    """读取 coords.json(全景坐标表, 固定只读 agent/utils/{folder}/coords.json)

    返回 {关卡名: (x, y)}: quests 坐标已归一化到 2.0 缩放基准, 导航用恒定 SCALE=2.0
    换算屏幕坐标, 不依赖地图尺寸。
    coords.json 顶层 map_width/map_height 为生成坐标表时的全景图原始尺寸, 纯元数据:
    仅离线工具(normalize_coords/sync_coords/simulate_blank_view)读写, 不参与运行时
    缩放或边界计算(边界保护由"滑动无进展/连续空屏"判定覆盖)"""
    path = os.path.join(root_dir, "agent", "utils", folder, "coords.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        return {q: (int(v["x"]), int(v["y"]))
                for q, v in data.get("quests", {}).items() if "x" in v and "y" in v}
    except Exception as e:
        mfaalog.warning(f"[导航] coords.json 读取失败: {e}")
    return {}


def prevent_touch(context):
    """手势操作(缩放/滑动)后防止误触进入关卡: 触发 pipeline 节点"防止导航误触" """
    touched = False
    try:
        detail = context.run_task("防止导航误触")
        if detail is not None:
            for node in detail.nodes:
                if node.name == "关闭误触" and node.action is not None and node.action.success:
                    touched = True
                    break
    except Exception as e:
        mfaalog.warning(f"[防误触] 检查失败: {e}")
    if touched:
        mfaalog.info("[防误触] 检测到误触, 已点击关闭返回")
    else:
        mfaalog.info("[防误触] 未检测到误触")
    time.sleep(1.5)
    return touched


class QuestDetector:
    """YOLO 关卡检测
    imgsz=256: 实测整图 640 下舞会会场类受周围元素干扰的名称条漏检(conf 0.12),
    256 下整图直接检出(conf 0.80); 7 张截图全量覆盖率 640=89% -> 256=100%
    管线: 高低阈合并 -> IoU 去重 -> nametag/tag 配对 -> 孤框小窗口(220x60)重检 -> 配对过滤
    """
    IMGSZ = 256
    CONF = 0.5        # 主检测阈值
    LOW_CONF = 0.2    # 低置信兜底阈值

    def __init__(self, model_path):
        from ultralytics import YOLO
        self.model = YOLO(model_path)

    @staticmethod
    def _norm_img(img):
        """兼容 Maa screencap 返回的 ndarray/RGBA/Image, 统一为 BGR uint8 ndarray"""
        import cv2
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

    def _infer(self, img, conf):
        r = self.model(img, conf=conf, imgsz=self.IMGSZ, verbose=False)[0]
        out = []
        for b in r.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
            out.append((x1, y1, x2, y2, float(b.conf[0]), int(b.cls[0])))
        return out

    @staticmethod
    def _iou(a, b):
        xx1, yy1 = max(a[0], b[0]), max(a[1], b[1])
        xx2, yy2 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0.0, xx2 - xx1) * max(0.0, yy2 - yy1)
        aa = (a[2] - a[0]) * (a[3] - a[1])
        bb = (b[2] - b[0]) * (b[3] - b[1])
        return inter / (aa + bb - inter + 1e-6)

    @staticmethod
    def _paired(nx, ny, nw, nh, tx, ty, tw, th):
        """nametag 与 tag 配对判定: tag 左边缘落在 nametag 右侧 40% 区域内, y 方向重叠"""
        return nx + nw * 0.4 <= tx <= nx + nw + 5 and ty < ny + nh and ty + th > ny

    def _recheck(self, img, box, direction, ww=WIN_W, wh=WIN_H):
        """孤框小窗口重检: right=在 nametag 右侧找 tag, left=在 tag 左侧找 nametag
        原 640x640 窗口: 名称条只占窗口一小块, resize 到 imgsz=256 后过小被模型漏检,
        导致孤框的配对对象找不回; 改约名称条大小的小窗口(220x60)裁剪推理,
        去除周围元素干扰后模型可正常识别"""
        x, y, w, h = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        H, W = img.shape[:2]
        cx = x + int(w * 0.4) + ww // 2 if direction == "right" else x - ww // 2
        cy = y + h // 2
        x0 = max(0, min(int(cx - ww / 2), W - ww))
        y0 = max(0, min(int(cy - wh / 2), H - wh))
        win = img[y0:y0 + wh, x0:x0 + ww]
        if win.size == 0:
            return []
        res = []
        for conf in (self.CONF, self.LOW_CONF):
            for (x1, y1, x2, y2, c, cl) in self._infer(win, conf):
                x1, y1, x2, y2 = x1 + x0, y1 + y0, x2 + x0, y2 + y0
                if direction == "right" and cl == 1:
                    if y1 < y + h and y2 > y and x1 >= x + w * 0.4 - 20 and x1 <= x + w + 60:
                        res.append((x1, y1, x2, y2, c, cl))
                elif direction == "left" and cl == 0:
                    if y1 < y + h and y2 > y and x2 >= x - 70 and x2 <= x + w + 40:
                        res.append((x1, y1, x2, y2, c, cl))
        return res

    def detect(self, img):
        """标准检测管线: 返回 (配对有效 nametag 框[(x,y,w,h,conf)], 未配对 tag 框[(x,y,w,h)])"""
        res = []
        for conf in (self.CONF, self.LOW_CONF):
            res += self._infer(img, conf)
        dedup = []
        for bb in sorted(res, key=lambda x: -x[4]):
            if any(self._iou(bb[:4], d[:4]) > 0.5 for d in dedup):
                continue
            dedup.append(bb)
        nametags = [(x1, y1, x2 - x1, y2 - y1, c)
                    for x1, y1, x2, y2, c, cl in dedup if cl == 0]
        tags = [(x1, y1, x2 - x1, y2 - y1, c)
                for x1, y1, x2, y2, c, cl in dedup if cl == 1]

        def paired_n(nx, ny, nw, nh):
            return any(self._paired(nx, ny, nw, nh, tx, ty, tw, th)
                       for tx, ty, tw, th, tc in tags)

        def paired_t(tx, ty, tw, th):
            return any(self._paired(nx, ny, nw, nh, tx, ty, tw, th)
                       for nx, ny, nw, nh, nc in nametags)

        un_n = [(x, y, w, h) for x, y, w, h, c in nametags if not paired_n(x, y, w, h)]
        un_t = [(x, y, w, h) for x, y, w, h, c in tags if not paired_t(x, y, w, h)]
        for box in un_n:
            for (x1, y1, x2, y2, c, cl) in self._recheck(img, box, "right"):
                if any(self._iou((x1, y1, x2, y2), (tx, ty, tx + tw, ty + th)) > 0.5
                       for tx, ty, tw, th, tc in tags):
                    continue
                tags.append((x1, y1, x2 - x1, y2 - y1, c))
        for box in un_t:
            for (x1, y1, x2, y2, c, cl) in self._recheck(img, box, "left"):
                if any(self._iou((x1, y1, x2, y2), (nx, ny, nx + nw, ny + nh)) > 0.3
                       for nx, ny, nw, nh, nc in nametags):
                    continue
                nametags.append((x1, y1, x2 - x1, y2 - y1, c))

        paired_tags = [(tx, ty, tw, th, c) for tx, ty, tw, th, c in tags
                       if any(self._paired(nx, ny, nw, nh, tx, ty, tw, th)
                              for nx, ny, nw, nh, _ in nametags)]
        valid = [(nx, ny, nw, nh, c) for nx, ny, nw, nh, c in nametags
                 if any(self._paired(nx, ny, nw, nh, tx, ty, tw, th)
                        for tx, ty, tw, th, _ in paired_tags)]
        unpaired_tags = [(tx, ty, tw, th) for (tx, ty, tw, th, _c) in tags
                         if not any(self._paired(nx, ny, nw, nh, tx, ty, tw, th)
                                    for (nx, ny, nw, nh, _) in valid)]
        return valid, unpaired_tags


# ---- matchTemplate 识别(中心锚点ROI, 替代原 SIFT/greedy_match_boxes) ----
def _center_roi(img, cx, cy, max_w, max_h, pad=ROI_PAD):
    """以 (cx,cy) 为中心, 按地图内最大模板尺寸+四周pad 画 ROI(BGR, clamp 到图内)
    解决 YOLO 框不完整(局部框只覆盖名称条一部分)导致匹配低分的问题:
    框中心基本可靠, ROI 保证完整包住名称条, 使多尺度模板匹配稳定 0.9+"""
    H, W = img.shape[:2]
    w, h = max_w + pad * 2, max_h + pad * 2
    x0 = max(0, min(int(cx - w / 2), W - w))
    y0 = max(0, min(int(cy - h / 2), H - h))
    return img[y0:y0 + h, x0:x0 + w]


def build_tpl_cache(templates):
    """预计算模板灰度图 + MT_SCALES 缩放金字塔(模板不变时可跨轮复用, 避免重复 cvtColor/resize)
    返回 {关卡名: (模板灰度, {scale: 缩放模板灰度})}"""
    import cv2
    cache = {}
    for name, tpl_bgr in templates.items():
        tg = cv2.cvtColor(tpl_bgr, cv2.COLOR_BGR2GRAY)
        pyr = {}
        for s in MT_SCALES:
            tw = max(12, int(tg.shape[1] * s))
            th = max(6, int(tg.shape[0] * s))
            pyr[s] = cv2.resize(tg, (tw, th))
        cache[name] = (tg, pyr)
    return cache


def _mt_best(gray, enlarged, tg, tpl_pyr):
    """多尺度 TM_CCOEFF_NORMED: A.模板缩放金字塔在ROI灰度上搜索 + B.ROI放大后原模板匹配, 取最大
    gray/enlarged/tg/tpl_pyr 均为预计算缓存, 复用避免重复 cvtColor/resize"""
    import cv2
    best = 0.0
    for rtpl in tpl_pyr.values():
        if rtpl.shape[1] > gray.shape[1] or rtpl.shape[0] > gray.shape[0]:
            continue
        r = cv2.matchTemplate(gray, rtpl, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(r.max()))
    for big in enlarged.values():
        if big.shape[1] < tg.shape[1] or big.shape[0] < tg.shape[0]:
            continue
        r = cv2.matchTemplate(big, tg, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(r.max()))
    return best


def mt_exclusive_match(roi_crops, templates, tpl_cache=None, th=MT_RECOG_TH):
    """中心ROI matchTemplate 独占匹配(替代 greedy_match_boxes/SIFT):
    每(框,模板)打分, 全局最高分优先双向 used, 分数>=th 才进入分配
    返回 [(box_idx, tpl_idx, score)] 按分数从高到低
    tpl_cache 由 build_tpl_cache 预计算, 跨轮复用(为空时内部构建)"""
    import cv2
    tpl_names = list(templates.keys())
    n, m = len(roi_crops), len(tpl_names)
    if tpl_cache is None:
        tpl_cache = build_tpl_cache(templates)
    # ROI 预计算: 灰度 + (1.2/1.5/2.0) 放大版本, 双重循环外只算一次
    roi_cache = []
    for i in range(n):
        if roi_crops[i] is None or roi_crops[i].size == 0:
            roi_cache.append(None)
            continue
        g = cv2.cvtColor(roi_crops[i], cv2.COLOR_BGR2GRAY)
        enlarged = {}
        for k in (1.2, 1.5, 2.0):
            w, h = int(g.shape[1] * k), int(g.shape[0] * k)
            enlarged[k] = cv2.resize(g, (w, h))
        roi_cache.append((g, enlarged))
    scores = np.zeros((n, m))
    for i in range(n):
        rc = roi_cache[i]
        if rc is None:
            continue
        g, enlarged = rc
        for j in range(m):
            tg, pyr = tpl_cache[tpl_names[j]]
            scores[i, j] = _mt_best(g, enlarged, tg, pyr)
    used_box, used_tpl = [False] * n, [False] * m
    assigned = []
    pairs = [(scores[i, j], i, j) for i in range(n) for j in range(m) if scores[i, j] >= th]
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    for s, i, j in pairs:
        if used_box[i] or used_tpl[j]:
            continue
        assigned.append((i, j, s))
        used_box[i], used_tpl[j] = True, True
    return assigned


@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    """方案B: 坐标反推定向导航(不缩放, 无兜底, 失败直接返回)"""

    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        mfaalog.info("=" * 50)
        mfaalog.info("[导航] 坐标反推定向导航启动（方案B: 不缩放）")
        try:
            AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ROOT_DIR = os.path.dirname(AGENT_DIR)

            # 步骤1: 参数(全部实时读 pipeline 节点——玩家选择经 options 覆盖
            # attach.quests / 关卡选择.template / 资源包配置.attach.resource_package, 读到的即运行时最终值)
            node = context.get_node_data(_argv.node_name) or {}
            target_quest = str((node.get("attach") or {}).get("quests") or "").strip()
            sel = context.get_node_data("关卡选择") or {}
            # MaaFramework 5.x 官方 dump: recognition.param.template 为数组, 取第 0 个
            tpl_val = ((sel.get("recognition") or {}).get("param") or {}).get("template")
            template = tpl_val[0] if isinstance(tpl_val, list) and tpl_val else ""
            cfg = context.get_node_data("资源包配置") or {}
            resource_package = str((cfg.get("attach") or {}).get("resource_package") or "").strip()
            mfaalog.info(f"[导航] 导航参数: quest={target_quest} template={template} pkg={resource_package}")

            # 步骤2: 素材目录 + 归一化坐标表
            quest_dir = resolve_quest_dir(ROOT_DIR, resource_package, template)
            templates = load_quest_templates(quest_dir)

            if target_quest not in templates:
                mfaalog.error(f"[导航] 素材库缺少目标关卡名称条截图: {quest_dir}/{target_quest}.png")
                return CustomAction.RunResult(success=False)
            mfaalog.info(f"[导航] 素材库 {len(templates)} 关")

            folder = os.path.dirname(template.replace("\\", "/")).strip("/")
            coords = load_quest_coords(ROOT_DIR, folder)

            # 目标在坐标表中缺失(素材名与 coords 键不一致) -> 直接失败, 无兜底
            if not coords or target_quest not in coords:
                mfaalog.error(f"[导航] 目标[{target_quest}]无坐标(coords 键缺失), 无法导航(无兜底)")
                return CustomAction.RunResult(success=False)

            # 中心锚点 ROI 基准: 地图内最大模板尺寸 + 四周冗余(每次加载后动态计算)
            max_w = max(t.shape[1] for t in templates.values())
            max_h = max(t.shape[0] for t in templates.values())
            # 模板灰度/缩放金字塔缓存: 主循环内 templates 不变, 跨轮复用(避免每轮重复 cvtColor/resize)
            tpl_cache = build_tpl_cache(templates)

            controller = context.tasker.controller
            t_xy = None          # 当前视角平移 (tx, ty)
            last_dir = None      # 最近一次滑动方向(单位向量), 空屏盲滑用
            empty_count = 0      # 连续空屏计数
            no_progress = 0      # 目标距中心连续不缩小计数
            last_dist = None     # 上一轮目标距屏幕中心距离
            wait_count = 0       # 预测到位后原地重试识别轮数

            # 步骤3: 主循环 - 识别 -> 反推 t -> 定向滑动
            for round_idx in range(MAX_ROUNDS):
                t_fresh = False   # 本轮是否成功从有效坐标锚点反推 t(防陈旧 t_xy 驱动预测)
                mfaalog.info(f"[导航] === 第{round_idx + 1}轮 ===")
                if round_idx > 0:
                    time.sleep(ROUND_INTERVAL)   # 轮间间隔: 雾气飘动后下一帧画面不同, 提高识别命中
                img = QuestDetector._norm_img(controller.post_screencap().wait().get())
                if img is None:
                    mfaalog.error("[导航] 截图失败")
                    return CustomAction.RunResult(success=False)

                nametags, tags = self._detector().detect(img)
                mfaalog.info(f"[导航] 检测框 {len(nametags)} nametag, {len(tags)} tag")

                # 识别关卡: 以YOLO框中心为锚, 按地图内最大模板尺寸+冗余画ROI, 多尺度
                # matchTemplate 独占匹配(阈值 MT_RECOG_TH=0.9) -> (名称, 屏幕中心)  [SIFT/greedy 已弃用]
                box_infos, roi_crops = [], []
                for (nx, ny, nw, nh, _c) in nametags:
                    nx, ny, nw, nh = int(nx), int(ny), int(nw), int(nh)
                    box_infos.append((nx, ny, nw, nh))
                    roi_crops.append(_center_roi(img, nx + nw // 2, ny + nh // 2, max_w, max_h))
                assigned = mt_exclusive_match(roi_crops, templates, tpl_cache)
                tpl_names = list(templates.keys())
                recognized = []   # [(name, 屏幕中心x, 屏幕中心y, score)]
                for (bi, bj, score) in assigned:
                    nx, ny, nw, nh = box_infos[bi]
                    name = tpl_names[bj]
                    # 点击点左偏 CLICK_X_OFFSET, 名称条中心可能被相邻关卡点击区覆盖
                    recognized.append((name, nx + nw // 2 - CLICK_X_OFFSET, ny + nh // 2, score))
                    mfaalog.info(f"[导航] 识别到: {name} ({score:.2f}) 点击=({nx + nw // 2 - CLICK_X_OFFSET},{ny + nh // 2})")

                # 目标在屏 -> 点击进入
                for (name, sx, sy, _score) in recognized:
                    if name == target_quest:
                        mfaalog.info(f"[导航] 目标[{target_quest}]在屏, 点击 ({sx},{sy})")
                        controller.post_click(sx, sy).wait()
                        return CustomAction.RunResult(success=True)

                if recognized:
                    # 反推 t: 识别关卡(在 coords 中) 的 屏幕中心 - SCALE*coords 取中位数
                    xs, ys = [], []
                    for (name, sx, sy, _sc) in recognized:
                        p = coords.get(name)
                        if p is None:
                            continue
                        xs.append(sx - SCALE * p[0])
                        ys.append(sy - SCALE * p[1])
                    if xs and ys:
                        t_xy = (float(np.median(xs)), float(np.median(ys)))
                        t_fresh = True
                        empty_count = 0
                        mfaalog.info(f"[导航] 反推视角平移 t=({t_xy[0]:.1f},{t_xy[1]:.1f}) 锚点{len(xs)}个")
                    else:
                        mfaalog.info("[导航] 识别关卡均不在坐标表, 无法反推 t")

                # 本轮未反推出新鲜 t(空屏/识别关卡无坐标锚点) -> 空屏计数, 超限失败; 否则盲滑
                if t_xy is None or not t_fresh:
                    empty_count += 1
                    mfaalog.info(f"[导航] 空屏(无坐标锚点) {empty_count}/{EMPTY_SCREEN_LIMIT}")
                    if empty_count >= EMPTY_SCREEN_LIMIT:
                        mfaalog.info("[导航] 连续空屏达阈值, 无法导航(无兜底)")
                        return CustomAction.RunResult(success=False)
                    if last_dir is not None:
                        start = (int(round(SCREEN_CENTER[0] - SWIPE_SIGN * last_dir[0] * SWIPE_DIST)),
                                 int(round(SCREEN_CENTER[1] - SWIPE_SIGN * last_dir[1] * SWIPE_DIST)))
                        end = (int(SCREEN_CENTER[0]), int(SCREEN_CENTER[1]))
                        controller.post_swipe(*start, *end, SWIPE_DURATION).wait()
                        mfaalog.info(f"[导航] 空屏盲滑沿上次方向 ({last_dir[0]:.2f},{last_dir[1]:.2f})")
                    time.sleep(1.5)
                    prevent_touch(context)   # 盲滑后检查误触进入关卡
                    continue

                # 目标预测位置
                pt = coords[target_quest]
                p_target = np.array([SCALE * pt[0] + t_xy[0], SCALE * pt[1] + t_xy[1]])
                dist_center = float(np.hypot(*(p_target - SCREEN_CENTER)))
                mfaalog.info(f"[导航] 目标预测屏幕位置 ({p_target[0]:.0f},{p_target[1]:.0f}), 距中心 {dist_center:.0f}px")

                # 到位判定: 名称条(模板尺寸)完整在屏幕内 + 中心在可视区 + 整体图标四角进入可视区(不被UI遮挡)
                th, tw = templates[target_quest].shape[:2]
                icon_ok = (_icon_in_visible(p_target[0], p_target[1], tw, th)
                           and 0 <= p_target[0] - tw / 2 and p_target[0] + tw / 2 <= SCREEN_W
                           and 0 <= p_target[1] - th / 2 and p_target[1] + th / 2 <= SCREEN_H)
                arrive = (_in_visible_area(p_target[0], p_target[1]) and icon_ok)
                mfaalog.info(f"[导航] 目标预测屏幕位置 ({p_target[0]:.0f},{p_target[1]:.0f}), "
                             f"距中心 {dist_center:.0f}px, 名称条({tw}x{th})整体可视={icon_ok} 可点={arrive}")

                # 目标预测到位(名称条完整可见): 停止滑动, 精确定位识别
                # 成功必须由"识别到目标"确认: 小窗口YOLO补检 / 附近YOLO框局部模板定位, 预测本身不算
                if arrive:
                    # 1) 小窗口 YOLO 补检: 整图推理漏检(周围元素干扰)时, 在预测位置开小窗口裁剪
                    #    推理找名称条, 检出框再用目标模板确认(半径加大到 LOCAL_RADIUS, 防宽模板放不下)
                    win_found = None
                    for (wx, wy, ww0, wh0) in self._detect_window_nametags(img, p_target):
                        loc = locate_quest_near(img, templates[target_quest],
                                                wx + ww0 / 2, wy + wh0 / 2,
                                                radius=LOCAL_RADIUS, min_score=WIN_MIN_MT)
                        if loc is not None:
                            win_found = (loc, wx, wy, ww0, wh0)
                            break
                    if win_found is not None:
                        (bx, by, bw, bh, lscore), _wx, _wy, _ww0, _wh0 = win_found
                        cx, cy = bx + bw // 2 - CLICK_X_OFFSET, by + bh // 2
                        complete = (0 <= bx and bx + bw <= SCREEN_W
                                    and 0 <= by and by + bh <= SCREEN_H)
                        clickable = _in_visible_area(cx, cy) and _icon_in_visible(cx, cy, bw, bh)
                        if complete and clickable:
                            mfaalog.info(f"[导航] 小窗口YOLO补检定位到位: ({cx},{cy}) score={lscore:.2f}")
                            controller.post_click(cx, cy).wait()
                            return CustomAction.RunResult(success=True)
                        # 小窗口定位命中但靠边不可点 -> 用真实位置继续滑动居中
                        p_target = np.array([float(cx), float(cy)])
                        dist_center = float(np.hypot(*(p_target - SCREEN_CENTER)))
                        mfaalog.info(f"[导航] 小窗口定位({cx},{cy}) score={lscore:.2f} "
                                     f"靠边不可点, 用真实位置继续滑动居中")
                    # 2) 预测位置附近有 YOLO 框(tag/nametag) -> 目标确实在该区域, 局部模板定位
                    nearby = []
                    for (bx0, by0, bw0, bh0, _c) in list(nametags) + list(tags):
                        if np.hypot(bx0 + bw0 / 2 - p_target[0],
                                    by0 + bh0 / 2 - p_target[1]) < NEAR_RADIUS:
                            nearby.append((int(bx0), int(by0), int(bw0), int(bh0)))
                    if nearby:
                        loc = locate_quest_near(img, templates[target_quest],
                                                float(p_target[0]), float(p_target[1]))
                        if loc is not None:
                            bx, by, bw, bh, lscore = loc
                            cx, cy = bx + bw // 2 - CLICK_X_OFFSET, by + bh // 2
                            complete = (0 <= bx and bx + bw <= SCREEN_W
                                        and 0 <= by and by + bh <= SCREEN_H)
                            clickable = _in_visible_area(cx, cy) and _icon_in_visible(cx, cy, bw, bh)
                            if complete and clickable:
                                mfaalog.info(f"[导航] 局部模板匹配定位到位: ({cx},{cy}) score={lscore:.2f}")
                                controller.post_click(cx, cy).wait()
                                return CustomAction.RunResult(success=True)
                            # 模板命中但靠边不可点(距可视区边<40px) -> 用真实位置继续滑动居中
                            p_target = np.array([float(cx), float(cy)])
                            dist_center = float(np.hypot(*(p_target - SCREEN_CENTER)))
                            mfaalog.info(f"[导航] 模板定位({cx},{cy}) score={lscore:.2f} "
                                         f"靠边不可点, 用真实位置继续滑动居中")
                        else:
                            wait_count += 1
                            mfaalog.info(f"[导航] 预测到位, 附近{NEAR_RADIUS}px有{len(nearby)}个YOLO框 "
                                         f"但局部模板未命中(<{MT_MIN_SCORE}), 原地重试 {wait_count}/{RECOG_WAIT_LIMIT}")
                            if wait_count >= RECOG_WAIT_LIMIT:
                                mfaalog.info(f"[导航] 预测已到位但 {RECOG_WAIT_LIMIT} 轮未识别到目标, 无法导航(无兜底)")
                                return CustomAction.RunResult(success=False)
                            time.sleep(1.5)   # 重试前等待, 给画面变化机会, 避免连续重复推理
                            continue
                    else:
                        wait_count += 1
                        mfaalog.info(f"[导航] 预测到位但附近{NEAR_RADIUS}px内无YOLO框, 原地重试 {wait_count}/{RECOG_WAIT_LIMIT}")
                        if wait_count >= RECOG_WAIT_LIMIT:
                            mfaalog.info(f"[导航] 预测已到位但 {RECOG_WAIT_LIMIT} 轮未识别到目标, 无法导航(无兜底)")
                            return CustomAction.RunResult(success=False)
                        time.sleep(1.5)   # 重试前等待, 给画面变化机会, 避免连续重复推理
                        continue

                # 滑动进展检测: 目标距中心不缩小 -> 判定无进展(边界/方向错) -> 失败
                if last_dist is not None and dist_center >= last_dist - 3.0:
                    no_progress += 1
                    mfaalog.info(f"[导航] 滑动无进展 {no_progress}/{NO_PROGRESS_LIMIT} (距中心 {dist_center:.0f} vs 上轮 {last_dist:.0f})")
                    if no_progress >= NO_PROGRESS_LIMIT:
                        mfaalog.info("[导航] 滑动连续无进展, 无法导航(无兜底)")
                        return CustomAction.RunResult(success=False)
                else:
                    no_progress = 0
                last_dist = dist_center

                # 定向滑动: 地图跟手, 手指沿"目标→中心"方向滑 SWIPE_DIST 距离
                # 起点=中心朝目标方向100px处(目标侧), 终点=屏幕中心, 每轮固定100px逼近
                vec = SCREEN_CENTER - p_target
                len_v = float(np.hypot(*vec))
                if len_v < 1e-6:
                    continue
                direc = vec / len_v
                last_dir = (direc[0], direc[1])
                start = (int(round(SCREEN_CENTER[0] - SWIPE_SIGN * direc[0] * SWIPE_DIST)),
                         int(round(SCREEN_CENTER[1] - SWIPE_SIGN * direc[1] * SWIPE_DIST)))
                end = (int(SCREEN_CENTER[0]), int(SCREEN_CENTER[1]))
                mfaalog.info(f"[导航] 定向滑动 方向 ({direc[0]:.2f},{direc[1]:.2f}) 起点{start}->中心{end}")
                controller.post_swipe(*start, *end, SWIPE_DURATION).wait()
                time.sleep(1.5)
                prevent_touch(context)   # 滑动后检查误触进入关卡

            # 步骤4: 超轮未达目标 -> 失败(无兜底)
            mfaalog.info("[导航] 方案B超最大轮数未达目标, 无法导航(无兜底)")
            return CustomAction.RunResult(success=False)

        except Exception as e:
            mfaalog.error(f"[导航] 严重错误: {str(e)}")
            return CustomAction.RunResult(success=False)

    def _detect_window_nametags(self, img, center, ww=WIN_W, wh=WIN_H):
        """整图漏检兜底: 在预测位置附近开小窗口(约名称条大小)推理找名称条
        整图推理时名称条受周围元素干扰被模型漏检; 小窗口裁剪去除干扰后模型可正常识别。
        返回 [(x, y, w, h)] 整图坐标的 nametag 框列表(高/低阈合并去重)"""
        H, W = img.shape[:2]
        if ww > W or wh > H:
            return []
        x0 = max(0, min(int(center[0] - ww / 2), W - ww))
        y0 = max(0, min(int(center[1] - wh / 2), H - wh))
        win = img[y0:y0 + wh, x0:x0 + ww]
        det = self._detector()
        raw = []
        for conf in (det.CONF, WIN_CONF):
            r = det.model(win, conf=conf, imgsz=det.IMGSZ, verbose=False)[0]
            for b in r.boxes:
                x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                raw.append((x1, y1, x2 - x1, y2 - y1, float(b.conf[0]), int(b.cls[0])))
        names = [(x, y, w, h, c) for x, y, w, h, c, cl in raw if cl == 0]
        dedup = []
        for (x, y, w, h, c) in sorted(names, key=lambda t: -t[4]):
            if any(QuestDetector._iou((x, y, x + w, y + h), (dx, dy, dx + dw, dy + dh)) > 0.5
                   for (dx, dy, dw, dh, _c) in dedup):
                continue
            dedup.append((x, y, w, h, c))
        return [(x0 + x, y0 + y, w, h) for (x, y, w, h, _c) in dedup]

    def _detector(self):
        """延迟加载全局检测器(ultralytics), 复用避免重复初始化"""
        det = getattr(self, "_det", None)
        if det is None:
            agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(agent_dir, "utils", "quest_detect.pt")
            det = QuestDetector(model_path)
            self._det = det
            mfaalog.info(f"[导航] 检测器加载: {model_path}")
        return det
