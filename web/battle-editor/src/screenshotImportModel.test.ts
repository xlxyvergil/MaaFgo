import test from 'node:test';
import assert from 'node:assert/strict';
import {clipboardImage,imageMime,MAX_IMAGE_BYTES,readScreenshotResult,screenshotTeam,readImageImportResult,shareImagePreview} from './screenshotImportModel';
import type {ScreenshotSlot} from './screenshotImportModel';
import type {Catalog} from './model';
const catalog:Catalog={servants:[{id:1,name:'甲'}],equips:[{id:2,name:'乙',recognizable:true},{id:3,name:'不可识别'}],mysticCodes:[]};
const slots=():ScreenshotSlot[]=>Array.from({length:6},(_,slot)=>({slot,servantId:slot===0?1:null,status:slot===0?'identified':'empty',support:slot===0,equipStatus:'empty'}));
test('screenshot formats and size are explicit; empty MIME may use filename extension',()=>{
 for(const type of ['image/png','image/jpeg','image/webp'])assert.equal(imageMime({type,size:10}),type);
 assert.equal(imageMime({type:'',name:'中文.JPEG',size:1}),'image/jpeg');
 for(const file of [{type:'image/gif',size:10},{type:'text/plain',name:'a.png',size:10},{type:'image/png',size:0},{type:'image/png',size:MAX_IMAGE_BYTES+1}])assert.throws(()=>imageMime(file));
});
test('clipboard image routing ignores ordinary text and rejects multiple images',()=>{
 const fake={} as File;
 assert.equal(clipboardImage([{kind:'string',type:'text/plain',getAsFile:()=>fake}]),null);
 assert.equal(clipboardImage([{kind:'file',type:'image/png',getAsFile:()=>fake}]),fake);
 assert.throws(()=>clipboardImage(Array.from({length:2},()=>({kind:'file',type:'image/png',getAsFile:()=>fake}))));
});
test('screenshot preview must contain all six ordered slots and strict identities',()=>{
 const original={slots:slots(),warnings:['请核对',null]};const read=readScreenshotResult(original);
 assert.deepEqual(read.warnings,['请核对']);assert.notEqual(read.slots[0],original.slots[0]);
 for(const bad of [slots().slice(1),[...slots().slice(0,5),slots()[0]],slots().map(s=>({...s,servantId:true})),slots().map(s=>({...s,support:'false'}))])assert.throws(()=>readScreenshotResult({slots:bad}));
});
test('screenshot import writes only observed requirements, without invented skill levels or master outfit',()=>{
 const rows=slots();rows[0].equipId=2;const before=structuredClone(rows);
 const data=screenshotTeam(rows,catalog);
 assert.deepEqual(data,{team:{name:'截图导入编队',onFieldSvts:[{supportType:'friend',svtId:1,equip1:{id:2}},null,null],backupSvts:[null,null,null]},actions:[]});
 assert.deepEqual(rows,before);
});
test('manual corrections support CE-only friend but reject owned CE without servant',()=>{
 const rows=slots();rows[0]={...rows[0],servantId:null,status:'unknown',equipId:2};
 assert.deepEqual(screenshotTeam(rows,catalog).team.onFieldSvts[0],{supportType:'friend',equip1:{id:2}});
 rows[0].support=false;assert.throws(()=>screenshotTeam(rows,catalog),/补选从者/);
});
test('manual correction rejects unsupported CE, unknown servant, repeated support and blank result',()=>{
 for(const key of ['servantId','equipId'] as const){const rows=slots();rows[0][key]=999;assert.throws(()=>screenshotTeam(rows,catalog));}
 const unsupported=slots();unsupported[0].equipId=3;assert.throws(()=>screenshotTeam(unsupported,catalog));
 const duplicate=slots();duplicate[1].support=true;assert.throws(()=>screenshotTeam(duplicate,catalog));
 const blank=slots().map(s=>({...s,servantId:null,support:false}));assert.throws(()=>screenshotTeam(blank,catalog));
});

const shareData=()=>({team:{name:'双回合分享',onFieldSvts:[{svtId:1,skillLvs:[10,9,8]},null,{supportType:'friend',equip1:{id:2,limitBreak:false}}],backupSvts:[{svtId:999},null,null],mysticCode:{mysticCodeId:210,level:10}},actions:[{type:'skill',svt:null,skill:2},{type:'attack',attacks:[{isAuto:true},{isTD:true,svt:0,card:-1}],options:{enemyTarget:2}},{type:'skill',svt:0,skill:1,options:{playerTarget:2}}],delegate:{replaceMemberIndexes:[[0,0]],future:[null]},unknown:{keep:['完整保留',0,false,null]}});

test('image routing distinguishes ordinary screenshots from checksummed share responses',()=>{
 const screenshot={slots:slots(),warnings:['游戏截图']};
 assert.deepEqual(readImageImportResult(screenshot),readScreenshotResult(screenshot));
 assert.deepEqual(readImageImportResult({...screenshot,kind:'screenshot'}),readScreenshotResult({...screenshot,kind:'screenshot'}));
 assert.throws(()=>readImageImportResult({...screenshot,kind:'unknown'}),/不支持/);
 assert.throws(()=>readImageImportResult({...screenshot,kind:'share',schema:1}),/顶层|编队/);
});

test('share image restoration preserves the complete Chaldea tree and failed validation for later editing',()=>{
 const data=shareData(),before=structuredClone(data),warnings=['请核对回合'],validation={valid:false,issues:[{code:'fixture',message:'需要核对',severity:'error'}],summary:{turns:2}};
 const restored=readImageImportResult({kind:'share',schema:1,data,warnings,validation,title:'分享标题',elapsedMs:12});
 assert.equal(restored.kind,'share');
 if(restored.kind!=='share')throw new Error('share result was flattened');
 assert.deepEqual(restored.data,before);assert.equal(restored.data,data);
 assert.deepEqual(restored.data.actions[1].attacks,[{isAuto:true},{isTD:true,svt:0,card:-1}]);
 assert.deepEqual(restored.data.delegate,{replaceMemberIndexes:[[0,0]],future:[null]});
 assert.deepEqual(restored.validation,validation);assert.deepEqual(restored.warnings,warnings);assert.notEqual(restored.warnings,warnings);
 assert.deepEqual(data,before);
});

test('share responses reject unknown schemas, malformed warnings and unusable editable shapes',()=>{
 const base={kind:'share',schema:1,data:shareData(),warnings:[],validation:null};
 for(const changed of [{schema:2},{schema:true},{warnings:'warning'},{warnings:[null]},{data:null},{data:{actions:[]}},{data:{team:{},actions:'bad'}},{validation:[]},{validation:{valid:true}},{validation:{valid:true,issues:[null]}},{validation:{valid:true,issues:[{message:2}]}}]){
  assert.throws(()=>readImageImportResult({...base,...changed}));
 }
});

test('share preview keeps CE-only support, unknown identities, real turn boundaries and all equipment slots',()=>{
 const data=shareData(),before=structuredClone(data);
 data.team.onFieldSvts[2]={supportType:'friend',equip1:{id:2,limitBreak:false},equip3:{id:999}} as any;
 const preview=shareImagePreview(data,catalog);
 assert.equal(preview.title,'双回合分享');assert.equal(preview.turns,2);assert.equal(preview.skills,2);assert.equal(preview.cards,1);
 assert.equal(preview.roster.length,6);assert.equal(preview.roster[0].name,'甲');assert.equal(preview.roster[1].name,'不要求此位置');
 assert.equal(preview.roster[2].name,'助战（仅要求礼装）');assert.equal(preview.roster[2].supportLabel,'好友助战');assert.deepEqual(preview.roster[2].equips.map(e=>e.name),['乙','礼装 #999']);
 assert.equal(preview.roster[3].name,'从者 #999');assert.equal(preview.mystic,'御主服 #210');
 delete (data.team.onFieldSvts[2] as any).equip3;
 assert.deepEqual(data,before);
});

test('share preview labels NPC and legacy fixed supports without changing their types',()=>{
 for(const [supportType,label] of [['npc','NPC 助战'],['fixed','固定助战']]){
  const data=shareData();data.team.onFieldSvts[2]={svtId:1,supportType} as any;
  const preview=shareImagePreview(data,catalog);
  assert.equal(preview.roster[2].support,true);assert.equal(preview.roster[2].supportLabel,label);
  assert.equal((data.team.onFieldSvts[2] as any).supportType,supportType);
 }
});

const chaldeaImageResult=(teamId=107574)=>({kind:'chaldea',schema:1,teamId,source:`https://link.chaldea.center/laplace/share?id=${teamId}`,data:shareData(),warnings:[],validation:{valid:true,issues:[]}});

test('Chaldea image ID routing preserves the entire fetched plan and its origin',()=>{
 const original=chaldeaImageResult(),before=structuredClone(original);
 const restored=readImageImportResult({...original,validation:{valid:false,issues:[{message:'请核对配置',severity:'error'}]},elapsedMs:350});
 assert.equal(restored.kind,'chaldea');
 if(restored.kind!=='chaldea')throw new Error('Chaldea image was treated as a formation screenshot');
 assert.equal(restored.teamId,107574);assert.equal(restored.source,original.source);
 assert.equal(restored.data,original.data);assert.deepEqual(restored.data,before.data);
 assert.deepEqual(restored.data.unknown,{keep:['完整保留',0,false,null]});
 assert.deepEqual(restored.data.actions,before.data.actions);assert.deepEqual(restored.data.delegate,before.data.delegate);
 assert.equal(restored.validation?.valid,false);assert.equal(restored.elapsedMs,350);
 assert.deepEqual(original,before);
});

test('Chaldea image results accept positive safe integer IDs without a six-digit limit',()=>{
 for(const teamId of [1,107574,1_000_000,Number.MAX_SAFE_INTEGER]){
  const result=readImageImportResult(chaldeaImageResult(teamId));
  assert.equal(result.kind,'chaldea');if(result.kind==='chaldea')assert.equal(result.teamId,teamId);
 }
});

test('Chaldea image results reject missing, ambiguous or unsafe IDs and noncanonical share links',()=>{
 const base=chaldeaImageResult();
 for(const teamId of [null,undefined,0,-1,1.5,'107574',true,NaN,Infinity,Number.MAX_SAFE_INTEGER+1]){
  assert.throws(()=>readImageImportResult({...base,teamId}),/Team ID/);
 }
 for(const source of [undefined,'','javascript:alert(1)','https://example.com/laplace/share?id=107574','http://link.chaldea.center/laplace/share?id=107574','https://link.chaldea.center/laplace/share?id=107575','https://link.chaldea.center/laplace/share?id=0107574',base.source+'&extra=1']){
  assert.throws(()=>readImageImportResult({...base,source}),/Team ID/);
 }
});

test('Chaldea image previews reject malformed fetched data, warnings and validation',()=>{
 const base=chaldeaImageResult();
 for(const changed of [{schema:2},{schema:true},{warnings:null},{warnings:[{}]},{data:null},{data:{team:[],actions:[]}},{data:{team:{},actions:'bad'}},{validation:{valid:'true',issues:[]}},{validation:{valid:true,issues:[{message:null}]}}]){
  assert.throws(()=>readImageImportResult({...base,...changed}));
 }
});

test('full-plan image preview labels each candidate as OR without flattening the three grand slots',()=>{
 const data=shareData();data.team.onFieldSvts[2]={supportType:'friend',grandSvt:true,equip1:{id:2,limitBreak:true,candidates:[{id:999,limitBreak:false},{id:3}]},equip2:{id:999},equip3:{id:3,candidates:[{id:2}]}} as any;
 const before=structuredClone(data),member=shareImagePreview(data,catalog).roster[2];
 assert.deepEqual(member.equips.map(e=>[e.sourceKey,e.candidateIndex]),[['equip1',0],['equip1',1],['equip1',2],['equip3',0],['equip3',1]]);
 assert.deepEqual(member.equips.map(e=>e.id),[2,999,3,3,2]);
 assert.match(member.equips[0].name,/普通礼装 · 候选 1\/3.*乙.*满破/);
 assert.match(member.equips[1].name,/普通礼装 · 或 2\/3.*非满破/);
 assert.match(member.equips[4].name,/报酬礼装 · 或 2\/2/);
 assert.deepEqual(data,before);
});

test('restored image previews leave grand bond artwork to the shared effect preview without changing the saved CE',()=>{
 const data=shareData();data.team.onFieldSvts[2]={supportType:'friend',grandSvt:true,equip2:{id:2,lv:100,limitBreak:true,future:{preserved:null}},classBoardData:{grandBondEquipSkillChange:true}} as any;
 const before=structuredClone(data),member=shareImagePreview(data,catalog).roster[2];
 assert.deepEqual(member.equips,[]);assert.deepEqual(data,before);
});
