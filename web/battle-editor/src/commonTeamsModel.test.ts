import test from 'node:test';
import assert from 'node:assert/strict';
import {appendCommonTeams,commonTeamDate,commonTeamEquipId,commonTeamQuery,readCommonTeamCatalog,readCommonTeamPage} from './commonTeamsModel';

const directory=()=>({classes:[{id:'saber',label:'剑'},{id:'ex1',label:'EX1'}],quests:[{id:'saber_7',classId:'saber',label:'冠位戴冠战 Ⅶ',difficulty:'100★★★',phase:1,questIds:[94150847,94150847]}]});
const team=(id=1)=>({id,name:'队伍',author:'作者',createdAt:1762400000,upvotes:3,downvotes:0,turnCount:3,servants:[{svtId:100100,support:false,equipId:9400340,grandSvt:true},{svtId:0,support:true,equipId:9400400,grandSvt:false},null,null,null,null]});
const request={questId:'saber_7',sort:'upvotes' as const,offset:0};
const page=()=>({...request,phase:1,pageSize:12,total:20,hasMore:true,nextOffset:12,teams:[team()]});

test('catalog keeps server class and difficulty order and safely deduplicates equivalent quest IDs',()=>{
 const result=readCommonTeamCatalog(directory());
 assert.deepEqual(result.classes.map(item=>item.id),['saber','ex1']);
 assert.deepEqual(result.quests[0].questIds,[94150847]);
 assert.equal(result.quests[0].difficulty,'100★★★');
});

test('catalog rejects unknown class references and duplicate selector keys rather than querying the wrong battle',()=>{
 assert.throws(()=>readCommonTeamCatalog({...directory(),quests:[{...directory().quests[0],classId:'archer'}]}));
 assert.throws(()=>readCommonTeamCatalog({...directory(),quests:[...directory().quests,...directory().quests]}));
 assert.throws(()=>readCommonTeamCatalog({...directory(),quests:[{...directory().quests[0],id:'https://other.example/'}]}));
 assert.throws(()=>readCommonTeamCatalog({classes:[],quests:[]}),/目录为空/);
});

test('page preserves all six positions including CE-only support and grand flags',()=>{
 const result=readCommonTeamPage(page(),request);
 assert.deepEqual(result.teams[0].servants[1],{svtId:0,support:true,equipId:9400400,grandSvt:false,equips:[{id:9400400,lv:null,limitBreak:null},null,null],bondEffect:'any'});
 assert.equal(result.teams[0].servants[0]?.grandSvt,true);
 assert.equal(result.teams[0].servants[2],null);
 assert.equal(result.nextOffset,12);
});

test('mismatched quest sort or cursor cannot be appended to a newly filtered list',()=>{
 for(const change of [{questId:'archer_7'},{sort:'created'},{offset:12}])assert.throws(()=>readCommonTeamPage({...page(),...change},request));
});

test('repeated teams across changing rankings are shown once without changing the original order',()=>{
 const first=readCommonTeamPage({...page(),teams:[team(4),team(2)]},request);
 const second=readCommonTeamPage({...page(),offset:12,nextOffset:null,hasMore:false,teams:[team(2),team(3)]},{...request,offset:12});
 assert.deepEqual(appendCommonTeams(first.teams,second.teams).map(item=>item.id),[4,2,3]);
 assert.equal(second.nextOffset,null);
});

test('a page with sparse or repeated data still uses server cursor, not visible card count',()=>{
 const result=readCommonTeamPage({...page(),teams:[team(1),team(1)]},request);
 assert.equal(appendCommonTeams([],result.teams).length,1);
 assert.equal(result.nextOffset,12);
 const empty=readCommonTeamPage({...page(),teams:[]},request);
 assert.equal(empty.hasMore,true);assert.equal(empty.nextOffset,12);
});

test('nonadvancing or oversized pages fail visibly instead of making endless load-more requests',()=>{
 for(const change of [{nextOffset:0},{nextOffset:null},{nextOffset:999},{pageSize:13},{total:-1},{total:12},{teams:Array.from({length:13},(_,i)=>team(i+1))}])assert.throws(()=>readCommonTeamPage({...page(),...change},request));
 assert.throws(()=>readCommonTeamPage({...page(),hasMore:false,nextOffset:12},request));
 assert.throws(()=>readCommonTeamPage({...page(),teams:[{...team(),id:0}]},request));
});

test('missing display text gets a stable fallback and unrecorded dates or turns stay unknown',()=>{
 const result=readCommonTeamPage({...page(),teams:[{...team(),name:'',author:'',createdAt:null,turnCount:null}]},request).teams[0];
 assert.equal(result.name,'Chaldea 队伍 #1');assert.equal(result.author,'未署名');
 assert.equal(result.turnCount,null);assert.equal(commonTeamDate(result.createdAt),'日期未记录');
});

test('query builder sends only the local selector, sort, and cursor',()=>{
 assert.equal(commonTeamQuery({...request,offset:12}),'/common-teams?quest=saber_7&sort=upvotes&offset=12');
});

test('grand preview preserves three separate CE slots and NP50 metadata without rewriting source',()=>{
 const member={...team().servants[0],equips:[{id:9400480,lv:100,limitBreak:true},{id:9300310,lv:80,limitBreak:true},{id:9407480,lv:100,limitBreak:false}],bondEffect:'np50'};
 const value={...page(),teams:[{...team(),servants:[member,null,null,null,null,null]}]},before=structuredClone(value);
 const result=readCommonTeamPage(value,request).teams[0].servants[0]!;
 assert.deepEqual(result.equips,member.equips);assert.equal(result.bondEffect,'np50');assert.deepEqual(value,before);
});

test('bond effect distinguishes explicit original from unrestricted and supports effect-only grand slots',()=>{
 for(const effect of ['original','any','np50'] as const){
  const member={svtId:0,support:true,equipId:0,grandSvt:true,equips:[null,null,{id:9407480,lv:100,limitBreak:false}],bondEffect:effect};
  const result=readCommonTeamPage({...page(),teams:[{...team(),servants:[member,null,null,null,null,null]}]},request).teams[0].servants[0]!;
  assert.equal(result.equips[0],null);assert.equal(result.equips[2]?.id,9407480);assert.equal(result.bondEffect,effect);
 }
});

test('ordinary preview never borrows grand-only CEs or a bond effect',()=>{
 const member={...team().servants[1],equips:[{id:9400400,lv:100,limitBreak:false},{id:9300310,lv:80,limitBreak:true},{id:9407480,lv:100,limitBreak:true}],bondEffect:'np50'};
 const result=readCommonTeamPage({...page(),teams:[{...team(),servants:[member,null,null,null,null,null]}]},request).teams[0].servants[0]!;
 assert.equal(result.grandSvt,false);assert.deepEqual(result.equips,[member.equips[0],null,null]);assert.equal(result.bondEffect,'any');
});

test('old primary-only services remain readable and malformed CE metadata fails visibly',()=>{
 const old=readCommonTeamPage(page(),request).teams[0].servants[0]!;
 assert.equal(old.equips[0]?.id,9400340);assert.equal(old.equips[1],null);assert.equal(old.bondEffect,'any');
 for(const change of [{equips:[]},{equips:[null,null]},{equips:[{id:true,lv:null,limitBreak:null},null,null]},{equips:[{id:1,lv:0,limitBreak:null},null,null]},{equips:[{id:1,lv:1,limitBreak:'false'},null,null]},{bondEffect:'invalid'}]){
  assert.throws(()=>readCommonTeamPage({...page(),teams:[{...team(),servants:[{...team().servants[0],...change},null,null,null,null,null]}]},request));
 }
});

test('missing bond CE artwork resolves only from its unique owner without adding a requirement',()=>{
 const member=readCommonTeamPage(page(),request).teams[0].servants[0]!,before=structuredClone(member),owners=new Map([[200900,9300310]]);
 assert.equal(commonTeamEquipId(member,1,owners,200900),9300310);
 assert.equal(commonTeamEquipId(member,2,owners,200900),0);
 assert.equal(commonTeamEquipId({...member,grandSvt:false},1,owners,200900),0);
 assert.equal(commonTeamEquipId(member,1,owners,100100),0);
 member.equips[1]={id:9307360,lv:1,limitBreak:null};assert.equal(commonTeamEquipId(member,1,owners,200900),9307360);
 member.equips[1]=null;assert.deepEqual(member,before);
});
