import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {selectedServantForm,projectServantForm,setServantForm} from './servantForms';
import {clone,filterCatalog,memberLabel,newBattle,replaceServant,servantFor,skillAt} from './model';
import type {Catalog,Json,Servant} from './model';
import {npAt,npAtAction,previewTurn} from './battlePreview';
import {partyCost} from './partyCost';
import {shareImageModel} from './shareImageModel';

const bundled=JSON.parse(readFileSync(new URL('../../../agent/battle_editor/data/catalog.json',import.meta.url),'utf8'));
const mash:Servant={...bundled.servants.find((item:Json)=>item.id===800100),name:'玛修·基列莱特'};
const catalog:Catalog={servants:[mash,{id:100100,name:'阿尔托莉雅',cost:16,rarity:5}],equips:[{id:9400010,name:'测试礼装',cost:12}],mysticCodes:[]};
const owned=():Json=>({svtId:800100,supportType:'none',skillLvs:[10,9,8],lv:80,equip1:{id:9400010,limitBreak:true},future:{nested:[null,false,5]}});

const paladin=():Json=>{const member=owned();setServantForm(member,mash,'paladin');return member;};
const npAttack=(svt?:number):Json=>({type:'attack',attacks:svt==null?[]:[{svt,isTD:true,card:-1}]});
const mashPlan=():Json=>({team:{onFieldSvts:[paladin(),{...paladin(),supportType:'friend'},null],backupSvts:[{svtId:100100},null,null],mysticCode:{mysticCodeId:20,level:10}},actions:[]});

test('Paladin shield grants three on-field turns of sword NP without changing saved Chaldea data',()=>{
 const data=mashPlan();data.actions=[npAttack(0),npAttack(0),npAttack(0),npAttack(0),npAttack()];
 const before=clone(data),metadata=clone(catalog);
 assert.deepEqual([0,1,2,3,4].map(turn=>previewTurn(data,catalog,turn).front[0].np?.id),[800107,800108,800108,800107,800108]);
 assert.deepEqual([0,1,2,3,4].map(turn=>previewTurn(data,catalog,turn).front[0].npState?.remainingTurns),[undefined,2,1,undefined,2]);
 assert.deepEqual([0,1,2,3,4].map(index=>npAtAction(data,catalog,index,0)?.cardType),[1,2,2,1,2]);
 assert.equal(npAtAction(data,catalog,1,0)?.name,'印证希望的人理之剑');
 assert.deepEqual(data,before);assert.deepEqual(catalog,metadata);
 assert.equal(data.team.onFieldSvts[0].tdId,800107);assert.equal(data.delegate,undefined);
});

test('loaded sword expires without another NP and earlier action edits recompute the state',()=>{
 const data=mashPlan();data.actions=[npAttack(0),npAttack(),npAttack(),npAttack()];
 assert.equal(npAtAction(data,catalog,2,0)?.id,800108);
 assert.equal(npAtAction(data,catalog,3,0)?.id,800107);
 data.actions[0].attacks=[];
 assert.equal(npAtAction(data,catalog,1,0)?.id,800107);
 data.actions[1].attacks=[{isAuto:true},{svt:0,isTD:true,card:-1}];
 assert.equal(npAtAction(data,catalog,1,0)?.id,800107);
 assert.equal(npAtAction(data,catalog,2,0)?.id,800108);
});

test('support and own Mash copies keep independent NP states',()=>{
 const data=mashPlan();data.actions=[npAttack(0),npAttack(1),npAttack()];
 let view=previewTurn(data,catalog,1);
 assert.deepEqual(view.front.slice(0,2).map(actor=>actor.np?.id),[800108,800107]);
 view=previewTurn(data,catalog,2);
 assert.deepEqual(view.front.slice(0,2).map(actor=>actor.npState?.remainingTurns),[1,2]);
});

test('Order Change moves loaded state with its member and pauses its duration in backup',()=>{
 const data=mashPlan();const swap={type:'skill',svt:null,skill:2};
 data.actions=[npAttack(0),clone(swap),npAttack(),npAttack(),clone(swap),npAttack(),npAttack(),npAttack()];
 data.delegate={replaceMemberIndexes:[[0,0],[0,0]]};
 const waiting=previewTurn(data,catalog,2);
 assert.equal(waiting.roster[3].initialSlot,0);assert.equal(waiting.roster[3].npState?.remainingTurns,2);
 const returned=previewTurn(data,catalog,3);
 assert.equal(returned.front[0].initialSlot,0);assert.equal(returned.front[0].np?.id,800108);assert.equal(returned.front[0].npState?.remainingTurns,2);
 assert.equal(previewTurn(data,catalog,4).front[0].npState?.remainingTurns,1);
 assert.equal(previewTurn(data,catalog,5).front[0].np?.id,800107);
});

test('retirement and actor guards cannot transfer or fabricate a loaded NP',()=>{
 const data=mashPlan();data.actions=[npAttack(0),{type:'retire',svt:0},npAttack()];
 const view=previewTurn(data,catalog,1);
 assert.equal(view.front[0].member?.svtId,100100);assert.equal(view.front[0].npState,undefined);
 assert.equal(view.front[1].np?.id,800107);
 data.actions=[{type:'attack',attacks:[{svt:0,isTD:true,card:-1,actor:{initialSlot:1,svtId:800100}}]},npAttack()];
 assert.equal(npAtAction(data,catalog,0,0,data.actions[0].attacks[0])?.id,800107);
 assert.equal(npAtAction(data,catalog,1,0)?.id,800107);
 assert.equal(npAtAction(data,catalog,1,1)?.id,800107);
 const mismatched=data.actions[0].attacks[0];data.actions=[npAttack(0),{type:'attack',attacks:[mismatched]}];
 assert.equal(npAtAction(data,catalog,1,0)?.id,800108);
 assert.equal(npAtAction(data,catalog,1,0,mismatched)?.id,800107);
});

test('only Paladin NP800107 grants loading; other forms and explicit NP variants stay intact',()=>{
 for(const form of mash.forms!.filter(form=>form.key!=='paladin')){
  const data=mashPlan();setServantForm(data.team.onFieldSvts[0],mash,form.key);data.actions=[npAttack(0),npAttack()];
  assert.equal(npAtAction(data,catalog,1,0)?.id,form.tdId);
  assert.equal(previewTurn(data,catalog,1).front[0].npState,undefined);
 }
 for(const tdId of [800197,800108,999999]){
  const data=mashPlan();data.team.onFieldSvts[0].tdId=tdId;data.actions=Array.from({length:5},()=>npAttack(0));
  assert.equal(npAtAction(data,catalog,4,0)?.id,tdId);
  assert.equal(previewTurn(data,catalog,4).front[0].npState,undefined);
 }
 const data=mashPlan();data.actions=[{type:'attack',attacks:[{isAuto:true,svt:0,isTD:true}]},npAttack()];
 assert.equal(npAtAction(data,catalog,1,0)?.id,800107);
 assert.equal(npAtAction(data,catalog,1,3),undefined);assert.equal(npAtAction(data,catalog,99,0),undefined);
 // Chaldea follows the explicit NP's effects even if the costume is unknown.
 data.team.onFieldSvts[0].limitCount=999999;data.actions=[npAttack(0),npAttack()];
 assert.equal(npAtAction(data,catalog,1,0)?.id,800108);
});

test('all six Mash builds save coherent standard Chaldea IDs without altering member requirements',()=>{
 assert.equal(mash.forms?.length,6);
 for(const form of mash.forms!){
  const member=owned(),before=clone(member);setServantForm(member,mash,form.key);
  assert.equal(member.svtId,800100);
  assert.deepEqual(member.future,before.future);
  assert.deepEqual(member,{...before,limitCount:form.limitCount,skillIds:form.skillIds,tdId:form.tdId});
  const view=projectServantForm(mash,member)!;
  assert.equal(view.rarity,form.rarity);assert.equal(view.cost,form.cost);
  assert.deepEqual([0,1,2].map(index=>skillAt(member,mash,index)?.id),form.skillIds);
  assert.equal(npAt(member,mash)?.id,form.tdId);
  assert.equal(selectedServantForm(mash,member)?.key,form.key);
 }
});

test('Chaldea ascensions and small or full costume numbers agree, including five-star summer Ver.03',()=>{
 for(const limit of [0,1,2])assert.equal(projectServantForm(mash,{limitCount:limit})?.rarity,3);
 for(const limit of [3,4,11,800130,14,800160,15,800170]){
  assert.equal(projectServantForm(mash,{limitCount:limit})?.rarity,4);
  assert.equal(projectServantForm(mash,{limitCount:limit})?.cost,0);
 }
 for(const limit of [12,800140,13,800150,18,800151])assert.equal(selectedServantForm(mash,{limitCount:limit})?.key,'ortenaus-restored');
 for(const limit of [17,800190,19,800200,20,800210]){
  const member={svtId:800100,limitCount:limit};
  assert.equal(projectServantForm(mash,member)?.rarity,5);
  assert.equal(projectServantForm(mash,member)?.cost,16);
  assert.equal(npAt(member,mash)?.id,800107);
 }
});

test('viewing omitted defaults or explicit historic variants is lossless and uses the chosen skills first',()=>{
 const member={...owned(),limitCount:800140,skillIds:[744450,460250,457000],tdId:800105};
 const before=JSON.stringify(member);
 assert.equal(selectedServantForm(mash,member)?.key,'black-barrel');
 assert.equal(skillAt(member,mash,2)?.id,457000);
 assert.equal(npAt(member,mash)?.id,800105);
 assert.equal(JSON.stringify(member),before);
 const legacy={svtId:800100,skillIds:[null,0,9999999],tdId:800108,future:true};
 const raw=JSON.stringify(legacy);
 assert.equal(selectedServantForm(mash,legacy)?.key,'normal');
 assert.equal(projectServantForm(mash,legacy)?.cost,0);
 assert.equal(skillAt(legacy,mash,1)?.id,2000);
 assert.equal(skillAt(legacy,mash,2)?.id,9999999);
 assert.equal(npAt(legacy,mash)?.cardType,2);
 assert.equal(JSON.stringify(legacy),raw);
});

test('owned Mash cost includes the selected form and CE, while support form costs remain free',()=>{
 const data=newBattle(),member=owned();data.team.onFieldSvts[0]=member;
 setServantForm(member,mash,'normal');assert.deepEqual(partyCost(data,catalog),{knownTotal:12,complete:true,issues:[]});
 setServantForm(member,mash,'paladin');assert.deepEqual(partyCost(data,catalog),{knownTotal:28,complete:true,issues:[]});
 member.supportType='friend';assert.deepEqual(partyCost(data,catalog),{knownTotal:0,complete:true,issues:[]});
});

test('battle preview takes selected cooldowns and ally targets instead of cross-form catalog defaults',()=>{
 const data=newBattle(),member=owned();data.team.onFieldSvts[0]=member;
 setServantForm(member,mash,'ortenaus-restored');
 let preview=previewTurn(data,catalog,0);
 assert.equal(preview.front[0].skills[1].skill?.id,460250);
 assert.equal(preview.front[0].skills[1].skill?.target,'none');
 setServantForm(member,mash,'paladin');preview=previewTurn(data,catalog,0);
 assert.equal(preview.front[0].skills[1].skill?.id,2550);
 assert.equal(preview.front[0].skills[1].skill?.target,'ally');
 assert.equal(preview.front[0].servant?.cost,16);
 assert.equal(memberLabel(member,catalog),'玛修·基列莱特【'+mash.forms!.find(form=>form.key==='paladin')!.label+'】');
});

test('share projection, serialization and undo snapshots retain the selected form without editor-only fields',()=>{
 const data=newBattle(),member=owned();data.team.onFieldSvts[0]=member;
 setServantForm(member,mash,'paladin');
 data.actions=[{type:'skill',svt:0,skill:1,options:{playerTarget:0}},{type:'attack',attacks:[{svt:0,isTD:true,card:-1}]}];
 const original=JSON.stringify(data),parsed=JSON.parse(original);
 const share=shareImageModel(parsed,catalog);
 assert.equal(share.members[0].rarity,'★★★★★');
 assert.equal(share.cost,'28 / 117');
 assert.equal(JSON.stringify(parsed),original);
 const before=clone(parsed);setServantForm(parsed.team.onFieldSvts[0],mash,'early');
 assert.equal(servantFor(catalog,800100,parsed.team.onFieldSvts[0])?.rarity,3);
 assert.equal(servantFor(catalog,800100,before.team.onFieldSvts[0])?.rarity,5);
 assert.deepEqual(Object.keys(parsed.team.onFieldSvts[0]).sort(),Object.keys(before.team.onFieldSvts[0]).sort());
});

test('unknown form imports keep explicit skills and NP, but never claim a known zero cost',()=>{
 const member={...owned(),limitCount:999999,skillIds:[2476450,2550,133550],tdId:800107};
 const raw=JSON.stringify(member),data=newBattle();data.team.onFieldSvts[0]=member;
 assert.equal(selectedServantForm(mash,member),undefined);
 assert.equal(projectServantForm(mash,member)?.cost,null);
 assert.equal(partyCost(data,catalog).complete,false);
 assert.equal(skillAt(member,mash,1)?.id,2550);
 assert.equal(npAt(member,mash)?.id,800107);
 assert.equal(JSON.stringify(member),raw);
});

test('one canonical Mash remains visible at every applicable rarity and replacing her resets form IDs',()=>{
 for(const rarity of ['3','4','5'])assert.equal(filterCatalog('servant',catalog,{rarity}).filter(item=>item.id===800100).length,1);
 const member=owned();setServantForm(member,mash,'paladin');
 const next=replaceServant(member,100100,catalog);
 for(const field of ['limitCount','skillIds','tdId'])assert.equal(next[field],undefined);
 assert.deepEqual(next.equip1,member.equip1);assert.deepEqual(next.future,member.future);
});
