import test from 'node:test';
import assert from 'node:assert/strict';
import type {Catalog,Json} from './model';
import {
 DEFAULT_PARTY_COST_LIMIT,PARTY_COST_STORAGE_KEY,loadPartyCostLimit,
 partyCost,partyCostLimitWarning,savePartyCostLimit,validPartyCostLimit,
} from './partyCost';

const catalog:Catalog={
 servants:[{id:100,name:'甲',cost:16,rarity:5,aliases:['101',102]},{id:200,name:'乙',cost:12,rarity:4},{id:300,name:'零费从者',cost:0,rarity:0}],
 equips:[{id:10,name:'金礼装',cost:12,rarity:5},{id:20,name:'银礼装',cost:9,rarity:4},{id:30,name:'零费礼装',cost:0,rarity:0}],
 mysticCodes:[],
};
function team(...roster:(Json|null)[]):Json {
 return {team:{onFieldSvts:roster.slice(0,3),backupSvts:roster.slice(3,6)},actions:[]};
}
const storage=(raw:string|null=null)=>{
 const entries=new Map<string,string>();if(raw!==null)entries.set(PARTY_COST_STORAGE_KEY,raw);
 return {getItem:(key:string)=>entries.get(key)??null,setItem:(key:string,value:string)=>{entries.set(key,value);},entries};
};

test('support alternatives cost zero while unsupported owned alternatives cannot imply an exact cost',()=>{
 const support=team({svtId:100,supportType:'friend',equip1:{id:10,candidates:[{id:20},{id:30}]}});
 assert.deepEqual(partyCost(support,catalog),{knownTotal:0,complete:true,issues:[]});
 const own=team({svtId:100,supportType:'none',equip1:{id:10,candidates:[{id:20}]}}),before=structuredClone(own);
 const result=partyCost(own,catalog);
 assert.equal(result.knownTotal,16);assert.equal(result.complete,false);assert.match(result.issues[0].message,/多候选/);assert.deepEqual(own,before);
});

test('sums all six normal slots including main essences and accepts known zero costs',()=>{
 const data=team({svtId:100,equip1:{id:10}},{svtId:200,equip1:{id:20}},{svtId:300,equip1:{id:30}},
  {svtId:100},{svtId:200},{svtId:300});
 assert.deepEqual(partyCost(data,catalog),{knownTotal:77,complete:true,issues:[]});
 assert.deepEqual(partyCost({team:{}},catalog),{knownTotal:0,complete:true,issues:[]});
});

test('friend npc and fixed support slots are free including unknown servants and grand essences',()=>{
 for(const supportType of ['friend','npc','fixed']){
  const data=team({svtId:100,equip1:{id:10}},{svtId:999,supportType,grandSvt:true,equip1:{id:'bad'},equip2:{id:10},equip3:{id:20}});
  assert.deepEqual(partyCost(data,catalog),{knownTotal:28,complete:true,issues:[]});
 }
 const result=partyCost(team({svtId:100,supportType:'future-support',equip1:{id:10}}),catalog);
 assert.equal(result.knownTotal,0);assert.equal(result.complete,false);assert.equal(result.issues[0].slot,0);
 assert.match(result.issues[0].message,/助战类型未知/);
});

test('numeric and numeric-string servant aliases use canonical costs without rarity guesses',()=>{
 assert.deepEqual(partyCost(team({svtId:101},{svtId:102}),catalog),{knownTotal:32,complete:true,issues:[]});
 const malformed={...catalog,servants:[{id:100,name:'别名异常',cost:16,aliases:['',false,'1e2'] as any}]};
 assert.equal(partyCost(team({svtId:100}),malformed).complete,true);
 assert.equal(partyCost(team({svtId:1}),malformed).complete,false);
 const colliding={...catalog,servants:[{id:100,name:'别名冲突',cost:16,aliases:[200]},{id:200,name:'主 ID',cost:12}]};
 assert.equal(partyCost(team({svtId:200}),colliding).knownTotal,12);
});

test('legacy main essence fallback matches display rules and explicit zero prevents fallback',()=>{
 for(const equip1 of [undefined,null,{}, {id:null}]){
  const result=partyCost(team({svtId:100,equip1,ceId:10,ceLv:0,ceLimitBreak:false}),catalog);
  assert.deepEqual(result,{knownTotal:28,complete:true,issues:[]});
 }
 assert.deepEqual(partyCost(team({svtId:100,equip1:{id:20},ceId:10}),catalog),{knownTotal:25,complete:true,issues:[]});
 assert.deepEqual(partyCost(team({svtId:100,equip1:{id:0},ceId:10}),catalog),{knownTotal:16,complete:true,issues:[]});
 assert.deepEqual(partyCost(team({svtId:100,equip1:{id:0},ceId:'bad'}),catalog),{knownTotal:16,complete:true,issues:[]});
 assert.deepEqual(partyCost(team({svtId:100,equip1:{id:10},ceId:'bad'}),catalog),{knownTotal:28,complete:true,issues:[]});
});

test('unconfigured slots do not add CE costs and identify selected or malformed main essences',()=>{
 for(const svtId of [undefined,null,0]){
  assert.deepEqual(partyCost(team({svtId}),catalog),{knownTotal:0,complete:true,issues:[]});
  for(const essence of [{equip1:{id:10}},{ceId:10},{equip1:{id:-2}}]){
   const result=partyCost(team(null,{svtId,...essence}),catalog);
   assert.equal(result.knownTotal,0);assert.equal(result.complete,false);assert.equal(result.issues[0].slot,1);
   assert.match(result.issues[0].message,/尚未指定从者/);
  }
  assert.equal(partyCost(team({svtId,grandSvt:true,equip2:{id:10}}),catalog).complete,false);
  assert.equal(partyCost(team({svtId,grandSvt:true,equip3:{id:20}}),catalog).complete,false);
 }
});

test('unknown or missing costs leave a useful partial total and never infer from rarity',()=>{
 const result=partyCost(team({svtId:999,equip1:{id:10}},{svtId:100,equip1:{id:999}},{svtId:200}),{
  ...catalog,servants:catalog.servants.map(item=>item.id===200?{...item,cost:null}:item),
 });
 assert.equal(result.knownTotal,28);assert.equal(result.complete,false);
 assert.deepEqual(result.issues.map(item=>item.slot),[0,1,2]);
 assert.match(result.issues[0].message,/图鉴缺少从者/);assert.match(result.issues[1].message,/图鉴缺少礼装/);
 for(const cost of [undefined,null,-1,0.5,101,NaN,Infinity,'16',false]){
  const broken={...catalog,servants:[{id:100,name:'甲',rarity:5,cost} as any],equips:[{id:10,name:'礼',rarity:5,cost} as any]};
  const value=partyCost(team({svtId:100,equip1:{id:10}}),broken);
  assert.equal(value.knownTotal,0);assert.equal(value.complete,false);assert.equal(value.issues.length,2);
 }
});

test('grand own slots remain unpriced and inactive extra essences never affect normal slots',()=>{
 const grand=partyCost(team({svtId:100,grandSvt:true,equip1:{id:10},equip2:{id:20},equip3:{id:30}},{svtId:200}),catalog);
 assert.equal(grand.knownTotal,12);assert.equal(grand.complete,false);assert.equal(grand.issues[0].slot,0);
 assert.match(grand.issues[0].message,/冠位.*游戏内实际 COST/);
 for(const grandSvt of [undefined,null,false]){
  assert.deepEqual(partyCost(team({svtId:100,grandSvt,equip1:{id:10},equip2:'bad',equip3:{id:999}}),catalog),{knownTotal:28,complete:true,issues:[]});
 }
 for(const grandSvt of ['true',1,0,'false',{},[]]){
  const result=partyCost(team({svtId:100,grandSvt,equip1:{id:10}},{svtId:200}),catalog);
  assert.equal(result.knownTotal,12);assert.equal(result.complete,false);assert.match(result.issues[0].message,/冠位从者标记无效/);
 }
});

test('malformed stored IDs cannot silently become empty requirements',()=>{
 for(const id of [-1,0.5,NaN,Infinity,Number.MAX_SAFE_INTEGER+1,'10',true,false,{},[]]){
  const servant=partyCost(team({svtId:id}),catalog);
  assert.equal(servant.knownTotal,0);assert.equal(servant.complete,false);assert.match(servant.issues[0].message,/从者 ID 无效/);
  const current=partyCost(team({svtId:100,equip1:{id},ceId:10}),catalog);
  assert.equal(current.knownTotal,16);assert.equal(current.complete,false);assert.match(current.issues[0].message,/主礼装 ID 无效/);
  const legacy=partyCost(team({svtId:100,ceId:id}),catalog);
  assert.equal(legacy.knownTotal,16);assert.equal(legacy.complete,false);assert.match(legacy.issues[0].message,/旧版礼装 ID 无效/);
 }
 for(const equip1 of ['bad',[],false,12])assert.equal(partyCost(team({svtId:100,equip1,ceId:10}),catalog).complete,false);
 for(const member of [12,[],false,'bad'])assert.equal(partyCost(team(member as any),catalog).complete,false);
});

test('cost inspection preserves all imported fields and catalog data',()=>{
 const data=team({svtId:101,ceId:10,ceLv:null,equip1:{id:null,custom:{preserve:[0,null,false]}}},
  {svtId:200,grandSvt:true,equip1:{id:20},equip2:{id:10},future:{a:1}});
 data.future={keep:[null,0]};const before=structuredClone(data),metadata=structuredClone(catalog);
 partyCost(data,catalog);assert.deepEqual(data,before);assert.deepEqual(catalog,metadata);
});

test('cost above the current limit is an advisory warning with the exact excess',()=>{
 const data=team(...Array.from({length:5},()=>({svtId:100,equip1:{id:10}}))),before=structuredClone(data);
 const summary=partyCost(data,catalog),original=structuredClone(summary);
 const warning=partyCostLimitWarning(summary,117);
 assert.equal(summary.knownTotal,140);
 assert.deepEqual(warning,{severity:'warning',code:'party_cost_over_limit',path:'team',message:'编队 COST 140 超过当前上限 117（超出 23）。请核对编队或调整上限；此提醒不影响保存。'});
 assert.deepEqual(summary,original);assert.deepEqual(data,before);
});

test('cost warning follows limit changes and vanishes at or below the limit',()=>{
 const summary=partyCost(team({svtId:100,equip1:{id:10}}),catalog);
 assert.equal(summary.knownTotal,28);
 assert.equal(partyCostLimitWarning(summary,28),null);assert.equal(partyCostLimitWarning(summary,117),null);
 assert.match(partyCostLimitWarning(summary,20)!.message,/上限 20（超出 8）/);
 assert.match(partyCostLimitWarning(summary,27)!.message,/上限 27（超出 1）/);
 assert.equal(partyCostLimitWarning(summary,29),null);
 for(const invalid of [0,301,1.5,NaN,Infinity])assert.equal(partyCostLimitWarning(summary,invalid),null);
});

test('unknown or grand costs report only a proven over-limit subtotal',()=>{
 const summary=partyCost(team({svtId:100,equip1:{id:999}},{svtId:200,grandSvt:true}),catalog),before=structuredClone(summary);
 assert.equal(summary.complete,false);assert.equal(summary.knownTotal,16);
 assert.equal(partyCostLimitWarning(summary,16),null);assert.equal(partyCostLimitWarning(summary,117),null);
 const warning=partyCostLimitWarning(summary,10)!;
 assert.equal(warning.severity,'warning');assert.match(warning.message,/已知 COST 小计 16/);
 assert.match(warning.message,/至少超出 6/);assert.match(warning.message,/部分 COST 待确认/);assert.match(warning.message,/不影响保存/);
 assert.deepEqual(summary,before);
});

test('support slots and cleared requirements cannot create stale over-limit warnings',()=>{
 const data=team({svtId:100,supportType:'none',equip1:{id:10}});
 assert.ok(partyCostLimitWarning(partyCost(data,catalog),20));
 for(const supportType of ['friend','npc','fixed']){
  data.team.onFieldSvts[0].supportType=supportType;
  assert.equal(partyCostLimitWarning(partyCost(data,catalog),20),null);
 }
 data.team.onFieldSvts[0]=null;assert.equal(partyCostLimitWarning(partyCost(data,catalog),20),null);
});

test('cost limit defaults to 117 and valid boundaries round-trip through an independent versioned key',()=>{
 assert.equal(DEFAULT_PARTY_COST_LIMIT,117);assert.equal(loadPartyCostLimit(null),117);
 const store=storage();assert.equal(loadPartyCostLimit(store),117);
 store.entries.set('maafgo.battleEditor.servantSelector.v1','untouched');
 for(const limit of [1,117,300]){
  assert.equal(validPartyCostLimit(limit),true);assert.equal(savePartyCostLimit(limit,store),true);
  assert.equal(loadPartyCostLimit(store),limit);
  assert.deepEqual(JSON.parse(store.entries.get(PARTY_COST_STORAGE_KEY)!),{version:1,limit});
 }
 assert.equal(store.entries.get('maafgo.battleEditor.servantSelector.v1'),'untouched');
});

test('invalid limits and storage shapes fall back without overwriting the last valid value',()=>{
 const store=storage(JSON.stringify({version:1,limit:120}));
 for(const limit of [0,-1,301,1.5,NaN,Infinity,'117',null,undefined,true,[],{}]){
  assert.equal(validPartyCostLimit(limit),false);assert.equal(savePartyCostLimit(limit as number,store),false);
  assert.equal(loadPartyCostLimit(store),120);
  assert.equal(loadPartyCostLimit(storage(JSON.stringify({version:1,limit}))),117);
 }
 for(const raw of ['{','null','[]','117','"117"','true','{}','{"version":2,"limit":120}','{"version":"1","limit":120}'])assert.equal(loadPartyCostLimit(storage(raw)),117);
});

test('unavailable storage read write quota and browser getter errors are safe',()=>{
 assert.equal(loadPartyCostLimit(),117);assert.equal(savePartyCostLimit(120),false);
 assert.equal(savePartyCostLimit(120,null),false);
 assert.equal(loadPartyCostLimit({getItem:()=>{throw new Error('blocked');}}),117);
 assert.equal(savePartyCostLimit(120,{setItem:()=>{throw new Error('full');}}),false);
 const original=Object.getOwnPropertyDescriptor(globalThis,'window');
 try {
  Object.defineProperty(globalThis,'window',{value:Object.defineProperty({},'localStorage',{get:()=>{throw new Error('blocked');}}),configurable:true});
  assert.equal(loadPartyCostLimit(),117);assert.equal(savePartyCostLimit(120),false);
 }finally {
  if(original)Object.defineProperty(globalThis,'window',original);else Reflect.deleteProperty(globalThis,'window');
 }
});
