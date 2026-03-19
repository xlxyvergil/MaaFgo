import asyncio
import re
from pathlib import Path

import shiboken6

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QFrame,
    QLabel,
)

from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QPalette, QGuiApplication, QPixmap, QColor, QPainter

from qfluentwidgets import (
    CheckBox,
    TransparentToolButton,
    BodyLabel,
    ListWidget,
    FluentIcon as FIF,
    isDarkTheme,
    qconfig,
    RoundMenu,
    Action,
    MessageBoxBase,
    LineEdit,
    SubtitleLabel,
    MessageBox,
    IndeterminateProgressRing,
    ProgressRing,
    IconWidget,
)
from app.core.Item import TaskItem, ConfigItem
from app.common.constants import _RESOURCE_, _CONTROLLER_, POST_ACTION
from app.core.core import ServiceCoordinator


class ClickableLabel(BodyLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class OptionLabel(QLabel):
    """选项标签：不拦截事件，让所有动作作用于父组件（ListItem）"""

    def __init__(self, text: str = "", parent=None):
        # 这里不把 text 交给 QLabel/BodyLabel 的 setText（会影响 sizeHint，导致布局抖动）
        super().__init__("", parent)
        self._marquee_text: str = ""
        self._text_width: int = 0
        self._offset_px: float = 0.0  # 0 -> max_offset
        self._direction: int = 1  # 1: 向左滚(偏移增大), -1: 向右滚(偏移减小)
        self._paused: bool = False
        self._text_color: QColor | None = None

        # 速度与节奏配置
        self._interval_ms: int = 30
        self._pause_ms: int = 1000
        self._speed_px_per_sec: float = 25.0  # 默认更慢一些
        self._step_px: float = self._speed_px_per_sec * (self._interval_ms / 1000.0)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._on_tick)

        self._pause_timer = QTimer(self)
        self._pause_timer.setSingleShot(True)
        self._pause_timer.timeout.connect(self._on_pause_finished)
        self._pause_next_direction: int | None = None

        if text:
            self.setText(text)

    def setStyleSheet(self, styleSheet: str) -> None:  # type: ignore[override]
        # QLabel 的 stylesheet 不一定会改变 palette()，但我们是自绘，需要自己解析 color
        super().setStyleSheet(styleSheet)
        self._text_color = self._parse_color_from_stylesheet(styleSheet)
        self.update()

    @staticmethod
    def _parse_color_from_stylesheet(styleSheet: str) -> QColor | None:
        if not styleSheet:
            return None
        # 避免误匹配 background-color / border-color 等
        m = re.search(
            r"(?<![-\w])color\s*:\s*([^;]+)", styleSheet, flags=re.IGNORECASE
        )
        if not m:
            return None
        color_str = (m.group(1) or "").strip()
        if not color_str:
            return None
        c = QColor(color_str)
        return c if c.isValid() else None

    def setMarqueeConfig(
        self,
        *,
        speed_px_per_sec: float | None = None,
        interval_ms: int | None = None,
        pause_ms: int | None = None,
    ) -> None:
        """配置跑马灯滚动参数。"""
        if interval_ms is not None and interval_ms > 0:
            self._interval_ms = int(interval_ms)
        if pause_ms is not None and pause_ms >= 0:
            self._pause_ms = int(pause_ms)
        if speed_px_per_sec is not None and speed_px_per_sec >= 0:
            self._speed_px_per_sec = float(speed_px_per_sec)
        self._step_px = self._speed_px_per_sec * (self._interval_ms / 1000.0)
        self.refresh_scroll(reset_offset=False)

    def text(self) -> str:  # type: ignore[override]
        return self._marquee_text

    def setText(self, text: str) -> None:  # type: ignore[override]
        # 只更新内部文本，不交给父类，避免 sizeHint 跟随每次更新变化
        self._marquee_text = text or ""
        super().setText("")  # 保持 QLabel 的真实文本为空，稳定布局
        self._offset_px = 0.0
        self._direction = 1
        self._paused = False
        self._pause_next_direction = None
        self._pause_timer.stop()
        self._recalc_metrics()
        self._update_timer_state()
        # 初次/重置后：起点也停顿 1 秒，再开始向后滚动
        if self._needs_scroll():
            self._start_pause(next_direction=1)
        self.update()

    def refresh_scroll(self, reset_offset: bool = True) -> None:
        """外部在 resize/布局变化后调用，用于重新计算是否需要滚动。"""
        if reset_offset:
            self._offset_px = 0.0
            self._direction = 1
            self._paused = False
            self._pause_next_direction = None
            self._pause_timer.stop()
        self._recalc_metrics()
        self._clamp_offset()
        self._update_timer_state()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 宽度变化会影响可滚动范围
        self.refresh_scroll(reset_offset=False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_timer_state()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._tick_timer.stop()
        self._pause_timer.stop()

    def _recalc_metrics(self) -> None:
        fm = self.fontMetrics()
        # horizontalAdvance 对中英文混排更稳定
        self._text_width = fm.horizontalAdvance(self._marquee_text) if self._marquee_text else 0

    def _available_width(self) -> int:
        rect = self.contentsRect()
        return max(0, rect.width())

    def _max_offset(self) -> int:
        return max(0, self._text_width - self._available_width())

    def _needs_scroll(self) -> bool:
        return bool(self._marquee_text) and self._max_offset() > 0 and self._step_px > 0

    def _clamp_offset(self) -> None:
        max_off = float(self._max_offset())
        if self._offset_px < 0:
            self._offset_px = 0.0
        elif self._offset_px > max_off:
            self._offset_px = max_off

    def _update_timer_state(self) -> None:
        if not self.isVisible():
            self._tick_timer.stop()
            return
        if self._needs_scroll():
            if not self._tick_timer.isActive():
                self._tick_timer.start(self._interval_ms)
        else:
            self._tick_timer.stop()
            self._paused = False
            self._pause_timer.stop()
            self._offset_px = 0.0
            self._direction = 1

    def _start_pause(self, next_direction: int) -> None:
        if self._pause_timer.isActive():
            return
        self._paused = True
        self._pause_next_direction = next_direction
        self._pause_timer.start(self._pause_ms)

    def _on_pause_finished(self) -> None:
        if self._pause_next_direction is not None:
            self._direction = self._pause_next_direction
        self._pause_next_direction = None
        self._paused = False

    def _on_tick(self) -> None:
        if not self._needs_scroll():
            self._update_timer_state()
            return
        if self._paused:
            return

        max_off = float(self._max_offset())
        if max_off <= 0:
            self._offset_px = 0.0
            self._direction = 1
            self.update()
            return

        self._offset_px += self._direction * self._step_px

        # 到头后：停 1 秒 -> 反向滚动；到起点：停 1 秒 -> 正向滚动
        if self._offset_px >= max_off:
            self._offset_px = max_off
            self._start_pause(next_direction=-1)
        elif self._offset_px <= 0.0:
            self._offset_px = 0.0
            self._start_pause(next_direction=1)

        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        rect = self.contentsRect()
        painter.setClipRect(rect)

        # 颜色优先取 stylesheet 里的 color，其次用 palette
        painter.setPen(
            self._text_color
            if self._text_color is not None
            else self.palette().color(QPalette.ColorRole.WindowText)
        )

        if not self._marquee_text:
            return

        fm = self.fontMetrics()
        baseline_y = rect.y() + (rect.height() + fm.ascent() - fm.descent()) // 2
        x = rect.x() - int(self._offset_px)
        painter.drawText(x, baseline_y, self._marquee_text)


# 列表项基类
class BaseListItem(QWidget):

    def __init__(self, item: ConfigItem | TaskItem, parent=None):
        super().__init__(parent)
        self.item = item
        # 默认允许的状态（某些子类可能在后续重设）
        self._interface_allowed: bool = True

        self._init_ui()
        self._apply_theme_colors()
        qconfig.themeChanged.connect(self._apply_theme_colors)

    def _resolve_text_color(self) -> str:
        """根据当前主题返回可读的文本颜色"""
        color = self.palette().color(QPalette.ColorRole.WindowText)
        if not isDarkTheme() and color.lightness() > 220:
            return "#202020"
        return color.name()

    def _apply_theme_colors(self, *_):
        """应用主题颜色到名称标签"""
        if hasattr(self, "_interface_allowed") and self._interface_allowed is False:
            return  # 禁用状态保持红色提示
        if hasattr(self, "name_label"):
            self.name_label.setStyleSheet(f"color: {self._resolve_text_color()};")

    def _init_ui(self):
        # 基础UI布局设置
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        # 确保 BaseListItem 的高度不超过 item 高度（44px）
        self.setFixedHeight(44)

        # 创建标签（子类可以重写或扩展）
        self.name_label = self._create_name_label()
        layout.addWidget(self.name_label)

        # 创建设置按钮（子类可以重写或扩展）
        self.setting_button = self._create_setting_button()
        self.setting_button.clicked.connect(self._select_in_parent_list)
        layout.addWidget(self.setting_button)

    def _create_name_label(self):
        # 子类可以重写此方法来自定义标签
        label = ClickableLabel(self.item.name)
        # 调整高度，确保总高度不超过 item 高度（44px）
        label.setFixedHeight(40)  # BaseListItem 没有 option_label，所以可以更高
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return label

    def _get_display_name(self):
        """获取显示名称（优先使用 label，否则使用 name）

        仅在 TaskListItem 中重写，用于从 interface 获取 label
        """
        return self.item.name

    def _create_setting_button(self):
        # 基类默认创建设置按钮，TaskListItem 会重写为删除按钮
        button = TransparentToolButton(FIF.SETTING)
        button.setFixedSize(34, 34)
        return button

    def _select_in_parent_list(self):
        # 在父列表中选择当前项的逻辑
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, ListWidget):
                for i in range(parent.count()):
                    list_item = parent.item(i)
                    widget = parent.itemWidget(list_item)
                    if widget == self:
                        parent.setCurrentItem(list_item)
                        break
                break
            parent = parent.parent()

    def _create_icon_label(
        self, icon_path: str, base_path: Path | None = None
    ) -> BodyLabel:
        """创建图标标签（通用方法，供子类复用）

        Args:
            icon_path: 图标路径（可能是相对路径或绝对路径）
            base_path: 如果 icon_path 是相对路径，相对于此路径。如果为 None，则相对于项目根目录

        Returns:
            BodyLabel 对象，已加载图标
        """
        icon_label = BodyLabel(self)
        icon_label.setFixedSize(24, 24)
        icon_label.setScaledContents(True)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 加载图标
        self._load_icon_to_label(icon_label, icon_path, base_path)

        return icon_label

    def _load_icon_to_label(
        self, icon_label: BodyLabel, icon_path: str, base_path: Path | None = None
    ):
        """将图标加载到标签中（通用方法，供子类复用）

        Args:
            icon_label: 要加载图标的标签
            icon_path: 图标路径（可能是相对路径或绝对路径）
            base_path: 如果 icon_path 是相对路径，相对于此路径。如果为 None，则相对于项目根目录
        """
        icon_file = Path(icon_path)

        # 处理相对路径
        if not icon_file.is_absolute():
            if base_path:
                icon_file = base_path / icon_path.lstrip("./")
            else:
                # 如果是相对路径，假设相对于项目根目录
                project_root = Path.cwd()
                icon_file = project_root / icon_path.lstrip("./")

        # 加载图标
        if icon_file.exists():
            pixmap = QPixmap(str(icon_file))
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(
                        24,
                        24,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )


# 任务列表项组件
class TaskListItem(BaseListItem):
    checkbox_changed = Signal(object)  # 发射 TaskItem 对象

    def __init__(
        self,
        task: TaskItem,
        interface: dict | None = None,
        service_coordinator: ServiceCoordinator | None = None,
        parent=None,
    ):
        self.task = task
        self.interface = interface or {}
        self.service_coordinator = service_coordinator
        super().__init__(task, parent)

        self._apply_interface_constraints()

        # 基础任务（资源、完成后操作）的复选框始终勾选且禁用
        if self.task.is_base_task():
            self.checkbox.setChecked(True)
            self.checkbox.setDisabled(True)

        self.checkbox.stateChanged.connect(self.on_checkbox_changed)

        # 连接选项标签的resize事件，以便在大小改变时重新检查滚动
        if hasattr(self, "option_label"):
            self.option_label.installEventFilter(self)

    def _apply_interface_constraints(self):
        """根据 interface 中的 task 列表决定是否允许此任务勾选/显示为禁用状态。"""
        interface_task_defs = self.interface.get("task")
        self._interface_allowed = True
        if isinstance(interface_task_defs, list) and not self.task.is_base_task():
            allowed_names = [
                task_def.get("name")
                for task_def in interface_task_defs
                if isinstance(task_def, dict) and task_def.get("name")
            ]
            self._interface_allowed = self.task.name in allowed_names
        if not self._interface_allowed:
            self.checkbox.setChecked(False)
            self.checkbox.setDisabled(True)
            self.name_label.setStyleSheet("color: #d32f2f;")
        else:
            # 只有非基础任务才需要解除禁用
            if not self.task.is_base_task():
                self.checkbox.setDisabled(False)
            self._apply_theme_colors()

    def _apply_theme_colors(self, *_):
        """应用主题颜色到名称标签，同时保持选项标签的灰色小字体样式"""
        super()._apply_theme_colors()
        # 选项标签保持灰色小字体样式，不受主题变化影响
        if hasattr(self, "option_label"):
            self.option_label.setStyleSheet("color: gray; font-size: 11px;")
            # 确保字体大小有效
            self._ensure_font_valid(self.option_label)

    @property
    def interface_allows(self) -> bool:
        return self._interface_allowed

    def update_interface(self, interface: dict | None):
        """在接口数据变更时重新评估任务是否被允许显示，并更新图标。"""
        self.interface = interface or {}
        self._apply_interface_constraints()
        # 更新图标
        self._update_icon()

    def _init_ui(self):
        # 创建水平布局
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        # 确保 TaskListItem 的高度不超过 item 高度（44px）
        self.setFixedHeight(44)

        # 复选框 - 任务项特有的UI元素
        self.checkbox = CheckBox()
        self.checkbox.setFixedSize(34, 34)
        self.checkbox.setChecked(self.task.is_checked)
        self.checkbox.setTristate(False)
        layout.addWidget(self.checkbox)

        # 添加图标（如果有）
        icon_path = self._get_task_icon_path()
        if icon_path:
            self.icon_label = self._create_icon_label(icon_path)
            layout.addWidget(self.icon_label)
        else:
            self.icon_label = None

        # 创建垂直布局容器（名称和选项）
        name_option_container = QWidget()
        name_option_layout = QVBoxLayout(name_option_container)
        name_option_layout.setContentsMargins(0, 0, 0, 0)
        name_option_layout.setSpacing(2)

        # 添加标签
        self.name_label = self._create_name_label()
        name_option_layout.addWidget(self.name_label)

        # 添加选项显示标签
        self.option_label = self._create_option_label()
        name_option_layout.addWidget(self.option_label)
        # 在添加到布局后更新显示
        self._update_option_display()

        layout.addWidget(name_option_container, stretch=1)

        # 添加状态标志（在删除按钮之前）
        self.status_widget = self._create_status_widget()
        layout.addWidget(self.status_widget)
        
        # 添加删除按钮（基础任务不能删除，会禁用）
        self.setting_button = self._create_setting_button()
        self.setting_button.clicked.connect(self._on_delete_button_clicked)
        # 基础任务禁用删除按钮
        if self.task.is_base_task():
            self.setting_button.setDisabled(True)
        layout.addWidget(self.setting_button)

        # 连接选项标签的resize事件，以便在大小改变时重新检查滚动
        if hasattr(self, "option_label"):
            self.option_label.installEventFilter(self)

    def eventFilter(self, obj, event):
        """事件过滤器，用于监听选项标签的大小变化"""
        if obj == self.option_label and event.type() == event.Type.Resize:
            # 当选项标签大小改变时，重新计算是否需要滚动（不重置滚动位置）
            label = self.option_label
            QTimer.singleShot(
                50,
                lambda: label.refresh_scroll(reset_offset=False)
                if shiboken6.isValid(label)
                else None,
            )
        return super().eventFilter(obj, event)

    def _get_task_icon_path(self) -> str | None:
        """从 interface.task 中获取当前任务的图标路径

        Returns:
            图标路径字符串，如果不存在则返回 None
        """
        if not self.interface:
            return None

        interface_task_defs = self.interface.get("task", [])

        # 查找与当前任务同名的数据块
        for task_def in interface_task_defs:
            if task_def.get("name") == self.task.name:
                icon_path = task_def.get("icon")
                if icon_path and isinstance(icon_path, str):
                    return icon_path
        return None

    def _create_icon_label(
        self, icon_path: str, base_path: Path | None = None
    ) -> BodyLabel:
        """创建图标标签（调用基类方法）

        Args:
            icon_path: 图标路径（可能是相对路径或绝对路径）
            base_path: 如果 icon_path 是相对路径，相对于此路径。如果为 None，则相对于项目根目录

        Returns:
            BodyLabel 对象，已加载图标
        """
        # 使用基类方法，相对路径相对于项目根目录
        return super()._create_icon_label(icon_path, base_path=base_path)

    def _load_icon_to_label(
        self, icon_label: BodyLabel, icon_path: str, base_path: Path | None = None
    ):
        """将图标加载到标签中（调用基类方法）

        Args:
            icon_label: 要加载图标的标签
            icon_path: 图标路径（可能是相对路径或绝对路径）
            base_path: 如果 icon_path 是相对路径，相对于此路径。如果为 None，则相对于项目根目录
        """
        # 使用基类方法，相对路径相对于项目根目录
        super()._load_icon_to_label(icon_label, icon_path, base_path=base_path)

    def _update_icon(self):
        """更新图标显示"""
        icon_path = self._get_task_icon_path()
        layout = self.layout()

        if layout is None or not isinstance(layout, QHBoxLayout):
            return

        if icon_path:
            # 如果有图标路径
            if self.icon_label is None:
                # 如果还没有图标标签，创建并插入到 checkbox 和 name_label 之间
                self.icon_label = self._create_icon_label(icon_path)
                # 找到 checkbox 和 name_label 的位置
                checkbox_index = layout.indexOf(self.checkbox)
                layout.insertWidget(checkbox_index + 1, self.icon_label)
            else:
                # 如果已有图标标签，更新图标
                self._load_icon_to_label(self.icon_label, icon_path)
        else:
            # 如果没有图标路径，移除图标标签
            if self.icon_label is not None:
                layout.removeWidget(self.icon_label)
                self.icon_label.deleteLater()
                self.icon_label = None

    def _get_display_name(self):
        """获取显示名称（从 interface 获取 label，否则使用 name）

        注意：保留 $ 前缀，它用于国际化标记
        """
        from app.utils.logger import logger

        # 修改为
        if self.task.item_id == _RESOURCE_:
            return self.tr("Resource")
        elif self.task.item_id == _CONTROLLER_:
            return self.tr("Controller")
        elif self.task.item_id == POST_ACTION:
            return self.tr("Post-Action")
        elif self.interface:
            for task in self.interface.get("task", []):
                if task["name"] == self.task.name:
                    display_label = task.get("label", task.get("name", self.task.name))
                    logger.info(f"任务显示: {self.task.name} -> {display_label}")
                    return display_label
        # 如果没有找到对应的 label，返回 name
        logger.warning(
            f"任务未找到 label，使用 name: {self.task.name} (interface={bool(self.interface)})"
        )
        return self.task.name

    def _create_name_label(self):
        """创建名称标签（使用 label 而不是 name）"""
        label = ClickableLabel(self._get_display_name())
        # 调整高度，确保总高度不超过 item 高度（44px）
        # item 高度 44px = name_label + spacing(2px) + option_label
        label.setFixedHeight(30)  # 从 34 调整为 30
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return label

    def _ensure_font_valid(self, label: QWidget):
        """确保标签的字体大小有效，防止出现负数"""
        font = label.font()
        if font.pointSize() <= 0:
            font.setPointSize(11)
            label.setFont(font)
        # 如果使用像素大小，也确保有效
        if font.pixelSize() <= 0 and font.pointSize() <= 0:
            font.setPointSize(11)
            label.setFont(font)

    def _create_option_label(self):
        """创建选项显示标签（支持自动滚动，事件传递给父组件）"""
        label = OptionLabel("")
        # 调整高度，确保总高度不超过 item 高度（44px）
        # item 高度 44px = name_label(30px) + spacing(2px) + option_label(12px)
        label.setFixedHeight(12)  # 从 20 调整为 12
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        label.setWordWrap(False)  # 不换行
        # 设置样式，使文本更小更淡
        label.setStyleSheet("color: gray; font-size: 11px;")
        # 确保字体大小有效，防止出现负数
        self._ensure_font_valid(label)
        # 禁用文本选择，让所有事件（点击、拖动等）直接作用于父组件 ListItem
        label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        # 跑马灯：慢速 + 两端停顿 1s + 往返滚动
        label.setMarqueeConfig(speed_px_per_sec=25.0, interval_ms=30, pause_ms=1000)

        self._option_full_text = ""

        return label

    def _extract_option_values(
        self,
        task_option: dict,
        result: list | None = None,
        interface_options: dict | None = None,
    ) -> list:
        """递归提取任务选项中的当前选择的可见值

        Args:
            task_option: 任务选项字典
            result: 结果列表（递归使用）
            interface_options: interface 中的选项定义，用于获取选项的 label

        Returns:
            提取的值列表（只包含当前选择的选项值）
        """
        if result is None:
            result = []

        if not isinstance(task_option, dict):
            return result

        # 如果没有传入 interface_options，尝试从 self.interface 获取
        if interface_options is None and hasattr(self, "interface"):
            interface_options = self.interface.get("option", {})

        for key, value in task_option.items():
            # 跳过特殊键（如 _speedrun_config）
            if key.startswith("_"):
                continue

            # 如果 value 是字典
            if isinstance(value, dict):
                # 检查是否有 hidden 标志（在 value 同级）
                if value.get("hidden", False):
                    continue

                # 只提取当前选择的选项值（必须有 value 字段）
                option_value = value.get("value")
                if option_value is not None:
                    # 尝试从 interface 获取选项的 label（用于显示）
                    display_value = None
                    if interface_options and key in interface_options:
                        option_def = interface_options[key]
                        # 如果是 combobox 类型，尝试根据 value 找到对应的 label
                        if option_def.get("type", "").lower() == "combobox":
                            options = option_def.get("options", [])
                            for opt in options:
                                if isinstance(opt, dict):
                                    opt_name = opt.get("name", "")
                                    opt_label = opt.get("label", opt_name)
                                    if opt_name == str(
                                        option_value
                                    ) or opt_label == str(option_value):
                                        display_value = opt_label
                                        break
                                elif str(opt) == str(option_value):
                                    display_value = str(opt)
                                    break

                    # 如果没找到 label，使用原始值
                    if display_value is None:
                        display_value = str(option_value)

                    # 如果 value 是字典（如 {"角色名": "破晓"}），提取其中的值
                    if isinstance(option_value, dict):
                        for sub_value in option_value.values():
                            if sub_value and str(sub_value).strip():
                                result.append(str(sub_value).strip())
                    else:
                        # 普通值，使用 display_value（可能是 label）
                        if display_value and display_value.strip():
                            result.append(display_value.strip())

                # 递归处理 children（只处理当前选择的选项的子选项）
                if (
                    "children" in value
                    and isinstance(value["children"], dict)
                    and option_value is not None
                ):
                    # 获取当前选项值的子选项定义（children 中的选项定义）
                    child_interface_options = None
                    if interface_options and key in interface_options:
                        option_def = interface_options[key]
                        children_def = option_def.get("children", {})
                        if (
                            isinstance(children_def, dict)
                            and option_value in children_def
                        ):
                            # children_def[option_value] 可能是一个选项定义列表或字典
                            child_option_structure = children_def[option_value]
                            # 如果是一个列表，提取其中的选项定义
                            if (
                                isinstance(child_option_structure, list)
                                and child_option_structure
                            ):
                                # 从列表中提取选项定义，构建一个字典
                                child_interface_options = {}
                                for child_opt in child_option_structure:
                                    if (
                                        isinstance(child_opt, dict)
                                        and "name" in child_opt
                                    ):
                                        child_interface_options[child_opt["name"]] = (
                                            child_opt
                                        )
                            elif isinstance(child_option_structure, dict):
                                # 如果直接是字典，可能需要进一步处理
                                # 这里假设子选项的结构与主选项类似
                                pass

                    # 递归处理子选项
                    self._extract_option_values(
                        value["children"], result, child_interface_options
                    )
            else:
                # 直接是值的情况（简单格式）- 这种情况表示当前选择的选项
                if value and str(value).strip():
                    result.append(str(value).strip())

        return result

    def _update_option_display(self):
        """更新选项显示"""
        # 尝试从 service_coordinator 获取最新的 task 对象，确保使用最新的 task_option
        if self.service_coordinator:
            try:
                latest_task = self.service_coordinator.task.get_task(self.task.item_id)
                if latest_task:
                    self.task = latest_task
            except Exception:
                # 如果获取失败，继续使用当前的 task 对象
                pass

        # 如果是基础任务，不显示选项
        if self.task.is_base_task():
            self._option_full_text = ""
            self.option_label.setText("")
            self.option_label.setToolTip("")
            return

        # 提取选项值（只显示当前选择的选项）
        interface_options = None
        if self.interface:
            interface_options = self.interface.get("option", {})
        option_values = self._extract_option_values(
            self.task.task_option, interface_options=interface_options
        )

        # 组合显示文本
        if option_values:
            display_text = " · ".join(option_values)
            self._option_full_text = display_text
            self.option_label.setToolTip(display_text)  # 设置工具提示以便查看完整内容
            # 交给 OptionLabel 自己判断是否需要滚动
            self.option_label.setText(display_text)
        else:
            self._option_full_text = ""
            self.option_label.setText("")
            self.option_label.setToolTip("")

    def on_checkbox_changed(self, state):
        # 复选框状态变更处理
        is_checked = state == 2
        self.task.is_checked = is_checked
        # 发射信号通知父组件更新
        self.checkbox_changed.emit(self.task)

    def contextMenuEvent(self, event):
        """右键菜单：单独运行任务、插入任务"""
        if not self.service_coordinator:
            return super().contextMenuEvent(event)

        menu = RoundMenu(parent=self)
        run_action = Action(FIF.PLAY, self.tr("Run this task"))
        run_action.triggered.connect(self._run_single_task)
        if self.task.is_base_task():
            run_action.setEnabled(False)
        menu.addAction(run_action)

        if not self.task.is_base_task():
            run_from_action = Action(
                FIF.RIGHT_ARROW, self.tr("Run from here")
            )
            run_from_action.triggered.connect(self._run_from_task)
            menu.addAction(run_from_action)

        # 插入任务选项（post action 和 controller 不显示）

        if self.task.item_id not in [POST_ACTION, _CONTROLLER_]:
            insert_action = Action(FIF.ADD, self.tr("Insert task"))
            insert_action.triggered.connect(self._insert_task)
            menu.addAction(insert_action)

        menu.popup(event.globalPos())
        event.accept()

    def _run_single_task(self):
        if not self.service_coordinator:
            return
        asyncio.create_task(self.service_coordinator.run_tasks_flow(self.task.item_id))

    def _run_from_task(self):
        if not self.service_coordinator or self.task.is_base_task():
            return
        asyncio.create_task(
            self.service_coordinator.run_manager.run_tasks_flow(
                start_task_id=self.task.item_id
            )
        )

    def _insert_task(self):
        """插入任务：在当前任务下方插入新任务"""
        if not self.service_coordinator:
            return

        # 保存当前任务的 item_id，用于在对话框关闭后重新查找索引
        current_task_id = self.task.item_id

        # 打开添加任务对话框
        from app.view.task_interface.components.AddTaskMessageBox import AddTaskDialog
        from app.common.signal_bus import signalBus

        task_map = getattr(self.service_coordinator.task, "default_option", {})
        interface = getattr(self.service_coordinator.task, "interface", {})

        # 过滤任务映射（根据当前工具栏的过滤模式，这里使用全部任务）
        filtered_task_map = task_map  # 可以根据需要添加过滤逻辑

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
                # 在对话框关闭后重新获取任务列表和索引（因为列表可能在对话框打开期间发生了变化）
                all_tasks = self.service_coordinator.task.get_tasks()
                current_idx = -1
                for i, task in enumerate(all_tasks):
                    if task.item_id == current_task_id:
                        current_idx = i
                        break

                # 计算插入位置：当前任务的下方（idx + 1）
                # 如果找不到当前任务，使用默认位置（-2，倒数第二个）
                if current_idx == -1:
                    insert_idx = -2
                    from app.utils.logger import logger

                    logger.warning(f"未找到任务 {current_task_id}，使用默认插入位置 -2")
                else:
                    insert_idx = current_idx + 1
                    from app.utils.logger import logger

                    logger.info(
                        f"找到任务 {current_task_id} 在索引 {current_idx}，将在索引 {insert_idx} 插入新任务 '{new_task.name}'"
                    )

                # 插入到指定位置
                self.service_coordinator.modify_task(new_task, insert_idx)

    def _create_status_widget(self):
        """创建状态标志组件"""
        widget = QWidget(self)
        widget.setFixedSize(24, 24)
        widget.hide()  # 默认隐藏
        # 创建布局用于放置状态图标或进度条
        status_layout = QHBoxLayout(widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_icon = None
        self._status_progress = None
        self._current_status = ""
        self._status_layout = status_layout
        return widget
    
    def update_status(self, status: str):
        """更新任务状态显示
        
        Args:
            status: 状态字符串，可选值:
                "running", "completed", "failed", "restart_success",
                "waiting", "skipped", ""(清除状态)
        """
        # 基础任务不显示状态标志
        if self.task.is_base_task():
            self.status_widget.hide()
            return
        
        self._current_status = status
        
        # 清除之前的状态组件
        if self._status_icon:
            self._status_layout.removeWidget(self._status_icon)
            self._status_icon.deleteLater()
            self._status_icon = None
        if self._status_progress:
            self._status_layout.removeWidget(self._status_progress)
            # 只对 IndeterminateProgressRing 调用 stop() 方法
            if isinstance(self._status_progress, IndeterminateProgressRing):
                self._status_progress.stop()
            self._status_progress.deleteLater()
            self._status_progress = None
        
        # 根据状态显示不同的图标
        if status == "running":
            # 显示加载动画
            self._status_progress = IndeterminateProgressRing(self.status_widget)
            self._status_progress.setFixedSize(20, 20)
            # 设置进度环宽度为更细
            self._status_progress.setStrokeWidth(2)
            self._status_layout.addWidget(self._status_progress)
            self._status_progress.start()
            self.status_widget.show()
        elif status == "completed":
            # 显示完成图标
            self._status_icon = IconWidget(FIF.ACCEPT, self.status_widget)
            self._status_icon.setFixedSize(20, 20)
            self._status_layout.addWidget(self._status_icon)
            self.status_widget.show()
        elif status == "failed":
            # 显示错误图标
            self._status_icon = IconWidget(FIF.CLOSE, self.status_widget)
            self._status_icon.setFixedSize(20, 20)
            self._status_layout.addWidget(self._status_icon)
            self.status_widget.show()
        elif status == "restart_success":
            # 显示信息图标（重启后成功）
            self._status_icon = IconWidget(FIF.ROTATE, self.status_widget)
            self._status_icon.setFixedSize(20, 20)
            self._status_layout.addWidget(self._status_icon)
            self.status_widget.show()
        elif status == "skipped":
            # 因 speedrun 被跳过：使用与完成相同的图标
            self._status_icon = IconWidget(FIF.ACCEPT, self.status_widget)
            self._status_icon.setFixedSize(20, 20)
            self._status_layout.addWidget(self._status_icon)
            self.status_widget.show()
        elif status == "waiting":
            # 显示等待图标：使用进度环显示 100% 进度，颜色为灰色
            self._status_progress = ProgressRing(self.status_widget)
            self._status_progress.setFixedSize(20, 20)
            # 设置进度环宽度为更细
            self._status_progress.setStrokeWidth(2)
            # 设置进度为 100%
            self._status_progress.setValue(100)
            # 设置颜色为灰色（使用相同的灰色作为前景和背景色）
            gray_color = QColor(128, 128, 128)  # 灰色
            self._status_progress.setCustomBarColor(gray_color, gray_color)
            self._status_layout.addWidget(self._status_progress)
            self.status_widget.show()
        else:
            # 清除状态，隐藏组件
            self.status_widget.hide()
    
    def _create_setting_button(self):
        """重写基类方法，创建删除按钮"""
        button = TransparentToolButton(FIF.DELETE)
        button.setFixedSize(34, 34)
        button.setToolTip(self.tr("Delete task"))
        return button

    def _on_delete_button_clicked(self):
        """处理删除按钮点击事件"""
        if not self.service_coordinator:
            return

        # 基础任务不能删除
        if self.task.is_base_task():
            return

        # 获取任务显示名称
        task_name = self._get_display_name()

        # 弹出确认对话框
        w = MessageBox(
            self.tr("Delete Task"),
            self.tr("Are you sure you want to delete task '{}'?").format(task_name),
            self.window(),
        )

        if w.exec():
            # 用户确认删除
            try:
                success = self.service_coordinator.delete_task(self.task.item_id)
                if not success:
                    from app.utils.logger import logger

                    logger.error(f"删除任务失败: {self.task.item_id}")
            except Exception as e:
                from app.utils.logger import logger

                logger.error(f"删除任务时发生错误: {e}")


# 特殊任务列表项组件
class SpecialTaskListItem(TaskListItem):
    """特殊任务列表项：隐藏checkbox，点击整个item相当于点击checkbox，并切换到任务设置"""

    def __init__(
        self,
        task: TaskItem,
        interface: dict | None = None,
        service_coordinator: ServiceCoordinator | None = None,
        parent=None,
    ):
        # 先调用父类初始化，创建checkbox等UI元素
        super().__init__(task, interface, service_coordinator, parent)

        # 隐藏checkbox
        self.checkbox.hide()

        # 隐藏选项标签（特殊任务不显示选项）
        if hasattr(self, "option_label"):
            self.option_label.hide()

        # 隐藏删除按钮（特殊任务不应该有删除按钮）
        if hasattr(self, "setting_button"):
            self.setting_button.hide()

        # 将整个item的点击事件绑定到checkbox逻辑
        # 点击name_label时触发选择
        self.name_label.clicked.connect(self._on_item_clicked)

        # 设置整个widget可点击
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _on_item_clicked(self):
        """处理item点击事件：相当于点击checkbox，并切换到任务设置"""
        if not self._interface_allowed or self.task.is_base_task():
            return

        # 如果当前未选中，则选中（相当于点击checkbox）
        # 这会触发checkbox状态改变，发射checkbox_changed信号，进而触发单选逻辑
        if not self.task.is_checked:
            # 触发checkbox状态改变，这会发射checkbox_changed信号
            # 单选逻辑会在_on_task_checkbox_changed中处理
            self.checkbox.setChecked(True)

        # 无论是否已选中，都切换到对应的任务设置（触发任务选择）
        if self.service_coordinator:
            self.service_coordinator.select_task(self.task.item_id)

        # 在父列表中选择当前项
        self._select_in_parent_list()

    def mousePressEvent(self, event):
        """重写鼠标点击事件，使整个widget可点击"""
        if event.button() == Qt.MouseButton.LeftButton:
            # 删除按钮已隐藏，直接触发item点击逻辑
            self._on_item_clicked()
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        """重写右键菜单事件：特殊任务不显示右键菜单"""
        # 特殊任务不需要右键菜单，直接忽略事件
        event.ignore()


# 重命名配置对话框
class RenameConfigDialog(MessageBoxBase):
    """重命名配置对话框"""

    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)

        # 设置对话框标题
        self.titleLabel = SubtitleLabel(self.tr("Rename config"), self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addSpacing(10)

        # 创建输入框布局
        name_layout = QVBoxLayout()
        name_label = BodyLabel(self.tr("Enter new config name:"), self)
        self.name_edit = LineEdit(self)
        self.name_edit.setText(current_name)
        self.name_edit.setPlaceholderText(self.tr("Enter the name of the config"))
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.selectAll()  # 选中所有文本以便快速输入

        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_edit)

        # 添加到视图布局
        self.viewLayout.addLayout(name_layout)

        # 设置对话框大小
        self.widget.setMinimumWidth(400)
        self.widget.setMinimumHeight(180)

        # 设置按钮文本
        self.yesButton.setText(self.tr("Confirm"))
        self.cancelButton.setText(self.tr("Cancel"))

        # 连接确认按钮信号
        self.yesButton.clicked.connect(self.on_confirm)

        # 设置焦点到输入框
        self.name_edit.setFocus()

    def on_confirm(self):
        """确认重命名"""
        new_name = self.name_edit.text().strip()
        if not new_name:
            # 如果名称为空，不接受
            return

        self.accept()

    def get_new_name(self) -> str:
        """获取新名称"""
        return self.name_edit.text().strip()


# 配置列表项组件
class ConfigListItem(BaseListItem):
    def __init__(
        self,
        config: ConfigItem,
        service_coordinator: ServiceCoordinator | None = None,
        parent=None,
    ):
        self.service_coordinator = service_coordinator
        self._locked: bool = False
        super().__init__(config, parent)

    def set_locked(self, locked: bool):
        self._locked = bool(locked)
        # 锁定时避免给出“可操作”的指针提示
        if self._locked:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.unsetCursor()

    def _init_ui(self):
        # 创建水平布局
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)

        # 添加图标（如果有）
        icon_path = self._get_bundle_icon_path()
        if icon_path:
            self.icon_label = self._create_icon_label(icon_path)
            layout.addWidget(self.icon_label)
        else:
            self.icon_label = None

        # 添加标签
        self.name_label = self._create_name_label()
        layout.addWidget(self.name_label)

        # 创建设置按钮但不添加到布局（隐藏）
        self.setting_button = self._create_setting_button()
        self.setting_button.clicked.connect(self._select_in_parent_list)
        self.setting_button.hide()  # 隐藏设置按钮

    def _get_bundle_icon_path(self) -> str | None:
        """从配置的 bundle 中获取图标路径

        Returns:
            图标路径字符串，如果不存在则返回 None
        """
        if not self.service_coordinator:
            return None

        bundle_name = getattr(self.item, "bundle", None)
        if not bundle_name or not isinstance(bundle_name, str):
            return None

        try:
            # 获取 bundle 信息
            bundle_info = self.service_coordinator.config.get_bundle(bundle_name)
            if not bundle_info:
                return None

            bundle_path_str = bundle_info.get("path", "")
            if not bundle_path_str:
                return None

            # 解析 bundle 路径
            bundle_path = Path(bundle_path_str)
            if not bundle_path.is_absolute():
                bundle_path = Path.cwd() / bundle_path

            # 查找 interface.json 或 interface.jsonc
            interface_path = bundle_path / "interface.jsonc"
            if not interface_path.exists():
                interface_path = bundle_path / "interface.json"

            if not interface_path.exists():
                return None

            # 使用 preview_interface 获取 interface 数据（不改变当前激活的 interface）
            from app.core.service.interface_manager import get_interface_manager

            interface_manager = get_interface_manager()
            current_language = interface_manager.get_language()
            interface_data = interface_manager.preview_interface(
                interface_path, language=current_language
            )

            if not interface_data:
                return None

            # 获取图标路径
            icon_relative = interface_data.get("icon", "")
            if not icon_relative:
                return None

            # 图标路径相对于 bundle 路径
            icon_path = bundle_path / icon_relative
            if icon_path.exists():
                return str(icon_path)

            return None
        except Exception as e:
            from app.utils.logger import logger

            logger.warning(f"获取 bundle '{bundle_name}' 图标失败: {e}")
            return None

    def _create_icon_label(
        self, icon_path: str, base_path: Path | None = None
    ) -> BodyLabel:
        """创建图标标签（调用基类方法）

        Args:
            icon_path: 图标路径（绝对路径）
            base_path: 如果 icon_path 是相对路径，相对于此路径。如果为 None，则相对于项目根目录

        Returns:
            BodyLabel 对象，已加载图标
        """
        # 使用基类方法，传入 bundle_path 作为 base_path（如果路径是相对的话）
        # 但这里 icon_path 已经是绝对路径，所以 base_path 传 None 即可
        return super()._create_icon_label(icon_path, base_path=base_path)

    def _load_icon_to_label(
        self, icon_label: BodyLabel, icon_path: str, base_path: Path | None = None
    ):
        """将图标加载到标签中（调用基类方法）

        Args:
            icon_label: 要加载图标的标签
            icon_path: 图标路径（绝对路径）
            base_path: 如果 icon_path 是相对路径，相对于此路径。如果为 None，则相对于项目根目录
        """
        # 使用基类方法，icon_path 已经是绝对路径
        super()._load_icon_to_label(icon_label, icon_path, base_path=base_path)

    def contextMenuEvent(self, event):
        """右键菜单：复制配置 ID、更改配置名"""
        menu = RoundMenu(parent=self)

        # 添加更改配置名选项
        rename_action = Action(FIF.EDIT, self.tr("Rename config"))
        rename_action.triggered.connect(self._rename_config)
        menu.addAction(rename_action)

        # 添加复制配置 ID 选项
        copy_action = Action(FIF.COPY, self.tr("Copy config ID"))
        copy_action.triggered.connect(self._copy_config_id)
        menu.addAction(copy_action)

        menu.popup(event.globalPos())
        event.accept()

    def _rename_config(self):
        """更改配置名称"""
        if not self.service_coordinator:
            return

        # 确保 item 是 ConfigItem 类型
        if not isinstance(self.item, ConfigItem):
            return

        # 获取当前配置名称
        current_name = self.item.name
        if not current_name:
            current_name = ""

        # 创建输入对话框，使用顶层窗口作为父组件
        dialog = RenameConfigDialog(current_name, self.window())
        if dialog.exec():
            new_name = dialog.get_new_name()
            if new_name and new_name.strip() and new_name != current_name:
                new_name = new_name.strip()

                # 更新配置项的 name
                self.item.name = new_name

                # 保存配置
                try:
                    success = self.service_coordinator.config.save_config(
                        self.item.item_id, self.item
                    )
                    if success:
                        # 更新显示的标签文本
                        self.name_label.setText(new_name)

                        # 发送配置已保存的信号，触发刷新
                        self.service_coordinator.signal_bus.config_saved.emit(True)
                    else:
                        from app.utils.logger import logger

                        logger.error(f"保存配置失败: {self.item.item_id}")
                except Exception as e:
                    from app.utils.logger import logger

                    logger.error(f"保存配置时发生错误: {e}")

    def _copy_config_id(self):
        config_id = getattr(self.item, "item_id", "") or ""
        if not config_id:
            return
        QGuiApplication.clipboard().setText(str(config_id))
