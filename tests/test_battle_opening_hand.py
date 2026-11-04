"""Opening-hand waiting delays the entire fixed plan without spending its skills/NP."""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
from battle.core.decider import RuleDecider
from battle.core.enums import CardColor as Color, PrimitiveKind as Kind, Scene
from battle.core.models import (BattlePlan, BattleState, CommandCard, Confidence, NpCard,
                                PlannedCard, ServantSkillAction, ServantState, SkillState, TurnPlan)
from battle.core.plan_parser import _parse_battle_policy
from battle.core.policy import BattlePolicy, CardPolicy, NativeCardStrategy, OpeningHandPolicy, SkillPolicy, StrategyProfile
from battle.runtime.runtime import AutoBattleRuntime

ROSTER = ("300500", "605300", "504500", "205500", None, None)
OPENING = OpeningHandPolicy(True, "605300", None, (Color.BUSTER, Color.QUICK), 2)
PLAN = BattlePlan((TurnPlan(servant_skills=(ServantSkillAction(2, 1),),
                           card_sequence=(PlannedCard(Kind.SELECT_NP, 2),
                                          PlannedCard(Kind.SELECT_CARD, 2, Color.BUSTER),
                                          PlannedCard(Kind.SELECT_CARD, 2, Color.QUICK))),
                   TurnPlan(servant_skills=(ServantSkillAction(3, 1),))))


def main():
    servants = tuple(ServantState(slot, (SkillState(True, Confidence(1)),) * 3,
                                 Confidence(1), ROSTER[slot - 1]) for slot in (1, 2, 3))
    return BattleState(Scene.MAIN_BATTLE, Confidence(1), (), (), (), servants=servants)


def command(*, ready=True):
    description = ((Color.BUSTER, 2), (Color.QUICK, 2), (Color.ARTS, 1), (Color.ARTS, 3), (Color.BUSTER, 1)) if ready else (
        (Color.ARTS, 2), (Color.ARTS, 1), (Color.QUICK, 3), (Color.ARTS, 3), (Color.BUSTER, 1))
    cards = tuple(CommandCard(slot, color, owner, Confidence(1), Confidence(1), owner_servant_id=ROSTER[owner - 1])
                  for slot, (color, owner) in enumerate(description, 1))
    return BattleState(Scene.COMMAND_SELECTION, Confidence(1), cards,
                       (NpCard(2, Confidence(1), owner_servant_id="605300"),), ())


def victory():
    return BattleState(Scene.VICTORY, Confidence(1), (), (), ())


def runtime_for(observations, *, opening=OPENING, enabled=True, max_turns=20, plan=PLAN):
    native = NativeCardStrategy(enabled=enabled, preferred_servant_ids=("605300",), opening_hand=opening)
    policy = BattlePolicy(CardPolicy(native_strategy=native), SkillPolicy(auto_servant_skills=False))
    decider = RuleDecider(policy, plan)
    decider.decide = Mock(wraps=decider.decide)
    events = []
    context = NS(tasker=NS(stopping=False), run_task=Mock(side_effect=lambda node: (
        events.append(("pipeline", node)) or NS(status=NS(succeeded=True)))))
    runtime = AutoBattleRuntime(context, Mock(), decider, StrategyProfile(max_turns=max_turns), policy,
                                roster_servant_ids=ROSTER)
    runtime._observe = Mock(side_effect=observations)
    runtime.executor = NS(open_command_cards=Mock(side_effect=lambda: events.append(("open", runtime._turn_index)) or True))
    runtime._wait_until = Mock(return_value=True)
    runtime._execute_skills = Mock(side_effect=lambda action: events.append(("skills", runtime._turn_index, action)) or True)
    runtime._execute_selection = Mock(side_effect=lambda action: events.append(("cards", runtime._turn_index, action)) or True)
    runtime._wait_turn_settled = Mock(return_value=True)
    runtime._post_attack_returned_to_map = False
    return runtime, events


class OpeningHandParserTests(unittest.TestCase):
    def parse(self, value):
        return _parse_battle_policy({"card_policy": {"native_strategy": {"enabled": True, "opening_hand": value}}}).card.native_strategy.opening_hand

    def test_defaults_are_disabled_and_valid_target_colors_budget_parse(self):
        self.assertFalse(_parse_battle_policy({}).card.native_strategy.opening_hand.enabled)
        parsed = self.parse({"enabled": True, "servant_id": 605300, "required_colors": ["B", "Q"], "max_wait_turns": 2})
        self.assertEqual(parsed, OPENING)
        self.assertEqual(self.parse({"enabled": True, "slot": 2, "required_colors": ["B", "B"], "max_wait_turns": 0}).servant_slot, 2)

    def test_bad_constraints_cannot_silently_become_a_weaker_required_hand(self):
        for colors in (["B", "invalid"], [], "BQ", ["B"] * 4):
            self.assertEqual(self.parse({"enabled": True, "required_colors": colors}).required_colors, ())
        for budget in (True, "2", -1, 6):
            self.assertEqual(self.parse({"enabled": True, "max_wait_turns": budget}).max_wait_turns, -1)


class OpeningHandRuntimeTests(unittest.TestCase):
    def test_ready_hand_returns_to_confirmed_main_before_first_skill_then_preserves_plan(self):
        runtime, events = runtime_for([main(), command(), main(), command(), victory()])
        result = runtime.run()
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.turns, 1)
        self.assertEqual([event[0] for event in events], ["open", "pipeline", "skills", "open", "cards"])
        self.assertEqual(events[1], ("pipeline", "战斗_选卡返回主界面"))
        picks = runtime._execute_selection.call_args.args[0].picks
        self.assertEqual([(pick.kind, pick.slot) for pick in picks], [(Kind.SELECT_NP, 2), (Kind.SELECT_CARD, 1), (Kind.SELECT_CARD, 2)])
        self.assertEqual(runtime.decider.plan, PLAN)

    def test_waiting_uses_no_skill_or_np_and_keeps_plan_turn_zero_until_hand_is_ready(self):
        runtime, events = runtime_for([main(), command(ready=False), main(), command(), main(), command(),
                                       main(), command(), victory()])
        result = runtime.run()
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.turns, 3)
        selected = [call.args[0] for call in runtime._execute_selection.call_args_list]
        self.assertTrue(all(pick.kind is Kind.SELECT_CARD for pick in selected[0].picks))
        self.assertEqual(selected[0].rationale_tag, "opening_hand:wait_without_skills_or_np")
        self.assertEqual(selected[1].picks[0].kind, Kind.SELECT_NP)
        self.assertEqual([call.kwargs["turn_index"] for call in runtime.decider.decide.call_args_list], [0, 0, 1, 1])
        self.assertEqual([event[1] for event in events if event[0] == "skills"], [0, 1])
        self.assertLess(next(i for i, event in enumerate(events) if event[0] == "cards"),
                        next(i for i, event in enumerate(events) if event[0] == "skills"))

    def test_wait_budget_exhaustion_stops_on_cards_without_burning_plan(self):
        runtime, _ = runtime_for([main(), command(ready=False), main(), command(ready=False)],
                                  opening=replace(OPENING, max_wait_turns=1))
        result = runtime.run()
        self.assertEqual((result.ok, result.reason, result.turns), (False, "opening_hand_wait_exhausted", 1))
        runtime._execute_skills.assert_not_called()
        self.assertEqual(runtime._execute_selection.call_count, 1)
        runtime.ctx.run_task.assert_not_called()

    def test_zero_budget_checks_once_without_spending_a_wait_turn(self):
        runtime, _ = runtime_for([main(), command(ready=False)], opening=replace(OPENING, max_wait_turns=0))
        self.assertEqual(runtime.run().reason, "opening_hand_wait_exhausted")
        runtime._execute_skills.assert_not_called()
        runtime._execute_selection.assert_not_called()

    def test_unknown_card_owner_color_or_target_identity_stops_before_waiting(self):
        base = command()
        bad_cards = (replace(base.cards[0], owner_slot=None), replace(base.cards[0], color=Color.UNKNOWN),
                     replace(base.cards[0], owner_confidence=Confidence(0)),
                     replace(base.cards[0], owner_servant_id="504500"))
        for bad in bad_cards:
            with self.subTest(card=bad):
                runtime, _ = runtime_for([main(), replace(base, cards=(bad,) + base.cards[1:])])
                self.assertEqual(runtime.run().reason, "opening_hand_hand_identity_or_color_unknown")
                runtime._execute_selection.assert_not_called()
                runtime._execute_skills.assert_not_called()
        runtime, _ = runtime_for([main(), command()], opening=replace(OPENING, servant_id="99999999"))
        self.assertEqual(runtime.run().reason, "opening_hand_target_identity_unknown")

    def test_mid_battle_command_entry_is_explicitly_unsupported_for_opening_gate(self):
        runtime, _ = runtime_for([command()])
        self.assertEqual(runtime.run().reason, "opening_hand_requires_main_battle_start")
        runtime._execute_selection.assert_not_called()

    def test_required_color_multiplicity_and_blocked_cards_are_checked(self):
        runtime, _ = runtime_for([])
        duplicated = replace(OPENING, required_colors=(Color.BUSTER, Color.BUSTER))
        ready, reason, _, _ = runtime._opening_hand_matches(command(), duplicated)
        self.assertFalse(ready)
        self.assertEqual(reason, "known_hand")
        cards = list(command().cards)
        cards[1] = replace(cards[1], color=Color.BUSTER)
        self.assertTrue(runtime._opening_hand_matches(replace(command(), cards=tuple(cards)), duplicated)[0])
        cards[1] = replace(cards[1], can_attack=False)
        self.assertFalse(runtime._opening_hand_matches(replace(command(), cards=tuple(cards)), duplicated)[0])

    def test_intentionally_empty_initial_slots_are_distinct_from_a_member_departure(self):
        runtime, _ = runtime_for([])
        solo = ("605300", None, None, None, None, None)
        runtime._initial_servant_ids = runtime.roster_servant_ids = solo
        self.assertTrue(runtime._opening_roster_unchanged())
        runtime.roster_servant_ids = (None,) * 6
        self.assertFalse(runtime._opening_roster_unchanged())

    def test_return_must_succeed_before_plan_skills_or_np_are_used(self):
        runtime, _ = runtime_for([main(), command()])
        runtime.ctx.run_task.side_effect = None
        runtime.ctx.run_task.return_value = NS(status=NS(succeeded=False))
        self.assertEqual(runtime.run().reason, "opening_hand_return_failed")
        runtime._execute_skills.assert_not_called()
        runtime._execute_selection.assert_not_called()

    def test_waiting_counts_against_actual_max_turns(self):
        runtime, _ = runtime_for([main(), command(ready=False)], max_turns=1)
        result = runtime.run()
        self.assertEqual((result.reason, result.turns), ("max_turns_exceeded", 1))
        runtime._execute_skills.assert_not_called()

    def test_frontline_replacement_member_order_change_or_retirement_aborts_delayed_plan(self):
        for change in ("roster", "order", "retire"):
            with self.subTest(change=change):
                runtime, _ = runtime_for([])
                frames = iter([main(), command(ready=False), main()])
                count = [0]

                def observe(**_):
                    count[0] += 1
                    if count[0] == 3:
                        if change == "roster":
                            runtime.roster_servant_ids = ROSTER[:2] + ("205500",) + ROSTER[3:]
                        elif change == "order":
                            runtime._member_order[:2] = [1, 0]
                        else:
                            runtime._retired_instances.add(0)
                    return next(frames)

                runtime._observe.side_effect = observe
                self.assertEqual(runtime.run().reason, "opening_hand_roster_changed_or_unknown")
                runtime._execute_skills.assert_not_called()

    def test_disabled_native_strategy_preserves_legacy_immediate_skill_behavior(self):
        runtime, events = runtime_for([main(), command(ready=False), victory()], enabled=False)
        self.assertTrue(runtime.run().ok)
        self.assertEqual([event[0] for event in events[:2]], ["skills", "open"])
        runtime.ctx.run_task.assert_not_called()

    def test_missing_plan_and_invalid_enabled_constraints_stop_before_any_device_action(self):
        for plan, opening in ((None, OPENING), (PLAN, replace(OPENING, required_colors=())),
                              (PLAN, replace(OPENING, max_wait_turns=-1))):
            runtime, _ = runtime_for([], plan=plan, opening=opening)
            self.assertEqual(runtime.run().reason, "invalid_opening_hand_config_or_missing_plan")
            runtime.executor.open_command_cards.assert_not_called()


if __name__ == "__main__":
    unittest.main()
