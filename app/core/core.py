from pathlib import Path
from typing import List, Dict, Any
import time
import shutil

import jsonc

from PySide6.QtCore import QTimer

from app.core.Item import (
    CoreSignalBus,
    FromeServiceCoordinator,
    ConfigItem,
    TaskItem,
)
from app.core.service.Config_Service import ConfigService, JsonConfigRepository
from app.core.service.Schedule_Service import ScheduleService
from app.core.service.Task_Service import TaskService
from app.core.service.Option_Service import OptionService
from app.core.service.interface_manager import get_interface_manager, InterfaceManager
from app.core.runner.task_flow import TaskFlowRunner
from app.core.log_processor import CallbackLogProcessor
from app.utils.logger import logger
from app.common.signal_bus import signalBus


class ServiceCoordinator:
    """服务协调器，整合配置、任务和选项服务"""

    def __init__(
        self,
        main_config_path: Path,
        configs_dir: Path | None = None,
        interface_path: Path | str | None = None,
    ):
        # 初始化信号总线
        self.signal_bus = CoreSignalBus()
        self.fs_signal_bus = FromeServiceCoordinator()
        
        # 存储待显示的错误信息（用于在 UI 初始化完成后显示）
        self._pending_error_message: tuple[str, str] | None = None
        self._main_config_path = main_config_path

        # 根据传入参数或主配置中的 bundle.path 解析 interface 路径
        self._interface_path = self._resolve_interface_path(
            main_config_path, interface_path
        )

        # 确定配置目录
        if configs_dir is None:
            configs_dir = main_config_path.parent / "configs"

        # 先基于解析出的 interface 初始化管理器与数据，再交给仓库与服务使用
        self.interface_manager: InterfaceManager = get_interface_manager(
            interface_path=self._interface_path
        )
        self._interface: Dict = self.interface_manager.get_interface()

        # 初始化存储库和服务
        self.config_repo = JsonConfigRepository(
            main_config_path, configs_dir, interface=self._interface
        )
        
        # 尝试初始化 ConfigService 和 TaskService，如果配置加载失败则重置配置
        try:
            self.config_service = ConfigService(self.config_repo, self.signal_bus)
            self.task_service = TaskService(
                self.config_service, self.signal_bus, self._interface
            )
        except (IndexError, ValueError, jsonc.JSONDecodeError, FileNotFoundError, Exception) as e:
            # 配置加载错误，尝试重置配置
            logger.error(f"配置加载失败: {e}")
            if self._handle_config_load_error(main_config_path, configs_dir, e):
                # 重置成功后重新初始化
                try:
                    self.config_service = ConfigService(self.config_repo, self.signal_bus)
                    self.task_service = TaskService(
                        self.config_service, self.signal_bus, self._interface
                    )
                except Exception as retry_error:
                    logger.error(f"重置配置后重新初始化失败: {retry_error}")
                    raise
            else:
                # 重置失败，抛出原始错误
                raise
        
        self.option_service = OptionService(self.task_service, self.signal_bus)
        self.config_service.register_on_change(self._on_config_changed)

        # 运行器

        self.task_runner = TaskFlowRunner(
            task_service=self.task_service,
            config_service=self.config_service,
            fs_signal_bus=self.fs_signal_bus,
        )
        schedule_store = main_config_path.parent / "schedules.json"
        self.schedule_service = ScheduleService(self, schedule_store)

        # 初始化日志处理器（将 callback 信号转换为 log_output 信号）
        self.log_processor = CallbackLogProcessor()

        # 连接信号
        self._connect_signals()

        # 在主要内容初始化完毕后，清理无效的 bundle 索引（不删除配置）
        self._cleanup_invalid_bundles()

    def _resolve_interface_path(
        self, main_config_path: Path, interface_path: Path | str | None
    ) -> Path | None:
        """根据传入路径或“当前激活配置”的 bundle.path 决定 interface 配置文件位置。

        优先级：
        1. 调用方显式传入的 interface_path
        2. multi_config.json 中 curr_config_id 对应配置文件里的 bundle.path
        3. multi_config.json 中第一个 bundle 的 path 下的 interface.jsonc/interface.json（兼容旧逻辑）
        4. 返回 None，交由 InterfaceManager 自行在 CWD 下探测
        """
        # 1. 显式传入时直接使用
        if interface_path:
            return Path(interface_path)

        # 2. 从主配置和“当前激活配置”解析 bundle.path
        try:
            if not main_config_path.exists():
                return None

            with open(main_config_path, "r", encoding="utf-8") as f:
                main_cfg: Dict[str, Any] = jsonc.load(f)

            # 2.1 优先使用当前激活配置（curr_config_id）对应配置里的 bundle
            curr_config_id = main_cfg.get("curr_config_id")
            if curr_config_id:
                configs_dir = main_config_path.parent / "configs"
                curr_config_path = configs_dir / f"{curr_config_id}.json"
                if curr_config_path.exists():
                    try:
                        with open(curr_config_path, "r", encoding="utf-8") as cf:
                            curr_cfg: Dict[str, Any] = jsonc.load(cf)
                        raw_bundle = curr_cfg.get("bundle")
                        bundle_name = self._normalize_bundle_name(raw_bundle)
                        candidate = self._resolve_interface_path_from_bundle(
                            bundle_name
                        )
                        if candidate:
                            logger.info(
                                f"从当前激活配置 {curr_config_id} 解析到 interface 路径: {candidate}"
                            )
                            return candidate
                    except Exception as e:
                        logger.warning(
                            f"从当前配置 {curr_config_id} 解析 interface 路径失败: {e}"
                        )

            # 2.2 兜底：使用 multi_config.json 里的第一个 bundle（兼容旧行为）
            bundle = main_cfg.get("bundle")
            if isinstance(bundle, dict) and bundle:
                first_bundle_name = next(iter(bundle.keys()))
                bundle_info = bundle.get(first_bundle_name, {})
                bundle_path_str = bundle_info.get("path")
                if bundle_path_str:
                    base_dir = Path(bundle_path_str)
                    if not base_dir.is_absolute():
                        base_dir = Path.cwd() / base_dir

                    # 优先使用 interface.jsonc，其次 interface.json
                    candidate = base_dir / "interface.jsonc"
                    if not candidate.exists():
                        candidate = base_dir / "interface.json"

                    if not candidate.exists():
                        logger.warning(
                            f"在 bundle 路径 {base_dir} 下未找到 interface.jsonc/interface.json"
                        )
                        return None

                    logger.info(f"从主配置解析到 interface 路径: {candidate}")
                    return candidate

            return None
        except Exception as e:
            logger.warning(f"从主配置解析 interface 路径失败: {e}")
            return None

    def _normalize_bundle_name(self, raw_bundle: Any) -> str | None:
        """从配置中的 bundle 字段推断 bundle 名称。

        兼容多种旧格式：
        - 新格式："MPA"
        - 旧格式1：{"MPA": {"name": "MPA", "path": "..."}}
        - 旧格式2：{"path": "..."} 或 {"name": "MPA", "path": "..."}
        """
        if isinstance(raw_bundle, str):
            return raw_bundle or None
        if isinstance(raw_bundle, dict):
            if not raw_bundle:
                return None
            first_key = next(iter(raw_bundle.keys()))
            first_val = raw_bundle[first_key]
            if isinstance(first_val, dict) and "path" in first_val:
                return first_key
            return str(raw_bundle.get("name") or first_key)
        return None

    def _resolve_interface_path_from_bundle(
        self, bundle_name: str | None
    ) -> Path | None:
        """根据 bundle 名称解析 interface 路径。"""
        if not bundle_name:
            return None

        try:
            bundle_info = None
            if hasattr(self, "config_service") and self.config_service:
                bundle_info = self.config_service.get_bundle(bundle_name)
            else:
                bundle_path_str = self._get_bundle_path_from_main_config(
                    bundle_name
                )
                if not bundle_path_str:
                    logger.warning(
                        f"未在主配置中找到 bundle: {bundle_name}"
                    )
                    return None
                bundle_info = {"path": bundle_path_str}
        except FileNotFoundError:
            logger.warning(f"未在主配置中找到 bundle: {bundle_name}")
            return None

        bundle_path_str = str(bundle_info.get("path", ""))
        if not bundle_path_str:
            return None

        base_dir = Path(bundle_path_str)
        if not base_dir.is_absolute():
            base_dir = Path.cwd() / base_dir

        # 优先使用 interface.jsonc，其次 interface.json
        candidate = base_dir / "interface.jsonc"
        if not candidate.exists():
            candidate = base_dir / "interface.json"

        if not candidate.exists():
            logger.warning(
                f"在 bundle 路径 {base_dir} 下未找到 interface.jsonc/interface.json"
            )
            return None

        return candidate

    def _get_bundle_path_from_main_config(self, bundle_name: str) -> str | None:
        """在 config_service 可用前，从 multi_config.json 中查找 bundle 的 path。"""
        try:
            main_config_path = self._main_config_path
        except AttributeError as exc:
            logger.error("ServiceCoordinator 缺少 _main_config_path，无法从主配置解析 bundle 路径")
            raise
        if not main_config_path or not main_config_path.exists():
            return None

        try:
            with open(main_config_path, "r", encoding="utf-8") as mf:
                main_cfg: Dict[str, Any] = jsonc.load(mf)
        except Exception:
            return None

        bundle_dict = main_cfg.get("bundle") or {}
        if not isinstance(bundle_dict, dict):
            return None

        bundle_info = bundle_dict.get(bundle_name)
        if isinstance(bundle_info, dict):
            return bundle_info.get("path")

        return None

    def _handle_config_load_error(
        self, main_config_path: Path, configs_dir: Path, error: Exception
    ) -> bool:
        """处理配置加载错误：备份损坏的配置文件并用默认配置覆盖
        
        Args:
            main_config_path: 主配置文件路径
            configs_dir: 配置目录路径
            error: 发生的错误
            
        Returns:
            bool: 是否成功重置配置
        """
        try:
            logger.warning(f"检测到配置加载错误，开始重置配置: {error}")
            
            # 获取当前配置ID和bundle信息（如果存在）
            current_config_id = None
            bundle_name = None
            config_name = "Default Config"
            
            # 优先尝试从 config_service 获取当前配置ID（如果已初始化）
            try:
                if hasattr(self, 'config_service') and self.config_service:
                    current_config_id = self.config_service.current_config_id
            except Exception:
                pass
            
            # 如果无法从 config_service 获取，尝试从主配置文件中读取
            if not current_config_id:
                try:
                    if main_config_path.exists():
                        with open(main_config_path, "r", encoding="utf-8") as f:
                            main_config_data = jsonc.load(f)
                            current_config_id = main_config_data.get("curr_config_id")
                            
                            # 尝试获取bundle信息
                            if not bundle_name:
                                bundle_dict = main_config_data.get("bundle", {}) or {}
                                if bundle_dict:
                                    bundle_name = next(iter(bundle_dict.keys()), None)
                except Exception:
                    pass
            
            # 尝试从损坏的配置文件中读取名称和bundle（如果可能）
            if current_config_id:
                config_file = configs_dir / f"{current_config_id}.json"
                if config_file.exists():
                    try:
                        with open(config_file, "r", encoding="utf-8") as cf:
                            # 尝试读取，即使可能失败
                            try:
                                broken_config_data = jsonc.load(cf)
                                config_name = broken_config_data.get("name", "Default Config")
                                if not bundle_name:
                                    bundle_name = broken_config_data.get("bundle")
                            except:
                                # 如果读取失败，使用默认值
                                pass
                    except:
                        pass
            
            # 如果没有获取到bundle，使用interface中的默认值
            if not bundle_name:
                bundle_name = self._interface.get("name", "Default Bundle")
            
            # 备份损坏的配置文件
            timestamp = int(time.time())
            backup_success = False
            broken_config_file = None
            
            if current_config_id:
                config_file = configs_dir / f"{current_config_id}.json"
                if config_file.exists():
                    try:
                        backup_path = config_file.with_suffix(
                            f".broken.{timestamp}.json"
                        )
                        shutil.copy2(config_file, backup_path)
                        logger.info(f"已备份损坏的子配置文件到: {backup_path}")
                        broken_config_file = config_file
                        backup_success = True
                    except Exception as e:
                        logger.error(f"备份子配置文件失败: {e}")
            
            # 如果找不到损坏的配置文件，无法修复
            if not broken_config_file or not broken_config_file.exists():
                logger.error("无法找到损坏的配置文件，无法修复")
                error_message = f"Config load failed. Unable to locate corrupted config file for recovery. Error details: {str(error)}"
                self._pending_error_message = ("error", error_message)
                return False
            
            # 确保 current_config_id 不为 None（如果为 None，从文件名中提取）
            if not current_config_id:
                # 从 broken_config_file 的文件名中提取 config_id
                config_id_from_file = broken_config_file.stem
                if config_id_from_file.startswith("c_"):
                    current_config_id = config_id_from_file
                else:
                    # 如果无法从文件名提取，生成新的 ID
                    current_config_id = ConfigItem.generate_id()
                    logger.warning(f"无法获取配置ID，生成新的ID: {current_config_id}")
            else:
                # 确保 current_config_id 是字符串类型
                current_config_id = str(current_config_id)
            
            # 确保 bundle_name 不为 None
            if not bundle_name:
                bundle_name = self._interface.get("name", "Default Bundle")
            bundle_name = str(bundle_name)
            
            # 创建默认配置项（使用相同的config_id和bundle）
            init_controller = self._interface.get("controller", [{}])[0].get("name", "")
            init_resource = self._interface.get("resource", [{}])[0].get("name", "")
            
            from app.common.constants import _RESOURCE_, _CONTROLLER_, POST_ACTION
            
            default_tasks = [
                TaskItem(
                    name="Controller",
                    item_id=_CONTROLLER_,
                    is_checked=True,
                    task_option={
                        "controller_type": init_controller,
                    },
                    is_special=False,
                ),
                TaskItem(
                    name="Resource",
                    item_id=_RESOURCE_,
                    is_checked=True,
                    task_option={
                        "resource": init_resource,
                    },
                    is_special=False,
                ),
                TaskItem(
                    name="Post-Action",
                    item_id=POST_ACTION,
                    is_checked=True,
                    task_option={},
                    is_special=False,
                ),
            ]
            
            default_config_item = ConfigItem(
                name=config_name,
                item_id=current_config_id,
                tasks=default_tasks,
                know_task=[],
                bundle=bundle_name,
            )
            
            # 将默认配置写入到损坏的配置文件中（覆盖）
            try:
                config_data = default_config_item.to_dict()
                with open(broken_config_file, "w", encoding="utf-8") as f:
                    jsonc.dump(config_data, f, indent=4, ensure_ascii=False)
                logger.info(f"已用默认配置覆盖损坏的配置文件: {broken_config_file}")
            except Exception as e:
                logger.error(f"覆盖损坏的配置文件失败: {e}")
                return False
            
            # 重新初始化配置仓库和服务
            self.config_repo = JsonConfigRepository(
                main_config_path, configs_dir, interface=self._interface
            )
            
            # 存储错误信息，等待 UI 初始化完成后显示
            # 使用英文作为基础消息，在 UI 层使用 tr 进行翻译
            if backup_success:
                error_message = f"Config load failed, automatically reset to default. Backup of corrupted config file completed. Error details: {str(error)}"
            else:
                error_message = f"Config load failed, automatically reset to default. Failed to backup corrupted config file. Error details: {str(error)}"
            self._pending_error_message = ("error", error_message)
            logger.info("Stored error message, waiting for UI initialization to complete")
            
            return True
            
        except Exception as e:
            logger.error(f"Exception occurred while handling config load error: {e}")
            error_message = f"Config load failed and error occurred while resetting config: {str(e)}"
            self._pending_error_message = ("error", error_message)
            return False

    def _cleanup_invalid_bundles(self) -> None:
        """检查 bundle.path 对应的 interface.json 是否存在，不存在则仅删除主配置中的 bundle 索引。

        注意：不会删除引用这些 bundle 的配置，交由后续逻辑按需处理。
        """
        try:
            bundle_names = self.config_service.list_bundles()
        except Exception as exc:
            logger.warning(f"读取 bundle 列表失败，跳过清理: {exc}")
            return

        if not bundle_names:
            return

        invalid_bundles: list[str] = []
        for name in bundle_names:
            iface_path = self._resolve_interface_path_from_bundle(name)
            if iface_path is None:
                logger.warning(
                    f"Bundle '{name}' 的 interface.json/interface.jsonc 未找到，将从主配置中移除索引"
                )
                invalid_bundles.append(name)

        if not invalid_bundles:
            return

        try:
            main_cfg = self.config_service._main_config
        except AttributeError as exc:
            logger.error("ConfigService 缺少 _main_config，无法清理无效 bundle 索引")
            raise
        if not isinstance(main_cfg, dict):
            logger.warning("主配置结构缺失或损坏，跳过无效 bundle 清理")
            return

        bundle_dict = main_cfg.get("bundle") or {}
        if not isinstance(bundle_dict, dict):
            bundle_dict = {}

        for name in invalid_bundles:
            if name in bundle_dict:
                logger.info(f"从主配置中移除无效 bundle 索引: {name}")
                bundle_dict.pop(name, None)

        main_cfg["bundle"] = bundle_dict
        try:
            self.config_service.save_main_config()
        except Exception as exc:
            logger.warning(f"保存主配置时出错（清理无效 bundle 索引后）: {exc}")

    def _update_interface_path_for_config(self, config_id: str):
        """根据配置的 bundle 更新 interface 路径（如果需要）。

        Args:
            config_id: 配置ID
        """
        if not config_id:
            return

        config = self.config_service.get_config(config_id)
        if not config:
            return
        try:
            bundle_name = config.bundle
        except AttributeError as exc:
            logger.error(f"配置 {config_id} 缺少 bundle 字段，无法更新 interface 路径")
            raise
        if not bundle_name:
            return

        # 根据配置的 bundle 名称解析新的 interface 路径
        new_interface_path = self._resolve_interface_path_from_bundle(bundle_name)

        # 如果路径发生变化，需要重新加载 interface
        if new_interface_path and new_interface_path != self._interface_path:
            logger.info(
                f"检测到配置 {config_id} 的 bundle 路径变化，"
                f"从 {self._interface_path} 切换到 {new_interface_path}"
            )
            self._reload_interface(new_interface_path)

    def _reload_interface(self, interface_path: Path | str | None):
        """重新加载 interface 并更新相关服务。

        Args:
            interface_path: 新的 interface 文件路径
        """
        # 更新保存的路径
        self._interface_path = interface_path

        # 重新加载 interface
        self.interface_manager.reload(interface_path=self._interface_path)
        self._interface = self.interface_manager.get_interface()

        # 更新相关服务的 interface 数据
        self.config_repo.interface = self._interface
        self.task_service.reload_interface(self._interface)

    def _connect_signals(self):
        """连接所有信号"""
        # UI请求保存配置
        self.signal_bus.need_save.connect(self._on_need_save)
        # 热更新完成后重新初始化
        signalBus.fs_reinit_requested.connect(self.reinit)

    def _on_config_changed(self, config_id: str):
        """配置变化后刷新内部服务状态"""
        if not config_id:
            return

        # 检查并更新 interface 路径（如果配置的 bundle 发生变化）
        self._update_interface_path_for_config(config_id)

        self.task_service.on_config_changed(config_id)
        self.option_service.clear_selection()

    # region 配置相关方法
    def update_bundle_path(
        self, bundle_name: str, new_path: str, bundle_display_name: str | None = None
    ) -> bool:
        """
        更新 multi_config.json 中指定 bundle 的路径。

        Args:
            bundle_name: bundle 的名称（作为字典的 key）
            new_path: 新的路径（相对路径或绝对路径）
            bundle_display_name: bundle 的显示名称（可选，如果提供会更新 name 字段）

        Returns:
            是否更新成功
        """
        main_config_path = self.config_repo.main_config_path
        if not main_config_path.exists():
            logger.error(f"主配置文件不存在: {main_config_path}")
            return False

        try:
            # 读取当前配置
            with open(main_config_path, "r", encoding="utf-8") as f:
                config_data: Dict[str, Any] = jsonc.load(f)

            # 确保 bundle 字段存在且为字典
            if "bundle" not in config_data:
                config_data["bundle"] = {}
            if not isinstance(config_data["bundle"], dict):
                config_data["bundle"] = {}

            # 更新或创建 bundle 信息
            if bundle_name not in config_data["bundle"]:
                config_data["bundle"][bundle_name] = {}

            bundle_info = config_data["bundle"][bundle_name]
            if not isinstance(bundle_info, dict):
                bundle_info = {}

            # 更新路径
            bundle_info["path"] = new_path
            # 如果提供了显示名称，也更新 name 字段
            if bundle_display_name is not None:
                bundle_info["name"] = bundle_display_name
            # 如果 name 字段不存在，使用 bundle_name 作为默认值
            elif "name" not in bundle_info:
                bundle_info["name"] = bundle_name

            config_data["bundle"][bundle_name] = bundle_info

            # 保存回文件
            with open(main_config_path, "w", encoding="utf-8") as f:
                jsonc.dump(config_data, f, indent=4, ensure_ascii=False)

            logger.info(
                f"已更新 bundle '{bundle_name}' 的路径为: {new_path} "
                f"(显示名称: {bundle_info.get('name', bundle_name)})"
            )

            # 如果当前激活配置使用的是这个 bundle，可能需要重新解析 interface 路径
            # 这里可以选择性地触发重新加载，但为了安全，先不自动触发
            # 调用方可以根据需要手动调用相关刷新方法

            return True

        except Exception as e:
            logger.error(f"更新 bundle 路径失败: {e}")
            return False

    def delete_bundle(self, bundle_name: str) -> bool:
        """从主配置中移除指定 bundle 的索引"""
        try:
            main_config = self.config_service._main_config
        except AttributeError as exc:
            logger.error("ConfigService 缺少 _main_config，无法删除 bundle")
            raise
        if not isinstance(main_config, dict):
            logger.warning("主配置缺失，无法删除 bundle")
            return False

        bundle_dict = main_config.get("bundle")
        if not isinstance(bundle_dict, dict):
            bundle_dict = {}

        if bundle_name not in bundle_dict:
            logger.info(f"Bundle '{bundle_name}' 不存在于主配置，跳过删除")
            return True

        bundle_dict.pop(bundle_name, None)
        main_config["bundle"] = bundle_dict
        success = self.config_service.save_main_config()
        if success:
            logger.info(f"已从主配置中移除 bundle: {bundle_name}")
            return True

        logger.error(f"保存主配置失败，bundle '{bundle_name}' 未被删除")
        return False

    def get_presets(self) -> List[Dict[str, Any]]:
        """获取 interface 中定义的所有预设配置列表。

        Returns:
            预设列表，每个元素是一个预设字典（含 name, label, description, icon, task 等字段）。
            如果没有预设则返回空列表。
        """
        presets = self._interface.get("preset", [])
        if not isinstance(presets, list):
            return []
        return presets

    def add_config(self, config_item: ConfigItem, preset_name: str | None = None) -> str:
        """添加配置，传入 ConfigItem 对象，返回新配置ID

        Args:
            config_item: 配置项对象
            preset_name: 可选的预设名称。如果指定，则在初始化任务后应用预设的勾选状态和选项值。
        """
        new_id = self.config_service.create_config(config_item)
        if new_id:
            # Select the new config
            self.config_service.current_config_id = new_id

            # Initialize the new config with tasks from interface
            self.task_service.init_new_config()

            # Apply preset if specified
            if preset_name:
                preset = self._find_preset(preset_name)
                if preset:
                    self.task_service.apply_preset(preset)

            # Notify UI incrementally
            self.fs_signal_bus.fs_config_added.emit(
                self.config_service.get_config(new_id)
            )
        return new_id

    def _find_preset(self, preset_name: str) -> Dict[str, Any] | None:
        """根据预设名称查找预设配置。

        Args:
            preset_name: 预设的 name 字段值

        Returns:
            匹配的预设字典，未找到返回 None
        """
        presets = self.get_presets()
        for preset in presets:
            if isinstance(preset, dict) and preset.get("name") == preset_name:
                return preset
        return None

    def delete_config(self, config_id: str) -> bool:
        """删除配置，传入 config id"""
        ok = self.config_service.delete_config(config_id)
        if ok:
            # notify UI incremental removal
            self.fs_signal_bus.fs_config_removed.emit(config_id)
        return ok

    def select_config(self, config_id: str) -> bool:
        """选择配置，传入 config id"""
        # 验证配置存在
        if not self.config_service.get_config(config_id):
            return False

        # 使用 ConfigService setter，回调将同步任务和选项
        self.config_service.current_config_id = config_id
        return self.config_service.current_config_id == config_id

    # endregion

    # region 任务相关方法

    def modify_task(self, task: TaskItem, idx: int = -2) -> bool:
        """修改或添加任务：传入 TaskItem，如果列表中没有对应 id 的任务，根据idx参数插入到指定位置，否则更新对应任务
        
        Args:
            task: 任务对象
            idx: 插入位置索引，默认为-2（倒数第二个位置）
        """
        ok = self.task_service.update_task(task, idx)
        if ok:
            self.fs_signal_bus.fs_task_modified.emit(task)
        return ok

    def update_task_checked(self, task_id: str, is_checked: bool) -> bool:
        """更新任务选中状态并处理特殊任务互斥"""
        tasks = self.task_service.get_tasks()
        target_task = None
        for t in tasks:
            if t.item_id == task_id:
                t.is_checked = is_checked
                target_task = t
                break
        else:
            return False

        unchecked_tasks = []
        if target_task.is_special and is_checked:
            for t in tasks:
                if t.item_id != task_id and t.is_special and t.is_checked:
                    t.is_checked = False
                    unchecked_tasks.append(t)

        changed_tasks = [target_task] + unchecked_tasks
        ok = self.task_service.update_tasks(changed_tasks)
        if ok:
            for task in changed_tasks:
                # UI 通知：保持与旧行为兼容
                self.signal_bus.task_updated.emit(task)
                self.fs_signal_bus.fs_task_modified.emit(task)

        return ok

    def modify_tasks(self, tasks: List[TaskItem]) -> bool:
        """批量修改/新增任务，减少多次磁盘写入。成功后发出 fs_task_updated（逐项或 tasks_loaded 已由 service 发出）。"""
        if not tasks:
            return True

        ok = self.task_service.update_tasks(tasks)
        if ok:
            # 兼容：对于希望逐项更新的监听者，仍发出逐项 task_updated 信号
            try:
                for t in tasks:
                    self.fs_signal_bus.fs_task_modified.emit(t)
            except Exception:
                pass
        return ok

    def delete_task(self, task_id: str) -> bool:
        """删除任务，传入 task id，基础任务不可删除（通过特殊 id 区分）"""
        config = self.config_service.get_current_config()
        if not config:
            return False
        # 基础任务 id 以 r_ f_ 开头（资源和完成后操作）
        base_prefix = ("r_", "f_")
        for t in config.tasks:
            if t.item_id == task_id and t.item_id.startswith(base_prefix):
                return False
        ok = self.task_service.delete_task(task_id)
        if ok:
            self.fs_signal_bus.fs_task_removed.emit(task_id)
        return ok

    def select_task(self, task_id: str) -> bool:
        """选中任务，传入 task id，并自动检查已知任务"""
        selected = self.option_service.select_task(task_id)
        self.task_service._check_know_task()
        return selected

    def reorder_tasks(self, new_order: List[str]) -> bool:
        """任务顺序更改，new_order 为 task_id 列表（新顺序）"""
        return self.task_service.reorder_tasks(new_order)

    # endregion
    def _on_need_save(self):
        """当UI请求保存时保存所有配置"""
        self.config_service.save_main_config()
        self.signal_bus.config_saved.emit(True)

    def reinit(self):
        """重新初始化服务协调器，用于热更新完成后刷新资源"""
        logger.info("开始重新初始化服务协调器...")
        try:
            # 重新加载主配置
            self.config_repo.load_main_config()

            # 刷新 interface 数据
            self.interface_manager.reload(self._interface_path)
            self._interface = self.interface_manager.get_interface()
            self.config_repo.interface = self._interface

            # 重新初始化任务服务（刷新 interface 数据）
            self.task_service.reload_interface(self._interface)

            # 通知 UI 配置已更新
            current_config_id = self.config_service.current_config_id
            if current_config_id:
                self.signal_bus.config_changed.emit(current_config_id)

            logger.info("服务协调器重新初始化完成")
        except Exception as e:
            logger.error(f"重新初始化服务协调器失败: {e}")

    async def run_tasks_flow(
        self, task_id: str | None = None
    ):
        """运行任务流的对外封装。

        :param task_id: 指定只运行某个任务（可选）
        """
        # 任务流执行前刷新一次 is_hidden，确保 runner 只需读取 is_checked/is_hidden
        try:
            self.task_service.refresh_hidden_flags()
        except Exception:
            pass
        return await self.task_runner.run_tasks_flow(task_id)

    async def stop_task_flow(self):
        """停止当前任务流（UI/外部调用，视为手动停止）。"""
        return await self.task_runner.stop_task(manual=True)

    async def stop_task(self, *, manual: bool = False):
        """停止当前任务流（供内部/调度等模块调用，可指定是否视为手动停止）。"""
        return await self.task_runner.stop_task(manual=manual)

    @property
    def run_manager(self) -> TaskFlowRunner:
        return self.task_runner

    # 提供获取服务的属性，以便UI层访问
    @property
    def interface_obj(self) -> InterfaceManager:
        return self.interface_manager

    @property
    def interface(self) -> Dict[str, Any]:
        """返回当前加载的 interface 字典"""
        return self._interface

    @property
    def config(self) -> ConfigService:
        return self.config_service

    @property
    def task(self) -> TaskService:
        return self.task_service

    @property
    def option(self) -> OptionService:
        return self.option_service

    @property
    def fs_signals(self) -> FromeServiceCoordinator:
        return self.fs_signal_bus

    @property
    def signals(self) -> CoreSignalBus:
        return self.signal_bus
    
    def get_pending_error_message(self) -> tuple[str, str] | None:
        """获取并清除待显示的错误信息
        
        Returns:
            tuple[str, str] | None: (level, message) 或 None
        """
        if self._pending_error_message:
            msg = self._pending_error_message
            self._pending_error_message = None
            return msg
        return None
