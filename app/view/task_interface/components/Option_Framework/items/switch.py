"""
Switch 选项项
开关按钮类型的选项
"""
from typing import Any, Dict, Optional

from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import BodyLabel, SwitchButton

from app.utils.logger import logger
from .base import OptionItemBase


class SwitchOptionItem(OptionItemBase):
    """
    开关按钮选项项
    用于 Yes/No 类型的二选一选项
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
        """初始化 Switch UI（标题和开关在同一行）"""
        # 创建水平布局容器，用于放置标题和开关
        switch_container = QWidget()
        switch_layout = QHBoxLayout(switch_container)
        switch_layout.setContentsMargins(0, 10, 0, 10)
        switch_layout.setSpacing(10)

        # 创建标签（标题）
        label_text = self.config.get("label", self.key)
        self._add_icon_to_layout(switch_layout, self.config.get("icon"))
        self.label = BodyLabel(label_text)
        switch_layout.addWidget(self.label)

        description = self.config.get("description")
        if description:
            indicator = self._create_description_indicator(description)
            switch_layout.addWidget(indicator)

        # 添加弹性空间，让开关靠右对齐
        switch_layout.addStretch()

        # 创建开关按钮
        self.control_widget = SwitchButton(parent=self)

        # 保存选项映射关系
        self._option_map = {"是": "Yes", "否": "No"}
        self._reverse_option_map = {"Yes": "是", "No": "否"}

        # 设置开关按钮的文本标签
        self.control_widget.setOnText("是")
        self.control_widget.setOffText("否")

        # 将开关按钮添加到水平布局
        switch_layout.addWidget(self.control_widget)

        # 将整个容器添加到主选项布局
        self.main_option_layout.addWidget(switch_container)

        # 预加载子选项
        self._preload_child_options()

        # 连接信号（在预创建子选项后）
        self.control_widget.checkedChanged.connect(self._on_switch_changed)

    def init_config(self):
        """初始化配置值"""
        if isinstance(self.control_widget, SwitchButton):
            is_checked = self.control_widget.isChecked()
            self.current_value = "Yes" if is_checked else "No"
            # 触发初始子选项显示（跳过动画）
            self._update_children_visibility(self.current_value, skip_animation=True)

    def _on_switch_changed(self, checked: bool):
        """开关按钮值改变处理"""
        actual_value = "Yes" if checked else "No"
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
                logger.warning(f"尝试为 switch 设置字典值，将忽略: {value}")
                return

        # 标准化值：处理各种可能的 Yes/No 变体
        value_str = str(value).strip()
        value_upper = value_str.upper()

        # 判断应该设置为 checked 还是 unchecked
        if value_upper in ["YES", "Y", "TRUE", "1", "ON", "是"]:
            target_checked = True
            target_value = "Yes"
        elif value_upper in ["NO", "N", "FALSE", "0", "OFF", "否"]:
            target_checked = False
            target_value = "No"
        else:
            logger.warning(f"switch 值类型不正确: {value}")
            return

        if isinstance(self.control_widget, SwitchButton):
            switch_button = self.control_widget
            switch_button.blockSignals(True)
            try:
                switch_button.setChecked(target_checked)
                self.current_value = target_value
                self._update_children_visibility(
                    target_value, skip_animation=skip_animation
                )
            finally:
                switch_button.blockSignals(False)
        else:
            logger.warning("switch 控件未准备好，无法设置值")

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


__all__ = ["SwitchOptionItem"]
