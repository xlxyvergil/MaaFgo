"""
Inputs 选项项
多输入框选项组类型
"""
import re
from typing import Any, Dict, Optional

from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import LineEdit

from app.common.signal_bus import signalBus
from app.utils.logger import logger
from .base import OptionItemBase


class InputsOptionItem(OptionItemBase):
    """
    多输入框选项组
    用于需要多个相关输入字段的场景
    每个输入框都有自己的标签、验证规则和默认值
    """

    def __init__(
        self, key: str, config: Dict[str, Any], parent: Optional["OptionItemBase"] = None
    ):
        # 设置 config_type
        config["type"] = "inputs"
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
        """初始化多输入框 UI"""
        # 创建主标签（如果有）
        main_label_text = self.config.get("label")
        if main_label_text:
            self.label = self._create_label_with_optional_icon(
                main_label_text,
                self.config.get("icon"),
                self.main_option_layout,
                self.config.get("description"),
            )

        # 创建多个输入框
        self.control_widget = {}  # 字典存储多个输入框
        inputs = self.config.get("inputs", [])

        for input_item in inputs:
            input_name = input_item.get("name", "")
            input_label_text = input_item.get("label", input_name)

            # 创建输入项容器
            input_container = QVBoxLayout()
            input_container.setContentsMargins(10, 5, 10, 5)

            # 创建标签
            input_label = self._create_label_with_optional_icon(
                input_label_text,
                input_item.get("icon"),
                input_container,
                input_item.get("description"),
            )

            # 创建输入框
            line_edit = LineEdit()

            # 设置默认值
            if "default" in input_item:
                line_edit.setText(str(input_item["default"]))

            # 设置占位提示
            if input_label_text:
                line_edit.setPlaceholderText(input_label_text)

            # 添加验证规则
            if "verify" in input_item:
                verify_pattern = input_item["verify"]
                pattern_msg = input_item.get("pattern_msg") or self.config.get(
                    "pattern_msg"
                )
                self._connect_validator(line_edit, verify_pattern, pattern_msg)

            input_container.addWidget(line_edit)
            self.main_option_layout.addLayout(input_container)

            # 连接信号
            line_edit.textChanged.connect(
                lambda text, name=input_name: self._on_lineedit_changed(name, text)
            )

            self.control_widget[input_name] = line_edit

    def init_config(self):
        """初始化配置值"""
        if isinstance(self.control_widget, dict):
            self.current_value = {
                name: widget.text() for name, widget in self.control_widget.items()
            }
        else:
            logger.warning("inputs 类型的控件未初始化，无法读取默认值")

    def _on_lineedit_changed(self, input_name: str, text: str):
        """输入框值改变处理"""
        if input_name and isinstance(self.current_value, dict):
            self.current_value[input_name] = text

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
            if isinstance(lineedit_value, dict):
                for input_name, input_value in lineedit_value.items():
                    if input_name in self.control_widget:
                        widget = self.control_widget[input_name]
                        widget.blockSignals(True)
                        try:
                            widget.setText(str(input_value))
                            if isinstance(self.current_value, dict):
                                self.current_value[input_name] = str(input_value)
                        finally:
                            widget.blockSignals(False)
            else:
                logger.warning(f"inputs 类型期望字典值，收到: {type(lineedit_value)}")
        else:
            logger.warning("inputs 类型的控件未准备好，无法设置值")

    def get_option(self) -> Dict[str, Any]:
        """获取当前选项的配置"""
        return {"value": self.current_value}

    def get_simple_option(self) -> Any:
        """获取简单的选项值"""
        return self.current_value


__all__ = ["InputsOptionItem"]
