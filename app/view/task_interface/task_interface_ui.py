from PySide6.QtCore import QMetaObject, QCoreApplication

from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QSizePolicy,
)


from app.core.core import ServiceCoordinator
from app.view.task_interface.components.LogoutputWidget import LogoutputWidget
from app.view.task_interface.components.ListToolBarWidget import (
    TaskListToolBarWidget,
    ConfigListToolBarWidget,
)
from app.view.task_interface.components.OptionWidget import OptionWidget
from app.view.task_interface.components.StartBarWidget import StartBarWidget


class UI_TaskInterface(object):
    def __init__(self, service_coordinator: ServiceCoordinator, parent=None):
        self.service_coordinator = service_coordinator
        self.parent = parent

    def setupUi(self, TaskInterface):
        TaskInterface.setObjectName("TaskInterface")
        # 主窗口
        self.main_layout = QHBoxLayout()
        self.log_output_widget = LogoutputWidget(service_coordinator=self.service_coordinator)
        
        # 设置日志区域固定宽度，取消侧向拉伸
        self.log_output_widget.setFixedWidth(344)  # 设置固定宽度344px
        log_policy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.log_output_widget.setSizePolicy(log_policy)

        self._init_control_panel()
        self._init_option_panel()

        self.main_layout.addWidget(self.control_panel)
        self.main_layout.addWidget(self.option_panel_widget)
        self.main_layout.addWidget(self.log_output_widget)
        self.main_layout.setStretch(0, 0)  # 控制面板：固定宽度，不拉伸
        self.main_layout.setStretch(1, 1)  # 选项/公告面板：可拉伸
        self.main_layout.setStretch(2, 0)  # 日志区域：固定宽度，不拉伸

        TaskInterface.setLayout(self.main_layout)
        self.retranslateUi(TaskInterface)
        QMetaObject.connectSlotsByName(TaskInterface)

    def _init_option_panel(self):
        """初始化选项面板"""
        self.option_panel_widget = QWidget()
        self.option_panel_layout = QVBoxLayout(self.option_panel_widget)
        self.option_panel = OptionWidget(service_coordinator=self.service_coordinator)
        # 移除固定宽度限制，允许选项面板横向拉伸
        self.option_panel.setMinimumWidth(344)  # 设置最小宽度而不是固定宽度
        # 设置大小策略：水平方向可拉伸，垂直方向可拉伸
        option_policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.option_panel.setSizePolicy(option_policy)
        self.option_panel_layout.addWidget(self.option_panel)

    def _init_control_panel(self):
        """初始化控制面板"""
        self.config_selection = ConfigListToolBarWidget(
            service_coordinator=self.service_coordinator
        )
        self.config_selection.setFixedWidth(344)
        self.config_selection.setFixedHeight(195)

        self.start_bar = StartBarWidget()
        self.start_bar.setFixedWidth(344)

        # 控制面板布局
        self.control_panel = QWidget()
        self.control_panel_layout = QVBoxLayout(self.control_panel)
        # 控制面板总体布局
        self.task_info = TaskListToolBarWidget(
            service_coordinator=self.service_coordinator,
            task_filter_mode="normal",
        )
        self.task_info.setFixedWidth(344)
        
        # 设置控制面板固定宽度，垂直方向可拉伸
        self.control_panel.setFixedWidth(344)
        control_policy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.control_panel.setSizePolicy(control_policy)

        self.control_panel_layout.addWidget(self.config_selection)
        self.control_panel_layout.addWidget(self.task_info)
        self.control_panel_layout.addWidget(self.start_bar)

        # 设置比例
        self.control_panel_layout.setStretch(0, 5)
        self.control_panel_layout.setStretch(1, 10)
        self.control_panel_layout.setStretch(2, 1)

    def retranslateUi(self, TaskInterface):
        _translate = QCoreApplication.translate
        TaskInterface.setWindowTitle(_translate("TaskInterface", "Form"))
