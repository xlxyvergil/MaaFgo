"""技能二次确认提醒按根任务去重，不随每场编队会话重建。"""
from threading import RLock


class SkillConfirmationReminders:
    def __init__(self):
        self._lock = RLock()
        self._tasks: dict[int, tuple[str, bool]] = {}

    def task_started(self, task_id: int, owner: str) -> None:
        with self._lock:
            self._tasks[task_id] = (owner, False)

    def claim(self, task_id: int) -> bool:
        with self._lock:
            owner, shown = self._tasks.get(task_id, ("", False))
            if shown:
                return False
            self._tasks[task_id] = (owner, True)
            return True

    def finish(self, task_id: int) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)

    def stop_owner(self, owner: str) -> None:
        with self._lock:
            for task_id, (task_owner, _) in list(self._tasks.items()):
                if not owner or task_owner in ("", owner):
                    self._tasks.pop(task_id)


skill_confirmation_reminders = SkillConfirmationReminders()
