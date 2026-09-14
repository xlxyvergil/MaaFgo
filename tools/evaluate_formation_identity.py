"""离线运行与原生采集相同的识别器。无设备连接、无游戏输入。

用法见 docs/通用编队身份识别.md。清单中的每个正样本需要三个独立截图
文件和六槽标注；单张图片不能复制三次冒充多帧证据。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from maa.agent.agent_server import AgentServer

# 离线评估不启动 Agent，只禁止导入模块时向 Agent 注册；识别代码不替换。
with patch.object(AgentServer, "custom_action", lambda *args: lambda cls: cls), \
     patch.object(AgentServer, "custom_recognition", lambda *args: lambda cls: cls):
    from formation_action import (
        FormationCalibration, FormationIdentityReader, _read_image, merge_formation_frames,
    )


def evaluate(manifest_path: Path, calibration_data: dict, roots: list[Path]) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    calibration = FormationCalibration.parse(calibration_data)
    started = time.perf_counter()
    reader = FormationIdentityReader(roots, calibration)
    report = {
        "manifest": str(manifest_path.resolve()),
        "calibration": calibration_data,
        "timing_scope": "offline matcher only; excludes device/Agent/Pipeline latency",
        "reader_init_ms": (time.perf_counter() - started) * 1000,
        "coverage": reader.coverage, "samples": [],
    }
    metrics = {name: 0 for name in (
        "samples", "positive_samples", "negative_samples", "sample_errors",
        "page_false_accepts", "page_false_rejects", "annotated_occupied_slots",
        "correct_identities", "wrong_identities", "unknown_identities",
        "occupancy_errors", "support_errors", "class_errors",
    )}
    for sample in manifest["samples"]:
        metrics["samples"] += 1
        expected_page = sample.get("expected_page", True)
        metrics["positive_samples" if expected_page else "negative_samples"] += 1
        row = {"id": sample["id"], "source": sample.get("source", "unspecified"),
               "expected_page": expected_page, "frames": []}
        frames, frame_errors = [], []
        begin = time.perf_counter()
        try:
            paths = [(manifest_path.parent / p).resolve() for p in sample["frames"]]
            if not paths or (expected_page and (len(paths) != 3 or len(set(paths)) != 3)):
                raise ValueError("positive sample requires three distinct original image files")
            expected = sample.get("slots", [])
            if expected_page and [s.get("slot") for s in expected] != list(range(1, 7)):
                raise ValueError("positive sample requires six ordered slot annotations")
            if expected_page and any(s.get("status") != "empty" and not s.get("servant_id") for s in expected):
                raise ValueError("occupied slot annotation requires servant_id")
            for index, path in enumerate(paths):
                image_bytes = path.read_bytes()
                frame_row = {"path": str(path), "sha256": hashlib.sha256(image_bytes).hexdigest()}
                tick = time.perf_counter()
                try:
                    frame = reader.read_frame(_read_image(path), full_audit=index == 2, diagnostics=True)
                    frames.append(frame)
                    frame_row["slots"] = [asdict(s) for s in frame]
                except ValueError as exc:
                    frame_errors.append(str(exc))
                    frame_row["error"] = str(exc)
                frame_row["matcher_ms"] = (time.perf_counter() - tick) * 1000
                row["frames"].append(frame_row)
            if not expected_page:
                metrics["page_false_accepts"] += bool(frames)
            elif frame_errors:
                metrics["page_false_rejects"] += 1
            else:
                merged = merge_formation_frames(frames)
                row["slots"] = [asdict(s) for s in merged]
                for truth, prediction in zip(expected, merged):
                    empty = truth.get("status") == "empty"
                    metrics["occupancy_errors"] += (prediction.status == "empty") != empty
                    if not empty:
                        if not truth.get("servant_id"):
                            raise ValueError("occupied slot annotation requires servant_id")
                        metrics["annotated_occupied_slots"] += 1
                        if prediction.servant_id is None:
                            metrics["unknown_identities"] += 1
                        elif prediction.servant_id == str(truth["servant_id"]):
                            metrics["correct_identities"] += 1
                        else:
                            metrics["wrong_identities"] += 1
                    if "is_support" in truth:
                        metrics["support_errors"] += prediction.is_support != truth["is_support"]
                    if truth.get("class_id") and prediction.class_id is not None:
                        metrics["class_errors"] += prediction.class_id != truth["class_id"]
        except (ValueError, KeyError, OSError) as exc:
            row["sample_error"] = str(exc)
            metrics["sample_errors"] += 1
        row["elapsed_ms"] = (time.perf_counter() - begin) * 1000
        report["samples"].append(row)
    totals = [x["elapsed_ms"] for x in report["samples"]]
    report["metrics"] = metrics
    report["median_sample_ms"] = statistics.median(totals) if totals else None
    report["max_sample_ms"] = max(totals) if totals else None
    count = metrics["annotated_occupied_slots"]
    report["identity_unknown_rate"] = metrics["unknown_identities"] / count if count else None
    report["acceptance_complete"] = False
    report["acceptance_note"] = (
        "An offline report is evidence for its listed samples only; verify held-out coverage, "
        "calibration provenance and Agent/device timing before acceptance."
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--image-root", action="append", type=Path,
                        help="Resource image roots in override order; defaults to base")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    calibration = json.loads(args.calibration.read_text(encoding="utf-8")) if args.calibration else {}
    report = evaluate(args.manifest, calibration, args.image_root or [ROOT / "assets/resource/base/image"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "metrics": report["metrics"],
                      "reader_init_ms": report["reader_init_ms"],
                      "median_sample_ms": report["median_sample_ms"]}, ensure_ascii=False))
    metrics = report["metrics"]
    return int(any(metrics[k] for k in ("sample_errors", "wrong_identities", "page_false_accepts",
                                       "page_false_rejects", "occupancy_errors", "support_errors", "class_errors")))


if __name__ == "__main__":
    raise SystemExit(main())
