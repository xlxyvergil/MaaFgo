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
        self._uuid = f"full_window_win32_{hWnd}"
        self._handle = None
        self._own = True
        super().__init__()
    
    def connect(self) -> bool:
        """连接控制器"""
        return self.hWnd is not None

    def request_uuid(self) -> str:
        """获取设备 UUID"""
        return self._uuid

    def start_app(self, intent: str) -> bool:
        """启动应用"""
        # 这里可以根据需要实现
        return True

    def stop_app(self, intent: str) -> bool:
        """停止应用"""
        # 这里可以根据需要实现
        return True

    def screencap(self) -> np.ndarray:
        """捕获完整窗口截图（包括标题栏），使用 BitBlt 方法"""
        if not self.hWnd:
            return np.array([])

        # 获取窗口的完整区域
        rect = win32gui.GetWindowRect(self.hWnd)
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top

        print(f"[FullWindowWin32] 窗口区域: left={left}, top={top}, right={right}, bottom={bottom}, width={width}, height={height}")

        if width <= 0 or height <= 0:
            return np.array([])

        # 获取窗口 DC
        hwndDC = win32gui.GetWindowDC(self.hWnd)
        saveDC = win32gui.CreateCompatibleDC(hwndDC)

        # 创建位图
        saveBitMap = win32gui.CreateCompatibleBitmap(hwndDC, width, height)
        win32gui.SelectObject(saveDC, saveBitMap)

        # 使用 BitBlt 捕获窗口
        import win32ui
        success = win32gui.BitBlt(saveDC, 0, 0, width, height, hwndDC, 0, 0, win32con.SRCCOPY)
        print(f"[FullWindowWin32] BitBlt 成功: {success}")

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

        print(f"[FullWindowWin32] 捕获的图像尺寸: {im.size}")

        # 清理资源
        win32gui.DeleteObject(saveBitMap.GetHandle())
        win32gui.DeleteDC(saveDC)
        win32gui.ReleaseDC(self.hWnd, hwndDC)

        # 转换为 BGR 格式
        result = np.array(im)[:, :, ::-1]
        print(f"[FullWindowWin32] 返回的数组形状: {result.shape}")
        return result

    def click(self, x: int, y: int) -> bool:
        """点击窗口相对坐标，使用 seize 模式"""
        if not self.hWnd:
            return False

        # 获取窗口位置
        rect = win32gui.GetWindowRect(self.hWnd)
        left, top, _, _ = rect

        # 计算绝对坐标
        absolute_x = left + x
        absolute_y = top + y

        # 使用 SendInput 发送输入事件（seize 模式）
        import ctypes
        from ctypes import wintypes

        # 定义 INPUT 结构
        class INPUT(ctypes.Structure):
            _fields_ = [
                ('type', wintypes.DWORD),
                ('union', ctypes.Union(
                    _fields_=[
                        ('mi', ctypes.Structure(
                            _fields_=[
                                ('dx', wintypes.LONG),
                                ('dy', wintypes.LONG),
                                ('mouseData', wintypes.DWORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('ki', ctypes.Structure(
                            _fields_=[
                                ('wVk', wintypes.WORD),
                                ('wScan', wintypes.WORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('hi', ctypes.Structure(
                            _fields_=[
                                ('uMsg', wintypes.DWORD),
                                ('wParamL', wintypes.WORD),
                                ('wParamH', wintypes.WORD)
                            ]
                        ))
                    ]
                ))
            ]

        # 设置鼠标位置
        ctypes.windll.user32.SetCursorPos(absolute_x, absolute_y)

        # 发送鼠标左键按下事件
        inputs = (INPUT * 2)()
        
        # 鼠标左键按下
        inputs[0].type = 0  # INPUT_MOUSE
        inputs[0].union.mi.dwFlags = 0x0002  # MOUSEEVENTF_LEFTDOWN
        
        # 鼠标左键抬起
        inputs[1].type = 0  # INPUT_MOUSE
        inputs[1].union.mi.dwFlags = 0x0004  # MOUSEEVENTF_LEFTUP

        ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        return True

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
        """滑动窗口相对坐标，使用 seize 模式"""
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

        # 使用 SendInput 发送输入事件（seize 模式）
        import ctypes
        from ctypes import wintypes

        # 定义 INPUT 结构
        class INPUT(ctypes.Structure):
            _fields_ = [
                ('type', wintypes.DWORD),
                ('union', ctypes.Union(
                    _fields_=[
                        ('mi', ctypes.Structure(
                            _fields_=[
                                ('dx', wintypes.LONG),
                                ('dy', wintypes.LONG),
                                ('mouseData', wintypes.DWORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('ki', ctypes.Structure(
                            _fields_=[
                                ('wVk', wintypes.WORD),
                                ('wScan', wintypes.WORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('hi', ctypes.Structure(
                            _fields_=[
                                ('uMsg', wintypes.DWORD),
                                ('wParamL', wintypes.WORD),
                                ('wParamH', wintypes.WORD)
                            ]
                        ))
                    ]
                ))
            ]

        # 设置鼠标位置到起点
        ctypes.windll.user32.SetCursorPos(start_x, start_y)

        # 发送鼠标左键按下事件
        inputs = (INPUT * 3)()
        
        # 鼠标左键按下
        inputs[0].type = 0  # INPUT_MOUSE
        inputs[0].union.mi.dwFlags = 0x0002  # MOUSEEVENTF_LEFTDOWN
        
        # 鼠标移动到终点
        inputs[1].type = 0  # INPUT_MOUSE
        inputs[1].union.mi.dx = end_x - start_x
        inputs[1].union.mi.dy = end_y - start_y
        inputs[1].union.mi.dwFlags = 0x0001  # MOUSEEVENTF_MOVE
        
        # 鼠标左键抬起
        inputs[2].type = 0  # INPUT_MOUSE
        inputs[2].union.mi.dwFlags = 0x0004  # MOUSEEVENTF_LEFTUP

        ctypes.windll.user32.SendInput(3, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        return True

    def touch_down(self, contact: int, x: int, y: int, pressure: int) -> bool:
        """触摸按下，使用 seize 模式"""
        return self.click(x, y)

    def touch_move(self, contact: int, x: int, y: int, pressure: int) -> bool:
        """触摸移动，使用 seize 模式"""
        if not self.hWnd:
            return False

        # 获取窗口位置
        rect = win32gui.GetWindowRect(self.hWnd)
        left, top, _, _ = rect

        # 计算绝对坐标
        absolute_x = left + x
        absolute_y = top + y

        # 使用 SendInput 发送输入事件（seize 模式）
        import ctypes
        from ctypes import wintypes

        # 定义 INPUT 结构
        class INPUT(ctypes.Structure):
            _fields_ = [
                ('type', wintypes.DWORD),
                ('union', ctypes.Union(
                    _fields_=[
                        ('mi', ctypes.Structure(
                            _fields_=[
                                ('dx', wintypes.LONG),
                                ('dy', wintypes.LONG),
                                ('mouseData', wintypes.DWORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('ki', ctypes.Structure(
                            _fields_=[
                                ('wVk', wintypes.WORD),
                                ('wScan', wintypes.WORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('hi', ctypes.Structure(
                            _fields_=[
                                ('uMsg', wintypes.DWORD),
                                ('wParamL', wintypes.WORD),
                                ('wParamH', wintypes.WORD)
                            ]
                        ))
                    ]
                ))
            ]

        # 设置鼠标位置
        ctypes.windll.user32.SetCursorPos(absolute_x, absolute_y)
        return True

    def touch_up(self, contact: int) -> bool:
        """触摸抬起"""
        return True

    def click_key(self, keycode: int) -> bool:
        """点击按键，使用 seize 模式"""
        import ctypes
        from ctypes import wintypes

        # 定义 INPUT 结构
        class INPUT(ctypes.Structure):
            _fields_ = [
                ('type', wintypes.DWORD),
                ('union', ctypes.Union(
                    _fields_=[
                        ('mi', ctypes.Structure(
                            _fields_=[
                                ('dx', wintypes.LONG),
                                ('dy', wintypes.LONG),
                                ('mouseData', wintypes.DWORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('ki', ctypes.Structure(
                            _fields_=[
                                ('wVk', wintypes.WORD),
                                ('wScan', wintypes.WORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('hi', ctypes.Structure(
                            _fields_=[
                                ('uMsg', wintypes.DWORD),
                                ('wParamL', wintypes.WORD),
                                ('wParamH', wintypes.WORD)
                            ]
                        ))
                    ]
                ))
            ]

        # 发送键盘按下和抬起事件
        inputs = (INPUT * 2)()
        
        # 键盘按下
        inputs[0].type = 1  # INPUT_KEYBOARD
        inputs[0].union.ki.wVk = keycode
        inputs[0].union.ki.dwFlags = 0  # 0 for key down
        
        # 键盘抬起
        inputs[1].type = 1  # INPUT_KEYBOARD
        inputs[1].union.ki.wVk = keycode
        inputs[1].union.ki.dwFlags = 0x0002  # KEYEVENTF_KEYUP

        ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        return True

    def input_text(self, text: str) -> bool:
        """输入文本，使用 seize 模式"""
        import ctypes
        from ctypes import wintypes

        # 定义 INPUT 结构
        class INPUT(ctypes.Structure):
            _fields_ = [
                ('type', wintypes.DWORD),
                ('union', ctypes.Union(
                    _fields_=[
                        ('mi', ctypes.Structure(
                            _fields_=[
                                ('dx', wintypes.LONG),
                                ('dy', wintypes.LONG),
                                ('mouseData', wintypes.DWORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('ki', ctypes.Structure(
                            _fields_=[
                                ('wVk', wintypes.WORD),
                                ('wScan', wintypes.WORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('hi', ctypes.Structure(
                            _fields_=[
                                ('uMsg', wintypes.DWORD),
                                ('wParamL', wintypes.WORD),
                                ('wParamH', wintypes.WORD)
                            ]
                        ))
                    ]
                ))
            ]

        for char in text:
            # 发送键盘按下和抬起事件
            inputs = (INPUT * 2)()
            
            # 键盘按下
            inputs[0].type = 1  # INPUT_KEYBOARD
            inputs[0].union.ki.wVk = ord(char)
            inputs[0].union.ki.dwFlags = 0  # 0 for key down
            
            # 键盘抬起
            inputs[1].type = 1  # INPUT_KEYBOARD
            inputs[1].union.ki.wVk = ord(char)
            inputs[1].union.ki.dwFlags = 0x0002  # KEYEVENTF_KEYUP

            ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        return True

    def key_down(self, keycode: int) -> bool:
        """按下按键，使用 seize 模式"""
        import ctypes
        from ctypes import wintypes

        # 定义 INPUT 结构
        class INPUT(ctypes.Structure):
            _fields_ = [
                ('type', wintypes.DWORD),
                ('union', ctypes.Union(
                    _fields_=[
                        ('mi', ctypes.Structure(
                            _fields_=[
                                ('dx', wintypes.LONG),
                                ('dy', wintypes.LONG),
                                ('mouseData', wintypes.DWORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('ki', ctypes.Structure(
                            _fields_=[
                                ('wVk', wintypes.WORD),
                                ('wScan', wintypes.WORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('hi', ctypes.Structure(
                            _fields_=[
                                ('uMsg', wintypes.DWORD),
                                ('wParamL', wintypes.WORD),
                                ('wParamH', wintypes.WORD)
                            ]
                        ))
                    ]
                ))
            ]

        # 发送键盘按下事件
        inputs = (INPUT * 1)()
        inputs[0].type = 1  # INPUT_KEYBOARD
        inputs[0].union.ki.wVk = keycode
        inputs[0].union.ki.dwFlags = 0  # 0 for key down

        ctypes.windll.user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        return True

    def key_up(self, keycode: int) -> bool:
        """抬起按键，使用 seize 模式"""
        import ctypes
        from ctypes import wintypes

        # 定义 INPUT 结构
        class INPUT(ctypes.Structure):
            _fields_ = [
                ('type', wintypes.DWORD),
                ('union', ctypes.Union(
                    _fields_=[
                        ('mi', ctypes.Structure(
                            _fields_=[
                                ('dx', wintypes.LONG),
                                ('dy', wintypes.LONG),
                                ('mouseData', wintypes.DWORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('ki', ctypes.Structure(
                            _fields_=[
                                ('wVk', wintypes.WORD),
                                ('wScan', wintypes.WORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('hi', ctypes.Structure(
                            _fields_=[
                                ('uMsg', wintypes.DWORD),
                                ('wParamL', wintypes.WORD),
                                ('wParamH', wintypes.WORD)
                            ]
                        ))
                    ]
                ))
            ]

        # 发送键盘抬起事件
        inputs = (INPUT * 1)()
        inputs[0].type = 1  # INPUT_KEYBOARD
        inputs[0].union.ki.wVk = keycode
        inputs[0].union.ki.dwFlags = 0x0002  # KEYEVENTF_KEYUP

        ctypes.windll.user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        return True

    def scroll(self, dx: int, dy: int) -> bool:
        """滚动，使用 seize 模式"""
        import ctypes
        from ctypes import wintypes

        # 定义 INPUT 结构
        class INPUT(ctypes.Structure):
            _fields_ = [
                ('type', wintypes.DWORD),
                ('union', ctypes.Union(
                    _fields_=[
                        ('mi', ctypes.Structure(
                            _fields_=[
                                ('dx', wintypes.LONG),
                                ('dy', wintypes.LONG),
                                ('mouseData', wintypes.DWORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('ki', ctypes.Structure(
                            _fields_=[
                                ('wVk', wintypes.WORD),
                                ('wScan', wintypes.WORD),
                                ('dwFlags', wintypes.DWORD),
                                ('time', wintypes.DWORD),
                                ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
                            ]
                        )),
                        ('hi', ctypes.Structure(
                            _fields_=[
                                ('uMsg', wintypes.DWORD),
                                ('wParamL', wintypes.WORD),
                                ('wParamH', wintypes.WORD)
                            ]
                        ))
                    ]
                ))
            ]

        # 发送鼠标滚轮事件
        inputs = (INPUT * 1)()
        inputs[0].type = 0  # INPUT_MOUSE
        inputs[0].union.mi.dwFlags = 0x0800  # MOUSEEVENTF_WHEEL
        inputs[0].union.mi.mouseData = dy * 120  # 滚动距离

        ctypes.windll.user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        return True
