import test from 'node:test';
import assert from 'node:assert/strict';
import {BATTLE_LIBRARY_STORAGE_KEY,battleLibraryTimeText,battleLibraryTitle,changeBattleLibrarySort,defaultBattleLibraryPreferences,filterAndSortBattleLibrary,loadBattleLibraryPreferences,normalizeBattleLibraryPreferences,saveBattleLibraryPreferences} from './battleLibraryModel';
import type {BattleLibraryItem,BattleLibraryPreferences} from './battleLibraryModel';

const preferences=(change:Partial<BattleLibraryPreferences>={}):BattleLibraryPreferences=>({...defaultBattleLibraryPreferences(),...change});
const item=(name:string,change:Partial<BattleLibraryItem>={}):BattleLibraryItem=>({name,...change});
const names=(rows:readonly BattleLibraryItem[],change:Partial<BattleLibraryPreferences>={},query='')=>filterAndSortBattleLibrary(rows,query,preferences(change)).map(row=>row.name);

test('favorites stay first in both directions and sorting within each group remains independent',()=>{
 const rows=[item('older.json',{modifiedAt:'2026-01-01T00:00:00Z'}),item('favorite-new.json',{favorite:true,modifiedAt:'2026-01-03T00:00:00Z'}),item('newer.json',{modifiedAt:'2026-01-04T00:00:00Z'}),item('favorite-old.json',{favorite:true,modifiedAt:'2026-01-02T00:00:00Z'})];
 assert.deepEqual(names(rows),['favorite-new.json','favorite-old.json','newer.json','older.json']);
 assert.deepEqual(names(rows,{direction:'asc'}),['favorite-old.json','favorite-new.json','older.json','newer.json']);
});

test('creation and modification order use distinct fields without modifying the original rows',()=>{
 const rows=[item('甲.json',{createdAt:'2025-01-01T00:00:00Z',modifiedAt:'2026-02-01T00:00:00Z'}),item('乙.json',{createdAt:'2026-01-01T00:00:00Z',modifiedAt:'2026-01-01T00:00:00Z'})],before=structuredClone(rows);
 assert.deepEqual(names(rows),['甲.json','乙.json']);
 assert.deepEqual(names(rows,{sortKey:'createdAt'}),['乙.json','甲.json']);
 assert.deepEqual(rows,before);
});

test('missing invalid or explicitly unknown times stay below recorded times in either direction',()=>{
 const rows=[item('c.json',{createdAt:null}),item('d.json',{createdAt:'bad'}),item('a.json',{createdAt:'2026-01-02T00:00:00Z'}),item('b.json',{createdAt:'2026-01-01T00:00:00Z'}),item('e.json',{createdAt:'2026-01-03T00:00:00Z',createdAtSource:'unknown'})];
 assert.deepEqual(names(rows,{sortKey:'createdAt',direction:'asc'}),['b.json','a.json','c.json','d.json','e.json']);
 assert.deepEqual(names(rows,{sortKey:'createdAt',direction:'desc'}),['a.json','b.json','c.json','d.json','e.json']);
 assert.deepEqual(names([item('favorite.json',{favorite:true}),...rows],{sortKey:'createdAt'}).slice(0,1),['favorite.json']);
});

test('natural title sorting handles numbers Chinese titles and deterministic filename ties',()=>{
 const rows=[item('z.json',{title:'方案10'}),item('a.json',{title:'方案2'}),item('B10.json',{title:'同名'}),item('B2.json',{title:'同名'}),item('方案3.json',{title:'   '})];
 const asc=names(rows,{sortKey:'name',direction:'asc'}),desc=names(rows,{sortKey:'name',direction:'desc'});
 assert.ok(asc.indexOf('a.json')<asc.indexOf('方案3.json')&&asc.indexOf('方案3.json')<asc.indexOf('z.json'));
 assert.ok(desc.indexOf('z.json')<desc.indexOf('方案3.json')&&desc.indexOf('方案3.json')<desc.indexOf('a.json'));
 assert.ok(asc.indexOf('B2.json')<asc.indexOf('B10.json'));assert.ok(desc.indexOf('B2.json')<desc.indexOf('B10.json'));
 assert.equal(battleLibraryTitle(item('file.json',{title:'  '})),'file.json');
});

test('search matches display titles and filenames before retaining favorite priority',()=>{
 const rows=[item('Alpha.json',{title:'甲方案'}),item('Beta.json',{title:'Alpha队伍',favorite:true}),item('Other.json',{favorite:true})];
 assert.deepEqual(names(rows,{},' alpha '),['Beta.json','Alpha.json']);
 assert.deepEqual(names(rows,{},'甲'),['Alpha.json']);assert.deepEqual(names(rows,{},'无匹配'),[]);
});

test('a first-seen timestamp is labelled honestly and never borrowed from modification time',()=>{
 assert.match(battleLibraryTimeText(item('a',{createdAt:'2026-01-01T00:00:00Z',createdAtSource:'firstSeen'}),'createdAt'),/^首次收录 /);
 assert.match(battleLibraryTimeText(item('a',{createdAt:'2026-01-01T00:00:00Z',createdAtSource:'birthtime'}),'createdAt'),/^创建 /);
 assert.equal(battleLibraryTimeText(item('a',{modifiedAt:'2026-01-01T00:00:00Z'}),'createdAt'),'创建时间未记录');
 assert.equal(battleLibraryTimeText(item('a',{createdAt:'2026-01-01T00:00:00Z',createdAtSource:'unknown'}),'createdAt'),'创建时间未记录');
 assert.match(battleLibraryTimeText(item('a',{modifiedAt:'2026-01-01T00:00:00Z'}),'name'),/^修改 /);
});

test('new sort fields choose useful default directions and preferences omit query/favorites',()=>{
 assert.deepEqual(defaultBattleLibraryPreferences(),{version:1,sortKey:'modifiedAt',direction:'desc'});
 assert.equal(changeBattleLibrarySort(preferences(),'name').direction,'asc');
 assert.equal(changeBattleLibrarySort(preferences({sortKey:'name',direction:'asc'}),'createdAt').direction,'desc');
 const entries=new Map<string,string>(),store={getItem:(key:string)=>entries.get(key)??null,setItem:(key:string,value:string)=>{entries.set(key,value);}};
 const selected=preferences({sortKey:'name',direction:'asc'});
 assert.equal(saveBattleLibraryPreferences({...selected,query:'不要保存',favorites:['a.json']} as BattleLibraryPreferences,store),true);
 assert.deepEqual(loadBattleLibraryPreferences(store),selected);
 assert.deepEqual(JSON.parse(entries.get(BATTLE_LIBRARY_STORAGE_KEY)!),selected);
});

test('corrupt denied and unavailable storage never prevents browsing',()=>{
 for(const raw of ['{','null','[]','false','{"version":2,"sortKey":"name"}'])assert.deepEqual(loadBattleLibraryPreferences({getItem:()=>raw}),defaultBattleLibraryPreferences());
 assert.deepEqual(normalizeBattleLibraryPreferences({version:1,sortKey:'__proto__',direction:'up'}),defaultBattleLibraryPreferences());
 assert.deepEqual(loadBattleLibraryPreferences({getItem:()=>{throw new Error('denied');}}),defaultBattleLibraryPreferences());
 assert.equal(saveBattleLibraryPreferences(preferences(),{setItem:()=>{throw new Error('full');}}),false);
 assert.deepEqual(loadBattleLibraryPreferences(null),defaultBattleLibraryPreferences());assert.equal(saveBattleLibraryPreferences(preferences(),null),false);
});
