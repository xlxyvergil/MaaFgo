"""Read-only, bounded Chaldea team search for the desktop editor.

Quest IDs come from the reviewed local catalogue, never from a supplied URL.
Only small display summaries leave this module; importing a team still uses the
editor's existing Chaldea import and validation path.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import copy
from functools import lru_cache
import gzip
import io
import json
from pathlib import Path
import socket
import ssl
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .storage import EditorError


API_URL = "https://api.chaldea.center/api/v1/teams/"
PAGE_SIZE = 12
MAX_OFFSET = 12_000
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
MAX_CONTENT_CHARS = 256 * 1024
MAX_DECODED_BYTES = 1024 * 1024
NETWORK_TIMEOUT = 12
CACHE_SECONDS = 60
MAX_CACHE_ENTRIES = 128

_cache: OrderedDict[tuple[str, str, int], tuple[float, dict]] = OrderedDict()
_latest_totals: OrderedDict[str, tuple[float, int]] = OrderedDict()
_cache_lock = threading.Lock()
_requests = threading.BoundedSemaphore(3)


class CommonTeamsError(EditorError):
    """An actionable message that the editor server can return unchanged."""


@lru_cache(maxsize=1)
def _catalogue() -> dict:
    path = Path(__file__).with_name("data") / "grand_quests.json"
    try:
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError("catalogue too large")
        data = json.loads(raw)
        ids = {entry["id"] for entry in data["quests"]}
        if len(ids) != len(data["quests"]):
            raise ValueError("duplicate quest key")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CommonTeamsError(
            "common_teams_catalogue", "冠位战关卡目录无法读取，暂时不能查询常用队伍。请重新解压完整的 MXU 安装包后重试。", 500
        ) from exc


def common_team_catalog() -> dict:
    """Return catalogue metadata without accessing the network."""
    data = _catalogue()
    return copy.deepcopy({
        "classes": data["classes"], "quests": data["quests"],
        "verifiedAt": data["verifiedAt"],
    })


class _ChaldeaRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if (target.scheme != "https" or target.hostname != "api.chaldea.center"
                or target.port not in (None, 443) or target.username is not None
                or not target.path.startswith("/api/v1/teams/")):
            raise CommonTeamsError(
                "common_teams_redirect", "Chaldea 返回了异常的跳转地址，无法继续查询队伍。请稍后点击“重试”。", 502
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _invalid_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def _request_json(query: list[tuple[str, str | int]]) -> dict:
    url = API_URL + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={
        "User-Agent": "MaaFGO-BattleEditor/1.0",
        "Accept": "application/json", "Accept-Encoding": "identity",
    })
    # Default certificate validation and system proxy configuration are retained.
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _ChaldeaRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CommonTeamsError(
                "common_teams_too_large", "Chaldea 返回的队伍列表过大，无法安全读取。请稍后重试，或在 Chaldea 中复制队伍分享链接，使用“导入”。", 502
            )
        data = json.loads(raw.decode("utf-8"), parse_constant=_invalid_constant)
        if not isinstance(data, dict):
            raise ValueError("response is not an object")
        return data
    except CommonTeamsError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            message = "Chaldea 暂时限制了查询次数，无法继续获取队伍。请稍等一分钟后点击“重试”。"
        else:
            message = "Chaldea 暂时无法提供队伍列表，查询未完成。请稍后点击“重试”；也可复制 Chaldea 队伍分享链接，使用“导入”。"
        raise CommonTeamsError("common_teams_remote", message, 502) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise CommonTeamsError(
            "common_teams_timeout", "连接 Chaldea 超时，未能获取队伍列表。请检查网络连接后点击“重试”。", 502
        ) from exc
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        raise CommonTeamsError(
            "common_teams_network", "无法连接 Chaldea，未能获取队伍列表。请检查网络或代理设置后点击“重试”。", 502
        ) from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CommonTeamsError(
            "common_teams_response", "Chaldea 返回的队伍列表无法读取，查询未完成。请稍后点击“重试”。", 502
        ) from exc


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 2**53 - 1 else None


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value if char >= " " or char in "\n\t").strip()[:limit]


def _battle_data(row: dict) -> dict | None:
    """Decode gzip/base64 without allowing an unbounded decompression."""
    encoded = row.get("data") or row.get("content")
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_CONTENT_CHARS:
        return None
    if encoded.startswith("G"):
        encoded = encoded[1:]
    elif not encoded.startswith("H4s"):
        return None
    try:
        packed = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            raw = stream.read(MAX_DECODED_BYTES + 1)
        if len(raw) > MAX_DECODED_BYTES:
            return None
        decoded = json.loads(raw.decode("utf-8"), parse_constant=_invalid_constant)
        return decoded if isinstance(decoded, dict) else None
    except (ValueError, OSError, EOFError, UnicodeError, RecursionError, zlib.error):
        return None


def _equip_preview(value: Any, *, legacy: dict | None = None) -> dict | None:
    if value is not None and not isinstance(value, dict):
        return None
    equip = value if isinstance(value, dict) else {}
    legacy = legacy or {}
    # Match equipForDisplay: only an absent/null ID inherits the legacy CE;
    # zero explicitly clears it, and malformed modern data must not revive it.
    raw_id = equip.get("id")
    if raw_id is not None and _positive_int(raw_id) is None:
        return None
    equip_id = _positive_int(raw_id) or _positive_int(legacy.get("ceId")) or 0
    level = _positive_int(equip["lv"] if "lv" in equip else legacy.get("ceLv"))
    limit_break = equip["limitBreak"] if "limitBreak" in equip else legacy.get("ceLimitBreak")
    if type(limit_break) is not bool:
        limit_break = None
    if not equip_id and level is None and limit_break is None:
        return None
    return {"id": equip_id, "lv": level, "limitBreak": limit_break}


def _slot(slot: Any) -> dict | None:
    if not isinstance(slot, dict):
        return None
    svt_id = _positive_int(slot.get("svtId")) or 0
    grand = slot.get("grandSvt") is True
    equips = [_equip_preview(slot.get("equip1"), legacy=slot), None, None]
    bond_effect = "any"
    if grand:
        equips[1] = _equip_preview(slot.get("equip2"))
        equips[2] = _equip_preview(slot.get("equip3"))
        board = slot.get("classBoardData")
        effect = board.get("grandBondEquipSkillChange") if isinstance(board, dict) else None
        bond_effect = "np50" if effect is True else "original" if effect is False else "any"
    equip_id = equips[0]["id"] if equips[0] else 0
    if not svt_id and not any(equips) and bond_effect == "any":
        return None
    return {
        "svtId": svt_id,
        "support": slot.get("supportType") in ("friend", "fixed", "npc"),
        "equipId": equip_id,
        "grandSvt": grand,
        "equips": equips,
        "bondEffect": bond_effect,
    }


def _summary(row: Any) -> dict | None:
    if not isinstance(row, dict) or not _positive_int(row.get("id")):
        return None
    battle = _battle_data(row)
    team = battle.get("team") if battle else None
    if not isinstance(team, dict):
        team = {}
    servants = []
    for key in ("onFieldSvts", "backupSvts"):
        value = team.get(key)
        part = value[:3] if isinstance(value, list) else []
        servants.extend([_slot(part[index]) if index < len(part) else None for index in range(3)])
    actions = battle.get("actions") if battle else None
    turns = sum(isinstance(action, dict) and action.get("type") == "attack" for action in actions) if isinstance(actions, list) else None
    votes = row.get("votes") if isinstance(row.get("votes"), dict) else {}
    name = _text(team.get("name"), 160) or (_text(battle.get("name"), 160) if battle else "")
    return {
        "id": row["id"],
        "name": name,
        "author": _text(row.get("username"), 80),
        "createdAt": _positive_int(row.get("created_at")) or _positive_int(row.get("createdAt")),
        "upvotes": _positive_int(row.get("up")) or _positive_int(row.get("upvotes")) or _positive_int(votes.get("up")) or 0,
        "downvotes": _positive_int(row.get("down")) or _positive_int(row.get("downvotes")) or _positive_int(votes.get("down")) or 0,
        "turnCount": turns,
        "servants": servants,
    }


def _checked_page(data: Any, limit: int) -> tuple[list, int]:
    rows, total = (data.get("data"), data.get("total")) if isinstance(data, dict) else (None, None)
    if (not isinstance(rows, list) or len(rows) > limit or type(total) is not int
            or total < 0 or total > 2**53 - 1):
        raise CommonTeamsError("common_teams_response", "Chaldea 返回的队伍列表不完整，查询未完成。请稍后点击“重试”。", 502)
    return rows, total


def _search_page(quest: dict, sort: str, offset: int) -> tuple[list, int]:
    query = [("questId", quest_id) for quest_id in quest["questIds"]]
    query.extend([("phase", quest["phase"]), ("sort", sort)])
    if sort == "upvotes":
        return _checked_page(_request_json(query + [("limit", PAGE_SIZE), ("offset", offset)]), PAGE_SIZE)

    # The public API's `created` order is oldest first, and it has no descending
    # switch (verified against the official OpenAPI and real multi-page results).
    # Read backwards from a short-lived total snapshot, then reverse each page.
    # New uploads append beyond that snapshot until it expires, keeping adjacent
    # pages stable. The regular result cache prevents repeat network requests.
    with _cache_lock:
        cached = _latest_totals.get(quest["id"])
        total = cached[1] if cached and time.monotonic() - cached[0] < CACHE_SECONDS else None
    if total is None:
        _, total = _checked_page(_request_json(query + [("limit", 1), ("offset", 0)]), 1)
        with _cache_lock:
            _latest_totals[quest["id"]] = (time.monotonic(), total)
            _latest_totals.move_to_end(quest["id"])
            while len(_latest_totals) > MAX_CACHE_ENTRIES:
                _latest_totals.popitem(last=False)
    if offset >= total:
        return [], total
    count = min(PAGE_SIZE, total - offset)
    rows, current_total = _checked_page(
        _request_json(query + [("limit", count), ("offset", total - offset - count)]), count
    )
    if current_total < total:
        # A deletion can move the tail while a cached snapshot is in use. Realign
        # once rather than leaving a nonempty catalogue on a spurious empty page.
        total = current_total
        with _cache_lock:
            _latest_totals[quest["id"]] = (time.monotonic(), total)
        if offset >= total:
            return [], total
        count = min(PAGE_SIZE, total - offset)
        rows, _ = _checked_page(
            _request_json(query + [("limit", count), ("offset", total - offset - count)]), count
        )
    return list(reversed(rows)), total


def search_common_teams(quest_key: str, *, sort: str = "upvotes", offset: int = 0) -> dict:
    """Search one reviewed quest, combining only official equivalent quest IDs."""
    if not isinstance(quest_key, str):
        raise CommonTeamsError("common_teams_quest", "尚未选择有效的冠位战副本，无法查询队伍。请在“副本”中重新选择后重试。")
    quest = next((entry for entry in _catalogue()["quests"] if entry["id"] == quest_key), None)
    if quest is None:
        raise CommonTeamsError("common_teams_quest", "所选冠位战副本不在当前关卡目录中，无法查询队伍。请在“副本”中重新选择后重试。")
    if sort not in ("upvotes", "created"):
        raise CommonTeamsError("common_teams_sort", "不支持所选队伍排序方式，无法查询。请将排序改为“高赞”或“最新”后重试。")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET or offset % PAGE_SIZE:
        raise CommonTeamsError("common_teams_offset", "队伍列表页码不正确，无法查询。请重新打开“常用队伍”后重试。")
    key = (quest_key, sort, offset)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            _cache.move_to_end(key)
            return copy.deepcopy(cached[1])
    if not _requests.acquire(blocking=False):
        raise CommonTeamsError("common_teams_busy", "常用队伍正在查询其他列表，暂时无法继续。请等待查询完成后点击“重试”。", 503)
    try:
        rows, total = _search_page(quest, sort, offset)
        teams, seen = [], set()
        allowed = set(quest["questIds"])
        for row in rows:
            if isinstance(row, dict):
                # Never display a team for another quest if the API ignores a filter.
                remote_quest = row.get("quest_id", row.get("questId"))
                if remote_quest is not None and (_positive_int(remote_quest) is None or remote_quest not in allowed):
                    continue
                if row.get("phase") is not None and (type(row["phase"]) is not int or row["phase"] != quest["phase"]):
                    continue
            summary = _summary(row)
            if summary and summary["id"] not in seen:
                seen.add(summary["id"])
                teams.append(summary)
        has_more = bool(rows) and offset + PAGE_SIZE < total and offset + PAGE_SIZE <= MAX_OFFSET
        result = {
            "questId": quest_key, "phase": quest["phase"], "sort": sort,
            "offset": offset, "pageSize": PAGE_SIZE, "total": total,
            "hasMore": has_more, "nextOffset": offset + PAGE_SIZE if has_more else None,
            "teams": teams,
        }
        with _cache_lock:
            _cache[key] = (time.monotonic(), copy.deepcopy(result))
            _cache.move_to_end(key)
            while len(_cache) > MAX_CACHE_ENTRIES:
                _cache.popitem(last=False)
        return result
    finally:
        _requests.release()
