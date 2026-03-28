import json
import os
import time
import socket
import struct
import subprocess
import threading
import urllib.request
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context


# BBC HTTP API 配置
BBC_HTTP_HOST = "127.0.0.1"
BBC_HTTP_PORT = 25002
BBC_TCP_PORT = 25001
BBC_BASE_URL = f"http://{BBC_HTTP_HOST}:{BBC_HTTP_PORT}"


def _http_get(path: str) -> dict:
    """HTTP GET 请求"""
    try:
        url = f"{BBC_BASE_URL}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"[HTTP GET Error] {path}: {e}")
        return {}


def _http_post(path: str, data: dict = None) -> dict:
    """HTTP POST 请求"""
    try:
        url = f"{BBC_BASE_URL}{path}"
        body = json.dumps(data, ensure_ascii=False).encode('utf-8') if data else b''
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"[HTTP POST Error] {path}: {e}")
        return {}


def _wait_for_bbc_services(timeout: int = 30) -> bool:
    """等待 BBC HTTP 和 TCP 服务就绪"""
    start_time = time.time()
    http_ready = False
    
    while time.time() - start_time < timeout:
        if not http_ready:
            try:
                resp = _http_get('/status')
                if resp.get('status') == 'running':
                    http_ready = True
                    print(f"[BBC] HTTP 服务已就绪")
                    return True
            except:
                pass
        time.sleep(0.5)
    
    print(f"[BBC] 服务启动超时")
    return False


def _popup_response(popup_id: str, action: str) -> bool:
    """发送弹窗决策到 BBC"""
    result = _http_post('/popup/response', {'id': popup_id, 'action': action})
    return result.get('success', False)


class BbcTcpClient:
    """BBC TCP 客户端 - 接收弹窗事件"""
    
    def __init__(self):
        self.sock = None
        self.running = False
        self.popup_callbacks = []
        self.thread = None
    
    def connect(self, timeout: int = 10) -> bool:
        """连接到 BBC TCP 服务"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((BBC_HTTP_HOST, BBC_TCP_PORT))
            self.sock.settimeout(None)
            print(f"[TCP] 已连接到 BBC TCP 服务 {BBC_HTTP_HOST}:{BBC_TCP_PORT}")
            return True
        except Exception as e:
            print(f"[TCP] 连接失败: {e}")
            return False
    
    def start_listening(self):
        """启动监听线程"""
        self.running = True
        self.thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.thread.start()
    
    def _receive_loop(self):
        """接收循环"""
        while self.running:
            try:
                length_bytes = self._recv_all(4)
                if not length_bytes:
                    break
                length = struct.unpack('>I', length_bytes)[0]
                
                data = self._recv_all(length)
                if not data:
                    break
                
                popup_data = json.loads(data.decode('utf-8'))
                print(f"[TCP] 收到弹窗: {popup_data.get('title', 'Unknown')}")
                
                for callback in self.popup_callbacks:
                    try:
                        callback(popup_data)
                    except Exception as e:
                        print(f"[TCP] 回调错误: {e}")
                        
            except Exception as e:
                if self.running:
                    print(f"[TCP] 接收错误: {e}")
                break
        
        print("[TCP] 接收循环结束")
    
    def _recv_all(self, n: int) -> bytes:
        """接收指定字节数的数据"""
        data = b''
        while len(data) < n:
            try:
                packet = self.sock.recv(n - len(data))
                if not packet:
                    return None
                data += packet
            except socket.timeout:
                continue
            except Exception as e:
                return None
        return data
    
    def stop(self):
        """停止监听"""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass


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


# ==================== Action 1: 设置 BBC 配置 ====================
@AgentServer.custom_action("setup_bbc_config")
class SetupBbcConfig(CustomAction):
    """设置 BBC 队伍配置 - 处理 bbc_team_config"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        print(f"[1/6] SetupBbcConfig: custom_action_param = {repr(argv.custom_action_param)}")
        
        bbc_team_config = _parse_single_param(argv)
        
        if not bbc_team_config:
            print("错误：未提供队伍配置文件路径")
            return CustomAction.RunResult(success=False)
        
        print(f"SetupBbcConfig: team_config={bbc_team_config}")
        
        settings_dir = os.path.join(BBC_PATH, 'settings')
        
        # 拼接固定路径
        team_config_path = os.path.join(settings_dir, bbc_team_config)
        
        if not os.path.exists(team_config_path):
            print(f"队伍配置文件不存在：{team_config_path}")
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


# ==================== Action 2: 执行BBC任务（整合版）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC任务 - 整合运行次数、苹果类型、启动、初始化和监控"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        global _bbc_hwnd, _bbc_controller
        
        # 从 Context 获取节点数据（包含 pipeline_override 合并后的值）
        node_data = context.get_node_data("执行BBC任务")
        print(f"[ExecuteBbcTask] node_data={node_data}")
        
        if not node_data:
            print(f"[ExecuteBbcTask] 错误：无法获取节点数据")
            return CustomAction.RunResult(success=False)
        
        # 从 attach 字段获取参数
        attach_data = node_data.get('attach', {})
        print(f"[ExecuteBbcTask] attach_data={attach_data}")
        run_count = attach_data.get('run_count')
        apple_type = attach_data.get('apple_type')
        support_order_mismatch = attach_data.get('support_order_mismatch', False)
        team_config_error = attach_data.get('team_config_error', False)
        
        if run_count is None or apple_type is None:
            print(f"[ExecuteBbcTask] 错误：参数不完整，run_count={run_count}, apple_type={apple_type}")
            return CustomAction.RunResult(success=False)
        
        run_count = int(run_count)
        print(f"[ExecuteBbcTask] run_count={run_count}, apple_type={apple_type}, support_order_mismatch={support_order_mismatch}, team_config_error={team_config_error}")
        
        print(f"[ExecuteBbcTask] run_count={run_count}, apple_type={apple_type}")
        
        # 2. 启动BBC进程
        bbc_exe_path = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
        if not os.path.exists(bbc_exe_path):
            print(f"BBC可执行文件不存在: {bbc_exe_path}")
            return CustomAction.RunResult(success=False)
        
        print("[2/3] 启动 BBC 进程...")
        subprocess.Popen(bbc_exe_path, shell=True)
        
        # 3. 执行BBC战斗并监控结束
        print("[3/3] 执行BBC战斗并监控结束...")
        if not self._execute_bbc_battle(context, run_count, apple_type, support_order_mismatch, team_config_error):
            print("[ExecuteBbcTask] 错误：BBC战斗执行失败")
            return CustomAction.RunResult(success=False)
        
        print("ExecuteBbcTask: 任务已完成")
        return CustomAction.RunResult(success=True)
    
    def _execute_bbc_battle(self, context, run_count, apple_type, support_order_mismatch=False, team_config_error=False):
        """执行BBC战斗流程 - 使用 HTTP API 和 TCP 通信"""
        try:
            # 等待 BBC HTTP 服务就绪
            print("[BBC] 等待 HTTP 服务就绪...")
            if not _wait_for_bbc_services(timeout=30):
                print("[BBC] HTTP 服务未就绪")
                return False
            
            # 连接 TCP 服务接收弹窗
            print("[BBC] 连接 TCP 服务...")
            tcp_client = BbcTcpClient()
            if not tcp_client.connect(timeout=10):
                print("[BBC] TCP 连接失败")
                return False
            
            # 存储弹窗处理配置和状态
            popup_config = {
                'support_order_mismatch': support_order_mismatch,
                'team_config_error': team_config_error,
                'battle_ended': False  # 战斗结束标志
            }
            
            # 战斗结束弹窗标题
            BATTLE_END_POPUPS = ['脚本停止！', '正在结束任务！', '未设置等级需求', '其他任务运行中']
            
            def handle_popup(popup_data):
                """处理弹窗事件"""
                popup_id = popup_data.get('id')
                title = popup_data.get('title', '')
                
                print(f"[Popup] 收到弹窗: {title}")
                
                # 检查是否是战斗结束弹窗
                for end_title in BATTLE_END_POPUPS:
                    if end_title in title:
                        print(f"[Popup] 检测到战斗结束弹窗: {title}")
                        popup_config['battle_ended'] = True
                        # 发送确认关闭弹窗
                        _popup_response(popup_id, 'ok')
                        return
                
                # 免责声明 - BBC 端自动处理
                if '免责声明' in title:
                    print("[Popup] 免责声明 - 等待 BBC 自动处理")
                    return
                
                # 助战排序不符合
                if '助战排序不符合' in title:
                    action = 'ok' if popup_config['support_order_mismatch'] else 'cancel'
                    print(f"[Popup] 助战排序不符合 - 发送 {action} 决策")
                    _popup_response(popup_id, action)
                    return
                
                # 队伍配置错误
                if '队伍配置错误' in title:
                    action = 'ok' if popup_config['team_config_error'] else 'cancel'
                    print(f"[Popup] 队伍配置错误 - 发送 {action} 决策")
                    _popup_response(popup_id, action)
                    return
                
                # 自动连接失败 - askokcancel 类型
                if '自动连接失败' in title:
                    # 确定=重试(ok)，取消=中断(cancel，视为结束)
                    action = 'ok'  # 默认重试
                    print(f"[Popup] 自动连接失败 - 发送 {action} 决策")
                    _popup_response(popup_id, action)
                    return
            
            tcp_client.popup_callbacks.append(handle_popup)
            tcp_client.start_listening()
            
            # 通过 HTTP API 设置运行参数
            print(f"[BBC] 设置运行次数: {run_count}")
            _http_post('/set/runcount', {'times': run_count})
            
            apple_type_map = {
                '金苹果': 'gold', '银苹果': 'silver', '蓝苹果': 'blue',
                '铜苹果': 'copper', '彩苹果': 'colorful'
            }
            api_apple_type = apple_type_map.get(apple_type, 'gold')
            print(f"[BBC] 设置苹果类型: {api_apple_type}")
            _http_post('/set/appletype', {'type': api_apple_type})
            
            print("[BBC] 设置战斗类型: continuous")
            _http_post('/set/battletype', {'type': 'continuous'})
            
            # 启动战斗
            print("[BBC] 启动战斗...")
            result = _http_post('/start')
            if not result.get('success'):
                print(f"[BBC] 启动战斗失败: {result}")
                tcp_client.stop()
                return False
            
            # 监控战斗结束
            print("[BBC] 开始监控战斗...")
            battle_ended = self._monitor_battle_http(tcp_client, popup_config)
            
            # 停止 TCP 客户端
            tcp_client.stop()
            
            return battle_ended
            
        except Exception as e:
            print(f"[BBC] 执行战斗流程出错: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _monitor_battle_http(self, tcp_client: BbcTcpClient, popup_config: dict) -> bool:
        """通过 TCP 弹窗消息监控战斗结束"""
        while True:
            time.sleep(1)
            
            # 检查是否收到战斗结束弹窗
            if popup_config.get('battle_ended'):
                print("[Monitor] 收到战斗结束弹窗，战斗结束")
                return True