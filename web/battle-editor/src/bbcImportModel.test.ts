import test from 'node:test';
import assert from 'node:assert/strict';
import {MAX_BBC_BYTES,bbcPreview,grandRequirementPreview,readBBCFile,readBBCImportResult} from './bbcImportModel';
import type {Catalog,Json} from './model';

const catalog:Catalog={servants:[{id:1,name:'阿尔托莉雅',aliases:[11],image:'/servant.webp'}],equips:[{id:20,name:'万华镜',image:'/equip.png'}],mysticCodes:[{id:210,name:'迦勒底战斗服'}]};
const data:Json={team:{name:'BBC 周回',onFieldSvts:[{svtId:11,supportType:'none',equip1:{id:20}},null,{supportType:'friend',equip1:{id:999}}],backupSvts:[null,null,null],mysticCode:{mysticCodeId:210}},actions:[{type:'skill',svt:0,skill:0},{type:'attack',attacks:[{svt:0,isTD:true},{isAuto:true}]},{type:'attack',attacks:[]}]};

test('BBC file reader accepts UTF-8 BOM and reads one JSON object without changing its fields',async()=>{
 const source={servant_0_name:'阿尔托莉雅',master_equip:0,round1_turns:1,round1_turn0_skill:[1],round1_turn0_np:[1],future:['保留']};
 const text='\uFEFF'+JSON.stringify(source);
 assert.deepEqual(await readBBCFile({size:new TextEncoder().encode(text).length,text:async()=>text}),source);
});

test('invalid or oversized BBC files fail before conversion',async()=>{
 for(const size of [0,MAX_BBC_BYTES+1])await assert.rejects(()=>readBBCFile({size,text:async()=>{throw new Error('must not read');}}),/为空或超过/);
 for(const text of ['{broken','null','[]','"配置"'])await assert.rejects(()=>readBBCFile({size:10,text:async()=>text}),/JSON/);
});

test('converted BBC response keeps data and all conversion warnings for explicit preview',()=>{
 const before=structuredClone(data),warnings=['策略按默认选卡补齐','特殊选项需要检查'];
 const converted=readBBCImportResult({data,warnings,validation:{valid:true,issues:[]},sourceName:'周回.json'});
 assert.deepEqual(converted,{data,warnings,validation:{valid:true,issues:[]},sourceName:'周回.json'});
 assert.deepEqual(data,before);assert.notEqual(converted.warnings,warnings);
 for(const value of [{data,warnings:[false],sourceName:'x'}, {data,sourceName:'x'}, {data,warnings:[]}, {data:{team:{},actions:'bad'},warnings:[],sourceName:'x'}, {data:{actions:[]},warnings:[],sourceName:'x'}])assert.throws(()=>readBBCImportResult(value));
});

test('BBC preview has six fixed positions, resolves alias names and retains CE-only support',()=>{
 const before=structuredClone(data),preview=bbcPreview(data,catalog);
 assert.equal(preview.title,'BBC 周回');assert.equal(preview.roster.length,6);
 assert.equal(preview.roster[0].name,'阿尔托莉雅');assert.equal(preview.roster[0].image,'/servant.webp');
 assert.equal(preview.roster[0].equips[0].name,'万华镜');assert.equal(preview.roster[1].name,'不要求此位置');
 assert.equal(preview.roster[2].name,'助战（仅要求礼装）');assert.equal(preview.roster[2].support,true);assert.equal(preview.roster[2].equips[0].name,'礼装 #999');
 assert.equal(preview.turns,2);assert.equal(preview.skills,1);assert.equal(preview.cards,1);assert.equal(preview.mystic,'迦勒底战斗服');
 assert.deepEqual(data,before);
});

test('BBC preview exposes OR candidates within their real grand slots and keeps all fetched data untouched',()=>{
 const source=structuredClone(data);source.team.onFieldSvts[2]={supportType:'npc',grandSvt:true,equip1:{id:20,limitBreak:true,candidates:[{id:999,limitBreak:false}]},equip2:{id:999},equip3:{id:20,candidates:[{id:998}]},unknown:{keep:null}};
 const before=structuredClone(source),member=bbcPreview(source,catalog).roster[2];
 assert.equal(member.support,true);
 assert.deepEqual(member.equips.map(e=>e.sourceKey),['equip1','equip1','equip3','equip3']);
 assert.deepEqual(member.equips.map(e=>e.id),[20,999,20,998]);
 assert.match(member.equips[0].name,/普通礼装 · 候选 1\/2.*万华镜.*满破/);
 assert.match(member.equips[1].name,/普通礼装 · 或 2\/2.*礼装 #999.*非满破/);
 assert.match(member.equips[3].name,/报酬礼装 · 或 2\/2/);
 assert.deepEqual(source,before);
});

test('Grand bond effects remain visible without inventing a CE, including the explicit original effect',()=>{
 for(const [effect,label] of [[true,'羁绊礼装效果：NP 50% 替换效果'],[false,'羁绊礼装效果：原始效果'],[undefined,'羁绊礼装效果：不限制']] as const){
  const source=structuredClone(data);source.team.onFieldSvts[2]={supportType:'friend',grandSvt:true,classBoardData:{future:null,...(effect===undefined?{}:{grandBondEquipSkillChange:effect})}};
  const before=structuredClone(source),member=bbcPreview(source,catalog).roster[2];
  assert.equal(member.grand,true);assert.equal(member.bondEffect,label);assert.equal(member.emptyEquipLabel,'未指定礼装卡面');assert.deepEqual(member.equips,[]);
  assert.deepEqual(source,before);
 }
});

test('Grand preview distinguishes unspecified and invalid effects without coercion and works for restored non-BBC members',()=>{
 for(const effect of [undefined,null,'NP50',0,1]){
  const member={svtId:1,supportType:'npc',grandSvt:true,classBoardData:{grandBondEquipSkillChange:effect}},before=structuredClone(member),preview=grandRequirementPreview(member);
  assert.equal(preview.bondEffect,effect==null?'羁绊礼装效果：不限制':'羁绊礼装效果：未知，请核对');assert.equal(preview.grand,true);assert.deepEqual(member,before);
 }
 assert.equal(grandRequirementPreview({svtId:1,grandSvt:true,classBoardData:{grandBondEquipSkillChange:false}}).bondEffect,'羁绊礼装效果：原始效果');
 for(const member of [null,{svtId:1},{svtId:1,grandSvt:false,classBoardData:{grandBondEquipSkillChange:true}}])assert.deepEqual(grandRequirementPreview(member),{grand:false,bondEffect:null,emptyEquipLabel:'不要求礼装'});
});

test('BBC grand effect preview ignores named legacy bond CEs while preserving their full records',()=>{
 const source=structuredClone(data);source.team.onFieldSvts[2]={supportType:'friend',grandSvt:true,equip2:{id:20,lv:100,limitBreak:true,unknown:{keep:['旧卡面']}},classBoardData:{grandBondEquipSkillChange:true}};
 const before=structuredClone(source),member=bbcPreview(source,catalog).roster[2];
 assert.deepEqual(member.equips,[]);assert.equal(member.bondEffect,'羁绊礼装效果：NP 50% 替换效果');assert.deepEqual(source,before);
});
