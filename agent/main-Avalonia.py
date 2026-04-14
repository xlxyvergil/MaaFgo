import os
import sys

# 设置工作目录为项目根目录（agent 的上级目录）
current_file_path = os.path.abspath(__file__)
current_script_dir = os.path.dirname(current_file_path)
project_root_dir = os.path.dirname(current_script_dir)
if os.getcwd() != project_root_dir:
    os.chdir(project_root_dir)

# 将脚本目录添加到 sys.path
if current_script_dir not in sys.path:
    sys.path.insert(0, current_script_dir)

from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

import bbc_action
import sequential_tasks_action
import general_navigation_action


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
