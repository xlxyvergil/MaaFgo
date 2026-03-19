import hashlib
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout
from qfluentwidgets import BodyLabel, ScrollArea, SimpleCardWidget

from app.utils.logger import logger
from app.utils.markdown_helper import render_markdown
from app.view.task_interface.components.ImagePreviewDialog import ImagePreviewDialog


class DescriptionWidget(QWidget):
    """独立的说明组件"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
    
    def _init_ui(self):
        """初始化UI"""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(5)
        
        # 创建描述标题
        self.description_title = BodyLabel(self.tr("Function Description"))
        self.description_title.setStyleSheet("font-size: 20px;")
        self.description_title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        # 创建描述卡片
        self.description_area_card = SimpleCardWidget()
        self.description_area_card.setClickEnabled(False)
        self.description_area_card.setBorderRadius(8)
        
        # 创建描述内容区域
        self.description_area_widget = QWidget()
        self.description_layout = QVBoxLayout(self.description_area_widget)
        self.description_layout.setContentsMargins(10, 10, 10, 10)
        
        # 描述内容
        self.description_content = BodyLabel()
        self.description_content.setWordWrap(True)
        self.description_content.setTextFormat(Qt.TextFormat.RichText)
        self.description_content.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.description_content.setOpenExternalLinks(False)
        self.description_content.linkActivated.connect(self._on_link_activated)
        self.description_content.setContextMenuPolicy(
            Qt.ContextMenuPolicy.NoContextMenu
        )
        self.description_content.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.description_layout.addWidget(self.description_content)
        self.description_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        # 创建滚动区域
        self.description_scroll_area = ScrollArea()
        self.description_scroll_area.setWidget(self.description_area_widget)
        self.description_scroll_area.setWidgetResizable(True)
        self.description_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.description_scroll_area.enableTransparentBackground()
        self.description_scroll_area.setStyleSheet(
            "background-color: transparent; border: none;"
        )
        
        # 将滚动区域添加到卡片
        card_layout = QVBoxLayout(self.description_area_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.addWidget(self.description_scroll_area)
        
        # 添加到主布局
        self.main_layout.addWidget(self.description_title)
        self.main_layout.addWidget(self.description_area_card)
        self.main_layout.setStretch(0, 1)  # 标题占用1单位
        self.main_layout.setStretch(1, 99)  # 内容占用99单位
    
    def set_description(self, description: str):
        """设置说明内容
        
        Args:
            description: 说明内容（Markdown格式）
        """
        if not description or not description.strip():
            self.description_content.setText("")
            return
        
        html = render_markdown(description)
        html = self._process_remote_images(html)
        self.description_content.setText(html)
    
    def _process_remote_images(self, html: str) -> str:
        """下载公告中的网络图片到本地缓存，并替换为本地路径以保证可显示/预览。"""
        if not html:
            return html
        
        urls = set(re.findall(r"https?://[^\s\"'>]+", html))
        if not urls:
            return html
        
        for url in urls:
            local_path = self._cache_remote_image(url)
            if not local_path:
                continue
            local_uri = Path(local_path).as_uri()
            html = html.replace(url, local_uri)
        return html

    def _cache_remote_image(self, url: str) -> str | None:
        """缓存网络图片到临时目录，返回本地路径。失败则返回None。"""
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return None
            
            cache_dir = Path(tempfile.gettempdir()) / "mfw_remote_images"
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            ext = Path(parsed.path).suffix
            # 简单兜底，避免过长或缺少后缀
            if not ext or len(ext) > 5:
                ext = ".img"
            
            filename = hashlib.sha1(url.encode("utf-8")).hexdigest() + ext
            file_path = cache_dir / filename
            if file_path.exists():
                return str(file_path)
            
            req = urllib.request.Request(url, headers={"User-Agent": "MFW-PyQt6"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            file_path.write_bytes(data)
            return str(file_path)
        except Exception as e:
            logger.warning(f"下载网络图片失败: {url}, {e}")
            return None
    
    def clear_description(self):
        """清除说明内容"""
        self.description_content.setText("")
    
    def _on_link_activated(self, link: str):
        """处理链接点击事件（用于图片预览）"""
        if link.startswith("image:"):
            # 提取图片路径
            image_path = link[6:]  # 移除 "image:" 前缀
            # 打开图片预览对话框
            dialog = ImagePreviewDialog(image_path, self)
            dialog.exec()

