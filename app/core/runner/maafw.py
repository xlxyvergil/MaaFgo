"""
MFW-ChainFlow Assistant
MFW-ChainFlow Assistant MaaFW核心
原作者: MaaXYZ
地址: https://github.com/MaaXYZ/MaaDebugger
修改:overflow65537
"""

import json
import os
import re
import sys
import importlib.util
from enum import Enum
from typing import List, Dict
import subprocess
import threading
from pathlib import Path
import numpy
from asyncify import asyncify

import maa
from maa.context import Context, ContextEventSink
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from maa.controller import (
    AdbController,
    Win32Controller,
    PlayCoverController,
    GamepadController,
)
from maa.tasker import Tasker
from maa.agent_client import AgentClient
from maa.resource import Resource
from maa.toolkit import Toolkit, AdbDevice, DesktopWindow
from maa.define import (
    MaaAdbScreencapMethodEnum,
    MaaAdbInputMethodEnum,
    MaaWin32InputMethodEnum,
    MaaWin32ScreencapMethodEnum,
    MaaGamepadTypeEnum,
)
from PySide6.QtCore import QObject, Signal

from app.utils.logger import logger


# 以下代码引用自 MaaDebugger 项目的 ./src/MaaDebugger/maafw/__init__.py 文件，用于生成maafw实例
class MaaFWError(Enum):
    RESOURCE_OR_CONTROLLER_NOT_INITIALIZED = 1
    AGENT_CONNECTION_FAILED = 2
    TASKER_NOT_INITIALIZED = 3
    AGENT_CONFIG_MISSING = 4
    AGENT_CONFIG_EMPTY_LIST = 5
    AGENT_CONFIG_INVALID = 6
    AGENT_CHILD_EXEC_MISSING = 7
    AGENT_START_FAILED = 8


from maa.controller import ControllerEventSink, Controller, NotificationType
from maa.resource import ResourceEventSink, Resource
from maa.tasker import TaskerEventSink, Tasker
from maa.context import ContextEventSink, Context

from app.common.signal_bus import signalBus


class MaaContextSink(ContextEventSink):
    def on_raw_notification(self, context: Context, msg: str, details: dict):
        focus_entry = (details.get("focus") or {}).get(msg)
        if not focus_entry:
            return

        # 兼容两种格式：
        # 旧格式 (str): "focus": { "Node.Action.Starting": "{name} 开始执行" }
        # 新格式 (dict): "focus": { "Node.Action.Starting": { "content": "{name} 开始执行", "display": "toast" } }
        if isinstance(focus_entry, str):
            content = focus_entry
            display = ["log"]
        elif isinstance(focus_entry, dict):
            content = focus_entry.get("content", "")
            raw_display = focus_entry.get("display", "log")
            if isinstance(raw_display, list):
                display = raw_display
            else:
                display = [raw_display]
        else:
            return

        if not content:
            return

        # 替换占位符
        content = content.replace("{name}", details.get("name", ""))
        content = content.replace("{task_id}", str(details.get("task_id", "")))
        content = content.replace("{list}", details.get("list", ""))

        signalBus.callback.emit({"name": "context", "details": content, "display": display})

        if msg == "Node.Recognition.Succeeded":
            if details.get("Abort", False):
                signalBus.callback.emit({"name": "abort"})
            if details.get("Notice", False):
                pass

    def on_node_next_list(
        self,
        context: Context,
        noti_type: NotificationType,
        detail: ContextEventSink.NodeNextListDetail,
    ):
        pass

    def on_node_action(
        self,
        context: Context,
        noti_type: NotificationType,
        detail: ContextEventSink.NodeActionDetail,
    ):
        pass

    def on_node_recognition(
        self,
        context: Context,
        noti_type: NotificationType,
        detail: ContextEventSink.NodeRecognitionDetail,
    ):
        pass


class MaaControllerEventSink(ControllerEventSink):
    def on_raw_notification(self, controller: Controller, msg: str, details: dict):
        pass

    def on_controller_action(
        self,
        controller: Controller,
        noti_type: NotificationType,
        detail: ControllerEventSink.ControllerActionDetail,
    ):
        # signalBus.callback.emit({"name": "controller", "status": noti_type.value})
        pass


class MaaResourceEventSink(ResourceEventSink):
    def on_raw_notification(self, resource: Resource, msg: str, details: dict):
        pass

    def on_resource_loading(
        self,
        resource: Resource,
        noti_type: NotificationType,
        detail: ResourceEventSink.ResourceLoadingDetail,
    ):
        signalBus.callback.emit({"name": "resource", "status": noti_type.value})


class MaaTaskerEventSink(TaskerEventSink):
    def on_raw_notification(self, tasker: Tasker, msg: str, details: dict):
        pass

    def on_tasker_task(
        self,
        tasker: Tasker,
        noti_type: NotificationType,
        detail: TaskerEventSink.TaskerTaskDetail,
    ):
        signalBus.callback.emit(
            {"name": "task", "task": detail.entry, "status": noti_type.value}
        )


maa_context_sink = MaaContextSink()
maa_controller_sink = MaaControllerEventSink()
maa_resource_sink = MaaResourceEventSink()
maa_tasker_sink = MaaTaskerEventSink()


class MaaFW(QObject):

    resource: Resource | None
    controller: (
        AdbController | Win32Controller | PlayCoverController | GamepadController | None
    )
    tasker: Tasker | None
    agent: AgentClient | None

    agent_thread: subprocess.Popen | None
    agent_output_thread: threading.Thread | None

    maa_controller_sink: MaaControllerEventSink | None
    maa_context_sink: MaaContextSink | None
    maa_resource_sink: MaaResourceEventSink | None
    maa_tasker_sink: MaaTaskerEventSink | None

    custom_info = Signal(int)
    agent_info = Signal(str)

    # 超时后仍未停止的 agent 进程最长等待时间
    AGENT_TERMINATE_TIMEOUT_SECONDS: float = 5.0

    def __init__(
        self,
        maa_controller_sink: MaaControllerEventSink | None = None,
        maa_context_sink: MaaContextSink | None = None,
        maa_resource_sink: MaaResourceEventSink | None = None,
        maa_tasker_sink: MaaTaskerEventSink | None = None,
    ):
        # 确保正确初始化 QObject 基类，避免 Qt 运行时错误
        super().__init__()

        Toolkit.init_option("./")
        self.resource = None
        self.controller = None
        self.tasker = None

        # 这里传入的是 Sink 类，需要在此处实例化，避免把类对象/descriptor 直接交给底层 C 接口
        self.maa_controller_sink = maa_controller_sink
        self.maa_context_sink = maa_context_sink
        self.maa_resource_sink = maa_resource_sink
        self.maa_tasker_sink = maa_tasker_sink

        self.agent = None
        self.agent_thread = None
        self.agent_output_thread = None

        self.agent_data_raw = None
        # 控制是否需要向 UI 报告自定义对象注册情况
        self.need_register_report: bool = False
        # 记录最近一次自定义对象加载的成功/失败情况
        self.custom_load_report: Dict[str, Dict[str, List]] = {
            "actions": {"success": [], "failed": []},
            "recognitions": {"success": [], "failed": []},
        }
        # 记录添加到 sys.path 的路径，用于后续清理
        self._custom_sys_paths: List[str] = []
        # 记录上次加载的 custom_root，用于清理模块缓存
        self._last_custom_root: Path | None = None

    def load_custom_objects(self, custom_config_path: str | Path) -> bool:
        """
        从 custom.json 加载并注册自定义动作/识别器。

        :param custom_config_path: custom.json 文件路径或包含它的目录
        :return: 是否成功加载到至少一个自定义对象
        """
        project_dir = Path.cwd()
        config_path = Path(
            str(custom_config_path).replace("{PROJECT_DIR}", str(project_dir))
        )
        if config_path.is_dir():
            config_path = config_path / "custom.json"

        if not config_path.exists():
            logger.warning(f"自定义配置文件 {config_path} 不存在")
            return False
        if not config_path.is_file():
            logger.warning(f"自定义配置路径 {config_path} 不是文件")
            return False

        try:
            with config_path.open("r", encoding="utf-8") as fp:
                custom_config: Dict[str, Dict] = json.load(fp)
        except Exception as exc:
            logger.error(f"读取自定义配置失败: {exc}")
            return False

        custom_root = config_path.parent.resolve()
        resource = self._init_resource()
        loaded_any = False
        self.custom_load_report = {
            "actions": {"success": [], "failed": []},
            "recognitions": {"success": [], "failed": []},
        }

        # 清理之前加载的模块：移除所有与之前 custom_root 相关的模块
        # 包括主模块、子模块和顶级包模块（如 action, Recognition）
        if hasattr(self, "_last_custom_root") and self._last_custom_root:
            modules_to_remove = []
            for module_key in list(sys.modules.keys()):
                # 移除所有以文件路径为key的模块
                if isinstance(module_key, str) and (
                    module_key.startswith(str(self._last_custom_root))
                    or (
                        os.path.isabs(module_key)
                        and Path(module_key).is_relative_to(self._last_custom_root)
                    )
                ):
                    modules_to_remove.append(module_key)
                # 检查模块的 __file__ 或 __path__ 是否在旧的 custom_root 下
                elif isinstance(module_key, str):
                    try:
                        module = sys.modules.get(module_key)
                        if not module:
                            continue

                        # 检查普通模块的 __file__
                        if hasattr(module, "__file__") and module.__file__:
                            try:
                                if (
                                    Path(module.__file__)
                                    .resolve()
                                    .is_relative_to(self._last_custom_root)
                                ):
                                    modules_to_remove.append(module_key)
                                    continue
                            except (ValueError, OSError):
                                pass

                        # 检查包模块的 __path__
                        if hasattr(module, "__path__"):
                            try:
                                for path_entry in module.__path__:
                                    if (
                                        Path(path_entry)
                                        .resolve()
                                        .is_relative_to(self._last_custom_root)
                                    ):
                                        modules_to_remove.append(module_key)
                                        break
                            except (ValueError, OSError):
                                pass
                    except Exception:
                        pass

            for module_key in set(modules_to_remove):
                try:
                    del sys.modules[module_key]
                    logger.debug(f"已清理模块缓存: {module_key}")
                except KeyError:
                    pass

        # 清理之前添加的 sys.path 条目（如果存在）
        for path in self._custom_sys_paths:
            if path in sys.path:
                sys.path.remove(path)
                logger.debug(f"已从 sys.path 移除: {path}")
        self._custom_sys_paths.clear()

        # 记录当前 custom_root，用于下次清理
        self._last_custom_root = custom_root

        # 将custom_root的父目录添加到sys.path，以便模块可以使用绝对导入
        # 例如：from MPAcustom.action.tool.LoadSetting 需要 MPAcustom 的父目录在 sys.path 中
        # 同时也要添加custom_root本身，以便相对导入也能工作
        custom_root_parent = str(custom_root.parent)
        custom_root_str = str(custom_root)

        # 添加父目录到sys.path（用于绝对导入，如 from MPAcustom.xxx）
        if custom_root_parent not in sys.path:
            sys.path.insert(0, custom_root_parent)
            self._custom_sys_paths.append(custom_root_parent)
            logger.debug(f"已将父目录 {custom_root_parent} 添加到 sys.path")

        # 添加custom_root本身到sys.path（用于相对导入）
        if custom_root_str not in sys.path:
            sys.path.insert(0, custom_root_str)
            self._custom_sys_paths.append(custom_root_str)
            logger.debug(f"已将 {custom_root_str} 添加到 sys.path")

        def _get_bucket(type_name: str) -> str | None:
            return {"action": "actions", "recognition": "recognitions"}.get(type_name)

        def _record_success(type_name: str, name: str):
            bucket = _get_bucket(type_name)
            if bucket:
                self.custom_load_report[bucket]["success"].append(name)

        def _record_failure(
            type_name: str, name: str, reason: str, level: str = "warning"
        ):
            bucket = _get_bucket(type_name)
            if bucket:
                self.custom_load_report[bucket]["failed"].append(
                    {"name": name, "reason": reason, "level": level}
                )

        for custom_name, custom in custom_config.items():
            custom_type: str = (custom.get("type") or "").strip()
            custom_class_name: str = custom.get("class") or ""
            custom_file_path: str = custom.get("file_path") or ""

            if not all([custom_type, custom_name, custom_class_name, custom_file_path]):
                reason = f"配置项 {custom} 缺少必要信息，跳过"
                logger.warning(reason)
                _record_failure(custom_type, custom_name, reason)
                continue

            # 处理占位符与相对路径
            custom_file_path = custom_file_path.replace(
                "{custom_path}", str(custom_root)
            )
            custom_file_path = custom_file_path.replace(
                "{PROJECT_DIR}", str(project_dir)
            )
            if not os.path.isabs(custom_file_path):
                custom_file_path = os.path.join(custom_root, custom_file_path)
            custom_file_path = os.path.abspath(custom_file_path)

            if not os.path.isfile(custom_file_path):
                reason = f"自定义脚本 {custom_file_path} 不存在，跳过 {custom_name}"
                logger.warning(reason)
                _record_failure(custom_type, custom_name, reason)
                continue

            module_name = Path(custom_file_path).stem
            module_key = str(custom_file_path)

            # 如果该文件路径的模块已存在，先移除（可能是之前加载的）
            if module_key in sys.modules:
                logger.debug(f"移除已存在的模块缓存: {module_key}")
                del sys.modules[module_key]

            # 计算模块的包名，用于支持相对导入
            # 将 custom_root.name 作为包名，这样 from .action.Fishing 可以工作
            custom_root_name = custom_root.name
            try:
                file_path_obj = Path(custom_file_path).resolve()
                custom_root_obj = custom_root.resolve()
                if file_path_obj.is_relative_to(custom_root_obj):
                    relative_path = file_path_obj.relative_to(custom_root_obj)
                    if len(relative_path.parts) > 1:
                        # 文件在子目录中
                        package_parts = [custom_root_name] + list(
                            relative_path.parts[:-1]
                        )
                        package_name = ".".join(package_parts)
                    else:
                        # 文件在根目录
                        package_name = custom_root_name
                else:
                    package_name = custom_root_name
            except (ValueError, AttributeError):
                package_name = custom_root_name

            spec = importlib.util.spec_from_file_location(module_name, custom_file_path)
            if spec is None or spec.loader is None:
                reason = f"无法获取模块 {module_name} 的 spec，跳过加载"
                logger.error(reason)
                _record_failure(custom_type, custom_name, reason, level="error")
                continue

            try:
                module = importlib.util.module_from_spec(spec)
                # 设置 __package__ 以支持相对导入（from .action.Fishing）
                # 绝对导入（from action.Fishing）通过 sys.path 自动支持
                module.__package__ = package_name

                # 使用文件路径作为key存储到sys.modules，避免同名模块冲突
                sys.modules[module_key] = module
                spec.loader.exec_module(module)  # type: ignore[arg-type]

                class_obj = getattr(module, custom_class_name, None)
                if class_obj is None:
                    reason = f"模块 {module_name} 中未找到类 {custom_class_name}，跳过"
                    logger.error(reason)
                    _record_failure(custom_type, custom_name, reason, level="error")
                    continue
                instance = class_obj()
            except Exception as exc:
                # 使用 logger.exception 自动记录完整的堆栈信息
                reason = f"加载自定义对象 {custom_name} 失败: {exc}"
                logger.exception(f"加载自定义对象 {custom_name} 失败")
                _record_failure(custom_type, custom_name, reason, level="error")
                continue

            if custom_type == "action":
                if not isinstance(instance, CustomAction):
                    reason = f"{custom_name} 不是 CustomAction 子类，跳过"
                    logger.warning(reason)
                    _record_failure(custom_type, custom_name, reason)
                    continue
                if resource.register_custom_action(custom_name, instance):
                    loaded_any = True
                    _record_success(custom_type, custom_name)
                else:
                    reason = f"自定义动作 {custom_name} 注册失败"
                    logger.warning(reason)
                    _record_failure(custom_type, custom_name, reason)
            elif custom_type == "recognition":
                if not isinstance(instance, CustomRecognition):
                    reason = f"{custom_name} 不是 CustomRecognition 子类，跳过"
                    logger.warning(reason)
                    _record_failure(custom_type, custom_name, reason)
                    continue
                if resource.register_custom_recognition(custom_name, instance):
                    loaded_any = True
                    _record_success(custom_type, custom_name)
                else:
                    reason = f"自定义识别器 {custom_name} 注册失败"
                    logger.warning(reason)
                    _record_failure(custom_type, custom_name, reason)
            else:
                logger.warning(f"未知的自定义类型 {custom_type}，跳过 {custom_name}")

        actions_success = self.custom_load_report["actions"]["success"]
        recognitions_success = self.custom_load_report["recognitions"]["success"]
        actions_failed = self.custom_load_report["actions"]["failed"]
        recognitions_failed = self.custom_load_report["recognitions"]["failed"]

        if actions_success:
            logger.info(f"成功加载自定义动作: {', '.join(actions_success)}")
        if recognitions_success:
            logger.info(f"成功加载自定义识别器: {', '.join(recognitions_success)}")

        if actions_failed:
            for item in actions_failed:
                logger.warning(f"自定义动作 {item['name']} 加载失败: {item['reason']}")
        if recognitions_failed:
            for item in recognitions_failed:
                logger.warning(f"自定义识别器 {item['name']} 加载失败: {item['reason']}")

        return loaded_any

    @staticmethod
    @asyncify
    def detect_adb() -> List[AdbDevice]:
        return Toolkit.find_adb_devices()

    @staticmethod
    @asyncify
    def detect_win32hwnd(window_regex: str) -> List[DesktopWindow]:
        windows = Toolkit.find_desktop_windows()
        return [win for win in windows if re.search(window_regex, win.window_name)]

    @asyncify
    def connect_adb(
        self,
        adb_path: str,
        address: str,
        screencap_method: int = 0,
        input_method: int = 0,
        config: Dict = {},
    ) -> bool:
        screencap_method = MaaAdbScreencapMethodEnum(screencap_method)

        input_method = MaaAdbInputMethodEnum(input_method)

        controller = AdbController(
            adb_path, address, screencap_method, input_method, config
        )
        controller = self._init_controller(controller)
        connected = controller.post_connection().wait().succeeded
        if not connected:
            print(f"Failed to connect {adb_path} {address}")
            return False

        return True

    @asyncify
    def connect_win32hwnd(
        self,
        hwnd: int,
        screencap_method: int = MaaWin32ScreencapMethodEnum.DXGI_DesktopDup,
        mouse_method: int = MaaWin32InputMethodEnum.Seize,
        keyboard_method: int = MaaWin32InputMethodEnum.Seize,
    ) -> bool:
        screencap_method = (
            screencap_method or MaaWin32ScreencapMethodEnum.DXGI_DesktopDup
        )
        mouse_method = mouse_method or MaaWin32InputMethodEnum.Seize
        keyboard_method = keyboard_method or MaaWin32InputMethodEnum.Seize
        controller = Win32Controller(
            hwnd,
            screencap_method=screencap_method,
            mouse_method=mouse_method,
            keyboard_method=keyboard_method,
        )
        controller = self._init_controller(controller)

        connected = controller.post_connection().wait().succeeded
        if not connected:
            print(f"Failed to connect {hwnd}")
            return False

        return True

    @asyncify
    def connect_playcover(self, address: str, uuid: str) -> bool:
        controller = PlayCoverController(address, uuid)
        controller = self._init_controller(controller)
        connected = controller.post_connection().wait().succeeded
        if not connected:
            print(f"Failed to connect {address} {uuid}")
            return False
        return True

    @asyncify
    def connect_gamepad(
        self,
        hwnd: int,
        gamepad_type: int = MaaGamepadTypeEnum.Xbox360,
        screencap_method: int = MaaWin32ScreencapMethodEnum.DXGI_DesktopDup,
    ) -> bool:
        controller = GamepadController(hwnd, gamepad_type, screencap_method)
        controller = self._init_controller(controller)
        connected = controller.post_connection().wait().succeeded
        if not connected:
            print(f"Failed to connect {hwnd} {gamepad_type}")
            return False
        return True

    def _init_controller(
        self,
        controller: (
            AdbController | Win32Controller | PlayCoverController | GamepadController
        ),
    ) -> AdbController | Win32Controller | PlayCoverController | GamepadController:
        if self.maa_controller_sink:
            controller.add_sink(self.maa_controller_sink)
        self.controller = controller
        return self.controller

    def _init_resource(self) -> Resource:
        if self.resource is None:
            self.resource = Resource()
            if self.maa_resource_sink:
                self.resource.add_sink(self.maa_resource_sink)
        return self.resource

    def _init_tasker(self) -> Tasker:
        if self.tasker is None:
            self.tasker = Tasker()
            self.tasker.add_context_sink(self.maa_context_sink)

            if self.maa_tasker_sink:
                self.tasker.add_sink(self.maa_tasker_sink)
        if not self.resource or not self.controller:
            raise RuntimeError("Resource 与 Controller 必须先初始化再初始化 Tasker")
        self.tasker.bind(self.resource, self.controller)
        return self.tasker

    def _init_agent(self, agent_data_raw: dict) -> bool:
        if not (self.resource and self.controller):
            raise RuntimeError("agent 初始化前必须存在 resource/controller")
        if not self.tasker:
            self.tasker = self._init_tasker()
        if self.agent:
            return True

        self.agent = AgentClient()
        self.agent.register_sink(self.resource, self.controller, self.tasker)
        self.agent.bind(self.resource)

        if not agent_data_raw:
            logger.warning("未找到agent配置")
            self._send_custom_info(MaaFWError.AGENT_CONFIG_MISSING)
            return False

        if isinstance(agent_data_raw, list):
            if agent_data_raw:
                agent_data: dict = agent_data_raw[0]
            else:
                agent_data = {}
                logger.warning("agent 配置为一个空列表，使用空字典作为默认值")
                self._send_custom_info(MaaFWError.AGENT_CONFIG_EMPTY_LIST)
        elif isinstance(agent_data_raw, dict):
            agent_data = agent_data_raw
        else:
            agent_data = {}
            logger.warning("agent 配置既不是字典也不是列表，使用空字典作为默认值")
            self._send_custom_info(MaaFWError.AGENT_CONFIG_INVALID)

        child_exec = agent_data.get("child_exec", "")
        if not child_exec:
            logger.warning("agent 配置缺少 child_exec，无法启动")
            self._send_custom_info(MaaFWError.AGENT_CHILD_EXEC_MISSING)
            return False

        socket_id = self.agent.identifier
        if callable(socket_id):
            socket_id = socket_id() or "maafw_socket_id"
        elif socket_id is None:
            socket_id = "maafw_socket_id"
        socket_id = str(socket_id)
        child_args = agent_data.get("child_args", [])
        project_dir = Path.cwd()
        child_args = [
            arg.replace("{PROJECT_DIR}", str(project_dir)) for arg in child_args
        ]
        agent_process: subprocess.Popen | None = None
        start_cmd = [child_exec, *child_args, socket_id]
        logger.debug(f"启动agent命令: {start_cmd}")
        # 如果是打包模式,使用utf8,否则使用gbk
        import os
        import sys

        # 使用 sys.frozen 判断是否打包（PyInstaller 标准方式）
        is_packed = getattr(sys, "frozen", False)
        encoding = "utf-8" if is_packed else "gbk"
        try:
            agent_process = subprocess.Popen(
                start_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding=encoding,
                errors="replace",
                bufsize=1,
            )
            self.agent_thread = agent_process
            self._watch_agent_output(agent_process)
        except Exception as e:
            logger.error(f"启动agent失败: {e}")
            self._send_custom_info(MaaFWError.AGENT_START_FAILED)
            return False
        if self.agent_data_raw and self.agent_data_raw.get("timeout"):
            timeout = self.agent_data_raw.get("timeout")
            self.agent.set_timeout(timeout)
        if not self.agent.connect():
            self._send_custom_info(MaaFWError.AGENT_CONNECTION_FAILED)
            return False
        return True

    @asyncify
    def load_resource(self, dir: str | Path, gpu_index: int = -1) -> bool:
        resource = self._init_resource()
        if not isinstance(gpu_index, int):
            logger.warning("gpu_index 不是 int 类型，使用默认值 -1")
            gpu_index = -1
        if gpu_index == -2:
            logger.debug("设置CPU推理")
            resource.use_cpu()
        elif gpu_index == -1:
            logger.debug("设置自动")
            resource.use_auto_ep()
        else:
            logger.debug(f"设置GPU推理: {gpu_index}")
            resource.use_directml(gpu_index)
        return resource.post_bundle(dir).wait().succeeded

    @asyncify
    def run_task(
        self,
        entry: str,
        pipeline_override: dict = {},
        save_draw: bool = False,
    ) -> bool:
        if not self.resource or not self.controller:
            self._send_custom_info(MaaFWError.RESOURCE_OR_CONTROLLER_NOT_INITIALIZED)
            return False

        tasker = self._init_tasker()

        if self.agent_data_raw:
            if not self._init_agent(self.agent_data_raw):
                return False
        if not tasker.inited:
            self._send_custom_info(MaaFWError.TASKER_NOT_INITIALIZED)
            return False
        tasker.set_save_draw(save_draw)
        return tasker.post_task(entry, pipeline_override).wait().succeeded

    @asyncify
    def stop_task(self):
        if self.tasker:
            try:
                self.tasker.post_stop().wait()
            except Exception as e:
                logger.error(f"停止任务失败: {e}")
            finally:
                self.tasker = None
            self.tasker = None
        if self.resource:
            try:
                self.resource.clear()
            except Exception as e:
                logger.error(f"清除资源失败: {e}")
            finally:
                self.resource = None
            self.resource = None
        if self.agent:
            try:
                self.agent.disconnect()
                self.agent_data_raw = None
            except Exception as e:
                logger.error(f"断开agent连接失败: {e}")
            finally:
                self.agent = None
            self.agent = None
        if self.agent_thread:
            try:
                self.agent_thread.terminate()
                try:
                    self.agent_thread.wait(timeout=self.AGENT_TERMINATE_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    logger.warning("等待 agent 终止超时，执行 kill 操作")
                    self.agent_thread.kill()
            except Exception as e:
                logger.error(f"终止agent线程失败: {e}")
            finally:
                self.agent_thread = None
        if self.agent_output_thread:
            self.agent_output_thread.join(timeout=0.1)
            self.agent_output_thread = None

    def _send_custom_info(self, error: MaaFWError):
        self.custom_info.emit(error.value)

    def _watch_agent_output(self, process: subprocess.Popen):
        def _forward_output():
            stream = process.stdout
            if not stream:
                return
            for line in stream:
                text = line.rstrip("\r\n")
                if text:
                    self.agent_info.emit(text)
            stream.close()

        watcher = threading.Thread(target=_forward_output, daemon=True)
        watcher.start()
        self.agent_output_thread = watcher

    async def screencap_test(self) -> numpy.ndarray:
        if not self.controller:
            raise RuntimeError("Controller not initialized")
        return self.controller.post_screencap().wait().get()
