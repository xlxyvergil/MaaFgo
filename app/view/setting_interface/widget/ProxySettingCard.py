from PySide6.QtCore import Qt, Signal

from PySide6.QtGui import QIcon
from qfluentwidgets import SettingCard, LineEdit, ComboBox, FluentIconBase
from typing import Union

class ProxySettingCard(SettingCard):
    def __init__(
        self, icon: Union[str, QIcon, FluentIconBase], title, content=None, parent=None
    ):
        # 有一个下拉框和一个输入框
        super().__init__(icon, title, content, parent)
        self.input = LineEdit(self)
        self.input.setPlaceholderText("<IP>:<PORT>")
        self.combobox = ComboBox(self)
        self.combobox.addItems(["HTTP", "SOCKS5"])

        self.hBoxLayout.addWidget(self.combobox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self.hBoxLayout.addWidget(self.input, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
