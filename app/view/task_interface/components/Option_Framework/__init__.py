"""
选项框架模块
提供动态生成选项界面的框架
"""
from .OptionItemWidget import OptionItemWidget
from .OptionFormWidget import OptionFormWidget
from .SpeedrunConfigWidget import SpeedrunConfigWidget
from .items import (
    OptionItemBase,
    OptionItemRegistry,
    TooltipComboBox,
    ComboBoxOptionItem,
    SwitchOptionItem,
    InputOptionItem,
    InputsOptionItem,
)

__all__ = [
    "OptionItemWidget",
    "OptionFormWidget",
    "SpeedrunConfigWidget",
    "OptionItemBase",
    "OptionItemRegistry",
    "TooltipComboBox",
    "ComboBoxOptionItem",
    "SwitchOptionItem",
    "InputOptionItem",
    "InputsOptionItem",
]
