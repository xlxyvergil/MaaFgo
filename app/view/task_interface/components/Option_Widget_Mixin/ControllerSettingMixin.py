from typing import Dict, Any, Type
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
from PySide6.QtGui import QIntValidator
from PySide6.QtCore import QTimer
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    LineEdit,
    ToolTipFilter,
)
from pathlib import Path

import jsonc
import sys
from app.utils.gpu_cache import gpu_cache
from app.utils.logger import logger
from app.common.config import cfg
from app.core.core import ServiceCoordinator
from app.widget.PathLineEdit import PathLineEdit
from app.view.task_interface.components.Option_Widget_Mixin.DeviceFinderWidget import (
    DeviceFinderWidget,
)
from PySide6.QtWidgets import QWidget
from maa.define import (
    MaaWin32InputMethodEnum,
    MaaWin32ScreencapMethodEnum,
    MaaAdbInputMethodEnum,
    MaaAdbScreencapMethodEnum,
)


def _build_method_options_from_enum(
    enum_cls: Type[Any],
    *,
    include_null: bool,
    include_default: bool,
) -> Dict[str, int]:
    """
    从 maa.define 枚举自动构建下拉框选项字典。

    约定：
    - `null`、`default` 采用兼容旧配置的展示键名；
    - 仅当枚举真实存在 `Default` 成员时才生成 `default`；
    - 其余成员按枚举定义顺序自动加入，避免硬编码维护。
    """
    members = getattr(enum_cls, "__members__", {})
    options: Dict[str, int] = {}

    if include_null and "Null" in members:
        options["null"] = int(members["Null"].value)

    if include_default and "Default" in members:
        options["default"] = int(members["Default"].value)

    for name, member in members.items():
        if name in {"Null", "All", "Default"}:
            continue
        options[name] = int(member.value)

    return options


class ControllerSettingWidget(QWidget):
    """
    控制器设置组件 - 固定UI实现
    """

    resource_setting_widgets: Dict[str, Any]
    CHILD = [300, 300]

    # 这些方法由 OptionWidget 动态设置（如果未设置则使用默认实现）
    _toggle_description: Any = None
    _set_description: Any = None

    # 映射表定义
    WIN32_INPUT_METHOD_ALIAS_VALUES: Dict[str, int] = _build_method_options_from_enum(
        MaaWin32InputMethodEnum,
        include_null=True,
        include_default=False,
    )
    WIN32_SCREENCAP_METHOD_ALIAS_VALUES: Dict[
        str, int
    ] = _build_method_options_from_enum(
        MaaWin32ScreencapMethodEnum,
        include_null=True,
        include_default=False,
    )
    ADB_SCREENCAP_OPTIONS: Dict[str, int] = _build_method_options_from_enum(
        MaaAdbScreencapMethodEnum,
        include_null=False,
        include_default=True,
    )
    ADB_INPUT_OPTIONS: Dict[str, int] = _build_method_options_from_enum(
        MaaAdbInputMethodEnum,
        include_null=False,
        include_default=True,
    )
    GAMEPAD_TYPE_OPTIONS: Dict[str, int] = {
        "Xbox360": 0,
        "DualShock4": 1,
    }

    def _resolve_gamepad_type_value(self, value: Any) -> int | None:
        """解析 gamepad_type（兼容 int / 0|1 字符串 / Xbox360|DualShock4 字符串）"""
        int_value = self._coerce_int(value)
        if int_value is not None:
            return int_value

        if not isinstance(value, str):
            return None

        normalized = self._normalize_method_name(value)
        if normalized in ("xbox360", "xbox"):
            return 0
        if normalized in ("dualshock4", "ds4"):
            return 1
        return None

    def _value_to_index(self, combo: ComboBox, value: Any) -> int:
        """将值转换为下拉框的索引，通过在下拉框中查找对应的 userData"""
        # 确保 value 是 int 类型
        int_value = self._coerce_int(value)
        if int_value is None:
            return 0

        # 在下拉框中查找对应的 userData
        for idx in range(combo.count()):
            item_data = combo.itemData(idx)
            # 处理可能的类型不匹配
            item_int = self._coerce_int(item_data)
            if item_int is not None and item_int == int_value:
                return idx
        return 0  # 默认返回0如果值不存在

    def _value_to_index_any(self, combo: ComboBox, value: Any) -> int:
        """将值转换为下拉框索引：兼容 int 与 string（优先按 int 比较，其次按原值比较）"""
        # 先走 int 兼容逻辑（支持 value="1" 但 itemData=1 的情况）
        int_value = self._coerce_int(value)
        for idx in range(combo.count()):
            item_data = combo.itemData(idx)
            item_int = self._coerce_int(item_data)
            if int_value is not None and item_int is not None and item_int == int_value:
                return idx

        # 再走原值比较（用于 gamepad_type 这类字符串）
        for idx in range(combo.count()):
            item_data = combo.itemData(idx)
            if item_data == value:
                return idx
            if isinstance(item_data, str) and isinstance(value, str):
                if item_data.lower() == value.lower():
                    return idx

        return 0

    def _coerce_int(self, value: Any) -> int | None:
        """尝试将值转换为整数，失败则返回 None"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _sync_controller_meta_fields(
        self,
        controller_name: str,
        controller_info: dict[str, Any] | None,
        *,
        persist: bool,
    ) -> dict[str, Any]:
        """
        将 interface 中控制器的元信息字段复制到“该控制器自己的子配置字典”中：
        例如 config 里的 "Win32控制器": {...}

        规则：
        - 控制器里写了什么（同名键），配置里就记什么
        - 如果为 None 或不存在就不记
        - 仅在值发生变化时才触发保存，避免无意义刷新
        """
        if not controller_name:
            return {}

        info = controller_info or {}
        controller_cfg = self.current_config.setdefault(controller_name, {})
        if not isinstance(controller_cfg, dict):
            controller_cfg = {}
            self.current_config[controller_name] = controller_cfg

        changed: dict[str, Any] = {}
        for key in ("permission_required", "display_short_side", "display_long_side", "display_raw"):
            if key not in info:
                continue
            value = info.get(key)
            if value is None:
                continue
            if controller_cfg.get(key) != value:
                controller_cfg[key] = value
                changed[key] = value

        if persist and changed:
            # 仅提交当前控制器子配置，确保落盘到 configs/*.json 的对应控制器块中
            self._auto_save_options({controller_name: controller_cfg})

        return changed

    def _persist_current_controller_meta_if_needed(self) -> None:
        """初始化阶段补偿：将当前控制器的 meta 字段复制到子配置并落盘。"""
        controller_name = self.current_controller_name
        ctrl_info = self.current_controller_info
        if not controller_name or not isinstance(ctrl_info, dict):
            return

        changed = self._sync_controller_meta_fields(
            controller_name, ctrl_info, persist=False
        )
        if not changed:
            return

        payload: dict[str, Any] = {controller_name: self.current_config[controller_name]}
        if "controller_type" in self.current_config:
            payload["controller_type"] = self.current_config["controller_type"]
        self._auto_save_options(payload)

    @staticmethod
    def _normalize_method_name(value: str) -> str:
        """将方法名标准化以便查找别名"""
        return "".join(ch.lower() for ch in value if ch.isalnum())

    def _normalize_alias_map(self, alias_map: Dict[str, int]) -> Dict[str, int]:
        """用标准化的键创建别名映射"""
        normalized_map: Dict[str, int] = {}
        for name, mapped_value in alias_map.items():
            normalized_key = self._normalize_method_name(name)
            if normalized_key:
                normalized_map[normalized_key] = mapped_value
        return normalized_map

    def _build_win32_method_alias_map(self) -> Dict[str, Dict[str, int]]:
        """构建 Win32 输入/截图方法别名映射"""
        input_aliases = self._normalize_alias_map(self.WIN32_INPUT_METHOD_ALIAS_VALUES)
        screencap_aliases = self._normalize_alias_map(
            self.WIN32_SCREENCAP_METHOD_ALIAS_VALUES
        )

        return {
            "mouse": input_aliases,
            "keyboard": input_aliases,
            "screencap": screencap_aliases,
        }

    def _resolve_win32_setting_value(
        self, value: Any, method_type: str | None = None
    ) -> int | None:
        """尝试解析 controller 配置中的输入/截图方法值"""
        int_value = self._coerce_int(value)
        if int_value is not None:
            return int_value

        if method_type is None or not isinstance(value, str):
            return None

        normalized_value = self._normalize_method_name(value)
        if not normalized_value:
            return None

        # 兼容旧 interface/config 的 "default" 写法：
        # Win32 枚举本身没有 Default 成员，这里对齐当前连接层默认行为。
        if normalized_value == "default":
            mt = method_type.lower()
            if mt in {"mouse", "keyboard", "input"}:
                return int(MaaWin32InputMethodEnum.Seize.value)
            if mt == "screencap":
                return int(MaaWin32ScreencapMethodEnum.DXGI_DesktopDup.value)

        alias_map = self.win32_method_alias_map.get(method_type.lower())
        if not alias_map:
            return None

        # 直接查找标准化后的键名
        if (mapped := alias_map.get(normalized_value)) is not None:
            return mapped

        # 兜底：包含关键字的情况也能命中（仅对 mouse/keyboard 生效）
        if method_type.lower() in {"mouse", "keyboard"}:
            fallback_rules = (
                ("sendmessagewithcursorpos", "sendmessagewithcursorpos"),
                ("postmessagewithcursorpos", "postmessagewithcursorpos"),
                ("sendmessage", "sendmessage"),
                ("postmessage", "postmessage"),
                ("legacyevent", "legacyevent"),
                ("postthreadmessage", "postthreadmessage"),
                ("seize", "seize"),
            )
            for keyword, alias_key in fallback_rules:
                if keyword in normalized_value and alias_key in alias_map:
                    return alias_map[alias_key]

        # 对于 screencap，如果直接查找失败，记录警告并返回 None
        if method_type.lower() == "screencap":
            logger.warning(
                f"无法解析截图方法值: {value} (标准化后: {normalized_value}), "
                f"可用的键: {sorted(alias_map.keys())}"
            )

        return None

    def _find_win32_candidate_value(
        self,
        normalized: dict[str, Any],
        candidates: list[str],
        method_type: str | None = None,
    ) -> int | None:
        """从一组候选键中获取并转换整型值"""
        for candidate in candidates:
            candidate_key = candidate.lower()
            if candidate_key in normalized:
                value = self._resolve_win32_setting_value(
                    normalized[candidate_key], method_type
                )
                if value is not None:
                    return value
        return None

    def _build_win32_default_mapping(
        self, controllers: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """构建 Win32 控制器的默认输入/截图映射"""
        win32_mapping: dict[str, dict[str, Any]] = {}
        for controller in controllers:
            if controller.get("type", "").lower() != "win32":
                continue
            controller_name = controller.get("name", "")
            if not controller_name:
                continue

            win32_config = controller.get("win32")
            if not isinstance(win32_config, dict):
                continue

            normalized = {
                str(key).lower(): value
                for key, value in win32_config.items()
                if isinstance(key, str)
            }

            mouse_value = self._find_win32_candidate_value(
                normalized, ["mouse_input", "mouse"], "mouse"
            )
            keyboard_value = self._find_win32_candidate_value(
                normalized, ["keyboard_input", "keyboard"], "keyboard"
            )
            general_input = self._find_win32_candidate_value(
                normalized, ["input", "input_method", "input_methods"], "input"
            )
            if mouse_value is None:
                mouse_value = general_input
            if keyboard_value is None:
                keyboard_value = general_input

            defaults: dict[str, int] = {}
            if mouse_value is not None:
                defaults["mouse_input_methods"] = mouse_value
            if keyboard_value is not None:
                defaults["keyboard_input_methods"] = keyboard_value

            screencap_value = self._find_win32_candidate_value(
                normalized,
                [
                    "screencap",
                    "screencap_method",
                    "screencap_methods",
                    "screenshot",
                    "screen_cap",
                ],
                "screencap",
            )
            if screencap_value is not None:
                defaults["win32_screencap_methods"] = screencap_value

            mapping_entry: dict[str, Any] = {"defaults": defaults}
            class_regex = normalized.get("class_regex")
            window_regex = normalized.get("window_regex")
            if class_regex:
                mapping_entry["class_regex"] = str(class_regex)
            if window_regex:
                mapping_entry["window_regex"] = str(window_regex)

            if (
                defaults
                or "class_regex" in mapping_entry
                or "window_regex" in mapping_entry
            ):
                win32_mapping[controller_name] = mapping_entry

        return win32_mapping

    def _build_gamepad_default_mapping(
        self, controllers: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """构建 Gamepad 控制器的默认映射（主要用于窗口筛选 & gamepad_type 默认值）"""
        gamepad_mapping: dict[str, dict[str, Any]] = {}
        for controller in controllers:
            if controller.get("type", "").lower() != "gamepad":
                continue
            controller_name = controller.get("name", "")
            if not controller_name:
                continue

            gamepad_config = controller.get("gamepad")
            if not isinstance(gamepad_config, dict):
                gamepad_config = {}

            normalized = {
                str(key).lower(): value
                for key, value in gamepad_config.items()
                if isinstance(key, str)
            }

            mapping_entry: dict[str, Any] = {}
            class_regex = normalized.get("class_regex")
            window_regex = normalized.get("window_regex")
            if class_regex:
                mapping_entry["class_regex"] = str(class_regex)
            if window_regex:
                mapping_entry["window_regex"] = str(window_regex)

            # 兼容 interface.json 里写 "Xbox360" / 0 的两种形式
            gt_val = self._resolve_gamepad_type_value(normalized.get("gamepad_type"))
            if gt_val is not None:
                mapping_entry["defaults"] = {"gamepad_type": gt_val}

            if mapping_entry:
                gamepad_mapping[controller_name] = mapping_entry

        return gamepad_mapping

    def _ensure_defaults(self, controller_cfg: dict, defaults: dict):
        """确保指定键存在于控制器配置里"""
        for key, value in defaults.items():
            controller_cfg.setdefault(key, value)

    def _ensure_win32_input_defaults(self, controller_cfg: dict, controller_name: str):
        """为 Win32 控制器设置输入/截图默认值"""
        # 只有当传入的控制器名称与当前选择的控制器一致时，才从interface中读取默认值
        if (
            not self.current_controller_name
            or controller_name != self.current_controller_name
        ):
            return

        win32_defaults = self.win32_default_mapping.get(controller_name, {}).get(
            "defaults", {}
        )
        for key in [
            "mouse_input_methods",
            "keyboard_input_methods",
            "win32_screencap_methods",
        ]:
            controller_cfg.setdefault(key, win32_defaults.get(key, 0))

    def _get_win32_regex_filters(
        self, controller_name: str
    ) -> tuple[str | None, str | None]:
        """获取 Win32 控制器的 class/window regex"""
        mapping_data = self.win32_default_mapping.get(controller_name, {})
        return (
            mapping_data.get("class_regex"),
            mapping_data.get("window_regex"),
        )

    def _get_gamepad_regex_filters(
        self, controller_name: str
    ) -> tuple[str | None, str | None]:
        """获取 Gamepad 控制器的 class/window regex"""
        mapping_data = getattr(self, "gamepad_default_mapping", {}).get(
            controller_name, {}
        )
        return (
            mapping_data.get("class_regex"),
            mapping_data.get("window_regex"),
        )

    def __init__(
        self,
        service_coordinator: ServiceCoordinator,
        parent_layout: QVBoxLayout,
        parent=None,
    ):
        """初始化控制器设置组件"""
        super().__init__(parent)
        self.service_coordinator = service_coordinator
        self.parent_layout = parent_layout
        self.current_config: Dict[str, Any] = {}
        self._syncing = False
        self.show_hide_option = bool(cfg.get(cfg.show_advanced_startup_options))
        self.resource_setting_widgets = {}

        # 当前控制器信息变量
        self.current_controller_label = None
        self.current_controller_name = None
        self.current_controller_type = None
        self.current_controller_info = None

        # 初始化interface相关数据
        self._rebuild_interface_data()

    def _rebuild_interface_data(self):
        """重新构建基于interface的数据结构（用于多配置模式下interface更新时）"""
        # 获取最新的interface
        interface = self.service_coordinator.interface

        # 构建控制器类型映射，根据平台过滤控制器
        controllers = interface.get("controller", [])
        filtered_controllers = []
        for ctrl in controllers:
            ctrl_type = ctrl.get("type", "").lower()
            # PlayCover 控制器只在 macOS 上显示
            if ctrl_type == "playcover" and sys.platform != "darwin":
                continue
            # Win32 控制器只在 Windows 上显示
            if ctrl_type == "win32" and sys.platform != "win32":
                continue
            # Gamepad 控制器只在 Windows 上显示
            if ctrl_type == "gamepad" and sys.platform != "win32":
                continue
            # ADB 控制器在所有平台都显示（不需要过滤）
            filtered_controllers.append(ctrl)

        self.controller_type_mapping = {
            ctrl.get("label", ctrl.get("name", "")): {
                "name": ctrl.get("name", ""),
                "type": ctrl.get("type", ""),
                "icon": ctrl.get("icon", ""),
                "description": ctrl.get("description", ""),
                # 按 interface 原样保留：None/不存在表示“不需要保存/不参与判断”
                "permission_required": ctrl.get("permission_required"),
                "display_short_side": ctrl.get("display_short_side"),
                "display_long_side": ctrl.get("display_long_side"),
                "display_raw": ctrl.get("display_raw"),
                "playcover": ctrl.get("playcover", {}),  # 保存 playcover 配置
                "gamepad": ctrl.get("gamepad", {}),  # 保存 gamepad 配置
            }
            for ctrl in filtered_controllers
        }
        self.win32_method_alias_map = self._build_win32_method_alias_map()
        self.win32_default_mapping = self._build_win32_default_mapping(
            interface.get("controller", [])
        )
        self.gamepad_default_mapping = self._build_gamepad_default_mapping(
            interface.get("controller", [])
        )
        agent_interface_config = interface.get("agent", {})
        interface_custom = interface.get("custom")
        self.interface_custom_default = (
            interface_custom if isinstance(interface_custom, str) else ""
        )
        self.current_config = self.service_coordinator.option_service.current_options
        self.current_config.setdefault("gpu", -1)
        agent_timeout_default = self._coerce_int(agent_interface_config.get("timeout"))
        if agent_timeout_default is None:
            agent_timeout_default = 30
        self.agent_timeout_default = agent_timeout_default
        self.current_config.setdefault("agent_timeout", self.agent_timeout_default)
        self.current_config.setdefault("gpu", -1)
        if not isinstance(self.current_config.get("custom"), str):
            self.current_config["custom"] = self.interface_custom_default

    def create_settings(self) -> None:
        """创建固定的控制器设置UI"""
        logger.info("Creating controller settings UI...")
        # 在多配置模式下，重新构建interface相关数据以确保使用最新的interface
        self._rebuild_interface_data()

        self._syncing = True
        try:
            self._clear_options()
            self._toggle_description(False)
            self.show_hide_option = bool(cfg.get(cfg.show_advanced_startup_options))

            # 创建控制器选择下拉框
            self._create_controller_combobox()
            # 创建搜索设备下拉框
            self._create_search_option()
            # 创建GPU加速下拉框
            self._create_gpu_option()
            # 创建 agent 启动超时时间选项
            self._create_agent_timeout_option()
            # 创建自定义模块路径输入（隐藏选项）
            self._create_custom_option()
            # 创建ADB、Win32和PlayCover子选项
            self._create_adb_children_option()
            self._create_win32_children_option()
            self._create_gamepad_children_option()
            self._create_playcover_children_option()
            # 默认隐藏所有子选项
            self._toggle_win32_children_option(False)
            self._toggle_adb_children_option(False)
            self._toggle_gamepad_children_option(False)
            self._toggle_playcover_children_option(False)
            # 设置初始值为当前配置中的控制器类型
            ctrl_combo: ComboBox = self.resource_setting_widgets["ctrl_combo"]
            controller_list = list(self.controller_type_mapping)

            if not controller_list:
                # 如果没有可用的控制器，直接返回
                return

            # 尝试找到匹配的控制器类型
            matched_label = None
            matched_idx = 0

            target_controller_name = self.current_config.get("controller_type", "")
            for idx, label in enumerate(controller_list):
                if (
                    self.controller_type_mapping[label]["name"]
                    == target_controller_name
                ):
                    matched_label = label
                    matched_idx = idx
                    break

            # 如果找不到匹配的，使用第一个可用的控制器
            if matched_label is None:
                matched_label = controller_list[0]
                matched_idx = 0
                # 更新配置为第一个控制器
                self.current_config["controller_type"] = self.controller_type_mapping[
                    matched_label
                ]["name"]

            # 更新当前控制器信息变量
            self.current_controller_label = matched_label
            self.current_controller_info = self.controller_type_mapping[matched_label]
            self.current_controller_name = self.current_controller_info["name"]
            self.current_controller_type = self.current_controller_info["type"].lower()

            # 先阻塞信号，设置索引，然后强制调用刷新函数（即使索引相同也要刷新）
            ctrl_combo.blockSignals(True)
            ctrl_combo.setCurrentIndex(matched_idx)
            ctrl_combo.blockSignals(False)

            # 设置控制器描述到公告页面
            if hasattr(self, "_set_description") and self._set_description:
                description = self.current_controller_info.get("description", "")
                self._set_description(description, has_options=True)
                # 如果有描述，显示描述区域
                if (
                    description
                    and hasattr(self, "_toggle_description")
                    and self._toggle_description
                ):
                    self._toggle_description(True)

            # 强制调用刷新函数，确保界面更新（即使索引没有变化）
            self._on_controller_type_changed(matched_label)
        finally:
            self._syncing = False

        # 初始化期间不会自动保存（_syncing=True 会短路 _auto_save_options），这里延迟补偿一次
        try:
            QTimer.singleShot(
                0,
                self._persist_current_controller_meta_if_needed,
            )
        except Exception:
            # 兜底：若定时器不可用，直接尝试一次（此时 _syncing 已为 False）
            self._persist_current_controller_meta_if_needed()

    def _create_controller_combobox(self):
        """创建控制器类型下拉框"""
        ctrl_label = BodyLabel(self.tr("Controller Type"))
        admin_hint = BodyLabel(self.tr("this controller requires admin permission to run"))
        admin_hint.setStyleSheet("color: rgb(255, 0, 0); font-weight: bold;")
        admin_hint.setVisible(False)
        self.resource_setting_widgets["ctrl_admin_hint"] = admin_hint

        title_layout = QHBoxLayout()
        title_layout.addWidget(ctrl_label)
        title_layout.addWidget(admin_hint)
        title_layout.addStretch()
        self.parent_layout.addLayout(title_layout)

        ctrl_combo = ComboBox()
        self.resource_setting_widgets["ctrl_combo"] = ctrl_combo
        controller_list = list(self.controller_type_mapping)
        for label in controller_list:
            icon = ""
            if self.controller_type_mapping[label]["icon"]:
                icon = self.controller_type_mapping[label]["icon"]

            ctrl_combo.addItem(label, icon)

        self.parent_layout.addWidget(ctrl_combo)
        ctrl_combo.currentTextChanged.connect(self._on_controller_type_changed)

    def _update_admin_permission_hint(
        self, controller_info: dict[str, Any] | None
    ) -> None:
        """根据 interface.permission_required 与当前权限，更新红色提示文案"""
        hint: BodyLabel | None = self.resource_setting_widgets.get("ctrl_admin_hint")
        if hint is None:
            return

        required = bool((controller_info or {}).get("permission_required", False))
        # 通过 cfg 读取运行时管理员标记（由主窗口在启动时刷新）
        try:
            is_admin = bool(cfg.get(cfg.is_admin))
        except Exception:
            is_admin = False

        # 兜底：若主窗口尚未刷新 cfg.is_admin，尝试在此处实时检测（Windows）
        if (not is_admin) and sys.platform.startswith("win32"):
            try:
                import ctypes

                is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
                try:
                    cfg.set(cfg.is_admin, is_admin)
                except Exception:
                    pass
            except Exception:
                is_admin = False

        hint.setVisible(required and (not is_admin))

    def _create_search_option(self):
        """创建搜索设备下拉框"""
        search_label = BodyLabel(self.tr("Search Device"))
        self.parent_layout.addWidget(search_label)
        self.resource_setting_widgets["search_combo_label"] = search_label

        search_combo = DeviceFinderWidget()
        self.resource_setting_widgets["search_combo"] = search_combo

        search_combo.combo_box.addItems(list(self.controller_type_mapping))

        self.parent_layout.addWidget(search_combo)
        search_combo.combo_box.currentTextChanged.connect(self._on_search_combo_changed)

    def _create_agent_timeout_option(self):
        """创建 Agent 启动超时时间输入"""
        timeout_label = BodyLabel(self.tr("Agent Timeout"))
        timeout_edit = LineEdit()
        timeout_validator = QIntValidator(-1, 2147483647, timeout_edit)
        timeout_edit.setValidator(timeout_validator)
        timeout_edit.setPlaceholderText(self.tr("-1 means infinite"))

        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(timeout_label)
        timeout_layout.addStretch()
        timeout_layout.addWidget(timeout_edit)
        self.parent_layout.addLayout(timeout_layout)

        self.resource_setting_widgets["agent_timeout_label"] = timeout_label
        self.resource_setting_widgets["agent_timeout"] = timeout_edit
        timeout_edit.textChanged.connect(self._on_agent_timeout_changed)

        self._toggle_children_visible(["agent_timeout"], self.show_hide_option)

    def _create_custom_option(self):
        """创建自定义模块路径输入"""
        self._create_resource_line_edit(
            self.tr("Custom Module Path"),
            "custom",
            self._on_custom_path_changed,
            True,
        )
        self._toggle_children_visible(["custom"], self.show_hide_option)

    def _create_gpu_option(self):
        """创建GPU加速下拉框"""
        gpu_label = BodyLabel(self.tr("GPU Acceleration"))
        self.parent_layout.addWidget(gpu_label)

        gpu_combo = ComboBox()
        self.parent_layout.addWidget(gpu_combo)

        self.resource_setting_widgets["gpu_combo_label"] = gpu_label
        self.resource_setting_widgets["gpu_combo"] = gpu_combo

        gpu_combo.currentIndexChanged.connect(self._on_gpu_option_changed)
        self._populate_gpu_combo_options()
        self._toggle_children_visible(["gpu_combo"], self.show_hide_option)

    def _populate_gpu_combo_options(self):
        combo: ComboBox | None = self.resource_setting_widgets.get("gpu_combo")
        if combo is None:
            return

        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self.tr("Auto"), userData=-1)
        combo.addItem(self.tr("CPU"), userData=-2)

        gpu_info = gpu_cache.get_gpu_info()
        for gpu_id in sorted(gpu_info):
            gpu_name = gpu_info[gpu_id]
            combo.addItem(f"GPU {gpu_id}: {gpu_name}", userData=gpu_id)

        combo.blockSignals(False)

    def _create_resource_line_edit(
        self,
        label_text: str,
        config_key: str,
        change_callback,
        path_lineedit: bool = False,
        placeholder: str = "",
        file_filter: str | None = None,
    ):
        """创建LineEdit组件的通用方法。path_lineedit=True 时使用 PathLineEdit；file_filter 为 None 时采用控件内置跨平台默认。"""
        label = BodyLabel(label_text)
        self.parent_layout.addWidget(label)
        if path_lineedit:
            edit = PathLineEdit(file_filter=file_filter)
        else:
            edit = LineEdit()
        if placeholder:
            edit.setPlaceholderText(placeholder)
        self.parent_layout.addWidget(edit)

        # 存储控件到字典 - 注意这里的键名保持不变，以便在_toggle方法中使用
        self.resource_setting_widgets[f"{config_key}_label"] = label
        self.resource_setting_widgets[config_key] = edit

        # 连接信号
        edit.textChanged.connect(change_callback)

    def _create_resource_combobox(
        self,
        label_text: str,
        config_key: str,
        options: dict,
        change_callback,
    ):
        """创建ComboBox组件的通用方法"""
        label = BodyLabel(label_text)
        self.parent_layout.addWidget(label)

        combo = ComboBox()

        for display, value in options.items():
            # 若 display 与 value（字符串形式）一致，则只显示 display，避免出现 "Xbox360 Xbox360" 之类的冗余文本
            text = str(display) if str(display) == str(value) else f"{display} {value}"
            combo.addItem(text, userData=value)

        self.parent_layout.addWidget(combo)

        # 存储控件到字典 - 注意这里的键名保持不变，以便在_toggle方法中使用
        self.resource_setting_widgets[f"{config_key}_label"] = label
        self.resource_setting_widgets[config_key] = combo

        # 连接信号
        combo.currentIndexChanged.connect(
            lambda index: change_callback(config_key, combo.itemData(index))
        )

    def _create_adb_children_option(self):
        """创建ADB子选项"""
        # ADB路径
        self._create_resource_line_edit(
            self.tr("ADB Path"),
            "adb_path",
            lambda text: self._on_child_option_changed("adb_path", text),
            True,
        )
        # ADB连接地址
        self._create_resource_line_edit(
            self.tr("ADB Address"),
            "address",
            lambda text: self._on_child_option_changed("address", text),
        )
        # 模拟器路径
        self._create_resource_line_edit(
            self.tr("Emulator Path"),
            "emulator_path",
            lambda text: self._on_child_option_changed("emulator_path", text),
            True,
        )
        # 模拟器参数
        self._create_resource_line_edit(
            self.tr("Emulator Params"),
            "emulator_params",
            lambda text: self._on_child_option_changed("emulator_params", text),
        )
        # 等待模拟器启动时间
        self._create_resource_line_edit(
            self.tr("Wait for Emulator StartUp Time"),
            "adb_wait_time",
            lambda text: self._on_child_option_changed("adb_wait_time", text),
            placeholder="0",
        )
        # adb_wait_time: 必须为非负整数（允许 0）
        wait_time_edit = self.resource_setting_widgets.get("adb_wait_time")
        if isinstance(wait_time_edit, LineEdit):
            wait_time_edit.setValidator(QIntValidator(0, 2147483647, wait_time_edit))

        # 截图方式
        self._create_resource_combobox(
            self.tr("Screencap Method"),
            "screencap_methods",
            self.ADB_SCREENCAP_OPTIONS,
            self._on_child_option_changed,
        )

        # 输入方式
        self._create_resource_combobox(
            self.tr("Input Method"),
            "input_methods",
            self.ADB_INPUT_OPTIONS,
            self._on_child_option_changed,
        )

        # 特殊配置
        self._create_resource_line_edit(
            self.tr("Special Config"),
            "config",
            lambda text: self._on_child_option_changed("config", text),
        )

    def _create_win32_children_option(self):
        """创建Win32子选项"""
        # HWND
        self._create_resource_line_edit(
            "HWND",
            "hwnd",
            lambda text: self._on_child_option_changed("hwnd", text),
        )
        # 程序路径
        self._create_resource_line_edit(
            self.tr("Program Path"),
            "program_path",
            lambda text: self._on_child_option_changed("program_path", text),
            True,
        )
        # 程序参数
        self._create_resource_line_edit(
            self.tr("Program Params"),
            "program_params",
            lambda text: self._on_child_option_changed("program_params", text),
        )
        # 等待启动时间
        self._create_resource_line_edit(
            self.tr("Wait for Launch Time"),
            "win32_wait_time",
            lambda text: self._on_child_option_changed("win32_wait_time", text),
            placeholder="0",
        )
        # win32_wait_time: 必须为非负整数（允许 0）
        wait_time_edit = self.resource_setting_widgets.get("win32_wait_time")
        if isinstance(wait_time_edit, LineEdit):
            wait_time_edit.setValidator(QIntValidator(0, 2147483647, wait_time_edit))

        # 鼠标输入方式
        self._create_resource_combobox(
            self.tr("Mouse Input Method"),
            "mouse_input_methods",
            self.WIN32_INPUT_METHOD_ALIAS_VALUES,
            self._on_child_option_changed,
        )
        # 键盘输入方式
        self._create_resource_combobox(
            self.tr("Keyboard Input Method"),
            "keyboard_input_methods",
            self.WIN32_INPUT_METHOD_ALIAS_VALUES,
            self._on_child_option_changed,
        )

        # 截图方式
        self._create_resource_combobox(
            self.tr("Screencap Method"),
            "win32_screencap_methods",
            self.WIN32_SCREENCAP_METHOD_ALIAS_VALUES,
            self._on_child_option_changed,
        )

    def _create_gamepad_children_option(self):
        """创建Gamepad子选项（类似 Win32，但使用 gamepad_type 替代鼠标/键盘输入方式）"""
        # HWND
        self._create_resource_line_edit(
            "HWND",
            "gamepad_hwnd",
            lambda text: self._on_child_option_changed("gamepad_hwnd", text),
        )
        # 程序路径
        self._create_resource_line_edit(
            self.tr("Program Path"),
            "gamepad_program_path",
            lambda text: self._on_child_option_changed("gamepad_program_path", text),
            True,
        )
        # 程序参数
        self._create_resource_line_edit(
            self.tr("Program Params"),
            "gamepad_program_params",
            lambda text: self._on_child_option_changed("gamepad_program_params", text),
        )
        # 等待启动时间
        self._create_resource_line_edit(
            self.tr("Wait for Launch Time"),
            "gamepad_wait_time",
            lambda text: self._on_child_option_changed("gamepad_wait_time", text),
            placeholder="0",
        )
        wait_time_edit = self.resource_setting_widgets.get("gamepad_wait_time")
        if isinstance(wait_time_edit, LineEdit):
            wait_time_edit.setValidator(QIntValidator(0, 2147483647, wait_time_edit))

        # 手柄类型
        self._create_resource_combobox(
            self.tr("Gamepad Type"),
            "gamepad_type",
            self.GAMEPAD_TYPE_OPTIONS,
            self._on_child_option_changed,
        )

    def _create_playcover_children_option(self):
        """创建PlayCover子选项"""
        # Address
        self._create_resource_line_edit(
            self.tr("Address"),
            "playcover_address",
            lambda text: self._on_child_option_changed("playcover_address", text),
            placeholder="host:port",
        )

    def _on_child_option_changed(self, key: str, value: Any):
        """子选项变化处理"""
        if self._syncing:
            return
        # 确保当前控制器信息已初始化
        if not self.current_controller_name or not self.current_controller_type:
            # 从配置中重新获取控制器信息作为 fallback
            controller_name = self.current_config.get("controller_type", "")
            for key_ctrl, ctrl_info in self.controller_type_mapping.items():
                if ctrl_info["name"] == controller_name:
                    # 更新当前控制器信息变量
                    self.current_controller_label = key_ctrl
                    self.current_controller_info = ctrl_info
                    self.current_controller_name = ctrl_info["name"]
                    self.current_controller_type = ctrl_info["type"].lower()
                    break
            else:
                # 如果没有找到匹配的控制器，返回
                return

        # 使用当前控制器信息变量
        current_controller_name = self.current_controller_name
        current_controller_type = self.current_controller_type
        if current_controller_type == "adb":
            self.current_config[current_controller_name] = self.current_config.get(
                current_controller_name,
                {
                    "adb_path": "",
                    "address": "",
                    "emulator_path": "",
                    "emulator_params": "",
                    "wait_time": 30,  # 默认等待模拟器启动 30s
                    "screencap_methods": 1,
                    "input_methods": 1,
                    "config": "{}",
                },
            )

        elif current_controller_type == "win32":
            self.current_config[current_controller_name] = self.current_config.get(
                current_controller_name,
                {
                    "hwnd": "",
                    "program_path": "",
                    "program_params": "",
                    "wait_time": 30,  # 默认等待程序启动 30s
                    "mouse_input_methods": 0,
                    "keyboard_input_methods": 0,
                    "win32_screencap_methods": 0,
                },
            )
        elif current_controller_type == "gamepad":
            self.current_config[current_controller_name] = self.current_config.get(
                current_controller_name,
                {
                    "hwnd": "",
                    "program_path": "",
                    "program_params": "",
                    "wait_time": 30,  # 默认等待程序启动 30s
                    "gamepad_type": 0,
                },
            )
        elif current_controller_type == "playcover":
            # 获取默认 UUID（从 interface 配置中）
            default_uuid = "maa.playcover"
            if (
                self.current_controller_info
                and "playcover" in self.current_controller_info
            ):
                playcover_config = self.current_controller_info.get("playcover", {})
                default_uuid = playcover_config.get("uuid", "maa.playcover")

            self.current_config[current_controller_name] = self.current_config.get(
                current_controller_name,
                {
                    "uuid": default_uuid,
                    "address": "",
                },
            )
            # 确保 uuid 字段存在（如果配置中已有但为空，使用默认值）
            if "uuid" not in self.current_config[
                current_controller_name
            ] or not self.current_config[current_controller_name].get("uuid"):
                self.current_config[current_controller_name]["uuid"] = default_uuid
        # Parse JSON string back to dict for "config" key
        if key == "config":
            try:
                self.current_config[current_controller_name][key] = jsonc.loads(value)
            except (jsonc.JSONDecodeError, ValueError):
                # If parsing fails, keep the string as-is or use an empty dict
                self.current_config[current_controller_name][key] = value
        elif key in ("adb_wait_time", "win32_wait_time", "gamepad_wait_time"):
            # 必须存在一个整数（不能为负数），空值自动回填为 0
            text = "" if value is None else str(value).strip()
            if text == "":
                wait_time = 0
            else:
                try:
                    wait_time = int(text)
                except ValueError:
                    wait_time = 0
            if wait_time < 0:
                wait_time = 0

            # 同步回输入框，避免出现空字符串/非法值
            edit = self.resource_setting_widgets.get(key)
            if isinstance(edit, (LineEdit, PathLineEdit)):
                canonical = str(wait_time)
                if edit.text() != canonical:
                    self._syncing = True
                    try:
                        edit.setText(canonical)
                    finally:
                        self._syncing = False

            # 配置层仍然统一使用 wait_time 存储（避免 adb_wait_time/win32_wait_time 混入配置）
            self.current_config[current_controller_name]["wait_time"] = wait_time
        elif key == "gamepad_hwnd":
            self.current_config[current_controller_name]["hwnd"] = value
        elif key == "gamepad_program_path":
            self.current_config[current_controller_name]["program_path"] = value
        elif key == "gamepad_program_params":
            self.current_config[current_controller_name]["program_params"] = value
        elif key == "playcover_address":
            # 将 playcover_address 映射到 address
            self.current_config[current_controller_name]["address"] = value
        else:
            self.current_config[current_controller_name][key] = value

        # 如果是 playcover 类型，清理掉旧的 playcover_uuid 字段
        if current_controller_type == "playcover":
            if "playcover_uuid" in self.current_config[current_controller_name]:
                del self.current_config[current_controller_name]["playcover_uuid"]

        # 仅提交当前控制器的配置，避免无关字段触发任务列表误刷新
        self._auto_save_options(
            {current_controller_name: self.current_config[current_controller_name]}
        )

    def _normalize_config_for_json(self, config: Any) -> Any:
        """递归规范化配置数据，确保所有路径类型都被转换为字符串

        Args:
            config: 需要规范化的配置数据

        Returns:
            规范化后的配置数据
        """
        if isinstance(config, Path):
            return str(config)
        elif isinstance(config, dict):
            return {
                key: self._normalize_config_for_json(value)
                for key, value in config.items()
            }
        elif isinstance(config, list):
            return [self._normalize_config_for_json(item) for item in config]
        else:
            return config

    def _auto_save_options(self, changed_options: dict[str, Any] | None = None):
        """自动保存当前选项

        changed_options:
            - 为 None 时：保存完整配置（兼容旧逻辑）
            - 为 dict 时：仅保存和广播其中包含的字段，避免无关字段导致任务列表重载
        """
        if self._syncing:
            return
        try:
            option_service = self.service_coordinator.option_service
            options_to_save = changed_options or self.current_config
            # 规范化配置数据，确保所有路径类型都被转换为字符串
            options_to_save = self._normalize_config_for_json(options_to_save)
            ok = option_service.update_options(options_to_save)
            # 强制同步到预配置任务，确保落盘
            from app.common.constants import _CONTROLLER_

            task = option_service.task_service.get_task(_CONTROLLER_)
            if task:
                # 只保存应该保存到 Controller 任务的字段
                # Controller 任务应该包含：controller_type, gpu, agent_timeout, custom, 以及控制器特定的配置（如 adb, win32）
                controller_task_option = {}
                # 保存基础字段
                if "controller_type" in self.current_config:
                    controller_task_option["controller_type"] = self.current_config[
                        "controller_type"
                    ]
                if "gpu" in self.current_config:
                    controller_task_option["gpu"] = self.current_config["gpu"]
                if "agent_timeout" in self.current_config:
                    controller_task_option["agent_timeout"] = self.current_config[
                        "agent_timeout"
                    ]
                if "custom" in self.current_config:
                    controller_task_option["custom"] = self.current_config["custom"]
                # 保存控制器特定的配置（使用控制器名称作为键）
                # 遍历所有控制器名称，保存其配置
                for controller_info in self.controller_type_mapping.values():
                    controller_name = controller_info["name"]
                    if controller_name in self.current_config:
                        # 规范化配置数据，确保所有路径类型都被转换为字符串
                        controller_task_option[controller_name] = (
                            self._normalize_config_for_json(
                                self.current_config[controller_name]
                            )
                        )

                # 更新任务选项（只更新相关字段，保留其他字段）
                # 规范化整个 controller_task_option，确保所有路径类型都被转换为字符串
                controller_task_option = self._normalize_config_for_json(
                    controller_task_option
                )
                task.task_option.update(controller_task_option)
                # 确保不包含 speedrun_config
                if "_speedrun_config" in task.task_option:
                    del task.task_option["_speedrun_config"]
                if not option_service.task_service.update_task(task):
                    logger.warning("控制器设置强制保存失败")
            else:
                logger.warning("未找到 Controller 任务，无法保存控制器设置")

            if not ok:
                logger.warning("资源设置保存返回 False（已尝试强制保存）")
            logger.info(f"选项自动保存成功: {self.current_config}")
        except Exception as e:
            logger.error(f"自动保存选项失败: {e}")

    def _toggle_adb_children_option(self, visible: bool):
        """控制ADB子选项的隐藏和显示"""
        adb_widgets = [
            "adb_path",
            "address",
            "emulator_path",
            "emulator_params",
            "adb_wait_time",
        ]
        adb_hide_widgets = [
            "screencap_methods",
            "input_methods",
            "config",
        ]
        self._toggle_children_visible(adb_widgets, visible)
        self._toggle_children_visible(
            adb_hide_widgets, (visible and self.show_hide_option)
        )

    # 填充新的子选项信息
    def _fill_children_option(self, controller_name: str) -> None:
        """填充新的子选项信息"""
        controller_type: str | None = None
        self.current_config.setdefault("gpu", -1)
        for controller_info in self.controller_type_mapping.values():
            if controller_info["name"] == controller_name:
                controller_type = controller_info["type"].lower()
                break
        # 如果仍然没有找到对应的控制器类型，则直接返回，避免传入 None
        if controller_type is None:
            logger.warning(f"未能为控制器 {controller_name!r} 找到对应的类型配置")
            return
        # 使用控制器名称作为键，而不是控制器类型
        # 兼容旧配置：如果使用控制器名称找不到，尝试使用控制器类型
        if controller_name in self.current_config:
            controller_cfg = self.current_config[controller_name]
        elif controller_type in self.current_config:
            # 迁移旧配置：将控制器类型的配置迁移到控制器名称
            controller_cfg = self.current_config[controller_type]
            self.current_config[controller_name] = controller_cfg
            # 可选：删除旧的控制器类型键（如果需要清理旧配置）
            # del self.current_config[controller_type]
        else:
            controller_cfg = {}
            self.current_config[controller_name] = controller_cfg
        if controller_type == "adb":
            adb_defaults = {
                "adb_path": "",
                "address": "",
                "emulator_path": "",
                "emulator_params": "",
                "wait_time": 30,  # 默认等待模拟器启动 30s
                "screencap_methods": 0,
                "input_methods": 0,
                "config": "{}",
            }
            self._ensure_defaults(controller_cfg, adb_defaults)
        elif controller_type == "win32":
            win32_defaults = {
                "hwnd": "",
                "program_path": "",
                "program_params": "",
                "wait_time": 30,  # 默认等待程序启动 30s
            }
            self._ensure_defaults(controller_cfg, win32_defaults)
            self._ensure_win32_input_defaults(controller_cfg, controller_name)
        elif controller_type == "gamepad":
            gamepad_defaults = {
                "hwnd": "",
                "program_path": "",
                "program_params": "",
                "wait_time": 30,  # 默认等待程序启动 30s
                "gamepad_type": 0,
            }
            self._ensure_defaults(controller_cfg, gamepad_defaults)
            # 兼容旧配置 / interface.json: "Xbox360"/"DualShock4" -> 0/1
            resolved = self._resolve_gamepad_type_value(
                controller_cfg.get("gamepad_type")
            )
            if resolved is None:
                resolved = (
                    getattr(self, "gamepad_default_mapping", {})
                    .get(controller_name, {})
                    .get("defaults", {})
                    .get("gamepad_type", 0)
                )
            controller_cfg["gamepad_type"] = resolved
        elif controller_type == "playcover":
            # 清理掉旧的 playcover_uuid 字段（如果存在）
            if "playcover_uuid" in controller_cfg:
                del controller_cfg["playcover_uuid"]
            # 获取默认 UUID（从 interface 配置中）
            default_uuid = "maa.playcover"
            controller_info = None
            for ctrl_info in self.controller_type_mapping.values():
                if ctrl_info["name"] == controller_name:
                    controller_info = ctrl_info
                    break
            if controller_info and "playcover" in controller_info:
                playcover_config = controller_info.get("playcover", {})
                default_uuid = playcover_config.get("uuid", "maa.playcover")

            playcover_defaults = {
                "uuid": default_uuid,
                "address": "",
            }
            self._ensure_defaults(controller_cfg, playcover_defaults)
        else:
            raise
        for name, widget in self.resource_setting_widgets.items():
            if name.endswith("_label"):
                continue
            elif isinstance(widget, (LineEdit, PathLineEdit)):
                # 特殊：UI 上的 adb_wait_time/win32_wait_time 映射到配置里的 wait_time
                if name in ("adb_wait_time", "win32_wait_time", "gamepad_wait_time"):
                    value = self.current_config[controller_name].get("wait_time", 0)
                    try:
                        v = int(value)
                    except (TypeError, ValueError):
                        v = 0
                    if v < 0:
                        v = 0
                    self.current_config[controller_name]["wait_time"] = v
                    widget.setText(str(v))
                elif name == "gamepad_hwnd":
                    widget.setText(
                        str(self.current_config[controller_name].get("hwnd", ""))
                    )
                elif name == "gamepad_program_path":
                    widget.setText(
                        str(
                            self.current_config[controller_name].get("program_path", "")
                        )
                    )
                elif name == "gamepad_program_params":
                    widget.setText(
                        str(
                            self.current_config[controller_name].get(
                                "program_params", ""
                            )
                        )
                    )
                elif name in self.current_config[controller_name]:
                    value = self.current_config[controller_name][name]
                    widget.setText(
                        jsonc.dumps(value) if isinstance(value, dict) else str(value)
                    )
            elif (
                isinstance(widget, ComboBox)
                and name in self.current_config[controller_name]
            ):
                target = self.current_config[controller_name][name]
                widget.setCurrentIndex(self._value_to_index_any(widget, target))
            elif name == "playcover_address" and controller_type == "playcover":
                # 对于 playcover，将 address 的值填充到 playcover_address 输入框
                if isinstance(widget, (LineEdit, PathLineEdit)):
                    value = self.current_config[controller_name].get("address", "")
                    widget.setText(str(value))
        self._fill_custom_option()
        self._fill_gpu_option()
        self._fill_agent_timeout_option()
        # 填充设备名称
        device_name = self.current_config[controller_name].get(
            "device_name", self.tr("Unknown Device")
        )
        # 阻断下拉框信号发送
        search_option: DeviceFinderWidget = self.resource_setting_widgets[
            "search_combo"
        ]
        # 确保 no_device_found 信号连接到 InfoBar 提示槽
        # 使用更安全的方式断开信号连接，避免 RuntimeWarning
        if search_option:
            try:
                # 先尝试断开旧连接（若未连接会抛 RuntimeError，忽略即可）
                # 使用 warnings 来抑制 PySide6 的 RuntimeWarning
                import warnings

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    search_option.no_device_found.disconnect(self._on_no_device_found)
            except (RuntimeError, TypeError):
                # 如果断开失败（连接不存在或对象已销毁），忽略错误
                pass
            except Exception:
                # 捕获其他所有异常，确保不会影响后续连接
                pass

            # 连接信号
            try:
                search_option.no_device_found.connect(self._on_no_device_found)
            except (AttributeError, RuntimeError) as e:
                logger.warning(f"连接 no_device_found 信号失败: {e}")

        # 确保 search_option 有效后再访问 combo_box
        if not search_option:
            logger.warning("search_option 为 None，无法设置设备名称")
            return

        combo_box = search_option.combo_box
        combo_box.blockSignals(True)
        # 先尝试定位已有的设备项，避免重复添加
        target_index = next(
            (
                i
                for i in range(combo_box.count())
                if combo_box.itemText(i) == device_name
            ),
            -1,
        )
        if target_index == -1:
            combo_box.addItem(device_name)
            target_index = combo_box.count() - 1
        combo_box.setCurrentIndex(target_index)
        combo_box.blockSignals(False)

    def _on_no_device_found(self, controller_type: str) -> None:
        """当未找到任何设备时，通过信号总线弹出 InfoBar 提示"""
        try:
            # 检查对象是否仍然有效
            if not hasattr(self, "service_coordinator"):
                return

            # 检查 controller_type 是否为有效字符串
            if not isinstance(controller_type, str):
                controller_type = str(controller_type) if controller_type else "unknown"

            # InfoBar 信号定义: info_bar_requested = Signal(str, str)  # (level, message)
            level = "warning"
            if controller_type.lower() == "adb":
                message = self.tr(
                    "No ADB devices were found. Please check emulator or device connection."
                )
            elif controller_type.lower() in ("win32", "gamepad"):
                message = self.tr(
                    "No desktop windows were found that match the filter."
                )
            else:
                message = self.tr("No devices were found for current controller type.")

            from app.common.signal_bus import signalBus

            # 确保 signalBus 对象有效后再发送信号
            if signalBus and hasattr(signalBus, "info_bar_requested"):
                try:
                    # 使用 QTimer 延迟发送信号，确保在界面更新完成后再显示 InfoBar
                    # 这样可以避免在清理选项组件时 InfoBar 被立即关闭
                    # 延迟时间需要足够长，确保界面切换和动画完成
                    def delayed_emit():
                        try:
                            # 再次检查对象有效性（防止在延迟期间对象被销毁）
                            if signalBus and hasattr(signalBus, "info_bar_requested"):
                                signalBus.info_bar_requested.emit(level, message)
                        except Exception as e:
                            logger.warning(f"延迟发送 InfoBar 提示失败: {e}")

                    # 延迟 300ms 发送，确保界面更新和动画完成后再显示 InfoBar
                    # 如果是在清理选项时触发的信号，这个延迟可以确保界面已经稳定
                    QTimer.singleShot(300, delayed_emit)
                except (RuntimeError, AttributeError) as e:
                    # 如果对象已销毁或信号不存在，记录警告但不崩溃
                    logger.warning(f"发送 InfoBar 提示失败（对象可能已销毁）: {e}")
        except Exception as e:
            # 捕获所有其他异常，防止回调崩溃
            logger.warning(f"处理 no_device_found 信号时发生错误: {e}")

    def _fill_custom_option(self):
        custom_edit = self.resource_setting_widgets.get("custom")
        if isinstance(custom_edit, (LineEdit, PathLineEdit)):
            custom_edit.blockSignals(True)
            default_value = self.interface_custom_default
            current_value = self.current_config.get("custom", default_value)
            if current_value is None:
                current_value = default_value
            normalized_value = str(current_value) if current_value is not None else ""
            custom_edit.setText(normalized_value)
            custom_edit.blockSignals(False)
            self.current_config["custom"] = normalized_value

    def _fill_gpu_option(self):
        combo = self.resource_setting_widgets.get("gpu_combo")
        if combo is None:
            return
        self._populate_gpu_combo_options()

        combo.blockSignals(True)
        value = self.current_config.get("gpu", -1)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = -1

        target_index = 0
        for idx in range(combo.count()):
            if combo.itemData(idx) == value:
                target_index = idx
                break

        combo.setCurrentIndex(target_index)
        combo.blockSignals(False)

    def _fill_agent_timeout_option(self):
        timeout_edit = self.resource_setting_widgets.get("agent_timeout")
        if isinstance(timeout_edit, LineEdit):
            timeout_edit.blockSignals(True)
            timeout_value = self.current_config.get(
                "agent_timeout", self.agent_timeout_default
            )
            timeout_int = self._coerce_int(timeout_value)
            timeout_text = (
                str(timeout_int)
                if timeout_int is not None
                else str(self.agent_timeout_default)
            )
            timeout_edit.setText(timeout_text)
            timeout_edit.blockSignals(False)

    def _on_custom_path_changed(self, text: str):
        self.current_config["custom"] = text
        # 仅提交 custom 字段，避免无关字段导致任务列表重载
        self._auto_save_options({"custom": text})

    def _on_agent_timeout_changed(self, text: str):
        if not text:
            return
        try:
            timeout_value = int(text)
        except ValueError:
            return
        self.current_config["agent_timeout"] = timeout_value
        # 仅提交 agent_timeout 字段
        self._auto_save_options({"agent_timeout": timeout_value})

    def _on_gpu_option_changed(self, index: int):
        combo = self.resource_setting_widgets.get("gpu_combo")
        if combo is None:
            return

        value = combo.itemData(index)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = -1

        self.current_config["gpu"] = value
        # 仅提交 gpu 字段
        self._auto_save_options({"gpu": value})

    def _toggle_win32_children_option(self, visible: bool):
        """控制Win32子选项的隐藏和显示"""
        win32_widgets = [
            "hwnd",
            "program_path",
            "program_params",
            "win32_wait_time",
        ]
        win32_hide_widgets = [
            "mouse_input_methods",
            "keyboard_input_methods",
            "win32_screencap_methods",
        ]
        self._toggle_children_visible(win32_widgets, visible)
        self._toggle_children_visible(win32_hide_widgets, visible)

    def _toggle_gamepad_children_option(self, visible: bool):
        """控制Gamepad子选项的隐藏和显示"""
        gamepad_widgets = [
            "gamepad_hwnd",
            "gamepad_program_path",
            "gamepad_program_params",
            "gamepad_wait_time",
            "gamepad_type",
        ]
        self._toggle_children_visible(gamepad_widgets, visible)

    def _toggle_playcover_children_option(self, visible: bool):
        """控制PlayCover子选项的隐藏和显示"""
        playcover_widgets = [
            "playcover_address",
        ]
        self._toggle_children_visible(playcover_widgets, visible)

    def _on_controller_type_changed(self, label: str):
        """控制器类型变化时的处理函数"""
        # 更新当前控制器信息变量
        self.current_controller_label = label
        self.current_controller_info = self.controller_type_mapping[label]
        self.current_controller_name = self.current_controller_info["name"]
        self.current_controller_type = self.current_controller_info["type"].lower()

        ctrl_info = self.current_controller_info
        new_type = self.current_controller_type

        # 更新当前配置
        self.current_config["controller_type"] = ctrl_info["name"]

        # interface.json 新增字段：permission_required / display_* 写入到“当前控制器子配置”
        controller_name = ctrl_info["name"]
        meta_changed = self._sync_controller_meta_fields(
            controller_name, ctrl_info, persist=False
        )

        # interface.json 新增字段：permission_required（需要管理员权限时显示红字提示）
        self._update_admin_permission_hint(ctrl_info)

        # 如果是 playcover 类型，确保 uuid 字段存在并保存
        if new_type == "playcover":
            if controller_name not in self.current_config:
                self.current_config[controller_name] = {}
            # 获取默认 UUID（从 interface 配置中）
            default_uuid = "maa.playcover"
            if "playcover" in ctrl_info:
                playcover_config = ctrl_info.get("playcover", {})
                default_uuid = playcover_config.get("uuid", "maa.playcover")
            # 如果配置中没有 uuid 或为空，设置默认值
            if "uuid" not in self.current_config[
                controller_name
            ] or not self.current_config[controller_name].get("uuid"):
                self.current_config[controller_name]["uuid"] = default_uuid
            # 确保 address 字段存在
            if "address" not in self.current_config[controller_name]:
                self.current_config[controller_name]["address"] = ""
            # 清理掉旧的 playcover_uuid 字段（如果存在）
            if "playcover_uuid" in self.current_config[controller_name]:
                del self.current_config[controller_name]["playcover_uuid"]
            # 保存 playcover 配置（包括 uuid 和 address）
            self._auto_save_options(
                {"controller_type": ctrl_info["name"], controller_name: self.current_config[controller_name]}
            )
        else:
            # 默认只提交 controller_type；若 meta 字段有变化则连同控制器子配置一起提交，确保落盘
            payload: dict[str, Any] = {"controller_type": ctrl_info["name"]}
            if meta_changed:
                payload[controller_name] = self.current_config.get(controller_name, {})
            self._auto_save_options(payload)

        # 更换搜索设备类型（playcover 不显示搜索设备）
        search_option: DeviceFinderWidget = self.resource_setting_widgets[
            "search_combo"
        ]
        if new_type == "playcover":
            # playcover 不显示搜索设备
            search_option.setVisible(False)
            if "search_combo_label" in self.resource_setting_widgets:
                self.resource_setting_widgets["search_combo_label"].setVisible(False)
        else:
            search_option.setVisible(True)
            if "search_combo_label" in self.resource_setting_widgets:
                self.resource_setting_widgets["search_combo_label"].setVisible(True)
            # Gamepad 复用 Win32 的窗口查找逻辑，但它们仍是独立控制器（仅设备搜索复用）
            search_option.change_controller_type(
                "win32" if new_type == "gamepad" else new_type
            )
            if new_type in ("win32", "gamepad"):
                if new_type == "win32":
                    class_regex, window_regex = self._get_win32_regex_filters(
                        ctrl_info["name"]
                    )
                else:
                    class_regex, window_regex = self._get_gamepad_regex_filters(
                        ctrl_info["name"]
                    )
                search_option.set_win32_filters(class_regex, window_regex)
            else:
                search_option.set_win32_filters(None, None)

        # 填充新的信息
        self._fill_children_option(ctrl_info["name"])

        # 更换控制器描述
        ctrl_combox: ComboBox = self.resource_setting_widgets["ctrl_combo"]
        ctrl_combox.installEventFilter(ToolTipFilter(ctrl_combox))
        ctrl_combox.setToolTip(ctrl_info["description"])

        # 设置控制器描述到公告页面
        if hasattr(self, "_set_description") and self._set_description:
            description = ctrl_info.get("description", "")
            self._set_description(description, has_options=True)

        # 刷新资源下拉框（通过回调或直接调用）
        callback = getattr(self, "_on_controller_changed_callback", None)
        if callback:
            # 检查是否在初始化阶段（_syncing 为 True 表示正在初始化）
            is_initializing = self._syncing
            # 如果回调支持 is_initializing 参数，传递它；否则只传递 label
            import inspect

            if len(inspect.signature(callback).parameters) > 1:
                callback(label, is_initializing)
            else:
                callback(label)
        elif hasattr(self, "_fill_resource_option"):
            getattr(self, "_fill_resource_option")()

        # 显示/隐藏对应的子选项
        if new_type == "adb":
            self._toggle_adb_children_option(True)
            self._toggle_win32_children_option(False)
            self._toggle_gamepad_children_option(False)
            self._toggle_playcover_children_option(False)
        elif new_type == "win32":
            self._toggle_adb_children_option(False)
            self._toggle_win32_children_option(True)
            self._toggle_gamepad_children_option(False)
            self._toggle_playcover_children_option(False)
        elif new_type == "gamepad":
            self._toggle_adb_children_option(False)
            self._toggle_win32_children_option(False)
            self._toggle_gamepad_children_option(True)
            self._toggle_playcover_children_option(False)
        elif new_type == "playcover":
            self._toggle_adb_children_option(False)
            self._toggle_win32_children_option(False)
            self._toggle_gamepad_children_option(False)
            self._toggle_playcover_children_option(True)

    def _on_search_combo_changed(self, device_name):
        if self._syncing:
            return
        current_controller_name = self.current_config["controller_type"]
        # 获取控制器类型
        current_controller_type = None
        for controller_info in self.controller_type_mapping.values():
            if controller_info["name"] == current_controller_name:
                current_controller_type = controller_info["type"].lower()
                break
        if not current_controller_type:
            return
        # 使用控制器名称作为键
        current_controller_config = self.current_config.setdefault(
            current_controller_name, {}
        )
        find_device_info = self.resource_setting_widgets[
            "search_combo"
        ].device_mapping.get(device_name)
        if find_device_info is None:
            return
        for key, value in find_device_info.items():
            # 处理所有路径类型（Path 基类检查会匹配 WindowsPath 和 PosixPath）
            if isinstance(value, Path):
                value = str(value)
            current_controller_config[key] = value
        current_controller_config["device_name"] = device_name

        # 确保 emulator_path 和 emulator_params 被正确设置（并转换为字符串）
        if "emulator_path" in find_device_info:
            emulator_path = find_device_info["emulator_path"]
            if isinstance(emulator_path, Path):
                emulator_path = str(emulator_path)
            current_controller_config["emulator_path"] = emulator_path
        if "emulator_params" in find_device_info:
            emulator_params = find_device_info["emulator_params"]
            # emulator_params 通常是字符串，但为了安全也检查一下
            if isinstance(emulator_params, Path):
                emulator_params = str(emulator_params)
            current_controller_config["emulator_params"] = emulator_params

        # 打印所有设备配置
        logger.info(f"[设备配置] 设备名称: {device_name}")
        logger.info(f"[设备配置] 控制器类型: {current_controller_type}")
        logger.info(
            f"[设备配置] 完整配置: {jsonc.dumps(current_controller_config, indent=2, ensure_ascii=False)}"
        )

        # 仅提交当前控制器配置，避免无关字段导致任务列表重载
        self._auto_save_options({current_controller_name: current_controller_config})
        self._fill_children_option(current_controller_name)

    def _toggle_children_visible(self, option_list: list, visible: bool):
        for widget_name in option_list:
            # 显示/隐藏标签和控件
            for suffix in ["_label", ""]:
                full_name = f"{widget_name}{suffix}"
                if full_name in self.resource_setting_widgets:
                    self.resource_setting_widgets[full_name].setVisible(visible)

    def _clear_options(self):
        """清空选项区域"""
        # 在清理之前，先断开所有信号连接，避免在清理过程中触发信号导致 InfoBar 显示问题
        if "search_combo" in self.resource_setting_widgets:
            search_option = self.resource_setting_widgets.get("search_combo")
            if search_option and hasattr(search_option, "no_device_found"):
                try:
                    import warnings

                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        search_option.no_device_found.disconnect(
                            self._on_no_device_found
                        )
                except (RuntimeError, TypeError):
                    pass
                except Exception:
                    pass

        # 从布局中移除所有控件
        widgets_to_remove = list(self.resource_setting_widgets.values())

        # 遍历布局，找到并移除这些控件
        if hasattr(self, "parent_layout"):
            items_to_remove = []
            for i in range(self.parent_layout.count()):
                item = self.parent_layout.itemAt(i)
                if item and item.widget() and item.widget() in widgets_to_remove:
                    items_to_remove.append(i)

            # 从后往前移除，避免索引问题
            for i in reversed(items_to_remove):
                item = self.parent_layout.takeAt(i)
                if item and item.widget():
                    widget = item.widget()
                    if widget:
                        widget.hide()
                        widget.setParent(None)
                        widget.deleteLater()

        # 清理字典
        self.resource_setting_widgets.clear()
        self.current_controller_type = None
