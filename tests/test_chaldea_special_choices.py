"""Replay choices keep their own queues and do not alter ordinary skill targets."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.core.models import BattleAction, OrderChangeAction, ServantSkillAction, TurnPlan
from battle.core.enums import Scene
from battle.core.policy import StrategyProfile
from battle.core.special_skills import (SPECIAL_SKILL_GROUPS, SELECT, TD, SpecialSkillChoiceError,
                                       SpecialSkillChoices, parse_special_skill_choices)
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.runtime.special_skills import SpecialSkillDispatcher
from battle.runtime.runtime import AutoBattleRuntime
from battle_editor.validation import validate_battle


class ChaldeaSpecialChoicesTests(unittest.TestCase):
    def test_official_card_values_and_legacy_names_are_not_button_positions(self):
        self.assertEqual(parse_special_skill_choices({"tdTypeChanges": [1, 2, 3, "arts", "buster", "quick"]}),
                         SpecialSkillChoices((1, 2, 3, 1, 2, 3)))

    def test_invalid_values_are_rejected_instead_of_becoming_defaults(self):
        for field, values in ((TD, [0, 4, True, None, "1", "bad"]),
                              (SELECT, [-1, 3, True, None, "0", 1.0])):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(SpecialSkillChoiceError):
                    parse_special_skill_choices({field: [value]})
        with self.assertRaises(SpecialSkillChoiceError):
            parse_special_skill_choices({TD: "arts"})

    def test_converter_preserves_both_queues_without_mutating_share(self):
        actions = [{"type": "skill", "svt": 0, "skill": 1, "options": {"playerTarget": 2, "enemyTarget": 0}},
                   {"type": "attack", "attacks": [{"svt": 0, "isTD": True}]}]
        delegate = {TD: [2, 3], SELECT: [1, 0]}
        before = copy.deepcopy((actions, delegate))
        plan = convert_chaldea_actions_to_battle_plan(actions, delegate)
        self.assertEqual(plan.special_choices, SpecialSkillChoices((2, 3), (1, 0)))
        self.assertEqual(plan.turns[0].servant_skills, (ServantSkillAction(1, 2, 3, 3),))
        self.assertEqual((actions, delegate), before)
        self.assertEqual(convert_chaldea_actions_to_battle_plan(actions).special_choices, SpecialSkillChoices())

    def test_registry_has_existing_nodes_and_covers_every_special_root(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/自动战斗_特殊技能.json").read_text("utf-8"))
        roots = {group.root for group in SPECIAL_SKILL_GROUPS}
        common = {"战斗_技能使用弹窗", "战斗_技能无法使用弹窗", "战斗_技能目标子屏", "战斗_特殊技能_无覆盖层"}
        self.assertEqual(set(nodes["战斗_特殊技能处理"]["next"]) - common, roots)
        for group in SPECIAL_SKILL_GROUPS:
            for option in group.options:
                self.assertIn(option, nodes)
            self.assertEqual(group.servant_ids, tuple(nodes["战斗_特殊技能处理"]["attach"]["special_skill_servant_ids"][group.root]))

    def test_editor_accepts_supported_choices_and_keeps_simulation_controls_blocked(self):
        data = {"team": {"onFieldSvts": [{"svtId": 1100900}, None, None], "backupSvts": [None] * 3},
                "delegate": {TD: ["buster"], SELECT: [1]},
                "actions": [{"type": "skill", "svt": 0, "skill": 1}, {"type": "attack", "attacks": []}]}
        self.assertTrue(validate_battle(data)["valid"], validate_battle(data))
        data["delegate"][SELECT] = [True]
        self.assertIn("invalid_special_choice", {issue["code"] for issue in validate_battle(data)["issues"]})
        data["delegate"] = {"canActivateDecisions": [True]}
        self.assertIn("unsupported_delegate", {issue["code"] for issue in validate_battle(data)["issues"]})

    def test_filtered_special_skill_stops_before_queue_can_shift(self):
        special, normal = ServantSkillAction(1, 2), ServantSkillAction(2, 1)
        dispatcher = SpecialSkillDispatcher(SpecialSkillChoices((2,)))
        turn = TurnPlan(servant_skills=(special, normal), skill_sequence=(special, normal))
        action = BattleAction(None, (), servant_skills=(normal,))
        with self.assertRaisesRegex(SpecialSkillChoiceError, "被跳过"):
            dispatcher.check_skipped(turn, action, ("1100900", "100100", None, None, None, None))
        dispatcher.check_skipped(turn, BattleAction(None, (), servant_skills=(special,)),
                                 ("1100900", "100100", None, None, None, None))
        self.assertEqual(dispatcher.positions[TD], 0)

    def test_swap_uses_the_incoming_servant_when_detecting_a_skipped_special(self):
        swap, special = OrderChangeAction(1, 4), ServantSkillAction(1, 2)
        dispatcher = SpecialSkillDispatcher(SpecialSkillChoices((2,)))
        turn = TurnPlan(servant_skills=(special,), skill_sequence=(swap, special))
        with self.assertRaisesRegex(SpecialSkillChoiceError, "被跳过"):
            dispatcher.check_skipped(turn, BattleAction(None, ()), ("100100", None, None, "1100900", None, None))

    def runtime(self, *, show_menu=True):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/自动战斗_特殊技能.json").read_text("utf-8"))
        group = SPECIAL_SKILL_GROUPS[0]
        names = [group.root + "_Chaldea选项检查", group.options[1]] if show_menu else ["战斗_特殊技能_无覆盖层"]
        context = NS(get_node_data=Mock(side_effect=lambda name: copy.deepcopy(nodes.get(name, {}))),
                     run_task=Mock(return_value=NS(status=NS(succeeded=True, failed=False),
                                                  nodes=[NS(name=name, completed=True) for name in names])),
                     tasker=NS(stopping=False))
        plan = convert_chaldea_actions_to_battle_plan(
            [{"type": "skill", "svt": 0, "skill": 1}, {"type": "attack", "attacks": []}], {TD: [2]},
        )
        runtime = AutoBattleRuntime(context, object(), NS(plan=plan), StrategyProfile(),
                                    roster_servant_ids=("1100900", None, None, None, None, None))
        runtime.executor = NS(cast_servant_skill=Mock(return_value=True))
        runtime._mark_action = Mock()
        runtime._wait_until = Mock(return_value=True)
        return runtime, plan

    def test_runtime_consumes_imported_choice_then_restores_legacy_after_plan(self):
        runtime, plan = self.runtime()
        skill = plan.turns[0].servant_skills[0]
        action = BattleAction(None, (), servant_skills=(skill,), skill_sequence=(skill,))
        with patch("battle.runtime.runtime.time.sleep"):
            self.assertTrue(runtime._execute_skills(action))
        gate = SPECIAL_SKILL_GROUPS[0].root + "_Chaldea选项检查"
        override = runtime.ctx.run_task.call_args.kwargs["pipeline_override"]
        self.assertEqual(override[gate]["next"], ["战斗_仇凛2技能_选B"])
        self.assertEqual(runtime._special_choice_dispatcher.positions[TD], 1)
        runtime._turn_index = 1
        runtime._run_special_skill_pipeline(skill=skill)
        self.assertNotIn(gate, runtime.ctx.run_task.call_args.kwargs["pipeline_override"])
        self.assertEqual(runtime._special_choice_dispatcher.positions[TD], 1)

    def test_runtime_failure_does_not_wait_or_proceed_to_cards(self):
        runtime, plan = self.runtime(show_menu=False)
        skill = plan.turns[0].servant_skills[0]
        with patch("battle.runtime.runtime.time.sleep"):
            self.assertFalse(runtime._execute_skill_cast("cast", lambda: True, None, (Scene.MAIN_BATTLE,), 15, skill=skill))
        runtime._wait_until.assert_not_called()
        self.assertEqual(runtime._special_choice_dispatcher.positions[TD], 0)

    def test_runtime_does_not_cast_when_a_configured_special_was_filtered(self):
        runtime, _ = self.runtime()
        self.assertFalse(runtime._execute_skills(BattleAction(None, ())))
        runtime.executor.cast_servant_skill.assert_not_called()
        runtime.ctx.run_task.assert_not_called()

    def test_runtime_pipeline_exception_is_a_choice_failure(self):
        runtime, plan = self.runtime()
        runtime.ctx.run_task.side_effect = RuntimeError("IPC unavailable")
        with patch("battle.runtime.runtime.time.sleep"):
            self.assertFalse(runtime._execute_skill_cast(
                "cast", lambda: True, None, (Scene.MAIN_BATTLE,), 15, skill=plan.turns[0].servant_skills[0],
            ))
        runtime._wait_until.assert_not_called()


if __name__ == "__main__":
    unittest.main()
