from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, time
import calendar
from pathlib import Path
from typing import Any, Callable, Deque, List, Optional, TYPE_CHECKING

import jsonc
from PySide6.QtCore import QObject, Signal

from app.common.signal_bus import signalBus
from app.utils.logger import logger

if TYPE_CHECKING:
    from app.core.core import ServiceCoordinator


SCHEDULE_SINGLE = "single"
SCHEDULE_DAILY = "daily"
SCHEDULE_WEEKLY = "weekly"
SCHEDULE_MONTHLY = "monthly"
WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class ScheduleEntry:
    entry_id: str
    config_id: str
    name: str
    schedule_type: str
    params: dict[str, Any]
    force_start: bool
    enabled: bool
    created_at: datetime
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "config_id": self.config_id,
            "name": self.name,
            "schedule_type": self.schedule_type,
            "params": self.params,
            "force_start": self.force_start,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScheduleEntry":
        return cls(
            entry_id=payload["entry_id"],
            config_id=payload.get("config_id", ""),
            name=payload.get("name", ""),
            schedule_type=payload.get("schedule_type", SCHEDULE_SINGLE),
            params=payload.get("params", {}),
            force_start=bool(payload.get("force_start", False)),
            enabled=bool(payload.get("enabled", True)),
            created_at=_parse_iso(payload.get("created_at")) or datetime.now(),
            last_run=_parse_iso(payload.get("last_run")),
            next_run=_parse_iso(payload.get("next_run")),
        )

    def describe(self, tr_func: Optional[Callable[[str], str]] = None) -> str:
        tr = tr_func or (lambda text: text)
        if self.schedule_type == SCHEDULE_SINGLE:
            return tr("Single at {time}").format(
                time=self._format_datetime(self.params.get("run_at"))
            )
        if self.schedule_type == SCHEDULE_DAILY:
            interval = max(1, int(self.params.get("interval_days", 1)))
            return tr("Daily every {n} day(s) at {time}").format(
                n=interval,
                time=self._format_time(
                    (self.params.get("hour", 0), self.params.get("minute", 0))
                ),
            )
        if self.schedule_type == SCHEDULE_WEEKLY:
            interval = max(
                1, int(self.params.get("interval_weeks", 1) or 1)
            )
            return tr("Weekly every {n} week(s) at {time}").format(
                n=interval,
                time=self._format_time(
                    (self.params.get("hour", 0), self.params.get("minute", 0))
                ),
            )
        if self.schedule_type == SCHEDULE_MONTHLY:
            month_value = int(self.params.get("month", 0))
            month_label = (
                tr("Every month")
                if month_value == 0
                else tr(MONTH_NAMES[(month_value - 1) % 12])
            )
            hour_minute = self._format_time(
                (self.params.get("hour", 0), self.params.get("minute", 0))
            )
            ordinal = self.params.get("ordinal")
            weekday = self.params.get("weekday")
            if ordinal is not None and weekday is not None:
                ordinal_label = (
                    tr("Last")
                    if int(ordinal) >= 4
                    else tr(("First", "Second", "Third", "Fourth")[int(ordinal)])
                )
                weekday_label = tr(WEEKDAY_NAMES[int(weekday) % 7])
                return tr("Monthly ({month}) on the {ordinal} {weekday} at {time}").format(
                    month=month_label, ordinal=ordinal_label, weekday=weekday_label, time=hour_minute
                )
            day = int(self.params.get("month_day", 1))
            return tr("Monthly ({month}) on day {day} at {time}").format(
                month=month_label, day=day, time=hour_minute
            )
        return tr("Custom")

    def _format_datetime(self, value: Optional[str]) -> str:
        parsed = _parse_iso(value) if isinstance(value, str) else None
        if parsed:
            return parsed.strftime("%Y-%m-%d %H:%M")
        return self.created_at.strftime("%Y-%m-%d %H:%M")

    def _format_time(self, value: tuple[Any, Any]) -> str:
        hour = int(value[0]) if value[0] is not None else 0
        minute = int(value[1]) if value[1] is not None else 0
        return f"{hour:02d}:{minute:02d}"

    def compute_next_run(
        self, reference: Optional[datetime] = None
    ) -> Optional[datetime]:
        now = reference or datetime.now()
        now = now.replace(second=0, microsecond=0)

        if self.schedule_type == SCHEDULE_SINGLE:
            run_at = _parse_iso(self.params.get("run_at"))
            if run_at and run_at > now:
                return run_at
            return None

        if self.schedule_type == SCHEDULE_DAILY:
            start_at = _parse_iso(self.params.get("start_at")) or now
            interval = max(1, int(self.params.get("interval_days", 1)))
            hour, minute = self._time_from_params(start_at)
            candidate = datetime.combine(start_at.date(), time(hour, minute))
            while candidate <= now:
                candidate += timedelta(days=interval)
            return candidate

        if self.schedule_type == SCHEDULE_WEEKLY:
            start_at = _parse_iso(self.params.get("start_at")) or now
            interval = max(1, int(self.params.get("interval_weeks", 0) or 1))
            weekdays = sorted({int(w) % 7 for w in self.params.get("weekdays", [])})
            if not weekdays:
                weekdays = [start_at.weekday()]
            hour, minute = self._time_from_params(start_at)
            week_start = start_at.date() - timedelta(days=start_at.weekday())
            max_weeks = 520
            week_index = 0
            while week_index < max_weeks:
                if week_index % interval == 0:
                    for weekday in weekdays:
                        candidate_date = week_start + timedelta(
                            weeks=week_index, days=weekday
                        )
                        candidate = datetime.combine(candidate_date, time(hour, minute))
                        if candidate > now and candidate >= start_at:
                            return candidate
                week_index += 1
            return None

        if self.schedule_type == SCHEDULE_MONTHLY:
            start_at = _parse_iso(self.params.get("start_at")) or now
            hour, minute = self._time_from_params(start_at)
            month_value = int(self.params.get("month", 0))
            months = list(range(1, 13)) if month_value == 0 else [month_value]
            month_day = self.params.get("month_day")
            ordinal = self.params.get("ordinal")
            weekday = self.params.get("weekday")
            month_days = []
            if isinstance(month_day, int):
                month_days = [month_day]
            elif month_day is None:
                month_days = [start_at.day]
            return self._find_next_monthly_candidate(
                now,
                start_at,
                months,
                month_days,
                int(ordinal) if ordinal is not None else None,
                int(weekday) if weekday is not None else None,
                hour,
                minute,
            )

        return None

    def _monthly_candidate(
        self, year: int, month: int, day: int, hour: int, minute: int
    ) -> Optional[datetime]:
        try:
            return datetime(year=year, month=month, day=day, hour=hour, minute=minute)
        except ValueError:
            return None

    def _time_from_params(self, fallback: datetime) -> tuple[int, int]:
        hour = int(self.params.get("hour", fallback.hour))
        minute = int(self.params.get("minute", fallback.minute))
        return hour, minute

    def _find_next_monthly_candidate(
        self,
        now: datetime,
        start_at: datetime,
        months: list[int],
        month_days: list[int],
        ordinal: Optional[int],
        weekday: Optional[int],
        hour: int,
        minute: int,
    ) -> Optional[datetime]:
        base_date = max(now, start_at)
        month_index = base_date.month - 1
        year_base = base_date.year
        months_set = sorted(set(months))
        for offset in range(0, 36):
            current_month = ((month_index + offset) % 12) + 1
            current_year = year_base + (month_index + offset) // 12
            if current_month not in months_set:
                continue
            candidates: list[datetime] = []
            for day in month_days:
                candidate = self._monthly_candidate(
                    current_year, current_month, day, hour, minute
                )
                if candidate and candidate > now and candidate >= start_at:
                    candidates.append(candidate)
            if ordinal is not None and weekday is not None:
                candidate = self._nth_weekday(
                    current_year,
                    current_month,
                    ordinal,
                    weekday,
                    hour,
                    minute,
                )
                if candidate and candidate > now and candidate >= start_at:
                    candidates.append(candidate)
            if candidates:
                return min(candidates)
        return None

    def _nth_weekday(
        self,
        year: int,
        month: int,
        ordinal: int,
        weekday: int,
        hour: int,
        minute: int,
    ) -> Optional[datetime]:
        if ordinal < 0 or weekday < 0 or weekday > 6:
            return None
        if ordinal < 4:
            first_day = datetime(year, month, 1)
            first_weekday = first_day.weekday()
            day = 1 + ((weekday - first_weekday) % 7) + ordinal * 7
            if day > calendar.monthrange(year, month)[1]:
                return None
            return datetime(year, month, day, hour, minute)
        last_day = calendar.monthrange(year, month)[1]
        for delta in range(0, last_day):
            candidate_day = last_day - delta
            candidate = datetime(year, month, candidate_day, hour, minute)
            if candidate.weekday() == weekday:
                return candidate
        return None


class ScheduleService(QObject):
    schedules_changed = Signal(list)

    def __init__(self, service_coordinator: "ServiceCoordinator", storage_path: Path):
        super().__init__()
        self.service_coordinator = service_coordinator
        self.storage_path = storage_path
        self._schedules: List[ScheduleEntry] = []
        self._pending_queue: Deque[ScheduleEntry] = deque()
        self._current_task: Optional[asyncio.Task] = None
        self._scheduler_task: Optional[asyncio.Task] = None
        self._check_interval = 15
        self._ensure_storage()
        self._load_schedules()

    def _ensure_storage(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.write_text("[]", encoding="utf-8")

    def _load_schedules(self) -> None:
        try:
            with open(self.storage_path, "r", encoding="utf-8") as handle:
                payload = jsonc.load(handle)
        except Exception as exc:
            logger.warning("无法加载计划任务文件: %s", exc)
            return
        if not isinstance(payload, list):
            logger.warning(
                "计划任务文件结构异常: 期望列表, 得到 %s", type(payload).__name__
            )
            return
        entries: List[ScheduleEntry] = []
        for raw in payload:
            try:
                entry = ScheduleEntry.from_dict(raw)
                entry.next_run = entry.compute_next_run()
                if entry.schedule_type == SCHEDULE_SINGLE and entry.next_run is None:
                    entry.enabled = False
                entries.append(entry)
            except Exception as exc:
                logger.exception("反序列化计划任务失败: %s", exc)
        self._schedules = entries
        self._sort_schedules()
        self._persist()
        self._notify_schedules_changed()

    def _persist(self) -> None:
        try:
            with open(self.storage_path, "w", encoding="utf-8") as handle:
                jsonc.dump(
                    [entry.to_dict() for entry in self._schedules],
                    handle,
                    indent=4,
                    ensure_ascii=False,
                )
        except Exception as exc:
            logger.exception("计划任务保存失败: %s", exc)

    def _notify_schedules_changed(self) -> None:
        self.schedules_changed.emit(self.get_schedules())

    def get_schedules(self) -> List[ScheduleEntry]:
        return list(self._schedules)

    def find_schedule(self, entry_id: str) -> Optional[ScheduleEntry]:
        for entry in self._schedules:
            if entry.entry_id == entry_id:
                return entry
        return None

    def add_schedule(self, entry: ScheduleEntry) -> bool:
        entry.next_run = entry.compute_next_run()
        if entry.next_run is None and entry.schedule_type != SCHEDULE_SINGLE:
            logger.warning("无法为计划任务生成下一次执行时间: %s", entry.describe())
            return False
        self._schedules.append(entry)
        self._sort_schedules()
        self._persist()
        self._notify_schedules_changed()
        self._log_info(f"计划任务：{entry.name} ({entry.describe()}) 已添加")
        return True

    def remove_schedule(self, entry_id: str) -> bool:
        entry = self.find_schedule(entry_id)
        if not entry:
            return False
        self._schedules.remove(entry)
        self._sort_schedules()
        try:
            self._pending_queue.remove(entry)
        except ValueError:
            pass
        self._persist()
        self._notify_schedules_changed()
        self._log_info(f"计划任务：{entry.name} ({entry.describe()}) 已删除")
        return True

    def set_schedule_enabled(self, entry_id: str, enabled: bool) -> bool:
        entry = self.find_schedule(entry_id)
        if not entry:
            return False
        entry.enabled = enabled
        if enabled and not entry.next_run:
            entry.next_run = entry.compute_next_run()
        self._sort_schedules()
        self._persist()
        self._notify_schedules_changed()
        self._log_info(
            f"计划任务：{entry.name} ({entry.describe()}) {'已启用' if enabled else '已禁用'}"
        )
        return True

    def _sort_schedules(self) -> None:
        self._schedules.sort(key=lambda entry: (entry.next_run is None, entry.next_run))

    def start(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.info("事件循环尚未就绪，稍后再启动计划任务调度器")
            return
        self._scheduler_task = loop.create_task(self._scheduler_loop())

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self._check_due_entries()
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("计划任务调度异常: %s", exc)
                await asyncio.sleep(self._check_interval)

    async def _check_due_entries(self) -> None:
        now = datetime.now().replace(second=0, microsecond=0)
        due = [
            entry
            for entry in self._schedules
            if entry.enabled and entry.next_run and entry.next_run <= now
        ]
        for entry in due:
            await self._trigger_entry(entry, now)

    async def _trigger_entry(self, entry: ScheduleEntry, now: datetime) -> None:
        self._log_info(f"计划任务：{entry.name} ({entry.describe()}) 触发")
        if entry.schedule_type == SCHEDULE_SINGLE:
            entry.enabled = False
            entry.next_run = None
        else:
            entry.next_run = entry.compute_next_run(
                reference=now + timedelta(seconds=1)
            )
        entry.last_run = now
        self._persist()
        self._notify_schedules_changed()
        if entry.force_start:
            await self._force_start(entry)
        else:
            self._pending_queue.append(entry)
            self._log_info(
                f"计划任务：{entry.name} 已加入队列，剩余 {len(self._pending_queue)} 个任务"
            )
            await self._try_start_next()

    async def _force_start(self, entry: ScheduleEntry) -> None:
        if self.service_coordinator.run_manager.is_running or (
            self._current_task and not self._current_task.done()
        ):
            await self.service_coordinator.stop_task()
            try:
                if self._current_task:
                    await self._current_task
            except asyncio.CancelledError:
                pass
        self._pending_queue.appendleft(entry)
        await self._try_start_next()

    async def _try_start_next(self) -> None:
        if self._current_task and not self._current_task.done():
            return
        if not self._pending_queue:
            return
        while self._pending_queue:
            next_entry = self._pending_queue[0]
            if (
                self.service_coordinator.run_manager.is_running
                and not next_entry.force_start
            ):
                await asyncio.sleep(1)
                continue
            next_entry = self._pending_queue.popleft()
            self._current_task = asyncio.create_task(self._execute_entry(next_entry))
            return

    async def _execute_entry(self, entry: ScheduleEntry) -> None:
        signalBus.log_clear_requested.emit()
        self._log_info(f"计划任务：{entry.name} ({entry.describe()}) 开始执行")
        original_config = self.service_coordinator.config.current_config_id
        switched = False
        if entry.config_id and entry.config_id != original_config:
            switched = self.service_coordinator.select_config(entry.config_id)
            if not switched:
                self._log_info(f"计划任务：目标配置 {entry.config_id} 不存在，跳过")
                self._current_task = None
                await self._try_start_next()
                return
            signalBus.config_changed.emit(entry.config_id)
            logger.info(
                "计划任务：配置切换完成，当前配置 %s",
                self.service_coordinator.config.current_config_id,
            )
        try:
            await self.service_coordinator.run_tasks_flow()
        except Exception as exc:
            logger.exception("计划任务执行失败: %s", exc)
            self._log_info(f"计划任务：{entry.name} 执行失败 {exc}")
        finally:
            if switched and entry.config_id != original_config:
                self.service_coordinator.select_config(original_config)
                logger.info(
                    "计划任务：已经恢复原始配置 %s",
                    self.service_coordinator.config.current_config_id,
                )
            self._current_task = None
            await self._try_start_next()

    def _log_info(self, message: str) -> None:
        logger.info(message)
        signalBus.info_bar_requested.emit("INFO", message)
