import test from 'node:test';
import assert from 'node:assert/strict';
import {SHARE_IMAGE_MAX_ACTIONS,SHARE_IMAGE_MAX_TEXT,shareImageModel} from './shareImageModel';
import type {Catalog,Json} from './model';
import type {CardPreviewConfig} from './npCardRequest';

const catalog:Catalog={
 servants:[
  {id:100,name:'前排甲',aliases:[101],className:'剑士',rarity:5,cost:16,image:'/media/servant/a.png',cards:[2,2,1,1,3],skills:[{id:1,name:'甲技能一',icon:'/media/skill/1.png'},{id:2,name:'甲技能二'},{id:3,name:'甲技能三'}],np:{id:11,name:'甲宝具',cardType:2}},
  {id:200,name:'候补乙',className:'术',rarity:4,cost:12,image:'/media/servant/b.png',cards:[1,1,1,2,3],skills:[{id:21,name:'乙技能一'},{id:22,name:'乙技能二'},{id:23,name:'乙技能三'}],np:{id:201,name:'乙宝具',cardType:1},npVariants:[{id:202,name:'乙强化宝具',cardType:1}]},
 ],equips:[{id:1,name:'万华镜',cost:12,image:'/media/equip/a.png'},{id:2,name:'羁绊礼装',cost:9},{id:3,name:'报酬礼装',cost:12}],
 mysticCodes:[{id:210,name:'决战用迦勒底制服',orderChange:true,image:'/media/ui/master.png',skills:[{id:1,num:1,name:'御主技能一'},{id:2,num:2,name:'御主技能二'},{id:3,num:3,name:'Order Change'}]}],
};
const config:CardPreviewConfig={enabled:true,regions:['JP','CN'],defaultRegion:'JP',endpoint:'/api/np-card',faceEndpoint:'/api/command-card'};
function team():Json{return {team:{name:'测试方案',onFieldSvts:[{svtId:101,skillLvs:[10,8,0]},null,null],backupSvts:[{svtId:200,tdId:202,skillLvs:[6,7,8]},null,null],mysticCode:{mysticCodeId:210,level:10}},actions:[],delegate:{replaceMemberIndexes:[]}};}

test('share projection keeps zero turns distinct from an explicit empty attack',()=>{
 const data=team(),original=structuredClone(data);
 for(const form of ['missing','null','empty']){
  if(form==='missing')delete data.actions;else data.actions=form==='null'?null:[];
  assert.equal(shareImageModel(data,catalog).turns.length,0);
 }
 data.actions=[{type:'attack',attacks:[]}];
 const model=shareImageModel(data,catalog);
 assert.equal(model.turns.length,1);assert.equal(model.turns[0].actions.length,1);
 const attack=model.turns[0].actions[0];assert.equal(attack.kind,'attack');
 if(attack.kind!=='attack')throw new Error('expected attack');
 assert.equal(attack.cards.length,3);assert.equal(attack.cards.every(card=>card.automatic),true);
 assert.deepEqual(data.team,original.team);assert.deepEqual(data.actions,[{type:'attack',attacks:[]}]);
});

test('share skill rows include their own special choice after ordinary skills and an order change',()=>{
 const withChoices:Catalog={...catalog,specialSkillChoices:{schemaVersion:1,groups:[
  {id:'test-np-color',servantIds:[100],skillNumbers:[2],field:'tdTypeChanges',defaultValue:1,options:[{value:1,label:'Arts 宝具'},{value:2,label:'Buster 宝具'},{value:3,label:'Quick 宝具'}]},
  {id:'test-option',servantIds:[200],skillNumbers:[1],field:'skillActSelectSelections',defaultValue:0,options:[{value:0,label:'不消耗'},{value:1,label:'消耗 10 个暴击星'}]},
 ]}};
 const data=team();data.actions=[{type:'skill',svt:0,skill:0},{type:'skill',svt:0,skill:1},{type:'skill',svt:null,skill:2},{type:'skill',svt:0,skill:0},{type:'attack',attacks:[]}];
 data.delegate={replaceMemberIndexes:[[0,0]],tdTypeChanges:['quick'],skillActSelectSelections:[1],unknownKeep:{value:true}};
 const before=structuredClone(data),rows=shareImageModel(data,withChoices).turns[0].actions;
 assert.equal(rows[0].kind,'skill');assert.equal(rows[0].choice,undefined);
 if(rows[1].kind==='attack'||rows[3].kind==='attack')throw new Error('expected skills');
 assert.match(rows[1].choice!,/Quick 宝具/);assert.match(rows[3].choice!,/消耗 10 个暴击星/);
 assert.equal(rows[3].actor.name,'候补乙');assert.deepEqual(data,before);
});

test('share rows resolve aliases and use the roster at the exact action point after a swap',()=>{
 const data=team();data.actions=[{type:'skill',svt:0,skill:0,options:{playerTarget:0}},{type:'skill',svt:null,skill:2},{type:'skill',svt:0,skill:1,options:{playerTarget:0,enemyTarget:2}},{type:'attack',attacks:[{isTD:true,svt:0}]}];
 data.delegate.replaceMemberIndexes=[[0,0]];
 const before=structuredClone(data),view=shareImageModel(data,catalog,{cardPreviewConfig:config,validation:{valid:true,issues:[]}}),rows=view.turns[0].actions;
 assert.equal(view.members[0].name,'前排甲');assert.equal(view.members[3].name,'候补乙');
 assert.equal(view.mystic.skills[2].name,'换人');
 assert.equal(rows[0].kind,'skill');assert.equal(rows[0].actor.name,'前排甲');assert.equal(rows[0].target?.name,'前排甲');
 assert.equal(rows[1].kind,'swap');assert.deepEqual(rows[1].swap?.map(p=>p.name),['前排甲','候补乙']);
 assert.equal(rows[2].kind,'skill');assert.equal(rows[2].actor.name,'候补乙');assert.equal(rows[2].name,'乙技能二');assert.equal(rows[2].level,'Lv.7');assert.equal(rows[2].target?.name,'候补乙');assert.equal(rows[2].enemy,'敌方 1 · 左侧');
 assert.equal(rows[3].kind,'attack');if(rows[3].kind==='attack'){assert.equal(rows[3].cards[0].name,'乙强化宝具');assert.match(rows[3].cards[0].image!,/svtId=200.*tdId=202/);}
 assert.deepEqual(data,before);
});

test('share attack sequence retains an automatic hole before NP and precise color/card intent',()=>{
 const data=team();data.actions=[{type:'attack',attacks:[{isAuto:true},{isTD:true,svt:0},{isTD:false,svt:0,card:1,cardType:2}],options:{enemyTarget:0}}];
 const row=shareImageModel(data,catalog,{cardPreviewConfig:config}).turns[0].actions[0];
 assert.equal(row.kind,'attack');if(row.kind!=='attack')throw new Error('expected attack');
 assert.deepEqual(row.cards.map(card=>[card.automatic,card.label]),[[true,'默认普通卡'],[false,'宝具 NP'],[false,'Buster · 红卡']]);
 assert.equal(row.cards[2].deckLabel,'牌组第 2 张');assert.match(row.cards[2].image!,/color=buster/);assert.equal(row.enemy,'敌方 3 · 右侧');
 assert.equal(data.actions[0].attacks.length,3);
});

test('unspecified support and legacy CE restrictions are readable without inventing servant attributes',()=>{
 const data=team();data.team.onFieldSvts=[{supportType:'friend',grandSvt:true,ceId:1,ceLimitBreak:false,equip2:{id:2},equip3:{id:3,limitBreak:true}},null,null];
 const view=shareImageModel(data,catalog),member=view.members[0];
 assert.equal(member.name,'助战（仅要求礼装）');assert.equal(member.support,'好友助战');assert.equal(member.image,undefined);assert.deepEqual(member.skills,[]);
 assert.deepEqual(member.equips.map(e=>[e.label,e.name]),[['普通礼装','万华镜'],['羁绊礼装','羁绊礼装'],['报酬礼装','报酬礼装']]);
 assert.match(member.equips[0].details,/非满破/);assert.equal(member.equips[1].details,'不限制');assert.equal(member.equips[1].image,undefined);assert.match(member.equips[2].details,/ · 满破/);
 assert.equal(view.members[1].name,'不要求此位置');assert.equal(member.details,'Lv.— · 宝具 —');
});

test('missing attack tails are displayed as defaults without writing new actions or cards',()=>{
 const data=team();data.actions=[{type:'skill',svt:0,skill:0}];
 const view=shareImageModel(data,catalog);assert.equal(view.turns[0].implicitEnd,true);assert.equal(view.turns[0].actions.length,1);
 data.actions.push({type:'attack',attacks:[]});const attack=shareImageModel(data,catalog).turns[0].actions[1];
 assert.equal(attack.kind,'attack');if(attack.kind==='attack')assert.deepEqual(attack.cards.map(c=>c.automatic),[true,true,true]);
 assert.deepEqual(data.actions[1].attacks,[]);
});

test('draft status and blockers stay explicit while unknown editor/local metadata is not rendered',()=>{
 const data=team();data._source='bbc';data.privateEditorState={token:'local-secret',sourcePath:'F:/private/local.json'};
 const view=shareImageModel(data,catalog,{validation:{valid:false,issues:[{severity:'error',message:'缺少从者'},{severity:'warning',message:'核对助战'}]},costLimit:118});
 assert.match(view.state,/草稿.*1 项阻断/);assert.equal(view.blocking,1);assert.equal(view.warnings,1);assert.match(view.cost,/\/ 118/);
 assert.doesNotMatch(JSON.stringify(view),/local-secret|private\/local/);
 assert.equal(shareImageModel(data,catalog,{validation:{valid:true,issues:[]}}).state,'通过结构检查');
 assert.match(shareImageModel(data,catalog).state,/草稿/);
});

test('unsupported actions and invalid swap pairs stay visible without changing later identities',()=>{
 const data=team();data.actions=[{type:'future-action',private:'do not render raw'},{type:'skill',svt:null,skill:2},{type:'skill',svt:0,skill:1}];data.delegate.replaceMemberIndexes=[[0,99]];
 const view=shareImageModel(data,catalog),rows=view.turns[0].actions;
 assert.equal(rows[0].kind,'unknown');assert.ok(view.notices.some(note=>note.includes('未支持')));
 if(rows[1].kind!=='attack')assert.match(rows[1].detail,/换人双方/);
 if(rows[2].kind!=='attack')assert.equal(rows[2].actor.name,'前排甲');
 assert.doesNotMatch(JSON.stringify(view),/do not render raw/);
});

test('single-image projection rejects excessive actions and visible text before canvas layout',()=>{
 const data=team();data.actions=Array.from({length:SHARE_IMAGE_MAX_ACTIONS+1},()=>({type:'attack',attacks:[]}));
 assert.throws(()=>shareImageModel(data,catalog),/200 条动作.*拆分/);
 data.actions=[];data.team.name='长'.repeat(SHARE_IMAGE_MAX_TEXT+1);
 assert.throws(()=>shareImageModel(data,catalog),/可见文字.*不会截断/);
 data.team.name='可见文字累计';data.team.onFieldSvts[0].appendLvs=Array(SHARE_IMAGE_MAX_TEXT/2+1).fill(1);
 assert.throws(()=>shareImageModel(data,catalog),/可见文字/);
 data.team.onFieldSvts[0].appendLvs=[];data.actions=[{type:'future-'+'x'.repeat(SHARE_IMAGE_MAX_TEXT)}];
 assert.throws(()=>shareImageModel(data,catalog),/可见文字/);
});

test('unrendered unknown JSON remains outside the visible-text budget',()=>{
 const data=team(),hidden='保真数据'.repeat(SHARE_IMAGE_MAX_TEXT);
 data.preservedUnknown={long:hidden};data.team.onFieldSvts[0].privateExtension={long:hidden};
 const view=shareImageModel(data,catalog);
 assert.equal(view.title,'测试方案');assert.equal(data.preservedUnknown.long,hidden);
 assert.doesNotMatch(JSON.stringify(view),/保真数据/);
});

test('share formation presents same-slot alternatives as OR while keeping grand CE slots separate',()=>{
 const data=team();data.team.onFieldSvts[0]={supportType:'friend',grandSvt:true,
  equip1:{id:1,lv:100,limitBreak:true,candidates:[{id:3,limitBreak:false,future:{keep:null}}]},
  equip2:{id:2},equip3:{id:3,candidates:[{id:1,lv:50}]},future:{keep:['unchanged']}};
 const before=structuredClone(data),equips=shareImageModel(data,catalog).members[0].equips;
 assert.deepEqual(equips.map(e=>e.name),['万华镜','报酬礼装','羁绊礼装','报酬礼装','万华镜']);
 assert.deepEqual(equips.map(e=>e.label),['普通礼装 · 候选 1/2','或 · 候选 2/2','羁绊礼装','报酬礼装 · 候选 1/2','或 · 候选 2/2']);
 assert.match(equips[0].details,/Lv.100 · 满破/);assert.match(equips[1].details,/非满破/);assert.match(equips[4].details,/Lv.50 · 不要求满破/);
 assert.deepEqual(equips.map(e=>e.candidateCount),[2,2,undefined,2,2]);
 assert.deepEqual(data,before);
});

test('grand bond effects render without choosing a CE and never expose imported bond CE artwork',()=>{
 for(const [effect,label] of [[true,'NP 50% 替换效果'],[false,'原始效果'],[undefined,'不限制']] as const){
  for(const legacyEquip of [undefined,{id:1,lv:100,limitBreak:true,future:{keep:['原卡面']}}]){
   const data=team();data.team.onFieldSvts[0]={supportType:'friend',grandSvt:true,classBoardData:{future:null,...(effect===undefined?{}:{grandBondEquipSkillChange:effect})},...(legacyEquip?{equip2:legacyEquip}:{})};
   const before=structuredClone(data),equips=shareImageModel(data,catalog).members[0].equips;
   assert.deepEqual(equips,[{label:'羁绊礼装',name:'羁绊礼装',details:label}]);
   assert.deepEqual(data,before);
  }
 }
});

test('share alternatives resolve missing metadata with text and retain the legacy primary requirements',()=>{
 const data=team();data.team.onFieldSvts[0]={supportType:'friend',ceId:1,ceLv:80,ceLimitBreak:false,equip1:{candidates:[{id:999,limitBreak:true}]}};
 const before=structuredClone(data),equips=shareImageModel(data,catalog).members[0].equips;
 assert.equal(equips.length,2);assert.equal(equips[0].name,'万华镜');assert.match(equips[0].details,/Lv.80 · 非满破/);
 assert.equal(equips[1].name,'礼装 #999');assert.match(equips[1].details,/满破/);assert.equal(equips[1].image,undefined);
 assert.deepEqual(data,before);
});
