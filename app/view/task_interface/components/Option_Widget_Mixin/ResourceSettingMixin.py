from typing import Dict, Any, Callable, Protocol, Optional
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    ToolTipFilter,
)
from PySide6.QtWidgets import QVBoxLayout

from app.utils.logger import logger
from app.core.core import ServiceCoordinator
from app.view.task_interface.components.Option_Framework.OptionFormWidget import OptionFormWidget


class ResourceSettingMixin:
    """
    资源设置 Mixin - 提供资源下拉框相关功能
    使用方法：在 OptionWidget 中使用多重继承添加此 mixin
    """

    option_page_layout: QVBoxLayout
    service_coordinator: ServiceCoordinator
    current_config: Dict[str, Any]
    tr: Callable[..., str]  # Qt翻译方法
    set_description: Callable[..., None]  # 设置描述方法（可选）
    _toggle_description: Callable[..., None]  # 切换描述显示（可选）

    def _init_resource_settings(self):
        """初始化资源设置相关属性"""
        if not hasattr(self, "resource_setting_widgets"):
            self.resource_setting_widgets: Dict[str, Any] = {}
        self._resource_syncing = False
        self.current_resource: str | None = None
        if not hasattr(self, "current_controller_label"):
            self.current_controller_label: str | None = None
        # 资源选项表单组件
        if not hasattr(self, "resource_option_form_widget"):
            self.resource_option_form_widget: Optional[OptionFormWidget] = None
        # 全局选项（interface.global_option）
        if not hasattr(self, "global_option_label"):
            self.global_option_label = None
        if not hasattr(self, "global_option_form_widget"):
            self.global_option_form_widget: Optional[OptionFormWidget] = None
        # 构建资源映射表
        self._rebuild_resource_mapping()

    def _rebuild_resource_mapping(self):
        """重新构建资源映射表（用于多配置模式下interface更新时）"""
        # 获取最新的interface
        interface = self.service_coordinator.interface

        # 获取控制器类型映射（应该由 ControllerSettingMixin 提供）
        if not hasattr(self, "controller_type_mapping") or not self.controller_type_mapping:
            # 如果没有控制器映射，创建一个临时的
            self.controller_type_mapping = {
                ctrl.get("label", ctrl.get("name", "")): {
                    "name": ctrl.get("name", ""),
                    "type": ctrl.get("type", ""),
                    "icon": ctrl.get("icon", ""),
                    "description": ctrl.get("description", ""),
                }
                for ctrl in interface.get("controller", [])
            }

        # 构建资源映射表
        # 使用 label（如果存在）或 name 作为键，确保与 controller_type_mapping 的键一致
        self.resource_mapping = {}
        # 使用 controller_type_mapping 的键来构建资源映射表，确保键的一致性
        for label in self.controller_type_mapping.keys():
            self.resource_mapping[label] = []
        
        # 遍历每个资源，确定它支持哪些控制器
        for resource in interface.get("resource", []):
            supported_controllers = resource.get("controller")
            if not supported_controllers:
                # 未指定支持的控制器则默认对所有控制器生效
                for key in self.resource_mapping:
                    self.resource_mapping[key].append(resource)
                continue

            # 资源中的 controller 字段存储的是控制器的 name（不是 type）
            # 例如：["安卓端", "桌面端"]
            for controller in interface.get("controller", []):
                controller_name = controller.get("name", "")
                # 检查控制器的 name 是否在资源支持的控制器列表中
                if controller_name in supported_controllers:
                    label = controller.get("label", controller.get("name", ""))
                    if label in self.resource_mapping:
                        self.resource_mapping[label].append(resource)
                    else:
                        logger.warning(
                            f"控制器标签 {label} 不在资源映射表中，无法添加资源 {resource.get('name', '')}"
                        )

    def create_resource_settings(self) -> None:
        """创建固定的资源设置UI"""
        # 在多配置模式下，重新构建资源映射表以确保使用最新的interface
        self._rebuild_resource_mapping()

        # 创建资源选择下拉框
        self._create_resource_option()

        # 填充资源选项
        self._fill_resource_option()
        
        # 根据当前资源渲染资源选项（如果有）
        self._update_resource_options()
        # 渲染全局选项（如果有 interface.global_option）
        self._update_global_options()

    def _create_resource_option(self):
        """创建资源选择下拉框"""
        resource_label = BodyLabel(self.tr("Resource"))
        self.option_page_layout.addWidget(resource_label)

        resource_combox = ComboBox()
        self.option_page_layout.addWidget(resource_combox)
        # 存储 label 和 combo，确保可以被正确控制显示/隐藏
        self.resource_setting_widgets["resource_combo_label"] = resource_label
        self.resource_setting_widgets["resource_combo"] = resource_combox
        resource_combox.currentTextChanged.connect(self._on_resource_combox_changed)

    def _on_resource_combox_changed(self, new_resource):
        """资源变化时的处理函数（只有用户主动更改时才触发）"""
        if self._resource_syncing:
            return
        
        # 如果新资源与当前资源相同，不处理（避免重复触发）
        if self.current_resource == new_resource:
            return
        
        # 更新当前资源信息变量
        self.current_resource = new_resource

        # 确保 current_controller_label 存在
        if not hasattr(self, "current_controller_label") or not self.current_controller_label:
            return

        current_controller_label = self.current_controller_label

        if current_controller_label not in self.resource_mapping:
            return

        for resource in self.resource_mapping[current_controller_label]:
            if resource.get("label", resource.get("name", "")) == self.current_resource:
                # 检查资源是否真的改变了
                old_resource = self.current_config.get("resource", "")
                new_resource_name = resource["name"]
                
                if old_resource == new_resource_name:
                    # 资源没有实际改变，不触发更新
                    return
                
                self.current_config["resource"] = new_resource_name
                res_combo: ComboBox = self.resource_setting_widgets["resource_combo"]
                if description := resource.get("description"):
                    res_combo.installEventFilter(ToolTipFilter(res_combo))
                    res_combo.setToolTip(description)
                    # 设置资源描述到公告页面
                    if hasattr(self, "set_description"):
                        self.set_description(description, has_options=True)
                else:
                    # 如果没有描述，清空公告页面
                    if hasattr(self, "set_description"):
                        self.set_description("", has_options=True)
                # 保存资源选项到Resource任务
                self._auto_save_resource_option(new_resource_name)
                # 获取当前资源的选项名称列表
                resource_option_names = resource.get("option", [])
                # 更新资源选项的 hidden 状态（根据新资源）
                self._update_resource_options_hidden_state(resource_option_names)
                # 更新资源选项（如果有）
                self._update_resource_options()
                # 资源变化时，通知任务列表更新（仅携带 resource 字段）
                self._notify_task_list_update()
                break

    def _auto_save_resource_option(self, resource_name: str, skip_sync_check: bool = False):
        """自动保存资源选项到Resource任务
        
        Args:
            resource_name: 资源名称
            skip_sync_check: 是否跳过 _syncing 检查（用于控制器类型切换时的自动保存）
        """
        if not skip_sync_check and self._resource_syncing:
            return
        try:
            from app.common.constants import _RESOURCE_
            option_service = self.service_coordinator.option_service
            # 更新当前配置
            self.current_config["resource"] = resource_name
            # 保存到Resource任务
            resource_task = option_service.task_service.get_task(_RESOURCE_)
            if resource_task:
                # 只保存 resource 字段，不保存其他字段（如 gpu, agent_timeout, custom, speedrun_config 等）
                resource_task.task_option["resource"] = resource_name
                # 确保不包含不应该保存到 Resource 任务的字段
                fields_to_remove = ["gpu", "agent_timeout", "custom", "_speedrun_config", "controller_type", "adb", "win32"]
                for field in fields_to_remove:
                    if field in resource_task.task_option:
                        del resource_task.task_option[field]
                if not option_service.task_service.update_task(resource_task):
                    logger.warning("资源选项保存失败")
            else:
                logger.warning("未找到 Resource 任务，无法保存资源选项")
            
            # 同时通过OptionService保存（用于触发信号）
            option_service.update_options({"resource": resource_name})
        except Exception as e:
            logger.error(f"自动保存资源选项失败: {e}")

    def _notify_task_list_update(self):
        """通知任务列表更新（资源变化时调用）"""
        try:
            # 通过信号总线通知任务列表更新
            if hasattr(self, "service_coordinator"):
                # 发出 option_updated 信号，任务列表可以监听此信号来更新
                # 仅携带 resource 字段，避免其他字段变化导致任务列表重载
                self.service_coordinator.signal_bus.option_updated.emit(
                    {"resource": self.current_config.get("resource")}
                )
        except Exception:
            pass

    def _fill_resource_option(self):
        """填充资源选项"""
        if "resource_combo" not in self.resource_setting_widgets:
            return

        resource_combo: ComboBox = self.resource_setting_widgets["resource_combo"]
        
        # 在填充时完全阻止信号，避免触发任务更新
        resource_combo.blockSignals(True)
        
        resource_combo.clear()

        # 确保 current_controller_label 存在
        if not hasattr(self, "current_controller_label") or not self.current_controller_label:
            logger.warning(f"current_controller_label 不存在或为空，无法填充资源选项")
            resource_combo.blockSignals(False)
            return

        current_controller_label = self.current_controller_label
        
        # 确保资源映射表已构建
        if not hasattr(self, "resource_mapping") or not self.resource_mapping:
            self._rebuild_resource_mapping()

        if current_controller_label not in self.resource_mapping:
            logger.warning(
                f"控制器标签 {current_controller_label} 不在资源映射表中。"
                f"可用的控制器标签: {list(self.resource_mapping.keys())}"
            )
            resource_combo.blockSignals(False)
            return

        # 使用当前控制器信息变量
        curren_config = self.resource_mapping[current_controller_label]
        
        for resource in curren_config:
            icon = resource.get("icon", "")
            resource_label = resource.get("label", resource.get("name", ""))
            resource_combo.addItem(resource_label, icon)

        # 根据 current_config 中的 resource 选择对应项
        target = self.current_config.get("resource", "")
        target_label = None
        for resource in curren_config:
            name = resource.get("name", "")
            label = resource.get("label", name)
            # 使用精确匹配，而不是 in 操作符，避免部分匹配问题
            if target and (target == name or target == label):
                target_label = label
                break
        
        if target_label:
            idx = resource_combo.findText(target_label)
            if idx >= 0:
                resource_combo.setCurrentIndex(idx)
                # 更新 current_resource，避免下次误判为变化
                self.current_resource = target_label
                # 确保 current_config 中的 resource 是最新的（使用 name，不是 label）
                # 同时获取资源的 description 并设置到公告页面
                for resource in curren_config:
                    if resource.get("label", resource.get("name", "")) == target_label:
                        self.current_config["resource"] = resource.get("name", "")
                        # 设置资源描述到公告页面
                        if hasattr(self, "set_description"):
                            description = resource.get("description", "")
                            self.set_description(description, has_options=True)
                            # 如果有描述，显示描述区域
                            if description and hasattr(self, "_toggle_description"):
                                self._toggle_description(True)
                        break
            else:
                logger.warning(f"未找到资源标签 {target_label} 在下拉框中")
        else:
            # 如果当前保存的资源不在新控制器的资源列表中，自动选择第一个资源并保存
            if target and curren_config:
                first_resource = curren_config[0]
                first_resource_name = first_resource.get("name", "")
                first_resource_label = first_resource.get("label", first_resource_name)
                
                # 设置下拉框为第一个资源
                idx = resource_combo.findText(first_resource_label)
                if idx >= 0:
                    resource_combo.setCurrentIndex(idx)
                    self.current_resource = first_resource_label
                    
                    # 更新配置并保存（跳过 _syncing 检查，因为这是控制器类型切换时的自动更新）
                    self.current_config["resource"] = first_resource_name
                    self._auto_save_resource_option(first_resource_name, skip_sync_check=True)
                    
                    # 设置资源描述到公告页面
                    if hasattr(self, "set_description"):
                        description = first_resource.get("description", "")
                        self.set_description(description, has_options=True)
                        # 如果有描述，显示描述区域
                        if description and hasattr(self, "_toggle_description"):
                            self._toggle_description(True)
                else:
                    logger.warning(f"未找到资源标签 {first_resource_label} 在下拉框中")
        
        # 恢复信号
        resource_combo.blockSignals(False)
        
        # 填充完成后，根据当前资源更新资源选项（如果有）
        if target_label or (target and curren_config):
            self._update_resource_options()
    
    def _get_current_resource_dict(self) -> Optional[Dict[str, Any]]:
        """获取当前资源的配置字典"""
        if not hasattr(self, "current_controller_label") or not self.current_controller_label:
            return None
        
        current_controller_label = self.current_controller_label
        
        if not hasattr(self, "resource_mapping") or current_controller_label not in self.resource_mapping:
            return None
        
        current_resource_name = self.current_config.get("resource", "")
        if not current_resource_name:
            return None
        
        for resource in self.resource_mapping[current_controller_label]:
            if resource.get("name", "") == current_resource_name:
                return resource
        
        return None
    
    def _get_current_controller_name(self) -> str:
        """获取当前选中的控制器 name（用于按 controller 字段过滤选项）"""
        from app.common.constants import _CONTROLLER_

        try:
            controller_task = self.service_coordinator.task.get_task(_CONTROLLER_)
            if controller_task and isinstance(controller_task.task_option, dict):
                return controller_task.task_option.get("controller_type", "") or ""
        except Exception:
            pass
        return ""

    def _build_resource_option_form_structure(self, resource: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """构建资源选项的表单结构
        
        Args:
            resource: 资源配置字典
            
        Returns:
            表单结构字典，如果没有选项则返回 None
        """
        # 获取资源的 option 字段
        resource_option_names = resource.get("option", [])
        if not resource_option_names:
            return None
        
        interface = self.service_coordinator.interface
        all_options = interface.get("option", {})
        
        if not all_options:
            return None
        
        form_structure = {}
        option_service = self.service_coordinator.option_service
        current_controller = self._get_current_controller_name()
        
        # 遍历资源需要的每个选项
        for option_name in resource_option_names:
            if option_name in all_options:
                option_def = all_options[option_name]
                # 按 controller 字段过滤：仅当当前控制器匹配时才显示
                if not option_service._is_option_visible_for_controller(
                    option_def, current_controller
                ):
                    continue
                # 使用 process_option_def 方法递归处理选项定义
                field_config = option_service.process_option_def(
                    option_def, all_options, option_name
                )
                form_structure[option_name] = field_config
        
        return form_structure if form_structure else None

    def _build_global_option_form_structure(self) -> Optional[Dict[str, Any]]:
        """从 interface.global_option 构建全局选项的表单结构。与 task/resource/controller 同级，无则返回 None。"""
        interface = self.service_coordinator.interface
        global_option_def = interface.get("global_option")
        if not global_option_def:
            return None
        all_options = interface.get("option", {})
        if not all_options:
            return None
        option_service = self.service_coordinator.option_service
        current_controller = self._get_current_controller_name()
        form_structure = {}
        if isinstance(global_option_def, list):
            for option_name in global_option_def:
                if option_name in all_options:
                    option_def = all_options[option_name]
                    # 按 controller 字段过滤
                    if not option_service._is_option_visible_for_controller(
                        option_def, current_controller
                    ):
                        continue
                    field_config = option_service.process_option_def(
                        option_def, all_options, option_name
                    )
                    form_structure[option_name] = field_config
        elif isinstance(global_option_def, dict):
            for option_name, option_def in global_option_def.items():
                if option_name == "description":
                    continue
                # 按 controller 字段过滤
                if isinstance(option_def, dict) and not option_service._is_option_visible_for_controller(
                    option_def, current_controller
                ):
                    continue
                field_config = option_service.process_option_def(
                    option_def, all_options, option_name
                )
                form_structure[option_name] = field_config
        return form_structure if form_structure else None

    def _update_global_options(self):
        """根据 interface.global_option 更新全局选项区域：有则显示「全局选项」标题与表单，无则不显示。"""
        form_structure = self._build_global_option_form_structure()
        if not form_structure:
            self._clear_global_options()
            return
        from app.common.constants import _RESOURCE_
        option_service = self.service_coordinator.option_service
        resource_task = option_service.task_service.get_task(_RESOURCE_)
        option_config = (resource_task.task_option.get("global_options", {}) if resource_task else {}) or {}
        if self.global_option_label is None:
            self.global_option_label = BodyLabel(self.tr("Global Option"))
            self.option_page_layout.addWidget(self.global_option_label)
        if self.global_option_form_widget is None:
            self.global_option_form_widget = OptionFormWidget()
            self.option_page_layout.addWidget(self.global_option_form_widget)
        self.global_option_form_widget.build_from_structure(form_structure, option_config)
        self._connect_global_option_signals()

    def _clear_global_options(self):
        """移除全局选项标题与表单。"""
        if self.global_option_label is not None:
            label = self.global_option_label
            self.global_option_label = None
            if label.parent():
                self.option_page_layout.removeWidget(label)
            label.deleteLater()
        if self.global_option_form_widget is not None:
            widget = self.global_option_form_widget
            self.global_option_form_widget = None
            if hasattr(widget, "option_items"):
                for option_item in widget.option_items.values():
                    try:
                        option_item.option_changed.disconnect()
                    except Exception:
                        pass
            widget._clear_options()
            if widget.parent():
                self.option_page_layout.removeWidget(widget)
            widget.deleteLater()

    def _connect_global_option_signals(self):
        """连接全局选项变化信号。"""
        if self.global_option_form_widget is None:
            return
        for option_item in self.global_option_form_widget.option_items.values():
            option_item.option_changed.connect(self._on_global_option_changed)
            self._connect_global_option_child_signals(option_item)

    def _connect_global_option_child_signals(self, option_item):
        """递归连接全局选项子项信号。"""
        for child_widget in option_item.child_options.values():
            child_widget.option_changed.connect(self._on_global_option_changed)
            self._connect_global_option_child_signals(child_widget)

    def _on_global_option_changed(self, key: str, value: Any):
        """全局选项变化时写入 Resource 任务的 task_option.global_options。"""
        from app.common.constants import _RESOURCE_
        option_service = self.service_coordinator.option_service
        resource_task = option_service.task_service.get_task(_RESOURCE_)
        if not resource_task:
            return
        if self.global_option_form_widget is None:
            return
        all_options = self.global_option_form_widget.get_options()
        if not isinstance(resource_task.task_option, dict):
            resource_task.task_option = {}
        resource_task.task_option["global_options"] = dict(all_options)
        option_service.task_service.update_task(resource_task)
        if option_service.current_task_id == _RESOURCE_:
            option_service.current_options.update(all_options)
            option_service.signal_bus.option_updated.emit(all_options)

    def _update_resource_options_hidden_state(self, current_resource_option_names: list):
        """更新资源选项的 hidden 状态（当资源切换时调用）
        
        Args:
            current_resource_option_names: 当前资源的选项名称列表
        """
        try:
            from app.common.constants import _RESOURCE_
            option_service = self.service_coordinator.option_service
            resource_task = option_service.task_service.get_task(_RESOURCE_)
            
            if not resource_task or "resource_options" not in resource_task.task_option:
                return
            
            # 获取所有可能的资源选项名称（从所有资源中收集）
            interface = self.service_coordinator.interface
            all_resource_option_names = set()
            for resource in interface.get("resource", []):
                resource_opts = resource.get("option", [])
                all_resource_option_names.update(resource_opts)
            
            # 更新 hidden 状态
            resource_options = resource_task.task_option["resource_options"]
            has_changes = False
            
            for option_name in all_resource_option_names:
                if option_name in resource_options:
                    existing_value = resource_options[option_name]
                    
                    if option_name not in current_resource_option_names:
                        # 不属于当前资源的选项，标记为 hidden
                        if isinstance(existing_value, dict):
                            if not existing_value.get("hidden", False):
                                resource_options[option_name] = {**existing_value, "hidden": True}
                                has_changes = True
                        else:
                            # 简单值转换为字典格式并标记为 hidden
                            resource_options[option_name] = {"value": existing_value, "hidden": True}
                            has_changes = True
                    else:
                        # 属于当前资源的选项，移除 hidden 标记（如果有）
                        if isinstance(existing_value, dict) and existing_value.get("hidden", False):
                            # 移除 hidden 字段
                            new_value = {k: v for k, v in existing_value.items() if k != "hidden"}
                            # 如果只剩下 value 字段，直接使用 value
                            if len(new_value) == 1 and "value" in new_value:
                                resource_options[option_name] = new_value["value"]
                            else:
                                resource_options[option_name] = new_value
                            has_changes = True
            
            # 如果有变化，保存任务
            if has_changes:
                option_service.task_service.update_task(resource_task)
        except Exception as e:
            logger.error(f"更新资源选项 hidden 状态失败: {e}")
    
    def _update_resource_options(self):
        """根据当前资源更新资源选项的显示"""
        # 获取当前资源
        current_resource = self._get_current_resource_dict()
        if not current_resource:
            # 如果没有当前资源，清除选项显示
            self._clear_resource_options()
            return
        
        # 构建表单结构
        form_structure = self._build_resource_option_form_structure(current_resource)
        if not form_structure:
            # 如果资源没有选项，清除选项显示
            self._clear_resource_options()
            return
        
        # 获取当前 Resource 任务的配置
        from app.common.constants import _RESOURCE_
        option_service = self.service_coordinator.option_service
        resource_task = option_service.task_service.get_task(_RESOURCE_)
        resource_config = resource_task.task_option if resource_task else {}
        
        # 确保 Resource 任务不包含不应该有的字段（清理从配置文件加载时可能存在的错误字段）
        if resource_task:
            fields_to_remove = ["gpu", "agent_timeout", "custom", "_speedrun_config", "controller_type", "adb", "win32"]
            has_changes = False
            for field in fields_to_remove:
                if field in resource_task.task_option:
                    del resource_task.task_option[field]
                    has_changes = True
            # 如果有清理字段，保存任务
            if has_changes:
                option_service.task_service.update_task(resource_task)
        
        # 从 resource_options 字段中提取资源选项的值
        # 向后兼容：如果存在旧的根级别资源选项，优先使用，并迁移到 resource_options
        resource_options = resource_config.get("resource_options", {})
        
        # 向后兼容：检查是否有旧的根级别资源选项需要迁移
        old_resource_options = {
            k: v for k, v in resource_config.items() 
            if k != "resource" and k != "resource_options" and k in form_structure
        }
        
        # 如果有旧的资源选项，合并到 resource_options（resource_options 优先）
        if old_resource_options:
            migrated_options = {**old_resource_options, **resource_options}
            resource_options = migrated_options
            
            # 迁移到 resource_options 字段（下次保存时会自动清理旧字段）
            if resource_task:
                if "resource_options" not in resource_task.task_option:
                    resource_task.task_option["resource_options"] = {}
                resource_task.task_option["resource_options"].update(migrated_options)
                # 移除旧的根级别资源选项
                for k in old_resource_options.keys():
                    if k in resource_task.task_option:
                        del resource_task.task_option[k]
                # 保存迁移后的配置
                option_service.task_service.update_task(resource_task)
        
        # 从 resource_options 中提取当前资源的选项配置
        # 过滤掉 hidden 的选项（它们不属于当前资源）
        option_config = {}
        for k, v in resource_options.items():
            if k in form_structure:
                # 如果是字典且包含 hidden 字段且 hidden=True，跳过（因为不属于当前资源）
                if isinstance(v, dict) and v.get("hidden", False):
                    continue
                # 如果包含 hidden 字段但 hidden=False，移除 hidden 字段
                if isinstance(v, dict) and "hidden" in v:
                    v = {k2: v2 for k2, v2 in v.items() if k2 != "hidden"}
                    if len(v) == 1 and "value" in v:
                        v = v["value"]
                option_config[k] = v
        
        # 创建或更新选项表单组件
        if self.resource_option_form_widget is None:
            self.resource_option_form_widget = OptionFormWidget()
            self.option_page_layout.addWidget(self.resource_option_form_widget)
        
        # 构建表单
        self.resource_option_form_widget.build_from_structure(form_structure, option_config)
        
        # 连接选项变化信号（需要在 build_from_structure 之后，因为会重新创建选项项）
        self._connect_resource_option_signals()
    
    def _clear_resource_options(self):
        """清除资源选项的显示"""
        if self.resource_option_form_widget is not None:
            widget_to_remove = self.resource_option_form_widget
            self.resource_option_form_widget = None  # 先设置为 None，避免重复调用
            
            # 断开所有信号连接
            if hasattr(widget_to_remove, "option_items"):
                for option_item in widget_to_remove.option_items.values():
                    try:
                        option_item.option_changed.disconnect()
                    except:
                        pass
            
            # 清空选项表单组件的内容
            widget_to_remove._clear_options()
            
            # 从布局中移除
            if widget_to_remove.parent():
                self.option_page_layout.removeWidget(widget_to_remove)
            
            # 删除组件
            widget_to_remove.deleteLater()
    
    def _connect_resource_option_signals(self):
        """连接资源选项的变化信号"""
        if self.resource_option_form_widget is None:
            return
        
        # 遍历所有选项项，连接它们的信号
        for option_item in self.resource_option_form_widget.option_items.values():
            # 连接选项变化信号
            option_item.option_changed.connect(self._on_resource_option_changed)
            # 递归连接子选项的信号
            self._connect_resource_child_option_signals(option_item)
    
    def _connect_resource_child_option_signals(self, option_item):
        """递归连接资源选项的子选项信号
        
        Args:
            option_item: 选项项组件
        """
        for child_widget in option_item.child_options.values():
            child_widget.option_changed.connect(self._on_resource_option_changed)
            # 递归连接子选项的子选项
            self._connect_resource_child_option_signals(child_widget)
    
    def _on_resource_option_changed(self, key: str, value: Any):
        """资源选项变化时的回调函数，用于保存到 Resource 任务的 task_option
        
        Args:
            key: 选项键名
            value: 选项值
        """
        try:
            # 获取当前所有资源选项配置
            if self.resource_option_form_widget is None:
                return
            
            all_options = self.resource_option_form_widget.get_options()
            
            # 获取当前资源的选项列表（用于验证哪些选项应该被保留）
            current_resource = self._get_current_resource_dict()
            if not current_resource:
                return
            
            resource_option_names = current_resource.get("option", [])
            if not resource_option_names:
                return
            
            # 只保存当前资源的选项（过滤掉不属于当前资源的选项）
            resource_options = {
                k: v for k, v in all_options.items() 
                if k in resource_option_names
            }
            
            # 保存到 Resource 任务的 task_option
            from app.common.constants import _RESOURCE_
            option_service = self.service_coordinator.option_service
            resource_task = option_service.task_service.get_task(_RESOURCE_)
            
            if resource_task:
                # 更新 task_option（保留 resource 字段，将资源选项保存到 resource_options 字段）
                if not isinstance(resource_task.task_option, dict):
                    resource_task.task_option = {}
                
                # 初始化 resource_options 字段（如果不存在）
                if "resource_options" not in resource_task.task_option:
                    resource_task.task_option["resource_options"] = {}
                
                # 获取所有可能的资源选项名称（从所有资源中收集）
                interface = self.service_coordinator.interface
                all_resource_option_names = set()
                for resource in interface.get("resource", []):
                    resource_opts = resource.get("option", [])
                    all_resource_option_names.update(resource_opts)
                
                # 对于不在当前资源选项列表中的选项，标记为 hidden（保留其值）
                # 对于当前资源的选项，移除 hidden 标记（如果有）
                existing_resource_options = resource_task.task_option["resource_options"].copy()
                
                for option_name in all_resource_option_names:
                    if option_name not in resource_option_names:
                        # 不属于当前资源的选项，如果存在值，标记为 hidden
                        if option_name in existing_resource_options:
                            existing_value = existing_resource_options[option_name]
                            # 如果已经是字典格式，添加或保留 hidden 标记
                            if isinstance(existing_value, dict):
                                existing_resource_options[option_name] = {**existing_value, "hidden": True}
                            else:
                                # 简单值转换为字典格式并标记为 hidden
                                existing_resource_options[option_name] = {"value": existing_value, "hidden": True}
                    else:
                        # 属于当前资源的选项，移除 hidden 标记（如果有）
                        if option_name in existing_resource_options:
                            existing_value = existing_resource_options[option_name]
                            if isinstance(existing_value, dict) and "hidden" in existing_value:
                                # 移除 hidden 字段
                                existing_resource_options[option_name] = {k: v for k, v in existing_value.items() if k != "hidden"}
                                # 如果只剩下 value 字段，直接使用 value
                                if len(existing_resource_options[option_name]) == 1 and "value" in existing_resource_options[option_name]:
                                    existing_resource_options[option_name] = existing_resource_options[option_name]["value"]
                
                # 更新当前资源的选项值到 resource_options 字段（覆盖隐藏的选项）
                existing_resource_options.update(resource_options)
                resource_task.task_option["resource_options"] = existing_resource_options
                
                # 确保不包含不应该保存到 Resource 任务的字段
                fields_to_remove = ["gpu", "agent_timeout", "custom", "_speedrun_config", "controller_type", "adb", "win32"]
                for field in fields_to_remove:
                    if field in resource_task.task_option:
                        del resource_task.task_option[field]
                
                # 清理根级别的旧资源选项（向后兼容，迁移到 resource_options）
                old_keys_to_remove = [
                    k for k in resource_task.task_option.keys() 
                    if k != "resource" and k != "resource_options" and k in all_resource_option_names
                ]
                for k in old_keys_to_remove:
                    del resource_task.task_option[k]
                
                # 保存任务
                if not option_service.task_service.update_task(resource_task):
                    logger.warning("资源选项保存失败")
                    return
                
                # 如果当前选中的是 Resource 任务，同时更新 OptionService 的 current_options
                if option_service.current_task_id == _RESOURCE_:
                    # 更新 OptionService 的本地选项字典
                    option_service.current_options.update(resource_options)
                    # 触发选项更新信号（用于通知UI更新）
                    option_service.signal_bus.option_updated.emit(resource_options)
        except Exception as e:
            logger.error(f"保存资源选项失败: {e}")

