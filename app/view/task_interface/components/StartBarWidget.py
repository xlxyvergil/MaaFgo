from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QWidget, QHBoxLayout
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    SimpleCardWidget,
    TransparentPushButton,
    TransparentDropDownPushButton,
    RoundMenu,
    Action,
    FluentIcon as FIF,
)


class StartBarWidget(QWidget):
    """启动栏组件"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_start_bar()
        self.start_bar_main_layout = QHBoxLayout(self)
        self.start_bar_main_layout.addWidget(self.start_bar)


    def _init_start_bar(self):
        """初始化启动栏"""
        # 启动/停止按钮（合并为一个）
        self.run_button = TransparentPushButton(self.tr("Start"), self, FIF.PLAY)
        self._is_running = False


        # 启动栏总体布局
        self.start_bar = SimpleCardWidget()
        self.start_bar.setClickEnabled(False)
        self.start_bar.setBorderRadius(8)

        self.start_bar_layout = QHBoxLayout(self.start_bar)
        self.start_bar_layout.addWidget(self.run_button)

