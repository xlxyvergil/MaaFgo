"""Resolve replay queues against actual menus using per-task Pipeline overrides."""
from __future__ import annotations

import copy
from collections import Counter

from ..core.models import OrderChangeAction, RetireAction, ServantSkillAction
from ..core.special_skills import SPECIAL_SKILL_GROUPS, TD, SpecialSkillChoiceError


ENTRY = "战斗_特殊技能处理"
NO_OVERLAY = "战斗_特殊技能_无覆盖层"
UNKNOWN_MENU = "战斗_特殊技能_未支持选项"
SKIP = ("战斗_技能使用弹窗", "战斗_技能无法使用弹窗")


def _name(branch):
    return branch if isinstance(branch, str) else branch.get("name")


class SpecialSkillDispatcher:
    def __init__(self, choices):
        self.queues = {TD: choices.td_type_changes,
                       "skillActSelectSelections": choices.skill_act_selections}
        self.positions = {key: 0 for key in self.queues}

    def check_skipped(self, turn, action, roster):
        """Stopping is required if skill filtering would shift a later menu's queue."""
        kept = Counter(action.servant_skills)
        roster = list(roster)
        sequence = turn.skill_sequence or turn.servant_skills
        for step in sequence:
            if isinstance(step, OrderChangeAction):
                front, back = step.starting_member_idx - 1, step.sub_member_idx - 1
                roster[front], roster[back] = roster[back], roster[front]
            elif isinstance(step, RetireAction):
                front = step.servant_slot - 1
                back = next((i for i in range(3, 6) if roster[i] is not None), None)
                if back is not None:
                    # Retired identities cannot return as candidates here.
                    roster[front] = roster.pop(back)
                    roster.append(None)
            if not isinstance(step, ServantSkillAction):
                continue
            if kept[step]:
                kept[step] -= 1
                continue
            sid = roster[step.servant_slot - 1]
            if sid is None or any(
                sid in group.servant_ids and step.skill_index in group.skills and self.queues[group.field]
                for group in SPECIAL_SKILL_GROUPS
            ):
                raise SpecialSkillChoiceError(
                    f"从者{step.servant_slot}技能{step.skill_index}被跳过，特殊选项队列将错位；停止重放"
                )

    def run(self, context, override, *, servant_id=None, skill_index=None):
        """Only commit a queue item after its selected node actually completes.

        Recognition gates cannot click; their sole successor is the requested
        option. In particular a grey Kukulkan consume button cannot fall back
        to the other option. Existing target selection overrides are retained.
        """
        override = copy.deepcopy(override)
        base = context.get_node_data(ENTRY) or {}
        branches = override.get(ENTRY, {}).get("next", base.get("next", []))
        groups = {group.root: group for group in SPECIAL_SKILL_GROUPS}
        routed, pending, expected = [], {}, []
        required = [group.root for group in SPECIAL_SKILL_GROUPS
                    if servant_id in group.servant_ids and skill_index in group.skills
                    and self.queues[group.field]]
        for branch in branches:
            group = groups.get(_name(branch))
            if group is not None and servant_id is not None and (
                servant_id not in group.servant_ids or skill_index not in group.skills
            ):
                continue
            if group is None or not self.queues[group.field]:
                routed.append(branch)
                continue
            if (context.get_node_data(group.root) or {}).get("enabled") is False:
                raise SpecialSkillChoiceError(f"{group.root}: 特殊技能处理被禁用")
            position = self.positions[group.field]
            queue = self.queues[group.field]
            value = queue[position] if position < len(queue) else None
            option_index = value - group.first_value if value is not None else -1
            selected = (group.options[option_index]
                        if 0 <= option_index < len(group.options) else None)
            gate = group.root + "_Chaldea选项检查"
            # References only read recognition from the original nodes. They do
            # not execute those nodes' default actions or change global state.
            probes = []
            for name in dict.fromkeys((group.root, *group.options)):
                recognition = (context.get_node_data(name) or {}).get("recognition", {})
                if recognition and recognition.get("type", "DirectHit") != "DirectHit":
                    probes.append(name)
            if not probes:
                raise SpecialSkillChoiceError(f"{group.root}: 缺少选项弹窗识别，拒绝盲点")
            override[gate] = {
                "recognition": {"type": "Or", "param": {"any_of": probes}},
                "action": {"type": "DoNothing"},
                "next": [selected] if selected else [],
                "timeout": 3000, "on_error": [],
            }
            if selected:
                # A direct-click option (Space Ishtar) must only be reached
                # through its positively recognized parent gate.
                node = context.get_node_data(selected) or {}
                if node.get("enabled") is False:
                    selected = None
                    override[gate]["next"] = []
                else:
                    override.setdefault(selected, {})["on_error"] = []
            routed.append(gate)
            pending[gate] = (group, selected, position, value)
            if servant_id is not None:
                expected.append(gate)
        if any(root + "_Chaldea选项检查" not in expected for root in required):
            raise SpecialSkillChoiceError("当前特殊技能未在识别范围中，无法重放已配置的选项")
        if expected:
            # Target selection belongs after the configured option, never as
            # a replacement for an unrecognized skill-internal menu.
            routed = [branch for branch in routed if _name(branch) != "战斗_技能目标子屏"]
        else:
            target_node = context.get_node_data("战斗_技能目标子屏") or {}
            recognition = copy.deepcopy(target_node.get("recognition", {}))
            if recognition.get("type") == "OCR":
                param = recognition.setdefault("param", {})
                param["expected"] = [text for text in param.get("expected", []) if text != "選択"]
                override.setdefault("战斗_技能目标子屏", {})["recognition"] = recognition
        # A future/unregistered menu must not be mistaken for an ordinary ally
        # target or direct cast merely because its backdrop still matches MAIN.
        routed = [item for branch in routed for item in (
            (UNKNOWN_MENU, branch) if _name(branch) == NO_OVERLAY else (branch,)
        )]
        override.setdefault(ENTRY, {})["next"] = routed
        detail = context.run_task(ENTRY, pipeline_override=override)
        if detail is None or not detail.status.succeeded:
            raise SpecialSkillChoiceError("特殊技能选项未成功执行；已停止，未改选默认项")
        nodes = getattr(detail, "nodes", None) or []
        names = [node.name for node in nodes]
        if UNKNOWN_MENU in names:
            raise SpecialSkillChoiceError("检测到未支持或未识别的技能选项菜单，停止以免点错")
        if any(name in SKIP for name in names):
            if expected:
                raise SpecialSkillChoiceError("配置了特殊选项的技能不可用，无法准确重放攻略")
            return detail
        matched = [gate for gate in pending if gate in names]
        if expected and not matched:
            raise SpecialSkillChoiceError("未检测到当前从者技能的选项弹窗，无法确认 Chaldea 选择")
        for gate in matched:
            group, selected, position, value = pending[gate]
            if selected is None:
                reason = "队列已耗尽" if value is None else f"选项 {value} 不适用或被禁用"
                raise SpecialSkillChoiceError(f"{group.root}: {group.field}[{position}] {reason}")
            completed = [node for node in nodes if node.name == selected]
            if not completed or any(getattr(node, "completed", True) is False for node in completed):
                raise SpecialSkillChoiceError(f"{group.root}: 未确认点击配置选项 {value}")
            self.positions[group.field] += 1
        return detail
