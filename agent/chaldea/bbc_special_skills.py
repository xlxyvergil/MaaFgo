"""Offline BBC menu instructions and complete native choice queues.

No BBC process, controller, online metadata client or editor package is imported.
The native registry owns defaults and menu membership. Optional local editor
facts narrow strengthened skill variants; missing facts fail closed only when
their queue is explicitly activated by this import.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_CORE = _module("_bbc_import_special_core", Path(__file__).resolve().parents[1] / "battle/core/special_skills.py")
SPECIAL_SKILL_GROUPS = _CORE.SPECIAL_SKILL_GROUPS
_SPACE_ISHTAR = "战斗_仇凛2技能_色卡选择"
_NAMED = {
    "Prelati": ("战斗_普雷拉蒂1技能_不换形态选择", {"不替换": 0, "替换": 1}),
    "Lilith": ("战斗_莉莉丝3技能_不赋予选择", {"不付与即死": 0, "付与即死": 1}),
}


def _group_facts():
    # File loading avoids importing battle_editor.__init__/server. Android may
    # omit the whole editor; ordinary BBC imports still do not activate a queue.
    path = Path(__file__).resolve().parents[1] / "battle_editor/special_choices.py"
    if not path.is_file():
        return {}
    module = _module("_bbc_import_special_facts", path)
    return {row["id"]: row for row in module.special_skill_catalog()["groups"]}


def _read_servants(root):
    path = Path(root) / "agent/battle_editor/data/catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return {}
    rows = data.get("servants", []) if isinstance(data, dict) else []
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            continue
        for sid in [row["id"], *(row.get("aliases") or [])]:
            if str(sid).isascii() and str(sid).isdigit():
                result[int(sid)] = row
    return result


class BbcSpecialSkillChoices:
    """Collect every menu occurrence before emitting either activated queue.

    ``convert`` handles supported BBC special opcodes and records their explicit
    choices. For ordinary converted skills call ``observe`` with the roster at
    that action point. Finally merge ``delegate()`` into the Chaldea delegate.
    ``skill_metadata`` and ``group_metadata`` are optional offline test inputs.
    """

    def __init__(self, root=ROOT, *, skill_metadata=None, group_metadata=None):
        self.skill_metadata = _read_servants(root) if skill_metadata is None else skill_metadata
        self.group_metadata = _group_facts() if group_metadata is None else group_metadata
        self.events = []
        self.explicit_fields = set()
        self._seen = {}

    def _match(self, action, roster):
        slot, index = action.get("svt"), action.get("skill")
        if type(slot) is not int or not 0 <= slot <= 2 or type(index) is not int or not 0 <= index <= 2:
            return None, None
        member = roster[slot] if slot < len(roster) else None
        if not isinstance(member, dict) or type(member.get("svtId")) is not int or member["svtId"] <= 0:
            return None, None
        meta = self.skill_metadata.get(member["svtId"], self.skill_metadata.get(str(member["svtId"]), {}))
        meta = meta if isinstance(meta, dict) else {}
        identities = {str(member["svtId"]), str(meta.get("id"))}
        aliases = meta.get("aliases")
        if isinstance(aliases, list):
            identities.update(str(sid) for sid in aliases)
        group = next((group for group in SPECIAL_SKILL_GROUPS
                      if index + 1 in group.skills and identities.intersection(group.servant_ids)), None)
        if group is None:
            return None, None
        facts = self.group_metadata.get(group.root)
        if not isinstance(facts, dict):
            return group, None
        allowed = facts.get("skillIds")
        if not allowed:
            return group, True
        selected = member.get("skillIds")
        chosen = selected[index] if isinstance(selected, list) and index < len(selected) else None
        if chosen is None or type(chosen) is int and chosen == 0:
            chosen = next((row.get("id") for row in meta.get("skills", [])
                           if isinstance(row, dict) and row.get("num") == index + 1), None)
        return group, chosen in allowed if type(chosen) is int and chosen > 0 else None

    def _record(self, action, roster, location, value=None, *, explicit=False):
        if id(action) in self._seen:
            return
        self._seen[id(action)] = action
        group, applicable = self._match(action, roster)
        if group is None:
            return
        if explicit:
            self.explicit_fields.add(group.field)
        self.events.append((group, value if explicit else group.default, applicable, location))

    def convert(self, raw, roster, location):
        if not isinstance(raw, list) or not raw:
            return None
        opcode = raw[0]
        if opcode == "BBDubai":
            raise ValueError(f"{location}: BBC BB迪拜专属选项尚无对应原生菜单，不能忽略选项当作普通技能导入")
        if isinstance(opcode, str) and opcode in _NAMED:
            root, values = _NAMED[opcode]
            if len(raw) != 3 or type(raw[1]) is not int or not 1 <= raw[1] <= 9 or not isinstance(raw[2], str) or raw[2] not in values:
                raise ValueError(f"{location}: {opcode} 专属指令的技能号或选项无效: {raw!r}")
            number, choice = raw[1], values[raw[2]]
        elif type(opcode) is int and opcode < 0:
            root = _SPACE_ISHTAR
            number = -opcode
            if (len(raw) != 2 or not 1 <= number <= 9 or (number - 1) % 3 != 1
                    or type(raw[1]) is not int or not 1 <= raw[1] <= 3):
                raise ValueError(f"{location}: 仅支持太空伊什塔尔 S2 的 BBC 负号改色指令；不能推测其他负号技能: {raw!r}")
            # BBC clicks left-to-right Q/A/B, not Chaldea CardType A/B/Q.
            choice = (3, 1, 2)[raw[1] - 1]
        else:
            return None
        action = {"type": "skill", "svt": (number - 1) // 3, "skill": (number - 1) % 3}
        group, applicable = self._match(action, roster)
        if group is None or group.root != root:
            raise ValueError(f"{location}: {raw!r} 与当前前排的真实从者及技能位置不匹配")
        if applicable is not True:
            raise ValueError(f"{location}: 无法确认特殊技能的强化版本／菜单资料，不能猜测选项")
        self._record(action, roster, location, choice, explicit=True)
        return action

    def observe(self, action, roster, location):
        if isinstance(action, dict) and action.get("type") == "skill":
            self._record(action, roster, location)

    def delegate(self):
        result = {}
        for field in sorted(self.explicit_fields):
            values = []
            for group, value, applicable, location in self.events:
                if group.field != field:
                    continue
                if applicable is not True:
                    raise ValueError(f"{location}: 同类特殊技能存在强化前或未知菜单，{field} 无法安全配对；请明确技能版本后再导入")
                values.append(value)
            result[field] = values
        return result
