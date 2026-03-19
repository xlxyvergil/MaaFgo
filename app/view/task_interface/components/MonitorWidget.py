from typing import Optional
import asyncio
from datetime import datetime
from pathlib import Path
import numpy as np
import random

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    FluentIcon as FIF,
    PixmapLabel,
    IndeterminateProgressRing,
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
from app.common.config import cfg


class MonitorWidget(QWidget):
    """简化的监控组件，用于嵌入到日志输出组件中"""

    def __init__(self, service_coordinator: ServiceCoordinator, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("MonitorWidget")
        self.service_coordinator = service_coordinator
        self._preview_pixmap: Optional[QPixmap] = None
        self._current_pil_image: Optional[Image.Image] = None
        self._monitoring_active = False
        self._monitor_loop_task: Optional[asyncio.Task] = None
        self._starting_monitoring = False  # 防止重复启动
        self._target_interval = 1.0 / 30
        self._low_power_mode = False  # 低功耗模式标志
        self._low_power_timer: Optional[QTimer] = None  # 低功耗模式使用的定时器

        # 停止监控的幂等/防抖：避免多次 stop 导致并发停止流程引发崩溃
        self._stopping_monitoring: bool = False
        self._stop_debounce_ms: int = 150
        self._stop_debounce_timer = QTimer(self)
        self._stop_debounce_timer.setSingleShot(True)
        self._stop_debounce_timer.timeout.connect(self._stop_monitoring_now)
        
        self.monitor_task = MonitorTask(
            task_service=self.service_coordinator.task_service,
            config_service=self.service_coordinator.config_service,
        )
        
        self._setup_ui()
        self._connect_signals()
        # 预览区默认不显示任何占位图：无图像时保持透明，让父级背景透出
        self._load_placeholder_image()
        self._init_loading_overlay()

    def _setup_ui(self) -> None:
        """设置UI（标题和按钮由外部管理，这里只包含预览区域）"""
        # 让监控预览区背景透明（无图像时不遮挡父级背景）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # 预览区域（使用透明容器包裹，避免不透明背景遮挡父级）
        # 默认横向：16:9比例，宽度344px，高度 = 344 * 9 / 16 = 194px
        # 纵向：9:16比例，宽度194px，高度 = 194 * 16 / 9 = 344px
        self._monitor_width = 344
        self._monitor_height = 194
        self._is_landscape = True  # 默认横向
        
        # 外层容器用普通 QWidget，避免 SimpleCardWidget 这类卡片控件在 paintEvent 里强制绘制底色
        self.preview_card = QWidget(self)
        self.preview_card.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.preview_card.setAutoFillBackground(False)
        self.preview_card.setStyleSheet("background-color: transparent;")
        # 设置初始尺寸（16:9比例）：宽度344px，高度194px
        self.preview_card.setFixedSize(self._monitor_width, self._monitor_height)
        # 设置大小策略为固定，不影响其他组件
        card_policy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.preview_card.setSizePolicy(card_policy)
        
        card_layout = QVBoxLayout(self.preview_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        
        self.preview_label = PixmapLabel(self.preview_card)
        self.preview_label.setObjectName("monitorPreviewLabel")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.preview_label.setAutoFillBackground(False)
        # 设置初始尺寸（16:9比例）：宽度344px，高度194px
        self.preview_label.setFixedSize(self._monitor_width, self._monitor_height)
        # 使用固定大小策略，不影响其他组件
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        # 设置缩放模式：不自动缩放，使用精确尺寸
        self.preview_label.setScaledContents(False)  # 禁用自动缩放，我们手动控制
        self.preview_label.setStyleSheet(
            """
            QLabel#monitorPreviewLabel {
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                background-color: transparent;
            }
            """
        )
        
        card_layout.addWidget(self.preview_label)
        # 不使用拉伸因子，使用固定尺寸
        self.main_layout.addWidget(self.preview_card, 0)
        
        # 设置整个组件为固定大小
        self.setFixedSize(self._monitor_width, self._monitor_height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _init_loading_overlay(self) -> None:
        """初始化加载图标覆盖层"""
        self._loading_overlay = QWidget(self.preview_label)
        self._loading_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._loading_overlay.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._loading_overlay.setStyleSheet(
            "background-color: rgba(0, 0, 0, 60); border-radius: 8px;"
        )
        layout = QHBoxLayout(self._loading_overlay)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._loading_indicator = IndeterminateProgressRing(self._loading_overlay)
        self._loading_indicator.setFixedSize(28, 28)
        layout.addWidget(self._loading_indicator)

        self._loading_overlay.hide()

    def _show_loading_overlay(self) -> None:
        """显示加载图标"""
        if hasattr(self, '_loading_overlay'):
            preview_size = self.preview_label.size()
            self._loading_overlay.setGeometry(0, 0, preview_size.width(), preview_size.height())
            self._loading_overlay.show()
            self._loading_indicator.start()

    def _hide_loading_overlay(self) -> None:
        """隐藏加载图标"""
        if hasattr(self, '_loading_overlay'):
            self._loading_overlay.hide()
            self._loading_indicator.stop()

    def _connect_signals(self):
        """连接信号"""
        # 监听任务开始/停止信号
        if hasattr(self.service_coordinator, 'fs_signals'):
            self.service_coordinator.fs_signals.fs_start_button_status.connect(
                self._on_task_status_changed
            )

        # 监听任务流结束信号：无论何种结束方式，都要停止监控（比按钮状态更及时）
        signalBus.task_flow_finished.connect(self._on_task_flow_finished)

    def _on_task_flow_finished(self, payload: dict) -> None:
        """任务流结束时的处理：停止监控（带防抖/幂等）"""
        if self._monitoring_active or self._starting_monitoring:
            logger.debug(f"[MonitorWidget] 收到 task_flow_finished: {payload}，请求停止监控")
            self._request_stop_monitoring(reason="task_flow_finished")

    def _request_stop_monitoring(self, *, reason: str = "") -> None:
        """请求停止监控（防抖合并多次触发）"""
        # 若既没在监控，也不处于启动流程，则无需处理
        if not (self._monitoring_active or self._starting_monitoring):
            return
        # 如果已经在停止流程中，直接忽略（幂等）
        if self._stopping_monitoring:
            logger.debug(f"[MonitorWidget] stop 已在进行中，忽略重复请求: {reason}")
            return

        # 多次触发合并为一次（restart timer）
        if self._stop_debounce_timer.isActive():
            self._stop_debounce_timer.stop()
        self._stop_debounce_timer.start(self._stop_debounce_ms)

    def _on_task_status_changed(self, status: dict):
        """处理任务状态变化"""
        is_running = status.get("text") == "STOP"
        if is_running and not self._monitoring_active and not self._starting_monitoring:
            # 任务开始，自动开始监控
            self._start_monitoring()
        elif not is_running and self._monitoring_active:
            # 任务停止，自动停止监控
            self._stop_monitoring()

    def _load_placeholder_image(self) -> None:
        """无图像时的占位行为：保持透明，不强制显示灰色占位图。"""
        # 仍保留默认图像尺寸信息（用于后续缩放逻辑/方向判断）
        self._monitor_image_width = 1280
        self._monitor_image_height = 720
        self._is_landscape = True
        self._clear_preview()

    def _clear_preview(self) -> None:
        """清空预览画面：无图像时保持透明，让父级背景透出。"""
        self._preview_pixmap = None
        self._current_pil_image = None
        if hasattr(self, "preview_label"):
            self.preview_label.clear()

    def _refresh_preview_image(self) -> None:
        """刷新预览图像（根据图片尺寸缩放到预览标签）"""
        if not self._preview_pixmap:
            # 无图像时保持透明（不显示灰色占位图）
            self._clear_preview()
            return
        
        # 使用当前的目标尺寸，而不是从标签获取（可能标签还没正确初始化）
        target_width = getattr(self, '_monitor_width', 344)
        target_height = getattr(self, '_monitor_height', 194)
        target_size = QSize(target_width, target_height)
        
        # 保持宽高比缩放，留出边距以显示背景
        # 使用 SmoothTransformation 保证缩放质量
        scaled = self._preview_pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,  # 保持宽高比，留出边距显示背景
            Qt.TransformationMode.SmoothTransformation,
        )
        
        self.preview_label.setPixmap(scaled)

    def _update_component_size(self, image_width: int, image_height: int) -> None:
        """根据图片尺寸更新组件大小"""
        # 判断是横向还是纵向
        is_landscape = image_width >= image_height
        
        # 如果方向没有变化，不需要更新
        if hasattr(self, '_is_landscape') and self._is_landscape == is_landscape:
            # 检查尺寸是否匹配
            if is_landscape:
                if self._monitor_width == 344 and self._monitor_height == 194:
                    return
            else:
                if self._monitor_width == 194 and self._monitor_height == 344:
                    return
        
        # 更新方向标志
        self._is_landscape = is_landscape
        
        # 根据方向设置预览尺寸
        if is_landscape:
            # 横向：1280x720 -> 344x194 (16:9)
            self._monitor_width = 344
            self._monitor_height = 194
        else:
            # 纵向：720x1280 -> 194x344 (9:16)
            self._monitor_width = 194
            self._monitor_height = 344
        
        # 更新组件尺寸
        self.preview_card.setFixedSize(self._monitor_width, self._monitor_height)
        self.preview_label.setFixedSize(self._monitor_width, self._monitor_height)
        self.setFixedSize(self._monitor_width, self._monitor_height)
        
        # 更新加载覆盖层位置
        if hasattr(self, '_loading_overlay') and self._loading_overlay.isVisible():
            self._loading_overlay.setGeometry(0, 0, self._monitor_width, self._monitor_height)

    def _apply_preview_from_pil(self, pil_image: Image.Image) -> None:
        """从 PIL 图像应用预览（支持 1280x720 和 720x1280 两种尺寸）"""
        # 获取图片实际尺寸
        image_width, image_height = pil_image.size
        
        # 更新组件大小以适应图片方向
        self._update_component_size(image_width, image_height)
        
        # 保存原始图片尺寸
        self._monitor_image_width = image_width
        self._monitor_image_height = image_height
        
        # 保持原始图片尺寸，不进行缩放（除非尺寸不匹配）
        # 如果图片尺寸不是预期的两种之一，则缩放到最接近的尺寸
        if (image_width, image_height) not in [(1280, 720), (720, 1280)]:
            # 判断应该使用哪种尺寸
            if image_width >= image_height:
                # 横向，缩放到 1280x720
                target_size = (1280, 720)
            else:
                # 纵向，缩放到 720x1280
                target_size = (720, 1280)
            
            pil_image = pil_image.resize(target_size, Image.Resampling.LANCZOS)
            self._monitor_image_width, self._monitor_image_height = target_size
        
        rgb_image = pil_image.convert("RGB")
        bytes_per_line = self._monitor_image_width * 3
        buffer = rgb_image.tobytes("raw", "RGB")
        qimage = QImage(
            buffer, 
            self._monitor_image_width, 
            self._monitor_image_height, 
            bytes_per_line, 
            QImage.Format.Format_RGB888
        )
        self._preview_pixmap = QPixmap.fromImage(qimage)
        self._current_pil_image = rgb_image.copy()
        # 刷新预览图像
        self._refresh_preview_image()

    def _get_controller(self):
        """获取控制器：优先使用任务流的控制器，如果没有则使用监控任务的控制器"""
        # 优先使用任务流的控制器（如果任务流已连接）
        if hasattr(self.service_coordinator, 'run_manager'):
            task_flow = self.service_coordinator.run_manager
            if task_flow and hasattr(task_flow, 'maafw'):
                controller = getattr(task_flow.maafw, 'controller', None)
                if controller is not None:
                    return controller
        
        # 回退到监控任务的控制器
        controller = getattr(self.monitor_task.maafw, 'controller', None)
        return controller
    
    def _capture_frame(self) -> Image.Image:
        """捕获一帧"""
        controller = self._get_controller()
        if controller is None:
            raise RuntimeError("控制器尚未初始化，无法抓取画面")
        raw_frame = controller.post_screencap().wait().get()
        if raw_frame is None:
            raise ValueError("采集返回空帧")
        return Image.fromarray(raw_frame[..., ::-1])
    
    def _get_cached_image(self) -> Optional[Image.Image]:
        """从缓存的图像中获取一帧（低功耗模式使用）"""
        try:
            controller = self._get_controller()
            if controller is None:
                return None
            
            # 尝试从 cached_image 获取图像
            cached_image = getattr(controller, 'cached_image', None)
            if cached_image is None:
                return None
            
            # cached_image 可能是 numpy array，需要转换为 PIL Image
            if isinstance(cached_image, np.ndarray):
                # 如果是 BGR 格式，需要转换为 RGB
                if len(cached_image.shape) == 3 and cached_image.shape[2] == 3:
                    return Image.fromarray(cached_image[..., ::-1])
                else:
                    return Image.fromarray(cached_image)
            elif isinstance(cached_image, Image.Image):
                return cached_image
            else:
                return None
        except Exception:
            return None

    def _start_monitor_loop(self) -> None:
        """启动监控循环"""
        if self._monitor_loop_task and not self._monitor_loop_task.done():
            logger.debug("[MonitorWidget] 监控循环任务已存在，跳过启动")
            return
        if self._monitoring_active:
            logger.debug("[MonitorWidget] 监控已激活，跳过启动")
            return
        logger.info("[MonitorWidget] 开始启动监控循环（正常模式）")
        suppress_asyncify_logging()
        suppress_qasync_logging()
        self._monitoring_active = True
        try:
            self._monitor_loop_task = asyncio.create_task(self._monitor_loop())
            logger.info("[MonitorWidget] 监控循环任务已创建")
        except Exception as e:
            # 如果创建任务失败，重置状态
            logger.error(f"[MonitorWidget] 创建监控循环任务失败: {e}")
            self._monitoring_active = False
            restore_asyncify_logging()
            restore_qasync_logging()
            raise
    
    def _start_low_power_monitoring(self) -> None:
        """启动低功耗模式监控（使用 QTimer 和 cached_image）"""
        if self._monitoring_active:
            logger.debug("[MonitorWidget] 监控已激活，跳过启动低功耗模式")
            return
        
        logger.info("[MonitorWidget] 开始启动低功耗模式监控")
        self._monitoring_active = True
        self._low_power_mode = True
        
        # 创建定时器，24帧 = 1/24 秒间隔
        self._low_power_timer = QTimer(self)
        self._low_power_timer.timeout.connect(self._low_power_refresh)
        interval_ms = int(1000 / 24)  # 24帧每秒
        self._low_power_timer.start(interval_ms)
        logger.info(f"[MonitorWidget] 低功耗模式定时器已启动，间隔: {interval_ms}ms (24fps)")
        
        # 立即刷新一次
        self._low_power_refresh()
    
    def _low_power_refresh(self) -> None:
        """低功耗模式刷新（从 cached_image 获取图像）"""
        if not self._monitoring_active or not self._low_power_mode:
            return
        
        # 检查控制器是否连接
        if not self._is_controller_connected():
            logger.warning("[MonitorWidget] 低功耗模式中检测到控制器断开")
            self._stop_monitoring()
            return
        
        # 从缓存的图像获取
        pil_image = self._get_cached_image()
        if pil_image:
            self._apply_preview_from_pil(pil_image)
        else:
            # 无图像时显示背景（透明，透出父级背景）
            self._clear_preview()
            # 偶尔输出日志，避免日志过多
            if random.random() < 0.01:  # 约1%的概率输出日志
                logger.debug("[MonitorWidget] 低功耗模式：无法从缓存获取图像")

    def _stop_monitor_loop(self) -> None:
        """停止监控循环"""
        logger.debug("[MonitorWidget] 停止监控循环")
        self._monitoring_active = False
        task = self._monitor_loop_task
        self._monitor_loop_task = None
        if task and not task.done():
            logger.debug("[MonitorWidget] 取消监控循环任务")
            task.cancel()
        restore_asyncify_logging()
        restore_qasync_logging()
    
    def _stop_low_power_monitoring(self) -> None:
        """停止低功耗模式监控"""
        logger.debug("[MonitorWidget] 停止低功耗模式监控")
        self._monitoring_active = False
        self._low_power_mode = False
        if self._low_power_timer:
            self._low_power_timer.stop()
            self._low_power_timer = None
            logger.debug("[MonitorWidget] 低功耗模式定时器已停止")

    async def _monitor_loop(self) -> None:
        """监控循环"""
        loop = asyncio.get_running_loop()
        frame_count = 0
        logger.info("[MonitorWidget] 监控循环已开始运行")
        try:
            while self._monitoring_active:
                start = loop.time()
                if not self._is_controller_connected():
                    logger.warning("[MonitorWidget] 监控循环中检测到控制器断开")
                    await self._handle_controller_disconnection()
                    return
                try:
                    pil_image = await asyncio.to_thread(self._capture_frame)
                except Exception as e:
                    logger.warning(f"[MonitorWidget] 捕获帧失败: {e}")
                    pil_image = None
                if pil_image:
                    frame_count += 1
                    # 每30帧输出一次日志（约每秒一次）
                    if frame_count % 30 == 0:
                        logger.debug(f"[MonitorWidget] 监控循环运行中，已捕获 {frame_count} 帧")
                    self._apply_preview_from_pil(pil_image)
                else:
                    # 没有有效图像时，显示背景（透明，透出父级背景）
                    self._clear_preview()
                elapsed = loop.time() - start
                wait = max(0, self._get_target_interval() - elapsed)
                await asyncio.sleep(wait)
        except asyncio.CancelledError:
            logger.info(f"[MonitorWidget] 监控循环被取消，共捕获 {frame_count} 帧")
            pass
        finally:
            logger.info(f"[MonitorWidget] 监控循环结束，共捕获 {frame_count} 帧")
            self._monitor_loop_task = None
            restore_asyncify_logging()
            restore_qasync_logging()

    def _get_target_interval(self) -> float:
        """获取目标间隔"""
        # 低功耗模式下使用24帧，否则使用30帧
        if self._low_power_mode:
            return 1.0 / 24
        return self._target_interval

    def _is_controller_connected(self) -> bool:
        """检查控制器是否连接：优先检查任务流的控制器"""
        # 优先检查任务流的控制器
        if hasattr(self.service_coordinator, 'run_manager'):
            task_flow = self.service_coordinator.run_manager
            if task_flow and hasattr(task_flow, 'maafw'):
                controller = getattr(task_flow.maafw, 'controller', None)
                if controller is not None:
                    connected = getattr(controller, "connected", None)
                    if connected is not False:
                        return True
        
        # 回退到监控任务的控制器
        controller = getattr(self.monitor_task.maafw, "controller", None)
        if controller is None:
            return False
        connected = getattr(controller, "connected", None)
        return connected is not False
    
    def _check_task_flow_controller_ready(self) -> bool:
        """检查任务流的控制器是否就绪（存在且connected为true）"""
        if not hasattr(self.service_coordinator, 'run_manager'):
            return False
        
        task_flow = self.service_coordinator.run_manager
        if not task_flow or not hasattr(task_flow, 'maafw'):
            return False
        
        maafw = task_flow.maafw
        controller = getattr(maafw, 'controller', None)
        if controller is None:
            return False
        
        connected = getattr(controller, 'connected', None)
        is_ready = connected is True
        return is_ready
    
    async def _get_required_wait_time(self) -> float:
        """从配置中获取需要的等待时间（如果配置了启动模拟器或程序）"""
        from app.common.constants import _CONTROLLER_
        
        try:
            # 获取控制器配置
            controller_cfg = self.service_coordinator.task_service.get_task(_CONTROLLER_)
            if not controller_cfg:
                return 0.0
            
            controller_raw = controller_cfg.task_option
            if not isinstance(controller_raw, dict):
                return 0.0
            
            # 获取控制器类型和名称
            controller_type = self._get_controller_type_from_config(controller_raw)
            controller_name = self._get_controller_name_from_config(controller_raw)
            
            # 获取控制器配置
            if controller_name in controller_raw:
                controller_config = controller_raw[controller_name]
            elif controller_type in controller_raw:
                controller_config = controller_raw[controller_type]
            else:
                controller_config = {}
            
            # 根据控制器类型检查等待时间
            if controller_type == "adb":
                # ADB 控制器：检查是否有模拟器路径和等待时间
                if controller_config.get("emulator_path", ""):
                    wait_time = int(controller_config.get("wait_time", 0))
                    return float(wait_time)
            elif controller_type == "win32":
                # Win32 控制器：检查是否有程序路径和等待时间
                if controller_config.get("program_path", ""):
                    wait_time = int(controller_config.get("wait_time", 0))
                    return float(wait_time)
            
            return 0.0
        except Exception:
            # 如果获取配置失败，返回0（不等待）
            return 0.0
    
    async def _get_controller_wait_time(self) -> float:
        """从配置中获取控制器的wait_time（不管是否有模拟器路径）"""
        from app.common.constants import _CONTROLLER_
        
        try:
            # 获取控制器配置
            controller_cfg = self.service_coordinator.task_service.get_task(_CONTROLLER_)
            if not controller_cfg:
                return 30.0  # 默认30秒
            
            controller_raw = controller_cfg.task_option
            if not isinstance(controller_raw, dict):
                return 30.0  # 默认30秒
            
            # 获取控制器类型和名称
            controller_type = self._get_controller_type_from_config(controller_raw)
            controller_name = self._get_controller_name_from_config(controller_raw)
            
            # 获取控制器配置
            if controller_name in controller_raw:
                controller_config = controller_raw[controller_name]
            elif controller_type in controller_raw:
                controller_config = controller_raw[controller_type]
            else:
                controller_config = {}
            
            # 根据控制器类型获取等待时间
            if controller_type == "adb":
                # ADB 控制器：获取wait_time（可能是字符串或数字）
                wait_time_str = controller_config.get("wait_time", "30")
                try:
                    wait_time = float(wait_time_str) if isinstance(wait_time_str, str) else float(wait_time_str)
                    return wait_time
                except (ValueError, TypeError):
                    return 30.0  # 默认30秒
            elif controller_type == "win32":
                # Win32 控制器：获取wait_time
                wait_time_str = controller_config.get("wait_time", "30")
                try:
                    wait_time = float(wait_time_str) if isinstance(wait_time_str, str) else float(wait_time_str)
                    return wait_time
                except (ValueError, TypeError):
                    return 30.0  # 默认30秒
            elif controller_type == "playcover":
                # PlayCover 控制器：目前不需要等待时间，但为了代码一致性，支持配置wait_time
                wait_time_str = controller_config.get("wait_time", "30")
                try:
                    wait_time = float(wait_time_str) if isinstance(wait_time_str, str) else float(wait_time_str)
                    return wait_time
                except (ValueError, TypeError):
                    return 30.0  # 默认30秒
            
            return 30.0  # 默认30秒
        except Exception:
            # 如果获取配置失败，返回默认30秒
            return 30.0
    
    def _get_controller_type_from_config(self, controller_raw: dict) -> str:
        """从配置中获取控制器类型"""
        try:
            controller_config = controller_raw.get("controller_type", {})
            if isinstance(controller_config, str):
                controller_name = controller_config
            elif isinstance(controller_config, dict):
                controller_name = controller_config.get("value", "")
            else:
                controller_name = ""
            
            controller_name = controller_name.lower()
            for controller in self.service_coordinator.task_service.interface.get("controller", []):
                if controller.get("name", "").lower() == controller_name:
                    return controller.get("type", "").lower()
            
            return ""
        except Exception:
            return ""
    
    def _get_controller_name_from_config(self, controller_raw: dict) -> str:
        """从配置中获取控制器名称"""
        try:
            controller_config = controller_raw.get("controller_type", {})
            if isinstance(controller_config, str):
                return controller_config
            elif isinstance(controller_config, dict):
                return controller_config.get("value", "")
            return ""
        except Exception:
            return ""

    async def _handle_controller_disconnection(self) -> None:
        """处理控制器断开"""
        # 既没在监控也没在启动流程时，无需处理
        if not (self._monitoring_active or self._starting_monitoring):
            return
        logger.warning("[MonitorWidget] 检测到控制器断开连接，请求停止监控（防抖/幂等）")
        # 统一走防抖/幂等停止流程，避免与 task_flow_finished / UI 停止并发触发导致崩溃
        self._request_stop_monitoring(reason="controller_disconnected")
        # 立刻让监控循环退出，避免在断开状态下继续反复触发断开处理
        self._monitoring_active = False

    def _update_button_state(self):
        """更新按钮状态（已废弃，保留以保持兼容性）"""
        pass

    def resizeEvent(self, event) -> None:
        """处理尺寸变化（固定尺寸，只刷新图像）"""
        super().resizeEvent(event)
        self._refresh_preview_image()
        # 更新加载图标覆盖层的位置
        if hasattr(self, '_loading_overlay') and self._loading_overlay.isVisible():
            preview_size = self.preview_label.size()
            self._loading_overlay.setGeometry(0, 0, preview_size.width(), preview_size.height())

    def _on_save_screenshot(self) -> None:
        """保存截图"""
        if not self._current_pil_image:
            signalBus.info_bar_requested.emit("warning", self.tr("No screenshot available to save"))
            return
        save_dir = Path("debug") / "save_screen"
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
        save_path = save_dir / filename
        try:
            self._current_pil_image.save(save_path)
            message = self.tr("Screenshot saved to ") + str(save_path)
            signalBus.info_bar_requested.emit("success", message)
        except Exception as exc:
            signalBus.info_bar_requested.emit("error", self.tr("Failed to save screenshot: ") + str(exc))

    def _on_monitor_control_clicked(self) -> None:
        """处理开始/停止监控按钮点击"""
        if self._monitoring_active:
            self._stop_monitoring()
        elif self._starting_monitoring:
            # 如果正在启动过程中，停止启动
            self._starting_monitoring = False
            self._update_button_state()
        else:
            self._start_monitoring()

    def _start_monitoring(self) -> None:
        """开始监控"""
        # 防止重复启动
        if self._monitoring_active or self._starting_monitoring:
            logger.debug("[MonitorWidget] 监控已在运行或正在启动，跳过")
            return
        
        logger.info("[MonitorWidget] 开始启动监控流程")
        self._starting_monitoring = True
        
        # 先显示占位图
        self._load_placeholder_image()
        
        async def _start_sequence():
            try:
                # 检查任务流的控制器是否就绪
                if not self._check_task_flow_controller_ready():
                    logger.info("[MonitorWidget] 任务流控制器未就绪，开始等待连接...")
                    # 显示加载图标
                    self._show_loading_overlay()
                    
                    # 从配置中获取wait_time，并加1秒作为备用时间
                    config_wait_time = await self._get_controller_wait_time()
                    max_wait_time = config_wait_time + 1.0  # 配置的等待时间 + 1秒备用
                    check_interval = 0.1  # 每100ms检查一次
                    waited_time = 0.0
                    
                    while waited_time < max_wait_time:
                        if not self._starting_monitoring:
                            # 用户点击了停止按钮
                            logger.info("[MonitorWidget] 用户取消了监控启动")
                            self._hide_loading_overlay()
                            return
                        
                        # 检查控制器是否就绪
                        if self._check_task_flow_controller_ready():
                            # 控制器就绪，隐藏加载图标并继续
                            logger.info(f"[MonitorWidget] 控制器已就绪，等待时间: {waited_time:.1f}秒")
                            self._hide_loading_overlay()
                            break
                        
                        await asyncio.sleep(check_interval)
                        waited_time += check_interval
                    
                    # 再次检查是否就绪
                    if not self._check_task_flow_controller_ready():
                        logger.warning(f"[MonitorWidget] 等待超时，控制器仍未就绪 (等待了 {waited_time:.1f}秒)")
                        if self._starting_monitoring:  # 只有在未被停止的情况下才显示错误
                            signalBus.info_bar_requested.emit(
                                "error", self.tr("Controller not ready. Please ensure the device is connected.")
                            )
                        self._hide_loading_overlay()
                        self._starting_monitoring = False
                        self._update_button_state()
                        return
                
                # 根据配置决定使用哪种监控模式
                use_low_power = cfg.get(cfg.low_power_monitoring_mode)
                logger.info(f"[MonitorWidget] 使用监控模式: {'低功耗模式' if use_low_power else '正常模式'}")
                
                if use_low_power:
                    # 低功耗模式：使用 QTimer 和 cached_image
                    self._start_low_power_monitoring()
                    logger.info("[MonitorWidget] 低功耗模式监控已启动")
                else:
                    # 正常模式：使用异步监控循环
                    self._start_monitor_loop()
                    
                    # 等待一小段时间确保循环已启动
                    await asyncio.sleep(0.1)
                    
                    # 检查监控是否真的启动了
                    if not self._monitoring_active:
                        logger.error("[MonitorWidget] 监控循环启动失败")
                        if self._starting_monitoring:  # 只有在未被停止的情况下才显示错误
                            signalBus.info_bar_requested.emit(
                                "error", self.tr("Failed to start monitoring loop")
                            )
                        self._starting_monitoring = False
                        self._update_button_state()
                        return
                    logger.info("[MonitorWidget] 正常模式监控循环已启动")
                
                self._update_button_state()
                signalBus.info_bar_requested.emit("success", self.tr("Monitoring started"))
                
                # 尝试捕获第一帧（仅正常模式）
                if not use_low_power:
                    try:
                        if not self._is_controller_connected():
                            logger.warning("[MonitorWidget] 控制器未连接，无法捕获第一帧")
                            await self._handle_controller_disconnection()
                            return
                        pil_image = await asyncio.to_thread(self._capture_frame)
                        if pil_image:
                            self._apply_preview_from_pil(pil_image)
                    except Exception as e:
                        logger.warning(f"[MonitorWidget] 捕获第一帧失败: {e}")
                        # 静默处理错误，不输出到日志组件
                        pass
            except Exception as exc:
                logger.error(f"[MonitorWidget] 启动监控流程失败: {exc}", exc_info=True)
                if self._starting_monitoring:  # 只有在未被停止的情况下才显示错误
                    signalBus.info_bar_requested.emit(
                        "error", self.tr("Failed to start monitoring: ") + str(exc)
                    )
                self._hide_loading_overlay()
                self._starting_monitoring = False
                if self._monitoring_active:
                    self._stop_monitor_loop()
            finally:
                self._hide_loading_overlay()
                self._starting_monitoring = False
                self._update_button_state()

        QTimer.singleShot(0, lambda: asyncio.create_task(_start_sequence()))

    def _stop_monitoring(self) -> None:
        """停止监控（防抖/幂等包装）"""
        self._request_stop_monitoring(reason="manual_or_ui")

    def _stop_monitoring_now(self) -> None:
        """立即停止监控（内部实现，避免直接在外部多次调用）"""
        if self._stopping_monitoring:
            logger.debug("[MonitorWidget] 已在停止监控流程中，忽略重复调用")
            return

        self._stopping_monitoring = True
        logger.info("[MonitorWidget] 开始停止监控")
        # 设置停止标志，中断等待过程
        self._starting_monitoring = False
        
        async def _stop_sequence():
            try:
                # 隐藏加载图标
                self._hide_loading_overlay()
                
                # 根据模式停止相应的监控
                if self._low_power_mode:
                    logger.info("[MonitorWidget] 停止低功耗模式监控")
                    self._stop_low_power_monitoring()
                else:
                    logger.info("[MonitorWidget] 停止正常模式监控循环")
                    self._stop_monitor_loop()
                
                try:
                    logger.debug("[MonitorWidget] 停止监控任务")
                    await self.monitor_task.maafw.stop_task()
                except Exception as e:
                    logger.warning(f"[MonitorWidget] 停止监控任务时出错: {e}")
                    # 静默处理错误，不输出到日志组件
                    pass
                
                try:
                    if self.monitor_task.maafw.controller:
                        logger.debug("[MonitorWidget] 清除监控任务控制器引用")
                        self.monitor_task.maafw.controller = None
                except Exception as e:
                    logger.warning(f"[MonitorWidget] 清除控制器引用时出错: {e}")
                    # 静默处理错误，不输出到日志组件
                    pass
                
                # 停止监控后显示占位图
                self._load_placeholder_image()
                
                self._update_button_state()
                logger.info("[MonitorWidget] 监控已停止")
                signalBus.info_bar_requested.emit("success", self.tr("Monitoring stopped"))
            except Exception as exc:
                logger.error(f"[MonitorWidget] 停止监控流程失败: {exc}", exc_info=True)
                signalBus.info_bar_requested.emit(
                    "error", self.tr("Failed to stop monitoring: ") + str(exc)
                )
            finally:
                # 确保幂等标志最终被释放
                self._stopping_monitoring = False
        
        QTimer.singleShot(0, lambda: asyncio.create_task(_stop_sequence()))

    def _on_open_monitor_dialog(self) -> None:
        """打开监控对话框"""
        from app.view.monitor_interface.monitor_interface import MonitorInterface
        from qfluentwidgets import MessageBoxBase
        from PySide6.QtWidgets import QApplication
        
        # 保存当前监控状态
        was_monitoring = self._monitoring_active
        
        class MonitorDialog(MessageBoxBase):
            """监控对话框"""
            def __init__(self, service_coordinator: ServiceCoordinator, was_monitoring: bool, parent=None):
                super().__init__(parent)
                self.setWindowTitle(self.tr("Monitor"))
                self.setMinimumSize(800, 600)
                
                # 获取屏幕大小，设置合理的初始窗口大小
                screen = QApplication.primaryScreen()
                if screen:
                    screen_size = screen.availableGeometry()
                    # 初始大小为屏幕的 70%
                    self.resize(
                        int(screen_size.width() * 0.7),
                        int(screen_size.height() * 0.7)
                    )
                
                # 创建监控界面
                self.monitor_interface = MonitorInterface(service_coordinator, self)
                
                # 设置布局
                layout = QVBoxLayout(self.widget)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.addWidget(self.monitor_interface)
                
                # 如果之前正在监控，同步状态到对话框中的监控界面
                if was_monitoring:
                    # 延迟启动监控，确保对话框已显示
                    QTimer.singleShot(100, lambda: self.monitor_interface._start_monitoring())
        
        dialog = MonitorDialog(self.service_coordinator, was_monitoring, self)
        dialog.exec()


    def _schedule_controller_disconnection(self) -> None:
        """安排控制器断开处理"""
        if not self._monitoring_active:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(self._handle_controller_disconnection())

