from copy import deepcopy
from typing import Any, Dict, List, Optional

from app.utils.logger import logger
from app.core.service.Config_Service import ConfigService
from app.core.Item import TaskItem, CoreSignalBus
from app.common.constants import _RESOURCE_, _CONTROLLER_

# 速通配置默认值
DEFAULT_SPEEDRUN_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "force": False,
    "mode": "daily",
    "trigger": {
        "daily": {"hour_start": 0},
        "weekly": {"weekday": [1], "hour_start": 0},
        "monthly": {"day": [1], "hour_start": 0},
    },
    "run": {"count": 1, "min_interval_hours": 0},
}


class TaskService:
    """任务服务实现"""

    def __init__(
        self,
        config_service: ConfigService,
        signal_bus: CoreSignalBus,
        interface: Dict[str, Any],
    ):
        self.config_service = config_service
        self.signal_bus = signal_bus
        self.current_tasks = []
        self.know_task = []
        self.interface = interface or {}
        self.default_option = {}
        self.on_config_changed(self.config_service.current_config_id)
        # UI 的任务勾选切换事件现在通过 ServiceCoordinator.modify_task 路径处理

    def on_config_changed(self, config_id: str):
        """当配置变化时加载对应任务（由协调器直接调用）"""
        if config_id:
            config = self.config_service.get_config(config_id)
            if config:
                self.current_tasks = config.tasks
                self.know_task = config.know_task
                self.default_option = self.gen_default_option()
                # 在配置加载时就计算一次“可运行/隐藏”标记，确保 runner 可直接使用
                self.refresh_hidden_flags()

                # 发出 TaskItem 列表，UI 层可以选择转换为 dict 显示
                self.signal_bus.tasks_loaded.emit(self.current_tasks)
                self._check_know_task()

    def _check_know_task(self) -> bool:
        unknown_tasks: list[str] = []
        if not self.interface:
            raise ValueError("Interface not loaded")

        interface_tasks = [t.get("name") for t in self.interface.get("task", [])]
        interface_tasks = [t for t in interface_tasks if isinstance(t, str) and t]

        # 当前配置中已存在的任务名（用于幂等：防止重复插入同名任务）
        # 注意：在多资源模式切换 bundle 时，interface 可能先 reload 再 on_config_changed，
        # 此时 self.current_tasks 仍是旧配置的任务列表；因此必须从“当前配置对象”取 tasks 判重。
        config = self.config_service.get_config(self.config_service.current_config_id)
        tasks_snapshot = (config.tasks if config else None) or (self.current_tasks or [])
        existing_task_names = {t.name for t in tasks_snapshot if hasattr(t, "name") and t.name}

        # 现有 know_task 去重（保持原顺序）
        seen_known: set[str] = set()
        dedup_known: list[str] = []
        for name in self.know_task or []:
            if not isinstance(name, str) or not name:
                continue
            if name in seen_known:
                continue
            seen_known.add(name)
            dedup_known.append(name)
        self.know_task = dedup_known

        for task in interface_tasks:
            if task not in seen_known:
                unknown_tasks.append(task)
                seen_known.add(task)

        for unknown_task in unknown_tasks:
            self.know_task.append(unknown_task)
            # 仅在配置里不存在同名任务时才真正添加（防止重复；多资源/普通模式通用）
            if unknown_task in existing_task_names:
                continue
            if self.add_task(unknown_task):
                existing_task_names.add(unknown_task)

        # 同步到当前配置对象并持久化
        config = self.config_service.get_config(self.config_service.current_config_id)
        if config:
            config.know_task = self.know_task.copy()
            self.config_service.update_config(config.item_id, config)

        return True

    def init_new_config(self):
        """初始化新配置的任务"""
        if not self.interface:
            raise ValueError("Interface not loaded")
        # Regenerate default options
        self.default_option = self.gen_default_option()

        # 重置任务列表，仅保留基础任务，防止重复追加
        config_id = self.config_service.current_config_id
        if not config_id:
            return
        config = self.config_service.get_config(config_id)
        if not config:
            return
        base_tasks = [t for t in config.tasks if t.is_base_task()]
        config.tasks = base_tasks
        self.config_service.update_config(config_id, config)
        self.current_tasks = base_tasks

        # Reset know_task and add all tasks from interface
        self.know_task = []
        self._check_know_task()

    def apply_preset(self, preset: Dict[str, Any]) -> bool:
        """在 init_new_config 之后应用预设配置。

        预设会修改已添加任务的勾选状态和选项值，但不会从 know_task 中移除任务。
        所有 interface 中的任务仍然保留在 know_task 中。

        Args:
            preset: 预设配置字典，包含 name, task 等字段。
                preset["task"] 是一个列表，每个元素包含:
                - name: 对应 interface task 的 name
                - enabled: 可选，是否勾选（默认 True）
                - option: 可选，该任务各配置项的预设值

        Returns:
            是否成功应用预设
        """
        if not preset or not isinstance(preset, dict):
            return False

        preset_tasks = preset.get("task", [])
        if not isinstance(preset_tasks, list):
            return False

        config_id = self.config_service.current_config_id
        if not config_id:
            return False
        config = self.config_service.get_config(config_id)
        if not config:
            return False

        # 构建预设任务名 -> 预设任务配置 的映射
        preset_task_map: Dict[str, Dict[str, Any]] = {}
        for pt in preset_tasks:
            if isinstance(pt, dict) and isinstance(pt.get("name"), str):
                preset_task_map[pt["name"]] = pt

        interface_options = self.interface.get("option", {}) if self.interface else {}

        # 遍历当前配置的所有任务，应用预设
        changed_tasks: List[TaskItem] = []
        for task in config.tasks:
            if task.is_base_task():
                continue

            if task.name in preset_task_map:
                pt = preset_task_map[task.name]
                # 应用勾选状态
                task.is_checked = pt.get("enabled", True)
                # 应用选项值
                preset_option = pt.get("option")
                if isinstance(preset_option, dict) and isinstance(task.task_option, dict):
                    self._apply_preset_option(task, preset_option, interface_options)
                changed_tasks.append(task)
            else:
                # 特殊任务（spt: true）无论是否在预设中都保持原状态，不取消勾选
                if task.is_special:
                    continue
                # 不在预设中的普通任务，默认不勾选
                if task.is_checked:
                    task.is_checked = False
                    changed_tasks.append(task)

        # 批量更新
        if changed_tasks:
            self.update_tasks(changed_tasks)

        return True

    def _apply_preset_option(
        self,
        task: TaskItem,
        preset_option: Dict[str, Any],
        interface_options: Dict[str, Any],
    ) -> None:
        """将预设的选项值应用到任务的 task_option 中。

        preset_option 的格式:
            键: 对应 interface option 中的键名
            值: 取决于 option.type:
                - select/switch: string (case.name)
                - checkbox: string[] (case.name 数组)
                - input: record<string, string> (输入字段 name → 值)

        Args:
            task: 要修改的任务
            preset_option: 预设的选项值
            interface_options: interface 中定义的所有选项模板
        """
        if not isinstance(task.task_option, dict):
            task.task_option = {}

        for option_key, preset_value in preset_option.items():
            if option_key not in task.task_option:
                # 该选项在当前任务中不存在，跳过
                continue

            option_template = interface_options.get(option_key, {})
            option_type = (option_template.get("type") or "select").lower()

            current_option = task.task_option[option_key]
            if not isinstance(current_option, dict):
                current_option = {}
                task.task_option[option_key] = current_option

            if option_type == "checkbox":
                # checkbox 类型：preset_value 应为 string[]（case.name 数组）
                if isinstance(preset_value, list):
                    current_option["value"] = list(preset_value)
                    # 更新 children 的 hidden 状态
                    self._update_children_visibility_checkbox(
                        current_option, preset_value, option_key, option_template, interface_options
                    )
            elif option_type in ("select", "switch"):
                # select/switch 类型：preset_value 应为 string（case.name）
                if isinstance(preset_value, str):
                    current_option["value"] = preset_value
                    # 更新 children 的 hidden 状态
                    self._update_children_visibility_select(
                        current_option, preset_value, option_key, option_template, interface_options
                    )
            elif option_type == "input":
                # input 类型：preset_value 应为 record<string, string>
                if isinstance(preset_value, dict):
                    if not isinstance(current_option.get("value"), dict):
                        current_option["value"] = {}
                    current_option["value"].update(preset_value)

    def _update_children_visibility_select(
        self,
        option_data: Dict[str, Any],
        selected_case_name: str,
        option_key: str,
        option_template: Dict[str, Any],
        interface_options: Dict[str, Any],
    ) -> None:
        """更新 select/switch 类型选项的子选项可见性。"""
        children = option_data.get("children")
        if not isinstance(children, dict):
            return

        cases = option_template.get("cases", [])
        for case in cases:
            case_name = case.get("name", "")
            option_values = case.get("option")
            if not option_values:
                continue

            if isinstance(option_values, str):
                child_keys = [option_values]
            elif isinstance(option_values, list):
                child_keys = [v for v in option_values if isinstance(v, str)]
            else:
                continue

            for index, child_option_key in enumerate(child_keys):
                child_key = f"{option_key}_child_{case_name}_{child_option_key}_{index}"
                if child_key in children:
                    if case_name == selected_case_name:
                        children[child_key].pop("hidden", None)
                    else:
                        children[child_key]["hidden"] = True

    def _update_children_visibility_checkbox(
        self,
        option_data: Dict[str, Any],
        selected_case_names: List[str],
        option_key: str,
        option_template: Dict[str, Any],
        interface_options: Dict[str, Any],
    ) -> None:
        """更新 checkbox 类型选项的子选项可见性。"""
        children = option_data.get("children")
        if not isinstance(children, dict):
            return

        selected_set = set(selected_case_names)
        cases = option_template.get("cases", [])
        for case in cases:
            case_name = case.get("name", "")
            option_values = case.get("option")
            if not option_values:
                continue

            if isinstance(option_values, str):
                child_keys = [option_values]
            elif isinstance(option_values, list):
                child_keys = [v for v in option_values if isinstance(v, str)]
            else:
                continue

            for index, child_option_key in enumerate(child_keys):
                child_key = f"{option_key}_child_{case_name}_{child_option_key}_{index}"
                if child_key in children:
                    if case_name in selected_set:
                        children[child_key].pop("hidden", None)
                    else:
                        children[child_key]["hidden"] = True

    def reload_interface(self, interface: Dict[str, Any]):
        """刷新 interface 数据，用于热更新后同步"""
        logger.info("重新加载 interface 数据...")
        if not interface:
            raise ValueError("Interface not loaded")
        self.interface = interface

        # 重新生成默认选项
        self.default_option = self.gen_default_option()

        # interface 变化可能影响任务可见性/可运行性，刷新一次隐藏标记
        self.refresh_hidden_flags()

        # 检查是否有新任务
        self._check_know_task()

        logger.info("interface 数据重新加载完成")

    def refresh_hidden_flags(self) -> None:
        """根据当前 Resource/Controller 选项以及 interface 约束刷新每个任务的 is_hidden。

        设计目标：任务配置层给出“可运行配置”，runner 只需要读取 is_checked/is_hidden。
        """
        try:
            # 当前资源与控制器类型（为空则视为“不过滤”）
            resource_name = ""
            controller_type = ""

            res_task = self.get_task(_RESOURCE_)
            if res_task and isinstance(res_task.task_option, dict):
                resource_name = str(res_task.task_option.get("resource", "") or "").strip()

            ctrl_task = self.get_task(_CONTROLLER_)
            if ctrl_task and isinstance(ctrl_task.task_option, dict):
                controller_type = str(ctrl_task.task_option.get("controller_type", "") or "").strip()

            interface_tasks = self.interface.get("task", []) if isinstance(self.interface, dict) else []
            # name -> def 快速索引
            task_def_map: dict[str, dict] = {}
            if isinstance(interface_tasks, list):
                for td in interface_tasks:
                    if isinstance(td, dict) and isinstance(td.get("name"), str) and td.get("name"):
                        task_def_map[td["name"]] = td

            def _allowed_by_list(value: Any, current: str) -> bool:
                """controller/resource 字段的通用判断：缺省/空 => 允许；否则必须命中。"""
                if not current:
                    return True
                if value in (None, "", [], {}):
                    return True
                allowed: list[str] = []
                if isinstance(value, str):
                    if value.strip():
                        allowed = [value.strip()]
                elif isinstance(value, list):
                    allowed = [str(x).strip() for x in value if x is not None and str(x).strip()]
                else:
                    # 非支持格式：兜底为允许，避免误伤
                    return True
                allowed_norm = {s.lower() for s in allowed if s}
                return current.strip().lower() in allowed_norm

            for t in self.current_tasks or []:
                if not isinstance(t, TaskItem):
                    continue
                if t.is_base_task():
                    t.is_hidden = False
                    continue
                td = task_def_map.get(t.name)
                if not td:
                    # interface 未定义该任务：默认不隐藏（兼容旧配置）
                    t.is_hidden = False
                    continue

                ok_resource = _allowed_by_list(td.get("resource", None), resource_name)
                ok_controller = _allowed_by_list(td.get("controller", None), controller_type)
                t.is_hidden = not (ok_resource and ok_controller)
        except Exception as exc:
            logger.warning(f"刷新任务隐藏标记失败，将保持现状: {exc}")

    def _get_interface_speedrun(self, task_name: str) -> Dict[str, Any]:
        """从 interface 中获取任务的 speedrun 配置"""
        if not self.interface:
            return {}
        for task in self.interface.get("task", []):
            if task.get("name") == task_name:
                speedrun_cfg = task.get("speedrun")
                return deepcopy(speedrun_cfg) if isinstance(speedrun_cfg, dict) else {}
        return {}

    def build_speedrun_config(
        self, task_name: str, existing: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        合成 speedrun 配置：默认值 <- interface 配置 <- 已保存配置
        """
        config: Dict[str, Any] = deepcopy(DEFAULT_SPEEDRUN_CONFIG)
        interface_cfg = self._get_interface_speedrun(task_name)
        if interface_cfg:
            self._deep_merge_dict(config, interface_cfg)
        if isinstance(existing, dict):
            self._deep_merge_dict(config, deepcopy(existing))
        return config

    def ensure_speedrun_config_for_task(
        self, task: TaskItem, persist: bool = False
    ) -> Dict[str, Any]:
        """
        确保任务包含标准化的 speedrun 配置；可选持久化
        
        注意：基础任务（Controller, Resource, Post-Action）不需要 speedrun_config
        """
        # 基础任务不需要 speedrun_config
        if task.is_base_task():
            # 如果基础任务中有 speedrun_config，删除它
            if isinstance(task.task_option, dict) and "_speedrun_config" in task.task_option:
                del task.task_option["_speedrun_config"]
                if persist:
                    self.update_task(task)
            return {}
        
        if not isinstance(task.task_option, dict):
            task.task_option = {}

        existing = task.task_option.get("_speedrun_config")
        normalized = self.build_speedrun_config(task.name, existing)
        if existing != normalized:
            task.task_option["_speedrun_config"] = normalized
            if persist:
                self.update_task(task)
        return normalized

    def add_task(self, task_name: str, is_special: bool = False) -> bool:
        """添加任务

        Args:
            task_name: 任务名称
            is_special: 是否为特殊任务,默认为 False
        """
        if not self.interface:
            raise ValueError("Interface not loaded")
        for task in self.interface.get("task", []):
            if task["name"] == task_name:
                # 检查 interface.json 中是否标记为特殊任务(spt字段)
                task_is_special = task.get("spt", is_special)

                # 为当前任务动态生成默认选项
                task_default_option = self.gen_single_task_default_option(task)

                # 任务是否默认选中
                default_check = False
                for task in self.interface.get("task", []):
                    if task["name"] == task_name:
                        default_check = task.get("default_check", False)
                        break

                new_task = TaskItem(
                    name=task["name"],
                    item_id=TaskItem.generate_id(is_special=task_is_special),
                    is_checked=default_check,
                    task_option=task_default_option,
                    is_special=task_is_special,
                )
                self.update_task(new_task)
                return True
        return False

    def gen_single_task_default_option(self, task: dict) -> dict[str, dict]:
        """生成单个任务的默认选项"""
        if not self.interface:
            raise ValueError("Interface not loaded")

        interface_options = self.interface.get("option", {})

        def _select_default_case(option_def: dict) -> Optional[dict]:
            cases = option_def.get("cases", [])
            if not cases:
                return None
            target_case_name = option_def.get("default_case")
            if target_case_name:
                for case in cases:
                    if case.get("name") == target_case_name:
                        return case
            return cases[0]

        def _normalize_child_payload(payload: Any) -> dict[str, Any]:
            if isinstance(payload, dict):
                if "value" in payload:
                    return deepcopy(payload)
                return {"value": deepcopy(payload)}
            return {"value": deepcopy(payload)}

        def _gen_option_defaults_recursive(
            option_key: str, option_template: dict
        ) -> Any:
            """递归生成选项默认值"""
            inputs = option_template.get("inputs")
            if isinstance(inputs, list) and inputs:
                nested_values: dict[str, Any] = {}
                for input_config in inputs:
                    input_name = input_config.get("name")
                    default_value = input_config.get("default", "")
                    pipeline_type = input_config.get("pipeline_type", "string")

                    if pipeline_type == "int":
                        try:
                            default_value = int(default_value) if default_value else 0
                        except (ValueError, TypeError):
                            pass

                    if input_name:
                        nested_values[input_name] = default_value
                return {"value": nested_values}

            cases = option_template.get("cases", [])
            if cases:
                option_type = (option_template.get("type") or "select").lower()

                if option_type == "checkbox":
                    # checkbox 类型：default_case 是列表，value 也是列表
                    default_case_names = option_template.get("default_case", [])
                    if isinstance(default_case_names, str):
                        default_case_names = [default_case_names]
                    # 如果没有指定 default_case，默认不选中任何项
                    selected_case_names_set = set(default_case_names)
                    option_result: dict[str, Any] = {"value": list(default_case_names)}

                    children: dict[str, Any] = {}
                    for case in cases:
                        case_name = case.get("name", "")
                        option_values = case.get("option")
                        if not option_values:
                            continue

                        if isinstance(option_values, str):
                            child_keys = [option_values]
                        elif isinstance(option_values, list):
                            child_keys = [
                                value for value in option_values if isinstance(value, str)
                            ]
                        else:
                            continue

                        for index, child_option_key in enumerate(child_keys):
                            child_template = interface_options.get(child_option_key)
                            if not child_template:
                                continue

                            child_default = _gen_option_defaults_recursive(
                                child_option_key, child_template
                            )
                            child_entry = _normalize_child_payload(child_default)

                            if case_name not in selected_case_names_set:
                                child_entry["hidden"] = True
                            else:
                                child_entry.pop("hidden", None)

                            child_key = (
                                f"{option_key}_child_{case_name}_{child_option_key}_{index}"
                            )
                            children[child_key] = child_entry

                    if children:
                        option_result["children"] = children

                    return option_result
                else:
                    # select / switch 类型
                    selected_case = _select_default_case(option_template)
                    if not selected_case:
                        return {}
                    selected_case_name = selected_case.get("name", "")
                    option_result: dict[str, Any] = {"value": selected_case_name}

                    children: dict[str, Any] = {}
                    for case in cases:
                        case_name = case.get("name", "")
                        option_values = case.get("option")
                        if not option_values:
                            continue

                        if isinstance(option_values, str):
                            child_keys = [option_values]
                        elif isinstance(option_values, list):
                            child_keys = [
                                value for value in option_values if isinstance(value, str)
                            ]
                        else:
                            continue

                        for index, child_option_key in enumerate(child_keys):
                            child_template = interface_options.get(child_option_key)
                            if not child_template:
                                continue

                            child_default = _gen_option_defaults_recursive(
                                child_option_key, child_template
                            )
                            child_entry = _normalize_child_payload(child_default)

                            if case_name != selected_case_name:
                                child_entry["hidden"] = True
                            else:
                                child_entry.pop("hidden", None)

                            child_key = (
                                f"{option_key}_child_{case_name}_{child_option_key}_{index}"
                            )
                            children[child_key] = child_entry

                    if children:
                        option_result["children"] = children

                    return option_result

            return {}

        task_name = task["name"]
        task_default_option = {}

        # Iterate through options defined for this task
        for option in task.get("option", []):
            option_template = interface_options.get(option)
            if option_template:
                option_defaults = _gen_option_defaults_recursive(
                    option, option_template
                )
                task_default_option[option] = option_defaults

        # 追加速通配置（使用 interface 或默认值）
        # 注意：基础任务（Controller, Resource, Post-Action）不需要 speedrun_config
        from app.common.constants import _RESOURCE_, _CONTROLLER_, POST_ACTION
        # 检查是否是基础任务（通过检查 task_name 是否匹配基础任务的名称）
        # 由于这里处理的是 interface 中的任务定义，需要检查 task_name
        # 但基础任务的名称可能不同，所以我们需要通过其他方式判断
        # 实际上，基础任务不会在 interface 的 task 列表中，所以这里不需要特殊处理
        # 但为了安全，我们检查 task_name 是否可能是基础任务
        is_base_task_name = task_name in ["Controller", "Resource", "Post-Action", "Pre-Configuration"]
        if not is_base_task_name:
            task_default_option["_speedrun_config"] = self.build_speedrun_config(task_name)

        return task_default_option

    def gen_default_option(self) -> dict[str, dict[str, dict]]:
        """生成所有任务的默认选项映射"""
        if not self.interface:
            raise ValueError("Interface not loaded")

        default_option = {}

        # Iterate through all tasks
        for task in self.interface.get("task", []):
            default_option[task["name"]] = self.gen_single_task_default_option(task)

        return default_option

    def apply_task_update(self, task_data: TaskItem, idx: int = -2) -> bool:
        """当任务更新时保存到当前配置（接收 TaskItem 或 dict）
        
        Args:
            task_data: 任务数据
            idx: 插入位置索引，默认为-2（倒数第二个位置）。如果是新任务且idx>=0，则插入到idx位置；如果idx<0，则插入到倒数第|idx|个位置
        """
        config_id = self.config_service.current_config_id
        if not config_id:
            return False

        config = self.config_service.get_config(config_id)
        if not config:
            return False

        # Normalize incoming to TaskItem
        if isinstance(task_data, TaskItem):
            incoming = task_data
        else:
            incoming = TaskItem.from_dict(task_data)

        # 查找并更新任务
        task_updated = False
        for i, task in enumerate(config.tasks):
            if task.item_id == incoming.item_id:
                config.tasks[i] = incoming
                task_updated = True
                break

        # 如果是新任务，根据idx参数插入到指定位置
        if not task_updated:
            original_len = len(config.tasks)
            # 确保 idx 是整数类型，处理可能的布尔值或其他类型
            if not isinstance(idx, int):
                if idx is False or idx == 0:
                    # 如果传入 False 或 0，使用默认值 -2
                    idx = -2
                else:
                    # 其他非整数类型，尝试转换或使用默认值
                    try:
                        idx = int(idx)
                    except (ValueError, TypeError):
                        idx = -2
            
            if idx >= 0:
                # 正数索引：插入到指定位置
                # 确保不超出范围，但允许插入到列表末尾（idx == len(config.tasks)）
                insert_pos = min(idx, original_len)
                config.tasks.insert(insert_pos, incoming)
                logger.info(f"插入新任务 '{incoming.name}' 到位置 {insert_pos} (请求位置: {idx}, 原列表长度: {original_len}, 新列表长度: {len(config.tasks)})")
            else:
                # 负数索引：从末尾计算位置（默认-2表示倒数第二个）
                # Python 的 insert(-2, item) 会在倒数第二个元素之前插入
                # 例如：idx=-2, len=5 -> insert(-2) 会在索引 3 插入（倒数第二个之前）
                # 但如果列表长度 <= |idx|，Python 会插入到位置 0，这不是我们想要的
                # 我们希望在这种情况下，插入到倒数第二个位置（即 len-1）
                if original_len > abs(idx):
                    # 列表足够长，使用标准的负数索引语义：len + idx + 1
                    # 例如：len=5, idx=-2 -> 5 + (-2) + 1 = 4，但 insert(-2) 实际是 3
                    # 实际上 Python 的 insert(-2) 等价于 insert(len + idx + 1)
                    insert_pos = original_len + idx + 1
                else:
                    # 列表太短，插入到倒数第二个位置（确保不插入到位置 0）
                    # 例如：len=2, idx=-2 -> 插入到位置 1（在 Pre-Configuration 和 Post-Action 之间）
                    insert_pos = original_len - 1
                config.tasks.insert(insert_pos, incoming)
                logger.info(f"插入新任务 '{incoming.name}' 到位置 {insert_pos} (负数索引: {idx}, 原列表长度: {original_len}, 新列表长度: {len(config.tasks)})")

        # 保存配置（直接传入 ConfigItem，由底层处理转换）
        if self.config_service.update_config(config_id, config):
            # 更新本地任务列表并发出对象列表
            self.current_tasks = config.tasks
            self.signal_bus.tasks_loaded.emit(self.current_tasks)
            return True
        return False

    def apply_task_order(self, task_order: List[str]) -> bool:
        """同步最新任务顺序到当前配置并持久化，但不强制刷新UI列表。"""
        config_id = self.config_service.current_config_id
        if not config_id:
            return False

        config = self.config_service.get_config(config_id)
        if not config:
            return False

        tasks_by_id = {task.item_id: task for task in config.tasks}
        ordered_tasks: list[TaskItem] = []
        for task_id in task_order:
            task = tasks_by_id.pop(task_id, None)
            if task is not None:
                ordered_tasks.append(task)

        # 追加未在拖拽序列中的任务，确保列表完整
        if tasks_by_id:
            ordered_tasks.extend(tasks_by_id.values())

        if not ordered_tasks:
            return False

        config.tasks = ordered_tasks

        if self.config_service.update_config(config_id, config):
            self.current_tasks = ordered_tasks
            return True
        return False

    def get_tasks(self) -> List[TaskItem]:
        """获取当前配置的任务列表"""
        return self.current_tasks

    def get_task(self, task_id: str) -> Optional[TaskItem]:
        """获取特定任务"""
        for task in self.current_tasks:
            if task.item_id == task_id:
                return task
        return None

    def update_task(self, task: TaskItem, idx: int = -2) -> bool:
        """更新任务
        
        Args:
            task: 任务对象
            idx: 插入位置索引，默认为-2（倒数第二个位置）
        """
        return self.apply_task_update(task, idx)

    def update_tasks(self, tasks: List[TaskItem]) -> bool:
        """批量更新任务：在当前配置中按 tasks 中的 item_id 替换或添加，最后一次性保存并发送 tasks_loaded 或逐项 task_updated。"""
        if not tasks:
            return True

        config_id = self.config_service.current_config_id
        if not config_id:
            return False

        config = self.config_service.get_config(config_id)
        if not config:
            return False

        # build a map for quick replace
        id_to_task = {t.item_id: t for t in tasks}

        replaced = set()
        for i, t in enumerate(config.tasks):
            if t.item_id in id_to_task:
                config.tasks[i] = id_to_task[t.item_id]
                replaced.add(t.item_id)

        # add tasks that are new (not replaced)
        for t in tasks:
            if t.item_id not in replaced:
                # 插入到倒数第二位,确保"完成后操作"始终在最后
                config.tasks.insert(-1, t)

        # 保存配置一次
        ok = self.config_service.update_config(config_id, config)
        if ok:
            # 更新本地任务列表并发送整体 loaded 信号（UI 会进行 diff）
            self.current_tasks = config.tasks
            # 优先发送 tasks_loaded 以便视图基于完整列表做最小更新
            self.signal_bus.tasks_loaded.emit(self.current_tasks)
        return ok

    def delete_task(self, task_id: str) -> bool:
        """删除任务（基础任务不可删除）"""
        config_id = self.config_service.current_config_id
        if not config_id:
            return False

        config = self.config_service.get_config(config_id)
        if not config:
            return False

        # 查找目标任务
        target_task = None
        for task in config.tasks:
            if task.item_id == task_id:
                target_task = task
                break
        if (
            target_task
            and hasattr(target_task, "is_base_task")
            and target_task.is_base_task()
        ):
            return False

        # 从配置中移除任务（非基础任务）
        config.tasks = [task for task in config.tasks if task.item_id != task_id]

        # 保存配置
        if self.config_service.update_config(config_id, config):
            # 更新本地任务列表
            self.current_tasks = config.tasks
            self.signal_bus.tasks_loaded.emit(self.current_tasks)
            return True

        return False

    def reorder_tasks(self, task_order: List[str]) -> bool:
        """重新排序任务"""
        return self.apply_task_order(task_order)

    def get_task_execution_info(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务的执行信息（entry 和 pipeline_override）

        Args:
            task_id: 任务ID

        Returns:
            Dict: 包含 entry 和 pipeline_override，格式为：
                {
                    "entry": "任务入口名称",
                    "pipeline_override": {...}
                }
            如果任务不存在或 interface 未加载，返回 None
        """
        # 获取任务
        task = self.get_task(task_id)
        if not task:
            logger.warning(f"任务 {task_id} 不存在")
            return None

        if not self.interface:
            logger.error("Interface 未加载")
            return None

        # 从 interface 中查找任务的 entry
        entry = None
        task_pipeline_override = {}

        for interface_task in self.interface.get("task", []):
            if interface_task.get("name") == task.name:
                entry = interface_task.get("entry", "")
                # 获取任务级别的 pipeline_override
                task_pipeline_override = interface_task.get("pipeline_override", {})
                break

        if not entry:
            logger.warning(f"任务 '{task.name}' 在 interface 中未找到 entry")
            return None

        from app.core.utils.pipeline_helper import (
            get_pipeline_override_from_task_option,
        )

        option_pipeline_override = get_pipeline_override_from_task_option(
            self.interface, task.task_option, task.item_id
        )

        # 深度合并：任务级 pipeline_override + 选项级 pipeline_override
        merged_override = {}

        # 先添加任务级的
        self._deep_merge_dict(merged_override, task_pipeline_override)

        # 再添加选项级的（选项级优先级更高）
        self._deep_merge_dict(merged_override, option_pipeline_override)

        return {"entry": entry, "pipeline_override": merged_override}

    def _deep_merge_dict(self, target: Dict, source: Dict) -> None:
        """深度合并两个字典

        Args:
            target: 目标字典（会被修改）
            source: 源字典
        """
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                # 递归合并
                self._deep_merge_dict(target[key], value)
            else:
                # 直接覆盖
                target[key] = value
