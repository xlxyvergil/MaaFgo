"""
ComboBox 选项项
下拉选择框类型的选项
"""
from typing import Any, Dict, Optional

from qfluentwidgets import ComboBox

from app.utils.logger import logger
from .base import OptionItemBase, TooltipComboBox


class ComboBoxOptionItem(OptionItemBase):
    """
    下拉选择框选项项
    用于从预定义的选项列表中选择一个值
    """

    def __init__(
        self, key: str, config: Dict[str, Any], parent: Optional["OptionItemBase"] = None
    ):
        super().__init__(key, config, parent)
        self.init_ui()
        self.init_config()
        # 初始化完成后启用动画
        self._animation_enabled = True

    def init_ui(self):
        """初始化 ComboBox UI"""
        # 创建标签
        label_text = self.config.get("label", self.key)
        self.label = self._create_label_with_optional_icon(
            label_text,
            self.config.get("icon"),
            self.main_option_layout,
            self.config.get("description"),
        )

        # 创建下拉框
        self.control_widget = TooltipComboBox()

        # 添加选项
        options = self.config.get("options", [])
        for option in options:
            option_icon = None
            option_description = None
            if isinstance(option, dict):
                label = option.get("label", "")
                name = option.get("name", label)
                option_icon = option.get("icon")
                option_description = option.get("description")
            else:
                label = str(option)
                name = label

            icon_to_use = self._resolve_icon(option_icon)
            self.control_widget.addItem(
                label,
                icon_to_use,
                None,
                option_description,
            )

            self._option_map[label] = name
            self._reverse_option_map[name] = label

        self.main_option_layout.addWidget(self.control_widget)

        # 预加载子选项
        self._preload_child_options()

        # 连接信号（在预创建子选项后）
        self.control_widget.currentTextChanged.connect(self._on_combobox_changed)

    def init_config(self):
        """初始化配置值"""
        if isinstance(self.control_widget, ComboBox):
            current_label = self.control_widget.currentText()
            self.current_value = self._option_map.get(current_label, current_label)
            # 触发初始子选项显示（跳过动画）
            self._update_children_visibility(self.current_value, skip_animation=True)

    def _on_combobox_changed(self, label: str):
        """下拉框值改变处理"""
        # 获取实际值（name）
        actual_value = self._option_map.get(label, label)
        self.current_value = actual_value

        # 处理子选项显示/隐藏
        self._update_children_visibility(actual_value)

        # 发出信号
        self.option_changed.emit(self.key, self.current_value)

    def set_value(self, value: Any, skip_animation: bool = True):
        """设置选项的值"""
        # 如果传入的是字典，提取 value
        if isinstance(value, dict):
            if "value" in value:
                value = value["value"]
            else:
                logger.warning(f"尝试为 combobox 设置字典值，将忽略: {value}")
                return

        # 确保值是字符串或可哈希的类型
        if not isinstance(value, (str, int, float)) and value is not None:
            logger.warning(f"combobox 值类型不正确: {type(value)}, 值: {value}")
            return

        # 尝试从反向映射获取 label
        label = self._reverse_option_map.get(str(value), str(value))
        if isinstance(self.control_widget, ComboBox):
            combobox = self.control_widget
            index = combobox.findText(label)
            if index >= 0:
                combobox.blockSignals(True)
                try:
                    combobox.setCurrentIndex(index)
                    self.current_value = str(value)
                    self._update_children_visibility(
                        str(value), skip_animation=skip_animation
                    )
                finally:
                    combobox.blockSignals(False)
        else:
            logger.warning("combobox 控件未准备好，无法设置值")

    def get_option(self) -> Dict[str, Any]:
        """获取当前选项的配置（递归获取子选项）"""
        result: Dict[str, Any] = {"value": self.current_value}

        # 递归获取子选项的配置
        children_config = {}
        active_child_keys = set()

        # 记录当前选中值对应的子选项键
        children_def = self.config.get("children", {})
        selected_str = str(self.current_value) if self.current_value is not None else ""
        matched_key = self._match_child_key(
            children_def,
            self.current_value,
            selected_str,
            selected_str.strip(),
        )
        if matched_key:
            active_child_keys = set(self._child_value_map.get(matched_key, []))

        # 获取所有已创建的子选项配置
        for child_key, child_widget in self.child_options.items():
            if child_widget:
                child_option = child_widget.get_option()

                # 获取子选项的 name（用于简洁的配置保存格式）
                child_name = child_widget.config.get("name", "")
                # 使用 child_name 作为配置 key（如果存在），否则使用内部 key
                config_key = child_name if child_name else child_key

                # 检查子选项是否被隐藏
                is_active_child = child_key in active_child_keys
                is_hidden = not (is_active_child and child_widget.isVisible())

                # 对于 input 类型的子选项，如果只有 value，直接使用值
                if child_widget.config_type in ["input", "inputs"] and "children" not in child_option:
                    if is_hidden:
                        children_config[config_key] = {
                            "value": child_option.get("value", ""),
                            "hidden": True,
                        }
                    else:
                        children_config[config_key] = child_option.get("value", "")
                else:
                    if is_hidden:
                        child_option["hidden"] = True
                    children_config[config_key] = child_option

        if children_config:
            result["children"] = children_config

        return result

    def get_simple_option(self) -> Any:
        """获取简单的选项值"""
        children = self.config.get("children", {})
        if children and self.current_value in children:
            child_widget = self.child_options.get(self.current_value)
            if child_widget and child_widget.isVisible():
                child_value = child_widget.get_simple_option()
                return {
                    "value": self.current_value,
                    "children": {self.current_value: child_value},
                }
        return self.current_value


__all__ = ["ComboBoxOptionItem"]
