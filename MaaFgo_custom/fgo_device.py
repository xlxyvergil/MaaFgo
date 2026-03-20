"""
FGO-py 设备接口适配器
将MAA的controller包装为FGO-py的device接口
"""
import cv2
import numpy as np
from maa.context import Context


class FgoDevice:
    """FGO-py设备接口，包装MAA controller"""
    
    def __init__(self, context: Context):
        self.context = context
        self.controller = context.tasker.controller
    
    def screenshot(self):
        """截图并转为numpy数组 (BGR格式，与FGO-py一致)"""
        image = self.controller.post_screencap().wait().get()
        img_array = np.array(image)
        # RGB -> BGR
        return cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    
    def touch(self, pos, delay=0):
        """点击坐标"""
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
            self.controller.post_click(x, y).wait()
        elif isinstance(pos, list):
            # 多个坐标，取第一个
            x, y = int(pos[0][0]), int(pos[0][1])
            self.controller.post_click(x, y).wait()
    
    def swipe(self, start, end, duration=500):
        """滑动"""
        x1, y1 = int(start[0]), int(start[1])
        x2, y2 = int(end[0]), int(end[1])
        self.controller.post_swipe(x1, y1, x2, y2, duration).wait()
    
    def press(self, key):
        """按键"""
        # 键盘映射 (FGO-py使用的特殊编码)
        key_map = {
            ' ': 32,      # 空格
            '\x08': 8,    # Backspace
            '\xBB': 187,  # =
            '\xBF': 191,  # /
            '\x25': 37,   # 左箭头
            '\x26': 38,   # 上箭头
            '\x27': 39,   # 右箭头
            '\x28': 40,   # 下箭头
            '\x67': 103,  # g
            '\x68': 104,  # h
            '\x69': 105,  # i
            '\x64': 100,  # d
            '\x65': 101,  # e
            '\x66': 102,  # f
        }
        key_code = key_map.get(key, ord(key) if len(key) == 1 else None)
        if key_code:
            self.controller.post_key(key_code).wait()
    
    def perform(self, actions, delay=0):
        """执行一系列操作 (FGO-py兼容)"""
        for action in actions:
            if isinstance(action, str):
                # 按键
                self.press(action)
            elif isinstance(action, (list, tuple)) and len(action) == 2:
                # 点击
                self.touch(action)
