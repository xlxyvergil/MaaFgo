"""Pipeline Override 辅助工具

提供从任务选项中提取 pipeline_override 的功能。

架构说明：
- ChildKeyParser: 基类，处理新格式的 child_key（直接使用 child_name 作为 key）
- LegacyChildKeyParser: 继承类，兼容旧格式（{父选项名}_child_{触发case名}_{子选项名}_{索引}）

未来移除旧格式支持时，只需将 _child_key_parser 替换为 ChildKeyParser() 即可。
"""

from typing import Dict, Any, List, Optional
from app.utils.logger import logger


# ==================== 子选项 Key 解析器 ====================

class ChildKeyParser:
    """子选项 key 解析器基类（新格式）
    
    新格式：child_key 就是选项名称本身（如 "输入A级角色名"）
    """
    
    def extract_option_name(self, child_key: str) -> str | None:
        """从子选项 key 中提取选项名称
        
        Args:
            child_key: 子选项 key
            
        Returns:
            str: 选项名称，解析失败返回 None
        """
        if not child_key:
            return None
        return child_key


class LegacyChildKeyParser(ChildKeyParser):
    """子选项 key 解析器（旧格式兼容）
    
    兼容两种格式：
    1. 新格式：直接返回 key（继承自父类）
    2. 旧格式：解析 {父选项名}_child_{触发case名}_{子选项名}_{索引}
    """
    
    def extract_option_name(self, child_key: str) -> str | None:
        """从子选项 key 中提取选项名称
        
        Args:
            child_key: 子选项 key
            
        Returns:
            str: 选项名称，解析失败返回 None
        """
        if not child_key:
            return None
            
        # 新格式：不包含 "_child_"，使用父类逻辑
        if "_child_" not in child_key:
            return super().extract_option_name(child_key)
        
        # 旧格式: {父选项名}_child_{触发case名}_{子选项名}_{索引}
        parts = child_key.split("_child_")
        if len(parts) != 2:
            logger.warning(f"无法解析子选项 key: {child_key}")
            return None

        # 后半部分格式: {触发case名}_{子选项名}_{索引}
        suffix = parts[1]

        # 找到最后一个 _ 分隔的索引
        last_underscore = suffix.rfind("_")
        if last_underscore == -1:
            logger.warning(f"无法解析子选项 key: {child_key}")
            return None

        # 去掉索引后的部分: {触发case名}_{子选项名}
        name_part = suffix[:last_underscore]

        # 找到第一个 _ 分隔触发case名和子选项名
        first_underscore = name_part.find("_")
        if first_underscore == -1:
            logger.warning(f"无法解析子选项 key: {child_key}")
            return None

        # 提取子选项名
        option_name = name_part[first_underscore + 1:]
        return option_name


# 全局解析器实例（使用兼容模式）
# 未来移除旧格式支持时，替换为: _child_key_parser = ChildKeyParser()
_child_key_parser = LegacyChildKeyParser()


# ==================== 公共 API ====================


def get_controller_option_pipeline_override(
    interface: Dict[str, Any], controller_task_option: Dict[str, Any]
) -> Dict[str, Any]:
    """从控制器选项中提取 pipeline_override

    根据当前控制器类型，查找 interface.json 中对应控制器定义的 option 列表，
    然后从 controller_task_option["controller_options"] 中提取对应选项的 pipeline_override。

    注意：当前 interface.json 中的控制器定义尚无 option 字段，此函数为预留接口，
    待未来控制器定义添加 option 字段后自动生效。

    Args:
        interface: interface.json 配置
        controller_task_option: Controller 任务的 task_option

    Returns:
        Dict: 控制器选项的 pipeline_override
    """
    if not interface or not controller_task_option:
        return {}

    controller_type = controller_task_option.get("controller_type")
    if not controller_type:
        return {}

    # 在 interface.controller 中找到当前控制器定义
    controllers = interface.get("controller", [])
    controller_def = None
    for ctrl in controllers:
        if ctrl.get("name") == controller_type:
            controller_def = ctrl
            break

    if not controller_def:
        return {}

    # 检查控制器定义是否声明了可用选项
    controller_option_names = controller_def.get("option", [])
    if not controller_option_names:
        return {}

    # 从 controller_task_option 中获取控制器选项值
    controller_options = controller_task_option.get("controller_options", {})
    if not controller_options:
        return {}

    options = interface.get("option", {})
    merged_override: Dict[str, Any] = {}

    for option_name, option_value in controller_options.items():
        # 只处理控制器定义中声明的选项
        if option_name in controller_option_names:
            _process_option_recursive(
                options, option_name, option_value, merged_override
            )

    return merged_override


def get_pipeline_override_from_task_option(
    interface: Dict[str, Any], task_options: Dict[str, Any], task_id: Optional[str] = None
) -> Dict[str, Any]:
    """从任务选项中提取 pipeline_override

    Args:
        interface: interface.json 配置
        task_options: 任务选项字典，格式如：
            {
                "作战关卡": "4-20 厄险",
                "复现次数": "x1",
                "自定义关卡": {
                    "章节号": "4",
                    "难度": "Hard",
                    "超时时间": 20000
                }
            }
            或复杂格式（包含子选项）：
            {
                "低阶柜台": {
                    "value": "No",
                    "children": {
                        "低阶柜台_child_Yes_金兔子(低阶柜台)_0": {
                            "value": "No",
                            "hidden": true
                        }
                    }
                }
            }
            对于 Resource 任务，资源选项保存在 "resource_options" 字段中
        task_id: 任务ID，用于判断是否为 Resource 任务

    Returns:
        Dict: 合并后的 pipeline_override
    """
    if not interface:
        logger.warning("Interface 配置为空")
        return {}

    merged_override = {}
    options = interface.get("option", {})

    # 如果是 Resource 任务，按优先级顺序处理各层选项
    # 合并优先级（从低到高）：global_options < resource_options
    # controller_options 由 get_controller_option_pipeline_override 单独处理
    from app.common.constants import _RESOURCE_
    if task_id == _RESOURCE_:
        # 1. 先处理 global_options（最低优先级）
        if "global_options" in task_options:
            global_options = task_options["global_options"]
            for option_name, option_value in global_options.items():
                _process_option_recursive(
                    options, option_name, option_value, merged_override
                )

        # 2. 再处理 resource_options（优先级高于 global_options）
        if "resource_options" in task_options:
            resource_options = task_options["resource_options"]
            for option_name, option_value in resource_options.items():
                _process_option_recursive(
                    options, option_name, option_value, merged_override
                )
    else:
        # 非 Resource 任务，按原来的逻辑处理所有选项
        for option_name, option_value in task_options.items():
            # 跳过内部字段和 resource_options 字段本身
            # 兼容：基础 Resource 任务会在根级保存 "resource"（资源选择），它不是可映射到 option 的 UI 选项
            if (
                option_name.startswith("_")
                or option_name == "resource_options"
                or option_name == "resource"
            ):
                continue
            # 处理选项（包括递归处理子选项）
            _process_option_recursive(
                options, option_name, option_value, merged_override
            )

    return merged_override


def _process_option_recursive(
    options: Dict[str, Any],
    option_name: str,
    option_value: Any,
    merged_override: Dict[str, Any],
) -> None:
    """递归处理选项及其子选项

    Args:
        options: interface 中的 option 配置
        option_name: 选项名称
        option_value: 选项值（可能是字符串、字典或包含 value/children 的复杂格式）
        merged_override: 合并结果字典（会被修改）
    """
    # 跳过内部状态/临时字段
    if option_name.startswith("_"):
        logger.debug(f"跳过内部选项: {option_name}")
        return

    # 如果选项本身被标记为隐藏，直接跳过
    if isinstance(option_value, dict) and option_value.get("hidden", False):
        logger.debug(f"跳过隐藏的选项: {option_name}")
        return

    # 提取实际的选项值和子选项
    actual_value, children = _extract_option_value_and_children(option_value)

    # 获取该选项的 pipeline_override
    option_override = _get_option_pipeline_override(options, option_name, actual_value)

    # 深度合并
    _deep_merge_dict(merged_override, option_override)

    # 递归处理子选项（只处理可见的）
    if children:
        for child_key, child_data in children.items():
            # 检查子选项是否隐藏
            if isinstance(child_data, dict) and child_data.get("hidden", False):
                logger.debug(f"跳过隐藏的子选项: {child_key}")
                continue

            # 从子选项 key 中提取实际的选项名称
            # 格式: {父选项名}_child_{触发case名}_{子选项名}_{索引}
            actual_option_name = _extract_child_option_name(child_key)

            if actual_option_name:
                _process_option_recursive(
                    options, actual_option_name, child_data, merged_override
                )


def _extract_option_value_and_children(
    option_value: Any,
) -> tuple[Any, Dict[str, Any] | None]:
    """从选项值中提取实际值和子选项

    Args:
        option_value: 选项值，可能是：
            - 字符串: "Yes"
            - 字典（input类型）: {"章节号": "4", "难度": "Hard"}
            - 复杂格式: {"value": "No", "children": {...}}

    Returns:
        tuple: (实际值, 子选项字典或None)
    """
    if not isinstance(option_value, dict):
        # 简单字符串值
        return option_value, None

    # 检查是否是复杂格式（包含 value 字段）
    if "value" in option_value:
        actual_value = option_value["value"]
        children = option_value.get("children")
        return actual_value, children

    # 普通字典（input类型的值）
    return option_value, None


def _extract_child_option_name(child_key: str) -> str | None:
    """从子选项 key 中提取实际的选项名称
    
    使用全局解析器实例，支持新旧格式兼容。
    
    Args:
        child_key: 子选项 key

    Returns:
        str: 实际的选项名称，如果解析失败返回 None
    """
    return _child_key_parser.extract_option_name(child_key)


def _get_option_pipeline_override(
    options: Dict[str, Any], option_name: str, option_value: str | Dict[str, Any]
) -> Dict[str, Any]:
    """获取指定选项的 pipeline_override"""
    if option_name not in options:
        logger.debug(f"选项 '{option_name}' 不存在于 interface 配置中")
        return {}

    option_config = options[option_name]
    option_type = option_config.get("type", "select")

    if option_type in ("select", "switch"):
        if not isinstance(option_value, str):
            logger.error(f"Select/Switch 选项值必须是字符串，实际类型: {type(option_value)}")
            return {}
        return _get_select_pipeline_override(option_config, option_value)
    elif option_type == "checkbox":
        if not isinstance(option_value, list):
            logger.error(f"Checkbox 选项值必须是列表，实际类型: {type(option_value)}")
            return {}
        return _get_checkbox_pipeline_override(option_config, option_value)
    elif option_type == "input":
        if not isinstance(option_value, dict):
            logger.error(f"Input 选项值必须是字典，实际类型: {type(option_value)}")
            return {}
        return _get_input_pipeline_override(option_config, option_value)
    else:
        logger.warning(f"未知的选项类型: {option_type}")
        return {}


def _get_select_pipeline_override(
    option_config: Dict[str, Any], case_name: str
) -> Dict[str, Any]:
    """获取 select 类型选项的 pipeline_override"""
    cases = option_config.get("cases", [])
    # 1) 优先精确匹配（保持行为不变）
    for case in cases:
        if case.get("name") == case_name:
            return case.get("pipeline_override", {})

    # 2) 兼容 Yes/No 这类历史配置写法：转成 interface 常用的 yes/no
    normalized = (case_name or "").strip()
    if normalized.lower() in ("yes", "no"):
        normalized = normalized.lower()

    # 3) 再做一次不区分大小写的匹配（用于 Yes/No vs yes/no 等）
    for case in cases:
        case_def_name = str(case.get("name", ""))
        if case_def_name.lower() == normalized.lower():
            return case.get("pipeline_override", {})

    available = [str(c.get("name", "")) for c in cases]
    logger.debug(f"未找到 case: {case_name}（可用: {available}）")
    return {}


def _get_checkbox_pipeline_override(
    option_config: Dict[str, Any], selected_names: List[str]
) -> Dict[str, Any]:
    """获取 checkbox 类型选项的 pipeline_override

    多个被选中 case 的 pipeline_override 按照 cases 数组中的定义顺序
    依次深度合并，与用户勾选的先后顺序无关。

    Args:
        option_config: 选项配置
        selected_names: 用户选中的 case name 列表

    Returns:
        合并后的 pipeline_override
    """
    cases = option_config.get("cases", [])
    selected_set = set(selected_names)
    merged: Dict[str, Any] = {}

    for case in cases:
        case_name = case.get("name", "")
        if case_name in selected_set:
            override = case.get("pipeline_override", {})
            if override:
                _deep_merge_dict(merged, override)

    return merged


def _get_input_pipeline_override(
    option_config: Dict[str, Any], input_values: Dict[str, Any]
) -> Dict[str, Any]:
    """获取 input 类型选项的 pipeline_override"""
    # 获取基础 pipeline_override
    base_override = option_config.get("pipeline_override", {})

    # 深拷贝以避免修改原始配置
    import copy

    result = copy.deepcopy(base_override)

    # 替换占位符
    result = _replace_placeholders(result, input_values)

    # 处理类型转换
    result = _convert_types(result, option_config, input_values)

    return result


def _replace_placeholders(
    pipeline_override: Dict[str, Any], input_values: Dict[str, Any]
) -> Dict[str, Any]:
    """替换 pipeline_override 中的占位符"""

    def replace_recursive(obj):
        """递归替换占位符"""
        if isinstance(obj, dict):
            return {k: replace_recursive(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [replace_recursive(item) for item in obj]
        elif isinstance(obj, str):
            # 替换字符串中的占位符
            result = obj
            for input_name, input_value in input_values.items():
                placeholder = f"{{{input_name}}}"
                result = result.replace(placeholder, str(input_value))
            return result
        else:
            return obj

    result = replace_recursive(pipeline_override)
    return result if isinstance(result, dict) else {}


def _convert_types(
    pipeline_override: Dict[str, Any],
    option_config: Dict[str, Any],
    input_values: Dict[str, Any],
) -> Dict[str, Any]:
    """根据 pipeline_type 转换值的类型"""
    inputs_config = option_config.get("inputs", [])

    # 创建输入值到类型的映射
    value_type_map = {}
    for input_config in inputs_config:
        input_name = input_config.get("name")
        pipeline_type = input_config.get("pipeline_type", "string")
        if input_name and input_name in input_values:
            input_value = input_values[input_name]
            value_type_map[str(input_value)] = pipeline_type

    # 递归转换类型
    def convert_recursive(obj):
        if isinstance(obj, dict):
            return {k: convert_recursive(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_recursive(item) for item in obj]
        elif isinstance(obj, str) and obj in value_type_map:
            return _convert_value_type(obj, value_type_map[obj])
        else:
            return obj

    result = convert_recursive(pipeline_override)
    return result if isinstance(result, dict) else {}


def _convert_value_type(value, pipeline_type: str):
    """转换单个值的类型"""
    try:
        if pipeline_type == "int":
            return int(value)
        elif pipeline_type == "float":
            return float(value)
        elif pipeline_type == "bool":
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        else:  # string
            return str(value)
    except (ValueError, TypeError) as e:
        logger.warning(f"类型转换失败: {value} -> {pipeline_type}, 错误: {e}")
        return value


def _deep_merge_dict(target: Dict, source: Dict) -> None:
    """深度合并两个字典"""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_merge_dict(target[key], value)
        else:
            target[key] = value
