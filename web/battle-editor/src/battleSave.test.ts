import test from 'node:test';
import assert from 'node:assert/strict';
import {battleSaveRequest,battleSourceRecord,prepareBattleSave,preparedSaveMatches} from './battleSave';
import type {BattleSource} from './battleSave';

const source:BattleSource={name:'原方案.json',hash:'a'.repeat(64),path:'F:\\安装目录\\config\\Battle\\原方案.json'};
const data={team:{name:'已编辑方案'},actions:[],future:{keep:[null,false,7]}};

test('bound files retain their original hash even when the basename case or extension spelling changes',async()=>{
 const bound={...source,name:'Named.JSON'};
 const prepared=await prepareBattleSave('named',data,bound,async()=>{throw new Error('Never replace the originally read hash with a fresh lookup');});
 assert.equal(prepared.target?.hash,bound.hash);assert.equal(prepared.name,bound.name);
 assert.deepEqual(battleSaveRequest(prepared),{path:'/battles/Named.JSON',method:'PUT',body:{data,expectedHash:bound.hash}});
});

test('external same-name imports read an existing target before any replacement request is built',async()=>{
 const calls:string[]=[];
 const prepared=await prepareBattleSave('原方案',data,null,async name=>{calls.push(name);return source;});
 assert.deepEqual(calls,['原方案.json']);assert.deepEqual(prepared.target,source);
 assert.equal(battleSaveRequest(prepared).body.expectedHash,source.hash);
});

test('missing targets use POST only and a collision error cannot automatically turn into PUT',async()=>{
 const prepared=await prepareBattleSave('新方案',data,null,async()=>null);
 assert.equal(prepared.target,null);
 assert.deepEqual(battleSaveRequest(prepared),{path:'/battles',method:'POST',body:{name:'新方案.json',data}});
});

test('confirmation snapshots detach data and target metadata, and changed documents require a new confirmation',async()=>{
 const current=structuredClone(data),target={...source};
 const prepared=await prepareBattleSave('原方案.json',current,null,async()=>target);
 assert.equal(preparedSaveMatches(prepared,current),true);
 current.future.keep.push(8);target.hash='b'.repeat(64);
 assert.equal(preparedSaveMatches(prepared,current),false);
 assert.deepEqual(prepared.data,data);assert.equal(prepared.target?.hash,source.hash);
});

test('lookup errors and unsafe names stop preparation instead of falling back to a write',async()=>{
 await assert.rejects(()=>prepareBattleSave('原方案',data,null,async()=>{throw new Error('offline');}),/offline/);
 await assert.rejects(()=>prepareBattleSave('../outside',data,null,async()=>source));
});

test('only complete server metadata can establish a bound source for draft recovery',()=>{
 assert.deepEqual(battleSourceRecord({...source,data}),source);
 for(const invalid of [null,{name:source.name},{...source,hash:'bad'},{...source,name:'../原方案.json'}])assert.equal(battleSourceRecord(invalid),null);
});

test('renaming a bound document finds the new target and never lends its old hash to another file',async()=>{
 const replacement={...source,name:'另一方案.JSON',hash:'b'.repeat(64),path:'F:\\安装目录\\config\\Battle\\另一方案.JSON'};
 const calls:string[]=[];
 const prepared=await prepareBattleSave('  另一方案  ',data,source,async name=>{calls.push(name);return replacement;});
 assert.deepEqual(calls,['另一方案.json']);assert.equal(prepared.name,replacement.name);
 assert.deepEqual(battleSaveRequest(prepared),{path:'/battles/'+encodeURIComponent(replacement.name),method:'PUT',body:{data,expectedHash:replacement.hash}});
 assert.equal(source.hash,'a'.repeat(64));
 const created=await prepareBattleSave('全新名称',data,source,async()=>null);
 assert.deepEqual(battleSaveRequest(created),{path:'/battles',method:'POST',body:{name:'全新名称.json',data}});
});

test('a confirmation freezes data when requested even if target lookup is still pending',async()=>{
 const current=structuredClone(data);
 let resolveLookup!:(target:BattleSource|null)=>void;
 const lookup=new Promise<BattleSource|null>(resolve=>{resolveLookup=resolve;});
 const preparing=prepareBattleSave('原方案',current,null,()=>lookup);
 current.future.keep.push(9);current.team.name='查找文件期间的修改';
 resolveLookup(source);
 const prepared=await preparing;
 assert.deepEqual(prepared.data,data);
 assert.equal(preparedSaveMatches(prepared,current),false);
 assert.equal(prepared.target?.hash,source.hash);
});

test('filename rejection happens before lookup and requests retain encoded exact target names',async()=>{
 let calls=0;
 for(const name of ['../outside.json','CON','a'.repeat(156)])await assert.rejects(()=>prepareBattleSave(name,data,null,async()=>{calls++;return source;}));
 assert.equal(calls,0);
 const exact={...source,name:'中文 空格 #100%.JSON'};
 const prepared=await prepareBattleSave('中文 空格 #100%.json',data,exact,async()=>{throw new Error('Bound target must not be looked up again');});
 assert.equal(battleSaveRequest(prepared).path,'/battles/'+encodeURIComponent(exact.name));
 assert.deepEqual(prepared.data.future,data.future);
});

test('draft bindings reject incomplete or transformed server identities instead of authorizing replacement',()=>{
 for(const invalid of [[],{...source,hash:'A'.repeat(64)},{...source,hash:source.hash+'0'},{...source,path:''},{...source,path:null},{...source,name:' 原方案.json '},{...source,name:'原方案'},{...source,name:'CON.json'},{...source,name:'甲'.repeat(156)+'.json'}])assert.equal(battleSourceRecord(invalid),null);
 const exact={...source,name:'MiXeD.JSON'};
 const restored=battleSourceRecord(exact)!;assert.deepEqual(restored,exact);
 restored.hash='c'.repeat(64);assert.equal(exact.hash,source.hash);
});
