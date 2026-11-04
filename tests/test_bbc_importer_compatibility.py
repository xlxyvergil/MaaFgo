"""BBC defaults and post-attack replacements keep their executable meaning."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from chaldea.bbc_importer import BbcImportError, convert_bbc_config, convert_bbc_document
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan
from battle.core.models import RetireAction
from battle_editor.validation import validate_battle


IDS = (100100, 100200, 100300, 100400, 100500, 100600)
CATALOG = {name: {"SN": sid, "_cards": [2, 2, 1, 1, 3]}
           for name, sid in zip("ABCDEF", IDS)}
EQUIPS = {"9400340": "CE A", "9400400": "CE B"}


def config(**changes):
    return {"servant_0_name": "A", "servant_1_name": "B", "servant_2_name": "C",
            "servant_3_name": "D", "servant_4_name": "E", "assistIdx": 2,
            "assistMode": "从者礼装", "assistEquip": "CE A", "master_equip": 17,
            **changes}


class BbcImporterCompatibilityTests(unittest.TestCase):
    def convert(self, document):
        before = copy.deepcopy(document)
        try:
            return convert_bbc_config(document, CATALOG, EQUIPS)
        finally:
            self.assertEqual(document, before)

    def test_absent_round_fields_and_zero_blank_rounds_are_free_battle(self):
        for fields in ({}, {"round1_turns": 0}, {"round1_turns": 1,
                       "round1_turn0_skill": [], "round1_turn0_np": []}):
            with self.subTest(fields=fields):
                share, _ = self.convert(config(**fields))
                self.assertEqual(share["actions"], [])
                self.assertIsNone(convert_chaldea_actions_to_battle_plan(share["actions"]))
                self.assertTrue(validate_battle(share)["valid"])

    def test_missing_full_equip_requires_mlb_for_all_candidates(self):
        for changes, expected in [({}, True), ({"fullEquip": 1}, True),
                                  ({"fullEquip": 0}, None), ({"fullEquip": None}, None)]:
            with self.subTest(changes=changes):
                share, _ = self.convert(config(assistEquip=["CE A", "CE B"], **changes))
                primary = share["team"]["onFieldSvts"][2]["equip1"]
                for candidate in [primary, *primary["candidates"]]:
                    self.assertIs(candidate.get("limitBreak"), expected)

    def test_missing_legacy_and_null_skill_switches_match_bbc(self):
        for changes, expected in [({}, [10, 10, 10]), ({"skillsLevel": 1}, [10, 10, 10]),
                                  ({"skillsLevel": 0}, None), ({"skillsLevel": None}, None),
                                  ({"skillsLevel": [7]}, [7, 0, 0])]:
            with self.subTest(changes=changes):
                share, warnings = self.convert(config(**changes))
                member = share["team"]["onFieldSvts"][2]
                self.assertEqual(member.get("skillLvs"), expected)
                if expected is not None:
                    self.assertEqual(member["appendLvs"], [0] * 5)
                self.assertFalse(any("技能等级筛选值无效" in message for message in warnings))
                self.assertNotIn("skillLvs", share["team"]["onFieldSvts"][0])

    def test_ce_only_does_not_gain_an_unusable_servant_skill_requirement(self):
        share, _ = self.convert(config(assistMode="仅礼装"))
        member = share["team"]["onFieldSvts"][2]
        self.assertNotIn("svtId", member)
        self.assertNotIn("skillLvs", member)
        self.assertEqual(member["equip1"], {"id": 9400340, "limitBreak": True})

    def test_each_candidate_template_is_checked_without_removing_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, data in [("agent/chaldea/bbc_servant_ids.json", {"schema": 1, "servants": CATALOG}),
                                   ("agent/utils/Chaldea/equip_names_CN.json", EQUIPS)]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")
            primary = root / "assets/resource/base/image/lizhuang/满破/CE A.png"
            primary.parent.mkdir(parents=True)
            primary.write_bytes(b"template presence fixture")
            candidate = root / "assets/resource/base/image/lizhuang/非满破/CE B.png"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"template presence fixture")
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            for full in (1, 0):
                with self.subTest(full=full):
                    data = config(assistEquip=["CE A", "CE B"], fullEquip=full)
                    source = copy.deepcopy(data)
                    share, warnings = convert_bbc_document(data, root=root)
                    equipment = share["team"]["onFieldSvts"][2]["equip1"]
                    self.assertEqual([equipment["id"], equipment["candidates"][0]["id"]], [9400340, 9400400])
                    missing = [message for message in warnings if "缺少对应助战识别模板" in message]
                    self.assertEqual(len(missing), full)
                    if full:
                        self.assertIn("9400400", missing[0])
                        self.assertIn("CE B", missing[0])
                    self.assertEqual(data, source)
            self.assertEqual(before, {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_single_replacement_is_after_attack_and_before_next_turn(self):
        share, _ = self.convert(config(
            servant_4_name=None, round1_turns=1, round1_turn0_np=[1],
            round1_turn0_replace={"1": 4, "4": None},
            round2_turns=1, round2_turn0_skill=[1], round2_turn0_np=[1]))
        self.assertEqual([action["type"] for action in share["actions"]],
                         ["attack", "retire", "skill", "attack"])
        self.assertEqual(share["actions"][1], {"type": "retire", "svt": 0,
                                               "actor": {"initialSlot": 0, "svtId": IDS[0]}})
        self.assertEqual(share["actions"][2]["actor"], {"initialSlot": 3, "svtId": IDS[3]})
        self.assertEqual(share["team"]["onFieldSvts"][0]["svtId"], IDS[0])
        self.assertTrue(validate_battle(share)["valid"])
        plan = convert_chaldea_actions_to_battle_plan(share["actions"], share["delegate"], 210,
                                                     initial_servant_ids=tuple(map(str, IDS[:4])) + (None, None))
        self.assertEqual(len(plan.turns), 2)
        self.assertIsInstance(plan.turns[1].skill_sequence[0], RetireAction)
        self.assertEqual(plan.turns[1].servant_skills[0].actor.initial_slot, 3)

    def test_swap_then_successive_deaths_keep_bbc_and_native_live_slots_equal(self):
        share, _ = self.convert(config(
            round1_turns=1, round1_turn0_skill=[1, [12, 1, 5], 1], round1_turn0_np=[1, 2],
            round1_turn0_replace={"1": 4, "4": 5, "5": None},
            round2_turns=1, round2_turn0_skill=[1], round2_turn0_np=[2],
            round2_turn0_replace={"1": 4, "4": None},
            round3_turns=1, round3_turn0_skill=[1], round3_turn0_np=[1]))
        self.assertEqual([action["actor"]["initialSlot"] for action in share["actions"]
                          if action["type"] == "skill" and action.get("svt") == 0], [0, 4, 3, 0])
        self.assertEqual([action["actor"]["initialSlot"] for action in share["actions"]
                          if action["type"] == "retire"], [4, 3])
        self.assertTrue(validate_battle(share)["valid"])
        plan = convert_chaldea_actions_to_battle_plan(share["actions"], share["delegate"], 210,
                                                     initial_servant_ids=tuple(map(str, IDS[:5])) + (None,))
        self.assertEqual(len(plan.turns), 3)
        self.assertEqual(plan.turns[2].servant_skills[0].actor.initial_slot, 0)

    def test_simultaneous_two_front_replacements_have_one_ordered_interpretation(self):
        share, _ = self.convert(config(
            round1_turns=1, round1_turn0_np=[1],
            round1_turn0_replace={"1": 4, "2": 5, "4": None, "5": None},
            round2_turns=1, round2_turn0_skill=[1, 4], round2_turn0_np=[1, 2]))
        self.assertEqual([action["svt"] for action in share["actions"] if action["type"] == "retire"], [0, 1])
        self.assertEqual([action["actor"]["initialSlot"] for action in share["actions"]
                          if action["type"] == "skill"], [3, 4])
        self.assertTrue(validate_battle(share)["valid"])

    def test_arbitrary_copy_swap_backup_delete_and_invalid_maps_are_rejected(self):
        for mapping in [{"1": 4}, {"1": 2, "2": 1}, {"4": None}, {"1": None},
                        {"1": True}, {"0": 4}, {"1": 7}, [1, 4]]:
            with self.subTest(mapping=mapping), self.assertRaisesRegex(BbcImportError, "replace"):
                self.convert(config(round1_turns=1, round1_turn0_np=[1], round1_turn0_replace=mapping,
                                    round2_turns=1, round2_turn0_np=[1]))

    def test_ambiguous_identity_and_missing_successor_are_not_silently_dropped(self):
        for changes in [{"servant_3_name": "A"}, {"servant_3_name": None, "servant_4_name": None}]:
            with self.subTest(changes=changes), self.assertRaisesRegex(BbcImportError, "replace"):
                self.convert(config(round1_turns=1, round1_turn0_np=[1],
                                    round1_turn0_replace={"1": 4, "4": None},
                                    round2_turns=1, round2_turn0_np=[1], **changes))
        with self.assertRaisesRegex(BbcImportError, "没有后续回合"):
            self.convert(config(servant_4_name=None, round1_turns=1, round1_turn0_np=[1],
                                round1_turn0_replace={"1": 4, "4": None}))


if __name__ == "__main__":
    unittest.main()
