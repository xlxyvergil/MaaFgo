"""初始编队的任务级内存会话。无 Maa / 图像 / 持久化依赖。

仅原生入口创建；Context 根任务号与随机会话令牌共同校验所有访问。
repeat 的 finally、Tasker 终态/停止回调负责清理。采集代次阻止过期回调
重新发布已失效的快照。这里不保存也不更新战斗中的前排身份。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from ..core.models import FormationSlot, InitialFormation


SESSION_NODE = "编队身份-会话"
GATE_NODE = "进本-点击队伍确认"
CAPTURE_NODE = "编队身份-采集"


@dataclass
class _Session:
    token: str
    repeat: bool
    battle: int = 1
    revision: int = 0
    snapshot: InitialFormation | None = None
    collecting: bool = False
    frames: list[tuple[FormationSlot, ...]] = field(default_factory=list)
    error: str = ""


class FormationSessions:
    def __init__(self):
        self._lock = RLock()
        self._sessions: dict[int, _Session] = {}
        self._owners: dict[int, str] = {}

    def task_started(self, task_id: int, owner: str) -> None:
        with self._lock:
            self._sessions.pop(task_id, None)
            self._owners[task_id] = owner

    def begin(self, task_id: int, *, repeat: bool = False) -> str:
        if type(task_id) is not int or task_id <= 0:
            raise ValueError("valid root task id required")
        with self._lock:
            token = uuid4().hex
            self._sessions[task_id] = _Session(token, repeat)
            return token

    def _get(self, task_id: int, token: str) -> _Session:
        session = self._sessions.get(task_id)
        if session is None or not token or session.token != token:
            raise ValueError("formation session missing or expired")
        return session

    def valid(self, task_id: int, token: str, *, repeat: bool = False) -> bool:
        with self._lock:
            try:
                session = self._get(task_id, token)
                return session.repeat if repeat else True
            except ValueError:
                return False

    def battle_started(self, task_id: int, token: str, battle: int) -> None:
        with self._lock:
            session = self._get(task_id, token)
            if battle != session.battle + 1 and battle != 1:
                raise ValueError("non-sequential formation session battle")
            if battle > 1 and not session.repeat:
                raise ValueError("single battle session cannot be reused")
            if session.collecting:
                self._invalidate(session, "unfinished_capture")
            session.battle = battle

    @staticmethod
    def _invalidate(session: _Session, reason: str) -> None:
        session.revision += 1
        session.snapshot = None
        session.collecting = False
        session.frames.clear()
        session.error = reason

    def invalidate(self, task_id: int, token: str, reason: str) -> None:
        with self._lock:
            session = self._get(task_id, token)
            self._invalidate(session, reason)

    def begin_capture(self, task_id: int, token: str) -> int:
        with self._lock:
            session = self._get(task_id, token)
            self._invalidate(session, "")
            session.collecting = True
            return session.revision

    def append_frame(self, task_id: int, token: str, revision: int,
                     slots: tuple[FormationSlot, ...], error: str = "") -> None:
        with self._lock:
            session = self._get(task_id, token)
            if not session.collecting or revision != session.revision:
                raise ValueError("expired formation capture")
            if error:
                session.error = error
            session.frames.append(slots)

    def frames(self, task_id: int, token: str, revision: int):
        with self._lock:
            session = self._get(task_id, token)
            if not session.collecting or revision != session.revision:
                raise ValueError("expired formation capture")
            if session.error:
                raise ValueError(session.error)
            return tuple(session.frames)

    def publish(self, task_id: int, token: str, revision: int,
                slots: tuple[FormationSlot, ...], calibration_id: str) -> InitialFormation:
        with self._lock:
            session = self._get(task_id, token)
            if not session.collecting or revision != session.revision or session.error:
                raise ValueError("invalid formation capture cannot be published")
            if len(session.frames) != 3:
                raise ValueError("three independently sampled frames required")
            snapshot = InitialFormation(
                slots=slots, task_id=task_id, session_id=token, revision=revision,
                captured_at=datetime.now(timezone.utc).isoformat(),
                captured_battle=session.battle, calibration_id=calibration_id,
            )
            session.snapshot = snapshot
            session.collecting = False
            session.frames.clear()
            return snapshot

    def read(self, task_id: int, token: str) -> tuple[InitialFormation | None, int]:
        with self._lock:
            session = self._get(task_id, token)
            # 失效后不可默默降级继续进本；从战斗页直接开始则 error 为空。
            if session.error or session.collecting:
                raise ValueError(session.error or "formation_capture_pending")
            return session.snapshot, session.battle

    def finish(self, task_id: int, token: str | None = None) -> None:
        with self._lock:
            session = self._sessions.get(task_id)
            if token is not None and (session is None or session.token != token):
                return
            self._sessions.pop(task_id, None)
            self._owners.pop(task_id, None)

    def stop_owner(self, owner: str) -> None:
        with self._lock:
            # 若客户端未提供 owner，保守清空，宁可失效也不跨停止继续使用。
            roots = set(self._sessions) | set(self._owners)
            for root in roots:
                if not owner or self._owners.get(root, "") in {"", owner}:
                    self.finish(root)


sessions = FormationSessions()


def context_session(context) -> tuple[int, str]:
    """v5.10.1/v5.12.3 Context 根任务 ID 在 run_task 克隆间保留。"""
    root = context.get_task_job().job_id
    node = context.get_node_data(SESSION_NODE) or {}
    token = (node.get("attach") or {}).get("session_id", "")
    return root, token if isinstance(token, str) else ""


def session_override(token: str) -> dict:
    # 只传递令牌，不将可变快照放入会被复制/字典合并的 pipeline attach。
    return {
        SESSION_NODE: {"attach": {"session_id": token}},
        GATE_NODE: {"next": [CAPTURE_NODE]},
    }
