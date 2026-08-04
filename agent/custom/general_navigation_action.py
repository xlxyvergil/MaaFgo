# -*- coding: utf-8 -*-
"""
通用导航 Action - 纯滑动 + YOLO检测 + 对比识别 + 特殊关卡ROI

流程:
1. 读取章节/关卡参数 + 资源包类型
2. 双指缩放到最大 + 左上角归位(顶部起点)
3. 循环(最多 MAX_ROUNDS 屏):
   a. 截图
   b. 截图 + QuestDetector(纯 Python ultralytics) 主检测 + 孤框窗口重检, 返回全部 nametag/tag 框
   c. 配对: tag 左边缘在 nametag 右侧 40% 区域内且 y 重叠才算有效关卡
      未配对 tag(孤点, nametag 置信度不足被滤掉)从 tag 向左扩展区域做兜底识别
   d. 对比识别: 有效框/兜底区域内容 vs 名称条素材库(多尺度模板匹配) -> 关卡名
   e. 目标关卡在屏 -> 点击框中心
   f. 无 -> 向下滑动 SWIPE_DIST(手指上滑=看下一屏)
4. 特殊关卡(被UI完全遮挡, YOLO识别不到): 滑动计数进入窗口时,
   在固定ROI内做模板匹配, 命中即点击
5. 到底判定: 连续两屏识别到的关卡集合相同 -> 到底, 导航失败

素材约定(每章):
  resource/{pkg}/image/map/{英文章节目录}/   目录由运行时"关卡选择"节点 template(map/{英文}/{关卡}.png) 推导
    {关卡名}.png      名称条截图(最大缩放视图下裁剪), 文件名=关卡名, 与关卡选择模板同目录
    special.json     可选, 特殊关卡配置:
                      {"关卡A": {"roi":[x,y,w,h], "template":"A.png", "swipe_count":5}, ...}
"""
import os
import time
import json
import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
import mfaalog

# 检测参数
MATCH_TH = 0.60           # 对比识别模板匹配阈值
MATCH_SCALES = [0.8, 0.9, 1.0, 1.1, 1.2]  # 对比识别多尺度

# 滑动与循环参数
SWIPE_DIST = 200          # 单次滑动距离(px), 手指上滑=看下方
SWIPE_DURATION = 300
MAX_ROUNDS = 30           # 最大滑动屏数

# 缩放与归位参数(无归位按钮, 靠捏合缩小+反向滑动归位到左上角起点)
ZOOM_ROUNDS = 3           # 双指捏合缩小轮数(建议3-4轮到最大可视范围)
HOME_SWIPE_START = (300, 250)   # 归位滑动: 从左上角...
HOME_SWIPE_END = (441, 391)     # ...向右下角(对角线约200px)
HOME_SWIPE_ROUNDS = 3           # 归位滑动轮数


def resolve_quest_dir(root_dir, resource_package, template_path):
    """从运行时 template(map/{英文}/{关卡}.png) 推导导航素材目录
    素材直接放章节文件夹: resource/{pkg}/image/map/{英文}/ (与关卡选择模板同目录)"""
    pkg = resource_package if resource_package in ("cn", "jp") else "base"
    folder = os.path.dirname(template_path).replace("\\", "/").strip("/")
    return os.path.join(root_dir, "resource", pkg, "image", "map", folder)


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
    return templates


def load_special_config(quest_dir):
    """加载特殊关卡配置(special.json), 不存在返回空 dict"""
    path = os.path.join(quest_dir, "special.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fp:
                return json.load(fp)
        except Exception as e:
            mfaalog.warning(f"[导航] 特殊关卡配置读取失败: {e}")
    return {}


def match_score(crop_gray, template_bgr):
    """多尺度模板匹配, 返回最高分(0~1)"""
    th, tw = template_bgr.shape[:2]
    if th < 4 or tw < 4:
        return 0.0
    best = 0.0
    for scale in MATCH_SCALES:
        w, h = max(4, int(tw * scale)), max(4, int(th * scale))
        if h > crop_gray.shape[0] or w > crop_gray.shape[1]:
            continue
        tpl = cv2.resize(template_bgr, (w, h))
        tpl_gray = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(crop_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        if max_val > best:
            best = float(max_val)
    return best


def identify_quest(crop_bgr, templates):
    """对比识别: 检测框内容 vs 素材库 -> (关卡名, 分数) 或 (None, 0)"""
    if crop_bgr is None or crop_bgr.size == 0 or not templates:
        return None, 0.0
    crop_gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    best_name, best_score = None, 0.0
    for name, tpl in templates.items():
        s = match_score(crop_gray, tpl)
        if s > best_score:
            best_score, best_name = s, name
    if best_name is not None and best_score >= MATCH_TH:
        return best_name, best_score
    return None, best_score


def pinch_zoom_out(controller):
    """双指捏合缩小地图(Maa 官方多点触控 API), 缩放到最大可视范围"""
    f1_start, f1_end = (200, 150), (560, 330)
    f2_start, f2_end = (1080, 570), (720, 390)
    steps = 6
    try:
        controller.post_touch_down(*f1_start, 0, 1.0).wait()
        controller.post_touch_down(*f2_start, 1, 1.0).wait()
        for i in range(1, steps + 1):
            t = i / steps
            p1 = (int(f1_start[0] + (f1_end[0] - f1_start[0]) * t),
                  int(f1_start[1] + (f1_end[1] - f1_start[1]) * t))
            p2 = (int(f2_start[0] + (f2_end[0] - f2_start[0]) * t),
                  int(f2_start[1] + (f2_end[1] - f2_start[1]) * t))
            controller.post_touch_move(*p1, 0, 1.0).wait()
            controller.post_touch_move(*p2, 1, 1.0).wait()
            time.sleep(0.05)
        controller.post_touch_up(0).wait()
        controller.post_touch_up(1).wait()
    except Exception as e:
        mfaalog.warning(f"[导航] 双指缩放失败: {e}，使用当前缩放")
        return
    time.sleep(0.5)


def pair_nametag_tag(boxes):
    """配对: 返回 (有效 nametag 框列表, 未配对 tag 框列表)
    boxes: 全部检测框 [(x,y,w,h,cls),...]
    nametag 框为含 tag 的完整名称条, 配对条件:
    tag 左边缘位于 nametag 右侧 40% 区域内(且不超出右侧 5px), y 方向有重叠
    (供外部测试脚本引用, 部署流程已不再使用)"""
    nametags = [(x, y, w, h) for x, y, w, h, cls in boxes if cls == 0]
    tags = [(x, y, w, h) for x, y, w, h, cls in boxes if cls == 1]
    valid = []
    tag_paired = [False] * len(tags)
    for nx, ny, nw, nh in nametags:
        for ti, (tx, ty, tw, th) in enumerate(tags):
            # tag 左边缘在 nametag 右侧 40% 区域内
            if not (nx + nw * 0.4 <= tx <= nx + nw + 5):
                continue
            # y 方向重叠
            if not (ty < ny + nh and ty + th > ny):
                continue
            valid.append((nx, ny, nw, nh))
            tag_paired[ti] = True
            break
    unpaired_tags = [t for t, p in zip(tags, tag_paired) if not p]
    return valid, unpaired_tags


class QuestDetector:
    """纯 Python 关卡检测: ultralytics(YOLOv8) 主检测 + 混合重检
    主检测整图 + 孤 nametag/tag 局部 640x640 窗口放大重检, 合并去重后返回
    """
    IMGSZ = 640
    CONF = 0.5        # 主检测阈值
    RC_CONF = 0.1     # 窗口重检阈值
    WIN = 640         # 重检窗口边长

    def __init__(self, model_path):
        from ultralytics import YOLO
        self.model = YOLO(model_path)

    def _infer(self, img, conf):
        """整图推理, 返回 [(x1,y1,x2,y2,score,cls)] 原图坐标(ultralytics 原生后处理)"""
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
        direction='left':  窗口偏左覆盖 tag + 左侧 nametag 区
        返回按方向过滤后的原图坐标框 [(x1,y1,x2,y2,score,cls)]"""
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
        """主检测 + 混合重检, 返回 (nametags, tags), 每项 [(x,y,w,h,conf)]"""
        boxes = self._infer(img, self.CONF)
        nametags = [(x1, y1, x2 - x1, y2 - y1, c) for x1, y1, x2, y2, c, cl in boxes if cl == 0]
        tags = [(x1, y1, x2 - x1, y2 - y1, c) for x1, y1, x2, y2, c, cl in boxes if cl == 1]

        def paired_n(nx, ny, nw, nh):
            return any(tx >= nx + nw * 0.4 and tx <= nx + nw + 5 and ty < ny + nh and ty + th > ny
                       for tx, ty, tw, th, tc in tags)

        def paired_t(tx, ty, tw, th):
            return any(tx >= nx + nw * 0.4 and tx <= nx + nw + 5 and ty < ny + nh and ty + th > ny
                       for nx, ny, nw, nh, nc in nametags)

        un_n = [(x, y, w, h) for x, y, w, h, c in nametags if not paired_n(x, y, w, h)]
        un_t = [(x, y, w, h) for x, y, w, h, c in tags if not paired_t(x, y, w, h)]
        # 孤 nametag 向右窗口重检找 tag
        for box in un_n:
            for (x1, y1, x2, y2, c, cl) in self._recheck(img, box, "right"):
                if any(self._iou((x1, y1, x2, y2), (tx, ty, tx + tw, ty + th)) > 0.5
                       for tx, ty, tw, th, tc in tags):
                    continue
                tags.append((x1, y1, x2 - x1, y2 - y1, c))
        # 孤 tag 向左窗口重检找 nametag
        for box in un_t:
            for (x1, y1, x2, y2, c, cl) in self._recheck(img, box, "left"):
                if any(self._iou((x1, y1, x2, y2), (nx, ny, nx + nw, ny + nh)) > 0.3
                       for nx, ny, nw, nh, nc in nametags):
                    continue
                nametags.append((x1, y1, x2 - x1, y2 - y1, c))
        return nametags, tags


@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        mfaalog.info("=" * 50)
        mfaalog.info("[导航] 通用导航 Action 启动（纯滑动+YOLO检测模式）")
        try:
            # 步骤1: 参数
            node_data = context.get_node_data("地图坐标导航")
            if not node_data:
                mfaalog.error("[导航] 无法获取节点数据")
                return CustomAction.RunResult(success=False)
            attach = node_data.get("attach", {})
            target_quest = attach.get("quests", "")

            # 从运行时"关卡选择"节点读取 template, 推导素材目录
            sel_node = context.get_node_data("关卡选择") or {}
            template = sel_node.get("template") or (sel_node.get("recognition") or {}).get("param", {}).get("template", "")
            if isinstance(template, list):
                template = template[0] if template else ""
            if not template:
                mfaalog.error("[导航] 无法获取关卡选择 template")
                return CustomAction.RunResult(success=False)

            resource_config = context.get_node_data("资源包配置")
            resource_package = resource_config.get("attach", {}).get("resource_package", "base") if resource_config else "base"

            if not target_quest:
                mfaalog.error(f"[导航] 参数缺失: quest={target_quest}")
                return CustomAction.RunResult(success=False)
            mfaalog.info(f"[导航] quest: {target_quest}, template: {template}, pkg: {resource_package}")

            # 步骤2: 素材目录
            AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ROOT_DIR = os.path.dirname(AGENT_DIR)
            quest_dir = resolve_quest_dir(ROOT_DIR, resource_package, template)
            templates = load_quest_templates(quest_dir)
            special = load_special_config(quest_dir)

            if target_quest not in templates:
                mfaalog.error(f"[导航] 素材库缺少目标关卡名称条截图: {quest_dir}/{target_quest}.png")
                return CustomAction.RunResult(success=False)
            mfaalog.info(f"[导航] 素材库 {len(templates)} 关, 特殊关卡 {len(special)} 个")

            # 步骤3: 缩放 + 反向滑动归位(回到左上角起点)
            # 注意: 不做 UI隐藏, 隐藏 UI 会导致 YOLO 需要识别的名称条/tag 不可见
            controller = context.tasker.controller
            for i in range(ZOOM_ROUNDS):
                try:
                    pinch_zoom_out(controller)
                    mfaalog.info(f"[导航] 缩放第{i + 1}轮完成")
                except Exception as e:
                    mfaalog.warning(f"[导航] 缩放失败: {e}，使用当前缩放")
                    break
            time.sleep(0.5)
            # 反向滑动归位: 从左上角向右下角滑动几轮(每轮约200px), 让地图回到左上角起点
            for i in range(HOME_SWIPE_ROUNDS):
                controller.post_swipe(*HOME_SWIPE_START, *HOME_SWIPE_END, SWIPE_DURATION).wait()
                time.sleep(0.3)
            time.sleep(0.5)

            # 步骤4: 特殊关卡: 目标在 special 配置中 -> 直接滑到底, 走特殊判定进入
            if target_quest in special:
                mfaalog.info(f"[导航] 目标[{target_quest}]为特殊关卡, 直接滑到底后特殊判定")
                last_set, swipe_count = None, 0
                img = None
                for round_idx in range(MAX_ROUNDS):
                    img = controller.post_screencap().wait().get()
                    if img is None:
                        break
                    nametags, _tags = self._detector().detect(img)
                    cur_set = set()
                    for (nx, ny, nw, nh, _c) in nametags:
                        name, _s = identify_quest(img[int(ny):int(ny + nh), int(nx):int(nx + nw)], templates)
                        if name:
                            cur_set.add(name)
                    # 到底判定: 连续两屏关卡集合相同且非空
                    if last_set is not None and cur_set == last_set and cur_set:
                        mfaalog.info(f"[导航] 滑到底, 关卡集合 {cur_set} 与上屏相同")
                        break
                    last_set = cur_set
                    controller.post_swipe(640, 600, 640, 600 - SWIPE_DIST, SWIPE_DURATION).wait()
                    swipe_count += 1
                    time.sleep(0.5)
                # 到底后: 固定 ROI 特殊判定
                cfg = special[target_quest]
                roi = cfg.get("roi")
                tpl_path = os.path.join(quest_dir, cfg.get("template", ""))
                if roi and img is not None and os.path.exists(tpl_path):
                    tpl = cv2.imdecode(np.fromfile(tpl_path, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if tpl is not None:
                        x, y, w, h = map(int, roi)
                        region = img[y:y + h, x:x + w]
                        if region.size:
                            s = match_score(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), tpl)
                            mfaalog.info(f"[导航] 特殊关卡[{target_quest}] 到底后 ROI匹配分 {s:.3f}")
                            if s >= MATCH_TH:
                                cx, cy = x + w // 2, y + h // 2
                                mfaalog.info(f"[导航] 特殊关卡[{target_quest}] 命中, 点击 ({cx},{cy})")
                                controller.post_click(cx, cy).wait()
                                return CustomAction.RunResult(success=True)
                mfaalog.error(f"[导航] 特殊关卡[{target_quest}] 特殊判定未命中 (滑动 {swipe_count} 次)")
                return CustomAction.RunResult(success=False)

            # 步骤5: 普通关卡滑动循环
            last_set = None      # 上一屏识别的关卡集合
            swipe_count = 0      # 已滑动次数
            for round_idx in range(MAX_ROUNDS):
                mfaalog.info(f"[导航] === 第{round_idx + 1}屏 ===")
                img = controller.post_screencap().wait().get()
                if img is None:
                    mfaalog.error("[导航] 截图失败")
                    return CustomAction.RunResult(success=False)

                # 5a. YOLO 检测全部关卡 (纯 Python ultralytics + 混合重检)
                nametags, tags = self._detector().detect(img)
                mfaalog.info(f"[导航] 检测框 {len(nametags)} nametag, {len(tags)} tag")

                # 5b. 对比识别 -> 当前屏关卡集合, 目标在屏直接点击进入
                cur_set = set()
                for (nx, ny, nw, nh, _c) in nametags:
                    nx, ny, nw, nh = int(nx), int(ny), int(nw), int(nh)
                    crop = img[ny:ny + nh, nx:nx + nw]
                    name, score = identify_quest(crop, templates)
                    if name is None:
                        continue
                    cur_set.add(name)
                    mfaalog.info(f"[导航] 识别到关卡: {name} ({score:.2f}) 框=({nx},{ny},{nw},{nh})")
                    if name == target_quest:
                        cx, cy = nx + nw // 2, ny + nh // 2
                        mfaalog.info(f"[导航] 目标[{target_quest}]在屏, 点击 ({cx},{cy})")
                        controller.post_click(cx, cy).wait()
                        return CustomAction.RunResult(success=True)

                # 5c. 到底判定: 连续两屏关卡集合相同且非空
                if last_set is not None and cur_set == last_set and cur_set:
                    mfaalog.info(f"[导航] 滑到底, 关卡集合 {cur_set} 与上屏相同")
                    break
                last_set = cur_set

                # 5d. 向下滑动(手指上滑, 看下方地图) 200px
                controller.post_swipe(640, 600, 640, 600 - SWIPE_DIST, SWIPE_DURATION).wait()
                swipe_count += 1
                time.sleep(0.5)

            mfaalog.error(f"[导航] 滑到底未找到目标关卡: {target_quest} (共滑动 {swipe_count} 次)")
            return CustomAction.RunResult(success=False)

        except Exception as e:
            mfaalog.error(f"[导航] 严重错误: {str(e)}")
            return CustomAction.RunResult(success=False)

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
            mfaalog.info(f"[导航] 检测器加载: {model_path}")
        return det
