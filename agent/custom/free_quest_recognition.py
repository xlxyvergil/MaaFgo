"""地图未完成数字标记：先找橙色圆章，再在圆章内确认数字。"""
import json

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition


def badge_candidates(image):
    height, width = image.shape[:2]
    unit = width / 1280
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(hsv, (3, 135, 150), (25, 255, 255))
    contours, _ = cv2.findContours(orange, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        cx, cy = x + w / 2, y + h / 2
        if not (18 * unit <= w <= 90 * unit and .78 <= w / h <= 1.22):
            continue
        if x < 3 or x + w >= width - 3 or y < 85 * unit or y + h >= height - 5:
            continue
        # 常驻礼物数、任务数、头像和菜单不属于地图点。
        if (cx < 610 * unit and cy > 500 * unit) or (cx > 1075 * unit and cy > 580 * unit):
            continue
        if cv2.contourArea(contour) / (w * h) < .62:
            continue
        pad = max(2, round(w * .13))
        x0, y0 = max(0, x-pad), max(0, y-pad)
        x1, y1 = min(width, x+w+pad), min(height, y+h+pad)
        patch = image[y0:y1, x0:x1]
        yy, xx = np.ogrid[y0:y1, x0:x1]
        radius = (w+h)/4
        distance = np.sqrt((xx-cx)**2+(yy-cy)**2) / radius
        ring = (distance > 1.01) & (distance < 1.16)
        white = np.min(patch, axis=2) > 205
        if not ring.any() or white[ring].mean() < .45:
            continue
        candidates.append([x, y, w, h])
    return sorted(candidates, key=lambda box: (box[1], box[0]))


@AgentServer.custom_recognition("free_quest_badge")
class FreeQuestBadge(CustomRecognition):
    def analyze(self, context, argv):
        params = json.loads(argv.custom_recognition_param or "{}")
        local = context.clone()
        for x, y, w, h in badge_candidates(argv.image):
            if context.tasker.stopping:
                return None
            inset = float(params.get("digit_inset_ratio", .15))
            if not 0 <= inset <= .25:
                raise ValueError("数字圆章内缩比例无效")
            pad = max(1, round(min(w, h) * inset))
            result = local.run_recognition("Free-数字确认", argv.image, {
                "Free-数字确认": {"recognition": {"param": {"roi": [x+pad, y+pad, w-2*pad, h-2*pad]}}}
            })
            if not result or not result.hit:
                continue
            dx, dy = params.get("click_offset_radius", [-1, 1])
            cx, cy = round(x+w/2+dx*w/2), round(y+h/2+dy*h/2)
            return CustomRecognition.AnalyzeResult(
                box=[cx, cy, 1, 1],
                detail={"badge_box": [x, y, w, h], "digit": result.raw_detail},
            )
        return None
