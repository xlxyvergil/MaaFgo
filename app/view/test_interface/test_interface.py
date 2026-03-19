from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, PrimaryPushButton

from app.common.signal_bus import signalBus
from app.core.core import ServiceCoordinator
from app.core.service.Schedule_Service import (
    ScheduleEntry,
    SCHEDULE_SINGLE,
)
from app.utils.logger import logger


class TestInterface(QWidget):
    """通用测试界面（目前用于计划任务调试）。"""

    def __init__(self, service_coordinator: ServiceCoordinator, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("TestInterface")
        self.service_coordinator = service_coordinator
        self._log_buffer: deque[str] = deque(maxlen=500)
        self._init_ui()
        signalBus.log_output.connect(self._on_log_output)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = BodyLabel("测试界面")
        header.setStyleSheet("font-weight: 600; font-size: 18px;")
        layout.addWidget(header)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        switch_btn = PrimaryPushButton("切换配置测试")
        switch_btn.clicked.connect(self._test_switch_config)
        button_layout.addWidget(switch_btn)

        run_btn = PrimaryPushButton("运行任务测试")
        run_btn.clicked.connect(self._test_run_tasks)
        button_layout.addWidget(run_btn)

        force_btn = PrimaryPushButton("强制运行测试")
        force_btn.clicked.connect(self._test_force_start)
        button_layout.addWidget(force_btn)

        layout.addLayout(button_layout)

        self._log_view = QPlainTextEdit(self)
        self._log_view.setReadOnly(True)
        self._log_view.setPlaceholderText("日志输出会在此处展示")
        self._log_view.setMinimumHeight(240)
        layout.addWidget(self._log_view)

    def _test_switch_config(self) -> None:
        configs = self.service_coordinator.config.list_configs()
        if not configs:
            signalBus.info_bar_requested.emit(
                "warning", "未找到任何配置，无法切换"
            )
            return

        current = self.service_coordinator.config.current_config_id
        target_info = None
        for config in configs:
            if config.get("item_id") != current:
                target_info = config
                break
        if not target_info:
            target_info = configs[0]
        target_id = target_info.get("item_id", "")
        if not target_id:
            signalBus.info_bar_requested.emit(
                "warning", "目标配置 ID 无效"
            )
            return

        success = self.service_coordinator.select_config(target_id)
        msg = (
            "切换配置成功: {id}".format(id=target_id)
            if success
            else "切换配置失败: {id}".format(id=target_id)
        )
        signalBus.info_bar_requested.emit("info" if success else "error", msg)
        logger.info("测试页面：切换配置 %s -> %s", current, target_id)

    def _test_run_tasks(self) -> None:
        if self.service_coordinator.run_manager.is_running:
            signalBus.info_bar_requested.emit(
                "warning", "任务流正在运行，无法重复启动"
            )
            return
        signalBus.info_bar_requested.emit("info", "已触发任务流测试")
        asyncio.create_task(self.service_coordinator.run_tasks_flow())

    def _test_force_start(self) -> None:
        configs = self.service_coordinator.config.list_configs()
        if not configs:
            signalBus.info_bar_requested.emit(
                "warning", "未找到任何配置，无法强制运行"
            )
            return
        current_id = self.service_coordinator.config.current_config_id
        if not current_id:
            current_id = configs[0].get("item_id", "")
        if not current_id:
            signalBus.info_bar_requested.emit(
                "warning", "当前配置 ID 不可用"
            )
            return

        entry = ScheduleEntry(
            entry_id=f"debug_{uuid4().hex}",
            config_id=current_id,
            name="调试条目",
            schedule_type=SCHEDULE_SINGLE,
            params={
                "run_at": (datetime.now() + timedelta(seconds=1)).isoformat(),
            },
            force_start=True,
            enabled=True,
            created_at=datetime.now(),
        )
        signalBus.info_bar_requested.emit("info", "已发起强制运行")
        asyncio.create_task(self.service_coordinator.schedule_service._force_start(entry))

    def _on_log_output(self, level: str, message: str) -> None:
        text = f"[{level}] {message}"
        self._log_buffer.append(text)
        self._log_view.setPlainText("\n".join(self._log_buffer))
        scrollbar = self._log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

