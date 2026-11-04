"""Grand effect-only support must retain its BBC mode without a named bond CE."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from chaldea.bbc_formatter import chaldea_to_bbc
from chaldea.bbc_importer import BbcImportError, convert_bbc_config


SERVANTS = {"A": {"SN": 100100}, "B": {"SN": 504500}, "C": {"SN": 604200}}
EQUIPS = {"1": "CE A", "3": "CE C", "5": "Bond CE"}


def battle(effect=None, *, ce_only=False):
    support = {"supportType": "friend", "grandSvt": True,
               "classBoardData": {"future": {"keep": [None]}}, "futureMember": 1}
    if not ce_only:
        support["svtId"] = 604200
    if effect is not None:
        support["classBoardData"]["grandBondEquipSkillChange"] = effect
    return {"team": {"onFieldSvts": [{"svtId": 100100}, {"svtId": 504500}, support],
                     "backupSvts": [None, None, None], "mysticCode": {"mysticCodeId": 210}},
            "actions": [{"type": "skill", "svt": 0, "skill": 0},
                        {"type": "attack", "attacks": [{"svt": 0, "isTD": True}]}],
            "futureRoot": {"keep": True}}


class GrandBondExportTests(unittest.TestCase):
    def export(self, data):
        original = copy.deepcopy(data)
        names = {item["SN"]: name for name, item in SERVANTS.items()}
        with patch("chaldea.bbc_formatter.get_equip_name", side_effect=lambda sid: EQUIPS.get(str(sid))) as equip_names, \
             patch("chaldea.bbc_formatter.get_servant_name", side_effect=names.get), \
             patch("chaldea.bbc_formatter.get_master_equip_sn", return_value=17):
            result = chaldea_to_bbc(data)
        self.equip_lookups = [call.args[0] for call in equip_names.call_args_list]
        self.assertEqual(data, original)
        return result

    def test_effect_only_grand_support_keeps_mode_and_all_three_effect_states(self):
        for effect, expected in ((True, "NP50"), (False, "初始"), (None, "任意")):
            with self.subTest(effect=effect):
                data = battle(effect)
                exported = self.export(data)
                self.assertEqual(exported["assistMode"], "冠位助战")
                self.assertEqual(exported["assistIdx"], 2)
                self.assertEqual(exported["jbType"], expected)
                self.assertEqual(exported["assistEquip"], [None, None, None])
                self.assertEqual(exported["round1_turn0_skill"], [1])
                self.assertEqual(exported["round1_turn0_np"], [1])
                self.assertNotIn("equip2", data["team"]["onFieldSvts"][2])
                recovered, _ = convert_bbc_config(exported, SERVANTS, EQUIPS)
                support = recovered["team"]["onFieldSvts"][2]
                self.assertIs(support["grandSvt"], True)
                self.assertEqual(support.get("classBoardData", {}).get("grandBondEquipSkillChange"), effect)
                self.assertFalse(any(key in support for key in ("equip1", "equip2", "equip3")))

    def test_grand_ce_only_support_roundtrips_effect_without_fabricating_identity(self):
        for effect in (True, False):
            with self.subTest(effect=effect):
                exported = self.export(battle(effect, ce_only=True))
                self.assertEqual(exported["assistMode"], "冠位助战仅礼装")
                self.assertIsNone(exported["servant_2_name"])
                self.assertEqual(exported["assistEquip"], [None, None, None])
                recovered, _ = convert_bbc_config(exported, SERVANTS, EQUIPS)
                support = recovered["team"]["onFieldSvts"][2]
                self.assertNotIn("svtId", support)
                self.assertNotIn("equip2", support)
                self.assertIs(support["grandSvt"], True)
                self.assertEqual(support.get("classBoardData", {}).get("grandBondEquipSkillChange"), effect)

    def test_unrestricted_ce_only_grand_needs_a_named_ce_instead_of_an_empty_requirement(self):
        data = battle(None, ce_only=True)
        with self.assertRaisesRegex(ValueError, "羁绊效果"):
            self.export(data)
        unrestricted = self.export(battle())
        unrestricted.update(assistMode="冠位助战仅礼装", servant_2_name=None)
        with self.assertRaisesRegex(BbcImportError, "没有能够导入"):
            convert_bbc_config(unrestricted, SERVANTS, EQUIPS)
        data["team"]["onFieldSvts"][2]["equip1"] = {"id": 1}
        exported = self.export(data)
        self.assertEqual(exported["jbType"], "任意")
        self.assertEqual(exported["assistEquip"], ["CE A", None, None])
        recovered, _ = convert_bbc_config(exported, SERVANTS, EQUIPS)
        support = recovered["team"]["onFieldSvts"][2]
        self.assertNotIn("svtId", support)
        self.assertNotIn("equip2", support)
        self.assertIs(support["grandSvt"], True)
        self.assertNotIn("grandBondEquipSkillChange", support.get("classBoardData", {}))

    def test_grand_primary_and_extra_ce_keep_real_slots_without_candidates(self):
        data = battle(True)
        data["team"]["onFieldSvts"][2].update(equip1={"id": 1}, equip3={"id": 3})
        exported = self.export(data)
        self.assertEqual(exported["assistEquip"], ["CE A", None, "CE C"])
        self.assertEqual(exported["jbType"], "NP50")
        self.assertEqual(exported["assistMode"], "冠位助战")

    def test_legacy_bond_ce_identity_and_mlb_do_not_enter_visible_ce_export(self):
        for equip_id, limit_break in ((999, None), (999, True), (5, False), (5, None)):
            with self.subTest(equip_id=equip_id, limit_break=limit_break):
                data = battle(True)
                support = data["team"]["onFieldSvts"][2]
                support.update(equip1={"id": 1, "limitBreak": True},
                               equip2={"id": equip_id, "limitBreak": limit_break,
                                       "lv": 100, "future": {"keep": [None]}},
                               equip3={"id": 3, "limitBreak": True})
                exported = self.export(data)
                self.assertEqual(exported["assistEquip"], ["CE A", None, "CE C"])
                self.assertEqual(exported["fullEquip"], 1)
                self.assertEqual(exported["jbType"], "NP50")
                self.assertNotIn(equip_id, self.equip_lookups)

    def test_bond_candidates_and_bond_card_only_ce_support_are_not_silently_weakened(self):
        data = battle(True)
        data["team"]["onFieldSvts"][2]["equip2"] = {"id": 5, "candidates": [{"id": 999}]}
        original = copy.deepcopy(data)
        with self.assertRaisesRegex(ValueError, "equip2.*候选"):
            self.export(data)
        self.assertEqual(data, original)
        config = self.export(battle())
        config.update(assistMode="冠位助战仅礼装", servant_2_name=None,
                      assistEquip=[None, "Bond CE", None])
        with self.assertRaisesRegex(BbcImportError, "没有能够导入"):
            convert_bbc_config(config, SERVANTS, EQUIPS)
        bond_only = battle(None, ce_only=True)
        bond_only["team"]["onFieldSvts"][2]["equip2"] = {"id": 5, "limitBreak": False}
        with self.assertRaisesRegex(ValueError, "羁绊效果"):
            self.export(bond_only)

    def test_grand_backup_support_retains_absolute_position_with_short_front(self):
        data = battle(False)
        support = data["team"]["onFieldSvts"][2]
        data["team"]["onFieldSvts"] = [{"svtId": 100100}]
        data["team"]["backupSvts"] = [support]
        exported = self.export(data)
        self.assertEqual(exported["assistIdx"], 3)
        self.assertEqual(exported["assistMode"], "冠位助战")
        self.assertEqual(exported["jbType"], "初始")

    def test_non_grand_single_ce_keeps_legacy_export_shape(self):
        for marker in (None, False):
            with self.subTest(grand=marker):
                data = battle(True)
                support = data["team"]["onFieldSvts"][2]
                if marker is None:
                    support.pop("grandSvt")
                else:
                    support["grandSvt"] = marker
                support["equip1"] = {"id": 1, "limitBreak": True}
                exported = self.export(data)
                self.assertEqual(exported["assistMode"], "从者礼装")
                self.assertEqual(exported["assistEquip"], "CE A")
                self.assertNotIn("jbType", exported)
                self.assertNotIn("fullEquip", exported)

    def test_owned_grand_servant_does_not_change_ordinary_support_export(self):
        data = battle()
        data["team"]["onFieldSvts"][0]["grandSvt"] = True
        data["team"]["onFieldSvts"][2].pop("grandSvt")
        exported = self.export(data)
        self.assertEqual(exported["assistMode"], "从者礼装")
        self.assertNotIn("jbType", exported)


if __name__ == "__main__":
    unittest.main()
