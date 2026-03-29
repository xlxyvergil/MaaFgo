import sys

from maa.agent.agent_server import AgentServer
from maa.tasker import Tasker

# 先导入自定义 Action 模块，让装饰器注册
import bbc_action
import sequential_tasks_action


def main():
    try:
        Tasker.set_log_dir("./debug")

        if len(sys.argv) < 2:
            print("Usage: python main.py <socket_id>")
            print("socket_id is provided by AgentIdentifier.")
            sys.exit(1)

        socket_id = sys.argv[-1]
        print(f"[Agent] 启动参数: socket_id={socket_id}")
        
        # 修复 MaaFramework TCP/IPC 识别 bug：纯数字 socket_id 会被错误识别为 IPC
        # 在纯数字前添加 tcp_ 前缀强制使用 TCP 模式
        if socket_id.isdigit():
            socket_id = f"tcp_{socket_id}"
            print(f"[Agent] 检测到纯数字 socket_id，已添加前缀: {socket_id}")

        print(f"[Agent] 正在启动 AgentServer...")
        AgentServer.start_up(socket_id)
        print(f"[Agent] AgentServer 启动成功，开始监听...")
        AgentServer.join()
        AgentServer.shut_down()
    except Exception as e:
        print(f"[Agent] 启动失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
