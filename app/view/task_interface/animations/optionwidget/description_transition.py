from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QVariantAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QSplitter, QWidget


class DescriptionTransitionAnimator(QObject):
    """控制公告区域展开/收起的大小 + 透明度过渡动画。"""

    def __init__(
        self,
        splitter: QSplitter,
        target_widget: QWidget,
        content_widget: QWidget | None = None,
        duration: int = 220,
        max_ratio: float = 0.5,
        min_height: int = 90,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.splitter = splitter
        self.target_widget = target_widget
        self.content_widget = content_widget  # 用于获取内容实际高度
        self.duration = duration
        self.max_ratio = max_ratio  # 最大占比
        self.min_height = min_height

        self._opacity_effect = QGraphicsOpacityEffect(self.target_widget)
        self.target_widget.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(1.0)

        self._opacity_animation = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._opacity_animation.setDuration(self.duration)
        self._opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._size_animation: QVariantAnimation | None = None
        self._is_expanded = self.target_widget.isVisible()
        self._animation_total = max(self.splitter.height(), 1)

    def set_max_ratio(self, ratio: float) -> None:
        """动态设置最大占比"""
        self.max_ratio = max(0.0, min(1.0, ratio))

    def set_content_widget(self, widget: QWidget | None) -> None:
        """设置内容控件，用于获取内容实际高度"""
        self.content_widget = widget

    def expand(self, force_from_zero: bool = False):
        """展开公告区域
        
        :param force_from_zero: 是否强制从0开始动画（用于首次展开或比例变化较大时）
        """
        if self._is_expanded and not self._is_animating() and not force_from_zero:
            return
        self._is_expanded = True
        self.target_widget.show()
        self._animation_total = self._measure_total_height()
        target_size = self._calculate_target_height(self._animation_total)
        
        # 获取当前大小
        current_size = self._current_description_size()
        
        # 如果强制从零开始，或者当前大小异常地大于目标（可能是splitter自动调整导致的）
        # 则强制从一个较小的起始值开始动画
        if force_from_zero or current_size >= target_size:
            # 从最小高度或0开始
            start_size = 0
            # 先立即设置到起始位置
            self._apply_splitter_sizes(start_size)
        else:
            start_size = current_size
        
        self._play_size_animation(start_size, target_size, self._on_expand_finished)
        # 透明度从当前值开始，如果强制从零则从0开始
        current_opacity = self._opacity_effect.opacity() if not force_from_zero else 0.0
        self._play_opacity_animation(current_opacity, 1.0)

    def update_size(self, force_animation: bool = False):
        """当内容改变但已经展开时，平滑过渡到新的高度（不收回）
        
        :param force_animation: 是否强制播放动画，即使大小相同
        """
        if not self._is_expanded:
            return
        self._animation_total = self._measure_total_height()
        current_size = self._current_description_size()
        target_size = self._calculate_target_height(self._animation_total)
        
        # 如果当前大小异常地大于目标（可能是splitter自动调整导致的）
        # 先重置到一个合理的起始位置
        if current_size > target_size and force_animation:
            # 从旧的目标位置开始（假设之前的max_ratio是0.5）
            old_max_size = int(self._animation_total * 0.5)
            start_size = min(current_size, old_max_size) if old_max_size > 0 else self.min_height
            self._apply_splitter_sizes(start_size)
            current_size = start_size
        
        if current_size != target_size or force_animation:
            self._play_size_animation(current_size, target_size, self._on_expand_finished)

    def collapse(self):
        if not self._is_expanded and not self._is_animating():
            return
        self._is_expanded = False
        start_size = self._current_description_size()
        self._animation_total = self._measure_total_height()
        self._play_size_animation(start_size, 0, self._on_collapse_finished)
        # 透明度从当前值开始
        current_opacity = self._opacity_effect.opacity()
        self._play_opacity_animation(current_opacity, 0.0)

    def toggle(self, visible: bool | None = None):
        if visible is None:
            visible = not self._is_expanded
        if visible:
            self.expand()
        else:
            self.collapse()

    def is_expanded(self) -> bool:
        return self._is_expanded

    def set_visible_immediate(self, visible: bool):
        self._is_expanded = visible
        self._animation_total = self._measure_total_height()
        if visible:
            self.target_widget.show()
            size = self._calculate_target_height(self._animation_total)
            self.splitter.setSizes([max(self._animation_total - size, 0), size])
        else:
            self.target_widget.hide()
            self.splitter.setSizes([self._animation_total, 0])
        self._opacity_effect.setOpacity(1.0)

    def _is_animating(self) -> bool:
        return bool(
            self._size_animation and self._size_animation.state() == QVariantAnimation.State.Running
        ) or self._opacity_animation.state() == QPropertyAnimation.State.Running

    def _play_size_animation(self, start: int, end: int, finished: Callable[[], None]):
        if self._size_animation:
            self._size_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(self.duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._apply_splitter_sizes)
        animation.finished.connect(finished)
        animation.start()
        self._size_animation = animation

    def _apply_splitter_sizes(self, value):
        desc_height = max(int(value), 0)
        other_height = max(self._animation_total - desc_height, 0)
        self.splitter.setSizes([other_height, desc_height])

    def _play_opacity_animation(self, start: float, end: float):
        if self._opacity_animation.state() == QPropertyAnimation.State.Running:
            self._opacity_animation.stop()
        self._opacity_animation.setStartValue(start)
        self._opacity_animation.setEndValue(end)
        self._opacity_animation.start()

    def _on_expand_finished(self):
        if self._size_animation:
            final = int(self._size_animation.endValue())
            self._apply_splitter_sizes(final)
        self._opacity_effect.setOpacity(1.0)

    def _on_collapse_finished(self):
        self.target_widget.hide()
        self.splitter.setSizes([self._animation_total, 0])
        self._opacity_effect.setOpacity(1.0)

    def _calculate_target_height(self, total_height: int) -> int:
        """根据内容实际高度计算目标高度，受最大比例限制"""
        # 获取内容实际需要的高度
        if self.content_widget:
            # 获取父容器的可用宽度来计算文本换行后的实际高度
            parent_width = self.splitter.width()
            if parent_width <= 0:
                parent_width = 400  # 默认宽度
            
            # 减去边距（左右各10px + 滚动条宽度约20px）
            available_width = parent_width - 40
            
            # 使用 heightForWidth 获取给定宽度下文本的实际高度
            # 这个方法会考虑换行，比 sizeHint 更准确
            content_height = self.content_widget.heightForWidth(available_width)
            
            # 如果 heightForWidth 返回 -1（不支持），回退到 sizeHint
            if content_height <= 0:
                content_height = self.content_widget.sizeHint().height()
            
            # 加上额外边距（标题约30px + 内边距上下各10px + 卡片边距等）
            extra_margin = 70
            desired_height = content_height + extra_margin
        else:
            # 如果没有内容控件，使用目标控件的 sizeHint
            desired_height = self.target_widget.sizeHint().height()
        
        # 计算最大允许高度（按比例限制）
        max_height = int(total_height * self.max_ratio)
        
        # 目标高度：内容实际需要的高度，但不超过最大限制，且不小于最小高度
        target = max(self.min_height, min(desired_height, max_height))
        return min(target, total_height)

    def _measure_total_height(self) -> int:
        height = max(self.splitter.height(), 1)
        sizes = self.splitter.sizes()
        if height <= 0 and sizes:
            height = sum(sizes)
        return max(height, 1)

    def _current_description_size(self) -> int:
        sizes = self.splitter.sizes()
        return sizes[1] if len(sizes) > 1 else 0

