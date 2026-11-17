import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {prepareImportedBattle} from './chaldeaImport';
import type {Json} from './model';

const sample=()=>JSON.parse(readFileSync(new URL('../../../tests/fixtures/battle_editor/chaldea_94255.json',import.meta.url),'utf-8')) as Json;

test('94255 imports without its simulator skip queue and keeps the original share intact',()=>{
 const source=sample(),before=structuredClone(source);
 const imported=prepareImportedBattle(source);
 assert.equal(imported.delegate,undefined);
 const expected=structuredClone(before);delete expected.delegate;
 assert.deepEqual(imported,expected);
 assert.deepEqual(source,before);
 assert.strictEqual(prepareImportedBattle(imported),imported);
});

test('cleanup preserves skill, NP, swap and unknown queues including nulls and zero values',()=>{
 const source=sample();Object.assign(source.delegate,{
  skillActSelectSelections:[0,2],tdTypeChanges:['buster',1],replaceMemberIndexes:[[0,2]],futureChoices:{keep:[null,0,false]},
 });
 const before=structuredClone(source),imported=prepareImportedBattle(source);
 const expected=structuredClone(before);delete expected.delegate.actWeightSelections;
 assert.deepEqual(imported,expected);
 assert.deepEqual(source,before);
 imported.delegate.replaceMemberIndexes[0][0]=1;
 assert.deepEqual(source,before);
});

test('actual or malformed choices remain visible to validation without shifting queue positions',()=>{
 for(const choices of [[1],[-1,1],[0],[-1,0],[true],['-1'],[-2],[],null,{},'-1']){
  const source=sample();source.delegate.actWeightSelections=choices;
  const before=structuredClone(source);
  assert.strictEqual(prepareImportedBattle(source),source);
  assert.deepEqual(source,before);
 }
});

test('missing or malformed delegate and unrelated simulator settings are preserved',()=>{
 for(const delegate of [undefined,null,[],false,3,'invalid',{}, {future:[null,false]}]){
  const source=sample();source.delegate=delegate;
  const before=structuredClone(source);
  assert.strictEqual(prepareImportedBattle(source),source);
  assert.deepEqual(source,before);
 }
});
