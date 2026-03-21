import json
import os
import time
import win32gui
import win32con
import win32api
import subprocess
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
from maa.controller import Win32Controller
from maa.toolkit import Toolkit
from maa.define import MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum


def _parse_single_param(argv: CustomAction.RunArg) -> str:
    """解析单个参数值，去掉可能的引号"""
    param = argv.custom_action_param if argv.custom_action_param else ""
    param = param.strip()
    # 循环去除多层引号
    while len(param) >= 2:
        if (param.startswith('"') and param.endswith('"')):
            param = param[1:-1].strip()
        elif (param.startswith("'") and param.endswith("'")):
            param = param[1:-1].strip()
        else:
            break
    return param


# 全局变量存储BBC窗口句柄和控制器
_bbc_hwnd = None
_bbc_controller = None

# 固定BBC路径
BBC_PATH = "./BBC/BBchannel"


def _get_scripts_settings_path() -> str:
    return os.path.join(BBC_PATH, 'scripts_settings.json')


def _load_scripts_settings() -> dict:
    path = _get_scripts_settings_path()
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_scripts_settings(settings: dict) -> None:
    path = _get_scripts_settings_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ==================== Action 1: 设置BBC配置 ====================
@AgentServer.custom_action("setup_bbc_config")
class SetupBbcConfig(CustomAction):
    """设置BBC队伍配置 - 处理 bbc_team_config"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        print(f"[1/6] SetupBbcConfig: custom_action_param = {repr(argv.custom_action_param)}")
        
        bbc_team_config = _parse_single_param(argv)
        
        if not bbc_team_config:
            print("错误：未提供队伍配置文件路径")
            return CustomAction.RunResult(success=False)
        
        print(f"SetupBbcConfig: team_config={bbc_team_config}")
        
        settings_dir = os.path.join(BBC_PATH, 'settings')
        
        # 读取队伍配置
        if os.path.isabs(bbc_team_config) or bbc_team_config.startswith('./') or bbc_team_config.startswith('../'):
            team_config_path = bbc_team_config
            if not team_config_path.endswith('.json'):
                team_config_path += '.json'
        else:
            team_config_path = os.path.join(settings_dir, f"{bbc_team_config}.json")
        
        if not os.path.exists(team_config_path):
            print(f"队伍配置文件不存在: {team_config_path}")
            return CustomAction.RunResult(success=False)
        
        with open(team_config_path, 'r', encoding='utf-8') as f:
            team_config = json.load(f)
        
        # 保存连接设置
        connect_settings = {}
        scripts_settings = _load_scripts_settings()
        for key in ["connectMode", "snapshotDevice", "operateDevice"]:
            if key in scripts_settings:
                connect_settings[key] = scripts_settings[key]
        
        # 替换配置并恢复连接设置
        scripts_settings = team_config
        scripts_settings.update(connect_settings)
        
        _save_scripts_settings(scripts_settings)
        print(f"SetupBbcConfig: 配置已保存")
        return True


# ==================== Action 2: 执行BBC任务（整合版）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC任务 - 整合运行次数、苹果类型、启动、初始化和监控"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        global _bbc_hwnd, _bbc_controller
        
        # 解析参数: run_count|apple_type
        param_str = _parse_single_param(argv) or "1|gold"
        parts = param_str.split("|")
        run_count = int(parts[0]) if parts[0].isdigit() else 1
        apple_type = parts[1] if len(parts) > 1 else "gold"
        
        print(f"[ExecuteBbcTask] run_count={run_count}, apple_type={apple_type}")
        
        # 1. 保存运行次数和苹果类型到配置
        scripts_settings = _load_scripts_settings()
        scripts_settings["bbc_run_count"] = run_count
        scripts_settings["apple_type"] = apple_type
        _save_scripts_settings(scripts_settings)
        print("[1/5] 配置已保存")
        
        # 2. 启动BBC进程
        bbc_exe_path = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
        if not os.path.exists(bbc_exe_path):
            print(f"BBC可执行文件不存在: {bbc_exe_path}")
            return CustomAction.RunResult(success=False)
        
        os.startfile(bbc_exe_path)
        print("[2/5] BBC进程已启动，等待窗口...")
        time.sleep(3)
        
        # 强制BBC窗口置顶
        import win32gui
        import win32con
        
        def find_and_focus_bbc():
            # 查找BBC窗口
            bbc_hwnd = None
            windows = Toolkit.find_desktop_windows()
            for w in windows:
                if "BBchannel" in w.window_name:
                    bbc_hwnd = w.hwnd
                    break
            
            if bbc_hwnd:
                # 强制置顶
                win32gui.SetWindowPos(
                    bbc_hwnd,
                    win32con.HWND_TOPMOST,  # 置顶
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
                )
                # 激活窗口
                win32gui.SetForegroundWindow(bbc_hwnd)
                print(f"BBC窗口已置顶: {bbc_hwnd}")
                return bbc_hwnd
            return None
        
        bbc_hwnd = find_and_focus_bbc()
        if bbc_hwnd:
            time.sleep(0.5)  # 等待置顶生效
        
        # 3. 查找BBC窗口
        windows = Toolkit.find_desktop_windows()
        _bbc_hwnd = None
        for w in windows:
            if "BBchannel" in w.window_name:
                _bbc_hwnd = w.hwnd
                break
        
        if not _bbc_hwnd:
            print("未找到BBC窗口")
            return CustomAction.RunResult(success=False)
        print(f"[3/5] BBC窗口已找到，hwnd={_bbc_hwnd}")
        
        # 4. 执行初始化（使用 PyAutoGUI 直接操作 BBC）
        try:
            self._init_bbc_pyautogui(run_count, apple_type)
        except Exception as e:
            print(f"执行初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return CustomAction.RunResult(success=False)
        print("[4/5] BBC初始化完成")
        
        # 5. 监控BBC执行完成
        success = self._wait_for_bbc_completion()
        
        print(f"ExecuteBbcTask: 任务{'成功' if success else '失败'}")
        return success
    
    def _wait_for_bbc_completion(self):
        print("等待BBC执行完成...")
        while True:
            popup_hwnd = self._find_bbc_popup()
            if popup_hwnd:
                print("检测到BBC弹窗")
                popup_text = self._get_window_text(popup_hwnd)
                print(f"弹窗内容: {popup_text}")
                
                screenshot_path = os.path.join(os.path.dirname(__file__), "..", "logs", "bbc_popup.png")
                os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                self._screenshot_window(popup_hwnd, screenshot_path)
                print(f"弹窗截图已保存至: {screenshot_path}")
                
                self._click_popup_ok(popup_hwnd)
                print("已关闭BBC进程")
                return True
            
            time.sleep(5)
    
    def _init_bbc_pyautogui(self, run_count, apple_type):
        """使用 PyAutoGUI 初始化 BBC"""
        import pyautogui
        import cv2
        import numpy as np
        from PIL import ImageGrab
        
        print(f"[BBC Init] run_count={run_count}, apple_type={apple_type}")
        
        # 获取 BBC 窗口位置
        bbc_rect = win32gui.GetWindowRect(_bbc_hwnd)
        print(f"BBC窗口位置: {bbc_rect}")
        
        def capture_bbc():
            """截取 BBC 窗口区域"""
            screenshot = ImageGrab.grab(bbox=bbc_rect)
            return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        
        def find_and_click(template_path, timeout=10):
            """在 BBC 窗口中查找图片并点击"""
            if not os.path.exists(template_path):
                print(f"模板不存在: {template_path}")
                return False
            
            template = cv2.imread(template_path)
            if template is None:
                print(f"无法加载模板: {template_path}")
                return False
            
            start = time.time()
            while time.time() - start < timeout:
                screen = capture_bbc()
                result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
                
                if max_val > 0.8:
                    # 计算点击位置（绝对屏幕坐标）
                    h, w = template.shape[:2]
                    click_x = bbc_rect[0] + max_loc[0] + w // 2
                    click_y = bbc_rect[1] + max_loc[1] + h // 2
                    print(f"找到 {os.path.basename(template_path)} 在 ({click_x}, {click_y})，相似度 {max_val:.2f}")
                    pyautogui.click(click_x, click_y)
                    time.sleep(0.3)
                    return True
                
                time.sleep(0.5)
            
            print(f"未找到: {os.path.basename(template_path)}")
            return False
        
        # 1. 点击 BBC 图标
        # 获取项目根目录（agent目录的父目录）
        project_dir = os.path.dirname(os.path.dirname(__file__))
        template_dir = os.path.join(project_dir, "assets", "resource", "image")
        if not find_and_click(os.path.join(template_dir, "bbc.png")):
            raise Exception("未找到BBC图标")
        time.sleep(0.5)
        
        # 2. 关闭免责声明（按 ESC）
        screen = capture_bbc()
        disclaimer_template = cv2.imread(os.path.join(template_dir, "免责声明.png"))
        if disclaimer_template is not None:
            result = cv2.matchTemplate(screen, disclaimer_template, cv2.TM_CCOEFF_NORMED)
            if cv2.minMaxLoc(result)[1] > 0.8:
                print("检测到免责声明，按ESC关闭")
                pyautogui.press('esc')
                time.sleep(0.3)
        
        # 3. 点击连接
        if not find_and_click(os.path.join(template_dir, "连接.png")):
            raise Exception("未找到连接按钮")
        time.sleep(0.5)
        
        # 4. 点击 mumu 高速
        if not find_and_click(os.path.join(template_dir, "mumu高速.png"), timeout=5):
            raise Exception("未找到mumu高速")
        time.sleep(0.5)
        
        # 5. 点击 mumu 高速2
        if not find_and_click(os.path.join(template_dir, "mumu高速2.png"), timeout=5):
            raise Exception("未找到mumu高速2")
        time.sleep(0.5)
        
        # 6. 点击刷本次数
        if not find_and_click(os.path.join(template_dir, "刷本次数.png")):
            raise Exception("未找到刷本次数")
        time.sleep(0.3)
        
        # 7. 点击输入框
        if not find_and_click(os.path.join(template_dir, "输入框.png")):
            raise Exception("未找到输入框")
        time.sleep(0.3)
        
        # 8. 输入次数
        print(f"输入次数: {run_count}")
        pyautogui.keyDown('ctrl')
        pyautogui.keyDown('a')
        pyautogui.keyUp('a')
        pyautogui.keyUp('ctrl')
        time.sleep(0.1)
        pyautogui.typewrite(str(run_count), interval=0.05)
        time.sleep(0.3)
        
        # 9. 选择苹果
        apple_images = {
            "gold": "金苹果.png",
            "silver": "银苹果.png", 
            "copper": "铜苹果.png",
            "natural": "自然回体.png"
        }
        target_apple = apple_images.get(apple_type, "金苹果.png")
        print(f"选择苹果: {apple_type}")
        
        for _ in range(10):
            if find_and_click(os.path.join(template_dir, target_apple), timeout=1):
                print(f"已选择 {apple_type}")
                break
            # 点击任意苹果切换
            for img in apple_images.values():
                if find_and_click(os.path.join(template_dir, img), timeout=1):
                    time.sleep(0.3)
                    break
        
        # 10. 点击开始按钮
        if not find_and_click(os.path.join(template_dir, "开始按钮.png")):
            raise Exception("未找到开始按钮")
        
        print("[BBC Init] 初始化完成")
    
    def _find_bbc_popup(self):
        def callback(_hwnd, extra):
            if win32gui.IsWindowVisible(_hwnd):
                window_title = win32gui.GetWindowText(_hwnd)
                if window_title in ["脚本停止！", "自动关机中！", "助战排序不符合", "队伍配置错误！", "正在结束任务！", "其他任务运行中"]:
                    extra.append(_hwnd)
        
        popups = []
        win32gui.EnumWindows(callback, popups)
        return popups[0] if popups else None
    
    def _get_window_text(self, hwnd):
        return win32gui.GetWindowText(hwnd)
    
    def _screenshot_window(self, hwnd, save_path):
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top
        
        hwndDC = win32gui.GetWindowDC(hwnd)
        saveDC = win32gui.CreateCompatibleDC(hwndDC)
        
        saveBitMap = win32gui.CreateCompatibleBitmap(hwndDC, width, height)
        win32gui.SelectObject(saveDC, saveBitMap)
        
        win32gui.BitBlt(saveDC, 0, 0, width, height, hwndDC, 0, 0, win32con.SRCCOPY)
        
        from PIL import Image
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
        im.save(save_path)
        
        win32gui.DeleteObject(saveBitMap.GetHandle())
        win32gui.DeleteDC(saveDC)
        win32gui.ReleaseDC(hwnd, hwndDC)
    
    def _click_popup_ok(self, hwnd):
        try:
            subprocess.run(['taskkill', '/f', '/im', 'BBchannel.exe'], check=False, capture_output=True)
            print("已关闭BBC进程")
        except Exception as e:
            print(f"关闭BBC进程时出错: {e}")
        time.sleep(1)

