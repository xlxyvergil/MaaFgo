# -*- coding: utf-8 -*-
"""
助战查询 Action

坐标系(三套):
  1. 全屏: 模拟器截图 1280x720, YOLO(support_det) 检测框所在坐标系
  2. 框内: YOLO 框左上角 = (0,0)。d:\\fgo\\坐标系.txt 的全部锚点均为框内相对坐标
  3. ROI : 以锚点为中心的裁剪窗口(尺寸见各常量注释)

流程:
  1. 截图 -> YOLO 检测助战条目框(按 y 排序, 即条目顺序)
  2. 对每个框判定(全屏坐标 = 框左上角 + 框内锚点):
     a. 英灵   : attach.servant(servantId) -> servant_map.json.images(f_xxx)
                 与框内(95,76)中心 60x60 头像窗口匹配, 任一模板满足即可
     b. 礼装   : 普通 ce / 冠位 ce_1+ce_2(lizhuang 模板; 空/空.png 跳过)
     c. 主动技能: skill_active "a-b-c" 拆分, 非0数字在锚点左下 ROI 拼接识别 == 期望
     d. 宝具等级: np_level/{ch|jp} 模板匹配 == 期望(目录按资源包选择)
     e. 英灵等级: 等级区域 x62-180,y15-42 数字拼接识别 == 期望
     f. 被动技能: skill_passive "a-b-c-d-e": 全0跳过;
                 有非0 -> 运行被动切换流水线 -> 重新截图 -> 按被动锚点匹配
  3. 全部条件满足 -> 点击该框中心; 否则判定下一框
  4. 无框满足 -> 失败

素材路径约定(相对 MaaFgo 根目录):
  pkg   = "cn" if resource_package=="cn" else "base"
  servant_face : resource/{pkg}/image/servant_face/f_*.png        (158x158, 已有)
  lizhuang     : resource/{pkg}/image/lizhuang/                    (礼装模板, 待放)
  np_level     : resource/{pkg}/image/np_level/ch|jp/              (宝具模板, 待放)
  servant_map  : agent/utils/servant_map.json                      (已有)
  support_det  : agent/utils/support_det.pt                        (YOLO 模型, 待放)
"""

import json
import os
import re
import sys
import time

import numpy as np

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

import mfaalog

# ---------------- 路径常量 ----------------
_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT_DIR = os.path.dirname(_AGENT_DIR)
SERVANT_MAP_PATH = os.path.join(_AGENT_DIR, "utils", "servant_map.json")
SUPPORT_MODEL_PATH = os.path.join(_AGENT_DIR, "utils", "support_det.pt")

# ---------------- YOLO 检测参数 ----------------
IMGSZ = 640          # support_det 训练尺寸
CONF = 0.5
LOW_CONF = 0.25

# ---------------- 框内锚点(坐标系.txt, YOLO 框左上角=0,0) ----------------
LEVEL_ROI = (62, 15, 118, 27)      # 英灵等级: x62-180 y15-42 -> (x,y,w,h)
# 英灵头像多边形(相对框顶点), 裁剪区域是 servant_face 大模板(158x158)的子区域:
# 用该截图在模板上滑动匹配找最高分
FACE_POLY = [(58, 40), (161, 42), (162, 84), (143, 85), (142, 105), (35, 109), (35, 60)]
TH_FACE = 0.75
CE_ANCHOR_NORMAL = (89, 157)       # 普通助战 礼装
CE_ANCHOR_GRAND = [(261, 61), (269, 153)]   # 冠位助战 礼装1/礼装2
CE_ANCHOR_BOND = (262, 106)        # 冠位助战 羁绊(暂不处理, 仅保留)
SKILL_ACTIVE = [(793, 175), (836, 175), (882, 175)]       # 主动技能 1-3 (数字左下角锚点)
SKILL_PASSIVE = [(794, 177), (832, 177), (869, 177), (907, 177), (945, 177)]  # 被动技能 1-5 (数字左下角锚点)
NP_ROI = (200, 74, 580, 94)        # 宝具: x200-780 y74-168 -> (x,y,w,h)
VIEW_ROI = (782, 102, 138, 39)     # 视图判断: 技能卡 x782-920 y102-141 (相对框, 与 skill/主动|被动.png 匹配)

# ---------------- OCR 资源(MaaCommonAssets ppocr_v6) ----------------
OCR_EN_DIR = os.path.join(_ROOT_DIR, "assets", "MaaCommonAssets", "OCR", "ppocr_v6")
OCR_REC_ONNX = os.path.join(OCR_EN_DIR, "rec.onnx")
OCR_REC_KEYS = os.path.join(OCR_EN_DIR, "keys.txt")

# ---------------- ROI 尺寸 ----------------
CE_WIN = 48            # 礼装窗口(锚点中心)
DIGIT_OFF = (-2, -22)    # 技能等级数字: ROI 左上角相对数字左下角锚点的偏移(锚点x左移2px, 向上22px)
DIGIT_SIZE = (30, 20)   # 技能等级数字 ROI 尺寸 (w, h): 30x20, 右上角 (锚点x+30, 锚点y-20)

# ---------------- 匹配阈值 ----------------
TH_CE = 0.70
TH_NP = 0.70
# np_level 模板为小图标(约40x40), 需在宝具 ROI 内多尺度滑动匹配
NP_SCALES = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5, 2.0)

# 被动技能切换流水线节点名(需与助战.json 的节点名一致; 流水线内自动按国服/日服切换资源)
PASSIVE_SWITCH_TASK = "被动技能切换"
# 被动识别截图参数: 切换后被动视图短暂显示, 随后游戏内主动/被动自动轮回, 需多帧捕捉
PASSIVE_SHOTS = 5        # 切换后连截帧数
PASSIVE_INTERVAL = 0.5   # 截图间隔(秒)
# 主动视图截图参数: 技能显示存在主动/被动自动轮回, 主动技能/宝具/等级识别同样需多帧确认
ACTIVE_SHOTS = 5         # 主动视图补截帧数
ACTIVE_INTERVAL = 0.5    # 截图间隔(秒)

# 空礼装标记(选此项则不进行礼装匹配)
EMPTY_CE = ("", "空.png")


# ---------------- 通用工具 ----------------
def _imread(path, gray=False):
    """支持中文路径读取图片; gray=True 返回灰度图"""
    import cv2
    flag = cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR
    if not os.path.isfile(path):
        return None
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), flag)
    return img


def _norm_img(img):
    """兼容 Maa screencap 返回的 ndarray/RGBA/PIL, 统一为 BGR uint8 ndarray"""
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


def _roi(img, bx, by, rx, ry, rw, rh):
    """全屏图 img 上取框内 ROI: 框左上角(bx,by) + 框内相对(rx,ry,rw,rh), 自动越界裁剪"""
    x0, y0 = bx + rx, by + ry
    h, w = img.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x0 + rw), min(h, y0 + rh)
    if x1 <= x0 or y1 <= y0:
        return None
    return img[y0:y1, x0:x1]


def _anchor_roi(img, bx, by, ax, ay, win):
    """以框内锚点(ax,ay)为中心取 win x win 窗口"""
    return _roi(img, bx, by, ax - win // 2, ay - win // 2, win, win)


def parse_skill(s):
    """解析 "0-0-0" 格式 -> [int,...]; 解析失败返回 None"""
    if not s:
        return []
    parts = [p.strip() for p in s.split("-")]
    if not all(re.fullmatch(r"\d+", p) for p in parts):
        return None
    return [int(p) for p in parts]


# ---------------- YOLO 助战条目检测 ----------------
class SupportDetector:
    """support_entry YOLO 检测: 返回全屏框 [(x1,y1,x2,y2,conf)] 按 y 排序"""

    def __init__(self, model_path):
        from ultralytics import YOLO
        self.model = YOLO(model_path)

    def detect(self, img):
        r = self.model(img, conf=CONF, imgsz=IMGSZ, verbose=False)[0]
        boxes = []
        for b in r.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
            boxes.append((x1, y1, x2, y2, float(b.conf[0])))
        if not boxes:
            # 低置信兜底
            r = self.model(img, conf=LOW_CONF, imgsz=IMGSZ, verbose=False)[0]
            boxes = []
            for b in r.boxes:
                x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                boxes.append((x1, y1, x2, y2, float(b.conf[0])))
        # 按 y 排序(条目顺序)
        boxes.sort(key=lambda b: (b[1], b[0]))
        return boxes


# ---------------- 助战 Action ----------------
@AgentServer.custom_action("support_action")
class SupportAction(CustomAction):
    """助战查询 Action: 全部条件满足才点击对应条目"""

    _servant_map = None

    @classmethod
    def _get_servant_map(cls):
        if cls._servant_map is None:
            with open(SERVANT_MAP_PATH, encoding="utf-8") as fp:
                cls._servant_map = json.load(fp)
        return cls._servant_map

    # ---------- 英灵匹配 ----------
    def _match_servant(self, img, face_dir, bx, by, images, support_type):
        # 头像匹配: 多边形截图在 servant_face 模板上滑动(截图是模板的子区域)
        import cv2
        fxs = [p[0] for p in FACE_POLY]; fys = [p[1] for p in FACE_POLY]
        poly = cv2.cvtColor(
            img[by + min(fys):by + max(fys) + 1, bx + min(fxs):bx + max(fxs) + 1],
            cv2.COLOR_BGR2GRAY)
        if poly.size:
            for f in images:
                tpl = _imread(os.path.join(face_dir, f), gray=True)
                if tpl is None or poly.shape[0] > tpl.shape[0] or poly.shape[1] > tpl.shape[1]:
                    continue
                s = float(cv2.matchTemplate(tpl, poly, cv2.TM_CCOEFF_NORMED).max())
                mfaalog.info(f"[SupportAction] 英灵头像 {f}: score={s:.3f}")
                if s >= TH_FACE:
                    return True
        return False

    # ---------- 礼装匹配 ----------
    def _match_ce(self, img, ce_dir, bx, by, ce_name, anchor):
        import cv2
        if ce_name in EMPTY_CE:
            mfaalog.info("[SupportAction] 礼装为空(跳过匹配)")
            return True
        tpl = _imread(os.path.join(ce_dir, ce_name), gray=True)
        roi = _anchor_roi(img, bx, by, anchor[0], anchor[1], CE_WIN)
        if roi is None:
            return False
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        t = cv2.resize(tpl, (roi_gray.shape[1], roi_gray.shape[0]),
                       interpolation=cv2.INTER_CUBIC)
        score = float(cv2.matchTemplate(roi_gray, t, cv2.TM_CCOEFF_NORMED).max())
        mfaalog.info(f"[SupportAction] 礼装 {ce_name}: score={score:.3f}")
        return score >= TH_CE

    # ---------- 技能等级匹配 ----------
    def _match_skill(self, img, bx, by, anchor, expect):
        """expect>0 时用 OCR 识别技能数字与期望比对; expect=0(未配置)视为匹配"""
        if expect <= 0:
            return True
        roi = _roi(img, bx, by, anchor[0] + DIGIT_OFF[0], anchor[1] + DIGIT_OFF[1],
                   DIGIT_SIZE[0], DIGIT_SIZE[1])
        if roi is None:
            return False
        got = SupportAction._ocr_skill_text(roi)
        ok = got is not None and got == str(expect)
        mfaalog.info(f"[SupportAction] 技能({anchor}) 期望={expect} "
                     f"OCR识别={got or '(无数字)'} -> {'OK' if ok else 'NO'}")
        return ok

    # ---------- 宝具等级匹配 ----------
    def _match_np(self, img, np_dir, bx, by, expect):
        import cv2
        roi = _roi(img, bx, by, NP_ROI[0], NP_ROI[1], NP_ROI[2], NP_ROI[3])
        if roi is None:
            return False
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        best = (TH_NP, None, None)   # (score, 等级, 文件名)
        for f in sorted(os.listdir(np_dir)):
            if not f.endswith(".png"):
                continue
            m = re.search(r"(\d+)", f)
            if not m:
                continue
            lv = int(m.group(1))
            tpl = _imread(os.path.join(np_dir, f), gray=True)
            if tpl is None:
                continue
            th, tw = tpl.shape
            # 模板为小图标(约40x40), 在 ROI 内多尺度滑动匹配
            for sc in NP_SCALES:
                sw, sh = round(tw * sc), round(th * sc)
                if sw > roi_gray.shape[1] or sh > roi_gray.shape[0]:
                    continue
                t = cv2.resize(tpl, (sw, sh), interpolation=cv2.INTER_CUBIC)
                score = float(cv2.matchTemplate(roi_gray, t, cv2.TM_CCOEFF_NORMED).max())
                if score > best[0]:
                    best = (score, lv, f)
        if best[1] is None:
            mfaalog.warning("[SupportAction] 宝具等级无命中")
            return False
        ok = best[1] == expect
        mfaalog.info(f"[SupportAction] 宝具 期望={expect} 识别=lv{best[1]}({best[2]}) "
                     f"score={best[0]:.3f} -> {'OK' if ok else 'NO'}")
        return ok

    # ---------- OCR 识别英灵等级(懒加载) ----------
    # PaddleOCR rec.onnx 直识别 "120/120" 格式, 跳过 det(检测框易漏第一位数字)
    _ocr_sess = None
    _ocr_keys = None

    @staticmethod
    def _ocr_load():
        if SupportAction._ocr_sess is not None:
            return SupportAction._ocr_sess or None, SupportAction._ocr_keys
        try:
            import onnxruntime as ort
            sess = ort.InferenceSession(OCR_REC_ONNX, providers=["CPUExecutionProvider"])
            with open(OCR_REC_KEYS, "r", encoding="utf-8") as f:
                keys = [ln.strip("\n") for ln in f.readlines()]
            SupportAction._ocr_sess, SupportAction._ocr_keys = sess, keys
            mfaalog.info(f"[SupportAction] OCR rec.onnx 加载成功 ({len(keys)} 类)")
        except Exception as e:
            mfaalog.warning(f"[SupportAction] OCR 加载失败, 退回模板识别: {e}")
            SupportAction._ocr_sess = False
        return (SupportAction._ocr_sess if SupportAction._ocr_sess else None), SupportAction._ocr_keys

    @staticmethod
    def _ocr_run(gray, upscale):
        """灰度图放大 upscale 倍 -> rec(v5) 推理 -> 原始识别文本(含噪声)"""
        sess, keys = SupportAction._ocr_load()
        if sess is None:
            return None
        import cv2
        h, w = gray.shape[:2]
        if h == 0 or w == 0:
            return None
        big = cv2.resize(gray, (w * upscale, h * upscale), interpolation=cv2.INTER_CUBIC)
        rgb = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
        rw = min(320, max(4, int(round(rgb.shape[1] * 48.0 / rgb.shape[0]))))
        im = cv2.resize(rgb, (rw, 48)).astype(np.float32) / 255.0
        im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
        im = ((im - 0.5) / 0.5).transpose(2, 0, 1)[None]
        p = sess.run(None, {sess.get_inputs()[0].name: im})[0][0]
        idx = p.argmax(axis=1)
        out, last = [], None
        for i in idx:
            if i != 0 and i != last:
                out.append(keys[i - 1] if 0 <= i - 1 < len(keys) else "?")
            last = i
        return "".join(out)

    @staticmethod
    def _ocr_level_text(roi):
        """OCR 识别等级区域, 返回当前等级 int; OCR 不可用/无匹配返回 None"""
        import cv2
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)[2:22, :]   # 去掉上下横线
        if g.size == 0:
            return None
        t = SupportAction._ocr_run(g, 3)
        if not t:
            return None
        m = re.search(r"(\d{1,3})/(\d{1,3})", t)
        if m:
            return int(m.group(1))
        m = re.search(r"(\d{1,3})7(\d{1,3})", t)   # 斜杠偶被识别为 7
        if m:
            cur, mx = int(m.group(1)), int(m.group(2))
            if 1 <= cur <= 130 and 1 <= mx <= 130:
                return cur
        return None

    @staticmethod
    def _ocr_skill_text(roi):
        """OCR 识别技能数字 ROI(30x20):
           灰色(无技能) -> "0"; 数字 1-10 -> 数字串; 多一位噪声 -> 规范化;
           无数字 -> "0" """
        import cv2
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        h, w = g.shape
        bin_ = (g > 150).astype(np.uint8)
        # 灰色判定: 数字为亮色, 亮像素占比过低 => 无技能(0级), 避免灰色区域被 OCR 读出噪声数字
        # 阈值 0.12: 真灰色(无技能) 亮占比 8-11%; 深色背景的数字(015) 约 19%, 仍可识别
        if bin_.mean() < 0.12:
            return "0"
        # 投影定位数字亮块: 只保留"部分亮"行/列(0<sum<w-3),
        # 剔除空行(全空)和全亮边框行/竖线(卡面分隔线), 避免边框被 OCR 误读成数字
        rows = [i for i in range(h) if 0 < bin_[i].sum() < w - 3]
        cols = [j for j in range(w) if 0 < bin_[:, j].sum() < h - 3]
        if not rows or not cols:
            return "0"
        y0, y1 = rows[0], rows[-1]
        x0, x1 = cols[0], cols[-1]
        sub = g[y0:y1 + 1, x0:x1 + 1]
        t = SupportAction._ocr_run(sub, 4)
        if not t:
            return None
        # "0" 的常见误读是 o/O(如 "1or" -> "10")
        t = re.sub(r"[oO]", "0", t)
        m = re.search(r"\d+", t)
        if not m:
            return None
        v = int(m.group(0))
        if v == 0:
            return "0"
        if v <= 10:
            return str(v)
        # 技能等级只有 1-10: 以 1 开头的多位数(如 107/195/167)是 "10" 的误读;
        # 其余(如 8->82)是多一位噪声, 取首位数
        s = str(v)
        if s[0] == "1":
            return "10"
        return s[0]

    # ---------- 英灵等级匹配(仅 OCR) ----------
    def _match_level(self, img, bx, by, expect):
        if expect <= 0:
            return True
        roi = _roi(img, bx, by, LEVEL_ROI[0], LEVEL_ROI[1], LEVEL_ROI[2], LEVEL_ROI[3])
        if roi is None:
            return False
        got = SupportAction._ocr_level_text(roi)
        ok = got == expect
        mfaalog.info(f"[SupportAction] 英灵等级 期望={expect} OCR识别={got if got is not None else '(无)'} -> {'OK' if ok else 'NO'}")
        return ok

    # ---------- 被动技能匹配(切换后视图) ----------
    def _match_passive(self, img, bx, by, expects):
        for i, anchor in enumerate(SKILL_PASSIVE):
            if i >= len(expects) or expects[i] <= 0:
                continue
            if not self._match_skill(img, bx, by, anchor, expects[i]):
                return False
        return True

    # ---------- 主动技能匹配(单帧) ----------
    def _check_active_skills(self, img, bx, by, actives):
        for i, v in enumerate(actives):
            if i >= len(SKILL_ACTIVE):
                break
            if not self._match_skill(img, bx, by, SKILL_ACTIVE[i], v):
                return False
        return True

    # ---------- 视图判断(主动/被动) ----------
    def _match_view(self, img, bx, by, skill_dir):
        """裁剪技能卡区域(VIEW_ROI), 与 skill/{主动,被动}.png 滑动匹配, 返回 "active"/"passive";
        模板目录按 pkg 动态选择(base/cn), 无法判定返回 None"""
        import cv2
        if not hasattr(self, "_view_tpls") or self._view_tpls_key != skill_dir:
            self._view_tpls = {
                "active": _imread(os.path.join(skill_dir, "主动.png"), gray=True),
                "passive": _imread(os.path.join(skill_dir, "被动.png"), gray=True),
            }
            self._view_tpls_key = skill_dir
        crop = _roi(img, bx, by, VIEW_ROI[0], VIEW_ROI[1], VIEW_ROI[2], VIEW_ROI[3])
        if crop is None:
            return None
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        scores = {}
        for key, t in self._view_tpls.items():
            if t is not None and t.shape[0] <= g.shape[0] and t.shape[1] <= g.shape[1]:
                scores[key] = float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED).max())
        if not scores:
            return None
        if "active" not in scores:
            return "passive"
        if "passive" not in scores:
            return "active"
        mfaalog.info(f"[SupportAction] 视图判定 主动={scores['active']:.3f} 被动={scores['passive']:.3f}")
        return "active" if scores["active"] >= scores["passive"] else "passive"

    # ---------- 主动视图综合判定(主动技能+宝具+英灵等级, 单帧) ----------
    def _check_active_view(self, img, bx, by, actives, np_dir, np_level, level):
        if not self._check_active_skills(img, bx, by, actives):
            return False
        if not self._match_np(img, np_dir, bx, by, np_level):
            return False
        if not self._match_level(img, bx, by, level):
            return False
        return True

    # ---------- 主流程 ----------
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = {}
            if argv.custom_action_param:
                try:
                    param = json.loads(argv.custom_action_param)
                except json.JSONDecodeError:
                    param = {}
            support_type = param.get("support_type", "normal")

            node = context.get_node_data(argv.node_name) or {}
            attach = node.get("attach") or {}
            servant_id = str(attach.get("servant") or "").strip()
            ce = str(attach.get("ce") or "").strip()
            ce_1 = str(attach.get("ce_1") or "").strip()
            ce_2 = str(attach.get("ce_2") or "").strip()
            skill_active = str(attach.get("skill_active") or "").strip()
            skill_passive = str(attach.get("skill_passive") or "").strip()
            try:
                np_level = int(attach.get("np_level") or 0)
            except (TypeError, ValueError):
                np_level = 0
            try:
                level = int(attach.get("level") or 0)
            except (TypeError, ValueError):
                level = 0

            active = parse_skill(skill_active)
            passive = parse_skill(skill_passive)
            if active is None:
                mfaalog.error(f"[SupportAction] skill_active 格式错误: {skill_active}")
                return CustomAction.RunResult(success=False)
            if passive is None:
                mfaalog.error(f"[SupportAction] skill_passive 格式错误: {skill_passive}")
                return CustomAction.RunResult(success=False)
            passive_need = any(v > 0 for v in passive)

            mfaalog.info(f"[SupportAction] 助战类型={support_type} 英灵={servant_id or '(未选)'} "
                         f"礼装={ce or ce_1 or ce_2 or '(空)'} 主动={active} 被动={passive} "
                         f"宝具={np_level} 等级={level}")

            cfg = context.get_node_data("资源包配置") or {}
            resource_package = str((cfg.get("attach") or {}).get("resource_package") or "").strip()
            pkg = "cn" if resource_package == "cn" else "base"
            base_dir = os.path.join(_ROOT_DIR, "resource", pkg, "image")
            face_dir = os.path.join(base_dir, "servant_face")
            ce_dir = os.path.join(base_dir, "lizhuang")
            np_dir = os.path.join(base_dir, "nplevel")   # 宝具模板按 pkg 动态选择(base/cn)
            skill_dir = os.path.join(base_dir, "skill")   # 视图判断模板(主动/被动), 按 pkg 动态选择
            mfaalog.info(f"[SupportAction] 素材根: {base_dir} 宝具目录: {np_dir}")

            if not servant_id:
                mfaalog.error("[SupportAction] 未选择英灵")
                return CustomAction.RunResult(success=False)

            smap = self._get_servant_map()
            srv = smap.get(servant_id)
            if not srv or not srv.get("images"):
                mfaalog.error(f"[SupportAction] servant_map 无该英灵: {servant_id}")
                return CustomAction.RunResult(success=False)

            # 礼装匹配目标(普通1个/冠位2个)
            ce_targets = []
            if support_type == "grand":
                ce_targets.append((ce_1, CE_ANCHOR_GRAND[0]))
                ce_targets.append((ce_2, CE_ANCHOR_GRAND[1]))
            else:
                ce_targets.append((ce, CE_ANCHOR_NORMAL))

            detector = SupportDetector(SUPPORT_MODEL_PATH)

            controller = context.tasker.controller
            img = _norm_img(controller.post_screencap().wait().get())
            if img is None:
                mfaalog.error("[SupportAction] 截图失败")
                return CustomAction.RunResult(success=False)
            boxes = detector.detect(img)
            mfaalog.info(f"[SupportAction] 检测到 {len(boxes)} 个助战条目")
            if not boxes:
                mfaalog.error("[SupportAction] 未检测到助战条目")
                return CustomAction.RunResult(success=False)

            passive_view = False
            for (bx, by, bx2, by2, conf) in boxes:
                mfaalog.info(f"[SupportAction] === 判定条目 框=({bx},{by})-({bx2},{by2}) conf={conf:.2f} ===")
                if not self._match_servant(img, face_dir, bx, by, srv["images"], support_type):
                    continue
                if not all(self._match_ce(img, ce_dir, bx, by, name, anc)
                           for name, anc in ce_targets):
                    continue
                # 主动视图(主动技能+宝具+英灵等级): 单帧失败则间隔补截多帧确认
                # (技能显示存在主动/被动自动轮回, 多帧捕捉主动视图)
                if not self._check_active_view(img, bx, by, active, np_dir, np_level, level):
                    ok = False
                    for _ in range(ACTIVE_SHOTS):
                        time.sleep(ACTIVE_INTERVAL)
                        img = _norm_img(controller.post_screencap().wait().get())
                        if img is None:
                            return CustomAction.RunResult(success=False)
                        # 视图已切到被动, 主动视图不会再现, 停止等待
                        if self._match_view(img, bx, by, skill_dir) == "passive":
                            break
                        if self._check_active_view(img, bx, by, active, np_dir, np_level, level):
                            ok = True
                            break
                    if not ok:
                        continue

                # 主动/宝具/等级 均满足, 剩被动
                if not passive_need:
                    cx, cy = (bx + bx2) // 2, (by + by2) // 2
                    controller.post_click(cx, cy).wait()
                    mfaalog.info(f"[SupportAction] 点击条目 ({cx},{cy})")
                    return CustomAction.RunResult(success=True)

                # 需要被动: 切换流水线 -> 间隔 PASSIVE_INTERVAL 连截 PASSIVE_SHOTS 帧,
                # 任一帧被动匹配成功即通过(点击后被动视图短暂显示, 随后主动/被动自动轮回)
                if not passive_view:
                    mfaalog.info(f"[SupportAction] 运行被动切换流水线: {PASSIVE_SWITCH_TASK}")
                    try:
                        context.run_task(PASSIVE_SWITCH_TASK)
                    except Exception as e:
                        mfaalog.warning(f"[SupportAction] 被动切换流水线异常: {e}")
                    passive_view = True

                passive_ok = False
                for i in range(PASSIVE_SHOTS):
                    img = _norm_img(controller.post_screencap().wait().get())
                    if img is None:
                        return CustomAction.RunResult(success=False)
                    # 仍是主动视图则等待被动轮回(避免在主动视图上误匹配)
                    if self._match_view(img, bx, by, skill_dir) == "active":
                        if i < PASSIVE_SHOTS - 1:
                            time.sleep(PASSIVE_INTERVAL)
                        continue
                    if self._match_passive(img, bx, by, passive):
                        passive_ok = True
                        break
                    if i < PASSIVE_SHOTS - 1:
                        time.sleep(PASSIVE_INTERVAL)
                if not passive_ok:
                    continue
                cx, cy = (bx + bx2) // 2, (by + by2) // 2
                controller.post_click(cx, cy).wait()
                mfaalog.info(f"[SupportAction] 点击条目 ({cx},{cy})")
                return CustomAction.RunResult(success=True)

            mfaalog.error("[SupportAction] 无满足全部条件的助战条目")
            return CustomAction.RunResult(success=False)
        except Exception as e:
            import traceback
            mfaalog.error(f"[SupportAction] 异常: {e}\n{traceback.format_exc()}")
            return CustomAction.RunResult(success=False)
