import test from 'node:test';
import assert from 'node:assert/strict';
import {actionMemberAt,actionTitle,appendNPEntry,appendSkillEntry,bindCardActor,bindEntryActor,clone,remapSlots,retirementInfoAt,rosterAt,turnsOf,writeTurns} from './model';
import type {Catalog,Entry,Json} from './model';
import {actionRosterAt,resolveActionActor} from './actionRoster';
import {previewTurn} from './battlePreview';
import {shareImageModel} from './shareImageModel';
import {setAttackCard,swapAttackCardPositions} from './attackCards';
import {setEntrySpecialSkillChoice,specialSkillChoiceLabel} from './specialSkillChoices';

const ids=[11,22,33,44,55,66],names=['甲','乙','丙','丁','戊','己'];
const catalog:Catalog={equips:[],mysticCodes:[{id:210,name:'换人服',skills:[{id:901,num:1,name:'强化'},{id:902,num:2,name:'回复'},{id:903,num:3,name:'换人'}]}],servants:ids.map((id,index)=>({id,name:names[index],image:`/${id}.png`,cards:[1,1,2,2,3],np:{id:id*100,name:`${names[index]}宝具`,cardType:1},skills:[0,1,2].map(skill=>({id:id*10+skill,num:skill+1,name:`${names[index]}技能${skill+1}`,target:'none',coolDown:Array(10).fill(6)}))})),specialSkillChoices:{schemaVersion:1,groups:[{id:'color',servantIds:[11],skillNumbers:[2],field:'tdTypeChanges',options:[{value:1,label:'Arts'},{value:2,label:'Buster'},{value:3,label:'Quick'}],defaultValue:1},{id:'choice',servantIds:[44],skillNumbers:[1],field:'skillActSelectSelections',options:[{value:0,label:'不消耗'},{value:1,label:'消耗'}],defaultValue:1}]}};
const fixture=():Json=>({team:{onFieldSvts:ids.slice(0,3).map(svtId=>({svtId,skillLvs:[10,10,10]})),backupSvts:ids.slice(3).map(svtId=>({svtId,skillLvs:[10,10,10]})),mysticCode:{mysticCodeId:210}},actions:[],future:{keep:[null,false]}});
const attack=()=>({type:'attack',attacks:[]});
const cast=(svt=0,skill=0)=>({type:'skill',svt,skill});
const retire=(svt=0):Entry=>({raw:{type:'retire',svt},index:0});

test('retirement promotes the next reserve into the same front slot and moves the original instance to the tail',()=>{
 const data=fixture();appendSkillEntry(data,0,retire(1),catalog);
 assert.deepEqual(data.actions[0],{type:'retire',svt:1,actor:{initialSlot:1,svtId:22}});
 assert.deepEqual(rosterAt(data,1).map(member=>member?.svtId),[11,44,33,55,66,22]);
 const view=previewTurn(data,catalog,0);assert.equal(view.front[1].key,'initial:3');assert.equal(view.roster[5].key,'initial:1');assert.equal(view.roster[5].retired,true);assert.equal(view.roster[5].skills[0].ready,false);
 assert.deepEqual(retirementInfoAt(data,0,catalog),{slot:1,member:data.team.onFieldSvts[1],replacement:data.team.backupSvts[0],initialSlot:1,replacementInitialSlot:3,valid:true});assert.deepEqual(data.future,{keep:[null,false]});
});

test('moving retirement before an old skill keeps the retired actor and never casts the incoming servant skill',()=>{
 const data=fixture();data.actions=[cast(),attack()];appendSkillEntry(data,0,retire(),catalog);
 let turns=clone(turnsOf(data,catalog));[turns[0].entries[0],turns[0].entries[1]]=[turns[0].entries[1],turns[0].entries[0]];writeTurns(data,turns,catalog);
 assert.deepEqual(data.actions[1].actor,{initialSlot:0,svtId:11});assert.equal(actionMemberAt(data,1)?.svtId,11);assert.match(actionTitle(turnsOf(data,catalog)[0].entries[1],data,catalog),/甲技能1/);
 const view=previewTurn(data,catalog,0);assert.ok(view.warnings.some(warning=>warning.code==='retired-actor'));assert.equal(view.front[0].servant?.id,44);assert.equal(view.front[0].skills[0].used,false);
 turns=clone(turnsOf(data,catalog));turns[0].entries.splice(1,1);writeTurns(data,turns,catalog);appendSkillEntry(data,0,{raw:cast(),index:0},catalog);
 assert.deepEqual(data.actions[1].actor,{initialSlot:3,svtId:44});assert.equal(previewTurn(data,catalog,0).front[0].skills[0].used,true);
});

test('deleting retirement does not turn an already configured replacement skill back into the original servant',()=>{
 const data=fixture();appendSkillEntry(data,0,retire(),catalog);appendSkillEntry(data,0,{raw:cast(),index:0},catalog);
 const turns=clone(turnsOf(data,catalog));turns[0].entries.splice(0,1);writeTurns(data,turns,catalog);
 assert.equal(actionMemberAt(data,0)?.svtId,44);assert.ok(previewTurn(data,catalog,0).warnings.some(warning=>warning.code==='actor-mismatch'));
 const fix=clone(turnsOf(data,catalog));bindEntryActor(fix[0].entries[0],data,0);writeTurns(data,fix,catalog);
 assert.deepEqual(data.actions[0].actor,{initialSlot:0,svtId:11});assert.equal(previewTurn(data,catalog,0).warnings.some(warning=>warning.code==='actor-mismatch'),false);
});

test('same-ID self and support copies retain independent identity and cooldown after retirement',()=>{
 const data=fixture();data.team.backupSvts[0]={svtId:11,supportType:'friend',skillLvs:[10,10,10]};data.actions=[cast(),attack()];appendSkillEntry(data,0,retire(),catalog);appendSkillEntry(data,0,{raw:cast(),index:0},catalog);
 const view=previewTurn(data,catalog,0);assert.equal(view.front[0].initialSlot,3);assert.equal(view.roster[5].initialSlot,0);assert.equal(view.front[0].skills[0].used,true);assert.equal(view.roster[5].skills[0].used,true);
 assert.deepEqual(data.actions[2].actor,{initialSlot:3,svtId:11});assert.equal(view.warnings.some(warning=>warning.code==='repeat-skill'),false);
});

test('Order Change followed by retirement returns the original reserve instance with its own cooldowns',()=>{
 const data=fixture();data.actions=[cast(),{type:'skill',svt:null,skill:2},cast(0,1),attack()];data.delegate={replaceMemberIndexes:[[0,0]]};
 appendSkillEntry(data,0,retire(),catalog);appendSkillEntry(data,0,{raw:cast(0,1),index:0},catalog);
 assert.deepEqual(data.actions[3].actor,{initialSlot:3,svtId:44});assert.deepEqual(data.actions[4].actor,{initialSlot:0,svtId:11});
 assert.deepEqual(rosterAt(data,5).map(member=>member?.svtId),[11,22,33,55,66,44]);
 const view=previewTurn(data,catalog,0);assert.equal(view.front[0].key,'initial:0');assert.equal(view.front[0].skills[0].remainingMax,6);assert.equal(view.front[0].skills[1].used,true);assert.equal(view.roster[5].key,'initial:3');assert.equal(view.roster[5].retired,true);
});

test('multiple retirements never recycle dead reserves and a copied retirement cannot kill its replacement',()=>{
 const data=fixture();appendSkillEntry(data,0,retire(),catalog);
 const copied=clone(turnsOf(data,catalog));copied[0].entries.splice(1,0,clone(copied[0].entries[0]));writeTurns(data,copied,catalog);
 assert.ok(previewTurn(data,catalog,0).warnings.some(warning=>warning.code==='retired-actor'));assert.equal(rosterAt(data,2)[0].svtId,44);
 copied[0].entries.splice(1,1);writeTurns(data,copied,catalog);appendSkillEntry(data,0,retire(),catalog);appendSkillEntry(data,0,retire(),catalog);appendSkillEntry(data,0,retire(),catalog);
 const view=previewTurn(data,catalog,0);assert.deepEqual(view.roster.map(actor=>actor.initialSlot),[5,1,2,0,3,4]);assert.ok(view.warnings.some(warning=>warning.code==='missing-replacement'));assert.deepEqual(view.roster.filter(actor=>actor.retired).map(actor=>actor.initialSlot),[0,3,4]);
});

test('empty backup holes are skipped and a swap cannot bring a retired tail instance back',()=>{
 const data=fixture();data.team.backupSvts[0]=null;appendSkillEntry(data,0,retire(),catalog);assert.equal(rosterAt(data,1)[0].svtId,55);
 appendSkillEntry(data,0,{raw:{type:'skill',svt:null,skill:2},replacement:[0,2],paired:true,index:0},catalog);
 assert.ok(previewTurn(data,catalog,0).warnings.some(warning=>warning.code==='invalid-swap'));assert.equal(rosterAt(data,2)[0].svtId,55);
});

test('replacement captions skip malformed reserve placeholders and guarded actor images retain the recorded ID after team edits',()=>{
 const data=fixture();data.team.backupSvts[0]={};appendSkillEntry(data,0,retire(),catalog);
 assert.equal(retirementInfoAt(data,0).replacement?.svtId,55);assert.equal(rosterAt(data,1)[0].svtId,55);
 data.team.onFieldSvts[0]={svtId:22};assert.equal(actionMemberAt(data,0)?.svtId,11);assert.match(retirementInfoAt(data,0).error??'',/原编队从者已改变/);
});

test('old NP remains bound across retirement insertion and reordering, while explicit replacement binds the new actor',()=>{
 const data=fixture();data.actions=[{type:'attack',attacks:[{svt:0,isTD:true,card:-1,future:'keep'}]}];appendSkillEntry(data,0,retire(),catalog);
 let card=data.actions[1].attacks[0];assert.deepEqual(card.actor,{initialSlot:0,svtId:11});assert.equal(resolveActionActor(actionRosterAt(data,1),card).issue?.code,'retired-actor');
 const turns=clone(turnsOf(data,catalog));swapAttackCardPositions(turns[0].entries[1].raw,0,2);writeTurns(data,turns,catalog);card=data.actions[1].attacks[2];assert.equal(card.actor.initialSlot,0);assert.equal(data.actions[1].attacks[0].actor,undefined);
 const replacement={svt:0,isTD:true,card:-1};bindCardActor(replacement,data,1);setAttackCard(data.actions[1],replacement,2);assert.deepEqual(data.actions[1].attacks[2].actor,{initialSlot:3,svtId:44});assert.equal(data.actions[1].attacks[2].future,'keep');
});

test('new NP appended after retirement uses the incoming instance and implicit ordinary cards stay unbound',()=>{
 const data=fixture();appendSkillEntry(data,0,retire(),catalog);appendNPEntry(data,0,0,catalog);
 assert.deepEqual(data.actions[1].attacks[0].actor,{initialSlot:3,svtId:44});
 const turns=clone(turnsOf(data,catalog));swapAttackCardPositions(turns[0].entries[1].raw,0,2);writeTurns(data,turns,catalog);assert.deepEqual(data.actions[1].attacks.slice(0,2),[{isAuto:true},{isAuto:true}]);
});

test('retirement keeps old special-skill choices with their original actor and the two delegate queues remain independent',()=>{
 const data=fixture();data.actions=[cast(0,1),attack()];data.delegate={tdTypeChanges:['quick',{future:'tail'}],future:'keep'};appendSkillEntry(data,0,retire(),catalog);
 const turns=clone(turnsOf(data,catalog));[turns[0].entries[0],turns[0].entries[1]]=[turns[0].entries[1],turns[0].entries[0]];writeTurns(data,turns,catalog);
 assert.deepEqual(data.delegate.tdTypeChanges,['quick',{future:'tail'}]);assert.equal(specialSkillChoiceLabel(turnsOf(data,catalog)[0].entries[1],catalog),'Quick');
 const entry:Entry={raw:cast(0,0),index:0};setEntrySpecialSkillChoice(entry,catalog.specialSkillChoices!.groups[1],0);appendSkillEntry(data,0,entry,catalog);
 assert.deepEqual(data.delegate.skillActSelectSelections,[0]);assert.deepEqual(data.delegate.tdTypeChanges,['quick',{future:'tail'}]);assert.equal(data.delegate.future,'keep');
});

test('share projection shows retirement, replacement and the original invalid skill/NP owner instead of new images',()=>{
 const data=fixture();data.actions=[cast(),{type:'attack',attacks:[{svt:0,isTD:true,card:-1}]}];appendSkillEntry(data,0,retire(),catalog);
 const turns=clone(turnsOf(data,catalog));[turns[0].entries[0],turns[0].entries[1]]=[turns[0].entries[1],turns[0].entries[0]];writeTurns(data,turns,catalog);
 const before=clone(data),shared=shareImageModel(data,catalog),[retirement,skill,attackRow]=shared.turns[0].actions;
 assert.equal(retirement.kind,'retire');if(skill.kind==='attack'||attackRow.kind!=='attack')throw new Error('projection kind');
 assert.equal(retirement.actor.name,'甲');assert.equal(retirement.target?.name,'丁');assert.equal(skill.actor.name,'甲');assert.equal(skill.name,'甲技能1');assert.match(skill.detail,/已退场/);assert.equal(attackRow.cards[0].person?.name,'甲');assert.equal(attackRow.cards[0].name,'甲宝具');assert.deepEqual(data,before);
});

test('ordinary legacy plans remain byte-value unchanged and actor metadata never leaks from read projections',()=>{
 const data=fixture();data.actions=[cast(),{type:'attack',attacks:[{svt:0,isTD:true,card:-1}]}];const before=clone(data),turns=turnsOf(data,catalog);previewTurn(data,catalog,0);shareImageModel(data,catalog);assert.deepEqual(data,before);writeTurns(data,turns,catalog);assert.deepEqual(data,before);
});

test('explicit role remapping also updates guarded initial-slot tokens after a retirement is removed',()=>{
 const data=fixture();data.actions=[cast(),{type:'attack',attacks:[{svt:0,isTD:true,card:-1}]}];appendSkillEntry(data,0,retire(),catalog);
 const turns=clone(turnsOf(data,catalog));turns[0].entries.splice(1,1);writeTurns(data,turns,catalog);remapSlots(data,0,1);
 assert.deepEqual(data.actions[0].actor,{initialSlot:1,svtId:11});assert.deepEqual(data.actions[1].attacks[0].actor,{initialSlot:1,svtId:11});assert.equal(actionMemberAt(data,0)?.svtId,11);assert.equal(previewTurn(data,catalog,0).warnings.some(warning=>warning.code==='actor-mismatch'),false);
});

test('explicit card changes replace an old guard even after every retirement action has been deleted',()=>{
 const data=fixture();data.actions=[{type:'attack',attacks:[{svt:0,isTD:true,card:-1}]}];appendSkillEntry(data,0,retire(),catalog);
 const turns=clone(turnsOf(data,catalog));turns[0].entries.splice(0,1);writeTurns(data,turns,catalog);
 const replacement={svt:1,isTD:true,card:-1};bindCardActor(replacement,data,0);setAttackCard(data.actions[0],replacement,0);
 assert.deepEqual(data.actions[0].attacks[0].actor,{initialSlot:1,svtId:22});assert.equal(resolveActionActor(actionRosterAt(data,0),data.actions[0].attacks[0]).issue,undefined);
});
