import sys

from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

import bbc_action
from navigation_action import ExecuteNavigation

def main():
    Toolkit.init_option("./")

    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)
        
    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    
    # 注册导航Action
    AgentServer.register_custom_action("ExecuteNavigation", ExecuteNavigation())
    
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
