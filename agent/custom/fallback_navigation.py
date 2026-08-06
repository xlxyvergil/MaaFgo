# -*- coding: utf-8 -*-
"""
保底导航模块 - 坐标反推方案(方案B)失败时的兜底逻辑

从原 general_navigation_action.py 提取, 保留完整旧逻辑(缩放+归位+纯滑动+全模板匹配),
不再注册为 CustomAction, 由方案B action(coordinate_navigation)在极端意外情况下
代码内直接调用。返回 bool(是否成功找到并点击目标), 由调用方包装成 RunResult。

保留的旧行为(与方案A完全一致):
1. 读取章节/关卡参数 + 资源包类型
2. 双指缩放到最大 + 左上角归位(顶部起点)
3. 循环(最多 MAX_ROUNDS 屏): 截图 -> YOLO检测 -> 对比识别 -> 目标在屏点击 / 无则向下滑动
4. 特殊关卡(被UI完全遮挡): 直接滑到底, 在指定位置放大后再走 YOLO+匹配识别
5. 到底判定: 连续两屏识别到的关卡集合相同 -> 到底, 导航失败

所有检测参数/手势参数与旧版一致, 不在此改动。
"""
import os
import time
import json
import cv2
import numpy as np
from maa.context import Context
import mfaalog

# 检测参数
PAD = 3                   # YOLO 框四周外扩像素(px): 吸收框位置抖动(±1-2px), 纯名称条内容匹配旧素材
SIFT_TH = 0.30            # SIFT 匹配判定阈值

# 坐标表辅助: coords.json 按 x/y 过滤 SIFT 候选, 反推视角偏移
SCREEN_H = 720            # 屏幕高度(统一 1280 宽坐标系, 720p)
COORDS_X_TOL = 60         # 候选x容差(px)
COORDS_Y_MARGIN = 80      # 候选y容差(px)

# 滑动与循环参数
SWIPE_DIST = 100          # 单次滑动距离(px)
SWIPE_DURATION = 300
MAX_ROUNDS = 30           # 最大滑动屏数
EMPTY_SCREEN_LIMIT = 3    # 连续空屏判定到底的阈值

# 手势落点(避开关卡元素, 避免误点击关卡)
ZOOM_FINGER_A = (72, 110)      # 双指捏合缩小: 指1落点(左上)
ZOOM_FINGER_B = (1178, 546)    # 双指捏合缩小: 指2落点(右下)
SWIPE_START = (46, 310)        # 上下滑动起始点(上滑=看下方)
PULL_SWIPE_START = (670, 666)  # 特殊关卡向右微调滑动起始点

# 缩放与归位参数
ZOOM_ROUNDS = 3           # 双指捏合缩小轮数
HOME_SWIPE_START = (46, 310)    # 归位滑动起点
HOME_SWIPE_END = (187, 451)     # 归位滑动终点(相对位移 141,141)
HOME_SWIPE_ROUNDS = 2           # 归位滑动轮数
HOME_RESET_ROUNDS = 2           # 二次置顶归位轮数
ZOOM_PULL_UP = 50         # 特殊关卡放大后向上微调距离(px)
ZOOM_PULL_RIGHT = 300     # 特殊关卡放大后向右微调总距离(px)
ZOOM_PULL_DURATION = 800  # 微调滑动持续时间(ms)
ZOOM_PULL_STEP = 100      # 右滑分段单次距离(px)
ZOOM_PULL_STEPS = 3       # 右滑分段次数


def resolve_quest_dir(root_dir, resource_package, template_path):
    """从运行时 template(map/{英文}/{关卡}.png) 推导导航素材目录
    素材直接放章节文件夹: resource/{pkg}/image/map/{英文}/ (与关卡选择模板同目录)"""
    pkg = resource_package if resource_package in ("cn", "jp") else "base"
    folder = os.path.dirname(template_path).replace("\\", "/").strip("/")
    return os.path.join(root_dir, "resource", pkg, "image", folder)


def load_quest_templates(quest_dir):
    """加载素材目录下所有名称条截图 -> {关卡名: BGR图}"""
    templates = {}
    if not os.path.isdir(quest_dir):
        return templates
    for f in sorted(os.listdir(quest_dir)):
        if f.endswith(".png") and f != "special.json":
            name = f[:-4]
            img = cv2.imdecode(np.fromfile(os.path.join(quest_dir, f), dtype=np.uint8),
                               cv2.IMREAD_COLOR)
            if img is not None:
                templates[name] = img
    clear_sift_cache()   # 素材重载后清空模板描述符缓存
    return templates


def load_special_config(path):
    """加载全局特殊关卡映射(special.json), 不存在返回空 dict"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fp:
                return json.load(fp)
        except Exception as e:
            mfaalog.warning(f"[导航] 特殊关卡配置读取失败: {e}")
    return {}


def identify_quest(crop_bgr, templates):
    """对比识别: YOLO框外扩PAD区域 vs 素材库 -> (关卡名, 分数) 或 (None, 0)"""
    if crop_bgr is None or crop_bgr.size == 0 or not templates:
        return None, 0.0
    best_name, best_score = None, 0.0
    for name, tpl in templates.items():
        s = match_sift(crop_bgr, tpl)
        if s > best_score:
            best_score, best_name = s, name
    if best_name is not None and best_score >= SIFT_TH:
        return best_name, best_score
    return None, best_score


# ---- SIFT 性能优化: 复用单例 SIFT/BFMatcher + 模板描述符缓存 ----
_SIFT = None
_BF = None
_TEMPLATE_CACHE = {}


def _get_sift():
    global _SIFT
    if _SIFT is None:
        try:
            _SIFT = cv2.SIFT_create()
        except Exception:
            _SIFT = False      # 环境不支持 SIFT: 标记不可用, 避免每次重试
    return _SIFT


def _get_bf():
    global _BF
    if _BF is None:
        _BF = cv2.BFMatcher(cv2.NORM_L2)
    return _BF


def _template_features(template_bgr):
    """模板 (灰度图, 关键点, 描述符), 带缓存"""
    key = id(template_bgr)
    hit = _TEMPLATE_CACHE.get(key)
    if hit is not None and hit[0] is template_bgr:
        return hit[1]
    tb = template_bgr
    if tb.ndim == 3 and tb.shape[2] == 4:
        tb = tb[:, :, :3]      # 丢弃 alpha 通道, 兼容 Maa screencap
    tg = cv2.cvtColor(tb, cv2.COLOR_BGR2GRAY)
    tk, td = _get_sift().detectAndCompute(tg, None)
    entry = (tg, tk, td if td is not None else None)
    _TEMPLATE_CACHE[key] = (template_bgr, entry)
    return entry


def clear_sift_cache():
    """清空模板描述符缓存"""
    _TEMPLATE_CACHE.clear()


def _crop_desc(crop_bgr):
    """裁剪框 (灰度图, 描述符): 每帧截图内容都变, 不缓存"""
    if crop_bgr is None or crop_bgr.size == 0:
        return None, None
    cb = crop_bgr
    if cb.ndim == 3 and cb.shape[2] == 4:
        cb = cb[:, :, :3]      # 丢弃 alpha 通道
    sift = _get_sift()
    if not sift:
        return None, None
    cg = cv2.cvtColor(cb, cv2.COLOR_BGR2GRAY)
    _, cd = sift.detectAndCompute(cg, None)
    return cg, cd


def _sift_pair_score(cd, template_bgr):
    """模板特征(缓存) vs 已提取裁剪框描述符 -> 匹配分"""
    _, tk, td = _template_features(template_bgr)
    if cd is None or td is None or len(tk) < 3:
        return 0.0
    matches = _get_bf().knnMatch(td, cd, k=2)
    good = [m for m, n in matches if m.distance < 0.75 * n.distance]
    return float(len(good)) / max(1.0, len(tk))


def match_sift(crop_bgr, template_bgr):
    """SIFT 特征匹配分数: good匹配数(ratio test 0.75) / 模板关键点数"""
    _, cd = _crop_desc(crop_bgr)
    if cd is None:
        return 0.0
    return _sift_pair_score(cd, template_bgr)


def greedy_match_boxes(box_crops, templates):
    """模板主导独占匹配: 每个模板只认领分数最高的一个框(全局最高分优先)
    返回 (assigned, unassigned_idx):
      assigned:      [(box_idx, tpl_idx, score), ...] 按分数从高到低
      unassigned_idx: [box_idx, ...] 未被任何模板认领的框索引"""
    tpl_names = list(templates.keys())
    n, m = len(box_crops), len(tpl_names)
    scores = np.zeros((n, m))
    cds = [_crop_desc(b)[1] for b in box_crops]
    for i in range(n):
        if box_crops[i] is None or box_crops[i].size == 0:
            continue
        for j in range(m):
            scores[i, j] = _sift_pair_score(cds[i], templates[tpl_names[j]])
    used_box = [False] * n
    used_tpl = [False] * m
    assigned = []
    pairs = [(scores[i, j], i, j) for i in range(n) for j in range(m)
             if scores[i, j] >= SIFT_TH]
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    for s, i, j in pairs:
        if used_box[i] or used_tpl[j]:
            continue
        assigned.append((i, j, s))
        used_box[i] = True
        used_tpl[j] = True
    unassigned = [i for i in range(n) if not used_box[i]]
    return assigned, unassigned


def load_quest_coords(root_dir, folder):
    """读取 coords.json(全景坐标表)
    固定只读 agent 附属数据目录 root_dir/agent/utils/{folder}/coords.json
    无坐标表时返回空 dict, 调用方退化为全模板匹配。"""
    path = os.path.join(root_dir, "agent", "utils", folder, "coords.json")
    if not os.path.isfile(path):
        return {}, 0
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        coords = {q: (int(v["x"]), int(v["y"]))
                  for q, v in data.get("quests", {}).items() if "x" in v and "y" in v}
        return coords, int(data.get("map_height", 0))
    except Exception as e:
        mfaalog.warning(f"[导航] coords.json 读取失败: {e}")
    return {}, 0


def match_with_coords(box_infos, box_crops, templates, coords, offset_y):
    """坐标表辅助独占匹配: 每框候选 = x容差 + 全景y在视口±margin 内的模板,
    候选内做全局最高分独占分配。
    返回 (assigned, offset_y):
      assigned:  [(box_idx, 关卡名, score), ...] 按分数从高到低
      offset_y:  由本次识别结果反推的视角顶部全景y(多关取中位数), 未识别则不变"""
    tpl_names = list(templates.keys())
    if not coords:
        assigned, _ = greedy_match_boxes(box_crops, templates)
        return [(bi, tpl_names[bj], s) for bi, bj, s in assigned], offset_y

    n = len(box_crops)
    cand = [[] for _ in range(n)]
    for i in range(n):
        if box_crops[i] is None or box_crops[i].size == 0:
            continue
        cx = box_infos[i][0] + box_infos[i][2] // 2
        cy = box_infos[i][1] + box_infos[i][3] // 2
        for j, name in enumerate(tpl_names):
            t = coords.get(name)
            if t is None:
                continue
            tx, ty = t
            if abs(tx - cx) > COORDS_X_TOL:
                continue
            if offset_y is not None and not (offset_y - COORDS_Y_MARGIN <= ty <= offset_y + SCREEN_H + COORDS_Y_MARGIN):
                continue
            cand[i].append(j)

    scores = np.full((n, len(tpl_names)), -1.0)
    cds = [_crop_desc(b)[1] for b in box_crops]
    for i in range(n):
        for j in cand[i]:
            scores[i, j] = _sift_pair_score(cds[i], templates[tpl_names[j]])
    used_box, used_tpl = [False] * n, [False] * len(tpl_names)
    assigned = []
    pairs = [(scores[i, j], i, j) for i in range(n) for j in range(len(tpl_names))
             if scores[i, j] >= SIFT_TH]
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    for s, i, j in pairs:
        if used_box[i] or used_tpl[j]:
            continue
        assigned.append((i, tpl_names[j], s))
        used_box[i] = True
        used_tpl[j] = True

    if assigned:
        offs = sorted(coords[name][1] - (box_infos[bi][1] + box_infos[bi][3] // 2)
                      for bi, name, _ in assigned)
        offset_y = offs[len(offs) // 2]
    return assigned, offset_y


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


def pinch_zoom_out(controller):
    """双指捏合缩小地图, 缩放到最大可视范围"""
    f1_start, f1_end = ZOOM_FINGER_A, (212, 165)
    f2_start, f2_end = ZOOM_FINGER_B, (1038, 491)
    steps = 8

    d1 = controller.post_touch_down(*f1_start, 0, 1)
    d2 = controller.post_touch_down(*f2_start, 1, 1)
    d1.wait()
    d2.wait()
    try:
        for i in range(1, steps + 1):
            t = i / steps
            p1 = (int(f1_start[0] + (f1_end[0] - f1_start[0]) * t),
                  int(f1_start[1] + (f1_end[1] - f1_start[1]) * t))
            p2 = (int(f2_start[0] + (f2_end[0] - f2_start[0]) * t),
                  int(f2_start[1] + (f2_end[1] - f2_start[1]) * t))
            m1 = controller.post_touch_move(*p1, 0, 1)
            m2 = controller.post_touch_move(*p2, 1, 1)
            m1.wait()
            m2.wait()
            time.sleep(0.02)
    finally:
        u1 = controller.post_touch_up(0)
        u2 = controller.post_touch_up(1)
        u1.wait()
        u2.wait()
    time.sleep(1.5)


def pinch_zoom_in(controller, center, spread=360):
    """以 center 为中心双指外扩放大地图"""
    W, H = 1280, 720
    cx, cy = int(center[0]), int(center[1])
    half = spread // 2
    f1_start, f1_end = (cx - half, cy), (cx - spread, cy)
    f2_start, f2_end = (cx + half, cy), (cx + spread, cy)
    steps = 8

    def clamp(x, y):
        return max(0, min(W - 1, int(x))), max(0, min(H - 1, int(y)))

    d1 = controller.post_touch_down(*clamp(*f1_start), 0, 1)
    d2 = controller.post_touch_down(*clamp(*f2_start), 1, 1)
    d1.wait()
    d2.wait()
    try:
        for i in range(1, steps + 1):
            t = i / steps
            p1 = (f1_start[0] + (f1_end[0] - f1_start[0]) * t,
                  f1_start[1] + (f1_end[1] - f1_start[1]) * t)
            p2 = (f2_start[0] + (f2_end[0] - f2_start[0]) * t,
                  f2_start[1] + (f2_end[1] - f2_start[1]) * t)
            m1 = controller.post_touch_move(*clamp(*p1), 0, 1)
            m2 = controller.post_touch_move(*clamp(*p2), 1, 1)
            m1.wait()
            m2.wait()
            time.sleep(0.02)
    finally:
        u1 = controller.post_touch_up(0)
        u2 = controller.post_touch_up(1)
        u1.wait()
        u2.wait()
    time.sleep(1.5)


def is_paired(nx, ny, nw, nh, tx, ty, tw, th):
    """nametag 与 tag 配对判定
    tag 左边缘落在 nametag 右侧 40% 区域内(且不超出右侧 5px), y 方向有重叠"""
    return nx + nw * 0.4 <= tx <= nx + nw + 5 and ty < ny + nh and ty + th > ny


def pair_nametag_tag(boxes):
    """配对: 返回 (有效 nametag 框列表, 未配对 tag 框列表)"""
    nametags = [(x, y, w, h) for x, y, w, h, cls in boxes if cls == 0]
    tags = [(x, y, w, h) for x, y, w, h, cls in boxes if cls == 1]
    valid = []
    tag_paired = [False] * len(tags)
    for nx, ny, nw, nh in nametags:
        for ti, (tx, ty, tw, th) in enumerate(tags):
            if not is_paired(nx, ny, nw, nh, tx, ty, tw, th):
                continue
            valid.append((nx, ny, nw, nh))
            tag_paired[ti] = True
            break
    unpaired_tags = [t for t, p in zip(tags, tag_paired) if not p]
    return valid, unpaired_tags


class QuestDetector:
    """纯 Python 关卡检测: ultralytics(YOLOv8) 主检测 + 混合重检
    主检测整图 + 孤 nametag/tag 局部 640x640 窗口放大重检, 合并去重后仅返回配对有效框
    """
    IMGSZ = 640
    CONF = 0.5        # 主检测阈值
    LOW_CONF = 0.2    # 低置信兜底阈值
    RC_CONF = 0.1     # 窗口重检阈值
    WIN = 640         # 重检窗口边长

    def __init__(self, model_path):
        from ultralytics import YOLO
        self.model = YOLO(model_path)

    @staticmethod
    def _norm_img(img):
        """规范化输入图像为 BGR uint8 numpy (H,W,3)"""
        if not isinstance(img, np.ndarray):
            if hasattr(img, "to_numpy"):
                img = img.to_numpy()
            elif hasattr(img, "data") and hasattr(img, "width") and hasattr(img, "height"):
                w, h = int(img.width), int(img.height)
                total = len(img.data)
                ch = total // (w * h) if w > 0 and h > 0 else -1
                if ch not in (1, 3, 4) or total != w * h * ch:
                    mfaalog.error(f"[检测] 截图 data 长度 {total} 与 {w}x{h} 不匹配, 返回 None")
                    return None
                img = np.frombuffer(img.data, dtype=np.uint8).reshape(h, w, ch).copy()
            else:
                mfaalog.error(f"[检测] 截图类型 {type(img).__name__} 无法规范化, 返回 None")
                return None
        if img.ndim == 3 and img.shape[2] == 4:
            img = img[:, :, :3]      # 丢弃 alpha 通道
        if img.dtype != np.uint8:
            img = img.astype(np.uint8)
        return img

    def _infer(self, img, conf):
        """整图推理, 返回 [(x1,y1,x2,y2,score,cls)] 原图坐标"""
        img = self._norm_img(img)
        if img is None:
            return []
        r = self.model(img, conf=conf, imgsz=self.IMGSZ, verbose=False)[0]
        res = []
        for b in r.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
            res.append((x1, y1, x2, y2, float(b.conf[0]), int(b.cls[0])))
        return res

    @staticmethod
    def _iou(a, b):
        xx1, yy1 = max(a[0], b[0]), max(a[1], b[1])
        xx2, yy2 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0.0, xx2 - xx1) * max(0.0, yy2 - yy1)
        aa = (a[2] - a[0]) * (a[3] - a[1])
        bb = (b[2] - b[0]) * (b[3] - b[1])
        return inter / (aa + bb - inter + 1e-6)

    def _recheck(self, img, box, direction):
        """孤框 box (x,y,w,h) 以 640x640 窗口放大重检
        direction='right': 窗口偏右覆盖 nametag + 右侧 tag 区
        direction='left':  窗口偏左覆盖 tag + 左侧 nametag 区"""
        x, y, w, h = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        W, H = img.shape[1], img.shape[0]
        cx = x + int(w * 0.7) if direction == "right" else x - 60
        cy = y + h // 2
        x0 = max(0, min(cx - self.WIN // 2, W - self.WIN))
        y0 = max(0, min(cy - self.WIN // 2, H - self.WIN))
        win = img[y0:y0 + self.WIN, x0:x0 + self.WIN]
        if win.size == 0:
            return []
        res = []
        for (x1, y1, x2, y2, c, cl) in self._infer(win, self.RC_CONF):
            x1, y1, x2, y2 = x1 + x0, y1 + y0, x2 + x0, y2 + y0
            if direction == "right" and cl == 1:
                if y1 < y + h and y2 > y and x1 >= x + w * 0.4 - 20 and x1 <= x + w + 60:
                    res.append((x1, y1, x2, y2, c, cl))
            elif direction == "left" and cl == 0:
                if y1 < y + h and y2 > y and x2 >= x - 70 and x2 <= x + w + 40:
                    res.append((x1, y1, x2, y2, c, cl))
        return res

    def detect(self, img):
        """主检测 + 低置信兜底 + 混合重检, 返回 (nametags, tags), 每项 [(x,y,w,h,conf)]
        返回前仅保留配对有效的框(孤 nametag/tag 不计)"""
        hi = self._infer(img, self.CONF)
        lo = self._infer(img, self.LOW_CONF)
        boxes = list(hi)
        for (x1, y1, x2, y2, c, cl) in lo:
            if any(self._iou((x1, y1, x2, y2), (hx1, hy1, hx2, hy2)) > 0.5
                   for (hx1, hy1, hx2, hy2, hc, hcl) in hi):
                continue
            boxes.append((x1, y1, x2, y2, c, cl))
        nametags = [(x1, y1, x2 - x1, y2 - y1, c) for x1, y1, x2, y2, c, cl in boxes if cl == 0]
        tags = [(x1, y1, x2 - x1, y2 - y1, c) for x1, y1, x2, y2, c, cl in boxes if cl == 1]

        def paired_n(nx, ny, nw, nh):
            return any(is_paired(nx, ny, nw, nh, tx, ty, tw, th)
                       for tx, ty, tw, th, tc in tags)

        def paired_t(tx, ty, tw, th):
            return any(is_paired(nx, ny, nw, nh, tx, ty, tw, th)
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
                       if any(is_paired(nx, ny, nw, nh, tx, ty, tw, th) for nx, ny, nw, nh, _ in nametags)]
        paired_nametags = [(nx, ny, nw, nh, c) for nx, ny, nw, nh, c in nametags
                           if any(is_paired(nx, ny, nw, nh, tx, ty, tw, th) for tx, ty, tw, th, _ in paired_tags)]
        return paired_nametags, paired_tags


class FallbackNavigation:
    """保底导航(旧方案A): 缩放+归位+纯滑动+全模板匹配
    不注册为 CustomAction, 由方案B action 在极端意外情况下调用。
    run(context, node_name) -> bool: True=找到目标并点击, False=失败"""

    def run(self, context: Context, node_name: str = "地图坐标导航") -> bool:
        mfaalog.info("=" * 50)
        mfaalog.info("[兜底导航] 进入保底流程（缩放+滑动+YOLO全模板匹配）")
        try:
            # 步骤1: 参数
            node_data = context.get_node_data(node_name)
            if not node_data:
                mfaalog.error("[兜底导航] 无法获取节点数据")
                return False
            attach = node_data.get("attach", {})
            target_quest = attach.get("quests", "")

            sel_node = context.get_node_data("关卡选择") or {}
            template = sel_node.get("template") or (sel_node.get("recognition") or {}).get("param", {}).get("template", "")
            if isinstance(template, list):
                template = template[0] if template else ""
            if not template:
                mfaalog.error("[兜底导航] 无法获取关卡选择 template")
                return False

            resource_config = context.get_node_data("资源包配置")
            resource_package = resource_config.get("attach", {}).get("resource_package", "base") if resource_config else "base"

            if not target_quest:
                mfaalog.error(f"[兜底导航] 参数缺失: quest={target_quest}")
                return False
            mfaalog.info(f"[兜底导航] quest: {target_quest}, template: {template}, pkg: {resource_package}")

            # 步骤2: 素材目录
            AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ROOT_DIR = os.path.dirname(AGENT_DIR)
            quest_dir = resolve_quest_dir(ROOT_DIR, resource_package, template)
            templates = load_quest_templates(quest_dir)
            special = load_special_config(os.path.join(AGENT_DIR, "utils", "special.json"))

            if target_quest not in templates:
                mfaalog.error(f"[兜底导航] 素材库缺少目标关卡名称条截图: {quest_dir}/{target_quest}.png")
                return False
            mfaalog.info(f"[兜底导航] 素材库 {len(templates)} 关, 特殊关卡 {len(special)} 个")

            # 步骤3: 缩放 + 反向滑动归位(回到左上角起点)
            controller = context.tasker.controller
            for i in range(ZOOM_ROUNDS):
                pinch_zoom_out(controller)
                mfaalog.info(f"[兜底导航] 缩放第{i + 1}轮完成")
                prevent_touch(context)
            time.sleep(1.5)
            for i in range(HOME_SWIPE_ROUNDS):
                controller.post_swipe(*HOME_SWIPE_START, *HOME_SWIPE_END, SWIPE_DURATION).wait()
                time.sleep(1.5)
                prevent_touch(context)
            time.sleep(1.5)
            for i in range(HOME_RESET_ROUNDS):
                controller.post_swipe(*SWIPE_START, SWIPE_START[0], SWIPE_START[1] + SWIPE_DIST, SWIPE_DURATION).wait()
                time.sleep(1.5)
                prevent_touch(context)

            # 步骤4: 特殊关卡: 目标在 special 配置中 -> 直接滑到底, 在指定位置放大后再走 YOLO+匹配识别
            if target_quest in special:
                mfaalog.info(f"[兜底导航] 目标[{target_quest}]为特殊关卡, 直接滑到底后放大识别")
                last_quests, empty_count, swipe_count = None, 0, 0
                for round_idx in range(MAX_ROUNDS):
                    img = QuestDetector._norm_img(controller.post_screencap().wait().get())
                    if img is None:
                        mfaalog.error("[兜底导航] 截图失败")
                        return False
                    nametags, _tags = self._detector().detect(img)
                    box_infos, box_crops = [], []
                    for (nx, ny, nw, nh, _c) in nametags:
                        nx, ny, nw, nh = int(nx), int(ny), int(nw), int(nh)
                        crop = img[max(0, ny - PAD):ny + nh + PAD, max(0, nx - PAD):nx + nw + PAD]
                        box_infos.append((nx, ny, nw, nh))
                        box_crops.append(crop if crop.size else None)
                    assigned, _un = greedy_match_boxes(box_crops, templates)
                    cur_set = set()
                    cur_quests = []
                    for (bi, _bj, _s) in assigned:
                        nx, ny, nw, nh = box_infos[bi]
                        name = list(templates.keys())[_bj]
                        cur_set.add(name)
                        cur_quests.append((name, nx, ny))
                    cur_quests = sorted(cur_quests)
                    if cur_quests:
                        empty_count = 0
                        if last_quests is not None and cur_quests == last_quests:
                            mfaalog.info(f"[兜底导航] 滑到底, 关卡集合 {cur_set} 与上屏坐标完全一致")
                            break
                    else:
                        empty_count += 1
                        if empty_count >= EMPTY_SCREEN_LIMIT:
                            mfaalog.info(f"[兜底导航] 连续 {empty_count} 屏未识别到关卡, 判定到底")
                            break
                    last_quests = cur_quests
                    controller.post_swipe(*SWIPE_START, SWIPE_START[0], SWIPE_START[1] - SWIPE_DIST, SWIPE_DURATION).wait()
                    swipe_count += 1
                    time.sleep(1.5)
                    prevent_touch(context)
                cfg = special[target_quest]
                center = tuple(int(v) for v in cfg.get("zoom_center", [640, 360]))
                zoom_rounds = int(cfg.get("zoom_rounds", 1))
                mfaalog.info(f"[兜底导航] 特殊关卡[{target_quest}] 到底, 以 {center} 为中心放大 {zoom_rounds} 轮后识别")
                for zi in range(zoom_rounds):
                    pinch_zoom_in(controller, center)
                    controller.post_swipe(*SWIPE_START, SWIPE_START[0], SWIPE_START[1] - ZOOM_PULL_UP, ZOOM_PULL_DURATION).wait()
                    time.sleep(1.5)
                    for _ in range(ZOOM_PULL_STEPS):
                        controller.post_swipe(*PULL_SWIPE_START, PULL_SWIPE_START[0] + ZOOM_PULL_STEP, PULL_SWIPE_START[1], ZOOM_PULL_DURATION).wait()
                        time.sleep(1.5)
                    time.sleep(1.5)
                    prevent_touch(context)
                    mfaalog.info(f"[兜底导航] 特殊关卡放大第{zi + 1}轮完成")
                    img = QuestDetector._norm_img(controller.post_screencap().wait().get())
                    if img is None:
                        break
                    nametags, _tags = self._detector().detect(img)
                    box_infos, box_crops = [], []
                    for (nx, ny, nw, nh, _c) in nametags:
                        nx, ny, nw, nh = int(nx), int(ny), int(nw), int(nh)
                        crop = img[max(0, ny - PAD):ny + nh + PAD, max(0, nx - PAD):nx + nw + PAD]
                        box_infos.append((nx, ny, nw, nh))
                        box_crops.append(crop if crop.size else None)
                    assigned, _un = greedy_match_boxes(box_crops, templates)
                    tpl_names = list(templates.keys())
                    for (bi, bj, score) in assigned:
                        name = tpl_names[bj]
                        if name != target_quest:
                            continue
                        nx, ny, nw, nh = box_infos[bi]
                        cx, cy = nx + nw // 2, ny + nh // 2
                        mfaalog.info(f"[兜底导航] 特殊关卡[{target_quest}] 放大后识别到, 点击 ({cx},{cy}) 匹配分 {score:.2f}")
                        controller.post_click(cx, cy).wait()
                        return True
                mfaalog.error(f"[兜底导航] 特殊关卡[{target_quest}] 放大 {zoom_rounds} 轮后仍未识别到 (滑动 {swipe_count} 次)")
                return False

            # 步骤5: 普通关卡滑动循环(坐标表辅助: 候选过滤 + offset_y 反推闭环)
            coords, _map_h = load_quest_coords(ROOT_DIR,
                                               os.path.dirname(template.replace("\\", "/")).strip("/"))
            offset_y = None
            last_bottom = None
            empty_count = 0
            swipe_count = 0
            for round_idx in range(MAX_ROUNDS):
                mfaalog.info(f"[兜底导航] === 第{round_idx + 1}屏 ===")
                img = QuestDetector._norm_img(controller.post_screencap().wait().get())
                if img is None:
                    mfaalog.error("[兜底导航] 截图失败")
                    return False

                nametags, tags = self._detector().detect(img)
                mfaalog.info(f"[兜底导航] 检测框 {len(nametags)} nametag, {len(tags)} tag")

                box_infos, box_crops = [], []
                for (nx, ny, nw, nh, _c) in nametags:
                    nx, ny, nw, nh = int(nx), int(ny), int(nw), int(nh)
                    crop = img[max(0, ny - PAD):ny + nh + PAD, max(0, nx - PAD):nx + nw + PAD]
                    box_infos.append((nx, ny, nw, nh))
                    box_crops.append(crop if crop.size else None)
                assigned, offset_y = match_with_coords(box_infos, box_crops, templates, coords, offset_y)
                cur_quests = []
                for (bi, name, score) in assigned:
                    nx, ny, nw, nh = box_infos[bi]
                    cur_quests.append((name, nx, ny))
                    mfaalog.info(f"[兜底导航] 识别到关卡: {name} ({score:.2f}) 框=({nx},{ny},{nw},{nh})")
                    if name == target_quest:
                        cx, cy = nx + nw // 2, ny + nh // 2
                        mfaalog.info(f"[兜底导航] 目标[{target_quest}]在屏, 点击 ({cx},{cy})")
                        controller.post_click(cx, cy).wait()
                        return True
                cur_quests = sorted(cur_quests)

                if cur_quests:
                    empty_count = 0
                    if coords:
                        bot_name, bot_sy, bot_ty = None, -1, -1
                        for name, nx, ny in cur_quests:
                            ty = coords.get(name, (0, 0))[1]
                            if ty > bot_ty:
                                bot_name, bot_sy, bot_ty = name, ny, ty
                        if bot_name is not None and last_bottom is not None \
                                and last_bottom[0] == bot_name and abs(bot_sy - last_bottom[1]) <= 2:
                            mfaalog.info(f"[兜底导航] 滑到底, 底部关卡[{bot_name}]两屏屏幕y "
                                         f"{last_bottom[1]}->{bot_sy} 不再移动")
                            break
                        if bot_name is not None:
                            last_bottom = (bot_name, bot_sy)
                else:
                    empty_count += 1
                    if empty_count >= EMPTY_SCREEN_LIMIT:
                        mfaalog.info(f"[兜底导航] 连续 {empty_count} 屏未识别到关卡, 判定到底")
                        break

                controller.post_swipe(*SWIPE_START, SWIPE_START[0], SWIPE_START[1] - SWIPE_DIST, SWIPE_DURATION).wait()
                swipe_count += 1
                time.sleep(1.5)
                prevent_touch(context)

            mfaalog.error(f"[兜底导航] 滑到底未找到目标关卡: {target_quest} (共滑动 {swipe_count} 次)")
            return False

        except Exception as e:
            mfaalog.error(f"[兜底导航] 严重错误: {str(e)}")
            return False

    def _detector(self):
        """延迟加载全局检测器(ultralytics), 复用避免重复初始化"""
        det = getattr(self, "_det", None)
        if det is None:
            agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(agent_dir, "utils", "quest_detect.pt")
            if not os.path.exists(model_path):
                model_path = os.path.join(agent_dir, "utils", "quest_detect.onnx")
            det = QuestDetector(model_path)
            self._det = det
            mfaalog.info(f"[兜底导航] 检测器加载: {model_path}")
        return det
