import test from 'node:test';
import assert from 'node:assert/strict';
import type {Catalog,Json} from './model';
import {restoreNamingSource,suggestBattleFileName,importedBattleFileName,canonicalBattleFileName,sameBattleFileName,availableBattleFileName} from './battleFileName';

const catalog:Catalog={servants:[{id:1,name:'阿尔托莉雅·卡斯特'},{id:2,name:'奥伯龙',aliases:[2002]},{id:3,name:'玛修'},{id:4,name:'候补从者'}],equips:[],mysticCodes:[]};
const formation=(slots:any[]):Json=>({team:{name:'标题不会参与命名',onFieldSvts:slots,backupSvts:[{svtId:4},null,null]},actions:[]});
const neverRandom=():string=>{throw new Error('A free base name must not request randomness.');};

test('Chinese names use the selected source and an available base name never consumes randomness',()=>{
 const data=formation([{svtId:1},{svtId:2},{svtId:3}]);
 assert.equal(suggestBattleFileName(data,catalog,'local',[],neverRandom),'local_阿尔托莉雅·卡斯特_奥伯龙_玛修.json');
 assert.equal(suggestBattleFileName(data,catalog,'chaldea',[],neverRandom),'chaldea_阿尔托莉雅·卡斯特_奥伯龙_玛修.json');
 assert.equal(suggestBattleFileName(data,catalog,'bbc',[],neverRandom),'bbc_阿尔托莉雅·卡斯特_奥伯龙_玛修.json');
 assert.equal(suggestBattleFileName(data,catalog,'local',['unrelated.json'],neverRandom),'local_阿尔托莉雅·卡斯特_奥伯龙_玛修.json');
});

test('canonical alias names and identical self/support servants keep their initial separate positions',()=>{
 const data=formation([{svtId:2002,supportType:'friend'},{svtId:2,supportType:'none'},{svtId:999999}]);
 assert.equal(suggestBattleFileName(data,catalog,'local',[],neverRandom),'local_奥伯龙_奥伯龙_从者999999.json');
});

test('empty positions keep their own numbers and never borrow backup members',()=>{
 assert.equal(suggestBattleFileName(formation([null,{svtId:2}]),catalog,'local',[],neverRandom),'local_空位1_奥伯龙_空位3.json');
 assert.equal(suggestBattleFileName({team:{backupSvts:[{svtId:4}]}},catalog,'local',[],neverRandom),'local_空位1_空位2_空位3.json');
});

test('initial formation names are unaffected by Order Change actions or the selected turn',()=>{
 const data:Json={...formation([{svtId:1},{svtId:2},{svtId:3}]),delegate:{replaceMemberIndexes:[[0,0]]},actions:[
  {type:'skill',svt:null,skill:2},{type:'attack',attacks:[]},{type:'skill',svt:0,skill:0}]};
 assert.equal(suggestBattleFileName(data,catalog,'chaldea',[],neverRandom),'chaldea_阿尔托莉雅·卡斯特_奥伯龙_玛修.json');
});

test('Windows illegal characters, controls, and edge spaces or dots cannot become path syntax',()=>{
 const special:Catalog={...catalog,servants:[{id:1,name:' ..甲/乙\\丙:丁*戊?己"庚<辛>壬|癸\u0000\u0085 .. '},{id:2,name:' . 乙 . '},{id:3,name:'CON'}]};
 const name=suggestBattleFileName(formation([{svtId:1},{svtId:2},{svtId:3}]),special,'local',[],neverRandom);
 assert.ok(name.startsWith('local_甲-乙-丙-丁-戊-己-庚-辛-壬-癸--_乙_CON'));
 assert.ok(name.endsWith('.json'));assert.doesNotMatch(name,/[<>:"/\\|?*]|\p{Cc}/u);
 for(const part of name.slice(0,-5).split('_'))assert.equal(part,part.replace(/^[\s.]+|[\s.]+$/gu,''));
 assert.equal(suggestBattleFileName(formation([{svtId:1}]),{...catalog,servants:[{id:1,name:' ... '}]},'local',[],neverRandom),'local_从者1_空位2_空位3.json');
});

test('each long name has at most forty UTF-16 units without cutting an emoji surrogate pair',()=>{
 const long:Catalog={...catalog,servants:[{id:1,name:'😀'.repeat(30)},{id:2,name:'甲'.repeat(39)+'😀尾'},{id:3,name:'乙'.repeat(100)}]};
 const name=suggestBattleFileName(formation([{svtId:1},{svtId:2},{svtId:3}]),long,'chaldea',[],neverRandom);
 const parts=name.slice(0,-5).split('_');
 assert.deepEqual(parts,['chaldea','😀'.repeat(20),'甲'.repeat(39),'乙'.repeat(40)]);
 assert.ok(name.length<=160);for(const part of parts.slice(1))assert.ok(part.length<=40);
 // Re-encoding complete code points retains exactly the same string.
 assert.equal(new TextDecoder().decode(new TextEncoder().encode(name)),name);
});

test('case-insensitive base collisions consume one normalized six-hex suffix',()=>{
 const data=formation([{svtId:1},{svtId:2},{svtId:3}]),base=suggestBattleFileName(data,catalog,'local',[],neverRandom);
 let calls=0;
 assert.equal(suggestBattleFileName(data,catalog,'local',[base.toUpperCase()],()=>{calls++;return 'A1B2C3';}),base.slice(0,-5)+'_a1b2c3.json');
 assert.equal(calls,1);
 assert.equal(suggestBattleFileName(data,catalog,'local',[base],()=>'/../A-B-C-!'),base.slice(0,-5)+'_abc000.json');
});

test('suffix collisions use numbered alternatives without requesting randomness again or losing the extension',()=>{
 const long:Catalog={...catalog,servants:catalog.servants.map(servant=>({...servant,name:'😀'.repeat(40)}))},data=formation([{svtId:1},{svtId:2},{svtId:3}]);
 const base=suggestBattleFileName(data,long,'chaldea',[],neverRandom),stem=base.slice(0,-5);
 const occupied=[base,`${stem}_abcdef.JSON`,`${stem}_abcdef_2.json`];let calls=0;
 const result=suggestBattleFileName(data,long,'chaldea',occupied,()=>{calls++;return 'abcdef';});
 assert.equal(result,`${stem}_abcdef_3.json`);assert.equal(calls,1);assert.ok(result.length<=160);
 assert.equal(new TextDecoder().decode(new TextEncoder().encode(result)),result);
});

test('restoring untrusted or old draft metadata only accepts exact supported source strings',()=>{
 assert.equal(restoreNamingSource('local'),'local');assert.equal(restoreNamingSource('chaldea'),'chaldea');assert.equal(restoreNamingSource('bbc'),'bbc');
 for(const value of [null,undefined,'CHALDEA','chaldea ',' local','share','',true,0,{},[],{source:'chaldea'},new String('chaldea')])assert.equal(restoreNamingSource(value),'local');
});

test('name suggestions preserve imported JSON, catalog metadata and the existing names array',()=>{
 const data:Json={...formation([{svtId:2002,equip1:{id:123,future:null}},null,{svtId:999999}]),unknown:{keep:[null,3]}};
 const dataBefore=structuredClone(data),catalogBefore=structuredClone(catalog),names=Object.freeze(['unrelated.json']);
 suggestBattleFileName(data,catalog,'chaldea',names,neverRandom);
 assert.deepEqual(data,dataBefore);assert.deepEqual(catalog,catalogBefore);assert.deepEqual(names,['unrelated.json']);
});

test('imported filenames take precedence over team titles and keep Chinese, spaces and extension case',()=>{
 const data=formation([{svtId:1}]);data.team.name='标题与文件名不同';const before=structuredClone(data);
 assert.equal(importedBattleFileName(data,' 我的 原方案.JSON '),'我的 原方案.JSON');
 assert.equal(importedBattleFileName(data,'我的 原方案'),'我的 原方案.json');
 assert.equal(importedBattleFileName(data),'标题与文件名不同.json');
 assert.equal(importedBattleFileName({team:{name:'命名方案.json'}}),'命名方案.json');
 assert.deepEqual(data,before);
});

test('unnamed or unsafe imported metadata falls back without selecting any path basename',()=>{
 const named={team:{name:'可信队名'}};
 for(const value of [undefined,null,{},'', '  ','.json','../原文件.json','C:\\目录\\原文件.json','C:原文件.json','/tmp/原文件.json']){
  assert.equal(importedBattleFileName(named,value),'可信队名.json');
 }
 for(const title of [undefined,null,{},'', ' ... ','未命名方案','未命名方案.json'])assert.equal(importedBattleFileName({team:{name:title}}),null);
 assert.equal(importedBattleFileName({team:{name:'未命名方案'}},'已有名字.json'),'已有名字.json');
});

test('a real uploaded filename is retained even when it matches the default unnamed team title',()=>{
 assert.equal(importedBattleFileName({team:{name:'原有队名'}},'未命名方案.json'),'未命名方案.json');
 assert.equal(importedBattleFileName({team:{name:'未命名方案'}},'未命名方案'),'未命名方案.json');
 assert.equal(importedBattleFileName({team:{name:'未命名方案'}}),null);
});

test('inherited names clean illegal characters and reserved device names without losing their useful title',()=>{
 assert.equal(importedBattleFileName({team:{name:' . 甲/乙\\丙:丁*戊?己"庚<辛>壬|癸\u0000 . '}}),'甲-乙-丙-丁-戊-己-庚-辛-壬-癸-.json');
 assert.equal(importedBattleFileName({},'活动:周回?.json'),'活动-周回-.json');
 for(const name of ['CON','prn.json','COM0.json','LPT¹.JSON']){
  const inherited=importedBattleFileName({},name)!;
  assert.ok(inherited.startsWith('方案_'));assert.doesNotThrow(()=>canonicalBattleFileName(inherited));
 }
});

test('canonical names normalize whitespace and missing extensions but reject unsafe and overlong names',()=>{
 assert.equal(canonicalBattleFileName('  中文 方案  '),'中文 方案.json');
 assert.equal(canonicalBattleFileName('MiXeD.JsOn'),'MiXeD.JsOn');
 for(const name of ['', ' ', '.', '..', '.hidden.json','foo.','../foo.json','C:\\foo.json','a/b','foo:bar','foo\u0000bar','CON.json','NUL.anything.json','LPT².json','a'.repeat(156)])assert.throws(()=>canonicalBattleFileName(name),name);
 assert.equal(canonicalBattleFileName('a'.repeat(155)), 'a'.repeat(155)+'.json');
 assert.equal(canonicalBattleFileName('a'.repeat(155)+'.json').length,160);
});

test('same-name comparison includes implicit JSON and Unicode case without conflating invalid paths',()=>{
 assert.equal(sameBattleFileName(' Foo.JSON ','foo'),true);
 assert.equal(sameBattleFileName('Ä方案.json','ä方案.JSON'),true);
 assert.equal(sameBattleFileName('原名.json','另一份.json'),false);
 assert.equal(sameBattleFileName('../foo.json','foo.json'),false);
 assert.equal(sameBattleFileName('CON.json','con'),false);
});

test('inherited names truncate complete Unicode points and reserve room for the extension',()=>{
 const inherited=importedBattleFileName({team:{name:'😀'.repeat(100)}})!;
 assert.equal(inherited,'😀'.repeat(77)+'.json');assert.ok(inherited.length<=160);
 assert.equal(new TextDecoder().decode(new TextEncoder().encode(inherited)),inherited);
 const fromFile=importedBattleFileName({},'甲'.repeat(154)+'😀.JSON')!;
 assert.equal(fromFile,'甲'.repeat(154)+'.JSON');assert.ok(fromFile.length<=160);
});

test('explicit Save As keeps a free inherited name and resolves case-insensitive suffix collisions once',()=>{
 assert.equal(availableBattleFileName('原方案.JSON',[],neverRandom),'原方案.JSON');
 const existing=Object.freeze(['原方案.json','原方案_abcdef.json','原方案_abcdef_2.JSON']);let calls=0;
 assert.equal(availableBattleFileName('原方案.JSON',existing,()=>{calls++;return 'ABCDEF';}),'原方案_abcdef_3.JSON');
 assert.equal(calls,1);assert.deepEqual(existing,['原方案.json','原方案_abcdef.json','原方案_abcdef_2.JSON']);
 assert.equal(availableBattleFileName('原方案',['原方案'],()=>'/../A-B-C-!'),'原方案_abc000.json');
});

test('Save As collision names retain their extension and Unicode integrity at the length limit',()=>{
 const base=importedBattleFileName({},'😀'.repeat(100)+'.JSON')!;
 const name=availableBattleFileName(base,[base.toLowerCase()],()=> '012345');
 assert.ok(name.endsWith('_012345.JSON'));assert.ok(name.length<=160);
 assert.equal(new TextDecoder().decode(new TextEncoder().encode(name)),name);
 assert.throws(()=>availableBattleFileName('../unsafe.json',[],neverRandom));
});
