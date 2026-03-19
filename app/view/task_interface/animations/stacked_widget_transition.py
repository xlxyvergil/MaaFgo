from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QParallelAnimationGroup,
    QRect,
)
from PySide6.QtWidgets import QWidget, QStackedWidget
from typing import Optional


class StackedWidgetTransitionAnimator(QObject):
    """QStackedWidget 切换时的过渡动画
    
    使用水平滑动效果：新页面从右侧滑入，旧页面向左侧滑出。
    """

    def __init__(
        self, 
        stacked_widget: QStackedWidget, 
        duration: int = 300,
        parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.stacked_widget = stacked_widget
        self.duration = duration
        self._current_widget: Optional[QWidget] = None
        self._next_index: int = -1
        self._original_geometries: dict[QWidget, QRect] = {}

    def setCurrentIndex(self, index: int, animated: bool = True):
        """切换到指定索引的页面
        
        Args:
            index: 目标页面索引
            animated: 是否使用动画
        """
        if index < 0 or index >= self.stacked_widget.count():
            return
        
        current_index = self.stacked_widget.currentIndex()
        if index == current_index:
            return
        
        if not animated:
            self.stacked_widget.setCurrentIndex(index)
            # 确保位置正确
            widget = self.stacked_widget.widget(index)
            if widget and widget in self._original_geometries:
                widget.setGeometry(self._original_geometries[widget])
            return
        
        # 获取当前和下一个 widget
        current_widget = self.stacked_widget.currentWidget()
        next_widget = self.stacked_widget.widget(index)
        
        if not current_widget or not next_widget:
            self.stacked_widget.setCurrentIndex(index)
            return
        
        self._current_widget = current_widget
        self._next_index = index
        
        # 确保两个 widget 都可见
        current_widget.show()
        next_widget.show()
        self.stacked_widget.setCurrentIndex(index)
        
        # 获取 stacked_widget 的几何形状作为基准
        stack_geo = self.stacked_widget.geometry()
        widget_width = stack_geo.width()
        widget_height = stack_geo.height()
        
        # QStackedWidget 中的 widget 位置应该相对于 stacked_widget，所以 x 和 y 应该是 0, 0
        # 保存或更新原始几何形状（widget 在 stacked_widget 中的位置是 0,0）
        if current_widget not in self._original_geometries:
            # widget 在 stacked_widget 中的位置是 0,0
            self._original_geometries[current_widget] = QRect(
                0, 0, widget_width, widget_height
            )
        if next_widget not in self._original_geometries:
            self._original_geometries[next_widget] = QRect(
                0, 0, widget_width, widget_height
            )
        
        # 更新几何形状以确保使用最新的尺寸（位置始终是 0,0）
        current_original_geo = QRect(
            0, 0, widget_width, widget_height
        )
        next_original_geo = QRect(
            0, 0, widget_width, widget_height
        )
        self._original_geometries[current_widget] = current_original_geo
        self._original_geometries[next_widget] = next_original_geo
        
        # 判断切换方向：index 增加表示向右切换（日志从右侧滑入），index 减少表示向左切换（日志向右滑出）
        is_forward = index > current_index  # 向前切换（0->1：选项到日志）
        
        # 设置初始位置和动画方向
        if is_forward:
            # 向前切换：当前 widget 向左滑出，下一个 widget 从右侧滑入
            current_widget.setGeometry(current_original_geo)
            next_widget.setGeometry(
                next_original_geo.x() + widget_width,
                next_original_geo.y(),
                widget_width,
                widget_height
            )
            
            current_end_x = current_original_geo.x() - widget_width
            next_start_x = next_original_geo.x() + widget_width
        else:
            # 向后切换：当前 widget 向右滑出，下一个 widget 从左侧滑入
            current_widget.setGeometry(current_original_geo)
            next_widget.setGeometry(
                next_original_geo.x() - widget_width,
                next_original_geo.y(),
                widget_width,
                widget_height
            )
            
            current_end_x = current_original_geo.x() + widget_width
            next_start_x = next_original_geo.x() - widget_width
        
        # 创建并行动画：同时滑动两个 widget
        parallel = QParallelAnimationGroup(self)
        
        # 当前 widget 滑出
        current_slide = QPropertyAnimation(current_widget, b"geometry", self)
        current_slide.setDuration(self.duration)
        current_slide.setStartValue(current_original_geo)
        current_slide.setEndValue(QRect(
            current_end_x,
            current_original_geo.y(),
            current_original_geo.width(),
            current_original_geo.height()
        ))
        current_slide.setEasingCurve(QEasingCurve.Type.InOutCubic)
        parallel.addAnimation(current_slide)
        
        # 下一个 widget 滑入
        next_slide = QPropertyAnimation(next_widget, b"geometry", self)
        next_slide.setDuration(self.duration)
        next_slide.setStartValue(QRect(
            next_start_x,
            next_original_geo.y(),
            next_original_geo.width(),
            next_original_geo.height()
        ))
        next_slide.setEndValue(next_original_geo)
        next_slide.setEasingCurve(QEasingCurve.Type.InOutCubic)
        parallel.addAnimation(next_slide)
        
        # 动画完成后清理
        parallel.finished.connect(self._on_animation_finished)
        
        parallel.start()

    def _on_animation_finished(self):
        """动画完成后的清理工作"""
        # 动画完成后，让布局系统重新管理 widget 的位置
        # 需要重置 widget 的位置，让布局系统重新计算
        if self._next_index >= 0:
            next_widget = self.stacked_widget.widget(self._next_index)
            if next_widget:
                # 重置位置，让布局系统重新管理
                # 使用 setGeometry(0, 0, width, height) 重置到正确位置
                stack_geo = self.stacked_widget.geometry()
                next_widget.setGeometry(0, 0, stack_geo.width(), stack_geo.height())
                # 触发布局更新
                next_widget.updateGeometry()
                self.stacked_widget.updateGeometry()
        
        if self._current_widget:
            # 重置位置，让布局系统重新管理
            stack_geo = self.stacked_widget.geometry()
            self._current_widget.setGeometry(0, 0, stack_geo.width(), stack_geo.height())
            # 触发布局更新
            self._current_widget.updateGeometry()
        
        self._current_widget = None
        self._next_index = -1

