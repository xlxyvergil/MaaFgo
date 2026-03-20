"""
File 选项项
文件选择类型的选项
"""
from typing import Any, Dict, Optional

from app.common.signal_bus import signalBus
from app.utils.logger import logger
from app.widget.PathLineEdit import PathLineEdit
from .base import OptionItemBase


class FileOptionItem(OptionItemBase):
    """
    文件选择选项项
    用于选择文件路径
    """

    def __init__(
        self, key: str, config: Dict[str, Any], parent: Optional["OptionItemBase"] = None
    ):
        # 设置 config_type
        config["type"] = "file"
        super().__init__(key, config, parent)
        self.init_ui()
        self.init_config()
        # 初始化完成后启用动画
        self._animation_enabled = True

    def init_ui(self):
        """初始化文件选择 UI"""
        # 创建标签
        label_text = self.config.get("label", self.key)
        self.label = self._create_label_with_optional_icon(
            label_text,
            self.config.get("icon"),
            self.main_option_layout,
            self.config.get("description"),
        )

        # 创建文件选择控件
        file_filter = self.config.get("filter")
        self.control_widget = PathLineEdit(file_filter=file_filter)

        # 设置默认值
        if "default" in self.config:
            self.control_widget.setText(str(self.config["default"]))

        # 设置占位提示
        placeholder = self.config.get("placeholder") or self.config.get("label", "")
        if placeholder:
            self.control_widget.setPlaceholderText(placeholder)

        # 连接信号
        self.control_widget.textChanged.connect(
            lambda text: self._on_file_path_changed(text)
        )

        self.main_option_layout.addWidget(self.control_widget)

    def init_config(self):
        """初始化配置值"""
        self.current_value = self.control_widget.text()

    def _on_file_path_changed(self, text: str):
        """文件路径改变处理"""
        self.current_value = text
        # 发出信号
        self.option_changed.emit(self.key, self.current_value)

    def set_value(self, value: Any, skip_animation: bool = True):
        """设置选项的值"""
        text_value = "" if value is None else str(value)
        self.control_widget.blockSignals(True)
        try:
            self.control_widget.setText(text_value)
            self.current_value = text_value
        finally:
            self.control_widget.blockSignals(False)

    def get_option(self) -> Dict[str, Any]:
        """获取当前选项的配置"""
        return {"value": self.current_value}

    def get_simple_option(self) -> Any:
        """获取简单的选项值"""
        return self.current_value


__all__ = ["FileOptionItem"]
