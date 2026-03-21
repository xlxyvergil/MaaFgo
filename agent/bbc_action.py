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
from maa.task import Task
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
        
        # 解析参数: run_count|apple_type|connection_type|ip_port
        param_str = _parse_single_param(argv) or "1|gold|mumu|127.0.0.1:5555"
        parts = param_str.split("|")
        run_count = int(parts[0]) if parts[0].isdigit() else 1
        apple_type = parts[1] if len(parts) > 1 else "gold"
        connection_type = parts[2] if len(parts) > 2 else "mumu"
        ip_port = parts[3] if len(parts) > 3 else "127.0.0.1:5555"
        
        print(f"[ExecuteBbcTask] run_count={run_count}, apple_type={apple_type}, connection_type={connection_type}, ip_port={ip_port}")
        
        # 1. 保存运行次数、苹果类型和连接类型到配置
        scripts_settings = _load_scripts_settings()
        scripts_settings["bbc_run_count"] = run_count
        scripts_settings["apple_type"] = apple_type
        scripts_settings["connection_type"] = connection_type
        if connection_type == "manual":
            scripts_settings["manual_ip_port"] = ip_port
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
        
        # 5. 使用自定义连接执行bbc启动流程
        print("[5/6] 执行BBC启动流程...")
        if not self._execute_bbc_startup(bbc_hwnd, connection_type, ip_port):
            print("[5/6] 错误：BBC启动流程失败")
            return CustomAction.RunResult(success=False)
        
        # 6. 使用Win32Controller执行刷本次数节点并监控战斗结束
        print("[6/6] 执行刷本次数节点并监控战斗结束...")
        if not self._execute_bbc_battle(bbc_hwnd, run_count):
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
    
    def _execute_bbc_startup(self, bbc_hwnd, connection_type, ip_port):
        """执行BBC启动流程"""
        try:
            # 创建临时连接和task
            from maa.controller import CustomController
            from maa.task import Task
            
            # 这里需要根据实际情况实现自定义控制器
            # 暂时使用Win32Controller作为示例
            controller = Win32Controller()
            controller.connect(str(bbc_hwnd))
            
            # 加载bbc.json配置
            bbc_json_path = os.path.join("./assets/resource/pipeline/bbc.json")
            if not os.path.exists(bbc_json_path):
                print(f"bbc.json不存在: {bbc_json_path}")
                return False
            
            # 执行bbc启动节点
            task = Task()
            task.load(bbc_json_path)
            task.set_start_node("bbc启动")
            
            # 根据连接类型设置next节点
            if connection_type == "mumu":
                # 修改bbc启动的next为mumu高速
                task.set_node_next("bbc启动", ["mumu高速"])
            elif connection_type == "ldplayer":
                # 修改bbc启动的next为雷电高速
                task.set_node_next("bbc启动", ["雷电高速"])
            elif connection_type == "manual":
                # 修改bbc启动的next为手动输入端口
                task.set_node_next("bbc启动", ["手动输入端口"])
            
            # 执行任务
            result = controller.run(task)
            if not result:
                print("执行bbc启动节点失败")
                return False
            
            # 根据连接类型执行后续流程
            if connection_type == "mumu":
                # 查找MuMu高速连接窗口
                attempt = 0
                while True:
                    attempt += 1
                    mumu_window = self._find_window_by_title("MuMu高速连接")
                    if mumu_window:
                        print(f"找到MuMu高速连接窗口（尝试 {attempt}），执行连接")
                        # 连接到MuMu窗口并执行mumu高速连接节点
                        mumu_controller = Win32Controller()
                        mumu_controller.connect(str(mumu_window))
                        mumu_task = Task()
                        mumu_task.load(bbc_json_path)
                        mumu_task.set_start_node("mumu高速连接")
                        mumu_result = mumu_controller.run(mumu_task)
                        mumu_controller.disconnect()
                        if not mumu_result:
                            print("执行mumu高速连接节点失败")
                            return False
                        break
                    time.sleep(1)
            
            elif connection_type == "ldplayer":
                # 查找雷电高速连接窗口
                attempt = 0
                while True:
                    attempt += 1
                    ld_window = self._find_window_by_title("雷电高速连接")
                    if ld_window:
                        print(f"找到雷电高速连接窗口（尝试 {attempt}），执行连接")
                        # 连接到雷电窗口并执行雷电高速连接节点
                        ld_controller = Win32Controller()
                        ld_controller.connect(str(ld_window))
                        ld_task = Task()
                        ld_task.load(bbc_json_path)
                        ld_task.set_start_node("雷电高速连接")
                        ld_result = ld_controller.run(ld_task)
                        ld_controller.disconnect()
                        if not ld_result:
                            print("执行雷电高速连接节点失败")
                            return False
                        break
                    time.sleep(1)
            
            elif connection_type == "manual":
                # 执行输入ip端口节点
                input_controller = Win32Controller()
                input_controller.connect(str(bbc_hwnd))
                input_task = Task()
                input_task.load(bbc_json_path)
                input_task.set_start_node("输入ip端口")
                
                # 修改输入ip节点的输入文本为用户提供的ip_port
                input_task.set_node_param("输入ip", "action", "param", {"input_text": ip_port})
                
                input_result = input_controller.run(input_task)
                input_controller.disconnect()
                if not input_result:
                    print("执行输入ip端口节点失败")
                    return False
                
                # 查找选择连接设备窗口
                attempt = 0
                while True:
                    attempt += 1
                    select_window = self._find_window_by_title("选择连接设备")
                    if select_window:
                        print(f"找到选择连接设备窗口（尝试 {attempt}），执行连接")
                        # 连接到选择连接设备窗口并执行选择连接设备ip节点
                        select_controller = Win32Controller()
                        select_controller.connect(str(select_window))
                        select_task = Task()
                        select_task.load(bbc_json_path)
                        select_task.set_start_node("选择连接设备ip")
                        select_result = select_controller.run(select_task)
                        select_controller.disconnect()
                        if not select_result:
                            print("执行选择连接设备ip节点失败")
                            return False
                        break
                    time.sleep(1)
            
            # 断开控制器连接
            controller.disconnect()
            return True
        except Exception as e:
            print(f"执行BBC启动流程出错: {e}")
            return False
    
    def _execute_bbc_battle(self, bbc_hwnd, run_count):
        """执行BBC战斗流程并监控结束"""
        try:
            # 创建Win32Controller连接到BBC窗口
            controller = Win32Controller()
            controller.connect(str(bbc_hwnd))
            
            # 加载bbc.json配置
            bbc_json_path = os.path.join("./assets/resource/pipeline/bbc.json")
            if not os.path.exists(bbc_json_path):
                print(f"bbc.json不存在: {bbc_json_path}")
                return False
            
            # 执行点击刷本次数节点
            task = Task()
            task.load(bbc_json_path)
            task.set_start_node("点击刷本次数")
            
            # 修改输入运行次数节点的输入文本为用户提供的run_count
            task.set_node_param("输入运行次数", "action", "param", {"input_text": str(run_count)})
            
            # 执行任务
            result = controller.run(task)
            if not result:
                print("执行点击刷本次数节点失败")
                controller.disconnect()
                return False
            
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
                        controller.disconnect()
                        # 关闭BBC窗口
                        win32gui.PostMessage(bbc_hwnd, win32con.WM_CLOSE, 0, 0)
                        return True
                time.sleep(5)
            
            # 超时
            print("战斗监控超时")
            controller.disconnect()
            return False
        except Exception as e:
            print(f"执行BBC战斗流程出错: {e}")
            return False

