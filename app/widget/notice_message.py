#   This file is part of MFW-ChainFlow Assistant.

#   MFW-ChainFlow Assistant is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published
#   by the Free Software Foundation, either version 3 of the License,
#   or (at your option) any later version.

#   MFW-ChainFlow Assistant is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
#   the GNU General Public License for more details.

#   You should have received a copy of the GNU General Public License
#   along with MFW-ChainFlow Assistant. If not, see <https://www.gnu.org/licenses/>.

#   Contact: err.overflow@gmail.com
#   Copyright (C) 2024-2025  MFW-ChainFlow Assistant. All rights reserved.

# This file incorporates work covered by the following copyright and
# permission notice:
#
#     AUTO_MAA Copyright (C) 2024-2025 DLmaster361
#     https://github.com/DLmaster361/AUTO_MAA


"""
MFW-ChainFlow Assistant
MFW-ChainFlow Assistant 公告面板
原作者:DLmaster361
地址:https://github.com/DLmaster361/AUTO_MAA
修改:overflow65537
"""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
)
from PySide6.QtCore import Qt, QTimer
from qfluentwidgets import (
    MessageBoxBase,
    Signal,
    CardWidget,
    BodyLabel,
    PrimaryPushButton,
    HeaderCardWidget,
    ScrollArea,
)
import re
from functools import partial
from pathlib import Path
from typing import List, Dict

from app.utils.markdown_helper import render_markdown, remote_image_cache

# 以下代码引用自 AUTO_MAA 项目的 ./app/ui/Widget.py 文件，用于创建公告对话框
ANNOUNCEMENT_BASE_DIR = Path.cwd() / "resource" / "announcement"


class NoticeMessageBox(MessageBoxBase):
    """公告对话框"""

    def __init__(self, parent, title: str, content: Dict[str, str]):
        super().__init__(parent)

        self.index = self.NoticeIndexCard(title, content, self)

        # 原 BodyLabel 初始化（保持不变）
        self.text = BodyLabel(self)
        self.text.setOpenExternalLinks(True)
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 新增：创建滚动区域并包裹 BodyLabel
        self.scroll_area = ScrollArea(self)
        #设置透明
        self.scroll_area.enableTransparentBackground()
        self.scroll_area.setWidgetResizable(True)  # 允许内容自适应大小
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )  # 隐藏水平滚动条
        self.scroll_area.setWidget(self.text)  # 将 BodyLabel 放入滚动区域

        self.button_yes = PrimaryPushButton(self.tr("Confirm and never show again"), self)
        self.button_cancel = PrimaryPushButton(self.tr("Confirm"), self)

        self.buttonGroup.hide()

        self.v_layout = QVBoxLayout()
        # 修改：将滚动区域添加到布局（原直接添加 text）
        self.v_layout.addWidget(self.scroll_area)
        self.button_layout = QHBoxLayout()
        self.button_layout.addWidget(self.button_yes)
        self.button_layout.addWidget(self.button_cancel)
        self.v_layout.addLayout(self.button_layout)

        self.h_layout = QHBoxLayout()
        self.h_layout.addWidget(self.index)
        self.h_layout.addLayout(self.v_layout)
        self.h_layout.setStretch(0, 1)
        self.h_layout.setStretch(1, 3)

        # 将组件添加到布局中
        self.viewLayout.addLayout(self.h_layout)
        self.widget.setFixedSize(1000, 640)

        self.index.index_changed.connect(self.__update_text)
        self.button_yes.clicked.connect(self.yesButton.click)
        self.button_cancel.clicked.connect(self.cancelButton.click)
        self.index.index_cards[0].clicked.emit()
        remote_image_cache.image_cached.connect(self._on_remote_image_cached)
        self._last_rendered_text = ""

    def __update_text(self, text: str):
        self._last_rendered_text = text

        html = render_markdown(text, base_path=ANNOUNCEMENT_BASE_DIR)
        html = re.sub(
            r"<code>(.*?)</code>",
            r"<span style='color: #009faa;'>\1</span>",
            html,
        )
        html = re.sub(
            r'(<a\s+[^>]*href="[^"]+"[^>]*)>', r'\1 style="color: #009faa;">', html
        )
        html = re.sub(r"<li><p>(.*?)</p></li>", r"<p><strong>◆ </strong>\1</p>", html)
        html = re.sub(r"<ul>(.*?)</ul>", r"\1", html)

        self.text.setText(f"<body>{html}</body>")

    def _on_remote_image_cached(self, _url: str):
        if self._last_rendered_text:
            self.__update_text(self._last_rendered_text)

    class NoticeIndexCard(HeaderCardWidget):

        index_changed = Signal(str)

        def __init__(self, title: str, content: Dict[str, str], parent=None): # type: ignore
            super().__init__(parent)
            self.setTitle(title)

            self.Layout = QVBoxLayout()
            self.viewLayout.addLayout(self.Layout)
            self.viewLayout.setContentsMargins(3, 0, 3, 3)

            self.index_cards: List[QuantifiedItemCard] = []

            for index, text in content.items():

                self.index_cards.append(QuantifiedItemCard([index, ""]))
                self.index_cards[-1].clicked.connect(
                    partial(self.index_changed.emit, text)
                )
                self.Layout.addWidget(self.index_cards[-1])

            if not content:
                self.Layout.addWidget(QuantifiedItemCard(["暂无信息", ""]))

            self.Layout.addStretch(1)


class QuantifiedItemCard(CardWidget):

    def __init__(self, item: list, parent=None):
        super().__init__(parent)

        self.Layout = QHBoxLayout(self)

        self.Name = BodyLabel(item[0], self)
        self.Numb = BodyLabel(str(item[1]), self)

        self.Layout.addWidget(self.Name)
        self.Layout.addStretch(1)
        self.Layout.addWidget(self.Numb)


class DelayedCloseNoticeMessageBox(NoticeMessageBox):
    """带延迟关闭功能的公告对话框
    
    在打开后5秒内无法关闭，适用于第一次打开或公告更新时自动弹出的场景。
    """

    def __init__(self, parent, title: str, content: Dict[str, str], enable_delay: bool = True):
        super().__init__(parent, title, content)
        self._enable_delay = enable_delay
        self._can_close = not enable_delay  # 如果启用延迟，初始时不能关闭
        self._remaining_seconds = 5  # 剩余秒数
        self._delay_timer = QTimer(self)
        self._delay_timer.setSingleShot(True)
        self._delay_timer.timeout.connect(self._enable_close)
        
        # 倒计时更新定时器（每秒更新一次）
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._update_countdown)
        
        # 创建倒计时标签，放在关闭按钮旁边
        self._countdown_label = BodyLabel("", self)
        self._countdown_label.setStyleSheet("color: #666; font-size: 12px;")
        
        # 如果启用延迟，初始时禁用关闭按钮并显示倒计时
        if self._enable_delay:
            self.button_cancel.setEnabled(False)
            self._update_countdown_text()
            # 将倒计时标签添加到按钮布局中（在关闭按钮之后）
            self.button_layout.addWidget(self._countdown_label)
        else:
            self._countdown_label.hide()

    def _update_countdown_text(self):
        """更新倒计时文本"""
        if self._enable_delay and self._remaining_seconds > 0:
            self._countdown_label.setText(f"({self._remaining_seconds}s)")
        else:
            self._countdown_label.setText("")

    def _update_countdown(self):
        """更新倒计时"""
        if self._remaining_seconds > 0:
            self._remaining_seconds -= 1
            self._update_countdown_text()
        else:
            self._countdown_timer.stop()

    def _enable_close(self):
        """启用关闭功能"""
        self._can_close = True
        self.button_cancel.setEnabled(True)
        self._countdown_timer.stop()
        self._countdown_label.setText("")

    def exec(self):
        """显示对话框并执行，如果启用延迟则在5秒后允许关闭"""
        if self._enable_delay:
            # 重置倒计时
            self._remaining_seconds = 5
            self._update_countdown_text()
            # 启动倒计时更新定时器（每秒更新一次）
            self._countdown_timer.start(1000)
            # 启动5秒定时器
            self._delay_timer.start(5000)  # 5000毫秒 = 5秒
        return super().exec()

    def show(self):
        """显示对话框，如果启用延迟则在5秒后允许关闭"""
        if self._enable_delay:
            # 重置倒计时
            self._remaining_seconds = 5
            self._update_countdown_text()
            # 启动倒计时更新定时器（每秒更新一次）
            self._countdown_timer.start(1000)
            # 启动5秒定时器
            self._delay_timer.start(5000)  # 5000毫秒 = 5秒
        return super().show()

    def closeEvent(self, event):
        """拦截关闭事件，如果延迟未结束则阻止关闭"""
        if not self._can_close:
            event.ignore()
            return
        # 停止定时器
        self._countdown_timer.stop()
        super().closeEvent(event)

    def reject(self):
        """拦截取消/关闭操作，如果延迟未结束则阻止关闭"""
        if not self._can_close:
            return
        # 停止定时器
        self._countdown_timer.stop()
        super().reject()
