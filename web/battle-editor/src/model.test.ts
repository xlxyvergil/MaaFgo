import test from 'node:test';
import assert from 'node:assert/strict';
import {assertEditable,clone,newBattle,newServantDefaults,newTeamSupportSlot,restoreNewTeamFirstSupport,newTeamMemberDefaults,fillMissingTaskSkillDefaults,members,setMember,turnsOf,writeTurns,rosterAt,makeCard,remapSlots,servantFor,skillAt,replaceServant,swapMembers,filterCatalog,appendSkillAt,appendSkillEntry,appendNPEntry} from './model';
import type {Json,Catalog} from './model';
import {hasServantIdentity,isUnspecifiedSupport,memberLabel,clearSupportServantRequirement,setMemberEquipRequirement,actionTitle} from './model';
import {startPartyDrag,updatePartyDrag,finishPartyDrag} from './partyDrag';
const catalog:Catalog={servants:[{id:1,name:'甲',cards:[2,1,1,3,2]},{id:4,name:'丁',cards:[]}],equips:[],mysticCodes:[]};
test('CE-only friend support is a real requirement with unknown identity, never an invented servant',()=>{
 for(const svtId of [undefined,null,0]){
  const member={supportType:'friend',svtId,equip1:{id:10}};
  assert.equal(isUnspecifiedSupport(member),true);assert.equal(hasServantIdentity(member),false);
  assert.equal(memberLabel(member,catalog),'助战（仅要求礼装）');
 }
 for(const svtId of [false,true,-1,0.5,'1',NaN,Infinity]){
  assert.equal(hasServantIdentity({svtId}),false);assert.equal(isUnspecifiedSupport({svtId,supportType:'friend'}),false);
 }
 assert.equal(hasServantIdentity({svtId:1}),true);assert.equal(memberLabel({svtId:1},catalog),'甲');
 assert.equal(memberLabel({svtId:999},catalog),'从者 #999');assert.equal(memberLabel(null,catalog),'未配置从者');
 for(const supportType of ['none','npc','fixed'])assert.equal(isUnspecifiedSupport({supportType}),false);
});
test('only the default new support can receive its first CE before a servant is chosen',()=>{
 const data=newBattle();setMemberEquipRequirement(data,0,'equip1',10,true);
 assert.deepEqual(members(data)[0],{supportType:'friend',equip1:{id:10}});
 assert.equal(newTeamSupportSlot(data,true),null);assert.equal(members(data)[1],null);
 for(const slot of [1,5]){
  const before=clone(data);assert.throws(()=>setMemberEquipRequirement(data,slot,'equip1',10,true));assert.deepEqual(data,before);
 }
 const old=newBattle();assert.throws(()=>setMemberEquipRequirement(old,0,'equip1',10,false));assert.deepEqual(old,newBattle());
 setMember(data,3,{supportType:'friend',equip1:{id:10,lv:80,future:null},future:{keep:1}});
 setMemberEquipRequirement(data,3,'equip1',20);
 assert.deepEqual(members(data)[3],{supportType:'friend',equip1:{id:20,lv:80,future:null},future:{keep:1}});
});
test('clearing support identity retains all CE, grand and unknown attributes and later selection restores skill defaults',()=>{
 const member:Json={supportType:'friend',svtId:1,lv:90,tdLv:5,skillLvs:[8,9,10],appendLvs:[1,2,3],skillIds:[11,12,13],tdId:14,limitCount:4,transformVal:1,
  grandSvt:true,classBoardData:{grandBondEquipSkillChange:true},equip1:{id:10,lv:100},equip2:{id:20},equip3:{id:30},ceId:40,future:{keep:null}};
 clearSupportServantRequirement(member);
 const expected={supportType:'friend',grandSvt:true,classBoardData:{grandBondEquipSkillChange:true},equip1:{id:10,lv:100},equip2:{id:20},equip3:{id:30},ceId:40,future:{keep:null}};
 assert.deepEqual(member,expected);
 const replaced=replaceServant(member,1,catalog);
 assert.deepEqual(replaced,{...newServantDefaults(),...expected,svtId:1});assert.deepEqual(member,expected);
 (replaced as Json).skillLvs[0]=2;assert.deepEqual(newServantDefaults().skillLvs,[10,10,10]);
 for(const supportType of ['none','npc','fixed']){
  const other={supportType,svtId:1,equip1:{id:10}},before=clone(other);assert.throws(()=>clearSupportServantRequirement(other));assert.deepEqual(other,before);
 }
});
test('unknown support cannot produce an active skill, NP or targeted face card and existing actions stay intact',()=>{
 const data:Json={team:{onFieldSvts:[{supportType:'friend',equip1:{id:10}},{svtId:1},null],backupSvts:[{svtId:4},null,null],mysticCode:{mysticCodeId:210}},actions:[{type:'attack',attacks:[]}]};
 const before=clone(data);
 assert.throws(()=>appendSkillEntry(data,0,{raw:{type:'skill',svt:0,skill:0},index:0}),/指定从者/);
 assert.throws(()=>appendNPEntry(data,0,0,catalog),/选择从者/);
 for(const type of ['np',1,2,3] as const)assert.throws(()=>makeCard(0,type,members(data),catalog),/选择从者/);
 assert.deepEqual(data,before);
 appendSkillEntry(data,0,{raw:{type:'skill',svt:1,skill:0,options:{playerTarget:0}},index:0});
 assert.equal(data.actions[0].options.playerTarget,0);
});
test('old actions on an unidentified support remain recorded with an explicit unresolved title',()=>{
 const data:Json={team:{onFieldSvts:[{supportType:'friend',skillIds:[123],equip1:{id:10}},null,null]},actions:[{type:'skill',svt:0,skill:0}]},before=clone(data);
 const label=actionTitle(turnsOf(data)[0].entries[0],data,catalog);
 assert.match(label,/助战（仅要求礼装）/);assert.match(label,/未指定从者，无法安排技能/);assert.doesNotMatch(label,/#123/);
 assert.deepEqual(data,before);
});
test('skill and card availability follows the roster after swapping an unknown support with a known backup',()=>{
 const data:Json={team:{onFieldSvts:[{supportType:'friend',equip1:{id:10}},null,null],backupSvts:[{svtId:1},null,null],mysticCode:{mysticCodeId:210}},actions:[]};
 appendSkillEntry(data,0,{raw:{type:'skill',svt:null,skill:2},replacement:[0,0],paired:true,index:0});
 appendSkillEntry(data,0,{raw:{type:'skill',svt:0,skill:0},index:0});appendNPEntry(data,0,0,catalog);
 assert.equal(data.actions[1].svt,0);assert.equal(data.actions[2].attacks[0].svt,0);
 appendSkillEntry(data,1,{raw:{type:'skill',svt:0,skill:1},index:0});
 assert.deepEqual(data.actions[3],{type:'skill',svt:0,skill:1});
});
const fixture=():Json=>({unknown:{null:null,deep:['x',{test:true}]},team:{name:'原名',onFieldSvts:[{svtId:1,skillLvs:[10,0,2],appendLvs:[0,10,0,0,0],equip1:{ceId:123,deep:null}}, {svtId:2}, {svtId:3}],backupSvts:[{svtId:4},null,null],mysticCode:{mysticCodeId:20}},actions:[{type:'skill',svt:0,skill:0,unknown:['keep']},{type:'skill',svt:null,skill:2},{type:'skill',svt:0,skill:1},{type:'attack',attacks:[{svt:0,isTD:true,card:-1}],options:{enemyTarget:2}},{type:'attack',attacks:[]}],delegate:{replaceMemberIndexes:[[0,0]],unknown:'keep'}});
test('new plans explicitly start with Chaldea mystic code 210 at level 10',()=>{const d=newBattle();assert.deepEqual(d.team.mysticCode,{mysticCodeId:210,level:10});assert.deepEqual(members(d),[null,null,null,null,null,null]);assert.deepEqual(d.actions,[]);});

test('zero turns and a recorded empty turn stay distinct through JSON and turn deletion',()=>{
 for(const form of ['missing','null','empty']){
  const data:Json={team:{name:'仅编队',onFieldSvts:[{svtId:1,supportType:'friend'}]},future:{keep:[null,false]}};
  if(form!=='missing')data.actions=form==='null'?null:[];
  const original=clone(data);assertEditable(data);assert.equal(turnsOf(data).length,0);assert.deepEqual(data,original);
  const empty={type:'attack',attacks:[],future:{keep:true}};
  writeTurns(data,[{entries:[{raw:empty,index:0}]}]);
  const restored=JSON.parse(JSON.stringify(data));
  assert.equal(turnsOf(restored).length,1);assert.deepEqual(restored.actions,[empty]);
  writeTurns(restored,[]);assert.equal(turnsOf(restored).length,0);assert.deepEqual(restored.actions,[]);
  assert.deepEqual(restored.team,original.team);assert.deepEqual(restored.future,original.future);
 }
});
test('each new plan owns independent formation, mystic code and action containers',()=>{const a=newBattle(),b=newBattle();a.team.mysticCode.level=1;a.team.mysticCode.mysticCodeId=20;setMember(a,0,{svtId:1});a.actions.push({type:'attack',attacks:[]});assert.deepEqual(b,newBattle());assert.notEqual(a.team,b.team);assert.notEqual(a.team.mysticCode,b.team.mysticCode);assert.notEqual(a.team.onFieldSvts,b.team.onFieldSvts);assert.notEqual(a.actions,b.actions);});
test('import and draft projections preserve missing, null and different mystic code values',()=>{for(const team of [{},{mysticCode:null},{mysticCode:{}},{mysticCode:{mysticCodeId:20}},{mysticCode:{mysticCodeId:210,level:null}},{mysticCode:{mysticCodeId:1,level:5}}]){const imported:Json={team,actions:[],unknown:null},before=clone(imported);newBattle();assertEditable(imported);members(imported);turnsOf(imported);rosterAt(imported,0);assert.deepEqual(imported,before);}});
test('new servant skill defaults follow task active 10 and five append zeros with independent arrays',()=>{const a=newServantDefaults(),b=newServantDefaults();assert.deepEqual(a,{supportType:'none',skillLvs:[10,10,10],appendLvs:[0,0,0,0,0]});a.skillLvs[0]=1;a.appendLvs[1]=10;assert.deepEqual(b,newServantDefaults());assert.notEqual(a.skillLvs,b.skillLvs);assert.notEqual(a.appendLvs,b.appendLvs);});
test('new team support suggests only the empty first slot without creating a placeholder member',()=>{
 const data=newBattle(),before=clone(data);
 assert.equal(newTeamSupportSlot(data,true),0);
 for(const disabled of [false,undefined,null,1,'true'])assert.equal(newTeamSupportSlot(data,disabled as boolean),null);
 assert.deepEqual(newTeamMemberDefaults(data,0,true),{...newServantDefaults(),supportType:'friend'});
 assert.deepEqual(newTeamMemberDefaults(data,1,true),newServantDefaults());
 assert.deepEqual(newTeamMemberDefaults(data,0,false),newServantDefaults());
 assert.deepEqual(data,before);
});
test('filling other positions first keeps the support suggestion until an actual support is selected',()=>{
 const data=newBattle();setMember(data,2,replaceServant(null,4,catalog,newTeamMemberDefaults(data,2,true)));
 assert.equal(newTeamSupportSlot(data,true),0);assert.equal(members(data)[2].supportType,'none');
 for(const supportType of ['friend','npc','fixed']){
  setMember(data,5,{svtId:1,supportType});const before=clone(data);
  assert.equal(newTeamSupportSlot(data,true),null);
  assert.equal(newTeamMemberDefaults(data,0,true).supportType,'none');
  assert.deepEqual(data,before);
 }
});
test('existing first members retain manual support types when replaced and clearing allows the new default again',()=>{
 const data=newBattle();
 for(const supportType of ['none','friend','npc','fixed',undefined]){
  const old:Json={svtId:1,lv:80,supportType,equip1:{id:34},future:{keep:null}};setMember(data,0,old);
  assert.equal(newTeamSupportSlot(data,true),null);
  const changed=replaceServant(old,4,catalog,newTeamMemberDefaults(data,0,true));
  assert.equal(changed.supportType,supportType);assert.deepEqual(changed,{...old,svtId:4});
 }
 setMember(data,0,null);
 assert.equal(newTeamSupportSlot(data,true),0);
 const selected=replaceServant(null,1,catalog,newTeamMemberDefaults(data,0,true));setMember(data,0,selected);
 assert.equal(selected.supportType,'friend');assert.equal(newTeamSupportSlot(data,true),null);
 swapMembers(data,0,3);
 assert.equal(newTeamSupportSlot(data,true),null);assert.equal(newTeamMemberDefaults(data,0,true).supportType,'none');
 setMember(data,3,null);assert.equal(newTeamSupportSlot(data,true),0);
});
test('new team defaults clone custom presets independently and do not rewrite data or base defaults',()=>{
 const data=newBattle(),before=clone(data),base:Json={...newServantDefaults(),supportType:'npc',future:{keep:[null]}};
 const expected=clone(base),first=newTeamMemberDefaults(data,0,true,base),other=newTeamMemberDefaults(data,1,true,base);
 assert.equal(first.supportType,'friend');assert.equal(other.supportType,'npc');
 first.skillLvs[0]=2;first.appendLvs[0]=8;first.future.keep.push(9);other.future.keep.push(3);
 assert.deepEqual(base,expected);assert.deepEqual(data,before);
 assert.deepEqual(newTeamMemberDefaults(data,0,false,base),expected);
 assert.notEqual(first.skillLvs,other.skillLvs);assert.notEqual(first.future,other.future);
});
test('only an explicit new team draft flag without a formal source restores the suggestion',()=>{
 for(const sourceName of [undefined,null,''])assert.equal(restoreNewTeamFirstSupport(true,sourceName),true);
 for(const value of [undefined,null,false,0,1,'true',{},[]])assert.equal(restoreNewTeamFirstSupport(value),false);
 for(const sourceName of ['local_saved.json',' ',0,false,{},[]])assert.equal(restoreNewTeamFirstSupport(true,sourceName),false);
 const imported:Json={team:{onFieldSvts:[null,null,null]},actions:[],future:{keep:null}},before=clone(imported);
 assert.equal(newTeamSupportSlot(imported,restoreNewTeamFirstSupport(undefined)),null);
 assert.equal(newTeamMemberDefaults(imported,0,false).supportType,'none');assert.deepEqual(imported,before);
});
test('only selecting an empty member adds task defaults and custom defaults are cloned',()=>{const a:Json=replaceServant(null,1,catalog),b:Json=replaceServant(null,4,catalog),expectedA:Json={...newServantDefaults(),svtId:1};assert.deepEqual(a,expectedA);a.skillLvs[0]=1;a.appendLvs[1]=10;assert.deepEqual(b,{...newServantDefaults(),svtId:4});const custom:Json={...newServantDefaults(),supportType:'friend',unknown:{keep:null}};const selected=replaceServant(null,1,catalog,custom);selected.skillLvs[0]=5;selected.unknown.keep='changed';assert.deepEqual(custom,{...newServantDefaults(),supportType:'friend',unknown:{keep:null}});});
test('existing imported members keep missing null empty and explicit levels when replaced or changing support type',()=>{for(const levels of [{},{skillLvs:null,appendLvs:null},{skillLvs:[],appendLvs:[]},{skillLvs:[0,6,10],appendLvs:[0,10,0]},{skillLvs:[5,0,10],appendLvs:[0,10,0,2,0]}]){const old={svtId:1,supportType:'friend',lv:90,...levels,unknown:{keep:null}},before=clone(old);assert.deepEqual(replaceServant(old,4,catalog),{...old,svtId:4});assert.deepEqual(old,before);const d:Json={team:{onFieldSvts:[old]},actions:[]};assertEditable(d);members(d);turnsOf(d);rosterAt(d,0);assert.deepEqual(old,before);for(const supportType of ['none','friend','npc']){const changed=replaceServant({...old,supportType},4,catalog);assert.deepEqual(changed,{...old,supportType,svtId:4});}}});
test('explicit skill preset fill only supplies nullish slots and preserves zeros extras and unrelated fields',()=>{const m:Json={svtId:1,supportType:'npc',lv:90,skillLvs:[0,null,undefined,8],appendLvs:[null,10,undefined,0,null,7],skillIds:[11,12,13],tdId:9,equip1:{id:3},unknown:{keep:null}};fillMissingTaskSkillDefaults(m);assert.deepEqual(m,{svtId:1,supportType:'npc',lv:90,skillLvs:[0,10,10,8],appendLvs:[0,10,0,0,0,7],skillIds:[11,12,13],tdId:9,equip1:{id:3},unknown:{keep:null}});const once=clone(m);fillMissingTaskSkillDefaults(m);assert.deepEqual(m,once);});
test('explicit skill preset fill handles missing null and short arrays without changing existing requirements',()=>{for(const initial of [{},{skillLvs:null,appendLvs:null},{skillLvs:undefined,appendLvs:undefined},{skillLvs:[],appendLvs:[]}]){const m:Json={svtId:1,...initial};fillMissingTaskSkillDefaults(m);assert.deepEqual(m,{svtId:1,skillLvs:[10,10,10],appendLvs:[0,0,0,0,0]});}const short:Json={skillLvs:[6],appendLvs:[0,10,0]};fillMissingTaskSkillDefaults(short);assert.deepEqual(short,{skillLvs:[6,10,10],appendLvs:[0,10,0,0,0]});});
test('explicit skill fill refuses malformed containers atomically without normalizing imported values',()=>{for(const initial of [{skillLvs:'10',appendLvs:[]},{skillLvs:null,appendLvs:{unknown:10}}]){const m:Json={svtId:1,...initial},before=clone(m);assert.throws(()=>fillMissingTaskSkillDefaults(m),/必须是数组/);assert.deepEqual(m,before);}});
test('import/view projection never changes missing/null/unknown fields',()=>{const d=fixture(),before=clone(d);turnsOf(d);members(d);rosterAt(d,3);assert.deepEqual(d,before);});
test('known path edit keeps all other fields byte-value equivalent',()=>{const d=fixture(),expected=clone(d);expected.team.name='新名';d.team.name='新名';assert.deepEqual(d,expected);});
test('unmodified action projection and reserialization retains complete JSON tree',()=>{const d=fixture(),expected=clone(d);writeTurns(d,clone(turnsOf(d)));assert.deepEqual(d,expected);});
test('slot setter preserves all other members and three vs five append shape',()=>{const d=fixture(),m=members(d);setMember(d,5,{svtId:5});assert.equal(members(d)[5].svtId,5);assert.deepEqual(members(d).slice(0,5),m.slice(0,5));assert.equal(d.team.onFieldSvts[0].appendLvs.length,5);});
test('order change replays before later skill and uses relative backup indexes',()=>{const d=fixture();assert.equal(rosterAt(d,1)[0].svtId,1);assert.equal(rosterAt(d,2)[0].svtId,4);assert.equal(rosterAt(d,2)[3].svtId,1);});
test('legacy absolute backup indexes replay without silent normalization',()=>{const d=fixture();d.delegate.replaceMemberIndexes=[[0,3]];assert.equal(rosterAt(d,2)[0].svtId,4);writeTurns(d,clone(turnsOf(d)));assert.deepEqual(d.delegate.replaceMemberIndexes,[[0,3]]);});
test('turn duplication/reordering keeps each swap paired to its delegate entry',()=>{const d=fixture();d.actions.push({type:'skill',svt:null,skill:2},{type:'attack',attacks:[]});d.delegate.replaceMemberIndexes.push([1,2]);const ts=clone(turnsOf(d));writeTurns(d,[ts[2],ts[0],clone(ts[0]),ts[1]]);assert.deepEqual(d.delegate.replaceMemberIndexes,[[1,2],[0,0],[0,0]]);assert.equal(rosterAt(d,1)[1],null);});
test('deleting swap removes its pairing without orphaning later swaps',()=>{const d=fixture();d.actions.push({type:'skill',svt:null,skill:2},{type:'attack',attacks:[]});d.delegate.replaceMemberIndexes.push([1,2]);const ts=clone(turnsOf(d));ts[0].entries.splice(1,1);writeTurns(d,ts);assert.deepEqual(d.delegate.replaceMemberIndexes,[[1,2]]);});
test('unconsumed imported delegate entries preserved during reorder',()=>{const d=fixture();d.delegate.replaceMemberIndexes.push([2,2]);const ts=clone(turnsOf(d));writeTurns(d,[ts[1],ts[0]]);assert.deepEqual(d.delegate.replaceMemberIndexes,[[0,0],[2,2]]);});
test('NP/BAQ mixed cards use canonical indexes from actual five-card metadata',()=>{const roster=members(fixture());assert.deepEqual(makeCard(0,1,roster,catalog),{svt:0,isTD:false,card:1,cardType:1});assert.deepEqual(makeCard(0,'np',roster,catalog),{svt:0,isTD:true,card:-1});assert.throws(()=>makeCard(0,2,[{svtId:4}],catalog),/牌组元数据/);});
test('same servant and color uses distinct real card indexes, never duplicates',()=>{const roster=members(fixture());const a=makeCard(0,1,roster,catalog),b=makeCard(0,1,roster,catalog,[a]);assert.deepEqual([a.card,b.card],[1,2]);assert.throws(()=>makeCard(0,1,roster,catalog,[a,b]),/已全部使用/);assert.throws(()=>makeCard(0,'np',roster,catalog,[{svt:0,isTD:true,card:-1}]),/重复/);});
test('skill recolor uses explicitly selected distinct real deck indexes for three Busters',()=>{const roster=members(fixture());const a=makeCard(0,2,roster,catalog,[],0),b=makeCard(0,2,roster,catalog,[a],1),c=makeCard(0,2,roster,catalog,[a,b],2);assert.deepEqual([a,b,c].map(x=>[x.card,x.cardType]),[[0,2],[1,2],[2,2]]);assert.throws(()=>makeCard(0,2,roster,catalog,[a],0),/已在当前出卡顺序中使用/);assert.throws(()=>makeCard(0,2,roster,catalog,[],5),/合法索引/);assert.throws(()=>makeCard(0,2,roster,catalog,[],1.5),/合法索引/);assert.throws(()=>makeCard(0,2,[{svtId:4}],catalog,[],0),/完整五张牌组/);});
test('explicit recolored card preserves imported index and mixes with NP without changing NP schema',()=>{const roster=members(fixture());const np=makeCard(0,'np',roster,catalog),card=makeCard(0,2,roster,catalog,[np],2);assert.deepEqual(np,{svt:0,isTD:true,card:-1});assert.deepEqual(card,{svt:0,isTD:false,card:2,cardType:2});const imported={svt:0,isTD:false,card:2,cardType:2,unknown:{keep:null}};assert.deepEqual({...imported,...makeCard(0,2,roster,catalog,[],imported.card)},imported);});
test('imported alias IDs find canonical images/skills/cards without rewriting raw member',()=>{const c=clone(catalog);c.servants[0].aliases=[10001];const member={svtId:10001};assert.equal(servantFor(c,10001)?.name,'甲');assert.equal(makeCard(0,1,[member],c).card,1);assert.equal(member.svtId,10001);});
test('imported skill variant IDs take precedence over default and preserve unknown ID labels',()=>{const servant={id:1,name:'甲',skills:[{id:10,name:'默认'}],skillVariants:[{id:11,name:'旧形态',target:'ally'}]};assert.equal(skillAt({skillIds:[11]},servant,0)?.name,'旧形态');assert.equal(skillAt({skillIds:[99]},servant,0)?.name,'技能 #99');assert.equal(skillAt({},servant,0)?.name,'默认');});
test('replacing a different servant resets identity variants while retaining unknown attributes',()=>{const old={svtId:1,lv:90,skillIds:[11],tdId:12,limitCount:3,transformVal:1,unknown:{keep:null},equip1:{id:9}};const changed=replaceServant(old,4,catalog);assert.deepEqual(changed,{svtId:4,lv:90,unknown:{keep:null},equip1:{id:9}});assert.equal(old.skillIds[0],11);const c=clone(catalog);c.servants[0].aliases=[10001];assert.deepEqual(replaceServant({...old,svtId:10001},1,c),{...old,svtId:1});});
test('unknown action survives turn editing and forbids role remapping',()=>{const d=fixture();d.actions.unshift({type:'unknown',custom:{v:null}});const ts=clone(turnsOf(d));writeTurns(d,ts);assert.deepEqual(d.actions[0],{type:'unknown',custom:{v:null}});assert.throws(()=>remapSlots(d,0,1),/换人或未知/);});
test('safe front-slot remap updates skill, ally target and all mixed attack cards',()=>{const d=fixture();d.team.mysticCode={mysticCodeId:1};d.actions=[{type:'skill',svt:0,skill:1,options:{playerTarget:1}},{type:'attack',attacks:[{svt:0,isTD:true,card:-1},{svt:1,card:0,cardType:2}]}];remapSlots(d,0,1);assert.equal(d.team.onFieldSvts[0].svtId,2);assert.equal(d.actions[0].svt,1);assert.equal(d.actions[0].options.playerTarget,0);assert.deepEqual(d.actions[1].attacks.map((c:any)=>c.svt),[1,0]);});
test('malformed known containers rejected before entering visual editor; unknown fields allowed',()=>{assert.throws(()=>assertEditable({team:'wrong'}),/team/);assert.throws(()=>assertEditable({team:{onFieldSvts:[123]}}),/非对象槽位/);assert.throws(()=>assertEditable({actions:{}}),/actions/);assert.doesNotThrow(()=>assertEditable({team:null,unknown:{nested:null},actions:[{type:'future',options:'anything'}]}));});
test('dragging exchanges the complete member and three equips while preserving all action positions',()=>{const d=fixture(),before=clone(d);d.team.onFieldSvts[0].grandSvt=true;d.team.onFieldSvts[0].equip2={id:2};d.team.onFieldSvts[0].equip3={id:3};const member=clone(members(d)[0]);swapMembers(d,0,3);assert.deepEqual(members(d)[3],member);assert.deepEqual(members(d)[0],before.team.backupSvts[0]);assert.deepEqual(d.actions,before.actions);assert.deepEqual(d.delegate,before.delegate);assert.deepEqual(d.unknown,before.unknown);});
test('dragging into an empty slot moves the complete member; invalid data never mutates the formation',()=>{const d=fixture(),original=clone(members(d)[0]);swapMembers(d,0,5);assert.equal(members(d)[0],null);assert.deepEqual(members(d)[5],original);const before=clone(d);swapMembers(d,5,5);assert.deepEqual(d,before);for(const n of [-1,6,0.5,NaN])assert.throws(()=>swapMembers(d,0,n),/槽位/);assert.deepEqual(d,before);});
test('common equips filter is independent of owned, rarity and text search',()=>{const c:Catalog={servants:[],mysticCodes:[],equips:[{id:1,name:'常用五星',rarity:5,common:true},{id:2,name:'常用四星',rarity:4,common:true},{id:3,name:'工程五星',rarity:5,common:false},{id:4,name:'未知归类五星',rarity:5}].map(e=>({...e,recognizable:true,image:`/media/equip/${e.id}.png`})),inventory:{equips:[1,3]}};assert.deepEqual(filterCatalog('equip',c,{common:true}).map(e=>e.id),[1,2]);assert.deepEqual(filterCatalog('equip',c,{common:true,owned:true}).map(e=>e.id),[1]);assert.deepEqual(filterCatalog('equip',c,{common:false,owned:true}).map(e=>e.id),[1,3]);assert.deepEqual(filterCatalog('equip',c,{common:true,rarity:'4',query:' 四星 '}).map(e=>e.id),[2]);assert.equal(filterCatalog('equip',c,{common:false}).length,4);});
test('equip selector always excludes metadata-only, missing-template and editor-only entries without mutating imports',()=>{const c:Catalog={servants:[],mysticCodes:[],equips:[{id:1,name:'工程礼装',recognizable:true,image:'/media/equip/f_10.png'},{id:2,name:'只有补图',recognizable:false,image:'/media/ui/f_20.png',common:true},{id:3,name:'缺图',recognizable:true,image:null},{id:4,name:'只有名称'}],inventory:{equips:[1,2,3,4]}};const imported:Json={team:{onFieldSvts:[{svtId:1,equip1:{id:2,lv:80,limitBreak:true,future:{keep:null}}}]},actions:[]},before=clone(imported);for(const f of [{},{common:false},{owned:true},{query:'只有'},{common:true}]){const ids=filterCatalog('equip',c,f).map(e=>e.id);assert.ok(ids.every(id=>id===1));}assert.deepEqual(filterCatalog('equip',c,{}).map(e=>e.id),[1]);assert.equal(c.equips.find(e=>e.id===2)?.name,'只有补图');assert.deepEqual(imported,before);});
test('direct class and rarity buttons combine with aliases and owned IDs including zero rarity',()=>{const c:Catalog={equips:[],mysticCodes:[],servants:[{id:1,name:'甲',classKey:'saber',rarity:0,aliases:[100],searchAliases:['外号']},{id:2,name:'乙',classKey:'archer',rarity:5}],inventory:{servants:[100]}};assert.deepEqual(filterCatalog('servant',c,{classKey:'saber',rarity:'0',query:'外号',owned:true}).map(s=>s.id),[1]);assert.equal(filterCatalog('servant',c,{classKey:'archer',rarity:'0'}).length,0);assert.equal(filterCatalog('servant',c,{common:true}).length,2);});
test('missing append slot never borrows an icon from another numbered append skill',()=>{const servant={id:1,name:'甲',appendSkills:[{id:10,num:1,name:'追加一',icon:'/a'},{id:50,num:5,name:'追加五',icon:'/e'}]};assert.equal(appendSkillAt(servant,0)?.id,10);assert.equal(appendSkillAt(servant,3),undefined);assert.equal(appendSkillAt(servant,4)?.id,50);});
test('pointer movement under six pixels stays a click and never swaps',()=>{const gesture=updatePartyDrag(startPartyDrag(1,0,100,100),103,104,1);assert.equal(gesture.active,false);assert.equal(gesture.target,null);assert.equal(finishPartyDrag(gesture),null);});
test('pointer drag commits exactly the last valid destination and retains its source',()=>{const initial=startPartyDrag(1,0,100,100),crossed=updatePartyDrag(initial,110,101,2),final=updatePartyDrag(crossed,800,101,5);assert.equal(initial.active,false);assert.equal(final.active,true);assert.deepEqual(finishPartyDrag(final),[0,5]);});
test('pointer drag dropping outside or returning to source does not exchange',()=>{const drag=updatePartyDrag(startPartyDrag(1,0,100,100),160,100,2);assert.equal(finishPartyDrag(updatePartyDrag(drag,100,100,0)),null);for(const target of [null,-1,6,NaN,1.5])assert.equal(finishPartyDrag(updatePartyDrag(drag,900,900,target)),null);});
test('first skill creates a single ordinary attack boundary with no invented enemy default',()=>{const d:Json={team:{onFieldSvts:[{svtId:1}]},unknown:{keep:null}},raw={type:'skill',svt:0,skill:0,options:{enemyTarget:2}};assert.equal(appendSkillEntry(d,0,{raw,index:0}),0);assert.deepEqual(d.actions,[raw,{type:'attack',attacks:[]}]);assert.deepEqual(d.unknown,{keep:null});});
test('stage skill append preserves an imported attack object and comes after existing swaps',()=>{const d=fixture(),originalAttack=clone(d.actions[3]);assert.equal(appendSkillEntry(d,0,{raw:{type:'skill',svt:0,skill:2},index:0}),3);assert.deepEqual(d.actions[4],originalAttack);assert.equal(rosterAt(d,3)[0].svtId,4);assert.deepEqual(d.delegate.replaceMemberIndexes,[[0,0]]);});
test('confirmed stage swap is one paired action and NP uses the swapped current position',()=>{const d:Json={team:{onFieldSvts:[{svtId:1},null,null],backupSvts:[{svtId:4}],mysticCode:{mysticCodeId:20}},actions:[{type:'attack',attacks:[],unknown:null}]};appendSkillEntry(d,0,{raw:{type:'skill',svt:null,skill:2},replacement:[0,0],paired:true,index:0});appendNPEntry(d,0,0,catalog);assert.deepEqual(d.delegate.replaceMemberIndexes,[[0,0]]);assert.equal(rosterAt(d,1)[0].svtId,4);assert.deepEqual(d.actions[1],{type:'attack',attacks:[{svt:0,isTD:true,card:-1}],unknown:null});});
test('stage NP append rejects duplicates and fourth cards without altering raw data',()=>{const d=fixture();appendNPEntry(d,1,0,catalog);const before=clone(d);assert.throws(()=>appendNPEntry(d,1,0,catalog),/重复/);assert.deepEqual(d,before);d.actions[d.actions.length-1].attacks=[{svt:0,isTD:true,card:-1},{svt:1,isTD:true,card:-1},{svt:2,isTD:true,card:-1}];const full=clone(d);assert.throws(()=>appendNPEntry(d,1,0,catalog),/最多/);assert.deepEqual(d,full);});
test('new empty turn NP creates canonical attack without assigning an enemy or touching earlier turns',()=>{const d=fixture(),before=clone(d);assert.equal(appendNPEntry(d,2,0,catalog),0);assert.deepEqual(d.actions.slice(0,-1),before.actions);assert.deepEqual(d.actions[d.actions.length-1],{type:'attack',attacks:[{svt:0,isTD:true,card:-1}]});assert.deepEqual(d.delegate,before.delegate);});
test('stage NP fills the first automatic position without moving the delayed NP or changing options',()=>{
 const d:Json={team:{onFieldSvts:[{svtId:1},{svtId:4},null]},actions:[{type:'attack',attacks:[{isAuto:true},{isAuto:true},{svt:1,isTD:true,card:-1,future:{keep:null}}],options:{enemyTarget:2}}]};
 appendNPEntry(d,0,0,catalog);
 assert.deepEqual(d.actions[0],{type:'attack',attacks:[{svt:0,isTD:true,card:-1},{isAuto:true},{svt:1,isTD:true,card:-1,future:{keep:null}}],options:{enemyTarget:2}});
 const before=clone(d);assert.throws(()=>appendNPEntry(d,0,1,catalog),/重复/);assert.deepEqual(d,before);
});
test('role remapping leaves automatic card requests completely intact',()=>{
 const d=fixture();d.team.mysticCode={mysticCodeId:1};
 d.actions=[{type:'attack',attacks:[{isAuto:true,future:{keep:null}},{svt:0,isTD:true,card:-1}]}];
 remapSlots(d,0,1);
 assert.deepEqual(d.actions[0].attacks,[{isAuto:true,future:{keep:null}},{svt:1,isTD:true,card:-1}]);
});
