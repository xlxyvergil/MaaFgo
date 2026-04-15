"""
顺序执行任务 Action
用于依次执行多个任务，任一任务失败则停止
"""

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context


@AgentServer.custom_action("ExecuteSequentialTasks")
class ExecuteSequentialTasks(CustomAction):
    """顺序执行多个任务"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """
        依次执行任务列表
        
        custom_action_param 格式：逗号分隔的任务名
        例："回主界面,队伍选择,章节导航,队伍配置"
        """
        param_str = argv.custom_action_param if argv.custom_action_param else ""
        
        # 去除首尾引号
        param_str = param_str.strip().strip('"').strip("'")
        
        # 解析参数：逗号分隔的任务列表
        if not param_str:
            print("[ExecuteSequentialTasks] 任务列表为空")
            return CustomAction.RunResult(success=True)
        
        # 按逗号分割任务名
        tasks = [task.strip() for task in param_str.split(",") if task.strip()]
        
        if not tasks:
            print("[ExecuteSequentialTasks] 任务列表为空")
            return CustomAction.RunResult(success=True)
        
        print(f"[ExecuteSequentialTasks] 开始顺序执行 {len(tasks)} 个任务: {tasks}")
        
        # 依次执行任务
        for i, task_name in enumerate(tasks, 1):
            print(f"[ExecuteSequentialTasks] [{i}/{len(tasks)}] 执行任务: {task_name}")
            
            try:
                result = context.run_task(task_name)
                if not result or not result.status.succeeded:
                    print(f"[ExecuteSequentialTasks] 任务 '{task_name}' 执行失败，停止后续任务")
                    return CustomAction.RunResult(success=False)
                
                print(f"[ExecuteSequentialTasks] 任务 '{task_name}' 执行成功")
                
            except Exception as e:
                print(f"[ExecuteSequentialTasks] 任务 '{task_name}' 执行异常: {e}")
                return CustomAction.RunResult(success=False)
        
        print(f"[ExecuteSequentialTasks] 所有任务执行完成")
        return CustomAction.RunResult(success=True)
