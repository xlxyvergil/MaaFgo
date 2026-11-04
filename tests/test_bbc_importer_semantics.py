"""BBC runtime semantics that must survive conversion into a native plan."""
import copy
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from chaldea.bbc_importer import (BbcImportError, convert_bbc_config,
                                  convert_bbc_document, load_bbc_catalogs)
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan


class BbcImportSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "A": {"SN": "100100", "_cards": [2, 2, 1, 1, 3]},
            "B": {"SN": "504500", "_cards": [1, 1, 1, 2, 3]},
            "C": {"SN": "604200", "_cards": [1, 1, 2, 2, 3]},
            "D": {"SN": "2300900", "_cards": [1, 2, 2, 2, 3]},
        }
        self.config = {
            "master_equip": 17, "servant_0_name": "A", "servant_1_name": "B",
            "servant_2_name": "C", "servant_3_name": "D", "assistIdx": 2,
            "assistMode": "仅从者", "round1_turns": 1,
            "round1_turn0_skill": [1], "round1_turn0_np": [1],
        }

    def convert(self, config=None):
        return convert_bbc_config(config or self.config, self.catalog, {"1": "CE"})

    @staticmethod
    def strategy(*slots):
        return [{**{f"card{i}": {"type": kind, "cards": choices,
                   "criticalStar": 0, "more_or_less": True}
                   for i, (kind, choices) in enumerate(slots, 1)},
                 "breakpoint": [False, False], "colorFirst": True}]

    def test_np_between_normal_cards_and_strategy_overrides_np_list(self):
        self.config["round1_turn0_np"] = [2]  # BBC ignores this when strategy exists.
        self.config["round1_turn0_strategy"] = self.strategy(
            (1, ["1B"]), (0, [1]), (1, ["1B"]))
        share, warnings = self.convert()
        attacks = share["actions"][-1]["attacks"]
        self.assertEqual([a["isTD"] for a in attacks], [False, True, False])
        self.assertEqual([a["card"] for a in attacks], [0, -1, 1])
        self.assertEqual(len(warnings), 1)  # The always-visible wave/fixed-turn notice.
        plan = convert_chaldea_actions_to_battle_plan(share["actions"], share["delegate"], 210)
        self.assertEqual(plan.turns[0].np_order, (1,))
        self.assertEqual([card.kind.value for card in plan.turns[0].card_sequence],
                         ["select_card", "select_np", "select_card"])

    def test_strategy_branch_ignores_stale_np_data_and_missing_skill_defaults_empty(self):
        self.config["round1_turn0_np"] = [6, "old-data"]
        del self.config["round1_turn0_skill"]
        self.config["round1_turn0_strategy"] = self.strategy((0, [2]), (2, []), (2, []))
        original = copy.deepcopy(self.config)
        share, _ = self.convert()
        self.assertEqual(share["actions"], [{"type": "attack", "attacks": [{"isTD": True, "svt": 1, "card": -1}]}])
        self.assertEqual(self.config, original)

    def test_default_card_slot_before_np_is_retained(self):
        self.config["round1_turn0_strategy"] = self.strategy((2, []), (0, [1]), (2, []))
        share, warnings = self.convert()
        self.assertEqual(share["actions"][-1]["attacks"],
                         [{"isAuto": True}, {"isTD": True, "svt": 0, "card": -1}])
        self.assertTrue(any("默认红蓝绿" in warning for warning in warnings))

    def test_dynamic_card_priorities_are_disclosed(self):
        row = self.strategy((0, [1]), (1, ["1B", "2A"]), (2, []))[0]
        self.config["round1_turn0_strategy"] = [row, copy.deepcopy(row)]
        share, warnings = self.convert()
        self.assertEqual(share["actions"][-1]["attacks"], [{"isTD": True, "svt": 0, "card": -1}])
        self.assertTrue(any("多套" in warning for warning in warnings))
        self.assertTrue(any("候选优先级" in warning for warning in warnings))

    def test_empty_normal_candidates_and_chain_breakpoints_are_rejected(self):
        for empty, breakpoint in [(True, False), (False, True)]:
            with self.subTest(empty=empty):
                row = self.strategy((1, [] if empty else ["1B"]), (0, [1]), (2, []))[0]
                row["breakpoint"] = [breakpoint, False]
                self.config["round1_turn0_strategy"] = [row]
                with self.assertRaises(BbcImportError):
                    self.convert()

    def test_np_type_zero_uses_first_candidate_as_bbc_does(self):
        self.config["round1_turn0_strategy"] = self.strategy((0, [2, 1]), (2, []), (2, []))
        share, _ = self.convert()
        self.assertEqual(share["actions"][-1]["attacks"][0]["svt"], 1)

    def test_swap_updates_card_deck_and_keeps_source_team_order(self):
        self.config["round1_turn0_skill"] = [[12, 1, 4], 1]
        self.config["round1_turn0_strategy"] = self.strategy((1, ["1B"]), (0, [1]), (2, []))
        share, _ = self.convert()
        self.assertEqual(share["delegate"]["replaceMemberIndexes"], [[0, 0]])
        self.assertEqual(share["actions"][-1]["attacks"][0]["card"], 1)  # D's deck, not A's.
        self.assertEqual(share["team"]["onFieldSvts"][0]["svtId"], 100100)

    def test_multiple_swaps_keep_ordered_actions(self):
        self.config["round1_turn0_skill"] = [[12, 1, 4], 1, [12, 4, 2], 4]
        share, _ = self.convert()
        self.assertEqual(share["delegate"]["replaceMemberIndexes"], [[0, 0], [1, 0]])
        self.assertEqual([a["svt"] for a in share["actions"][:-1]], [None, 0, None, 1])

    def test_enemy_selection_applies_after_command_and_to_next_turn(self):
        self.config.update({"round1_turns": 2, "round1_turn0_skill": [1, [13, 3], 2],
                            "round1_turn1_skill": [3], "round1_turn1_np": [1]})
        share, _ = self.convert()
        self.assertNotIn("options", share["actions"][0])
        for action in share["actions"][1:]:
            self.assertEqual(action["options"]["enemyTarget"], 0)
        plan = convert_chaldea_actions_to_battle_plan(share["actions"], share["delegate"], 210)
        self.assertIsNone(plan.turns[0].skill_sequence[0].target_enemy)
        self.assertEqual(plan.turns[0].skill_sequence[1].target_enemy, 3)
        self.assertEqual(plan.turns[1].target_enemy, 3)

    def test_trailing_blank_editor_row_does_not_shift_later_waves(self):
        self.config.update({"round1_turns": 3,
                            "round1_turn1_skill": [2], "round1_turn1_np": [2],
                            "round1_turn2_skill": [], "round1_turn2_np": [],
                            "round2_turns": 1, "round2_turn0_skill": [3], "round2_turn0_np": [3]})
        share, _ = self.convert()
        self.assertEqual(share["_bbcWaveTurns"], [2, 1])
        self.assertEqual([a["attacks"][0]["svt"] for a in share["actions"] if a["type"] == "attack"], [0, 1, 2])

    def test_blank_tail_wave_is_not_an_extra_attack(self):
        self.config.update({"round2_turns": 1, "round2_turn0_skill": [], "round2_turn0_np": []})
        share, _ = self.convert()
        self.assertEqual(share["_bbcWaveTurns"], [1, 0])
        self.assertEqual(len([a for a in share["actions"] if a["type"] == "attack"]), 1)

    def test_blank_middle_wave_cannot_guess_when_to_resume(self):
        self.config.update({"round2_turns": 0, "round3_turns": 1,
                            "round3_turn0_skill": [], "round3_turn0_np": [1]})
        with self.assertRaisesRegex(BbcImportError, "中间波次"):
            self.convert()

    def test_empty_condition_clears_instead_of_being_a_branch(self):
        self.config["round1_turn0_condition"] = []
        self.convert()
        self.config["round1_turn0_condition"] = [{"card1": {"type": 1, "cards": ["1B"]}}]
        with self.assertRaisesRegex(BbcImportError, "条件"):
            self.convert()

    def test_death_replacement_and_custom_option_do_not_silently_drop(self):
        self.config["round1_turn0_replace"] = {"1": 4, "4": None}
        with self.assertRaisesRegex(BbcImportError, "退场"):
            self.convert()
        del self.config["round1_turn0_replace"]
        self.config["round1_turn0_skill"] = [["BBDubai", 3, 0]]
        with self.assertRaisesRegex(BbcImportError, "特殊技能"):
            self.convert()

    def test_ce_only_support_omits_servant_and_cannot_use_its_skill(self):
        self.config.update({"assistMode": "仅礼装", "assistEquip": "CE", "assistIdx": 0,
                            "fullEquip": 0, "round1_turn0_skill": [4], "round1_turn0_np": [2]})
        share, _ = self.convert()
        member = share["team"]["onFieldSvts"][0]
        self.assertEqual(member, {"supportType": "friend", "equip1": {"id": 1}})
        self.config["round1_turn0_skill"] = [1]
        with self.assertRaisesRegex(BbcImportError, "没有确定的从者"):
            self.convert()

    def test_unrestricted_levels_and_unselected_owned_attributes_stay_absent(self):
        self.config.update({"NPlevel": 0, "servantLevel": 0, "fullEquip": 0, "skillsLevel": 0})
        share, _ = self.convert()
        for member in share["team"]["onFieldSvts"]:
            self.assertNotIn("lv", member)
            self.assertNotIn("tdLv", member)
            self.assertNotIn("skillLvs", member)

    def test_grand_bond_effect_does_not_require_a_named_bond_ce(self):
        self.config.update({"assistMode": "冠位助战", "assistEquip": [None, None, None]})
        for source, expected in [("NP50", True), ("初始", False)]:
            self.config["jbType"] = source
            member = self.convert()[0]["team"]["onFieldSvts"][2]
            self.assertEqual(member["classBoardData"]["grandBondEquipSkillChange"], expected)
            self.assertNotIn("equip2", member)

    def test_grand_equipment_preserves_three_slots_and_only_requires_full_when_selected(self):
        self.config.update({"assistMode": "冠位助战", "assistEquip": ["CE", "CE", "CE"], "fullEquip": 0})
        share, warnings = self.convert()
        member = share["team"]["onFieldSvts"][2]
        self.assertTrue(any("不会自动交换" in message for message in warnings))
        for slot in (1, 2, 3):
            self.assertEqual(member[f"equip{slot}"], {"id": 1})
        self.config["fullEquip"] = 1
        member = self.convert()[0]["team"]["onFieldSvts"][2]
        for slot in (1, 2, 3):
            self.assertIs(member[f"equip{slot}"]["limitBreak"], True)

    def test_real_ciel_config_keeps_buster_np_buster_and_trims_blank_tail(self):
        config = json.loads((ROOT / "tests/fixtures/bbc/希耶尔_光狐_梅林.json").read_text(encoding="utf-8"))
        share, warnings = convert_bbc_document(config, root=ROOT)
        self.assertEqual(share["_bbcWaveTurns"], [1, 1, 0])
        turns = [action for action in share["actions"] if action["type"] == "attack"]
        self.assertEqual(len(turns), 2)
        self.assertEqual([card["isTD"] for card in turns[0]["attacks"]], [False, True, False])
        self.assertEqual(turns[0]["options"]["enemyTarget"], 0)
        self.assertTrue(any("固定回合" in warning for warning in warnings))

    def test_new_mystic_code_ids_are_supported(self):
        for bbc, expected in [(26, 460), (27, 470)]:
            self.config["master_equip"] = bbc
            self.assertEqual(self.convert()[0]["team"]["mysticCode"]["mysticCodeId"], expected)

    def test_unselected_mystic_code_is_allowed_until_master_skill_is_used(self):
        self.config["master_equip"] = None
        self.assertEqual(self.convert()[0]["team"]["mysticCode"], {})
        self.config["round1_turn0_skill"] = [10]
        with self.assertRaisesRegex(BbcImportError, "没有指定御主礼装"):
            self.convert()

    def test_numeric_bools_and_excessive_rounds_fail_before_iteration(self):
        for field, value in [("master_equip", True), ("assistIdx", False),
                             ("round1_turns", True), ("round1_turns", 10**100)]:
            with self.subTest(field=field, value=value):
                config = dict(self.config, **{field: value})
                with self.assertRaises(BbcImportError):
                    self.convert(config)
        config = dict(self.config, **{"round" + "9" * 5000 + "_turns": 1})
        with self.assertRaises(BbcImportError):
            self.convert(config)

    def test_ambiguous_alias_is_rejected_and_numeric_sn_is_strict(self):
        self.config["servant_0_name"] = "shared"
        for item in self.catalog.values():
            item["other_name"] = ["shared"]
        with self.assertRaisesRegex(BbcImportError, "唯一匹配"):
            self.convert()
        self.config["servant_0_name"] = "A"
        self.catalog["A"]["SN"] = True
        with self.assertRaises(BbcImportError):
            self.convert()

    def test_bundled_data_works_without_bbc_or_network_and_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ["agent/chaldea/bbc_servant_ids.json", "agent/utils/Chaldea/equip_names_CN.json",
                             "agent/utils/Chaldea/servant_names_CN.json", "agent/battle_editor/data/catalog.json"]:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            config = json.loads((ROOT / "tests/fixtures/bbc/爱尔奎特_光狐_光狐.json").read_text(encoding="utf-8-sig"))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with patch.object(socket, "socket", side_effect=AssertionError("network unavailable")):
                share, warnings = convert_bbc_document(config, root=root)
            self.assertEqual(share["team"]["onFieldSvts"][0]["svtId"], 2300500)
            self.assertTrue(warnings)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})
            self.assertFalse((root / "config/Battle").exists())
            for server in [None, [], {}, 1, True]:
                if server is None:
                    continue
                with self.assertRaises(BbcImportError):
                    convert_bbc_document(dict(config, server=server), root=root)

    def test_missing_support_template_is_disclosed_without_removing_known_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, document in [
                ("agent/chaldea/bbc_servant_ids.json", {"schema": 1, "servants": self.catalog}),
                ("agent/utils/Chaldea/equip_names_CN.json", {"1": "Missing CE"}),
            ]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(document), encoding="utf-8")
            self.config.update({"assistMode": "从者礼装", "assistEquip": "Missing CE", "fullEquip": 0})
            share, warnings = convert_bbc_document(self.config, root=root)
            self.assertEqual(share["team"]["onFieldSvts"][2]["equip1"], {"id": 1})
            self.assertTrue(any("识别模板" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
