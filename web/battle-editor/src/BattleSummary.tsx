import {useEffect,useState} from 'react';
import {ArrowLeftRight,ArrowRight,FileQuestion,Info,Shield,Swords,Target,UserRoundMinus} from 'lucide-react';
import {actionTitle,colorName,isSwap,rosterAt,servantFor,skillAt,turnsOf,hasServantIdentity,memberLabel,isRetire,retirementInfoAt,actionMemberAt} from './model';
import type {Catalog,Entry,Json} from './model';
import {isAutoAttackCard} from './attackCards';
import {npAt} from './battlePreview';
import {SkillIcon} from './PartyCard';
import {CardArtwork} from './NPCardPreview';
import {cardPreviewRegion,artworkColor} from './npCardRequest';
import type {CardPreviewConfig} from './npCardRequest';
import './battleSummary.css';
import {specialSkillChoiceLabel} from './specialSkillChoices';
import './specialSkillChoices.css';

function SummaryPortrait({src,label}:{src?:string|null;label:string}) {
 const [failed,setFailed]=useState(false);
 useEffect(()=>setFailed(false),[src]);
 return <span className="summary-portrait" aria-hidden="true">
  {src&&!failed?<img src={src} alt="" loading="lazy" draggable={false} onError={()=>setFailed(true)}/>:<span>{label.slice(0,1)||'?'}</span>}
 </span>;
}

const validFront=(slot:unknown):slot is number=>Number.isInteger(slot)&&Number(slot)>=0&&Number(slot)<3;
function SummaryMember({roster,slot,catalog,backup=false,memberOverride}:{roster:any[];slot:unknown;catalog:Catalog;backup?:boolean;memberOverride?:Json|null}) {
 const valid=backup?Number.isInteger(slot)&&Number(slot)>=3&&Number(slot)<6:validFront(slot);
 const member=memberOverride===undefined?(valid?roster[Number(slot)]:undefined):memberOverride,servant=hasServantIdentity(member)?servantFor(catalog,member.svtId):undefined;
 const position=valid?`${backup?'候补':'前排'} ${Number(slot)-(backup?3:0)+1}`:`无效${backup?'候补':'前排'} ${String(slot??'未指定')}`;
 const name=memberLabel(member,catalog);
 return <span className={`summary-person ${!hasServantIdentity(member)?'summary-unresolved':''}`} title={`${position} · ${name}`}>
  <SummaryPortrait src={servant?.image} label={hasServantIdentity(member)?name:'?'}/>
  <span className="summary-person-name"><small>{position}</small><span>{name}</span></span>
 </span>;
}

function SummaryEnemy({value}:{value:unknown}) {
 const label=value===2?'敌方 1 · 左侧':value===1?'敌方 2 · 中间':value===0?'敌方 3 · 右侧':`未支持敌方目标 ${String(value)}`;
 return <span className="summary-enemy"><Target size={14} aria-hidden="true"/>{label}</span>;
}

function SummaryEntry({entry,data,catalog,region,config}:{entry:Entry;data:Json;catalog:Catalog;region:string;config?:CardPreviewConfig}) {
 const raw=entry.raw,roster=rosterAt(data,entry.index);
 if(raw?.type==='attack') {
  const cards=Array.isArray(raw.attacks)?raw.attacks:[];
  return <div className="summary-action summary-attack">
   <span className="summary-action-label"><Swords size={16} aria-hidden="true"/>攻击 · 回合结束</span>
   {raw.options?.enemyTarget!=null&&<SummaryEnemy value={raw.options.enemyTarget}/>}
   {cards.length?<div className="summary-attack-cards" aria-label="摘要出卡顺序">{cards.map((card:Json,i:number)=>{
    if(isAutoAttackCard(card))return <span className="summary-card-step" key={i}>
     {i>0&&<ArrowRight size={14} className="summary-arrow" aria-label="然后"/>}
     <span className="summary-card-item summary-muted"><Swords size={18} aria-hidden="true"/><span className="summary-card-label">默认普通卡</span></span>
    </span>;
    const member=validFront(card?.svt)?actionMemberAt(data,entry.index,card):undefined,actorPresent=hasServantIdentity(member),servant=actorPresent?servantFor(catalog,member.svtId):undefined;
    const np=card?.isTD?npAt(member,servant):undefined,color=card?.isTD?np?.cardType:card?.cardType;
    const npColor=artworkColor(color),label=!actorPresent?`未指定从者，无法安排${card?.isTD?'宝具':'指定普通卡'}`:card?.isTD?(npColor?`宝具 · ${npColor[0].toUpperCase()+npColor.slice(1)}`:np?.name??'宝具资料未知'):colorName(color);
    return <span className="summary-card-step" key={i}>
     {i>0&&<ArrowRight size={14} className="summary-arrow" aria-label="然后"/>}
     <span className="summary-card-item" title={card?.isTD?np?.name:label}>
      <SummaryMember roster={roster} slot={card?.svt} catalog={catalog} memberOverride={member}/>
      {actorPresent?<CardArtwork member={member} np={np} color={color} kind={card?.isTD?'np':'face'} region={region} config={config} className="summary-card-artwork"/>:<FileQuestion size={20} className="summary-unresolved" aria-hidden="true"/>}
      <span className="summary-card-label">{label}</span>
     </span>
    </span>;
   })}</div>:<span className="summary-muted">按通用规则选普通卡，不使用宝具</span>}
  </div>;
 }
 if(isRetire(raw)){
  const retirement=retirementInfoAt(data,entry.index,catalog);
  return <div className="summary-action summary-retirement"><SummaryMember roster={roster} slot={raw.svt} catalog={catalog} memberOverride={retirement.member}/><span className="summary-action-label"><UserRoundMinus size={17}/>退场 · 移至队伍末尾</span><ArrowRight size={16} className="summary-arrow" aria-label="递补"/><SummaryMember roster={roster} slot={raw.svt} catalog={catalog} memberOverride={retirement.replacement}/><span className="summary-muted">递补进场</span>{!retirement.valid&&<span className="summary-retirement-error">{retirement.error}</span>}</div>;
 }
 if(raw?.type!=='skill')return <div className="summary-action summary-unresolved"><FileQuestion size={16} aria-hidden="true"/>{actionTitle(entry,data,catalog)}</div>;
 const mystic=catalog.mysticCodes.find(m=>m.id===data.team?.mysticCode?.mysticCodeId),master=raw.svt==null;
 const member=validFront(raw.svt)?actionMemberAt(data,entry.index,raw):undefined,actorPresent=master||hasServantIdentity(member),servant=hasServantIdentity(member)?servantFor(catalog,member.svtId):undefined;
 const validSkill=Number.isInteger(raw.skill)&&raw.skill>=0&&raw.skill<3;
 const meta=validSkill&&actorPresent?(master?mystic?.skills?.find(skill=>skill.num===raw.skill+1)??mystic?.skills?.find((skill,i)=>skill.num==null&&i===raw.skill):skillAt(member??undefined,servant,raw.skill)):undefined;
 const swap=isSwap(data,raw),name=swap?'换人':!actorPresent?'未指定从者，无法安排技能':meta?.name??(validSkill?`技能 ${raw.skill+1}`:`未支持技能 ${String(raw.skill)}`);
 const pair=entry.replacement,front=Array.isArray(pair)?pair[0]:undefined,back=Array.isArray(pair)?pair[1]:undefined;
 const backup=Number.isInteger(back)?back<3?back+3:back:undefined;
 return <div className={`summary-action ${swap?'summary-swap':''}`}>
  {master?<span className="summary-person" title={mystic?.name??'御主礼装未指定'}><SummaryPortrait src={mystic?.image} label="御主"/><span>御主</span></span>:<SummaryMember roster={roster} slot={raw.svt} catalog={catalog} memberOverride={member}/>}
  <span className="summary-muted">{swap?'使用':'使用技能'}</span>
  <span className="summary-skill" title={name}>{validSkill&&actorPresent?<SkillIcon skill={meta?{...meta,name}:undefined} index={raw.skill}/>:<Shield size={24} aria-hidden="true"/>}<span>{name}</span></span>
  {specialSkillChoiceLabel(entry,catalog)&&<span className="special-choice-badge">{specialSkillChoiceLabel(entry,catalog)}</span>}
  {swap?<span className="summary-swap-pair">
   {validFront(front)&&backup!=null&&backup>=3&&backup<6?<><SummaryMember roster={roster} slot={front} catalog={catalog}/><ArrowLeftRight size={16} className="summary-arrow" aria-label="交换"/><SummaryMember roster={roster} slot={backup} backup catalog={catalog}/></>:<span className="summary-unresolved">换人位置未配对或无效</span>}
  </span>:<>
   {raw.options?.playerTarget!=null&&<span className="summary-recipient"><ArrowRight size={16} className="summary-arrow" aria-label="目标"/><SummaryMember roster={roster} slot={raw.options.playerTarget} catalog={catalog}/></span>}
   {raw.options?.enemyTarget!=null&&<SummaryEnemy value={raw.options.enemyTarget}/>}
  </>}
 </div>;
}

export function BattleSummary({data,catalog,cardPreviewRegion:preference,npCardPreview}:{data:Json;catalog:Catalog;cardPreviewRegion?:string;npCardPreview?:CardPreviewConfig}) {
 const turns=turnsOf(data,catalog),region=cardPreviewRegion(preference,npCardPreview);
 return <div className="summary-panel visual-battle-summary">
  <div className="subsection-title">打法摘要 <small>共 {turns.length} 回合</small></div>
  {turns.length?turns.map((turn,i)=><div className="summary-turn" key={i}><span>{String(i+1).padStart(2,'0')}</span><div className="summary-turn-content"><b>第 {i+1} 回合</b><div className="summary-actions">{turn.entries.map(entry=><SummaryEntry entry={entry} data={data} catalog={catalog} region={region} config={npCardPreview} key={entry.index}/>)}</div></div></div>):<p className="muted">未设置回合逻辑，全程使用通用自动战斗。</p>}
  <div className="hint"><Info size={15}/><span>未指定的面卡和不可用宝具由当前默认策略补齐。计划结束后恢复原生默认策略。</span></div>
 </div>;
}
