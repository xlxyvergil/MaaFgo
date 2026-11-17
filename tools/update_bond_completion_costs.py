# -*- coding: utf-8 -*-
"""由本地从者/礼装归档增量生成羁绊补齐 COST 索引。

FGO 的普通编成 COST 由稀有度固定决定；玛修、安哥拉曼纽与部分纪念礼装有特例。
未知的 0 星/NPC 数据不会被猜测写入。已有 ID 的手工 COST 始终保留。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SERVANT_COST_BY_RARITY = {1: 3, 2: 4, 3: 7, 4: 12, 5: 16}
EQUIP_COST_BY_RARITY = {1: 1, 2: 3, 3: 5, 4: 9, 5: 12}
SERVANT_SPECIAL_COST = {"800100": 0, "1100100": 4}
# 英灵肖像：玛修·基列莱特虽为四星，实际 COST 为 5（普通四星礼装为 9）。
EQUIP_SPECIAL_COST = {"9400970": 5}


def load(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--servants", type=Path, default=root / "agent/custom/servant_list.json")
    parser.add_argument("--equips", type=Path, default=root / "agent/custom/equip_list.json")
    parser.add_argument("--output", type=Path, default=root / "agent/custom/bond_completion_costs.json")
    args = parser.parse_args()

    old = load(args.output)
    servant_costs = {str(k): int(v) for k, v in (old.get("servants") or {}).items()}
    equip_costs = {str(k): int(v) for k, v in (old.get("equips") or {}).items()}

    with args.servants.open(encoding="utf-8-sig") as file:
        servants = json.load(file).get("servants", [])
    with args.equips.open(encoding="utf-8-sig") as file:
        equips = json.load(file).get("equips", [])

    for item in servants:
        sid = str(item.get("id") or "")
        if not sid or sid in servant_costs:
            continue
        if sid in SERVANT_SPECIAL_COST:
            servant_costs[sid] = SERVANT_SPECIAL_COST[sid]
            continue
        cost = SERVANT_COST_BY_RARITY.get(item.get("rarity"))
        if cost is not None:
            servant_costs[sid] = cost
    for item in equips:
        eid = str(item.get("id") or "")
        if not eid or eid in equip_costs:
            continue
        if eid in EQUIP_SPECIAL_COST:
            equip_costs[eid] = EQUIP_SPECIAL_COST[eid]
            continue
        cost = EQUIP_COST_BY_RARITY.get(item.get("rarity"))
        if cost is not None:
            equip_costs[eid] = cost

    output = {
        "schema_version": 1,
        "servants": dict(sorted(servant_costs.items(), key=lambda pair: int(pair[0]))),
        "equips": dict(sorted(equip_costs.items(), key=lambda pair: int(pair[0]))),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(f"servants={len(output['servants'])} equips={len(output['equips'])} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
