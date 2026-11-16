"""护符召唤次数与完成状态；界面识别、点击和重试由 Pipeline 承载。"""
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

import mfaalog


PREFIX = "自动抽护符-"
ENTRY = PREFIX + "单次入口"
DONE = PREFIX + "单次完成"


@AgentServer.custom_action("ticket_summon")
class TicketSummonAction(CustomAction):
    def run(self, context, argv):
        completed = 0
        try:
            data = context.get_node_data(argv.node_name) or {}
            count = (data.get("attach") or {}).get("ticket_count", 1)
            if isinstance(count, bool) or not str(count).isdigit() or not 1 <= int(count) <= 9999:
                mfaalog.error("[自动抽护符] 抽取次数不是 1～9999 的整数，无法开始召唤。请在‘抽取次数’中填写要消耗的护符张数后重试。")
                return CustomAction.RunResult(success=False)
            count = int(count)
            # run_task 共用命中计数；只重置本流程，每张护符重新获得单次提交和有界重试预算。
            nodes = data["attach"]["reset_hit_nodes"]
            for index in range(count):
                if context.tasker.stopping:
                    return CustomAction.RunResult(success=False)
                for name in nodes:
                    context.clear_hit_count(name)
                mfaalog.info(f"[自动抽护符] 准备使用第 {index + 1}/{count} 张护符（2回召唤也只消耗1张）。")
                detail = context.run_task(ENTRY)
                if context.tasker.stopping:
                    return CustomAction.RunResult(success=False)
                if not detail or not detail.status.succeeded or context.get_hit_count(DONE) != 1:
                    mfaalog.error(f"[自动抽护符] 流程已停止，已确认完成 {completed}/{count} 次。若已点击‘决定’但未看到结果，请先查看游戏内‘召唤履历’确认本次是否已消耗护符，再填写剩余次数重试。")
                    return CustomAction.RunResult(success=False)
                completed += 1
                mfaalog.info(f"[自动抽护符] 已完成 {completed}/{count} 次，已返回当前卡池。")
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            mfaalog.error(f"[自动抽护符] 执行中断，已确认完成 {completed} 次：{exc}。请先查看游戏内‘召唤履历’确认已消耗的护符，再填写剩余次数重试。")
            return CustomAction.RunResult(success=False)
