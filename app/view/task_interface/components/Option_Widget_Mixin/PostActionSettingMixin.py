from typing import Any, Dict, List, Tuple

from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    LineEdit,
    ToolTipFilter,
    ToolTipPosition,
)
from PySide6.QtWidgets import QVBoxLayout


from app.utils.logger import logger
from app.widget.PathLineEdit import PathLineEdit

from app.core.core import ServiceCoordinator


class PostActionSettingMixin:
    """
    完成后操作设置 Mixin - 提供完成后操作设置功能
    使用方法：在 OptionWidget 中使用多重继承添加此 mixin
    """

    option_page_layout: QVBoxLayout
    service_coordinator: ServiceCoordinator

    _CONFIG_KEY = "post_action"
    _ALWAYS_RUN_KEY = "always_run"
    _ACTION_ORDER: List[str] = [
        "none",
        "close_controller",
        "run_program",
        # 一组互斥：切换其他配置 / 退出软件 / 关机
        "run_other",
        "close_software",
        "shutdown",
    ]
    _PRIMARY_ACTIONS = {"none", "shutdown", "run_other"}
    _SECONDARY_ACTIONS = {"close_controller", "close_software"}
    _OPTIONAL_ACTIONS = {"run_program"}
    _DEFAULT_STATE: Dict[str, Any] = {
        "none": True,
        "shutdown": False,
        "close_controller": False,
        "close_software": False,
        "run_other": False,
        "run_program": False,
        "always_run": False,
        "target_config": "",
        "program_path": "",
        "program_args": "",
    }

    _EXCLUSIVE_EXIT_GROUP = {"run_other", "close_software", "shutdown"}

    def tr(
        self, sourceText: str, /, disambiguation: str | None = None, n: int = -1
    ) -> str: ...

    def _init_post_action_settings(self):
        """初始化完成后操作设置相关属性"""
        if not hasattr(self, "post_action_widgets"):
            self.post_action_widgets: Dict[str, Any] = {}
        self._post_action_syncing = False
        if not hasattr(self, "_post_action_state"):
            self._post_action_state: Dict[str, Any] = {}

    def _get_action_label(self, action_key: str) -> str:
        """返回动作对应的可翻译文案"""
        mapping = {
            "none": self.tr("Do nothing"),
            "shutdown": self.tr("Shutdown"),
            "close_controller": self.tr("Close controller"),
            "close_software": self.tr("Close software"),
            "run_other": self.tr("Run other configuration"),
            "run_program": self.tr("Run other program"),
        }
        return mapping.get(action_key, action_key)

    # region UI 构建
    def create_post_action_settings(self) -> None:
        """创建完成后操作设置界面"""
        if not hasattr(self, "option_page_layout"):
            raise ValueError(
                self.tr(
                    "option_page_layout is not set, cannot render post action options"
                )
            )

        # 注意：_clear_options() 和 _toggle_description(False) 已经在 _apply_post_action_settings_with_animation 中调用
        # 这里不再重复调用，避免重复清理导致问题
        self._ensure_post_action_state()

        self.post_action_widgets.clear()

        title = BodyLabel(self.tr("Finish"))
        self.option_page_layout.addWidget(title)
        self.option_page_layout.addSpacing(8)

        # 独立开关：不参与任何互斥逻辑
        always_run = CheckBox(self.tr("always run"))
        always_run.toggled.connect(self._on_post_action_always_run_changed)
        always_run.installEventFilter(
            ToolTipFilter(always_run, 0, ToolTipPosition.TOP)
        )
        always_run.setToolTip(self.tr("Whether to run the post-action regardless of success or failure"))
        self.option_page_layout.addWidget(always_run)
        self.post_action_widgets[self._ALWAYS_RUN_KEY] = always_run
        self.option_page_layout.addSpacing(8)

        for action_key in self._ACTION_ORDER:
            checkbox = CheckBox(self._get_action_label(action_key))
            checkbox.toggled.connect(
                lambda checked, key=action_key: self._on_post_action_checkbox_changed(
                    key, checked
                )
            )
            self.option_page_layout.addWidget(checkbox)
            self.post_action_widgets[action_key] = checkbox

        self.option_page_layout.addSpacing(12)
        combo_label = BodyLabel(self.tr("Select the configuration to run"))
        self.option_page_layout.addWidget(combo_label)

        combo = ComboBox()
        for config_id, display_name in self._load_available_configs():
            combo.addItem(display_name, userData=config_id)
        combo.currentIndexChanged.connect(
            lambda index: self._on_other_config_changed(combo, index)
        )
        self.option_page_layout.addWidget(combo)
        self.post_action_widgets["target_config"] = combo

        self._create_program_input_fields()
        self._apply_post_action_state_to_widgets()

    # endregion

    # region 状态 & 互斥逻辑
    def _ensure_post_action_state(self) -> None:
        """确保配置中存在完成后操作状态"""
        if not isinstance(self.current_config, dict):
            self.current_config = {}

        raw_state = self.current_config.get(self._CONFIG_KEY)
        if not isinstance(raw_state, dict):
            raw_state = {}

        merged = dict(self._DEFAULT_STATE)
        merged.update(raw_state)

        # 一组互斥：run_other / close_software / shutdown
        # 兼容旧配置：若多选，按优先级保留一个（shutdown > close_software > run_other）
        if merged.get("shutdown"):
            merged["close_software"] = False
            merged["run_other"] = False
        elif merged.get("close_software"):
            merged["run_other"] = False
        elif merged.get("run_other"):
            merged["close_software"] = False
            merged["shutdown"] = False

        # 新的互斥逻辑：只有"无动作"与其他选项互斥
        if merged.get("none"):
            # 如果"无动作"被选中，其他选项都设为False
            for action_key in self._PRIMARY_ACTIONS.union(
                self._SECONDARY_ACTIONS
            ).union(self._OPTIONAL_ACTIONS):
                if action_key != "none":
                    merged[action_key] = False
        else:
            # 如果有其他选项被选中，确保"无动作"为False
            has_other_selected = any(
                merged.get(action_key, False)
                for action_key in self._PRIMARY_ACTIONS.union(
                    self._SECONDARY_ACTIONS
                ).union(self._OPTIONAL_ACTIONS)
                if action_key != "none"
            )
            if has_other_selected:
                merged["none"] = False

        self.current_config[self._CONFIG_KEY] = merged
        self._post_action_state = merged

    def _apply_post_action_state_to_widgets(self) -> None:
        """同步状态到控件"""
        self._post_action_syncing = True
        always_run = self.post_action_widgets.get(self._ALWAYS_RUN_KEY)
        if isinstance(always_run, CheckBox):
            always_run.setChecked(
                bool(self._post_action_state.get(self._ALWAYS_RUN_KEY))
            )

        for action_key in self._PRIMARY_ACTIONS.union(self._SECONDARY_ACTIONS).union(
            self._OPTIONAL_ACTIONS
        ):
            widget = self.post_action_widgets.get(action_key)
            if isinstance(widget, CheckBox):
                widget.setChecked(bool(self._post_action_state.get(action_key)))

        combo = self.post_action_widgets.get("target_config")
        if isinstance(combo, ComboBox):
            target = self._post_action_state.get("target_config", "")
            target_index = combo.findData(target)
            if target_index < 0 and target:
                combo.addItem(self.tr("Unknown config"), userData=target)
                target_index = combo.findData(target)

            combo.blockSignals(True)
            combo.setCurrentIndex(target_index if target_index >= 0 else 0)
            combo.blockSignals(False)
            combo.setEnabled(bool(self._post_action_state.get("run_other")))

        self._apply_program_inputs_state()
        self._update_program_inputs_enabled()
        self._post_action_syncing = False

    def _on_post_action_always_run_changed(self, checked: bool) -> None:
        """独立开关：始终运行完成后动作（不参与互斥）"""
        if self._post_action_syncing:
            return
        self._post_action_state[self._ALWAYS_RUN_KEY] = checked
        self._save_post_action_state()

    def _on_post_action_checkbox_changed(self, key: str, checked: bool) -> None:
        if self._post_action_syncing:
            return

        self._post_action_syncing = True
        self._post_action_state[key] = checked

        if checked:
            if key == "none":
                # 选中"无动作"时，取消所有其他选项
                self._deactivate_all_post_actions_except_none()
            else:
                # 选中其他任何选项时，取消"无动作"
                self._deactivate_none_action()
                # 一组互斥：切换其他配置 / 退出软件 / 关机
                if key in self._EXCLUSIVE_EXIT_GROUP:
                    if key != "shutdown":
                        self._deactivate_shutdown()
                    if key != "close_software":
                        self._deactivate_close_software()
                    if key != "run_other":
                        self._deactivate_run_other()
        else:
            # 取消选择后，检查是否所有动作选项都未选中
            all_action_keys = self._PRIMARY_ACTIONS.union(
                self._SECONDARY_ACTIONS
            ).union(self._OPTIONAL_ACTIONS)
            has_any_selected = any(
                self._post_action_state.get(action_key, False)
                for action_key in all_action_keys
            )
            if not has_any_selected:
                # 如果什么都没选，自动勾选"无动作"
                self._post_action_state["none"] = True
                none_widget = self.post_action_widgets.get("none")
                if isinstance(none_widget, CheckBox):
                    none_widget.setChecked(True)

        self._update_combo_enabled_state()
        self._update_program_inputs_enabled()
        self._post_action_syncing = False
        self._save_post_action_state()

    def _deactivate_all_post_actions_except_none(self) -> None:
        """取消除"无动作"外的所有选项"""
        all_other_actions = self._PRIMARY_ACTIONS.union(self._SECONDARY_ACTIONS).union(
            self._OPTIONAL_ACTIONS
        ) - {"none"}
        for action_key in all_other_actions:
            widget = self.post_action_widgets.get(action_key)
            if isinstance(widget, CheckBox):
                widget.setChecked(False)
            self._post_action_state[action_key] = False
        # 更新相关UI状态
        self._update_combo_enabled_state()
        self._update_program_inputs_enabled()

    def _deactivate_none_action(self) -> None:
        """取消"无动作"选项"""
        none_widget = self.post_action_widgets.get("none")
        if isinstance(none_widget, CheckBox):
            none_widget.setChecked(False)
        self._post_action_state["none"] = False

    def _deactivate_shutdown(self) -> None:
        """取消"关机"选项"""
        shutdown_widget = self.post_action_widgets.get("shutdown")
        if isinstance(shutdown_widget, CheckBox):
            shutdown_widget.setChecked(False)
        self._post_action_state["shutdown"] = False

    def _deactivate_close_software(self) -> None:
        """取消"退出软件"选项"""
        close_software_widget = self.post_action_widgets.get("close_software")
        if isinstance(close_software_widget, CheckBox):
            close_software_widget.setChecked(False)
        self._post_action_state["close_software"] = False

    def _deactivate_run_other(self) -> None:
        """取消"切换其他配置"选项"""
        run_other_widget = self.post_action_widgets.get("run_other")
        if isinstance(run_other_widget, CheckBox):
            run_other_widget.setChecked(False)
        self._post_action_state["run_other"] = False

    def _on_other_config_changed(self, combo: ComboBox, index: int) -> None:
        if self._post_action_syncing:
            return
        self._post_action_state["target_config"] = combo.itemData(index) or ""
        self._save_post_action_state()

    def _update_combo_enabled_state(self) -> None:
        combo = self.post_action_widgets.get("target_config")
        if not isinstance(combo, ComboBox):
            return

        enabled = bool(self._post_action_state.get("run_other"))
        combo.setEnabled(enabled)
        if enabled and not self._post_action_state.get("target_config"):
            # 自动选择第一个有效配置
            for idx in range(combo.count()):
                data = combo.itemData(idx)
                if data:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
                    self._post_action_state["target_config"] = data
                    break
        elif not enabled:
            combo.blockSignals(True)
            if combo.count() > 0:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)
            self._post_action_state["target_config"] = ""

    def _create_program_input_fields(self) -> None:
        """创建运行其他程序的路径与参数输入框"""
        path_label = BodyLabel(self.tr("Program path"))
        self.option_page_layout.addWidget(path_label)

        # PathLineEdit 未传 file_filter 时使用内置跨平台默认（Windows: .exe+全部，macOS/Linux: 全部）
        path_input = PathLineEdit()
        path_input.setPlaceholderText(self.tr("Select executable path"))
        path_input.textChanged.connect(
            lambda text: self._on_program_input_changed("program_path", text)
        )
        self.option_page_layout.addWidget(path_input)

        args_label = BodyLabel(self.tr("Program arguments"))
        self.option_page_layout.addWidget(args_label)

        args_input = LineEdit()
        args_input.setPlaceholderText(self.tr("Extra startup arguments"))
        args_input.textChanged.connect(
            lambda text: self._on_program_input_changed("program_args", text)
        )
        self.option_page_layout.addWidget(args_input)

        self.post_action_widgets["program_path_label"] = path_label
        self.post_action_widgets["program_path"] = path_input
        self.post_action_widgets["program_args_label"] = args_label
        self.post_action_widgets["program_args"] = args_input

    def _apply_program_inputs_state(self) -> None:
        """根据状态填充程序路径与参数"""
        path_widget = self.post_action_widgets.get("program_path")
        if isinstance(path_widget, PathLineEdit):
            path_widget.blockSignals(True)
            path_widget.setText(self._post_action_state.get("program_path", ""))
            path_widget.blockSignals(False)

        args_widget = self.post_action_widgets.get("program_args")
        if isinstance(args_widget, LineEdit):
            args_widget.blockSignals(True)
            args_widget.setText(self._post_action_state.get("program_args", ""))
            args_widget.blockSignals(False)

    def _update_program_inputs_enabled(self) -> None:
        """根据开关控制输入框可用状态"""
        enabled = bool(self._post_action_state.get("run_program"))
        for key in (
            "program_path_label",
            "program_path",
            "program_args_label",
            "program_args",
        ):
            widget = self.post_action_widgets.get(key)
            if widget:
                widget.setEnabled(enabled)

    def _on_program_input_changed(self, key: str, value: str) -> None:
        if self._post_action_syncing:
            return
        self._post_action_state[key] = value
        self._save_post_action_state()

    # endregion

    # region 数据 & 持久化
    def _load_available_configs(self) -> List[Tuple[str, str]]:
        configs: List[Tuple[str, str]] = []
        config_service = getattr(self.service_coordinator, "config_service", None)
        if not config_service:
            return configs

        try:
            for info in config_service.list_configs():
                configs.append(
                    (
                        info.get("item_id", ""),
                        info.get("name", "") or self.tr("Unnamed Configuration"),
                    )
                )
        except Exception as exc:
            logger.error(f"加载配置列表失败: {exc}")
        return configs

    def _save_post_action_state(self) -> None:
        try:
            # 仅写入 post_action 片段，避免携带无关字段导致覆盖
            payload = dict(self._post_action_state)
            self.current_config[self._CONFIG_KEY] = payload
            option_service = self.service_coordinator.option_service
            ok = option_service.update_option(self._CONFIG_KEY, payload)
            if not ok:
                # 兜底：直接更新 POST_ACTION 任务后再持久化
                from app.common.constants import POST_ACTION

                task = option_service.task_service.get_task(POST_ACTION)
                if task:
                    # 只保存 post_action 字段，不保存其他字段（如 speedrun_config 等）
                    task.task_option[self._CONFIG_KEY] = payload
                    # 确保不包含 speedrun_config
                    if "_speedrun_config" in task.task_option:
                        del task.task_option["_speedrun_config"]
                    if not option_service.task_service.update_task(task):
                        logger.warning("完成后操作配置兜底保存失败")
                else:
                    logger.warning("未找到 Post-Action 任务，无法保存完成后操作配置")
        except Exception as exc:
            logger.error(f"保存完成后操作配置失败: {exc}")

    # endregion
