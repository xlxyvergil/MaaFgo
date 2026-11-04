"""Read the public Team ID in a Chaldea share image's footer, entirely locally.

Only the small light footer is inspected.  The existing PP-OCRv5 recognition
model reads isolated text lines; a detector model and device are not needed.
An ID is accepted only next to an explicit Team label and a Chaldea footer.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

from .storage import EditorError

MAX_TEAM_ID = 2**53 - 1
_TEAM = re.compile(r"team\s*(?:id\s*)?[:：#]?\s*([1-9][0-9]{0,15})\Z", re.I)
_URL = re.compile(r"(?:https?://)?(?:www\.)?chaldea\.center/laplace/?\Z", re.I)
_CREATOR = re.compile(r"createdbychaldeaapp(?:v?[0-9]+(?:\.[0-9]+)*)?\Z", re.I)


def _compact(text):
    return re.sub(r"\s+", "", text).strip()


def _anchor(text):
    value = _compact(text)
    return bool(_URL.fullmatch(value) or _CREATOR.fullmatch(value))


def _team(text):
    match = _TEAM.fullmatch(text.strip())
    if not match:
        return None
    value = int(match[1])
    return value if value <= MAX_TEAM_ID else None


def _interpret_lines(lines):
    """Keep positions and confidence: a number elsewhere in a battle is not an ID."""
    anchors = [line for line in lines if line['score'] >= .90 and _anchor(line['text'])]
    if not anchors:
        return None
    anchor_y = max(line['box'][1] for line in anchors)
    candidates = [line for line in lines if line['box'][1] < anchor_y and
                  line['score'] >= .90 and _team(line['text']) is not None]
    ids = {_team(line['text']) for line in candidates}
    if len(ids) > 1:
        raise EditorError('chaldea_team_ambiguous', '已识别到 Chaldea 分享图片，但页脚出现多个不同的 Team ID，请使用只包含一个队伍的原始图片。', 422)
    if not ids:
        raise EditorError('chaldea_team_missing', '已识别到 Chaldea 分享图片，但未能可靠识别页脚的 Team ID。请保留底部“Team 数字”及 Chaldea 链接；未发布队伍或没有 Team ID 的图片无法导入配置。', 422)
    return next(iter(ids)), anchors, candidates


def _footer_lines(image):
    """Cheap geometry gate, returning at most twelve single-line footer crops."""
    import cv2
    import numpy as np
    height, width = image.shape[:2]
    # A modest dark screen border is harmless; never scan the whole long image.
    probe = image[max(0, height - min(height, width)):]
    gray = cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY)
    non_dark = np.flatnonzero(np.count_nonzero(gray > 170, axis=1) > width * .15)
    if len(non_dark) and non_dark[-1] < len(probe) - 1:
        height -= len(probe) - 1 - int(non_dark[-1])
    columns = np.flatnonzero(np.any(gray > 170, axis=0))
    left, right = 0, width
    if len(columns) and columns[0] < width * .15 and columns[-1] > width * .85:
        left, right = int(columns[0]), int(columns[-1]) + 1
    top = max(0, height - min(1200, round(width * .8)))
    footer = image[top:height, left:right]
    if not footer.size:
        return []
    scale = min(1.0, 1200 / (right-left))
    if scale < 1:
        footer = cv2.resize(footer, (round((right-left) * scale), round(len(footer) * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(footer, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(footer, cv2.COLOR_BGR2HSV)
    band = max(1, min(len(footer), round(footer.shape[1] * .24)))
    light = (gray[-band:] >= 200) & (hsv[-band:, :, 1] < 45)
    if float(light.mean()) < .62:
        return []
    dark = (gray < 145) & (hsv[:, :, 1] < 65)
    rows = np.count_nonzero(dark, axis=1) >= max(3, footer.shape[1] * .007)
    edges = np.diff(np.r_[False, rows, False].astype('int8'))
    bands = list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))
    results = []
    for y0, y1 in reversed(bands):
        if not 5 <= y1 - y0 <= min(120, footer.shape[1] * .16):
            continue
        xs = np.flatnonzero(dark[y0:y1].any(axis=0))
        if not len(xs):
            continue
        x0, x1 = int(xs[0]), int(xs[-1]) + 1
        if x1 - x0 < 35 or x1 - x0 > footer.shape[1] * .98:
            continue
        pad = max(3, round((y1 - y0) * .18))
        box = (max(0, x0-pad), max(0, int(y0)-pad), min(footer.shape[1], x1+pad), min(len(footer), int(y1)+pad))
        crop = footer[box[1]:box[3], box[0]:box[2]]
        results.append((crop, [left+round(box[0]/scale), top+round(box[1]/scale), round((box[2]-box[0])/scale), round((box[3]-box[1])/scale)]))
        if len(results) == 12:
            break
    return results


class _Reader:
    def __init__(self, model, keys):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        self.keys = Path(keys).read_text(encoding='utf-8').splitlines() + [' ']

    def read(self, image, binary=False):
        import cv2
        import numpy as np
        if binary:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            _, gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        height, width = image.shape[:2]
        resized_width = min(1536, max(8, round(width * 48 / height)))
        tensor = cv2.resize(image, (resized_width, 48), interpolation=cv2.INTER_CUBIC)
        tensor = (tensor[:, :, ::-1].astype(np.float32) / 127.5 - 1).transpose(2, 0, 1)[None]
        probabilities = self.session.run(None, {self.input_name: tensor})[0][0]
        indexes = probabilities.argmax(axis=1)
        result, scores, previous = [], [], 0
        for index, score in zip(indexes, probabilities.max(axis=1)):
            if index and index != previous:
                result.append(self.keys[index-1] if index <= len(self.keys) else '?')
                scores.append(float(score))
            previous = index
        return ''.join(result), sum(scores) / len(scores) if scores else 0.0


@lru_cache(maxsize=2)
def _reader(root):
    root = Path(root)
    for relative in ('resource/model/ocr', 'assets/resource/model/ocr',
                     'resource/base/model/ocr', 'assets/resource/base/model/ocr',
                     'resource/base/model/OCR', 'assets/resource/base/model/OCR'):
        directory = root / relative
        if (directory / 'rec.onnx').is_file() and (directory / 'keys.txt').is_file():
            try:
                return _Reader(directory / 'rec.onnx', directory / 'keys.txt')
            except Exception as exc:
                raise EditorError('chaldea_ocr_unavailable', 'Chaldea 图片文字识别组件无法加载，请使用完整桌面资源包，或手动输入分享链接 / Team ID。', 503) from exc
    raise EditorError('chaldea_ocr_unavailable', '缺少 Chaldea 图片文字识别模型，请使用完整桌面资源包，或手动输入分享链接 / Team ID。', 503)


def recognize_chaldea_image(image, root):
    crops = _footer_lines(image)
    if not crops:
        return None
    reader = _reader(str(Path(root).resolve()))
    lines = []
    # URL and creator lines are normally the last two. Stop early on ordinary
    # light documents instead of OCR-ing every action or loading game templates.
    for crop, box in crops[:3]:
        text, score = reader.read(crop)
        lines.append({'text': text, 'score': score, 'box': box})
    if not any(line['score'] >= .90 and _anchor(line['text']) for line in lines):
        return None
    for crop, box in crops[3:]:
        text, score = reader.read(crop)
        lines.append({'text': text, 'score': score, 'box': box})
    interpreted = _interpret_lines(lines)
    team_id, anchors, candidates = interpreted
    # A second independent contrast rendering must read the same explicit ID;
    # never repair O/0, fill missing digits, or guess from another battle number.
    confirmed = set()
    for candidate in candidates:
        crop = next(crop for crop, box in crops if box == candidate['box'])
        text, score = reader.read(crop, binary=True)
        other = _team(text) if score >= .90 else None
        if other is not None and other != team_id:
            raise EditorError('chaldea_team_ambiguous', '已识别到 Chaldea 分享图片，但 Team ID 的两次识别结果不一致，请使用更清晰的原始图片。', 422)
        if other == team_id:
            confirmed.add(team_id)
    if not confirmed:
        raise EditorError('chaldea_team_uncertain', '已识别到 Chaldea 分享图片，但 Team ID 不够清晰，无法可靠读取。请使用原始图片，或手动输入分享链接 / Team ID。', 422)
    return {'kind': 'chaldea', 'teamId': team_id,
            'source': f'https://link.chaldea.center/laplace/share?id={team_id}',
            'layout': {'footerAnchors': [line['box'] for line in anchors], 'teamLabel': candidates[0]['box']},
            'warnings': []}
