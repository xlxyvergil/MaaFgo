"""图片预览对话框"""
import re
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QScrollArea,
    QWidget,
    QApplication,
)
from qfluentwidgets import PrimaryPushButton


class ImagePreviewDialog(QDialog):
    """图片预览对话框，支持大图展示"""

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self._init_ui()
        self._load_image()

    def _init_ui(self):
        """初始化UI"""
        self.setWindowTitle("图片预览")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        # 设置最小窗口大小
        self.setMinimumSize(400, 300)
        
        # 获取屏幕大小，设置合理的初始窗口大小
        screen = QApplication.primaryScreen()
        if screen:
            screen_size = screen.availableGeometry()
            # 初始大小为屏幕的 70%
            self.resize(
                int(screen_size.width() * 0.7),
                int(screen_size.height() * 0.7)
            )

        # 主布局
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(10)

        # 创建滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setStyleSheet(
            """
            QScrollArea {
                background-color: #1a1a1a;
                border: 1px solid #333;
                border-radius: 8px;
            }
            """
        )

        # 图片容器
        self.image_container = QWidget()
        self.image_container.setStyleSheet("background-color: transparent;")
        self.container_layout = QVBoxLayout(self.image_container)
        self.container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.container_layout.setContentsMargins(20, 20, 20, 20)

        # 图片标签
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet(
            """
            QLabel {
                background-color: transparent;
            }
            """
        )
        self.container_layout.addWidget(self.image_label)

        self.scroll_area.setWidget(self.image_container)
        self.main_layout.addWidget(self.scroll_area, 1)

        # 关闭按钮
        self.close_btn = PrimaryPushButton("关闭")
        self.close_btn.setFixedWidth(120)
        self.close_btn.clicked.connect(self.close)
        
        # 按钮居中
        btn_layout = QVBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_layout.addWidget(self.close_btn)
        self.main_layout.addLayout(btn_layout)

    def _load_image(self):
        """加载并显示图片"""
        image_path = self.image_path
        
        # 处理可能的 file:/// 前缀
        if image_path.startswith("file:///"):
            image_path = image_path[8:]  # 移除 file:///
        elif image_path.startswith("file://"):
            image_path = image_path[7:]  # 移除 file://
        
        # 尝试加载图片（支持本地与 http/https）
        pixmap = None
        if image_path.startswith(("http://", "https://")):
            pixmap = self._load_remote_pixmap(image_path)
        else:
            pixmap = QPixmap(image_path)
        
        if not pixmap or pixmap.isNull():
            # 如果加载失败，显示错误信息
            self.image_label.setText(f"无法加载图片:\n{self.image_path}")
            self.image_label.setStyleSheet(
                """
                QLabel {
                    color: #ff6b6b;
                    font-size: 14px;
                    padding: 20px;
                }
                """
            )
            return
        
        # 获取屏幕大小
        screen = QApplication.primaryScreen()
        if screen:
            screen_size = screen.availableGeometry()
            max_width = int(screen_size.width() * 0.9)
            max_height = int(screen_size.height() * 0.85)
        else:
            max_width = 1600
            max_height = 900

        # 如果图片太大，按比例缩放
        if pixmap.width() > max_width or pixmap.height() > max_height:
            pixmap = pixmap.scaled(
                QSize(max_width, max_height),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        
        self.image_label.setPixmap(pixmap)
        
        # 根据图片大小调整窗口
        # 窗口大小 = 图片大小 + 边距 + 按钮区域
        window_width = min(pixmap.width() + 60, max_width)
        window_height = min(pixmap.height() + 100, max_height)
        
        # 确保不小于最小尺寸
        window_width = max(window_width, 400)
        window_height = max(window_height, 300)
        
        self.resize(window_width, window_height)
        
        # 居中显示
        if screen:
            screen_geo = screen.availableGeometry()
            self.move(
                screen_geo.center().x() - self.width() // 2,
                screen_geo.center().y() - self.height() // 2
            )

    def keyPressEvent(self, event):
        """按 ESC 关闭窗口"""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def _load_remote_pixmap(self, url: str) -> QPixmap | None:
        """下载网络图片并转换为 QPixmap。失败返回 None。"""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MFW-PyQt6"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            image = QImage.fromData(data)
            if image.isNull():
                return None
            return QPixmap.fromImage(image)
        except Exception:
            return None

