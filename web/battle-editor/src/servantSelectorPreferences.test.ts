import test from 'node:test';
import assert from 'node:assert/strict';
import {
 SERVANT_SELECTOR_STORAGE_KEY,SERVANT_SORT_OPTIONS,defaultServantSelectorPreferences,
 loadServantSelectorPreferences,normalizeServantSelectorPreferences,saveServantSelectorPreferences,sortServants,
} from './servantSelectorPreferences';
import type {ServantSelectorPreferences} from './servantSelectorPreferences';
import type {Servant} from './model';

const preferences=(extra:Partial<ServantSelectorPreferences>={}):ServantSelectorPreferences=>({...defaultServantSelectorPreferences(),...extra});
const storage=(value:string|null=null)=>{
 const entries=new Map<string,string>();if(value!==null)entries.set(SERVANT_SELECTOR_STORAGE_KEY,value);
 return {getItem:(key:string)=>entries.get(key)??null,setItem:(key:string,item:string)=>{entries.set(key,item);},entries};
};
const servant=(id:number,extra:Partial<Servant>={}):Servant=>({id,name:`从者 ${id}`,...extra});

test('fresh preferences use ascending internal IDs and independent unrestricted filters',()=>{
 assert.deepEqual(defaultServantSelectorPreferences(),{version:1,classKey:'',rarity:'',sortKey:'id',direction:'asc'});
 const a=defaultServantSelectorPreferences(),b=defaultServantSelectorPreferences();a.rarity='5';assert.equal(b.rarity,'');
 assert.deepEqual(SERVANT_SORT_OPTIONS.map(option=>option.key),['id','collectionNo','rarity']);
});

test('class rarity and sort choices survive storage round-trip without plan/query/advanced state',()=>{
 const store=storage(),selected=preferences({classKey:'alterEgo',rarity:'0',sortKey:'collectionNo',direction:'desc'});
 assert.equal(saveServantSelectorPreferences({...selected,query:'not stored',advanced:{gender:'female'}} as ServantSelectorPreferences,store),true);
 assert.deepEqual(loadServantSelectorPreferences(['saber','alterEgo'],store),selected);
 assert.deepEqual([...store.entries.keys()],[SERVANT_SELECTOR_STORAGE_KEY]);
 assert.deepEqual(JSON.parse(store.entries.get(SERVANT_SELECTOR_STORAGE_KEY)!),selected);
});

test('saved choices tolerate an unloaded catalog but drop absent classes once loaded',()=>{
 const selected=preferences({classKey:'loreGrandCaster',rarity:'5',direction:'desc'}),store=storage(JSON.stringify(selected));
 assert.deepEqual(loadServantSelectorPreferences(undefined,store),selected);
 assert.equal(loadServantSelectorPreferences(['saber'],store).classKey,'');
 assert.equal(loadServantSelectorPreferences([],store).classKey,'');
 assert.equal(loadServantSelectorPreferences(['loreGrandCaster'],store).classKey,'loreGrandCaster');
 assert.equal(normalizeServantSelectorPreferences(selected,[]).rarity,'5');
});

test('all supported rarities including zero restore as strings',()=>{
 for(const rarity of ['','0','1','2','3','4','5'])assert.equal(normalizeServantSelectorPreferences(preferences({rarity})).rarity,rarity);
 for(const rarity of [0,5,'6','-1','05',' 5',null,true])assert.equal(normalizeServantSelectorPreferences({...preferences(),rarity}).rarity,'');
});

test('bad JSON unsupported schema and invalid individual fields fall back safely',()=>{
 for(const raw of ['{','null','[]','"string"','3','true','{"version":2,"classKey":"saber"}','{"classKey":"saber"}'])assert.deepEqual(loadServantSelectorPreferences(['saber'],storage(raw)),defaultServantSelectorPreferences());
 assert.deepEqual(normalizeServantSelectorPreferences({version:1,classKey:['saber'],rarity:'bad',sortKey:'__proto__',direction:'DESC'}),defaultServantSelectorPreferences());
 assert.deepEqual(normalizeServantSelectorPreferences({version:1,classKey:' saber',rarity:'4',sortKey:'rarity',direction:'desc'}),preferences({rarity:'4',sortKey:'rarity',direction:'desc'}));
});

test('missing or denied storage does not interrupt selection',()=>{
 assert.deepEqual(loadServantSelectorPreferences(undefined,null),defaultServantSelectorPreferences());
 assert.equal(saveServantSelectorPreferences(preferences(),null),false);
 assert.deepEqual(loadServantSelectorPreferences(undefined,{getItem:()=>{throw new Error('SecurityError');}}),defaultServantSelectorPreferences());
 assert.equal(saveServantSelectorPreferences(preferences(),{setItem:()=>{throw new Error('QuotaExceededError');}}),false);
 assert.deepEqual(loadServantSelectorPreferences(),defaultServantSelectorPreferences());
 assert.equal(saveServantSelectorPreferences(preferences()),false);
});

test('a blocked browser localStorage getter also falls back without throwing',()=>{
 const original=Object.getOwnPropertyDescriptor(globalThis,'window');
 const browser=Object.defineProperty({},'localStorage',{get:()=>{throw new Error('SecurityError');}});
 try {
  Object.defineProperty(globalThis,'window',{value:browser,configurable:true});
  assert.deepEqual(loadServantSelectorPreferences(),defaultServantSelectorPreferences());
  assert.equal(saveServantSelectorPreferences(preferences()),false);
 }finally {
  if(original)Object.defineProperty(globalThis,'window',original);
  else Reflect.deleteProperty(globalThis,'window');
 }
});

test('ID sorting is numeric across the whole result and never mutates source order',()=>{
 const items=[servant(100),servant(2),servant(10)],before=structuredClone(items);
 assert.deepEqual(sortServants(items,'id','asc').map(item=>item.id),[2,10,100]);
 assert.deepEqual(sortServants(items,'id','desc').map(item=>item.id),[100,10,2]);
 assert.deepEqual(items,before);
 const many=Array.from({length:50},(_,i)=>servant(50-i));
 assert.deepEqual(sortServants(many,'id','asc').slice(0,24).map(item=>item.id),Array.from({length:24},(_,i)=>i+1));
});

test('collection number zero missing and invalid values stay last in either direction',()=>{
 const items=[servant(9,{collectionNo:0}),servant(5),servant(8,{collectionNo:NaN}),servant(7,{collectionNo:Infinity}),servant(6,{collectionNo:-1}),servant(4,{collectionNo:20}),servant(3,{collectionNo:2})];
 assert.deepEqual(sortServants(items,'collectionNo','asc').map(item=>item.id),[3,4,5,6,7,8,9]);
 assert.deepEqual(sortServants(items,'collectionNo','desc').map(item=>item.id),[4,3,5,6,7,8,9]);
});

test('zero-star servants sort as real values while missing rarity stays last',()=>{
 const items=[servant(2,{rarity:5}),servant(4,{rarity:0}),servant(1),servant(3,{rarity:3})];
 assert.deepEqual(sortServants(items,'rarity','asc').map(item=>item.id),[4,3,2,1]);
 assert.deepEqual(sortServants(items,'rarity','desc').map(item=>item.id),[2,3,4,1]);
});

test('equal primary values always use ascending IDs in either direction',()=>{
 const items=[servant(10,{rarity:5,collectionNo:8}),servant(2,{rarity:5,collectionNo:8}),servant(9,{rarity:4,collectionNo:7})];
 for(const direction of ['asc','desc'] as const){
  assert.deepEqual(sortServants(items,'rarity',direction).filter(item=>item.rarity===5).map(item=>item.id),[2,10]);
  assert.deepEqual(sortServants(items,'collectionNo',direction).filter(item=>item.collectionNo===8).map(item=>item.id),[2,10]);
 }
});
