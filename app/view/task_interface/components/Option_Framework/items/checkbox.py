"""
Checkbox 选项项
多选框类型的选项，用户可从预定义选项中选择多个
"""
from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import CheckBox

from app.utils.logger import logger
from .base import OptionItemBase


class CheckBoxOptionItem(OptionItemBase):
    """
    多选框选项项
    用户可以同时勾选多个 case，所有被选中的 case 的 pipeline_override
    按照 cases 数组中的定义顺序依次合并生效。
    """

    def __init__(
        self, key: str, config: Dict[str, Any], parent: Optional["OptionItemBase"] = None
    ):
        super().__init__(key, config, parent)
        self._checkbox_widgets: List[CheckBox] = []
        self._checkbox_name_map: Dict[CheckBox, str] = {}  # checkbox -> case name
        self.current_value: List[str] = []  # 当前选中的 case name 列表
        self.init_ui()
        self.init_config()
        # 初始化完成后启用动画
        self._animation_enabled = True

    def init_ui(self):
        """初始化 Checkbox UI"""
        # 创建标签
        label_text = self.config.get("label", self.key)
        self.label = self._create_label_with_optional_icon(
            label_text,
            self.config.get("icon"),
            self.main_option_layout,
            self.config.get("description"),
        )

        # 创建多选框容器
        checkbox_container = QWidget()
        checkbox_layout = QVBoxLayout(checkbox_container)
        checkbox_layout.setContentsMargins(10, 0, 0, 0)
        checkbox_layout.setSpacing(4)

        # 为每个 case 创建一个 CheckBox（兼容 cases 和 options 两种字段名）
        options = self.config.get("cases", self.config.get("options", []))
        for option in options:
            if isinstance(option, dict):
                label = option.get("label", option.get("name", ""))
                name = option.get("name", label)
                description = option.get("description")
            else:
                label = str(option)
                name = label
                description = None

            cb = CheckBox(label)
            if description:
                cb.setToolTip(description)

            cb.stateChanged.connect(self._on_checkbox_changed)
            checkbox_layout.addWidget(cb)

            self._checkbox_widgets.append(cb)
            self._checkbox_name_map[cb] = name
            self._option_map[label] = name
            self._reverse_option_map[name] = label

        self.main_option_layout.addWidget(checkbox_container)

        # 预加载子选项
        self._preload_child_options()

    def init_config(self):
        """初始化配置值（应用 default_case）"""
        default_case = self.config.get("default_case", [])
        if isinstance(default_case, str):
            default_case = [default_case]

        for cb in self._checkbox_widgets:
            name = self._checkbox_name_map[cb]
            cb.blockSignals(True)
            cb.setChecked(name in default_case)
            cb.blockSignals(False)

        self.current_value = list(default_case)
        # 触发初始子选项显示（跳过动画）
        self._update_children_for_checkbox(skip_animation=True)

    def _on_checkbox_changed(self, _state: int):
        """任一多选框状态改变"""
        self.current_value = self._collect_selected_names()
        self._update_children_for_checkbox()
        self.option_changed.emit(self.key, self.current_value)

    def _collect_selected_names(self) -> List[str]:
        """收集所有选中的 case name，按 cases 定义顺序"""
        selected = []
        for cb in self._checkbox_widgets:
            if cb.isChecked():
                selected.append(self._checkbox_name_map[cb])
        return selected

    def _update_children_for_checkbox(self, skip_animation: bool = False):
        """
        更新 checkbox 子选项可见性：
        只要某个 case 被选中，就显示对应的子选项
        """
        children = self.config.get("children", {})
        if not children:
            return

        selected_set = set(self.current_value)
        visible_any = False

        for option_value, child_config in children.items():
            if option_value not in self._child_value_map:
                self.add_child_option(option_value, child_config)

            child_keys = self._child_value_map.get(option_value, [])
            is_selected = option_value in selected_set
            for child_key in child_keys:
                widget = self.child_options.get(child_key)
                if widget:
                    widget.setVisible(is_selected)
                    if is_selected:
                        visible_any = True

        animator = self._children_animator
        use_animation = (
            self._animation_enabled and not skip_animation and animator is not None
        )

        if visible_any:
            if use_animation and animator is not None:
                animator.expand()
            else:
                self.children_wrapper.setVisible(True)
                self.children_wrapper.setMaximumHeight(16777215)
        else:
            if use_animation and animator is not None:
                animator.collapse(on_finished=self._hide_all_children)
            else:
                self._hide_all_children()
                self.children_wrapper.setVisible(False)
                self.children_wrapper.setMaximumHeight(0)

    def set_value(self, value: Any, skip_animation: bool = True):
        """设置选项的值"""
        # 如果传入的是字典，提取 value
        if isinstance(value, dict):
            if "value" in value:
                value = value["value"]
            else:
                logger.warning(f"尝试为 checkbox 设置字典值，将忽略: {value}")
                return

        # 将值标准化为列表
        if isinstance(value, str):
            value = [value]
        elif not isinstance(value, list):
            logger.warning(f"checkbox 值类型不正确: {type(value)}, 值: {value}")
            return

        value_set = set(str(v) for v in value)

        for cb in self._checkbox_widgets:
            name = self._checkbox_name_map[cb]
            cb.blockSignals(True)
            cb.setChecked(name in value_set)
            cb.blockSignals(False)

        self.current_value = [
            self._checkbox_name_map[cb]
            for cb in self._checkbox_widgets
            if cb.isChecked()
        ]
        self._update_children_for_checkbox(skip_animation=skip_animation)

    def get_option(self) -> Dict[str, Any]:
        """获取当前选项的配置（递归获取子选项）"""
        result: Dict[str, Any] = {"value": list(self.current_value)}

        # 递归获取子选项的配置
        children_config: Dict[str, Any] = {}
        selected_set = set(self.current_value)

        children_def = self.config.get("children", {})

        for child_key, child_widget in self.child_options.items():
            if child_widget is None:
                continue
            child_option = child_widget.get_option()
            child_name = child_widget.config.get("name", "")
            config_key = child_name if child_name else child_key

            # 判断该子选项所属的 case 是否被选中
            option_value = self.get_option_value_for_child_key(child_key)
            is_active = option_value in selected_set and child_widget.isVisible()

            if child_widget.config_type in ["input", "inputs"] and "children" not in child_option:
                if not is_active:
                    children_config[config_key] = {
                        "value": child_option.get("value", ""),
                        "hidden": True,
                    }
                else:
                    children_config[config_key] = child_option.get("value", "")
            else:
                if not is_active:
                    child_option["hidden"] = True
                children_config[config_key] = child_option

        if children_config:
            result["children"] = children_config

        return result

    def get_simple_option(self) -> Any:
        """获取简单的选项值"""
        return list(self.current_value)


__all__ = ["CheckBoxOptionItem"]
