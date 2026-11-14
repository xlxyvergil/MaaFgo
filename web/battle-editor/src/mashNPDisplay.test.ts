import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import type {Catalog,Json,Servant} from './model';
import {setServantForm} from './servantForms';
import {shareImageModel} from './shareImageModel';

const bundled=JSON.parse(readFileSync(new URL('../../../agent/battle_editor/data/catalog.json',import.meta.url),'utf8'));
const mash:Servant={...bundled.servants.find((item:Json)=>item.id===800100),name:'玛修·基列莱特'};
const catalog:Catalog={servants:[mash],equips:[],mysticCodes:[]};
const config={enabled:true,regions:['JP','CN'],defaultRegion:'JP',endpoint:'/api/np-card',faceEndpoint:'/api/command-card'};

test('share image uses the NP name, color and card artwork at each attack boundary',()=>{
 const member:Json={svtId:800100,skillLvs:[10,10,10]};setServantForm(member,mash,'paladin');
 const data:Json={team:{onFieldSvts:[member,null,null],backupSvts:[null,null,null]},actions:[
  {type:'attack',attacks:[{isTD:true,svt:0,card:-1}]},
  {type:'attack',attacks:[{isTD:true,svt:0,card:-1}]},
 ]};
 const original=structuredClone(data),model=shareImageModel(data,catalog,{cardPreviewConfig:config});
 const first=model.turns[0].actions[0],second=model.turns[1].actions[0];
 assert.equal(first.kind,'attack');assert.equal(second.kind,'attack');
 if(first.kind!=='attack'||second.kind!=='attack')throw new Error('expected attack rows');
 assert.equal(first.cards[0].name,mash.npVariants!.find(np=>np.id===800107)!.name);
 assert.equal(first.cards[0].color,'arts');assert.match(first.cards[0].image!,/tdId=800107(?:&|$)/);
 assert.equal(second.cards[0].name,mash.npVariants!.find(np=>np.id===800108)!.name);
 assert.equal(second.cards[0].color,'buster');assert.match(second.cards[0].image!,/tdId=800108(?:&|$)/);
 assert.deepEqual(data,original);
 assert.equal(data.team.onFieldSvts[0].tdId,800107);
});

test('BB Dubai defense uses G.G.G. Arts artwork in the summary and returns to the initial NP after expiry',()=>{
 const bb:Servant={...bundled.servants.find((item:Json)=>item.id===2300600),name:'BB迪拜'};
 const bbCatalog:Catalog={servants:[bb],equips:[],mysticCodes:[],specialSkillChoices:{schemaVersion:1,groups:[{
  id:'bb-dubai',servantIds:[2300600],skillNumbers:[3],field:'tdTypeChanges',defaultValue:1,durationTurns:3,
  options:[{value:1,label:'攻击宝具（C.C.C.）',cardType:1,npId:2300601},{value:2,label:'防御宝具（G.G.G.）',cardType:1,npId:2300698}],
 }]}};
 const data:Json={team:{onFieldSvts:[{svtId:2300600},null,null],backupSvts:[null,null,null]},delegate:{tdTypeChanges:[2]},
  actions:[{type:'skill',svt:0,skill:2},...Array.from({length:4},()=>({type:'attack',attacks:[{isTD:true,svt:0,card:-1}]}))]};
 const before=structuredClone(data),model=shareImageModel(data,bbCatalog,{cardPreviewConfig:config});
 const first=model.turns[0].actions.at(-1)!,last=model.turns[3].actions.at(-1)!;
 assert.equal(first.kind,'attack');assert.equal(last.kind,'attack');
 if(first.kind!=='attack'||last.kind!=='attack')throw new Error('expected attack rows');
 assert.equal(first.cards[0].name,'Ｇ.Ｇ.Ｇ.');assert.equal(first.cards[0].color,'arts');
 assert.match(first.cards[0].image!,/tdId=2300698(?:&|$)/);
 assert.equal(last.cards[0].name,'Ｃ.Ｃ.Ｃ.');assert.match(last.cards[0].image!,/tdId=2300601(?:&|$)/);
 assert.deepEqual(data,before);
});
