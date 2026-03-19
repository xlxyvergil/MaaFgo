from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QWidget


class HeightAnimator(QObject):
    """
    控件高度动画助手，通过调整 maximumHeight 实现展开/收起效果。
    """

    def __init__(self, target: QWidget, duration: int = 220, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._target = target
        self._animation = QPropertyAnimation(target, b"maximumHeight", self)
        self._animation.setDuration(duration)
        self._animation.setEasingCurve(QEasingCurve(QEasingCurve.Type.InOutCubic))
        self._animation.finished.connect(self._on_finished)
        self._expanding = False
        self._finish_callback: Optional[Callable[[], None]] = None

    def expand(self, on_finished: Optional[Callable[[], None]] = None):
        """播放展开动画。"""
        self._animation.stop()
        self._expanding = True
        self._ensure_visible()
        start_value = self._target.maximumHeight()
        end_value = self._content_height()
        self._animation.setStartValue(start_value)
        self._animation.setEndValue(end_value)
        self._finish_callback = on_finished
        self._animation.start()

    def collapse(self, on_finished: Optional[Callable[[], None]] = None):
        """播放收起动画。"""
        if not self._target.isVisible():
            # 如果已经不可见，直接调用回调
            if on_finished:
                on_finished()
            return

        self._animation.stop()
        self._expanding = False
        start_value = self._target.height() or self._content_height()
        self._animation.setStartValue(start_value)
        self._animation.setEndValue(0)
        self._finish_callback = on_finished
        self._animation.start()

    def _ensure_visible(self):
        if not self._target.isVisible():
            self._target.setVisible(True)
            self._target.setMaximumHeight(0)

    def _content_height(self) -> int:
        # 临时允许使用最大高度以便测量理想高度。
        self._target.setMaximumHeight(16777215)
        self._target.adjustSize()
        hinted = self._target.sizeHint().height()
        return max(hinted, 0)

    def _on_finished(self):
        if self._expanding:
            self._target.setMaximumHeight(16777215)
        else:
            # 收起动画完成：先确保 maximumHeight 为 0，然后延迟隐藏以避免布局抖动
            self._target.setMaximumHeight(0)
            # 延迟一帧设置 visible，避免布局同时处理两个变化导致抖动
            QTimer.singleShot(0, self._hide_target)

        callback = self._finish_callback
        self._finish_callback = None
        if callback:
            callback()
    
    def _hide_target(self):
        """延迟隐藏目标控件"""
        # 仅当动画不是展开状态时隐藏（避免在快速切换时隐藏正在展开的控件）
        if not self._expanding and self._target.maximumHeight() == 0:
            self._target.setVisible(False)


__all__ = ["HeightAnimator"]

