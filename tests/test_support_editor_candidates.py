"""Editor CE alternatives preserve the Chaldea primary and execute per-cell ORs."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'agent'), str(ROOT / 'agent/custom')]

from chaldea.support_criteria import build_support_criteria
from agent.battle_editor.validation import validate_battle
from test_support_fallback import Context
import support_action as support


def share(member):
    return {'team': {'onFieldSvts': [member, None, None],
                     'backupSvts': [None, None, None]}, 'actions': []}


def member():
    return {'supportType': 'friend', 'svtId': 100100,
            'equip1': {'id': 101, 'limitBreak': True, 'lv': 80,
                       'future': {'preserve': None},
                       'candidates': [{'id': 202, 'limitBreak': False}, {'id': 303}]}}


SERVANTS = {'100100': {'id': '100100', 'class': 'saber', 'images': ['test.png']}}


def criteria(data):
    return build_support_criteria(data, SERVANTS, resolve_name=lambda value: f'礼装{value}')


class CandidateContractTests(unittest.TestCase):
    def test_three_choices_have_independent_states_without_mutating_imports(self):
        data = share(member())
        before = copy.deepcopy(data)
        result = criteria(data)
        self.assertEqual(result['ce_specs'], [{'slot': 1, 'source_slot': 1, 'id': 101,
                                             'name': '礼装101.png', 'status': '满破'}])
        self.assertEqual([(row['id'], row['status']) for row in result['ce_groups'][0]['candidates']],
                         [(101, '满破'), (202, '非满破'), (303, '任意')])
        self.assertEqual(data, before)
        report = validate_battle(data)
        self.assertTrue(report['valid'], report)
        self.assertIn('equip_candidates_extension', {issue['code'] for issue in report['issues']})
        self.assertEqual(data, before)

    def test_grand_cells_remain_separate_and_bond_filter_keeps_its_meaning(self):
        item = member()
        item.update(grandSvt=True, equip2={'id': 999},
                    equip3={'id': 404, 'candidates': [{'id': 505, 'limitBreak': True}]},
                    classBoardData={'grandBondEquipSkillChange': False})
        result = criteria(share(item))
        self.assertEqual([(group['slot'], group['source_slot']) for group in result['ce_groups']],
                         [(1, 1), (2, 3)])
        self.assertEqual([len(group['candidates']) for group in result['ce_groups']], [3, 2])
        self.assertEqual(result['ce_bond'], 'original')
        self.assertTrue(validate_battle(share(item))['valid'])

    def test_legacy_primary_fields_and_extra_candidate_are_both_used(self):
        item = {'supportType': 'friend', 'ceId': 101, 'ceLimitBreak': False,
                'equip1': {'future': 1, 'candidates': [{'id': 202}]}}
        data = share(item)
        before = copy.deepcopy(data)
        result = criteria(data)
        self.assertEqual([(row['id'], row['status']) for row in result['ce_groups'][0]['candidates']],
                         [(101, '非满破'), (202, '任意')])
        self.assertTrue(validate_battle(data)['valid'])
        self.assertEqual(data, before)

    def test_primary_mlb_uses_field_level_legacy_fallback_consistent_with_editor(self):
        cases = [
            # An explicit modern ID does not hide a legacy state if its own
            # limitBreak field is absent.
            ({'id': 101}, False, '非满破'),
            ({'id': 101}, True, '满破'),
            # A legacy ID may be paired with an explicit current state.
            ({'id': None, 'limitBreak': False}, True, '非满破'),
            ({'id': None, 'limitBreak': True}, False, '满破'),
            # Null is an explicit unrestricted state, not a missing property.
            ({'id': 101, 'limitBreak': None}, True, '任意'),
            ({'id': None, 'limitBreak': None}, False, '任意'),
        ]
        for current, legacy, expected in cases:
            for multiple in (False, True):
                with self.subTest(current=current, legacy=legacy, multiple=multiple):
                    stored = copy.deepcopy(current)
                    if multiple:
                        stored['candidates'] = [{'id': 202, 'limitBreak': False}]
                    data = share({'supportType': 'friend', 'ceId': 101,
                                  'ceLimitBreak': legacy, 'equip1': stored})
                    before = copy.deepcopy(data)
                    result = criteria(data)
                    self.assertEqual(result['ce_specs'][0]['status'], expected)
                    self.assertEqual(result['ce_groups'][0]['candidates'][0]['status'], expected)
                    if multiple:
                        self.assertEqual(result['ce_groups'][0]['candidates'][1]['status'], '非满破')
                    self.assertTrue(validate_battle(data)['valid'])
                    self.assertEqual(data, before)

    def test_explicit_zero_primary_does_not_revive_a_legacy_id_or_its_candidates(self):
        for extras in (None, [], [{'id': 202}]):
            with self.subTest(extras=extras):
                data = share({'supportType': 'friend', 'ceId': 101, 'ceLimitBreak': True,
                              'equip1': {'id': 0, 'limitBreak': False, 'candidates': extras}})
                before = copy.deepcopy(data)
                if extras:
                    with self.assertRaises(ValueError):
                        criteria(data)
                    self.assertFalse(validate_battle(data)['valid'])
                else:
                    result = criteria(data)
                    self.assertEqual(result['ce_specs'], [])
                    self.assertEqual(result['ce_groups'], [])
                self.assertEqual(data, before)

    def test_absent_empty_or_null_extra_candidates_preserve_the_old_primary(self):
        for extra in (None, []):
            with self.subTest(extra=extra):
                data = share({'svtId': 100100, 'supportType': 'friend',
                              'equip1': {'id': 101, 'candidates': extra}})
                result = criteria(data)
                self.assertEqual(len(result['ce_groups'][0]['candidates']), 1)
                report = validate_battle(data)
                self.assertTrue(report['valid'])
                self.assertNotIn('equip_candidates_extension', {i['code'] for i in report['issues']})

    def test_all_supported_member_types_keep_candidates(self):
        for support_type in ('friend', 'npc', 'fixed'):
            data = share(dict(member(), supportType=support_type))
            self.assertTrue(validate_battle(data)['valid'], support_type)
            self.assertEqual(len(criteria(data)['ce_groups'][0]['candidates']), 3)

    def test_too_many_choices_fail_without_silent_truncation(self):
        data = share(member())
        data['team']['onFieldSvts'][0]['equip1']['candidates'].append({'id': 404})
        before = copy.deepcopy(data)
        with self.assertRaises(ValueError):
            criteria(data)
        report = validate_battle(data)
        self.assertFalse(report['valid'])
        self.assertIn('too_many_equip_candidates', {i['code'] for i in report['issues']})
        self.assertEqual(data, before)

    def test_duplicate_id_rejected_even_if_full_break_state_differs(self):
        data = share(member())
        data['team']['onFieldSvts'][0]['equip1']['candidates'][0]['id'] = 101
        with self.assertRaisesRegex(ValueError, '重复'):
            criteria(data)
        report = validate_battle(data)
        self.assertFalse(report['valid'])
        self.assertIn('duplicate_equip_candidate', {i['code'] for i in report['issues']})

    def test_unsupported_owned_or_invisible_grand_bond_candidates_are_not_ignored(self):
        cases = [dict(member(), supportType='none'),
                 {'svtId': 100100, 'supportType': 'friend', 'equip2': member()['equip1']},
                 {'svtId': 100100, 'supportType': 'friend', 'grandSvt': True,
                  'equip2': member()['equip1']}]
        for item in cases:
            with self.subTest(item=item):
                data = share(item)
                # Give an otherwise valid support when the candidate belongs to an own member.
                if item['supportType'] == 'none':
                    data['team']['onFieldSvts'][1] = {'supportType': 'friend', 'equip1': {'id': 9}}
                with self.assertRaises(ValueError):
                    criteria(data)
                self.assertFalse(validate_battle(data)['valid'])

    def test_malformed_candidates_fail_precisely_and_preserve_source(self):
        cases = [False, {}, [{'id': None}], [{}], [None], [{'id': True}],
                 [{'id': -1}], [{'id': 2**53}], [{'id': 202, 'limitBreak': 1}],
                 [{'id': 202, 'lv': 101}], [{'id': 202, 'candidates': []}]]
        for extra in cases:
            with self.subTest(extra=extra):
                data = share(member())
                data['team']['onFieldSvts'][0]['equip1']['candidates'] = extra
                before = copy.deepcopy(data)
                with self.assertRaises(ValueError):
                    criteria(data)
                self.assertFalse(validate_battle(data)['valid'])
                self.assertEqual(data, before)

    def test_choices_without_a_primary_are_rejected(self):
        for primary in (None, 0):
            data = share({'supportType': 'friend', 'equip1': {'id': primary,
                          'candidates': [{'id': 202}]}})
            with self.assertRaises(ValueError):
                criteria(data)
            report = validate_battle(data)
            self.assertFalse(report['valid'])
            self.assertIn('missing_equip_candidate_primary', {i['code'] for i in report['issues']})

    def test_candidate_primary_has_the_same_strict_id_and_boolean_contract(self):
        for source in ('current', 'legacy'):
            for primary in (True, '101', 101.0, 2**53):
                with self.subTest(source=source, primary=primary):
                    item = {'supportType': 'friend', 'equip1': {'candidates': [{'id': 202}]}}
                    if source == 'current':
                        item['equip1']['id'] = primary
                    else:
                        item['ceId'] = primary
                    with self.assertRaises(ValueError):
                        criteria(share(item))
                    self.assertFalse(validate_battle(share(item))['valid'])
        item = member()
        item['equip1']['limitBreak'] = 'true'
        with self.assertRaises(ValueError):
            criteria(share(item))
        self.assertFalse(validate_battle(share(item))['valid'])


class ChaldeaCandidateExecutionTests(unittest.TestCase):
    def setUp(self):
        self.context = Context(enabled=False)
        self.context.attach.update(chaldea_enabled=True, chaldea_import_source='test')
        self.addCleanup(support.sessions.stop_owner, '')
        self.action = support.SupportAction()
        for name, result in {
            '_get_servant_map': SERVANTS,
            '_get_detector': NS(detect=Mock(return_value=[(0, 0, 100, 100, .99)])),
            '_init_scale': None, '_tap': None, '_shot_base': object(), '_ref_shift': (0, 0),
            '_match_ce': False, '_match_bond': True, '_check_active_view': True,
        }.items():
            setattr(self.action, name, Mock(return_value=result))
        self.action._swipe = Mock(side_effect=lambda *args: setattr(self.context.tasker, 'stopping', True))
        for name in ('time.sleep', 'mfaalog.info', 'mfaalog.error'):
            target = patch('support_action.' + name)
            target.start()
            self.addCleanup(target.stop)
        # Use CE-only friend support to isolate CE selection from identity/skill matching.
        self.item = member()
        self.item.pop('svtId')

    def run_search(self):
        data = share(self.item)
        converted = criteria(data)
        with patch('support_action.fetch_share_data', return_value=(data, None, None)), \
             patch('support_action.build_support_criteria', return_value=converted), \
             patch('support_action.os.path.isfile', return_value=True):
            return self.action.run(self.context, NS(node_name='Chaldea助战action',
                                   custom_action_param='{"source_mode":"chaldea"}'))

    def test_second_candidate_matches_its_own_state_and_stops_before_third(self):
        self.action._match_ce.side_effect = [False, True]
        self.assertTrue(self.run_search().success)
        self.assertEqual([call.args[-3:] for call in self.action._match_ce.call_args_list], [
            ('礼装101.png', support.CE_ANCHOR_NORMAL, '满破'),
            ('礼装202.png', support.CE_ANCHOR_NORMAL, '非满破')])
        self.assertEqual(self.action._tap.call_args.args[-2:], (50, 50))

    def test_all_alternatives_miss_does_not_select_entry(self):
        self.assertFalse(self.run_search().success)
        self.assertEqual(self.action._match_ce.call_count, 3)
        self.assertEqual(self.action._tap.call_count, 3)  # Only the initial class tab clicks.

    def test_grand_each_cell_needs_its_own_or_match(self):
        self.item.update(grandSvt=True, equip3={'id': 404, 'candidates': [
            {'id': 505, 'limitBreak': True}]}, classBoardData={'grandBondEquipSkillChange': False})
        self.action._match_ce.side_effect = [False, True, False, True]
        self.assertTrue(self.run_search().success)
        self.assertEqual([call.args[-3:] for call in self.action._match_ce.call_args_list], [
            ('礼装101.png', support.CE_ANCHOR_GRAND[0], '满破'),
            ('礼装202.png', support.CE_ANCHOR_GRAND[0], '非满破'),
            ('礼装404.png', support.CE_ANCHOR_GRAND[1], '任意'),
            ('礼装505.png', support.CE_ANCHOR_GRAND[1], '满破')])
        self.action._match_bond.assert_called_once()
        self.assertEqual(self.action._match_bond.call_args.args[-1], 'original')

    def test_grand_first_cell_hit_does_not_override_missing_second_cell(self):
        self.item.update(grandSvt=True, equip3={'id': 404, 'candidates': [{'id': 505}]})
        self.action._match_ce.side_effect = [True, False, False]
        self.assertFalse(self.run_search().success)
        self.action._match_bond.assert_not_called()
        self.assertEqual(self.action._tap.call_count, 3)

    def test_missing_template_is_reported_before_search_instead_of_ignoring_candidate(self):
        data = share(self.item)
        converted = criteria(data)
        with patch('support_action.fetch_share_data', return_value=(data, None, None)), \
             patch('support_action.build_support_criteria', return_value=converted), \
             patch('support_action.os.path.isfile', side_effect=lambda path: '礼装202.png' not in path):
            result = self.action.run(self.context, NS(node_name='Chaldea助战action',
                                     custom_action_param='{"source_mode":"chaldea"}'))
        self.assertFalse(result.success)
        self.action._get_detector.assert_not_called()
        self.action._tap.assert_not_called()


if __name__ == '__main__':
    unittest.main()
