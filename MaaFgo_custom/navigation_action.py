"""
导航Action - 使用FGO-py导航逻辑
"""
from maa.context import Context
from maa.custom_action import CustomAction
from .fgo_device import FgoDevice
from .fgo_nav import reishift


class ExecuteNavigation(CustomAction):
    """
    执行关卡导航
    
    参数:
        quest: 关卡标识，如 "1-0-3-0" (冬木X-D)
    
    导航完成后返回成功
    """
    
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        # 获取参数
        quest_str = argv.custom_action_param
        if not quest_str:
            context.run_task("Toast", {"message": "导航失败：未指定关卡"})
            return CustomAction.RunResult(success=False)
        
        # 解析quest tuple
        try:
            quest = tuple(int(x) for x in quest_str.split("-"))
        except ValueError:
            context.run_task("Toast", {"message": f"导航失败：无效的关卡标识 {quest_str}"})
            return CustomAction.RunResult(success=False)
        
        # 创建设备接口
        device = FgoDevice(context)
        
        # 执行导航
        try:
            context.run_task("Toast", {"message": f"开始导航到 {quest_str}"})
            reishift(quest, device)
            context.run_task("Toast", {"message": f"导航完成：{quest_str}"})
            return CustomAction.RunResult(success=True)
        except Exception as e:
            context.run_task("Toast", {"message": f"导航失败：{str(e)}"})
            return CustomAction.RunResult(success=False)
