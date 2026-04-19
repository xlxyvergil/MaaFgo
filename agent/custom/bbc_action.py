import json
import os
import time
import socket
import struct
import logging
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 配置日志输出到文件
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(AGENT_DIR, 'bbc_debug.log')

# 创建具名 logger
logger = logging.getLogger("BbcAction")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _fh = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(_fh)


# BBC TCP 配置
BBC_TCP_HOST = "127.0.0.1"
BBC_TCP_PORT = 25001


# TCP客户端管理
class BbcTcpClient:
    """BBC TCP 客户端 - 同步发送命令并等待响应"""
    
    def __init__(self):
        self.sock = None
    
    def connect(self, timeout: int = 10) -> bool:
        """连接到 BBC TCP 服务"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((BBC_TCP_HOST, BBC_TCP_PORT))
            print(f"[TCP] 已连接到 BBC TCP 服务 {BBC_TCP_HOST}:{BBC_TCP_PORT}")
            return True
        except Exception as e:
            print(f"[TCP] 连接失败: {e}")
            return False
    
    def send_command(self, cmd: str, args: dict = None, timeout: int = None) -> dict:
        """发送命令并同步等待响应"""
        if not self.sock:
            return {'success': False, 'error': 'Not connected'}
        
        data = {'cmd': cmd, 'args': args or {}}
        try:
            # 发送命令
            msg = json.dumps(data, ensure_ascii=False).encode('utf-8')
            msg_with_len = len(msg).to_bytes(4, 'big') + msg
            self.sock.sendall(msg_with_len)
            
            # 同步接收响应
            if timeout is not None:
                self.sock.settimeout(timeout)
            else:
                self.sock.settimeout(None)  # 阻塞模式，无超时
            
            length_bytes = self._recv_all(4)
            if not length_bytes:
                return {'success': False, 'error': 'Connection closed'}
            
            length = struct.unpack('>I', length_bytes)[0]
            response_data = self._recv_all(length)
            if not response_data:
                return {'success': False, 'error': 'No response data'}
            
            return json.loads(response_data.decode('utf-8'))
        except socket.timeout:
            return {'success': False, 'error': 'Timeout waiting for response'}
        except Exception as e:
            print(f"[TCP] 发送命令失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _recv_all(self, n: int) -> bytes:
        """接收指定字节数的数据"""
        data = b''
        while len(data) < n:
            try:
                packet = self.sock.recv(n - len(data))
                if not packet:
                    return None
                data += packet
            except Exception:
                return None
        return data
    
    def stop(self):
        """关闭连接"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None



# ==================== Action: 执行BBC任务（仅战斗部分）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC战斗任务 - 通过TCP发送战斗参数"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        # 从 Context 获取节点数据（包含 pipeline_override 合并后的值）
        node_data = context.get_node_data("执行BBC任务")
        print(f"[ExecuteBbcTask] node_data={node_data}")
        
        if not node_data:
            print(f"[ExecuteBbcTask] 错误：无法获取节点数据")
            return CustomAction.RunResult(success=False)
        
        # 从 attach 字段获取所有参数
        attach_data = node_data.get('attach', {})
        print(f"[ExecuteBbcTask] attach_data={attach_data}")
        
        # 提取战斗相关参数（移除连接相关参数）
        team_config = attach_data.get('bbc_team_config', '')
        run_count = attach_data.get('run_count')
        apple_type = attach_data.get('apple_type')
        battle_type = attach_data.get('battle_type', '连续出击')
        support_order_mismatch = attach_data.get('support_order_mismatch', False)
        team_config_error = attach_data.get('team_config_error', False)
        
        # 验证必需参数
        if not team_config:
            print(f"[ExecuteBbcTask] 错误：未提供队伍配置文件路径")
            return CustomAction.RunResult(success=False)
        
        if run_count is None or apple_type is None:
            print(f"[ExecuteBbcTask] 错误：参数不完整，run_count={run_count}, apple_type={apple_type}")
            return CustomAction.RunResult(success=False)
        
        run_count = int(run_count)
        print(f"[ExecuteBbcTask] team_config={team_config}, run_count={run_count}, apple_type={apple_type}, battle_type={battle_type}")
        
        # 执行BBC战斗流程（通过TCP发送战斗参数）
        popup_title, popup_message = self._execute_bbc_battle(
            team_config, run_count, apple_type, battle_type,
            support_order_mismatch, team_config_error
        )
        
        # 无论成功与否，都将弹窗信息通过 pipeline_override 输出到 JSON 节点
        if popup_title or popup_message:
            display_text = f"{popup_title}: {popup_message}" if popup_title else popup_message
            context.override_pipeline({
                "bbc弹窗信息输出": {
                    "focus": {
                        "Node.Recognition.Starting": f"<span style=\"color: #FF0000;\">{display_text}</span>"
                    }
                }
            })
            print(f"[ExecuteBbcTask] 弹窗信息已输出: {display_text}")
        
        return CustomAction.RunResult(success=True)

    def _execute_bbc_battle(self, team_config, run_count, apple_type, battle_type,
                            support_order_mismatch, team_config_error):
        """执行BBC战斗流程：事件驱动模式
        返回: (popup_title, popup_message) 元组
        """
        print("[ExecuteBbcTask] 连接 BBC TCP 服务...")
        tcp_client = BbcTcpClient()
        
        connect_start_time = time.time()
        connect_timeout = 10
        connected = False
        
        while time.time() - connect_start_time < connect_timeout:
            if tcp_client.connect(timeout=1):
                connected = True
                break
            time.sleep(0.2)
        
        if not connected:
            print("[ExecuteBbcTask] TCP 连接失败，请确认BBC已启动")
            return '', 'TCP连接失败，请确认BBC已启动'
        
        print("[ExecuteBbcTask] TCP 连接成功，开始战斗流程...")
        
        # 步骤0: 检查模拟器连接状态
        print("[ExecuteBbcTask] 步骤0: 检查模拟器连接状态...")
        status_result = tcp_client.send_command('get_status', {}, timeout=5)
        if not status_result.get('success'):
            tcp_client.stop()
            return '', '获取状态失败，BBC服务异常'
        
        if not status_result.get('device_connected'):
            tcp_client.stop()
            return '', '模拟器未连接，请先执行 start_bbc'
        
        # 执行战斗流程（一次性调用）
        print("[ExecuteBbcTask] 执行战斗流程...")
        battle_result = tcp_client.send_command('execute_battle', {
            'team_config': team_config,
            'run_count': run_count,
            'apple_type': apple_type,
            'battle_type': battle_type,
            'support_order_mismatch': support_order_mismatch,
            'team_config_error': team_config_error
        }, timeout=None)  # 无超时，等待战斗结束
        
        tcp_client.stop()
        
        if not battle_result.get('success'):
            popup_title = battle_result.get('popup_title', '')
            popup_message = battle_result.get('popup_message', battle_result.get('error', '未知错误'))
            print(f"[ExecuteBbcTask] 战斗失败: {popup_title} - {popup_message}")
            return popup_title, popup_message
        
        popup_title = battle_result.get('popup_title', '')
        popup_message = battle_result.get('popup_message', '')
        print(f"[ExecuteBbcTask] 战斗结束: {popup_title} - {popup_message}")
        return popup_title, popup_message
    
