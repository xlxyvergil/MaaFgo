"""Effect-only Grand bond settings survive editor storage and native battle import."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from battle_editor.storage import BattleStorage
from battle_editor.validation import validate_battle
from chaldea.support_criteria import build_support_criteria
from auto_battle_action import _build_plan_from_share, _battle_setup_from_chaldea_share


class GrandBondContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / "tests/fixtures/chaldea/太女尼_78119_94149837.json").read_text(encoding="utf-8"))
        servants = json.loads((ROOT / "agent/custom/servant_list.json").read_text(encoding="utf-8"))
        cls.servants = {servant["id"]: servant for servant in servants["servants"]}

    def check_roundtrip(self, legacy_equip):
        baseline = copy.deepcopy(self.fixture)
        baseline["actions"] = [
            {"type": "skill", "svt": 1, "skill": 0, "options": {"playerTarget": 0}},
            {"type": "attack", "attacks": [{"isAuto": True}, {"isTD": True, "svt": 1, "card": -1}]},
        ]
        expected_plan = _build_plan_from_share(baseline)
        self.assertIsNotNone(expected_plan)
        self.assertEqual(len(expected_plan.turns), 1)
        for effect, expected in ((True, "50np"), (False, "original"), (None, "any")):
            with self.subTest(effect=effect, legacy_equip=legacy_equip), tempfile.TemporaryDirectory() as folder:
                data = copy.deepcopy(baseline)
                member = data["team"]["onFieldSvts"][0]
                if legacy_equip:
                    member["equip2"]["future"] = {"keep": [None, 7]}
                else:
                    member.pop("equip2", None)
                board = member.setdefault("classBoardData", {})
                board["future"] = {"keep": [None, False]}
                if effect is None:
                    board.pop("grandBondEquipSkillChange", None)
                else:
                    board["grandBondEquipSkillChange"] = effect
                before = copy.deepcopy(data)
                report = validate_battle(data)
                self.assertTrue(report["valid"], report)
                store = BattleStorage(folder)
                draft = store.save_draft("grand-contract", {"data": data, "revision": 0})
                self.assertEqual(draft["data"], before)
                saved = store.save_battle("冠位羁绊效果.json", data, create=True)
                restored = store.read_battle(saved["name"])["data"]
                self.assertEqual(restored, before)
                criteria = build_support_criteria(restored, self.servants)
                self.assertEqual(criteria["ce_bond"], expected)
                self.assertEqual([ce["source_slot"] for ce in criteria["ce_specs"]], [1, 3])
                self.assertEqual(_build_plan_from_share(restored), expected_plan)
                imported_plan, roster = _battle_setup_from_chaldea_share({
                    "chaldea_import_source": str(store.battle_dir / saved["name"]),
                })
                self.assertEqual(imported_plan, expected_plan)
                self.assertEqual(len(roster), 6)
                self.assertEqual(data, before)
                self.assertEqual(restored, before)

    def test_no_named_bond_ce_preserves_all_three_effects_and_native_plan(self):
        self.check_roundtrip(legacy_equip=False)

    def test_legacy_bond_ce_and_unknown_fields_remain_lossless(self):
        self.check_roundtrip(legacy_equip=True)


if __name__ == "__main__":
    unittest.main()
