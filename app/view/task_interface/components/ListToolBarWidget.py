from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
)


from qfluentwidgets import (
    SimpleCardWidget,
    ToolTipPosition,
    ToolTipFilter,
    BodyLabel,
    ListWidget,
    ToolButton,
    FluentIcon as FIF,
)


from app.view.task_interface.components.ListWidget import (
    TaskDragListWidget,
    ConfigListWidget,
)
from app.view.task_interface.components.AddTaskMessageBox import (
    AddConfigDialog,
    AddTaskDialog,
)
from app.core.core import ServiceCoordinator
from app.view.task_interface.components.ListItem import TaskListItem, ConfigListItem
from app.common.signal_bus import signalBus
from app.common.constants import _RESOURCE_, _CONTROLLER_, PRE_CONFIGURATION


class BaseListToolBarWidget(QWidget):

    def __init__(self, service_coordinator: ServiceCoordinator, parent=None):
        super().__init__(parent)
        self.service_coordinator = service_coordinator

        self._init_title()
        self._init_selection()

        self.title_layout.setContentsMargins(0, 0, 2, 0)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.addLayout(self.title_layout)
        self.main_layout.addWidget(self.selection_widget)

    def _init_title(self):
        """初始化标题栏"""
        # 标题
        self.selection_title = BodyLabel()
        self.selection_title.setStyleSheet("font-size: 20px;")
        self.selection_title.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # 选择全部按钮
        self.select_all_button = ToolButton(FIF.CHECKBOX)
        self.select_all_button.installEventFilter(
            ToolTipFilter(self.select_all_button, 0, ToolTipPosition.TOP)
        )
        self.select_all_button.setToolTip(self.tr("Select All"))

        # 取消选择全部
        self.deselect_all_button = ToolButton(FIF.CLEAR_SELECTION)
        self.deselect_all_button.installEventFilter(
            ToolTipFilter(self.deselect_all_button, 0, ToolTipPosition.TOP)
        )
        self.deselect_all_button.setToolTip(self.tr("Deselect All"))

        # 添加
        self.add_button = ToolButton(FIF.ADD)
        self.add_button.installEventFilter(
            ToolTipFilter(self.add_button, 0, ToolTipPosition.TOP)
        )
        self.add_button.setToolTip(self.tr("Add"))

        # 删除
        self.delete_button = ToolButton(FIF.DELETE)
        self.delete_button.installEventFilter(
            ToolTipFilter(self.delete_button, 0, ToolTipPosition.TOP)
        )
        self.delete_button.setToolTip(self.tr("Delete"))

        # 切换按钮（用于切换到特殊任务列表）
        self.switch_button = ToolButton(FIF.RIGHT_ARROW)
        self.switch_button.installEventFilter(
            ToolTipFilter(self.switch_button, 0, ToolTipPosition.TOP)
        )
        self.switch_button.setToolTip(self.tr("Switch to Special Tasks"))
        # 默认隐藏，只在普通任务模式下显示
        self.switch_button.hide()

        # 布局
        self.title_layout = QHBoxLayout()
        # 设置边距
        self.title_layout.addWidget(self.selection_title)
        self.title_layout.addWidget(self.select_all_button)
        self.title_layout.addWidget(self.deselect_all_button)
        self.title_layout.addWidget(self.delete_button)
        self.title_layout.addWidget(self.add_button)
        self.title_layout.addWidget(self.switch_button)

    def _init_task_list(self):
        """初始化任务列表"""
        self.task_list = ListWidget(parent=self)

    def _init_selection(self):
        """初始化配置选择"""
        self._init_task_list()

        # 配置选择列表布局
        self.selection_widget = SimpleCardWidget()
        self.selection_widget.setClickEnabled(False)
        self.selection_widget.setBorderRadius(8)
        self.selection_layout = QVBoxLayout(self.selection_widget)
        self.selection_layout.addWidget(self.task_list)

    def set_title(self, title: str):
        """设置标题"""
        self.selection_title.setText(title)


class ConfigListToolBarWidget(BaseListToolBarWidget):
    def __init__(self, service_coordinator: ServiceCoordinator, parent=None):
        super().__init__(service_coordinator=service_coordinator, parent=parent)

        self.service_coordinator = service_coordinator
        self._locked: bool = False

        self.select_all_button.hide()
        self.deselect_all_button.hide()

        self.add_button.clicked.connect(self.add_config)
        self.delete_button.clicked.connect(self.remove_config)

        # 设置配置列表标题
        self.set_title(self.tr("Configurations"))

        # 任务运行中锁定配置列表（禁止切换/增删）
        try:
            self.service_coordinator.fs_signal_bus.fs_start_button_status.connect(
                self._on_start_button_status_changed
            )
        except Exception:
            pass

    def _on_start_button_status_changed(self, status: dict):
        """根据任务流状态锁定/解锁配置列表。"""
        is_running = status.get("text") == "STOP"
        self.set_locked(is_running)

    def set_locked(self, locked: bool):
        """锁定后禁止新增/删除配置，并通知列表组件拦截点击切换。"""
        locked = bool(locked)
        if self._locked == locked:
            return
        self._locked = locked

        self.add_button.setEnabled(not locked)
        self.delete_button.setEnabled(not locked)

        if (
            hasattr(self, "task_list")
            and self.task_list
            and hasattr(self.task_list, "set_locked")
        ):
            try:
                self.task_list.set_locked(locked)
            except Exception:
                pass

    def _init_task_list(self):
        """初始化配置列表"""
        self.task_list = ConfigListWidget(
            service_coordinator=self.service_coordinator, parent=self
        )

    def add_config(self):
        """添加配置项。"""
        if self._locked:
            signalBus.info_bar_requested.emit(
                "warning", self.tr("Task is running, configurations are locked.")
            )
            return
        # 通过对话框创建新配置
        bundles = []
        config_service = self.service_coordinator.config
        try:
            # 优先通过 ConfigService 的接口获取 bundle 列表，避免直接依赖内部结构
            bundle_names = config_service.list_bundles()
            for name in bundle_names:
                try:
                    info = config_service.get_bundle(name)
                except FileNotFoundError:
                    continue
                bundle_info = {
                    "name": str(info.get("name", name)),
                    "path": str(info.get("path", "")),
                }
                bundles.append(bundle_info)
        except Exception:
            # 回退：尝试从 _main_config 读取（兼容旧数据/异常场景）
            try:
                main_cfg = config_service._main_config  # type: ignore[attr-defined]
            except Exception:
                main_cfg = None
            if main_cfg:
                bundle_source = main_cfg.get("bundle", [])
                if isinstance(bundle_source, dict):
                    for name, value in bundle_source.items():
                        bundle_info = {"name": name}
                        if isinstance(value, dict):
                            bundle_info["path"] = value.get("path", "")
                        else:
                            bundle_info["path"] = str(value)
                        bundles.append(bundle_info)
                elif isinstance(bundle_source, list):
                    bundles = bundle_source

        dlg = AddConfigDialog(
            resource_bundles=bundles,
            parent=self.window(),
            interface=self.service_coordinator.interface,
            service_coordinator=self.service_coordinator,
        )
        if dlg.exec():
            cfg = dlg.get_config_item()
            if cfg:
                preset_name = dlg.get_selected_preset_name()
                self.service_coordinator.add_config(cfg, preset_name=preset_name)

    def remove_config(self):
        """移除配置项"""
        if self._locked:
            signalBus.info_bar_requested.emit(
                "warning", self.tr("Task is running, configurations are locked.")
            )
            return
        config_list = self.service_coordinator.config.list_configs()
        if len(config_list) <= 1:
            signalBus.info_bar_requested.emit(
                "warning", self.tr("Cannot delete the last configuration!")
            )
            signalBus.info_bar_requested.emit(
                "warning", self.tr("Cannot delete the last configuration!")
            )
            return False
        cur = self.task_list.currentItem()
        if not cur:
            return
        widget = self.task_list.itemWidget(cur)
        if not widget:
            return
        if isinstance(widget, ConfigListItem):
            cfg_id = widget.item.item_id
        else:
            cfg_id = None
        if not cfg_id:
            return
        # 调用服务删除即可,视图通过信号刷新
        self.service_coordinator.delete_config(cfg_id)


class TaskListToolBarWidget(BaseListToolBarWidget):

    def __init__(
        self,
        service_coordinator: ServiceCoordinator,
        parent=None,
        task_filter_mode: str = "all",
    ):
        self._task_filter_mode = (
            task_filter_mode
            if task_filter_mode in ("all", "normal", "special")
            else "all"
        )
        super().__init__(service_coordinator=service_coordinator, parent=parent)
        self.core_signalBus = self.service_coordinator.signal_bus
        if self._task_filter_mode == "special":
            self._apply_special_mode_ui()
        elif self._task_filter_mode == "normal":
            # 普通任务模式下，检查是否有特殊任务，有则显示切换按钮
            if self._has_special_tasks():
                self.switch_button.show()
            else:
                self.switch_button.hide()
        # 选择全部按钮
        self.select_all_button.clicked.connect(self.select_all)
        # 取消选择全部按钮
        self.deselect_all_button.clicked.connect(self.deselect_all)
        # 添加按钮（使用 lambda 确保 idx 参数使用默认值 -2）
        self.add_button.clicked.connect(lambda: self.add_task(-2))
        # 删除按钮
        self.delete_button.clicked.connect(self.remove_selected_task)

        # 设置任务列表标题
        self.set_title(self.tr("Tasks"))

        # 监听配置切换信号，当配置切换时重新检查是否有特殊任务
        self.core_signalBus.config_changed.connect(self._on_config_changed)

        # 初始填充任务列表
        # 不在工具栏直接刷新列表：视图会订阅 ServiceCoordinator 的信号自行更新

    def _init_task_list(self):
        """初始化任务列表"""
        self.task_list = TaskDragListWidget(
            service_coordinator=self.service_coordinator,
            parent=self,
            filter_mode=self._task_filter_mode,
        )

    def _has_special_tasks(self) -> bool:
        """检查当前 interface 中是否有带特殊任务标志的任务

        Returns:
            bool: 如果有特殊任务返回 True，否则返回 False
        """
        try:
            interface = self.service_coordinator.task.interface
            if not interface:
                return False

            task_defs = interface.get("task", [])
            if not isinstance(task_defs, list):
                return False

            # 检查是否有任务的 spt 字段为 True
            for task_def in task_defs:
                if task_def.get("spt", False):
                    return True

            return False
        except Exception:
            # 如果检查过程中出现异常，默认返回 False（隐藏按钮）
            return False

    def _on_config_changed(self, config_id: str):
        """配置切换时的回调，重新检查是否有特殊任务并更新按钮状态"""
        if self._task_filter_mode == "normal":
            # 只在普通任务模式下更新按钮状态
            if self._has_special_tasks():
                self.switch_button.show()
            else:
                self.switch_button.hide()

    def select_all(self):
        """选择全部"""
        self.task_list.select_all()

    def deselect_all(self):
        """取消选择全部"""
        self.task_list.deselect_all()

    def add_task(self, idx: int = -2):
        """添加任务

        Args:
            idx: 插入位置索引，默认为-2（倒数第二个位置）
        """
        # 打开添加任务对话框
        task_map = self.service_coordinator.task.default_option
        interface = self.service_coordinator.task.interface
        filtered_task_map = self._filter_task_map_by_mode(task_map, interface)
        if not filtered_task_map:
            signalBus.info_bar_requested.emit(
                "warning", self.tr("No available tasks to add.")
            )
            return
        dlg = AddTaskDialog(
            task_map=filtered_task_map, interface=interface, parent=self.window()
        )
        if dlg.exec():
            new_task = dlg.get_task_item()
            if new_task:
                # 持久化到服务层
                self.service_coordinator.modify_task(new_task, idx)

    def _filter_task_map_by_mode(
        self, task_map: dict[str, dict], interface: dict | None
    ) -> dict[str, dict]:
        """根据当前任务过滤模式筛选可添加的任务."""
        if not isinstance(task_map, dict):
            return {}

        interface = interface or {}
        task_special_map: dict[str, bool] = {}
        for task_def in interface.get("task", []):
            name = task_def.get("name")
            if name:
                task_special_map[name] = task_def.get("spt", False)

        # 获取当前配置中的资源/控制器类型
        current_resource_name = ""
        current_controller_type = ""
        try:
            # 新版本：资源选项位于固定基础任务 `Resource`
            resource_task = self.service_coordinator.task.get_task(_RESOURCE_)
            if resource_task and isinstance(resource_task.task_option, dict):
                current_resource_name = resource_task.task_option.get("resource", "")
            # 向后兼容：旧版本可能把 resource 放在 `Pre-Configuration`
            if not current_resource_name:
                pre_config_task = self.service_coordinator.task.get_task(
                    PRE_CONFIGURATION
                )
                if pre_config_task and isinstance(pre_config_task.task_option, dict):
                    current_resource_name = pre_config_task.task_option.get(
                        "resource", ""
                    )

            # 控制器类型优先从固定基础任务 `Controller` 读取
            controller_task = self.service_coordinator.task.get_task(_CONTROLLER_)
            if controller_task and isinstance(controller_task.task_option, dict):
                current_controller_type = controller_task.task_option.get(
                    "controller_type", ""
                )
            # 向后兼容：旧版本可能把 controller_type 放在 `Pre-Configuration`
            if not current_controller_type:
                pre_config_task = self.service_coordinator.task.get_task(
                    PRE_CONFIGURATION
                )
                if pre_config_task and isinstance(pre_config_task.task_option, dict):
                    current_controller_type = pre_config_task.task_option.get(
                        "controller_type", ""
                    )
        except Exception:
            pass

        def _include(task_name: str) -> bool:
            is_special = task_special_map.get(task_name, False)
            if self._task_filter_mode == "special":
                if not is_special:
                    return False
            elif self._task_filter_mode == "normal":
                if is_special:
                    return False

            # 根据资源过滤任务
            if current_resource_name or current_controller_type:
                for task_def in interface.get("task", []):
                    if task_def.get("name") != task_name:
                        continue

                    # --- controller 过滤（空=全显示） ---
                    if current_controller_type:
                        controllers = task_def.get("controller", None)
                        if controllers not in (None, "", [], {}):
                            allowed_ctrl: list[str] = []
                            if isinstance(controllers, str):
                                if controllers.strip():
                                    allowed_ctrl = [controllers.strip()]
                            elif isinstance(controllers, list):
                                allowed_ctrl = [
                                    str(x).strip()
                                    for x in controllers
                                    if x is not None and str(x).strip()
                                ]
                            # 非支持格式：默认全显示
                            if allowed_ctrl:
                                if (
                                    str(current_controller_type).strip().lower()
                                    not in {s.lower() for s in allowed_ctrl}
                                ):
                                    return False

                    # --- resource 过滤（空=全显示） ---
                    if current_resource_name:
                        task_resources = task_def.get("resource", [])
                        # 如果任务没有 resource 字段，或者 resource 为空列表，表示所有资源都可用
                        if not task_resources:
                            return True
                        # 如果任务的 resource 列表包含当前资源，则显示
                        if current_resource_name in task_resources:
                            return True
                        return False

            return True

        return {name: opts for name, opts in task_map.items() if _include(name)}

    def _apply_special_mode_ui(self):
        """特殊任务界面隐藏批量与增删按钮，但显示切换按钮"""
        for btn in (
            self.select_all_button,
            self.deselect_all_button,
            self.add_button,
            self.delete_button,
        ):
            btn.hide()
        # 特殊任务模式下显示切换按钮（用于切换回普通任务）
        self.switch_button.show()
        # 更新切换按钮的图标和提示文本
        from qfluentwidgets import FluentIcon as FIF

        self.switch_button.setIcon(FIF.LEFT_ARROW)
        self.switch_button.setToolTip(self.tr("Switch to Normal Tasks"))

    def switch_filter_mode(self):
        """切换任务列表的过滤模式（normal <-> special）"""
        if self._task_filter_mode == "normal":
            # 切换到特殊任务模式
            self._task_filter_mode = "special"
            self._apply_special_mode_ui()
            self.set_title(self.tr("Special Tasks"))
        elif self._task_filter_mode == "special":
            # 切换到普通任务模式
            self._task_filter_mode = "normal"
            # 显示所有按钮
            self.select_all_button.show()
            self.deselect_all_button.show()
            self.add_button.show()
            self.delete_button.show()
            # 检查是否有特殊任务，有则显示切换按钮，否则隐藏
            if self._has_special_tasks():
                self.switch_button.show()
            else:
                self.switch_button.hide()
            # 更新切换按钮的图标和提示文本
            from qfluentwidgets import FluentIcon as FIF

            self.switch_button.setIcon(FIF.RIGHT_ARROW)
            self.switch_button.setToolTip(self.tr("Switch to Special Tasks"))
            self.set_title(self.tr("Tasks"))
        else:
            # all 模式不支持切换
            return

        # 更新任务列表的过滤模式并刷新
        if hasattr(self, "task_list") and self.task_list:
            self.task_list._filter_mode = self._task_filter_mode
            self.task_list.update_list()

    def remove_selected_task(self):
        cur = self.task_list.currentItem()
        if not cur:
            return
        widget = self.task_list.itemWidget(cur)
        if not widget or not isinstance(widget, TaskListItem):
            return
        task_id = widget.task.item_id if widget.task else None
        if not task_id:
            return
        elif widget.task.is_base_task():
            from app.common.signal_bus import signalBus

            signalBus.info_bar_requested.emit(
                "warning",
                self.tr(
                    "Base tasks (Resource, Post-Task) cannot be deleted (ID: {id})"
                ).format(id=task_id),
            )
            return False
        # 删除通过服务层执行，视图会通过fs系列信号刷新
        self.service_coordinator.delete_task(task_id)
