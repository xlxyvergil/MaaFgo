"""识别战斗主界面的三位前排从者；只读截图，不连接设备或操作游戏。

例：python tools/recognize_battle_frontline.py screenshot.png --output result.json
支持多个截图；同一次调用复用模板索引。Python 调用使用
battle.perception.status_catalog.StatusCatalogRecognizer。

将 agent 加入 Python 模块路径后，可复用实例：
    matcher = StatusCatalogRecognizer()
    result = matcher.detect(bgr_image)
返回 slots 中的 servant_id；无法确认时为 None。索引只缓存模板，不缓存
截图或槽位身份。默认 margin_scope=shortlist；--full-audit 才是全库分差。
调用方须提供稳定的战斗主界面，本工具不判断页面、空位、助战或候补。
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys


def positive_int(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("线程数必须是至少为 1 的整数") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("线程数必须至少为 1")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path, help="16:9 战斗主界面截图")
    parser.add_argument("--output", "-o", type=Path, help="JSON 输出文件；省略时写入标准输出")
    parser.add_argument("--status-root", action="append", type=Path,
                        help="ServantStatus 目录，可多次指定；默认使用项目资源")
    parser.add_argument("--min-score", type=float, default=0.90, help="最低精排分数，默认 0.90")
    parser.add_argument("--min-margin", type=float, default=0.05, help="最低不同身份分差，默认 0.05")
    parser.add_argument("--full-audit", action="store_true",
                        help="对全库精排，输出全库次名与分差；耗时较长")
    parser.add_argument("--threads", type=positive_int, default=1,
                        help="当前脚本进程的 OpenCV/BLAS 线程数，默认 1")
    args = parser.parse_args(argv)

    if args.output and any(args.output.resolve() == path.resolve() for path in args.images):
        print("输出文件与输入截图相同，无法保存识别结果。请用 --output 指定另一个 JSON 文件，"
              "以保留原始截图。", file=sys.stderr)
        return 2

    # Only the standalone process sets thread limits. Importing the recognizer
    # in an Agent must leave the caller's process-wide settings untouched.
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[key] = str(args.threads)
    try:
        import cv2
        import numpy as np

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
        from battle.perception.status_catalog import StatusCatalogRecognizer
    except ImportError as exc:
        print("无法加载头像识别依赖，不能分析截图。请使用已安装项目依赖的 Python，"
              f"或先安装 numpy 和 opencv-python 后重试。原因：{exc}", file=sys.stderr)
        return 2

    cv2.setNumThreads(args.threads)
    try:
        matcher = StatusCatalogRecognizer(args.status_root, min_score=args.min_score,
                                          min_margin=args.min_margin)
        results = []
        for path in args.images:
            try:
                image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8),
                                     cv2.IMREAD_COLOR)
            except OSError as exc:
                raise ValueError(f"无法读取截图“{path}”。请检查文件是否存在且可读后重试。原因：{exc}") from exc
            if image is None:
                raise ValueError(f"截图“{path}”不是可读取的图片。请重新保存原始游戏截图后重试。")
            result = matcher.detect(image, full_audit=args.full_audit)
            results.append({"image": str(path.resolve()), "size": list(image.shape[1::-1]),
                            **asdict(result)})
        report = {"schema_version": 1, "index": matcher.index_stats, "results": results}
        output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (ValueError, OSError, cv2.error) as exc:
        print(f"战斗头像识别未能完成：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
