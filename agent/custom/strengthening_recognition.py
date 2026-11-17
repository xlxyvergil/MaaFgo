"""强化任务列表逐行识别；共享幕间的卡片定位，不共享幕间/地图语义。"""
import json
import re
import unicodedata

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition

from interlude_recognition import _ocr_items, row_candidates


def scan_strengthening(context, image, params=None):
    params = params or {}
    report = {"entries": [], "row_count": 0, "locked_count": 0,
              "completed_count": 0, "uncertain_count": 0, "rows": []}
    local = context.clone()
    sx, sy = image.shape[1] / 1280, image.shape[0] / 720
    for candidate in row_candidates(image, params):
        if context.tasker.stopping:
            return report
        # 下沿残卡随下一次重叠翻页再扫描，不能把半行当完整条目点击。
        if candidate["clipped"]:
            continue
        x, y, w, h = candidate["row_box"]
        result = local.run_recognition("强化-条目文字", image, {
            "强化-条目文字": {"recognition": {"param": {
                "roi": [round(765 * sx), y, round(335 * sx), h]
            }}}
        })
        items = _ocr_items(result)
        compact = re.sub(r"\s+", "", unicodedata.normalize(
            "NFKC", " ".join(i["text"] for i in items))).lower()
        candidate["ocr"] = items
        report["row_count"] += 1
        report["rows"].append(candidate)
        marker = ("强化关卡" in compact or "强化任务" in compact or "強化クエスト" in compact
                  or "strengthenquest" in compact)
        # 读取本行实际消耗：0、半减及普通AP均可；未识别到数值时保留None，不能默认0。
        # 不拼接不同OCR框的数字：右侧奖励碎字“1”不能把AP23串成AP231。
        # 只读取当前卡片左下AP消耗区，避开更下方的活动半减说明。
        costs = set()
        for item in items:
            if not (item["box"][0] < round(900 * sx)
                    and y + round(45 * sy) <= item["box"][1] < y + round(120 * sy)):
                continue
            text = unicodedata.normalize("NFKC", item["text"])
            match = re.search(r"(?<![a-z])ap\s*([0-9]+|o)", text, re.I)
            if match:
                costs.add(int(match.group(1).lower().replace("o", "0")))
        ap_cost = next(iter(costs)) if len(costs) == 1 else None
        candidate["ap_cost"] = ap_cost
        progress = any(s in compact for s in ("任务进行度", "任务进度", "クエスト進行度"))
        locked = re.search(
            r"(?:(?:后开放|後開放)(?!済|中)|未开放|未开启|未解锁|未開放|"
            r"(?:で|後(?:に)?)(?:開放|解放)(?!済|中))", compact) is not None
        evidence = marker or progress or locked
        if not evidence:
            status = "uncertain"
        elif locked or candidate["brightness"] < params.get("min_brightness", 135):
            status = "locked"
        elif any(re.fullmatch(r"[-－]*(?:clear[!！]?|完成|クリア(?:済み)?[!！]?)[-－]*", i["text"], re.I)
                 and i["box"][1] >= y + round(70 * sy) for i in items):
            status = "completed"
        elif marker and ap_cost is not None and progress:
            status = "available"
        else:
            status = "uncertain"
        candidate["status"] = status
        if status != "available":
            report[status + "_count"] += 1
            continue
        title = next((i["text"] for i in items
                      if any(s in i["text"] for s in ("强化关卡", "强化任务", "強化クエスト"))), "已开启强化关卡")
        report["entries"].append({"box": [round(950 * sx), y + round(74 * sy), 1, 1],
                                  "title": title, "ap_cost": ap_cost,
                                  "row_box": candidate["row_box"], "ocr": items})
    if not report["row_count"]:
        report["uncertain_count"] = max(1, report["uncertain_count"])
    return report


@AgentServer.custom_recognition("strengthening_entry")
class StrengtheningEntry(CustomRecognition):
    def analyze(self, context, argv):
        report = scan_strengthening(context, argv.image, json.loads(argv.custom_recognition_param or "{}"))
        if report["entries"]:
            entry = report["entries"][0]
            return CustomRecognition.AnalyzeResult(box=entry["box"], detail=entry)
        return None


@AgentServer.custom_recognition("strengthening_scan")
class StrengtheningScan(CustomRecognition):
    def analyze(self, context, argv):
        report = scan_strengthening(context, argv.image, json.loads(argv.custom_recognition_param or "{}"))
        return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail=report)
