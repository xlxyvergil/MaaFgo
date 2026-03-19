"""带浏览按钮的路径输入控件"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QFileDialog
from qfluentwidgets import LineEdit, ToolButton, FluentIcon as FIF


def _default_file_filter() -> str:
    """跨平台默认文件过滤：Windows 下可选 .exe，macOS/Linux 下用 All Files (*) 以支持无扩展名可执行文件。"""
    if sys.platform == "win32":
        return "Executable (*.exe);;All Files (*.*)"
    return "All Files (*)"


class PathLineEdit(QWidget):
    """带浏览按钮的路径输入控件
    
    功能:
    - 包含一个输入框和一个浏览按钮
    - 点击浏览按钮打开文件选择对话框
    - 支持自定义文件过滤器；未传入时按平台使用默认过滤器（Windows: .exe+全部，macOS/Linux: 全部）
    """

    def __init__(self, parent=None, file_filter: str | None = None):
        """初始化路径输入控件
        
        Args:
            parent: 父控件
            file_filter: 文件过滤器，如 "Executable (*.exe);;All Files (*.*)"；为 None 时使用跨平台默认值
        """
        super().__init__(parent)
        self.file_filter = file_filter if file_filter is not None else _default_file_filter()
        self._init_ui()

    def _init_ui(self):
        """初始化UI"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.line_edit = LineEdit()
        layout.addWidget(self.line_edit, stretch=1)

        self.browse_button =ToolButton(FIF.FOLDER)
        self.browse_button.setFixedWidth(35)
        self.browse_button.clicked.connect(self._on_browse_clicked)
        layout.addWidget(self.browse_button)

    def _on_browse_clicked(self):
        """浏览按钮点击事件"""
        # 获取当前路径作为对话框初始目录
        current_path = self.text()
        
        # 打开文件选择对话框
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select File"),
            current_path or "",
            self.file_filter,
        )
        
        if file_path:
            self.setText(file_path)

    def text(self) -> str:
        """获取文本"""
        return self.line_edit.text()

    def setText(self, text: str):
        """设置文本"""
        self.line_edit.setText(text)

    def setPlaceholderText(self, text: str):
        """设置占位符文本"""
        self.line_edit.setPlaceholderText(text)

    def setObjectName(self, name: str):
        """设置对象名称"""
        super().setObjectName(name)
        # 同时设置内部输入框的对象名称,方便通过 findChild 查找
        self.line_edit.setObjectName(f"{name}_inner")

    def setToolTip(self, tooltip: str):
        """设置提示文本"""
        self.line_edit.setToolTip(tooltip)

    def blockSignals(self, block: bool) -> bool:
        """阻止/允许信号"""
        return self.line_edit.blockSignals(block)

    @property
    def textChanged(self):
        """文本改变信号"""
        return self.line_edit.textChanged

    def setFileFilter(self, file_filter: str):
        """设置文件过滤器
        
        Args:
            file_filter: 文件过滤器,例如 "Executable Files (*.exe);;All Files (*.*)"
        """
        self.file_filter = file_filter
