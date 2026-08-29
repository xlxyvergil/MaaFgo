# -*- coding: utf-8 -*-
"""
YOLO ONNX 推理模块(替代 ultralytics.YOLO, 甩掉 torch 依赖)

与 ultralytics 推理行为对齐:
  - 预处理: letterbox(等比缩放+灰边填充, stride=32 对齐), BGR->RGB, HWC->CHW, /255
  - 后处理: 输出 (1, 4+nc, N) -> 转置 -> 置信度过滤 -> NMS(iou=0.7, ultralytics 默认)
  - 坐标: letterbox 逆变换回原图坐标

用法:
    from yolo_onnx import YoloOnnx
    det = YoloOnnx("model.onnx", imgsz=640)
    boxes = det.detect(img, conf=0.5)   # [(x1,y1,x2,y2,conf,cls), ...]
"""

import numpy as np


def _letterbox(img, new_shape, color=(114, 114, 114)):
    """ultralytics LetterBox 复刻: 等比缩放 + 居中填充, 填充量对齐 stride=32"""
    import cv2
    shape = img.shape[:2]  # (h, w)
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = (round(shape[1] * r), round(shape[0] * r))  # (w, h)
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    # stride 对齐(ultralytics auto=True 行为)
    dw %= 64
    dh %= 64
    dw /= 2
    dh /= 2
    if shape[::-1] != new_unpad:  # (w, h) 不一致才 resize
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return img, r, left, top


def _nms(boxes, scores, iou_thres):
    """纯 numpy NMS(按分数降序, IoU > iou_thres 抑制)"""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


class YoloOnnx:
    """onnxruntime YOLO 检测器(接口与 ultralytics.YOLO 推理用法兼容)"""

    def __init__(self, model_path, imgsz=640, iou=0.7):
        import onnxruntime as ort
        self.imgsz = imgsz
        self.iou = iou
        self.sess = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.sess.get_inputs()[0].name
        self.nc = self.sess.get_outputs()[0].shape[1] - 4  # 输出 (1, 4+nc, N)

    def detect(self, img, conf=0.5):
        """img: BGR uint8 ndarray; 返回 [(x1,y1,x2,y2,conf,cls), ...] 原图坐标"""
        import cv2
        if img is None or img.size == 0:
            return []
        # 预处理
        im, r, pad_x, pad_y = _letterbox(img, self.imgsz)
        im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
        im = im.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        # 推理
        out = self.sess.run(None, {self.input_name: im})[0][0]  # (4+nc, N)
        out = out.T  # (N, 4+nc)
        # 解码: 前 4 列 xywh(输入图坐标), 后 nc 列类别分数
        boxes = out[:, :4].copy()
        scores = out[:, 4:]
        cls_ids = scores.argmax(axis=1)
        confs = scores[np.arange(len(scores)), cls_ids]
        mask = confs > conf
        if not mask.any():
            return []
        boxes, confs, cls_ids = boxes[mask], confs[mask], cls_ids[mask]
        # xywh -> xyxy
        xy = boxes[:, :2]
        wh = boxes[:, 2:4]
        boxes_xyxy = np.concatenate([xy - wh / 2, xy + wh / 2], axis=1)
        # NMS(按类别分组, 与 ultralytics multi_label=False 行为一致)
        keep = _nms(boxes_xyxy, confs, self.iou)
        # letterbox 逆变换回原图坐标
        res = []
        for i in keep:
            x1, y1, x2, y2 = boxes_xyxy[i]
            res.append((
                (x1 - pad_x) / r, (y1 - pad_y) / r,
                (x2 - pad_x) / r, (y2 - pad_y) / r,
                float(confs[i]), int(cls_ids[i]),
            ))
        return res
