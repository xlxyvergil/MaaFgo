import win32gui
import win32api
import win32con
import numpy as np
from PIL import Image
from maa.controller import CustomController
from maa.define import MaaControllerFeatureEnum


class FullWindowWin32Controller(CustomController):
    """自定义 Win32 控制器，捕获完整窗口（包括标题栏）"""

    def __init__(self, hWnd):
        """初始化控制器
        
        Args:
            hWnd: 窗口句柄
        """
        self.hWnd = hWnd
        self.uuid = f"full_window_win32_{hWnd}"
        super().__init__()

    def connect(self) -> bool:
        """连接控制器"""
        return self.hWnd is not None

    def request_uuid(self) -> str:
        """获取设备 UUID"""
        return self.uuid

    def start_app(self, intent: str) -> bool:
        """启动应用"""
        # 这里可以根据需要实现
        return True

    def stop_app(self, intent: str) -> bool:
        """停止应用"""
        # 这里可以根据需要实现
        return True

    def screencap(self) -> np.ndarray:
        """捕获完整窗口截图（包括标题栏）"""
        if not self.hWnd:
            return np.array([])

        # 获取窗口的完整区域
        rect = win32gui.GetWindowRect(self.hWnd)
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top

        if width <= 0 or height <= 0:
            return np.array([])

        # 获取窗口 DC
        hwndDC = win32gui.GetWindowDC(self.hWnd)
        saveDC = win32gui.CreateCompatibleDC(hwndDC)

        # 创建位图
        saveBitMap = win32gui.CreateCompatibleBitmap(hwndDC, width, height)
        win32gui.SelectObject(saveDC, saveBitMap)

        # 使用 BitBlt 捕获窗口
        win32gui.BitBlt(saveDC, 0, 0, width, height, hwndDC, 0, 0, win32con.SRCCOPY)

        # 将位图转换为 numpy 数组
        bmpinfo = saveBitMap.GetInfo()
        bmpstr = saveBitMap.GetBitmapBits(True)
        im = Image.frombuffer(
            'RGB',
            (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
            bmpstr,
            'raw',
            'BGRX',
            0,
            1
        )

        # 清理资源
        win32gui.DeleteObject(saveBitMap.GetHandle())
        win32gui.DeleteDC(saveDC)
        win32gui.ReleaseDC(self.hWnd, hwndDC)

        # 转换为 BGR 格式
        return np.array(im)[:, :, ::-1]

    def click(self, x: int, y: int) -> bool:
        """点击窗口相对坐标"""
        if not self.hWnd:
            return False

        # 获取窗口位置
        rect = win32gui.GetWindowRect(self.hWnd)
        left, top, _, _ = rect

        # 计算绝对坐标
        absolute_x = left + x
        absolute_y = top + y

        # 执行点击
        win32api.SetCursorPos((absolute_x, absolute_y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        return True

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
        """滑动窗口相对坐标"""
        if not self.hWnd:
            return False

        # 获取窗口位置
        rect = win32gui.GetWindowRect(self.hWnd)
        left, top, _, _ = rect

        # 计算绝对坐标
        start_x = left + x1
        start_y = top + y1
        end_x = left + x2
        end_y = top + y2

        # 执行滑动
        win32api.SetCursorPos((start_x, start_y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        
        # 简单实现，实际可以根据 duration 来控制滑动速度
        win32api.SetCursorPos((end_x, end_y))
        
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        return True

    def touch_down(self, contact: int, x: int, y: int, pressure: int) -> bool:
        """触摸按下"""
        return self.click(x, y)

    def touch_move(self, contact: int, x: int, y: int, pressure: int) -> bool:
        """触摸移动"""
        if not self.hWnd:
            return False

        # 获取窗口位置
        rect = win32gui.GetWindowRect(self.hWnd)
        left, top, _, _ = rect

        # 计算绝对坐标
        absolute_x = left + x
        absolute_y = top + y

        # 移动鼠标
        win32api.SetCursorPos((absolute_x, absolute_y))
        return True

    def touch_up(self, contact: int) -> bool:
        """触摸抬起"""
        return True

    def click_key(self, keycode: int) -> bool:
        """点击按键"""
        win32api.keybd_event(keycode, 0, 0, 0)
        win32api.keybd_event(keycode, 0, win32con.KEYEVENTF_KEYUP, 0)
        return True

    def input_text(self, text: str) -> bool:
        """输入文本"""
        for char in text:
            win32api.keybd_event(ord(char), 0, 0, 0)
            win32api.keybd_event(ord(char), 0, win32con.KEYEVENTF_KEYUP, 0)
        return True

    def key_down(self, keycode: int) -> bool:
        """按下按键"""
        win32api.keybd_event(keycode, 0, 0, 0)
        return True

    def key_up(self, keycode: int) -> bool:
        """抬起按键"""
        win32api.keybd_event(keycode, 0, win32con.KEYEVENTF_KEYUP, 0)
        return True

    def scroll(self, dx: int, dy: int) -> bool:
        """滚动"""
        # 实现鼠标滚轮滚动
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, dy)
        return True
