"""The offline editor adapter must keep combat data and locked formation intact."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.battle_editor.bond_optimizer import BondPlanner
from agent.battle_editor.storage import EditorError


class BondEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.servants = [{'id':str(i),'name':f'从者{i}','class':'saber','rarity':star,
                          'images':[f'f_{i}.png'],'bond':{'tags':tags}}
                         for i,star,tags in [(100,5,['female']),(200,5,['male']),(300,4,['female']),(400,0,['male']),(500,5,['female'])]]
        self.equips = [
            {'id':'10','name':'全体','images':['f_10.png'],'bond':{'bonus_type':'percent','bonus':10,'target':'all',
              'atlas_effects':[{'strength_status':1,'percent_bonus':2},{'strength_status':99,'percent_bonus':10}]}},
            {'id':'20','name':'女性','images':['f_20.png'],'bond':{'bonus_type':'percent','bonus':20,'target':'female',
              'atlas_effects':[{'strength_status':1,'percent_bonus':4},{'strength_status':99,'percent_bonus':20}]}},
            {'id':'30','name':'固定','images':['f_30.png'],'bond':{'bonus_type':'flat_per_servant','bonus':50,'target':'all'}},
            {'id':'40','name':'活动','images':['f_40.png'],'bond':{'bonus_type':'percent','bonus':100,'target':'all','event_id':1}},
            {'id':'50','name':'战斗礼装','images':['f_50.png'],'bond':{'bonus_type':'none','bonus':0}},
        ]
        self.write('agent/custom/servant_list.json', {'servants':self.servants})
        self.write('agent/custom/equip_list.json', {'equips':self.equips})
        self.write('agent/custom/bond_completion_costs.json', {'servants':{'100':16,'200':16,'300':12,'400':0,'500':16},
                                                              'equips':{'10':12,'20':12,'30':5,'40':0,'50':12,'99':9}})
        for row in self.servants:
            self.png('servant_face/' + row['images'][0])
        for row in self.equips:
            self.png('EquipFaces/list/' + row['images'][0])
            self.png('EquipFaces/team/' + row['images'][0])
        for kind,rows in [('servants',self.servants),('equips',self.equips)]:
            self.write(f'config/Inventory/player_{kind}.json', {'schema_version':1,'kind':kind,'scan_complete':True,
                       'catalog_complete':True,'count':len(rows),'generated_at':'2026-10-24T00:00:00+08:00',
                       kind:[{'id':x['id'],'limit_break_known':x['id']=='10','limit_break':x['id']=='10'} for x in rows]})
        self.catalog = {'servants':[{'id':int(x['id']),'name':x['name'],'aliases':[101] if x['id']=='100' else []} for x in self.servants],
                        'equips':[{'id':int(x['id']),'name':x['name']} for x in self.equips]}

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),'utf-8')

    def png(self, relative):
        path = self.root / 'assets/resource/base/image' / relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'template present')

    def planner(self): return BondPlanner(self.root,self.catalog)
    def data(self):
        return {'team':{'name':'测试','onFieldSvts':[{'svtId':100,'future':{'keep':[1,None]},'skillIds':[11,12,13],
                 'equip1':{'id':50,'futureCe':{'keep':True}}},None,None], 'backupSvts':[None,None,None],
                 'mysticCode':{'mysticCodeId':210,'level':10},'unknownTeam':{'keep':1}},
                'actions':[{'type':'future-action','unknown':{'a':[1,None]}},{'type':'skill','svt':0,'skill':0}],
                'unknownRoot':{'text':'保留'}}

    @staticmethod
    def members(data): return data['team']['onFieldSvts'] + data['team']['backupSvts']

    def test_preserves_all_existing_data_and_commits_only_new_slots(self):
        data=self.data(); before=copy.deepcopy(data)
        files={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=self.planner().optimize(data,{})
        self.assertEqual(data,before)
        self.assertEqual(result['data']['actions'],data['actions'])
        self.assertEqual(result['data']['unknownRoot'],data['unknownRoot'])
        self.assertEqual(result['data']['team']['unknownTeam'],data['team']['unknownTeam'])
        self.assertEqual(result['data']['team']['onFieldSvts'][0],data['team']['onFieldSvts'][0])
        self.assertIsNone(result['data']['team']['backupSvts'][2])
        self.assertEqual(result['summary']['addedServants'],4)
        ids=[m['svtId'] for m in self.members(result['data']) if m]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertLessEqual(result['summary']['afterCost'],117)
        self.assertGreater(result['summary']['gain'],0)
        self.assertEqual(files,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_support_and_its_ce_never_contribute_or_change(self):
        data=self.data(); support={'svtId':100,'supportType':'friend','equip1':{'id':40},'grandSvt':True,'unknown':[1,2]}
        data['team']['onFieldSvts'][1]=support
        result=self.planner().optimize(data,{'costLimit':28})
        self.assertEqual(result['data']['team']['onFieldSvts'][1],support)
        self.assertEqual(result['summary']['beforeCost'],28)
        self.assertEqual(result['summary']['beforeBond'],1440)
        self.assertEqual(result['summary']['reservedSupportSlot'],1)

    def test_cost_shortage_falls_back_to_zero_star(self):
        result=self.planner().optimize(self.data(),{'costLimit':28})
        ids=[m['svtId'] for m in self.members(result['data']) if m]
        self.assertEqual(ids,[100,400])
        self.assertLessEqual(result['summary']['afterCost'],28)

    def test_preferred_rarity_is_applied_before_other_stars(self):
        result=self.planner().optimize(self.data(),{'preferredRarity':4})
        self.assertEqual(result['data']['team']['onFieldSvts'][1]['svtId'],300)

    def test_explicit_exclusions_and_alias_deduplication(self):
        data=self.data(); data['team']['onFieldSvts'][0]['svtId']=101
        result=self.planner().optimize(data,{'excludeServantIds':[500,200]})
        ids=[m['svtId'] for m in self.members(result['data']) if m]
        self.assertNotIn(100,ids); self.assertNotIn(200,ids); self.assertNotIn(500,ids)
        self.assertIn(101,ids)

    def test_replace_equips_is_explicit_and_preserves_unknown_equip_fields(self):
        result=self.planner().optimize(self.data(),{'modifyEquips':True})
        member=result['data']['team']['onFieldSvts'][0]
        self.assertNotEqual(member.get('equip1',{}).get('id'),50)
        self.assertEqual(member['equip1']['futureCe'],{'keep':True})
        self.assertEqual(member['future'],{'keep':[1,None]})

    def test_event_equips_excluded_and_mlb_requirements_written(self):
        result=self.planner().optimize(self.data(),{})
        equips=[m['equip1'] for m in self.members(result['data']) if m and m.get('equip1')]
        self.assertNotIn(40,[e['id'] for e in equips])
        for e in equips:
            if e['id'] in (10,20): self.assertIs(e['limitBreak'],True)

    def test_confirmed_mlb_filter_rejects_unknown_mlb(self):
        result=self.planner().optimize(self.data(),{'confirmedMlbOnly':True})
        ids=[m['equip1']['id'] for m in self.members(result['data']) if m and m.get('equip1')]
        self.assertIn(10,ids); self.assertNotIn(20,ids)

    def test_protected_unmlb_equip_scores_base_and_not_added_twice(self):
        data=self.data(); data['team']['onFieldSvts'][0]['equip1']={'id':10,'limitBreak':False}
        result=self.planner().optimize(data,{})
        self.assertEqual(result['summary']['beforeBond'],1468)
        ids=[m['equip1']['id'] for m in self.members(result['data']) if m and m.get('equip1')]
        self.assertEqual(ids.count(10),1)

    def test_legacy_equip_and_limit_break_are_protected(self):
        data=self.data(); m=data['team']['onFieldSvts'][0]; m.pop('equip1');m.update(ceId=10,ceLimitBreak=True)
        result=self.planner().optimize(data,{})
        self.assertEqual(result['data']['team']['onFieldSvts'][0],m)
        self.assertEqual(result['summary']['beforeBond'],1584)

    def test_grand_requires_explicit_actual_cost_and_keeps_three_equips(self):
        data=self.data(); m=data['team']['onFieldSvts'][0];m.update(grandSvt=True,equip2={'id':10},equip3={'id':20},classBoardData={'future':3})
        with self.assertRaisesRegex(EditorError,'实际COST'): self.planner().optimize(data,{})
        result=self.planner().optimize(data,{'currentCost':28,'modifyEquips':True})
        self.assertEqual(result['data']['team']['onFieldSvts'][0],m)
        self.assertEqual(result['summary']['beforeCost'],28)

    def test_unknown_fixed_equip_effect_keeps_known_cost(self):
        data=self.data();data['team']['onFieldSvts'][0]['equip1']={'id':99,'future':1}
        result=self.planner().optimize(data,{})
        self.assertEqual(result['summary']['beforeCost'],25)
        self.assertEqual(result['data']['team']['onFieldSvts'][0]['equip1'],{'id':99,'future':1})
        self.assertTrue(any('零加成' in s for s in result['warnings']))

    def test_missing_templates_never_become_candidates(self):
        (self.root/'assets/resource/base/image/servant_face/f_500.png').unlink()
        (self.root/'assets/resource/base/image/EquipFaces/list/f_20.png').unlink()
        result=self.planner().optimize(self.data(),{})
        self.assertNotIn(500,[m['svtId'] for m in self.members(result['data']) if m])
        self.assertNotIn(20,[m['equip1']['id'] for m in self.members(result['data']) if m and m.get('equip1')])

    def test_editor_only_picture_cannot_replace_missing_team_template(self):
        (self.root/'assets/resource/base/image/EquipFaces/team/f_20.png').unlink()
        preview=self.root/'agent/battle_editor/data/ui/media/f_20.png'
        preview.parent.mkdir(parents=True)
        preview.write_bytes(b'editor preview only')
        result=self.planner().optimize(self.data(),{'ownedEquipsOnly':False})
        self.assertNotIn(20,[m['equip1']['id'] for m in self.members(result['data']) if m and m.get('equip1')])

    def test_invalid_inventory_requires_explicit_opt_out(self):
        for corruption in ({'schema_version':0}, {'schema_version':1,'kind':'servants','scan_complete':False},
                           {'schema_version':1,'kind':'servants','scan_complete':True,'count':2,'servants':[{'id':100},{'id':100}]}):
            self.write('config/Inventory/player_servants.json',corruption)
            with self.assertRaisesRegex(EditorError,'本地从者库不可用'): self.planner().optimize(self.data(),{})
            result=self.planner().optimize(self.data(),{'ownedServantsOnly':False})
            self.assertTrue(any('全图鉴' in s for s in result['warnings']))

    def test_options_and_team_shape_fail_without_mutating_input(self):
        for patch in ({'costLimit':True},{'costLimit':0},{'preferredRarity':6},{'bondBase':-1},{'ownedEquipsOnly':'true'},
                      {'reservedSupportSlot':6},{'excludeServantIds':[True]},{'unknown':1},{'currentCost':0}):
            data=self.data();before=copy.deepcopy(data)
            with self.subTest(patch=patch), self.assertRaises(EditorError): self.planner().optimize(data,patch)
            self.assertEqual(data,before)
        for rows in ({},False,[{},None,None],[{'svtId':100,'supportType':'other'},None,None]):
            data=self.data();data['team']['onFieldSvts']=rows
            with self.assertRaises(EditorError): self.planner().optimize(data,{})

    def test_invalid_support_and_retained_cost_are_rejected(self):
        data=self.data()
        with self.assertRaisesRegex(EditorError,'空位'): self.planner().optimize(data,{'reservedSupportSlot':0})
        with self.assertRaisesRegex(EditorError,'超过上限'): self.planner().optimize(data,{'costLimit':27})
        data['team']['backupSvts']=[{'svtId':200,'supportType':'friend'},{'svtId':300,'supportType':'npc'},None]
        with self.assertRaisesRegex(EditorError,'一个助战'): self.planner().optimize(data,{})

    def test_no_local_duplicate_and_no_more_than_five(self):
        data=self.data(); data['team']['onFieldSvts'][1]={'svtId':101}
        with self.assertRaisesRegex(EditorError,'重复自有'): self.planner().optimize(data,{})
        data['team']['onFieldSvts']=[{'svtId':100},{'svtId':200},{'svtId':300}]
        data['team']['backupSvts']=[{'svtId':400},{'svtId':500},{'svtId':100}]
        with self.assertRaisesRegex(EditorError,'空位'): self.planner().optimize(data,{})

    def test_empty_candidate_range_returns_clear_warning(self):
        self.write('config/Inventory/player_equips.json',{'schema_version':1,'kind':'equips','scan_complete':True,'count':0,'equips':[]})
        result=self.planner().optimize(self.data(),{})
        self.assertEqual(result['summary']['candidateEquips'],0)
        self.assertTrue(any('没有可用' in s for s in result['warnings']))


class BondWorkerTests(unittest.TestCase):
    def server(self):
        return SimpleNamespace(root=Path(__file__).resolve().parents[1],_bond_planning=threading.BoundedSemaphore(1))

    def test_worker_timeout_releases_planner_and_never_returns_partial_data(self):
        from agent.battle_editor.server import EditorHTTPServer as EditorServer
        server=self.server()
        with patch('agent.battle_editor.server.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',45)) as run:
            with self.assertRaisesRegex(EditorError,'超过45秒'): EditorServer.optimize_bond(server,{}, {})
        self.assertEqual(run.call_args.kwargs['timeout'],45)
        self.assertTrue(server._bond_planning.acquire(False))
        server._bond_planning.release()

    def test_busy_worker_does_not_launch_another_process(self):
        from agent.battle_editor.server import EditorHTTPServer as EditorServer
        server=self.server();server._bond_planning.acquire()
        with patch('agent.battle_editor.server.subprocess.run') as run:
            with self.assertRaisesRegex(EditorError,'已有羁绊规划'): EditorServer.optimize_bond(server,{}, {})
            run.assert_not_called()
        server._bond_planning.release()

    def test_worker_errors_preserve_actionable_code(self):
        from agent.battle_editor.server import EditorHTTPServer as EditorServer
        server=self.server()
        response={'ok':False,'error':{'code':'bond_inventory_unavailable','message':'库存不可用','status':422}}
        with patch('agent.battle_editor.server.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(response))):
            with self.assertRaises(EditorError) as raised: EditorServer.optimize_bond(server,{}, {})
        self.assertEqual(raised.exception.code,'bond_inventory_unavailable')
        self.assertEqual(raised.exception.status,422)
        self.assertTrue(server._bond_planning.acquire(False));server._bond_planning.release()


if __name__=='__main__': unittest.main()
