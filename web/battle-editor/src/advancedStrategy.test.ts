import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {newBattle,assertEditable,turnsOf} from './model';
import {GlobalAdvancedStrategy as AdvancedStrategy} from './GlobalAdvancedStrategy';
import {advancedStrategyOf,defaultAdvancedStrategy,patchAdvancedStrategy,parseNativeStrategyOverrides,strategyInteger,advancedStrategyIssues} from './advancedStrategy';

test('new documents carry independent unified strategy defaults',()=>{
 const first=newBattle(),second=newBattle();
 assert.deepEqual(first.maaAdvancedStrategy,{preset:'balanced',openingHand:{enabled:false,servantId:null,servantSlot:null,requiredColors:['B','Q'],maxWaitTurns:2},nativeStrategyOverrides:{},autoServantSkills:true,maxTurns:40});
 first.maaAdvancedStrategy.openingHand.requiredColors.push('A');(first.maaAdvancedStrategy.nativeStrategyOverrides as Record<string,unknown>).preferred_slots=[2];
 assert.deepEqual(second.maaAdvancedStrategy,defaultAdvancedStrategy());
});

test('viewing old and partial plans or focusing unchanged controls never writes fields',()=>{
 for(const data of [{actions:[]},{team:null,actions:null},{maaAdvancedStrategy:{maxTurns:70,future:{keep:null}}}]){
  const before=structuredClone(data);assertEditable(data);assert.doesNotThrow(()=>turnsOf(data));
  const projected=advancedStrategyOf(data);
  assert.equal(projected.preset,'balanced');assert.equal(projected.autoServantSkills,true);
  patchAdvancedStrategy(data,{nativeStrategyOverrides:{}});patchAdvancedStrategy(data,{openingHand:{servantSlot:null,requiredColors:['B','Q']}});
  assert.deepEqual(data,before);
  projected.openingHand.requiredColors.push('A');assert.deepEqual(data,before);
 }
});

test('editing materializes global strategy without inventing a team or actions',()=>{
 const data={future:{keep:[null,false]}};
 patchAdvancedStrategy(data,{maxTurns:65,openingHand:{enabled:true,servantSlot:2,requiredColors:['Q','Q']}});
 assert.equal('team' in data,false);assert.equal('actions' in data,false);
 const saved=JSON.parse(JSON.stringify(data));
 assert.deepEqual(advancedStrategyOf(saved),{...defaultAdvancedStrategy(),maxTurns:65,openingHand:{...defaultAdvancedStrategy().openingHand,enabled:true,servantSlot:2,requiredColors:['Q','Q']}});
 assert.deepEqual(saved.future,{keep:[null,false]});
});

test('nested unknown fields survive independent edits and JSON roundtrips',()=>{
 const data={maaAdvancedStrategy:{preset:'brave',future:{keep:null},openingHand:{futureOpening:{keep:true},servantId:605300},nativeStrategyOverrides:{futureNative:{keep:[1,2]},brave_chain_weight:20}},actions:[]};
 const before=structuredClone(data);
 patchAdvancedStrategy(data,{openingHand:{maxWaitTurns:3},autoServantSkills:false});
 const restored=advancedStrategyOf(JSON.parse(JSON.stringify(data)));
 assert.deepEqual(restored.future,before.maaAdvancedStrategy.future);assert.deepEqual(restored.openingHand.futureOpening,{keep:true});
 assert.deepEqual(restored.nativeStrategyOverrides,before.maaAdvancedStrategy.nativeStrategyOverrides);
 assert.equal(restored.openingHand.servantId,605300);assert.equal(restored.openingHand.maxWaitTurns,3);
});

test('separate loaded documents have independent strategies and editing is undoable by snapshot',()=>{
 const old={actions:[]},before=structuredClone(old),other={maaAdvancedStrategy:{preset:'np',maxTurns:90}};
 patchAdvancedStrategy(old,{preset:'mighty',maxTurns:12});
 assert.equal(advancedStrategyOf(old).maxTurns,12);assert.equal(advancedStrategyOf(other).maxTurns,90);
 assert.equal(advancedStrategyOf(before).preset,'balanced');assert.equal('maaAdvancedStrategy' in before,false);
});

test('malformed imported strategy remains visible and is never silently replaced',()=>{
 for(const raw of [null,[],true,'broken']){
  const data={maaAdvancedStrategy:raw},before=structuredClone(data);
  assert.ok(advancedStrategyIssues(data).length);assert.throws(()=>patchAdvancedStrategy(data,{maxTurns:50}));assert.deepEqual(data,before);
 }
 const data={maaAdvancedStrategy:{openingHand:[]}},before=structuredClone(data);
 assert.throws(()=>patchAdvancedStrategy(data,{maxTurns:50}));assert.deepEqual(data,before);
});

test('controls reject invalid numeric ranges and non-object override JSON',()=>{
 assert.deepEqual(parseNativeStrategyOverrides(''),{});assert.deepEqual(parseNativeStrategyOverrides('{"preferred_slots":[2]}'),{preferred_slots:[2]});
 for(const text of ['[1]','null','false','1','{"broken"'])assert.throws(()=>parseNativeStrategyOverrides(text));
 for(const text of ['','0','101','1.5','NaN','Infinity'])assert.throws(()=>strategyInteger(text,1,100,'最大战斗回合'));
 assert.equal(strategyInteger('40',1,100,'最大战斗回合'),40);assert.equal(strategyInteger('0',0,5,'等待回合'),0);
 assert.deepEqual(advancedStrategyIssues({maaAdvancedStrategy:{openingHand:{servantId:605300,requiredColors:['Q','Q']}}}),[]);
 assert.equal(advancedStrategyIssues({maaAdvancedStrategy:{maxTurns:false,openingHand:{maxWaitTurns:6},nativeStrategyOverrides:[]}}).length,3);
});

test('invalid edited values stay in the draft so tab changes cannot save an older valid value',()=>{
 const data={actions:[]};
 patchAdvancedStrategy(data,{nativeStrategyOverrides:'{"unfinished"',maxTurns:'101'});
 const restored=JSON.parse(JSON.stringify(data));
 assert.equal(restored.maaAdvancedStrategy.nativeStrategyOverrides,'{"unfinished"');
 assert.equal(restored.maaAdvancedStrategy.maxTurns,'101');
 assert.equal(advancedStrategyIssues(restored).length,2);
 patchAdvancedStrategy(restored,{nativeStrategyOverrides:{preferred_slots:[2]},maxTurns:60});
 assert.deepEqual(advancedStrategyIssues(restored),[]);
});

test('global strategy renders collapsed without a selected action or formation',()=>{
 const html=renderToStaticMarkup(createElement(AdvancedStrategy,{data:{actions:[]},onChange:()=>{},onError:()=>{}}));
 assert.match(html,/<details[^>]+id="global-advanced-strategy"/);assert.doesNotMatch(html,/<details[^>]+\bopen(?:=|\s|>)/);
 assert.match(html,/全局高级策略/);assert.match(html,/使用编辑器\/chaldea配置/);assert.match(html,/value="40"/);
 assert.match(html,/value="balanced" selected=""/);assert.doesNotMatch(html,/开局等牌目标从者 ID/);
 assert.match(html,/type="checkbox"[^>]*checked=""/);
 const withOpening=renderToStaticMarkup(createElement(AdvancedStrategy,{data:{maaAdvancedStrategy:{openingHand:{enabled:true,servantSlot:2}}},onChange:()=>{},onError:()=>{}}));
 assert.match(withOpening,/开局等牌目标从者 ID/);assert.match(withOpening,/开局等牌所需牌色/);
});
