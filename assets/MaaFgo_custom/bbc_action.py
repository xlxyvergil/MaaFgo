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
class ExecuteBbcTask(CustomAction):
    """执行BBC任务 - 整合运行次数、苹果类型、启动、初始化和监控"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        global _bbc_hwnd, _bbc_controller
        
        # 解析JSON格式参数
        import json
        param_str = _parse_single_param(argv) or '{"run_count": 1, "apple_type": "gold"}'
        try:
            params = json.loads(param_str)
            run_count = params.get("run_count", 1)
            apple_type = params.get("apple_type", "gold")
        except json.JSONDecodeError:
            # 兼容旧格式
            parts = param_str.split("|")
            run_count = int(parts[0]) if parts[0].isdigit() else 1
            apple_type = parts[1] if len(parts) > 1 else "gold"
        
        print(f"[ExecuteBbcTask] run_count={run_count}, apple_type={apple_type}")
        
        # 1. 保存运行次数和苹果类型到配置
        scripts_settings = _load_scripts_settings()
        scripts_settings["bbc_run_count"] = run_count
        scripts_settings["apple_type"] = apple_type
        _save_scripts_settings(scripts_settings)
        print("[1/6] 配置已保存")
        
        # 2. 启动BBC进程
        bbc_exe_path = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
        if not os.path.exists(bbc_exe_path):
            print(f"BBC可执行文件不存在: {bbc_exe_path}")
            return CustomAction.RunResult(success=False)
        
        os.startfile(bbc_exe_path)
        print("[2/6] BBC进程已启动，等待窗口...")
        time.sleep(3)
        
        # 3. 查找并关闭免责声明窗口，确认已关闭
        print("[3/6] 查找并关闭免责声明窗口...")
        attempt = 0
        while True:
            attempt += 1
            disclaimer_hwnd = self._find_window_by_title("免责声明！")
            if disclaimer_hwnd:
                print(f"[3/6] 检测到免责声明窗口（尝试 {attempt}），正在关闭...")
                self._close_window_by_title("免责声明！")
                time.sleep(1)
            else:
                print("[3/6] 免责声明窗口已关闭")
                break
        
        # 4. 查找BBC窗口
        print("[4/6] 查找BBC窗口...")
        bbc_hwnd = None
        attempt = 0
        while True:
            attempt += 1
            bbc_hwnd = self._find_window_by_title("BBchannel")
            if bbc_hwnd:
                print(f"[4/6] BBC窗口已找到，hwnd={bbc_hwnd}（尝试 {attempt}）")
                break
            time.sleep(1)
        
        # 5. 执行bbc启动流程
        print("[5/6] 执行BBC启动流程...")
        if not self._execute_bbc_startup(context, bbc_hwnd, apple_type):
            print("[5/6] 错误：BBC启动流程失败")
            return CustomAction.RunResult(success=False)
        
        # 6. 执行刷本次数节点并监控战斗结束
        print("[6/6] 执行刷本次数节点并监控战斗结束...")
        if not self._execute_bbc_battle(context, bbc_hwnd, run_count):
            print("[6/6] 错误：BBC战斗执行失败")
            return CustomAction.RunResult(success=False)
        
        print("ExecuteBbcTask: 任务已完成")
        return True
    
    def _find_window_by_title(self, title):
        """根据标题查找窗口"""
        def callback(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                if title in window_title:
                    extra.append(hwnd)
        
        matches = []
        win32gui.EnumWindows(callback, matches)
        return matches[0] if matches else None
    
    def _close_window_by_title(self, title):
        """根据标题关闭窗口"""
        hwnd = self._find_window_by_title(title)
        if hwnd:
            # 发送关闭消息
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return True
        return False
    
    def _execute_bbc_startup(self, context, bbc_hwnd, apple_type):
        """执行BBC启动流程"""
        try:
            from maa.controller import Win32Controller
            from maa.tasker import Tasker
            from maa.define import MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum
            
            # 连接到BBC窗口执行启动
            print("[ExecuteBbcTask] 连接到BBC窗口执行启动")
            # 连接到 BBC 窗口，明确配置截图和输入方式
            bbc_controller = Win32Controller(
                bbc_hwnd,
                screencap_method=MaaWin32ScreencapMethodEnum.PrintWindow,
                mouse_method=MaaWin32InputMethodEnum.Seize,
                keyboard_method=MaaWin32InputMethodEnum.Seize
            )
            bbc_controller.post_connection().wait()
            
            # 使用共享资源
            resource = context.tasker.resource if hasattr(context, 'tasker') and context.tasker else None
            if not resource:
                from maa.resource import Resource
                resource = Resource()
                # 加载资源
                bundle_result = resource.post_bundle("./assets/resource").wait()
                if not bundle_result.succeeded:
                    print("[ExecuteBbcTask] 资源加载失败")
                    return False
            
            # 创建Tasker执行任务
            tasker = Tasker()
            # 绑定资源和控制器
            tasker.bind(resource, bbc_controller)
            
            # 执行启动任务 - 点击刷本次数，设置执行次数和选择苹果的节点
            # 根据用户选择的苹果类型设置 next 节点
            apple_type_map = {
                "金苹果": "金苹果",
                "彩苹果": "彩苹果",
                "蓝苹果": "蓝苹果",
                "银苹果": "银苹果",
                "铜苹果": "铜苹果"
            }
            selected_apple = apple_type_map.get(apple_type, "金苹果")  # 默认金苹果
            
            print(f"[ExecuteBbcTask] 选择的苹果类型: {selected_apple}")
            
            pipeline_override = {
                "输入运行次数": {
                    "action": {"type": "InputText", "param": {"input_text": "1"}},  # 默认执行1次
                    "next": [selected_apple, "[JumpBack]选苹果"]  # 根据用户选择的苹果类型动态变更 next 节点
                }
            }
            print(f"[ExecuteBbcTask] 执行 点击刷本次数 任务，pipeline_override: {pipeline_override}")
            result = tasker.post_task("点击刷本次数", pipeline_override).wait().succeeded
            print(f"[ExecuteBbcTask] 点击刷本次数 任务执行结果: {result}")
            
            # 清理资源
            tasker = None
            
            return result
        except Exception as e:
            print(f"执行BBC启动流程出错: {e}")
            return False
    
    def _execute_bbc_battle(self, context, bbc_hwnd, run_count):
        """执行BBC战斗流程并监控结束"""
        try:
            from maa.controller import Win32Controller
            from maa.tasker import Tasker
            from maa.define import MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum
            
            # 创建控制器实例，明确配置截图和输入方式
            controller = Win32Controller(
                bbc_hwnd,
                screencap_method=MaaWin32ScreencapMethodEnum.PrintWindow,
                mouse_method=MaaWin32InputMethodEnum.Seize,
                keyboard_method=MaaWin32InputMethodEnum.Seize
            )
            
            # 连接控制器
            controller.post_connection().wait()
            
            # 测试截图并保存
            import cv2
            import os
            
            # 确保截图目录存在
            screenshot_dir = "./screenshots"
            if not os.path.exists(screenshot_dir):
                os.makedirs(screenshot_dir)
            
            # 执行截图
            screenshot = controller.screencap()
            if screenshot.size > 0:
                # 保存截图
                screenshot_path = os.path.join(screenshot_dir, f"bbc_screenshot_{int(time.time())}.png")
                # 注意：screenshot 是 BGR 格式，cv2.imwrite 会自动处理
                cv2.imwrite(screenshot_path, screenshot)
                print(f"[ExecuteBbcTask] 截图已保存到: {screenshot_path}")
                print(f"[ExecuteBbcTask] 截图尺寸: {screenshot.shape}")
            else:
                print("[ExecuteBbcTask] 截图失败，返回空数组")
            
            # 使用共享资源
            resource = context.tasker.resource if hasattr(context, 'tasker') and context.tasker else None
            if not resource:
                from maa.resource import Resource
                resource = Resource()
                # 加载资源（使用正确的资源路径）
                resource.post_bundle("./assets/resource").wait()
            
            # 创建 tasker 并绑定控制器
            tasker = Tasker()
            tasker.bind(resource, controller)
            
            # 执行任务
            pipeline_override = {
                "输入运行次数": {
                    "action": {"type": "InputText", "param": {"input_text": str(run_count)}}
                }
            }
            
            # 执行任务
            result = tasker.post_task("点击刷本次数", pipeline_override).wait().succeeded
            
            # 清理资源
            tasker = None
            
            # 监控战斗结束
            print("开始监控BBC战斗结束...")
            battle_end_windows = ["脚本停止！", "助战排序不符合", "队伍配置错误！", "正在结束任务！"]
            max_wait_time = 3600  # 最大等待时间1小时
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                for window_title in battle_end_windows:
                    if self._find_window_by_title(window_title):
                        print(f"检测到战斗结束窗口: {window_title}")
                        # 强制关闭BBC
                        print("强制关闭BBC...")
                        # 关闭BBC窗口
                        win32gui.PostMessage(bbc_hwnd, win32con.WM_CLOSE, 0, 0)
                        return True
                time.sleep(5)
            
            # 超时
            print("战斗监控超时")
            return False
        except Exception as e:
            print(f"执行BBC战斗流程出错: {e}")
            return False

