import sys
import os

# ================= 1. 环境初始化 (兼容阿瓦隆与MWU) =================
AGENT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 添加依赖库路径 (MWU 规范: agent/libs/)
LIBS_PATH = os.path.join(AGENT_ROOT, 'libs')
if os.path.exists(LIBS_PATH) and LIBS_PATH not in sys.path:
    sys.path.insert(0, LIBS_PATH)

# 添加自定义业务逻辑路径 (MWU 规范: agent/custom/)
CUSTOM_PATH = os.path.join(AGENT_ROOT, 'custom')
if os.path.exists(CUSTOM_PATH) and CUSTOM_PATH not in sys.path:
    sys.path.insert(0, CUSTOM_PATH)

print(f"[Agent Init] Root: {AGENT_ROOT}")
print(f"[Agent Init] Libs loaded: {os.path.exists(LIBS_PATH)}")
print(f"[Agent Init] Custom loaded: {os.path.exists(CUSTOM_PATH)}")

# ================= 2. 导入核心模块 =================
from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

# 导入自定义 Action (从 custom 目录)
import bbc_action
import sequential_tasks_action
import general_navigation_action


def main():
    # 设置工作目录为项目根目录（兼容阿瓦隆）
    project_root_dir = os.path.dirname(AGENT_ROOT)
    if os.getcwd() != project_root_dir:
        os.chdir(project_root_dir)
        print(f"[Agent] Working directory changed to: {project_root_dir}")

    # 初始化 MaaToolkit 选项
    Toolkit.init_option("./")

    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)

    socket_id = sys.argv[-1]
    print(f"[Agent] Starting Agent Server with socket_id: {socket_id}")

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
