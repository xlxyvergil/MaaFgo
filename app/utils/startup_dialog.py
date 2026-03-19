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

"""
MFW-ChainFlow Assistant
启动阶段通用弹窗模块
作者: overflow65537
"""

import sys
import traceback
from enum import Enum, auto
from typing import Optional, Callable, List, Tuple
from dataclasses import dataclass, field

from PySide6.QtWidgets import QApplication, QVBoxLayout, QHBoxLayout, QWidget
from PySide6.QtCore import Qt, QObject, QUrl
from PySide6.QtGui import QDesktopServices
from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    PrimaryPushButton,
    PushButton,
    TextEdit,
)


class StartupDialogType(Enum):
    """启动弹窗类型"""

    INFO = auto()  # 信息提示
    WARNING = auto()  # 警告
    ERROR = auto()  # 错误
    CRITICAL = auto()  # 严重错误


@dataclass
class DialogButton:
    """弹窗按钮配置"""

    text: str
    callback: Optional[Callable[[], None]] = None
    is_primary: bool = False
    open_url: Optional[str] = None  # 如果设置，点击后打开此 URL


@dataclass
class StartupDialogConfig:
    """启动弹窗配置"""

    dialog_type: StartupDialogType = StartupDialogType.INFO
    title: str = ""
    content: str = ""
    detail: str = ""  # 详细信息（可展开/滚动显示，如堆栈信息）
    buttons: List[DialogButton] = field(default_factory=list)
    exit_after_close: bool = False  # 关闭后是否退出程序
    exit_code: int = 0  # 退出码


class StartupDialog(MessageBoxBase):
    """通用启动弹窗

    用于启动阶段的各种提示：
    - 多开警告
    - 缺少运行时库
    - 未捕获的全局异常
    等
    """

    def __init__(self, config: StartupDialogConfig, parent=None):
        super().__init__(parent)

        self.config = config
        self._clicked_button: Optional[DialogButton] = None

        self._setup_ui()

    def _setup_ui(self):
        """设置 UI"""
        # 隐藏默认按钮组
        self.buttonGroup.hide()

        # 主布局
        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)

        # 标题
        if self.config.title:
            title_label = SubtitleLabel(self.config.title, self)
            title_label.setWordWrap(True)
            main_layout.addWidget(title_label)

        # 内容
        if self.config.content:
            content_label = BodyLabel(self.config.content, self)
            content_label.setWordWrap(True)
            content_label.setAlignment(Qt.AlignmentFlag.AlignTop)
            main_layout.addWidget(content_label)

        # 详细信息（可滚动）
        if self.config.detail:
            detail_edit = TextEdit(self)
            detail_edit.setPlainText(self.config.detail)
            detail_edit.setReadOnly(True)
            detail_edit.setMinimumHeight(150)
            detail_edit.setMaximumHeight(300)
            main_layout.addWidget(detail_edit)

        # 按钮区域
        if self.config.buttons:
            button_layout = QHBoxLayout()
            button_layout.setSpacing(8)
            button_layout.addStretch()

            for btn_config in self.config.buttons:
                if btn_config.is_primary:
                    btn = PrimaryPushButton(btn_config.text, self)
                else:
                    btn = PushButton(btn_config.text, self)

                # 绑定点击事件
                btn.clicked.connect(
                    lambda checked, b=btn_config: self._on_button_clicked(b)
                )
                button_layout.addWidget(btn)

            main_layout.addLayout(button_layout)

        self.viewLayout.addLayout(main_layout)

        # 设置最小宽度
        self.widget.setMinimumWidth(400)

    def _on_button_clicked(self, btn_config: DialogButton):
        """处理按钮点击"""
        self._clicked_button = btn_config

        # 执行回调
        if btn_config.callback:
            btn_config.callback()

        # 打开 URL
        if btn_config.open_url:
            QDesktopServices.openUrl(QUrl(btn_config.open_url))

        # 关闭弹窗
        self.accept()

    def exec(self) -> int:
        """执行弹窗"""
        result = super().exec()

        # 关闭后是否退出
        if self.config.exit_after_close:
            sys.exit(self.config.exit_code)

        return result

    @property
    def clicked_button(self) -> Optional[DialogButton]:
        """获取被点击的按钮"""
        return self._clicked_button


class StartupDialogManager(QObject):
    """启动弹窗管理器
    
    用于管理启动阶段的各种弹窗，支持 i18n。
    使用 self.tr() 方法进行翻译，pylupdate6 可以正确识别。
    
    使用示例:
        manager = StartupDialogManager()
        manager.show_vcredist_missing()
        manager.show_instance_running()
        manager.show_uncaught_exception(exc_type, exc_value, exc_traceback)
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__()
        self._parent = parent
        self._dummy_parent: Optional[QWidget] = None

    def _ensure_app_exists(self) -> QApplication:
        """确保 QApplication 实例存在"""
        app = QApplication.instance()
        if app is None or not isinstance(app, QApplication):
            app = QApplication([sys.argv[0]])
        return app

    def _create_dummy_parent(self) -> QWidget:
        """创建一个用于弹窗的临时父窗口
        
        MessageBoxBase 需要一个有效的父窗口来计算位置和尺寸。
        当没有主窗口时，创建一个透明的临时窗口作为父窗口。
        """
        parent = QWidget()
        parent.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        parent.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 获取屏幕尺寸并设置窗口大小和位置
        app = QApplication.instance()
        if app and isinstance(app, QApplication):
            screen = app.primaryScreen()
            if screen:
                geometry = screen.availableGeometry()
                parent.setGeometry(geometry)

        parent.show()
        return parent

    def _get_parent(self) -> QWidget:
        """获取弹窗的父窗口"""
        if self._parent is not None:
            return self._parent
        self._dummy_parent = self._create_dummy_parent()
        return self._dummy_parent

    def _cleanup_dummy_parent(self) -> None:
        """清理临时父窗口"""
        if self._dummy_parent is not None:
            self._dummy_parent.close()
            self._dummy_parent.deleteLater()
            self._dummy_parent = None

    def _copy_to_clipboard(self, text: str) -> None:
        """复制文本到剪贴板"""
        app = QApplication.instance()
        if app and isinstance(app, QApplication):
            clipboard = app.clipboard()
            if clipboard:
                clipboard.setText(text)

    @staticmethod
    def get_vcredist_download_url() -> Tuple[str, str]:
        """获取 VC++ Redistributable 下载链接和平台名称"""
        if sys.platform == "win32":
            return ("https://aka.ms/vs/17/release/vc_redist.x64.exe", "Windows")
        elif sys.platform == "darwin":
            return ("https://support.apple.com/downloads", "macOS")
        else:
            return (
                "https://docs.microsoft.com/en-us/cpp/linux/download-install-and-setup-the-linux-development-workload",
                "Linux",
            )

    def show_vcredist_missing(self) -> None:
        """显示缺少 VC++ Redistributable 的弹窗"""
        download_url, platform_name = self.get_vcredist_download_url()

        config = StartupDialogConfig(
            dialog_type=StartupDialogType.CRITICAL,
            title=self.tr("Missing Runtime Library"),
            content=self.tr(
                "Failed to load MAA framework library.\n\n"
                "Microsoft Visual C++ Redistributable runtime library is missing.\n\n"
                "Current system: {platform}\n\n"
                "Please download and install Visual C++ Redistributable, then restart the program."
            ).format(platform=platform_name),
            buttons=[
                DialogButton(
                    text=self.tr("Open Download Page"),
                    is_primary=True,
                    open_url=download_url,
                ),
                DialogButton(
                    text=self.tr("Exit"),
                    is_primary=False,
                ),
            ],
            exit_after_close=True,
            exit_code=1,
        )

        self._ensure_app_exists()
        try:
            dialog = StartupDialog(config, self._get_parent())
            dialog.exec()
        finally:
            self._cleanup_dummy_parent()

    def show_instance_running(self) -> None:
        """显示程序已运行的弹窗"""
        config = StartupDialogConfig(
            dialog_type=StartupDialogType.WARNING,
            title=self.tr("Program Already Running"),
            content=self.tr(
                "The program is already running.\n\n"
                "Please close the existing window or terminate the process in Task Manager before starting again."
            ),
            buttons=[
                DialogButton(
                    text=self.tr("OK"),
                    is_primary=True,
                ),
            ],
            exit_after_close=True,
            exit_code=0,
        )

        self._ensure_app_exists()
        try:
            dialog = StartupDialog(config, self._get_parent())
            dialog.exec()
        finally:
            self._cleanup_dummy_parent()

    def show_uncaught_exception(
        self, exc_type, exc_value, exc_traceback
    ) -> None:
        """显示未捕获异常的弹窗"""
        # 格式化堆栈信息
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
        tb_text = "".join(tb_lines)

        config = StartupDialogConfig(
            dialog_type=StartupDialogType.ERROR,
            title=self.tr("Program Error"),
            content=self.tr(
                "The program encountered an unhandled exception.\n\n"
                "Error type: {exc_type}\n"
                "Error message: {exc_value}\n\n"
                "Below is the detailed stack trace. You can copy it and report to the developer:"
            ).format(exc_type=exc_type.__name__, exc_value=str(exc_value)),
            detail=tb_text,
            buttons=[
                DialogButton(
                    text=self.tr("Copy Error Info"),
                    is_primary=False,
                    callback=lambda: self._copy_to_clipboard(tb_text),
                ),
                DialogButton(
                    text=self.tr("Continue"),
                    is_primary=True,
                ),
            ],
            exit_after_close=False,
            exit_code=0,
        )

        self._ensure_app_exists()
        try:
            dialog = StartupDialog(config, self._get_parent())
            dialog.exec()
        finally:
            self._cleanup_dummy_parent()

    def show_custom(
        self,
        title: str,
        content: str,
        dialog_type: StartupDialogType = StartupDialogType.INFO,
        detail: str = "",
        buttons: Optional[List[DialogButton]] = None,
        exit_after_close: bool = False,
        exit_code: int = 0,
    ) -> Optional[DialogButton]:
        """显示自定义弹窗

        Args:
            title: 标题
            content: 内容
            dialog_type: 弹窗类型
            detail: 详细信息
            buttons: 按钮列表，如果为 None 则使用默认的"确定"按钮
            exit_after_close: 关闭后是否退出
            exit_code: 退出码

        Returns:
            被点击的按钮配置，如果没有点击任何按钮则返回 None
        """
        if buttons is None:
            buttons = [
                DialogButton(
                    text=self.tr("OK"),
                    is_primary=True,
                ),
            ]

        config = StartupDialogConfig(
            dialog_type=dialog_type,
            title=title,
            content=content,
            detail=detail,
            buttons=buttons,
            exit_after_close=exit_after_close,
            exit_code=exit_code,
        )

        self._ensure_app_exists()
        try:
            dialog = StartupDialog(config, self._get_parent())
            dialog.exec()
            return dialog.clicked_button
        finally:
            self._cleanup_dummy_parent()


# ============ 便捷函数（保持向后兼容） ============


def show_vcredist_missing_dialog(parent=None) -> None:
    """显示缺少 VC++ Redistributable 的弹窗"""
    manager = StartupDialogManager(parent)
    manager.show_vcredist_missing()


def show_instance_running_dialog(parent=None) -> None:
    """显示程序已运行的弹窗"""
    manager = StartupDialogManager(parent)
    manager.show_instance_running()


def show_uncaught_exception_dialog(
    exc_type, exc_value, exc_traceback, parent=None
) -> None:
    """显示未捕获异常的弹窗"""
    manager = StartupDialogManager(parent)
    manager.show_uncaught_exception(exc_type, exc_value, exc_traceback)


def show_custom_dialog(
    title: str,
    content: str,
    dialog_type: StartupDialogType = StartupDialogType.INFO,
    detail: str = "",
    buttons: Optional[List[DialogButton]] = None,
    exit_after_close: bool = False,
    exit_code: int = 0,
    parent=None,
) -> Optional[DialogButton]:
    """显示自定义弹窗"""
    manager = StartupDialogManager(parent)
    return manager.show_custom(
        title=title,
        content=content,
        dialog_type=dialog_type,
        detail=detail,
        buttons=buttons,
        exit_after_close=exit_after_close,
        exit_code=exit_code,
    )
