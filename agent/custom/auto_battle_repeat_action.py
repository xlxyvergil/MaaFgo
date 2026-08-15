"""MFW Custom Action：原生自动战斗 — 多次循环（连续出击模式）。

利用 FGO 连续出击机制，在战斗结算时点「继续」直接进入下一场，
而非每场都回主界面重新进本。

流程：
  第 1 场: 执行进本 → auto_battle → 结束战斗 → 作战成功(点下一步)
           → 连续出击_继续(点继续按钮) → auto_battle (第 2 场)
  第 2 场: auto_battle → 结束战斗 → 作战成功(点下一步)
           → 连续出击_继续(点继续按钮) → auto_battle (第 3 场)
  ...
 最后 1 场: auto_battle → 结束战斗 → 作战成功(点下一步)
           → 关闭连续出击 → 战斗完成-回主界面

节点参数（custom_action_param，JSON）示例：
  {"battle_count": 5}
  {"battle_count": 3, "chaldea_import_source": "https://chaldea.center/team?id=17300", "max_turns": 20}

参数说明：
  battle_count (int): 战斗次数，默认 1，范围 1~999
  其余参数（chaldea_import_source / max_turns / strategy_profile / card_policy / skill_policy）
  会透传给 auto_battle custom action。
"""
import json
import os
import sys

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

import mfaalog

# pipeline 节点名
_NODE_FIRST_BATTLE = "原生自动战斗_多次_第一场"
_NODE_NEXT_BATTLE = "原生自动战斗_多次_下一场"
_NODE_LAST_BATTLE = "原生自动战斗_多次_最后一场"

# 安全上限
_MAX_BATTLE_COUNT = 999


@AgentServer.custom_action("auto_battle_repeat")
class AutoBattleRepeatAction(CustomAction):
    """多次原生自动战斗循环控制器（连续出击模式）。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        param = _load_param(argv.custom_action_param)
        battle_count = _parse_battle_count(param)

        # 需要透传给内层 auto_battle 的参数（去掉 battle_count 自身）
        inner_param = {k: v for k, v in param.items() if k != "battle_count"}
        battle_override = _build_battle_override(inner_param)

        mfaalog.info(
            f"[auto_battle_repeat] start battle_count={battle_count} "
            f"has_override={bool(battle_override)}"
        )

        success_count = 0
        last_error = ""

        for i in range(battle_count):
            current = i + 1
            is_first = (i == 0)
            is_last = (i == battle_count - 1)
            mfaalog.info(f"[auto_battle_repeat] === Battle {current}/{battle_count} ===")

            # 选择对应的 pipeline 入口节点
            if is_first and is_last:
                # 只有 1 场：走原始完整流程（进本 → 战斗 → 结算 → 回主界面）
                entry = "原生自动战斗调度"
            elif is_first:
                # 第一场（非最后一场）：进本 → 战斗 → 结算 → 连续出击继续
                entry = _NODE_FIRST_BATTLE
            elif is_last:
                # 最后一场（非第一场）：战斗 → 结算 → 关闭连续出击 → 回主界面
                entry = _NODE_LAST_BATTLE
            else:
                # 中间场：战斗 → 结算 → 连续出击继续
                entry = _NODE_NEXT_BATTLE

            try:
                detail = context.run_task(entry, pipeline_override=battle_override)
            except Exception as e:
                last_error = f"异常: {e}"
                mfaalog.error(f"[auto_battle_repeat] Battle {current} {last_error}")
                break

            if detail is None:
                last_error = "run_task 返回 None（入口节点不存在或启动失败）"
                mfaalog.error(f"[auto_battle_repeat] Battle {current} {last_error}")
                break

            if detail.status.failed:
                last_error = f"pipeline 状态 failed (entry={detail.entry})"
                mfaalog.error(f"[auto_battle_repeat] Battle {current} 失败: {last_error}")
                break

            if not detail.status.succeeded:
                last_error = "pipeline 状态异常"
                mfaalog.error(f"[auto_battle_repeat] Battle {current} {last_error}")
                break

            success_count += 1
            mfaalog.info(
                f"[auto_battle_repeat] Battle {current}/{battle_count} 成功 "
                f"(累计 {success_count})"
            )

        ok = success_count == battle_count
        mfaalog.info(
            f"[auto_battle_repeat] end ok={ok} "
            f"success_count={success_count}/{battle_count}"
        )

        # 通过 pipeline_override 输出结果信息到 GUI
        if not ok:
            display_text = (
                f"战斗中断：已完成 {success_count}/{battle_count} 场"
                + (f"（{last_error}）" if last_error else "")
            )
            context.override_pipeline({
                "bbc弹窗信息输出": {
                    "focus": {
                        "Node.Recognition.Starting":
                            f'<span style="color: #FF0000;">{display_text}</span>'
                    }
                }
            })
        else:
            context.override_pipeline({
                "bbc弹窗信息输出": {
                    "focus": {
                        "Node.Recognition.Starting":
                            f'<span style="color: #008000;">'
                            f'原生自动战斗完成：共 {success_count} 场'
                            f'</span>'
                    }
                }
            })

        return CustomAction.RunResult(success=ok)


def _load_param(raw_param: object) -> dict:
    """将 Maa 传入的 custom_action_param 规范化为 dict。"""
    if isinstance(raw_param, dict):
        return raw_param
    if not isinstance(raw_param, str) or not raw_param.strip():
        return {}
    try:
        parsed = json.loads(raw_param)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_battle_count(param: dict) -> int:
    """解析战斗次数，非法值回退到 1。"""
    raw = param.get("battle_count", 1)
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return 1
    try:
        count = int(raw)
    except (ValueError, TypeError):
        return 1
    return max(1, min(count, _MAX_BATTLE_COUNT))


def _build_battle_override(inner_param: dict) -> dict:
    """构建 pipeline_override，将内层参数注入到战斗入口节点。

    三个调度节点（第一场/下一场/最后一场）的 next 都指向「原生自动战斗入口」，
    后者的 action 是 Custom(auto_battle)，参数来自 custom_action_param。

    如果 inner_param 非空，则覆盖「原生自动战斗入口」的 custom_action_param；
    如果为空（纯默认策略），则不做任何覆盖，使用 pipeline 原始定义。
    """
    if not inner_param:
        return {}

    return {
        "原生自动战斗入口": {
            "action": {
                "type": "Custom",
                "param": {
                    "custom_action": "auto_battle",
                    "custom_action_param": inner_param,
                },
            },
        },
    }
