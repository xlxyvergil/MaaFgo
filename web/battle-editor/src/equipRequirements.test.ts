import test from 'node:test';
import assert from 'node:assert/strict';
import type {Equip,Json} from './model';
import {matchesSupportEquip,supportEquipRequirement} from './supportEquipSelection';
import {clearEquipRequirement,equipForDisplay,hasStoredEquipRequirement,equipCandidatesForDisplay,supportsEquipCandidates,setEquipCandidate,updateEquipCandidate,removeEquipCandidate} from './equipRequirements';

test('clearing slot one removes the complete object and all legacy fallback fields only',()=>{
 const second={id:202,future:{keep:true}},third={id:303},board={grandBondEquipSkillChange:true,future:1};
 const member:Json={svtId:100,grandSvt:true,equip1:{id:101,lv:80,limitBreak:true,future:{drop:'whole object'}},
  equip2:second,equip3:third,ceId:999,ceLimitBreak:false,ceLv:55,classBoardData:board,future:{keep:['member']}};
 clearEquipRequirement(member,'equip1');
 assert.deepEqual(member,{svtId:100,grandSvt:true,equip2:second,equip3:third,classBoardData:board,future:{keep:['member']}});
 assert.equal(member.equip2,second);assert.equal(member.equip3,third);assert.equal(member.classBoardData,board);
 assert.equal(hasStoredEquipRequirement(member,'equip1'),false);assert.equal(equipForDisplay(member,'equip1'),undefined);
});

test('each grand slot is independent and clearing two or three preserves legacy slot one',()=>{
 for(const key of ['equip2','equip3']){
  const member:Json={svtId:100,grandSvt:true,equip1:{id:101},equip2:{id:202,future:'bond'},equip3:{id:303},
   ceId:999,ceLimitBreak:true,ceLv:80,classBoardData:{grandBondEquipSkillChange:false},future:{keep:1}};
  const expected=structuredClone(member);delete expected[key];
  clearEquipRequirement(member,key);assert.deepEqual(member,expected);
 }
});

test('own null, zero, empty and legacy-only records remain clearable without inherited placeholders',()=>{
 for(const value of [null,undefined,{id:null},{id:0},{}]){
  const member:Json={equip1:value,future:1};assert.equal(hasStoredEquipRequirement(member,'equip1'),true);
  clearEquipRequirement(member,'equip1');assert.deepEqual(member,{future:1});
 }
 for(const key of ['ceId','ceLimitBreak','ceLv']){
  const member:Json={[key]:null};assert.equal(hasStoredEquipRequirement(member,'equip1'),true);
  assert.equal(hasStoredEquipRequirement(member,'equip2'),false);clearEquipRequirement(member,'equip1');assert.deepEqual(member,{});
 }
 const inherited=Object.create({equip1:{id:101},ceId:999}) as Json;
 assert.equal(hasStoredEquipRequirement(inherited,'equip1'),false);
 assert.equal(hasStoredEquipRequirement(null,'equip1'),false);assert.equal(hasStoredEquipRequirement({},'equip1'),false);
});

test('display prefers a positive current ID, preserves explicit zero/false and copies unknown fields',()=>{
 const member:Json={equip1:{id:101,lv:0,limitBreak:false,future:{nested:['keep']}},ceId:999,ceLv:80,ceLimitBreak:true,future:'member'};
 const before=structuredClone(member),display=equipForDisplay(member,'equip1')!;
 assert.deepEqual(display,{id:101,lv:0,limitBreak:false,future:{nested:['keep']}});
 display.future.nested.push('display only');display.lv=100;assert.deepEqual(member,before);
});

test('null or absent current IDs display compatible legacy attributes without writing them back',()=>{
 for(const stored of [undefined,null,{}, {id:null,future:{keep:true}}, {id:undefined}]){
  const member:Json={ceId:101,ceLv:80,ceLimitBreak:true,future:'member'};
  if(stored!==undefined)member.equip1=stored;
  const before=structuredClone(member),display=equipForDisplay(member,'equip1')!;
  assert.deepEqual(display,{...(stored??{}),id:101,lv:80,limitBreak:true});assert.deepEqual(member,before);
 }
 assert.deepEqual(equipForDisplay({equip1:{id:101},ceLv:80,ceLimitBreak:true},'equip1'),{id:101,lv:80,limitBreak:true});
 assert.deepEqual(equipForDisplay({equip1:{id:101,lv:null,limitBreak:null},ceLv:80,ceLimitBreak:true},'equip1'),{id:101,lv:null,limitBreak:null});
});

test('zero or malformed current IDs never borrow a legacy ID, and grand slots never borrow slot one',()=>{
 for(const id of [0,-1,0.5,NaN,Infinity,Number.MAX_SAFE_INTEGER+1,true,false,'101','0']){
  const member:Json={equip1:{id},ceId:999},before=structuredClone(member);
  assert.equal(equipForDisplay(member,'equip1'),undefined);assert.deepEqual(member,before);
 }
 for(const member of [null,{}, {ceId:0},{ceId:-1},{ceId:'101'},{equip1:[]},{equip1:'bad'}])assert.equal(equipForDisplay(member,'equip1'),undefined);
 assert.equal(equipForDisplay({ceId:999,ceLv:80,ceLimitBreak:true,equip2:{id:null}},'equip2'),undefined);
 assert.deepEqual(equipForDisplay({ceId:999,ceLv:80,ceLimitBreak:true,equip3:{id:303}},'equip3'),{id:303});
});

test('invalid slot keys fail before mutating the member',()=>{
 for(const key of ['equip0','equip4','ceId','svtId','__proto__','']){
  const member:Json={svtId:100,equip1:{id:101},ceId:999},before=structuredClone(member);
  assert.throws(()=>clearEquipRequirement(member,key));assert.throws(()=>hasStoredEquipRequirement(member,key));assert.throws(()=>equipForDisplay(member,key));
  assert.deepEqual(member,before);
 }
});

test('candidate display keeps the original tree and legacy requirements intact',()=>{
 const member:Json={supportType:'friend',ceId:101,ceLv:80,ceLimitBreak:false,equip1:{candidates:[{id:202,limitBreak:true,future:{keep:null}}],future:'first'},future:'member'};
 const before=structuredClone(member),choices=equipCandidatesForDisplay(member,'equip1');
 assert.deepEqual(choices,[{id:101,lv:80,limitBreak:false,future:'first'},{id:202,limitBreak:true,future:{keep:null}}]);
 (choices[1].future as Json).keep='display only';assert.deepEqual(member,before);
});

test('candidate selection is ordered OR within one slot and preserves per-option attributes',()=>{
 const member:Json={supportType:'friend',ceId:101,ceLv:80,ceLimitBreak:false,unknown:{keep:true}};
 setEquipCandidate(member,'equip1',1,202);setEquipCandidate(member,'equip1',2,303);
 updateEquipCandidate(member,'equip1',1,{lv:100,limitBreak:true});
 updateEquipCandidate(member,'equip1',2,{limitBreak:undefined});
 assert.deepEqual(member,{supportType:'friend',equip1:{id:101,lv:80,limitBreak:false,candidates:[{id:202,lv:100,limitBreak:true},{id:303}]},unknown:{keep:true}});
 assert.throws(()=>setEquipCandidate(member,'equip1',2,101));
 assert.equal(member.equip1.candidates[1].id,303);
});

test('card replacement changes only the primary candidate and keeps its alternatives',()=>{
 const member:Json={supportType:'friend',equip1:{id:101,lv:50,candidates:[{id:202,limitBreak:true},{id:303}],future:{keep:null}}};
 setEquipCandidate(member,'equip1',0,404);
 assert.deepEqual(member.equip1,{id:404,lv:50,candidates:[{id:202,limitBreak:true},{id:303}],future:{keep:null}});
});

test('removing the first promotes a complete next candidate and never revives legacy CE fields',()=>{
 const member:Json={supportType:'friend',ceId:999,ceLv:12,equip1:{id:101,future:'removed',candidates:[{id:202,lv:100,limitBreak:true,future:{keep:null}},{id:303,limitBreak:false}]}};
 removeEquipCandidate(member,'equip1',0);
 assert.deepEqual(member,{supportType:'friend',equip1:{id:202,lv:100,limitBreak:true,future:{keep:null},candidates:[{id:303,limitBreak:false}]}});
 removeEquipCandidate(member,'equip1',1);assert.equal(member.equip1.candidates,undefined);
 removeEquipCandidate(member,'equip1',0);assert.deepEqual(member,{supportType:'friend'});
});

test('candidate edits can clear a legacy MLB restriction without it reappearing',()=>{
 const member:Json={supportType:'friend',ceId:101,ceLimitBreak:true,ceLv:80};
 updateEquipCandidate(member,'equip1',0,{limitBreak:undefined,lv:undefined});
 assert.deepEqual(member,{supportType:'friend',equip1:{id:101}});
});

test('only executable support slots accept additional candidates and rejected edits are atomic',()=>{
 for(const member of [{supportType:'none',equip1:{id:101}},{supportType:'friend',equip2:{id:101}},{supportType:'friend',grandSvt:true,equip2:{id:101}}] as Json[]){
  const key=member.equip2?'equip2':'equip1',before=structuredClone(member);
  assert.equal(supportsEquipCandidates(member,key),false);assert.throws(()=>setEquipCandidate(member,key,1,202));assert.deepEqual(member,before);
 }
 const member:Json={supportType:'npc',grandSvt:true,equip3:{id:101}};
 assert.equal(supportsEquipCandidates(member,'equip3'),true);setEquipCandidate(member,'equip3',1,202);
 assert.deepEqual(member.equip3,{id:101,candidates:[{id:202}]});
 for(const index of [-1,3,1.5])assert.throws(()=>setEquipCandidate(member,'equip3',index,303));
});

test('incomplete and oversized imported candidate trees remain readable without automatic rewriting',()=>{
 const member:Json={supportType:'friend',equip1:{id:101,candidates:[{id:202},null,{id:404}]}};
 const before=structuredClone(member);
 assert.deepEqual(equipCandidatesForDisplay(member,'equip1'),[{id:101},{id:202},{}]);
 assert.throws(()=>setEquipCandidate(member,'equip1',0,505));assert.deepEqual(member,before);
 const empty:Json={supportType:'friend'};assert.throws(()=>setEquipCandidate(empty,'equip1',1,202));assert.deepEqual(empty,{supportType:'friend'});
});

test('support-only selection respects member type and grand card-image slots without mutation',()=>{
 const friend={supportType:'friend',grandSvt:true,equip1:{id:1,limitBreak:true,candidates:[{id:2,limitBreak:false},{id:3}]},equip2:{id:4},equip3:{id:5,limitBreak:false}};
 const before=structuredClone(friend);
 assert.equal(supportEquipRequirement(friend,'equip1'), 'limitBreak');
 assert.equal(supportEquipRequirement(friend,'equip1',1), 'nonLimitBreak');
 assert.equal(supportEquipRequirement(friend,'equip1',2), 'any');
 assert.equal(supportEquipRequirement(friend,'equip3'), 'nonLimitBreak');
 assert.equal(supportEquipRequirement(friend,'equip2'), null);
 for(const supportType of ['npc','fixed'])assert.equal(supportEquipRequirement({...friend,supportType},'equip1'),'limitBreak');
 for(const supportType of ['none',undefined])assert.equal(supportEquipRequirement({...friend,supportType},'equip1'),null);
 assert.equal(supportEquipRequirement({...friend,grandSvt:false},'equip3'),null);
 assert.equal(supportEquipRequirement(null,'equip1',0,true),'any');
 assert.equal(supportEquipRequirement(null,'equip1'),null);
 assert.equal(supportEquipRequirement({supportType:'friend',ceId:1,ceLimitBreak:false},'equip1'),'nonLimitBreak');
 assert.deepEqual(friend,before);
});

test('support recognition is not inferred from common or team portraits and a toggle restores candidates',()=>{
 const item=(extra:Partial<Equip>={}):Equip=>({id:1,name:'太空女主人',image:'/media/equip/a.png',recognizable:true,common:true,...extra});
 assert.equal(matchesSupportEquip(item(),'any'),false);
 assert.equal(matchesSupportEquip(item({supportRecognition:{limitBreak:false,nonLimitBreak:false}}),'any'),false);
 const mlb=item({supportRecognition:{limitBreak:true,nonLimitBreak:false},common:false});
 assert.equal(matchesSupportEquip(mlb,'limitBreak'),true);
 assert.equal(matchesSupportEquip(mlb,'nonLimitBreak'),false);
 assert.equal(matchesSupportEquip(mlb,'any'),true);
 const non=item({supportRecognition:{limitBreak:false,nonLimitBreak:true}});
 assert.equal(matchesSupportEquip(non,'nonLimitBreak'),true);
 assert.equal(matchesSupportEquip(non,'limitBreak'),false);
 assert.equal(matchesSupportEquip(non,'any'),true);
 assert.equal(matchesSupportEquip(item(),'any',false),true);
 assert.equal(matchesSupportEquip(item(),null),true);
});
