"""
MFW-ChainFlow Assistant
Bundle 管理界面
作者:overflow65537
"""

import jsonc
import time
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

from PySide6.QtCore import Qt, Signal, QMetaObject, QCoreApplication, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidgetItem,
    QLabel,
    QSizePolicy,
    QFileDialog,
)

from qfluentwidgets import (
    ScrollArea,
    ListWidget,
    BodyLabel,
    CardWidget,
    TitleLabel,
    SimpleCardWidget,
    ToolButton,
    ToolTipFilter,
    ToolTipPosition,
    FluentIcon as FIF,
    TogglePushButton,
    MessageBoxBase,
    TransparentPushButton,
    LineEdit,
    SubtitleLabel,
)

from app.core.core import ServiceCoordinator
from app.core.service.interface_manager import get_interface_manager
from app.utils.logger import logger
from app.utils.update import Update, MultiResourceUpdate
from app.common.signal_bus import signalBus
from app.utils.markdown_helper import render_markdown
from app.widget.notice_message import NoticeMessageBox
import os


class BundleListItem(QWidget):
    """Bundle 列表项组件"""

    def __init__(
        self,
        bundle_name: str,
        bundle_version: str,
        icon_path: Optional[str],
        parent=None,
    ):
        super().__init__(parent)
        self.bundle_name = bundle_name
        self.bundle_version = bundle_version
        self.latest_version: Optional[str] = None

        layout = QHBoxLayout(self)
        # 调整边距，确保总高度不超过 item 边界
        # 设置固定高度 64px，确保不超过 item 边界
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        self.setFixedHeight(64)

        # 图标
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setScaledContents(True)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if icon_path:
            icon_file = Path(icon_path)
            if icon_file.exists():
                pixmap = QPixmap(str(icon_file))
                if not pixmap.isNull():
                    self.icon_label.setPixmap(
                        pixmap.scaled(
                            32,
                            32,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )

        # 名称和版本
        text_layout = QVBoxLayout()
        # 调整间距，确保总高度不超过 64px
        # 64px = 上下边距(8+8) + 图标(32) + 文本区域(16) = 64px
        text_layout.setSpacing(2)  # 从 4 调整为 2
        text_layout.setContentsMargins(0, 0, 0, 0)

        self.name_label = QLabel(bundle_name, self)
        self.name_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        # 设置固定高度，确保不超过边界
        self.name_label.setFixedHeight(18)  # 14px 字体 + 4px 行高

        # 当前版本
        self.version_label = QLabel(bundle_version, self)
        self.version_label.setStyleSheet("font-size: 12px; color: gray;")
        self.version_label.setWordWrap(True)
        # 设置最大高度，防止超出边界
        self.version_label.setMaximumHeight(16)  # 12px 字体 + 4px 行高

        # 最新版本
        self.latest_version_label = QLabel("", self)
        self.latest_version_label.setStyleSheet("font-size: 12px; color: #0078d4;")
        self.latest_version_label.setWordWrap(True)
        self.latest_version_label.setMaximumHeight(16)  # 12px 字体 + 4px 行高
        self.latest_version_label.hide()  # 初始状态隐藏

        text_layout.addWidget(self.name_label)
        text_layout.addWidget(self.version_label)
        text_layout.addWidget(self.latest_version_label)

        layout.addWidget(self.icon_label)
        layout.addLayout(text_layout)
        layout.addStretch()

        # 查看更新日志按钮
        self.update_log_button = ToolButton(FIF.QUICK_NOTE, self)
        self.update_log_button.setFixedSize(32, 32)
        self.update_log_button.installEventFilter(
            ToolTipFilter(self.update_log_button, 0, ToolTipPosition.TOP)
        )
        self.update_log_button.setToolTip(
            QCoreApplication.translate("BundleInterface", "Open update log")
        )
        layout.addWidget(self.update_log_button)

        # 删除按钮
        self.delete_button = ToolButton(FIF.DELETE, self)
        self.delete_button.setFixedSize(32, 32)
        self.delete_button.installEventFilter(
            ToolTipFilter(self.delete_button, 0, ToolTipPosition.TOP)
        )
        self.delete_button.setToolTip(
            QCoreApplication.translate("BundleInterface", "Delete bundle")
        )
        layout.addWidget(self.delete_button)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def update_latest_version(self, latest_version: Optional[str]):
        """更新最新版本信息"""
        self.latest_version = latest_version
        if latest_version and latest_version != self.bundle_version:
            # 显示最新版本（蓝色表示有更新）
            self.latest_version_label.setText(
                self.tr("Latest version: {}").format(latest_version)
            )
            self.latest_version_label.setStyleSheet("font-size: 12px; color: #0078d4;")
            self.latest_version_label.show()
        else:
            # 隐藏最新版本标签（已是最新或未检查到）
            self.latest_version_label.setText("")
            self.latest_version_label.hide()


class BundleDetailWidget(QWidget):
    """Bundle 详情显示组件（右侧滚动区域内容）"""

    def __init__(
        self,
        bundle_name: str,
        bundle_data: Dict[str, Any],
        interface_data: Dict[str, Any],
        parent=None,
    ):
        super().__init__(parent)
        self.bundle_name = bundle_name
        self.bundle_data = bundle_data
        self.interface_data = interface_data

        # 设置透明背景和边框
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # 描述信息（来自 interface 的 description 字段）
        self._add_description_section(layout)
        # 联系方式（welcome 已在底部按钮中单独弹出）
        self._add_contact_section(layout)

        layout.addStretch()

    def _add_description_section(self, parent_layout: QVBoxLayout):
        """添加描述区域"""
        section_layout = QVBoxLayout()
        section_layout.setSpacing(8)
        section_layout.setContentsMargins(0, 0, 0, 0)

        title = TitleLabel(self.tr("Description"), self)
        section_layout.addWidget(title)

        description_text = (
            self.interface_data.get("description", "")
            or self.interface_data.get("welcome", "")
            or self.tr("No description available")
        )
        description_label = BodyLabel(self)
        description_label.setWordWrap(True)
        # 支持 Markdown 格式
        description_label.setTextFormat(Qt.TextFormat.RichText)
        description_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        description_label.setOpenExternalLinks(True)
        html_content = render_markdown(description_text)
        description_label.setText(html_content)
        section_layout.addWidget(description_label)

        parent_layout.addLayout(section_layout)

    def _add_contact_section(self, parent_layout: QVBoxLayout):
        """添加联系方式区域"""
        section_layout = QVBoxLayout()
        section_layout.setSpacing(8)
        section_layout.setContentsMargins(0, 0, 0, 0)

        title = TitleLabel(self.tr("Contact"), self)
        section_layout.addWidget(title)

        contact_text = self.interface_data.get("contact", "") or self.tr(
            "No contact information available"
        )
        contact_label = BodyLabel(self)
        contact_label.setWordWrap(True)
        # 支持 Markdown 格式
        contact_label.setTextFormat(Qt.TextFormat.RichText)
        contact_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        contact_label.setOpenExternalLinks(True)
        html_content = render_markdown(contact_text)
        contact_label.setText(html_content)
        section_layout.addWidget(contact_label)

        parent_layout.addLayout(section_layout)


class UI_BundleInterface(object):
    """Bundle 管理界面 UI 类"""

    def __init__(self, service_coordinator: ServiceCoordinator, parent=None):
        self.service_coordinator = service_coordinator
        self.parent = parent

    def setupUi(self, BundleInterface):
        BundleInterface.setObjectName("BundleInterface")

        # 主布局（水平布局）
        main_layout = QHBoxLayout()

        # 左侧列表区域（30%）
        self._init_list_panel(BundleInterface)
        main_layout.addWidget(self.list_panel, 3)  # stretch=3 对应 30%

        # 右侧详情区域（70%）
        self._init_detail_panel(BundleInterface)
        main_layout.addWidget(self.detail_panel, 7)  # stretch=7 对应 70%

        # 将水平布局设置为 QWidget 的主布局
        BundleInterface.setLayout(main_layout)

        QMetaObject.connectSlotsByName(BundleInterface)

    def _init_list_panel(self, parent):
        """初始化左侧列表面板（带标题和卡片）"""
        _translate = QCoreApplication.translate

        # 列表面板容器
        self.list_panel = QWidget(parent)
        list_panel_layout = QVBoxLayout(self.list_panel)

        # 标题布局
        self.list_title_layout = QHBoxLayout()
        self.list_title_layout.setContentsMargins(0, 0, 2, 0)

        # 标题
        self.list_title = BodyLabel()
        self.list_title.setStyleSheet("font-size: 20px;")
        self.list_title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.list_title_layout.addWidget(self.list_title)

        # 自动更新开关按钮
        self.auto_update_switch = TogglePushButton(parent)
        self.auto_update_switch.setIcon(FIF.UPDATE)
        self.auto_update_switch.setText(_translate("BundleInterface", "Auto Update"))
        self.auto_update_switch.installEventFilter(
            ToolTipFilter(self.auto_update_switch, 0, ToolTipPosition.TOP)
        )
        self.auto_update_switch.setToolTip(_translate("BundleInterface", "Auto Update"))
        # 从配置读取自动更新状态
        from app.common.config import cfg

        auto_update_enabled = cfg.get(cfg.bundle_auto_update)  # type: ignore
        self.auto_update_switch.setChecked(auto_update_enabled)
        self.list_title_layout.addWidget(self.auto_update_switch)

        # 添加bundle按钮
        self.add_bundle_button = ToolButton(FIF.FOLDER_ADD, parent)
        self.add_bundle_button.installEventFilter(
            ToolTipFilter(self.add_bundle_button, 0, ToolTipPosition.TOP)
        )
        self.add_bundle_button.setToolTip(_translate("BundleInterface", "Add Bundle"))
        self.list_title_layout.addWidget(self.add_bundle_button)

        # 更新所有bundle按钮
        self.update_all_button = ToolButton(FIF.SYNC, parent)
        self.update_all_button.installEventFilter(
            ToolTipFilter(self.update_all_button, 0, ToolTipPosition.TOP)
        )
        self.update_all_button.setToolTip(
            _translate("BundleInterface", "Update All Bundles")
        )
        self.list_title_layout.addWidget(self.update_all_button)

        list_panel_layout.addLayout(self.list_title_layout)

        # 列表卡片
        self.list_card = SimpleCardWidget()
        self.list_card.setClickEnabled(False)
        self.list_card.setBorderRadius(8)
        list_card_layout = QVBoxLayout(self.list_card)

        # 列表组件
        self.list_widget = ListWidget(self.list_card)
        list_card_layout.addWidget(self.list_widget)

        list_panel_layout.addWidget(self.list_card)

    def _init_detail_panel(self, parent):
        """初始化右侧详情面板（带标题和卡片）"""
        _translate = QCoreApplication.translate

        # 详情面板容器
        self.detail_panel = QWidget(parent)
        detail_panel_layout = QVBoxLayout(self.detail_panel)

        # 标题布局
        self.detail_title_layout = QHBoxLayout()

        # 标题
        self.detail_title = BodyLabel()
        self.detail_title.setStyleSheet("font-size: 20px;")
        self.detail_title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.detail_title_layout.addWidget(self.detail_title)

        detail_panel_layout.addLayout(self.detail_title_layout)

        # 详情卡片
        self.detail_card = SimpleCardWidget()
        self.detail_card.setClickEnabled(False)
        self.detail_card.setBorderRadius(8)
        detail_card_layout = QVBoxLayout(self.detail_card)

        # 滚动区域
        self.scroll_area = ScrollArea(self.detail_card)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # 设置滚动区域透明背景和边框
        self.scroll_area.setStyleSheet("background: transparent; border: none;")

        self.detail_widget = QWidget()
        # 设置透明背景和边框
        self.detail_widget.setStyleSheet("background: transparent; border: none;")
        self.detail_layout = QVBoxLayout(self.detail_widget)

        # 默认提示
        self.default_label = BodyLabel(
            _translate("BundleInterface", "Please select a Bundle from the left"),
            self.detail_widget,
        )
        self.default_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.default_label.setStyleSheet("font-size: 16px; color: gray; padding: 40px;")
        self.detail_layout.addWidget(self.default_label)
        self.detail_layout.addStretch()

        self.scroll_area.setWidget(self.detail_widget)
        detail_card_layout.addWidget(self.scroll_area)

        # 许可证 / 欢迎信息按钮（位于滚动区域下方，靠右）
        self.license_button = TransparentPushButton(
            _translate("BundleInterface", "License"), self.detail_card, FIF.CERTIFICATE
        )
        self.license_button.setFixedHeight(36)

        self.welcome_button = TransparentPushButton(
            _translate("BundleInterface", "Welcome"), self.detail_card, FIF.INFO
        )
        self.welcome_button.setFixedHeight(36)

        bottom_button_layout = QHBoxLayout()
        bottom_button_layout.addStretch()
        bottom_button_layout.addWidget(self.welcome_button)
        bottom_button_layout.addWidget(self.license_button)
        detail_card_layout.addLayout(bottom_button_layout)

        detail_panel_layout.addWidget(self.detail_card)


class BundleInterface(UI_BundleInterface, QWidget):
    """Bundle 管理界面"""

    bundle_selected = Signal(str)  # 发送选中的 bundle 名称

    def __init__(self, service_coordinator: ServiceCoordinator, parent=None):
        QWidget.__init__(self, parent=parent)
        UI_BundleInterface.__init__(
            self,
            service_coordinator=service_coordinator,
            parent=parent,
        )
        self.setupUi(self)
        self.service_coordinator = service_coordinator
        self._bundle_data: Dict[str, Dict[str, Any]] = {}
        self._latest_versions: Dict[str, Optional[str]] = (
            {}
        )  # bundle_name -> latest_version
        self._update_checkers: Dict[str, Update] = {}  # bundle_name -> Update checker
        self._current_updater: Optional[Update] = None  # 当前正在运行的更新器
        self._current_bundle_name: Optional[str] = None  # 当前正在更新的bundle名称
        self._update_queue: list[str] = []  # 更新队列
        self._is_updating_all = False
        self._selected_bundle_name: Optional[str] = None  # 当前选中的 bundle 名称

        # 设置标题
        self.list_title.setText(self.tr("Bundle List"))
        self.detail_title.setText(self.tr("Bundle Details"))

        self.auto_update_switch.setText(self.tr("Auto Update"))
        self.auto_update_switch.setToolTip(self.tr("Auto Update"))

        # 连接信号
        self.list_widget.currentItemChanged.connect(self._on_bundle_selected)
        self.add_bundle_button.clicked.connect(self._on_add_bundle_clicked)
        self.update_all_button.clicked.connect(self._on_update_all_bundles)
        self.auto_update_switch.toggled.connect(self._on_auto_update_changed)
        self.license_button.clicked.connect(self._open_license_dialog)
        self.welcome_button.clicked.connect(self._open_welcome_dialog)

        # 监听更新停止信号（Update 类会自动发送，用于通知主界面）
        signalBus.update_stopped.connect(self._on_update_stopped)

        # 监听多资源适配启用信号，刷新 bundle 列表
        signalBus.multi_resource_adaptation_enabled.connect(
            self._on_multi_resource_enabled
        )

        # 加载 bundle 列表
        self._load_bundles()

        # 启动时自动检查所有资源的更新（仅在多资源适配开启时）
        if self._is_multi_resource_enabled():
            self._check_all_updates()

    def _load_bundles(self, force_refresh: bool = False):
        """从 service_coordinator 加载所有 bundle

        Args:
            force_refresh: 是否强制刷新（更新后使用，直接读取文件而不使用缓存）
        """
        self.list_widget.clear()
        self._bundle_data.clear()

        try:
            bundle_names = self.service_coordinator.config.list_bundles()
            if not bundle_names:
                logger.warning("未找到任何 bundle")
                return

            for bundle_name in bundle_names:
                try:
                    bundle_info = self.service_coordinator.config.get_bundle(
                        bundle_name
                    )
                    bundle_path_str = bundle_info.get("path", "")
                    bundle_display_name = bundle_info.get("name", bundle_name)

                    if not bundle_path_str:
                        logger.warning(f"Bundle '{bundle_name}' 没有路径信息")
                        continue

                    # 解析路径
                    bundle_path = Path(bundle_path_str)
                    if not bundle_path.is_absolute():
                        bundle_path = Path.cwd() / bundle_path

                    # 读取 interface.json 或 interface.jsonc
                    interface_path = bundle_path / "interface.jsonc"
                    if not interface_path.exists():
                        interface_path = bundle_path / "interface.json"

                    icon_path = None
                    interface_data = {}
                    if interface_path.exists():
                        try:
                            if force_refresh:
                                # 强制刷新模式：直接读取文件，不使用缓存
                                # 这样可以确保读取到更新后的最新内容
                                logger.debug(
                                    f"强制刷新模式：直接读取 interface 文件: {interface_path}"
                                )
                                # 多次尝试读取，确保文件系统同步
                                max_retries = 3
                                for retry in range(max_retries):
                                    try:
                                        with open(
                                            interface_path, "r", encoding="utf-8"
                                        ) as f:
                                            interface_data = jsonc.load(f)
                                        if interface_data:
                                            break
                                    except (IOError, jsonc.JSONDecodeError) as e:
                                        if retry < max_retries - 1:
                                            logger.debug(
                                                f"读取 interface 文件失败，重试 {retry + 1}/{max_retries}: {e}"
                                            )
                                            time.sleep(0.1)  # 等待文件系统同步
                                        else:
                                            logger.error(
                                                f"读取 interface 文件失败: {e}"
                                            )
                                            raise

                                # 如果需要翻译，再使用 InterfaceManager 进行翻译
                                if interface_data:
                                    interface_manager = get_interface_manager()
                                    current_language = interface_manager.get_language()
                                    # 使用 preview_interface 进行翻译（会重新读取文件，但我们已经确保文件是最新的）
                                    translated_data = (
                                        interface_manager.preview_interface(
                                            interface_path, language=current_language
                                        )
                                    )
                                    if translated_data:
                                        interface_data = translated_data
                                    else:
                                        logger.warning(
                                            f"preview_interface 返回空数据，使用直接读取的数据"
                                        )
                            else:
                                # 正常模式：使用 InterfaceManager 的 preview_interface 方法加载并翻译 interface 文件
                                # 这样可以支持 i18n 功能
                                interface_manager = get_interface_manager()
                                current_language = interface_manager.get_language()

                                # 预览并翻译该 bundle 的 interface 文件
                                interface_data = interface_manager.preview_interface(
                                    interface_path, language=current_language
                                )

                                # 如果预览失败（返回空字典），回退到直接读取
                                if not interface_data:
                                    logger.warning(
                                        f"使用 preview_interface 加载失败，回退到直接读取: {interface_path}"
                                    )
                                    with open(
                                        interface_path, "r", encoding="utf-8"
                                    ) as f:
                                        interface_data = jsonc.load(f)

                            icon_relative = interface_data.get("icon", "")
                            if icon_relative:
                                icon_path = bundle_path / icon_relative
                                if not icon_path.exists():
                                    icon_path = None
                        except Exception as e:
                            logger.warning(
                                f"读取 interface 文件失败 {interface_path}: {e}"
                            )
                            # 如果出错，尝试直接读取原始文件
                            try:
                                with open(interface_path, "r", encoding="utf-8") as f:
                                    interface_data = jsonc.load(f)
                            except Exception as e2:
                                logger.error(f"直接读取 interface 文件也失败: {e2}")

                    # 获取版本信息
                    bundle_version = interface_data.get("version", self.tr("Unknown version"))

                    # 保存数据
                    self._bundle_data[bundle_name] = {
                        "name": bundle_display_name,
                        "path": str(bundle_path),
                        "icon": str(icon_path) if icon_path else None,
                        "interface": interface_data,
                    }

                    # 创建列表项
                    item_widget = BundleListItem(
                        bundle_display_name,
                        bundle_version,
                        str(icon_path) if icon_path else None,
                    )

                    # 如果有已检查到的最新版本，立即显示
                    if bundle_name in self._latest_versions:
                        latest_version = self._latest_versions[bundle_name]
                        item_widget.update_latest_version(latest_version)

                    # 连接更新日志按钮的点击事件
                    item_widget.update_log_button.clicked.connect(
                        lambda checked=False, name=bundle_name: self._open_bundle_update_log( # type: ignore
                            name
                        )
                    )

                    # 连接删除按钮的点击事件
                    item_widget.delete_button.clicked.connect(
                        lambda checked=False, name=bundle_name: self._on_delete_bundle_clicked( # type: ignore
                            name
                        )
                    )

                    list_item = QListWidgetItem(self.list_widget)
                    # 设置固定的 item 高度，确保与 BundleListItem 的高度一致（64px）
                    from PySide6.QtCore import QSize
                    list_item.setSizeHint(QSize(0, 64))
                    list_item.setData(
                        Qt.ItemDataRole.UserRole, bundle_name
                    )  # 保存原始名称
                    self.list_widget.setItemWidget(list_item, item_widget)
                    self.list_widget.addItem(list_item)

                except Exception as e:
                    logger.error(f"加载 bundle '{bundle_name}' 失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"加载 bundle 列表失败: {e}")

    def _on_bundle_selected(self, current: QListWidgetItem, previous: QListWidgetItem):
        """处理 bundle 选择事件"""
        if not current:
            return

        bundle_name = current.data(Qt.ItemDataRole.UserRole)
        if not bundle_name:
            return

        # 记录当前选中的 bundle 名称，供许可证对话框使用
        self._selected_bundle_name = bundle_name

        # 发送信号
        self.bundle_selected.emit(bundle_name)

        # 更新右侧显示
        self._update_detail_view(bundle_name)

    def _update_detail_view(self, bundle_name: str):
        """更新右侧详情视图"""
        # 清除现有内容（但保留 default_label）
        items_to_remove = []
        for i in range(self.detail_layout.count()):
            item = self.detail_layout.itemAt(i)
            if item:
                widget = item.widget()
                if widget and widget != self.default_label:
                    items_to_remove.append(i)

        # 从后往前删除，避免索引变化
        for i in reversed(items_to_remove):
            item = self.detail_layout.takeAt(i)
            if item:
                widget = item.widget()
                if widget:
                    widget.setParent(None)  # 先移除父级关系
                    widget.deleteLater()

        # 处理 stretch 项
        # 查找并移除所有 stretch 项
        for i in range(self.detail_layout.count() - 1, -1, -1):
            item = self.detail_layout.itemAt(i)
            if item and not item.widget():  # stretch 项没有 widget
                self.detail_layout.takeAt(i)

        # 隐藏默认提示（如果仍然有效）
        try:
            if hasattr(self, "default_label") and self.default_label:
                # 检查对象是否仍然有效
                try:
                    if self.default_label.isVisible():
                        self.default_label.hide()
                except RuntimeError:
                    # 对象已被删除，忽略错误
                    pass
        except (AttributeError, RuntimeError):
            # default_label 不存在或已被删除，忽略错误
            pass

        # 获取 bundle 数据
        bundle_data = self._bundle_data.get(bundle_name, {})
        if not bundle_data:
            logger.warning(f"Bundle '{bundle_name}' 数据不存在")
            return

        interface_data = bundle_data.get("interface", {})
        if not interface_data:
            logger.warning(f"Bundle '{bundle_name}' interface 数据不存在")
            return

        # 创建详情组件
        detail = BundleDetailWidget(
            bundle_name, bundle_data, interface_data, self.detail_widget
        )
        self.detail_layout.addWidget(detail)
        self.detail_layout.addStretch()

    def _open_license_dialog(self):
        """显示当前选中 Bundle 的许可证信息"""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        if not self._selected_bundle_name:
            # 未选择 bundle 时不弹框，避免无意义操作
            return

        bundle_name = self._selected_bundle_name
        bundle_data = self._bundle_data.get(bundle_name, {})
        interface_data = bundle_data.get("interface", {}) or {}

        license_text = interface_data.get(
            "license", self.tr("No license information for this bundle")
        )
        bundle_display_name = bundle_data.get("name", bundle_name)

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("License") + f" - {bundle_display_name}")
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        content_label = BodyLabel(license_text, dialog)
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )

        scroll_area = ScrollArea(dialog)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.enableTransparentBackground()
        scroll_area.setWidget(content_label)
        scroll_area.setMinimumHeight(280)

        layout.addWidget(scroll_area)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close, parent=dialog
        )
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.resize(480, 280)
        dialog.exec()

    def _open_welcome_dialog(self):
        """显示当前选中 Bundle 的 welcome 信息"""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        if not self._selected_bundle_name:
            return

        bundle_name = self._selected_bundle_name
        bundle_data = self._bundle_data.get(bundle_name, {})
        interface_data = bundle_data.get("interface", {}) or {}

        welcome_text = interface_data.get(
            "welcome", self.tr("No welcome message for this bundle")
        )
        bundle_display_name = bundle_data.get("name", bundle_name)

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Welcome") + f" - {bundle_display_name}")
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        content_label = BodyLabel(dialog)
        content_label.setWordWrap(True)
        # 支持 Markdown
        content_label.setTextFormat(Qt.TextFormat.RichText)
        content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        content_label.setOpenExternalLinks(True)
        html_content = render_markdown(welcome_text)
        content_label.setText(html_content)

        scroll_area = ScrollArea(dialog)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.enableTransparentBackground()
        scroll_area.setWidget(content_label)
        scroll_area.setMinimumHeight(280)

        layout.addWidget(scroll_area)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close, parent=dialog
        )
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.resize(480, 280)
        dialog.exec()

    def _is_multi_resource_enabled(self) -> bool:
        """检查多资源适配是否已开启"""
        from app.common.config import cfg

        return bool(cfg.get(cfg.multi_resource_adaptation))

    def _check_all_updates(self):
        """检查所有bundle的更新"""
        # 检查多资源适配是否开启
        if not self._is_multi_resource_enabled():
            logger.debug("多资源适配未开启，跳过检查bundle更新")
            return

        try:
            bundle_names = self.service_coordinator.config.list_bundles()
            if not bundle_names:
                return

            for bundle_name in bundle_names:
                bundle_data = self._bundle_data.get(bundle_name)
                if not bundle_data:
                    continue

                interface_data = bundle_data.get("interface", {})
                if not interface_data:
                    continue

                # 创建更新检查器
                checker = Update(
                    service_coordinator=self.service_coordinator,
                    stop_signal=signalBus.update_stopped,
                    progress_signal=signalBus.update_progress,
                    info_bar_signal=signalBus.info_bar_requested,
                    interface=interface_data,
                    check_only=True,
                )

                # 连接检查结果信号
                checker.check_result_ready.connect(
                    lambda result, name=bundle_name: self._on_update_check_result(
                        name, result
                    )
                )
                checker.finished.connect(checker.deleteLater)

                self._update_checkers[bundle_name] = checker
                checker.start()
                logger.info(f"开始检查 bundle '{bundle_name}' 的更新")

        except Exception as e:
            logger.error(f"检查所有bundle更新失败: {e}", exc_info=True)

    def _check_single_bundle_update(self, bundle_name: str):
        """检查单个 bundle 的更新"""
        # 检查多资源适配是否开启
        if not self._is_multi_resource_enabled():
            return

        try:
            bundle_data = self._bundle_data.get(bundle_name)
            if not bundle_data:
                logger.warning(f"Bundle '{bundle_name}' 数据不存在，无法检查更新")
                return

            interface_data = bundle_data.get("interface", {})
            if not interface_data:
                logger.warning(
                    f"Bundle '{bundle_name}' interface 数据不存在，无法检查更新"
                )
                return

            # 创建更新检查器
            checker = Update(
                service_coordinator=self.service_coordinator,
                stop_signal=signalBus.update_stopped,
                progress_signal=signalBus.update_progress,
                info_bar_signal=signalBus.info_bar_requested,
                interface=interface_data,
                check_only=True,
            )

            # 连接检查结果信号
            checker.check_result_ready.connect(
                lambda result, name=bundle_name: self._on_update_check_result(
                    name, result
                )
            )
            checker.finished.connect(checker.deleteLater)

            self._update_checkers[bundle_name] = checker
            checker.start()
            logger.info(f"开始检查新添加的 bundle '{bundle_name}' 的更新")

        except Exception as e:
            logger.error(f"检查 bundle '{bundle_name}' 更新失败: {e}", exc_info=True)

    def _on_update_check_result(self, bundle_name: str, result: dict):
        """处理单个bundle的更新检查结果"""
        try:
            latest_version = result.get("latest_update_version", "")
            if latest_version:
                self._latest_versions[bundle_name] = latest_version
                # 更新列表项的显示
                self._update_bundle_item_version(bundle_name, latest_version)
                logger.info(f"Bundle '{bundle_name}' 最新版本: {latest_version}")
            else:
                self._latest_versions[bundle_name] = None
                # 如果没有找到更新，使用当前版本
                bundle_data = self._bundle_data.get(bundle_name, {})
                interface_data = bundle_data.get("interface", {})
                current_version = interface_data.get("version", self.tr("Unknown version"))
                self._update_bundle_item_version(bundle_name, current_version)
        except Exception as e:
            logger.error(
                f"处理 bundle '{bundle_name}' 更新检查结果失败: {e}", exc_info=True
            )

    def _update_bundle_item_version(
        self, bundle_name: str, latest_version: Optional[str]
    ):
        """更新列表项的版本显示"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == bundle_name:
                item_widget = self.list_widget.itemWidget(item)
                if isinstance(item_widget, BundleListItem):
                    item_widget.update_latest_version(latest_version)
                break

    def start_auto_update_all(self):
        """供主界面调用的自动更新所有bundle入口"""
        # 检查多资源适配是否开启
        if not self._is_multi_resource_enabled():
            logger.warning("多资源适配未开启，无法自动更新bundle")
            signalBus.all_updates_completed.emit()
            return

        if self._current_updater:
            logger.warning("已有更新任务正在进行中")
            return

        logger.info("开始自动更新所有bundle...")
        try:
            bundle_names = self.service_coordinator.config.list_bundles()
            if not bundle_names:
                logger.warning("没有找到任何bundle")
                # 没有bundle，直接发送完成信号
                signalBus.all_updates_completed.emit()
                return

            # 过滤出有更新的bundle
            bundles_to_update = []
            for bundle_name in bundle_names:
                latest_version = self._latest_versions.get(bundle_name)
                bundle_data = self._bundle_data.get(bundle_name, {})
                interface_data = bundle_data.get("interface", {})
                current_version = interface_data.get("version", "")

                # 如果还没有检查过更新（latest_version 为 None），直接加入更新队列
                # 这样新添加的 bundle 也能被更新
                if latest_version is None:
                    logger.info(f"Bundle '{bundle_name}' 尚未检查更新，加入更新队列")
                    bundles_to_update.append(bundle_name)
                # 如果有最新版本且与当前版本不同，加入更新队列
                elif latest_version and latest_version != current_version:
                    bundles_to_update.append(bundle_name)

            if not bundles_to_update:
                logger.info("所有bundle都是最新版本，无需更新")
                # 没有需要更新的bundle，直接发送完成信号
                signalBus.all_updates_completed.emit()
                return

            # 将需要更新的bundle加入队列
            self._update_queue = bundles_to_update
            self._is_updating_all = True
            self._start_next_update()

        except Exception as e:
            logger.error(f"自动更新所有bundle失败: {e}", exc_info=True)
            self._is_updating_all = False
            # 发生错误，发送完成信号
            signalBus.all_updates_completed.emit()

    def _on_update_all_bundles(self):
        """更新所有bundle按钮点击事件"""
        # 检查多资源适配是否开启
        if not self._is_multi_resource_enabled():
            signalBus.info_bar_requested.emit(
                "warning",
                self.tr(
                    "Multi-resource adaptation is not enabled. Please enable it in Settings first."
                ),
            )
            logger.warning("多资源适配未开启，无法更新bundle")
            return

        if self._current_updater:
            logger.warning("已有更新任务正在进行中")
            return

        logger.info("开始更新所有bundle...")
        try:
            bundle_names = self.service_coordinator.config.list_bundles()
            if not bundle_names:
                logger.warning("没有找到任何bundle")
                return

            # 过滤出有更新的bundle
            bundles_to_update = []
            for bundle_name in bundle_names:
                latest_version = self._latest_versions.get(bundle_name)
                bundle_data = self._bundle_data.get(bundle_name, {})
                interface_data = bundle_data.get("interface", {})
                current_version = interface_data.get("version", "")

                # 如果还没有检查过更新（latest_version 为 None），直接加入更新队列
                # 这样新添加的 bundle 也能被更新
                if latest_version is None:
                    logger.info(f"Bundle '{bundle_name}' 尚未检查更新，加入更新队列")
                    bundles_to_update.append(bundle_name)
                # 如果有最新版本且与当前版本不同，加入更新队列
                elif latest_version and latest_version != current_version:
                    bundles_to_update.append(bundle_name)

            if not bundles_to_update:
                logger.info("所有bundle都是最新版本，无需更新")
                signalBus.info_bar_requested.emit(
                    "info", self.tr("All bundles are up to date")
                )
                return

            # 将需要更新的bundle加入队列
            self._update_queue = bundles_to_update
            self._is_updating_all = True
            self._start_next_update()

        except Exception as e:
            logger.error(f"更新所有bundle失败: {e}", exc_info=True)
            self._is_updating_all = False

    def _on_single_bundle_update(self, bundle_name: str):
        """处理单个bundle的更新"""
        # 检查多资源适配是否开启
        if not self._is_multi_resource_enabled():
            signalBus.info_bar_requested.emit(
                "warning",
                self.tr(
                    "Multi-resource adaptation is not enabled. Please enable it in Settings first."
                ),
            )
            logger.warning("多资源适配未开启，无法更新bundle")
            return

        if self._current_updater:
            logger.warning("已有更新任务正在进行中")
            return

        logger.info(f"开始更新单个bundle: {bundle_name}")
        self._update_queue = [bundle_name]
        self._is_updating_all = False
        self._start_next_update()

    def _start_next_update(self):
        """开始下一个更新任务"""
        if not self._update_queue:
            # 所有更新完成
            is_auto_update_all = self._is_updating_all  # 保存状态
            self._is_updating_all = False
            self._current_updater = None

            if not is_auto_update_all:
                # 如果不是自动更新所有模式，显示通知
                signalBus.info_bar_requested.emit(
                    "success", self.tr("All updates completed")
                )
            logger.info("所有更新任务完成")

            # 使用 QTimer 延迟刷新，避免阻塞 UI 线程，同时等待文件系统同步
            def _refresh_after_update():
                # 重新加载bundles以刷新版本信息（强制刷新）
                self._load_bundles(force_refresh=True)
                # 重新检查更新以获取最新的版本信息（仅在多资源适配开启时）
                if self._is_multi_resource_enabled():
                    self._check_all_updates()

                # 如果是自动更新所有模式，发送所有更新完成信号
                if is_auto_update_all:
                    signalBus.all_updates_completed.emit()
                    logger.info(
                        "Bundle 自动更新完成，已发送 all_updates_completed 信号"
                    )

            # 延迟 500ms 执行刷新，确保文件系统同步完成，但不阻塞 UI
            QTimer.singleShot(500, _refresh_after_update)
            return

        bundle_name = self._update_queue.pop(0)
        bundle_data = self._bundle_data.get(bundle_name)
        if not bundle_data:
            # 如果bundle数据不存在，继续下一个
            self._start_next_update()
            return

        interface_data = bundle_data.get("interface", {})
        if not interface_data:
            # 如果interface数据不存在，继续下一个
            self._start_next_update()
            return

        # 创建更新器（使用 MultiResourceUpdate 子类处理多资源更新）
        updater = MultiResourceUpdate(
            service_coordinator=self.service_coordinator,
            stop_signal=signalBus.update_stopped,
            progress_signal=signalBus.update_progress,
            info_bar_signal=signalBus.info_bar_requested,
            interface=interface_data,
            force_full_download=False,
        )

        # 连接更新完成信号
        updater.finished.connect(lambda: self._on_update_finished(bundle_name))

        self._current_updater = updater
        self._current_bundle_name = bundle_name  # 保存当前更新的bundle名称
        updater.start()
        logger.info(f"开始更新 bundle: {bundle_name}")
        signalBus.info_bar_requested.emit(
            "info", self.tr(f"Updating bundle: {bundle_name}")
        )

    def _on_update_finished(self, bundle_name: str):
        """更新线程完成回调（线程结束，但不一定表示更新成功）"""
        logger.info(f"Bundle '{bundle_name}' 更新线程完成")
        # 注意：实际的更新状态通过 update_stopped 信号处理

    def _on_update_stopped(self, status: int):
        """更新停止信号处理（Update 类自动发送，用于通知主界面和更新 UI）"""
        if not self._current_updater or not self._current_bundle_name:
            # 如果不是当前 bundle 的更新，忽略（可能是其他地方的更新）
            return

        bundle_name = self._current_bundle_name
        is_auto_update_all = self._is_updating_all  # 保存状态
        logger.info(f"Bundle '{bundle_name}' 更新停止，状态码: {status}")

        if status == 1:
            # 热更新完成
            logger.info(f"Bundle '{bundle_name}' 热更新成功完成")
            if not is_auto_update_all:
                # 如果不是自动更新所有，显示通知
                signalBus.info_bar_requested.emit(
                    "success", self.tr(f"Bundle '{bundle_name}' updated successfully")
                )
        elif status == 0:
            # 用户取消
            logger.warning(f"Bundle '{bundle_name}' 更新被取消")
            if not is_auto_update_all:
                signalBus.info_bar_requested.emit(
                    "warning", self.tr(f"Update cancelled: {bundle_name}")
                )
        elif status == 2:
            # 需要重启
            logger.info(f"Bundle '{bundle_name}' 需要重启以完成更新")
            if not is_auto_update_all:
                signalBus.info_bar_requested.emit(
                    "info", self.tr(f"Restart required for bundle: {bundle_name}")
                )
        else:
            # 其他错误
            logger.error(f"Bundle '{bundle_name}' 更新失败，状态码: {status}")
            if not is_auto_update_all:
                signalBus.info_bar_requested.emit(
                    "error", self.tr(f"Update failed for bundle: {bundle_name}")
                )

        # 清理当前更新器
        self._current_updater = None
        self._current_bundle_name = None

        # 继续下一个更新（所有更新完成后再重新加载）
        self._start_next_update()

    def _on_auto_update_changed(self, checked: bool):
        """自动更新开关状态改变事件"""
        from app.common.config import cfg

        try:
            cfg.set(cfg.bundle_auto_update, checked)
            logger.info(f"Bundle 自动更新设置已更新: {'开启' if checked else '关闭'}")
        except Exception as e:
            logger.error(f"更新 Bundle 自动更新设置失败: {e}", exc_info=True)

    def _on_multi_resource_enabled(self):
        """响应多资源适配启用信号，刷新 bundle 列表"""
        logger.info("收到多资源适配启用信号，刷新 bundle 列表")
        # 重新加载配置服务的主配置
        try:
            self.service_coordinator.config_service.load_main_config()
            logger.debug("已重新加载主配置")
        except Exception as e:
            logger.warning(f"重新加载主配置失败: {e}")

        # 重新加载 bundle 列表
        self._load_bundles()

        # 如果多资源适配已开启，检查更新
        if self._is_multi_resource_enabled():
            QCoreApplication.processEvents()
            self._check_all_updates()

    def _on_add_bundle_clicked(self):
        """打开添加 bundle 对话框"""
        # 检查多资源适配是否开启
        if not self._is_multi_resource_enabled():
            signalBus.info_bar_requested.emit(
                "warning",
                self.tr(
                    "Multi-resource adaptation is not enabled. Please enable it in Settings first."
                ),
            )
            logger.warning("多资源适配未开启，无法添加bundle")
            return

        dialog = AddBundleDialog(
            service_coordinator=self.service_coordinator, parent=self
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        bundle_name, bundle_path = dialog.get_bundle_info()
        if not bundle_name or not bundle_path:
            return

        # 重新加载配置服务的主配置，确保能获取到新添加的 bundle
        try:
            self.service_coordinator.config_service.load_main_config()
            logger.debug("已重新加载主配置，确保获取到新添加的 bundle")
        except Exception as e:
            logger.warning(f"重新加载主配置失败: {e}")

        # 重新加载 bundle 列表以显示新添加的 bundle
        self._load_bundles()

        # 触发 UI 更新，确保列表刷新
        QCoreApplication.processEvents()

        # 检查新添加的 bundle 的更新（如果多资源适配已开启）
        if self._is_multi_resource_enabled():
            self._check_single_bundle_update(bundle_name)

        logger.info(f"已添加新 bundle: {bundle_name} -> {bundle_path}")
        
        # 显示成功提示
        signalBus.info_bar_requested.emit(
            "success",
            self.tr("Bundle '{}' added successfully").format(bundle_name)
        )

    def _load_release_notes(self, bundle_name: str) -> dict:
        """加载指定 bundle 的更新日志

        Args:
            bundle_name: bundle 名称（用于确定文件夹）

        Returns:
            更新日志字典，key 为版本号，value 为日志内容
        """
        release_notes_dir = f"./release_notes/{bundle_name}"
        notes = {}

        if not os.path.exists(release_notes_dir):
            return notes

        try:
            for filename in os.listdir(release_notes_dir):
                if filename.endswith(".md"):
                    version = filename[:-3]  # 移除 .md 后缀
                    file_path = os.path.join(release_notes_dir, filename)
                    with open(file_path, "r", encoding="utf-8") as f:
                        notes[version] = f.read()
        except Exception as e:
            logger.error(f"加载 bundle '{bundle_name}' 的更新日志失败: {e}")

        # 按版本号排序（降序，最新版本在前）
        sorted_notes = dict(sorted(notes.items(), key=lambda x: x[0], reverse=True))
        return sorted_notes

    def _open_bundle_update_log(self, bundle_name: str):
        """打开指定 bundle 的更新日志对话框

        Args:
            bundle_name: bundle 名称
        """
        release_notes = self._load_release_notes(bundle_name)

        if not release_notes:
            # 如果没有本地更新日志，显示提示信息
            release_notes = {
                self.tr("No update log"): self.tr(
                    "No update log found locally for this bundle.\n\n"
                    "Please check for updates first, or visit the GitHub releases page."
                )
            }

        # 获取 bundle 显示名称
        bundle_data = self._bundle_data.get(bundle_name, {})
        bundle_display_name = bundle_data.get("name", bundle_name)

        # 使用 NoticeMessageBox 显示更新日志
        dialog = NoticeMessageBox(
            parent=self,
            title=self.tr("Update Log") + f" - {bundle_display_name}",
            content=release_notes,
        )
        # 隐藏"确认且不再显示"按钮，只保留确认按钮
        dialog.button_yes.hide()
        dialog.exec()

    def _on_delete_bundle_clicked(self, bundle_name: str):
        """处理删除 bundle 按钮点击事件

        Args:
            bundle_name: bundle 名称
        """
        # 获取 bundle 显示名称
        bundle_data = self._bundle_data.get(bundle_name, {})
        bundle_display_name = bundle_data.get("name", bundle_name)

        # 查找所有使用该 bundle 的配置
        configs_to_delete = []
        try:
            all_configs = self.service_coordinator.config.list_configs()
            for config_info in all_configs:
                config_id = config_info.get("item_id")
                if not config_id:
                    continue
                
                # 获取配置的完整数据
                config_item = self.service_coordinator.config.get_config(config_id)
                if config_item and config_item.bundle == bundle_name:
                    configs_to_delete.append({
                        "id": config_id,
                        "name": config_item.name
                    })
        except Exception as e:
            logger.error(f"查找使用 bundle '{bundle_name}' 的配置时出错: {e}")
            signalBus.info_bar_requested.emit(
                "error",
                self.tr("Failed to find configurations using this bundle: {}").format(str(e))
            )
            return

        # 构建确认消息
        config_names = [cfg["name"] for cfg in configs_to_delete]
        if config_names:
            message = self.tr(
                "Are you sure you want to delete bundle '{}'?\n\n"
                "The following configurations using this bundle will also be deleted:\n"
                "{}"
            ).format(bundle_display_name, "\n".join(f"  - {name}" for name in config_names))
        else:
            message = self.tr(
                "Are you sure you want to delete bundle '{}'?"
            ).format(bundle_display_name)

        # 弹出确认对话框
        from qfluentwidgets import MessageBox
        
        msg_box = MessageBox(
            self.tr("Delete Bundle"),
            message,
            self
        )

        if msg_box.exec() != msg_box.DialogCode.Accepted:
            return

        # 用户确认，开始删除
        try:
            # 先删除所有使用该 bundle 的配置
            for config_info in configs_to_delete:
                config_id = config_info["id"]
                config_name = config_info["name"]
                try:
                    success = self.service_coordinator.delete_config(config_id)
                    if success:
                        logger.info(f"已删除配置: {config_name} ({config_id})")
                    else:
                        logger.warning(f"删除配置失败: {config_name} ({config_id})")
                except Exception as e:
                    logger.error(f"删除配置 '{config_name}' 时出错: {e}")

            # 删除 bundle
            success = self.service_coordinator.delete_bundle(bundle_name)
            if success:
                logger.info(f"已删除 bundle: {bundle_display_name} ({bundle_name})")
                signalBus.info_bar_requested.emit(
                    "success",
                    self.tr("Bundle '{}' and {} related configuration(s) deleted successfully").format(
                        bundle_display_name, len(configs_to_delete)
                    )
                )
                
                # 重新加载配置服务的主配置
                try:
                    self.service_coordinator.config_service.load_main_config()
                except Exception as e:
                    logger.warning(f"重新加载主配置失败: {e}")
                
                # 刷新 bundle 列表
                self._load_bundles()
            else:
                logger.error(f"删除 bundle 失败: {bundle_display_name}")
                signalBus.info_bar_requested.emit(
                    "error",
                    self.tr("Failed to delete bundle: {}").format(bundle_display_name)
                )
        except Exception as e:
            logger.error(f"删除 bundle '{bundle_name}' 时发生错误: {e}", exc_info=True)
            signalBus.info_bar_requested.emit(
                "error",
                self.tr("An error occurred while deleting bundle: {}").format(str(e))
            )


class AddBundleDialog(MessageBoxBase):
    """添加 Bundle 对话框"""

    def __init__(
        self, service_coordinator: ServiceCoordinator | None = None, parent=None
    ) -> None:
        super().__init__(parent)
        self._service_coordinator = service_coordinator
        self.setWindowTitle(self.tr("Add Resource Bundle"))
        self.widget.setMinimumWidth(420)
        self.widget.setMinimumHeight(200)

        self.titleLabel = SubtitleLabel(self.tr("Add Resource Bundle"), self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addSpacing(8)

        # 资源名称
        self.name_layout = QVBoxLayout()
        self.name_label = BodyLabel(self.tr("Bundle Name:"), self)
        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText(self.tr("Enter the name of the bundle"))
        self.name_edit.setClearButtonEnabled(True)
        self.name_layout.addWidget(self.name_label)
        self.name_layout.addWidget(self.name_edit)

        # 资源路径 + 选择按钮
        self.path_layout = QHBoxLayout()
        self.path_label = BodyLabel(self.tr("Interface File:"), self)
        self.path_edit = LineEdit(self)
        self.path_edit.setPlaceholderText(
            self.tr("Select interface.json or interface.jsonc file")
        )
        self.path_edit.setClearButtonEnabled(True)
        self.path_button = ToolButton(FIF.DOCUMENT, self)
        self.path_button.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.path_button.clicked.connect(self._choose_bundle_source)

        self.path_layout.addWidget(self.path_edit)
        self.path_layout.addWidget(self.path_button)

        # 名称行：标签+输入框
        self.viewLayout.addLayout(self.name_layout)
        self.viewLayout.addSpacing(4)
        # 路径行：标签+输入框+按钮
        self.viewLayout.addWidget(self.path_label)
        self.viewLayout.addLayout(self.path_layout)

        self.yesButton.setText(self.tr("Confirm"))
        self.cancelButton.setText(self.tr("Cancel"))
        # 先断开可能的默认连接，再连接我们的处理方法
        try:
            self.yesButton.clicked.disconnect()
        except TypeError:
            # 如果没有连接，disconnect() 会抛出 TypeError，忽略即可
            pass
        self.yesButton.clicked.connect(self._on_confirm)

        self._bundle_name: str = ""
        self._bundle_path: str = ""
        self._is_processing: bool = False  # 防止重复执行

    def _choose_bundle_source(self) -> None:
        """选择 interface.json 文件。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose Interface File"),
            "./",
            "Interface Files (interface.json interface.jsonc);;JSON Files (*.json *.jsonc);;All Files (*)",
        )
        if not file_path:
            return

        p = Path(file_path)

        # 只接受 interface.json 或 interface.jsonc 文件
        if p.is_file() and p.name.lower() in ("interface.json", "interface.jsonc"):
            self.path_edit.setText(str(p))
            # 读取 interface.json 的 name 字段以预填 bundle 名称
            self._read_interface_name_and_prefill(p)
        else:
            signalBus.info_bar_requested.emit(
                "warning",
                self.tr("Please select interface.json or interface.jsonc file")
            )

    def _read_interface_name_and_prefill(self, interface_path: Path) -> None:
        """读取 interface.json 的 name 字段并预填到名称输入框。"""
        interface_name = ""
        try:
            with open(interface_path, "r", encoding="utf-8") as f:
                data = jsonc.load(f)
            iface_name = data.get("name")
            if isinstance(iface_name, str) and iface_name.strip():
                interface_name = iface_name.strip()
        except Exception:
            interface_name = ""

        current_name = self.name_edit.text().strip()
        if not current_name:
            if interface_name:
                self.name_edit.setText(interface_name)
            else:
                self.name_edit.setText("Default Bundle")

    def _on_confirm(self) -> None:
        # 防止重复执行
        if self._is_processing:
            logger.warning("[防重复] _on_confirm 已在处理中，忽略重复调用")
            return
        
        self._is_processing = True
        logger.info("=" * 60)
        logger.info("开始添加 bundle 流程")
        logger.info("=" * 60)
        
        name = self.name_edit.text().strip()
        interface_file_path = self.path_edit.text().strip()
        
        logger.info(f"[步骤1] 获取用户输入:")
        logger.info(f"  - 名称输入框内容: '{name}'")
        logger.info(f"  - 路径输入框内容: '{interface_file_path}'")

        def _show_error(msg: str) -> None:
            # 通过信号总线发送 InfoBar 通知，由主窗口统一处理
            logger.error(f"[错误] {msg}")
            signalBus.info_bar_requested.emit("error", msg)
            self._is_processing = False  # 错误时重置标志

        try:
            logger.info("[步骤2] 验证输入路径")
            if not interface_file_path:
                logger.error("[步骤2] 失败: 路径为空")
                _show_error(self.tr("Interface file path cannot be empty"))
                return
            logger.info(f"[步骤2] 路径不为空: '{interface_file_path}'")

            interface_path = Path(interface_file_path)
            logger.info(f"[步骤2] 转换为 Path 对象: {interface_path}")
            logger.info(f"[步骤2] 绝对路径: {interface_path.resolve()}")
            
            if not interface_path.exists():
                logger.error(f"[步骤2] 失败: 文件不存在 - {interface_path}")
                _show_error(self.tr("Selected interface file does not exist"))
                return
            logger.info(f"[步骤2] 文件存在: {interface_path.exists()}")

            if not interface_path.is_file():
                logger.error(f"[步骤2] 失败: 不是文件 - {interface_path}")
                _show_error(self.tr("Selected path is not a file"))
                return
            logger.info(f"[步骤2] 是文件: {interface_path.is_file()}")

            file_name_lower = interface_path.name.lower()
            logger.info(f"[步骤2] 文件名: '{interface_path.name}' (小写: '{file_name_lower}')")
            if file_name_lower not in ("interface.json", "interface.jsonc"):
                logger.error(f"[步骤2] 失败: 文件名不符合要求 - '{interface_path.name}'")
                _show_error(self.tr("Please select interface.json or interface.jsonc file"))
                return
            logger.info(f"[步骤2] 文件名验证通过")

            # 读取 interface.json 获取 name
            logger.info("[步骤3] 读取 interface.json 文件")
            interface_name = ""
            try:
                logger.info(f"[步骤3] 打开文件: {interface_path}")
                with open(interface_path, "r", encoding="utf-8") as f:
                    data = jsonc.load(f)
                logger.info(f"[步骤3] 成功读取 JSON 数据，键: {list(data.keys())[:10]}...")
                
                iface_name = data.get("name")
                logger.info(f"[步骤3] 从数据中获取 'name' 字段: '{iface_name}' (类型: {type(iface_name)})")
                
                if isinstance(iface_name, str) and iface_name.strip():
                    interface_name = iface_name.strip()
                    logger.info(f"[步骤3] 提取的 bundle 名称: '{interface_name}'")
                else:
                    logger.warning(f"[步骤3] 'name' 字段无效: {iface_name}")
            except Exception as e:
                logger.error(f"[步骤3] 读取 interface.json 失败: {e}", exc_info=True)
                _show_error(self.tr("Failed to read interface.json: {}").format(str(e)))
                return

            # 使用 interface.json 中的 name 作为 bundle_name
            if not interface_name:
                logger.error("[步骤3] 失败: interface.json 中没有有效的 'name' 字段")
                _show_error(self.tr("interface.json does not contain a valid 'name' field"))
                return

            bundle_name = interface_name
            logger.info(f"[步骤3] 最终确定的 bundle 名称: '{bundle_name}'")

            # 通过服务层接口写入 bundle，避免直接操作私有 _main_config
            logger.info("[步骤4] 检查服务协调器")
            if not self._service_coordinator:
                logger.error("[步骤4] 失败: 服务协调器未初始化")
                _show_error(self.tr("Service is not ready, cannot save bundle"))
                return
            logger.info("[步骤4] 服务协调器可用")

            coordinator = self._service_coordinator
            config_service = coordinator.config_service
            logger.info(f"[步骤4] 获取配置服务: {type(config_service).__name__}")

            # 检查是否已存在同名的 bundle
            logger.info("[步骤5] 检查是否已存在同名的 bundle")
            try:
                existing_bundles = config_service.list_bundles()
                logger.info(f"[步骤5] 现有 bundle 列表: {existing_bundles}")
                logger.info(f"[步骤5] 检查 bundle 名称 '{bundle_name}' 是否已存在")
                if bundle_name in existing_bundles:
                    logger.error(f"[步骤5] 失败: bundle 名称 '{bundle_name}' 已存在")
                    _show_error(self.tr("Bundle name already exists"))
                    return
                logger.info(f"[步骤5] bundle 名称 '{bundle_name}' 可用")
            except Exception as exc:
                logger.error(f"[步骤5] 检查现有 bundle 时出错: {exc}", exc_info=True)
                _show_error(self.tr("Failed to check existing bundles: {}").format(str(exc)))
                return

            # 获取 interface.json 的父目录（源 bundle 目录路径）
            logger.info("[步骤6] 确定源 bundle 目录")
            source_bundle_dir = interface_path.parent
            logger.info(f"[步骤6] interface.json 父目录: {source_bundle_dir}")
            logger.info(f"[步骤6] 绝对路径: {source_bundle_dir.resolve()}")

            # 验证源 bundle 目录是否存在且为目录
            logger.info("[步骤6] 验证源 bundle 目录")
            if not source_bundle_dir.exists():
                logger.error(f"[步骤6] 失败: 源目录不存在 - {source_bundle_dir}")
                _show_error(self.tr("Bundle directory does not exist: {}").format(str(source_bundle_dir)))
                return
            logger.info(f"[步骤6] 源目录存在: {source_bundle_dir.exists()}")
            
            if not source_bundle_dir.is_dir():
                logger.error(f"[步骤6] 失败: 不是目录 - {source_bundle_dir}")
                _show_error(self.tr("Bundle path is not a directory: {}").format(str(source_bundle_dir)))
                return
            logger.info(f"[步骤6] 是目录: {source_bundle_dir.is_dir()}")
            
            # 列出源目录内容
            try:
                source_items = list(source_bundle_dir.iterdir())
                logger.info(f"[步骤6] 源目录内容 ({len(source_items)} 项):")
                for item in source_items[:10]:  # 只显示前10项
                    logger.info(f"  - {item.name} ({'目录' if item.is_dir() else '文件'})")
                if len(source_items) > 10:
                    logger.info(f"  ... 还有 {len(source_items) - 10} 项")
            except Exception as e:
                logger.warning(f"[步骤6] 列出源目录内容时出错: {e}")

            # 确定目标 bundle 目录（使用 bundle_name 作为文件夹名）
            logger.info("[步骤7] 确定目标 bundle 目录")
            bundle_dir = Path.cwd() / "bundle" / bundle_name
            logger.info(f"[步骤7] 当前工作目录: {Path.cwd()}")
            logger.info(f"[步骤7] 目标目录: {bundle_dir}")
            logger.info(f"[步骤7] 目标目录绝对路径: {bundle_dir.resolve()}")

            # 检查源目录是否已经是目标目录（规范化路径比较）
            logger.info("[步骤8] 检查源目录和目标目录的关系")
            try:
                source_resolved = source_bundle_dir.resolve()
                bundle_resolved = bundle_dir.resolve()
                logger.info(f"[步骤8] 源目录绝对路径: {source_resolved}")
                logger.info(f"[步骤8] 目标目录绝对路径: {bundle_resolved}")
                logger.info(f"[步骤8] 路径是否相同: {source_resolved == bundle_resolved}")
                
                if source_resolved == bundle_resolved:
                    # 源目录已经是目标目录，不需要移动，直接使用
                    logger.info(f"[步骤8] 源目录已经是目标目录，跳过移动: {bundle_dir}")
                elif bundle_resolved.is_relative_to(source_resolved):
                    # 目标目录是源目录的子目录，这是不允许的
                    logger.error(f"[步骤8] 失败: 目标目录是源目录的子目录")
                    logger.error(f"[步骤8] 源目录: {source_resolved}")
                    logger.error(f"[步骤8] 目标目录: {bundle_resolved}")
                    _show_error(
                        self.tr("Cannot move bundle: target directory is inside source directory")
                    )
                    return
                else:
                    logger.info(f"[步骤8] 源目录和目标目录不同，需要移动")
            except Exception as e:
                logger.warning(f"[步骤8] 检查源目录和目标目录关系时出错: {e}", exc_info=True)

            # 如果目标目录已存在，直接删除并覆盖
            logger.info("[步骤9] 检查目标目录是否存在")
            logger.info(f"[步骤9] 目标目录存在: {bundle_dir.exists()}")
            if bundle_dir.exists():
                logger.info(f"[步骤9] 目标目录已存在，直接删除并覆盖")
                
                # 删除现有目录
                logger.info("[步骤9] 开始删除现有目录")
                try:
                    if bundle_dir.is_dir():
                        logger.info(f"[步骤9] 删除目录: {bundle_dir}")
                        shutil.rmtree(bundle_dir)
                        logger.info(f"[步骤9] 目录删除成功")
                    else:
                        logger.info(f"[步骤9] 删除文件: {bundle_dir}")
                        bundle_dir.unlink()
                        logger.info(f"[步骤9] 文件删除成功")
                except Exception as e:
                    logger.error(f"[步骤9] 删除现有 bundle 目录失败: {e}", exc_info=True)
                    _show_error(
                        self.tr(
                            "Failed to remove existing bundle directory: {}"
                        ).format(str(e))
                    )
                    return
                logger.info(f"[步骤9] 现有目录已删除，目标目录现在不存在: {not bundle_dir.exists()}")

            # 只在源目录不是目标目录时才需要移动
            logger.info("[步骤10] 判断是否需要移动文件")
            try:
                source_resolved = source_bundle_dir.resolve()
                bundle_resolved = bundle_dir.resolve()
                need_move = source_resolved != bundle_resolved
                logger.info(f"[步骤10] 需要移动: {need_move}")
            except Exception as e:
                logger.warning(f"[步骤10] 判断是否需要移动时出错，默认需要移动: {e}")
                need_move = True

            if need_move:
                logger.info("[步骤11] 开始移动文件流程")
                # 创建目标目录
                logger.info("[步骤11.1] 创建目标目录")
                try:
                    logger.info(f"[步骤11.1] 创建目录: {bundle_dir}")
                    bundle_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(f"[步骤11.1] 目录创建成功，存在: {bundle_dir.exists()}")
                except Exception as e:
                    logger.error(f"[步骤11.1] 创建 bundle 目录失败: {e}", exc_info=True)
                    _show_error(
                        self.tr("Failed to create bundle directory: {}").format(str(e))
                    )
                    return

                # 移动源目录下的所有文件和文件夹到目标目录
                logger.info("[步骤11.2] 开始移动文件")
                try:
                    source_items = list(source_bundle_dir.iterdir())
                    logger.info(f"[步骤11.2] 需要移动 {len(source_items)} 项")
                    
                    moved_count = 0
                    for item in source_items:
                        target_item = bundle_dir / item.name
                        logger.info(f"[步骤11.2] 移动: {item.name} ({'目录' if item.is_dir() else '文件'})")
                        logger.info(f"[步骤11.2]   源: {item}")
                        logger.info(f"[步骤11.2]   目标: {target_item}")
                        
                        shutil.move(str(item), str(target_item))
                        moved_count += 1
                        logger.info(f"[步骤11.2]   移动成功 ({moved_count}/{len(source_items)})")
                    
                    logger.info(f"[步骤11.2] 所有文件移动完成，共移动 {moved_count} 项到: {bundle_dir}")
                    
                    # 验证目标目录内容
                    try:
                        target_items = list(bundle_dir.iterdir())
                        logger.info(f"[步骤11.2] 目标目录现在包含 {len(target_items)} 项")
                    except Exception as e:
                        logger.warning(f"[步骤11.2] 验证目标目录内容时出错: {e}")
                    
                    # 如果源目录为空，尝试删除源目录（可选）
                    logger.info("[步骤11.3] 检查源目录是否为空")
                    try:
                        remaining_items = list(source_bundle_dir.iterdir())
                        logger.info(f"[步骤11.3] 源目录剩余项: {len(remaining_items)}")
                        if not remaining_items:
                            logger.info(f"[步骤11.3] 源目录为空，尝试删除: {source_bundle_dir}")
                            source_bundle_dir.rmdir()
                            logger.info(f"[步骤11.3] 源目录删除成功")
                        else:
                            logger.info(f"[步骤11.3] 源目录不为空，保留源目录")
                    except Exception as e:
                        logger.warning(f"[步骤11.3] 删除源目录时出错（可忽略）: {e}")
                except Exception as e:
                    logger.error(f"[步骤11.2] 移动 bundle 到目标目录失败: {e}", exc_info=True)
                    _show_error(
                        self.tr("Failed to move bundle to target directory: {}").format(
                            str(e)
                        )
                    )
                    # 清理：如果移动失败，删除已创建的目标目录
                    logger.info("[步骤11.2] 清理失败的目标目录")
                    try:
                        if bundle_dir.exists():
                            logger.info(f"[步骤11.2] 删除目标目录: {bundle_dir}")
                            shutil.rmtree(bundle_dir)
                            logger.info(f"[步骤11.2] 目标目录已清理")
                    except Exception as cleanup_err:
                        logger.error(f"[步骤11.2] 清理目标目录时出错: {cleanup_err}")
                    return
            else:
                # 源目录已经是目标目录，不需要移动
                logger.info(f"[步骤11] 源目录已经是目标目录，跳过移动: {bundle_dir}")

            # 将路径转换为相对路径
            logger.info("[步骤12] 转换路径为相对路径")
            try:
                cwd_resolved = Path.cwd().resolve()
                bundle_resolved = bundle_dir.resolve()
                logger.info(f"[步骤12] 当前工作目录: {cwd_resolved}")
                logger.info(f"[步骤12] bundle 绝对路径: {bundle_resolved}")
                
                rel = bundle_resolved.relative_to(cwd_resolved)
                normalized = f"./{rel.as_posix()}"
                logger.info(f"[步骤12] 相对路径: {rel}")
                logger.info(f"[步骤12] 规范化路径: {normalized}")
            except Exception as e:
                logger.warning(f"[步骤12] 转换为相对路径失败，使用绝对路径: {e}")
                normalized = os.path.abspath(str(bundle_dir))
                logger.info(f"[步骤12] 使用绝对路径: {normalized}")

            # 更新 bundle 配置
            logger.info("[步骤13] 更新 bundle 配置")
            logger.info(f"[步骤13] bundle 名称: {bundle_name}")
            logger.info(f"[步骤13] bundle 路径: {normalized}")
            try:
                success = coordinator.update_bundle_path(
                    bundle_name=bundle_name,
                    new_path=normalized,
                    bundle_display_name=bundle_name,
                )
                logger.info(f"[步骤13] update_bundle_path 返回: {success}")
                if not success:
                    logger.error("[步骤13] 更新 bundle 路径失败")
                    _show_error(self.tr("Failed to update bundle path"))
                    return
                logger.info("[步骤13] bundle 配置更新成功")
            except Exception as exc:
                logger.error(f"[步骤13] 更新 bundle 路径时出错: {exc}", exc_info=True)
                _show_error(self.tr("Failed to update bundle path: {}").format(str(exc)))
                return

            self._bundle_name = bundle_name
            self._bundle_path = normalized
            logger.info("=" * 60)
            logger.info(f"成功添加 bundle: {bundle_name} -> {normalized}")
            logger.info("=" * 60)
            self._is_processing = False  # 成功时重置标志
            self.accept()
        except Exception as e:
            logger.error("=" * 60)
            logger.error(f"添加 bundle 时发生未预期的错误: {e}", exc_info=True)
            logger.error("=" * 60)
            self._is_processing = False  # 异常时重置标志
            _show_error(self.tr("An unexpected error occurred: {}").format(str(e)))

    def get_bundle_info(self) -> tuple[str, str]:
        return self._bundle_name, self._bundle_path

    def closeEvent(self, event) -> None:
        """对话框关闭事件。"""
        super().closeEvent(event)
