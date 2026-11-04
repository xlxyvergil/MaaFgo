import test from 'node:test';
import assert from 'node:assert/strict';
import {BOND_OPTIMIZER_STORAGE_KEY,defaultBondOptimizerPreferences,loadBondOptimizerPreferences,normalizeBondOptimizerPreferences,saveBondOptimizerPreferences} from './bondOptimizerPreferences';
import type {BondOptimizerPreferences} from './bondOptimizerPreferences';
import {PARTY_COST_STORAGE_KEY} from './partyCost';

const preferences=(extra:Partial<BondOptimizerPreferences>={}):BondOptimizerPreferences=>({...defaultBondOptimizerPreferences(),...extra});
function storage(raw?:string){const entries=new Map<string,string>();if(raw!==undefined)entries.set(BOND_OPTIMIZER_STORAGE_KEY,raw);return {entries,getItem:(key:string)=>entries.get(key)??null,setItem:(key:string,value:string)=>{entries.set(key,value);}};}

test('fresh optimizer preferences use explicit defaults and independent exclusion lists',()=>{
 assert.deepEqual(defaultBondOptimizerPreferences(),{version:1,bondBase:1200,preferredRarity:5,ownedServantsOnly:true,ownedEquipsOnly:true,modifyEquips:false,confirmedMlbOnly:false,reservedSupportSlot:null,excludeServantIds:[]});
 const first=defaultBondOptimizerPreferences(),second=defaultBondOptimizerPreferences();first.excludeServantIds.push(100100);assert.deepEqual(second.excludeServantIds,[]);assert.deepEqual(loadBondOptimizerPreferences(storage()),second);
});
test('bond base accepts integer endpoints but rejects coercion fractions and out-of-range numbers',()=>{
 for(const bondBase of [1,1200,999999,1000000])assert.equal(normalizeBondOptimizerPreferences(preferences({bondBase})).bondBase,bondBase);
 for(const bondBase of [0,-1,1000001,NaN,Infinity,1.5,'1200',true,null,[],{}]){
  const normalized=normalizeBondOptimizerPreferences({...preferences({preferredRarity:0}),bondBase});assert.equal(normalized.bondBase,1200);assert.equal(normalized.preferredRarity,0);
 }
});
test('rarity and reserved support require real 0-based integers and keep null unrestricted',()=>{
 for(let value=0;value<=5;value++){const normalized=normalizeBondOptimizerPreferences(preferences({preferredRarity:value,reservedSupportSlot:value}));assert.equal(normalized.preferredRarity,value);assert.equal(normalized.reservedSupportSlot,value);}
 for(const value of [-1,6,1.5,NaN,Infinity,'0',false,[],{},undefined,null]){
  const normalized=normalizeBondOptimizerPreferences({...preferences(),preferredRarity:value,reservedSupportSlot:value});assert.equal(normalized.preferredRarity,5);assert.equal(normalized.reservedSupportSlot,null);
 }
});
test('each boolean is strict and invalid values do not erase other valid execution options',()=>{
 const keys=['ownedServantsOnly','ownedEquipsOnly','modifyEquips','confirmedMlbOnly'] as const;
 for(const key of keys){
  for(const value of [true,false])assert.equal(normalizeBondOptimizerPreferences({...preferences(),[key]:value})[key],value);
  for(const value of ['true','false',0,1,null,undefined,[],{}]){const normalized=normalizeBondOptimizerPreferences({...preferences({bondBase:1500,reservedSupportSlot:0}),[key]:value});assert.equal(normalized[key],defaultBondOptimizerPreferences()[key]);assert.equal(normalized.bondBase,1500);assert.equal(normalized.reservedSupportSlot,0);}
 }
});
test('excluded servant IDs are positive safe integers deduplicated in original order without mutating input',()=>{
 const input={...preferences(),excludeServantIds:[200100,100100,200100,0,-1,1.2,'100100',true,null,NaN,Infinity,Number.MAX_SAFE_INTEGER+1,Number.MAX_SAFE_INTEGER,1]},before=structuredClone(input);
 const normalized=normalizeBondOptimizerPreferences(input);assert.deepEqual(normalized.excludeServantIds,[200100,100100,Number.MAX_SAFE_INTEGER,1]);assert.deepEqual(input,before);
 normalized.excludeServantIds.push(300100);assert.equal(input.excludeServantIds.includes(300100),false);
 for(const excludeServantIds of [null,undefined,100100,'100100',{},true])assert.deepEqual(normalizeBondOptimizerPreferences({...preferences(),excludeServantIds}).excludeServantIds,[]);
});
test('the backend exclusion limit applies after validation and deduplication',()=>{
 const input={...preferences(),excludeServantIds:[0,-1,...Array(1500).fill(1),...Array.from({length:1001},(_,index)=>index+1)]};
 const normalized=normalizeBondOptimizerPreferences(input);assert.equal(normalized.excludeServantIds.length,1000);assert.deepEqual(normalized.excludeServantIds,Array.from({length:1000},(_,index)=>index+1));assert.equal(input.excludeServantIds.length,2503);
});
test('unsupported schemas shapes and broken JSON fall back without attempting coercion',()=>{
 for(const input of [null,undefined,[],true,0,'value',{}, {version:'1',bondBase:9000},{version:2,bondBase:9000}])assert.deepEqual(normalizeBondOptimizerPreferences(input),defaultBondOptimizerPreferences());
 for(const raw of ['{','null','[]','true','0','"value"','{}','{"version":2,"bondBase":9000}','{"version":"1","bondBase":9000}'])assert.deepEqual(loadBondOptimizerPreferences(storage(raw)),defaultBondOptimizerPreferences());
});
test('all reusable options round-trip through a fresh load while other storage keys remain intact',()=>{
 const store=storage(),sharedCost=JSON.stringify({version:1,limit:117});store.entries.set(PARTY_COST_STORAGE_KEY,sharedCost);
 const selected=preferences({bondBase:1,preferredRarity:0,ownedServantsOnly:false,ownedEquipsOnly:false,modifyEquips:true,confirmedMlbOnly:true,reservedSupportSlot:0,excludeServantIds:[100100,200100]});
 assert.equal(saveBondOptimizerPreferences(selected,store),true);assert.deepEqual(loadBondOptimizerPreferences(store),selected);assert.deepEqual(loadBondOptimizerPreferences(storage(store.entries.get(BOND_OPTIMIZER_STORAGE_KEY)!)),selected);assert.equal(store.entries.get(PARTY_COST_STORAGE_KEY),sharedCost);assert.equal(store.entries.size,2);
});
test('only known reusable fields are written and loading or changing an unsaved draft never writes',()=>{
 const selected=preferences({bondBase:2000,excludeServantIds:[100100]}),store=storage();
 assert.equal(saveBondOptimizerPreferences({...selected,query:'private search',result:{team:{name:'temporary'}},currentCost:99,costLimit:300,grand:true,data:{actions:[]},error:'transient'} as BondOptimizerPreferences,store),true);
 assert.deepEqual(JSON.parse(store.entries.get(BOND_OPTIMIZER_STORAGE_KEY)!),selected);
 const saved=store.entries.get(BOND_OPTIMIZER_STORAGE_KEY),draft=loadBondOptimizerPreferences(store);draft.bondBase=9999;draft.excludeServantIds.push(200100);assert.equal(store.entries.get(BOND_OPTIMIZER_STORAGE_KEY),saved);assert.deepEqual(loadBondOptimizerPreferences(store),selected);
});
test('missing disabled throwing or quota-limited storage cannot interrupt the optimizer',()=>{
 assert.deepEqual(loadBondOptimizerPreferences(null),defaultBondOptimizerPreferences());assert.equal(saveBondOptimizerPreferences(preferences(),null),false);
 assert.deepEqual(loadBondOptimizerPreferences({getItem:()=>{throw new Error('SecurityError');}}),defaultBondOptimizerPreferences());assert.equal(saveBondOptimizerPreferences(preferences(),{setItem:()=>{throw new Error('QuotaExceededError');}}),false);
 assert.deepEqual(loadBondOptimizerPreferences(),defaultBondOptimizerPreferences());assert.equal(saveBondOptimizerPreferences(preferences()),false);
});
test('a denied browser localStorage getter safely falls back for reads and writes',()=>{
 const original=Object.getOwnPropertyDescriptor(globalThis,'window');
 try{Object.defineProperty(globalThis,'window',{configurable:true,value:Object.defineProperty({},'localStorage',{get:()=>{throw new Error('SecurityError');}})});assert.deepEqual(loadBondOptimizerPreferences(),defaultBondOptimizerPreferences());assert.equal(saveBondOptimizerPreferences(preferences()),false);}
 finally{if(original)Object.defineProperty(globalThis,'window',original);else Reflect.deleteProperty(globalThis,'window');}
});
