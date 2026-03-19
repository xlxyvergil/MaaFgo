from copy import deepcopy
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QFrame
from qfluentwidgets import (
    ComboBox,
    SwitchButton,
    BodyLabel,
    SpinBox,
    DoubleSpinBox,
    LineEdit,
    isDarkTheme,
    qconfig,
)

from app.core.service.Task_Service import DEFAULT_SPEEDRUN_CONFIG
from app.view.task_interface.components.Option_Framework.animations import HeightAnimator


class SpeedrunConfigWidget(QWidget):
    """速通规则配置页"""

    config_changed = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config: Dict[str, Any] = deepcopy(DEFAULT_SPEEDRUN_CONFIG)
        self._updating = False
        self._init_ui()
        self.set_config(self._config, emit=False)

    # region UI
    def _init_ui(self) -> None:
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(12)

        # 标题
        title = BodyLabel(self.tr("Speedrun Configuration"))
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.main_layout.addWidget(title)

        # 模式切换
        mode_form = QFormLayout()
        mode_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        mode_form.setFormAlignment(Qt.AlignmentFlag.AlignLeft)

        self.mode_combo = ComboBox(self)
        self.mode_combo.addItem(self.tr("Daily"), userData="daily")
        self.mode_combo.addItem(self.tr("Weekly"), userData="weekly")
        self.mode_combo.addItem(self.tr("Monthly"), userData="monthly")
        mode_form.addRow(self.tr("Mode"), self.mode_combo)
        self.main_layout.addLayout(mode_form)

        # 每日
        self.daily_container = QWidget(self)
        daily_layout = QFormLayout(self.daily_container)
        self.daily_hour_combo = self._build_hour_combo()
        daily_layout.addRow(self.tr("Refresh Hour"), self.daily_hour_combo)
        self.daily_section, self.daily_line = self._wrap_with_indicator(self.daily_container)
        self.main_layout.addWidget(self.daily_section)

        # 每周
        self.weekly_container = QWidget(self)
        weekly_layout = QFormLayout(self.weekly_container)
        self.weekday_combo = ComboBox(self)
        weekdays = [
            (self.tr("Monday"), 1),
            (self.tr("Tuesday"), 2),
            (self.tr("Wednesday"), 3),
            (self.tr("Thursday"), 4),
            (self.tr("Friday"), 5),
            (self.tr("Saturday"), 6),
            (self.tr("Sunday"), 7),
        ]
        for label, value in weekdays:
            self.weekday_combo.addItem(label, userData=value)
        self.week_hour_combo = self._build_hour_combo()
        weekly_layout.addRow(self.tr("Weekday"), self.weekday_combo)
        weekly_layout.addRow(self.tr("Refresh Hour"), self.week_hour_combo)
        self.weekly_section, self.weekly_line = self._wrap_with_indicator(self.weekly_container)
        self.main_layout.addWidget(self.weekly_section)

        # 每月
        self.monthly_container = QWidget(self)
        monthly_layout = QFormLayout(self.monthly_container)
        self.month_day_combo = ComboBox(self)
        for day in range(1, 32):
            self.month_day_combo.addItem(f"{day}", userData=day)
        self.month_hour_combo = self._build_hour_combo()
        monthly_layout.addRow(self.tr("Day"), self.month_day_combo)
        monthly_layout.addRow(self.tr("Refresh Hour"), self.month_hour_combo)
        self.monthly_section, self.monthly_line = self._wrap_with_indicator(self.monthly_container)
        self.main_layout.addWidget(self.monthly_section)

        # 通用配置
        common_title = BodyLabel(self.tr("General"))
        common_title.setStyleSheet("font-weight: 600;")
        self.main_layout.addWidget(common_title)

        common_form = QFormLayout()
        common_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        common_form.setFormAlignment(Qt.AlignmentFlag.AlignLeft)

        self.count_spin = SpinBox(self)
        self.count_spin.setRange(0, 9999)
        self.count_spin.setValue(1)
        common_form.addRow(self.tr("Refresh Count"), self.count_spin)

        self.interval_spin = DoubleSpinBox(self)
        self.interval_spin.setRange(0.0, 10000.0)
        self.interval_spin.setDecimals(2)
        self.interval_spin.setSingleStep(0.5)
        common_form.addRow(self.tr("Min Interval (hours)"), self.interval_spin)

        enable_row = QHBoxLayout()
        self.enabled_switch = SwitchButton(self)
        self.enabled_switch.setOnText(self.tr("Enabled"))
        self.enabled_switch.setOffText(self.tr("Disabled"))
        enable_label = BodyLabel(self.tr("Enable Speedrun"))
        enable_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        enable_row.addWidget(enable_label)
        enable_row.addStretch()
        enable_row.addWidget(self.enabled_switch)
        enable_container = QWidget(self)
        enable_container.setLayout(enable_row)

        common_form.addRow(enable_container)
        # 运行时信息（只读）
        self.current_count_edit = LineEdit(self)
        self.current_count_edit.setReadOnly(True)
        self.current_count_edit.setPlaceholderText(self.tr("N/A"))
        common_form.addRow(self.tr("Current Count"), self.current_count_edit)

        self.last_run_edit = LineEdit(self)
        self.last_run_edit.setReadOnly(True)
        self.last_run_edit.setPlaceholderText(self.tr("N/A"))
        common_form.addRow(self.tr("Last Run Time"), self.last_run_edit)
        self.main_layout.addLayout(common_form)

        self._bind_signals()
        self._init_animators()
        self._update_mode_visibility("daily", animate=False)

    def _build_hour_combo(self) -> ComboBox:
        combo = ComboBox(self)
        for hour in range(0, 24):
            combo.addItem(f"{hour:02d}:00", userData=hour)
        return combo

    def _bind_signals(self) -> None:
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.daily_hour_combo.currentIndexChanged.connect(self._on_value_changed)
        self.weekday_combo.currentIndexChanged.connect(self._on_value_changed)
        self.week_hour_combo.currentIndexChanged.connect(self._on_value_changed)
        self.month_day_combo.currentIndexChanged.connect(self._on_value_changed)
        self.month_hour_combo.currentIndexChanged.connect(self._on_value_changed)
        self.count_spin.valueChanged.connect(self._on_value_changed)
        self.interval_spin.valueChanged.connect(self._on_value_changed)
        self.enabled_switch.checkedChanged.connect(self._on_value_changed)
        qconfig.themeChanged.connect(self._on_theme_changed)

    # endregion

    # region data helpers
    def _deep_merge(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                self._deep_merge(target[key], value)
            else:
                target[key] = deepcopy(value)

    def _set_combo_value(self, combo: ComboBox, value: Any, fallback_index: int = 0):
        matched = False
        for idx in range(combo.count()):
            if combo.itemData(idx) == value:
                combo.setCurrentIndex(idx)
                matched = True
                break
        if not matched:
            combo.setCurrentIndex(fallback_index if combo.count() else -1)

    def _get_combo_value(self, combo: ComboBox, default: Any = None) -> Any:
        idx = combo.currentIndex()
        if idx < 0:
            return default
        data = combo.itemData(idx)
        return data if data is not None else default

    # endregion

    # region public API
    def set_config(self, config: Optional[Dict[str, Any]], emit: bool = True) -> None:
        """应用配置到控件"""
        merged = deepcopy(DEFAULT_SPEEDRUN_CONFIG)
        if isinstance(config, dict):
            self._deep_merge(merged, config)
        
        # 清理trigger对象，移除不应该存在的字段（如run）
        # trigger应该只包含daily、weekly、monthly三个键
        if isinstance(merged.get("trigger"), dict):
            trigger = merged["trigger"]
            # 只保留合法的trigger键
            valid_keys = {"daily", "weekly", "monthly"}
            keys_to_remove = [key for key in trigger.keys() if key not in valid_keys]
            for key in keys_to_remove:
                del trigger[key]

        self._updating = True
        self._config = merged

        mode = str(merged.get("mode", "daily")).lower()
        self._set_combo_value(self.mode_combo, mode, 0)

        trigger = merged.get("trigger", {})
        daily_hour = (
            trigger.get("daily", {}).get("hour_start", 0)
            if isinstance(trigger, dict)
            else 0
        )
        self._set_combo_value(self.daily_hour_combo, int(daily_hour), 0)

        weekly = trigger.get("weekly", {}) if isinstance(trigger, dict) else {}
        weekday = None
        weekdays = weekly.get("weekday", [])
        if isinstance(weekdays, list) and weekdays:
            weekday = weekdays[0]
        self._set_combo_value(self.weekday_combo, weekday if weekday else 1, 0)
        self._set_combo_value(
            self.week_hour_combo, int(weekly.get("hour_start", 0)), 0
        )

        monthly = trigger.get("monthly", {}) if isinstance(trigger, dict) else {}
        days = monthly.get("day", [])
        day_value = days[0] if isinstance(days, list) and days else 1
        self._set_combo_value(self.month_day_combo, int(day_value), 0)
        self._set_combo_value(
            self.month_hour_combo, int(monthly.get("hour_start", 0)), 0
        )

        run_cfg = merged.get("run", {}) if isinstance(merged.get("run"), dict) else {}
        self.count_spin.setValue(int(run_cfg.get("count", 1) or 0))
        self.interval_spin.setValue(float(run_cfg.get("min_interval_hours", 0) or 0))

        self.enabled_switch.setChecked(bool(merged.get("enabled", False)))

        self._update_mode_visibility(mode)
        self._updating = False

        if emit:
            self._emit_change()

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return deepcopy(self._config)

    def set_runtime_state(
        self, state: Optional[Dict[str, Any]], speedrun_cfg: Optional[Dict[str, Any]]
    ) -> None:
        """设置运行时展示信息（当前剩余次数、上次运行时间）"""
        state = state or {}
        speedrun_cfg = speedrun_cfg or {}

        run_cfg = speedrun_cfg.get("run", {}) if isinstance(speedrun_cfg, dict) else {}
        default_count = 1
        try:
            if isinstance(run_cfg, dict):
                default_count = int(run_cfg.get("count", 1) or 1)
        except (TypeError, ValueError):
            default_count = 1

        remaining = state.get("remaining_count")
        if isinstance(remaining, int) and remaining >= 0:
            self.current_count_edit.setText(str(remaining))
        else:
            self.current_count_edit.setText(str(default_count))

        last_entries = state.get("last_runtime")
        epoch_iso = "1970-01-01T00:00:00"
        text = epoch_iso
        if isinstance(last_entries, list) and last_entries:
            text = str(last_entries[-1])
        elif isinstance(last_entries, (str, int, float)):
            text = str(last_entries)
        self.last_run_edit.setText(text or epoch_iso)

    # endregion

    # region events
    def _on_mode_changed(self, _: int) -> None:
        mode_value = self._get_combo_value(self.mode_combo, "daily")
        self._update_mode_visibility(str(mode_value))
        self._on_value_changed()

    def _on_value_changed(self, *args, **kwargs) -> None:  # type: ignore[override]
        if self._updating:
            return
        self._emit_change()

    def _emit_change(self) -> None:
        self._config = self._collect_config()
        self.config_changed.emit(deepcopy(self._config))

    # endregion

    def _update_mode_visibility(self, mode: str, animate: bool = True) -> None:
        mode = (mode or "").lower()
        targets = {
            "daily": self.daily_section,
            "weekly": self.weekly_section,
            "monthly": self.monthly_section,
        }
        animators = getattr(self, "_mode_animators", {})
        for key, section in targets.items():
            if not section:
                continue
            is_target = key == mode
            animator = animators.get(key)
            if animate and animator:
                if is_target:
                    animator.expand()
                else:
                    animator.collapse()
            else:
                section.setVisible(is_target)
                section.setMaximumHeight(16777215 if is_target else 0)
    def _init_animators(self) -> None:
        """初始化模式容器的展开/收起动画"""
        self._mode_animators = {
            "daily": HeightAnimator(self.daily_section, duration=200, parent=self),
            "weekly": HeightAnimator(self.weekly_section, duration=200, parent=self),
            "monthly": HeightAnimator(self.monthly_section, duration=200, parent=self),
        }

    def _wrap_with_indicator(self, inner: QWidget) -> tuple[QWidget, QFrame]:
        """为模式区包裹指示竖线并返回 (wrapper, line)"""
        wrapper = QWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        line = QFrame(wrapper)
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedWidth(3)
        self._set_indicator_color(line)
        layout.addWidget(line)
        layout.addWidget(inner, 1)
        wrapper.setVisible(False)
        wrapper.setMaximumHeight(0)
        return wrapper, line

    def _set_indicator_color(self, line: QFrame) -> None:
        """根据主题更新指示线颜色"""
        if isDarkTheme():
            line.setStyleSheet(
                "QFrame { background-color: rgba(255, 255, 255, 0.3); border: none; border-radius: 1px; }"
            )
        else:
            line.setStyleSheet(
                "QFrame { background-color: rgba(0, 0, 0, 0.2); border: none; border-radius: 1px; }"
            )

    def _on_theme_changed(self):
        """主题切换时刷新指示线颜色"""
        for line in [self.daily_line, self.weekly_line, self.monthly_line]:
            self._set_indicator_color(line)

    def _collect_config(self) -> Dict[str, Any]:
        config = deepcopy(DEFAULT_SPEEDRUN_CONFIG)
        mode_value = str(self._get_combo_value(self.mode_combo, "daily")).lower()
        config["mode"] = mode_value
        config["enabled"] = self.enabled_switch.isChecked()

        count_value = self.count_spin.value()
        interval_value = float(self.interval_spin.value())
        config["run"]["count"] = int(count_value)
        config["run"]["min_interval_hours"] = interval_value

        if mode_value == "weekly":
            weekday = int(self._get_combo_value(self.weekday_combo, 1))
            hour = int(self._get_combo_value(self.week_hour_combo, 0))
            config["trigger"]["weekly"]["weekday"] = [weekday]
            config["trigger"]["weekly"]["hour_start"] = hour
        elif mode_value == "monthly":
            day = int(self._get_combo_value(self.month_day_combo, 1))
            hour = int(self._get_combo_value(self.month_hour_combo, 0))
            config["trigger"]["monthly"]["day"] = [day]
            config["trigger"]["monthly"]["hour_start"] = hour
        else:
            hour = int(self._get_combo_value(self.daily_hour_combo, 0))
            config["trigger"]["daily"]["hour_start"] = hour

        return config


