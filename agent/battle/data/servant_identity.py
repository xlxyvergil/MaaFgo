"""Local servant identities and names for battle rosters and logs."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _catalog():
    path = Path(__file__).resolve().parents[2] / "custom" / "servant_list.json"
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)["servants"]


@lru_cache(maxsize=1)
def _servant_lookup():
    from chaldea.servant_aliases import build_servant_lookup

    return build_servant_lookup(_catalog())


def _positive_servant_id(servant_id: object) -> str | None:
    if isinstance(servant_id, bool):
        return None
    if isinstance(servant_id, str):
        servant_id = servant_id.strip()
        if not servant_id.isdecimal():
            return None
        servant_id = int(servant_id)
    if not isinstance(servant_id, int) or servant_id <= 0:
        return None
    return str(servant_id)


def canonical_servant_id(servant_id: object) -> str | None:
    """归一有效从者 ID；目录外的正数 ID 保留，未知身份返回 None。"""
    sid = _positive_servant_id(servant_id)
    if sid is None:
        return None
    servant = _servant_lookup().get(sid)
    return str(servant["id"]) if servant is not None else sid


def servant_template_ids(servant_id: object) -> tuple[str, ...]:
    """同一规范身份的资源目录，包含目录中明确声明的变身形态。"""
    sid = _positive_servant_id(servant_id)
    if sid is None:
        return ()
    # 纯视觉模块只读本地目录，不因查找模板导入 Chaldea 的网络及日志模块。
    # 与 build_servant_lookup 一致：显式别名优先于可能残留的旧独立记录。
    servant = next((item for item in _catalog()
                    if sid in {str(alias) for alias in item.get("aliases", [])}), None)
    if servant is None:
        servant = next((item for item in _catalog() if str(item["id"]) == sid), None)
    if servant is None:
        return (sid,)
    canonical = str(servant["id"])
    aliases = sorted(str(alias) for alias in servant.get("aliases", []))
    return tuple(dict.fromkeys((canonical, *aliases)))


def servant_name(servant_id):
    return next((item["name"] for item in _catalog()
                 if str(item["id"]) == servant_id), servant_id or "未知")
