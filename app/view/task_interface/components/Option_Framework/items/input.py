"""
Input 选项项
单输入框类型的选项
"""
import re
from typing import Any, Dict, Optional

from qfluentwidgets import LineEdit

from app.common.signal_bus import signalBus
from app.utils.logger import logger
from .base import OptionItemBase


class InputOptionItem(OptionItemBase):
    """
    单输入框选项项
    用于用户输入单个文本值
    支持验证规则和默认值
    """

    def __init__(
        self, key: str, config: Dict[str, Any], parent: Optional["OptionItemBase"] = None
    ):
        # 设置 config_type
        config["type"] = "input"
        super().__init__(key, config, parent)
        self.init_ui()
        self.init_config()
        # 初始化完成后启用动画
        self._animation_enabled = True

    def _connect_validator(
        self, line_edit: LineEdit, pattern: str, message: Optional[str]
    ):
        """将验证规则应用到 line edit，并在首次失效时显示 InfoBar 警告"""
        try:
            last_invalid = False

            def validate(text: str):
                nonlocal last_invalid
                invalid = bool(text and not re.match(pattern, text))
                line_edit.setError(invalid)
                if invalid and message and not last_invalid:
                    signalBus.info_bar_requested.emit("warning", message)
                last_invalid = invalid

            line_edit.textChanged.connect(validate)
        except Exception as e:
            logger.error(f"设置输入验证规则失败: {pattern}, 错误: {e}")

    def init_ui(self):
        """初始化单输入框 UI"""
        inputs = self.config.get("inputs", [])

        if inputs:
            # 从 inputs 数组获取第一个输入配置
            input_item = inputs[0]
            input_name = input_item.get("name", self.key)

            # 创建标签
            label_text = input_item.get("label") or self.config.get("label", self.key)
            description = input_item.get("description") or self.config.get("description")
            if label_text:
                self.label = self._create_label_with_optional_icon(
                    label_text,
                    input_item.get("icon") or self.config.get("icon"),
                    self.main_option_layout,
                    description,
                )

            # 创建输入框
            line_edit = LineEdit()
            self.control_widget = {input_name: line_edit}

            # 设置默认值
            if "default" in input_item:
                line_edit.setText(str(input_item["default"]))

            # 设置占位提示
            placeholder = input_item.get("label") or self.config.get("label", "")
            if placeholder:
                line_edit.setPlaceholderText(placeholder)

            # 添加验证规则
            if "verify" in input_item:
                verify_pattern = input_item["verify"]
                pattern_msg = input_item.get("pattern_msg") or self.config.get(
                    "pattern_msg"
                )
                self._connect_validator(line_edit, verify_pattern, pattern_msg)

            # 连接信号
            line_edit.textChanged.connect(
                lambda text, name=input_name: self._on_lineedit_changed(name, text)
            )

            self.main_option_layout.addWidget(line_edit)
        else:
            # 无 inputs 配置，创建简单的单输入框
            label_text = self.config.get("label", self.key)
            self.label = self._create_label_with_optional_icon(
                label_text,
                self.config.get("icon"),
                self.main_option_layout,
                self.config.get("description"),
            )

            self.control_widget = LineEdit()

            # 设置默认值
            if "default" in self.config:
                self.control_widget.setText(str(self.config["default"]))

            # 添加验证规则
            if "verify" in self.config:
                verify_pattern = self.config["verify"]
                pattern_msg = self.config.get("pattern_msg")
                self._connect_validator(self.control_widget, verify_pattern, pattern_msg)

            # 连接信号
            self.control_widget.textChanged.connect(
                lambda text: self._on_lineedit_changed(None, text)
            )

            self.main_option_layout.addWidget(self.control_widget)

    def init_config(self):
        """初始化配置值"""
        if isinstance(self.control_widget, dict):
            # 字典形式存储的输入框
            self.current_value = {
                name: widget.text() for name, widget in self.control_widget.items()
            }
        elif isinstance(self.control_widget, LineEdit):
            # 单个输入框
            self.current_value = self.control_widget.text()
        else:
            logger.warning("input 类型的控件未初始化，无法读取默认值")

    def _on_lineedit_changed(self, input_name: Optional[str], text: str):
        """输入框值改变处理"""
        if isinstance(self.control_widget, dict):
            if input_name:
                self.current_value[input_name] = text
        else:
            self.current_value = text

        # 发出信号
        self.option_changed.emit(self.key, self.current_value)

    def _unwrap_lineedit_value(self, value: Any) -> Any:
        """如果是字典格式（包含 value 字段），提取真正的输入值"""
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value

    def set_value(self, value: Any, skip_animation: bool = True):
        """设置选项的值"""
        lineedit_value = self._unwrap_lineedit_value(value)

        if isinstance(self.control_widget, dict):
            # 字典形式存储的输入框
            if isinstance(lineedit_value, dict):
                for input_name, input_value in lineedit_value.items():
                    if input_name in self.control_widget:
                        widget = self.control_widget[input_name]
                        widget.blockSignals(True)
                        try:
                            widget.setText(str(input_value))
                            self.current_value[input_name] = str(input_value)
                        finally:
                            widget.blockSignals(False)
            elif self.control_widget:
                # 单值映射到唯一的输入框
                input_name = next(iter(self.control_widget))
                widget = self.control_widget[input_name]
                widget.blockSignals(True)
                try:
                    text_value = "" if lineedit_value is None else str(lineedit_value)
                    widget.setText(text_value)
                    self.current_value[input_name] = text_value
                finally:
                    widget.blockSignals(False)
        elif isinstance(self.control_widget, LineEdit):
            # 单个输入框
            text_value = ""
            if isinstance(lineedit_value, dict):
                if lineedit_value:
                    text_value = str(next(iter(lineedit_value.values())))
            elif lineedit_value is not None:
                text_value = str(lineedit_value)
            self.control_widget.blockSignals(True)
            try:
                self.control_widget.setText(text_value)
                self.current_value = text_value
            finally:
                self.control_widget.blockSignals(False)
        else:
            logger.warning("input 类型的控件未准备好，无法设置值")

    def get_option(self) -> Dict[str, Any]:
        """获取当前选项的配置"""
        return {"value": self.current_value}

    def get_simple_option(self) -> Any:
        """获取简单的选项值"""
        return self.current_value


__all__ = ["InputOptionItem"]
