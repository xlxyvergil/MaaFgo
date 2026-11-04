"""Bounded, atomic storage for original Chaldea trees and separate editor drafts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 4 * 1024 * 1024


class EditorError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def project_identity(root: Path) -> str:
    # normcase matters for two differently cased paths to the same Windows install.
    return digest(os.path.normcase(str(root.resolve())).encode("utf-8"))


def decode_json(raw: bytes) -> Any:
    if len(raw) > MAX_JSON_BYTES:
        raise EditorError("too_large", "JSON 超过 4 MiB 限制", 413)
    try:
        def invalid(value):
            raise ValueError(f"不支持的数值: {value}")
        return json.loads(raw.decode("utf-8-sig"), parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise EditorError("invalid_json", f"JSON 无法读取：{exc}", 400) from exc


def encode_json(value: Any) -> bytes:
    try:
        raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise EditorError("invalid_json", f"数据无法序列化：{exc}") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise EditorError("too_large", "JSON 超过 4 MiB 限制", 413)
    return raw


def within(path: Path, base: Path) -> Path:
    resolved, boundary = path.resolve(), base.resolve()
    if not resolved.is_relative_to(boundary):
        raise EditorError("unsafe_path", "路径超出本实例允许的目录", 403)
    return resolved


def battle_name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise EditorError("invalid_name", "请使用 1–160 字符的 JSON 文件名")
    if value != value.strip() or value.endswith((".", " ")) or value.startswith("."):
        raise EditorError("invalid_name", "文件名不能以点开头或以空格、点结尾")
    if any(ord(c) < 32 or c in '\\/:*?"<>|' for c in value):
        raise EditorError("invalid_name", "文件名包含路径或 Windows 非法字符")
    if not value.lower().endswith(".json"):
        value += ".json"
    stem = value.split(".")[0].upper()
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]", stem):
        raise EditorError("invalid_name", "文件名为 Windows 保留名称")
    return value


class BattleStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.battle_dir = self.root / "config" / "Battle"
        self.editor_dir = self.root / "config" / "BattleEditor"
        self.draft_dir = self.editor_dir / "drafts"
        self.backup_dir = self.editor_dir / "backups"
        self.lock = threading.RLock()
        for directory in (self.battle_dir, self.draft_dir, self.backup_dir):
            within(directory, self.root)
            directory.mkdir(parents=True, exist_ok=True)
            within(directory, self.root)

    def _path(self, name: str) -> Path:
        return within(self.battle_dir / battle_name(name), self.battle_dir)

    def _read(self, path: Path) -> bytes:
        if not path.is_file():
            raise EditorError("not_found", "文件不存在，请刷新方案库", 404)
        if path.stat().st_size > MAX_JSON_BYTES:
            raise EditorError("too_large", "JSON 超过 4 MiB 限制", 413)
        return path.read_bytes()

    def read_battle(self, name: str) -> dict:
        with self.lock:
            path = self._path(name)
            raw = self._read(path)
            return {"name": path.name, "data": decode_json(raw), "hash": digest(raw), "path": str(path)}

    def list_battles(self) -> dict:
        rows = []
        with self.lock:
            for file in sorted(self.battle_dir.glob("*.json"), key=lambda p: p.name.casefold()):
                row = {"name": file.name, "title": file.stem, "turnCount": 0, "servants": []}
                try:
                    path = self._path(file.name)
                    row["modifiedAt"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                    item = self.read_battle(file.name)
                    row["hash"] = item["hash"]
                    data = item["data"]
                    if not isinstance(data, dict):
                        raise EditorError("invalid_structure", "缺少 Chaldea team 对象")
                    team = data.get("team")
                    if not isinstance(team, dict):
                        # Strategy/actions-only files are valid editor documents too.
                        # Validate their contract, but project an empty formation only
                        # for the summary; listing must never materialize JSON fields.
                        from .validation import validate_battle
                        report = validate_battle(data)
                        error = next((item for item in report["issues"] if item["severity"] == "error"), None)
                        if error:
                            raise EditorError("invalid_structure", f"{error['path']}: {error['message']}")
                        team = {}
                    if team.get("name") is not None and not isinstance(team["name"], str):
                        raise EditorError("invalid_structure", "team.name 不是字符串，请修正原文件")
                    row["title"] = team.get("name") or file.stem
                    front = team.get("onFieldSvts")
                    row["servants"] = [s.get("svtId") if isinstance(s, dict) else None for s in (front if isinstance(front, list) else [])]
                    actions = data.get("actions")
                    row["turnCount"] = sum(isinstance(a, dict) and a.get("type") == "attack" for a in (actions if isinstance(actions, list) else []))
                except (EditorError, OSError) as exc:
                    row["error"] = str(exc)
                rows.append(row)
        return {"battles": rows, "directory": str(self.battle_dir)}

    def _atomic(self, path: Path, raw: bytes, *, create: bool = False) -> None:
        # Temp file is on the target filesystem; neither a crash nor disk full exposes half a JSON.
        fd, temp_name = tempfile.mkstemp(prefix=".editor-", suffix=".tmp", dir=path.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if create:
                # Windows rename is atomic and never replaces an existing name, including
                # removable exFAT volumes without hard links. POSIX rename overwrites, so
                # use link there for the equivalent no-replace operation.
                try:
                    if os.name == "nt":
                        os.rename(temp, path)
                    else:
                        os.link(temp, path)
                except FileExistsError as exc:
                    raise EditorError("already_exists", "同名文件已经存在，请另存或打开后覆盖", 409) from exc
            else:
                os.replace(temp, path)
            actual = self._read(path)
            if actual != raw or digest(actual) != digest(raw):
                raise EditorError("verification_failed", "写入后的内容或哈希校验失败；原文件备份已保留", 500)
        finally:
            temp.unlink(missing_ok=True)

    def save_battle(self, name: str, data: Any, expected_hash: str | None = None, *, create: bool = False) -> dict:
        raw = encode_json(data)
        with self.lock:
            path = self._path(name)
            if create:
                if path.stem.isdigit():
                    raise EditorError("invalid_name", "新建文件名请包含队名，避免与线上分享 ID 混淆")
                if path.exists():
                    raise EditorError("already_exists", "同名文件已经存在，请另存或打开后覆盖", 409)
            else:
                if not isinstance(expected_hash, str) or not re.fullmatch("[0-9a-f]{64}", expected_hash):
                    raise EditorError("hash_required", "覆盖前必须提供打开文件时的 expectedHash")
                old = self._read(path)
                current_hash = digest(old)
                if current_hash != expected_hash:
                    raise EditorError("file_conflict", "该文件已被其他窗口或程序修改，请重新载入或另存", 409, {"currentHash": current_hash})
                backup = within(self.backup_dir / (path.stem + "_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8] + ".json"), self.backup_dir)
                self._atomic(backup, old, create=True)
                if digest(self._read(path)) != current_hash:
                    raise EditorError("file_conflict", "备份时原文件又发生变化，请重新载入或另存", 409, {"currentHash": digest(self._read(path))})
            self._atomic(path, raw, create=create)
            return self.read_battle(path.name)

    def _draft_path(self, draft_id: str) -> Path:
        if not isinstance(draft_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", draft_id):
            raise EditorError("invalid_draft_id", "草稿 ID 不合法")
        return within(self.draft_dir / (draft_id + ".json"), self.draft_dir)

    def read_draft(self, draft_id: str) -> dict:
        with self.lock:
            return decode_json(self._read(self._draft_path(draft_id)))

    def list_drafts(self) -> dict:
        drafts = []
        with self.lock:
            for path in self.draft_dir.glob("*.json"):
                try:
                    item = self.read_draft(path.stem)
                    data = item.get("data")
                    team = data.get("team") if isinstance(data, dict) else None
                    if item.get("id") != path.stem or type(item.get("revision")) is not int or not isinstance(item.get("savedAt"), str):
                        raise EditorError("invalid_draft", "草稿元信息损坏")
                    title = team.get("name") if isinstance(team, dict) and isinstance(team.get("name"), str) else "未命名草稿"
                    meta = {key: item.get(key) for key in ("id", "revision", "savedAt", "sourceName", "sourceHash")}
                    for key in ("sourceName", "sourceHash"):
                        if not isinstance(meta.get(key), str):
                            meta[key] = None
                    drafts.append(meta | {"title": title})
                except (EditorError, OSError, AttributeError):
                    drafts.append({"id": path.stem, "error": "草稿损坏，原文件仍保留"})
        drafts.sort(key=lambda d: d.get("savedAt") or "", reverse=True)
        return {"drafts": drafts}

    def save_draft(self, draft_id: str, payload: dict) -> dict:
        with self.lock:
            path = self._draft_path(draft_id)
            if "data" not in payload:
                raise EditorError("missing_data", "缺少草稿 data")
            revision = payload.get("revision")
            if type(revision) is not int or revision < 0:
                raise EditorError("invalid_revision", "revision 必须是非负整数")
            for key in ("sourceName", "sourceHash"):
                if payload.get(key) is not None and not isinstance(payload[key], str):
                    raise EditorError("invalid_draft_metadata", f"草稿 {key} 必须是字符串或 null")
            current = self.read_draft(draft_id) if path.exists() else None
            current_revision = current.get("revision", 0) if current else 0
            if revision != current_revision:
                raise EditorError("draft_conflict", "此草稿已更新，请恢复最新草稿或使用新的草稿 ID", 409, {"currentRevision": current_revision})
            item = {"id": draft_id, "revision": revision + 1, "savedAt": utc_now(), "data": payload["data"], "editorState": payload.get("editorState"), "sourceName": payload.get("sourceName"), "sourceHash": payload.get("sourceHash")}
            self._atomic(path, encode_json(item), create=current is None)
            return item
