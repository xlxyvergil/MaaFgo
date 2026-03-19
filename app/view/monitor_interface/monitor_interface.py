from typing import Optional

from asyncify import asyncify
import asyncio
from datetime import datetime
from pathlib import Path
from time import time

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    PixmapLabel,
    PrimaryPushButton,
)

from app.core.core import ServiceCoordinator
from app.core.runner.monitor_task import MonitorTask
from app.utils.logger import (
    logger,
    restore_asyncify_logging,
    restore_qasync_logging,
    suppress_asyncify_logging,
    suppress_qasync_logging,
)
from app.common.signal_bus import signalBus


class _ClickablePreviewLabel(PixmapLabel):
    clicked = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent=parent)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            self.clicked.emit(int(pos.x()), int(pos.y()))
        super().mouseReleaseEvent(event)


class MonitorInterface(QWidget):
    """显示实时画面并提供截图/监控设置入口的子页面。"""

    def __init__(self, service_coordinator: ServiceCoordinator, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("MonitorInterface")
        self.service_coordinator = service_coordinator
        self._preview_pixmap: Optional[QPixmap] = None
        self._current_pil_image: Optional[Image.Image] = None
        self._preview_scaled_size: QSize = QSize(0, 0)
        self._image_width: Optional[int] = None
        self._image_height: Optional[int] = None
        self._is_landscape: Optional[bool] = None
        self._setup_ui()
        self.monitor_task = MonitorTask(
            task_service=self.service_coordinator.task_service,
            config_service=self.service_coordinator.config_service,
        )
        self._monitor_loop_task: Optional[asyncio.Task] = None
        self._image_processing_task: Optional[asyncio.Task] = None
        self._monitoring_active = False
        self._target_interval = 1.0 / 120  # 120 FPS
        self._image_queue: Optional[asyncio.Queue] = None
        self._max_queue_size = 2  # 限制队列大小，避免内存占用过大

    def _setup_ui(self) -> None:
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(28, 18, 28, 18)
        self.main_layout.setSpacing(14)

        self.preview_label = _ClickablePreviewLabel(self)
        self.preview_label.setObjectName("monitorPreviewLabel")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(360)
        self.preview_label.setMinimumWidth(640)
        # 初始使用 Expanding 策略，当检测到图片尺寸后会调整
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.preview_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_label.setStyleSheet(
            """
            QLabel#monitorPreviewLabel {
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                background-color: rgba(255, 255, 255, 0.02);
            }
            """
        )
        self.main_layout.addWidget(self.preview_label, 1)
        self.preview_label.clicked.connect(self._on_preview_clicked)
        self.preview_label.setToolTip(self.tr("Click to sync this frame to the device"))
        self._fps_overlay = BodyLabel(self.tr("FPS: --"), self.preview_label)
        self._fps_overlay.setObjectName("monitorFpsOverlay")
        self._fps_overlay.setStyleSheet(
            """
            QLabel#monitorFpsOverlay {
                background-color: rgba(0, 0, 0, 0.55);
                color: #ffffff;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 12px;
            }
            """
        )
        self._fps_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._fps_overlay.adjustSize()
        self._last_frame_timestamp: Optional[float] = None
        self._last_fps_overlay_update: Optional[float] = None

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(18)
        controls_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self.save_button = PrimaryPushButton(self.tr("Save Screenshot"), self)
        self.save_button.setIcon(FIF.CAMERA)
        self.save_button.setIconSize(QSize(18, 18))
        self.save_button.clicked.connect(self._on_save_screenshot)
        self.save_button.setToolTip(
            self.tr("Capture the current preview and store it on disk")
        )
        controls_layout.addWidget(self.save_button)

        self.monitor_control_button = PrimaryPushButton(self.tr("Start Monitoring"), self)
        self.monitor_control_button.setIcon(FIF.PLAY)
        self.monitor_control_button.setIconSize(QSize(18, 18))
        self.monitor_control_button.clicked.connect(self._on_monitor_control_clicked)
        self.monitor_control_button.setToolTip(
            self.tr("Start monitoring task")
        )
        controls_layout.addWidget(self.monitor_control_button)

        controls_layout.addStretch()
        self.main_layout.addLayout(controls_layout)
        self.main_layout.setStretch(0, 1)
        self.main_layout.setStretch(1, 0)

    def _load_placeholder_image(self) -> None:
        pixmap = QPixmap("app/assets/icons/logo.png")
        if pixmap.isNull():
            logger.warning("无法加载监控子页面的占位图标，路径可能不存在。")
            return
        self._preview_pixmap = pixmap
        self._refresh_preview_image()
        self._update_fps_overlay(None)

    def _refresh_preview_image(self) -> None:
        if not self._preview_pixmap:
            return
        target_size = self.preview_label.size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            return
        scaled = self._preview_pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)
        self._preview_scaled_size = scaled.size()
        self._reposition_fps_overlay()

    def set_preview_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        """动态更新监控画面截图。"""
        self._preview_pixmap = pixmap
        self._current_pil_image = None
        self._refresh_preview_image()

    def _update_preview_size_policy(self, image_width: int, image_height: int, force_update: bool = False) -> None:
        """根据图片尺寸更新预览标签的大小策略"""
        # 判断是横向还是纵向
        is_landscape = image_width >= image_height
        
        # 如果方向没有变化且不是强制更新，检查是否需要更新
        if not force_update and self._is_landscape == is_landscape and self._image_width == image_width and self._image_height == image_height:
            # 检查当前预览标签大小是否合理，如果不合理则更新
            current_size = self.preview_label.size()
            if current_size.width() > 0 and current_size.height() > 0:
                # 如果当前大小合理，不需要更新
                return
        
        # 更新图片尺寸信息
        self._image_width = image_width
        self._image_height = image_height
        self._is_landscape = is_landscape
        
        # 计算宽高比
        aspect_ratio = image_width / image_height if image_height > 0 else 1.0
        
        # 获取可用空间
        available_width = self.width() - 56  # 减去左右边距 (28 * 2)
        available_height = self.height() - 200  # 减去上下边距和控件高度
        
        # 根据宽高比和可用空间计算合适的尺寸
        if is_landscape:
            # 横向：以宽度为主
            target_width = min(available_width, 1280)
            target_height = int(target_width / aspect_ratio)
            # 确保不超过可用高度
            if target_height > available_height:
                target_height = available_height
                target_width = int(target_height * aspect_ratio)
        else:
            # 纵向：以高度为主
            target_height = min(available_height, 1280)
            target_width = int(target_height * aspect_ratio)
            # 确保不超过可用宽度
            if target_width > available_width:
                target_width = available_width
                target_height = int(target_width / aspect_ratio)
        
        # 设置最小尺寸，确保不会太小
        min_width = 640 if is_landscape else 360
        min_height = 360 if is_landscape else 640
        
        target_width = max(target_width, min_width)
        target_height = max(target_height, min_height)
        
        # 设置预览标签的固定尺寸（保持宽高比）
        self.preview_label.setFixedSize(target_width, target_height)
        # 更新大小策略为固定，保持宽高比
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

    def _apply_preview_from_pil(self, pil_image: Image.Image) -> None:
        # 获取图片尺寸
        image_width, image_height = pil_image.size
        
        # 根据图片尺寸更新预览标签大小
        self._update_preview_size_policy(image_width, image_height)
        
        rgb_image = pil_image.convert("RGB")
        bytes_per_line = image_width * 3
        buffer = rgb_image.tobytes("raw", "RGB")
        qimage = QImage(
            buffer, image_width, image_height, bytes_per_line, QImage.Format.Format_RGB888
        )
        self._preview_pixmap = QPixmap.fromImage(qimage)
        self._current_pil_image = rgb_image.copy()
        self._refresh_preview_image()
        current_timestamp = time()
        fps_value: Optional[float] = None
        if self._last_frame_timestamp is not None:
            interval = current_timestamp - self._last_frame_timestamp
            if interval > 0:
                fps_value = 1.0 / interval
        self._last_frame_timestamp = current_timestamp
        self._update_fps_overlay(fps_value)

    def _update_fps_overlay(self, fps_value: Optional[float]) -> None:
        if fps_value is None:
            text = f"{self.tr('FPS')}: --"
            self._last_fps_overlay_update = None
        else:
            now = time()
            if (
                self._last_fps_overlay_update is not None
                and now - self._last_fps_overlay_update < 0.5
            ):
                return
            self._last_fps_overlay_update = now
            text = f"{self.tr('FPS')}: {fps_value:.1f}"
        self._fps_overlay.setText(text)
        self._fps_overlay.adjustSize()
        self._reposition_fps_overlay()

    def _reposition_fps_overlay(self) -> None:
        if not self._fps_overlay:
            return
        preview_size = self.preview_label.size()
        overlay_size = self._fps_overlay.size()
        margin = 12
        x = preview_size.width() - overlay_size.width() - margin
        y = preview_size.height() - overlay_size.height() - margin
        self._fps_overlay.move(max(x, 0), max(y, 0))

    def _capture_frame(self) -> Image.Image:
        controller = self.monitor_task.maafw.controller
        if controller is None:
            raise RuntimeError("控制器尚未初始化，无法抓取画面")
        raw_frame = controller.post_screencap().wait().get()
        if raw_frame is None:
            raise ValueError("采集返回空帧")
        return Image.fromarray(raw_frame[..., ::-1])

    def _get_target_interval(self) -> float:
        return self._target_interval

    def _start_monitor_loop(self) -> None:
        if self._monitor_loop_task and not self._monitor_loop_task.done():
            return
        suppress_asyncify_logging()
        suppress_qasync_logging()
        self._monitoring_active = True
        # 创建图片处理队列
        self._image_queue = asyncio.Queue(maxsize=self._max_queue_size)
        # 启动图片处理任务
        self._image_processing_task = asyncio.create_task(self._image_processing_loop())
        # 启动监控循环
        self._monitor_loop_task = asyncio.create_task(self._monitor_loop())

    def _stop_monitor_loop(self) -> None:
        self._monitoring_active = False
        # 停止监控循环
        task = self._monitor_loop_task
        self._monitor_loop_task = None
        if task and not task.done():
            task.cancel()
        # 图片处理任务会在处理完队列中的图片后自动退出
        # 不立即取消，让它处理完剩余的图片
        restore_asyncify_logging()
        restore_qasync_logging()
    
    async def _wait_for_image_processing_complete(self, timeout: float = 1.0) -> None:
        """等待图片处理任务完成（处理完队列中的图片）"""
        if self._image_processing_task and not self._image_processing_task.done():
            try:
                await asyncio.wait_for(self._image_processing_task, timeout=timeout)
            except asyncio.TimeoutError:
                # 超时，取消任务
                if not self._image_processing_task.done():
                    self._image_processing_task.cancel()
                    try:
                        await self._image_processing_task
                    except asyncio.CancelledError:
                        pass
            except Exception:
                pass
        # 清空队列引用
        self._image_queue = None
        self._image_processing_task = None

    async def _monitor_loop(self) -> None:
        """监控循环：只负责截图，不处理图片"""
        loop = asyncio.get_running_loop()
        try:
            while self._monitoring_active:
                start = loop.time()
                if not self._is_controller_connected():
                    await self._handle_controller_disconnection()
                    return
                try:
                    # 异步截图，不阻塞
                    pil_image = await asyncio.to_thread(self._capture_frame)
                except Exception as exc:
                    logger.debug("监控循环：截图失败：%s", exc)
                    pil_image = None
                
                # 将截图放入队列，由图片处理任务处理
                if pil_image and self._image_queue is not None:
                    try:
                        # 如果队列已满，丢弃最旧的图片，放入新图片
                        if self._image_queue.full():
                            try:
                                self._image_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        self._image_queue.put_nowait(pil_image)
                    except asyncio.QueueFull:
                        # 队列已满，跳过这一帧
                        pass
                
                # 计算等待时间，保持目标 FPS
                elapsed = loop.time() - start
                wait = max(0, self._get_target_interval() - elapsed)
                await asyncio.sleep(wait)
        except asyncio.CancelledError:
            pass
        finally:
            self._monitor_loop_task = None
            restore_asyncify_logging()
            restore_qasync_logging()
    
    async def _image_processing_loop(self) -> None:
        """图片处理循环：从队列中取出图片并处理，不影响截图速度"""
        try:
            while self._monitoring_active or (self._image_queue and not self._image_queue.empty()):
                # 检查队列是否存在
                if self._image_queue is None:
                    break
                try:
                    # 从队列中获取图片，设置超时避免无限等待
                    pil_image = await asyncio.wait_for(
                        self._image_queue.get(),
                        timeout=0.1
                    )
                    # 处理图片（转换为 QPixmap 并更新 UI）
                    if pil_image:
                        self._apply_preview_from_pil(pil_image)
                except asyncio.TimeoutError:
                    # 超时，继续循环检查
                    continue
                except Exception as exc:
                    logger.debug("图片处理循环：处理图片失败：%s", exc)
        except asyncio.CancelledError:
            pass
        finally:
            self._image_processing_task = None

    def _is_controller_connected(self) -> bool:
        controller = getattr(self.monitor_task.maafw, "controller", None)
        if controller is None:
            return False
        connected = getattr(controller, "connected", None)
        return connected is not False

    async def _handle_controller_disconnection(self) -> None:
        if not self._monitoring_active:
            return
        logger.warning("监控子页面：检测到控制器断开，停止监控。")
        self._monitoring_active = False
        current_task = asyncio.current_task()
        if current_task is not self._monitor_loop_task:
            self._stop_monitor_loop()
        try:
            await self.monitor_task.maafw.stop_task()
        except Exception as exc:
            logger.exception("监控子页面：停止任务失败：%s", exc)
        # 销毁连接对象，回到初始状态
        try:
            if self.monitor_task.maafw.controller:
                self.monitor_task.maafw.controller = None
                logger.info("监控子页面：已销毁连接对象")
        except Exception as exc:
            logger.exception("监控子页面：销毁连接对象失败：%s", exc)
        # 更新按钮状态
        if hasattr(self, 'monitor_control_button'):
            self.monitor_control_button.setText(self.tr("Start Monitoring"))
            self.monitor_control_button.setIcon(FIF.PLAY)
            self.monitor_control_button.setToolTip(self.tr("Start monitoring task"))

    def _schedule_controller_disconnection(self) -> None:
        if not self._monitoring_active:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(self._handle_controller_disconnection())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 如果已经有图片尺寸信息，重新计算预览标签大小（强制更新）
        if self._image_width is not None and self._image_height is not None:
            self._update_preview_size_policy(self._image_width, self._image_height, force_update=True)
        self._refresh_preview_image()

    def _on_save_screenshot(self) -> None:
        logger.info("监控子页面：用户请求保存截图。")
        if not self._current_pil_image:
            logger.warning("监控子页面：当前不存在可保存的截图。")
            return
        save_dir = Path("debug") / "save_screen"
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
        save_path = save_dir / filename
        try:
            self._current_pil_image.save(save_path)
            logger.info("监控子页面：截图已保存至 %s", save_path)
            message = self.tr("Screenshot saved to ")+str(save_path)
            signalBus.info_bar_requested.emit("success", message)
        except Exception as exc:
            logger.exception("监控子页面：保存截图失败：%s", exc)

    def _on_preview_clicked(self, x: int, y: int) -> None:
        if not self.service_coordinator:
            return
        if not self._is_controller_connected():
            self._schedule_controller_disconnection()
            return
        handler = getattr(self.service_coordinator, "sync_monitor_preview_click", None)
        if callable(handler):
            try:
                handler()
            except Exception as exc:
                logger.exception(f"同步预览点击至设备失败：{exc}")

        coords = self._map_visual_click_to_device(x, y)
        if not coords:
            logger.warning("监控子页面：点击未落在画面范围内，忽略此次事件。")
            return
        controller = getattr(self.monitor_task.maafw, "controller", None)
        if controller is None:
            logger.warning("监控子页面：控制器未初始化，无法同步点击。")
            return

        try:
            controller.post_click(*coords).wait()
            logger.debug("监控子页面：已同步点击到设备，坐标 %s。", coords)
        except Exception as exc:
            logger.exception("监控子页面：同步点击失败：%s", exc)

    def _map_visual_click_to_device(self, x: int, y: int) -> tuple[int, int] | None:
        """将 UI 中的点击位置映射为标准 1280×720 的设备坐标。"""
        if (
            not self._preview_scaled_size
            or self._preview_scaled_size.width() <= 0
            or self._preview_scaled_size.height() <= 0
        ):
            return None

        label_width = self.preview_label.width()
        label_height = self.preview_label.height()
        scaled_width = self._preview_scaled_size.width()
        scaled_height = self._preview_scaled_size.height()

        x_offset = max(0, (label_width - scaled_width) // 2)
        y_offset = max(0, (label_height - scaled_height) // 2)

        rel_x = x - x_offset
        rel_y = y - y_offset
        if rel_x < 0 or rel_y < 0 or rel_x >= scaled_width or rel_y >= scaled_height:
            return None

        normalized_x = rel_x / scaled_width
        normalized_y = rel_y / scaled_height

        target_width = 1280
        target_height = 720
        device_x = int(round(normalized_x * target_width))
        device_y = int(round(normalized_y * target_height))
        device_x = max(0, min(device_x, target_width - 1))
        device_y = max(0, min(device_y, target_height - 1))

        return device_x, device_y


    def _on_monitor_control_clicked(self) -> None:
        """处理开始/停止监控按钮点击"""
        if self._monitoring_active:
            # 当前正在监控，切换到停止监控
            self._stop_monitoring()
        else:
            # 当前未监控，切换到开始监控
            self._start_monitoring()

    def _start_monitoring(self) -> None:
        """开始监控任务"""
        # 立即锁定按钮，防止重复点击
        self.monitor_control_button.setEnabled(False)
        
        async def _start_sequence():
            try:
                # 如果控制器未连接，先连接
                if not self._is_controller_connected():
                    connected = await self.monitor_task._connect()
                    if not connected:
                        logger.error("设备连接失败，无法开始监控")
                        signalBus.info_bar_requested.emit(
                            "error", self.tr("Device connection failed, cannot start monitoring")
                        )
                        return
                
                # 启动监控循环
                self._start_monitor_loop()
                
                # 更新按钮状态
                self.monitor_control_button.setText(self.tr("Stop Monitoring"))
                self.monitor_control_button.setIcon(FIF.CLOSE)
                self.monitor_control_button.setToolTip(self.tr("Stop monitoring task"))
                
                signalBus.info_bar_requested.emit("success", self.tr("Monitoring started"))
                
                # 立即捕获一帧以显示画面
                try:
                    if not self._is_controller_connected():
                        await self._handle_controller_disconnection()
                        return
                    pil_image = await asyncio.to_thread(self._capture_frame)
                except Exception as exc:
                    logger.exception("监控子页面：开始监控后刷新画面失败：%s", exc)
                else:
                    if pil_image:
                        self._apply_preview_from_pil(pil_image)
            except Exception as exc:
                logger.exception("监控子页面：开始监控失败：%s", exc)
                signalBus.info_bar_requested.emit(
                    "error", self.tr("Failed to start monitoring: ") + str(exc)
                )
            finally:
                # 无论成功还是失败，都重新启用按钮
                self.monitor_control_button.setEnabled(True)

        # 使用 QTimer 延迟发送，防止异步任务阻塞 UI
        QTimer.singleShot(0, lambda: asyncio.create_task(_start_sequence()))

    def _stop_monitoring(self) -> None:
        """停止监控任务"""
        # 立即锁定按钮，防止重复点击
        self.monitor_control_button.setEnabled(False)
        
        async def _stop_sequence():
            try:
                # 停止监控循环
                self._stop_monitor_loop()
                
                # 等待图片处理任务完成（最多等待1秒）
                await self._wait_for_image_processing_complete(timeout=1.0)
                
                # 停止任务
                try:
                    await self.monitor_task.maafw.stop_task()
                except Exception as exc:
                    logger.exception("监控子页面：停止任务失败：%s", exc)
                
                # 销毁连接对象，回到初始状态
                try:
                    if self.monitor_task.maafw.controller:
                        self.monitor_task.maafw.controller = None
                        logger.info("监控子页面：已销毁连接对象")
                except Exception as exc:
                    logger.exception("监控子页面：销毁连接对象失败：%s", exc)
                
                # 更新按钮状态
                self.monitor_control_button.setText(self.tr("Start Monitoring"))
                self.monitor_control_button.setIcon(FIF.PLAY)
                self.monitor_control_button.setToolTip(self.tr("Start monitoring task"))
                
                signalBus.info_bar_requested.emit("success", self.tr("Monitoring stopped"))
            except Exception as exc:
                logger.exception("监控子页面：停止监控失败：%s", exc)
                signalBus.info_bar_requested.emit(
                    "error", self.tr("Failed to stop monitoring: ") + str(exc)
                )
            finally:
                # 无论成功还是失败，都重新启用按钮
                self.monitor_control_button.setEnabled(True)
        
        # 使用 QTimer 延迟发送，防止异步任务阻塞 UI
        QTimer.singleShot(0, lambda: asyncio.create_task(_stop_sequence()))

    def lock_monitor_page(self, stop_loop: bool = True) -> None:
        """停止监控任务（保留此方法以保持向后兼容）。"""
        if stop_loop:
            self._stop_monitor_loop()
        else:
            self._monitoring_active = False
        # 更新按钮状态
        if hasattr(self, 'monitor_control_button'):
            self.monitor_control_button.setText(self.tr("Start Monitoring"))
            self.monitor_control_button.setIcon(FIF.PLAY)
            self.monitor_control_button.setToolTip(self.tr("Start monitoring task"))
