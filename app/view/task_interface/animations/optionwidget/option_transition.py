from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QParallelAnimationGroup,
    QPoint,
)
from PySide6.QtWidgets import QWidget, QGraphicsOpacityEffect
from typing import Callable, Optional


class OptionTransitionAnimator(QObject):
    """Option 面板切换时的过渡动画

    使用淡出+向上滑动 -> 内容更新 -> 淡入+从上滑入的方式，提供流畅的过渡效果。
    """

    def __init__(
        self, target_widget: QWidget, duration: int = 180, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.target = target_widget
        self.duration = duration
        self._original_pos: QPoint | None = None
        self._slide_offset = 20  # 滑动距离

        # 配置透明度效果
        self._effect = QGraphicsOpacityEffect(self.target)
        self.target.setGraphicsEffect(self._effect)
        self._effect.setOpacity(1.0)

        # ===== 出场动画组 =====
        self._fade_out_group = QParallelAnimationGroup(self)

        # 淡出动画
        self._opacity_out = QPropertyAnimation(self._effect, b"opacity", self)
        self._opacity_out.setDuration(self.duration)
        self._opacity_out.setStartValue(1.0)
        self._opacity_out.setEndValue(0.0)
        self._opacity_out.setEasingCurve(QEasingCurve.Type.OutQuad)

        # 向上滑动动画
        self._slide_out = QPropertyAnimation(self.target, b"pos", self)
        self._slide_out.setDuration(self.duration)
        self._slide_out.setEasingCurve(QEasingCurve.Type.OutQuad)

        self._fade_out_group.addAnimation(self._opacity_out)
        self._fade_out_group.addAnimation(self._slide_out)

        # ===== 入场动画组 =====
        self._fade_in_group = QParallelAnimationGroup(self)

        # 淡入动画
        self._opacity_in = QPropertyAnimation(self._effect, b"opacity", self)
        self._opacity_in.setDuration(self.duration)
        self._opacity_in.setStartValue(0.0)
        self._opacity_in.setEndValue(1.0)
        self._opacity_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        # 从上方滑入动画
        self._slide_in = QPropertyAnimation(self.target, b"pos", self)
        self._slide_in.setDuration(self.duration)
        self._slide_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade_in_group.addAnimation(self._opacity_in)
        self._fade_in_group.addAnimation(self._slide_in)

        self._pending_update: Optional[Callable[[], None]] = None

        # 串联动画：淡出完成后执行更新，再淡入
        self._fade_out_group.finished.connect(self._on_fade_out_finished)

    def _on_fade_out_finished(self):
        # 恢复到原始位置（准备入场动画）
        if self._original_pos is not None:
            self.target.move(self._original_pos)

        # 执行等待的更新动作（清空/填充等）
        if self._pending_update:
            try:
                self._pending_update()
            finally:
                self._pending_update = None

        # 设置入场动画的起始和结束位置
        if self._original_pos is not None:
            start_pos = QPoint(
                self._original_pos.x(), self._original_pos.y() - self._slide_offset
            )
            self._slide_in.setStartValue(start_pos)
            self._slide_in.setEndValue(self._original_pos)

        # 开始淡入
        self._fade_in_group.start()

    def play(self, update_callable: Optional[Callable[[], None]] = None):
        """开始一次过渡。

        Args:
            update_callable: 在淡出完成后调用的函数（通常用于清空旧内容并填充新内容）。
        """
        self._pending_update = update_callable

        # 如果当前仍在动画中，先停止以避免叠加
        if self._fade_out_group.state() == QParallelAnimationGroup.State.Running:
            self._fade_out_group.stop()
        if self._fade_in_group.state() == QParallelAnimationGroup.State.Running:
            self._fade_in_group.stop()

        # 记录原始位置
        self._original_pos = self.target.pos()

        # 设置出场动画的起始和结束位置
        end_pos = QPoint(
            self._original_pos.x(), self._original_pos.y() - self._slide_offset
        )
        self._slide_out.setStartValue(self._original_pos)
        self._slide_out.setEndValue(end_pos)

        # 确保从当前透明度开始
        current_opacity = self._effect.opacity()
        self._opacity_out.setStartValue(current_opacity)

        self._fade_out_group.start()

