"""多模态视觉 Prompt 构造。"""
from __future__ import annotations

import json

from .models import VisionRequest


SYSTEM_PROMPT = """你是 MaaFgo 战斗界面的视觉分析器。只分析截图，不执行点击，不输出坐标，不制定战术。
必须返回一个 JSON 对象，schema_version 必须为 1。无法确认的字段使用 null，并写入 unknown_fields。
槽位约束：面卡 ui_slot 为 1..5，从者/敌人 slot 为 1..3，置信度为 0..1。
scene 只能是 unknown、main_battle、command_selection、skill_target_selection、order_change、victory、defeat、dialog。
卡色只能是 B、A、Q。不要输出 Markdown、解释文字或 JSON 之外的内容。"""


def build_user_prompt(request: VisionRequest) -> str:
    context = {
        "turn_index": request.turn_index,
        "requested_fields": list(request.requested_fields),
        "recent_actions": list(request.recent_actions),
        "mfw_state": repr(request.state) if request.state is not None else None,
    }
    return "请分析附带截图，只返回符合 schema 的 JSON。上下文：\n" + json.dumps(
        context,
        ensure_ascii=False,
        default=str,
    )