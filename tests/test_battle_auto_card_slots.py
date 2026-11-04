"""Maa default face-card slots retain their position around planned NP cards."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle.core.decider import RuleDecider
from battle.core.enums import CardColor, PrimitiveKind, Scene
from battle.core.models import BattleState, CommandCard, Confidence, NpCard
from battle.core.policy import StrategyProfile
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.execution import coords
from battle.runtime.runtime import AutoBattleRuntime
from battle_editor.validation import DEFAULTS, validate_battle


AUTO = {"isAuto": True}
NP = {"svt": 0, "isTD": True, "card": -1}


def battle(attacks):
    return {"team": {"onFieldSvts": [{"svtId": 100100}, None, None],
                     "backupSvts": [None, None, None]},
            "actions": [{"type": "attack", "attacks": attacks}]}


class AutoCardSlotValidationTests(unittest.TestCase):
    def errors(self, data):
        return [item for item in validate_battle(data)["issues"] if item["severity"] == "error"]

    def test_default_slots_need_no_frontline_reference_and_preserve_unknown_fields(self):
        data = battle([dict(AUTO, future={"keep": [None, True]}), AUTO, NP])
        before = copy.deepcopy(data)
        report = validate_battle(data)
        self.assertTrue(report["valid"], report)
        self.assertEqual((report["summary"]["cards"], report["summary"]["noblePhantasms"]), (2, 1))
        self.assertTrue(any(item["code"] == "preserved_field" and item["path"] == "actions[0].attacks[0].future"
                            for item in report["issues"]))
        self.assertEqual(data, before)

    def test_non_boolean_auto_flags_are_blocking(self):
        for flag in (None, 0, 1, "true", "false", [], {}):
            with self.subTest(flag=flag):
                data = battle([dict(NP, isAuto=flag)])
                errors = self.errors(data)
                self.assertTrue(any(item["code"] == "invalid_boolean" and item["path"].endswith(".isAuto")
                                    for item in errors), errors)

    def test_auto_slots_reject_every_specific_card_field_even_when_null_or_false(self):
        for key in ("svt", "card", "isTD", "cardType", "critical"):
            for value in (None, False, 0):
                with self.subTest(key=key, value=value):
                    errors = self.errors(battle([dict(AUTO, **{key: value})]))
                    self.assertTrue(any(item["code"] == "conflicting_auto_card" for item in errors), errors)

    def test_false_auto_flag_keeps_normal_explicit_card_validation(self):
        self.assertFalse(self.errors(battle([dict(NP, isAuto=False)])))
        self.assertTrue(self.errors(battle([{"isAuto": False}])))

    def test_null_cards_and_more_than_three_slots_remain_invalid(self):
        self.assertTrue(any(item["code"] == "invalid_card" for item in self.errors(battle([None, NP]))))
        self.assertTrue(any(item["code"] == "too_many_cards" for item in self.errors(battle([AUTO] * 4))))

    def test_capability_description_identifies_maa_extension(self):
        self.assertIn("Maa 扩展", DEFAULTS["description"]["autoAttackCard"])
        self.assertIn("Chaldea", DEFAULTS["description"]["autoAttackCard"])


class AutoCardSlotExecutionTests(unittest.TestCase):
    def plan(self, attacks):
        return convert_chaldea_actions_to_battle_plan([{"type": "attack", "attacks": attacks}])

    def state(self, *, np_available=True, face_cards=None):
        return BattleState(
            scene=Scene.COMMAND_SELECTION, scene_confidence=Confidence(1),
            cards=tuple(face_cards) if face_cards is not None else tuple(
                CommandCard(slot, CardColor.BUSTER, None, Confidence(1)) for slot in range(1, 6)),
            np_cards=(NpCard(1, Confidence(1), available=np_available),), enemies=(),
        )

    def picks(self, attacks, **state):
        return RuleDecider(plan=self.plan(attacks)).decide(self.state(**state)).picks

    def assert_unique_faces(self, picks):
        slots = [pick.slot for pick in picks if pick.kind is PrimitiveKind.SELECT_CARD]
        self.assertEqual(len(slots), len(set(slots)))
        self.assertTrue(all(1 <= slot <= 5 for slot in slots))

    def test_default_slots_survive_conversion_without_frontline_or_color(self):
        plan = self.plan([AUTO, AUTO, NP])
        self.assertEqual([card.servant_slot for card in plan.turns[0].card_sequence], [0, 0, 1])
        self.assertEqual([card.color for card in plan.turns[0].card_sequence], [None, None, None])
        self.assertEqual(plan.turns[0].np_order, (1,))

    def test_np_in_second_slot_gets_default_faces_before_and_after(self):
        picks = self.picks([AUTO, NP])
        self.assertEqual([pick.kind for pick in picks],
                         [PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_NP, PrimitiveKind.SELECT_CARD])
        self.assertEqual([pick.slot for pick in picks], [1, 1, 2])
        self.assert_unique_faces(picks)

    def test_np_in_third_slot_gets_two_default_faces_first_without_owner_recognition(self):
        picks = self.picks([AUTO, AUTO, NP])
        self.assertEqual([pick.kind for pick in picks],
                         [PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_NP])
        self.assertEqual([pick.slot for pick in picks], [1, 2, 1])
        self.assert_unique_faces(picks)

    def test_explicit_face_is_reserved_before_filling_an_earlier_default_slot(self):
        faces = [CommandCard(1, CardColor.ARTS, 2, Confidence(1)),
                 *[CommandCard(slot, CardColor.BUSTER, None, Confidence(1)) for slot in range(2, 6)]]
        attacks = [AUTO, {"svt": 1, "card": 0, "cardType": 1}, NP]
        picks = self.picks(attacks, face_cards=faces)
        self.assertEqual([(pick.kind, pick.slot) for pick in picks],
                         [(PrimitiveKind.SELECT_CARD, 2), (PrimitiveKind.SELECT_CARD, 1),
                          (PrimitiveKind.SELECT_NP, 1)])
        self.assert_unique_faces(picks)

    def test_missing_explicit_face_is_filled_in_place_without_moving_np(self):
        picks = self.picks([AUTO, {"svt": 1, "card": 0, "cardType": 1}, NP])
        self.assertEqual([pick.kind for pick in picks],
                         [PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_NP])
        self.assertEqual([pick.slot for pick in picks], [1, 2, 1])
        self.assert_unique_faces(picks)

    def test_unavailable_np_uses_a_distinct_default_face_in_its_position(self):
        for attacks in ([AUTO, NP], [AUTO, AUTO, NP]):
            with self.subTest(attacks=attacks):
                picks = self.picks(attacks, np_available=False)
                self.assertEqual([pick.kind for pick in picks], [PrimitiveKind.SELECT_CARD] * 3)
                self.assertEqual([pick.slot for pick in picks], [1, 2, 3])
                self.assert_unique_faces(picks)

    def test_default_slots_choose_attackable_faces_first(self):
        faces = [CommandCard(slot, CardColor.BUSTER, None, Confidence(1), can_attack=slot > 2)
                 for slot in range(1, 6)]
        picks = self.picks([AUTO, AUTO, NP], face_cards=faces)
        self.assertEqual([pick.slot for pick in picks], [3, 4, 1])
        self.assert_unique_faces(picks)

    def test_malformed_auto_records_never_become_default_or_explicit_cards(self):
        invalid = [dict(AUTO, **{key: None}) for key in ("svt", "card", "isTD", "cardType", "critical")]
        invalid += [dict(NP, isAuto=value) for value in (None, 0, 1, "true")]
        for card in invalid:
            with self.subTest(card=card):
                turn = self.plan([card, NP]).turns[0]
                self.assertEqual(len(turn.card_sequence), 1)
                self.assertIs(turn.card_sequence[0].kind, PrimitiveKind.SELECT_NP)
        self.assertEqual(len(self.plan([dict(NP, isAuto=False)]).turns[0].card_sequence), 1)


class AutoCardSlotRuntimeTests(unittest.TestCase):
    def execute(self, attacks, *, np_available=True, android=False, blocked=()):
        plan = convert_chaldea_actions_to_battle_plan([{"type": "attack", "attacks": attacks}])
        state = BattleState(
            Scene.COMMAND_SELECTION, Confidence(1),
            tuple(CommandCard(slot, CardColor.BUSTER, None, Confidence(1), can_attack=slot not in blocked)
                  for slot in range(1, 6)),
            (NpCard(1, Confidence(1), available=np_available),), (),
        )
        context = NS(tasker=NS(stopping=False),
                     run_task=Mock(return_value=NS(status=NS(succeeded=True))))
        controller = Mock()
        runtime = AutoBattleRuntime(context, controller, RuleDecider(plan=plan), StrategyProfile())
        runtime._observe = Mock(side_effect=[state, BattleState(Scene.VICTORY, Confidence(1), (), (), ())])
        runtime._wait_turn_settled = Mock(return_value=True)
        runtime._post_attack_returned_to_map = False
        # Use the actual decider, validation, normalization, dispatch and Executor.
        # Only the observation stream, controller and animation delay are fake.
        with patch("battle.execution.executor.is_android", return_value=android), \
                patch("battle.runtime.runtime.time.sleep"):
            result = runtime.run()
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.turns, 1)
        if android:
            controller.post_click.assert_not_called()
            return [call.args[0] for call in context.run_task.call_args_list]
        context.run_task.assert_not_called()
        return [call.args for call in controller.post_click.call_args_list]

    def test_json_through_runtime_dispatches_np_in_second_and_third_positions(self):
        for attacks, sequence in (([AUTO, NP], ("face1", "np", "face2")),
                                  ([AUTO, AUTO, NP], ("face1", "face2", "np"))):
            for android in (False, True):
                with self.subTest(attacks=attacks, android=android):
                    expected = {"face1": "战斗_安卓选择指令卡1", "face2": "战斗_安卓选择指令卡2",
                                "np": "战斗_安卓选择宝具1"} if android else {
                        "face1": tuple(map(int, coords.center(coords.CARD_ROI[1]))),
                        "face2": tuple(map(int, coords.center(coords.CARD_ROI[2]))),
                        "np": coords.NP_CLICK[1],
                    }
                    self.assertEqual(self.execute(attacks, android=android), [expected[key] for key in sequence])

    def test_unavailable_np_dispatches_exactly_three_distinct_normal_cards(self):
        for attacks in ([AUTO, NP], [AUTO, AUTO, NP]):
            with self.subTest(attacks=attacks):
                actual = self.execute(attacks, np_available=False)
                self.assertEqual(actual, [tuple(map(int, coords.center(coords.CARD_ROI[slot])))
                                          for slot in (1, 2, 3)])
                self.assertEqual(len(set(actual)), 3)

    def test_normalization_preserves_np_position_and_avoids_blocked_faces_when_available(self):
        actual = self.execute([AUTO, AUTO, NP], blocked=(1, 2))
        self.assertEqual(actual, [tuple(map(int, coords.center(coords.CARD_ROI[slot]))) for slot in (3, 4)]
                         + [coords.NP_CLICK[1]])


if __name__ == "__main__":
    unittest.main()
