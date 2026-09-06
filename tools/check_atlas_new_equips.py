"""检查 Atlas Academy JP 是否存在本地礼装 CSV 尚未收录的新增图鉴号。"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fetch_equip(collection_no: int, region: str) -> dict[str, Any] | None:
    url = f"https://api.atlasacademy.io/nice/{region}/equip/{collection_no}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"图鉴号 {collection_no} 查询失败：HTTP {exc.code}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"图鉴号 {collection_no} 返回格式错误")
    return {
        "equipId": data.get("id"),
        "collectionNo": data.get("collectionNo"),
        "name_jp": data.get("name"),
        "rarity": data.get("rarity"),
        "type": data.get("type"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--region", default="JP", choices=("JP", "NA"))
    parser.add_argument("--lookahead", type=int, default=40)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.lookahead <= 0 or args.workers <= 0:
        raise ValueError("lookahead 与 workers 必须为正数")

    with args.csv_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    existing_collection = {int(row["collectionNo"]) for row in rows}
    first = max(existing_collection) + 1
    candidates = range(first, first + args.lookahead)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        found = [item for item in pool.map(lambda no: fetch_equip(no, args.region), candidates) if item]

    print(f"本地最大图鉴号：{first - 1}")
    print(f"Atlas 扫描范围：{first}–{first + args.lookahead - 1}")
    print(f"新增礼装：{len(found)}")
    print(json.dumps(found, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
