import test from 'node:test';
import assert from 'node:assert/strict';
import {appendSkillEntry,clone,remapSlots,turnsOf,writeTurns} from './model';
import type {Catalog,Entry,Json} from './model';
import {reconcileSpecialSkillChoices,setEntrySpecialSkillChoice,specialSkillChoiceDiagnostics,specialSkillChoiceFor,specialSkillChoiceLabel} from './specialSkillChoices';
import type {SpecialSkillChoiceGroup} from './specialSkillChoices';

// Deliberately small fixtures: production group membership only comes from catalog.
const td:SpecialSkillChoiceGroup={id:'color',servantIds:[11],skillNumbers:[2],field:'tdTypeChanges',options:[{value:1,label:'Arts'},{value:2,label:'Buster'},{value:3,label:'Quick'}],defaultValue:1};
const act:SpecialSkillChoiceGroup={id:'consume',servantIds:[22],skillNumbers:[1,2,3],field:'skillActSelectSelections',options:[{value:0,label:'不消耗'},{value:1,label:'消耗'}],defaultValue:1};
const limited:SpecialSkillChoiceGroup={id:'enhanced',servantIds:[33],skillNumbers:[3],field:'skillActSelectSelections',options:[{value:0,label:'常态'},{value:1,label:'形态'}],defaultValue:0,skillIds:[333]};
const catalog:Catalog={servants:[{id:11,name:'颜色',aliases:[111],skills:[{id:110,num:1,name:'一'},{id:112,num:2,name:'二'}]},{id:22,name:'星星'},{id:33,name:'强化',skills:[{id:331,num:1,name:'一'},{id:332,num:2,name:'二'},{id:333,num:3,name:'三'}]}],equips:[],mysticCodes:[],specialSkillChoices:{schemaVersion:1,groups:[td,act,limited]}};
const skill=(svt:number,skill:number,future?:unknown)=>({type:'skill',svt,skill,...(future===undefined?{}:{future})});
const attack=()=>({type:'attack',attacks:[]});
const fixture=(actions:any[],delegate?:Json):Json=>({future:{keep:[null,false]},team:{onFieldSvts:[{svtId:11},{svtId:22},{svtId:33}],backupSvts:[{svtId:22},null,null],mysticCode:{mysticCodeId:210}},actions,...(delegate?{delegate}:{} )});
const entries=(data:Json)=>clone(turnsOf(data,catalog));
const fresh=(svt:number,index:number):Entry=>({raw:skill(svt,index),index:999});

test('two independent queues follow reordered actions and preserve raw strings, tails and unknown JSON',()=>{
 const data=fixture([skill(0,1,{keep:true}),skill(1,0),attack(),skill(0,1),skill(1,2),attack()],{tdTypeChanges:['quick','Arts',{future:'td-tail'}],skillActSelectSelections:[0,1,{future:'act-tail'}],future:{keep:null}});
 const t=entries(data),[first,second]=t;
 assert.equal(specialSkillChoiceLabel(first.entries[0],catalog),'Quick');
 writeTurns(data,[second,first],catalog);
 assert.deepEqual(data.delegate.tdTypeChanges,['Arts','quick',{future:'td-tail'}]);
 assert.deepEqual(data.delegate.skillActSelectSelections,[1,0,{future:'act-tail'}]);
 assert.deepEqual(data.actions[3].future,{keep:true});assert.deepEqual(data.future,{keep:[null,false]});assert.deepEqual(data.delegate.future,{keep:null});
 assert.equal(JSON.stringify(data).includes('specialChoice'),false);
});

test('deletion removes its own value, copying repeats it, and each field remains independent',()=>{
 const data=fixture([skill(0,1),skill(1,0),skill(0,1),attack()],{tdTypeChanges:[2,3,'tail'],skillActSelectSelections:[0,'tail']});
 const t=entries(data);t[0].entries.splice(0,1);const duplicate=clone(t[0].entries[1]);t[0].entries.splice(2,0,duplicate);writeTurns(data,t,catalog);
 assert.deepEqual(data.delegate.tdTypeChanges,[3,3,'tail']);assert.deepEqual(data.delegate.skillActSelectSelections,[0,'tail']);
 const copy=entries(data);copy.push(clone(copy[0]));writeTurns(data,copy,catalog);
 assert.deepEqual(data.delegate.tdTypeChanges,[3,3,3,3,'tail']);assert.deepEqual(data.delegate.skillActSelectSelections,[0,0,'tail']);
});

test('an explicit option activates one global queue with per-group defaults including value zero',()=>{
 const data=fixture([skill(1,0),skill(0,1),skill(2,2),attack()]);
 const before=clone(data);
 const t=entries(data);setEntrySpecialSkillChoice(t[0].entries[0],act,0);
 const diagnostics=writeTurns(data,t,catalog);
 assert.deepEqual(data.delegate.skillActSelectSelections,[0,0]);assert.equal(Object.hasOwn(data.delegate,'tdTypeChanges'),false);
 assert.ok(diagnostics.some(d=>d.code==='special_defaults_materialized'));
 assert.ok(reconcileSpecialSkillChoices(before,data,catalog).some(d=>d.code==='special_defaults_materialized'));
 const newStep=fresh(1,1);setEntrySpecialSkillChoice(newStep,act,1);appendSkillEntry(data,0,newStep,catalog);
 assert.deepEqual(data.delegate.skillActSelectSelections,[0,0,1]);assert.equal(data.actions.at(-1).type,'attack');
});

test('read and unrelated edits preserve missing, null and empty queues without activating native defaults',()=>{
 for(const queue of [undefined,null,[]]){
  const data=fixture([skill(0,1),skill(1,1),attack()],queue===undefined?{future:'keep'}:{tdTypeChanges:queue,skillActSelectSelections:clone(queue),future:'keep'}),before=clone(data);
  const t=entries(data);assert.deepEqual(data,before);writeTurns(data,t,catalog);assert.deepEqual(data,before);
  t[0].entries.at(-1)!.raw.options={enemyTarget:2};writeTurns(data,t,catalog);
  assert.deepEqual(data.delegate,before.delegate);assert.equal(data.actions.at(-1).options.enemyTarget,2);
 }
});

test('a partial imported queue stays partial until an explicit user choice fills missing uses',()=>{
 const data=fixture([skill(0,1),skill(0,1),attack()],{tdTypeChanges:['Buster'],future:true});
 const t=entries(data);t[0].entries.at(-1)!.raw.options={enemyTarget:0};writeTurns(data,t,catalog);
 assert.deepEqual(data.delegate.tdTypeChanges,['Buster']);assert.ok(specialSkillChoiceDiagnostics(data,catalog).some(d=>d.code==='special_queue_exhausted'));
 assert.equal(t[0].entries[1].specialChoice?.missing,true);assert.equal(specialSkillChoiceLabel(t[0].entries[1],catalog),'缺少技能选项');
 const moved=entries(data);[moved[0].entries[0],moved[0].entries[1]]=[moved[0].entries[1],moved[0].entries[0]];const before=clone(data);
 assert.throws(()=>writeTurns(data,moved,catalog),/不完整/);assert.deepEqual(data,before);
 setEntrySpecialSkillChoice(moved[0].entries[0],td,3);writeTurns(data,moved,catalog);assert.deepEqual(data.delegate.tdTypeChanges,[3,'Buster']);
});

test('an imported queue gap cannot be silently repaired by copying or adding an unconfigured skill',()=>{
 const data=fixture([skill(1,0),skill(2,2),attack()],{skillActSelectSelections:[0]});
 const before=clone(data);assert.throws(()=>appendSkillEntry(data,0,fresh(1,2),catalog),/不完整/);assert.deepEqual(data,before);
 const explicit=fresh(1,2);setEntrySpecialSkillChoice(explicit,act,1);appendSkillEntry(data,0,explicit,catalog);assert.deepEqual(data.delegate.skillActSelectSelections,[0,0,1]);
});

test('imported invalid values and unconsumed tail are lossless even when another choice is explicitly set',()=>{
 const data=fixture([skill(1,0),skill(1,1),attack()],{skillActSelectSelections:[null,7,{unknown:[0,false]}]});
 const t=entries(data);setEntrySpecialSkillChoice(t[0].entries[1],act,0);writeTurns(data,t,catalog);
 assert.deepEqual(data.delegate.skillActSelectSelections,[null,0,{unknown:[0,false]}]);
 assert.ok(specialSkillChoiceDiagnostics(data,catalog).some(d=>d.code==='special_option_unsupported'));
});

test('swap replay binds the post-swap actor, and moving the swap cannot transfer a choice to a different menu',()=>{
 const swap={raw:{type:'skill',svt:null,skill:2},replacement:[0,0],paired:true,index:0};
 const data=fixture([swap.raw,skill(0,0),attack()],{replaceMemberIndexes:[[0,0]],skillActSelectSelections:[0]});
 const t=entries(data);assert.equal(t[0].entries[1].specialChoice?.groupId,'consume');
 writeTurns(data,t,catalog);assert.deepEqual(data.delegate.skillActSelectSelections,[0]);
 const unsafe=fixture([skill(0,1),swap.raw,skill(0,1),attack()],{replaceMemberIndexes:[[0,0]],tdTypeChanges:[2],skillActSelectSelections:[0]});
 const moved=entries(unsafe),before=clone(unsafe);[moved[0].entries[0],moved[0].entries[1]]=[moved[0].entries[1],moved[0].entries[0]];
 assert.throws(()=>writeTurns(unsafe,moved,catalog),/类型已改变/);assert.deepEqual(unsafe,before);
});

test('unknown swap/actor or malformed active queue allows a no-op but blocks unsafe remapping atomically',()=>{
 for(const data of [fixture([{type:'skill',svt:null,skill:2},skill(0,1),attack()],{tdTypeChanges:[2]}),fixture([skill(0,1),attack()],{tdTypeChanges:{future:true}})]){
  const before=clone(data);writeTurns(data,entries(data),catalog);assert.deepEqual(data,before);
  const changed=entries(data);changed[0].entries.unshift(fresh(0,1));assert.throws(()=>writeTurns(data,changed,catalog),/无法可靠配对/);assert.deepEqual(data,before);
 }
});

test('confirmed variants use explicit IDs while zero/null/missing use catalog defaults; unsupported variants stay unresolved',()=>{
 for(const value of [undefined,null,0,333]){
  const member:Json={svtId:33,...(value===undefined?{}:{skillIds:[null,null,value]})};
  assert.equal(specialSkillChoiceFor(member,catalog.servants[2],2,catalog)?.id,'enhanced');
 }
 assert.equal(specialSkillChoiceFor({svtId:33,skillIds:[0,0,330]},catalog.servants[2],2,catalog),undefined);
 for(const value of [-1,'333',true])assert.equal(specialSkillChoiceFor({svtId:33,skillIds:[0,0,value]},catalog.servants[2],2,catalog),undefined);
 assert.equal(specialSkillChoiceFor({svtId:33},undefined,2,catalog),undefined);
 assert.equal(specialSkillChoiceFor({svtId:111},catalog.servants[0],1,catalog)?.id,'color');
 const data=fixture([skill(2,2),skill(1,0),attack()],{skillActSelectSelections:[0,1]});data.team.onFieldSvts[2].skillIds=[null,null,330];
 assert.ok(specialSkillChoiceDiagnostics(data,catalog).some(d=>d.code==='special_choice_variant_unresolved'&&d.severity==='error'));
 assert.equal(turnsOf(data,catalog)[0].entries[1].specialChoice,undefined);
 const t=entries(data);t[0].entries.pop();t[0].entries.push({raw:attack(),index:90});writeTurns(data,t,catalog);assert.deepEqual(data.delegate.skillActSelectSelections,[0,1]);
});

test('team edits reconcile aliases and role remaps without moving values; different menus require reselection',()=>{
 const before=fixture([skill(0,1),skill(1,0),attack()],{tdTypeChanges:[3],skillActSelectSelections:[0],future:'keep'});
 const alias=clone(before);alias.team.onFieldSvts[0].svtId=111;reconcileSpecialSkillChoices(before,alias,catalog);assert.deepEqual(alias.delegate,before.delegate);
 const remapped=clone(before);remapSlots(remapped,0,1);reconcileSpecialSkillChoices(before,remapped,catalog);assert.deepEqual(remapped.delegate,before.delegate);
 const changed=clone(before);changed.team.onFieldSvts[0]={svtId:22};assert.throws(()=>reconcileSpecialSkillChoices(before,changed,catalog),/类型已改变/);
 const combined=clone(before);writeTurns(combined,entries(combined),catalog);combined.team.onFieldSvts[0]={svtId:22};assert.throws(()=>reconcileSpecialSkillChoices(before,combined,catalog),/类型已改变/);
});

test('metadata not yet loaded preserves opaque queues but blocks related skill/roster edits',()=>{
 const empty:Catalog={servants:[],equips:[],mysticCodes:[]},data=fixture([skill(0,1),attack()],{tdTypeChanges:['quick',{future:true}]});
 const t=clone(turnsOf(data,empty));t[0].entries.at(-1)!.raw.options={enemyTarget:2};writeTurns(data,t,empty);
 assert.deepEqual(data.delegate.tdTypeChanges,['quick',{future:true}]);assert.ok(specialSkillChoiceDiagnostics(data,empty).some(d=>d.code==='special_metadata_unavailable'));
 t[0].entries.unshift(fresh(1,0));const before=clone(data);assert.throws(()=>writeTurns(data,t,empty),/资料尚未加载/);assert.deepEqual(data,before);
 const changed=clone(data);changed.team.onFieldSvts[0].svtId=22;assert.throws(()=>reconcileSpecialSkillChoices(data,changed,empty),/资料尚未加载/);
});

test('implicit defaults and exhausted queues are visibly distinct and legacy TD case is not normalized',()=>{
 const data=fixture([skill(0,1),attack()]),entry=turnsOf(data,catalog)[0].entries[0];
 assert.equal(specialSkillChoiceLabel(entry,catalog),'Arts（默认）');
 data.delegate={tdTypeChanges:['ARTS']};assert.match(specialSkillChoiceLabel(turnsOf(data,catalog)[0].entries[0],catalog),/未支持/);
 assert.ok(specialSkillChoiceDiagnostics(data,catalog).some(d=>d.code==='special_option_unsupported'));
 const missing=fixture([skill(1,0),skill(1,1),attack()],{skillActSelectSelections:[0]}),t=entries(missing);
 assert.equal(t[0].entries[1].specialChoice?.missing,true);setEntrySpecialSkillChoice(t[0].entries[1],act,1);
 assert.equal(t[0].entries[1].specialChoice?.missing,undefined);writeTurns(missing,t,catalog);assert.deepEqual(missing.delegate.skillActSelectSelections,[0,1]);
});
