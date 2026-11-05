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

from ..core.models import (Confidence, FormationSlot, InitialFormation, REQUIRED_FRAME_COUNT,
                           VERIFIED_FORMATION_SOURCE, CONTINUOUS_FORMATION_SOURCE)
from .bond_stop_state import bond_stops


SESSION_NODE = "编队身份-会话"
GATE_NODE = "进本-点击队伍确认"
CAPTURE_NODE = "编队身份-采集"
TEAPOT_NODE = "原生战斗-校验茶壶"
FALLBACK_GATE_NODE = "进本-助战兜底跳过编队"
FALLBACK_TEAPOT_NODE = "原生战斗-兜底校验茶壶"
START_NODE = "进本-实际点击队伍确认"
CONTINUE_NODE = "连续出击-记录初始编队"


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
    support_fallback: bool = False
    capture_seed: tuple[FormationSlot, ...] = ()
    continuation: InitialFormation | None = None
    continuation_battle: int = 0


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
            bond_stops.clear_formation(task_id)
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
            elif battle != session.battle and session.error == "formation_update_pending":
                self._invalidate(session, "unfinished_formation_update")
            if session.continuation is not None and battle != session.continuation_battle:
                self._invalidate(session, "")
            session.battle = battle

    @staticmethod
    def _invalidate(session: _Session, reason: str) -> None:
        session.revision += 1
        session.snapshot = None
        session.collecting = False
        session.frames.clear()
        session.capture_seed = ()
        session.continuation = None
        session.continuation_battle = 0
        session.error = reason

    def prepare_continuation(self, task_id: int, token: str) -> bool:
        """仅在实际点击连续出击后保存初始编队；选择助战前不暴露旧快照。"""
        with self._lock:
            session = self._get(task_id, token)
            snapshot = session.snapshot
            eligible = (session.repeat and not session.error and not session.collecting
                        and not session.support_fallback and snapshot is not None
                        and snapshot.captured_battle == session.battle
                        and snapshot.source in {"formation_confirmation", VERIFIED_FORMATION_SOURCE,
                                                CONTINUOUS_FORMATION_SOURCE}
                        and snapshot.calibration_id not in {"", "unverified"}
                        and sum(s.is_support is True for s in snapshot.slots) == 1
                        and all(type(s.is_support) is bool for s in snapshot.slots))
            self._invalidate(session, session.error or ("unfinished_capture" if session.collecting else ""))
            bond_stops.clear_formation(task_id)
            if eligible:
                session.continuation = snapshot
                session.continuation_battle = session.battle + 1
            return bool(eligible)

    def support_selected(self, task_id: int, token: str, servant_id: str | None,
                         *, confidence: Confidence = Confidence(0.0),
                         matched_template: str = "") -> InitialFormation | None:
        """使用本次头像匹配成功并实际点击的助战，不使用未验证的筛选条件补身份。"""
        with self._lock:
            session = self._get(task_id, token)
            previous = session.continuation
            can_reuse = (previous is not None and session.repeat and not session.error
                         and not session.collecting and not session.support_fallback
                         and session.continuation_battle == session.battle
                         and isinstance(servant_id, str) and servant_id.isdecimal()
                         and int(servant_id) > 0)
            self._invalidate(session, session.error or ("unfinished_capture" if session.collecting else ""))
            session.support_fallback = False
            bond_stops.clear_formation(task_id)
            if not can_reuse:
                return None
            # 相同从者可能同时出现在自有位和助战位，必须按唯一助战标记替换。
            slots = tuple(FormationSlot(
                s.slot, "identified", servant_id, is_support=True,
                identity_confidence=confidence, matched_template=matched_template,
                reason="continuous_support_selected", search_scope="support_selection",
            ) if s.is_support else s for s in previous.slots)
            snapshot = InitialFormation(
                slots, task_id, token, session.revision,
                datetime.now(timezone.utc).isoformat(), session.battle,
                source=CONTINUOUS_FORMATION_SOURCE, calibration_id=previous.calibration_id,
            )
            session.snapshot = snapshot
            bond_stops.set_formation(task_id, slots)
            return snapshot

    def invalidate(self, task_id: int, token: str, reason: str) -> None:
        with self._lock:
            session = self._get(task_id, token)
            self._invalidate(session, reason)
            bond_stops.clear_formation(task_id)

    def skip_capture(self, task_id: int, token: str) -> None:
        """丢弃本次采集并允许战斗在无初始快照时继续。"""
        with self._lock:
            session = self._get(task_id, token)
            self._invalidate(session, "")
            bond_stops.clear_formation(task_id)

    def set_support_fallback(self, task_id: int, token: str, active: bool) -> None:
        """兜底状态随当前助战保留；重新选到目标助战或结束会话后恢复。"""
        with self._lock:
            session = self._get(task_id, token)
            # 调用方只在实际选中助战/兜底成功后通知。即使类型没变，
            # 重新选到的助战和锁定编队也不应沿用旧身份。
            self._invalidate(session, "")
            session.support_fallback = active
            bond_stops.clear_formation(task_id)

    def support_fallback(self, task_id: int, token: str) -> bool:
        with self._lock:
            return self._get(task_id, token).support_fallback

    def begin_update(self, task_id: int, token: str) -> int:
        """编队修改前撤销旧结果，返回只允许本次成功结果发布的代次。"""
        with self._lock:
            session = self._get(task_id, token)
            self._invalidate(session, "formation_update_pending")
            bond_stops.clear_formation(task_id)
            return session.revision

    def abandon_update(self, task_id: int, token: str, revision: int) -> None:
        with self._lock:
            session = self._get(task_id, token)
            if session.revision == revision and session.error == "formation_update_pending":
                self._invalidate(session, "")

    def publish_verified(self, task_id: int, token: str, revision: int,
                         slots: tuple[FormationSlot, ...]) -> InitialFormation:
        with self._lock:
            session = self._get(task_id, token)
            if (session.revision != revision or session.error != "formation_update_pending"
                    or session.collecting or session.support_fallback):
                raise ValueError("expired or invalid formation update")
            snapshot = InitialFormation(
                slots=tuple(slots), task_id=task_id, session_id=token, revision=revision,
                captured_at=datetime.now(timezone.utc).isoformat(), captured_battle=session.battle,
                source=VERIFIED_FORMATION_SOURCE, calibration_id="chaldea-final-check-v1",
            )
            session.snapshot = snapshot
            session.error = ""
            bond_stops.set_formation(task_id, snapshot.slots)
            return snapshot

    def verified_snapshot(self, task_id: int, token: str) -> InitialFormation | None:
        with self._lock:
            session = self._get(task_id, token)
            snapshot = session.snapshot
            if (session.error or session.collecting or session.support_fallback or snapshot is None
                    or snapshot.source != VERIFIED_FORMATION_SOURCE
                    or snapshot.captured_battle != session.battle):
                return None
            return snapshot

    def begin_capture(self, task_id: int, token: str, *, reuse_verified=False) -> int:
        with self._lock:
            session = self._get(task_id, token)
            previous = self.verified_snapshot(task_id, token) if reuse_verified else None
            self._invalidate(session, "")
            bond_stops.clear_formation(task_id)
            session.capture_seed = previous.slots if previous is not None else ()
            session.collecting = True
            return session.revision

    def capture_seed(self, task_id: int, token: str, revision: int) -> tuple[FormationSlot, ...]:
        with self._lock:
            session = self._get(task_id, token)
            if not session.collecting or session.revision != revision:
                raise ValueError("expired formation capture")
            return session.capture_seed

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
            if len(session.frames) != REQUIRED_FRAME_COUNT:
                raise ValueError("independently sampled frames required")
            if session.capture_seed:
                # 已复核槽位不是新截图证据，不伪造独立采样帧数或视觉分数。
                slots = tuple(known if known.status != "unknown" else sampled
                              for known, sampled in zip(session.capture_seed, slots))
            snapshot = InitialFormation(
                slots=slots, task_id=task_id, session_id=token, revision=revision,
                captured_at=datetime.now(timezone.utc).isoformat(),
                captured_battle=session.battle, calibration_id=calibration_id,
                source=VERIFIED_FORMATION_SOURCE if session.capture_seed else "formation_confirmation",
            )
            session.snapshot = snapshot
            session.collecting = False
            session.frames.clear()
            session.capture_seed = ()
            bond_stops.set_formation(task_id, snapshot.slots)
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


def session_override(token: str, context=None) -> dict:
    # 只传递令牌，不将可变快照放入会被复制/字典合并的 pipeline attach。
    override = {
        SESSION_NODE: {"attach": {"session_id": token}},
        # 仅原生会话在编队汇合后检查茶壶；BBC 和独立 Chaldea 编队保留原路径。
        # 助战兜底不采集身份，但茶壶仍须按用户设置核对后才允许实际开始。
        GATE_NODE: {"next": [TEAPOT_NODE]},
        TEAPOT_NODE: {"next": [CAPTURE_NODE]},
        FALLBACK_GATE_NODE: {"next": [FALLBACK_TEAPOT_NODE]},
        FALLBACK_TEAPOT_NODE: {"next": [START_NODE]},
    }
    if context is not None:
        # 独立编队任务/旧资源可能没有连续出击节点，不创建悬空引用。
        # 保留当前任务已有的补体力等后继；重复安装会话时也只挂一次。
        continuation = context.get_node_data("连续出击_继续") or {}
        marker = context.get_node_data(CONTINUE_NODE) or {}
        following = continuation.get("next")
        if marker and isinstance(following, list):
            jump_back = f"[JumpBack]{CONTINUE_NODE}"
            override["连续出击_继续"] = {"next": [jump_back, *[
                item for item in following if item != jump_back
                and not (isinstance(item, dict) and item.get("name") == CONTINUE_NODE)
            ]]}
    return override
