from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QMouseEvent
from qfluentwidgets import PrimaryPushButton


class RightCheckPrimaryPushButton(PrimaryPushButton):
    rightClicked = Signal()

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit()
        super().mousePressEvent(e)
