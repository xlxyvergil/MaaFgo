import {actionMemberAt,assertEditable,hasServantIdentity,isSwap,memberLabel,members,retirementInfoAt,servantFor,skillAt,turnsOf} from './model';
import {actionRosterAt,isRetire,resolveActionActor} from './actionRoster';
import type {Catalog,Json} from './model';
import {equipCandidatesForDisplay} from './equipRequirements';
import {grandBondEffectLabel} from './grandBondEffect';
import {isAutoAttackCard} from './attackCards';
import {npAtAction} from './battlePreview';
import {artworkColor,cardArtworkRequest,cardPreviewRegion} from './npCardRequest';
import type {CardPreviewConfig} from './npCardRequest';
import {partyCost} from './partyCost';
import {specialSkillChoiceLabel} from './specialSkillChoices';

export const SHARE_IMAGE_WIDTH=1200;
export const SHARE_IMAGE_MAX_HEIGHT=12000;
export const SHARE_IMAGE_MAX_PIXELS=20_000_000;
export const SHARE_IMAGE_MAX_ACTIONS=200;
export const SHARE_IMAGE_MAX_TEXT=50_000;
export interface SharePerson {name:string;position:string;image?:string|null;classIcon?:string|null;support:string;slot:number|null;identified:boolean}
export interface ShareEquip {name:string;image?:string|null;label:string;details:string;candidateIndex?:number;candidateCount?:number;kind?:'bond-effect'}
export interface ShareMember extends SharePerson {className:string;rarity:string;details:string;skills:{name:string;icon?:string;level:string}[];append:string;grand:boolean;equips:ShareEquip[]}
export interface ShareCard {person?:SharePerson;label:string;name:string;color:string|null;image:string|null;automatic:boolean;deckLabel:string}
export interface ShareSkill {kind:'skill'|'swap'|'retire'|'unknown';actor:SharePerson;name:string;icon?:string;number:string;level:string;target?:SharePerson;swap?:[SharePerson,SharePerson];enemy:string;detail:string;choice?:string}
export interface ShareAttack {kind:'attack';cards:ShareCard[];enemy:string}
export type ShareAction=ShareSkill|ShareAttack;
export interface ShareTurn {number:number;actions:ShareAction[];implicitEnd:boolean}
export interface ShareImageModel {title:string;members:ShareMember[];mystic:{name:string;image?:string|null;level:string;skills:{name:string;icon?:string;number:number}[]};turns:ShareTurn[];actions:number;cost:string;state:string;blocking:number;warnings:number;notices:string[]}

const front=(value:unknown):value is number=>typeof value==='number'&&Number.isInteger(value)&&value>=0&&value<3;
const validSlot=(value:unknown):value is number=>typeof value==='number'&&Number.isInteger(value)&&value>=0&&value<6;
const level=(value:unknown):string=>typeof value==='number'&&Number.isInteger(value)&&value>0?String(value):'—';
function person(member:Json|null,slot:number|null,catalog:Catalog):SharePerson {
 const servant=hasServantIdentity(member)?servantFor(catalog,member.svtId,member):undefined;
 const support=member?.supportType==='friend'?'好友助战':member?.supportType==='npc'?'NPC 助战':member?.supportType==='fixed'?'固定助战':'';
 return {slot,name:member?memberLabel(member,catalog):'不要求此位置',position:slot==null?'御主':`${slot<3?'前排':'候补'} ${slot+1}`,image:servant?.image,classIcon:servant?.classIcon,support,identified:hasServantIdentity(member)};
}
function enemyLabel(value:unknown):string {
 return value==null?'':value===2?'敌方 1 · 左侧':value===1?'敌方 2 · 中间':value===0?'敌方 3 · 右侧':`敌方目标未支持（${String(value)}）`;
}
function rejectLongText():never {throw new Error('方案的可见文字超过单张图片排版容量，请缩短名称、说明或拆分方案后导出；不会截断内容。');}
function assertTextBudget(model:ShareImageModel):void {
 // Only this display projection is counted. Image URLs and original unknown
 // JSON metadata are not visible text and do not consume the layout budget.
 const hidden=new Set(['image','icon','classIcon','kind','color']);
 const pending:unknown[]=[model];let remaining=SHARE_IMAGE_MAX_TEXT;
 while(pending.length){
  const value=pending.pop();
  if(typeof value==='string'){remaining-=value.length;if(remaining<0)rejectLongText();}
  else if(Array.isArray(value))pending.push(...value);
  else if(value&&typeof value==='object')for(const [key,child]of Object.entries(value))if(!hidden.has(key))pending.push(child);
 }
}

/** Project the recorded plan only; no inventory, editor state or unknown local fields are rendered. */
export function shareImageModel(data:Json,catalog:Catalog,options:{region?:string;cardPreviewConfig?:CardPreviewConfig;validation?:Json;costLimit?:number}={}):ShareImageModel {
 if(Array.isArray(data?.actions)&&data.actions.length>SHARE_IMAGE_MAX_ACTIONS)throw new Error(`方案超过 ${SHARE_IMAGE_MAX_ACTIONS} 条动作，无法放入一张分享图片，请拆分方案后导出；不会截断内容。`);
 if(typeof data?.team?.name==='string'&&data.team.name.length>SHARE_IMAGE_MAX_TEXT)rejectLongText();
 if(Array.isArray(data?.actions)&&data.actions.reduce((count:number,action:Json)=>count+(Array.isArray(action?.attacks)?action.attacks.length:0),0)>SHARE_IMAGE_MAX_ACTIONS*3)throw new Error('方案的出卡记录过多，无法放入一张分享图片，请拆分方案后导出；不会截断内容。');
 assertEditable(data);
 const initial=members(data),mystic=catalog.mysticCodes.find(item=>item.id===data.team?.mysticCode?.mysticCodeId);
 if(initial.some(member=>Array.isArray(member?.appendLvs)&&member.appendLvs.length>SHARE_IMAGE_MAX_TEXT/2))rejectLongText();
 const master:SharePerson={slot:null,name:mystic?.name??'御主礼装未指定',position:'御主',image:mystic?.image,support:'',identified:true};
 let roster=[...initial];const notices=new Set<string>();
 const selectedRegion=cardPreviewRegion(options.region,options.cardPreviewConfig);
 const lookup=(slot:unknown):SharePerson=>validSlot(slot)?person(roster[slot],slot,catalog):{slot:null,name:'目标位置无效',position:'待修正',support:'',identified:false};
 const turns=turnsOf(data,catalog).map((turn,index):ShareTurn=>({number:index+1,implicitEnd:turn.entries.at(-1)?.raw?.type!=='attack',actions:turn.entries.map(entry=>{
  const action=entry.raw;
  roster=actionRosterAt(data,entry.index).map(actor=>actor.member);
  if(isRetire(action)){
   const info=retirementInfoAt(data,entry.index,catalog);if(info.error)notices.add(info.error);
   return {kind:'retire',actor:person(info.member,front(action.svt)?action.svt:null,catalog),name:'从者退场',number:'',level:'',enemy:'',detail:info.error??'等待从者自然退场后递补，不会主动使从者死亡。',...(info.replacement?{target:person(info.replacement,front(action.svt)?action.svt:null,catalog)}:{})};
  }
  if(action?.type==='attack'){
   const recorded=Array.isArray(action.attacks)?action.attacks:[];
   const cards:ShareCard[]=Array.from({length:Math.max(3,recorded.length)},(_,cardIndex)=>{
    const card=recorded[cardIndex];
    if(card==null||isAutoAttackCard(card))return {label:'默认普通卡',name:'按当前策略补齐',color:null,image:null,automatic:true,deckLabel:''};
    const member=front(card.svt)?actionMemberAt(data,entry.index,card):undefined;
    const np=card.isTD?npAtAction(data,catalog,entry.index,card.svt,card):undefined,color=card.isTD?np?.cardType:card.cardType;
    const normalized=artworkColor(color),request=cardArtworkRequest({member,np,color,kind:card.isTD?'np':'face',region:selectedRegion,config:options.cardPreviewConfig});
    if(!hasServantIdentity(member))notices.add('存在未指定从者的技能或出卡，请先检查执行条件。');
    const issue=resolveActionActor(actionRosterAt(data,entry.index),card).issue;if(issue)notices.add(issue.message);
    return {person:person(member??null,front(card.svt)?card.svt:null,catalog),label:card.isTD?'宝具 NP':normalized?`${normalized==='buster'?'Buster · 红卡':normalized==='arts'?'Arts · 蓝卡':'Quick · 绿卡'}`:'未知普通卡',name:card.isTD?np?.name??'宝具资料未知':memberLabel(member,catalog),color:normalized,image:request.url,automatic:false,deckLabel:!card.isTD&&Number.isInteger(card.card)&&card.card>=0&&card.card<5?`牌组第 ${card.card+1} 张`:''};
   });
   return {kind:'attack',cards,enemy:enemyLabel(action.options?.enemyTarget)};
  }
  if(action?.type!=='skill'){
   notices.add('图片包含当前执行器未支持的动作；完整记录仍保留在回导标记中。');
   return {kind:'unknown',actor:{slot:null,name:'保留动作',position:'未支持动作',support:'',identified:false},name:typeof action?.type==='string'?action.type:'未知动作',number:'?',level:'',enemy:'',detail:'请在编辑器中检查这条动作。'};
  }
  const isMaster=action.svt==null,member=front(action.svt)?actionMemberAt(data,entry.index,action):undefined;
  const servant=hasServantIdentity(member)?servantFor(catalog,member.svtId,member):undefined;
  const validSkill=Number.isInteger(action.skill)&&action.skill>=0&&action.skill<3;
  const skill=validSkill?(isMaster?mystic?.skills?.find(item=>item.num===action.skill+1)??mystic?.skills?.find((item,i)=>item.num==null&&i===action.skill):skillAt(member??undefined,servant,action.skill)):undefined;
  const swap=isSwap(data,action),pair=entry.replacement;
  const frontSlot=Array.isArray(pair)?pair[0]:undefined,rawBack=Array.isArray(pair)?pair[1]:undefined,backSlot=Number.isInteger(rawBack)?rawBack<3?rawBack+3:rawBack:undefined;
  const validPair=front(frontSlot)&&validSlot(backSlot)&&backSlot>=3;
  const actorIssue=isMaster?undefined:resolveActionActor(actionRosterAt(data,entry.index),action).issue;
  const projected:ShareSkill={kind:swap?'swap':'skill',actor:isMaster?master:person(member??null,front(action.svt)?action.svt:null,catalog),name:swap?'换人':skill?.name??`技能 ${Number(action.skill)+1}`,icon:skill?.icon,number:validSkill?String(action.skill+1):'?',level:`Lv.${level(isMaster?data.team?.mysticCode?.level:member?.skillLvs?.[action.skill])}`,enemy:enemyLabel(action.options?.enemyTarget),detail:actorIssue?.message??(!isMaster&&!hasServantIdentity(member)?'未指定从者，无法安排此技能':'')};
  const choice=specialSkillChoiceLabel(entry,catalog);if(choice)projected.choice=choice;
  if(projected.detail)notices.add('存在未指定从者的技能或出卡，请先检查执行条件。');
  if(swap){
   if(validPair){projected.swap=[lookup(frontSlot),lookup(backSlot)];[roster[frontSlot],roster[backSlot]]=[roster[backSlot],roster[frontSlot]];}
   else {projected.detail='换人双方未配对或位置无效，请检查。';notices.add(projected.detail);}
  }else if(action.options?.playerTarget!=null)projected.target=lookup(action.options.playerTarget);
  return projected;
 })}));
 const cost=partyCost(data,catalog),limit=options.costLimit??117;
 const issues=Array.isArray(options.validation?.issues)?options.validation!.issues:[];
 const blocking=issues.filter((issue:Json)=>issue.severity==='error').length,warnings=issues.filter((issue:Json)=>issue.severity==='warning').length;
 const model:ShareImageModel={
  title:typeof data.team?.name==='string'&&data.team.name.trim()?data.team.name.trim():'MaaFGO 编队方案',
  members:initial.map((member,slot)=>{
   const servant=hasServantIdentity(member)?servantFor(catalog,member.svtId,member):undefined;
   const grand=member?.grandSvt===true;
   const equips=['equip1','equip2','equip3'].flatMap<ShareEquip>((key,index)=>{
    if(grand&&key==='equip2')return [{kind:'bond-effect',label:'羁绊礼装',name:'羁绊礼装',details:grandBondEffectLabel(member)}];
    const candidates=equipCandidatesForDisplay(member,key),label=grand?['普通礼装','羁绊礼装','报酬礼装'][index]:`礼装 ${index+1}`;
    return candidates.map((requirement,candidateIndex)=>{
     const item=catalog.equips.find(equip=>equip.id===requirement.id);
     return {label:candidates.length>1?`${candidateIndex?'或':label} · 候选 ${candidateIndex+1}/${candidates.length}`:label,name:item?.name??(requirement.id?`礼装 #${requirement.id}`:'未指定候选'),image:item?.image,details:`Lv.${level(requirement.lv)} · ${requirement.limitBreak===true?'满破':requirement.limitBreak===false?'非满破':'不要求满破'}`,...(candidates.length>1?{candidateIndex,candidateCount:candidates.length}:{})};
    });
   });
   return {...person(member,slot,catalog),className:servant?.className??(member?'职介未指定':''),rarity:servant?.rarity==null?'':servant.rarity===0?'☆ · 0星':'★'.repeat(Math.max(0,Math.min(5,servant.rarity))),details:member?`Lv.${level(member.lv)} · 宝具 ${level(member.tdLv)}`:'',skills:hasServantIdentity(member)?[0,1,2].map(i=>({name:skillAt(member,servant,i)?.name??`技能 ${i+1}`,icon:skillAt(member,servant,i)?.icon,level:level(member.skillLvs?.[i])})):[],append:Array.isArray(member?.appendLvs)&&member.appendLvs.length?`追加 ${member.appendLvs.map(level).join('/')}`:'',grand,equips};
  }),
  mystic:{name:mystic?.name??(data.team?.mysticCode?.mysticCodeId?`御主礼装 #${data.team.mysticCode.mysticCodeId}`:'未指定御主礼装'),image:mystic?.image,level:`Lv.${level(data.team?.mysticCode?.level)}`,skills:(mystic?.skills??[]).map((skill,index)=>({name:isSwap(data,{type:'skill',svt:null,skill:(skill.num??index+1)-1})?'换人':skill.name??`技能 ${index+1}`,icon:skill.icon,number:skill.num??index+1}))},
  turns,actions:Array.isArray(data.actions)?data.actions.length:0,cost:`${cost.complete?'':'≥'}${cost.knownTotal} / ${limit}${cost.complete?'':'（部分未知）'}`,
  state:options.validation?.valid===true&&blocking===0?'通过结构检查':blocking?`草稿 · ${blocking} 项阻断问题`:'草稿 · 尚未确认执行检查',blocking,warnings,notices:[...notices],
 };
 assertTextBudget(model);
 return model;
}
