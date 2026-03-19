from app.core.runner.task_flow import TaskFlowRunner
from app.core.service.Task_Service import TaskService
from app.core.service.Config_Service import ConfigService


class MonitorTask(TaskFlowRunner):
    """监控任务"""

    def __init__(
        self,
        task_service: TaskService,
        config_service: ConfigService,
    ):
        super().__init__(task_service, config_service, None)
        self.screen_pixmap = None

    async def _connect(self):
        from app.common.constants import _CONTROLLER_, _RESOURCE_

        controller_cfg = self.task_service.get_task(_CONTROLLER_)
        if not controller_cfg:
            raise ValueError("未找到基础预配置任务")
        return await self.connect_device(controller_cfg.task_option)