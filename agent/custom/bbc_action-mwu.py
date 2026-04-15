import json
import os
import time
import socket
import struct
import subprocess
import logging
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 配置日志输出到文件
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(AGENT_DIR, 'bbc_debug.log')

# 创建具名 logger，只写入 bbc_debug.log，不影响其他模块
logger = logging.getLogger("BbcAction")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # 禁止向根 logger 传播，避免污染其他模块的日志
    _fh = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(_fh)


# BBC TCP 配置
BBC_TCP_HOST = "127.0.0.1"
BBC_TCP_PORT = 25001

# 固定 BBC 路径 - 使用相对于 agent 根目录的路径
AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# MWU 环境下 BBC 位于 resource/BBchannel
BBC_PATH = os.path.join(AGENT_ROOT, '..', 'resource', 'BBchannel')
# 正式版本
BBC_EXE_PATH = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
# 调试版本（测试用，会显示控制台窗口）
# BBC_EXE_PATH = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel_debug.exe')

# 确保路径是绝对的并存在
BBC_EXE_PATH = os.path.abspath(BBC_EXE_PATH)
print(f"[BBC] BBC 路径：{BBC_EXE_PATH}")
print(f"[BBC] BBC 存在：{os.path.exists(BBC_EXE_PATH)}")

# TCP客户端管理（非全局，由Action自行管理）
# 移除全局单例，避免模块导入时创建线程锁


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


# ==================== Action: 执行BBC任务（整合版）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC任务 - 根据连接方式执行相应流程"""

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
        
        # 提取所有参数
        team_config = attach_data.get('bbc_team_config', '')
        run_count = attach_data.get('run_count')
        apple_type = attach_data.get('apple_type')
        battle_type = attach_data.get('battle_type', '连续出击')
        connect = attach_data.get('connect', 'auto')
        support_order_mismatch = attach_data.get('support_order_mismatch', False)
        team_config_error = attach_data.get('team_config_error', False)
        
        # 连接相关参数
        mumu_path = attach_data.get('mumu_path', '')
        mumu_index = attach_data.get('mumu_index', 0)
        mumu_pkg = attach_data.get('mumu_pkg', 'com.bilibili.fatego')
        mumu_app_index = attach_data.get('mumu_app_index', 0)
        ld_path = attach_data.get('ld_path', '')
        ld_index = attach_data.get('ld_index', 0)
        manual_port = attach_data.get('manual_port', '')
        
        # 验证必需参数
        if not team_config:
            print(f"[ExecuteBbcTask] 错误：未提供队伍配置文件路径")
            return CustomAction.RunResult(success=False)
        
        if run_count is None or apple_type is None:
            print(f"[ExecuteBbcTask] 错误：参数不完整，run_count={run_count}, apple_type={apple_type}")
            return CustomAction.RunResult(success=False)
        
        run_count = int(run_count)
        print(f"[ExecuteBbcTask] team_config={team_config}, run_count={run_count}, apple_type={apple_type}, battle_type={battle_type}, connect={connect}")
        
        # 执行完整BBC流程（启动+配置+战斗）
        success, popup_message = self._execute_full_bbc_flow(
            team_config, run_count, apple_type, battle_type, connect,
            support_order_mismatch, team_config_error,
            mumu_path, mumu_index, mumu_pkg, mumu_app_index,
            ld_path, ld_index, manual_port)

        if success:
            print(f"[ExecuteBbcTask] 执行成功，返回消息: {popup_message}")

            # 【直接在这里根据消息决定下一步去哪】
            if "羁绊" in popup_message:
                context.override_next("执行BBC任务", ["BBC弹窗-羁绊"])
            elif "测试" in popup_message:  # 假设你想匹配其他关键字
                context.override_next("执行BBC任务", ["BBC弹窗-测试"])
            else:
                # 如果没有匹配到特殊的弹窗，走默认的 next
                pass

            return CustomAction.RunResult(success=True)
        else:
            return CustomAction.RunResult(success=False)

    def _execute_full_bbc_flow(self, team_config, run_count, apple_type, battle_type, connect,
                                support_order_mismatch, team_config_error,
                                mumu_path, mumu_index, mumu_pkg, mumu_app_index,
                                ld_path, ld_index, manual_port):
        """执行完整BBC流程：启动 -> 配置 -> 战斗"""
        try:
            # ========== 步骤1: 启动BBC ==========
            print("[BBC] 步骤1: 启动BBC...")
            
            # 检查BBC可执行文件
            if not os.path.exists(BBC_EXE_PATH):
                print(f"[BBC] BBC可执行文件不存在: {BBC_EXE_PATH}")
                return False
            
            # 启动 BBC 进程
            print("[BBC] 启动 BBC 进程...")
            print(f"[BBC] BBC 路径：{BBC_EXE_PATH}")
            logger.info(f"[BBC] 启动 BBC 进程，路径：{BBC_EXE_PATH}")
                        
            # 切换到 BBC 所在目录再启动
            bbc_dir = os.path.dirname(BBC_EXE_PATH)
            # debug 版需要 CREATE_NEW_CONSOLE，否则从无控制台的父进程启动时看不到输出
            _is_debug = BBC_EXE_PATH.endswith('_debug.exe')
            _creation_flags = subprocess.CREATE_NEW_CONSOLE if _is_debug else 0
            proc = subprocess.Popen([BBC_EXE_PATH], cwd=bbc_dir, creationflags=_creation_flags)
            logger.info(f"[BBC] 已启动进程，PID: {proc.pid}")
                        
            print("[BBC] BBC 启动命令已发送")
            
            # 启动后直接尝试连接TCP，循环重试直到成功或超时
            print("[BBC] 启动后尝试连接 TCP 服务...")
            tcp_client = BbcTcpClient()
            
            connect_start_time = time.time()
            connect_timeout = 30  # 总超时30秒
            connected = False
            
            while time.time() - connect_start_time < connect_timeout:
                if tcp_client.connect(timeout=1):  # 单次连接超时1秒
                    connected = True
                    break
                time.sleep(0.2)  # 失败间隔0.2秒
            
            if not connected:
                print("[BBC] TCP 连接失败，超时")
                return False
            
            print("[BBC] TCP 连接成功，发送任务参数...")
            
            # 直接发送 run_bbc_task 命令，在服务端执行完整流程
            result = tcp_client.send_command('run_bbc_task', {
                'team_config': team_config,
                'run_count': run_count,
                'apple_type': apple_type,
                'battle_type': battle_type,
                'connect': connect,
                'support_order_mismatch': support_order_mismatch,
                'team_config_error': team_config_error,
                'mumu_path': mumu_path,
                'mumu_index': int(mumu_index),
                'mumu_pkg': mumu_pkg,
                'mumu_app_index': int(mumu_app_index),
                'ld_path': ld_path,
                'ld_index': int(ld_index),
                'manual_port': manual_port
            }, timeout=None)  # 无超时，等待任务完成
            
            tcp_client.stop()
            
            logger.info(f"[BBC] TCP响应: {result}")
            
            if result.get('success'):
                popup_title = result.get('popup_title', '')
                popup_message = result.get('popup_message', '')
                user_decision = result.get('user_decision', '')
                logger.info(f"[BBC] 任务执行成功: {popup_title}, message={popup_message}")
                print(f"[BBC] 任务执行成功: {popup_title}")
                if popup_message:
                    print(f"[BBC] 详情: {popup_message}")
                return True, popup_message
            else:
                reason = result.get('reason', 'unknown')
                error = result.get('error', '')
                popup_title = result.get('popup_title', '')
                popup_message = result.get('popup_message', '')
                user_decision = result.get('user_decision', '')
                result_info = result.get('result', {})
                
                if popup_title or popup_message:
                    # 有弹窗信息，显示具体原因
                    display_title = popup_title if popup_title else '任务已取消'
                    # 用户选择映射为中文
                    decision_map = {
                        'ok': '确定',
                        'cancel': '取消',
                        'yes': '是',
                        'no': '否',
                        'retry': '重试'
                    }
                    friendly_decision = decision_map.get(user_decision, user_decision)
                    logger.error(f"[BBC] 任务失败: {display_title}, {popup_message}, 用户选择: {friendly_decision}")
                    print(f"[BBC] 任务失败: {display_title}")
                    if popup_message:
                        print(f"[BBC] 详情: {popup_message}")
                elif error:
                    logger.error(f"[BBC] 任务执行失败: {error}")
                    print(f"[BBC] 任务执行失败: {error}")
                elif result_info:
                    description = result_info.get('description', '')
                    logger.error(f"[BBC] 任务执行失败: {reason}, {description}")
                    print(f"[BBC] 任务执行失败: {reason}")
                    if description:
                        print(f"[BBC] 错误信息: {description}")
                else:
                    logger.error(f"[BBC] 任务执行失败: {reason}")
                    print(f"[BBC] 任务执行失败: {reason}")
                return False, popup_message
            
        except Exception as e:
            print(f"[BBC] 执行战斗流程出错: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            # 杀掉 BBC 进程
            try:
                if proc.poll() is None:
                    print(f"[BBC] 终止 BBC 进程 PID: {proc.pid}")
                    proc.terminate()
                    proc.wait(timeout=5)
                    if proc.poll() is None:
                        proc.kill()
                        print(f"[BBC] 强制杀死 BBC 进程")
                else:
                    print(f"[BBC] BBC 进程已结束")
            except Exception as e:
                print(f"[BBC] 终止进程时出错: {e}")
    
