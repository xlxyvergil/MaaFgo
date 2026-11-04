"""CE-only friend support round-trips and reaches the existing ALL/CE search path."""
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'agent'), str(ROOT / 'agent/custom')]
from agent.battle_editor.server import create_server
from agent.battle_editor.validation import validate_battle
from chaldea.support_criteria import build_support_criteria
from formation_test_support import formation
from test_support_fallback import Context
import support_action
import test_battle_editor_bond as bond_fixtures


def team(support=None):
    return {'team': {'onFieldSvts': [support or {'supportType': 'friend', 'equip1': {'id': 9400340}},
                                    {'svtId': 100100, 'supportType': 'none'}, None],
                     'backupSvts': [None, None, None],
                     'mysticCode': {'mysticCodeId': 210, 'level': 10}}, 'actions': []}


def errors(report):
    return [issue for issue in report['issues'] if issue['severity'] == 'error']


class CeOnlySupportCriteriaTests(unittest.TestCase):
    def criteria(self, support):
        return build_support_criteria(team(support), {}, resolve_name=lambda _: '礼装')

    def test_absent_null_and_zero_friend_identity_need_no_servant_metadata(self):
        for identity in ({}, {'svtId': None}, {'svtId': 0}):
            support = {'supportType': 'friend', 'equip1': {'id': 9400340, 'limitBreak': True}, **identity}
            before = copy.deepcopy(support)
            result = self.criteria(support)
            self.assertEqual((result['servant_id'], result['class_name'], result['images']), ('', 'any', []))
            self.assertEqual(result['active'], [0] * 3)
            self.assertEqual(result['passive'], [0] * 5)
            self.assertEqual((result['level'], result['np_level']), (0, 0))
            self.assertEqual([(ce['id'], ce['status']) for ce in result['ce_specs']], [(9400340, '满破')])
            self.assertEqual(support, before)

    def test_no_identity_discards_stale_level_filters_but_keeps_grand_ce_mapping(self):
        support = {'supportType': 'friend', 'grandSvt': True, 'lv': 120, 'tdLv': 5,
                   'skillLvs': [10] * 3, 'appendLvs': [10] * 5,
                   'equip1': {'id': 1}, 'equip2': {'id': 2}, 'equip3': {'id': 3, 'limitBreak': True},
                   'classBoardData': {'grandBondEquipSkillChange': True}}
        result = self.criteria(support)
        self.assertEqual(result['support_type'], 'grand')
        self.assertEqual(result['ce_bond'], '50np')
        self.assertEqual([(ce['id'], ce['slot'], ce['source_slot']) for ce in result['ce_specs']],
                         [(1, 1, 1), (3, 2, 3)])
        self.assertEqual((result['active'], result['passive'], result['np_level'], result['level']),
                         ([0] * 3, [0] * 5, 0, 0))

    def test_missing_npc_fixed_and_malformed_friend_identity_do_not_become_any(self):
        for kind in ('npc', 'fixed'):
            for identity in ({}, {'svtId': None}, {'svtId': 0}):
                with self.subTest(kind=kind, identity=identity), self.assertRaises(ValueError):
                    self.criteria({'supportType': kind, **identity})
        for value in (False, True, -1, '', '0', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.criteria({'supportType': 'friend', 'svtId': value})

    def test_legacy_ce_and_explicit_zero_follow_existing_filter_rules(self):
        support = {'supportType': 'friend', 'ceId': 3, 'ceLimitBreak': True}
        self.assertEqual(self.criteria(support)['ce_specs'][0]['id'], 3)
        support['equip1'] = {'id': 0}
        self.assertEqual(self.criteria(support)['ce_specs'], [])

    def test_real_chaldea_action_uses_all_and_ce_without_any_identity_checks(self):
        context = Context(enabled=False)
        self.addCleanup(support_action.sessions.stop_owner, '')
        context.attach.update(chaldea_enabled=True, chaldea_import_source='fixture')
        support = {'supportType': 'friend', 'ceId': 9400340, 'ceLimitBreak': True,
                   'lv': 120, 'tdLv': 5, 'skillLvs': [10] * 3, 'appendLvs': [10] * 5}
        action = support_action.SupportAction()
        for name, value in {'_get_servant_map': {}, '_get_detector': NS(detect=Mock(return_value=[(0, 0, 100, 100, .99)])),
                            '_init_scale': None, '_tap': None, '_shot_base': object(), '_ref_shift': (0, 0),
                            '_match_ce': True, '_match_servant': False, '_check_active_view': False,
                            '_match_passive': False, '_is_connecting': False}.items():
            setattr(action, name, Mock(return_value=value))
        with patch('support_action.fetch_share_data', return_value=(team(support), None, None)), \
                patch('support_action.os.path.isfile', return_value=True), \
                patch('support_action.time.sleep'), patch('support_action.mfaalog.info'):
            result = action.run(context, NS(node_name='Chaldea助战action', custom_action_param='{"source_mode":"chaldea"}'))
        self.assertTrue(result.success)
        action._match_servant.assert_not_called()
        action._check_active_view.assert_not_called()
        action._match_passive.assert_not_called()
        action._match_ce.assert_called_once()
        self.assertEqual(action._tap.call_args_list,
                         [call(context.tasker.controller, *support_action.CLASS_TABS['ALL'])] * 3
                         + [call(context.tasker.controller, 50, 50)])


class CeOnlySupportValidationTests(unittest.TestCase):
    def test_ce_only_friend_is_present_and_preserves_missing_null_and_zero(self):
        for identity in ({}, {'svtId': None}, {'svtId': 0}):
            data = team({'supportType': 'friend', 'equip1': {'id': 9400340}, **identity})
            before = copy.deepcopy(data)
            report = validate_battle(data)
            self.assertTrue(report['valid'], report)
            self.assertNotIn('missing_support', {issue['code'] for issue in report['issues']})
            self.assertEqual(data, before)

    def test_unidentified_owned_npc_fixed_or_boolean_identity_is_rejected(self):
        for kind in ('none', 'npc', 'fixed'):
            for identity in ({}, {'svtId': None}, {'svtId': 0}):
                self.assertTrue(errors(validate_battle(team({'supportType': kind, **identity}))))
        for value in (False, True, -1, '0'):
            self.assertTrue(errors(validate_battle(team({'supportType': 'friend', 'svtId': value}))))

    def test_missing_filters_and_ignored_identity_requirements_are_explicit_warnings(self):
        data = team({'supportType': 'friend', 'lv': 90, 'tdLv': 5, 'skillLvs': [10] * 3,
                     'appendLvs': [0] * 5, 'future': {'keep': [None]}})
        report = validate_battle(data)
        self.assertTrue(report['valid'], report)
        codes = {issue['code'] for issue in report['issues'] if issue['severity'] == 'warning'}
        self.assertIn('unrestricted_support', codes)
        self.assertIn('unspecified_support_attributes', codes)
        zero = validate_battle(team({'supportType': 'friend', 'skillLvs': [0] * 3,
                                    'appendLvs': [0] * 5, 'equip1': {'id': 1}}))
        self.assertNotIn('unspecified_support_attributes', {issue['code'] for issue in zero['issues']})

    def test_identity_cannot_execute_skills_nps_or_targeted_face_cards(self):
        actions = [{'type': 'skill', 'svt': 0, 'skill': 0},
                   {'type': 'attack', 'attacks': [{'svt': 0, 'isTD': True, 'card': -1}]},
                   {'type': 'attack', 'attacks': [{'svt': 0, 'card': 0, 'cardType': 1}]}]
        for action in actions:
            data = team();data['actions'] = [action];before = copy.deepcopy(data)
            report = validate_battle(data)
            self.assertFalse(report['valid'])
            issue = next(issue for issue in errors(report) if issue['code'] == 'unspecified_support_actor')
            self.assertEqual(issue['actionIndex'], 0)
            self.assertEqual(data, before)

    def test_it_can_receive_ally_target_and_swap_by_slot_but_not_act_after_swap(self):
        data = team()
        data['actions'] = [{'type': 'skill', 'svt': 1, 'skill': 0, 'options': {'playerTarget': 0}},
                           {'type': 'attack', 'attacks': [{'isAuto': True}]}]
        self.assertTrue(validate_battle(data)['valid'])
        data['team']['backupSvts'][0] = {'svtId': 100200, 'supportType': 'none'}
        data['delegate'] = {'replaceMemberIndexes': [[0, 0]]}
        data['actions'] = [{'type': 'skill', 'svt': None, 'skill': 2},
                           {'type': 'skill', 'svt': 0, 'skill': 0},
                           {'type': 'attack', 'attacks': [{'svt': 0, 'isTD': True, 'card': -1}]}]
        self.assertTrue(validate_battle(data)['valid'])
        data['delegate']['replaceMemberIndexes'] = [[1, 0]]
        # Move the unknown support to the frontline slot previously occupied by a known servant.
        data['team']['onFieldSvts'][0], data['team']['backupSvts'][0] = data['team']['backupSvts'][0], data['team']['onFieldSvts'][0]
        data['actions'][1]['svt'] = 1
        data['actions'][2]['attacks'][0]['svt'] = 1
        self.assertIn('unspecified_support_actor', {issue['code'] for issue in errors(validate_battle(data))})


class CeOnlySupportFormationTests(unittest.TestCase):
    def action(self):
        action = formation.AutoFormationFromChaldea.__new__(formation.AutoFormationFromChaldea)
        action._get_servant_info = Mock(return_value={'id': 100100})
        action._fail = Mock(return_value=False)
        action.use_support_substitution = False
        return action

    def test_unidentified_friend_becomes_support_target_without_pretending_identity(self):
        action = self.action();data = team();before = copy.deepcopy(data)
        expected = action._build_expected(data)
        self.assertEqual(expected[0]['kind'], 'SUPPORT')
        self.assertIsNone(expected[0]['svt_id'])
        self.assertEqual(expected[0]['equip_id'], 9400340)
        action.expected = expected
        actual = [{'kind': 'SUPPORT', 'svt_id': None}] + [{'kind': 'EMPTY', 'svt_id': None}] * 5
        self.assertTrue(action._configure_support_target(actual, 1))
        self.assertTrue(action._matches(expected[0], actual[0]))
        action._get_servant_info.assert_called_once_with(100100)
        self.assertEqual(data, before)

    def test_specific_support_and_substitution_still_require_actual_identity(self):
        action = self.action();action.expected = action._build_expected(team({'supportType': 'friend', 'svtId': 100100}))
        actual = [{'kind': 'SUPPORT', 'svt_id': None}] + [{'kind': 'EMPTY', 'svt_id': None}] * 5
        self.assertFalse(action._configure_support_target(actual, 1))
        action.use_support_substitution = True
        self.assertFalse(action._configure_support_target(actual, 0))
        action.expected = self.action()._build_expected(team())
        self.assertFalse(action._configure_support_target([{'kind': 'LOCAL', 'svt_id': 1}] * 6, 1))

    def stable_layout(self, expected, frames):
        action = self.action()
        action.expected = expected
        action.context = NS(tasker=NS(stopping=False))
        action._detect_slots = Mock(side_effect=frames)
        with patch.object(formation.time, 'monotonic', side_effect=[
                0, 0, 0, formation.SLOT_LAYOUT_VERIFY_TIMEOUT_SECONDS + 1]), \
                patch.object(formation.time, 'sleep'):
            return action._detect_slots_stable()

    def test_any_support_identity_may_fluctuate_before_and_after_relocation(self):
        expected = [{'kind': 'SUPPORT', 'svt_id': None}] + [{'kind': 'EMPTY', 'svt_id': None}] * 5
        # The observed support can still be away from its requested destination.
        for slot in (0, 3):
            for identities in ((100100, None), (None, 100100)):
                with self.subTest(slot=slot, identities=identities):
                    frames = [[{'kind': 'EMPTY', 'svt_id': None} for _ in range(6)] for _ in range(2)]
                    for frame, identity in zip(frames, identities):
                        frame[slot] = {'kind': 'SUPPORT', 'svt_id': identity}
                    self.assertIs(self.stable_layout(expected, frames), frames[1])

    def test_stability_still_requires_support_marker_and_other_identities(self):
        any_support = [{'kind': 'SUPPORT', 'svt_id': None}] + [{'kind': 'EMPTY', 'svt_id': None}] * 5
        specific_support = copy.deepcopy(any_support)
        specific_support[0]['svt_id'] = 100100
        cases = [
            (specific_support, {'kind': 'SUPPORT', 'svt_id': 100100}, {'kind': 'SUPPORT', 'svt_id': None}),
            ([{'kind': 'EMPTY', 'svt_id': None}] * 6,
             {'kind': 'SUPPORT', 'svt_id': 100100}, {'kind': 'SUPPORT', 'svt_id': None}),
            (any_support, {'kind': 'SUPPORT', 'svt_id': None}, {'kind': 'LOCAL', 'svt_id': None}),
            (any_support, {'kind': 'LOCAL', 'svt_id': 100100}, {'kind': 'LOCAL', 'svt_id': None}),
        ]
        for expected, before, after in cases:
            with self.subTest(expected=expected[0], before=before, after=after):
                frames = [[{'kind': 'EMPTY', 'svt_id': None} for _ in range(6)] for _ in range(2)]
                frames[0][0], frames[1][0] = before, after
                self.assertIsNone(self.stable_layout(expected, frames))

    def test_bond_planning_keeps_whole_ce_only_support_slot(self):
        fixture = bond_fixtures.BondEditorTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        data = fixture.data();support = {'supportType': 'friend', 'equip1': {'id': 40}, 'future': [None]}
        data['team']['onFieldSvts'][1] = copy.deepcopy(support)
        before = copy.deepcopy(data)
        result = fixture.planner().optimize(data, {'costLimit': 28})
        self.assertEqual(result['data']['team']['onFieldSvts'][1], support)
        self.assertEqual(result['summary']['reservedSupportSlot'], 1)
        self.assertEqual(result['summary']['beforeCost'], 28)
        self.assertEqual(data, before)


class CeOnlySupportHttpTests(unittest.TestCase):
    def test_formal_save_restore_and_reject_actor_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix='ce-only 中文 ') as directory:
            root = Path(directory);web = root / 'assets/web/battle-editor';web.mkdir(parents=True)
            (web / 'index.html').write_text('<!doctype html><title>test</title>', encoding='utf-8')
            server = create_server(root, 0);server._catalog = {'servants': []}
            thread = threading.Thread(target=server.serve_forever);thread.start()
            def request(method, path, payload=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=10)
                connection.request(method, path, body=None if payload is None else json.dumps(payload).encode(),
                                   headers={'X-Editor-Token': server.token, 'Content-Type': 'application/json'})
                response = connection.getresponse();body = json.loads(response.read());status = response.status
                connection.close();return status, body
            try:
                data = team();data['team']['onFieldSvts'][0]['future'] = {'keep': [None]};before = copy.deepcopy(data)
                status, saved = request('POST', '/api/battles', {'name': 'ce_only.json', 'data': data})
                self.assertEqual(status, 201, saved)
                self.assertTrue(saved['validation']['valid'])
                status, restored = request('GET', '/api/battles/ce_only.json')
                self.assertEqual(restored['data'], before)
                invalid = copy.deepcopy(data);invalid['actions'] = [{'type': 'skill', 'svt': 0, 'skill': 0}]
                status, rejected = request('PUT', '/api/battles/ce_only.json', {'data': invalid, 'expectedHash': saved['hash']})
                self.assertEqual(status, 422, rejected)
                self.assertEqual(request('GET', '/api/battles/ce_only.json')[1]['data'], before)
                self.assertEqual(data, before)
            finally:
                server.stop();thread.join(5);server.server_close()


if __name__ == '__main__':
    unittest.main()
