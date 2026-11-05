"""Editor bond planning must budget Mash's chosen form without changing it."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent.battle_editor.bond_optimizer import BondPlanner
from agent.battle_editor.catalog import build_catalog
from agent.battle_editor.storage import EditorError

ROOT = Path(__file__).resolve().parents[1]


class MashBondPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mash = next(row for row in build_catalog(ROOT)['servants'] if row['id'] == 800100)
        cls.forms = {row['key']: row for row in cls.mash['forms']}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('agent/custom/servant_list.json', {'servants': [
            {'id': '800100', 'name': '玛修', 'rarity': 3, 'images': ['f_8001000.png'], 'bond': {'tags': ['female']}},
            {'id': '100', 'name': '原队友', 'rarity': 5, 'images': ['f_1000.png'], 'bond': {'tags': ['female']}}]})
        self.write('agent/custom/equip_list.json', {'equips': []})
        self.write('agent/custom/bond_completion_costs.json', {'servants': {'800100': 0, '100': 10}, 'equips': {}})
        self.write('config/Inventory/player_servants.json', {'schema_version': 1, 'kind': 'servants',
                   'scan_complete': True, 'count': 2, 'servants': [{'id': '800100'}, {'id': '100'}]})
        self.write('config/Inventory/player_equips.json', {'schema_version': 1, 'kind': 'equips',
                   'scan_complete': True, 'count': 0, 'equips': []})
        image = self.root / 'assets/resource/base/image/servant_face'
        image.mkdir(parents=True)
        for name in ['f_8001000.png', 'f_1000.png']:
            (image / name).write_bytes(b'existing recognition template')
        self.catalog = {'servants': [copy.deepcopy(self.mash), {'id': 100, 'name': '原队友'}], 'equips': []}

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def planner(self):
        return BondPlanner(self.root, self.catalog)

    def member(self, key='paladin', **overrides):
        form = self.forms[key]
        return {'svtId': 800100, 'supportType': 'none', 'limitCount': form['limitCount'],
                'skillIds': list(form['skillIds']), 'tdId': form['tdId'],
                'skillLvs': [3, 4, 5], 'futureMember': [None, False, {'keep': 17}], **overrides}

    def data(self, member=None):
        return {'team': {'name': '马修形态预算', 'onFieldSvts': [member or self.member(), None, None],
                         'backupSvts': [None, None, None]},
                'actions': [{'type': 'skill', 'svt': 0, 'skill': 1}], 'futureRoot': {'keep': [None]}}

    def options(self, **overrides):
        return {'reservedSupportSlot': 5, 'excludeServantIds': [100], **overrides}

    def test_five_star_aliases_are_rejected_when_the_retained_cost_exceeds_limit(self):
        for limit in self.forms['paladin']['limitCounts']:
            with self.subTest(limit=limit):
                data = self.data(self.member(limitCount=limit))
                before = copy.deepcopy(data)
                with self.assertRaises(EditorError) as raised:
                    self.planner().optimize(data, self.options(costLimit=15))
                self.assertEqual(raised.exception.code, 'bond_cost_exceeded')
                self.assertIn('16', str(raised.exception))
                self.assertIn('队伍COST上限', str(raised.exception))
                self.assertEqual(data, before)

    def test_five_star_before_and_after_include_sixteen_without_rewriting_explicit_choices(self):
        data = self.data(self.member(skillIds=[2476450, 970660, 133550], tdId=800108, transformVal=7))
        before = copy.deepcopy(data)
        planner = self.planner()
        files = {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        result = planner.optimize(data, self.options(costLimit=16))
        self.assertEqual((result['summary']['beforeCost'], result['summary']['afterCost']), (16, 16))
        self.assertEqual(result['data'], before)
        self.assertEqual(data, before)
        self.assertEqual(planner.servants[800100]['cost'], 0)
        self.assertEqual(planner.servants[800100]['rarity'], 3)
        self.assertEqual(files, {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_early_normal_and_ortinax_stay_zero_cost_and_keep_all_original_fields(self):
        for key in ['early', 'normal', 'ortenaus', 'black-barrel', 'ortenaus-restored']:
            for limit in self.forms[key]['limitCounts']:
                with self.subTest(form=key, limit=limit):
                    data = self.data(self.member(key, limitCount=limit))
                    before = copy.deepcopy(data)
                    result = self.planner().optimize(data, self.options(costLimit=1))
                    self.assertEqual((result['summary']['beforeCost'], result['summary']['afterCost']), (0, 0))
                    self.assertEqual(result['data'], before)
                    self.assertEqual(data, before)

    def test_omitted_and_null_limit_use_chaldeas_default_ascension_without_materializing_it(self):
        for omitted in [True, False]:
            data = self.data(self.member('normal'))
            member = data['team']['onFieldSvts'][0]
            if omitted:
                member.pop('limitCount')
            else:
                member['limitCount'] = None
            before = copy.deepcopy(data)
            result = self.planner().optimize(data, self.options(costLimit=1))
            self.assertEqual(result['summary']['beforeCost'], 0)
            self.assertEqual(result['data'], before)

    def test_unknown_form_is_not_guessed_from_matching_explicit_skills_or_np(self):
        for limit in [999, '800190', True, -1]:
            with self.subTest(limit=limit):
                data = self.data(self.member(limitCount=limit))
                before = copy.deepcopy(data)
                with self.assertRaises(EditorError) as raised:
                    self.planner().optimize(data, self.options())
                self.assertEqual(raised.exception.code, 'bond_mash_form_unknown')
                self.assertIn('第1槽', str(raised.exception))
                self.assertIn('切换马修形态', str(raised.exception))
                self.assertEqual(data, before)

    def test_support_does_not_add_cost_or_change_even_when_its_shape_is_unknown(self):
        support = self.member(supportType='friend', limitCount=999)
        data = self.data({'svtId': 100, 'supportType': 'none'})
        data['team']['onFieldSvts'][1] = support
        result = self.planner().optimize(data, self.options(costLimit=10, excludeServantIds=[100, 800100]))
        self.assertEqual((result['summary']['beforeCost'], result['summary']['afterCost']), (10, 10))
        self.assertEqual(result['data']['team']['onFieldSvts'][1], support)
        self.assertEqual(result['summary']['reservedSupportSlot'], 1)

    def test_newly_filled_mash_uses_four_star_normal_budget_and_standard_chaldea_fields(self):
        data = self.data({'svtId': 100, 'supportType': 'none'})
        before = copy.deepcopy(data)
        result = self.planner().optimize(data, self.options(costLimit=10, preferredRarity=4))
        self.assertEqual(result['summary']['addedServants'], 1)
        self.assertEqual((result['summary']['beforeCost'], result['summary']['afterCost']), (10, 10))
        added = result['data']['team']['onFieldSvts'][1]
        self.assertEqual(added['svtId'], 800100)
        for key in ['limitCount', 'skillIds', 'tdId']:
            self.assertEqual(added[key], self.forms['normal'][key])
        self.assertNotIn('_editorForm', added)
        self.assertEqual(data, before)
        self.assertEqual(result['data']['actions'], data['actions'])

    def test_grand_team_still_uses_explicit_actual_cost_including_unknown_mash_shape(self):
        for limit in [800190, 999]:
            data = self.data(self.member(grandSvt=True, limitCount=limit, equip1={}, equip2={}, equip3={}))
            before = copy.deepcopy(data)
            result = self.planner().optimize(data, self.options(costLimit=7, currentCost=7))
            self.assertEqual((result['summary']['beforeCost'], result['summary']['afterCost']), (7, 7))
            self.assertEqual(result['data'], before)
            self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main()
