import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {newBattle,assertEditable,turnsOf} from './model';
import {GlobalAdvancedStrategy as AdvancedStrategy} from './GlobalAdvancedStrategy';
import {advancedStrategyOf,defaultAdvancedStrategy,patchAdvancedStrategy,parseNativeStrategyOverrides,strategyInteger,advancedStrategyIssues,preferredAttackerMode} from './advancedStrategy';

test('new documents carry independent unified strategy defaults',()=>{
 const first=newBattle(),second=newBattle();
 assert.deepEqual(first.maaAdvancedStrategy,{preset:'balanced',preferredAttacker:'',openingHand:{enabled:false,servantId:null,servantSlot:null,requiredColors:['B','Q'],maxWaitTurns:2},nativeStrategyOverrides:{},autoServantSkills:true,maxTurns:40});
 first.maaAdvancedStrategy.openingHand.requiredColors.push('A');(first.maaAdvancedStrategy.nativeStrategyOverrides as Record<string,unknown>).preferred_slots=[2];
 assert.deepEqual(second.maaAdvancedStrategy,defaultAdvancedStrategy());
});

test('viewing old and partial plans or focusing unchanged controls never writes fields',()=>{
 for(const data of [{actions:[]},{team:null,actions:null},{maaAdvancedStrategy:{maxTurns:70,future:{keep:null}}}]){
  const before=structuredClone(data);assertEditable(data);assert.doesNotThrow(()=>turnsOf(data));
  const projected=advancedStrategyOf(data);
  assert.equal(projected.preset,'balanced');assert.equal(projected.autoServantSkills,true);
  patchAdvancedStrategy(data,{nativeStrategyOverrides:{}});patchAdvancedStrategy(data,{openingHand:{servantSlot:null,requiredColors:['B','Q']}});patchAdvancedStrategy(data,{preferredAttacker:''});
  assert.deepEqual(data,before);
  projected.openingHand.requiredColors.push('A');assert.deepEqual(data,before);
 }
});

test('editing writes only explicit strategy fields without inventing a team or actions',()=>{
 const data={future:{keep:[null,false]}};
 patchAdvancedStrategy(data,{maxTurns:65,openingHand:{enabled:true,servantSlot:2,requiredColors:['Q','Q']}});
 assert.equal('team' in data,false);assert.equal('actions' in data,false);
 const saved=JSON.parse(JSON.stringify(data));
 assert.deepEqual(saved.maaAdvancedStrategy,{maxTurns:65,openingHand:{enabled:true,servantSlot:2,requiredColors:['Q','Q']}});
 assert.deepEqual(advancedStrategyOf(saved),{...defaultAdvancedStrategy(),maxTurns:65,openingHand:{...defaultAdvancedStrategy().openingHand,enabled:true,servantSlot:2,requiredColors:['Q','Q']}});
 assert.deepEqual(saved.future,{keep:[null,false]});
});

test('independent strategy edits retain JSON-only opening colors without materializing unrelated defaults',()=>{
 const original={team:{onFieldSvts:[{svtId:100200,equip1:{id:42,lv:80}}]},actions:[{type:'attack',attacks:[]}],future:{keep:[null,false]},maaAdvancedStrategy:{
  openingHand:{enabled:true,futureOpening:{keep:true}},
  nativeStrategyOverrides:{opening_hand:{servant_id:'505500',required_colors:['A','A'],futureNative:{keep:[1,null]}}},
  futureStrategy:{keep:'保留'},
 }};
 assert.deepEqual(advancedStrategyOf(original).openingHand.requiredColors,['A','A']);
 const html=renderToStaticMarkup(createElement(AdvancedStrategy,{data:original,onChange:()=>{},onError:()=>{}}));
 assert.match(html,/aria-label="开局等牌所需牌色"[^>]*value="A, A"/);
 for(const patch of [{preferredAttacker:'702900'},{maxTurns:65},{openingHand:{servantId:'702900'}},{openingHand:{servantSlot:2}}]){
  const data=structuredClone(original),before=structuredClone(data);
  patchAdvancedStrategy(data,patch);
  const expected={...before.maaAdvancedStrategy,...patch,openingHand:{...before.maaAdvancedStrategy.openingHand,...(patch.openingHand??{})}};
  assert.deepEqual(data.maaAdvancedStrategy,expected);
  assert.equal('requiredColors' in data.maaAdvancedStrategy.openingHand,false);
  assert.deepEqual(data.maaAdvancedStrategy.nativeStrategyOverrides,before.maaAdvancedStrategy.nativeStrategyOverrides);
  assert.deepEqual(data.team,before.team);assert.deepEqual(data.actions,before.actions);assert.deepEqual(data.future,before.future);
 }
 const selected={...structuredClone(original),maaAdvancedStrategy:{...structuredClone(original.maaAdvancedStrategy),openingHand:{...structuredClone(original.maaAdvancedStrategy.openingHand),servantId:'100200'}}};
 const beforeClear=structuredClone(selected);
 patchAdvancedStrategy(selected,{openingHand:{servantId:null}});
 assert.deepEqual(selected.maaAdvancedStrategy,{...beforeClear.maaAdvancedStrategy,openingHand:{...beforeClear.maaAdvancedStrategy.openingHand,servantId:null}});
 assert.deepEqual(selected.team,beforeClear.team);assert.deepEqual(selected.actions,beforeClear.actions);
});

test('explicit opening colors can override JSON with the displayed default while retaining unknown fields',()=>{
 const data={maaAdvancedStrategy:{openingHand:{enabled:true,futureOpening:{keep:true}},nativeStrategyOverrides:{opening_hand:{servant_id:'505500',required_colors:['A','A'],futureNative:'保留'}},futureStrategy:{keep:[null,false]}}};
 const before=structuredClone(data);
 patchAdvancedStrategy(data,{openingHand:{requiredColors:['B','Q']}});
 assert.deepEqual(data.maaAdvancedStrategy,{...before.maaAdvancedStrategy,openingHand:{...before.maaAdvancedStrategy.openingHand,requiredColors:['B','Q']}});
 assert.deepEqual(data.maaAdvancedStrategy.nativeStrategyOverrides,before.maaAdvancedStrategy.nativeStrategyOverrides);
});

test('adding an attacker preference to an old plan does not add an opening hand object',()=>{
 const data={team:{onFieldSvts:[{svtId:100200}]},actions:[],future:{keep:true}};
 const before=structuredClone(data);
 patchAdvancedStrategy(data,{preferredAttacker:'702900'});
 assert.deepEqual(data,{...before,maaAdvancedStrategy:{preferredAttacker:'702900'}});
 assert.equal('openingHand' in (data as Record<string,any>).maaAdvancedStrategy,false);
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
 assert.match(html,/value="balanced" selected=""/);assert.doesNotMatch(html,/选择开局等牌目标从者/);
 assert.match(html,/type="checkbox"[^>]*checked=""/);
 const withOpening=renderToStaticMarkup(createElement(AdvancedStrategy,{data:{maaAdvancedStrategy:{openingHand:{enabled:true,servantSlot:2}}},onChange:()=>{},onError:()=>{}}));
 assert.match(withOpening,/aria-label="选择开局等牌目标从者"[^>]*aria-haspopup="dialog"/);assert.doesNotMatch(withOpening,/开局等牌目标从者 ID/);assert.match(withOpening,/开局等牌所需牌色/);
 for(const servantId of ['','0',605300]){
  const imported=renderToStaticMarkup(createElement(AdvancedStrategy,{data:{maaAdvancedStrategy:{openingHand:{enabled:true,servantId}}},onChange:()=>{},onError:()=>{}}));
  assert.match(imported,/aria-label="清除开局等牌目标从者"/);
 }
});

test('attacker identity types survive editing, drafts and undo without changing JSON preferences',()=>{
 const data={maaAdvancedStrategy:{nativeStrategyOverrides:{preferred_slots:[2,3],preferred_servant_ids:['605300'],future:{keep:true}}}};
 const original=structuredClone(data);
 assert.equal(advancedStrategyOf(data).preferredAttacker,'');
 for(const preferredAttacker of [2,'2','605300','']){
  patchAdvancedStrategy(data,{preferredAttacker});
  const restored=JSON.parse(JSON.stringify(data));
  assert.equal(restored.maaAdvancedStrategy.preferredAttacker,preferredAttacker);
  assert.deepEqual(restored.maaAdvancedStrategy.nativeStrategyOverrides,original.maaAdvancedStrategy.nativeStrategyOverrides);
  assert.deepEqual(advancedStrategyIssues(restored),[]);
 }
 assert.equal(preferredAttackerMode(2),'slot');assert.equal(preferredAttackerMode('2'),'servant');
 assert.equal(preferredAttackerMode(advancedStrategyOf(original).preferredAttacker),'inherit');
 for(const invalid of ['0','01',0,4,null,true,[],{},'broken','1000000000000']){
  const draft={maaAdvancedStrategy:{preferredAttacker:invalid}};
  assert.ok(advancedStrategyIssues(draft).some(issue=>issue.includes('主输出')));
  assert.deepEqual(JSON.parse(JSON.stringify(draft)).maaAdvancedStrategy.preferredAttacker,invalid);
 }
});

test('attacker controls render a portrait picker while preserving imported identities and missing catalogue IDs',()=>{
 const catalog={servants:[{id:605300,name:'斯卡哈·斯卡蒂',image:'/605300.png',collectionNo:215,className:'术',aliases:[605399]}],equips:[],mysticCodes:[]};
 const render=(preferredAttacker:unknown)=>renderToStaticMarkup(createElement(AdvancedStrategy,{data:{team:{backupSvts:[{svtId:605399}]},maaAdvancedStrategy:{preferredAttacker}},catalog,onChange:()=>{},onError:()=>{},onChooseServant:()=>{}}));
 const inherited=render('');assert.match(inherited,/value="inherit" selected=""/);assert.doesNotMatch(inherited,/aria-label="选择主输出从者"/);
 const position=render(3);assert.match(position,/aria-label="主输出位置"/);assert.match(position,/value="3" selected=""/);
 const identity=render('605300');assert.match(identity,/<button[^>]*aria-label="选择主输出从者"[^>]*aria-haspopup="dialog"/);assert.match(identity,/src="\/605300.png"/);assert.match(identity,/斯卡哈·斯卡蒂/);assert.match(identity,/ID 605300/);assert.doesNotMatch(identity,/搜索主输出从者|<select[^>]*aria-label="主输出从者"/);
 const alias=render('605399');assert.match(alias,/斯卡哈·斯卡蒂/);assert.match(alias,/ID 605399/);
 const missing=render('999999');assert.match(missing,/从者 #999999/);assert.match(missing,/目录中暂无资料 · ID 999999/);
 const pending=render('0');assert.match(pending,/请选择主输出从者/);assert.match(pending,/尚未选择主输出从者/);
});
