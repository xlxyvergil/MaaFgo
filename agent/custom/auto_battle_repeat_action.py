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
           → 战斗结束关闭_不回主界面 → 战斗完成-回主界面

节点参数（custom_action_param，JSON）示例：
  {"battle_count": 5}
  {"battle_count": 3, "chaldea_import_source": "https://chaldea.center/team?id=17300", "max_turns": 20}

参数说明：
  battle_count (int): 战斗次数，默认 1，范围 1~999。可通过 custom_action_param
    或入口节点（原生自动战斗_多次入口）的 attach.battle_count 注入，前者优先。
  reset_hit_nodes (list[str], 可选): 每场战斗前需要重置命中计数的节点列表。
    默认值为包含 max_hit=1 的关键节点，确保多场战斗时节点可重复触发。
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

# 默认需要重置命中计数的节点（所有 max_hit=1 且跨场复用的节点）
# 这些节点在第 1 场战斗后命中计数达到上限，第 2 场起会被跳过
_DEFAULT_RESET_HIT_NODES = [
    "执行原生自动战斗",
    "执行战斗结束_不回主界面",
    "执行战斗结束_连续出击继续",
    "执行进本",
    "跳过剧情-点击跳过",
    "点击关卡"
]


@AgentServer.custom_action("auto_battle_repeat")
class AutoBattleRepeatAction(CustomAction):
    """多次原生自动战斗循环控制器（连续出击模式）。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        param = _load_param(argv.custom_action_param)

        # "原生自动战斗次数" option 通过 attach 注入 battle_count（attach 与 action
        # 是不同字段, 不会被 "Chaldea导入" 等 option 对 action 的 override 覆盖）。
        # 优先级: custom_action_param > 节点 attach。
        if "battle_count" not in param:
            try:
                node_data = context.get_node_data("原生自动战斗_多次入口") or {}
                attach = node_data.get("attach") or {}
                if "battle_count" in attach:
                    param["battle_count"] = attach["battle_count"]
                    mfaalog.info(f"[auto_battle_repeat] battle_count 来自节点 attach: {attach['battle_count']}")
            except Exception as e:
                mfaalog.warn(f"[auto_battle_repeat] 读取节点 attach 失败: {e}")

        battle_count = _parse_battle_count(param)
        reset_hit_nodes = _parse_reset_hit_nodes(param)

        # 需要透传给内层 auto_battle 的参数（去掉 battle_count 和 reset_hit_nodes）
        inner_param = {k: v for k, v in param.items() if k not in ("battle_count", "reset_hit_nodes")}
        battle_override = _build_battle_override(inner_param)

        mfaalog.info(
            f"[auto_battle_repeat] start battle_count={battle_count} "
            f"has_override={bool(battle_override)} reset_nodes={len(reset_hit_nodes)}"
        )
        if inner_param:
            mfaalog.info(f"[auto_battle_repeat] inner_param: {json.dumps(inner_param, ensure_ascii=False)[:200]}")

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

            # 在每场战斗前重置节点命中计数（修复 bug：Context.task_state_ 是共享的）
            _reset_hit_counts(context, reset_hit_nodes)

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


def _parse_reset_hit_nodes(param: dict) -> list[str]:
    """解析需要重置命中计数的节点列表。
    
    Args:
        param: custom_action_param 字典
    
    Returns:
        节点名列表。若参数未提供或为空，返回默认列表。
    """
    nodes = param.get("reset_hit_nodes")
    
    # 未提供或为 None：使用默认列表
    if nodes is None:
        return _DEFAULT_RESET_HIT_NODES.copy()
    
    # 提供了但不是 list：使用默认列表（容错）
    if not isinstance(nodes, list):
        mfaalog.warn(f"[auto_battle_repeat] reset_hit_nodes 应为 list，实际类型 {type(nodes)}，使用默认值")
        return _DEFAULT_RESET_HIT_NODES.copy()
    
    # 提供了空列表：尊重用户意图，不重置任何节点
    if not nodes:
        mfaalog.info("[auto_battle_repeat] reset_hit_nodes=[] 显式指定，将不重置任何节点")
        return []
    
    # 过滤出字符串元素（容错）
    valid_nodes = [n for n in nodes if isinstance(n, str) and n.strip()]
    if len(valid_nodes) != len(nodes):
        mfaalog.warn(
            f"[auto_battle_repeat] reset_hit_nodes 中有非字符串元素，"
            f"已过滤（{len(nodes)} -> {len(valid_nodes)}）"
        )
    
    return valid_nodes


def _reset_hit_counts(context: Context, node_names: list[str]) -> None:
    """重置指定节点的命中计数。
    
    Args:
        context: MaaFramework Context 对象
        node_names: 节点名列表
    """
    if not node_names:
        return
    
    for node_name in node_names:
        context.clear_hit_count(node_name)
    
    mfaalog.debug(f"[auto_battle_repeat] 已重置 {len(node_names)} 个节点的命中计数")


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
