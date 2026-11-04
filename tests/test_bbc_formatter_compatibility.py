"""BBC export must not cast a servant menu for Order Change or drop actor guards."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]
import chaldea

from chaldea.bbc_formatter import chaldea_to_bbc, convert_actions_to_bbc_rounds
from chaldea.bbc_importer import convert_bbc_config
from chaldea.servant_types import MYSTIC_CODE_ID_TO_BBC_SN
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan


class BbcFormatterCompatibilityTests(unittest.TestCase):
    @staticmethod
    def actions():
        return [{"type": "skill", "svt": None, "skill": 2},
                {"type": "attack", "attacks": [{"svt": 0, "isTD": True, "card": -1}]}]

    def test_order_change_exports_bbc_master_three_operand_instruction_and_roundtrips(self):
        for pair, expected in (([0, 0], [12, 1, 4]), ([2, 2], [12, 3, 6]),
                               ([1, 3], [12, 2, 4]), ([2, 5], [12, 3, 6])):
            with self.subTest(pair=pair):
                actions = self.actions()
                delegate = {"replaceMemberIndexes": [pair]}
                original = copy.deepcopy((actions, delegate))
                rounds = convert_actions_to_bbc_rounds(actions, delegate, 210)
                self.assertEqual(rounds["round1_turn0_skill"], [expected])
                config = {**rounds, "master_equip": MYSTIC_CODE_ID_TO_BBC_SN[210],
                          **{f"servant_{index}_name": str(index) for index in range(6)}}
                catalog = {str(index): {"SN": 100100 + index * 100, "_cards": [2, 2, 1, 1, 3]} for index in range(6)}
                restored, _ = convert_bbc_config(config, catalog, {})
                self.assertEqual(restored["delegate"]["replaceMemberIndexes"], [[expected[1] - 1, expected[2] - 4]])
                plan = convert_chaldea_actions_to_battle_plan(restored["actions"], restored["delegate"], 210)
                self.assertEqual((plan.turns[0].order_change.starting_member_idx,
                                  plan.turns[0].order_change.sub_member_idx), tuple(expected[1:]))
                self.assertEqual((actions, delegate), original)

    def test_multiple_order_changes_keep_their_distinct_front_and_reserve_pairs(self):
        actions = [*self.actions(), *self.actions()]
        rounds = convert_actions_to_bbc_rounds(actions, {"replaceMemberIndexes": [[2, 0], [1, 2]]}, 440)
        self.assertEqual(rounds["round1_turn0_skill"], [[12, 3, 4]])
        self.assertEqual(rounds["round2_turn0_skill"], [[12, 2, 6]])

    def test_order_change_never_falls_back_to_a_guessed_or_invalid_pair(self):
        for pairs in (None, [], {}, [[True, 0]], [[0, True]], [[-1, 0]], [[3, 0]],
                      [[0, 6]], [[0]], [[0, 0, 1]], ["bad"]):
            with self.subTest(pairs=pairs), self.assertRaisesRegex(ValueError, "换人"):
                convert_actions_to_bbc_rounds(self.actions(), {"replaceMemberIndexes": pairs}, 210)

    def test_actor_guards_without_retirement_are_rejected_in_skill_face_and_np_actions(self):
        guarded = {"initialSlot": 1, "svtId": 100200}
        cases = [
            [{"type": "skill", "svt": 0, "skill": 0, "actor": guarded}, self.actions()[1]],
            [{"type": "attack", "attacks": [{"svt": 0, "isTD": True, "card": -1, "actor": guarded}]}],
            [{"type": "attack", "attacks": [{"svt": 0, "isTD": False, "card": 0, "actor": guarded}]}],
        ]
        for actions in cases:
            original = copy.deepcopy(actions)
            with self.subTest(actions=actions), self.assertRaisesRegex(ValueError, "actor.*原生自动战斗"):
                convert_actions_to_bbc_rounds(actions)
            self.assertEqual(actions, original)

    def test_top_level_conversion_also_rejects_guarded_edited_plans(self):
        data = {"team": {"onFieldSvts": [{"svtId": 100100}], "mysticCode": {}},
                "actions": [{"type": "skill", "svt": 0, "skill": 0,
                             "actor": {"initialSlot": 0, "svtId": 100100}}, self.actions()[1]]}
        with patch("chaldea.bbc_formatter.get_servant_name", return_value="A"), \
                self.assertRaisesRegex(ValueError, "actor"):
            chaldea_to_bbc(data)

    def test_plain_existing_np_and_skill_output_stays_compatible(self):
        actions = [{"type": "skill", "svt": 1, "skill": 2, "options": {"playerTarget": 0}}, self.actions()[1]]
        rounds = convert_actions_to_bbc_rounds(actions)
        self.assertEqual(rounds["round1_turn0_skill"], [[6, 1]])
        self.assertEqual(rounds["round1_turn0_np"], [1])

    def test_face_cards_and_automatic_holes_cannot_silently_become_leading_np(self):
        np = {"svt": 0, "isTD": True, "card": -1}
        for cards in ([{"isAuto": True}, np], [np, {"isAuto": True}, {"svt": 1, "isTD": True}],
                      [{"svt": 0, "isTD": False, "card": 0, "cardType": 2}, np],
                      [{"svt": 0, "card": 0}], [{"isAuto": True}], [{"isAuto": "false", **np}]):
            actions = [{"type": "attack", "attacks": cards}]
            before = copy.deepcopy(actions)
            with self.subTest(cards=cards), self.assertRaisesRegex(ValueError, "混排顺序.*原生自动战斗"):
                convert_actions_to_bbc_rounds(actions)
            self.assertEqual(actions, before)

    def test_nonempty_advanced_strategy_is_rejected_while_empty_or_absent_remains_compatible(self):
        base = {"team": {"onFieldSvts": [], "backupSvts": [], "mysticCode": {}},
                "actions": [self.actions()[1]]}
        baseline = chaldea_to_bbc(base)
        for value in (None, {}):
            self.assertEqual(chaldea_to_bbc({**base, "maaAdvancedStrategy": value}), baseline)
        for value in ({"preset": "np"}, {"autoServantSkills": False}, {"maxTurns": 7},
                      {"openingHand": {"enabled": True, "servantSlot": 1}}, {"preset": "balanced"},
                      {"nativeStrategyOverrides": {"color_weights": {"A": 4}}}):
            data = {**copy.deepcopy(base), "maaAdvancedStrategy": value}
            before = copy.deepcopy(data)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "maaAdvancedStrategy.*原生自动战斗"):
                chaldea_to_bbc(data)
            self.assertEqual(data, before)

    def test_trailing_skill_turn_flushes_to_a_preserved_skill_and_default_normal_card_round(self):
        for leading in ([], [self.actions()[1]]):
            actions = leading + [{"type": "skill", "svt": 1, "skill": 0}]
            rounds = convert_actions_to_bbc_rounds(actions)
            last = len(leading) + 1
            self.assertEqual(rounds[f"round{last}_turn0_skill"], [4])
            self.assertEqual(rounds[f"round{last}_turn0_np"], [])
            config = {**rounds, "servant_0_name": "A", "servant_1_name": "B"}
            catalog = {"A": {"SN": 100100}, "B": {"SN": 100200}}
            restored, _ = convert_bbc_config(config, catalog, {})
            before = convert_chaldea_actions_to_battle_plan(actions)
            after = convert_chaldea_actions_to_battle_plan(restored["actions"])
            self.assertEqual(after.turns, before.turns)

    def test_rejected_extensions_never_create_or_overwrite_exported_bbc_file(self):
        base = {"team": {"onFieldSvts": [], "backupSvts": [], "mysticCode": {}},
                "actions": [self.actions()[1]]}
        cases = [
            {**copy.deepcopy(base), "maaAdvancedStrategy": {"preset": "np"}},
            {**copy.deepcopy(base), "actions": [{"type": "attack", "attacks": [{"isAuto": True}, {"svt": 0, "isTD": True}]}]},
            {**copy.deepcopy(base), "actions": [{"type": "attack", "attacks": [{"svt": 0, "isTD": False, "card": 0}]}]},
        ]
        for data in cases:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory, \
                    patch("chaldea.fetch_share_data", return_value=(data, 1, 2)):
                with self.assertRaisesRegex(ValueError, "原生自动战斗"):
                    chaldea.fetch_and_convert("source", output_dir=directory)
                self.assertEqual(list(Path(directory).iterdir()), [])
                existing = Path(directory) / "chaldea_1_2.json"
                original = b'{"previous":"valid BBC"}'
                existing.write_bytes(original)
                with self.assertRaisesRegex(ValueError, "原生自动战斗"):
                    chaldea.fetch_and_convert("source", output_dir=directory)
                self.assertEqual(existing.read_bytes(), original)
                self.assertEqual(list(Path(directory).iterdir()), [existing])

    def test_old_pure_np_order_remains_exportable_through_the_file_entry(self):
        data = {"team": {"onFieldSvts": [], "backupSvts": [], "mysticCode": {}},
                "actions": [{"type": "attack", "attacks": [
                    {"svt": 2, "isTD": True, "card": -1},
                    {"svt": 0, "isTD": True, "card": -1, "isAuto": False}]}]}
        with tempfile.TemporaryDirectory() as directory, \
                patch("chaldea.fetch_share_data", return_value=(data, 1, 2)):
            filename = chaldea.fetch_and_convert("source", output_dir=directory)
            import json
            result = json.loads((Path(directory) / filename).read_text("utf-8"))
            self.assertEqual(result["round1_turn0_np"], [3, 1])


if __name__ == "__main__":
    unittest.main()
