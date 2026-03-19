"""
选项项组件
单个选项的独立组件，支持 combobox 和 lineedit 类型，以及子选项
"""
# type: ignore[attr-defined]
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Union
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSizePolicy,
    QFrame,
    QLabel,
    QLayout,
)
from PySide6.QtCore import Qt, Signal
from qfluentwidgets import ComboBox, LineEdit, BodyLabel, ToolTipFilter, SwitchButton, isDarkTheme, qconfig
import re
from app.common.signal_bus import signalBus
from app.utils.logger import logger
from app.view.task_interface.components.Option_Framework.animations import HeightAnimator


class TooltipComboBox(ComboBox):
    """继承自 ComboBox，支持下拉菜单选项的描述文本显示"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self._item_descriptions: List[Optional[str]] = []

    def addItem(
        self,
        text: str,
        icon: Any = None,
        userData: Any = None,
        description: Optional[str] = None,
    ):
        super().addItem(text, icon, userData)
        self._item_descriptions.append(description)

    def removeItem(self, index: int):
        super().removeItem(index)
        if 0 <= index < len(self._item_descriptions):
            self._item_descriptions.pop(index)

    def clear(self):
        super().clear()
        self._item_descriptions.clear()

    def _createComboMenu(self):
        """创建使用描述委托的下拉菜单"""
        from app.view.task_interface.components.Option_Framework.items.base import (
            _DescriptionComboBoxMenu,
        )

        return _DescriptionComboBoxMenu(self._item_descriptions, parent=self)


class OptionItemWidget(QWidget):
    """
    选项项组件
    一个独立的选项组件，包含标题和对应的控件（combobox 或 lineedit）
    支持子选项，可以递归获取选项配置
    """
    
    # 信号：选项值改变时发出
    option_changed = Signal(str, object)  # key, value
    
    def __init__(self, key: str, config: Dict[str, Any], parent: Optional[QWidget] = None):
        """
        初始化选项项组件
        
        :param key: 选项的键名
        :param config: 选项配置字典，包含 label, type, description, options/inputs 等
        :param parent: 父组件
        """
        super().__init__(parent)
        self.key = key
        self.config = config
        self.config_type = config.get("type", "combobox")
        self.child_options: Dict[str, 'OptionItemWidget'] = {}  # 子选项组件字典
        self._child_value_map: Dict[str, List[str]] = {}
        self._child_name_map: Dict[Tuple[str, str], str] = {}
        self.current_value: Any = None  # 当前选中的值
        self.control_widget: Union[ComboBox, SwitchButton, LineEdit, Dict[str, LineEdit], None] = None
        inputs_value = self.config.get("inputs")
        self._single_input_mode = (
            self.config_type == "lineedit"
            and isinstance(inputs_value, list)
            and len(inputs_value) == 1
            and self.config.get("single_input", False)
        )
        
        # 动画控制标志：初始化和配置应用时跳过动画
        self._animation_enabled = False
        self._children_animator: Optional[HeightAnimator] = None
        
        self._init_ui()
        self._init_config()
        
        # 初始化完成后启用动画
        self._animation_enabled = True

    ICON_SIZE = 18

    def _resolve_icon(self, icon_source: Any) -> Optional[QIcon]:
        """将 icon 字段转换为 QIcon 对象"""
        if not icon_source:
            return None
        if isinstance(icon_source, QIcon):
            return icon_source
        if isinstance(icon_source, QPixmap):
            return QIcon(icon_source)
        if isinstance(icon_source, Path):
            icon_source = str(icon_source)
        if isinstance(icon_source, str):
            icon = QIcon(icon_source)
            return icon if not icon.isNull() else None
        return None

    def _add_icon_to_layout(self, layout: QHBoxLayout, icon_source: Any) -> Optional[QLabel]:
        """在横向布局前端插入图标标签"""
        icon = self._resolve_icon(icon_source)
        if not icon:
            return None

        pixmap = icon.pixmap(self.ICON_SIZE, self.ICON_SIZE)
        if pixmap.isNull():
            return None

        icon_label = QLabel()
        icon_label.setPixmap(pixmap)
        icon_label.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        icon_label.setScaledContents(True)
        layout.addWidget(icon_label)
        return icon_label

    def _create_description_indicator(self, tooltip_text: str) -> BodyLabel:
        """创建用于展示描述的问号标记"""
        indicator = BodyLabel("?")
        indicator.setObjectName("OptionDescriptionIndicator")
        indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        indicator.setFixedSize(18, 18)
        border_color = "rgba(255, 255, 255, 0.25)" if isDarkTheme() else "rgba(0, 0, 0, 0.25)"
        background_color = (
            "rgba(255, 255, 255, 0.08)" if isDarkTheme() else "rgba(0, 0, 0, 0.04)"
        )
        indicator.setStyleSheet(
            f"""
            QLabel#OptionDescriptionIndicator {{
                border: 1px solid {border_color};
                border-radius: 9px;
                padding: 0px;
                font-weight: 600;
                font-size: 12px;
                color: palette(windowText);
                background-color: {background_color};
            }}
            """
        )
        indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        filter = ToolTipFilter(indicator)
        indicator.installEventFilter(filter)
        indicator.setToolTip(tooltip_text)
        return indicator

    def _create_label_with_optional_icon(
        self,
        text: str,
        icon_source: Any,
        parent_layout: QLayout,
        description: Optional[str] = None,
    ) -> BodyLabel:
        """创建带图标和可选描述标记的横向布局，并返回 BodyLabel"""
        container = QWidget()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(6)
        self._add_icon_to_layout(container_layout, icon_source)

        label = BodyLabel(text)
        container_layout.addWidget(label)

        if description:
            indicator = self._create_description_indicator(description)
            container_layout.addWidget(indicator)

        container_layout.addStretch()
        parent_layout.addWidget(container)
        return label
    
    def _init_ui(self):
        """初始化UI"""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(5, 5, 5, 5)
        self.main_layout.setSpacing(5)
        
        # 创建主选项容器（包含标签和控件），确保不因子选项变化而抖动
        self.main_option_container = QWidget()
        self.main_option_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.main_option_layout = QVBoxLayout(self.main_option_container)
        self.main_option_layout.setContentsMargins(0, 0, 0, 0)
        self.main_option_layout.setSpacing(5)
        self.main_layout.addWidget(self.main_option_container)
        
        # 先创建子选项容器（用于存放子选项）
        # 必须在创建控件之前创建，因为 _create_combobox 中可能会调用 add_child_option
        self.children_container = QWidget()
        self.children_layout = QVBoxLayout(self.children_container)
        self.children_layout.setContentsMargins(0, 0, 0, 0)
        
        # 根据类型创建对应的控件
        # switch 类型的布局不同，需要特殊处理
        if self.config_type == "switch":
            self._create_switch()
        else:
            # 其他类型：标题在上，组件在下
            # 创建标签
            label_text = self.config.get("label", self.key)
            if not self._single_input_mode:
                self.label = self._create_label_with_optional_icon(
                    label_text,
                    self.config.get("icon"),
                    self.main_option_layout,
                    self.config.get("description"),
                )
            
            # 创建对应的控件
            if self.config_type == "combobox":
                self._create_combobox()
            elif self.config_type == "lineedit":
                self._create_lineedit()
            else:
                logger.warning(f"不支持的选项类型: {self.config_type}")
        
        # 创建子选项包装容器（水平布局：竖线 + 子选项容器）
        self.children_wrapper = QWidget()
        self.children_wrapper_layout = QHBoxLayout(self.children_wrapper)
        self.children_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        self.children_wrapper_layout.setSpacing(8)
        
        # 创建竖线控件
        self.children_indicator_line = QFrame()
        self.children_indicator_line.setFrameShape(QFrame.Shape.VLine)
        self.children_indicator_line.setFrameShadow(QFrame.Shadow.Plain)
        self.children_indicator_line.setFixedWidth(3)
        self._update_indicator_line_color()
        # 监听主题变化，更新竖线颜色
        qconfig.themeChanged.connect(self._update_indicator_line_color)
        
        # 将竖线和子选项容器添加到水平布局
        self.children_wrapper_layout.addWidget(self.children_indicator_line)
        self.children_wrapper_layout.addWidget(self.children_container, 1)  # stretch=1 让子选项容器占据剩余空间
        
        # 将子选项包装容器添加到主布局
        self.main_layout.addWidget(self.children_wrapper)
        
        # 初始状态隐藏子选项包装容器（将在 _init_config 中根据是否有子选项来设置可见性）
        self.children_wrapper.setVisible(False)
        self.children_wrapper.setMaximumHeight(0)
        
        # 创建子选项包装容器的动画控制器（动画作用于包装容器，而不是子选项容器本身）
        self._children_animator = HeightAnimator(self.children_wrapper, duration=200, parent=self)
    
    def _create_combobox(self):
        """创建下拉框"""
        self.control_widget = TooltipComboBox()
        
        # 保存选项映射关系 (label -> name 和 name -> label)
        self._option_map = {}  # label -> name
        self._reverse_option_map = {}  # name -> label
        
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
        
        self._preload_child_options()
        
        # 连接信号（在预创建子选项后）
        self.control_widget.currentTextChanged.connect(self._on_combobox_changed)
    
    def _create_switch(self):
        """创建开关按钮（标题和开关在同一行）"""
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
        
        # switch 类型固定为 Yes 和 No 两个选项
        # 设置开关按钮的文本标签
        self.control_widget.setOnText("是")
        self.control_widget.setOffText("否")
        
        # 将开关按钮添加到水平布局
        switch_layout.addWidget(self.control_widget)
        
        # 将整个容器添加到主选项布局
        self.main_option_layout.addWidget(switch_container)
        
        self._preload_child_options()
        
        # 连接信号（在预创建子选项后）
        self.control_widget.checkedChanged.connect(self._on_switch_changed)
    
    def _connect_validator(self, line_edit: LineEdit, pattern: str, message: str | None):
        """将验证规则应用到line edit，并在首次失效时显示 InfoBar 警告。"""
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

    def _create_lineedit(self):
        """创建输入框"""
        inputs = self.config.get("inputs", [])

        # 单输入模式：只渲染一个输入框，避免重复的标题/描述
        if self._single_input_mode and inputs:
            input_item = inputs[0]
            input_name = input_item.get("name", self.key)
            self.control_widget = {}
            line_edit = LineEdit()

            label_text = input_item.get("label") or self.config.get("label", self.key)
            description = input_item.get("description") or self.config.get("description")
            if label_text:
                single_label = self._create_label_with_optional_icon(
                    label_text,
                    input_item.get("icon") or self.config.get("icon"),
                    self.main_option_layout,
                    description,
                )

            # 设置默认值
            if "default" in input_item:
                line_edit.setText(str(input_item["default"]))

            # 设置占位提示（优先使用 input label）
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

            line_edit.textChanged.connect(
                lambda text, name=input_name: self._on_lineedit_changed(name, text)
            )

            self.control_widget[input_name] = line_edit
            self.main_option_layout.addWidget(line_edit)
        elif "inputs" in self.config:
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
        else:
            # 单个输入框
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
    
    def _init_config(self):
        """初始化配置值"""
        if self.config_type == "combobox" and isinstance(self.control_widget, ComboBox):
            current_label = self.control_widget.currentText()
            self.current_value = self._option_map.get(current_label, current_label)
            # 触发初始子选项显示（跳过动画）
            self._update_children_visibility(self.current_value, skip_animation=True)
        elif self.config_type == "switch" and isinstance(self.control_widget, SwitchButton):
            # switch 类型：checked -> "Yes", unchecked -> "No"
            is_checked = self.control_widget.isChecked()
            self.current_value = "Yes" if is_checked else "No"
            # 触发初始子选项显示（跳过动画）
            self._update_children_visibility(self.current_value, skip_animation=True)
        elif self.config_type == "lineedit":
            if isinstance(self.control_widget, dict):
                # 多输入框类型
                self.current_value = {
                    name: widget.text() for name, widget in self.control_widget.items()
                }
            else:
                # 单个输入框
                if isinstance(self.control_widget, LineEdit):
                    self.current_value = self.control_widget.text()
                else:
                    logger.warning("lineedit 类型的控件未初始化，无法读取默认值")
    
    def _on_combobox_changed(self, label: str):
        """下拉框值改变处理"""
        # 获取实际值（name）
        actual_value = self._option_map.get(label, label)
        self.current_value = actual_value
        
        # 处理子选项显示/隐藏
        self._update_children_visibility(actual_value)
        
        # 发出信号
        self.option_changed.emit(self.key, self.current_value)
    
    def _on_switch_changed(self, checked: bool):
        """开关按钮值改变处理"""
        # switch 类型：checked -> "Yes", unchecked -> "No"
        actual_value = "Yes" if checked else "No"
        self.current_value = actual_value
        
        # 处理子选项显示/隐藏
        self._update_children_visibility(actual_value)
        
        # 发出信号
        self.option_changed.emit(self.key, self.current_value)
    
    def _on_lineedit_changed(self, input_name: Optional[str], text: str):
        """输入框值改变处理"""
        if isinstance(self.control_widget, dict):
            # 多输入框类型
            if input_name:
                self.current_value[input_name] = text
        else:
            # 单个输入框
            self.current_value = text
        
        # 发出信号
        self.option_changed.emit(self.key, self.current_value)
    
    def _update_children_visibility(self, selected_value: Any, skip_animation: bool = False):
        """
        更新子选项的可见性
        
        :param selected_value: 当前选中的值
        :param skip_animation: 是否跳过动画（用于初始化和配置应用）
        """
        children = self.config.get("children", {})
        selected_value_str = str(selected_value) if selected_value is not None else ""
        normalized_selected = selected_value_str.strip()

        matched_key = self._match_child_key(
            children, selected_value, selected_value_str, normalized_selected
        )

        # 判断是否使用动画
        animator = self._children_animator
        use_animation = self._animation_enabled and not skip_animation and animator is not None

        if matched_key:
            if matched_key not in self._child_value_map:
                self.add_child_option(matched_key, children.get(matched_key))

            # 获取需要显示的子选项键列表
            target_child_keys = set(self._child_value_map.get(matched_key, []))
            
            # 隐藏不匹配的子选项，显示匹配的子选项
            visible_any = False
            for child_key, child_widget in self.child_options.items():
                should_show = child_key in target_child_keys
                child_widget.setVisible(should_show)
                if should_show:
                    visible_any = True

            if visible_any:
                if use_animation and animator is not None:
                    # 使用动画展开
                    animator.expand()
                else:
                    # 无动画直接显示
                    self.children_wrapper.setVisible(True)
                    self.children_wrapper.setMaximumHeight(16777215)
            else:
                if use_animation and animator is not None:
                    animator.collapse(on_finished=self._hide_all_children)
                else:
                    self._hide_all_children()
                    self.children_wrapper.setVisible(False)
                    self.children_wrapper.setMaximumHeight(0)
        else:
            if use_animation:
                # 使用动画收起，动画完成后再隐藏子选项内容
                if animator is not None:
                    animator.collapse(on_finished=self._hide_all_children)
            else:
                # 无动画直接隐藏
                self._hide_all_children()
                self.children_wrapper.setVisible(False)
                self.children_wrapper.setMaximumHeight(0)
    
    def _hide_all_children(self):
        """隐藏所有子选项（收起动画完成后调用）"""
        for child_widget in self.child_options.values():
            child_widget.setVisible(False)
    
    def _update_indicator_line_color(self):
        """更新子选项指示线的颜色（根据主题）"""
        if isDarkTheme():
            # 深色主题：使用较亮的颜色
            self.children_indicator_line.setStyleSheet(
                "QFrame { background-color: rgba(255, 255, 255, 0.3); border: none; border-radius: 1px; }"
            )
        else:
            # 浅色主题：使用较深的颜色
            self.children_indicator_line.setStyleSheet(
                "QFrame { background-color: rgba(0, 0, 0, 0.2); border: none; border-radius: 1px; }"
            )

    def _match_child_key(
        self,
        children: Dict[str, Any],
        selected_value: Any,
        selected_value_str: str,
        normalized_selected: str,
    ) -> Optional[str]:
        """匹配哪个子选项集合应该被显示"""
        if selected_value_str in children:
            return selected_value_str
        if selected_value in children:
            return selected_value
        if normalized_selected in children:
            return normalized_selected

        for key in children.keys():
            key_str = str(key)
            key_stripped = key_str.strip()
            if key_str == selected_value_str:
                return key
            if key_stripped == normalized_selected:
                return key
            if str(key) == str(selected_value):
                return key

        return None
    
    def _preload_child_options(self):
        for option_value, child_config in self.config.get("children", {}).items():
            self.add_child_option(option_value, child_config)

    def add_child_option(self, option_value: str, child_config: Any):
        """
        添加子选项组件
        
        :param option_value: 选项值（当下拉框选中此值时显示）
        :param child_config: 子选项配置，支持 dict 或 list
        """
        self._create_child_widgets_for_config(option_value, child_config)

    def _create_child_widgets_for_config(self, option_value: str, child_config: Any):
        if not child_config:
            return

        if isinstance(child_config, dict) and child_config.get("_type") == "multi":
            configs = child_config.get("items", [])
        elif isinstance(child_config, list):
            configs = child_config
        else:
            configs = [child_config]

        for index, config in enumerate(configs):
            self._create_single_child_widget(option_value, config, index)

    def _create_single_child_widget(self, option_value: str, child_config: Dict[str, Any], index: int):
        if not isinstance(child_config, dict):
            return

        child_copy = dict(child_config)
        child_name = child_copy.get("name") or child_copy.get("label") or f"{option_value}_{index}"
        child_copy["name"] = child_name

        if (option_value, child_name) in self._child_name_map:
            return

        child_key = f"{self.key}_child_{option_value}_{child_name}_{index}"
        child_widget = OptionItemWidget(child_key, child_copy, self)
        child_widget.setVisible(False)
        self.child_options[child_key] = child_widget
        self._child_value_map.setdefault(option_value, []).append(child_key)
        self._child_name_map[(option_value, child_name)] = child_key
        self.children_layout.addWidget(child_widget)

    def get_child_widgets_for_value(self, option_value: str) -> List['OptionItemWidget']:
        child_keys = self._child_value_map.get(option_value, [])
        return [self.child_options[key] for key in child_keys if key in self.child_options]

    def find_child_widget(self, option_value: str, child_config: Dict[str, Any]) -> Optional['OptionItemWidget']:
        child_name = child_config.get("name")
        if child_name:
            child_key = self._child_name_map.get((option_value, child_name))
            if child_key:
                return self.child_options.get(child_key)

        child_widgets = self.get_child_widgets_for_value(option_value)
        return child_widgets[0] if child_widgets else None

    def get_option_value_for_child_key(self, child_key: str) -> Optional[str]:
        """根据 child_key 判断该子选项归属哪个 option_value"""
        for option_value, child_keys in self._child_value_map.items():
            if child_key in child_keys:
                return option_value
        return None
    
    def find_child_by_name(self, child_name: str) -> Optional[Tuple[str, 'OptionItemWidget']]:
        """根据 child_name 查找子选项

        兼容新配置格式：config_key 是 child_name 而非 child_key
        
        Args:
            child_name: 子选项的名称（如 "输入A级角色名"）
            
        Returns:
            Optional[Tuple[str, OptionItemWidget]]: (option_value, child_widget) 元组，
            未找到时返回 None
        """
        for (option_value, name), child_key in self._child_name_map.items():
            if name == child_name:
                child_widget = self.child_options.get(child_key)
                if child_widget:
                    return (option_value, child_widget)
        return None
    
    def _unwrap_lineedit_value(self, value: Any) -> Any:
        """如果是字典格式（包含 value 字段），提取真正的输入值。"""
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value

    def set_value(self, value: Any, skip_animation: bool = True):
        """
        设置选项的值
        
        :param value: 要设置的值
        :param skip_animation: 是否跳过动画（默认跳过，用于配置应用）
        """
        if self.config_type == "combobox":
            # 如果传入的是字典，说明可能是配置对象，尝试提取 value
            if isinstance(value, dict):
                # 如果是字典但没有 value 字段，可能是输入框的值，不应该用在这里
                if "value" in value:
                    value = value["value"]
                else:
                    # 如果字典不是配置格式，可能是输入框的值，不应该用于 combobox
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
                        self._update_children_visibility(str(value), skip_animation=skip_animation)
                    finally:
                        combobox.blockSignals(False)
            else:
                logger.warning("combobox 控件未准备好，无法设置值")
        elif self.config_type == "switch":
            # 如果传入的是字典，说明可能是配置对象，尝试提取 value
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
                    self._update_children_visibility(target_value, skip_animation=skip_animation)
                finally:
                    switch_button.blockSignals(False)
            else:
                logger.warning("switch 控件未准备好，无法设置值")
        elif self.config_type == "lineedit":
            lineedit_value = self._unwrap_lineedit_value(value)

            if isinstance(self.control_widget, dict):
                # 多输入框类型（包含 single_input 模式）
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
                elif self._single_input_mode and self.control_widget:
                    # 单输入模式：直接映射到唯一的输入框
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
                logger.warning("lineedit 类型的控件未准备好，无法设置值")
    
    def get_option(self) -> Dict[str, Any]:
        """
        获取当前选项的配置（递归获取子选项）
        
        :return: 选项配置字典，格式如 {"value": ..., "name": ..., "hidden": ..., "children": {...}}
        """
        result: Dict[str, Any] = {
            "value": self.current_value
        }
        
        # 递归获取子选项的配置
        children_config = {}
        active_child_keys = set()
        if self.config_type in ["combobox", "switch"]:
            # 记录当前选中值对应的子选项键，用于后续判断隐藏状态
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
        
        # 对于 combobox 和 switch 类型，需要获取所有已创建的子选项配置
        if self.config_type in ["combobox", "switch"]:
            for child_key, child_widget in self.child_options.items():
                if child_widget:
                    # 获取子选项的配置
                    child_option = child_widget.get_option()
                    
                    # 获取子选项的 name（从结构字典中获取）
                    child_name = ""
                    for option_value, child_keys in self._child_value_map.items():
                        if child_key in child_keys:
                            child_structure = self.config.get("children", {}).get(option_value, {})
                            if isinstance(child_structure, dict):
                                child_name = child_structure.get("name", "")
                            elif isinstance(child_structure, list) and child_structure:
                                first_child = child_structure[0]
                                if isinstance(first_child, dict):
                                    child_name = first_child.get("name", "")
                            break
                    
                    # 使用 child_name 作为配置键，如果没有 name 则回退到 child_key
                    config_key = child_name if child_name else child_key
                    
                    # 检查子选项是否被隐藏（当前选中值不等于此子选项的键值时，该子选项被隐藏）
                    is_active_child = child_key in active_child_keys
                    is_hidden = not (is_active_child and child_widget.isVisible())
                    
                    # 对于 lineedit 类型的子选项，如果只有 value，直接使用值
                    if child_widget.config_type == "lineedit" and "children" not in child_option:
                        # lineedit 类型保持简单值，但如果被隐藏需要转换为字典格式
                        if is_hidden:
                            children_config[config_key] = {
                                "value": child_option.get("value", ""),
                                "hidden": True,
                            }
                        else:
                            children_config[config_key] = child_option.get("value", "")
                    else:
                        # 将子选项的 name 添加到配置中（用于从接口文件中获取具体选项）
                        if child_name:
                            child_option["name"] = child_name
                        # 添加隐藏属性（用于 runner 跳过已隐藏的配置）
                        if is_hidden:
                            child_option["hidden"] = True
                        children_config[config_key] = child_option
        
        # 如果有子选项配置，添加到结果中
        if children_config:
            result["children"] = children_config
        
        return result
    
    def get_simple_option(self) -> Any:
        """
        获取简单的选项值（不包含 children 结构）
        对于有子选项的情况，返回的值会包含子选项的值
        
        :return: 选项值
        """
        if self.config_type in ["combobox", "switch"]:
            # 如果有子选项，需要组装子选项的值
            children = self.config.get("children", {})
            if children and self.current_value in children:
                child_widget = self.child_options.get(self.current_value)
                if child_widget and child_widget.isVisible():
                    child_value = child_widget.get_simple_option()
                    return {
                        "value": self.current_value,
                        "children": {self.current_value: child_value}
                    }
            return self.current_value
        else:
            return self.current_value

