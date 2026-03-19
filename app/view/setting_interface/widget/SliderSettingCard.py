from typing import Callable, Optional, Union

from PySide6.QtCore import Qt
from qfluentwidgets import SettingCard, BodyLabel, FluentIconBase, Slider

from app.common.config import cfg, ConfigItem


class SliderSettingCard(SettingCard):
    """通用滑杆设置卡片，基于 qfluentwidgets 的 SettingCard。"""

    def __init__(
        self,
        icon: Union[str, FluentIconBase],
        title: str,
        content: str = "",
        parent=None,
        *,
        minimum: int = 0,
        maximum: int = 100,
        step: int = 1,
        value: Optional[int] = None,
        suffix: str = "",
        config_item: Optional[ConfigItem] = None,
        on_value_changed: Optional[Callable[[int], None]] = None,
    ):
        """
        :param icon: 左侧图标
        :param title: 标题
        :param content: 描述文本
        :param parent: 父级窗口
        :param minimum: 最小值
        :param maximum: 最大值
        :param step: 步长
        :param value: 初始值（优先于 config_item）
        :param suffix: 显示在数值后的后缀，例如 %
        :param config_item: 绑定的配置项，若提供则会自动读写
        :param on_value_changed: 自定义回调，接收当前值
        """
        super().__init__(icon, title, content, parent)

        self._config_item = config_item
        self._suffix = suffix
        self._on_value_changed = on_value_changed

        initial = (
            value
            if value is not None
            else (cfg.get(config_item) if config_item else minimum)
        )
        initial = self._clamp(initial, minimum, maximum)

        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(minimum, maximum)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(max(step * 2, 1))
        self.slider.setValue(initial)
        self.slider.setFixedWidth(150)

        self.valueLabel = BodyLabel(self._format_value(initial), self)
        self.valueLabel.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        

        self.hBoxLayout.addWidget(self.slider)
        self.hBoxLayout.addWidget(self.valueLabel)
        self.hBoxLayout.addSpacing(16)

        self.slider.valueChanged.connect(self._handle_value_changed)

    def _clamp(self, val: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(maximum, int(val)))

    def _format_value(self, val: int) -> str:
        return f"{val}{self._suffix}"

    def _handle_value_changed(self, value: int):
        """内部统一处理：更新显示、写入配置、回调。"""
        self.valueLabel.setText(self._format_value(value))
        if self._config_item:
            cfg.set(self._config_item, value)
        if self._on_value_changed:
            self._on_value_changed(value)

    # 对外辅助方法
    def value(self) -> int:
        return int(self.slider.value())

    def setValue(self, value: int):
        self.slider.setValue(value)

    def setRange(self, minimum: int, maximum: int):
        self.slider.setRange(minimum, maximum)