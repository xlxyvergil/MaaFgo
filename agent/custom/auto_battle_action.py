"""MFW Custom Action 入口：原生自动战斗（V1b）。

与 bbc_action 并列，作为可选战斗后端；不与 bbc_* 相互 import。
默认不改变现有 pipeline，需在节点显式使用 custom_action="auto_battle"。

节点参数（custom_action_param，JSON）示例：
  {"strategy_profile":"farm-safe-v1","max_turns":20,"save_evidence":true}
  {"chaldea_import_source":"<链接/ID/压缩数据>", "max_turns":20}
"""
import os
import sys

# 让 agent/battle 可作为顶层包导入（main.py 只把 custom 目录加进了 path）
_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

import mfaalog
from battle.core.decider import RuleDecider
from battle.core.models import BattlePlan
from battle.core.plan_parser import (_load_action_param, _parse_battle_policy,
                                    _parse_plan, _parse_strategy_profile)
from battle.core.policy import StrategyProfile
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.runtime.runtime import AutoBattleRuntime
from chaldea import fetch_share_data


@AgentServer.custom_action("auto_battle")
class AutoBattleAction(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        param = _load_action_param(argv.custom_action_param)
        profile = _parse_strategy_profile(param)
        battle_policy = _parse_battle_policy(param)
        plan = _parse_plan(param)
        if plan is None:
            plan = _plan_from_chaldea_share(param)
        decider = RuleDecider(battle_policy, plan=plan)

        # Agent 模式下，每次调用 context.tasker.controller 都会通过反向 IPC
        # 获取一个新的 handle，只有第一次有效。因此在这里获取一次并传递下去。
        controller = context.tasker.controller

        plan_status = "loaded" if plan is not None else "none"
        plan_turns = len(plan.turns) if plan is not None else 0
        mfaalog.info(
            f"[auto_battle] start profile={profile.id} "
            f"max_turns={profile.max_turns} plan={plan_status} plan_turns={plan_turns}"
        )
        result = AutoBattleRuntime(context, controller, decider, profile, battle_policy).run()
        mfaalog.info(f"[auto_battle] end ok={result.ok} reason={result.reason} turns={result.turns}")

        # TODO(save_evidence)：失败时保存截图/状态证据
        return CustomAction.RunResult(success=result.ok)


def _plan_from_chaldea_share(param: dict) -> BattlePlan | None:
    """从参数解析出 Chaldea 来源并转成 BattlePlan。

    支持两种形态（显式 ``plan`` 优先级更高，由调用方保证已在此前解析）：
    1. ``chaldea_share``：已解码的 BattleShareData dict（离线注入用）。
    2. ``chaldea_import_source``：链接/ID/压缩串，复用 agent/chaldea 的
       fetch_share_data 下载 + 解码（team_id/quest_id 走 API，data= 离线）。
    """
    share = param.get("chaldea_share")
    if isinstance(share, dict):
        return _build_plan_from_share(share)

    source = param.get("chaldea_import_source")
    if isinstance(source, str) and source.strip():
        share_data, _quest_id, _team_id = fetch_share_data(source.strip())
        if not share_data:
            mfaalog.info("[auto_battle] chaldea_import_source 下载/解码失败")
            return None
        return _build_plan_from_share(share_data)

    return None


def _build_plan_from_share(share: dict) -> BattlePlan:
    """把 BattleShareData actions 转成 BattlePlan。"""
    actions = share.get("actions")
    mystic_code_id = (share.get("mysticCode") or {}).get("id")
    plan = convert_chaldea_actions_to_battle_plan(
        actions,
        delegate=share.get("delegate"),
        mystic_code_id=mystic_code_id,
    )
    mfaalog.info(f"[auto_battle] chaldea_share -> plan turns={len(plan.turns)}")
    return plan
