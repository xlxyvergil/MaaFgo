"""Local servant names for selecting and logging a preferred attacker."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _catalog():
    path = Path(__file__).resolve().parents[2] / "custom" / "servant_list.json"
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)["servants"]


def _key(value):
    return "".join(char for char in str(value).casefold() if char.isalnum())


def resolve_servant_id(value):
    wanted = _key(value)
    if not wanted:
        return None
    exact = []
    partial = []
    for servant in _catalog():
        sid = str(servant["id"])
        names = [_key(servant.get(field, "")) for field in ("name", "name_jp")]
        if wanted == sid or wanted in {str(alias) for alias in servant.get("aliases", [])}:
            return sid
        if wanted in names:
            exact.append(sid)
        elif any(wanted in name for name in names):
            partial.append(sid)
    matches = set(exact or partial)
    return matches.pop() if len(matches) == 1 else None


def servant_name(servant_id):
    return next((item["name"] for item in _catalog()
                 if str(item["id"]) == servant_id), servant_id or "未知")
