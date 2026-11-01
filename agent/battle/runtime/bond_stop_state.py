"""羁绊结算的根任务状态；独立于战斗帧和编队身份缓存的生命周期。"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any
from uuid import uuid4


@dataclass
class BondStop:
    reason: str
    message: str
    battle: int
    formation_slots: tuple[int, ...] = ()
    result_columns: tuple[int, ...] = ()
    image: Any = field(default=None, repr=False)
    latest_image: Any = field(default=None, repr=False)
    screenshot_path: str = ""
    latest_screenshot_path: str = ""
    screenshot_error: str = ""
    evidence_attempted: bool = False

    def display(self) -> str:
        text = self.message
        if self.screenshot_path:
            text += f"；截图已保存：{self.screenshot_path}"
        if self.latest_screenshot_path:
            text += f"；最新现场截图：{self.latest_screenshot_path}"
        if self.screenshot_error:
            text += f"；截图保存失败：{self.screenshot_error}"
        return text


@dataclass
class _Run:
    token: str
    owner: str = ""
    battle: int = 1
    formation: tuple[dict, ...] = ()
    checked_images: tuple[str, ...] = ()
    stopped: BondStop | None = None
    another_battle_planned: bool = False
    deferred: BondStop | None = None
    controller: Any = field(default=None, repr=False)


class BondStopStates:
    def __init__(self):
        self._lock = RLock()
        self._runs: dict[int, _Run] = {}

    def begin(self, root: int, owner: str = "") -> str:
        if type(root) is not int or root <= 0:
            raise ValueError("invalid bond-stop root task")
        with self._lock:
            previous = self._runs.get(root)
            run = _Run(uuid4().hex, owner or (previous.owner if previous else ""))
            self._runs[root] = run
            return run.token

    def ensure(self, root: int) -> str:
        with self._lock:
            return self._runs[root].token if root in self._runs else self.begin(root)

    def valid(self, root: int, token: str) -> bool:
        with self._lock:
            run = self._runs.get(root)
            return bool(run and run.token == token)

    def battle_started(self, root: int, battle: int, *, keep_formation=False,
                       another_battle_planned: bool = False) -> None:
        with self._lock:
            self.ensure(root)
            run = self._runs[root]
            if run.stopped:
                raise ValueError("bond-stop already latched")
            run.battle = battle
            run.another_battle_planned = another_battle_planned
            run.deferred = None
            run.checked_images = ()
            if not keep_formation:
                run.formation = ()

    def formation(self, root: int) -> tuple[dict, ...]:
        with self._lock:
            run = self._runs.get(root)
            return tuple(dict(slot) for slot in run.formation) if run else ()

    def battle_number(self, root: int) -> int:
        with self._lock:
            run = self._runs.get(root)
            return run.battle if run else 1

    def set_controller(self, root: int, controller) -> None:
        with self._lock:
            self.ensure(root)
            self._runs[root].controller = controller

    def controller(self, root: int):
        with self._lock:
            run = self._runs.get(root)
            return run.controller if run else None

    def set_formation(self, root: int, slots) -> None:
        """只保留槽位证据，不保留姓名、身份模板或动态前排位置。"""
        fields = ("slot", "status", "is_support", "reason")
        value = tuple({key: slot.get(key) if isinstance(slot, dict) else getattr(slot, key, None)
                       for key in fields} for slot in slots)
        with self._lock:
            self.ensure(root)
            self._runs[root].formation = value
            self._runs[root].checked_images = ()

    def clear_formation(self, root: int) -> None:
        with self._lock:
            run = self._runs.get(root)
            if run:
                run.formation = ()
                run.checked_images = ()

    def checked(self, root: int, digest: str) -> bool:
        with self._lock:
            run = self._runs.get(root)
            return bool(run and digest and digest in run.checked_images)

    def mark_checked(self, root: int, token: str, digest: str) -> None:
        with self._lock:
            if self.valid(root, token):
                run = self._runs[root]
                run.checked_images = tuple(dict.fromkeys((*run.checked_images, digest)))[-8:]

    def latch(self, root: int, token: str, *, reason: str, message: str,
              image=None, latest_image=None, formation_slots=(), result_columns=()) -> BondStop | None:
        with self._lock:
            if not self.valid(root, token):
                return None
            run = self._runs[root]
            if run.stopped is None:
                run.stopped = BondStop(reason, message, run.battle, tuple(formation_slots),
                                       tuple(result_columns), image.copy() if image is not None else None,
                                       latest_image.copy() if latest_image is not None else None)
            return run.stopped

    def get(self, root: int) -> BondStop | None:
        with self._lock:
            run = self._runs.get(root)
            return run.stopped if run else None

    def can_defer(self, root: int) -> bool:
        with self._lock:
            run = self._runs.get(root)
            return bool(run and run.another_battle_planned and run.stopped is None)

    def deferred(self, root: int) -> BondStop | None:
        with self._lock:
            run = self._runs.get(root)
            return run.deferred if run else None

    def defer(self, root: int, token: str, *, message: str,
              image=None, latest_image=None) -> BondStop | None:
        """只在已明确还有下一场时保存延后证据，不把延后当作真正停止。"""
        with self._lock:
            if not self.valid(root, token) or not self.can_defer(root):
                return None
            run = self._runs[root]
            if run.deferred is None:
                run.deferred = BondStop(
                    reason="bond_upgrade_deferred", message=message, battle=run.battle,
                    image=image.copy() if image is not None else None,
                    latest_image=latest_image.copy() if latest_image is not None else None,
                )
            return run.deferred

    def finish(self, root: int) -> None:
        with self._lock:
            self._runs.pop(root, None)

    def stop_owner(self, owner: str) -> None:
        with self._lock:
            for root, run in tuple(self._runs.items()):
                if not owner or run.owner in {"", owner}:
                    self._runs.pop(root, None)


bond_stops = BondStopStates()
