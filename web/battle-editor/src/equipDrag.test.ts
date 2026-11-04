import test from 'node:test';
import assert from 'node:assert/strict';
import {equipForDisplay} from './equipRequirements';
import type {Json} from './model';
import {canDropEquipDrag,canStartEquipDrag,equipDragPosition,swapEquipRequirements} from './equipDrag';

const at=(slot:number,key='equip1')=>({slot,key});

test('candidate groups move as one requirement only between executable support slots',()=>{
 const data:Json={team:{onFieldSvts:[{svtId:1,supportType:'friend',grandSvt:true,equip1:{id:101,candidates:[{id:202,limitBreak:true,future:{keep:null}}]},equip3:{id:303,limitBreak:false}},{svtId:2,supportType:'none',equip1:{id:404}},null],backupSvts:[null,null,null]},actions:[]};
 const before=structuredClone(data);
 assert.equal(canDropEquipDrag(data,at(0),at(1)),false);assert.equal(swapEquipRequirements(data,at(0),at(1)),false);assert.deepEqual(data,before);
 assert.equal(canDropEquipDrag(data,at(1),at(0)),false);
 assert.equal(canDropEquipDrag(data,at(0),at(0,'equip2')),false);
 assert.equal(swapEquipRequirements(data,at(0),at(0,'equip3')),true);
 assert.deepEqual(data.team.onFieldSvts[0].equip3,{id:101,candidates:[{id:202,limitBreak:true,future:{keep:null}}]});
 assert.deepEqual(data.team.onFieldSvts[0].equip1,{id:303,limitBreak:false});
 assert.deepEqual(data.actions,[]);
});
const fixture=():Json=>({
 team:{onFieldSvts:[
  {svtId:100,supportType:'none',lv:90,skillLvs:[10,10,10],equip1:{id:101,lv:80,limitBreak:true,future:{nested:['a',{value:1}]}},futureMember:{keep:1}},
  {svtId:200,supportType:'friend',tdLv:3,equip1:{id:202,lv:0,limitBreak:false,future:{nested:['b']}}},
  {svtId:300,grandSvt:true,equip1:{id:301,lv:null,limitBreak:null},equip2:{id:302,lv:100,future:{grand:['second']}},equip3:{id:303,limitBreak:true},classBoardData:{future:['board']}}
 ],backupSvts:[{svtId:400,appendLvs:[0,10,0]},null,null],mysticCode:{mysticCodeId:210,level:10},futureTeam:['keep']},
 actions:[{type:'skill',svt:0,skill:1,options:{future:[1]}},{type:'attack',attacks:[{svt:0,isTD:true,card:-1}]}],
 delegate:{replaceMemberIndexes:[[0,1]],future:{keep:true}},futureRoot:{keep:['root']}
});

test('ordinary CE exchange keeps complete records and every non-CE field unchanged',()=>{
 const data=fixture(),before=structuredClone(data),a=data.team.onFieldSvts[0],b=data.team.onFieldSvts[1];
 const originalA=a.equip1,originalB=b.equip1;
 assert.equal(swapEquipRequirements(data,at(0),at(1)),true);
 const expected=structuredClone(before);[expected.team.onFieldSvts[0].equip1,expected.team.onFieldSvts[1].equip1]=[expected.team.onFieldSvts[1].equip1,expected.team.onFieldSvts[0].equip1];
 assert.deepEqual(data,expected);assert.equal(data.team.onFieldSvts[0],a);assert.equal(data.team.onFieldSvts[1],b);
 assert.notEqual(a.equip1,originalB);assert.notEqual(b.equip1,originalA);
 a.equip1.future.nested.push('detached');assert.deepEqual(originalB,before.team.onFieldSvts[1].equip1);
});

test('moving into an existing member with no CE leaves the source unrestricted',()=>{
 const data=fixture(),before=structuredClone(data),source=data.team.onFieldSvts[0].equip1;
 assert.equal(canStartEquipDrag(data,at(3)),false);assert.equal(canDropEquipDrag(data,at(0),at(3)),true);
 assert.equal(swapEquipRequirements(data,at(0),at(3)),true);
 assert.equal(Object.hasOwn(data.team.onFieldSvts[0],'equip1'),false);
 assert.deepEqual(data.team.backupSvts[0].equip1,source);
 assert.deepEqual(data.actions,before.actions);assert.deepEqual(data.delegate,before.delegate);
 assert.equal(equipForDisplay(data.team.onFieldSvts[0],'equip1'),undefined);
});

test('grand bond slot rejects every drag direction without changing legacy CE data or effect settings',()=>{
 for(const effect of [undefined,false,true]){
  const data=fixture(),grand=data.team.onFieldSvts[2];
  if(effect!==undefined)grand.classBoardData.grandBondEquipSkillChange=effect;
  const before=structuredClone(data),bond=grand.equip2,board=grand.classBoardData;
  assert.equal(equipDragPosition(data,at(2,'equip2')),null);
  assert.equal(canStartEquipDrag(data,at(2,'equip2')),false);
  for(const other of [at(0),at(1),at(2),at(2,'equip3'),at(3)]){
   assert.equal(canDropEquipDrag(data,at(2,'equip2'),other),false);
   assert.equal(canDropEquipDrag(data,other,at(2,'equip2')),false);
   assert.equal(swapEquipRequirements(data,at(2,'equip2'),other),false);
   assert.equal(swapEquipRequirements(data,other,at(2,'equip2')),false);
   assert.deepEqual(data,before);
  }
  assert.equal(grand.equip2,bond);assert.equal(grand.classBoardData,board);
 }
});

test('main and additional grand CE exchange while the bond record and effect stay on their servant',()=>{
 const data=fixture(),before=structuredClone(data);
 assert.equal(swapEquipRequirements(data,at(2),at(2,'equip3')),true);
 const expected=structuredClone(before);[expected.team.onFieldSvts[2].equip1,expected.team.onFieldSvts[2].equip3]=[expected.team.onFieldSvts[2].equip3,expected.team.onFieldSvts[2].equip1];
 assert.deepEqual(data,expected);
});

test('main slots carry their complete original legacy packages rather than reviving old aliases',()=>{
 const data=fixture(),a=data.team.onFieldSvts[0],b=data.team.onFieldSvts[1];
 Object.assign(a,{ceId:999,ceLv:99,ceLimitBreak:false});
 b.equip1={id:null,future:{legacy:['kept']}};Object.assign(b,{ceId:808,ceLv:77,ceLimitBreak:true});
 const before=structuredClone(data),displayA=equipForDisplay(a,'equip1'),displayB=equipForDisplay(b,'equip1');
 assert.equal(swapEquipRequirements(data,at(0),at(1)),true);
 for(const key of ['equip1','ceId','ceLv','ceLimitBreak']){
  assert.deepEqual(a[key],before.team.onFieldSvts[1][key]);assert.deepEqual(b[key],before.team.onFieldSvts[0][key]);
 }
 assert.deepEqual(equipForDisplay(a,'equip1'),displayB);assert.deepEqual(equipForDisplay(b,'equip1'),displayA);
});

test('moving legacy-only main to an empty main removes aliases from the original member',()=>{
 const data=fixture(),a=data.team.onFieldSvts[0];delete a.equip1;Object.assign(a,{ceId:707,ceLv:80,ceLimitBreak:true});
 assert.equal(swapEquipRequirements(data,at(0),at(3)),true);
 for(const key of ['equip1','ceId','ceLv','ceLimitBreak'])assert.equal(Object.hasOwn(a,key),false);
 assert.deepEqual(data.team.backupSvts[0],{svtId:400,appendLvs:[0,10,0],ceId:707,ceLv:80,ceLimitBreak:true});
 assert.equal(equipForDisplay(a,'equip1'),undefined);
});

test('legacy main moved to the grand additional slot keeps unknown fields and preserves the bond record',()=>{
 const data=fixture(),a=data.team.onFieldSvts[0],grand=data.team.onFieldSvts[2];
 a.equip1={id:null,future:{unknown:[{keep:'deep'}]}};Object.assign(a,{ceId:707,ceLv:80,ceLimitBreak:true});
 const previousSecondary=structuredClone(grand.equip3),grandMain=structuredClone(grand.equip1),grandBond=structuredClone(grand.equip2);
 assert.equal(swapEquipRequirements(data,at(0),at(2,'equip3')),true);
 assert.deepEqual(grand.equip3,{id:707,lv:80,limitBreak:true,future:{unknown:[{keep:'deep'}]}});
 assert.deepEqual(a.equip1,previousSecondary);for(const key of ['ceId','ceLv','ceLimitBreak'])assert.equal(Object.hasOwn(a,key),false);
 assert.deepEqual(grand.equip1,grandMain);assert.deepEqual(grand.equip2,grandBond);
});

test('a legacy target main is normalized when its CE exchanges with a secondary source',()=>{
 const data=fixture(),a=data.team.onFieldSvts[0],grand=data.team.onFieldSvts[2];
 delete a.equip1;Object.assign(a,{ceId:505,ceLv:0,ceLimitBreak:false});
 const secondary=structuredClone(grand.equip3);
 assert.equal(swapEquipRequirements(data,at(2,'equip3'),at(0)),true);
 assert.deepEqual(a.equip1,secondary);assert.deepEqual(grand.equip3,{id:505,lv:0,limitBreak:false});
 for(const key of ['ceId','ceLv','ceLimitBreak'])assert.equal(Object.hasOwn(a,key),false);
});

test('current main values override legacy values during only the participating main normalization',()=>{
 const data=fixture(),grand=data.team.onFieldSvts[2];
 Object.assign(grand,{ceId:999,ceLv:80,ceLimitBreak:true});
 assert.equal(swapEquipRequirements(data,at(2),at(2,'equip3')),true);
 assert.deepEqual(grand.equip3,{id:301,lv:null,limitBreak:null});
 assert.deepEqual(grand.equip1,{id:303,limitBreak:true});
 for(const key of ['ceId','ceLv','ceLimitBreak'])assert.equal(Object.hasOwn(grand,key),false);
});

test('an empty explicit-zero target retains its unknown object and never borrows a stale ID',()=>{
 const data=fixture(),target=data.team.backupSvts[0];
 data.team.onFieldSvts[2].equip3={id:303,lv:100,future:{grand:['third']}};
 const bondBefore=structuredClone(data.team.onFieldSvts[2].equip2);
 Object.assign(target,{equip1:{id:0,future:{placeholder:true}},ceId:999,ceLv:12,ceLimitBreak:false});
 assert.equal(canStartEquipDrag(data,at(3)),false);
 assert.equal(swapEquipRequirements(data,at(2,'equip3'),at(3)),true);
 assert.deepEqual(data.team.onFieldSvts[2].equip3,{id:0,lv:12,limitBreak:false,future:{placeholder:true}});
 assert.equal(equipForDisplay(data.team.onFieldSvts[2],'equip3'),undefined);
 assert.deepEqual(target.equip1,{id:303,lv:100,future:{grand:['third']}});
 assert.deepEqual(data.team.onFieldSvts[2].equip2,bondBefore);
 for(const key of ['ceId','ceLv','ceLimitBreak'])assert.equal(Object.hasOwn(target,key),false);
});

test('empty null requirements and absent secondary cells move independently without touching another main legacy',()=>{
 const data=fixture(),a=data.team.onFieldSvts[0],grand=data.team.onFieldSvts[2];
 delete grand.equip3;Object.assign(grand,{ceId:909,ceLv:99,ceLimitBreak:true});
 const mainBefore=structuredClone(grand.equip1);
 assert.equal(swapEquipRequirements(data,at(0),at(2,'equip3')),true);
 assert.equal(Object.hasOwn(a,'equip1'),false);assert.deepEqual(grand.equip1,mainBefore);
 assert.equal(grand.ceId,909);assert.equal(grand.ceLv,99);assert.equal(grand.ceLimitBreak,true);
 a.equip1=null;
 assert.equal(swapEquipRequirements(data,at(2,'equip3'),at(0)),true);
 assert.equal(grand.equip3,null);assert.equal(equipForDisplay(grand,'equip3'),undefined);
});

test('invalid addresses, empty members, hidden secondary cells, and the same cell reject atomically',()=>{
 const invalid=[null,undefined,{},[],at(-1),at(6),at(1.5),at(NaN),at(Infinity),at(0,'equip0'),at(0,'ceId'),at(0,'__proto__'),at(0,'equip2'),at(2,'equip2'),at(1,'equip3'),at(4),at(5)];
 for(const position of invalid){
  const data=fixture(),before=structuredClone(data);
  assert.equal(equipDragPosition(data,position),null);assert.equal(canStartEquipDrag(data,position),false);
  assert.equal(canDropEquipDrag(data,at(0),position),false);assert.equal(swapEquipRequirements(data,at(0),position),false);
  assert.equal(swapEquipRequirements(data,position,at(0)),false);assert.deepEqual(data,before);
 }
 const data=fixture(),before=structuredClone(data);
 assert.equal(canDropEquipDrag(data,at(0),at(0)),false);assert.equal(swapEquipRequirements(data,at(0),at(0)),false);assert.deepEqual(data,before);
 assert.deepEqual(equipDragPosition(data,at(2,'equip3')),{slot:2,key:'equip3'});
});

test('empty or malformed source records cannot drag and malformed targets never partly mutate',()=>{
 for(const stored of [undefined,null,{}, {id:0},{id:null},[], 'bad', {id:-1},{id:'101'},{id:Infinity},{id:Number.MAX_SAFE_INTEGER+1}]){
  const data=fixture();data.team.onFieldSvts[0].equip1=stored;const before=structuredClone(data);
  assert.equal(canStartEquipDrag(data,at(0)),false);assert.equal(swapEquipRequirements(data,at(0),at(1)),false);assert.deepEqual(data,before);
 }
 for(const stored of [[], 'bad',{id:-1},{id:'101'},{id:Infinity}]){
  const data=fixture();data.team.onFieldSvts[1].equip1=stored;const before=structuredClone(data);
  assert.equal(canDropEquipDrag(data,at(0),at(1)),false);assert.equal(swapEquipRequirements(data,at(0),at(1)),false);assert.deepEqual(data,before);
 }
 const data=fixture();data.team.backupSvts[0].ceId='999';const before=structuredClone(data);
 assert.equal(swapEquipRequirements(data,at(2,'equip3'),at(3)),false);assert.deepEqual(data,before);
});

test('all dragged CE fields are detached before either side changes',()=>{
 const data=fixture(),a=data.team.onFieldSvts[0],b=data.team.onFieldSvts[1],before=structuredClone(data);
 // Cloning a malformed non-JSON field fails before either member is edited.
 b.equip1.future.unsupported=()=>{};
 assert.equal(swapEquipRequirements(data,at(0),at(1)),false);
 delete b.equip1.future.unsupported;assert.deepEqual(data,before);
 a.equip1.future.unsupported=()=>{};
 assert.equal(canStartEquipDrag(data,at(0)),false);assert.equal(swapEquipRequirements(data,at(0),at(1)),false);
 delete a.equip1.future.unsupported;assert.deepEqual(data,before);
});
