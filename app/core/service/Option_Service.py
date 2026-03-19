from typing import Any, Dict, Optional

from app.core.service.Task_Service import TaskService
from app.core.Item import CoreSignalBus


class OptionService:
    """选项服务实现"""

    def __init__(self, task_service: TaskService, signal_bus: CoreSignalBus):
        self.task_service = task_service
        self.signal_bus = signal_bus
        self.current_task_id = None
        self.current_options: Dict[str, Any] = {}
        self.form_structure: Optional[Dict[str, Any]] = {}  # 保存当前表单结构

    def select_task(self, task_id: str) -> bool:
        """当任务被选中时加载选项和表单结构"""
        self.current_task_id = task_id
        task = self.task_service.get_task(task_id)
        if task:
            # 确保速通配置存在并与 interface 对齐（基础任务不需要 speedrun_config）
            try:
                self.task_service.ensure_speedrun_config_for_task(task, persist=True)
            except Exception:
                pass
            self.current_options = task.task_option
            from app.common.constants import _RESOURCE_, _CONTROLLER_, POST_ACTION

            if task.item_id == _RESOURCE_:
                self.form_structure = {"type": "resource"}
            elif task.item_id == _CONTROLLER_:
                self.form_structure = {"type": "controller"}
            elif task.item_id == POST_ACTION:
                self.form_structure = {"type": "post_action"}
            else:
                # 获取表单结构
                self.form_structure = self.get_form_structure_by_task_name(
                    task.name, self.task_service.interface
                )
            self.signal_bus.options_loaded.emit()
            return True
        return False

    def clear_selection(self) -> None:
        """重置当前选项状态，用于配置切换等场景。"""
        self.current_task_id = None
        self.current_options = {}
        self.form_structure = {}

    def _on_option_updated(self, option_data: Dict[str, Any]) -> bool:
        """当选项更新时保存到当前任务"""
        if not self.current_task_id:
            return False

        task = self.task_service.get_task(self.current_task_id)
        if not task:
            return False

        # 更新任务中的选项并持久化
        task.task_option.update(option_data)

        # 基础任务不应该包含 speedrun_config
        from app.common.constants import _RESOURCE_, _CONTROLLER_, POST_ACTION

        if task.is_base_task() and "_speedrun_config" in task.task_option:
            del task.task_option["_speedrun_config"]

        success = self.task_service.update_task(task)

        # 发出选项更新信号，通知UI层更新显示
        if success:
            # 若更新了资源/控制器类型，则刷新所有任务的 is_hidden（供 runner 直接使用）
            if "resource" in option_data or "controller_type" in option_data:
                try:
                    self.task_service.refresh_hidden_flags()
                except Exception:
                    pass
            # 控制器类型变化时，重新计算当前任务的 form_structure（按 interface.option[*].controller 过滤），并通知 UI 刷新选项表单
            if "controller_type" in option_data and task and not task.is_base_task():
                interface = getattr(self.task_service, "interface", None)
                if interface:
                    self.form_structure = self.get_form_structure_by_task_name(
                        task.name, interface
                    ) or {}
                    self.signal_bus.options_loaded.emit()
            self.signal_bus.option_updated.emit(option_data)

        return success

    def get_options(self) -> Dict[str, Any]:
        """获取当前任务的选项"""
        return self.current_options

    def get_option(self, option_key: str) -> Any:
        """获取特定选项"""
        return self.current_options.get(option_key)

    def update_option(self, option_key: str, option_value: Any) -> bool:
        """更新选项"""
        # 更新本地选项字典
        self.current_options[option_key] = option_value
        return self._on_option_updated({option_key: option_value})

    def update_options(self, options: Dict[str, Any]) -> bool:
        """批量更新选项"""
        # 批量更新本地选项字典
        self.current_options.update(options)
        return self._on_option_updated(options)

    def get_form_structure(self) -> Optional[Dict[str, Any]]:
        """获取当前表单结构"""
        return self.form_structure

    def get_form_field(self, field_name: str) -> Optional[Dict[str, Any]]:
        """获取特定表单字段的配置"""
        if isinstance(self.form_structure, dict):
            field_value = self.form_structure.get(field_name)
            # 确保返回的是字典类型，如果不是则返回None
            return field_value if isinstance(field_value, dict) else None
        return None

    def _copy_icon_from_source(
        self, target: Dict[str, Any], source: Dict[str, Any]
    ) -> None:
        """将 source 中的 icon 字段复制到 target"""
        icon = source.get("icon")
        if icon:
            target["icon"] = icon

    def _copy_description_from_source(
        self, target: Dict[str, Any], source: Dict[str, Any]
    ) -> None:
        """将 source 中的 description/doc 字段复制到 target"""
        description = source.get("description") or source.get("doc")
        if description:
            target["description"] = description

    def process_option_def(
        self,
        option_def: Dict[str, Any],
        all_options: Dict[str, Dict[str, Any]],
        option_key: str = "",
    ) -> Dict[str, Any]:
        """
        递归处理选项定义，处理select类型中cases的子选项(option参数)

        Args:
            option_def: 选项定义字典
            all_options: 所有选项定义的字典
            option_key: 选项的键名，当没有name和label时使用

        Returns:
            Dict: 处理后的字段配置，可能包含children属性存储子选项
        """
        field_config = {}
        option_type = option_def.get("type")
        if isinstance(option_type, str):
            option_type = option_type.lower()

        # 向后兼容：缺失 type 的选项默认视为 combobox
        if not option_type:
            option_type = "combobox"

        # 保存选项的 name（用于从接口文件中获取具体选项）
        if option_key:
            field_config["name"] = option_key

        # 设置字段标签，处理$前缀
        # 优先使用label，其次是name，如果都没有则使用option_key
        label = option_def.get("label", option_def.get("name", option_key))
        field_config["label"] = label

        # 处理description字段（向后兼容 doc）
        description = option_def.get("description")
        if not description and "doc" in option_def:
            description = option_def["doc"]
        if description:
            field_config["description"] = description

        # 复制 icon 信息（如果有）
        self._copy_icon_from_source(field_config, option_def)

        # 处理不同类型的选项

        # 处理 switch 类型
        if option_type == "switch":
            field_config["type"] = "switch"
            # switch 类型固定为 YES 和 NO 两个选项
            options = [{"name": "Yes", "label": "是"}, {"name": "No", "label": "否"}]
            children = {}

            # 处理 cases 中的子选项，但只关注 YES 和 NO
            for case in option_def.get("cases", []):
                case_name = case.get("name", "")
                # 标准化处理：不区分大小写，处理各种可能的 YES/NO 变体
                case_name_upper = (
                    case_name.upper()
                    if isinstance(case_name, str)
                    else str(case_name).upper()
                )

                # 确定对应的标准名称（Yes 或 No）
                if case_name_upper in ["YES", "Y", "TRUE", "1", "ON"]:
                    standard_name = "Yes"
                elif case_name_upper in ["NO", "N", "FALSE", "0", "OFF"]:
                    standard_name = "No"
                else:
                    # 如果不匹配，跳过
                    continue

                # 递归处理 cases 中的子选项(option参数)
                child_fields = []
                if "option" in case:
                    option_value = case["option"]

                    def _append_child(opt_value: str):
                        if isinstance(opt_value, str) and opt_value in all_options:
                            sub_option_def = all_options[opt_value]
                            child_field = self.process_option_def(
                                sub_option_def, all_options, opt_value
                            )
                            if "name" not in child_field:
                                child_field["name"] = opt_value
                            child_fields.append(child_field)

                    if isinstance(option_value, str):
                        _append_child(option_value)
                    elif isinstance(option_value, list):
                        for opt in option_value:
                            _append_child(opt)

                if child_fields:
                    if len(child_fields) == 1:
                        children[standard_name] = child_fields[0]
                    else:
                        children[standard_name] = child_fields

            field_config["options"] = options
            # 如果有子选项，添加children属性
            if children:
                field_config["children"] = children

        elif option_type == "input":
            inputs_source = option_def.get("inputs", [])
            inputs = [dict(item) for item in inputs_source]
            has_main_label = bool(option_def.get("label"))

            # 区分 input 和 inputs 类型：
            # - 有主 label → type = "inputs"（选项组，显示主标签+子输入框列表）
            # - 没有主 label → type = "input"（每个都是独立的单输入框）
            if has_main_label:
                # 有主 label，作为 inputs 选项组
                field_config["type"] = "inputs"
                field_config["inputs"] = inputs
                field_config["single_input"] = False
            else:
                # 没有主 label，每个都作为独立的 input
                field_config["type"] = "input"
                field_config["inputs"] = inputs
                field_config["single_input"] = len(inputs) == 1
                if len(inputs) > 1:
                    field_config["independent_inputs"] = True  # 标记为独立输入模式

            # 传递verify字段到表单结构中
            if "verify" in option_def:
                field_config["verify"] = option_def["verify"]
            # 如果有默认值，使用第一个input的默认值
            if inputs and "default" in inputs[0]:
                field_config["default"] = inputs[0]["default"]
            # 处理 option 级别的 pattern_msg
            option_pattern_msg = option_def.get("pattern_msg")
            if option_pattern_msg:
                field_config["pattern_msg"] = option_pattern_msg

            # 为每个input项传递verify字段
            for input_item in field_config["inputs"]:
                # 如果input项没有自己的verify字段，使用父级的verify字段
                if "verify" not in input_item and "verify" in option_def:
                    input_item["verify"] = option_def["verify"]
                # 如果input项没有自己的 pattern_msg，则继承父级 pattern_msg
                if "pattern_msg" not in input_item and option_pattern_msg:
                    input_item["pattern_msg"] = option_pattern_msg
        else:
            # 默认类型为combobox；checkbox 类型也走同样的逻辑
            field_config["type"] = option_type if option_type == "checkbox" else "combobox"
            options = []
            children = {}

            # 处理cases中的每个选项
            for case in option_def.get("cases", []):
                # 优先使用label，如果没有则使用name
                display_label = case.get("label", case.get("name", ""))
                option_name = case.get("name", display_label)

                option_entry = {"name": option_name, "label": display_label}
                self._copy_icon_from_source(option_entry, case)
                self._copy_description_from_source(option_entry, case)
                options.append(option_entry)

                # 递归处理cases中的子选项(option参数)
                child_fields = []
                if "option" in case:
                    option_value = case["option"]

                    def _append_child(opt_value: str):
                        if isinstance(opt_value, str) and opt_value in all_options:
                            sub_option_def = all_options[opt_value]
                            child_field = self.process_option_def(
                                sub_option_def, all_options, opt_value
                            )
                            if "name" not in child_field:
                                child_field["name"] = opt_value
                            child_fields.append(child_field)

                    if isinstance(option_value, str):
                        _append_child(option_value)
                    elif isinstance(option_value, list):
                        for opt in option_value:
                            _append_child(opt)

                if child_fields:
                    if len(child_fields) == 1:
                        children[option_name] = child_fields[0]
                    else:
                        children[option_name] = child_fields

            field_config["options"] = options
            # 如果有子选项，添加children属性
            if children:
                field_config["children"] = children
            # 传递 default_case（checkbox 使用列表，select 使用字符串）
            if "default_case" in option_def:
                field_config["default_case"] = option_def["default_case"]

        return field_config

    def _is_option_visible_for_controller(
        self, option_def: Dict[str, Any], current_controller: str
    ) -> bool:
        """
        根据 interface.option[*].controller 判断当前控制器下是否应显示该选项。

        规则（与任务列表 controller 过滤一致）：
        - 缺省/空：对所有控制器显示
        - 字符串：仅当当前 controller 与该字符串匹配时显示
        - 列表：仅当当前 controller 在列表中时显示（不区分大小写）
        """
        controllers = option_def.get("controller", None)
        if controllers in (None, "", [], {}):
            return True
        current_norm = (current_controller or "").strip().lower()
        if not current_norm:
            return True
        allowed: list = []
        if isinstance(controllers, str):
            if controllers.strip():
                allowed = [controllers.strip()]
        elif isinstance(controllers, list):
            allowed = [
                str(x).strip()
                for x in controllers
                if x is not None and str(x).strip()
            ]
        else:
            return True
        allowed_norm = {s.lower() for s in allowed if s}
        return current_norm in allowed_norm

    def get_form_structure_by_task_name(
        self, task_name: str, interface: dict
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        根据任务名称从interface获取对应的表单结构

        Args:
            task_name: 任务名称

        Returns:
            Dict: 表单结构字典，用于DynamicFormMixin的update_form方法
                  如果未找到对应任务或选项，返回None
        """
        if (
            not hasattr(self.task_service, "interface")
            or not self.task_service.interface
        ):
            return None

        form_structure = {}

        # 遍历interface中的任务
        for task in interface.get("task", []):
            if task.get("name") == task_name:
                # 获取任务的option字段（字符串数组）
                task_option_names = task.get("option", [])
                # 检查任务是否有description字段
                task_description = task.get("description") or task.get("doc")
                if task_description:
                    form_structure["description"] = task_description
                # 获取顶层的option定义
                all_options = interface.get("option", {})

                # 当前选中的控制器类型（用于按 interface.option[*].controller 过滤）
                current_controller = ""
                from app.common.constants import _CONTROLLER_

                controller_task = self.task_service.get_task(_CONTROLLER_)
                if controller_task and isinstance(controller_task.task_option, dict):
                    current_controller = (
                        controller_task.task_option.get("controller_type") or ""
                    )

                # 遍历任务需要的每个选项
                for option_name in task_option_names:
                    if option_name not in all_options:
                        continue
                    option_def = all_options[option_name]
                    if not self._is_option_visible_for_controller(
                        option_def, current_controller
                    ):
                        continue
                    # 使用process_option_def方法递归处理选项定义，传入option_name作为键名
                    field_config = self.process_option_def(
                        option_def, all_options, option_name
                    )
                    form_structure[option_name] = field_config
                break

        return form_structure if form_structure else None
