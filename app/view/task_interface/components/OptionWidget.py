import hashlib
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QSplitter
from qfluentwidgets import BodyLabel, ScrollArea, SimpleCardWidget, SegmentedWidget

from app.utils.logger import logger
from app.utils.markdown_helper import render_markdown
from app.view.task_interface.animations.optionwidget import (
    DescriptionTransitionAnimator,
    OptionTransitionAnimator,
)
from app.view.task_interface.components.ImagePreviewDialog import ImagePreviewDialog
from app.view.task_interface.components.Option_Framework import (
    OptionFormWidget,
    SpeedrunConfigWidget,
)
from app.view.task_interface.components.Option_Widget_Mixin.PostActionSettingMixin import (
    PostActionSettingMixin,
)
from app.view.task_interface.components.Option_Widget_Mixin.ResourceSettingMixin import (
    ResourceSettingMixin,
)
from app.view.task_interface.components.Option_Widget_Mixin.ControllerSettingMixin import (
    ControllerSettingWidget,
)
from ....core.core import ServiceCoordinator


class OptionWidget(QWidget, ResourceSettingMixin, PostActionSettingMixin):
    current_config: Dict[str, Any]
    parent_layout: QVBoxLayout

    def __init__(
        self,
        service_coordinator: ServiceCoordinator,
        parent=None,
    ):
        # 先调用QWidget的初始化
        self.service_coordinator = service_coordinator
        QWidget.__init__(self, parent)

        # 初始化UI组件（需要先创建布局）
        self._init_ui()

        # 初始化控制器设置组件（仍然使用 Widget 方式，因为它比较复杂）
        self.controller_setting_widget = ControllerSettingWidget(
            service_coordinator, self.option_page_layout
        )

        # 设置控制器组件的 current_config
        self.current_config = self.controller_setting_widget.current_config

        # 设置控制器组件的回调方法
        self.controller_setting_widget._clear_options = self._clear_options
        self.controller_setting_widget._set_description = self.set_description
        self.controller_setting_widget._toggle_description = self._toggle_description

        # 初始化 Resource 和 PostAction Mixin（它们现在是 OptionWidget 的一部分）
        self._init_resource_settings()
        self._init_post_action_settings()
        
        # 共享控制器相关属性到 Resource Mixin（ResourceSettingMixin 需要）
        self.controller_type_mapping = self.controller_setting_widget.controller_type_mapping
        self.current_controller_label = self.controller_setting_widget.current_controller_label

        # 设置控制器类型变化时的回调，用于更新资源下拉框
        def on_controller_type_changed(label, is_initializing=False):
            """当控制器类型变化时，更新资源下拉框

            Args:
                label: 控制器标签
                is_initializing: 是否在初始化阶段（如果是，不触发任务列表更新）
            """
            # 更新资源 Mixin 的控制器标签
            self.current_controller_label = label
            # 更新资源 Mixin 的控制器映射（以防有变化）
            self.controller_type_mapping = (
                self.controller_setting_widget.controller_type_mapping
            )
            # 重新构建资源映射表（确保使用最新的控制器信息）
            self._rebuild_resource_mapping()

            if label not in self.resource_mapping:
                logger.warning(
                    f"控制器 {label} 不在资源映射表中！可用的控制器: {list(self.resource_mapping.keys())}"
                )

            # 刷新资源下拉框（如果资源下拉框已创建）
            if (
                "resource_combo"
                in self.resource_setting_widgets
            ):
                if hasattr(self, "_fill_resource_option"):
                    self._fill_resource_option()

                    # 控制器类型变化后，强制保存一次资源设置（仅在非初始化时）
                    if not is_initializing:
                        current_resource = (
                            self.current_config.get(
                                "resource", ""
                            )
                        )
                        if current_resource:
                            self._auto_save_resource_option(
                                current_resource, skip_sync_check=True
                            )
                            # 触发任务列表更新（延迟触发，确保资源已保存）
                            from PySide6.QtCore import QTimer

                            QTimer.singleShot(
                                50,
                                lambda: self.service_coordinator.signal_bus.option_updated.emit(
                                    {"resource": current_resource}
                                ),
                            )
            else:
                # 即使资源下拉框不存在，也需要更新资源任务的配置
                # 检查当前资源是否在新控制器的资源列表中
                if label in self.resource_mapping:
                    current_resources = self.resource_mapping[
                        label
                    ]
                    if current_resources:
                        # 获取当前保存的资源
                        from app.common.constants import _RESOURCE_

                        resource_task = self.service_coordinator.task_service.get_task(
                            _RESOURCE_
                        )
                        current_resource_name = ""
                        if resource_task and isinstance(
                            resource_task.task_option, dict
                        ):
                            current_resource_name = resource_task.task_option.get(
                                "resource", ""
                            )

                        # 检查当前资源是否在新控制器的资源列表中
                        resource_found = False
                        for resource in current_resources:
                            resource_name = resource.get("name", "")
                            resource_label = resource.get("label", resource_name)
                            if current_resource_name and current_resource_name in (
                                resource_name,
                                resource_label,
                            ):
                                resource_found = True
                                break

                        # 如果当前资源不在新控制器的资源列表中，自动选择第一个资源并保存（仅在非初始化时）
                        if (
                            not resource_found
                            and current_resource_name
                            and not is_initializing
                        ):
                            first_resource = current_resources[0]
                            first_resource_name = first_resource.get("name", "")

                            # 更新配置并保存
                            if resource_task:
                                resource_task.task_option["resource"] = (
                                    first_resource_name
                                )
                                self.service_coordinator.task_service.update_task(
                                    resource_task
                                )

                                # 触发任务列表更新（延迟触发，确保资源任务已保存）
                                from PySide6.QtCore import QTimer

                                QTimer.singleShot(
                                    50,
                                    lambda: self.service_coordinator.signal_bus.option_updated.emit(
                                        {"resource": first_resource_name}
                                    ),
                                )
                            else:
                                logger.warning(f"未找到 Resource 任务，无法保存资源")
                        elif not is_initializing:
                            # 即使资源没有变化，也要触发任务列表更新（确保任务列表根据当前资源正确显示）
                            if resource_task and isinstance(
                                resource_task.task_option, dict
                            ):
                                final_resource = resource_task.task_option.get(
                                    "resource", ""
                                )
                                if final_resource:
                                    # 使用 QTimer 延迟触发，确保资源任务已保存
                                    from PySide6.QtCore import QTimer

                                    QTimer.singleShot(
                                        50,
                                        lambda: self.service_coordinator.signal_bus.option_updated.emit(
                                            {"resource": final_resource}
                                        ),
                                    )

        # 将回调设置到控制器组件
        setattr(
            self.controller_setting_widget,
            "_on_controller_changed_callback",
            on_controller_type_changed,
        )
        self.speedrun_widget.config_changed.connect(self._on_speedrun_changed)

        # 连接CoreSignalBus的options_loaded信号
        service_coordinator.signal_bus.options_loaded.connect(self._on_options_loaded)
        service_coordinator.signal_bus.config_changed.connect(self._on_config_changed)
        
        # 监听运行状态变化，禁用/启用选项编辑
        from app.common.signal_bus import signalBus
        signalBus.task_status_changed.connect(self._on_task_status_changed)
        
        # 初始化时隐藏描述区域
        self._toggle_description(visible=False, animate=False)
        
        # 初始化时检查运行状态并设置选项可用性
        self._update_options_enabled()

    def _init_ui(self):
        """初始化UI"""
        self.main_layout = QVBoxLayout(self)

        self.title_widget = BodyLabel(self.tr("Options"))
        self.title_widget.setStyleSheet("font-size: 20px;")
        self.title_widget.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # ==================== 选项区域 ==================== #
        # 创建选项卡片
        self.option_area_card = SimpleCardWidget()
        self.option_area_card.setClickEnabled(False)
        self.option_area_card.setBorderRadius(8)

        # 创建滚动区域
        self.option_area_widget = ScrollArea()
        self.option_area_widget.setWidgetResizable(
            True
        )  # 重要:允许内部widget自动调整大小
        # 禁用横向滚动
        self.option_area_widget.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.option_area_widget.enableTransparentBackground()
        # 设置透明无边框
        self.option_area_widget.setStyleSheet(
            "background-color: transparent; border: none;"
        )

        # 创建一个容器widget来承载布局
        option_container = QWidget()
        self.option_area_layout = QVBoxLayout(option_container)  # 将布局关联到容器
        self.option_area_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.option_area_layout.setContentsMargins(10, 10, 10, 10)  # 添加内边距

        # 堆叠页面：0 选项；1 速通规则
        self.option_stack = QStackedWidget()
        self.option_area_layout.addWidget(self.option_stack)

        # 选项页
        self.option_page = QWidget()
        self.option_page_layout = QVBoxLayout(self.option_page)
        self.option_page_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.option_page_layout.setContentsMargins(0, 0, 0, 0)

        # 保存布局引用（某些 Mixin 可能需要）
        self.parent_layout = self.option_page_layout

        # 创建新的选项表单组件
        self.option_form_widget = OptionFormWidget()
        self.option_page_layout.addWidget(self.option_form_widget)

        # 速通配置页
        self.speedrun_widget = SpeedrunConfigWidget()
        speedrun_page = QWidget()
        speedrun_layout = QVBoxLayout(speedrun_page)
        speedrun_layout.setContentsMargins(0, 0, 0, 0)
        speedrun_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        speedrun_layout.addWidget(self.speedrun_widget)
        speedrun_layout.addStretch()

        self.option_stack.addWidget(self.option_page)
        self.option_stack.addWidget(speedrun_page)
        self.option_stack.setCurrentIndex(0)

        # 将容器widget设置到滚动区域
        self.option_area_widget.setWidget(option_container)
        # 初始化过渡动画器
        self._option_animator = OptionTransitionAnimator(option_container)

        # 底部分段切换（固定在卡片底部，不随滚动）
        self.segmented_switcher = SegmentedWidget(self)
        self.segmented_switcher.addItem(routeKey="options", text=self.tr("Options"))
        self.segmented_switcher.addItem(routeKey="speedrun", text=self.tr("Speedrun"))
        self.segmented_switcher.currentItemChanged.connect(self._on_segmented_changed)
        self.segmented_switcher.setCurrentItem("options")
        # 初始化时隐藏，待任务加载后按需显示
        self.segmented_switcher.setVisible(False)

        # 创建一个垂直布局给卡片,然后将滚动区域和切换栏添加到这个布局中
        card_layout = QVBoxLayout()
        card_layout.addWidget(self.option_area_widget, 1)  # 滚动区域占满剩余空间
        card_layout.addWidget(self.segmented_switcher, 0, Qt.AlignmentFlag.AlignBottom)  # 切换栏固定在底部
        card_layout.setContentsMargins(10, 0, 10, 10)
        card_layout.setSpacing(5)
        self.option_area_card.setLayout(card_layout)

        # ==================== 描述区域 ==================== #
        # 创建描述标题（直接放在主布局中）
        self.description_title = BodyLabel(self.tr("Function Description"))
        self.description_title.setStyleSheet("font-size: 20px;")
        self.description_title.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # 创建描述卡片
        self.description_area_card = SimpleCardWidget()
        self.description_area_card.setClickEnabled(False)
        self.description_area_card.setBorderRadius(8)

        # 正确的布局层次结构
        self.description_area_widget = (
            QWidget()
        )  # 使用普通Widget作为容器，而不是ScrollArea
        self.description_layout = QVBoxLayout(
            self.description_area_widget
        )  # 这个布局只属于widget
        self.description_layout.setContentsMargins(10, 10, 10, 10)  # 设置适当的边距

        # 描述内容区域
        self.description_content = BodyLabel()
        self.description_content.setWordWrap(True)
        self.description_content.setTextFormat(Qt.TextFormat.RichText)
        # 启用链接点击交互（用于图片点击）
        self.description_content.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.description_content.setOpenExternalLinks(False)  # 不自动打开外部链接
        self.description_content.linkActivated.connect(self._on_link_activated)
        self.description_content.setContextMenuPolicy(
            Qt.ContextMenuPolicy.NoContextMenu
        )
        self.description_content.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.description_layout.addWidget(self.description_content)

        self.description_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 创建滚动区域来包裹内容
        self.description_scroll_area = ScrollArea()
        self.description_scroll_area.setWidget(self.description_area_widget)
        self.description_scroll_area.setWidgetResizable(True)
        self.description_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.description_scroll_area.enableTransparentBackground()
        self.description_scroll_area.setStyleSheet(
            "background-color: transparent; border: none;"
        )

        # 将滚动区域添加到卡片
        card_layout = QVBoxLayout(self.description_area_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.addWidget(self.description_scroll_area)

        # ==================== 分割器 ==================== #
        # 创建垂直分割器，实现可调整比例功能
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setStyleSheet(
            """
            QSplitter::handle:vertical {
                background: transparent;   
            }
            """
        )

        # 创建选项区域容器（仅用于分割器）
        self.option_splitter_widget = QWidget()
        self.option_splitter_layout = QVBoxLayout(self.option_splitter_widget)
        self.option_splitter_layout.addWidget(self.option_area_card)
        self.option_splitter_layout.setContentsMargins(0, 13, 0, 0)

        # 创建描述区域容器（仅用于分割器）
        self.description_splitter_widget = QWidget()
        self.description_splitter_layout = QVBoxLayout(self.description_splitter_widget)
        self.description_splitter_layout.addWidget(self.description_title)
        self.description_splitter_layout.addWidget(self.description_area_card)
        # 设置占用比例
        self.description_splitter_layout.setStretch(0, 1)  # 标题占用1单位
        self.description_splitter_layout.setStretch(1, 99)  # 内容占用99单位
        self.description_splitter_layout.setContentsMargins(0, 0, 0, 0)

        # 添加到分割器
        self.splitter.addWidget(self.option_splitter_widget)  # 上方：选项区域
        self.splitter.addWidget(self.description_splitter_widget)  # 下方：描述区域

        # 设置初始比例
        self.splitter.setSizes([90, 10])  # 90% 和 10% 的初始比例
        self._description_animator = DescriptionTransitionAnimator(
            self.splitter,
            self.description_splitter_widget,
            content_widget=self.description_content,  # 传入内容控件用于计算实际高度
            max_ratio=0.5,  # 默认最大比例50%
            min_height=90,
        )

        # 添加到主布局
        self.main_layout.addWidget(self.title_widget)  # 直接添加标题
        self.main_layout.addWidget(self.splitter)  # 添加分割器
        # 添加主布局间距
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(5)

    # ==================== UI 辅助方法 ==================== #

    def _on_segmented_changed(self, key: str) -> None:
        """通过分段控件切换主选项/速通页"""
        if key == "speedrun":
            self.option_stack.setCurrentIndex(1)
        else:
            self.option_stack.setCurrentIndex(0)

    def _set_speedrun_visible(self, visible: bool) -> None:
        """控制速通页显隐（资源/完成后任务隐藏）"""
        self.segmented_switcher.setVisible(visible)
        if not visible:
            # 只显示常规选项页
            self.option_stack.setCurrentIndex(0)
            self.segmented_switcher.setCurrentItem("options")
            self.speedrun_widget.set_config(None, emit=False)

    def _apply_speedrun_config(self) -> None:
        """加载当前任务的速通配置到 UI"""
        option_service = self.service_coordinator.option
        task_service = self.service_coordinator.task
        task_id = getattr(option_service, "current_task_id", None)
        task = task_service.get_task(task_id) if task_id else None
        if not task:
            self.speedrun_widget.set_config(None, emit=False)
            return

        # 特殊任务不显示速通配置
        if getattr(task, "is_special", False):
            self._set_speedrun_visible(False)
            return

        existing_cfg = (
            task.task_option.get("_speedrun_config")
            if isinstance(task.task_option, dict)
            else None
        )
        merged_cfg = task_service.build_speedrun_config(task.name, existing_cfg)

        # 如果缺失或需要修正，持久化到任务
        if not isinstance(task.task_option, dict):
            task.task_option = {}
        if task.task_option.get("_speedrun_config") != merged_cfg:
            task.task_option["_speedrun_config"] = merged_cfg
            task_service.update_task(task)

        try:
            option_service.current_options["_speedrun_config"] = merged_cfg
        except Exception:
            pass

        state = {}
        if isinstance(task.task_option, dict):
            state = task.task_option.get("_speedrun_state", {}) or {}

        self.speedrun_widget.set_config(merged_cfg, emit=False)
        try:
            self.speedrun_widget.set_runtime_state(state, merged_cfg)
        except Exception as exc:
            logger.warning(f"设置速通运行时信息失败: {exc}")

    def _on_speedrun_changed(self, config: Dict[str, Any]) -> None:
        """速通配置修改后立即保存到当前任务"""
        try:
            self.service_coordinator.option.update_option("_speedrun_config", config)
        except Exception as exc:
            logger.error(f"保存速通配置失败: {exc}")

    def set_description(self, description: str, has_options: bool = True):
        """设置描述内容
        
        :param description: 描述内容
        :param has_options: 是否有选项内容，用于确定公告最大占比
                           - True: 最大占比50%
                           - False: 最大占比100%
        """
        # 处理 None 或空字符串
        if not description:
            description = ""
        
        # 如果description为空，隐藏描述区域
        if not description.strip():
            self.description_content.setText("")
            self._toggle_description(visible=False)
            return

        # 计算新的最大比例
        new_max_ratio = 0.5 if has_options else 1.0
        # 获取旧的最大比例来判断是否有变化
        old_max_ratio = self._description_animator.max_ratio
        ratio_changed = abs(new_max_ratio - old_max_ratio) > 0.1
        
        # 设置最大比例：有选项时50%，无选项时100%
        self._description_animator.set_max_ratio(new_max_ratio)

        html = render_markdown(description)
        html = self._process_remote_images(html)
        
        # 检查公告是否已经展开
        was_expanded = self._description_animator.is_expanded()
        
        # 设置内容
        self.description_content.setText(html)
        
        if was_expanded:
            # 如果已经展开，直接平滑过渡到新高度（不收回再展开）
            # 如果比例发生了变化，强制播放动画
            self._description_animator.update_size(force_animation=ratio_changed)
        else:
            # 如果之前是隐藏的，正常展开
            # 如果没有选项（100%），强制从零开始动画，防止瞬间占满
            self._description_animator.expand(force_from_zero=not has_options)

    def _process_remote_images(self, html: str) -> str:
        """处理公告中的图片（网络图片下载到本地缓存，本地图片转换为URI格式）。"""
        if not html:
            return html
        
        # 处理网络图片（http/https）
        urls = set(re.findall(r"https?://[^\s\"'>]+", html))
        for url in urls:
            local_path = self._cache_remote_image(url)
            if not local_path:
                continue
            local_uri = Path(local_path).as_uri()
            html = html.replace(url, local_uri)
        
        # 处理本地图片路径（转换为 file:// URI 格式）
        # 匹配 <img> 标签中的 src 属性，支持相对路径和绝对路径
        img_pattern = re.compile(
            r'(<img[^>]*src=["\'])([^"\']+)(["\'][^>]*>)',
            re.IGNORECASE
        )
        
        def replace_local_path(match):
            prefix = match.group(1)
            src = match.group(2)
            suffix = match.group(3)
            
            # 如果已经是 file:// URI 或 http/https，跳过
            if src.startswith(('file://', 'http://', 'https://')):
                return match.group(0)
            
            # 如果是相对路径或绝对路径，转换为 file:// URI
            try:
                # 尝试解析为路径
                img_path = Path(src)
                # 如果是相对路径，尝试相对于当前工作目录
                if not img_path.is_absolute():
                    # 相对路径保持原样，让 Qt 自己处理
                    # 或者可以尝试相对于 bundle 路径
                    pass
                
                # 如果文件存在，转换为绝对路径的 URI
                if img_path.exists():
                    absolute_path = img_path.resolve()
                    uri = absolute_path.as_uri()
                    return f'{prefix}{uri}{suffix}'
                else:
                    # 文件不存在，保持原样（可能是相对路径，运行时才能确定）
                    return match.group(0)
            except Exception:
                # 解析失败，保持原样
                return match.group(0)
        
        html = img_pattern.sub(replace_local_path, html)
        
        return html

    def _cache_remote_image(self, url: str) -> str | None:
        """缓存网络图片到临时目录，返回本地路径。失败则返回None。"""
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return None
            
            cache_dir = Path(tempfile.gettempdir()) / "mfw_remote_images"
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            ext = Path(parsed.path).suffix
            # 简单兜底，避免过长或缺少后缀
            if not ext or len(ext) > 5:
                ext = ".img"
            
            filename = hashlib.sha1(url.encode("utf-8")).hexdigest() + ext
            file_path = cache_dir / filename
            if file_path.exists():
                return str(file_path)
            
            req = urllib.request.Request(url, headers={"User-Agent": "MFW-PyQt6"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            file_path.write_bytes(data)
            return str(file_path)
        except Exception as e:
            logger.warning(f"下载网络图片失败: {url}, {e}")
            return None

    def _toggle_description(self, visible: bool | None = None, animate: bool = True) -> None:
        """切换描述区域的显示/隐藏
        visible: True显示，False隐藏，None切换当前状态
        """
        if visible is None:
            # 切换当前状态
            visible = not self._description_animator.is_expanded()

        if visible:
            if animate:
                self._description_animator.expand()
            else:
                self._description_animator.set_visible_immediate(True)
        else:
            if animate:
                self._description_animator.collapse()
            else:
                self._description_animator.set_visible_immediate(False)
    
    def _on_link_activated(self, link: str):
        """处理链接点击事件"""
        if link.startswith("image:"):
            # 提取图片路径
            image_path = link[6:]  # 移除 "image:" 前缀
            # 打开图片预览对话框
            dialog = ImagePreviewDialog(image_path, self)
            dialog.exec()

    # ==================== 公共方法 ====================

    def reset(self):
        """重置选项区域和描述区域"""
        self._clear_options()
        self.description_content.setText("")
        self._toggle_description(visible=False)
        self.segmented_switcher.setCurrentItem("options")
        self.segmented_switcher.setVisible(False)
        self.option_stack.setCurrentIndex(0)
        self.speedrun_widget.set_config(None, emit=False)
        # 显示选项区域，因为已经专门的方法清除选项
        self.option_splitter_widget.show()

        self.current_task = None
        self.current_config = {}

    def _on_config_changed(self, config_id: str):
        """配置切换时清除选项面板"""
        # 清除选项和当前选中的任务选项
        self.reset()

    def set_title(self, title: str):
        """设置标题"""
        self.title_widget.setText(title)

    def _clear_options(self):
        """清空选项区域"""
        # 清空设置组件的字典
        if hasattr(self, "controller_setting_widget"):
            self.controller_setting_widget.resource_setting_widgets.clear()
        if hasattr(self, "resource_setting_widgets"):
            self.resource_setting_widgets.clear()
        if hasattr(self, "post_action_widgets"):
            self.post_action_widgets.clear()
        
        # 清除资源选项（如果存在）
        if hasattr(self, "_clear_resource_options"):
            self._clear_resource_options()
        # 清除全局选项（如果存在）
        if hasattr(self, "_clear_global_options"):
            self._clear_global_options()

        # 使用新框架清空选项
        self.option_form_widget._clear_options()

        # 移除选项页中除 option_form_widget 之外的控件/布局
        items_to_remove = []
        for i in reversed(range(self.option_page_layout.count())):
            item = self.option_page_layout.itemAt(i)
            if not item:
                continue
            if item.widget() == self.option_form_widget:
                continue
            items_to_remove.append(i)

        for i in items_to_remove:
            item = self.option_page_layout.takeAt(i)
            if not item:
                continue
            if item.widget():
                widget = item.widget()
                if widget:
                    widget.hide()
                    widget.setParent(None)
                    widget.deleteLater()
            elif item.layout():
                layout = item.layout()
                while layout and layout.count() > 0:
                    child_item = layout.takeAt(0)
                    if child_item and child_item.widget():
                        child_widget = child_item.widget()
                        if child_widget:
                            child_widget.hide()
                            child_widget.setParent(None)
                            child_widget.deleteLater()
                if layout:
                    layout.deleteLater()

        # 强制更新布局和几何结构
        self.option_page_layout.update()
        self.updateGeometry()

    def update_form_from_structure(self, form_structure, config=None):
        """
        直接从表单结构更新表单
        :param form_structure: 表单结构定义
        :param config: 可选的配置对象，用于设置表单的当前选择
        """
        # 构建表单（排除 description 字段）
        form_structure_copy = {
            k: v for k, v in form_structure.items() if k != "description"
        }

        # 检查是否有有效的选项项（有 type 字段的字典项）
        has_valid_options = False
        for key, item_config in form_structure_copy.items():
            if isinstance(item_config, dict) and "type" in item_config:
                has_valid_options = True
                break

        # 处理表单结构中的描述字段（传入是否有选项的信息）
        description = None
        if isinstance(form_structure, dict) and "description" in form_structure:
            description = form_structure["description"]

        # 如果有有效选项，构建表单；否则清空
        if has_valid_options:
            self.option_area_card.show()
            self._option_animator.play(
                lambda: self._apply_form_structure_with_animation(
                    form_structure_copy, config
                )
            )
        else:
            # 没有有效选项，使用动画清空选项区域
            self._option_animator.play(lambda: self._clear_options())
            # 保持选项区域可见
            self.option_area_card.show()

        # 设置描述内容（set_description会处理显示/隐藏和动画过渡）
        # 放在最后确保选项区域已经正确设置可见性
        self.set_description(description or "", has_options=has_valid_options)

    def _apply_form_structure_with_animation(self, form_structure, config):
        """异步更新选项表单并连接信号（用于动画回调）。"""
        # 先清空旧选项
        self._clear_options()
        # 构建新选项
        self.option_form_widget.build_from_structure(form_structure, config)
        self._connect_option_signals()
        # 更新选项的启用/禁用状态
        self._update_options_enabled()

    def get_current_form_config(self):
        """
        获取当前表单的配置
        :return: 当前选择的配置字典
        """
        # 使用新的OptionFormWidget框架获取配置
        return self.option_form_widget.get_options()

    def _connect_option_signals(self):
        """
        连接选项变化信号以实现自动保存
        """
        # 遍历所有选项项，连接它们的信号
        for option_item in self.option_form_widget.option_items.values():
            # 连接选项变化信号
            option_item.option_changed.connect(self._on_option_changed)

            # 递归连接子选项的信号
            self._connect_child_option_signals(option_item)

    def _connect_child_option_signals(self, option_item):
        """
        递归连接子选项的信号

        :param option_item: 选项项组件
        """
        for child_widget in option_item.child_options.values():
            child_widget.option_changed.connect(self._on_option_changed)
            # 递归连接子选项的子选项
            self._connect_child_option_signals(child_widget)

    def _on_option_changed(self, key: str, value: Any):
        """
        选项变化时的回调函数，用于自动保存

        :param key: 选项键名
        :param value: 选项值
        """
        try:
            # 获取当前所有配置
            all_config = self.get_current_form_config()
            # 调用OptionService的update_options方法保存选项
            self.service_coordinator.option_service.update_options(all_config)
        except Exception as e:
            # 如果保存失败，记录错误但不影响用户操作
            logger.error(f"自动保存选项失败: {e}")

    def _on_options_loaded(self):
        """
        当选项被加载时触发
        """
        # 先从OptionService获取form_structure
        form_structure = self.service_coordinator.option.get_form_structure()

        # 只使用form_structure更新表单
        if form_structure:
            # 获取最新配置并更新到共享字典中（保持字典引用不变）
            new_config = self.service_coordinator.option.get_options()
            self.current_config.clear()
            self.current_config.update(new_config)
            # 同步更新控制器组件的 current_config（Resource 和 PostAction 现在直接使用 self.current_config）
            self.controller_setting_widget.current_config = self.current_config

            # 判断任务类型
            form_type = form_structure.get("type")
            is_resource = form_type == "resource"
            is_controller = form_type == "controller"
            is_post_action = form_type == "post_action"

            if is_resource:
                # 资源任务 - 只显示资源下拉框
                self._option_animator.play(
                    lambda: self._apply_resource_settings_with_animation()
                )
                # 资源类型的描述会在 create_settings 中设置

            elif is_controller:
                # 控制器任务 - 显示控制器设置
                self._option_animator.play(
                    lambda: self._apply_controller_settings_with_animation()
                )
                # 控制器类型的描述会在 create_settings 中设置

            elif is_post_action:
                # 完成后操作 - 使用动画过渡
                self._option_animator.play(
                    lambda: self._apply_post_action_settings_with_animation()
                )
                # 从 form_structure 中获取描述（如果有的话）
                description = form_structure.get("description", "")
                self.set_description(description, has_options=True)

            else:
                # 正常的表单更新逻辑（update_form_from_structure会处理公告）
                self.update_form_from_structure(form_structure, self.current_config)
        else:
            # 没有表单时清除界面
            self.reset()
            logger.info("没有提供form_structure，已清除界面")

        # 同步速通配置到堆叠页（资源/完成后/特殊任务隐藏速通页）
        option_service = self.service_coordinator.option
        task_service = self.service_coordinator.task
        task_id = getattr(option_service, "current_task_id", None)
        task = task_service.get_task(task_id) if task_id else None
        is_special_task = getattr(task, "is_special", False) if task else False

        speedrun_visible = (
            not (
                form_structure
                and form_structure.get("type")
                in ["resource", "controller", "post_action"]
            )
            and not is_special_task
        )

        if not speedrun_visible:
            # 资源/完成后/特殊任务：隐藏分段与速通
            self._set_speedrun_visible(False)
            self.segmented_switcher.setVisible(False)
            self.option_stack.setCurrentIndex(0)
            self.segmented_switcher.setCurrentItem("options")
            return

        # 普通任务：根据是否有选项决定是否显示分段控件
        has_valid_options = False
        if isinstance(form_structure, dict):
            for k, v in form_structure.items():
                if k == "description":
                    continue
                if isinstance(v, dict) and ("type" in v):
                    has_valid_options = True
                    break

        if has_valid_options:
            # 有选项：显示分段，默认落在选项页
            self.segmented_switcher.setVisible(True)
            self._set_speedrun_visible(True)
            self._apply_speedrun_config()
            self.option_stack.setCurrentIndex(0)
            self.segmented_switcher.setCurrentItem("options")
        else:
            # 无选项：隐藏分段，直接展示速通配置
            self.segmented_switcher.setVisible(False)
            self.option_stack.setCurrentIndex(1)
            self._apply_speedrun_config()
            self.segmented_switcher.setCurrentItem("speedrun")
        
        # 更新选项的启用/禁用状态（在所有选项加载完成后）
        self._update_options_enabled()

    def _apply_resource_settings_with_animation(self):
        """在动画回调中应用资源设置（只显示资源下拉框）"""
        self._clear_options()
        # 确保选项区域可见
        self.option_area_card.show()
        # 资源任务只显示资源下拉框，不显示控制器设置
        # 但资源下拉框需要控制器信息，所以先初始化控制器数据
        self.controller_setting_widget._rebuild_interface_data()

        # 同步控制器相关属性到资源 Mixin
        self.controller_type_mapping = (
            self.controller_setting_widget.controller_type_mapping
        )

        # 从Controller任务的配置中获取当前控制器类型（资源任务应该使用Controller任务的配置）
        from app.common.constants import _CONTROLLER_

        controller_task = self.service_coordinator.task_service.get_task(_CONTROLLER_)
        controller_name = ""
        if controller_task and isinstance(controller_task.task_option, dict):
            controller_name = controller_task.task_option.get("controller_type", "")

        # 如果Controller任务中没有配置，尝试从当前配置中获取（作为fallback）
        if not controller_name:
            controller_name = self.current_config.get("controller_type", "")

        if controller_name:
            # 查找对应的控制器标签
            for (
                label,
                ctrl_info,
            ) in self.controller_setting_widget.controller_type_mapping.items():
                if ctrl_info["name"] == controller_name:
                    self.current_controller_label = label
                    break
            else:
                # 如果找不到，使用第一个控制器
                if self.controller_setting_widget.controller_type_mapping:
                    first_label = list(
                        self.controller_setting_widget.controller_type_mapping.keys()
                    )[0]
                    self.current_controller_label = first_label
        else:
            # 如果没有配置，使用第一个控制器
            if self.controller_setting_widget.controller_type_mapping:
                first_label = list(
                    self.controller_setting_widget.controller_type_mapping.keys()
                )[0]
                self.current_controller_label = first_label

        # 从Resource任务的配置中获取当前资源值，并设置到current_config中
        # 注意：必须在 create_resource_settings 之前设置，因为 create_resource_settings 会调用 _fill_resource_option
        from app.common.constants import _RESOURCE_

        resource_task = self.service_coordinator.task_service.get_task(_RESOURCE_)
        if resource_task and isinstance(resource_task.task_option, dict):
            resource_name = resource_task.task_option.get("resource", "")
            if resource_name:
                # 确保 current_config 中有 resource 字段
                self.current_config["resource"] = resource_name

        # 创建资源下拉框（此时 current_config 中已经有 resource 值了）
        self.create_resource_settings()
        # 更新选项的启用/禁用状态
        self._update_options_enabled()

    def _apply_controller_settings_with_animation(self):
        """在动画回调中应用控制器设置"""
        self._clear_options()
        # 确保选项区域可见
        self.option_area_card.show()
        # 确保 _toggle_description 方法已设置（防止在某些情况下丢失）
        if (
            not hasattr(self.controller_setting_widget, "_toggle_description")
            or self.controller_setting_widget._toggle_description is None
        ):
            self.controller_setting_widget._toggle_description = (
                self._toggle_description
            )
        # 控制器任务只显示控制器设置，不显示资源下拉框
        self.controller_setting_widget.create_settings()
        # 更新选项的启用/禁用状态
        self._update_options_enabled()

        logger.info("控制器设置表单已创建")

    def _apply_post_action_settings_with_animation(self):
        """在动画回调中应用完成后操作设置"""
        self._clear_options()
        # 确保选项区域可见
        self.option_area_card.show()

        # 从 POST_ACTION 任务的配置中获取完成后操作配置，并设置到 current_config 中
        from app.common.constants import POST_ACTION

        post_action_task = self.service_coordinator.task_service.get_task(POST_ACTION)
        if post_action_task and isinstance(post_action_task.task_option, dict):
            post_action_config = post_action_task.task_option.get("post_action", {})
            if post_action_config:
                # 确保 current_config 中有 post_action 字段
                self.current_config["post_action"] = post_action_config

        # 创建完成后操作设置（此时 current_config 中已经有 post_action 值了）
        self.create_post_action_settings()
        # 更新选项的启用/禁用状态
        self._update_options_enabled()
        
        logger.info("完成后操作设置表单已创建")

    def _set_layout_visibility(self, layout, visible):
        """
        递归设置布局中所有控件的可见性
        :param layout: 要设置的布局
        :param visible: 是否可见
        """
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget():
                item.widget().setVisible(visible)
            elif item.layout():
                self._set_layout_visibility(item.layout(), visible)

    def _on_task_status_changed(self, task_id: str, status: str):
        """监听任务状态变化，更新选项的启用/禁用状态"""
        self._update_options_enabled()

    def _update_options_enabled(self):
        """根据运行状态更新所有选项的启用/禁用状态"""
        # 检查是否有任务正在运行
        is_running = self.service_coordinator.run_manager.is_running
        self._set_options_enabled(not is_running)

    def _set_options_enabled(self, enabled: bool):
        """设置所有选项控件的启用/禁用状态
        
        :param enabled: True 启用，False 禁用
        """
        # 禁用/启用控制器设置组件中的控件
        if hasattr(self, 'controller_setting_widget'):
            # 先设置整个组件的启用状态（这会递归处理布局中的所有子控件）
            self._set_widget_enabled(self.controller_setting_widget, enabled)
            # 同时确保 resource_setting_widgets 字典中的所有控件都被处理
            # （有些控件可能不在布局中，需要单独处理）
            if hasattr(self.controller_setting_widget, 'resource_setting_widgets'):
                for widget in self.controller_setting_widget.resource_setting_widgets.values():
                    if widget and hasattr(widget, 'setEnabled'):
                        widget.setEnabled(enabled)
        
        # 禁用/启用资源设置组件中的控件
        if hasattr(self, 'resource_setting_widgets'):
            for widget in self.resource_setting_widgets.values():
                if widget:
                    self._set_widget_enabled(widget, enabled)
        
        # 禁用/启用完成后操作设置组件中的控件
        if hasattr(self, 'post_action_widgets'):
            for widget in self.post_action_widgets.values():
                if widget:
                    self._set_widget_enabled(widget, enabled)
        
        # 禁用/启用普通任务的选项表单控件
        if hasattr(self, 'option_form_widget'):
            self._set_widget_enabled(self.option_form_widget, enabled)
        
        # 禁用/启用速通配置控件
        if hasattr(self, 'speedrun_widget'):
            self._set_widget_enabled(self.speedrun_widget, enabled)

    def _set_widget_enabled(self, widget, enabled: bool):
        """递归设置控件及其子控件的启用/禁用状态
        
        :param widget: 要设置的控件
        :param enabled: True 启用，False 禁用
        """
        if widget is None:
            return
        
        # 设置控件本身的启用状态
        if hasattr(widget, 'setEnabled'):
            widget.setEnabled(enabled)
        
        # 如果控件有布局，处理布局中的控件（这会递归处理所有子控件）
        if hasattr(widget, 'layout') and widget.layout():
            self._set_layout_enabled(widget.layout(), enabled)
        # 如果没有布局，直接处理子控件
        elif hasattr(widget, 'children'):
            for child in widget.children():
                self._set_widget_enabled(child, enabled)

    def _set_layout_enabled(self, layout, enabled: bool):
        """递归设置布局中所有控件的启用/禁用状态
        
        :param layout: 要设置的布局
        :param enabled: True 启用，False 禁用
        """
        if layout is None:
            return
        
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            
            if item.widget():
                widget = item.widget()
                if widget and hasattr(widget, 'setEnabled'):
                    widget.setEnabled(enabled)
                # 递归处理子控件
                self._set_widget_enabled(widget, enabled)
            elif item.layout():
                # 递归处理子布局
                self._set_layout_enabled(item.layout(), enabled)
