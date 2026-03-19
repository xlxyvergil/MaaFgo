from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QIcon
from qfluentwidgets import SettingCard, PrimaryPushButton, ComboBox, ConfigItem, FluentIconBase
from typing import Union

from qfluentwidgets import qconfig


class DoubleButtonSettingCard(SettingCard):
    """Setting card with a push button"""

    clicked = Signal()
    clicked2 = Signal()

    def __init__(
        self,
        text,
        text2,
        icon: Union[str, QIcon, FluentIconBase],
        title,
        configItem: ConfigItem | None = None,
        comboBox=True,
        content=None,
        parent=None,
    ):
        """
        Parameters
        ----------
        text: str
            the text of push button
        text2: str
            the text of push button
        icon: str | QIcon | FluentIconBase
            the icon to be drawn

        title: str
            the title of card

        content: str
            the content of card

        parent: QWidget
            parent widget
        """
        super().__init__(icon, title, content, parent)
        self.button = PrimaryPushButton(text, self)
        self.button2 = PrimaryPushButton(text2, self)

        if comboBox:
            self.combobox = ComboBox(self)
            self.hBoxLayout.addWidget(self.combobox, 0, Qt.AlignmentFlag.AlignRight)
            self.hBoxLayout.addSpacing(16)
            self.combobox.addItems(
                [
                    self.tr("stable"),
                    self.tr("beta"),
                ]
            )
            self.combobox.currentIndexChanged.connect(self.setValue)

        self.hBoxLayout.addWidget(self.button2, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self.hBoxLayout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

        self.button.clicked.connect(self.clicked)
        self.button2.clicked.connect(self.clicked2)

        self.configItem = configItem
        if configItem:
            self.setValue(qconfig.get(configItem))
            configItem.valueChanged.connect(self.setValue)

    def setValue(self, value):
        if self.configItem:
            qconfig.set(self.configItem, value)
