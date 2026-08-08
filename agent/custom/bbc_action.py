import os
import sys
import time
import threading
from enum import IntEnum
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

from bbc_connection_manager import get_manager
import mfaalog


class BattleType(IntEnum):
    """战斗类型枚举 - 对应 BBC BATTLE_TYPE 索引"""
    CONTINUOUS = 0  # 连续出击(或强化本)
    TOWER_AUTO_SEQUENCE = 1  # 自动编队爬塔(应用操作序列设置)
    TOWER_AUTO_AI = 2  # 自动编队爬塔(程序自主技能宝具)
    INTERLUDE = 3  # 幕间物语(部分需手动)
    FREE_QUEST = 4  # 清自由本(不包括2.7)
    MAIN_LIKE = 5  # 类主线(部分情况手动)
    MAIN_STORY = 6  # 主线物语·大奥(部分情况手动)


# ==================== Action: 执行BBC任务（仅战斗部分）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC战斗任务 - 事件驱动模式"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """主入口：带自动重启的战斗流程
        
        执行逻辑：
        1. 最多重试2次，每次失败后重启BBC进程
        2. 执行单次战斗流程，检查是否需要重启
        3. 如果检测到游戏异常（need_restart=True），进入下一次循环
        4. 成功或达到最大重试次数后返回结果
        """
        max_retries = 2  # 最多重试2次
        last_error = None
        
        for attempt in range(max_retries):
            if attempt > 0:
                mfaalog.warning(f"[ExecuteBbcTask] 第{attempt}次重试...")
                # 执行BBC重启：先停止再启动，等待进程完全退出
                if not self._restart_bbc(context):
                    return CustomAction.RunResult(success=False)
            
            # 执行单次战斗流程（不含重启逻辑）
            result = self._execute_single_battle(context)
            last_error = result.get('error', '')
            
            # 检查是否需要重启：游戏异常时标记为 need_restart=True
            if result.get('need_restart', False):
                mfaalog.warning("[ExecuteBbcTask] 检测到游戏异常，准备重启...")
                continue  # 进入下一次循环，触发BBC重启
            else:
                # 返回最终结果
                if result['success']:
                    return CustomAction.RunResult(success=True)
                else:
                    # 失败时通过 pipeline_override 输出错误信息到GUI
                    if last_error:
                        context.override_pipeline({
                            "bbc弹窗信息输出": {
                                "focus": {
                                    "Node.Recognition.Starting": f"<span style=\"color: #FF0000;\">{last_error}</span>"
                                }
                            }
                        })
                    return CustomAction.RunResult(success=False)
        
        # 达到最大重试次数，输出最终错误信息
        error_msg = f"战斗失败（已重试{max_retries-1}次）" + (f": {last_error}" if last_error else "")
        mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
        context.override_pipeline({
            "bbc弹窗信息输出": {
                "focus": {
                    "Node.Recognition.Starting": f"<span style=\"color: #FF0000;\">{error_msg}</span>"
                }
            }
        })
        return CustomAction.RunResult(success=False)
    
    def _execute_single_battle(self, context: Context) -> dict:
        """执行单次战斗流程（不含重启逻辑）
        
        执行步骤：
        1. 从节点数据提取参数（队伍配置、运行次数、苹果类型、战斗类型等）
        2. 创建共享状态和弹窗监听线程
        3. 确保BBC TCP连接有效
        4. 设置弹窗回调函数，清空消息队列
        5. 验证模拟器连接状态
        6. 加载配置并启动战斗
        7. 等待战斗结束（心跳检查+弹窗处理）
        8. 输出结果并返回
        """
        try:
            # 从 Context 获取节点数据（包含 attach 参数）
            node_data = context.get_node_data("执行BBC任务")
            if not node_data:
                mfaalog.error("[ExecuteBbcTask] 无法获取节点数据")
                return {'success': False, 'error': '无法获取节点数据'}
            
            attach_data = node_data.get('attach', {})
            
            # 提取必需参数
            team_config = attach_data.get('bbc_team_config', '')  # BBC配置文件名
            run_count = attach_data.get('run_count')  # 运行次数
            apple_type = attach_data.get('apple_type')  # 苹果类型
            battle_type_value = attach_data.get('battle_type', 0)  # 战斗类型索引值 (0-6)
            
            # 将整数转换为 BattleType 枚举，便于后续使用 .name 和 .value
            try:
                battle_type = BattleType(battle_type_value)
            except ValueError:
                error_msg = f"无效的战斗类型: {battle_type_value} (有效范围: 0-6)"
                mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
                return {'success': False, 'error': error_msg}
            
            # 直接使用配置文件中的布尔值（BBC Server 需要 True/False）
            support_order_mismatch = attach_data.get('support_order_mismatch', False)  # 助战排序不符合时的响应
            team_config_error = attach_data.get('team_config_error', False)  # 队伍配置错误时的响应
            
            # 验证必需参数完整性
            if not team_config or run_count is None or apple_type is None:
                error_msg = f"参数不完整: team={team_config}, count={run_count}, apple={apple_type}"
                mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
                return {'success': False, 'error': error_msg}
            
            run_count = int(run_count)
            mfaalog.info(f"[ExecuteBbcTask] 参数: team={team_config}, count={run_count}, apple={apple_type}, type={battle_type.name}({battle_type.value})")
            
            # 提前创建共享状态并启动监听线程
            popup_event = threading.Event()  # 用于通知主线程弹窗事件
            state = {
                'finished': False,  # 战斗是否结束
                'popup_title': '',  # 弹窗标题
                'popup_message': '',  # 弹窗内容
                'popup_event': popup_event
            }
            
            # 启动弹窗监听线程（在 action 开头启动，确保能捕获所有弹窗）
            def popup_listener():
                """后台监听线程：等待弹窗事件通知"""
                while not state['finished']:
                    popup_event.wait()  # 无限等待弹窗事件
                    popup_event.clear()
                    mfaalog.info("[ExecuteBbcTask] 弹窗监听线程收到通知")
            
            listener_thread = threading.Thread(target=popup_listener, daemon=True)
            listener_thread.start()
            mfaalog.info("[ExecuteBbcTask] 弹窗监听线程已启动")
            
            # 获取或创建 Manager 实例（进程级单例）
            manager = get_manager()
            
            # 步顤1: 尝试TCP连接，失败则触发bbc_start
            if not self._ensure_bbc_connected(context):
                return {'success': False, 'error': 'BBC连接失败'}
            
            # 提前设置弹窗回调（在清空队列之前，确保不会错过弹窗）
            def on_popup(msg):
                """弹窗回调函数 - 快速返回，不阻塞监听线程
                
                功能：
                1. 记录弹窗信息
                2. 调用 _handle_popups 处理弹窗逻辑
                3. 通知监听线程
                """
                popup_title = msg.get('popup_title', '')
                popup_message = msg.get('popup_message', '')
                mfaalog.info(f"[ExecuteBbcTask] 收到弹窗: {popup_title} - {popup_message}")
                if not state['finished']:
                    try:
                        # 尝试获取 manager 实例
                        local_manager = get_manager()
                        self._handle_popups([msg], support_order_mismatch, team_config_error, state, local_manager)
                    except Exception as e:
                        mfaalog.error(f"[ExecuteBbcTask] 处理弹窗时出错: {e}")
                    state['popup_event'].set()  # 通知监听线程
            
            manager.set_popup_callback(on_popup)
            mfaalog.info("[ExecuteBbcTask] 弹窗回调已设置")
            
            # 清空消息队列，避免读取历史弹窗
            manager.clear_message_queue()
            
            # 步顤2: 验证模拟器连接
            if not self._verify_emulator_connection(attach_data, context):
                manager.disconnect_tcp()
                return {'success': False, 'error': '模拟器连接失败'}
            
            # 步骤3: 配置并启动战斗（同时启动回调监听）
            battle_result = self._setup_and_start_battle(
                team_config, run_count, apple_type, battle_type,
                support_order_mismatch, team_config_error, state, manager
            )
            if battle_result is None:
                manager.disconnect_tcp()
                return {'success': False, 'error': '战斗启动失败'}
            
            # 步骤4: 等待战斗结束（心跳检查+弹窗处理分离）
            popup_title, popup_message = self._wait_for_battle_end(context, state)
            
            manager.disconnect_tcp()
            
            # 步骤5: 输出结果到GUI
            if popup_title or popup_message:
                display_text = f"{popup_title}: {popup_message}" if popup_title else popup_message
                context.override_pipeline({
                    "bbc弹窗信息输出": {
                        "focus": {
                            "Node.Recognition.Starting": f"<span style=\"color: #FF0000;\">{display_text}</span>"
                        }
                    }
                })
                mfaalog.info(f"[ExecuteBbcTask] 战斗结束: {display_text}")
            else:
                mfaalog.info("[ExecuteBbcTask] 战斗正常结束")
            
            # 返回结果和是否需要重启的标志
            # 如果是错误状态，标记为失败且需要重启
            if popup_title == '错误' or '错误' in (popup_message or ''):
                return {
                    'success': False,
                    'error': f"{popup_title}: {popup_message}",
                    'need_restart': True  # 错误状态需要重启
                }
            
            return {
                'success': True,
                'need_restart': state.get('need_restart', False)
            }
            
        except Exception as e:
            error_msg = f"异常: {str(e)}"
            mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
            return {'success': False, 'error': error_msg}
    
    def _restart_bbc(self, context: Context) -> bool:
        """重启BBC进程
        
        执行流程：
        1. 断开当前TCP连接
        2. 调用 pipeline 节点"停止bbc"终止进程
        3. 等待3秒确保进程完全退出
        4. 调用 pipeline 节点"启动bbc"启动新进程
        5. 等待3秒让BBC完成初始化
        """
        manager = get_manager()
        try:
            # 先断开当前连接，避免旧连接干扰
            manager.disconnect_tcp()
            time.sleep(1)
            
            mfaalog.info("[Restart] 停止BBC进程...")
            stop_result = context.run_task("停止bbc")
            if not stop_result:
                mfaalog.error("[Restart] 停止BBC失败")
                return False
            
            # 等待进程完全退出，防止残留窗口
            time.sleep(3)
            
            mfaalog.info("[Restart] 启动BBC进程...")
            start_result = context.run_task("启动bbc")
            if not start_result:
                mfaalog.error("[Restart] 启动BBC失败")
                return False
            
            time.sleep(3)
            mfaalog.info("[Restart] BBC重启完成")
            return True
            
        except Exception as e:
            mfaalog.error(f"[Restart] 重启异常: {e}")
            return False
    
    def _ensure_bbc_connected(self, context: Context):
        """确保BBC已连接，必要时触发bbc_start
        
        检查逻辑：
        1. 测试当前TCP连接是否有效（发送空消息）
        2. 如果连接失效，调用 pipeline 节点"启动bbc"
        3. 重新检查连接状态
        """
        manager = get_manager()
        # 检查连接是否有效（通过发送测试消息验证）
        if manager.ensure_connected(timeout=3):
            mfaalog.info("[ExecuteBbcTask] TCP连接有效")
            return True
        
        mfaalog.warning("[ExecuteBbcTask] TCP连接失效，触发bbc_start...")
        
        # 触发bbc_start pipeline节点（会执行完整的重启流程）
        result = context.run_task("启动bbc")
        if not result:
            mfaalog.error("[ExecuteBbcTask] bbc_start执行失败")
            return False
        
        # 重新检查连接
        time.sleep(2)
        if manager.ensure_connected(timeout=5):
            mfaalog.info("[ExecuteBbcTask] bbc_start后TCP连接成功")
            return True
        
        mfaalog.error("[ExecuteBbcTask] bbc_start后TCP仍连接失败")
        return False
    
    def _verify_emulator_connection(self, attach_data: dict, context: Context) -> bool:
        """验证模拟器连接，必要时调用Manager重启
        
        验证逻辑：
        1. 通过 get_connection 命令获取当前模拟器状态
        2. 提取用户配置的参数（path、index、pkg等）
        3. 检查当前连接的模拟器参数是否与配置匹配
        4. 如果不匹配或未连接，调用 restart_bbc_and_connect 完整重启
        """
        manager = get_manager()
        conn_status = manager.send_command('get_connection', {}, timeout=5)
        
        # 检查是否有模拟器参数
        device_info = conn_status.get('device_info', {})
        emulator_params = device_info.get('emulator_params', {})
        
        if emulator_params:
            # 提取用户配置的参数（直接使用标准命令名）
            connect_cmd = attach_data.get('connect', 'auto')  # connect_mumu / connect_ld / connect_adb / auto
            
            expected_args = {}
            if connect_cmd == 'connect_mumu':
                # MuMu模拟器：需要 path、index、pkg、app_index
                expected_args = {
                    'path': attach_data.get('mumu_path', ''),
                    'index': int(attach_data.get('mumu_index', 0)),
                    'pkg': attach_data.get('mumu_pkg', 'com.bilibili.fatego'),
                    'app_index': int(attach_data.get('mumu_app_index', 0))
                }
            elif connect_cmd == 'connect_ld':
                # 雷电模拟器：需要 path、index
                expected_args = {
                    'path': attach_data.get('ld_path', ''),
                    'index': int(attach_data.get('ld_index', 0))
                }
            elif connect_cmd == 'connect_adb':
                # ADB直连：需要 IP 地址
                expected_args = {
                    'ip': attach_data.get('manual_port', '')
                }
            
            # 检查参数是否匹配
            params_match = manager.check_emulator_params_match(connect_cmd, expected_args, emulator_params)
            if params_match:
                mfaalog.info(f"[ExecuteBbcTask] 模拟器已连接且参数匹配: {emulator_params}")
                return True
            else:
                mfaalog.warning(f"[ExecuteBbcTask] 模拟器参数不匹配，期望: {expected_args}, 实际: {emulator_params}")
        
        mfaalog.warning("[ExecuteBbcTask] 模拟器未连接或参数不匹配，调用Manager重启BBC...")
        
        # 提取连接参数（直接使用标准命令名）
        connect_cmd = attach_data.get('connect', 'auto')  # connect_mumu / connect_ld / connect_adb / auto
        
        connect_args = {}
        if connect_cmd == 'connect_mumu':
            connect_args = {
                'path': attach_data.get('mumu_path', ''),
                'index': int(attach_data.get('mumu_index', 0)),
                'pkg': attach_data.get('mumu_pkg', 'com.bilibili.fatego'),
                'app_index': int(attach_data.get('mumu_app_index', 0))
            }
        elif connect_cmd == 'connect_ld':
            connect_args = {
                'path': attach_data.get('ld_path', ''),
                'index': int(attach_data.get('ld_index', 0))
            }
        elif connect_cmd == 'connect_adb':
            connect_args = {
                'ip': attach_data.get('manual_port', '')
            }
        elif connect_cmd == 'auto':
            connect_args = {
                'mode': 'auto'
            }
        
        # 调用Manager的完整重启流程（杀进程->启动->等待就绪->连接模拟器）
        success = manager.restart_bbc_and_connect(connect_cmd, connect_args, max_retries=3)
        
        if success:
            mfaalog.info("[ExecuteBbcTask] BBC重启并连接成功")
            return True
        else:
            mfaalog.error("[ExecuteBbcTask] BBC重启失败")
            return False
    
    def _setup_and_start_battle(self, team_config: str, run_count: int, 
                                apple_type: str, battle_type: BattleType,
                                support_order_mismatch: bool, team_config_error: bool,
                                state: dict, manager) -> dict:
        """配置并启动战斗
        
        执行步骤：
        1. 加载BBC配置文件
        2. 设置苹果类型、运行次数、战斗类型等参数
        3. 每次设置后检查是否有弹窗（如助战排序不符合）
        4. 发送 start_battle 命令启动战斗（带重试机制）
        5. 检查UI状态确认战斗是否成功启动
        """
        # 回调已在 _execute_single_battle 中设置，这里直接使用
        
        # 加载配置：从 settings 目录读取 JSON 文件
        mfaalog.info(f"[ExecuteBbcTask] 加载配置: {team_config}")
        result = manager.send_command('load_config', {'filename': team_config}, timeout=10)
        if not result.get('success'):
            mfaalog.error(f"[ExecuteBbcTask] 加载配置失败: {result.get('error')}")
            return None
        
        # 检查配置阶段是否有弹窗
        popup_msgs = manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state, manager):
            return state
        
        # 设置参数：苹果类型（金苹果、银苹果等）
        mfaalog.info(f"[ExecuteBbcTask] 设置苹果类型: {apple_type}")
        manager.send_command('set_apple_type', {'apple_type': apple_type}, timeout=5)
        
        popup_msgs = manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state, manager):
            return state
        
        # 设置运行次数
        mfaalog.info(f"[ExecuteBbcTask] 设置运行次数: {run_count}")
        manager.send_command('set_run_times', {'times': run_count}, timeout=5)
        
        # 设置战斗类型：发送给 BBC Server 整数索引值
        mfaalog.info(f"[ExecuteBbcTask] 设置战斗类型: {battle_type.name}({battle_type.value})")
        manager.send_command('set_battle_type', {'battle_type': battle_type.value}, timeout=5)
        
        # 启动战斗前最后检查一次弹窗
        popup_msgs = manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state, manager):
            return state
        
        # 启动战斗（带重试机制，最多重试3次）
        mfaalog.info("[ExecuteBbcTask] 启动战斗...")
        max_retries = 3
        battle_started = False
        
        for retry in range(max_retries):
            # 发送启动命令
            result = manager.send_command('start_battle', {}, timeout=10)
            if not result.get('success'):
                error = result.get('error', '')
                mfaalog.error(f"[ExecuteBbcTask] 启动战斗命令失败: {error}")
                
                # 检查是否是阵容未设置错误（Servant slot 未配置）
                if 'Servant slot' in error:
                    mfaalog.warning(f"[ExecuteBbcTask] 阵容未设置，重新触发点击 ({retry+1}/{max_retries})")
                    time.sleep(2)
                    continue
                else:
                    return None
            
            # 等待并检查状态
            time.sleep(2)
            ui_status = manager.send_command('get_ui_status', {}, timeout=5)
            
            # 检查是否成功启动：battle_running 或 device_running 为 True
            if ui_status.get('battle_running') or ui_status.get('device_running'):
                mfaalog.info("[ExecuteBbcTask] 战斗已启动")
                battle_started = True
                break
            
            # 检查UI提示文本（顶部Label）
            top_label = ui_status.get('top_label', '')
            mfaalog.info(f"[ExecuteBbcTask] UI状态: {top_label}")
            
            if '前辈！请设置好阵容再出战哦！' in top_label:
                mfaalog.warning(f"[ExecuteBbcTask] 检测到阵容未设置提示，重新触发点击 ({retry+1}/{max_retries})")
                time.sleep(2)
                continue
            
            # 检查是否有其他弹窗
            popup_msgs = manager.get_messages_by_title('', timeout=2)
            if popup_msgs:
                mfaalog.info(f"[ExecuteBbcTask] 检测到弹窗: {popup_msgs[0].get('popup_title', '')}")
        
        if not battle_started:
            error_msg = "启动战斗失败（阵容未设置，已重试3次）"
            mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
            return {'success': False, 'error': error_msg, 'need_restart': True}
        
        mfaalog.info("[ExecuteBbcTask] 战斗已启动，等待结束...")
        return state
    
    def _handle_popups(self, messages: list, support_order_mismatch: bool, 
                      team_config_error: bool, state: dict, manager) -> bool:
        """处理弹窗消息
        
        支持的弹窗类型：
        1. 助战排序不符合 (askyesno): 根据配置选择继续或停止
        2. 队伍配置错误 (askokcancel): 根据配置选择继续或停止
        3. 脚本停止 (showwarning): 检查是否为游戏异常，标记 need_restart
        4. 其他单按钮弹窗 (showwarning/showerror/showinfo): BBC Server自动关闭
        
        返回值：
        - True: 战斗结束，需要退出
        - False: 继续执行
        """
        for msg in messages:
            popup_title = msg.get('popup_title', '')
            popup_message = msg.get('popup_message', '')
            popup_id = msg.get('popup_id', '')
            
            mfaalog.info(f"[Callback] 收到弹窗: {popup_title}")
            
            # 处理助战排序不符合 (askyesno 类型，使用布尔值 True/False)
            if '助战排序不符合' in popup_title:
                action = support_order_mismatch  # True=继续, False=停止
                mfaalog.info(f"[Callback] 助战弹窗(askyesno)，响应: {action}")
                
                if popup_id:
                    # 发送弹窗响应命令给 BBC Server
                    manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': action
                    }, timeout=5)
                
                if not action:  # False = 停止
                    state['finished'] = True
                    state['popup_title'] = popup_title
                    state['popup_message'] = popup_message
                    mfaalog.info("[Callback] 用户拒绝助战，战斗结束")
                    return True
                else:  # True = 继续
                    state['popup_title'] = ''
                    state['popup_message'] = ''
                    mfaalog.info("[Callback] 用户选择继续助战")
            
            # 处理队伍配置错误 (askokcancel 类型，使用布尔值 True/False)
            elif '队伍配置错误' in popup_title:
                action = team_config_error  # True=继续, False=停止
                mfaalog.info(f"[Callback] 队伍配置弹窗(askokcancel)，响应: {action}")
                
                if popup_id:
                    manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': action
                    }, timeout=5)
                
                if not action:  # False = 停止
                    state['finished'] = True
                    state['popup_title'] = popup_title
                    state['popup_message'] = popup_message
                    mfaalog.info("[Callback] 用户拒绝队伍配置，战斗结束")
                    return True
                else:  # True = 继续
                    state['popup_title'] = ''
                    state['popup_message'] = ''
                    mfaalog.info("[Callback] 用户选择继续队伍配置")
            
            # 处理脚本停止 (showwarning 类型，BBC Server自动关闭)
            elif '脚本停止' in popup_title:
                mfaalog.info(f"[Callback] 检测到脚本停止: {popup_message}")
                
                # 检查是否是游戏异常导致的脚本停止
                if any(keyword in popup_message for keyword in [
                    '疑似游戏已闪退',
                    '疑似模拟器崩溃',
                    '高速接口获取截图失败'
                ]):
                    mfaalog.error(f"[Callback] 游戏异常导致的脚本停止: {popup_message}")
                    state['finished'] = True
                    state['popup_title'] = '游戏异常'
                    state['popup_message'] = popup_message
                    state['need_restart'] = True  # 标记需要重启
                    return True
                else:
                    mfaalog.info("[Callback] 任务正常结束")
                    # 正常结束不设置弹窗信息，避免被误判为异常
                    state['finished'] = True
                    return True
            
            # 处理其他单按钮弹窗 (showwarning/showerror/showinfo 类型，BBC Server自动关闭)
            elif any(keyword in popup_title for keyword in ['正在结束任务', '其他任务运行中']):
                mfaalog.info(f"[Callback] 检测到提示弹窗: {popup_title}:{popup_message}")
                
                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                mfaalog.info("[Callback] 提示弹窗已处理，战斗结束")
                return True
            
            # 处理未知弹窗（兜底）
            else:
                mfaalog.warning(f"[Callback] 未知弹窗: {popup_title}:{popup_message}")
                # BBC Server 会自动关闭，标记为结束
                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                return True
        
        return False
    
    def _wait_for_battle_end(self, context: Context, state: dict):
        """等待战斗结束 - 心跳检查和弹窗处理分离

        工作原理：
        1. 主线程每30秒发送一次 get_status 心跳检查
        2. 如果 BBC 服务无响应，标记为错误并需要重启
        3. 心跳成功后截图并运行"助战错误识别"pipeline，识别到助战错误则停止BBC脚本并正常结束任务
        4. 弹窗处理由回调函数在后台线程完成，通过 state['finished'] 通知主线程
        5. 当 state['finished'] 为 True 时，返回弹窗信息
        """

        # 获取 manager 实例
        manager = get_manager()

        # 主线程：心跳检查 + 截图识别
        heartbeat_interval = 30  # 30秒一次心跳
        miss_count = 0           # 连续识别到助战错误的次数
        miss_threshold = 1       # 阈值，达到则停止BBC脚本并正常结束任务

        while not state['finished']:
            time.sleep(heartbeat_interval)

            # 心跳检查：发送 get_status 命令验证 BBC 服务是否正常
            status = manager.send_command('get_status', {}, timeout=5)
            if not status.get('success'):
                mfaalog.warning("[ExecuteBbcTask] BBC服务无响应")
                state['finished'] = True
                state['popup_title'] = '错误'
                state['popup_message'] = 'BBC服务异常'
                state['need_restart'] = True  # 标记需要重启BBC
                break

            # 截图并通过"助战错误识别"pipeline 判断是否处于助战错误状态
            try:
                mfaalog.info("[ExecuteBbcTask] 开始截图检测战斗状态...")
                image = context.tasker.controller.post_screencap().wait().get()
                if image is None:
                    mfaalog.warning("[ExecuteBbcTask] 截图返回空数据，跳过本次战斗状态检测")
                    continue

                mfaalog.info(f"[ExecuteBbcTask] 截图成功，图像尺寸: {image.shape}")

                # 运行"助战错误识别"pipeline（模板匹配，兼容各服），
                # 专门识别两个准确结果节点：有助战错误 / 无助战错误
                mfaalog.info("[ExecuteBbcTask] 开始识别助战错误...")
                task_detail = context.run_task("助战错误识别")
                has_error = False
                if task_detail is not None:
                    for node in task_detail.nodes:
                        if not node.completed:
                            continue
                        if node.name == "有助战错误":
                            has_error = True
                            break
                        if node.name == "无助战错误":
                            has_error = False
                            break

                if has_error:
                    miss_count += 1
                    mfaalog.warning(
                        f"[ExecuteBbcTask] 识别到助战错误 ({miss_count}/{miss_threshold})"
                    )
                    if miss_count >= miss_threshold:
                        mfaalog.info(f"[ExecuteBbcTask] 识别到助战错误，停止BBC脚本并正常结束任务")
                        # 先覆盖"执行战斗结束"的 next 为"阶段判断循环"（确保覆盖必然生效），
                        # 再停止BBC脚本：stop_battle 会触发"脚本停止"弹窗，避免其干扰后续流程
                        # （助战错误的点击会使游戏重新进入登录状态，"结束战斗"流程无法走通）
                        mfaalog.info("[ExecuteBbcTask] 覆盖'执行战斗结束'为阶段判断循环，处理游戏状态...")
                        context.override_next("执行战斗结束", ["阶段判断循环"])
                        # 停止BBC脚本，避免继续干扰模拟器操作
                        stop_result = manager.send_command('stop_battle', {}, timeout=5)
                        if stop_result.get('success'):
                            mfaalog.info("[ExecuteBbcTask] BBC脚本已停止")
                        else:
                            mfaalog.warning(f"[ExecuteBbcTask] 停止BBC脚本失败: {stop_result.get('error', '')}")
                        # 等待BBC脚本完全停止，避免干扰后续任务
                        time.sleep(3)
                        # 正常结束不设置弹窗信息，避免被误判为异常
                        state['finished'] = True
                        break
                else:
                    miss_count = 0
                    mfaalog.info("[ExecuteBbcTask] 未识别到助战错误，战斗进行中")
            except Exception as e:
                mfaalog.warning(f"[ExecuteBbcTask] 截图识别异常: {e}")

        return state['popup_title'], state['popup_message']
    
