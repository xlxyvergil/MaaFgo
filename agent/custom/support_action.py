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
     a. 英灵   : attach.servant(servantId) -> servant_list.json.images(f_xxx);
                 attach.class_name(中文职介名) 由 英灵选择 职介 case 固定注入;
                 any=不要求英灵, 保留当前职介页签并跳过英灵及其技能/宝具/等级要求
                 与框内(95,76)中心 60x60 头像窗口匹配, 任一模板满足即可
     b. 礼装   : 普通 ce / 冠位 ce_1+ce_2(lizhuang 模板; 0_空.png 跳过)
     c. 主动技能: skill_active_1/2/3 独立键, 非0数字在锚点左下 ROI 拼接识别 >= 期望
     d. 宝具等级: nplevel 模板匹配, 识别等级 >= 期望(目录按资源包选择)
     e. 英灵等级: 等级区域 x62-180,y15-42 数字拼接识别 >= 期望
     f. 被动技能: skill_passive_1~5 独立键: 全0跳过;
                 有非0 -> 运行被动切换流水线 -> 重新截图 -> 按被动锚点匹配
  3. 全部条件满足 -> 点击该框中心; 否则判定下一框
  4. 无框满足 -> 失败

素材路径约定(相对 MaaFgo 根目录):
  pkg   = 实际加载资源层的 loaded_resource_package；旧资源无标记时兼容 resource_package
  servant_face : resource/base/image/servant_face/f_*.png          (头像共用)
  lizhuang     : resource/base/image/lizhuang/                     (礼装共用)
  np_level     : resource/{pkg}/image/nplevel/                      (宝具等级模板)
  skill        : resource/{pkg}/image/skill/                        (主动/被动视图模板)
  support      : resource/{pkg}/image/support/                      (参照物模板)
  servant_list : agent/custom/servant_list.json                    (已有)
  support_det  : agent/utils/support_det.onnx                       (YOLO ONNX 模型)
"""

import json
import os
import re
import sys
import time
from functools import lru_cache

import numpy as np

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

import mfaalog
from resource_package import resolve_resource_package
from chaldea import fetch_share_data
from chaldea.servant_aliases import build_servant_lookup
from chaldea.support_criteria import build_support_criteria
from battle.runtime.formation_session import context_session, sessions
from battle.core.models import Confidence
from battle.data.servant_identity import canonical_servant_id
from support_ce import extension_entries, resolve_external_templates

# ---------------- 路径常量 ----------------
_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT_DIR = os.path.dirname(_AGENT_DIR)
SERVANT_LIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servant_list.json")
SUPPORT_MODEL_PATH = os.path.join(_AGENT_DIR, "utils", "support_det.onnx")

# ---------------- YOLO 检测参数 ----------------
IMGSZ = 640          # support_det 训练尺寸
CONF = 0.5
LOW_CONF = 0.25
BASE_W, BASE_H = 1280, 720

# ---------------- 框内锚点(坐标系.txt, YOLO 框左上角=0,0) ----------------
LEVEL_ROI = (62, 15, 118, 27)      # 英灵等级: x62-180 y15-42 -> (x,y,w,h)
# 英灵头像多边形(相对框顶点), 裁剪区域是 servant_face 大模板(158x158)的子区域:
# 用该截图在模板上滑动匹配找最高分。
# 多边形本身已把头像上覆盖的特殊 UI(右上角职介/等级、右下角金星/星级)排除在外:
#   左上斜切(51,39)->(27,57) 让开左上角图标, 右下缺口 x140-166 y84-108 让开金星/星级
FACE_POLY = [(27, 57), (27, 108), (140, 108), (140, 84), (166, 84), (166, 39), (51, 39)]
TH_FACE = 0.75
TH_UNRESTRICTED_FACE_MARGIN = 0.10   # 仅礼装助战补身份须排除相似的其他从者
CE_ANCHOR_NORMAL = (6, 134)        # 普通助战 礼装 左上角(166x53 窗口)
CE_ANCHOR_GRAND = [(179, 35), (179, 129)]   # 冠位助战 礼装1/礼装2 左上角(166x53 窗口)
# 日服现场图标左上角约为框内 (186,93)，旧 29x29 区域会截断图案。
# 保留完整 24x24 模板的滑动空间，只搜索羁绊图标附近，避免扩大到相邻礼装。
BOND_ROI = (180, 84, 40, 40)
BOND_TEMPLATES = {"50np": "50np.png", "original": "羁绊.png"}   # 羁绊选项 -> 模板(带绿幕), 纯图案多服通用, 固定取 base 包 skill 目录
TH_BOND = 0.40                     # 较高分须严格超过此值，才确认羁绊效果图标
SKILL_ACTIVE = [(791, 154, 27, 22), (836, 154, 29, 22), (881, 154, 28, 22)]   # 主动技能 1-3 数字方框 (x,y,w,h)
SKILL_PASSIVE = [(791, 156, 25, 20), (828, 156, 29, 20), (866, 156, 26, 20),
                 (903, 156, 24, 20), (941, 156, 25, 20)]        # 被动技能 1-5 数字方框 (x,y,w,h)
# 宝具: x200-780 y74-178。日服贞德 Lv.4 的底部超出旧 y168 边界，
# 向下保留 10px 余量，避免完整模板被裁断；阈值和多尺度匹配规则不变。
NP_ROI = (200, 74, 580, 104)
VIEW_ROI = (782, 102, 138, 39)     # 视图判断: 技能卡 x782-920 y102-141 (相对框, 与 skill/主动|被动.png 匹配)

# ---------------- 框内参照物坐标重新定位 ----------------
# YOLO 框本身会漂移, 直接用框左上角当原点会让上面所有锚点整体偏移。
# 每个框先在框内定位参照物 {资源包}/image/support/助战编入确认.png(复用已有资源),
# 求出它与标定位置的差, 再把框顶点平移过去 —— 平移后图内坐标即等于标定坐标系, 上面锚点常量可直接套用。
REF_BASE = (1051, 26)   # 参照物左上角在标定坐标系中的位置(上面锚点常量即在该坐标系下量的)
REF_PAD = 40            # 参照物搜索范围: 以期望位置为中心向外扩 px
TH_REF = 0.80           # 低分参照物不能用于平移头像/技能等所有识别区域

# ---------------- 职介筛选 tab(全屏坐标, 助战选择界面顶部职介栏) ----------------
CLASS_TABS = {
    "剑士": (159, 130),
    "弓兵": (228, 130),
    "枪兵": (296, 130),
    "骑兵": (362, 130),
    "魔术师": (434, 130),
    "暗杀者": (497, 130),
    "狂战士": (565, 130),
    "OTHER": (629, 130),   # 盾/裁定者/复仇者/降临者/兽/他人格/伪装者/月之癌 共用
    "ALL": (91, 130),      # all 阶(职介筛选选项选 ALL 时点击)
}

# ---------------- OCR 资源：兼容打包版和开发目录 ----------------
_PACKAGED_OCR_DIR = os.path.join(_ROOT_DIR, "resource", "model", "ocr")
OCR_EN_DIR = (_PACKAGED_OCR_DIR if os.path.isdir(_PACKAGED_OCR_DIR)
              else os.path.join(_ROOT_DIR, "assets", "resource", "base", "model", "ocr"))
OCR_REC_ONNX = os.path.join(OCR_EN_DIR, "rec.onnx")
OCR_REC_KEYS = os.path.join(OCR_EN_DIR, "keys.txt")


def _image_dir(package, *parts):
    """Return an image directory from either a packaged or development layout."""
    relative = os.path.join(package, "image", *parts)
    packaged = os.path.join(_ROOT_DIR, "resource", relative)
    if os.path.isdir(packaged):
        return packaged
    return os.path.join(_ROOT_DIR, "assets", "resource", relative)


def _resolve_resource_package(context):
    """Use the loaded image layer, not a possibly stale per-task option."""
    return resolve_resource_package(context, caller="助战")

# ---------------- ROI 尺寸 ----------------
CE_ROI = (166, 53)        # 礼装匹配窗口(w,h): 左上角锚点, 与礼装模板(约153x40)同量级(左右各+3, 向下+3)

# ---------------- 匹配阈值 ----------------
TH_CE = 0.70
# 礼装右下角形态校验: 满破/非满破等仅右下角不同的礼装, 取卡面右下角约30x30区域与模板右下角匹配
CE_BR_SIZE = 30
TH_CE_BR = 0.90              # 右下角绝对阈值: 仅当另一状态模板缺失时兜底使用
# 满破/非满破 的差异只是右下角一颗很淡的星(如 迦勒底午茶时光 灰度差仅77),
# 绝对分数达不到高阈值, 故优先与同名的另一状态模板比"谁更像", 取高者; 该表用于找对应模板
CE_SUB_SWAP = {"满破": "非满破", "非满破": "满破"}
TH_NP = 0.70
# np_level 模板为小图标(约40x40), 需在宝具 ROI 内多尺度滑动匹配
NP_SCALES = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5, 2.0)

# 主动/被动视图切换点击坐标(全屏 1280x720): 点1次切被动, 点2次切回主动
VIEW_SWITCH_POS = (846, 127)

# 未匹配时滑动+刷新循环
SWIPE_START = (559, 669)        # 滑动起点(全屏): 点击后单指移动到终点
SWIPE_END = (559, 389)           # 滑动终点(全屏): 垂直向上滑动 280px
SWIPE_DURATION = 500            # 滑动持续时间(ms)
SWIPE_SETTLE = 1              # 滑动结束后等待列表稳定再识别(秒), 避免惯性滚动导致识别不准
MAX_SWIPE_BEFORE_REFRESH = 12    # 未设置查找范围时，连续滑动12次未匹配后刷新
MAX_SWIPE_BUDGET = 80           # 单轮列表查找范围上限，避免错误配置导致长时间无法刷新
REFRESH_TASK = "助战刷新"        # 刷新流水线(由外部提供, 直接 run_task 调用)
FALLBACK_REFRESH_LIMIT = 10
CONNECT_ROI = (1158, 623, 101, 86)  # 连接中检测区域: x1158-1259 y623-709
TH_WHITE = 245                  # 纯白判定阈值(灰度 >= 该值视为白)
# 实测: 正常助战列表该区域白占比 0-0.3%, 连接中约 33%; 阈值取 10% 区分度充足
WHITE_RATIO = 0.10              # 白像素占比 >= 10% 表示正在连接, 不能继续检测

# 空礼装标记(选此项则不进行礼装匹配；保留旧名称兼容已有配置)
EMPTY_CE = ("0_空.png", "空.png")


# ---------------- 通用工具 ----------------
def _max_swipes_before_refresh(attach):
    """允许特定任务查找更长的助战列表，其余任务保留原来的刷新频率。"""
    value = attach.get("max_swipes_before_refresh", MAX_SWIPE_BEFORE_REFRESH)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SWIPE_BUDGET:
        raise ValueError(
            f"助战列表的查找范围必须是 1～{MAX_SWIPE_BUDGET} 次滑动，当前设置无法开始筛选。"
            "请更新资源包后重试；也可先手动选好符合队伍要求的助战，再从编队界面启动。"
        )
    return value


def _imread(path, gray=False):
    """支持中文路径读取图片; gray=True 返回灰度图"""
    import cv2
    flag = cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR
    if not os.path.isfile(path):
        return None
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), flag)
    return img


@lru_cache(maxsize=1)
def _ce_builtin_names():
    try:
        with open(os.path.join(_AGENT_DIR, "utils", "Chaldea", "equip_names_CN.json"), encoding="utf-8-sig") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return {}


def _support_ce_name(equip_id):
    name = _ce_builtin_names().get(str(equip_id))
    if name:
        return name
    for entry in extension_entries(_ROOT_DIR):
        if str(entry["id"]) == str(equip_id):
            return entry["name"]
    from chaldea.game_data import get_equip_name
    return get_equip_name(equip_id)


def _ce_template_paths(ce_dir, ce_name, status, equip_id=None):
    """Each state uses its built-in template first, then the optional config pack."""
    statuses = CE_SUB_SWAP if status == "任意" else (status,)
    paths = []
    if isinstance(ce_name, str) and ce_name.startswith("support-ce:"):
        try:
            equip_id = int(ce_name.split(":", 1)[1])
        except ValueError:
            return []
        name = _ce_builtin_names().get(str(equip_id))
        trans = {n: n - 0xFEE0 for a, b in ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A)) for n in range(a, b + 1)}
        ce_name = str(name).translate(trans).replace("/", "／").replace("\\", "＼") + ".png" if name else f"f_{equip_id}0.png"
    # Chaldea retains stable IDs even when translated display names differ.
    # Existing name-based custom settings remain compatible.
    external = []
    for sub in statuses:
        if os.path.basename(ce_name) != ce_name or "\\" in ce_name or ":" in ce_name:
            continue
        paths.append(os.path.join(ce_dir, sub, ce_name) if sub else os.path.join(ce_dir, ce_name))
        external.extend(str(path) for path in resolve_external_templates(_ROOT_DIR, ce_name, sub, equip_id))
    return paths + external


def _custom_ce_candidates(attach, key):
    """多选使用独立 attach 键，避免多个 case 的列表覆盖；兼容旧单选。"""
    if attach.get(f"{key}-多选") is True:
        prefix = f"{key}-扩展候选:" if attach.get(f"{key}-扩展包") is True else f"{key}-候选:"
        return [name[len(prefix):] for name, selected in attach.items()
                if name.startswith(prefix) and selected is True] or [EMPTY_CE[0]]
    value = attach[key]
    # 也接受直接传入的文件名列表，便于复用 action 的外部调用。
    values = value if isinstance(value, list) else [value]
    if not all(isinstance(name, str) for name in values):
        raise ValueError(f"礼装候选必须是文件名或文件名列表: {key}")
    return list(dict.fromkeys(name.strip() for name in values if name.strip())) or [EMPTY_CE[0]]


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


# ---------------- YOLO 助战条目检测 ----------------
class SupportDetector:
    """support_entry YOLO 检测: 返回全屏框 [(x1,y1,x2,y2,conf)] 按 y 排序"""

    def __init__(self, model_path):
        from utils.yolo_onnx import YoloOnnx
        self.model = YoloOnnx(model_path, imgsz=IMGSZ)

    def detect(self, img):
        boxes = self.model.detect(img, conf=CONF)
        if not boxes:
            # 低置信兜底
            boxes = self.model.detect(img, conf=LOW_CONF)
        # 按 y 排序(条目顺序)
        boxes.sort(key=lambda b: (b[1], b[0]))
        # 坐标统一取整: YOLO/ONNX 输出的框坐标可能是 float, 直接用于切片会触发
        # "TypeError: slice indices must be integers"
        return [(int(x1), int(y1), int(x2), int(y2), c) for (x1, y1, x2, y2, c, _cl) in boxes]


# ---------------- 助战 Action ----------------
@AgentServer.custom_action("support_action")
class SupportAction(CustomAction):
    """助战查询 Action: 全部条件满足才点击对应条目"""

    _servant_map = None
    _detector = None
    _face_mask = None

    def _init_scale(self, controller):
        """Read the device resolution and initialize reference-to-device scaling."""
        self.sx = self.sy = 1.0
        self._screen_size = (BASE_W, BASE_H)
        self._shot_base(controller)

    def _shot_base(self, controller):
        """Return each screenshot in the 1280x720 reference coordinate system.

        Detection, templates, and all entry-relative anchors are calibrated at
        720p. Controller operations are converted back to the device size by
        _tap and _swipe.
        """
        import cv2
        img = _norm_img(controller.post_screencap().wait().get())
        if img is None:
            return None
        height, width = img.shape[:2]
        screen_size = (width, height)
        if screen_size != self._screen_size:
            self._screen_size = screen_size
            self.sx = width / BASE_W
            self.sy = height / BASE_H
            mfaalog.diagnostic(
                f"[SupportAction] screen={width}x{height}, "
                f"scale={self.sx:.3f}x{self.sy:.3f}"
            )
        if screen_size == (BASE_W, BASE_H):
            return img
        return cv2.resize(img, (BASE_W, BASE_H), interpolation=cv2.INTER_LINEAR)

    def _point_to_screen(self, x, y):
        """Convert a 1280x720 reference point to a controller point."""
        width, height = self._screen_size
        return (
            min(width - 1, max(0, int(round(x * self.sx)))),
            min(height - 1, max(0, int(round(y * self.sy)))),
        )

    def _tap(self, controller, x, y):
        return controller.post_click(*self._point_to_screen(x, y)).wait().succeeded

    def _swipe(self, controller, x1, y1, x2, y2, duration):
        controller.post_swipe(
            *self._point_to_screen(x1, y1),
            *self._point_to_screen(x2, y2), duration,
        ).wait()

    @classmethod
    def _get_face_mask(cls):
        """构建英灵头像多边形 mask, 缓存复用;
        用于物理排除覆盖在头像上的特殊 UI(图标/金星/职介/等级/星级), 形状由 FACE_POLY 决定"""
        if cls._face_mask is None:
            import cv2
            fxs = [p[0] for p in FACE_POLY]; fys = [p[1] for p in FACE_POLY]
            xmin, xmax, ymin, ymax = min(fxs), max(fxs), min(fys), max(fys)
            bw, bh = xmax - xmin + 1, ymax - ymin + 1
            m = np.zeros((bh, bw), np.uint8)
            poly = np.array([(x - xmin, y - ymin) for (x, y) in FACE_POLY], np.int32)
            cv2.fillPoly(m, [poly], 255)
            cls._face_mask = m
        return cls._face_mask

    @classmethod
    def _get_detector(cls):
        """懒加载并缓存 YOLO 检测器, 避免每次 run 重复加载模型"""
        if cls._detector is None:
            cls._detector = SupportDetector(SUPPORT_MODEL_PATH)
        return cls._detector

    @classmethod
    def _get_servant_map(cls):
        if cls._servant_map is None:
            with open(SERVANT_LIST_PATH, encoding="utf-8") as fp:
                data = json.load(fp)
            cls._servant_map = build_servant_lookup(data.get("servants", []))
        return cls._servant_map

    # ---------- 参照物定位(消除 YOLO 框偏移) ----------
    def _ref_shift(self, img, bx, by):
        """在框内定位参照物 {资源包}/image/support/助战编入确认.png, 返回框顶点所需的平移 (dx,dy);
        平移后参照物回到标定位置 REF_BASE, 框内坐标即等于标定坐标系。
        定位不可信时返回 None，调用方跳过该条目，禁止使用低分坐标。"""
        import cv2
        tpl = _imread(os.path.join(self._ref_dir, self._ref_name), gray=True)
        if tpl is None:
            mfaalog.error(f"[SupportAction] 参照模板缺失: {self._ref_name} ({self._ref_dir})")
            return None
        th, tw = tpl.shape[:2]
        ex, ey = bx + REF_BASE[0], by + REF_BASE[1]      # 参照物期望左上角(全屏)
        x0, y0 = max(0, ex - REF_PAD), max(0, ey - REF_PAD)
        x1, y1 = min(img.shape[1], ex + tw + REF_PAD), min(img.shape[0], ey + th + REF_PAD)
        roi = img[y0:y1, x0:x1]
        if roi.shape[0] < th or roi.shape[1] < tw:
            mfaalog.error("[SupportAction] 参照物搜索区小于模板, 无法定位")
            return None
        res = cv2.matchTemplate(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, (mx, my) = cv2.minMaxLoc(res)
        if not np.isfinite(score) or score < TH_REF:
            mfaalog.diagnostic(
                f"[SupportAction] 参照物 {self._ref_name}: score={score:.3f}/{TH_REF:.2f} "
                "定位不可信，跳过当前助战条目，不应用平移"
            )
            return None
        dx, dy = x0 + mx - ex, y0 + my - ey
        mfaalog.diagnostic(f"[SupportAction] 参照物 {self._ref_name}: 实测=({x0 + mx},{y0 + my}) "
                     f"期望=({ex},{ey}) score={score:.3f} -> 框顶点平移=({dx},{dy})")
        return dx, dy

    # ---------- 英灵匹配 ----------
    def _match_servant(self, img, face_dir, bx, by, images, support_type):
        # 头像匹配: 裁剪框内头像区域, 用多边形 mask 物理排除覆盖 UI(金星/职介/等级/星级),
        # 再把多边形外像素设为脸部均值(中性化, 不参与相关度), 最后在 servant_face 模板上滑动匹配
        import cv2
        bx, by = int(bx), int(by)
        fxs = [p[0] for p in FACE_POLY]; fys = [p[1] for p in FACE_POLY]
        xmin, xmax, ymin, ymax = min(fxs), max(fxs), min(fys), max(fys)
        poly = img[by + ymin:by + ymax + 1, bx + xmin:bx + xmax + 1]
        if poly.size == 0:
            return False
        cg = cv2.cvtColor(poly, cv2.COLOR_BGR2GRAY)
        mask = self._get_face_mask()
        sel = mask > 0
        face_mean = int(round(float(cg[sel].mean())))
        cg = cg.copy()
        cg[~sel] = face_mean   # 多边形外(特殊覆盖UI)设为脸部均值, 中性化排除
        for f in images:
            tpl = _imread(os.path.join(face_dir, f), gray=True)
            if tpl is None or cg.shape[0] > tpl.shape[0] or cg.shape[1] > tpl.shape[1]:
                continue
            s = float(cv2.matchTemplate(tpl, cg, cv2.TM_CCOEFF_NORMED).max())
            mfaalog.diagnostic(f"[SupportAction] 英灵头像 {f}: score={s:.3f}")
            if s >= TH_FACE:
                self._matched_face_evidence = (s, f)
                return True
        return False

    # ---------- 礼装匹配 ----------
    def _match_ce(self, img, ce_dir, bx, by, ce_name, anchor, ce_sub="", ce_size=CE_ROI):
        import cv2
        bx, by = int(bx), int(by)
        if ce_name in EMPTY_CE:
            mfaalog.diagnostic("[SupportAction] 礼装为空(跳过匹配)")
            return True
        equip_id = getattr(self, "_ce_ids", {}).get(ce_name)
        tpl_paths = _ce_template_paths(ce_dir, ce_name, ce_sub, equip_id)
        tpl = next((image for path in tpl_paths
                    if (image := _imread(path, gray=True)) is not None), None)
        if tpl is None:
            mfaalog.warning(f"[SupportAction] 礼装模板不存在: {ce_name} "
                            f"(目录 {'/'.join(os.path.dirname(path) for path in tpl_paths)})")
            return False
        # 锚点是礼装框左上角: 直接以锚点为左上角取 160x50 窗口(与礼装模板同量级),
        # 固定尺度整卡匹配: 模板与卡面显示同尺寸, 不做缩放搜索
        roi = _roi(img, bx, by, anchor[0], anchor[1], ce_size[0], ce_size[1])
        if roi is None:
            return False
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        if tpl.shape[0] > roi_gray.shape[0] or tpl.shape[1] > roi_gray.shape[1]:
            mfaalog.warning(f"[SupportAction] 礼装模板大于匹配窗口: {ce_name} "
                            f"{tpl.shape[1]}x{tpl.shape[0]} > {ce_size[0]}x{ce_size[1]}")
            return False
        _, score, _, (mx, my) = cv2.minMaxLoc(
            cv2.matchTemplate(roi_gray, tpl, cv2.TM_CCOEFF_NORMED))
        if ce_sub == "任意":
            # A star can dominate otherwise almost blank artwork. Either state
            # is allowed, so identity matching must try both available templates.
            for state in CE_SUB_SWAP:
                alternate = next((image for path in _ce_template_paths(ce_dir, ce_name, state, equip_id)
                                  if (image := _imread(path, gray=True)) is not None), None)
                if alternate is not None and alternate.shape[0] <= roi_gray.shape[0] and alternate.shape[1] <= roi_gray.shape[1]:
                    score = max(score, float(cv2.matchTemplate(roi_gray, alternate, cv2.TM_CCOEFF_NORMED).max()))
        if score < TH_CE:
            mfaalog.diagnostic(f"[SupportAction] 礼装 {ce_name}: 整卡={score:.3f}(未过)")
            return False
        if ce_sub == "任意":
            mfaalog.diagnostic(f"[SupportAction] 礼装 {ce_name}: 整卡={score:.3f}，不限制满破状态")
            return True
        # 右下角形态校验: 满破/非满破等仅右下角不同的礼装整卡主体相同得分均高,
        # 在最佳匹配位置取卡面右下角约30x30区域与模板右下角匹配。
        # 该标记很淡, 绝对分数不可靠, 故与同名的另一状态模板(满破<->非满破)比谁更像, 取高者定状态
        h, w = tpl.shape[:2]
        s2 = min(CE_BR_SIZE, h, w)
        card_br = roi_gray[my + h - s2: my + h, mx + w - s2: mx + w]
        tpl_br = tpl[h - s2: h, w - s2: w]
        score_br = float(cv2.matchTemplate(card_br, tpl_br, cv2.TM_CCOEFF_NORMED).max())
        other_sub = CE_SUB_SWAP.get(ce_sub)
        other = next((image for path in _ce_template_paths(ce_dir, ce_name, other_sub, equip_id)
                      if (image := _imread(path, gray=True)) is not None), None) if other_sub else None
        if other is not None:
            if (other.shape[1], other.shape[0]) != (w, h):
                other = cv2.resize(other, (w, h), interpolation=cv2.INTER_AREA)
            other_br = other[h - s2: h, w - s2: w]
            # The two full templates differ only on the star. Compare those
            # pixels directly: NCC gives spurious 1.0 on a constant white patch.
            mask = np.abs(tpl_br.astype(np.int16) - other_br.astype(np.int16)) > 3
            if not np.any(mask):
                mfaalog.warning(f"[SupportAction] 礼装 {ce_name} 的两种状态模板没有可区分的星标，无法确认满破状态")
                return False
            observed = card_br.astype(np.float32)
            error_wanted = float(np.square(observed - tpl_br.astype(np.float32))[mask].mean())
            error_other = float(np.square(observed - other_br.astype(np.float32))[mask].mean())
            mfaalog.diagnostic(f"[SupportAction] 礼装 {ce_name}: 整卡={score:.3f} "
                         f"{ce_sub}星标误差={error_wanted:.1f} vs {other_sub}={error_other:.1f}")
            return error_wanted < error_other
        mfaalog.diagnostic(f"[SupportAction] 礼装 {ce_name}: 整卡={score:.3f} 右下角={score_br:.3f}")
        if float(tpl_br.std()) <= 1.0:
            mfaalog.warning(f"[SupportAction] 礼装 {ce_name} 缺少另一状态图片，且右下角没有可区分特征，无法确认满破状态")
            return False
        return score_br >= TH_CE_BR

    # ---------- 冠位羁绊判断 ----------
    def _match_bond(self, img, bx, by, bond_opt):
        """羁绊区域与 base/image/skill/{50np,羁绊}.png 做掩膜灰度匹配。
        绿幕像素不参与相关系数计算，避免礼装背景影响效果分类。
        模板是纯图案(不含文字), 多服通用, 故固定取 base 包而非当前 pkg
        两张模板分别打分，较高分须严格大于 0.40 且两者不相等，才确认效果；
        低分或同分视为未识别到，跳过当前条目。不限效果时保持跳过检查。"""
        import cv2
        if bond_opt not in BOND_TEMPLATES:
            return True
        crop = _roi(img, bx, by, BOND_ROI[0], BOND_ROI[1], BOND_ROI[2], BOND_ROI[3])
        if crop is None:
            return False
        g_roi = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        scores = {}
        for opt, name in BOND_TEMPLATES.items():
            t_bgr = _imread(os.path.join(_image_dir("base", "skill"), name))
            if t_bgr is None:
                return False
            # 以绿幕生成掩膜；不能将绿幕涂白后当成有效特征参与匹配。
            b = t_bgr[:, :, 0].astype(int)
            g = t_bgr[:, :, 1].astype(int)
            r = t_bgr[:, :, 2].astype(int)
            green = (g > 100) & (g > b + 30) & (g > r + 30)
            t = cv2.cvtColor(t_bgr, cv2.COLOR_BGR2GRAY)
            mask = (~green).astype(np.uint8) * 255
            if (t.shape[0] > g_roi.shape[0] or t.shape[1] > g_roi.shape[1]
                    or not np.any(mask)):
                return False
            result = cv2.matchTemplate(g_roi, t, cv2.TM_CCOEFF_NORMED, mask=mask)
            # 平坦背景上的局部方差可能为零，仅保留可计算的候选位置。
            finite = result[np.isfinite(result)]
            scores[opt] = float(finite.max()) if finite.size else float("nan")
        best = max(scores, key=scores.get)
        if (not all(np.isfinite(score) for score in scores.values())
                or scores[best] <= TH_BOND
                or scores["50np"] == scores["original"]):
            mfaalog.diagnostic(
                "[SupportAction] 未能确认羁绊礼装效果图标：" +
                " ".join(f"{k}={v:.3f}" for k, v in scores.items()) +
                f"，需较高分大于 {TH_BOND:.2f} 且两者分数不同，跳过当前助战条目"
            )
            return False
        mfaalog.diagnostic(f"[SupportAction] 羁绊: " +
                     " ".join(f"{k}={v:.3f}" for k, v in scores.items()) +
                     f" -> {best} (期望 {bond_opt})")
        return bond_opt == best

    # ---------- 技能等级匹配 ----------
    def _match_skill(self, img, bx, by, box, expect):
        """expect>0 时用 OCR 识别技能数字, 识别等级 >= 期望视为匹配; expect=0(不要求)直接通过
        box = 数字方框 (x,y,w,h), 相对 YOLO 框左上角"""
        if expect <= 0:
            return True
        roi = _roi(img, bx, by, box[0], box[1], box[2], box[3])
        if roi is None:
            return False
        got = SupportAction._ocr_skill_text(roi)
        ok = got is not None and int(got) >= expect
        mfaalog.diagnostic(f"[SupportAction] 技能({box}) 期望>={expect} "
                     f"OCR识别={got or '(无数字)'} -> {'OK' if ok else 'NO'}")
        return ok

    # ---------- 宝具等级匹配 ----------
    def _match_np(self, img, np_dir, bx, by, expect):
        import cv2
        if expect <= 0:
            return True
        if not os.path.isdir(np_dir):
            mfaalog.error(f"[SupportAction] 宝具模板目录不存在: {np_dir}")
            return False
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
            mfaalog.diagnostic("[SupportAction] 宝具等级无命中")
            return False
        ok = best[1] >= expect
        mfaalog.diagnostic(f"[SupportAction] 宝具 期望>={expect} 识别=lv{best[1]}({best[2]}) "
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
            from utils.onnx_session import create_session
            sess = create_session(OCR_REC_ONNX)
            with open(OCR_REC_KEYS, "r", encoding="utf-8") as f:
                keys = [ln.strip("\n") for ln in f.readlines()]
            SupportAction._ocr_sess, SupportAction._ocr_keys = sess, keys
            mfaalog.diagnostic(f"[SupportAction] OCR rec.onnx 加载成功 ({len(keys)} 类)")
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
        """返回当前等级；没有等级或无法解析时返回 None，并保留 OCR 原文。"""
        import cv2
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)[2:22, :]   # 去掉上下横线
        if g.size == 0:
            return None
        t = SupportAction._ocr_run(g, 3)
        if not t:
            return None
        # 排序可能让头像上方显示其他属性，不能把攻击力/生命值当作等级。
        if re.match(r"^(?:攻击|攻撃|生命|ATK|HP)", t.strip(), re.IGNORECASE):
            mfaalog.diagnostic(f"[SupportAction] 从者等级区域显示其他属性，原文={t!r}")
            return None
        m = re.search(r"(\d{1,3})/(\d{1,3})", t)
        if m:
            return int(m.group(1))
        m = re.search(r"(\d{1,3})7(\d{1,3})", t)   # 斜杠偶被识别为 7
        if m:
            cur, mx = int(m.group(1)), int(m.group(2))
            if 1 <= cur <= 130 and 1 <= mx <= 130:
                return cur
        mfaalog.diagnostic(f"[SupportAction] 从者等级区域 OCR 无法解析，原文={t!r}")
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
        got = SupportAction._ocr_level_text(roi) if roi is not None else None
        if got is None and not getattr(self, "_level_recognition_failed", False):
            self._level_recognition_failed = True
            mfaalog.warning(
                f"[SupportAction] 助战从者等级识别失败（要求至少{expect}级），当前条目无法确认等级，正在继续查找。"
                "请检查游戏“助战选择”界面是否已开启从者等级显示，确认头像上方显示“90/90”这类等级。"
            )
        ok = got is not None and got >= expect
        mfaalog.diagnostic(f"[SupportAction] 英灵等级 期望>={expect} OCR识别={got if got is not None else '(无)'} -> {'OK' if ok else 'NO'}")
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
        mfaalog.diagnostic(f"[SupportAction] 视图判定 主动={scores['active']:.3f} 被动={scores['passive']:.3f}")
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

    # ---------- 刷新后连接中检测 ----------
    def _is_connecting(self, controller):
        """刷新后判断是否仍在连接: (1158,623)-(1259,709) 区域白像素占比 >= 10%(WHITE_RATIO) 表示连接中;
        截图失败视为已连接完成(避免卡死), 交由后续识别流程处理"""
        import cv2
        img = self._shot_base(controller)
        if img is None:
            return False
        x0, y0 = CONNECT_ROI[0], CONNECT_ROI[1]
        x1 = min(img.shape[1], x0 + CONNECT_ROI[2])
        y1 = min(img.shape[0], y0 + CONNECT_ROI[3])
        if x1 <= x0 or y1 <= y0:
            return False
        gray = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        white = float((gray >= TH_WHITE).mean())
        mfaalog.diagnostic(f"[SupportAction] 刷新连接检测: 白像素占比={white:.2f}")
        return white >= WHITE_RATIO

    # ---------- 主流程 ----------
    def _identify_unrestricted_support(self, image, face_dir, bx, by):
        """在已点击条目的留存帧上补识别身份；不改变礼装筛选或再次操作列表。"""
        import cv2
        xs, ys = zip(*FACE_POLY)
        crop = image[int(by) + min(ys):int(by) + max(ys) + 1,
                     int(bx) + min(xs):int(bx) + max(xs) + 1]
        mask = self._get_face_mask() > 0
        if crop.shape[:2] != mask.shape:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray[~mask] = int(round(float(gray[mask].mean())))
        candidates = {}
        for servant in self._get_servant_map().values():
            sid = canonical_servant_id(servant.get("id"))
            if sid:
                candidates.setdefault(sid, set()).update(servant.get("images") or ())
        ranked, scores = [], {}
        for sid, filenames in candidates.items():
            best = (-1.0, "")
            for filename in sorted(filenames):
                if filename not in scores:
                    template = _imread(os.path.join(face_dir, filename), gray=True)
                    score = (-1.0 if template is None or any(
                        size > limit for size, limit in zip(gray.shape, template.shape)
                    ) else float(cv2.matchTemplate(template, gray, cv2.TM_CCOEFF_NORMED).max()))
                    scores[filename] = score if np.isfinite(score) else -1.0
                if scores[filename] > best[0]:
                    best = (scores[filename], filename)
            if best[1]:
                ranked.append((best[0], sid, best[1]))
        ranked.sort(reverse=True)
        if not ranked:
            return None
        score, sid, template = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        mfaalog.diagnostic(f"[SupportAction] 仅礼装助战身份复核：候选={sid} "
                          f"score={score:.4f}/{TH_FACE:.2f} second={second:.4f} "
                          f"margin={score - second:.4f}/{TH_UNRESTRICTED_FACE_MARGIN:.2f} "
                          f"template={template}")
        if score < TH_FACE or score - second < TH_UNRESTRICTED_FACE_MARGIN:
            return None
        self._matched_face_evidence = (score, template)
        return sid

    def _record_selected_support(self, root, token, servant_id, *, image=None,
                                 face_dir=None, bx=0, by=0):
        if not token:
            return
        if not servant_id and sessions.awaiting_continuous_support(root, token):
            self._matched_face_evidence = (0.0, "")
            try:
                if image is not None and face_dir is not None:
                    servant_id = self._identify_unrestricted_support(image, face_dir, bx, by)
            except Exception as exc:
                # 这是选择成功后的辅助识别；缺图或低分不能撤销自有从者的证据。
                mfaalog.diagnostic(f"[SupportAction] 本次助战身份暂未确认：{exc}")
        score, template = getattr(self, "_matched_face_evidence", (0.0, ""))
        snapshot = sessions.support_selected(
            root, token, canonical_servant_id(servant_id),
            confidence=Confidence(score, "support_selection"), matched_template=template,
        )
        if snapshot is not None:
            support = next(s for s in snapshot.slots if s.is_support)
            mfaalog.info(f"[初始编队] 连续出击沿用自有编队，助战位={support.slot} "
                         f"本次助战={support.servant_id or '未识别，仅保留自有从者身份'} "
                         f"头像匹配={score:.4f}")

    def _select_fallback(self, context, controller, root, token):
        """撤销职介筛选并复用首个助战入口，不在本子任务中执行编队。"""
        if context.tasker.stopping:
            return CustomAction.RunResult(success=False)
        mfaalog.info("[SupportAction] 刷新10次仍未找到目标，改用首个助战并跳过 Chaldea 配置")
        detail = context.run_task(
            "助战兜底-选择首个助战",
            pipeline_override={"进本-选择助战": {"next": [], "on_error": []}},
        )
        if context.tasker.stopping or detail is None or not detail.status.succeeded:
            mfaalog.error("[SupportAction] 首个助战选择失败，停止任务")
            return CustomAction.RunResult(success=False)
        sessions.set_support_fallback(root, token, True)
        return CustomAction.RunResult(success=True)

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        # 每次助战查找独立记录；实时提醒只输出一次，避免滑动刷新时刷屏。
        self._level_recognition_failed = False
        result = self._run_search(context, argv)
        if not result.success and self._level_recognition_failed and not context.tasker.stopping:
            mfaalog.warning(
                "[SupportAction] 本次助战任务失败，查找过程中曾出现从者等级识别失败。"
                "请检查游戏“助战选择”界面是否已开启从者等级显示，确认头像上方显示“90/90”这类等级后重试。"
            )
        return result

    def _run_search(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            param = {}
            if argv.custom_action_param:
                try:
                    param = json.loads(argv.custom_action_param)
                except json.JSONDecodeError:
                    param = {}
            source_mode = param.get("source_mode")
            node = context.get_node_data(argv.node_name)
            attach = node.get("attach") or {}
            max_swipes_before_refresh = _max_swipes_before_refresh(attach)
            fallback_enabled = attach.get("fallback_after_refresh") is True
            root, token = None, ""
            try:
                root, token = context_session(context)
                if not sessions.valid(root, token):
                    root, token = None, ""
            except (AttributeError, TypeError, ValueError):
                root, token = None, ""  # 独立助战/BBC 不创建原生会话。
            if fallback_enabled and not token:
                mfaalog.error("[SupportAction] 助战兜底需要有效的原生战斗会话")
                return CustomAction.RunResult(success=False)
            skip_servant = False
            self._ce_ids = {}
            if source_mode == "chaldea":
                if attach.get("chaldea_enabled") is not True:
                    mfaalog.error(
                        "[SupportAction] 助战方式已选择使用编辑器/Chaldea 配置，但尚未开启方案导入。"
                        "请开启战斗选项中的“使用编辑器/chaldea配置”，并填写“编辑器/chaldea方案导入”；"
                        "如需在 MAA 中直接设置助战，请将助战方式改为“自定义助战”。"
                    )
                    return CustomAction.RunResult(success=False)
                source = str(attach.get("chaldea_import_source") or "").strip()
                if not source:
                    mfaalog.error(
                        "[SupportAction] 已开启使用编辑器/Chaldea 配置，但没有填写队伍方案。"
                        "请在“编辑器/chaldea方案导入”中填写分享链接、队伍 ID、关卡 ID 或选择本地队伍文件。"
                    )
                    return CustomAction.RunResult(success=False)
                share_data, _, _ = fetch_share_data(source)
                try:
                    criteria = build_support_criteria(share_data, self._get_servant_map(), resolve_name=_support_ce_name)
                except ValueError as exc:
                    mfaalog.error(f"[SupportAction] Chaldea 助战配置无效: {exc}")
                    return CustomAction.RunResult(success=False)
                support_type = criteria["support_type"]
                class_name = criteria["class_name"]
                skip_servant = class_name == "any"
                class_all = "def"
                servant_id = criteria["servant_id"]
                ce_bond = criteria["ce_bond"]
                groups = criteria.get("ce_groups")
                if groups is None:
                    groups = [{"slot": spec["slot"], "candidates": [spec]}
                              for spec in criteria["ce_specs"]]
                identities = {}
                for group in groups:
                    for spec in group["candidates"]:
                        identities.setdefault(spec["name"], set()).add(spec.get("id"))
                def ce_key(spec):
                    return f"support-ce:{spec['id']}" if len(identities[spec["name"]]) > 1 and spec.get("id") else spec["name"]
                self._ce_ids = {ce_key(spec): spec["id"] for group in groups
                                for spec in group["candidates"] if spec.get("id")}
                ce_targets = [
                    ([(ce_key(spec), spec["status"]) for spec in group["candidates"]],
                     CE_ANCHOR_GRAND[group["slot"] - 1]
                     if support_type == "grand" else CE_ANCHOR_NORMAL)
                    for group in groups
                ]
                active = criteria["active"]
                passive = criteria["passive"]
                np_level = criteria["np_level"]
                level = criteria["level"]
            else:
                support_type = param.get("support_type", "normal")
                class_name = str(attach["class_name"]).strip()   # 玩家点击的职介
                class_all = str(attach["class_all"]).strip()     # all=ALL 阶, def=按职介
                skip_servant = class_name == "any"
                servant_id = "" if skip_servant else str(attach["servant"]).strip()
                # 新配置逐格保存状态；旧配置仍兼容共用的 礼装状态 字段。
                ce_status = str(attach["礼装状态"]).strip()
                if support_type == "grand":
                    ce_bond = str(attach["ce_bond"]).strip()
                    slots = [(f"冠位助战-{i}号礼装", anchor) for i, anchor in enumerate(CE_ANCHOR_GRAND, 1)]
                else:
                    ce_bond = "any"
                    slots = [("普通助战-礼装", CE_ANCHOR_NORMAL)]
                ce_targets = []
                for slot, anchor in slots:
                    status = str(attach.get(f"{slot}-状态", ce_status)).strip()
                    ce_targets.append(([(name, status) for name in
                                        _custom_ce_candidates(attach, f"{slot}-{status}")], anchor))
                # 技能等级: 0=不要求。
                active = [0] * 3 if skip_servant else [int(attach[f"skill_active_{i}"]) for i in range(1, 4)]
                passive = [0] * 5 if skip_servant else [int(attach[f"skill_passive_{i}"]) for i in range(1, 6)]
                np_level = 0 if skip_servant else int(attach["np_level"])
                level = 0 if skip_servant else int(attach["level"])

            passive_need = any(v > 0 for v in passive)

            ce_names = ["|".join(name for name, _ in choices) for choices, _ in ce_targets]
            mfaalog.info(f"[SupportAction] 助战类型={support_type} 职介={class_name or '(未选)'} "
                         f"英灵={servant_id or ('(不要求)' if skip_servant else '(未选)')} "
                         f"礼装={'/'.join(ce_names) if ce_names else '(空)'} 主动={active} 被动={passive} "
                         f"宝具={np_level} 等级={level} 每轮最多滑动={max_swipes_before_refresh}")

            pkg = _resolve_resource_package(context)
            base_dir = _image_dir(pkg)
            # 英灵头像/礼装固定放 base, 不用 pkg 区分
            face_dir = _image_dir("base", "servant_face")
            ce_dir = _image_dir("base", "lizhuang")
            for choices, _ in ce_targets:
                for ce_name, status in choices:
                    if ce_name in EMPTY_CE or (source_mode != "chaldea" and not ce_name.startswith("support-ce:")):
                        continue
                    if not any(os.path.isfile(path)
                               for path in _ce_template_paths(ce_dir, ce_name, status, self._ce_ids.get(ce_name))):
                        display_name = os.path.splitext(ce_name)[0]
                        if ce_name.startswith("support-ce:"):
                            eid = ce_name.split(":", 1)[1]
                            display_name = f"{_ce_builtin_names().get(eid, '礼装')}（ID {eid}）"
                        mfaalog.error(
                            f"[SupportAction] 找不到礼装「{display_name}」的{status}助战图片，无法按此条件选择助战。"
                            "请打开编队编辑器，在“选择概念礼装”中点击“安装礼装扩展”，"
                            "选择包含该礼装的扩展包 ZIP，安装后重试；也可重新选择已有识别图片的礼装。"
                        )
                        return CustomAction.RunResult(success=False)
            np_dir = os.path.join(base_dir, "nplevel")   # 宝具模板按 pkg 动态选择(base/cn)
            skill_dir = os.path.join(base_dir, "skill")   # 视图判断模板(主动/被动), 按 pkg 动态选择
            mfaalog.diagnostic(f"[SupportAction] 素材根: {base_dir} 宝具目录: {np_dir}")
            # 参照物: {资源包}/image/support/助战编入确认.png, 复用已有资源做框内坐标重新定位
            self._ref_dir = _image_dir(pkg, "support")
            self._ref_name = "助战编入确认.png"

            srv = None
            if not skip_servant:
                if not servant_id:
                    mfaalog.error("[SupportAction] 未选择英灵")
                    return CustomAction.RunResult(success=False)

                smap = self._get_servant_map()
                srv = smap.get(servant_id)
                if not srv or not srv.get("images"):
                    mfaalog.error(f"[SupportAction] servant_list 无该英灵: {servant_id}")
                    return CustomAction.RunResult(success=False)

            detector = self._get_detector()

            controller = context.tasker.controller
            self._init_scale(controller)

            # 不要求英灵时沿用游戏当前页签；指定英灵时才按职介筛选设置切换。
            if skip_servant:
                mfaalog.info("[SupportAction] 不要求英灵，保留当前职介页签")
            elif class_name:
                if "all" in class_all:
                    tab = CLASS_TABS["ALL"]    # ALL 阶: 不按具体职介
                else:
                    tab = CLASS_TABS.get(class_name, CLASS_TABS["OTHER"])
                for _ in range(3):
                    self._tap(controller, tab[0], tab[1])
                    time.sleep(0.5)
                mfaalog.info(f"[SupportAction] 点击职介筛选: {class_name} @ {tab}")

            # 识别一次: 截图->YOLO检测->逐个框判定; 命中点击条目并返回 True(未命中 False)
            def try_match():
                if context.tasker.stopping:
                    return False
                img = self._shot_base(controller)
                if img is None:
                    mfaalog.error("[SupportAction] 截图失败")
                    return False
                boxes = detector.detect(img)
                mfaalog.diagnostic(f"[SupportAction] 检测到 {len(boxes)} 个助战条目")
                if not boxes:
                    mfaalog.error("[SupportAction] 未检测到助战条目")
                    return False

                for (bx, by, bx2, by2, conf) in boxes:
                    mfaalog.diagnostic(f"[SupportAction] === 判定条目 框=({bx},{by})-({bx2},{by2}) conf={conf:.2f} ===")
                    # 框内参照物重定位: 平移框顶点到标定坐标系, 消除 YOLO 框偏移
                    shift = self._ref_shift(img, bx, by)
                    if shift is None:
                        continue
                    dx, dy = shift
                    bx, by, bx2, by2 = bx + dx, by + dy, bx2 + dx, by2 + dy
                    mfaalog.diagnostic(f"[SupportAction] 重定位后 框=({bx},{by})-({bx2},{by2})")
                    self._matched_face_evidence = (0.0, "")
                    if not skip_servant and not self._match_servant(img, face_dir, bx, by, srv["images"], support_type):
                        continue
                    if not all(any(self._match_ce(img, ce_dir, bx, by, name, anc, sub)
                                   for name, sub in choices)
                               for choices, anc in ce_targets):
                        continue
                    # 冠位助战羁绊判断(50np/original/any)
                    if support_type == "grand" and not self._match_bond(img, bx, by, ce_bond):
                        continue
                    # 主动视图(主动技能+宝具+英灵等级): 单帧判定, 失败跳过该条目
                    if not skip_servant and not self._check_active_view(img, bx, by, active, np_dir, np_level, level):
                        continue

                    # 主动/宝具/等级 均满足, 剩被动
                    if not passive_need:
                        cx, cy = (bx + bx2) // 2, (by + by2) // 2
                        if self._tap(controller, cx, cy) is False:
                            raise ValueError("没有成功点击选中的助战，无法确认本次借用的从者。请检查模拟器连接后重新运行任务。")
                        self._record_selected_support(root, token, servant_id if not skip_servant else None,
                                                      image=img, face_dir=face_dir, bx=bx, by=by)
                        mfaalog.info(f"[SupportAction] 点击条目 ({cx},{cy})")
                        return True

                    # 需要被动: 点击1次切到被动视图, 间隔0.5s稳定后再单帧识别
                    self._tap(controller, *VIEW_SWITCH_POS)
                    time.sleep(0.5)
                    img = self._shot_base(controller)
                    if img is None:
                        return False
                    passive_ok = self._match_passive(img, bx, by, passive)

                    # 被动识别结束, 无论是否点击条目, 点击2次(846,127)切回主动视图, 每次间隔0.5s
                    self._tap(controller, *VIEW_SWITCH_POS)
                    time.sleep(0.5)
                    self._tap(controller, *VIEW_SWITCH_POS)
                    time.sleep(0.5)

                    if not passive_ok:
                        continue
                    # 切回主动页后重新定位和核对头像，不能把被动切换前的旧坐标
                    # 当作本次实际选中的助战身份。只要求礼装的选择仍不补造身份。
                    current = self._shot_base(controller)
                    if current is None:
                        return False
                    shift = self._ref_shift(current, bx, by)
                    if shift is None:
                        continue
                    dx, dy = shift
                    bx, by, bx2, by2 = bx + dx, by + dy, bx2 + dx, by2 + dy
                    self._matched_face_evidence = (0.0, "")
                    if not skip_servant and not self._match_servant(
                            current, face_dir, bx, by, srv["images"], support_type):
                        continue
                    cx, cy = (bx + bx2) // 2, (by + by2) // 2
                    if self._tap(controller, cx, cy) is False:
                        raise ValueError("没有成功点击选中的助战，无法确认本次借用的从者。请检查模拟器连接后重新运行任务。")
                    self._record_selected_support(root, token, servant_id if not skip_servant else None,
                                                  image=current, face_dir=face_dir, bx=bx, by=by)
                    mfaalog.info(f"[SupportAction] 点击条目 ({cx},{cy})")
                    return True

                return False

            # 首次识别(主动视图)
            if try_match():
                return CustomAction.RunResult(success=True)

            # 每轮先查顶部再滑动查完整列表；刷新10次后的列表也完整查找后才兜底。
            mfaalog.info("[SupportAction] 首次识别无匹配, 进入滑动/刷新循环")
            swipe_count = 0
            refresh_count = 0
            while True:
                # 监听停止信号: MXU 点停止后立即中断查找流程
                if context.tasker.stopping:
                    mfaalog.info("[SupportAction] 检测到任务停止信号, 中断助战查找")
                    return CustomAction.RunResult(success=False)
                self._swipe(controller, *SWIPE_START, *SWIPE_END, SWIPE_DURATION)
                swipe_count += 1
                time.sleep(SWIPE_SETTLE)   # 等列表惯性滚动结束, 画面稳定后再识别
                mfaalog.info(f"[SupportAction] 第 {swipe_count} 次滑动, 重新识别")
                if try_match():
                    return CustomAction.RunResult(success=True)
                if swipe_count >= max_swipes_before_refresh:
                    swipe_count = 0
                    if fallback_enabled and refresh_count >= FALLBACK_REFRESH_LIMIT:
                        return self._select_fallback(context, controller, root, token)
                    mfaalog.info(f"[SupportAction] 连续{max_swipes_before_refresh}次滑动未匹配, 执行{REFRESH_TASK}")
                    detail = context.run_task(REFRESH_TASK)
                    if context.tasker.stopping or detail is None or not detail.status.succeeded:
                        mfaalog.error("[SupportAction] 助战刷新失败，停止查找")
                        return CustomAction.RunResult(success=False)
                    refresh_count += 1
                    # 刷新后持续检测连接状态: 连接区白像素占比>=10%(WHITE_RATIO) 时不能继续检测, 等待其消失
                    mfaalog.info("[SupportAction] 等待刷新连接完成...")
                    while self._is_connecting(controller):
                        if context.tasker.stopping:
                            mfaalog.info("[SupportAction] 检测到任务停止信号, 中断助战查找")
                            return CustomAction.RunResult(success=False)
                        time.sleep(0.5)
                    mfaalog.info("[SupportAction] 刷新完成, 继续检测")
                    if try_match():
                        return CustomAction.RunResult(success=True)
        except Exception as e:
            import traceback
            mfaalog.error(f"[SupportAction] 异常: {e}\n{traceback.format_exc()}")
            return CustomAction.RunResult(success=False)
