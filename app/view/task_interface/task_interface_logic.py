import asyncio

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QWidget,
    QApplication,
)
from PySide6.QtGui import QShowEvent
from qfluentwidgets import (
    FluentIcon as FIF,
)

from app.view.task_interface.task_interface_ui import UI_TaskInterface
from app.utils.logger import logger
from app.common.config import cfg, Config


class TaskInterface(UI_TaskInterface, QWidget):

    def __init__(self, service_coordinator=None, parent=None):
        QWidget.__init__(self, parent=parent)
        UI_TaskInterface.__init__(
            self, service_coordinator=service_coordinator, parent=parent
        )
        self.setupUi(self)
        self.service_coordinator = service_coordinator

        self.task_info.set_title(self.tr("Task Information"))
        self.config_selection.set_title(self.tr("Configuration Selection"))

        # 连接启动/停止按钮事件
        self.start_bar.run_button.clicked.connect(self._on_run_button_clicked)
        
        # 连接切换按钮事件（如果存在）
        if hasattr(self.task_info, 'switch_button'):
            self.task_info.switch_button.clicked.connect(self._on_switch_button_clicked)

        # 连接服务协调器的信号，用于更新按钮状态
        self.service_coordinator.fs_signals.fs_start_button_status.connect(
            self._on_button_status_changed
        )

    def _on_start_button_clicked(self):
        """处理开始按钮点击事件"""
        # 启动任务流
        asyncio.create_task(self.service_coordinator.run_tasks_flow())

    def _on_stop_button_clicked(self):
        """处理停止按钮点击事件"""
        # 停止任务流
        asyncio.create_task(self.service_coordinator.stop_task_flow())

    def _on_run_button_clicked(self):
        """处理启动/停止按钮点击事件"""
        # 检查按钮当前状态并执行相应操作
        if self.start_bar.run_button.text() == self.tr("Start"):
            # 立即禁用按钮
            self.start_bar.run_button.setDisabled(True)
            # 强制处理UI事件，确保按钮状态立即更新
            QApplication.processEvents()

            # 检查当前是否为特殊任务模式
            is_special_mode = (
                hasattr(self.task_info, '_task_filter_mode') 
                and self.task_info._task_filter_mode == "special"
            )
            
            if is_special_mode:
                # 特殊任务模式：需要先检查是否有选中的特殊任务
                self.log_output_widget.clear_log()
                target_task = self._get_selected_special_task()
                if not target_task:
                    from app.common.signal_bus import signalBus
                    signalBus.info_bar_requested.emit(
                        "warning", self.tr("Please select a special task to run.")
                    )
                    self.start_bar.run_button.setEnabled(True)
                    return

                # 同步内存态：确保服务层当前任务列表中只有该特殊任务被视为选中，但不落盘
                try:
                    for task in self.service_coordinator.task.get_tasks():
                        if task.is_special:
                            task.is_checked = task.item_id == target_task.item_id
                except Exception:
                    pass

                def _start_special_task():
                    asyncio.create_task(
                        self.service_coordinator.run_tasks_flow(task_id=target_task.item_id)
                    )
                QTimer.singleShot(0, _start_special_task)
            else:
                # 普通任务模式：使用原有逻辑
                def _start_task():
                    self.log_output_widget.clear_log()
                    asyncio.create_task(self.service_coordinator.run_tasks_flow())

                # 使用 QTimer 延迟执行，避免阻塞UI更新
                QTimer.singleShot(0, _start_task)
        else:
            # 立即禁用按钮
            self.start_bar.run_button.setDisabled(True)
            # 强制处理UI事件，确保按钮状态立即更新
            QApplication.processEvents()

            def _stop_task():
                asyncio.create_task(self.service_coordinator.stop_task_flow())

            # 使用 QTimer 延迟执行
            QTimer.singleShot(0, _stop_task)

    def _on_button_status_changed(self, status):
        """处理按钮状态变化信号"""
        """状态格式: {"text": "STOP", "status": "disabled"}"""
        # 更新启动/停止按钮状态
        is_running = status.get("text") == "STOP"
        if is_running:
            self.start_bar.run_button.setText(self.tr("Stop"))
            self.start_bar.run_button.setIcon(FIF.CLOSE)
            # 任务流运行时，禁用任务列表的编辑功能
            self._set_task_list_editable(False)
        else:
            self.start_bar.run_button.setText(self.tr("Start"))
            self.start_bar.run_button.setIcon(FIF.PLAY)
            # 任务流停止时，启用任务列表的编辑功能
            self._set_task_list_editable(True)

        # 设置按钮是否可用
        self.start_bar.run_button.setEnabled(status.get("status") != "disabled")
    
    def _set_task_list_editable(self, enabled: bool):
        """设置任务列表的编辑功能是否可用
        
        Args:
            enabled: True 表示启用编辑功能，False 表示禁用
        """
        if not hasattr(self, 'task_info') or not self.task_info:
            return
        
        task_list = getattr(self.task_info, 'task_list', None)
        if not task_list:
            return
        
        # 禁用/启用拖动功能
        task_list.setDragEnabled(enabled)
        task_list.setAcceptDrops(enabled)
        
        # 禁用/启用工具栏按钮
        if hasattr(self.task_info, 'add_button'):
            self.task_info.add_button.setEnabled(enabled)
        if hasattr(self.task_info, 'delete_button'):
            self.task_info.delete_button.setEnabled(enabled)
        if hasattr(self.task_info, 'select_all_button'):
            self.task_info.select_all_button.setEnabled(enabled)
        if hasattr(self.task_info, 'deselect_all_button'):
            self.task_info.deselect_all_button.setEnabled(enabled)
        
        # 禁用/启用所有任务项的 checkbox 和删除按钮
        for i in range(task_list.count()):
            item = task_list.item(i)
            if not item:
                continue
            widget = task_list.itemWidget(item)
            if not widget:
                continue
            # 禁用/启用 checkbox（基础任务始终保持禁用）
            if hasattr(widget, 'checkbox') and hasattr(widget, 'task'):
                # 基础任务的 checkbox 始终保持禁用状态
                if not widget.task.is_base_task():
                    widget.checkbox.setEnabled(enabled)
            # 禁用/启用删除按钮（基础任务的删除按钮始终保持禁用）
            if hasattr(widget, 'setting_button') and hasattr(widget, 'task'):
                # 基础任务的删除按钮始终保持禁用状态
                if not widget.task.is_base_task():
                    widget.setting_button.setEnabled(enabled)
    
    def showEvent(self, event: QShowEvent):
        """界面显示时自动选中第0个任务"""
        super().showEvent(event)
        # 使用定时器延迟执行，确保任务列表已经加载完成
        def _reset_ui():
            # 清除选项面板
            self.option_panel.reset()
            # 清除普通任务列表的选中状态（特殊任务列表保持原样）
            if hasattr(self, 'task_info') and hasattr(self.task_info, 'task_list'):
                task_list = self.task_info.task_list
                # 只对普通任务列表清除选中状态
                if hasattr(task_list, '_filter_mode') and task_list._filter_mode != "special":
                    # 先清除选项服务的状态，避免状态不一致
                    if self.service_coordinator and hasattr(self.service_coordinator, 'option'):
                        option_service = self.service_coordinator.option
                        if hasattr(option_service, 'clear_selection'):
                            option_service.clear_selection()
                    # 使用 setCurrentRow(-1) 完全清除选中状态
                    # 这会触发 currentItemChanged 信号（current 为 None），确保状态完全清除
                    task_list.setCurrentRow(-1)
                    # 强制更新UI，确保选中状态完全清除
                    task_list.update()
        QTimer.singleShot(50, _reset_ui)
    
    def _on_switch_button_clicked(self):
        """处理切换按钮点击事件，切换任务列表的过滤模式"""
        try:
            if hasattr(self.task_info, 'switch_filter_mode'):
                self.task_info.switch_filter_mode()
                logger.info(f"已切换任务列表过滤模式为: {self.task_info._task_filter_mode}")
                # 清除选项面板的选中状态
                if hasattr(self, 'option_panel'):
                    self.option_panel.reset()
        except Exception as exc:
            logger.error(f"切换任务列表过滤模式失败: {exc}", exc_info=True)
    
    def _get_selected_special_task(self):
        """从特殊任务列表中获取当前选中的任务（仅内存态）。"""
        task_list_widget = getattr(self.task_info, "task_list", None)
        if not task_list_widget:
            return None
        try:
            for row in range(task_list_widget.count()):
                item = task_list_widget.item(row)
                widget = task_list_widget.itemWidget(item)
                if isinstance(widget, type(None)):  # 防御性，避免 None
                    continue
                task = getattr(widget, "task", None)
                # 直接检查task.is_checked，不依赖checkbox的可见性
                if task and task.is_special and task.is_checked:
                    return task
        except Exception:
            pass
        return None
