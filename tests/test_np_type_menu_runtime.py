"""Actor-scoped NP menus fail safely and defaults do not alter replay queues."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.core.models import ServantSkillAction
from battle.core.policy import StrategyProfile
from battle.core.special_skills import SPECIAL_SKILL_GROUPS, TD, SELECT, SpecialSkillChoiceError, SpecialSkillChoices
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.runtime.runtime import AutoBattleRuntime
from battle.runtime.special_skills import ENTRY, NO_OVERLAY, NP_IDENTITY_GUARD, SKIP, SpecialSkillDispatcher

PIPELINE = json.loads((ROOT / "assets/resource/base/pipeline/自动战斗_特殊技能.json").read_text(encoding="utf-8"))
GROUPS = {group.servant_ids[0]: group for group in SPECIAL_SKILL_GROUPS if group.field == TD}


def runtime(roster=("2300600", "200100", "1100900", None, None, None), plan=None):
    context = NS(get_node_data=Mock(side_effect=lambda name: copy.deepcopy(PIPELINE.get(name, {}))),
                 run_task=Mock(), tasker=NS(stopping=False))
    instance = AutoBattleRuntime(context, object(), NS(plan=plan), StrategyProfile(), roster_servant_ids=roster)

    def selected(entry, pipeline_override):
        gates = [name for name in pipeline_override if name.endswith("_Chaldea选项检查")]
        assert len(gates) == 1, gates
        gate = gates[0]
        option = pipeline_override[gate]["next"][0]
        return detail([gate, option])

    context.run_task.side_effect = selected
    return instance


def detail(names):
    return NS(entry=ENTRY, status=NS(succeeded=True, failed=False),
              nodes=[NS(name=name, completed=True) for name in names])


class NpTypeMenuRuntimeTests(unittest.TestCase):
    def test_new_np_menu_clicks_use_recognized_boxes_and_only_full_parent_gates(self):
        entry = PIPELINE[ENTRY]["next"]
        for sid in ("2300600", "200100"):
            group = GROUPS[sid]
            self.assertLess(entry.index(group.root), entry.index(GROUPS["1100900"].root))
            self.assertEqual(PIPELINE[group.root]["recognition"]["type"], "And")
            for option in group.options:
                recognition = PIPELINE[option]["recognition"]
                self.assertEqual(recognition["type"], "OCR" if sid == "2300600" else "Or")
                if sid == "200100":
                    choices = recognition["param"]["any_of"]
                    self.assertEqual([item["recognition"]["type"] for item in choices], ["ColorMatch", "OCR"])
                    self.assertEqual(choices[0]["recognition"]["param"]["method"], 4)
                self.assertIs(PIPELINE[option]["action"]["param"]["target"], True)
            instance = runtime()
            slot = 1 if sid == "2300600" else 2
            instance._run_special_skill_pipeline(skill=ServantSkillAction(slot, 3))
            override = instance.ctx.run_task.call_args.kwargs["pipeline_override"]
            probes = override[group.root + "_Chaldea选项检查"]["recognition"]["param"]["any_of"]
            self.assertEqual(probes, [group.root])

    def test_unconfigured_new_menus_use_actor_default_without_creating_a_replay_cursor(self):
        for sid, slot in (("2300600", 1), ("200100", 2)):
            with self.subTest(sid=sid):
                instance = runtime()
                group = GROUPS[sid]
                instance._run_special_skill_pipeline(skill=ServantSkillAction(slot, 3))
                override = instance.ctx.run_task.call_args.kwargs["pipeline_override"]
                self.assertEqual(override[group.root + "_Chaldea选项检查"]["next"], [group.options[0]])
                self.assertNotIn("战斗_技能目标子屏", override[ENTRY]["next"])
                self.assertFalse(hasattr(instance, "_special_choice_dispatcher"))

    def test_defaults_leave_another_configured_field_and_its_position_untouched(self):
        plan = convert_chaldea_actions_to_battle_plan(
            [{"type": "skill", "svt": 0, "skill": 2}, {"type": "attack", "attacks": []}],
            {SELECT: [1, 0]},
        )
        instance = runtime(plan=plan)
        cursor = instance._special_choice_dispatcher = SpecialSkillDispatcher(plan.special_choices)
        cursor.positions[SELECT] = 1
        instance._run_special_skill_pipeline(skill=ServantSkillAction(1, 3))
        self.assertIs(instance._special_choice_dispatcher, cursor)
        self.assertEqual(cursor.positions, {TD: 0, SELECT: 1})

    def test_explicit_bb_emiya_and_space_ishtar_share_the_same_td_cursor(self):
        actions = [{"type": "skill", "svt": slot, "skill": index}
                   for slot, index in ((0, 2), (1, 2), (2, 1))]
        plan = convert_chaldea_actions_to_battle_plan(actions + [{"type": "attack", "attacks": []}], {TD: [2, 1, 3]})
        instance = runtime(plan=plan)
        expected = [("2300600", 1, 3, 1), ("200100", 2, 3, 0), ("1100900", 3, 2, 2)]
        for sid, slot, skill_index, option_index in expected:
            instance._run_special_skill_pipeline(skill=ServantSkillAction(slot, skill_index))
            group = GROUPS[sid]
            override = instance.ctx.run_task.call_args.kwargs["pipeline_override"]
            self.assertEqual(override[group.root + "_Chaldea选项检查"]["next"], [group.options[option_index]])
            self.assertFalse(any(other.root in override[ENTRY]["next"]
                                 for other in GROUPS.values() if other.root != group.root))
        self.assertEqual(instance._special_choice_dispatcher.positions[TD], 3)

    def test_unknown_actor_never_routes_a_generic_color_menu_or_an_ally_target(self):
        for configured in (False, True):
            plan = convert_chaldea_actions_to_battle_plan(
                [{"type": "skill", "svt": 0, "skill": 2}, {"type": "attack", "attacks": []}],
                {TD: [2]},
            ) if configured else None
            instance = runtime(roster=(None,) * 6, plan=plan)
            instance.ctx.run_task.side_effect = None
            instance.ctx.run_task.return_value = detail([NP_IDENTITY_GUARD])
            with self.subTest(configured=configured), self.assertRaisesRegex(SpecialSkillChoiceError, "无法安全点击"):
                instance._run_special_skill_pipeline(skill=ServantSkillAction(1, 3))
            override = instance.ctx.run_task.call_args.kwargs["pipeline_override"]
            self.assertFalse(any(group.root in override[ENTRY]["next"] for group in GROUPS.values()))
            self.assertLess(override[ENTRY]["next"].index(NP_IDENTITY_GUARD),
                            override[ENTRY]["next"].index("战斗_技能目标子屏"))
            self.assertNotIn("選択", override["战斗_技能目标子屏"]["recognition"]["param"]["expected"])
            if configured:
                self.assertEqual(instance._special_choice_dispatcher.positions[TD], 0)

    def test_pre_upgrade_emiya_direct_cast_and_unavailable_default_do_not_require_a_menu(self):
        for node in (NO_OVERLAY, *SKIP):
            instance = runtime()
            instance.ctx.run_task.side_effect = None
            instance.ctx.run_task.return_value = detail([node])
            with self.subTest(node=node):
                self.assertEqual(instance._run_special_skill_pipeline(skill=ServantSkillAction(2, 3)), node)
                self.assertFalse(hasattr(instance, "_special_choice_dispatcher"))

    def test_explicit_choice_still_requires_a_real_menu_for_emiya(self):
        plan = convert_chaldea_actions_to_battle_plan(
            [{"type": "skill", "svt": 1, "skill": 2}, {"type": "attack", "attacks": []}], {TD: [1]},
        )
        instance = runtime(plan=plan)
        instance.ctx.run_task.side_effect = None
        instance.ctx.run_task.return_value = detail([NO_OVERLAY])
        with self.assertRaisesRegex(SpecialSkillChoiceError, "未检测到"):
            instance._run_special_skill_pipeline(skill=ServantSkillAction(2, 3))
        self.assertEqual(instance._special_choice_dispatcher.positions[TD], 0)

    def test_a_recognized_but_unconfirmed_new_menu_never_falls_back_to_default(self):
        instance = runtime()
        instance.ctx.run_task.side_effect = None
        instance.ctx.run_task.return_value = detail([NP_IDENTITY_GUARD])
        with self.assertRaisesRegex(SpecialSkillChoiceError, "无法安全点击"):
            instance._run_special_skill_pipeline(skill=ServantSkillAction(1, 3))


if __name__ == "__main__":
    unittest.main()
