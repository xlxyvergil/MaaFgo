# -*- coding: utf-8 -*-
"""编队页御主礼装校验与选择；固定交互维护在御主礼装 Pipeline。"""

from dataclasses import dataclass
from functools import lru_cache
import html
import json
from pathlib import Path
import time

import cv2
import numpy as np

import mfaalog


DATA_PATH = Path(__file__).with_name("mystic_code_data.json")
TEAM_ROI = (10, 640, 85, 80)
SELECTED_ROI = (594, 160, 154, 154)
LIST_ROI = (552, 611, 662, 80)
THRESHOLD = 0.90
TEAM_FALLBACK_PRIMARY_MIN = 0.88
MAX_LIST_PAGES = 10


def extract_mystic_code_id(share_data):
    """兼容编辑器/Chaldea 与旧版顶层格式；0/null 表示未指定。"""
    if not isinstance(share_data, dict):
        raise ValueError("invalid_chaldea_team: 编队数据必须是对象")
    team = share_data.get("team")
    team_code = team.get("mysticCode") if isinstance(team, dict) else None
    legacy_code = share_data.get("mysticCode")
    value = None
    for code, key in ((team_code, "mysticCodeId"), (legacy_code, "id")):
        if code is None:
            continue
        if not isinstance(code, dict):
            raise ValueError("invalid_mystic_code: 御主礼装数据必须是对象")
        value = code.get(key)
        if value is not None:
            break
    if value is None or type(value) is int and value == 0:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError(f"invalid_mystic_code: 无效御主礼装 ID {value!r}")
    return value


@lru_cache(maxsize=1)
def _catalog():
    with DATA_PATH.open(encoding="utf-8") as stream:
        data = json.load(stream)
    return {int(item["id"]): item for item in data["mysticCodes"]}


@lru_cache(maxsize=256)
def _read_icon(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"resource_missing: 御主礼装图标需要透明 PNG: {path}")
    return image


@dataclass(frozen=True)
class IconMatch:
    score: float
    center: tuple
    template: str
    primary_score: float | None = None


def _icons(formation, item):
    icons = []
    for image_id in (item["maleImageId"], item["femaleImageId"]):
        relative = f"MysticCodes/masterequip{int(image_id):05d}.png"
        for root in formation.image_roots:
            path = Path(root) / relative
            if path.is_file():
                icons.append((str(path), _read_icon(str(path))))
                break
    if not icons:
        raise ValueError(f"resource_missing: 未找到御主礼装 {item['id']} 的识别资源")
    return icons


def match_icon(image, icons, scene):
    """按游戏界面的固定显示比例匹配衣服内部，透明区不参与评分。"""
    if image is None or image.size == 0:
        return None
    sx, sy = image.shape[1] / 1280, image.shape[0] / 720
    roi, scales = {
        "team": (TEAM_ROI, (0.370, 0.375, 0.380)),
        "selected": (SELECTED_ROI, (0.99, 1.00, 1.01)),
        "list": (LIST_ROI, (0.495, 0.500, 0.505)),
    }[scene]
    x, y, width, height = roi
    x, y, width, height = (round(x * sx), round(y * sy), round(width * sx), round(height * sy))
    region = image[y:y + height, x:x + width]
    best = None
    fallback = None
    for path, source in icons:
        # Atlas sprite edges blend with a different background in each screen.
        # Erode at the original size before scaling, retaining interior black
        # fabric reconstructed by the resource builder's exterior flood fill.
        # A clothing silhouette can enclose atlas-black gaps between sleeves
        # and torso; those pixels are transparent in game, so also omit nearly
        # black samples from recognition without deleting their asset colors.
        source_mask = ((source[:, :, 3] >= 245) &
                       (np.max(source[:, :, :3], axis=2) > 25)).astype(np.uint8) * 255
        inner_mask = (cv2.erode(source_mask, np.ones((7, 7), dtype=np.uint8))
                      if scene == "team" else None)
        source_mask = cv2.erode(source_mask, np.ones((5, 5), dtype=np.uint8))
        for scale in scales:
            target_size = (max(1, round(source.shape[1] * scale * sx)), max(1, round(source.shape[0] * scale * sy)))
            if target_size[0] > region.shape[1] or target_size[1] > region.shape[0]:
                continue
            scaled = cv2.resize(source[:, :, :3], target_size, interpolation=cv2.INTER_LINEAR)
            mask = cv2.resize(source_mask, target_size, interpolation=cv2.INTER_NEAREST)
            if scene == "list":
                # Lv labels overlay the bottom of the carousel sprites. Keep
                # the complete icon box for its click center, exclude text.
                mask[-max(1, round(16 * sy)):] = 0
            if np.count_nonzero(mask) < 20:
                continue
            values = cv2.matchTemplate(region, scaled, cv2.TM_CCOEFF_NORMED, mask=mask)
            values[~np.isfinite(values)] = -1
            _, score, _, point = cv2.minMaxLoc(values)
            if best is None or score > best.score:
                best = IconMatch(float(score), (x + point[0] + target_size[0] // 2, y + point[1] + target_size[1] // 2), path)
            # 小图外缘可能混入按钮底色。仅对原匹配已接近阈值的同一模板、
            # 同一尺寸和同一坐标验证更内侧像素；全面改用7×7会混淆相近黑服。
            if scene == "team" and TEAM_FALLBACK_PRIMARY_MIN <= score < THRESHOLD:
                inner = cv2.resize(inner_mask, target_size, interpolation=cv2.INTER_NEAREST)
                if np.count_nonzero(inner) < 20:
                    continue
                inner_values = cv2.matchTemplate(region, scaled, cv2.TM_CCOEFF_NORMED, mask=inner)
                inner_values[(~np.isfinite(inner_values)) | (values < TEAM_FALLBACK_PRIMARY_MIN)] = -1
                _, inner_score, _, inner_point = cv2.minMaxLoc(inner_values)
                if inner_score >= THRESHOLD and (fallback is None or inner_score > fallback.score):
                    fallback = IconMatch(float(inner_score),
                        (x + inner_point[0] + target_size[0] // 2, y + inner_point[1] + target_size[1] // 2),
                        path, float(values[inner_point[1], inner_point[0]]))
    return best if best is not None and best.score >= THRESHOLD else fallback or best


def _match(formation, icons, scene, name):
    result = match_icon(formation._shot(), icons, scene)
    if result is not None:
        detail = f" 内区复核前分数={result.primary_score:.4f}" if result.primary_score is not None else ""
        mfaalog.info(f"[御主礼装] {name} scene={scene} 最高分={result.score:.4f} 阈值={THRESHOLD:.2f} 模板={result.template} 坐标={result.center}{detail}")
    return result


def _stable_match(formation, icons, scene, name, *, max_checks=3):
    last = None
    for _ in range(max_checks):
        if formation.context.tasker.stopping:
            return None
        current = _match(formation, icons, scene, name)
        if current is not None and current.score >= THRESHOLD:
            if last is not None and max(abs(current.center[i] - last.center[i]) for i in (0, 1)) <= 6:
                return current
            last = current
        else:
            last = None
        time.sleep(0.25)
    return None


def _error(formation, message):
    context = formation.context
    if not context.tasker.stopping:
        try:
            context.override_pipeline({"御主礼装-用户提示": {"focus": {"Node.Recognition.Starting": html.escape(message, quote=True)}}})
            context.run_task("御主礼装-用户提示")
        except Exception as exc:
            mfaalog.warning(f"[御主礼装] 用户提示输出失败: {exc}")
    return formation._fail(message)


def ensure_mystic_code(formation, mystic_code_id, *, allow_change):
    """只读校验，或在不匹配时更换；任何失败均原地停止并保留现场。"""
    if mystic_code_id is None:
        return True
    if formation.context.tasker.stopping:
        return False
    try:
        item = _catalog().get(mystic_code_id)
        if item is None:
            return _error(formation, f"御主礼装资源缺失：ID {mystic_code_id}，请更新资源后重试。")
        name = f"{item['name']} (ID {mystic_code_id})"
        icons = _icons(formation, item)
        if _stable_match(formation, icons, "team", name):
            mfaalog.info(f"[御主礼装] 编队已匹配：{name}")
            return True
        if formation.context.tasker.stopping:
            return False
        if not allow_change:
            return _error(formation, f"御主礼装不匹配：需要{name}。请手动更换，或选择自动编队。已停止进入战斗。")
        if not formation._run_pipeline("御主礼装-打开"):
            return _error(formation, f"无法打开御主礼装界面：{name}，已停止进入战斗。")
        if not formation._run_pipeline("御主礼装-确认选择界面"):
            return _error(formation, "未能确认御主礼装选择界面，已停止并保留现场。")
        if not _stable_match(formation, icons, "selected", name):
            found = False
            for page in range(MAX_LIST_PAGES):
                if formation.context.tasker.stopping:
                    return False
                match = _stable_match(formation, icons, "list", name)
                if formation.context.tasker.stopping:
                    return False
                if match is not None:
                    formation.controller.post_click(*match.center).wait()
                    time.sleep(0.6)
                    # 装备与返回后可能先收到上一页缓存帧；给资源加载留下有限余量。
                    if not _stable_match(formation, icons, "selected", name, max_checks=8):
                        return _error(formation, f"御主礼装选择后复核失败：{name}，已停止并保留现场。")
                    found = True
                    break
                if page + 1 < MAX_LIST_PAGES and not formation._run_pipeline("御主礼装-列表左滑查找"):
                    return _error(formation, "御主礼装列表滑动失败，已停止并保留现场。")
            if not found:
                return _error(formation, f"未找到御主礼装：{name}，请确认账号已持有该礼装。已停止进入战斗。")
        if formation.context.tasker.stopping:
            return False
        if not formation._run_pipeline("御主礼装-返回编队"):
            return _error(formation, "御主礼装更换后返回编队失败，已停止并保留现场。")
        if not _stable_match(formation, icons, "team", name, max_checks=8):
            return _error(formation, f"编队御主礼装最终复核失败：{name}，已停止进入战斗。")
        mfaalog.info(f"[御主礼装] 已更换并复核：{name}")
        return True
    except (ValueError, OSError, KeyError, cv2.error) as exc:
        return _error(formation, f"御主礼装校验失败：{exc}，已停止进入战斗。")
