import sys
import os

# 将 agent 目录添加到 Python 路径，确保能导入同级模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

# 先导入自定义 Action 模块，让装饰器注册
import bbc_action
import sequential_tasks_action


def main():
    Toolkit.init_option("./")

    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)

    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()