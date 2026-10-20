"""Local servant names for battle identity logs."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _catalog():
    path = Path(__file__).resolve().parents[2] / "custom" / "servant_list.json"
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)["servants"]


def servant_name(servant_id):
    return next((item["name"] for item in _catalog()
                 if str(item["id"]) == servant_id), servant_id or "未知")
