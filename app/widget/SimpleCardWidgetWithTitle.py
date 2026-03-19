from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Qt


from qfluentwidgets import SimpleCardWidget, BodyLabel


class SimpleCardWidgetWithTitle(QWidget):
    def __init__(self, title, LayoutClass, parent=None):

        super().__init__(parent)
        self.title = title
        self.vbox = QVBoxLayout(self)
        self.title = BodyLabel(title)
        self.title.setStyleSheet("font-size: 20px;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.main_widget = SimpleCardWidget()
        self.main_widget_layout: QVBoxLayout = LayoutClass(self.main_widget)

        self.vbox.addWidget(self.title)
        self.vbox.addWidget(self.main_widget)
        # 设置比例1:99
        self.vbox.setStretch(0, 1)
        self.vbox.setStretch(1, 99)
