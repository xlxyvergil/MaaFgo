"""
图像检测模块 (从FGO-py fgoDetect.py提取并适配)
"""
import cv2
import numpy as np
import os


class DetectBase:
    """基础检测类"""
    
    def __init__(self, device):
        self.device = device
        self.im = None
        self.refresh()
    
    def refresh(self):
        """刷新截图"""
        self.im = self.device.screenshot()
        return self
    
    def _crop(self, rect):
        """裁剪区域"""
        return self.im[rect[1]:rect[3], rect[0]:rect[2]]
    
    def _loc(self, img, rect=(0, 0, 1280, 720)):
        """模板匹配定位"""
        if isinstance(img, tuple):
            img_data, mask = img
        else:
            img_data, mask = img, None
        result = cv2.matchTemplate(self._crop(rect), img_data, cv2.TM_SQDIFF_NORMED, mask=mask)
        return cv2.minMaxLoc(result)
    
    def _compare(self, img, rect=(0, 0, 1280, 720), threshold=0.05):
        """比较是否匹配"""
        return threshold > self._loc(img, rect)[0]
    
    def _find(self, img, rect=(0, 0, 1280, 720), threshold=0.05):
        """查找位置"""
        loc = self._loc(img, rect)
        if loc[0] < threshold:
            return (rect[0] + loc[2][0] + img[0].shape[1] // 2,
                    rect[1] + loc[2][1] + img[0].shape[0] // 2)
        return None


class Detect(DetectBase):
    """FGO-py检测类"""
    
    _img_cache = {}
    
    @classmethod
    def loadImage(cls, name):
        """加载图像"""
        if name not in cls._img_cache:
            base_path = os.path.join(os.path.dirname(__file__), 'images')
            paths = [
                os.path.join(base_path, f'{name}.png'),
                os.path.join(base_path, 'map', f'{name}.png'),
                os.path.join(base_path, 'map', 'entrance', f'{name}.png'),
            ]
            for path in paths:
                if os.path.exists(path):
                    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
                    if img is not None:
                        if img.shape[2] == 4 if len(img.shape) == 3 else False:
                            cls._img_cache[name] = (img[:, :, :3], img[:, :, 3])
                        else:
                            cls._img_cache[name] = (img, None)
                        break
        return cls._img_cache.get(name)
    
    def isMainInterface(self):
        """是否在主界面"""
        return self._compare(self.loadImage('menu'), (1104, 613, 1267, 676))
    
    def isQuestListBegin(self):
        """是否在关卡列表顶部"""
        return self._compare(self.loadImage('listbar'), (1258, 95, 1278, 115))
    
    def findChapter(self, chapter):
        """查找章节入口"""
        name = '-'.join(str(x) for x in chapter)
        img = self.loadImage(name)
        if img:
            return self._find(img, (640, 90, 1230, 600))
        return None
    
    def findMapCamera(self, chapter):
        """查找地图视角位置"""
        name = '-'.join(str(x) for x in chapter) if isinstance(chapter, tuple) else str(chapter)
        atlas = self.loadImage(f'atlas/{name}')
        if atlas is None:
            return np.array([640, 360])
        
        screen = self._crop((200, 200, 1080, 520))
        screen_small = cv2.resize(screen, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
        
        result = cv2.matchTemplate(atlas[0], screen_small, cv2.TM_SQDIFF_NORMED)
        loc = cv2.minMaxLoc(result)[2]
        
        return np.array(loc) / 0.3 + np.array([440, 160])
