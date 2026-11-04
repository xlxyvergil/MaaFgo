"""BBC normal groups and grand per-cell CE candidates retain their real meaning."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'agent'), str(ROOT / 'agent/custom')]

from chaldea.bbc_importer import BbcImportError, convert_bbc_config
from chaldea.bbc_formatter import chaldea_to_bbc
import chaldea


SERVANTS = {'A': {'SN': 100100}, 'B': {'SN': 504500}, 'C': {'SN': 604200}}
EQUIPS = {'1': 'CE A', '2': 'CE B', '3': 'CE C', '4': 'CE D', '5': 'Bond CE'}


def config(**changes):
    return {'servant_0_name': 'A', 'servant_1_name': 'B', 'servant_2_name': 'C',
            'assistIdx': 2, 'assistMode': '从者礼装', 'assistEquip': 'CE A',
            'master_equip': 17, 'fullEquip': 0, 'round1_turns': 1, 'round1_turn0_np': [1], **changes}


def battle(equip, *, grand=False, support_type='friend'):
    member = {'svtId': 604200, 'supportType': support_type, 'equip1': equip}
    if grand:
        member['grandSvt'] = True
    return {'team': {'onFieldSvts': [{'svtId': 100100}, {'svtId': 504500}, member],
                     'backupSvts': [None, None, None],
                     'mysticCode': {'mysticCodeId': 210}},
            'actions': [{'type': 'attack', 'attacks': [{'svt': 0, 'isTD': True}]}]}


class BbcEquipCandidateTests(unittest.TestCase):
    def convert(self, source):
        before = copy.deepcopy(source)
        result = convert_bbc_config(source, SERVANTS, EQUIPS)
        self.assertEqual(source, before)
        return result

    def export(self, data):
        before = copy.deepcopy(data)
        by_id = {int(key): value for key, value in EQUIPS.items()}
        names = {value['SN']: name for name, value in SERVANTS.items()}
        with patch('chaldea.bbc_formatter.get_equip_name', side_effect=by_id.get), \
             patch('chaldea.bbc_formatter.get_servant_name', side_effect=names.get), \
             patch('chaldea.bbc_formatter.get_master_equip_sn', return_value=17):
            try:
                return chaldea_to_bbc(data)
            finally:
                self.assertEqual(data, before)

    def test_ordinary_outer_list_is_or_in_one_equipment_cell(self):
        share, _ = self.convert(config(assistEquip=['CE A', 'CE B', 'CE C']))
        member = share['team']['onFieldSvts'][2]
        self.assertEqual(member['equip1'], {'id': 1, 'candidates': [{'id': 2}, {'id': 3}]})
        self.assertNotIn('equip2', member)
        self.assertNotIn('equip3', member)

    def test_ordinary_nested_groups_only_execute_the_first_and_explain_the_rest(self):
        share, warnings = self.convert(config(assistEquip=[['CE A', 'CE D'], ['CE B'], 'CE C']))
        self.assertEqual(share['team']['onFieldSvts'][2]['equip1'],
                         {'id': 1, 'candidates': [{'id': 2}, {'id': 3}]})
        self.assertTrue(any('实际逻辑' in message and '后续 1 个候选' in message for message in warnings))

    def test_grand_outer_cells_and_inner_or_choices_remain_distinct(self):
        share, _ = self.convert(config(assistMode='冠位助战', fullEquip=1,
            assistEquip=[['CE A', 'CE B'], 'Bond CE', ['CE C', 'CE D']]))
        member = share['team']['onFieldSvts'][2]
        self.assertEqual(member['equip1'], {'id': 1, 'limitBreak': True,
                                         'candidates': [{'id': 2, 'limitBreak': True}]})
        self.assertEqual(member['equip2'], {'id': 5, 'limitBreak': True})
        self.assertEqual(member['equip3'], {'id': 3, 'limitBreak': True,
                                         'candidates': [{'id': 4, 'limitBreak': True}]})

    def test_full_zero_does_not_create_non_mlb_requirements(self):
        share, _ = self.convert(config(assistEquip=['CE A', 'CE B'], fullEquip=0))
        self.assertEqual(share['team']['onFieldSvts'][2]['equip1'],
                         {'id': 1, 'candidates': [{'id': 2}]})

    def test_ce_only_normal_and_grand_candidates_need_no_servant_identity(self):
        for mode, equipment in [('仅礼装', ['CE A', 'CE B']),
                                ('冠位助战仅礼装', [['CE A', 'CE B'], None, ['CE C', 'CE D']])]:
            with self.subTest(mode=mode):
                share, _ = self.convert(config(assistMode=mode, assistEquip=equipment, servant_2_name=None))
                member = share['team']['onFieldSvts'][2]
                self.assertNotIn('svtId', member)
                self.assertEqual(member['equip1'], {'id': 1, 'candidates': [{'id': 2}]})
                self.assertEqual(member.get('grandSvt'), True if mode.startswith('冠位') else None)

    def test_unknown_candidates_are_reported_and_deduplication_does_not_change_or(self):
        share, warnings = self.convert(config(assistEquip=['CE A', 'not in catalog', 'CE A']))
        self.assertEqual(share['team']['onFieldSvts'][2]['equip1'], {'id': 1})
        self.assertTrue(any('not in catalog' in message for message in warnings))

    def test_more_than_three_candidates_or_groups_is_explicitly_rejected(self):
        for source in [config(assistEquip=['CE A'] * 4),
                       config(assistMode='冠位助战', assistEquip=[['CE A', 'CE B', 'CE C', 'CE D'], None, None])]:
            with self.subTest(source=source), self.assertRaisesRegex(BbcImportError, '3'):
                self.convert(source)

    def test_single_equipment_structure_stays_unchanged(self):
        share, _ = self.convert(config(assistEquip='CE A', fullEquip=1))
        self.assertEqual(share['team']['onFieldSvts'][2]['equip1'], {'id': 1, 'limitBreak': True})
        exported = self.export(battle({'id': 1, 'limitBreak': True, 'candidates': []}))
        self.assertEqual(exported['assistEquip'], 'CE A')
        self.assertNotIn('fullEquip', exported)

    def test_normal_candidates_roundtrip_through_bbc_without_nested_group_bug(self):
        data = battle({'id': 1, 'candidates': [{'id': 2}, {'id': 3}]})
        exported = self.export(data)
        self.assertEqual(exported['assistEquip'], ['CE A', 'CE B', 'CE C'])
        self.assertEqual(exported['fullEquip'], 0)
        recovered, _ = self.convert(exported)
        self.assertEqual(recovered['team']['onFieldSvts'][2]['equip1'], data['team']['onFieldSvts'][2]['equip1'])

    def test_grand_candidates_roundtrip_and_bond_icon_setting_is_independent(self):
        data = battle({'id': 1, 'limitBreak': True, 'candidates': [{'id': 2, 'limitBreak': True}]}, grand=True)
        member = data['team']['onFieldSvts'][2]
        member.update(equip3={'id': 3, 'limitBreak': True, 'candidates': [{'id': 4, 'limitBreak': True}]},
                      classBoardData={'grandBondEquipSkillChange': True})
        exported = self.export(data)
        self.assertEqual(exported['assistEquip'], [['CE A', 'CE B'], None, ['CE C', 'CE D']])
        self.assertEqual((exported['fullEquip'], exported['assistMode'], exported['jbType']), (1, '冠位助战', 'NP50'))
        recovered, _ = self.convert(exported)
        restored = recovered['team']['onFieldSvts'][2]
        for key in ('equip1', 'equip3', 'classBoardData'):
            self.assertEqual(restored[key], member[key])

    def test_bbc_export_rejects_inexpressible_per_candidate_mlb_filters(self):
        for first, second in [(True, None), (None, True), (False, False), (False, True)]:
            with self.subTest(first=first, second=second), self.assertRaisesRegex(ValueError, '满破'):
                self.export(battle({'id': 1, 'limitBreak': first,
                                    'candidates': [{'id': 2, 'limitBreak': second}]}))

    def test_primary_candidate_inherits_legacy_mlb_only_when_current_field_is_missing(self):
        for legacy, expected_error in [(False, '非满破'), (True, 'fullEquip')]:
            data = battle({'id': 1, 'candidates': [{'id': 2}]})
            data['team']['onFieldSvts'][2]['ceLimitBreak'] = legacy
            with self.subTest(legacy=legacy), self.assertRaisesRegex(ValueError, expected_error):
                self.export(data)
        data = battle({'id': 1, 'candidates': [{'id': 2, 'limitBreak': True}]})
        data['team']['onFieldSvts'][2]['ceLimitBreak'] = True
        self.assertEqual(self.export(data)['fullEquip'], 1)

    def test_explicit_null_or_false_primary_mlb_overrides_legacy_state(self):
        for current, legacy in [(None, False), (None, True), (False, True)]:
            data = battle({'id': 1, 'limitBreak': current, 'candidates': [{'id': 2}]})
            data['team']['onFieldSvts'][2]['ceLimitBreak'] = legacy
            with self.subTest(current=current, legacy=legacy):
                if current is False:
                    with self.assertRaisesRegex(ValueError, '非满破'):
                        self.export(data)
                else:
                    self.assertEqual(self.export(data)['fullEquip'], 0)

    def test_missing_or_null_primary_id_inherits_legacy_id_without_changing_candidates(self):
        for current in [{}, {'id': None}]:
            data = battle({**current, 'candidates': [{'id': 2}]})
            data['team']['onFieldSvts'][2].update(ceId=1, ceLimitBreak=True)
            data['team']['onFieldSvts'][2]['equip1']['limitBreak'] = None
            with self.subTest(current=current):
                exported = self.export(data)
                self.assertEqual(exported['assistEquip'], ['CE A', 'CE B', None])
                self.assertEqual(exported['fullEquip'], 0)

    def test_current_id_and_legacy_mlb_resolve_independently(self):
        data = battle({'id': 2, 'candidates': [{'id': 3, 'limitBreak': True}]})
        data['team']['onFieldSvts'][2].update(ceId=1, ceLimitBreak=True)
        exported = self.export(data)
        self.assertEqual(exported['assistEquip'], ['CE B', 'CE C', None])
        self.assertEqual(exported['fullEquip'], 1)

    def test_zero_primary_id_never_inherits_legacy_id(self):
        data = battle({'id': 0, 'candidates': [{'id': 2}]})
        data['team']['onFieldSvts'][2]['ceId'] = 1
        with self.assertRaisesRegex(ValueError, '有效 ID'):
            self.export(data)
        data = battle({'id': 0}, grand=True)
        data['team']['onFieldSvts'][2].update(ceId=1,
            equip3={'id': 3, 'candidates': [{'id': 4}]})
        self.assertEqual(self.export(data)['assistEquip'], [None, None, ['CE C', 'CE D']])

    def test_inexpressible_conversion_never_writes_a_partial_bbc_file(self):
        data = battle({'id': 1, 'limitBreak': True, 'candidates': [{'id': 2}]})
        with tempfile.TemporaryDirectory() as directory, \
             patch('chaldea.fetch_share_data', return_value=(data, 1, 2)), \
             patch('chaldea.chaldea_to_bbc', side_effect=self.export):
            with self.assertRaisesRegex(ValueError, 'fullEquip'):
                chaldea.fetch_and_convert('source', output_dir=directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_ce_only_candidate_export_does_not_fabricate_identity(self):
        for grand in (False, True):
            data = battle({'id': 1, 'candidates': [{'id': 2}]}, grand=grand)
            del data['team']['onFieldSvts'][2]['svtId']
            exported = self.export(data)
            self.assertEqual(exported['assistMode'], '冠位助战仅礼装' if grand else '仅礼装')
            restored, _ = self.convert(exported)
            self.assertNotIn('svtId', restored['team']['onFieldSvts'][2])

    def test_backup_support_keeps_its_real_slot_when_front_array_is_short(self):
        data = battle({'id': 1, 'candidates': [{'id': 2}]})
        member = data['team']['onFieldSvts'][2]
        data['team']['onFieldSvts'] = [{'svtId': 100100}]
        data['team']['backupSvts'] = [member]
        self.assertEqual(self.export(data)['assistIdx'], 3)

    def test_invalid_candidate_shape_and_npc_constraints_fail_instead_of_disappearing(self):
        for equip in [{'id': 1, 'candidates': 'CE B'},
                      {'id': 1, 'candidates': [{'id': 2}] * 3},
                      {'id': 1, 'candidates': [{'id': 999}]}]:
            with self.subTest(equip=equip), self.assertRaises(ValueError):
                self.export(battle(equip))
        with self.assertRaisesRegex(ValueError, 'NPC'):
            self.export(battle({'id': 1, 'candidates': [{'id': 2}]}, support_type='npc'))


if __name__ == '__main__':
    unittest.main()
