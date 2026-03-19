"""
Interface 管理器
用于加载和管理 interface 配置文件，并提供国际化支持
"""

import jsonc
from pathlib import Path
from typing import Dict, Any, Optional, Sequence
from copy import deepcopy

from app.common.config import cfg
from app.utils.logger import logger
from app.core.service.i18n_service import I18nService
from app.utils.custom_builder import build_custom_bundle


class InterfaceManager:
    """Interface 管理器（单例模式）"""

    _instance = None
    _translated_interface: Dict[str, Any] = {}
    _original_interface: Dict[str, Any] = {}
    _current_language: str = "zh_cn"
    _interface_path: Optional[Path] = None
    _interface_dir: Path = Path.cwd()
    _file_text_fields = {"contact", "license", "welcome", "description"}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self._initialized = False

    # 内部工具: 重置状态, 保留当前语言设置
    def _reset_state(self):
        self._initialized = False
        self._original_interface = {}
        self._translated_interface = {}
        self._interface_path = None
        self._interface_dir = Path.cwd()
        # i18n 服务实例随 interface 生命周期重建
        self._i18n_service = I18nService(language=self._current_language)

    def _normalize_interface_path(
        self, interface_path: Optional[Path | str]
    ) -> Optional[Path]:
        """
        解析 interface 路径:
        - 传入路径优先
        - 其次使用已存在的路径
        - 否则按默认规则搜索项目根目录
        """
        if interface_path:
            return Path(interface_path)
        if self._interface_path:
            return self._interface_path

        interface_path_jsonc = Path.cwd() / "interface.jsonc"
        logger.debug(f"尝试加载: {interface_path_jsonc}")
        if interface_path_jsonc.exists():
            return interface_path_jsonc

        interface_path_json = Path.cwd() / "interface.json"
        logger.debug(f"尝试加载: {interface_path_json}")
        return interface_path_json

    def _detect_language_from_config(self) -> str:
        """根据全局配置推断语言代码"""
        language_map = {
            "Chinese (China)": "zh_cn",
            "Chinese (Hong Kong)": "zh_hk",
            "English": "en_us",
        }
        qt_locale = cfg.get(cfg.language)
        locale_name = (
            qt_locale.value.name() if hasattr(qt_locale, "value") else "Chinese (China)"
        )
        return language_map.get(locale_name, "zh_cn")

    def initialize(
        self,
        interface_path: Optional[Path | str] = None,
        language: Optional[str] = None,
    ):
        """
        初始化 Interface 管理器

        Args:
            interface_path: interface 配置文件路径，默认为项目根目录下的 interface.jsonc 或 interface.json
            language: 语言代码（如 "zh_cn", "en_us", "zh_hk"），默认从配置读取
        """
        desired_path = self._normalize_interface_path(interface_path)
        if language is not None:
            desired_language = language
        elif not self._initialized and self._current_language == "zh_cn":
            # 首次初始化时根据配置自动探测语言
            desired_language = self._detect_language_from_config()
        else:
            # 已有语言设置则沿用
            desired_language = self._current_language

        # 如果已初始化且路径/语言未变，直接返回；否则重置并重新初始化
        if (
            self._initialized
            and desired_path == self._interface_path
            and desired_language == self._current_language
        ):
            return
        if self._initialized:
            self._reset_state()

        self._interface_path = desired_path
        self._interface_dir = desired_path.parent if desired_path else Path.cwd()
        self._current_language = desired_language
        # 更新 i18n 服务语言
        self._i18n_service = I18nService(language=self._current_language)

        # 加载原始 interface 配置
        if self._interface_path is None:
            logger.error("未指定 interface 配置文件路径")
            self._original_interface = {}
            return

        try:
            with open(self._interface_path, "r", encoding="utf-8") as f:
                self._original_interface = jsonc.load(f)
            logger.debug(f"加载配置文件: {self._interface_path}")
        except FileNotFoundError:
            logger.error(f"未找到配置文件: {self._interface_path}")
            self._original_interface = {}
            return
        except jsonc.JSONDecodeError as e:
            logger.error(f"配置文件格式错误: {e}")
            self._original_interface = {}
            return

        # 解析 import 字段：加载并合并其他 PI 文件中的 task 和 option
        self._resolve_imports()

        # 检查 agent 嵌入式配置并尝试转换
        self.apply_agent_customization()

        # 设置当前语言
        # 如果未显式传入语言且当前语言为默认值，使用配置推断（兼容旧逻辑）
        if language is None and self._current_language == "zh_cn":
            self._current_language = self._detect_language_from_config()

        # 通过 i18n 服务加载翻译文件并翻译 interface
        self._load_translations()
        self._translate_interface()

        self._initialized = True

    def _load_translations(self):
        """加载翻译文件到 i18n 服务"""
        if not self._original_interface:
            return
        # 委托给 I18nService，根据当前语言从 interface 中加载翻译
        self._i18n_service.load_translations_from_interface(
            self._original_interface, self._interface_dir
        )

    def _translate_interface(self):
        """翻译整个 interface 配置"""
        if not self._original_interface:
            logger.warning("原始 interface 配置为空，无法翻译")
            self._translated_interface = {}
            return

        # 深拷贝原始数据
        self._translated_interface = deepcopy(self._original_interface)

        # 翻译顶层字段
        self._translate_dict(self._translated_interface)

        # 尝试将可能指向文本文件的字段展开
        self._resolve_text_fields_from_files(self._translated_interface)

        logger.debug(f"interface 配置翻译完成，当前语言: {self._current_language}")

        # 自动补全label字段：如果label不存在或为空，用name填充
        self._auto_fill_label(self._translated_interface)

    def _translate_dict(self, data: Any) -> Any:
        """
        递归翻译字典中的所有值

        Args:
            data: 要翻译的数据（可以是 dict, list, str 等）

        Returns:
            翻译后的数据
        """
        if isinstance(data, dict):
            # 递归翻译字典中的每个值
            for key, value in data.items():
                # 特殊处理 label, icon, description, title, welcome, contact 等需要翻译的字段
                if (
                    key
                    in (
                        "label",
                        "icon",
                        "description",
                        "license",
                        "title",
                        "welcome",
                        "contact",
                    )
                    and isinstance(value, str)
                ):
                    data[key] = self._i18n_service.translate_text(value)
                else:
                    data[key] = self._translate_dict(value)

        elif isinstance(data, list):
            # 递归翻译列表中的每个元素
            for i, item in enumerate(data):
                data[i] = self._translate_dict(item)

        elif isinstance(data, str):
            # 直接翻译字符串（如果以 $ 开头）
            return self._i18n_service.translate_text(data)

        return data

    def _resolve_text_fields_from_files(self, data: Any):
        """
        递归检查指定字段，如果对应值指向存在的文本文件则读取其内容
        """
        if isinstance(data, dict):
            for key, value in data.items():
                if key in self._file_text_fields and isinstance(value, str):
                    data[key] = self._try_load_text_from_path(value)
                else:
                    self._resolve_text_fields_from_files(value)

        elif isinstance(data, list):
            for item in data:
                self._resolve_text_fields_from_files(item)

    def _try_load_text_from_path(self, value: str) -> str:
        """
        尝试将字符串当作文件路径读取文本内容，读取失败则返回原始字符串
        """
        if not value:
            return value

        candidate = value.strip()
        if not candidate:
            return value

        target_path = Path(candidate)
        if not target_path.is_absolute():
            target_path = self._interface_dir / target_path

        if not target_path.exists() or not target_path.is_file():
            return value

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return value

    def _deep_merge_option(self, base: Dict[str, Any], override: Dict[str, Any]) -> None:
        """
        将 override 深度合并到 base（仅合并 option 结构：同键为 dict 则递归，否则 override 覆盖）。
        """
        for key, ov_val in override.items():
            if key not in base:
                base[key] = deepcopy(ov_val)
            elif isinstance(base[key], dict) and isinstance(ov_val, dict):
                self._deep_merge_option(base[key], ov_val)
            else:
                base[key] = deepcopy(ov_val)

    def _resolve_imports(self) -> None:
        """
        解析 interface 的 import 字段：
        按顺序加载每个路径对应的 PI 文件，仅合并其中的 task 和 option 到当前配置。
        路径为相对于 interface 同目录的相对路径。
        """
        import_paths = self._original_interface.get("import")
        if not isinstance(import_paths, list):
            return
        to_remove = []
        for i, item in enumerate(import_paths):
            if not isinstance(item, str):
                to_remove.append(i)
        for i in reversed(to_remove):
            import_paths.pop(i)
        if not import_paths:
            return

        for rel_path in import_paths:
            path_str = (rel_path or "").strip()
            if not path_str:
                continue
            target = Path(path_str)
            if not target.is_absolute():
                target = (self._interface_dir / target).resolve()
            if not target.exists() or not target.is_file():
                logger.warning(f"import 文件不存在或不是文件，已跳过: {target}")
                continue
            try:
                with open(target, "r", encoding="utf-8") as f:
                    imported = jsonc.load(f)
            except (OSError, UnicodeDecodeError, jsonc.JSONDecodeError) as e:
                logger.warning(f"加载 import 文件失败 {target}: {e}")
                continue

            # 仅合并 task、option 和 preset
            if isinstance(imported.get("task"), list):
                base_tasks = self._original_interface.setdefault("task", [])
                if not isinstance(base_tasks, list):
                    base_tasks = []
                    self._original_interface["task"] = base_tasks
                base_tasks.extend(imported["task"])
            if isinstance(imported.get("option"), dict):
                base_option = self._original_interface.setdefault("option", {})
                if not isinstance(base_option, dict):
                    base_option = {}
                    self._original_interface["option"] = base_option
                self._deep_merge_option(base_option, imported["option"])
            if isinstance(imported.get("preset"), list):
                base_preset = self._original_interface.setdefault("preset", [])
                if not isinstance(base_preset, list):
                    base_preset = []
                    self._original_interface["preset"] = base_preset
                base_preset.extend(imported["preset"])

        # 合并完成后移除 import 字段，避免下游误用
        self._original_interface.pop("import", None)

    def _auto_fill_label(self, data: Any):
        """
        递归自动补全label字段：如果label不存在或为空，用name字段的值填充

        Args:
            data: 要处理的数据
        """
        if isinstance(data, dict):
            # 如果有name字段但没有label字段，用name填充
            if "name" in data and ("label" not in data or not data["label"]):
                data["label"] = data["name"]

            # 递归处理字典中的每个值
            for key, value in data.items():
                self._auto_fill_label(value)

        elif isinstance(data, list):
            # 递归处理列表中的每个元素
            for i, item in enumerate(data):
                self._auto_fill_label(item)

    def get_interface(self) -> Dict[str, Any]:
        """
        获取翻译后的 interface 配置

        Returns:
            翻译后的 interface 字典
        """
        if not self._initialized:
            self.initialize()

        return self._translated_interface

    def get_original_interface(self) -> Dict[str, Any]:
        """
        获取原始的 interface 配置

        Returns:
            原始 interface 字典
        """
        return self._original_interface

    def apply_agent_customization(self):
        """
        若 interface 中 agent 存在且设置了 embedded，则复制入口目录并生成 custom。

        该方法对外公开，供每次更新后调用（无需再检测是否更新）。
        """
        self._handle_embedded_agent()

    def _handle_embedded_agent(self):
        agent_info = self._original_interface.get("agent")
        if not agent_info:
            return
        if not agent_info.get("embedded"):
            logger.debug("agent 配置中没有 embedded 字段，跳过嵌入式转换")
            return
        logger.debug("处理嵌入式 agent")

        child_args = agent_info.get("child_args", [])
        entry_path = self._resolve_agent_entry(child_args)
        if entry_path is None:
            logger.warning("找不到 agent.child_args 指向的启动脚本，跳过嵌入式转换")
            return

        project_name = str(self._original_interface.get("name") or self._interface_dir.name).strip() or "custom"
        custom_dir = self._interface_dir / f"{project_name}_custom"

        try:
            build_custom_bundle(entry_path, custom_dir)
        except Exception as exc:
            logger.exception("转换嵌入式 agent 失败: %s", exc)
            return

        self._original_interface["_agent_backup"] = deepcopy(agent_info)
        self._original_interface.pop("agent", None)

        # 计算相对于 bundle 目录（_interface_dir）的相对路径
        custom_relative = custom_dir.relative_to(self._interface_dir) / "custom.json"
        self._original_interface["custom"] = custom_relative.as_posix()

        # 将修改后的 interface 保存回文件（保存到正确的 bundlepath 位置）
        if self._interface_path:
            try:
                with open(self._interface_path, "w", encoding="utf-8") as f:
                    jsonc.dump(self._original_interface, f, indent=4, ensure_ascii=False)
                logger.debug(f"已将 custom 字段保存到: {self._interface_path}")
            except Exception as exc:
                logger.exception(f"保存 interface 文件失败: {exc}")

    def _resolve_agent_entry(self, child_args: Sequence[Any]) -> Path | None:
        """
        解析 agent.child_args 的入口脚本路径（优先前两个参数）。
        """
        for idx in range(min(2, len(child_args))):
            arg = child_args[idx]
            if not isinstance(arg, str):
                continue
            candidate = arg.strip()
            if not candidate:
                continue
            candidate = candidate.replace("{PROJECT_DIR}", str(self._interface_dir))
            candidate_path = Path(candidate)
            if not candidate_path.is_absolute():
                candidate_path = (self._interface_dir / candidate_path).resolve()
            if candidate_path.exists():
                return candidate_path
        return None

    def preview_interface(
        self,
        interface_path: Path | str,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        加载并返回指定路径的 interface 配置，但不修改当前管理器的内部状态。

        Args:
            interface_path: 要加载的 interface 配置文件路径（json/jsonc）
            language: 语言代码（如 "zh_cn", "en_us", "zh_hk"）。如果为 None：
                - 若当前尚未初始化且语言为默认值，则按配置自动推断；
                - 否则使用当前管理器的语言设置。

        Returns:
            翻译后的 interface 字典；如加载失败则返回空字典。
        """
        path = Path(interface_path)
        if not path.exists():
            logger.error("预览 interface 失败，文件不存在: %s", path)
            return {}

        # 备份当前状态，确保外部看起来“无副作用”
        backup_initialized = self._initialized
        backup_original_interface = deepcopy(self._original_interface)
        backup_translated_interface = deepcopy(self._translated_interface)
        backup_language = self._current_language
        backup_interface_path = self._interface_path
        backup_interface_dir = self._interface_dir

        try:
            # 使用临时状态加载指定文件
            self._interface_path = path
            self._interface_dir = path.parent
            with open(path, "r", encoding="utf-8") as f:
                self._original_interface = jsonc.load(f)

            self._resolve_imports()

            # 选择语言（逻辑与 initialize 尽量保持一致）
            if language is not None:
                self._current_language = language
            elif not backup_initialized and self._current_language == "zh_cn":
                self._current_language = self._detect_language_from_config()
            else:
                # 复用当前语言
                self._current_language = backup_language

            # 为预览重新构建 i18n 服务并加载翻译
            self._i18n_service = I18nService(language=self._current_language)
            self._load_translations()
            self._translate_interface()

            # 返回深拷贝，防止外部修改内部缓存
            return deepcopy(self._translated_interface)
        except Exception as exc:
            logger.error("预览 interface 失败: %s", exc)
            return {}
        finally:
            # 恢复原有状态
            self._initialized = backup_initialized
            self._original_interface = backup_original_interface
            self._translated_interface = backup_translated_interface
            self._current_language = backup_language
            self._interface_path = backup_interface_path
            self._interface_dir = backup_interface_dir

    def get_language(self) -> str:
        """
        获取当前语言代码

        Returns:
            当前语言代码，如 "zh_cn", "en_us"
        """
        return self._current_language

    def set_language(self, language: str):
        """
        设置当前语言

        Args:
            language: 语言代码，如 "zh_cn", "en_us"
        """
        if language == self._current_language:
            return

        self._current_language = language
        # 同步到 i18n 服务
        self._i18n_service.language = language

        # 重新加载翻译
        self._load_translations()
        self._translate_interface()

    def refresh(self):
        """刷新翻译（当语言切换时调用）"""
        if not self._original_interface:
            logger.warning("原始 interface 配置为空，无法刷新翻译")
            return

        # 重新翻译
        self._translate_interface()
        logger.info(f"interface 配置翻译已刷新，当前语言: {self._current_language}")

    def reload(
        self,
        interface_path: Optional[Path | str] = None,
        language: Optional[str] = None,
    ):
        """重新加载 interface 配置文件（热更新或路径/语言变更后调用）"""
        logger.info("重新加载 interface 配置文件...")
        desired_path = self._normalize_interface_path(interface_path)
        desired_language = language or self._current_language

        self._reset_state()
        self.initialize(interface_path=desired_path, language=desired_language)

        logger.info("interface 配置文件重新加载完成")


# 全局单例实例
_interface_manager = InterfaceManager()


def get_interface_manager(
    interface_path: Optional[Path | str] = None, language: Optional[str] = None
) -> InterfaceManager:
    """
    获取 Interface 管理器单例实例

    Args:
        interface_path: interface 配置文件路径（可为 json/jsonc）
        language: 语言代码（如 "zh_cn", "en_us", "zh_hk"），默认从配置读取

    Returns:
        InterfaceManager 实例

    Example:
        >>> interface_manager = get_interface_manager("path/to/interface.jsonc", "en_us")
        >>> interface = interface_manager.get_interface()
        >>> print(interface["task"][0]["label"])  # 已翻译的任务标签
    """
    _interface_manager.initialize(interface_path=interface_path, language=language)
    return _interface_manager


def refresh_interface_translation():
    """
    刷新 interface 翻译

    在语言切换后调用此函数，重新翻译 interface 配置

    Example:
        >>> from app.utils.interface_manager import get_interface_manager
        >>> interface_manager = get_interface_manager()
        >>> interface_manager.set_language("en_us")
        >>> refresh_interface_translation()
    """
    _interface_manager.refresh()
