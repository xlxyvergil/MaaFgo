"""
选项注册器
负责管理和创建不同类型的选项项
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Type

from app.utils.logger import logger

if TYPE_CHECKING:
    from .base import OptionItemBase


class OptionItemRegistry:
    """
    选项项注册器
    用于注册和创建不同类型的选项项组件
    支持扩展：只需注册新的选项类型即可支持新的控件类型
    """

    # 类型到类的映射
    _registry: Dict[str, Type["OptionItemBase"]] = {}

    # 默认类型（当无法识别类型时使用）
    _default_type: str = "combobox"

    @classmethod
    def register(cls, type_name: str, item_class: Type["OptionItemBase"]) -> None:
        """
        注册选项类型

        :param type_name: 类型名称（如 "combobox", "switch", "input", "inputs"）
        :param item_class: 对应的选项项类
        """
        cls._registry[type_name.lower()] = item_class
        logger.debug(f"注册选项类型: {type_name} -> {item_class.__name__}")

    @classmethod
    def unregister(cls, type_name: str) -> bool:
        """
        取消注册选项类型

        :param type_name: 类型名称
        :return: 是否成功取消注册
        """
        type_name_lower = type_name.lower()
        if type_name_lower in cls._registry:
            del cls._registry[type_name_lower]
            logger.debug(f"取消注册选项类型: {type_name}")
            return True
        return False

    @classmethod
    def get_class(cls, type_name: str) -> Optional[Type["OptionItemBase"]]:
        """
        获取选项类型对应的类

        :param type_name: 类型名称
        :return: 对应的选项项类，如果未注册则返回 None
        """
        return cls._registry.get(type_name.lower())

    @classmethod
    def create(
        cls,
        key: str,
        config: Dict[str, Any],
        parent: Optional[Any] = None,
    ) -> "OptionItemBase":
        """
        创建选项项实例

        :param key: 选项的键名
        :param config: 选项配置字典
        :param parent: 父组件
        :return: 选项项实例
        """
        # 获取类型，默认为 combobox
        type_name = config.get("type", cls._default_type)
        if isinstance(type_name, str):
            type_name = type_name.lower()

        # 向后兼容：lineedit 类型映射到对应的 input/inputs
        if type_name == "lineedit":
            inputs = config.get("inputs", [])
            single_input = config.get("single_input", False)
            if single_input or len(inputs) <= 1:
                type_name = "input"
            else:
                type_name = "inputs"

        # 获取对应的类
        item_class = cls._registry.get(type_name)

        if item_class is None:
            logger.warning(
                f"未知的选项类型 '{type_name}'，使用默认类型 '{cls._default_type}'"
            )
            item_class = cls._registry.get(cls._default_type)

            if item_class is None:
                raise ValueError(
                    f"默认选项类型 '{cls._default_type}' 未注册"
                )

        return item_class(key, config, parent)

    @classmethod
    def get_registered_types(cls) -> list:
        """
        获取所有已注册的类型名称

        :return: 类型名称列表
        """
        return list(cls._registry.keys())

    @classmethod
    def is_registered(cls, type_name: str) -> bool:
        """
        检查类型是否已注册

        :param type_name: 类型名称
        :return: 是否已注册
        """
        return type_name.lower() in cls._registry

    @classmethod
    def set_default_type(cls, type_name: str) -> None:
        """
        设置默认类型

        :param type_name: 类型名称
        """
        if type_name.lower() not in cls._registry:
            logger.warning(f"设置的默认类型 '{type_name}' 未注册")
        cls._default_type = type_name.lower()

    @classmethod
    def clear(cls) -> None:
        """清空所有注册的类型（主要用于测试）"""
        cls._registry.clear()


def register_default_types():
    """注册默认的选项类型"""
    from .combobox import ComboBoxOptionItem
    from .switch import SwitchOptionItem
    from .input import InputOptionItem
    from .inputs import InputsOptionItem
    from .checkbox import CheckBoxOptionItem

    OptionItemRegistry.register("combobox", ComboBoxOptionItem)
    OptionItemRegistry.register("select", ComboBoxOptionItem)  # select 是 combobox 的别名
    OptionItemRegistry.register("switch", SwitchOptionItem)
    OptionItemRegistry.register("input", InputOptionItem)
    OptionItemRegistry.register("inputs", InputsOptionItem)
    OptionItemRegistry.register("lineedit", InputOptionItem)  # lineedit 向后兼容
    OptionItemRegistry.register("checkbox", CheckBoxOptionItem)

    logger.debug("\u5df2\u6ce8\u518c\u9ed8\u8ba4\u9009\u9879\u7c7b\u578b: combobox, select, switch, input, inputs, lineedit, checkbox")


# 模块加载时自动注册默认类型
register_default_types()


__all__ = ["OptionItemRegistry", "register_default_types"]
