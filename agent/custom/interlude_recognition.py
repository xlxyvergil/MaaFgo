"""幕间列表与专属地图的逐行识别；颜色只筛行，文字确认行的业务类型。"""
import json
import re

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition


def _rect(box):
    if hasattr(box, "x"):
        return [int(box.x), int(box.y), int(box.w), int(box.h)]
    return [int(value) for value in box]


def _runs(indices, gap=5):
    if not len(indices):
        return []
    return np.split(indices, np.where(np.diff(indices) > gap)[0] + 1)


def row_candidates(image, params=None):
    """返回棕色卡片顶部；保留暗色候选供扫描报告，绝不凭饱和度直接选本。"""
    params = params or {}
    height, width = image.shape[:2]
    scale_x, scale_y = width / 1280, height / 720
    x, y, w, h = params.get("roi", [624, 95, 610, 615])
    x, y = max(0, round(x * scale_x)), max(0, round(y * scale_y))
    right, bottom = min(width, x + round(w * scale_x)), min(height, y + round(h * scale_y))
    left_sample, right_sample = round(790 * scale_x), round(1085 * scale_x)
    if x >= right or y >= bottom or left_sample >= right_sample:
        return []
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    warm = cv2.inRange(hsv, (3, 55, 40), (35, 255, 255)) > 0
    density = warm[y:bottom, left_sample:right_sample].mean(axis=1)
    bright = warm & (hsv[:, :, 2] >= float(params.get("min_brightness", 135)))
    bright_density = bright[y:bottom, left_sample:right_sample].mean(axis=1)
    minimum_ratio = float(params.get("warm_row_ratio", .40))
    bright_runs = [run for run in _runs(np.where(bright_density >= minimum_ratio)[0])
                   if 28 * scale_y <= int(run[-1] - run[0] + 1) <= 170 * scale_y]
    # 地图背景会与棕色卡片连成一条暗色带。先用亮色头部定位真实卡片，
    # 再补不与亮卡重叠的暗卡，防止把背景的y95当卡片起点并误判锁定。
    dark_runs = [run for run in _runs(np.where(density >= minimum_ratio)[0])
                 if not any(abs(int(run[0]) - int(bright_run[0])) < 144 * scale_y
                            for bright_run in bright_runs)]
    runs = sorted(bright_runs + dark_runs, key=lambda run: int(run[0]))
    candidates = []
    for run in runs:
        run_height = int(run[-1] - run[0] + 1)
        # 专属地图背景也可能呈棕色；卡片色带最多一行高，不能把背景当列表行。
        if not 28 * scale_y <= run_height <= 170 * scale_y:
            continue
        top = y + int(run[0])
        row_height = min(round(144 * scale_y), bottom - top)
        if row_height < 45 * scale_y:
            continue
        header = hsv[top:min(top + round(44 * scale_y), bottom),
                     round(800 * scale_x):round(1080 * scale_x)]
        if not header.size:
            continue
        candidates.append({
            "row_box": [x, top, right - x, row_height],
            "brightness": float(np.median(header[:, :, 2])),
            "warm_ratio": float(np.mean(warm[top:min(top + round(44 * scale_y), bottom),
                                              left_sample:right_sample])),
            "clipped": top + round(144 * scale_y) > bottom,
        })
    return candidates


def _ocr_items(result):
    if not result:
        return []
    items = []
    for item in result.all_results:
        text = str(getattr(item, "text", "") or "").strip()
        if text and getattr(item, "box", None) is not None:
            items.append({"text": text, "box": _rect(item.box),
                          "score": float(getattr(item, "score", 0))})
    return sorted(items, key=lambda item: (item["box"][1], item["box"][0]))


def _free_rows(context, image, params):
    """正向确认专属地图剩余的是绿色Free卡，不能把OCR失败当作空地图。"""
    height, width = image.shape[:2]
    sx, sy = width / 1280, height / 720
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (36, 65, 55), (96, 255, 255)) > 0
    y0, y1 = round(95 * sy), min(height, round(710 * sy))
    density = green[y0:y1, round(790 * sx):round(1085 * sx)].mean(axis=1)
    free_count = 0
    for run in _runs(np.where(density > .45)[0]):
        if not 28 * sy <= int(run[-1] - run[0] + 1) <= 170 * sy:
            continue
        top = y0 + int(run[0])
        roi = [round(630 * sx), top, round(470 * sx), min(round(144 * sy), y1 - top)]
        result = context.run_recognition("幕间-条目文字", image, {
            "幕间-条目文字": {"recognition": {"param": {"roi": roi}}}
        })
        text = re.sub(r"\s+", "", " ".join(item["text"] for item in _ocr_items(result))).lower()
        if ("自由" in text and "关卡" in text) or "freequest" in text or "フリークエスト" in text:
            free_count += 1
    return free_count


def scan_entries(context, image, *, map_mode=False, params=None):
    """状态扫描即使没有可挑战项也返回报告；不确定行与置灰行分别计数。"""
    params = params or {}
    report = {"entries": [], "locked_count": 0, "row_count": 0, "uncertain_count": 0,
              "completed_count": 0, "free_count": 0, "empty_confirmed": False,
              "rows": [], "map": bool(map_mode)}
    local = context.clone()
    height, width = image.shape[:2]
    sx, sy = width / 1280, height / 720
    for candidate in row_candidates(image, params):
        if context.tasker.stopping:
            return report
        x, y, w, h = candidate["row_box"]
        roi = [round(765 * sx), y, round(335 * sx), h]
        result = local.run_recognition("幕间-条目文字", image, {
            "幕间-条目文字": {"recognition": {"param": {"roi": roi}}}
        })
        items = _ocr_items(result)
        texts = [item["text"] for item in items]
        compact = re.sub(r"\s+", "", " ".join(texts)).lower()
        candidate["ocr"] = items
        # 固定色带只负责找候选；绿色 Free 行和地图背景没有这三种幕间标识。
        stage = "前往故事" in compact or "物語の舞台" in compact
        # 国服字距较大的英文实测会拆为lnterlu/e，I也会读成小写l。
        interlude = re.search(r"[il1]nterlu(?:d)?e?", compact) is not None or "幕间物语" in compact or "幕間の物語" in compact
        ap = re.search(r"ap[0-9o]", compact) is not None
        if map_mode and ap and not interlude:
            marker = local.run_recognition("幕间-幕间标记", image, {
                "幕间-幕间标记": {"recognition": {"param": {
                    "roi": [round(780 * sx), y + round(44 * sy), round(170 * sx), round(24 * sy)]
                }}}
            })
            marker_items = _ocr_items(marker)
            marker_text = re.sub(r"\s+", "", " ".join(item["text"] for item in marker_items)).lower()
            candidate["marker_ocr"] = marker_items
            interlude = re.search(r"[il1]nterlu(?:d)?e?", marker_text) is not None
        # 日服条件行会以“絆Lv.4で開放”等结尾；“開放条件緩和中”和
        # “編成条件あり”分别表示临时放宽和编队限制，不能据此跳过已开放任务。
        locked_text = any(re.search(
            r"(?:(?:后开放|後開放)(?!済|中)|未开放|未開放|未解锁|未开启|"
            r"(?:で|後(?:に)?)(?:開放|解放)(?!済|中))", re.sub(r"\s+", "", text))
                          for text in texts)
        progress = any(label in compact for label in ("任务进行度", "任务进度", "クエスト進行度"))
        task_evidence = stage or interlude or ap or progress or locked_text
        dark = candidate["brightness"] < float(params.get("min_brightness", 135))
        # 地图背景棕色候选既无任务标记也无锁定说明时忽略；列表色带则须报告未知。
        if map_mode and dark and not task_evidence:
            continue
        report["row_count"] += 1
        report["rows"].append(candidate)
        if dark and not task_evidence:
            report["uncertain_count"] += 1
            candidate["status"] = "uncertain"
            continue
        if dark or locked_text:
            report["locked_count"] += 1
            candidate["status"] = "locked"
            continue
        completed = any(re.search(r"clear|(?:^|[-－])(?:クリア(?:済み)?[!！]?|完成)(?:$|[-－])",
                                  item["text"], re.I)
                        and item["box"][1] >= y + round(70 * sy) for item in items)
        if map_mode:
            # 完成角标在卡片左下、标题OCR区域之外，复用国日服各自的Free完成模板。
            # 只查当前行下沿，下面绿色Free的完成标签不能把本行判成已完成。
            clear_roi = [round(624 * sx), y + round(100 * sy), round(200 * sx),
                         max(1, min(round(75 * sy), height - y - round(100 * sy)))]
            clear = local.run_recognition("Free-完成角标", image, {
                "Free-完成角标": {"recognition": {"param": {"roi": clear_roi}}}
            })
            completed |= bool(clear and clear.hit)
        if completed:
            report["completed_count"] += 1
            candidate["status"] = "completed"
            continue
        # 专属地图只认 Interlude 战斗卡；普通 Free 即使可挑战也绝不能选。
        accepted = (interlude and ap) if map_mode else (stage or (ap and (interlude or progress)))
        if not accepted:
            report["uncertain_count"] += 1
            candidate["status"] = "uncertain"
            continue
        title_items = []
        for item in items:
            normalized = re.sub(r"\s+", "", item["text"]).lower()
            marker_text = re.fullmatch(r"[il1]nt(?:er)?(?:[il1]u)?d?e?", normalized)
            if (item["box"][1] >= y + round(50 * sy) or item["score"] < .85
                    or marker_text or re.fullmatch(r"[a-z]", normalized)
                    or re.search(r"pickup|推荐|职阶|消耗|任务进[行]?度|消費|クエスト進行度|推奨|クラス|"
                                 r"^(?:new|新)[!！]?$|^前回(?:実行)?$|^上次(?:执行|執行|进行|進行)$",
                                 normalized)):
                continue
            title_items.append(item)
        # 背景地图碎字偶尔出现在卡片上沿，低置信或更早的字框不能抢走真实标题。
        title_items.sort(key=lambda item: (-item["score"], item["box"][1], item["box"][0]))
        title = title_items[0]["text"] if title_items else ""
        kind = "map" if map_mode else ("stage" if stage else "direct")
        # x950远离最右的任务信息/奖励按钮；即使底部菜单遮住卡片右端也不点菜单。
        box = [round(950 * sx), min(y + round(74 * sy), y + h - 5), 1, 1]
        entry = {"box": box, "row_box": candidate["row_box"], "kind": kind,
                 "title": title, "title_box": title_items[0]["box"] if title_items else None,
                 "brightness": candidate["brightness"], "warm_ratio": candidate["warm_ratio"],
                 "ocr": items}
        candidate["status"] = "available"
        report["entries"].append(entry)
    if map_mode and not report["entries"]:
        report["free_count"] = _free_rows(local, image, params)
        report["empty_confirmed"] = bool(
            not report["uncertain_count"]
            and (report["free_count"] or report["completed_count"] or report["locked_count"]))
    if not report["row_count"] and not report["empty_confirmed"]:
        report["uncertain_count"] = max(1, report["uncertain_count"])
    return report


def _scan(context, argv, *, map_mode=False):
    params = json.loads(argv.custom_recognition_param or "{}")
    return scan_entries(context, argv.image, map_mode=map_mode, params=params)


@AgentServer.custom_recognition("interlude_entry")
class InterludeEntry(CustomRecognition):
    def analyze(self, context, argv):
        report = _scan(context, argv)
        if report["entries"]:
            entry = report["entries"][0]
            return CustomRecognition.AnalyzeResult(box=entry["box"], detail=entry)
        return None


@AgentServer.custom_recognition("interlude_quest")
class InterludeQuest(CustomRecognition):
    def analyze(self, context, argv):
        report = _scan(context, argv, map_mode=True)
        if report["entries"]:
            entry = report["entries"][0]
            return CustomRecognition.AnalyzeResult(box=entry["box"], detail=entry)
        return None


@AgentServer.custom_recognition("interlude_scan")
class InterludeScan(CustomRecognition):
    def analyze(self, context, argv):
        params = json.loads(argv.custom_recognition_param or "{}")
        report = scan_entries(context, argv.image, map_mode=params.get("map", False), params=params)
        return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail=report)
