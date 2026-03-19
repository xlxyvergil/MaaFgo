from asyncio.base_futures import _FINISHED
import re

from PySide6.QtCore import Qt, QRunnable, QThreadPool, Signal, QObject
from PySide6.QtWidgets import QWidget, QHBoxLayout

from qfluentwidgets import ComboBox, ToolButton
from qfluentwidgets import FluentIcon as FIF

from maa.toolkit import Toolkit

from app.utils.logger import logger
from app.utils.controller_utils import ControllerHelper


class DeviceFinderTask(QRunnable):
    class Signals(QObject):
        finished = Signal(dict, str)  # device_mapping, controller_type

    def __init__(self, controller_type):
        super().__init__()
        self.controller_type = controller_type
        self.signals = DeviceFinderTask.Signals()

    def run(self):
        device_mapping = {}
        try:
            if self.controller_type.lower() == "adb":
                devices = Toolkit.find_adb_devices()
                for device in devices:
                    # 尝试从设备 config 中携带的 ld pid 反查雷电序号
                    ld_pid = (
                        (getattr(device, "config", {}) or {})
                        .get("extras", {})
                        .get("ld", {})
                        .get("pid")
                    )
                    device_index = ControllerHelper.resolve_emulator_index(
                        device, ld_pid=ld_pid
                    )
                    display_name = (
                        f"{device.name}[{device_index}]({device.address})"
                        if device_index is not None
                        else f"{device.name}({device.address})"
                    )
                    # 自动生成 ADB 控制器（模拟器）运行路径和参数
                    adb_path_str = str(device.adb_path) if device.adb_path else None
                    emulator_path, emulator_params = (
                        ControllerHelper.generate_emulator_launch_info(
                            device.name, device_index, adb_path_str
                        )
                    )

                    device_mapping[display_name] = {
                        "name": device.name,
                        "adb_path": device.adb_path,
                        "address": device.address,
                        "screencap_methods": device.screencap_methods,
                        "input_methods": device.input_methods,
                        "config": device.config,
                        "device_index": device_index,
                        "emulator_path": emulator_path,
                        "emulator_params": emulator_params,
                    }

            elif self.controller_type.lower() == "win32":
                devices = Toolkit.find_desktop_windows()
                for device in devices:
                    device_mapping[
                        f"{device.window_name or 'Unknow Window'}({device.hwnd})"
                    ] = {
                        "window_name": device.window_name,
                        "class_name": device.class_name,
                        "hwnd": str(device.hwnd),
                    }
        finally:
            # Convert all integer values that might exceed 64-bit signed limits to strings
            safe_mapping = {}
            for key, value in device_mapping.items():
                safe_value = value.copy()

                # Special handling for adb device config which might contain large integers
                if "config" in safe_value and isinstance(safe_value["config"], dict):
                    for config_key, config_value in safe_value["config"].items():
                        if isinstance(config_value, int) and not (
                            -9223372036854775808 <= config_value <= 9223372036854775807
                        ):
                            safe_value["config"][config_key] = str(config_value)

                # Handle any other large integer fields we might have missed
                for field, field_value in safe_value.items():
                    if isinstance(field_value, int) and not (
                        -9223372036854775808 <= field_value <= 9223372036854775807
                    ):
                        safe_value[field] = str(field_value)

                safe_mapping[key] = safe_value

            self.signals.finished.emit(safe_mapping, self.controller_type)


class DeviceFinderWidget(QWidget):
    # 当未找到任何设备时发出的信号
    # 参数为当前控制器类型字符串，例如 "adb" 或 "win32"
    no_device_found = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._win32_class_pattern = None
        self._win32_window_pattern = None
        self.current_controller_type = None
        self.device_mapping = {}

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.combo_box = ComboBox()
        self.combo_box.setFixedWidth(260)
        layout.addWidget(self.combo_box, stretch=1)

        self.search_button = ToolButton(FIF.SEARCH)
        self.search_button.setFixedWidth(35)
        self.search_button.clicked.connect(self._on_search_clicked)

        layout.addWidget(self.search_button)

    def change_controller_type(self, new_type: str):
        self.current_controller_type = new_type
        self.combo_box.clear()
        self.device_mapping = {}

    def _on_search_clicked(self):
        self.search_button.setDisabled(True)
        if self.current_controller_type is None:
            raise ValueError("Controller type not set")

        # 创建任务并将其提交到线程池
        task = DeviceFinderTask(self.current_controller_type)
        task.signals.finished.connect(self._on_device_found)
        QThreadPool.globalInstance().start(task)

    def _on_device_found(self, device_mapping, controller_type):
        # 确保当前控制器类型与查找结果一致
        if controller_type != self.current_controller_type:
            return

        if controller_type.lower() == "win32":
            filtered = {}
            for key, device in device_mapping.items():
                if self._matches_win32_filters(device):
                    filtered[key] = device
            device_mapping = filtered

        # 如果没有找到任何设备，仅发出信号，不清空已有下拉框内容
        if not device_mapping:
            self.no_device_found.emit(controller_type)
        else:
            # 更新设备映射和下拉框
            self.device_mapping = device_mapping
            self.combo_box.clear()
            self.combo_box.addItems(list(device_mapping.keys()))

        self.search_button.setEnabled(True)

    def _matches_win32_filters(self, device: dict) -> bool:
        if not (self._win32_class_pattern or self._win32_window_pattern):
            return True

        class_name = str(device.get("class_name") or "")
        window_name = str(device.get("window_name") or "")
        class_match = (
            bool(self._win32_class_pattern.search(class_name))
            if self._win32_class_pattern
            else True
        )
        window_match = (
            bool(self._win32_window_pattern.search(window_name))
            if self._win32_window_pattern
            else True
        )

        if self._win32_class_pattern and self._win32_window_pattern:
            return class_match and window_match
        return class_match and window_match

    def set_win32_filters(self, class_regex: str | None, window_regex: str | None):
        self._win32_class_pattern = self._compile_regex(class_regex, "class")
        self._win32_window_pattern = self._compile_regex(window_regex, "window")

    def _compile_regex(self, pattern: str | None, label: str):
        if not pattern:
            return None
        try:
            return re.compile(pattern)
        except re.error as exc:
            logger.warning(f"正则过滤器 [{label}] 编译失败: {exc}")
            return None
