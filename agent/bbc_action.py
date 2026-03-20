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
    while (param.startswith('"') and param.endswith('"')) or (param.startswith("'") and param.endswith("'")):
        param = param[1:-1]
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
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
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
        return CustomAction.RunResult(success=True)


# ==================== Action 2: 设置运行次数 ====================
@AgentServer.custom_action("set_bbc_run_count")
class SetBbcRunCount(CustomAction):
    """设置BBC运行次数 - 处理 bbc_run_count"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        print(f"[2/6] SetBbcRunCount: custom_action_param = {repr(argv.custom_action_param)}")
        
        bbc_run_count_str = _parse_single_param(argv) or "1"
        
        try:
            bbc_run_count = int(bbc_run_count_str)
        except ValueError:
            bbc_run_count = 1
        
        print(f"SetBbcRunCount: count={bbc_run_count}")
        
        scripts_settings = _load_scripts_settings()
        scripts_settings["bbc_run_count"] = bbc_run_count
        _save_scripts_settings(scripts_settings)
        
        print(f"SetBbcRunCount: 运行次数已设置")
        return CustomAction.RunResult(success=True)


# ==================== Action 3: 设置苹果类型 ====================
@AgentServer.custom_action("set_apple_type")
class SetAppleType(CustomAction):
    """设置BBC苹果类型 - 处理 apple_type"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        print(f"[3/6] SetAppleType: custom_action_param = {repr(argv.custom_action_param)}")
        
        apple_type = _parse_single_param(argv) or "copper"
        
        print(f"SetAppleType: apple_type={apple_type}")
        
        scripts_settings = _load_scripts_settings()
        scripts_settings["apple_type"] = apple_type
        _save_scripts_settings(scripts_settings)
        
        print(f"SetAppleType: 苹果类型已设置")
        return CustomAction.RunResult(success=True)


# ==================== Action 4: 启动BBC进程 ====================
@AgentServer.custom_action("start_bbc_process")
class StartBbcProcess(CustomAction):
    """启动BBC进程 - 无参数"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        global _bbc_hwnd
        
        print(f"[4/6] StartBbcProcess: 启动BBC进程")
        
        bbc_exe_path = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
        if os.path.exists(bbc_exe_path):
            os.startfile(bbc_exe_path)
        else:
            print(f"BBC可执行文件不存在: {bbc_exe_path}")
            return CustomAction.RunResult(success=False)
        
        print("等待BBC窗口加载...")
        time.sleep(3)
        
        windows = Toolkit.find_desktop_windows()
        _bbc_hwnd = None
        for w in windows:
            if "BBchannel" in w.window_name:
                _bbc_hwnd = w.hwnd
                break
        
        if not _bbc_hwnd:
            print("未找到BBC窗口")
            return CustomAction.RunResult(success=False)
        
        print(f"StartBbcProcess: BBC窗口已找到，hwnd={_bbc_hwnd}")
        return CustomAction.RunResult(success=True)


# ==================== Action 5: 执行BBC初始化 ====================
@AgentServer.custom_action("execute_bbc_init")
class ExecuteBbcInit(CustomAction):
    """执行BBC初始化Pipeline - 无参数"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        global _bbc_hwnd, _bbc_controller
        
        print(f"[5/6] ExecuteBbcInit: 连接BBC并执行初始化")
        
        if not _bbc_hwnd:
            print("错误：BBC窗口句柄未设置")
            return CustomAction.RunResult(success=False)
        
        _bbc_controller = Win32Controller(
            hwnd=_bbc_hwnd,
            screencap_method=MaaWin32ScreencapMethodEnum.PrintWindow,
            mouse_method=MaaWin32InputMethodEnum.PostMessage,
            keyboard_method=MaaWin32InputMethodEnum.PostMessage,
        )
        
        connected = _bbc_controller.post_connection().wait().succeeded
        if not connected:
            print("连接BBC窗口失败")
            return CustomAction.RunResult(success=False)
        
        print("ExecuteBbcInit: 已连接到BBC窗口")
        
        try:
            task_detail = context.run_task("bbc_config")
            if not task_detail:
                print("bbc_config pipeline 执行失败")
                return CustomAction.RunResult(success=False)
        except Exception as e:
            print(f"执行 pipeline 失败: {e}")
            return CustomAction.RunResult(success=False)
        
        print("ExecuteBbcInit: 初始化完成")
        return CustomAction.RunResult(success=True)


# ==================== Action 6: 检测BBC完成 ====================
@AgentServer.custom_action("monitor_bbc_completion")
class MonitorBbcCompletion(CustomAction):
    """检测BBC执行完成 - 无参数"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        global _bbc_controller
        
        print(f"[6/6] MonitorBbcCompletion: 检测BBC执行完成")
        
        success = self._wait_for_bbc_completion()
        
        if _bbc_controller:
            _bbc_controller.post_inactive().wait()
        
        print(f"MonitorBbcCompletion: 任务{'成功' if success else '失败'}")
        return CustomAction.RunResult(success=success)
    
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


# ==================== 其他 Action ====================
@AgentServer.custom_action("select_team_action")
class SelectTeamAction(CustomAction):
    """选择队伍"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        params = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        team_index = params.get("team_index", "1")
        
        pipeline_name = f"team_{team_index}"
        
        try:
            task_detail = context.run_task(pipeline_name)
            if task_detail:
                print(f"已执行队伍 {team_index} 的选择Pipeline: {pipeline_name}")
                return CustomAction.RunResult(success=True)
            else:
                print(f"队伍选择Pipeline执行失败: {pipeline_name}")
                return CustomAction.RunResult(success=False)
        except Exception as e:
            print(f"执行队伍选择Pipeline失败: {e}")
            return CustomAction.RunResult(success=False)
