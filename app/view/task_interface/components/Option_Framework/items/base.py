"""
选项项基类
提供所有选项项的共用功能和接口
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, ComboBox, ToolTipFilter, isDarkTheme, qconfig
from qfluentwidgets.components.widgets.menu import RoundMenu

from app.utils.logger import logger
from app.view.task_interface.components.Option_Framework.animations import HeightAnimator

if TYPE_CHECKING:
    from PySide6.QtWidgets import QLayout


class _DescriptionIndicatorDelegate(QStyledItemDelegate):
    """带描述文本的下拉框菜单项委托

    在选项文本下方以较小、较淡的字体绘制描述文本。
    """

    # 常量
    DESCRIPTION_FONT_SIZE = 11
    NORMAL_ITEM_HEIGHT = 33
    DESCRIPTION_EXTRA_HEIGHT = 18
    INDICATOR_WIDTH = 3
    INDICATOR_X = 6
    INDICATOR_RADIUS = 1.5

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._descriptions: List[Optional[str]] = []

    def set_descriptions(self, descriptions: List[Optional[str]]):
        """设置每个选项对应的描述列表"""
        self._descriptions = list(descriptions)

    def _get_description(self, index) -> Optional[str]:
        row = index.row()
        if 0 <= row < len(self._descriptions):
            return self._descriptions[row]
        return None

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        desc = self._get_description(index)
        h = self.NORMAL_ITEM_HEIGHT
        if desc:
            h += self.DESCRIPTION_EXTRA_HEIGHT
        size.setHeight(h)
        return size

    def paint(self, painter: "QPainter", option: "QStyleOptionViewItem", index):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPen
        from qfluentwidgets import themeColor

        # 检查分隔符
        if index.model().data(index, Qt.DecorationRole) == "seperator":
            painter.save()
            c = 0 if not isDarkTheme() else 255
            pen = QPen(QColor(c, c, c, 25), 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            rect = option.rect
            painter.drawLine(0, rect.y() + 4, rect.width() + 12, rect.y() + 4)
            painter.restore()
            return

        desc = self._get_description(index)

        if not desc:
            # 无描述时使用默认绘制
            super().paint(painter, option, index)
        else:
            # 有描述时自定义绘制：先画默认背景/高亮，然后画文本和描述
            # 手动绘制背景（高亮态）
            from PySide6.QtWidgets import QStyle

            style = option.widget.style() if option.widget else QApplication.style()
            # 绘制背景
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)

            painter.save()
            painter.setRenderHints(
                painter.RenderHint.Antialiasing
                | painter.RenderHint.TextAntialiasing
                | painter.RenderHint.SmoothPixmapTransform
            )

            if not (option.state & QStyle.StateFlag.State_Enabled):
                painter.setOpacity(0.5 if isDarkTheme() else 0.6)

            rect = option.rect

            # 计算文本区域偏移（考虑图标）
            icon = index.data(Qt.DecorationRole)
            text_x = 12
            if icon and not icon.isNull():
                text_x = 44  # 为图标留出空间

            # 绘制主文本（上半部分）
            main_font = painter.font()
            text = index.data(Qt.DisplayRole) or ""
            text_color = QColor(255, 255, 255) if isDarkTheme() else QColor(0, 0, 0)
            painter.setPen(text_color)
            painter.setFont(main_font)
            main_text_rect = QRectF(
                text_x,
                rect.y() + 2,
                rect.width() - text_x - 12,
                self.NORMAL_ITEM_HEIGHT - 4,
            )
            painter.drawText(
                main_text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

            # 绘制描述文本（下半部分）
            desc_font = painter.font()
            desc_font.setPointSizeF(self.DESCRIPTION_FONT_SIZE * 0.75)
            painter.setFont(desc_font)
            desc_color = QColor(255, 255, 255, 140) if isDarkTheme() else QColor(0, 0, 0, 120)
            painter.setPen(desc_color)
            desc_rect = QRectF(
                text_x,
                rect.y() + self.NORMAL_ITEM_HEIGHT - 6,
                rect.width() - text_x - 12,
                self.DESCRIPTION_EXTRA_HEIGHT,
            )
            painter.drawText(
                desc_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                desc,
            )

            painter.restore()

        # 绘制选中指示条
        from PySide6.QtWidgets import QStyle

        if option.state & QStyle.StateFlag.State_Selected:
            painter.save()
            painter.setRenderHints(
                painter.RenderHint.Antialiasing
                | painter.RenderHint.SmoothPixmapTransform
                | painter.RenderHint.TextAntialiasing
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(themeColor())
            indicator_y = option.rect.y() + (option.rect.height() - 15) // 2
            painter.drawRoundedRect(
                self.INDICATOR_X,
                indicator_y,
                self.INDICATOR_WIDTH,
                15,
                self.INDICATOR_RADIUS,
                self.INDICATOR_RADIUS,
            )
            painter.restore()


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
        from qfluentwidgets.components.widgets.menu import (
            MenuAnimationType,
        )

        menu = _DescriptionComboBoxMenu(self._item_descriptions, parent=self)
        return menu


class _DescriptionComboBoxMenu(RoundMenu):
    """支持描述文本的下拉框菜单"""

    NORMAL_ITEM_HEIGHT = 33
    DESCRIPTION_EXTRA_HEIGHT = 18

    def __init__(self, descriptions: List[Optional[str]], parent=None):
        super().__init__(title="", parent=parent)
        self._descriptions = list(descriptions)

        self.view.setViewportMargins(0, 2, 0, 6)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # 使用自定义委托
        self._desc_delegate = _DescriptionIndicatorDelegate(self.view)
        self._desc_delegate.set_descriptions(self._descriptions)
        self.view.setItemDelegate(self._desc_delegate)
        self.view.setObjectName("comboListWidget")

        self.setItemHeight(self.NORMAL_ITEM_HEIGHT)

    def _adjustItemText(self, item, action):
        """重写以支持不同高度的菜单项"""
        w = super()._adjustItemText(item, action)

        # 根据对应选项是否有描述来设置不同高度
        row = self.view.count() - 1  # 当前刚添加的项
        desc = None
        if 0 <= row < len(self._descriptions):
            desc = self._descriptions[row]

        h = self.NORMAL_ITEM_HEIGHT
        if desc:
            h += self.DESCRIPTION_EXTRA_HEIGHT

        from PySide6.QtCore import QSize

        item.setSizeHint(QSize(item.sizeHint().width(), h))
        return w

    def exec(self, pos, ani=True, aniType=None):
        from qfluentwidgets.components.widgets.menu import MenuAnimationType

        if aniType is None:
            aniType = MenuAnimationType.DROP_DOWN
        self.view.adjustSize(pos, aniType)
        self.adjustSize()
        return super().exec(pos, ani, aniType)


class OptionItemBase(QWidget):
    """
    选项项基类
    提供所有选项项的共用功能，子类需要实现特定类型的控件创建和值处理
    """

    # 信号：选项值改变时发出
    option_changed = Signal(str, object)  # key, value

    # 图标大小常量
    ICON_SIZE = 18

    def __init__(
        self, key: str, config: Dict[str, Any], parent: Optional[QWidget] = None
    ):
        """
        初始化选项项基类

        :param key: 选项的键名
        :param config: 选项配置字典，包含 label, type, description, options/inputs 等
        :param parent: 父组件
        """
        super().__init__(parent)
        self.key = key
        self.config = config
        self.config_type = config.get("type", "combobox")
        self.child_options: Dict[str, "OptionItemBase"] = {}  # 子选项组件字典
        self._child_value_map: Dict[str, List[str]] = {}
        self._child_name_map: Dict[Tuple[str, str], str] = {}
        self.current_value: Any = None  # 当前选中的值
        self.control_widget: Any = None  # 子类设置具体控件类型

        # 动画控制标志：初始化和配置应用时跳过动画
        self._animation_enabled = False
        self._children_animator: Optional[HeightAnimator] = None

        # 选项映射（用于 label <-> name 转换）
        self._option_map: Dict[str, str] = {}  # label -> name
        self._reverse_option_map: Dict[str, str] = {}  # name -> label

        self._init_base_ui()

    def _init_base_ui(self):
        """初始化基础 UI 结构"""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(5, 5, 5, 5)
        self.main_layout.setSpacing(5)

        # 创建主选项容器（包含标签和控件），确保不因子选项变化而抖动
        self.main_option_container = QWidget()
        self.main_option_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.main_option_layout = QVBoxLayout(self.main_option_container)
        self.main_option_layout.setContentsMargins(0, 0, 0, 0)
        self.main_option_layout.setSpacing(5)
        self.main_layout.addWidget(self.main_option_container)

        # 创建子选项容器（用于存放子选项）
        self.children_container = QWidget()
        self.children_layout = QVBoxLayout(self.children_container)
        self.children_layout.setContentsMargins(0, 0, 0, 0)

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
        self.children_wrapper_layout.addWidget(self.children_container, 1)

        # 将子选项包装容器添加到主布局
        self.main_layout.addWidget(self.children_wrapper)

        # 初始状态隐藏子选项包装容器
        self.children_wrapper.setVisible(False)
        self.children_wrapper.setMaximumHeight(0)

        # 创建子选项包装容器的动画控制器
        self._children_animator = HeightAnimator(
            self.children_wrapper, duration=200, parent=self
        )

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

    def _add_icon_to_layout(
        self, layout: QHBoxLayout, icon_source: Any
    ) -> Optional[QLabel]:
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
        border_color = (
            "rgba(255, 255, 255, 0.25)" if isDarkTheme() else "rgba(0, 0, 0, 0.25)"
        )
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
        filter_obj = ToolTipFilter(indicator)
        indicator.installEventFilter(filter_obj)
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

    def _update_indicator_line_color(self):
        """更新子选项指示线的颜色（根据主题）"""
        if isDarkTheme():
            self.children_indicator_line.setStyleSheet(
                "QFrame { background-color: rgba(255, 255, 255, 0.3); border: none; border-radius: 1px; }"
            )
        else:
            self.children_indicator_line.setStyleSheet(
                "QFrame { background-color: rgba(0, 0, 0, 0.2); border: none; border-radius: 1px; }"
            )

    def _update_children_visibility(
        self, selected_value: Any, skip_animation: bool = False
    ):
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
        use_animation = (
            self._animation_enabled and not skip_animation and animator is not None
        )

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
        else:
            if use_animation:
                if animator is not None:
                    animator.collapse(on_finished=self._hide_all_children)
            else:
                self._hide_all_children()
                self.children_wrapper.setVisible(False)
                self.children_wrapper.setMaximumHeight(0)

    def _hide_all_children(self):
        """隐藏所有子选项（收起动画完成后调用）"""
        for child_widget in self.child_options.values():
            child_widget.setVisible(False)

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
        """预加载所有子选项"""
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
        """为配置创建子选项控件"""
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

    def _create_single_child_widget(
        self, option_value: str, child_config: Dict[str, Any], index: int
    ):
        """创建单个子选项控件"""
        if not isinstance(child_config, dict):
            return

        child_copy = dict(child_config)
        child_name = (
            child_copy.get("name")
            or child_copy.get("label")
            or f"{option_value}_{index}"
        )
        child_copy["name"] = child_name

        if (option_value, child_name) in self._child_name_map:
            return

        child_key = f"{self.key}_child_{option_value}_{child_name}_{index}"

        # 使用注册器创建子选项
        from .registry import OptionItemRegistry

        child_widget = OptionItemRegistry.create(child_key, child_copy, self)
        child_widget.setVisible(False)
        self.child_options[child_key] = child_widget
        self._child_value_map.setdefault(option_value, []).append(child_key)
        self._child_name_map[(option_value, child_name)] = child_key
        self.children_layout.addWidget(child_widget)

    def get_child_widgets_for_value(
        self, option_value: str
    ) -> List["OptionItemBase"]:
        """获取指定值对应的子选项控件列表"""
        child_keys = self._child_value_map.get(option_value, [])
        return [
            self.child_options[key] for key in child_keys if key in self.child_options
        ]

    def find_child_widget(
        self, option_value: str, child_config: Dict[str, Any]
    ) -> Optional["OptionItemBase"]:
        """查找子选项控件"""
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

    def find_child_by_name(self, child_name: str) -> Optional[Tuple[str, "OptionItemBase"]]:
        """
        通过 child_name 查找子选项

        :param child_name: 子选项的 name（interface.json 中的 option 名称）
        :return: (option_value, child_widget) 元组，如果未找到返回 None
        """
        for (option_value, name), child_key in self._child_name_map.items():
            if name == child_name:
                child_widget = self.child_options.get(child_key)
                if child_widget:
                    return (option_value, child_widget)
        return None

    # ===== 子类必须实现的方法 =====

    def init_ui(self):
        """初始化具体控件 UI（子类实现）"""
        raise NotImplementedError("子类必须实现 init_ui 方法")

    def init_config(self):
        """初始化配置值（子类实现）"""
        raise NotImplementedError("子类必须实现 init_config 方法")

    def set_value(self, value: Any, skip_animation: bool = True):
        """
        设置选项的值

        :param value: 要设置的值
        :param skip_animation: 是否跳过动画（默认跳过，用于配置应用）
        """
        raise NotImplementedError("子类必须实现 set_value 方法")

    def get_option(self) -> Dict[str, Any]:
        """
        获取当前选项的配置（递归获取子选项）

        :return: 选项配置字典
        """
        raise NotImplementedError("子类必须实现 get_option 方法")

    def get_simple_option(self) -> Any:
        """
        获取简单的选项值（不包含 children 结构）

        :return: 选项值
        """
        return self.current_value


__all__ = ["OptionItemBase", "TooltipComboBox"]
