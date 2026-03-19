from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QSize, QByteArray
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QToolButton, QDialog
from PySide6.QtWidgets import QApplication

from qfluentwidgets import BodyLabel, ScrollArea, SimpleCardWidget, ToolTipFilter, ToolTipPosition


@dataclass(slots=True)
class LogItemData:
    level: str
    task_name: str
    # 已经格式化过的文本（可为 HTML）
    message: str
    has_rich_content: bool
    timestamp: str
    # 压缩后的完整图片（JPG bytes）；None 表示没有图片
    image_bytes: QByteArray | None = None


class LogItemWidget(SimpleCardWidget):
    """单条日志条目控件：左预览，右组合布局（等级+任务名 / 信息 / 时间）。"""

    def __init__(
        self,
        data: LogItemData,
        *,
        thumb_box: QSize = QSize(54, 54),
        placeholder_icon: QIcon | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setClickEnabled(False)
        self.setBorderRadius(8)
        self._data = data
        self._thumb_box = thumb_box
        self._placeholder_icon = placeholder_icon or QIcon()

        self._preview_button: QToolButton | None = None
        self._level_label = BodyLabel(data.level or "INFO")
        self._task_label = BodyLabel(data.task_name)
        self._message_label = BodyLabel(data.message)
        self._time_label = BodyLabel(data.timestamp)

        self._build_ui()
        self.set_data(data)

    def _build_ui(self) -> None:
        # SimpleCardWidget 内部已有布局，我们需要创建内容容器
        content_widget = QWidget(self)
        root = QHBoxLayout(content_widget)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 左侧：预览图标（可选）
        self._preview_button = QToolButton(self)
        self._preview_button.setAutoRaise(True)
        self._preview_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_button.setFixedSize(self._thumb_box)
        self._preview_button.setIconSize(self._thumb_box)
        self._preview_button.clicked.connect(self._on_preview_clicked)
        # 使用 qfluentwidgets 的 ToolTipFilter 优化 tooltip 显示
        self._preview_button.installEventFilter(
            ToolTipFilter(self._preview_button, 0, ToolTipPosition.TOP)
        )
        root.addWidget(
            self._preview_button,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )

        # 右侧：组合布局
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(3)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # 等级文字：更克制（不使用按钮）
        self._level_label.setStyleSheet("font-size: 11px; font-weight: 700;")
        top_row.addWidget(self._level_label, 0)

        # 任务名
        self._task_label.setStyleSheet("font-weight: 600;")
        top_row.addWidget(self._task_label, 1)

        right.addLayout(top_row)

        # 日志信息
        self._message_label.setWordWrap(True)
        right.addWidget(self._message_label, 0)

        # 时间
        self._time_label.setStyleSheet("font-size: 11px; opacity: 0.75;")
        right.addWidget(self._time_label, 0, Qt.AlignmentFlag.AlignLeft)

        root.addLayout(right, 1)
        
        # 将内容容器添加到 SimpleCardWidget
        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.addWidget(content_widget)

    def set_data(self, data: LogItemData) -> None:
        self._data = data
        self._level_label.setText(data.level or "INFO")
        self._task_label.setText(data.task_name)
        self._message_label.setText(data.message)
        self._message_label.setTextFormat(
            Qt.TextFormat.RichText if data.has_rich_content else Qt.TextFormat.PlainText
        )
        self._time_label.setText(data.timestamp)

        if self._preview_button is None:
            return

        if data.image_bytes and not data.image_bytes.isEmpty():
            from app.utils.logger import logger
            try:
                logger.debug(f"[LogItemWidget] Loading image, bytes size: {data.image_bytes.size()}")
                pixmap = QPixmap()
                # 显式转为 bytes，避免某些 PySide6 环境下 QByteArray 隐式转换失败
                # 如果图片数据已被释放（所有引用该图片的条目都被删除），data() 可能返回无效数据
                raw = data.image_bytes.data()
                if raw and pixmap.loadFromData(raw):
                    logger.debug(f"[LogItemWidget] Image loaded successfully, size: {pixmap.width()}x{pixmap.height()}")
                    # 自动适配 16:9 或 9:16：保持比例缩放到方形盒内
                    scaled = pixmap.scaled(
                        self._thumb_box,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self._preview_button.setIcon(QIcon(scaled))
                    self._preview_button.setIconSize(self._thumb_box)
                    self._preview_button.setVisible(True)
                    self._preview_button.setEnabled(True)
                    self._preview_button.setCursor(Qt.CursorShape.PointingHandCursor)
                    self._preview_button.setToolTip(self.tr("Click to view full image"))
                    return
                else:
                    logger.warning("[LogItemWidget] Failed to load image from bytes (data may be released), showing placeholder")
            except Exception as e:
                logger.warning(f"[LogItemWidget] Exception loading image from bytes: {e}, showing placeholder")

        # 没有图片：显示占位 icon（优先 interface icon，其次应用 icon；都没有则隐藏）
        if not self._placeholder_icon.isNull():
            self._preview_button.setIcon(self._placeholder_icon)
            self._preview_button.setIconSize(self._thumb_box)
            self._preview_button.setVisible(True)
            self._preview_button.setEnabled(False)
            self._preview_button.setCursor(Qt.CursorShape.ArrowCursor)
            self._preview_button.setToolTip(self.tr("No image"))
        else:
            self._preview_button.setIcon(QIcon())
            self._preview_button.setToolTip("")
            self._preview_button.setVisible(False)

    @property
    def level(self) -> str:
        return self._data.level or "INFO"

    def apply_theme(self, *, base_text_color: str, level_color: str) -> None:
        """由外层统一刷新主题色。"""
        # 让等级颜色影响：任务名 / 日志内容 / 等级文字
        # 富文本(HTML)可能自带颜色，这里的 styleSheet 作为兜底色（不会强行覆盖所有 span）
        self._message_label.setStyleSheet(f"color: {level_color};")
        self._task_label.setStyleSheet(f"font-weight: 600; color: {level_color};")
        self._time_label.setStyleSheet("font-size: 11px; opacity: 0.75;")
        # 等级文字：用颜色区分，不用背景色
        self._level_label.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {level_color};"
        )

    def _on_preview_clicked(self) -> None:
        data = self._data
        if not data.image_bytes or data.image_bytes.isEmpty():
            return
        try:
            pixmap = QPixmap()
            # 显式转为 bytes，避免某些 PySide6 环境下 QByteArray 隐式转换失败
            # 如果图片数据已被释放（所有引用该图片的条目都被删除），data() 可能返回无效数据
            raw = data.image_bytes.data()
            if not raw or not pixmap.loadFromData(raw):
                # 图片数据无效或已释放，无法显示预览
                from app.utils.logger import logger
                logger.warning("[LogItemWidget] Cannot preview image: data may be released")
                return
        except Exception as e:
            # 处理可能的异常（例如数据已被释放导致访问无效内存）
            from app.utils.logger import logger
            logger.warning(f"[LogItemWidget] Exception previewing image: {e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Log Image"))
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)

        scroll = ScrollArea(dlg)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        label = BodyLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setPixmap(pixmap)
        scroll.setWidget(label)

        layout.addWidget(scroll, 1)

        # 根据图片尺寸自动调整初始窗口大小（上限：屏幕可用区域的 90%）
        try:
            screen = dlg.screen() or (
                QApplication.primaryScreen() if QApplication.instance() else None
            )
            if screen:
                avail = screen.availableGeometry()
                max_w = int(avail.width() * 0.9)
                max_h = int(avail.height() * 0.9)
                desired_w = min(max_w, max(640, pixmap.width() + 24))
                desired_h = min(max_h, max(420, pixmap.height() + 24))
                dlg.resize(desired_w, desired_h)

                # 大图直接最大化，避免还要手动拉伸
                if pixmap.width() > int(avail.width() * 0.9) or pixmap.height() > int(
                    avail.height() * 0.9
                ):
                    dlg.showMaximized()
                    dlg.exec()
                    return
        except Exception:
            dlg.resize(900, 520)

        dlg.exec()
