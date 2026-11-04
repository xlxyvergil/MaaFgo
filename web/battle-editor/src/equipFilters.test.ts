import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {filterCatalog} from './model';
import type {Catalog,Equip} from './model';
import {equipAdvancedFilterCount,equipEffectSummary,matchEquipAdvanced,newEquipAdvancedFilters,normalizeEquipAdvancedFilters,sortEquips} from './equipFilters';
import type {EquipAdvancedFilters,EquipEffectSet,EquipFilterMetadata} from './equipFilters';

const effects=(tags:string[]=[],initialNp:number|null=0):EquipEffectSet=>({tags,initialNp,effects:[]});
const meta=(extra:Partial<EquipFilterMetadata>={}):EquipFilterMetadata=>({kind:'normal',kindLabel:'普通礼装',source:'normal',sourceLabel:'常驻',atkMax:2000,hpMax:0,statType:'atk',base:effects(['buster'],30),mlb:effects(['buster','npDamage'],50),...extra});
const equip=(id:number,extra:Partial<Equip>={}):Equip=>({id,name:`礼装${id}`,image:`${id}.png`,recognizable:true,cost:12,rarity:5,filters:meta(),...extra});
const advanced=(extra:Partial<EquipAdvancedFilters>={}):EquipAdvancedFilters=>({...newEquipAdvancedFilters(),...extra});
const catalog=(equips:Equip[]):Catalog=>({servants:[],equips,mysticCodes:[],equipFilters:{schemaVersion:1,kinds:[{id:'normal',label:'普通礼装'},{id:'bond',label:'羁绊礼装'},{id:'unknown',label:'未知'}],sources:[{id:'normal',label:'常驻'},{id:'eventReward',label:'活动奖励'}],effects:[{id:'buster',label:'Buster 性能',group:'card'},{id:'npDamage',label:'宝具威力',group:'damage'},{id:'bondGain',label:'羁绊收益',group:'bonus'}]}});

test('base/MLB are query bases independent from inventory or a configured CE limitBreak',()=>{
 const item=equip(1),original=structuredClone(item);
 assert.equal(matchEquipAdvanced(item,advanced({initialNpMin:50})).matches,false);
 assert.equal(matchEquipAdvanced(item,advanced({basis:'mlb',initialNpMin:50})).matches,true);
 assert.equal(matchEquipAdvanced(item,advanced({effects:['npDamage']})).matches,false);
 assert.equal(matchEquipAdvanced(item,advanced({basis:'mlb',effects:['npDamage']})).matches,true);
 const cat=catalog([item]);cat.inventory={equips:[1]};
 assert.deepEqual(filterCatalog('equip',cat,{owned:true,equipAdvanced:advanced({basis:'mlb',initialNpMin:50})}),[item]);
 assert.deepEqual(item,original);
});

test('compact summary avoids repeating initial NP or crowding rich zero-NP effects',()=>{
 const item=equip(1,{filters:meta({base:effects(['npStart','buster'],80)})}),cat=catalog([item]);cat.equipFilters!.effects.push({id:'npStart',label:'初始 NP'});
 assert.equal(equipEffectSummary(item,'base',cat).text,'初始 NP 80% · Buster 性能');
 item.filters!.base.initialNp=0;item.filters!.base.tags=['buster'];assert.equal(equipEffectSummary(item,'base',cat).text,'Buster 性能');
 assert.equal(equipEffectSummary(item,'base',cat).initialNp,0);
});

test('compact summaries prefer indexed numeric descriptions while leaving unclassified effects to details',()=>{
 const item=equip(1,{filters:meta({base:{tags:['npTurn','npDamage'],initialNp:0,effects:[{tags:['npTurn'],description:'每回合 NP 8%',conditional:false,target:'自身',value:8,unit:'%'},{tags:['npDamage'],description:'宝具威力 60%',conditional:false,target:'自身',value:60,unit:'%'},{tags:[],description:'每回合 HP 减少 500',conditional:false,target:'自身',value:-500,unit:'HP'}],details:['每回合获得8%NP，宝具威力60%，HP减少500']}})});
 const summary=equipEffectSummary(item,'base',catalog([item]));
 assert.equal(summary.text,'每回合 NP 8% · 宝具威力 60%');assert.equal(summary.lines.length,3);assert.equal(item.filters!.base.details!.length,1);
});

test('all/any effects combine with independent NP/cost/stat requirements without mutating inputs',()=>{
 const item=equip(1),filters=advanced({effects:['buster','npDamage']});
 assert.equal(matchEquipAdvanced(item,filters).matches,false);assert.deepEqual(filters.effects,['buster','npDamage']);
 assert.equal(matchEquipAdvanced(item,{...filters,effectMode:'any'}).matches,true);
 assert.equal(matchEquipAdvanced(item,{...filters,basis:'mlb',initialNpMin:50,costMax:12,statType:'atk'}).matches,true);
 assert.equal(matchEquipAdvanced(item,{...filters,basis:'mlb',initialNpMin:51}).matches,false);
 assert.equal(matchEquipAdvanced(item,{...filters,basis:'mlb',costMax:11}).matches,false);
 assert.equal(matchEquipAdvanced(item,{...filters,basis:'mlb',statType:'hp'}).matches,false);
 assert.equal(matchEquipAdvanced(item,advanced({costMin:13,costMax:12})).matches,false);
});

test('conditional effects are opt-in, retain explanations and never inflate unconditional initial NP',()=>{
 const conditional={tags:['npDamage','bondGain'],description:'前排存在指定从者时，宝具威力提升并获得额外羁绊',conditional:true,target:'party',condition:'指定从者在场'},
  conditionalCharge={tags:['initialNp'],description:'符合条件时额外获得50% NP',conditional:true,target:'self',condition:'场地满足条件'};
 const item=equip(2,{filters:meta({base:{tags:['buster'],initialNp:0,effects:[conditional,conditionalCharge]}})});
 assert.equal(matchEquipAdvanced(item,advanced({effects:['npDamage']})).matches,false);
 const match=matchEquipAdvanced(item,advanced({effects:['buster','npDamage'],includeConditional:true}));assert.equal(match.matches,true);assert.equal(match.conditionalOnly,true);
 assert.equal(matchEquipAdvanced(item,advanced({effects:['buster','npDamage'],effectMode:'any',includeConditional:true})).conditionalOnly,false);
 assert.equal(matchEquipAdvanced(item,advanced({includeConditional:true,initialNpMin:1})).matches,false);
 const summary=equipEffectSummary(item,'base',catalog([item]));
 assert.deepEqual(summary.labels,['Buster 性能']);assert.deepEqual(summary.conditionalLabels,['宝具威力','羁绊收益','initialNp']);
 assert.deepEqual(summary.lines[0],{text:conditional.description,conditional:true,target:'party',condition:'指定从者在场'});assert.match(summary.text,/条件效果/);
});

test('bond craft-essence kind and bond reward gain are independent dimensions',()=>{
 const bond=equip(1,{filters:meta({kind:'bond',kindLabel:'羁绊礼装',source:'bondReward',sourceLabel:'羁绊奖励',bondOwner:100100})}),gain=equip(2,{filters:meta({base:effects(['bondGain'])})}),cat=catalog([bond,gain]);
 assert.deepEqual(filterCatalog('equip',cat,{equipKind:'bond'}),[bond]);
 assert.deepEqual(filterCatalog('equip',cat,{equipAdvanced:advanced({effects:['bondGain']})}),[gain]);
 assert.deepEqual(filterCatalog('equip',cat,{equipKind:'bond',equipAdvanced:advanced({effects:['bondGain']})}),[]);
});

test('bond CE search also resolves its owner names aliases and IDs without changing indexed records',()=>{
 const snow=equip(9300070,{name:'雪之城',filters:meta({kind:'bond',bondOwner:700100})}),other=equip(2),cat=catalog([snow,other]);
 cat.servants=[{id:700100,name:'赫拉克勒斯',nameJp:'ヘラクレス',aliases:[9937001],searchAliases:['海叔','Hercules']}];
 const before=structuredClone(cat);
 for(const query of ['雪之城','赫拉克勒斯','ヘラクレス','海叔','hErCuLeS','700100','9937001'])assert.deepEqual(filterCatalog('equip',cat,{query}),[snow]);
 assert.deepEqual(filterCatalog('equip',cat,{query:'700100',common:true}),[]);assert.deepEqual(cat,before);
 cat.servants=[];assert.deepEqual(filterCatalog('equip',cat,{query:'700100'}),[snow]);
});

test('unknown facts never pass zero/range gates and remain explainable separately from known zero',()=>{
 const unknown=equip(1,{filters:undefined,cost:null}),knownZero=equip(2,{filters:meta({base:effects([],0),atkMax:0,hpMax:0,statType:'none'}),cost:0});
 assert.equal(matchEquipAdvanced(unknown,newEquipAdvancedFilters()).matches,true);
 assert.deepEqual(matchEquipAdvanced(unknown,advanced({initialNpMax:0,costMax:0,statType:'atk',effects:['buster']})),{matches:false,conditionalOnly:false,matchedEffects:[],unknown:['effects','initialNp','cost','statType']});
 assert.equal(matchEquipAdvanced(unknown,advanced({statType:'unknown'})).matches,true);
 assert.equal(matchEquipAdvanced(knownZero,advanced({initialNpMax:0,costMax:0,statType:'none'})).matches,true);
 assert.equal(equipEffectSummary(unknown).known,false);assert.equal(equipEffectSummary(unknown).initialNp,null);assert.match(equipEffectSummary(unknown).text,/未收录/);assert.doesNotMatch(equipEffectSummary(unknown).text,/0%/);
 const partial=equip(3,{filters:meta({base:effects([],null)})});assert.equal(equipEffectSummary(partial).initialNp,null);assert.match(equipEffectSummary(partial).text,/未知/);
 assert.deepEqual(filterCatalog('equip',catalog([unknown,knownZero]),{equipKind:'unknown',equipSource:'unknown'}),[unknown]);
});

test('quick filters intersect recognition/common/owned/rarity/search without silently clearing any restriction',()=>{
 const items=[equip(1,{name:'活动 A',common:true,filters:meta({source:'eventReward'})}),equip(2,{name:'活动 B',common:false,filters:meta({source:'eventReward'})}),equip(3,{recognizable:false,common:true,filters:meta({source:'eventReward'})}),equip(4,{image:null,common:true,filters:meta({source:'eventReward'})})],cat=catalog(items);cat.inventory={equips:[2]};
 assert.deepEqual(filterCatalog('equip',cat,{equipSource:'eventReward',common:true}),[items[0]]);
 assert.deepEqual(filterCatalog('equip',cat,{equipSource:'eventReward',common:true,owned:true}),[]);
 assert.deepEqual(filterCatalog('equip',cat,{equipSource:'eventReward',common:false,owned:true,rarity:'5',query:'活动'}),[items[1]]);
});

test('normalization rejects malformed primitives, retains zero bounds and explains contradictory ranges as no matches',()=>{
 for(const value of [null,[],0,true,'bad'])assert.deepEqual(normalizeEquipAdvancedFilters(value),newEquipAdvancedFilters());
 const normalized=normalizeEquipAdvancedFilters({effects:['buster','buster',false,'bad',' x '],basis:'holding',effectMode:'both',includeConditional:'true',statType:'false',initialNpMin:0,initialNpMax:0,costMin:-1,costMax:NaN},catalog([]));
 assert.deepEqual(normalized,{...newEquipAdvancedFilters(),effects:['buster'],initialNpMin:0,initialNpMax:0});
 assert.deepEqual(normalizeEquipAdvancedFilters({effects:['future']}),advanced({effects:['future']}));
 assert.equal(equipAdvancedFilterCount(advanced({effects:['buster','npDamage'],basis:'mlb',includeConditional:true,initialNpMin:0,costMin:0,costMax:12,statType:'atk'})),5);
 const contradiction=normalizeEquipAdvancedFilters({initialNpMin:100,initialNpMax:30});assert.equal(matchEquipAdvanced(equip(1),contradiction).matches,false);
});

test('sorts numeric IDs/collection/rarity/COST across the full result with unknowns last in both directions',()=>{
 const rows=[equip(40,{collectionNo:0,cost:null}),equip(20,{collectionNo:9,cost:12}),equip(10,{collectionNo:3,cost:0,rarity:0}),equip(30,{collectionNo:9,cost:12})],original=[...rows];
 assert.deepEqual(sortEquips(rows,'id','asc').map(row=>row.id),[10,20,30,40]);
 assert.deepEqual(sortEquips(rows,'collectionNo','desc').map(row=>row.id),[20,30,10,40]);
 assert.deepEqual(sortEquips(rows,'cost','asc').map(row=>row.id),[10,20,30,40]);
 assert.deepEqual(sortEquips(rows,'cost','desc').map(row=>row.id),[20,30,10,40]);
 assert.deepEqual(sortEquips(rows,'rarity','asc').map(row=>row.id),[10,20,30,40]);assert.deepEqual(rows,original);
});

test('bundled real CE index agrees with frontend gates, numeric summaries, bond type and support conditions',()=>{
 const index=JSON.parse(readFileSync(new URL('../../../agent/battle_editor/data/equip_filters.json',import.meta.url),'utf8')) as {equips:Record<string,EquipFilterMetadata>};
 const real=(id:number)=>{assert.ok(index.equips[id],`missing bundled filter facts for ${id}`);return equip(id,{filters:index.equips[id]});};
 const kaleidoscope=real(9400340),cosmos=real(9400400),grail=real(9400480),mona=real(9400780),snow=real(9300070),bella=real(9403690);
 assert.equal(matchEquipAdvanced(kaleidoscope,advanced({initialNpMin:100})).matches,false);assert.equal(matchEquipAdvanced(kaleidoscope,advanced({basis:'mlb',initialNpMin:100})).matches,true);
 assert.equal(matchEquipAdvanced(cosmos,advanced({effects:['npTurn'],initialNpMin:1})).matches,false);assert.match(equipEffectSummary(cosmos).text,/8%/);assert.match(equipEffectSummary(cosmos,'mlb').text,/10%/);
 assert.match(equipEffectSummary(grail).text,/60%/);assert.match(equipEffectSummary(grail,'mlb').text,/80%/);assert.equal(matchEquipAdvanced(grail,advanced({effects:['heal']})).matches,false);
 assert.match(equipEffectSummary(mona).text,/2%/);assert.match(equipEffectSummary(mona,'mlb').text,/10%/);
 assert.equal(snow.filters!.kind,'bond');assert.equal(snow.filters!.bondOwner,700100);assert.equal(matchEquipAdvanced(snow,advanced({effects:['bond'],includeConditional:true})).matches,false);
 assert.equal(matchEquipAdvanced(snow,advanced({effects:['guts']})).matches,false);assert.equal(matchEquipAdvanced(snow,advanced({effects:['guts'],includeConditional:true})).conditionalOnly,true);
 const supportSummary=equipEffectSummary(bella,'mlb');assert.match(supportSummary.text,/5%/);assert.doesNotMatch(supportSummary.text,/15%/);assert.ok(supportSummary.lines.some(line=>line.conditional&&line.condition?.includes('助战')));
});
