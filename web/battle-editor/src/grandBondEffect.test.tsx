import test from 'node:test';
import assert from 'node:assert/strict';
import type {Json} from './model';
import {grandBondEffect,grandBondEffectLabel,setGrandBondEffect} from './grandBondEffect';

test('class-board effect remains independent of a named bond CE and preserves unspecified imports',()=>{
 for(const [value,expected] of [[true,'np50'],[false,'original'],[undefined,'any']] as const){
  const member:Json={grandSvt:true,classBoardData:{grandBondEquipSkillChange:value,future:1}};
  const before=structuredClone(member);
  assert.equal(grandBondEffect(member),expected);assert.deepEqual(member,before);
 }
 assert.equal(grandBondEffect({grandSvt:true,equip2:{id:9310000}}),'any');
 assert.equal(grandBondEffectLabel({classBoardData:{grandBondEquipSkillChange:true}}),'NP 50% 替换效果');
});

test('effect edits preserve original bond CE, unrelated class-board fields, and explicit false',()=>{
 const member:Json={svtId:100100,grandSvt:true,equip1:{id:10},equip2:{id:20,future:['legacy']},equip3:{id:30},classBoardData:{future:{keep:true}},unknown:null};
 const before=structuredClone(member);
 setGrandBondEffect(member,'np50');assert.equal(member.classBoardData.grandBondEquipSkillChange,true);
 setGrandBondEffect(member,'original');assert.equal(member.classBoardData.grandBondEquipSkillChange,false);
 assert.equal(grandBondEffect(member),'original');
 setGrandBondEffect(member,'any');assert.deepEqual(member,before);
});

test('new effect-only requirements need no equip2 ID and invalid edits do not mutate data',()=>{
 const member:Json={grandSvt:true};setGrandBondEffect(member,'any');assert.deepEqual(member,{grandSvt:true});
 setGrandBondEffect(member,'np50');assert.deepEqual(member,{grandSvt:true,classBoardData:{grandBondEquipSkillChange:true}});
 for(const value of [{grandSvt:false},{grandSvt:true,classBoardData:'invalid'}]){
  const before=structuredClone(value);assert.throws(()=>setGrandBondEffect(value,'np50'));assert.deepEqual(value,before);
 }
});
