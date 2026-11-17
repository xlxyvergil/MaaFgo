"""Real BBC menu opcodes preserve native queues without device or network IO."""
import copy
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "agent/custom")]

from chaldea.bbc_special_skills import BbcSpecialSkillChoices
from battle.data.chaldea_converter import convert_chaldea_actions_to_battle_plan


def skill(slot, index):
    return {"type": "skill", "svt": slot, "skill": index}


class BbcSpecialChoicesTests(unittest.TestCase):
    def test_space_ishtar_uses_bbc_screen_qab_order_in_each_actual_position(self):
        for slot in range(3):
            for button, card_type in ((1, 3), (2, 1), (3, 2)):
                with self.subTest(slot=slot, button=button):
                    roster = [{"svtId": 100100} for _ in range(6)]
                    roster[slot] = {"svtId": 1100900}
                    choices = BbcSpecialSkillChoices(ROOT)
                    action = choices.convert([-(slot * 3 + 2), button], roster, "turn")
                    self.assertEqual(action, skill(slot, 1))
                    self.assertEqual(choices.delegate(), {"tdTypeChanges": [card_type]})

    def test_prelati_and_lilith_exact_strings_and_real_skill_ids(self):
        cases = (("Prelati", 505600, 0, "不替换", 0),
                 ("Prelati", 505700, 0, "替换", 1),
                 ("Lilith", 405900, 2, "不付与即死", 0),
                 ("Lilith", 405900, 2, "付与即死", 1))
        for opcode, sid, index, option, value in cases:
            with self.subTest(opcode=opcode, option=option):
                roster = [None, {"svtId": sid}, None]
                choices = BbcSpecialSkillChoices(ROOT)
                action = choices.convert([opcode, 4 + index, option], roster, "turn")
                self.assertEqual(action, skill(1, index))
                self.assertEqual(choices.delegate(), {"skillActSelectSelections": [value]})

    def test_ordinary_menus_before_and_after_an_explicit_choice_get_individual_defaults(self):
        roster = [{"svtId": 2501100}, {"svtId": 505600}, {"svtId": 405900}]
        choices = BbcSpecialSkillChoices(ROOT)
        before = skill(0, 0)
        choices.observe(before, roster, "before")
        actions = [before, choices.convert(["Prelati", 4, "替换"], roster, "choice")]
        ordinary = skill(2, 2)
        choices.observe(ordinary, roster, "ordinary_lilith")
        actions.append(ordinary)
        actions.append(choices.convert(["Lilith", 9, "付与即死"], roster, "explicit_lilith"))
        after = skill(0, 1)
        choices.observe(after, roster, "after")
        actions.append(after)
        delegate = choices.delegate()
        self.assertEqual(delegate, {"skillActSelectSelections": [1, 1, 0, 1, 1]})
        plan = convert_chaldea_actions_to_battle_plan(actions, delegate)
        self.assertEqual(len(plan.turns[0].servant_skills), 5)
        self.assertEqual(plan.special_choices.skill_act_selections, (1, 1, 0, 1, 1))

    def test_fields_are_independent_and_do_not_consume_master_or_ordinary_skills(self):
        roster = [{"svtId": 1100900}, {"svtId": 505600}, {"svtId": 2501100}]
        before = copy.deepcopy(roster)
        choices = BbcSpecialSkillChoices(ROOT)
        choices.observe(skill(0, 1), roster, "ordinary_color")
        choices.observe(skill(0, 0), roster, "not_menu")
        choices.observe(skill(None, 2), roster, "master")
        choices.convert(["Prelati", 4, "不替换"], roster, "explicit_act")
        choices.convert([-2, 3], roster, "explicit_color")
        choices.observe(skill(2, 1), roster, "ordinary_act")
        self.assertEqual(choices.delegate(), {"tdTypeChanges": [1, 2], "skillActSelectSelections": [0, 1]})
        self.assertEqual(roster, before)

    def test_actual_roster_after_swapping_is_the_only_source_of_actor_identity(self):
        roster = [{"svtId": 100100}, {"svtId": 504500}, {"svtId": 604200}, {"svtId": 1100900}]
        choices = BbcSpecialSkillChoices(ROOT)
        with self.assertRaisesRegex(ValueError, "真实从者"):
            choices.convert([-2, 2], roster, "before_swap")
        roster[0], roster[3] = roster[3], roster[0]
        self.assertEqual(choices.convert([-2, 2], roster, "after_swap"), skill(0, 1))
        self.assertEqual(choices.delegate()["tdTypeChanges"], [1])

    def test_supported_named_opcode_does_not_authorize_a_different_actor_or_skill(self):
        roster = [{"svtId": 505600}, {"svtId": 405900}, {"svtId": 1100900}]
        invalid = (["Prelati", 5, "替换"], ["Lilith", 3, "付与即死"], [-2, 2],
                   [-8, 0], [-8, 4], [-8, True], [-9, 1], [-7, 1],
                   ["Prelati", True, "替换"], ["Prelati", 1, []],
                   ["Lilith", 6, "unknown"], ["Lilith", 6])
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                BbcSpecialSkillChoices(ROOT).convert(raw, roster, "bad")

    def test_bbdubai_converts_both_np_variants_in_each_actual_frontline_slot(self):
        for slot in range(3):
            for button, variant in ((0, 1), (1, 2)):
                with self.subTest(slot=slot, button=button):
                    roster = [{"svtId": 100100} for _ in range(6)]
                    roster[slot] = {"svtId": 2300600}
                    choices = BbcSpecialSkillChoices(ROOT)
                    action = choices.convert(["BBDubai", slot * 3 + 3, button], roster, "turn")
                    self.assertEqual(action, skill(slot, 2))
                    self.assertEqual(choices.delegate(), {"tdTypeChanges": [variant]})

    def test_bbdubai_preserves_shared_queue_order_with_space_ishtar_and_defaults(self):
        roster = [{"svtId": 2300600}, {"svtId": 1100900}, None]
        choices = BbcSpecialSkillChoices(ROOT)
        choices.observe(skill(0, 2), roster, "ordinary_bb_default")
        choices.convert([-5, 1], roster, "space_ishtar_quick")
        choices.convert(["BBDubai", 3, 1], roster, "bb_defense")
        choices.observe(skill(1, 1), roster, "ordinary_space_ishtar_default")
        choices.convert(["BBDubai", 3, 0], roster, "bb_attack")
        self.assertEqual(choices.delegate(), {"tdTypeChanges": [1, 3, 2, 1, 1]})

    def test_bbdubai_uses_its_actual_position_after_an_order_change(self):
        roster = [{"svtId": 100100}, None, None, {"svtId": 2300600}]
        choices = BbcSpecialSkillChoices(ROOT)
        with self.assertRaisesRegex(ValueError, "真实从者"):
            choices.convert(["BBDubai", 3, 1], roster, "before_swap")
        roster[0], roster[3] = roster[3], roster[0]
        self.assertEqual(choices.convert(["BBDubai", 3, 1], roster, "after_swap"), skill(0, 2))
        self.assertEqual(choices.delegate(), {"tdTypeChanges": [2]})

    def test_bbdubai_rejects_wrong_actor_skill_or_button_without_creating_a_queue(self):
        roster = [{"svtId": 2300600}, {"svtId": 100100}, None]
        invalid = (["BBDubai"], ["BBDubai", 3], ["BBDubai", 3, 0, "extra"],
                   ["BBDubai", 1, 0], ["BBDubai", 2, 0], ["BBDubai", 0, 0],
                   ["BBDubai", 10, 0], ["BBDubai", True, 0],
                   ["BBDubai", 3, -1], ["BBDubai", 3, 2], ["BBDubai", 3, True],
                   ["BBDubai", 3, "0"], ["BBDubai", 6, 0])
        for raw in invalid:
            with self.subTest(raw=raw):
                choices = BbcSpecialSkillChoices(ROOT)
                with self.assertRaises(ValueError):
                    choices.convert(raw, roster, "invalid_bb")
                self.assertEqual(choices.delegate(), {})

    def test_unknown_opcodes_return_to_the_strict_importer(self):
        choices = BbcSpecialSkillChoices(ROOT)
        self.assertIsNone(choices.convert(["unsupported", 1, 0], [], "bbc"))
        self.assertIsNone(choices.convert([1, 2], [], "bbc"))
        self.assertEqual(choices.delegate(), {})

    def test_unactivated_fields_never_create_queues_or_reject_ordinary_older_variants(self):
        choices = BbcSpecialSkillChoices(ROOT, skill_metadata={})
        choices.observe(skill(0, 2), [{"svtId": 1100100, "skillIds": [None, None, 199550]}], "old")
        self.assertEqual(choices.delegate(), {})
        choices.convert([-5, 2], [{"svtId": 1100100}, {"svtId": 1100900}], "color")
        self.assertEqual(choices.delegate(), {"tdTypeChanges": [1]})

    def test_older_or_unknown_strengthened_variant_blocks_the_activated_field_without_skipping(self):
        for selected in (199550, 458650, 999999, "458900", True):
            with self.subTest(selected=selected):
                roster = [{"svtId": 1100100, "skillIds": [None, None, selected]}, {"svtId": 505600}]
                choices = BbcSpecialSkillChoices(ROOT, skill_metadata={})
                choices.observe(skill(0, 2), roster, "uncertain_variant")
                choices.convert(["Prelati", 4, "替换"], roster, "explicit")
                with self.assertRaisesRegex(ValueError, "uncertain_variant.*无法安全配对"):
                    choices.delegate()
        choices = BbcSpecialSkillChoices(ROOT, skill_metadata={})
        roster = [{"svtId": 1100100}, {"svtId": 505600}]
        choices.convert(["Prelati", 4, "替换"], roster, "explicit_first")
        choices.observe(skill(0, 2), roster, "missing_metadata_after")
        with self.assertRaisesRegex(ValueError, "missing_metadata_after"):
            choices.delegate()

    def test_explicit_zero_null_and_missing_skill_id_use_the_confirmed_local_default(self):
        for selected in (None, 0, 458900):
            roster = [{"svtId": 1100100, "skillIds": [None, None, selected]}, {"svtId": 505600}]
            choices = BbcSpecialSkillChoices(ROOT, skill_metadata={1100100: {"id": 1100100, "skills": [{"num": 3, "id": 458900}]}})
            choices.observe(skill(0, 2), roster, "confirmed")
            choices.convert(["Prelati", 4, "不替换"], roster, "explicit")
            self.assertEqual(choices.delegate(), {"skillActSelectSelections": [1, 0]})

    def test_repeated_observe_is_idempotent_but_equal_distinct_actions_still_consume_separate_items(self):
        choices = BbcSpecialSkillChoices(ROOT)
        roster = [{"svtId": 2501100}, {"svtId": 505600}]
        action = skill(0, 0)
        choices.observe(action, roster, "one")
        choices.observe(action, roster, "one_again")
        choices.observe(skill(0, 0), roster, "two")
        explicit = choices.convert(["Prelati", 4, "替换"], roster, "three")
        choices.observe(explicit, roster, "three_again")
        self.assertEqual(choices.delegate(), {"skillActSelectSelections": [1, 1, 1]})

    def test_missing_optional_menu_facts_fails_closed_only_for_explicit_choices(self):
        choices = BbcSpecialSkillChoices(ROOT, group_metadata={})
        choices.observe(skill(0, 0), [{"svtId": 2501100}], "plain")
        self.assertEqual(choices.delegate(), {})
        with self.assertRaisesRegex(ValueError, "菜单资料"):
            choices.convert(["Prelati", 4, "替换"], [None, {"svtId": 505600}], "explicit")

    def test_standalone_helper_load_has_no_maa_controller_editor_server_or_network_dependency(self):
        script = r'''
import importlib.util
from pathlib import Path
import socket,sys
root=Path(sys.argv[1])
def forbidden(*a,**k):raise AssertionError('network use')
socket.socket=forbidden
spec=importlib.util.spec_from_file_location('offline_bbc_choices',root/'agent/chaldea/bbc_special_skills.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
choices=module.BbcSpecialSkillChoices(root)
assert choices.convert(['Prelati',1,'替换'],[{'svtId':505600}],'offline')=={'type':'skill','svt':0,'skill':0}
assert choices.delegate()=={'skillActSelectSelections':[1]}
assert not any(name=='maa' or name.startswith('maa.') or name in ('battle_editor.server','agent.battle_editor.server') for name in sys.modules)
'''
        process = subprocess.run([sys.executable, "-c", script, str(ROOT)], capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()
