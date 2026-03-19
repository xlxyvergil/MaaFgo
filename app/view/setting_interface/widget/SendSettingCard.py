from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
from qfluentwidgets import MessageBoxBase
from qfluentwidgets  import CheckBox
from app.common.config import cfg


class SendSettingCard(MessageBoxBase):
    """选择发送通知的时机"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.widget.setMinimumWidth(350)
        self.widget.setMinimumHeight(100)
        self.init_widget()

    def init_widget(self):
        self.when_connect_failed = CheckBox(self.tr("When Connect Failed"), self)
        self.when_post_task = CheckBox(self.tr("When Post Task"), self)
        self.when_task_failed = CheckBox(self.tr("When Task Failed"), self)

        col1 = QVBoxLayout()
        col2 = QVBoxLayout()

        col1.addWidget(self.when_connect_failed)
        col2.addWidget(self.when_post_task)
        col2.addWidget(self.when_task_failed)

        mainLayout = QHBoxLayout()
        mainLayout.addLayout(col1)
        mainLayout.addLayout(col2)
        self.viewLayout.addLayout(mainLayout)

        self.when_connect_failed.setChecked(cfg.get(cfg.when_connect_failed))
        self.when_post_task.setChecked(cfg.get(cfg.when_post_task))
        self.when_task_failed.setChecked(cfg.get(cfg.when_task_failed))

    def save_setting(self):
        cfg.set(cfg.when_connect_failed, self.when_connect_failed.isChecked())
        cfg.set(cfg.when_post_task, self.when_post_task.isChecked())
        cfg.set(cfg.when_task_failed, self.when_task_failed.isChecked())
