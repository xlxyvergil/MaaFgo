import asyncio
import calendar
import io
import os
import platform
import re
import shlex
import subprocess
import sys
import time as _time

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict
from PySide6.QtCore import QCoreApplication, QObject, QTimer
from app.common.constants import (
    POST_ACTION,
    _CONTROLLER_,
    _RESOURCE_,
)
from app.common.signal_bus import signalBus
from app.common.config import cfg

from maa.toolkit import Toolkit
from maa.define import (
    MaaWin32ScreencapMethodEnum,
    MaaWin32InputMethodEnum,
    MaaAdbInputMethodEnum,
    MaaAdbScreencapMethodEnum,
)
from app.utils.notice import NoticeTiming, send_notice, send_thread

from app.utils.logger import logger
from app.core.service.Config_Service import ConfigService
from app.core.service.Task_Service import TaskService
from app.core.runner.maafw import (
    MaaFW,
    MaaFWError,
    maa_context_sink,
    maa_controller_sink,
    maa_resource_sink,
    maa_tasker_sink,
)
from app.utils.controller_utils import ControllerHelper

from app.core.Item import FromeServiceCoordinator, TaskItem


def _ndarray_to_png_bytes(ndarray) -> bytes | None:
    """将 BGR 格式的 numpy 截图转为 PNG 字节（用于随通知发送）。"""
    try:
        from PIL import Image

        # 控制器返回的通常是 BGR，转为 RGB
        if (
            hasattr(ndarray, "shape")
            and len(ndarray.shape) >= 3
            and ndarray.shape[2] >= 3
        ):
            pil = Image.fromarray(ndarray[..., ::-1])
        else:
            pil = Image.fromarray(ndarray)
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


class TaskFlowRunner(QObject):
    """负责执行任务流的运行时组件"""

    def __init__(
        self,
        task_service: TaskService,
        config_service: ConfigService,
        fs_signal_bus: FromeServiceCoordinator | None = None,
    ):
        super().__init__()
        self.task_service = task_service
        self.config_service = config_service
        # 提供给主窗口退出清理使用：停止外部通知线程
        # 注意：send_thread 定义于 app.utils.notice，为全局单例
        self.send_thread = send_thread
        if fs_signal_bus:
            self.maafw = MaaFW(
                maa_context_sink=maa_context_sink,
                maa_controller_sink=maa_controller_sink,
                maa_resource_sink=maa_resource_sink,
                maa_tasker_sink=maa_tasker_sink,
            )
            self.fs_signal_bus = fs_signal_bus
        else:
            self.maafw = MaaFW()
            self.fs_signal_bus = None
        self.maafw.custom_info.connect(self._handle_maafw_custom_info)
        self.maafw.agent_info.connect(self._handle_agent_info)
        self.process = None

        self.need_stop = False
        self.monitor_need_stop = False
        self._is_running = False
        # 防止同一次任务流退出时重复发射“结束”信号（幂等保护）
        self._task_flow_finished_emitted: bool = False
        self._next_config_to_run: str | None = None
        self.adb_controller_raw: dict[str, Any] | None = None
        self.adb_activate_controller: str | None = None
        self.adb_controller_config: dict[str, Any] | None = None
        self._config_switch_delay = 0.5

        # bundle 相关：在任务流开始时根据当前配置初始化
        self.bundle_path: str = "./"

        # 默认 pipeline_override（来自 Resource 任务）
        self._default_pipeline_override: Dict[str, Any] = {}

        # 任务超时相关
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(False)  # 改为周期性定时器，每小时触发一次
        self._timeout_timer.timeout.connect(self._on_task_timeout)
        self._timeout_active_entry = ""
        # 当前正在执行的任务ID，用于超时处理
        self._current_running_task_id: str | None = None
        # 任务开始时间：task_id -> 开始时间戳
        self._task_start_times: dict[str, float] = {}
        # 是否处于单任务模式（单任务模式下不进行长期任务检查）
        self._is_single_task_mode: bool = False
        # 标记是否为"手动停止"，用于控制是否发送完成通知
        self._manual_stop = False
        # 任务结果摘要：task_id -> 状态字符串（running/completed/failed/waiting/skipped等）
        self._task_results: dict[str, str] = {}
        # 任务运行状态标记：每个任务开始前置为 True，收到 abort 信号时置为 False
        self._current_task_ok: bool = True
        # 日志收集列表：用于收集任务运行过程中的日志，供超时通知使用
        self._log_messages: list[tuple[str, str, str]] = []  # (level, text, timestamp)

        # 监听 MaaFW 回调信号，用于接收 abort 等特殊事件
        signalBus.callback.connect(self._handle_maafw_callback)

        # 连接前置检查失败原因（用于在上层发送更明确的通知文案）
        self._connect_error_reason: str | None = None

    def _is_admin_runtime(self) -> bool:
        """运行时检测是否具备管理员权限（优先用 cfg 标记，失败则在 Windows 上兜底检测）。"""
        try:
            is_admin = bool(cfg.get(cfg.is_admin))
        except Exception:
            is_admin = False

        if is_admin:
            return True

        if sys.platform.startswith("win32"):
            try:
                import ctypes

                is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
                try:
                    cfg.set(cfg.is_admin, is_admin)
                except Exception:
                    pass
                return is_admin
            except Exception:
                return False

        return False

    def _handle_maafw_callback(self, payload: Dict[str, Any]):
        """处理来自 MaaFW 的通用回调信号（包括自定义的 abort 信号）。

        当前实现只负责更新内部状态变量，不直接控制任务流转。
        """
        try:
            name = payload.get("name", "")
            if name == "abort":
                # 收到 abort 信号：仅标记当前任务状态，等待 run_task 完成后再由调用方判断
                self._current_task_ok = False
        except Exception as exc:
            logger.warning(f"处理 MaaFW 回调信号时出错: {exc}")

    def _handle_agent_info(self, info: str):
        if "| WARNING |" in info:
            # 从warning开始截断
            info = info.split("| WARNING |")[1]
            signalBus.log_output.emit("WARNING", info)
        elif "| ERROR |" in info:
            # 从error开始截断
            info = info.split("| ERROR |")[1]
            signalBus.log_output.emit("ERROR", info)
        elif "| INFO |" in info:
            # 从info开始截断
            info = info.split("| INFO |")[1]
            signalBus.log_output.emit("INFO", info)

    def _handle_maafw_custom_info(self, error_code: int):
        try:
            error = MaaFWError(error_code)
            match error:
                case MaaFWError.RESOURCE_OR_CONTROLLER_NOT_INITIALIZED:
                    msg = self.tr("Resource or controller not initialized")
                case MaaFWError.AGENT_CONNECTION_FAILED:
                    msg = self.tr("Agent connection failed")
                case MaaFWError.TASKER_NOT_INITIALIZED:
                    msg = self.tr("Tasker not initialized")
                case _:
                    msg = self.tr("Unknown MaaFW error code: {}").format(error_code)
            signalBus.log_output.emit("ERROR", msg)
        except ValueError:
            logger.warning(f"Received unknown MaaFW error code: {error_code}")
            signalBus.log_output.emit(
                "WARNING", self.tr("Unknown MaaFW error code: {}").format(error_code)
            )

    async def _get_notice_screenshot_bytes(self) -> bytes | None:
        """若设置中开启「随通知发送截图」且控制器可用，则截屏并返回 PNG 字节，否则返回 None。"""
        if not cfg.get(cfg.notice_send_screenshot):
            return None
        if not getattr(self, "maafw", None) or not getattr(
            self.maafw, "controller", None
        ):
            return None
        try:
            img = await self.maafw.screencap_test()
            if img is not None:
                return _ndarray_to_png_bytes(img)
        except Exception:
            pass
        return None

    async def run_tasks_flow(
        self,
        task_id: str | None = None,
        *,
        start_task_id: str | None = None,
    ):
        """任务完整流程：连接设备、加载资源、批量运行任务

        :param start_task_id: 可选，指定从某个任务开始执行，其前面的任务会被跳过。
        """
        if self._is_running:
            logger.warning("任务流已经在运行，忽略新的启动请求")
            return
        self._is_running = True
        self.need_stop = False
        self._manual_stop = False
        self._task_flow_finished_emitted = False
        # 清空任务开始时间记录
        self._task_start_times.clear()
        # 跟踪任务流是否成功启动并执行了任务
        self._tasks_started = False
        # 重置本次任务流的结果摘要
        self._task_results.clear()

        # 发送任务流启动通知
        send_notice(
            NoticeTiming.WHEN_FLOW_STARTED,
            self.tr("Task Flow Started"),
            self.tr("Task flow has been started."),
        )
        # 重置超时状态
        self._reset_task_timeout_state()
        is_single_task_mode = task_id is not None
        self._is_single_task_mode = is_single_task_mode
        effective_start_task_id = None
        if not is_single_task_mode and start_task_id:
            current_tasks = self.task_service.current_tasks
            for task in current_tasks:
                if task.item_id == start_task_id:
                    effective_start_task_id = start_task_id
                    break
            if effective_start_task_id is None:
                logger.warning(
                    "未找到起始任务 '%s'，将从头开始执行任务序列", start_task_id
                )
        else:
            effective_start_task_id = None

        # 注意：is_hidden 由配置层（TaskService/Coordinator/UI）负责刷新；
        # runner 仅消费 is_checked/is_hidden 来执行任务流

        # 初始化任务状态：仅在完整运行时将所有选中的任务设置为等待中
        # 单独运行时，只会在对应的任务处显示进行中/完成/失败，不显示等待图标
        # 使用 QTimer 延迟发送，确保任务列表 UI 已经准备好
        def set_waiting_status():
            # 只在完整运行模式（非单任务模式）时设置等待状态
            if not is_single_task_mode:
                all_tasks = self.task_service.get_tasks()
                start_reached = effective_start_task_id is None
                for task in all_tasks:
                    if effective_start_task_id and not start_reached:
                        if task.item_id == effective_start_task_id:
                            start_reached = True
                        else:
                            continue

                    if (
                        not task.is_base_task()
                        and task.is_checked
                        and not task.is_hidden
                    ):
                        # 完整运行时，设置当前起始任务及之后的选中任务为等待中
                        signalBus.task_status_changed.emit(task.item_id, "waiting")

        # 延迟 200ms 发送，确保任务列表已经渲染完成
        QTimer.singleShot(200, set_waiting_status)

        # 初始化日志收集列表
        self._log_messages.clear()

        def collect_log(level: str, text: str):
            """收集日志信息（包含收到的时间戳）"""
            timestamp = datetime.now().strftime("%H:%M:%S")
            self._log_messages.append((level, text, timestamp))

        # 连接日志输出信号
        signalBus.log_output.connect(collect_log)
        current_config = self.config_service.get_config(
            self.config_service.current_config_id
        )
        if not current_config:
            # 保持 bundle_path 的安全默认值
            self.bundle_path = "./"
        else:
            self.bundle_path = self.config_service.get_bundle_path_for_config(
                current_config
            )
        try:
            if self.fs_signal_bus:
                self.fs_signal_bus.fs_start_button_status.emit(
                    {"text": "STOP", "status": "disabled"}
                )
            controller_cfg = self.task_service.get_task(_CONTROLLER_)
            if not controller_cfg:
                raise ValueError("未找到基础预配置任务")
            resource_cfg = self.task_service.get_task(_RESOURCE_)
            if not resource_cfg:
                raise ValueError("未找到资源设置任务")

            # 先加载资源，再连接控制器
            logger.info("开始加载资源...")
            signalBus.log_output.emit("INFO", self.tr("Starting to load resources..."))
            if not await self.load_resources(resource_cfg.task_option):
                logger.error("资源加载失败")
                return
            logger.info("资源加载完成")

            # 构建默认 pipeline_override
            # 合并优先级（从低到高）：global_option < resource.option < controller.option
            # 任务级 override 在 run_task 中合并（最高优先级）
            from app.core.utils.pipeline_helper import (
                get_pipeline_override_from_task_option,
                get_controller_option_pipeline_override,
                _deep_merge_dict,
            )

            # 1. global_option + resource.option（已在函数内按优先级合并）
            self._default_pipeline_override = get_pipeline_override_from_task_option(
                self.task_service.interface, resource_cfg.task_option, _RESOURCE_
            )

            # 2. controller.option（优先级高于 resource.option 和 global_option）
            controller_override = get_controller_option_pipeline_override(
                self.task_service.interface, controller_cfg.task_option
            )
            if controller_override:
                _deep_merge_dict(self._default_pipeline_override, controller_override)

            if self.task_service.interface.get("agent", None):
                self.maafw.agent_data_raw = self.task_service.interface.get(
                    "agent", None
                )
                signalBus.log_output.emit("INFO", self.tr("Agent Service Start"))

            if self.task_service.interface.get("custom", None) and self.maafw.resource:
                signalBus.log_output.emit(
                    "INFO", self.tr("Starting to load custom components...")
                )
                self.maafw.resource.clear_custom_recognition()
                self.maafw.resource.clear_custom_action()

                # 兼容绝对路径与相对 bundle.path 的自定义配置路径
                custom_config_path = self.task_service.interface.get("custom", "")
                if custom_config_path:
                    bundle_path_str = self.bundle_path or "./"
                    base_dir = Path(bundle_path_str)
                    if not base_dir.is_absolute():
                        base_dir = (Path.cwd() / base_dir).resolve()

                    # 先处理占位符与前导分隔符
                    raw_custom = str(custom_config_path).replace("{PROJECT_DIR}", "")
                    normalized_custom = raw_custom.lstrip("\\/")
                    custom_path_obj = Path(normalized_custom)

                    # 绝对路径：直接使用，保持兼容已有配置
                    if custom_path_obj.is_absolute():
                        custom_config_path = custom_path_obj
                    else:
                        # 相对路径：视为相对 bundle.path 的路径
                        custom_config_path = (base_dir / normalized_custom).resolve()

                result = self.maafw.load_custom_objects(
                    custom_config_path=custom_config_path
                )
                if not result:
                    failed_actions = self.maafw.custom_load_report["actions"]["failed"]
                    failed_recogs = self.maafw.custom_load_report["recognitions"][
                        "failed"
                    ]
                    detail_parts = [
                        f"动作 {item.get('name', '')}: {item.get('reason', '')}"
                        for item in failed_actions
                    ] + [
                        f"识别器 {item.get('name', '')}: {item.get('reason', '')}"
                        for item in failed_recogs
                    ]
                    detail_msg = (
                        "；".join([part for part in detail_parts if part]) or "未知原因"
                    )

                    logger.error(f"自定义组件加载失败，流程终止: {detail_msg}")
                    signalBus.log_output.emit(
                        "ERROR",
                        self.tr(
                            "Custom components loading failed, the flow is terminated: "
                        )
                        + detail_msg,
                    )
                    signalBus.log_output.emit(
                        "ERROR", self.tr("please try to reset resource in setting")
                    )
                    await self.stop_task()
                    return
            # 资源加载完成后连接控制器
            logger.info("开始连接设备...")
            signalBus.log_output.emit("INFO", self.tr("Starting to connect device..."))
            self._connect_error_reason = None
            resource_target = (
                resource_cfg.task_option.get("resource")
                if resource_cfg
                and isinstance(getattr(resource_cfg, "task_option", None), dict)
                else None
            )
            connected = await self.connect_device(
                controller_cfg.task_option, resource_target=resource_target
            )
            if not connected:
                logger.error("设备连接失败")
                send_notice(
                    NoticeTiming.WHEN_CONNECT_FAILED,
                    self.tr("Device Connection Failed"),
                    self._connect_error_reason
                    or self.tr("Failed to connect to the device."),
                )
                return
            signalBus.log_output.emit("INFO", self.tr("Device connected successfully"))
            logger.info("设备连接成功")
            image_bytes = await self._get_notice_screenshot_bytes()
            send_notice(
                NoticeTiming.WHEN_CONNECT_SUCCESS,
                self.tr("Device Connected Successfully"),
                self.tr("Device has been connected successfully."),
                image_bytes=image_bytes,
            )
            start_time = _time.time()
            await self.maafw.screencap_test()
            end_time = _time.time()
            signalBus.callback.emit(
                {"name": "speed_test", "details": end_time - start_time}
            )
            tasks_to_run = self._collect_tasks_to_run(
                task_id=task_id,
                effective_start_task_id=effective_start_task_id,
                is_single_task_mode=is_single_task_mode,
            )
            if not tasks_to_run:
                return
            if is_single_task_mode:
                logger.debug(f"开始执行单个任务: {task_id}")
            self._tasks_started = True
            for task in tasks_to_run:
                if not task:
                    continue
                # 每个任务开始前，假定其可以正常完成
                self._current_task_ok = True
                # 记录当前正在执行的任务，用于超时处理
                self._current_running_task_id = task.item_id
                # 发送任务运行中状态
                signalBus.task_status_changed.emit(task.item_id, "running")
                try:
                    task_result = await self.run_task(
                        task.item_id,
                        skip_speedrun=is_single_task_mode,
                    )
                    if task_result == "skipped":
                        # 因 speedrun 限制被跳过：记录结果并在列表中显示为“已跳过”
                        self._task_results[task.item_id] = "skipped"
                        signalBus.task_status_changed.emit(task.item_id, "skipped")
                        continue
                    # 如果任务显式返回 False，视为致命失败，终止整个任务流
                    if task_result is False:
                        msg = f"任务执行失败: {task.name}, 返回 False，终止流程"
                        logger.error(msg)
                        # 记录任务结果
                        self._task_results[task.item_id] = "failed"
                        # 发送任务失败状态
                        signalBus.task_status_changed.emit(task.item_id, "failed")
                        # 发送任务失败通知
                        if not self._manual_stop:
                            image_bytes = await self._get_notice_screenshot_bytes()
                            send_notice(
                                NoticeTiming.WHEN_TASK_FAILED,
                                self.tr("Task Failed"),
                                self.tr(
                                    "Task '{}' failed and the flow was terminated."
                                ).format(task.name),
                                image_bytes=image_bytes,
                            )
                        await self.stop_task()
                        break

                    # 任务运行过程中如果触发了 abort 信号，则认为该任务未成功完成，
                    # 但不中断整个任务流，直接切换到下一个任务。
                    if not self._current_task_ok:
                        logger.warning(
                            f"任务执行被中途中止(abort): {task.name}，切换到下一个任务"
                        )
                        # 记录任务结果并发送任务失败状态
                        self._task_results[task.item_id] = "failed"
                        signalBus.task_status_changed.emit(task.item_id, "failed")
                        # 发送任务失败通知
                        if not self._manual_stop:
                            image_bytes = await self._get_notice_screenshot_bytes()
                            send_notice(
                                NoticeTiming.WHEN_TASK_FAILED,
                                self.tr("Task Failed"),
                                self.tr("Task '{}' was aborted.").format(task.name),
                                image_bytes=image_bytes,
                            )
                    else:
                        # 记录任务结果
                        status = "completed"
                        self._task_results[task.item_id] = status
                        signalBus.task_status_changed.emit(task.item_id, status)
                        # 发送任务成功通知
                        image_bytes = await self._get_notice_screenshot_bytes()
                        send_notice(
                            NoticeTiming.WHEN_TASK_SUCCESS,
                            self.tr("Task Completed"),
                            self.tr(
                                "Task '{}' has been completed successfully."
                            ).format(task.name),
                            image_bytes=image_bytes,
                        )

                except Exception as exc:
                    logger.error(f"任务执行失败: {task.name}, 错误: {str(exc)}")
                    # 发送任务失败状态
                    signalBus.task_status_changed.emit(task.item_id, "failed")
                    # 发送任务失败通知
                    if not self._manual_stop:
                        image_bytes = await self._get_notice_screenshot_bytes()
                        send_notice(
                            NoticeTiming.WHEN_TASK_FAILED,
                            self.tr("Task Failed"),
                            self.tr("Task '{}' failed with error: {}").format(
                                task.name, str(exc)
                            ),
                            image_bytes=image_bytes,
                        )

                # 清除当前执行任务记录
                self._current_running_task_id = None

                if self.need_stop:
                    logger.debug("收到停止请求，流程终止")
                    break

            # 只有在任务流正常完成（非手动停止）时才输出"所有任务都已完成"
            if self._is_tasks_flow_completed_normally():
                signalBus.log_output.emit(
                    "INFO", self.tr("All tasks have been completed")
                )

        except Exception as exc:
            logger.error(f"任务流程执行异常: {str(exc)}")
            signalBus.log_output.emit("ERROR", self.tr("Task flow error: ") + str(exc))
            import traceback

            logger.critical(traceback.format_exc())
        finally:
            # 任务流退出信号：放在 finally 的最前面，确保监控等 UI 可以立即响应停止，
            # 不会被“完成后操作/清理”等耗时逻辑拖慢。
            if not self._task_flow_finished_emitted:
                self._task_flow_finished_emitted = True
                try:
                    signalBus.task_flow_finished.emit(
                        {
                            "manual_stop": bool(self._manual_stop),
                            "need_stop": bool(self.need_stop),
                            "single_task_mode": bool(is_single_task_mode),
                            "tasks_started": bool(self._tasks_started),
                        }
                    )
                except Exception as exc:
                    # UI 信号不应影响任务流清理流程
                    logger.debug(f"发射 task_flow_finished 信号失败（忽略）: {exc}")

            # 先发送任务完成通知（在完成后操作之前，以便退出软件时可以等待通知发送完成）
            # 断开日志收集信号
            signalBus.log_output.disconnect(collect_log)

            # 发送收集的日志信息（仅在非手动停止时发送）
            # 注意：这里检查 _manual_stop 标志，如果为 True 则不发送通知
            if not self._manual_stop and self._log_messages:
                # 将日志信息格式化为文本（包含收到的时间戳）
                # 格式：[时间][日志等级]日志内容
                log_text_lines: list[str] = []
                for log_item in self._log_messages:
                    if len(log_item) == 3:
                        level, text, timestamp = log_item
                    else:
                        # 兼容旧格式（没有时间戳的情况）
                        level, text = log_item[:2]  # type: ignore[misc]
                        timestamp = datetime.now().strftime("%H:%M:%S")
                    # 翻译日志级别
                    translated_level = self._translate_log_level(level)
                    log_text_lines.append(f"[{timestamp}][{translated_level}]{text}")

                log_text = "\n".join(log_text_lines)

                if log_text and not is_single_task_mode:
                    # 注意：外部通知不应阻断后续完成后操作（关闭控制器/关机/退出等）
                    try:
                        image_bytes = await self._get_notice_screenshot_bytes()
                        send_notice(
                            NoticeTiming.WHEN_POST_TASK,
                            self.tr("Task Flow Completed"),
                            log_text,
                            image_bytes=image_bytes,
                        )
                    except Exception as exc:
                        logger.warning(
                            "发送任务流完成通知失败（忽略并继续完成后操作）: %s", exc
                        )

            # 判断是否需要执行完成后操作
            # - 默认：只有任务流未被 stop_task() 标记（need_stop=False）时才执行
            # - 若完成后操作配置启用 always_run：即使流程因“非手动停止”的失败而触发 stop_task()，也会执行完成后操作
            always_run_post_action = False
            try:
                post_task = self.task_service.get_task(POST_ACTION)
                post_cfg = (
                    post_task.task_option.get("post_action") if post_task else None
                )
                if isinstance(post_cfg, dict):
                    always_run_post_action = bool(post_cfg.get("always_run"))
            except Exception:
                always_run_post_action = False

            should_run_post_action = (
                not is_single_task_mode
                and self._tasks_started
                and not self._manual_stop
                and (not self.need_stop or always_run_post_action)
            )
            try:
                if should_run_post_action:
                    await self._handle_post_action()
            except Exception as exc:
                logger.error(f"完成后操作执行失败: {exc}")

            # 在调用 stop_task 之前保存 _manual_stop 标志，避免被覆盖
            # 因为 stop_task 可能会在 finally 块中被调用，但我们需要保留手动停止的状态
            was_manual_stop = self._manual_stop

            # 在 finally 块中调用 stop_task
            # 如果 _manual_stop 已经是 True，说明是手动停止，stop_task 会直接返回（因为 need_stop 已经是 True）
            # 如果 _manual_stop 是 False，说明是正常完成或异常退出，调用 stop_task 时也不设置 manual
            await self.stop_task()

            # 恢复 _manual_stop 标志（防止 stop_task 中的逻辑意外修改）
            self._manual_stop = was_manual_stop

            self._is_running = False

            # 清除所有任务状态
            all_tasks = self.task_service.get_tasks()
            for task in all_tasks:
                if not task.is_base_task():
                    signalBus.task_status_changed.emit(task.item_id, "")

            next_config = self._next_config_to_run
            self._next_config_to_run = None
            if next_config:
                logger.info(
                    "完成后自动启动配置: %s（等待 %.2f 秒）",
                    next_config,
                    self._config_switch_delay,
                )
                await asyncio.sleep(self._config_switch_delay)
                asyncio.create_task(self.run_tasks_flow())

    def _collect_tasks_to_run(
        self,
        task_id: str | None,
        effective_start_task_id: str | None,
        is_single_task_mode: bool,
    ) -> list[TaskItem]:
        """构建本次任务流要执行的任务列表"""
        tasks: list[TaskItem] = []
        if is_single_task_mode:
            if not task_id:
                return tasks
            task = self.task_service.get_task(task_id)
            if not task:
                logger.error(f"任务 ID '{task_id}' 不存在")
                return tasks
            # 执行层只关心“任务是否被禁用”，不展开禁用原因
            if self._is_task_disabled(task):
                return tasks
            if not task.is_checked:
                return tasks
            tasks.append(task)
            return tasks

        start_reached = effective_start_task_id is None
        for task in self.task_service.current_tasks:
            if effective_start_task_id and not start_reached:
                if task.item_id == effective_start_task_id:
                    start_reached = True
                else:
                    continue

            if task.name in [_CONTROLLER_, _RESOURCE_, POST_ACTION]:
                continue

            if not task.is_checked or task.is_special:
                continue

            if self._is_task_disabled(task):
                continue

            tasks.append(task)

        return tasks

    def _translate_log_level(self, level: str) -> str:
        """翻译日志级别"""
        level_upper = (level or "").upper()
        level_map = {
            "INFO": self.tr("INFO"),
            "WARNING": self.tr("WARNING"),
            "ERROR": self.tr("ERROR"),
            "CRITICAL": self.tr("CRITICAL"),
        }
        return level_map.get(level_upper, level)

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def connect_device(
        self,
        controller_raw: Dict[str, Any],
        resource_target: str | None = None,
    ):
        """连接 MaaFW 控制器"""
        # 连接前置检查：若控制器需要管理员权限但当前不是管理员，则直接中止
        self._connect_error_reason = None
        controller_name = ""
        try:
            controller_type_raw = controller_raw.get("controller_type")
            if isinstance(controller_type_raw, str):
                controller_name = controller_type_raw.strip()
            elif isinstance(controller_type_raw, dict):
                controller_name = str(
                    controller_type_raw.get("value", "") or ""
                ).strip()
        except Exception:
            controller_name = ""

        if not controller_name:
            msg = self.tr(
                "Controller name is empty, please configure controller in settings"
            )
            self._connect_error_reason = msg
            logger.error("控制器名称为空，无法连接设备")
            signalBus.log_output.emit("ERROR", msg)
            try:
                await self.stop_task()
            except Exception:
                pass
            return False

        interface_controller = self._get_interface_controller_entry(controller_name)
        if not interface_controller:
            msg = self.tr(
                "Controller '{}' not found, please reset controller in settings"
            ).format(controller_name)
            self._connect_error_reason = msg
            logger.error(f"未找到控制器名称: {controller_name}")
            signalBus.log_output.emit("ERROR", msg)
            try:
                await self.stop_task()
            except Exception:
                pass
            return False

        controller_name = interface_controller.get("name", controller_name)

        # interface 中控制器可带 option，作为默认项与 task_option 合并（task 覆盖 interface）
        # option 可含 resource/controller 列表：仅当当前 resource/controller 在列表中时才应用（为空则全部显示）
        if isinstance(controller_name, str) and controller_name:
            for ctrl in (self.task_service.interface or {}).get("controller", []):
                if not isinstance(ctrl, dict) or ctrl.get("name") != controller_name:
                    continue
                opt = ctrl.get("option")
                if not isinstance(opt, dict):
                    break
                allow_res = opt.get("resource")
                allow_ctrl = opt.get("controller")
                if (
                    isinstance(allow_res, list)
                    and len(allow_res)
                    and resource_target not in allow_res
                ):
                    break
                if (
                    isinstance(allow_ctrl, list)
                    and len(allow_ctrl)
                    and controller_name not in allow_ctrl
                ):
                    break
                # 合并时排除 option 的 resource/controller 标记字段，不写入实际配置
                opt_effective = {
                    k: v for k, v in opt.items() if k not in ("resource", "controller")
                }
                controller_raw = {**opt_effective, **controller_raw}
                break

        # 首选：从“控制器子配置”读取（例如 controller_raw["Win32控制器"]["permission_required"]）
        permission_required = None
        display_short_side = None
        display_long_side = None
        display_raw = None
        if isinstance(controller_name, str) and controller_name:
            try:
                controller_cfg = controller_raw.get(controller_name)
                if isinstance(controller_cfg, dict):
                    permission_required = controller_cfg.get("permission_required")
                    display_short_side = controller_cfg.get("display_short_side")
                    display_long_side = controller_cfg.get("display_long_side")
                    display_raw = controller_cfg.get("display_raw")
            except Exception:
                permission_required = None

        # 兼容：如果配置里还没保存 permission_required（例如用户重启后未进入控制器设置界面）
        # 则从 interface.json 中按 controller_type 反查
        if permission_required is None:
            if isinstance(controller_name, str) and controller_name:
                try:
                    for ctrl in (self.task_service.interface or {}).get(
                        "controller", []
                    ):
                        if not isinstance(ctrl, dict):
                            continue
                        if ctrl.get("name") == controller_name:
                            permission_required = ctrl.get("permission_required")
                            display_short_side = ctrl.get("display_short_side")
                            display_long_side = ctrl.get("display_long_side")
                            display_raw = ctrl.get("display_raw")
                            break
                except Exception:
                    permission_required = None
                    display_short_side = None
                    display_long_side = None
                    display_raw = None

        if permission_required is True and (not self._is_admin_runtime()):
            msg = self.tr("this Controller requires admin permission to run")
            self._connect_error_reason = msg
            logger.error(msg)
            signalBus.log_output.emit("ERROR", msg)
            # 立即停止任务流（而不是等待上层 finally）
            try:
                await self.stop_task()
            except Exception:
                pass
            return False

        try:
            controller_type = self._get_controller_type(controller_raw)
        except (TypeError, ValueError) as exc:
            msg = self.tr(
                "Controller configuration is invalid, please reset controller in settings"
            )
            self._connect_error_reason = msg
            logger.error(f"控制器配置无效: {exc}")
            signalBus.log_output.emit("ERROR", msg)
            try:
                await self.stop_task()
            except Exception:
                pass
            return False
        if self.fs_signal_bus:
            self.fs_signal_bus.fs_start_button_status.emit(
                {"text": "STOP", "status": "enabled"}
            )
        if controller_type == "adb":
            controller = await self._connect_adb_controller(controller_raw)
        elif controller_type == "win32":
            controller = await self._connect_win32_controller(controller_raw)
        elif controller_type == "gamepad":
            controller = await self._connect_gamepad_controller(controller_raw)
        elif controller_type == "playcover":
            controller = await self._connect_playcover_controller(controller_raw)
        else:
            raise ValueError("不支持的控制器类型")

        if not controller or not self.maafw.controller:
            return False

        if display_short_side or display_long_side:
            if display_short_side:
                self.maafw.controller.set_screenshot_target_short_side(
                    display_short_side
                )
                logger.debug(f"设置控制器分辨率: 短边 {display_short_side}")
            if display_long_side:
                self.maafw.controller.set_screenshot_target_long_side(display_long_side)
                logger.debug(f"设置控制器分辨率: 长边 {display_long_side}")
        elif display_raw:
            self.maafw.controller.set_screenshot_use_raw_size(display_raw)
            logger.debug("设置控制器分辨率: 原始大小")
        return True

    async def load_resources(self, resource_raw: Dict[str, Any]):
        """根据配置加载资源"""
        if self.maafw.resource:
            self.maafw.resource.clear()

        resource_target = resource_raw.get("resource")
        resource_path = []

        controller_cfg = self.task_service.get_task(_CONTROLLER_)
        gpu_idx = -1
        if controller_cfg:
            controller_type_raw = controller_cfg.task_option.get("controller_type", "")
            if isinstance(controller_type_raw, str):
                controller_name = controller_type_raw.strip()
            elif isinstance(controller_type_raw, dict):
                controller_name = str(
                    controller_type_raw.get("value", "") or ""
                ).strip()
            else:
                controller_name = ""
        else:
            msg = self.tr(
                "Controller config not found, please configure controller first"
            )
            logger.error("未找到控制器配置")
            signalBus.log_output.emit("ERROR", msg)
            await self.stop_task()
            return False

        controller_entry = self._get_interface_controller_entry(controller_name)
        if not controller_entry:
            msg = self.tr(
                "Controller '{}' not found, please reset controller in settings"
            ).format(controller_name or "unknown")
            logger.error(f"未找到控制器名称: {controller_name}")
            signalBus.log_output.emit("ERROR", msg)
            await self.stop_task()
            return False

        attach_resource_path: list = []
        raw_attach = controller_entry.get("attach_resource_path")
        if isinstance(raw_attach, list):
            attach_resource_path = [
                p for p in raw_attach if isinstance(p, str) and (p or "").strip()
            ]

        if not resource_target:
            msg = self.tr(
                "Resource target is empty, please configure resource in settings"
            )
            logger.error("未找到资源目标")
            signalBus.log_output.emit("ERROR", msg)
            await self.stop_task()
            return False

        for resource in self.task_service.interface.get("resource", []):
            if resource["name"] == resource_target:
                logger.debug(f"加载资源: {resource['path']}")
                resource_path = resource["path"]
                break

        if self.need_stop:
            return False

        if not resource_path:
            msg = self.tr(
                "Resource '{}' not found, please reset resource in settings"
            ).format(resource_target)
            logger.error(f"未找到目标资源: {resource_target}")
            signalBus.log_output.emit("ERROR", msg)
            signalBus.log_output.emit(
                "ERROR", self.tr("please try to reset resource in setting")
            )
            await self.stop_task()
            return False

        for path_item in resource_path:
            # 所有资源路径均为相对路径：优先相对于当前 bundle.path，再回落到项目根目录
            bundle_path_str = self.bundle_path or "./"

            # 先解析 bundle 基础目录为绝对路径
            bundle_base = Path(bundle_path_str)
            if not bundle_base.is_absolute():
                bundle_base = (Path.cwd() / bundle_base).resolve()

            # 兼容旧格式：移除占位符 {PROJECT_DIR}，并清理前导分隔符
            raw = str(path_item)
            raw = raw.replace("{PROJECT_DIR}", "")
            normalized = raw.lstrip("\\/")

            # 资源实际路径 = bundle 基础目录 / 相对资源路径
            resource = (bundle_base / normalized).resolve()
            if not resource.exists():
                logger.error(f"资源不存在: {resource}")
                signalBus.log_output.emit(
                    "ERROR",
                    self.tr("Resource ")
                    + path_item
                    + self.tr(" not found in bundle: ")
                    + bundle_path_str,
                )
                signalBus.log_output.emit(
                    "ERROR", self.tr("please try to reset resource in setting")
                )
                return False

            logger.debug(f"加载资源: {resource}")
            res_cfg = self.task_service.get_task(_RESOURCE_)
            gpu_idx = res_cfg.task_option.get("gpu", -1) if res_cfg else -1
            await self.maafw.load_resource(resource, gpu_idx)
            logger.debug(f"资源加载完成: {resource}")
        # v2.2.0：控制器 attach_resource_path，在 resource.path 加载完成后额外加载
        bundle_path_str = self.bundle_path or "./"
        bundle_base = Path(bundle_path_str)
        if not bundle_base.is_absolute():
            bundle_base = (Path.cwd() / bundle_base).resolve()
        for path_item in attach_resource_path:
            if self.need_stop:
                return False
            raw = str(path_item).replace("{PROJECT_DIR}", "").strip().lstrip("\\/")
            if not raw:
                continue
            resource = (bundle_base / raw).resolve()
            if not resource.exists():
                logger.warning(f"控制器附加资源不存在，已跳过: {resource}")
                continue
            logger.debug(f"加载控制器附加资源: {resource}")
            await self.maafw.load_resource(resource, gpu_idx)
        return True

    async def run_task(self, task_id: str, skip_speedrun: bool = False):
        """执行指定任务"""
        task = self.task_service.get_task(task_id)
        if not task:
            logger.error(f"任务 ID '{task_id}' 不存在")
            return
        # 执行层只关心“任务是否被禁用”，不展开禁用原因
        # 注意：即使 UI 未刷新也没关系，因为 run_tasks_flow 开始时会刷新一次 is_hidden；
        # 若未来有独立调用 run_task 的路径，可在此处补一次刷新。
        elif self._is_task_disabled(task):
            return
        elif not task.is_checked:
            return
        speedrun_cfg = self._resolve_speedrun_config(task)
        # 仅依据任务自身的速通开关，不再依赖全局 speedrun_mode；单任务执行可跳过校验
        if (not skip_speedrun) and speedrun_cfg and speedrun_cfg.get("enabled", False):
            allowed, reason = self._evaluate_speedrun(task, speedrun_cfg)
            if not allowed:
                signalBus.log_output.emit(
                    "INFO",
                    self.tr("Task ")
                    + task.name
                    + self.tr(" follows speedrun limit, skipping this run: ")
                    + reason,
                )
                return "skipped"

        raw_info = self.task_service.get_task_execution_info(task_id)
        logger.info(f"任务 '{task.name}' 的执行信息: {raw_info}")
        if raw_info is None:
            logger.error(f"无法获取任务 '{task.name}' 的执行信息")
            return

        entry = raw_info.get("entry", "") or ""
        task_pipeline_override = raw_info.get("pipeline_override", {})

        # 合并默认 override（global + resource + controller）和任务自身的 override
        # 任务 override 优先级最高，使用深度合并以正确处理嵌套字典
        import copy
        from app.core.utils.pipeline_helper import _deep_merge_dict

        pipeline_override = copy.deepcopy(self._default_pipeline_override)
        _deep_merge_dict(pipeline_override, task_pipeline_override)

        if not self.maafw.resource:
            logger.error("资源未初始化，无法执行任务")
            return

        self._start_task_timeout(entry)

        if not await self.maafw.run_task(
            entry, pipeline_override, cfg.get(cfg.save_screenshot)
        ):
            logger.error(f"任务 '{task.name}' 执行失败")
            # 发送任务失败通知
            if not self._manual_stop:
                image_bytes = await self._get_notice_screenshot_bytes()
                send_notice(
                    NoticeTiming.WHEN_TASK_FAILED,
                    self.tr("Task Failed"),
                    self.tr("Task '{}' execution failed.").format(task.name),
                    image_bytes=image_bytes,
                )
            self._stop_task_timeout()
            return
        self._stop_task_timeout()
        # 仅在任务未被 abort 且正常完成时记录速通耗时
        if self._current_task_ok:
            self._record_speedrun_runtime(task)

    async def stop_task(self, *, manual: bool = False):
        """停止当前正在运行的任务

        Args:
            manual: 是否为“手动停止”（由用户或外部调用显式触发）。
        """
        if manual:
            # 在任何情况下都记录手动停止的意图，避免后续错误发送通知
            self._manual_stop = True
        if self.need_stop:
            return
        self.need_stop = True
        self._stop_task_timeout()
        if self.fs_signal_bus:
            signalBus.log_output.emit("INFO", self.tr("Stopping task..."))
            self.fs_signal_bus.fs_start_button_status.emit(
                {"text": "STOP", "status": "disabled"}
            )
        await self.maafw.stop_task()
        if self.fs_signal_bus:
            self.fs_signal_bus.fs_start_button_status.emit(
                {"text": "START", "status": "enabled"}
            )
        self._is_running = False
        logger.info("任务流停止")

    def _start_task_timeout(self, entry: str):
        """开始任务超时计时，每小时检查一次（单任务模式下不启动）"""
        # 单任务模式下不进行长期任务检查
        if self._is_single_task_mode:
            return

        entry_text = (entry or "").strip() or self.tr("Unknown Task Entry")
        # 如果entry不同，重置状态
        if entry_text != self._timeout_active_entry:
            self._timeout_active_entry = entry_text

        # 记录任务开始时间
        if self._current_running_task_id:
            self._task_start_times[self._current_running_task_id] = _time.time()

        # 每小时（3600秒）检查一次
        timeout_seconds = 3600
        self._timeout_timer.stop()
        self._timeout_timer.start(timeout_seconds * 1000)

    def _stop_task_timeout(self):
        """停止任务超时计时"""
        self._timeout_timer.stop()
        # 清除当前任务的开始时间
        if (
            self._current_running_task_id
            and self._current_running_task_id in self._task_start_times
        ):
            del self._task_start_times[self._current_running_task_id]

    def _reset_task_timeout_state(self):
        """重置任务超时状态"""
        self._timeout_timer.stop()
        self._timeout_active_entry = ""
        self._current_running_task_id = None
        # 清空所有任务开始时间记录
        self._task_start_times.clear()

    def _is_tasks_flow_completed_normally(self) -> bool:
        """判断任务流是否正常完成（非手动停止）"""
        return not self.need_stop and not self._manual_stop

    def _get_collected_logs(self) -> str:
        """获取收集到的任务日志内容"""
        if not self._log_messages:
            return ""

        # 将日志信息格式化为文本（包含收到的时间戳）
        # 格式：[时间][日志等级]日志内容
        log_text_lines: list[str] = []
        for log_item in self._log_messages:
            if len(log_item) == 3:
                level, text, timestamp = log_item
            else:
                # 兼容旧格式（没有时间戳的情况）
                level, text = log_item[:2]  # type: ignore[misc]
                timestamp = datetime.now().strftime("%H:%M:%S")
            # 翻译日志级别
            translated_level = self._translate_log_level(level)
            log_text_lines.append(f"[{timestamp}][{translated_level}]{text}")

        return "\n".join(log_text_lines)

    def _on_task_timeout(self):
        """任务超时处理：每小时检查一次，如果任务运行超过1小时则发送通知（单任务模式下不执行）"""
        # 单任务模式下不进行长期任务检查
        if self._is_single_task_mode:
            self._timeout_timer.stop()
            return

        if not self._current_running_task_id:
            # 没有正在运行的任务，停止定时器
            self._timeout_timer.stop()
            return

        # 获取当前任务的开始时间
        task_start_time = self._task_start_times.get(self._current_running_task_id)
        if not task_start_time:
            # 没有开始时间记录，重新记录并继续
            self._task_start_times[self._current_running_task_id] = _time.time()
            return

        # 计算任务运行时间
        current_time = _time.time()
        elapsed_seconds = current_time - task_start_time
        elapsed_hours = elapsed_seconds / 3600

        # 如果运行时间超过1小时，发送通知
        if elapsed_hours >= 1.0:
            entry_text = self._timeout_active_entry or self.tr("Unknown Task Entry")

            # 格式化运行时间
            hours = int(elapsed_hours)
            minutes = int((elapsed_seconds % 3600) / 60)
            if hours > 0:
                time_str = self.tr("{} hours {} minutes").format(hours, minutes)
            else:
                time_str = self.tr("{} minutes").format(minutes)

            timeout_message = self.tr(
                "Task entry '{}' has been running for {}. This may indicate a problem. Please check the task status."
            ).format(entry_text, time_str)

            logger.warning(timeout_message)
            signalBus.log_output.emit("WARNING", timeout_message)

            # 获取收集到的任务日志内容
            log_content = self._get_collected_logs()

            # 发送外部通知（类型为"任务超时"），内容为任务总结中的日志
            notice_content = log_content if log_content else timeout_message
            # 超时回调为同步，不在此处截屏；可后续改为异步调度
            send_notice(
                NoticeTiming.WHEN_TASK_TIMEOUT,
                self.tr("Task running time too long"),
                notice_content,
            )

        # 定时器会继续运行，一小时后再次检查

    async def _connect_adb_controller(self, controller_raw: Dict[str, Any]):
        """连接 ADB 控制器"""
        if not isinstance(controller_raw, dict):
            logger.error(
                f"控制器配置格式错误(ADB)，期望 dict，实际 {type(controller_raw)}: {controller_raw}"
            )
            return False

        activate_controller = controller_raw.get("controller_type")
        if activate_controller is None:
            logger.error(f"未找到控制器配置: {controller_raw}")
            return False

        # 获取控制器类型和名称
        controller_type = self._get_controller_type(controller_raw)
        controller_name = self._get_controller_name(controller_raw)

        self.adb_controller_raw = controller_raw
        self.adb_activate_controller = activate_controller

        # 使用控制器名称作为键来获取配置（兼容旧配置：如果找不到则尝试使用控制器类型）
        if controller_name in controller_raw:
            controller_config = controller_raw[controller_name]
        elif controller_type in controller_raw:
            # 兼容旧配置：迁移到控制器名称
            controller_config = controller_raw[controller_type]
            controller_raw[controller_name] = controller_config
        else:
            controller_config = {}
            controller_raw[controller_name] = controller_config
        self.adb_controller_config = controller_config

        # 提前读取并保存原始的 input_methods 和 screencap_methods
        # 仅当原配置里显式存在时才在设备发现后恢复，避免覆盖设备探测结果。
        has_raw_input_method = "input_methods" in controller_config
        has_raw_screen_method = "screencap_methods" in controller_config
        raw_input_method = int(
            controller_config.get(
                "input_methods", int(MaaAdbInputMethodEnum.Default.value)
            )
        )
        raw_screen_method = int(
            controller_config.get(
                "screencap_methods", int(MaaAdbScreencapMethodEnum.Default.value)
            )
        )

        logger.info("每次连接前自动搜索 ADB 设备...")
        signalBus.log_output.emit("INFO", self.tr("Auto searching ADB devices..."))
        found_device = await self._auto_find_adb_device(
            controller_raw, controller_type, controller_config
        )
        if found_device:
            self._save_device_to_config(controller_raw, controller_name, found_device)
            controller_config = controller_raw[controller_name]
            self.adb_controller_config = controller_config
            # 恢复原始的 input_methods 和 screencap_methods
            if has_raw_input_method:
                controller_config["input_methods"] = raw_input_method
            if has_raw_screen_method:
                controller_config["screencap_methods"] = raw_screen_method

        adb_path = controller_config.get("adb_path", "")
        address = controller_config.get("address", "")

        # 检查 adb 路径和连接地址
        if not adb_path:
            error_msg = self.tr(
                "ADB path is empty, please configure ADB path in settings"
            )
            logger.error("ADB 路径为空")
            signalBus.log_output.emit("ERROR", error_msg)
            return False

        if not address:
            error_msg = self.tr(
                "ADB connection address is empty, please configure device connection in settings"
            )
            logger.error("ADB 连接地址为空")
            signalBus.log_output.emit("ERROR", error_msg)
            return False
        # 使用之前保存的原始值（已在重新搜索前读取）

        def normalize_input_method(value: int) -> int:
            mask = (1 << 64) - 1
            value &= mask
            if value & (1 << 63):
                value -= 1 << 64
            return value

        input_method = normalize_input_method(raw_input_method)
        screen_method = normalize_input_method(raw_screen_method)
        config = controller_config.get("config", {})

        if await self.maafw.connect_adb(
            adb_path,
            address,
            screen_method,
            input_method,
            config,
        ):
            # 连接成功后额外等待 5 秒，防止程序初始化未完成
            await asyncio.sleep(5)
            return True
        elif controller_config.get("emulator_path", ""):
            logger.info("尝试启动模拟器")
            signalBus.log_output.emit("INFO", self.tr("try to start emulator"))
            emu_path = controller_config.get("emulator_path", "")
            emu_params = controller_config.get("emulator_params", "")
            wait_emu_start = int(controller_config.get("wait_time", 0))

            self.process = self._start_process(emu_path, emu_params)
            # 启动后轮询连接，连接成功则提前退出等待
            if wait_emu_start > 0:
                poll_ok = await self._poll_connect(
                    wait_emu_start,
                    self.tr("waiting for emulator start..."),
                    lambda: self.maafw.connect_adb(
                        adb_path, address, screen_method, input_method, config,
                    ),
                )
                if poll_ok:
                    # 轮询连接成功后额外等待 5 秒，防止程序初始化未完成
                    await asyncio.sleep(5)
                    return True
                if self.need_stop:
                    return False
            else:
                if await self.maafw.connect_adb(
                    adb_path, address, screen_method, input_method, config,
                ):
                    # 启动模拟器后首次直接连接成功时，额外等待 5 秒
                    await asyncio.sleep(5)
                    return True
        signalBus.log_output.emit("ERROR", self.tr("Device connection failed"))
        return False

    async def _connect_win32_controller(self, controller_raw: Dict[str, Any]):
        """连接 Win32 控制器"""
        # 验证平台：Win32 只在 Windows 上支持
        if sys.platform != "win32":
            error_msg = self.tr("Win32 controller is only supported on Windows")
            logger.error("Win32 控制器仅在 Windows 上支持")
            signalBus.log_output.emit("ERROR", error_msg)
            return False

        activate_controller = controller_raw.get("controller_type")
        if activate_controller is None:
            logger.error(f"未找到控制器配置: {controller_raw}")
            return False

        # 获取控制器类型和名称
        controller_type = self._get_controller_type(controller_raw)
        controller_name = self._get_controller_name(controller_raw)

        # 使用控制器名称作为键来获取配置（兼容旧配置：如果找不到则尝试使用控制器类型）
        if controller_name in controller_raw:
            controller_config = controller_raw[controller_name]
        elif controller_type in controller_raw:
            # 兼容旧配置：迁移到控制器名称
            controller_config = controller_raw[controller_type]
            controller_raw[controller_name] = controller_config
        else:
            controller_config = {}
            controller_raw[controller_name] = controller_config

        # 提前读取并保存原始的配置值
        raw_screencap_method = controller_config.get("win32_screencap_methods")
        raw_mouse_method = controller_config.get("mouse_input_methods")
        raw_keyboard_method = controller_config.get("keyboard_input_methods")

        def _restore_raw_methods():
            if raw_screencap_method is not None:
                controller_config["win32_screencap_methods"] = raw_screencap_method
            if raw_mouse_method is not None:
                controller_config["mouse_input_methods"] = raw_mouse_method
            if raw_keyboard_method is not None:
                controller_config["keyboard_input_methods"] = raw_keyboard_method

        def _collect_win32_params():
            hwnd_raw = controller_config.get("hwnd", 0)
            try:
                hwnd_value = int(hwnd_raw)
            except (TypeError, ValueError):
                hwnd_value = 0
            screencap = (
                raw_screencap_method
                if raw_screencap_method is not None
                else controller_config.get("win32_screencap_methods", 1)
            )
            mouse = (
                raw_mouse_method
                if raw_mouse_method is not None
                else controller_config.get("mouse_input_methods", 1)
            )
            keyboard = (
                raw_keyboard_method
                if raw_keyboard_method is not None
                else controller_config.get("keyboard_input_methods", 1)
            )

            def _safe_int(value: Any) -> int | None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            # 兼容旧配置里将 -1 作为 Win32 "default" 的写法。
            if _safe_int(screencap) == -1:
                screencap = int(MaaWin32ScreencapMethodEnum.DXGI_DesktopDup.value)
            if _safe_int(mouse) == -1:
                mouse = int(MaaWin32InputMethodEnum.Seize.value)
            if _safe_int(keyboard) == -1:
                keyboard = int(MaaWin32InputMethodEnum.Seize.value)
            return hwnd_value, screencap, mouse, keyboard

        logger.info("每次连接前自动搜索 Win32 窗口...")
        signalBus.log_output.emit("INFO", self.tr("Auto searching Win32 windows..."))
        found_device = await self._auto_find_win32_window(
            controller_raw, controller_type, controller_name, controller_config
        )
        if found_device:
            self._save_device_to_config(controller_raw, controller_name, found_device)
            controller_config = controller_raw[controller_name]
            _restore_raw_methods()
            hwnd, screencap_method, mouse_method, keyboard_method = (
                _collect_win32_params()
            )
            if not hwnd:
                error_msg = self.tr(
                    "Window handle (hwnd) is empty, please configure window connection in settings"
                )
                logger.error("Win32 窗口句柄为空")
                signalBus.log_output.emit("ERROR", error_msg)
                return False

            # 需求：如果已搜索到窗口，则直接尝试连接并返回成功/失败（不再启动程序兜底）
            connect_success = await self.maafw.connect_win32hwnd(
                hwnd,
                screencap_method,
                mouse_method,
                keyboard_method,
            )
            if not connect_success:
                signalBus.log_output.emit("ERROR", self.tr("Device connection failed"))
            return bool(connect_success)

        # 需求：首次未搜索到窗口时，才检查是否配置了启动程序路径
        program_path = (controller_config.get("program_path") or "").strip()
        if not program_path:
            logger.error("Win32 控制器未匹配窗口且未配置启动程序")
            signalBus.log_output.emit("ERROR", self.tr("Device connection failed"))
            return False

        # 启动程序+参数，轮询搜索窗口并连接
        signalBus.log_output.emit("INFO", self.tr("try to start program"))
        logger.info("尝试启动程序")
        program_params = controller_config.get("program_params", "")
        wait_program_start = int(controller_config.get("wait_time", 0))
        self.process = self._start_process(program_path, program_params)

        async def _try_find_and_connect_win32():
            nonlocal controller_config
            found = await self._auto_find_win32_window(
                controller_raw, controller_type, controller_name, controller_config
            )
            if not found:
                return False
            self._save_device_to_config(controller_raw, controller_name, found)
            controller_config = controller_raw[controller_name]
            _restore_raw_methods()
            hwnd, screencap_method, mouse_method, keyboard_method = (
                _collect_win32_params()
            )
            if not hwnd:
                return False
            return await self.maafw.connect_win32hwnd(
                hwnd, screencap_method, mouse_method, keyboard_method
            )

        if wait_program_start > 0:
            poll_ok = await self._poll_connect(
                wait_program_start,
                self.tr("waiting for program start..."),
                _try_find_and_connect_win32,
            )
            if poll_ok:
                return True
            if self.need_stop:
                return False
        else:
            if await _try_find_and_connect_win32():
                return True

        logger.error("启动程序后未找到与配置匹配的 Win32 窗口")
        signalBus.log_output.emit("ERROR", self.tr("Device connection failed"))
        return False

    async def _connect_gamepad_controller(self, controller_raw: Dict[str, Any]):
        """连接 Gamepad 控制器（复用 Win32 的窗口查找能力，但连接逻辑独立）"""
        # 验证平台：Gamepad 只在 Windows 上支持
        if sys.platform != "win32":
            error_msg = self.tr("Gamepad controller is only supported on Windows")
            logger.error("Gamepad 控制器仅在 Windows 上支持")
            signalBus.log_output.emit("ERROR", error_msg)
            return False

        if not isinstance(controller_raw, dict):
            logger.error(
                f"控制器配置格式错误(Gamepad)，期望 dict，实际 {type(controller_raw)}: {controller_raw}"
            )
            return False

        activate_controller = controller_raw.get("controller_type")
        if activate_controller is None:
            logger.error(f"未找到控制器配置: {controller_raw}")
            return False

        # 获取控制器类型和名称
        controller_type = self._get_controller_type(controller_raw)
        controller_name = self._get_controller_name(controller_raw)

        # 使用控制器名称作为键来获取配置（兼容旧配置：如果找不到则尝试使用控制器类型）
        if controller_name in controller_raw:
            controller_config = controller_raw[controller_name]
        elif controller_type in controller_raw:
            controller_config = controller_raw[controller_type]
            controller_raw[controller_name] = controller_config
        else:
            controller_config = {}
            controller_raw[controller_name] = controller_config

        def _collect_gamepad_params():
            hwnd_raw = controller_config.get("hwnd", 0)
            try:
                hwnd_value = int(hwnd_raw)
            except (TypeError, ValueError):
                hwnd_value = 0
            gamepad_type_raw = controller_config.get("gamepad_type", 0)
            try:
                gamepad_type_value = int(gamepad_type_raw)
            except (TypeError, ValueError):
                gamepad_type_value = 0
            return hwnd_value, gamepad_type_value

        # 优先用 interface.json 里的默认截图方式（可选）
        def _resolve_gamepad_screencap_method() -> int:
            entry = self._get_interface_controller_entry(controller_name) or {}
            gamepad_cfg = entry.get("gamepad") or {}
            raw = gamepad_cfg.get("screencap")
            if isinstance(raw, int):
                return raw
            if raw and isinstance(raw, str):
                return int(MaaWin32ScreencapMethodEnum[raw].value)
            return int(MaaWin32ScreencapMethodEnum.DXGI_DesktopDup.value)

        screencap_method = _resolve_gamepad_screencap_method()

        logger.info("每次连接前自动搜索 Gamepad 窗口...")
        signalBus.log_output.emit("INFO", self.tr("Auto searching desktop windows..."))
        found_device = await self._auto_find_win32_window(
            controller_raw, controller_type, controller_name, controller_config
        )
        if found_device:
            self._save_device_to_config(controller_raw, controller_name, found_device)
            controller_config = controller_raw[controller_name]
            hwnd, gamepad_type = _collect_gamepad_params()
            if not hwnd:
                error_msg = self.tr(
                    "Window handle (hwnd) is empty, please configure window connection in settings"
                )
                logger.error("Gamepad 窗口句柄为空")
                signalBus.log_output.emit("ERROR", error_msg)
                return False

            connect_success = await self.maafw.connect_gamepad(
                hwnd, gamepad_type, screencap_method
            )
            if not connect_success:
                signalBus.log_output.emit("ERROR", self.tr("Device connection failed"))
            return bool(connect_success)

        # 若未搜索到窗口时，才检查是否配置了启动程序路径
        program_path = (controller_config.get("program_path") or "").strip()
        if not program_path:
            logger.error("Gamepad 控制器未匹配窗口且未配置启动程序")
            signalBus.log_output.emit("ERROR", self.tr("Device connection failed"))
            return False

        signalBus.log_output.emit("INFO", self.tr("try to start program"))
        logger.info("尝试启动程序")
        program_params = controller_config.get("program_params", "")
        wait_program_start = int(controller_config.get("wait_time", 0))
        self.process = self._start_process(program_path, program_params)
        if wait_program_start > 0:
            countdown_ok = await self._countdown_wait(
                wait_program_start,
                self.tr("waiting for program start..."),
            )
            if not countdown_ok:
                return False

        found_after_launch = await self._auto_find_win32_window(
            controller_raw, controller_type, controller_name, controller_config
        )
        if not found_after_launch:
            logger.error("启动程序后未找到与配置匹配的窗口")
            signalBus.log_output.emit("ERROR", self.tr("Device connection failed"))
            return False

        self._save_device_to_config(controller_raw, controller_name, found_after_launch)
        controller_config = controller_raw[controller_name]
        hwnd, gamepad_type = _collect_gamepad_params()
        if not hwnd:
            error_msg = self.tr(
                "Window handle (hwnd) is empty, please configure window connection in settings"
            )
            logger.error("Gamepad 窗口句柄为空")
            signalBus.log_output.emit("ERROR", error_msg)
            return False

        connect_success = await self.maafw.connect_gamepad(
            hwnd, gamepad_type, screencap_method
        )
        if not connect_success:
            signalBus.log_output.emit("ERROR", self.tr("Device connection failed"))
        return bool(connect_success)

    async def _connect_playcover_controller(self, controller_raw: Dict[str, Any]):
        """连接 PlayCover 控制器"""
        # 验证平台：PlayCover 只在 macOS 上支持
        if sys.platform != "darwin":
            error_msg = self.tr("PlayCover controller is only supported on macOS")
            logger.error("PlayCover 控制器仅在 macOS 上支持")
            signalBus.log_output.emit("ERROR", error_msg)
            return False

        if not isinstance(controller_raw, dict):
            logger.error(
                f"控制器配置格式错误(PlayCover)，期望 dict，实际 {type(controller_raw)}: {controller_raw}"
            )
            return False

        activate_controller = controller_raw.get("controller_type")
        if activate_controller is None:
            logger.error(f"未找到控制器配置: {controller_raw}")
            return False

        # 获取控制器类型和名称
        controller_type = self._get_controller_type(controller_raw)
        controller_name = self._get_controller_name(controller_raw)

        # 使用控制器名称作为键来获取配置（兼容旧配置：如果找不到则尝试使用控制器类型）
        if controller_name in controller_raw:
            controller_config = controller_raw[controller_name]
        elif controller_type in controller_raw:
            # 兼容旧配置：迁移到控制器名称
            controller_config = controller_raw[controller_type]
            controller_raw[controller_name] = controller_config
        else:
            controller_config = {}
            controller_raw[controller_name] = controller_config

        # 从配置中读取 uuid 和 address
        uuid = controller_config.get("uuid", "")
        address = controller_config.get("address", "")

        # 检查 uuid 和 address 是否为空
        if not uuid:
            error_msg = self.tr(
                "PlayCover UUID is empty, please configure UUID in settings"
            )
            logger.error("PlayCover UUID 为空")
            signalBus.log_output.emit("ERROR", error_msg)
            return False

        if not address:
            error_msg = self.tr(
                "PlayCover connection address is empty, please configure address in settings"
            )
            logger.error("PlayCover 连接地址为空")
            signalBus.log_output.emit("ERROR", error_msg)
            return False

        logger.debug(f"PlayCover 参数: uuid={uuid}, address={address}")

        logger.info(f"正在连接 PlayCover: {address} (UUID: {uuid})")
        msg = self.tr("Connecting to PlayCover: {address} (UUID: {uuid})").format(
            address=address,
            uuid=uuid,
        )
        signalBus.log_output.emit("INFO", msg)

        if await self.maafw.connect_playcover(address, uuid):
            logger.info("PlayCover 连接成功")
            signalBus.log_output.emit(
                "INFO", self.tr("PlayCover connected successfully")
            )
            return True
        else:
            error_msg = self.tr("Failed to connect to PlayCover")
            logger.error("PlayCover 连接失败")
            signalBus.log_output.emit("ERROR", error_msg)
            return False

    def _parse_address_components(self, address: str | None) -> tuple[str, str | None]:
        """提取 ADB 地址和端口"""
        raw_address = (address or "").strip()
        if not raw_address:
            return "", None
        if ":" in raw_address:
            host, port = raw_address.rsplit(":", 1)
            return host.strip(), port.strip() or None
        return raw_address, None

    def _extract_device_base_name(self, device_name: str) -> str:
        """从设备名称中提取基础名称

        例如：
        - "雷电模拟器-LDPlayer[0](emulator-5554)" -> "雷电模拟器-LDPlayer[0]"
        - "MuMu模拟器(127.0.0.1:7555)" -> "MuMu模拟器"
        - "雷电模拟器-LDPlayer[0]" -> "雷电模拟器-LDPlayer[0]"
        """
        # 只去掉 (address) 部分，保留 [index] 部分
        # 匹配格式：name[index](address) 或 name(address) 或 name[index]
        pattern = r"^(.+?)(?:\(.*?\))?$"
        match = re.match(pattern, device_name.strip())
        if match:
            return match.group(1).strip()
        return device_name.strip()

    def _should_use_new_adb_device(
        self,
        old_config: Dict[str, Any],
        new_device: Dict[str, Any] | None,
    ) -> bool:
        """判断自动搜索到的 ADB 设备是否和旧配置一致"""
        if not new_device:
            return False

        old_adb_path = (old_config.get("adb_path") or "").strip()
        new_adb_path = (new_device.get("adb_path") or "").strip()

        old_name = self._extract_device_base_name(old_config.get("device_name") or "")
        new_name = self._extract_device_base_name(new_device.get("device_name") or "")

        # 如果旧配置中 adb_path 或 device_name 为空，则使用新配置
        if not old_adb_path or not old_name:
            return True

        # 两者都必须匹配
        adb_path_match = old_adb_path == new_adb_path
        name_match = old_name == new_name

        return adb_path_match and name_match

    def _should_use_new_win32_window(
        self,
        old_config: Dict[str, Any],
        new_device: Dict[str, Any] | None,
    ) -> bool:
        """判断自动搜索到的 Win32 窗口是否属于旧配置"""
        if not new_device:
            return False

        old_name = (old_config.get("device_name") or "").strip()
        new_name = (new_device.get("device_name") or "").strip()

        # 如果旧配置没有设备名，只要有新设备名就使用
        if not old_name:
            return bool(new_name)
        # 如果旧配置有设备名，需要新设备名存在且与旧配置匹配
        elif new_name:
            return old_name == new_name
        else:
            return False

    def _get_interface_controller_entry(
        self, controller_name: str
    ) -> Dict[str, Any] | None:
        """根据控制器名称查找 interface 中的控制器定义"""
        if not controller_name:
            return None
        controller_lower = controller_name.strip().lower()
        for controller in self.task_service.interface.get("controller", []):
            if controller.get("name", "").lower() == controller_lower:
                return controller
        return None

    def _compile_win32_regex(
        self, pattern: str | None, label: str
    ) -> re.Pattern | None:
        """编译 Win32 过滤正则，失败时返回 None"""
        if not pattern:
            return None
        try:
            return re.compile(pattern)
        except re.error as exc:
            logger.warning(f"Win32 {label} 过滤正则编译失败: {exc}")
            return None

    def _get_win32_filter_patterns(
        self, controller_name: str
    ) -> tuple[re.Pattern | None, re.Pattern | None]:
        """从 interface 中提取 Win32 过滤正则"""
        controller_entry = self._get_interface_controller_entry(controller_name)
        if not controller_entry:
            return None, None
        win32_cfg = controller_entry.get("win32") or {}
        return (
            self._compile_win32_regex(win32_cfg.get("class_regex"), "类名"),
            self._compile_win32_regex(win32_cfg.get("window_regex"), "窗口名"),
        )

    def _get_gamepad_filter_patterns(
        self, controller_name: str
    ) -> tuple[re.Pattern | None, re.Pattern | None]:
        """从 interface 中提取 Gamepad 过滤正则（复用 Win32 的 regex 编译逻辑）"""
        controller_entry = self._get_interface_controller_entry(controller_name)
        if not controller_entry:
            return None, None
        gamepad_cfg = controller_entry.get("gamepad") or {}
        return (
            self._compile_win32_regex(gamepad_cfg.get("class_regex"), "类名"),
            self._compile_win32_regex(gamepad_cfg.get("window_regex"), "窗口名"),
        )

    def _window_matches_win32_filters(
        self,
        window_info: Dict[str, Any],
        class_pattern: re.Pattern | None,
        window_pattern: re.Pattern | None,
    ) -> bool:
        """检查窗口是否满足 Win32 过滤正则（类名+窗口名）"""
        if not class_pattern and not window_pattern:
            return True

        class_name = str(window_info.get("class_name") or "")
        window_name = str(window_info.get("window_name") or "")
        class_match = bool(class_pattern.search(class_name)) if class_pattern else True
        window_match = (
            bool(window_pattern.search(window_name)) if window_pattern else True
        )
        return class_match and window_match

    def _strip_bracket_content(self, text: str) -> str:
        """去除字符串中括号及括号内内容，用于窗口标题匹配消歧。

        例：
        - "雷电模拟器(123456)" -> "雷电模拟器"
        - "Foo（bar）[baz]" -> "Foo"
        """
        if not text:
            return ""
        # 支持英文/中文圆括号、方括号、中文方括号
        pattern = r"[\(\（\[\【].*?[\)\）\]\】]"
        return re.sub(pattern, "", str(text)).strip()

    def _start_process(
        self, entry: str | Path, argv: list[str] | tuple[str, ...] | str | None = None
    ) -> subprocess.Popen:
        """根据入口路径/命令开启子进程，返回 Popen 对象"""
        command = [str(entry)]
        if argv is not None:
            import shlex

            if isinstance(argv, (list, tuple)):
                # If argv is already a list/tuple, just append the arguments directly
                # Don't split them again as they're already parsed
                command.extend(str(arg) for arg in argv)
            else:
                # If argv is a string, split it properly
                command.extend(shlex.split(str(argv)))

        logger.debug(f"准备启动子进程: {command}")
        return subprocess.Popen(command)

    async def _poll_connect(
        self,
        wait_seconds: int,
        message: str,
        connect_coro_fn,
        retry_interval: int = 3,
    ) -> bool:
        """启动程序后按原倒计时规则等待，同时周期性尝试连接，连接成功则提前返回 True。

        :param connect_coro_fn: 无参可调用对象，返回 awaitable，结果为 True 表示连接成功
        :param retry_interval: 每次连接失败后等待的秒数
        """
        if wait_seconds <= 0:
            return False

        thresholds = [60, 30, 15, 10, 5, 4, 3, 2, 1]
        log_points = {wait_seconds}
        for point in thresholds:
            if wait_seconds >= point:
                log_points.add(point)

        since_last_try = retry_interval  # 首次立即尝试

        for remaining in range(wait_seconds, 0, -1):
            if remaining in log_points:
                signalBus.log_output.emit(
                    "INFO",
                    message + str(remaining) + self.tr(" seconds"),
                )
            if self.need_stop:
                return False

            since_last_try += 1
            if since_last_try >= retry_interval:
                since_last_try = 0
                try:
                    if await connect_coro_fn():
                        signalBus.log_output.emit(
                            "INFO", self.tr("Device connected successfully")
                        )
                        return True
                except Exception:
                    pass

            await asyncio.sleep(1)

        # 最后再尝试一次
        try:
            if await connect_coro_fn():
                signalBus.log_output.emit(
                    "INFO", self.tr("Device connected successfully")
                )
                return True
        except Exception:
            pass

        return False

    async def _countdown_wait(self, wait_seconds: int, message: str) -> bool:
        """按指定阈值输出倒计时日志，返回 False 表示提前停止"""

        if wait_seconds <= 0:
            return True

        thresholds = [60, 30, 15, 10, 5, 4, 3, 2, 1]
        log_points = {wait_seconds}
        for point in thresholds:
            if wait_seconds >= point:
                log_points.add(point)

        for remaining in range(wait_seconds, 0, -1):
            if remaining in log_points:
                signalBus.log_output.emit(
                    "INFO",
                    message + str(remaining) + self.tr(" seconds"),
                )
                log_points.remove(remaining)
            if self.need_stop:
                return False
            await asyncio.sleep(1)
        return True

    def _get_controller_name(self, controller_raw: Dict[str, Any]) -> str:
        """获取控制器名称"""
        if not isinstance(controller_raw, dict):
            raise TypeError(
                f"controller_raw 类型错误，期望 dict，实际 {type(controller_raw)}: {controller_raw}"
            )

        controller_config = controller_raw.get("controller_type", {})
        if isinstance(controller_config, str):
            controller_name = controller_config
        elif isinstance(controller_config, dict):
            controller_name = controller_config.get("value", "")
        else:
            controller_name = ""

        # 验证控制器名称是否存在
        controller_name_lower = controller_name.lower()
        for controller in self.task_service.interface.get("controller", []):
            if controller.get("name", "").lower() == controller_name_lower:
                return controller.get("name", "")

        raise ValueError(f"未找到控制器名称: {controller_raw}")

    def _get_controller_type(self, controller_raw: Dict[str, Any]) -> str:
        """获取控制器类型"""
        if not isinstance(controller_raw, dict):
            raise TypeError(
                f"controller_raw 类型错误，期望 dict，实际 {type(controller_raw)}: {controller_raw}"
            )

        controller_config = controller_raw.get("controller_type", {})
        if isinstance(controller_config, str):
            controller_name = controller_config
        elif isinstance(controller_config, dict):
            controller_name = controller_config.get("value", "")
        else:
            controller_name = ""

        controller_name = controller_name.lower()
        for controller in self.task_service.interface.get("controller", []):
            if controller.get("name", "").lower() == controller_name:
                return controller.get("type", "").lower()

        raise ValueError(f"未找到控制器类型: {controller_raw}")

    async def _auto_find_adb_device(
        self,
        controller_raw: Dict[str, Any],
        controller_type: str,
        controller_config: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """自动搜索 ADB 设备并找到与旧配置一致的那一项"""
        try:
            devices = Toolkit.find_adb_devices()
            if not devices:
                logger.warning("未找到任何 ADB 设备")
                return None

            all_device_infos = []
            for device in devices:
                # 优先使用设备自身的 pid，如果没有则使用配置中的 pid
                device_ld_pid = (
                    (
                        (device.config or {})
                        if hasattr(device, "config") and isinstance(device.config, dict)
                        else {}
                    )
                    .get("extras", {})
                    .get("ld", {})
                    .get("pid")
                )
                if device_ld_pid is None:
                    device_ld_pid = (
                        controller_config.get("config", {})
                        .get("extras", {})
                        .get("ld", {})
                        .get("pid")
                    )
                device_index = ControllerHelper.resolve_emulator_index(
                    device, ld_pid=device_ld_pid
                )
                display_name = (
                    f"{device.name}[{device_index}]({device.address})"
                    if device_index is not None
                    else f"{device.name}({device.address})"
                )

                device_info = {
                    "adb_path": str(device.adb_path),
                    "address": device.address,
                    "screencap_methods": device.screencap_methods,
                    "input_methods": device.input_methods,
                    "config": device.config,
                    "device_name": display_name,
                }
                all_device_infos.append(device_info)
                if self._should_use_new_adb_device(controller_config, device_info):
                    return device_info
            logger.debug("ADB 设备列表均未满足与配置匹配的条件，跳过更新")
            logger.debug(f"所有 ADB 设备信息: {all_device_infos}")
            return None

        except Exception as e:
            logger.error(f"自动搜索 ADB 设备时出错: {e}")
            return None

    async def _auto_find_win32_window(
        self,
        controller_raw: Dict[str, Any],
        controller_type: str,
        controller_name: str,
        controller_config: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """自动搜索 Win32 窗口并找到与旧配置一致的那一项"""
        try:
            windows = Toolkit.find_desktop_windows()
            if not windows:
                logger.warning("未找到任何 Win32 窗口")
                return None

            all_window_infos = []
            if controller_type == "gamepad":
                class_pattern, window_pattern = self._get_gamepad_filter_patterns(
                    controller_name
                )
            else:
                class_pattern, window_pattern = self._get_win32_filter_patterns(
                    controller_name
                )
            matched_window_infos: list[Dict[str, Any]] = []
            for window in windows:
                window_info = {
                    "hwnd": str(window.hwnd),
                    "window_name": window.window_name,
                    "class_name": window.class_name,
                    "device_name": f"{window.window_name or 'Unknown Window'}({window.hwnd})",
                }
                all_window_infos.append(window_info)
                if not self._window_matches_win32_filters(
                    window_info, class_pattern, window_pattern
                ):
                    continue
                matched_window_infos.append(window_info)

            # 先只基于 class/window 正则过滤；如果只有一个候选，直接返回它
            if len(matched_window_infos) == 1:
                return matched_window_infos[0]

            # 若过滤出多个候选，再使用旧配置的 device_name 做消歧：
            # 去除括号及括号内内容后，与 window_name 对比，命中则返回。
            if len(matched_window_infos) > 1:
                old_device_name = (controller_config.get("device_name") or "").strip()
                old_title = self._strip_bracket_content(old_device_name)
                if old_title:
                    for win in matched_window_infos:
                        if (
                            self._strip_bracket_content(win.get("window_name") or "")
                            == old_title
                        ):
                            return win

                # 消歧失败时，保持行为确定性：返回第一个候选
                return matched_window_infos[0]
            logger.debug("Win32 窗口列表均未满足与配置匹配的条件，跳过更新")
            logger.debug(f"所有 Win32 窗口信息: {all_window_infos}")
            return None

        except Exception as e:
            logger.error(f"自动搜索 Win32 窗口时出错: {e}")
            return None

    def _save_device_to_config(
        self,
        controller_raw: Dict[str, Any],
        controller_name: str,
        device_info: Dict[str, Any],
    ) -> None:
        """保存设备信息到配置

        Args:
            controller_raw: 控制器原始配置
            controller_name: 控制器名称（name）
            device_info: 设备信息字典
        """
        try:
            # 确保控制器配置存在（使用控制器名称作为键）
            if controller_name not in controller_raw:
                controller_raw[controller_name] = {}

            # 更新设备信息
            controller_raw[controller_name].update(device_info)

            # 获取预配置任务并更新
            if controller_cfg := self.task_service.get_task(_CONTROLLER_):
                controller_cfg.task_option.update(controller_raw)
                self.task_service.update_task(controller_cfg)
                logger.info(f"设备配置已保存: {device_info.get('device_name', '')}")

        except Exception as e:
            logger.error(f"保存设备配置时出错: {e}")

    async def _handle_post_action(self) -> None:
        """
        统一处理完成后操作顺序（串行执行，避免动作未生效）：

        规则：
        - 关闭控制器、运行其他程序：优先执行，且会等待动作完成（尽力等待控制器真正关闭）
        - 切换配置：只要求前两者完成，不等待外部通知（因为不关软件）
        - 关机/退出软件：在执行前等待外部通知发送完成（避免通知丢失）
        """
        post_task = self.task_service.get_task(POST_ACTION)
        if not post_task:
            return

        post_config = post_task.task_option.get("post_action")
        if not isinstance(post_config, dict):
            return

        # 1) 无动作：直接返回（不会与其他选项同时存在）
        if post_config.get("none"):
            logger.debug("完成后操作: 无动作")
            return

        # 2) 第一阶段：关闭控制器 / 运行其他程序（必须先完成）
        if post_config.get("close_controller"):
            logger.debug("完成后操作: 关闭控制器")
            await self._close_controller_and_wait()

        if post_config.get("run_program"):
            logger.debug("完成后操作: 运行其他程序")
            await self._run_program_from_post_action(
                post_config.get("program_path", ""),
                post_config.get("program_args", ""),
            )

        # 3) 第二阶段：切换配置（不等待外部通知）
        if post_config.get("run_other"):
            if target_config := (post_config.get("target_config") or "").strip():
                logger.info(
                    "完成后操作: 运行其他配置，等待 %.2f 秒再切换",
                    self._config_switch_delay,
                )
                await asyncio.sleep(self._config_switch_delay)
                await self._run_other_configuration(target_config)
            else:
                logger.warning("完成后运行其他配置开关被激活，但未配置目标配置")

        # 4) 第三阶段：退出/关机（需要等待外部通知发送完成）
        if post_config.get("close_software"):
            logger.debug("完成后操作: 退出软件")
            await self._close_software()
            return  # 退出软件后不再执行后续操作

        if post_config.get("shutdown"):
            logger.debug("完成后操作: 关机")
            await self._shutdown_system_after_notice()

    async def _run_program_from_post_action(
        self, program_path: str, program_args: str
    ) -> None:
        """根据配置启动指定程序，等待退出"""
        executable = (program_path or "").strip()
        if not executable:
            logger.warning("完成后程序未填写路径，跳过")
            return

        args_list = self._parse_program_args(program_args)
        try:
            process = await asyncio.to_thread(
                self._start_process, executable, args_list or None
            )
        except Exception as exc:
            logger.error(f"启动完成后程序失败: {exc}")
            return

        logger.debug(f"完成后程序已启动: {executable}")
        try:
            return_code = await asyncio.to_thread(process.wait)
            logger.debug(f"完成后程序已退出，返回码: {return_code}")
        except Exception as exc:
            logger.error(f"等待完成后程序退出时失败: {exc}")

    def _parse_program_args(self, args: str) -> list[str]:
        """解析完成后程序的参数字符串"""
        trimmed = (args or "").strip()
        if not trimmed:
            return []

        try:
            return shlex.split(trimmed, posix=os.name != "nt")
        except ValueError as exc:
            logger.warning(f"解析完成后程序参数失败，退回简单分割: {exc}")
            return [item for item in trimmed.split() if item]

    async def _run_other_configuration(self, config_id: str) -> None:
        """尝试切换到指定的配置"""
        config_service = self.config_service
        if not config_service:
            logger.warning("配置服务未初始化，跳过运行其他配置")
            return

        target_config = config_service.get_config(config_id)
        if not target_config:
            logger.warning(f"完成后操作指定的配置不存在: {config_id}")
            return

        config_service.current_config_id = config_id
        if config_service.current_config_id == config_id:
            logger.debug(f"已切换至完成后指定配置: {config_id}")
            signalBus.log_clear_requested.emit()
            self._next_config_to_run = config_id
        else:
            logger.warning(f"切换至配置 {config_id} 失败")

    async def _close_software(self) -> None:
        """发出退出信号让程序自身关闭"""
        app = QCoreApplication.instance()
        if not app:
            logger.warning("完成后关闭软件: 无法获取 QCoreApplication 实例")
            return

        logger.debug("完成后关闭软件: 等待通知发送完成")
        await self._wait_for_notice_delivery()
        logger.debug("完成后关闭软件: 退出应用")
        app.quit()

    async def _wait_for_notice_delivery(self, timeout: float = 10.0) -> None:
        """等待通知线程将当前队列中的消息发送完毕"""
        if not hasattr(send_thread, "wait_until_idle"):
            return

        try:
            if not send_thread.is_idle():
                signalBus.info_bar_requested.emit(
                    "info",
                    self.tr(
                        "Notifications are being sent, please wait up to {} seconds"
                    ).format(int(timeout)),
                )
            completed = await asyncio.to_thread(send_thread.wait_until_idle, timeout)
            if not completed:
                logger.warning(
                    "等待通知发送完成超时: %s 秒，仍有未完成的通知任务", timeout
                )
        except Exception as exc:
            logger.warning("等待通知发送完成时出错: %s", exc)

    async def _shutdown_system_after_notice(self) -> None:
        """关机前等待外部通知发送完成（避免通知丢失）"""
        logger.debug("完成后关机: 等待通知发送完成")
        await self._wait_for_notice_delivery()
        logger.debug("完成后关机: 执行关机命令")
        self._shutdown_system()

    async def _close_controller_and_wait(self, timeout: float = 10.0) -> None:
        """关闭控制器，并尽力等待控制器真正退出（避免紧接着退出软件/关机时关闭动作未生效）"""
        # 先发起关闭动作（原逻辑）
        self._close_controller()

        # 再尽力等待真正关闭（仅对可检测的场景做等待；失败/超时不影响后续动作）
        try:
            controller_cfg = self.task_service.get_task(_CONTROLLER_)
            if not controller_cfg or not isinstance(controller_cfg.task_option, dict):
                return
            controller_raw = controller_cfg.task_option

            try:
                controller_type = self._get_controller_type(controller_raw)
            except Exception:
                return

            if controller_type == "win32":
                controller_name = self._get_controller_name(controller_raw)
                win32_config = None
                if controller_name in controller_raw:
                    win32_config = controller_raw.get(controller_name)
                elif "win32" in controller_raw:
                    win32_config = controller_raw.get("win32")
                if not isinstance(win32_config, dict):
                    return

                hwnd_raw = win32_config.get("hwnd", 0)
                if not hwnd_raw:
                    return
                await self._wait_win32_window_closed(hwnd_raw, timeout=timeout)
                return

            if controller_type == "adb":
                # 通过当前记录的 ADB address 尽力判断设备是否仍存在
                adb_cfg = self.adb_controller_config
                if not isinstance(adb_cfg, dict):
                    return
                address = (adb_cfg.get("address") or "").strip()
                if not address:
                    return
                await self._wait_adb_device_disconnected(address, timeout=timeout)
                return
        except Exception as exc:
            logger.debug("等待控制器关闭时出错（忽略）: %s", exc)

    async def _wait_win32_window_closed(
        self, hwnd: int | str, timeout: float = 10.0
    ) -> None:
        """等待 Win32 窗口关闭（短超时轮询）"""
        if not sys.platform.startswith("win"):
            return
        try:
            hwnd_value = int(hwnd) if isinstance(hwnd, str) else int(hwnd)
        except Exception:
            return
        if hwnd_value <= 0:
            return

        def _is_window(hwnd_int: int) -> bool:
            import ctypes

            user32 = ctypes.windll.user32
            return bool(user32.IsWindow(hwnd_int))

        start = _time.time()
        while True:
            exists = await asyncio.to_thread(_is_window, hwnd_value)
            if not exists:
                logger.debug("完成后关闭控制器: Win32 窗口已关闭 (hwnd=%s)", hwnd_value)
                return
            if _time.time() - start >= timeout:
                logger.warning(
                    "完成后关闭控制器: 等待 Win32 窗口关闭超时 (hwnd=%s, timeout=%.1fs)",
                    hwnd_value,
                    timeout,
                )
                return
            await asyncio.sleep(0.2)

    async def _wait_adb_device_disconnected(
        self, address: str, timeout: float = 10.0
    ) -> None:
        """等待 ADB 设备断开（短超时轮询，尽力而为）"""
        normalized = (address or "").strip()
        if not normalized:
            return

        def _still_exists(addr: str) -> bool:
            try:
                devices = Toolkit.find_adb_devices() or []
                for dev in devices:
                    try:
                        dev_addr = str(dev.address).strip()
                    except Exception:
                        dev_addr = ""
                    if dev_addr == addr:
                        return True
                return False
            except Exception:
                return False

        start = _time.time()
        while True:
            exists = await asyncio.to_thread(_still_exists, normalized)
            if not exists:
                logger.debug("完成后关闭控制器: ADB 设备已断开 (%s)", normalized)
                return
            if _time.time() - start >= timeout:
                logger.warning(
                    "完成后关闭控制器: 等待 ADB 设备断开超时 (%s, timeout=%.1fs)",
                    normalized,
                    timeout,
                )
                return
            await asyncio.sleep(0.3)

    def _close_controller(self) -> None:
        """关闭控制器 - 根据当前运行的控制器类型执行不同的关闭操作"""
        controller_cfg = self.task_service.get_task(_CONTROLLER_)
        if not controller_cfg:
            logger.warning("未找到控制器配置，无法关闭控制器")
            return

        controller_raw = controller_cfg.task_option
        if not isinstance(controller_raw, dict):
            logger.warning("控制器配置格式错误，无法关闭控制器")
            return

        try:
            controller_type = self._get_controller_type(controller_raw)
        except Exception as exc:
            logger.warning(f"获取控制器类型失败: {exc}")
            return

        if controller_type == "adb":
            # 关闭 ADB 控制器：运行原本的关闭模拟器逻辑
            if self.adb_controller_config is None:
                logger.warning("ADB 控制器配置不存在，无法关闭")
                return

            adb_address = self.adb_controller_config.get("address", "")
            if ":" in adb_address:
                adb_port = adb_address.split(":")[-1]
            elif "-" in adb_address:
                adb_port = adb_address.split("-")[-1]
            else:
                adb_port = None
            adb_path = self.adb_controller_config.get("adb_path")

            device_name = self.adb_controller_config.get("device_name", "")

            if "mumuplayer12" in device_name.lower():
                ControllerHelper.close_mumu(adb_path, adb_port)
            elif "ldplayer" in device_name.lower():
                ld_pid_cfg = (
                    self.adb_controller_config.get("config", {})
                    .get("extras", {})
                    .get("ld", {})
                    .get("pid")
                )
                ControllerHelper.close_ldplayer(adb_path, ld_pid_cfg)
            else:
                logger.warning(f"未找到对应的模拟器: {device_name}")
        elif controller_type == "win32":
            # 关闭 Win32 控制器：通过 hwnd 关闭窗口
            controller_name = self._get_controller_name(controller_raw)
            if controller_name in controller_raw:
                win32_config = controller_raw[controller_name]
            elif controller_type in controller_raw:
                win32_config = controller_raw[controller_type]
            else:
                logger.warning("未找到 Win32 控制器配置")
                return

            hwnd_raw = win32_config.get("hwnd", 0)
            if not hwnd_raw:
                logger.warning("Win32 控制器窗口句柄为空，无法关闭")
                return

            # 调用 ControllerHelper 关闭窗口
            ControllerHelper.close_win32_window(hwnd_raw)
        elif controller_type == "playcover":
            # 关闭 PlayCover 控制器：什么都不做
            logger.debug("PlayCover 控制器无需关闭操作")
        else:
            logger.warning(f"未知的控制器类型: {controller_type}")

    def shutdown(self):
        """
        关机
        """
        shutdown_commands = {
            "Windows": "shutdown /s /t 1",
            "Linux": "shutdown now",
            "Darwin": "sudo shutdown -h now",  # macOS
        }
        os.system(shutdown_commands.get(platform.system(), ""))

    def _shutdown_system(self) -> None:
        """执行系统关机命令，兼容 Windows/macOS/Linux"""
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["shutdown", "/s", "/t", "0"], check=False)
            elif sys.platform == "darwin":
                subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)
            else:
                subprocess.run(["shutdown", "-h", "now"], check=False)
            logger.debug("完成后执行关机命令")
        except Exception as exc:
            logger.error(f"执行关机命令失败: {exc}")

    def _resolve_speedrun_config(self, task: TaskItem) -> Dict[str, Any] | None:
        """优先使用任务保存的速通配置，其次使用 interface，最终回落默认值"""
        try:
            if not isinstance(task.task_option, dict):
                task.task_option = {}

            existing_cfg = task.task_option.get("_speedrun_config")
            merged_cfg = self.task_service.build_speedrun_config(
                task.name, existing_cfg
            )
            if task.task_option.get("_speedrun_config") != merged_cfg:
                task.task_option["_speedrun_config"] = merged_cfg
                self.task_service.update_task(task)
            return merged_cfg if isinstance(merged_cfg, dict) else {}
        except Exception as exc:
            logger.warning(f"合成速通配置失败，使用 interface 数据: {exc}")
            interface_task = self._get_task_by_name(task.name)
            return (
                interface_task.get("speedrun")
                if interface_task and isinstance(interface_task, dict)
                else {}
            )

    def _evaluate_speedrun(
        self, task: TaskItem, speedrun: Dict[str, Any]
    ) -> tuple[bool, str]:
        """校验 speedrun 限制"""
        if not speedrun or not isinstance(speedrun, dict):
            return True, ""

        if speedrun.get("enabled") is False:
            return True, ""

        run_cfg = speedrun.get("run") or {}
        if not isinstance(run_cfg, dict):
            run_cfg = {}

        count_limit_value = self._get_speedrun_count_limit(run_cfg)
        if count_limit_value is None:
            return True, ""

        state = self._ensure_speedrun_state(task)
        history_entries = state.get("last_runtime", [])
        history = self._parse_speedrun_history(history_entries)
        last_run = history[-1] if history else datetime(1970, 1, 1)
        now = datetime.now()

        next_refresh = self._get_speedrun_next_refresh_time(last_run, speedrun)
        if not next_refresh:
            return True, ""

        state_dirty = False
        remaining_count = state.get("remaining_count")
        if not isinstance(remaining_count, int):
            remaining_count = -1
        # 如果剩余次数小于0，则设置为0
        if remaining_count < 0:
            state["remaining_count"] = 0
            remaining_count = 0
            state_dirty = True

        # 如果当前时间大于下次刷新时间，且剩余次数不等于限制次数，则更新剩余次数
        if now >= next_refresh and state.get("remaining_count") != count_limit_value:
            state["remaining_count"] = count_limit_value
            remaining_count = count_limit_value
            state_dirty = True

        # 如果剩余次数为0，则返回False
        if remaining_count == 0:
            if state_dirty:
                self.task_service.update_task(task)
            return False, self.tr("This period's remaining execution count is 0")

        min_interval_value = self._get_speedrun_min_interval(run_cfg)
        if min_interval_value and history:
            last_run_time = history[-1]
            elapsed = (now - last_run_time).total_seconds()
            if elapsed < min_interval_value * 3600:
                if state_dirty:
                    self.task_service.update_task(task)
                return (
                    False,
                    self.tr(
                        "Not enough time passed since last run. Minimum interval is "
                    )
                    + str(min_interval_value)
                    + self.tr(" hours."),
                )

        if state_dirty:
            self.task_service.update_task(task)
        return True, ""

    def _record_speedrun_runtime(self, task: TaskItem) -> None:
        """记录 speedrun 运行时间"""
        state = self._ensure_speedrun_state(task)

        history = self._parse_speedrun_history(state.get("last_runtime", []))
        history.append(datetime.now())
        last_entry = history[-1]
        state["last_runtime"] = [last_entry.isoformat()]
        self._consume_speedrun_count(state)
        remaining = state.get("remaining_count", -1)
        logger.info(
            f"任务 '{task.name}' 已记录 speedrun 运行时间, 最新 {state['last_runtime'][-1]}, 剩余 {remaining}"
        )
        self.task_service.update_task(task)

    def _parse_speedrun_history(self, raw_history: Any) -> list[datetime]:
        entries = raw_history or []
        if not isinstance(entries, list):
            entries = [entries]

        parsed: list[datetime] = []
        epoch = datetime(1970, 1, 1)

        for entry in entries:
            parsed_entry: datetime | None = None
            if isinstance(entry, (int, float)):
                try:
                    parsed_entry = datetime.fromtimestamp(entry)
                except (OverflowError, OSError):
                    parsed_entry = None
            elif isinstance(entry, str):
                try:
                    parsed_entry = datetime.fromisoformat(entry)
                except ValueError:
                    try:
                        parsed_entry = datetime.fromtimestamp(float(entry))
                    except (TypeError, ValueError, OverflowError, OSError):
                        parsed_entry = None

            # 对不合法时间回退到 epoch
            parsed.append(parsed_entry or epoch)

        parsed.sort()
        return parsed

    def _get_speedrun_next_refresh_time(
        self, base_time: datetime, speedrun: Dict[str, Any]
    ) -> datetime | None:
        mode = (speedrun.get("mode") or "").lower()
        trigger_cfg = speedrun.get("trigger") or {}
        if not isinstance(trigger_cfg, dict):
            return None

        if mode == "daily":
            daily_trigger = trigger_cfg.get("daily") or {}
            hour_start = self._normalize_hour_value(daily_trigger.get("hour_start"))
            if hour_start is None:
                hour_start = 0
            return self._next_daily_refresh_time(base_time, hour_start)

        if mode == "weekly":
            weekly_trigger = trigger_cfg.get("weekly") or {}
            weekdays = self._collect_valid_ints(weekly_trigger.get("weekday", []), 1, 7)
            hour_start = self._normalize_hour_value(weekly_trigger.get("hour_start"))
            if hour_start is None:
                hour_start = 0
            return self._next_weekly_refresh_time(base_time, weekdays, hour_start)

        if mode == "monthly":
            monthly_trigger = trigger_cfg.get("monthly") or {}
            days = self._collect_valid_ints(monthly_trigger.get("day", []), 1, 31)
            hour_start = self._normalize_hour_value(monthly_trigger.get("hour_start"))
            if hour_start is None:
                hour_start = 0
            return self._next_monthly_refresh_time(base_time, days, hour_start)

        return None

    def _next_daily_refresh_time(
        self, base_time: datetime, hour_start: int
    ) -> datetime:
        candidate = base_time.replace(
            hour=hour_start, minute=0, second=0, microsecond=0
        )
        if candidate <= base_time:
            candidate += timedelta(days=1)
        return candidate

    def _next_weekly_refresh_time(
        self, base_time: datetime, weekdays: list[int], hour_start: int
    ) -> datetime | None:
        allowed = weekdays or list(range(1, 8))
        start_date = base_time.date()
        for day_offset in range(14):
            candidate_date = start_date + timedelta(days=day_offset)
            if candidate_date.isoweekday() not in allowed:
                continue
            candidate = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour_start,
                0,
                0,
            )
            if candidate > base_time:
                return candidate
        offset = 14
        while True:
            candidate_date = start_date + timedelta(days=offset)
            if candidate_date.isoweekday() in allowed:
                candidate = datetime(
                    candidate_date.year,
                    candidate_date.month,
                    candidate_date.day,
                    hour_start,
                    0,
                    0,
                )
                if candidate > base_time:
                    return candidate
            offset += 1

    def _next_monthly_refresh_time(
        self, base_time: datetime, days: list[int], hour_start: int
    ) -> datetime | None:
        allowed_days = sorted(set(days)) if days else list(range(1, 32))
        start_year = base_time.year
        start_month = base_time.month
        for month_offset in range(24):
            month_index = start_month - 1 + month_offset
            year = start_year + month_index // 12
            month = (month_index % 12) + 1
            days_in_month = calendar.monthrange(year, month)[1]
            for day in allowed_days:
                if day > days_in_month:
                    continue
                candidate = datetime(year, month, day, hour_start, 0, 0)
                if candidate > base_time:
                    return candidate
        return None

    def _collect_valid_ints(
        self, raw_value: Any, min_value: int, max_value: int
    ) -> list[int]:
        if not isinstance(raw_value, (list, tuple)):
            return []

        normalized: list[int] = []
        for item in raw_value:
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            if min_value <= number <= max_value:
                normalized.append(number)

        return normalized

    def _normalize_hour_value(self, raw_value: Any) -> int | None:
        try:
            hour = int(raw_value)
        except (TypeError, ValueError):
            return None

        hour = max(0, hour)
        # 将小时限制在0-23之间
        hour %= 24
        return hour

    def _ensure_speedrun_state(self, task: TaskItem) -> dict:
        if not isinstance(task.task_option, dict):
            task.task_option = {}
        state = task.task_option.get("_speedrun_state")
        if not isinstance(state, dict):
            epoch = datetime(1970, 1, 1)
            state = {
                "last_runtime": [epoch.isoformat()],
                "remaining_count": -1,
            }
            task.task_option["_speedrun_state"] = state
        if "last_runtime" not in state or not isinstance(state["last_runtime"], list):
            epoch = datetime(1970, 1, 1)
            state["last_runtime"] = [epoch.isoformat()]
        if "remaining_count" not in state or not isinstance(
            state["remaining_count"], int
        ):
            state["remaining_count"] = -1
        return state

    def _get_speedrun_count_limit(self, run_cfg: Dict[str, Any]) -> int | None:
        count_limit = run_cfg.get("count")
        try:
            return int(count_limit) if count_limit not in (None, "", False) else None
        except (TypeError, ValueError):
            return None

    def _get_speedrun_min_interval(self, run_cfg: Dict[str, Any]) -> float | None:
        min_interval = run_cfg.get("min_interval_hours")
        try:
            return (
                float(min_interval) if min_interval not in (None, "", False) else None
            )
        except (TypeError, ValueError):
            return None

    def _consume_speedrun_count(self, state: dict) -> None:
        remaining = state.get("remaining_count")
        if isinstance(remaining, int) and remaining > 0:
            state["remaining_count"] = remaining - 1

    def _get_task_by_name(self, name: str) -> Dict[str, Any]:
        interface = self.task_service.interface
        tasks = interface.get("task")

        if not isinstance(tasks, list):
            return {}
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if task.get("name") == name:
                return task

        return {}

    def _is_task_disabled(self, task: TaskItem) -> bool:
        """统一的“任务是否被禁用”判断（执行层只关心结论，不关心原因）。

        禁用来源可能包括：
        - UI 侧标记的 is_hidden
        - 配置层（TaskService）计算出的 resource/controller 约束
        """
        # 约定：任务流执行前由配置层（TaskService/Coordinator/UI）刷新过 is_hidden
        return bool(task.is_hidden)
