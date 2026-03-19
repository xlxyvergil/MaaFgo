"""
MFW-ChainFlow Assistant
日志处理器 - 将 callback 信号转换为 log_output 信号
作者:overflow65537
"""

from PySide6.QtCore import QObject
from app.common.signal_bus import signalBus
from app.core.service.i18n_service import get_i18n_service


class CallbackLogProcessor(QObject):
    """
    回调日志处理器
    将 signalBus.callback 信号转换为 signalBus.log_output 信号
    在 core 层统一处理日志输出逻辑
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 连接 callback 信号到处理函数
        signalBus.callback.connect(self._on_callback)

    def _on_callback(self, signal: dict):
        """处理 MAA Sink 发送的回调信号并转换为日志输出"""
        if not isinstance(signal, dict):
            return

        signal_name = signal.get("name", "")
        status = signal.get("status", 0)  # 1=Starting, 2=Succeeded, 3=Failed

        # 处理截图测试信号
        if signal_name == "speed_test":
            latency_ms = int(signal.get("details", 0) * 1000)
            level = self._latency_level(latency_ms)
            message = self.tr("screenshot test success, time: ") + f"{latency_ms}ms"
            signalBus.log_output.emit(level, message)
            return

        # 处理资源加载信号
        if signal_name == "resource":
            self._handle_resource_signal(status)

        # 处理控制器/模拟器连接信号
        elif signal_name == "controller":
            self._handle_controller_signal(status)

        # 处理任务执行信号
        elif signal_name == "task":
            task = signal.get("task", "")
            self._handle_task_signal(status, task)

        # 处理上下文信息信号
        elif signal_name == "context":
            details = signal.get("details", "")
            if details:
                # 使用全局 i18n 服务，根据原始 label 翻译为当前语言文本
                details = get_i18n_service().translate_label(details)
                # 获取 display 渠道列表，默认为 ["log"]
                display_channels = signal.get("display", ["log"])
                if isinstance(display_channels, str):
                    display_channels = [display_channels]
                self._dispatch_display(details, display_channels)

    def _handle_resource_signal(self, status: int):
        """处理资源加载信号 - 只输出失败"""
        # status: 1=Starting, 2=Succeeded, 3=Failed
        if status == 3:
            message = self.tr("Resource Loading Failed")
            signalBus.log_output.emit("ERROR", message)

    def _handle_controller_signal(self, status: int):
        """处理控制器/模拟器连接信号 - 只输出开始和失败"""
        # status: 1=Starting, 2=Succeeded, 3=Failed
        if status == 1:
            message = self.tr("Controller Started Connect")
            signalBus.log_output.emit("INFO", message)
        elif status == 3:
            message = self.tr("Controller Connect Failed")
            signalBus.log_output.emit("ERROR", message)

    def _handle_task_signal(self, status: int, task: str):
        """处理任务执行信号 - 只输出开始和失败"""
        # status: 1=Starting, 2=Succeeded, 3=Failed
        task_text = task if task else self.tr("Unknown Task")
        # 跳过停止任务信号
        if task_text in ["MaaNS::Tasker::post_stop", "MaaTaskerPostStop"]:
            return
        elif status == 1:
            message = self.tr("Task started execution: ") + task_text
            signalBus.log_output.emit("INFO", message)
        elif status == 3:
            message = self.tr("Task execution failed: ") + task_text
            signalBus.log_output.emit("ERROR", message)

    def _dispatch_display(self, message: str, channels: list):
        """
        根据 display 渠道列表分发消息

        :param message: 已翻译/替换占位符后的消息文本
        :param channels: display 渠道列表，如 ["log", "toast"]
        """
        for channel in channels:
            channel = channel.strip().lower() if isinstance(channel, str) else ""
            if channel == "log":
                signalBus.log_output.emit("INFO", message)
            elif channel == "toast":
                signalBus.focus_toast.emit(message)
            elif channel == "notification":
                signalBus.focus_notification.emit(message)
            elif channel == "dialog":
                signalBus.focus_dialog.emit(message)
            elif channel == "modal":
                signalBus.focus_modal.emit(message)
            else:
                # 未知渠道，默认走日志
                signalBus.log_output.emit("INFO", message)

    def _latency_level(self, latency_ms: int) -> str:
        """根据延迟时间确定日志级别"""
        if latency_ms <= 30:
            return "INFO"
        elif latency_ms <= 100:
            return "WARNING"
        elif latency_ms <= 200:
            return "ERROR"
        return "CRITICAL"
