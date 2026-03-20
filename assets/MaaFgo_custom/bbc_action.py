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


class ExecuteBbcTask(CustomAction):
    """执行BBC任务"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        # 获取参数 - 从 custom_action_param 获取
        print(f"ExecuteBbcTask: custom_action_param = {repr(argv.custom_action_param)}")
        
        # 尝试从 custom_action_param 解析参数（现在直接是字典）
        params = {}
        if argv.custom_action_param:
            try:
                param_str = argv.custom_action_param.strip()
                # 处理可能的嵌套引号（如 '"{...}"' 或 '"\"{...}\""'）
                while (param_str.startswith('"') and param_str.endswith('"')) or (param_str.startswith("'") and param_str.endswith("'")):
                    # 去掉外层引号
                    param_str = param_str[1:-1]
                    # 处理转义的引号
                    param_str = param_str.replace('\\"', '"')
                params = json.loads(param_str)
                if not isinstance(params, dict):
                    params = {}
            except json.JSONDecodeError as e:
                # 如果不是 JSON，可能是直接传递的字符串值
                print(f"JSON解析失败: {e}, param_str={repr(param_str)}")
                params = {}
        
        print(f"ExecuteBbcTask: parsed params = {params}")
        
        # 从 params 获取参数值（现在每个选项只传递自己的参数）
        bbc_team_config = params.get('bbc_team_config', '')
        bbc_run_count_str = params.get('bbc_run_count', '')
        apple_type = params.get('apple_type', '')
        
        # 如果参数为空，使用默认值
        if not bbc_team_config:
            bbc_team_config = "哈贝喵_Caber_Caber"
        if not bbc_run_count_str:
            bbc_run_count_str = "1"
        if not apple_type:
            apple_type = "copper"
        
        try:
            bbc_run_count = int(bbc_run_count_str)
        except ValueError:
            bbc_run_count = 1
        
        print(f"ExecuteBbcTask: team={bbc_team_config}, count={bbc_run_count}, apple={apple_type}")
        
        # 辅助函数：获取参数（使用默认值）
        def get_param(key, default=""):
            return params.get(key, default)
        
        # 固定BBC路径
        bbc_path = "./BBC/BBchannel"
        
        # 构建配置文件路径
        settings_dir = os.path.join(bbc_path, 'settings')
        scripts_settings_path = os.path.join(bbc_path, 'scripts_settings.json')
        
        # 1. 读取队伍配置
        # 如果 bbc_team_config 已经是完整路径，直接使用；否则构建路径
        if os.path.isabs(bbc_team_config) or bbc_team_config.startswith('./') or bbc_team_config.startswith('../'):
            # 已经是路径，直接使用
            team_config_path = bbc_team_config
            # 如果没有.json后缀，添加它
            if not team_config_path.endswith('.json'):
                team_config_path += '.json'
        else:
            # 是相对路径或文件名，构建完整路径
            team_config_path = os.path.join(settings_dir, f"{bbc_team_config}.json")
        
        if not os.path.exists(team_config_path):
            print(f"队伍配置文件不存在: {team_config_path}")
            return CustomAction.RunResult(success=False)
        
        with open(team_config_path, 'r', encoding='utf-8') as f:
            team_config = json.load(f)
        
        # 2. 读取当前scripts_settings
        scripts_settings = {}
        connect_settings = {}
        
        if os.path.exists(scripts_settings_path):
            with open(scripts_settings_path, 'r', encoding='utf-8') as f:
                scripts_settings = json.load(f)
            
            # 3. 保存连接设置（BBC的逻辑）
            for key in ["connectMode", "snapshotDevice", "operateDevice"]:
                if key in scripts_settings:
                    connect_settings[key] = scripts_settings[key]
        
        # 4. 完全替换配置（BBC的逻辑）
        scripts_settings = team_config
        
        # 5. 恢复连接设置
        scripts_settings.update(connect_settings)
        
        # 6. 更新其他参数
        run_count = get_param("bbc_run_count")
        if run_count:
            scripts_settings["bbc_run_count"] = run_count
        apple = get_param("apple_type")
        if apple:
            scripts_settings["apple_type"] = apple
        
        # 7. 保存scripts_settings
        with open(scripts_settings_path, 'w', encoding='utf-8') as f:
            json.dump(scripts_settings, f, ensure_ascii=False, indent=2)
        
        # 8. 更新UIsettings
        uisettings_path = os.path.join(bbc_path, 'UIsettings.json')
        uisettings = {}
        
        if os.path.exists(uisettings_path):
            with open(uisettings_path, 'r', encoding='utf-8') as f:
                uisettings = json.load(f)
        
        # 更新UI设置
        ui_settings = [
            "autoBG", "effect", "showAssistSettingBeforeStart", "autoConnect",
            "capMethod", "adbori", "adbtouch", "maxtouch"
        ]
        
        for setting in ui_settings:
            val = get_param(setting)
            if val:
                uisettings[setting] = val
        
        # 保存UIsettings
        with open(uisettings_path, 'w', encoding='utf-8') as f:
            json.dump(uisettings, f, ensure_ascii=False, indent=2)
        
        # 9. 启动BBC
        bbc_exe_path = os.path.join(bbc_path, 'dist', 'BBchannel64', 'BBchannel.exe')
        if os.path.exists(bbc_exe_path):
            os.startfile(bbc_exe_path)
        else:
            print(f"BBC可执行文件不存在: {bbc_exe_path}")
            return CustomAction.RunResult(success=False)
        
        # 10. 等待BBC窗口加载
        time.sleep(3)
        
        # 11. 创建Win32控制器连接BBC窗口 - 修正创建方式
        windows = Toolkit.find_desktop_windows()
        bbc_hwnd = None
        for w in windows:
            if "BBchannel" in w.window_name:
                bbc_hwnd = w.hwnd
                break
        
        if not bbc_hwnd:
            print("未找到BBC窗口")
            return CustomAction.RunResult(success=False)
        
        win32_controller = Win32Controller(
            hwnd=bbc_hwnd,
            screencap_method=MaaWin32ScreencapMethodEnum.PrintWindow,
            mouse_method=MaaWin32InputMethodEnum.PostMessage,
            keyboard_method=MaaWin32InputMethodEnum.PostMessage,
        )
        
        connected = win32_controller.post_connection().wait().succeeded
        if not connected:
            print("连接BBC窗口失败")
            return CustomAction.RunResult(success=False)
        
        # 12. 执行BBC配置Pipeline - 使用 context.run_task 同步执行
        # 该Pipeline用于连接模拟器、使用苹果、输入执行次数等操作
        pipeline_args = {
            "apple_type": apple_type,
            "bbc_run_count": bbc_run_count,
            "bbc_team_config": bbc_team_config
        }
        
        try:
            # 使用 context.run_task 同步执行，会阻塞直到完成
            task_detail = context.run_task("bbc_config", pipeline_args)
            if not task_detail:
                print("bbc_config pipeline 执行失败")
                win32_controller.post_inactive().wait()
                return CustomAction.RunResult(success=False)
        except Exception as e:
            print(f"执行 pipeline 失败: {e}")
            win32_controller.post_inactive().wait()
            return CustomAction.RunResult(success=False)
        
        # 13. 等待BBC执行完成
        success = self._wait_for_bbc_completion(bbc_run_count)
        
        # 14. 清理资源
        win32_controller.post_inactive().wait()
        
        return CustomAction.RunResult(success=success)

    def _wait_for_bbc_completion(self, _expected_runs):
        """等待BBC执行完成"""
        # 无限等待直到出现弹窗
        while True:
            # 检查是否出现消息框（弹窗）
            popup_hwnd = self._find_bbc_popup()
            if popup_hwnd:
                print("检测到BBC弹窗")
                # 捕获弹窗内容
                popup_text = self._get_window_text(popup_hwnd)
                print(f"弹窗内容: {popup_text}")
                
                # 对弹窗进行截图
                screenshot_path = os.path.join(os.path.dirname(__file__), "..", "logs", "bbc_popup.png")
                os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                self._screenshot_window(popup_hwnd, screenshot_path)
                print(f"弹窗截图已保存至: {screenshot_path}")
                
                # 关闭BBC进程
                self._click_popup_ok(popup_hwnd)
                print("已关闭BBC进程")
                
                # 只要出现弹窗，就认为BBC任务已结束
                return True
            
            time.sleep(5)  # 每5秒检查一次

    def _find_bbc_popup(self):
        """查找BBC的消息弹窗"""
        def callback(_hwnd, extra):
            if win32gui.IsWindowVisible(_hwnd):
                window_title = win32gui.GetWindowText(_hwnd)
                # BBC的所有可能弹窗标题
                if window_title in ["脚本停止！", "自动关机中！", "助战排序不符合", "队伍配置错误！", "正在结束任务！", "其他任务运行中"]:
                    extra.append(_hwnd)
        
        popups = []
        win32gui.EnumWindows(callback, popups)
        return popups[0] if popups else None

    def _get_window_text(self, hwnd):
        """获取窗口文本内容"""
        return win32gui.GetWindowText(hwnd)

    def _screenshot_window(self, hwnd, save_path):
        """对指定窗口进行截图"""
        # 获取窗口矩形
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top
        
        # 创建DC
        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32gui.CreateCompatibleDC(hwndDC)
        saveDC = win32gui.CreateCompatibleDC(hwndDC)
        
        # 创建位图
        saveBitMap = win32gui.CreateCompatibleBitmap(hwndDC, width, height)
        win32gui.SelectObject(saveDC, saveBitMap)
        
        # 复制窗口内容到位图
        win32gui.BitBlt(saveDC, 0, 0, width, height, hwndDC, 0, 0, win32con.SRCCOPY)
        
        # 保存位图
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
        
        # 清理资源
        win32gui.DeleteObject(saveBitMap.GetHandle())
        win32gui.DeleteDC(mfcDC)
        win32gui.DeleteDC(saveDC)
        win32gui.ReleaseDC(hwnd, hwndDC)

    def _click_popup_ok(self, hwnd):
        """关闭BBC进程"""
        # 使用taskkill命令关闭BBC进程
        try:
            # 终止所有名为BBchannel.exe的进程
            subprocess.run(['taskkill', '/f', '/im', 'BBchannel.exe'], check=False, capture_output=True)
            print("已关闭BBC进程")
        except Exception as e:
            print(f"关闭BBC进程时出错: {e}")
        
        # 等待进程关闭
        time.sleep(1)


class ExecuteNavigation(CustomAction):
    """执行章节和关卡导航"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        # 从 custom_action_param 获取 quest 值
        print(f"ExecuteNavigation: custom_action_param = {repr(argv.custom_action_param)}")
        
        quest_value = argv.custom_action_param if argv.custom_action_param else ""
        
        # 去掉可能的引号（JSON 字符串格式）
        if (quest_value.startswith('"') and quest_value.endswith('"')) or (quest_value.startswith("'") and quest_value.endswith("'")):
            quest_value = quest_value[1:-1]
        
        print(f"ExecuteNavigation: quest_value = {repr(quest_value)}")
        
        if not quest_value or quest_value.startswith("${"):
            print("quest 参数未设置或格式错误")
            return CustomAction.RunResult(success=False)
        
        # 解析 quest 格式: "章节,关卡"
        parts = quest_value.split(",")
        if len(parts) != 2:
            print(f"quest 格式错误: {quest_value}, 应为 '章节,关卡'")
            return CustomAction.RunResult(success=False)
        
        chapter, stage = parts[0].strip(), parts[1].strip()
        print(f"导航到章节: {chapter}, 关卡: {stage}")
        
        # TODO: 实现实际的导航逻辑
        # 暂时直接返回成功，等待实现具体的导航代码
        print(f"[TODO] 导航到章节: {chapter}, 关卡: {stage}")
        return CustomAction.RunResult(success=True)


class SelectTeamAction(CustomAction):
    """选择队伍"""
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        # 获取参数
        params = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        team_index = params.get("team_index", "1")
        
        # 构建队伍选择Pipeline名称
        pipeline_name = f"team_{team_index}"
        
        # 执行队伍选择Pipeline - 使用 context.run_task 同步执行
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
