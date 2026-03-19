from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QIcon,
    QIntValidator,
)


from qfluentwidgets import (
    BodyLabel,
    FluentIconBase,
    SettingCard,
    LineEdit,
    PasswordLineEdit,
    ToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from app.view.setting_interface.widget.RightCheckPrimaryPushButton import (
    RightCheckPrimaryPushButton,
)

import time

from typing import Optional, Union

from app.common.config import cfg


class LineEditCard(SettingCard):
    """设置中的输入框卡片"""

    def __init__(
        self,
        icon: Union[str, QIcon, FluentIconBase],
        title: str,
        holderText: str = "",
        content=None,
        parent=None,
        is_passwork: bool = False,
        num_only=True,
        button: bool = False,
        button_type: str = "",
        button_text: str = "",
    ):
        """
        初始化输入框卡片。

        :param icon: 图标
        :param title: 标题
        :param holderText: 占位符文本
        :param content: 内容
        :param parent: 父级控件
        :param is_passwork: 是否是密码输入框
        :param num_only: 是否只能输入数字
        :param button: 是否显示按钮
        :param button_type: 按钮类型
        :param button_text: 按钮文本
        """
        super().__init__(icon, title, content, parent)

        if is_passwork:
            self.lineEdit = PasswordLineEdit(self)
        else:
            self.lineEdit = LineEdit(self)
        if button_type == "primary":
            self.button = RightCheckPrimaryPushButton(button_text, self)
            self.button.rightClicked.connect(self._on_right_clicked)
        else:
            self.toolbutton = ToolButton(FIF.FOLDER_ADD, self)

        # 设置布局
        self.hBoxLayout.addWidget(self.lineEdit, 0)
        self.hBoxLayout.addSpacing(16)

        if button:
            if button_type == "primary":
                self.hBoxLayout.addWidget(self.button, 0)
            else:
                self.hBoxLayout.addWidget(self.toolbutton, 0)
            self.hBoxLayout.addSpacing(16)
            self.lineEdit.setFixedWidth(300)

        else:
            self.toolbutton.hide()
        # 设置占位符文本

        self.lineEdit.setText(str(holderText))

        # 设置输入限制
        if num_only:
            self.lineEdit.setValidator(QIntValidator())

    def _on_right_clicked(self):
        """处理右键点击事件"""
        self.lineEdit.setEnabled(True)


class MirrorCdkLineEditCard(LineEditCard):
    """专用的 MirrorCDK 密码输入卡片，额外展示剩余时间。"""

    def __init__(
        self,
        icon: Union[str, QIcon, FluentIconBase],
        title: str,
        holderText: str = "",
        content=None,
        parent=None,
        button_text: Optional[str] = None,
    ):
        super().__init__(
            icon,
            title,
            holderText=holderText,
            content=content,
            parent=parent,
            is_passwork=True,
            num_only=False,
            button=True,
            button_type="primary",
            button_text=button_text or "",
        )

        self.button.setText(button_text or self.tr("About Mirror"))
        self.bodyLabel = BodyLabel(self)
        self.bodyLabel.setWordWrap(False)
        self.bodyLabel.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.bodyLabel.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        line_edit_index = self.hBoxLayout.indexOf(self.lineEdit)
        if line_edit_index >= 0:
            self.hBoxLayout.insertWidget(line_edit_index, self.bodyLabel)
            self.hBoxLayout.insertSpacing(line_edit_index + 1, 8)
        else:
            self.hBoxLayout.addWidget(self.bodyLabel)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_remaining_time)
        self._timer.start()
        self.lineEdit.textChanged.connect(self._on_line_edit_changed)
        self._refresh_remaining_time()
        
    def _on_line_edit_changed(self, text: str):
        """CDK 更改时重置过期时间并更新提示。"""
        if cfg.get(cfg.cdk_expired_time) != -1:
            cfg.set(cfg.cdk_expired_time, -1)
        self._refresh_remaining_time()

    def _refresh_remaining_time(self):
        raw = cfg.get(cfg.cdk_expired_time)
        try:
            expiry = int(raw)
        except (TypeError, ValueError):
            expiry = -1

        prefix = self.tr("Remaining time: ")
        color = "#9da3ad"

        if expiry == -1:
            label_text = self.tr("Unknown")
        else:
            now = int(time.time())
            remaining = expiry - now
            if remaining <= 0:
                cfg.set(cfg.cdk_expired_time, -1)
                label_text = self.tr("Expired")
                color = "#f03e3e"
            else:
                label_text = self._format_remaining_time(remaining)
                color = self._color_for_remaining(remaining)

        self.bodyLabel.setStyleSheet(f"color: {color};")
        self.bodyLabel.setText(prefix + label_text)

    def _format_remaining_time(self, delta_seconds: int) -> str:
        units = (
            (365 * 24 * 3600, self.tr("year")),
            (24 * 3600, self.tr("day")),
            (3600, self.tr("hour")),
            (60, self.tr("minute")),
            (1, self.tr("second")),
        )
        for unit_seconds, unit_label in units:
            if delta_seconds >= unit_seconds:
                amount = delta_seconds // unit_seconds
                return f"{amount} {unit_label}"
        return self.tr("Less than a second")

    def _color_for_remaining(self, remaining_seconds: int) -> str:
        if remaining_seconds >= 7 * 24 * 3600:
            return "#40c057"
        if remaining_seconds >= 24 * 3600:
            return "#f59f00"
        return "#f03e3e"
