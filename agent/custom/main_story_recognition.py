"""主线 NEXT：BBC 原始 1920 模板的彩色遮罩匹配，不调用 BBC。"""
from functools import lru_cache
import json
from pathlib import Path

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition


@lru_cache(maxsize=4)
def load_templates(server: str):
    if server not in ("cn", "jp"):
        raise ValueError("主线模板仅支持 cn / jp")
    root = Path(__file__).resolve().parents[2]
    for resources in (root / "resource", root / "assets" / "resource"):
        folder = resources / server / "image" / "main_story"
        if (folder / "next.png").is_file():
            image = cv2.imdecode(np.fromfile(folder / "next.png", np.uint8), cv2.IMREAD_COLOR)
            mask = cv2.imdecode(np.fromfile(folder / "next_mask.png", np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None or mask is None or image.shape[:2] != mask.shape:
                raise ValueError("主线 NEXT 模板/遮罩损坏")
            return image, np.where(mask > 127, 255, 0).astype(np.uint8)
    raise FileNotFoundError(f"缺少 {server}/image/main_story/next.png")


def match_next(image, template, mask, *, panel=False, threshold=0.2, stopped=lambda: False):
    """返回点击点与证据；坐标随截图尺寸变化，越界候选不点击。"""
    height, width = image.shape[:2]
    unit = width / 1920
    # 列表固定尺寸；地图允许缩放。避开标题栏与下方常驻控件。
    x0, y0 = (round(width * 0.46) if panel else 0), round(height * 0.07)
    y1 = round(height * 0.84)
    crop = image[y0:y1, x0:width]
    scales = [1.0] if panel else [1.0] + [i / 20 for i in range(8, 47) if i != 20]
    best = None
    for scale in scales:
        if stopped():
            return None
        size = (round(template.shape[1] * unit * scale), round(template.shape[0] * unit * scale))
        if min(size) < 8 or size[0] > crop.shape[1] or size[1] > crop.shape[0]:
            continue
        templ = cv2.resize(template, size, interpolation=cv2.INTER_AREA)
        m = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
        scores = cv2.matchTemplate(crop, templ, cv2.TM_SQDIFF_NORMED, mask=m)
        scores = np.nan_to_num(scores, nan=1.0, posinf=1.0, neginf=1.0)
        score, _, loc, _ = cv2.minMaxLoc(scores)
        if score > threshold or (best and score >= best[1]["difference"]):
            continue
        x = round(x0 + loc[0] + size[0] / 2)
        offset = 100 * unit if panel else 150 * unit * scale
        y = round(y0 + loc[1] + size[1] / 2 + offset)
        if not (0 <= x < width and y0 <= y < y1):
            continue
        best = ([x, y, 1, 1], {"difference": score, "scale": scale,
                              "marker_box": [x0 + loc[0], y0 + loc[1], *size],
                              "panel": panel})
    return best


@AgentServer.custom_recognition("main_story_next")
class MainStoryNext(CustomRecognition):
    def analyze(self, context, argv):
        params = json.loads(argv.custom_recognition_param or "{}")
        config = context.get_node_data("主线-配置") or {}
        server = config.get("attach", {}).get("server", "cn")
        template, mask = load_templates(server)
        tasker = context.tasker
        result = match_next(argv.image, template, mask, panel=params.get("panel", False),
                            stopped=lambda: tasker.stopping)
        if result:
            return CustomRecognition.AnalyzeResult(box=result[0], detail=result[1])
        return None
