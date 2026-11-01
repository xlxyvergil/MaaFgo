import {useEffect,useState} from 'react';
import type {PointerEvent as ReactPointerEvent} from 'react';
import {GripVertical,Plus,UserRound,ShieldCheck,X} from 'lucide-react';
import {skillAt,hasServantIdentity,isUnspecifiedSupport,memberLabel} from './model';
import type {Catalog,Json,Servant} from './model';
import {equipCandidatesForDisplay,equipForDisplay,hasStoredEquipRequirement,supportsEquipCandidates} from './equipRequirements';
import type {EquipDragPosition} from './equipDrag';
import {GrandBondEffectControl} from './GrandBondEffectControl';
import type {GrandBondEffect} from './grandBondEffect';
import './candidateCarousel.css';

export function SkillIcon({skill,index,append=false}:{skill?:Json;index:number;append?:boolean}) {
 const [failed,setFailed]=useState(false);useEffect(()=>setFailed(false),[skill?.icon]);
 return <span className={`skill-icon ${append?'append':''}`} title={skill?.name??`${append?'追加':'主动'}技能 ${index+1} · 图标未知`}>
  {skill?.icon&&!failed?<img src={skill.icon} alt={skill.name??`技能 ${index+1}`} loading="lazy" draggable={false} onError={()=>setFailed(true)}/>:<span aria-label="图标未知">{index+1}</span>}
 </span>;
}

function SafeImage({src,alt,className}:{src?:string|null;alt:string;className?:string}) {
 const [failed,setFailed]=useState(false);useEffect(()=>setFailed(false),[src]);
 return src&&!failed?<img className={className} src={src} alt={alt} loading="lazy" draggable={false} onError={()=>setFailed(true)}/>:<span className="image-fallback" aria-label={`${alt} · 暂无图片`}>{alt.slice(0,1)||'◇'}</span>;
}

function CandidateArtwork({member,equipKey,catalog,paused=false}:{member:Json|null;equipKey:string;catalog:Catalog;paused?:boolean}) {
 const candidates=equipCandidatesForDisplay(member,equipKey).filter(candidate=>Number.isSafeInteger(candidate.id)&&candidate.id>0).slice(0,supportsEquipCandidates(member,equipKey)?3:1),count=candidates.length;
 const signature=candidates.map(candidate=>`${candidate.id}:${candidate.lv??''}:${candidate.limitBreak??''}`).join('|');
 const [frame,setFrame]=useState(0),[animated,setAnimated]=useState(false);
 useEffect(()=>{
  setFrame(0);
  if(count<2){setAnimated(false);return;}
  const preference=window.matchMedia('(prefers-reduced-motion: reduce)');
  let timer:ReturnType<typeof setInterval>|undefined;
  const synchronize=()=>{
   if(timer!==undefined){clearInterval(timer);timer=undefined;}
   const active=!document.hidden&&!preference.matches&&!paused;
   setAnimated(active);if(preference.matches)setFrame(0);
   if(active)timer=setInterval(()=>setFrame(value=>(value+1)%count),3600);
  };
  preference.addEventListener('change',synchronize);document.addEventListener('visibilitychange',synchronize);synchronize();
  return()=>{if(timer!==undefined)clearInterval(timer);preference.removeEventListener('change',synchronize);document.removeEventListener('visibilitychange',synchronize);};
 },[signature,count,paused]);
 const visible=count?frame%count:0,ce=candidates[visible],item=catalog.equips.find(equip=>equip.id===ce?.id),pulse=animated?' fgo-candidate-pulse':'';
 return <>
  <span key={`image-${visible}`} className={`fgo-equip-preview${pulse}`} style={{aspectRatio:item?.imageWidth&&item.imageHeight?`${item.imageWidth}/${item.imageHeight}`:'150/68'}}>{ce?<SafeImage src={item?.image} alt={item?.name??`礼装 #${ce.id}`}/>:<Plus size={20}/>} {count>1&&<span className="fgo-candidate-count" aria-hidden="true">{visible+1}/{count}</span>}</span>
  <strong key={`name-${visible}`} className={pulse||undefined} title={item?.name}>{item?.name??(ce?`礼装 #${ce.id}`:'不要求礼装')}</strong>
  <small key={`details-${visible}`} className={pulse||undefined}>{member?.grandSvt?`礼装 ${equipKey.slice(-1)} · `:''}{ce?.lv!=null?`Lv.${ce.lv}`:ce?'未指定等级':'点击选择'}{ce?.limitBreak?' ◆':''}</small>
  {ce&&item?.recognizable===false&&<small>缺少工程识别模板</small>}
 </>;
}

export interface PartyCardDragBindings {
 onDragStart:(event:React.DragEvent<HTMLElement>)=>void;onDragEnd:()=>void;onDragOver:(event:React.DragEvent<HTMLElement>)=>void;onDrop:(event:React.DragEvent<HTMLElement>)=>void;
 onPointerStart:(event:ReactPointerEvent<HTMLElement>)=>void;onPointerMove:(event:ReactPointerEvent<HTMLElement>)=>void;onPointerEnd:(event:ReactPointerEvent<HTMLElement>)=>void;onPointerCancel:(event:ReactPointerEvent<HTMLElement>)=>void;onClickCapture:(event:React.MouseEvent<HTMLElement>)=>void;
 onPointerDownCapture?:(event:ReactPointerEvent<HTMLElement>)=>void;onLostPointerCapture?:(event:ReactPointerEvent<HTMLElement>)=>void;
}

export function PartyCard({member,servant,catalog,index,defaultSupport=false,selected,dragging,dropTarget,onSelect,onChoose,onEquip,onClearEquip,onClearMember,onBondEffectChange,equipDrag,onDragStart,onDragEnd,onDragOver,onDrop,onPointerStart,onPointerMove,onPointerEnd,onPointerCancel,onClickCapture,onPointerDownCapture,onLostPointerCapture}:{
 member:Json|null;servant?:Servant;catalog:Catalog;index:number;defaultSupport?:boolean;selected:boolean;dragging:boolean;dropTarget:boolean;
 onSelect:()=>void;onChoose:()=>void;onEquip:(key:string)=>void;onClearEquip:(key:string)=>void;onClearMember:()=>void;
 onBondEffectChange:(value:GrandBondEffect)=>void;
 equipDrag:{dragged:EquipDragPosition|null;target:EquipDragPosition|null;bindEquip:(position:EquipDragPosition)=>PartyCardDragBindings};
}&PartyCardDragBindings) {
 const support=member?.supportType&&member.supportType!=='none',identified=hasServantIdentity(member),unspecified=isUnspecifiedSupport(member);
 const ceKeys:EquipDragPosition['key'][]=member?.grandSvt?['equip1','equip2','equip3']:['equip1'];
 return <article className={`fgo-member-card ${selected?'selected':''} ${!member?'empty':''} ${dragging?'dragging party-drag-placeholder':''} ${dropTarget?'drop-target':''}`} draggable data-slot={index} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragOver={onDragOver} onDrop={onDrop} onPointerDownCapture={onPointerDownCapture} onPointerMove={onPointerMove} onPointerUp={onPointerEnd} onPointerCancel={onPointerCancel} onLostPointerCapture={onLostPointerCapture} onClickCapture={onClickCapture} aria-label={`编队槽位 ${index+1}`}>
  <div className="fgo-card-order"><button className="slot-select" onPointerDown={onPointerStart} onClick={onSelect} aria-label={`编辑槽位 ${index+1} 属性`}><span>{['1st','2nd','3rd','4th','5th','6th'][index]}</span><small>{index<3?'前排':'候补'}</small></button><span className="member-drag-handle" onPointerDown={onPointerStart} title="拖动交换整位从者与礼装"><GripVertical size={15}/></span>{member&&<button type="button" className="fgo-member-clear" data-no-drag="" draggable={false} aria-label={`清除槽位 ${index+1} 从者要求`} title="清除此位置的从者与礼装要求（不要求此位置）" onPointerDown={event=>event.stopPropagation()} onDragStart={event=>{event.preventDefault();event.stopPropagation();}} onClick={event=>{event.stopPropagation();onClearMember();}}><X size={14} aria-hidden="true"/></button>}</div>
  <button className={`fgo-portrait ${servant?.portrait?'full-portrait':'face-portrait'}`} onPointerDown={onPointerStart} onClick={onChoose} aria-label={`${identified?'更换':'选择'}槽位 ${index+1} 从者${unspecified?'（仅要求礼装）':!member&&defaultSupport?'（默认好友助战）':''}`}>
   {hasServantIdentity(member)?<><SafeImage src={servant?.portrait||servant?.image} alt={servant?.name??`从者 #${member.svtId}`}/><span className="portrait-shade"/><span className="fgo-class-badge">{servant?.classIcon||catalog.uiAssets?.classes?.[servant?.classKey??'']?<img src={servant?.classIcon||catalog.uiAssets?.classes?.[servant?.classKey??'']} alt={servant?.className??'职阶'} draggable={false}/>:<ShieldCheck size={25}/>}<small>{servant?.className??'从者'}</small></span>{support&&<span className="fgo-support-mark" title={member.supportType==='npc'?'NPC 助战':'好友助战'}>{catalog.uiAssets?.support?<img src={catalog.uiAssets.support} alt="助战" draggable={false}/>:<UserRound size={17}/>}<b>{member.supportType==='npc'?'NPC':'助战'}</b></span>}{member.grandSvt&&<span className="fgo-grand-mark">GRAND</span>}</>:<><span className="fgo-empty-servant"><Plus size={28}/><strong>{unspecified?'不要求从者':defaultSupport?'选择助战从者':'选择从者'}</strong><small>{unspecified?'仅按礼装筛选':'SERVANT'}</small></span>{(support||defaultSupport)&&<span className="fgo-support-mark" title={unspecified?'好友助战：不要求从者，仅按礼装筛选':'新队伍选入后默认为好友助战'}>{catalog.uiAssets?.support?<img src={catalog.uiAssets.support} alt="助战" draggable={false}/>:<UserRound size={17}/>}<b>{unspecified?'助战':'默认助战'}</b></span>}</>}
  </button>
  <button className="fgo-member-info" onPointerDown={onPointerStart} onClick={member||defaultSupport?onSelect:onChoose} aria-label={`编辑槽位 ${index+1} ${servant?.name??'从者'} 信息`}>
   <span className="fgo-rarity" aria-label={`${servant?.rarity??0} 星`}>{identified?('★'.repeat(servant?.rarity??0)||'☆'):'—'}</span>
   <strong className="fgo-servant-name">{member?memberLabel(member,catalog):'不要求此位置'}</strong>
   {identified?<><span className="fgo-member-stats"><span>Lv.<b>{member?.lv??'—'}</b></span><span>宝具 <b>{member?.tdLv??'—'}</b></span></span>
   <span className="fgo-card-skills">{[0,1,2].map(i=><span key={i}><SkillIcon skill={skillAt(member??undefined,servant,i)} index={i}/><b>{member?.skillLvs?.[i]??'—'}</b></span>)}</span></>:unspecified?<span className="fgo-member-stats">只按礼装要求选择助战</span>:null}
  </button>
  <div className={`fgo-card-equips ${member?.grandSvt?'grand-equips':''}`} aria-label={`槽位 ${index+1} 概念礼装`}>
   {ceKeys.map((key,i)=>{if(key==='equip2'&&member?.grandSvt===true)return <div className="fgo-equip-slot" key={key}><GrandBondEffectControl compact member={member} onChange={onBondEffectChange} label={`槽位 ${index+1} 礼装 2 羁绊礼装效果`}/></div>;const ce=equipForDisplay(member,key),item=catalog.equips.find(e=>e.id===ce?.id),stored=hasStoredEquipRequirement(member,key),drag=equipDrag.bindEquip({slot:index,key}),isSource=equipDrag.dragged?.slot===index&&equipDrag.dragged.key===key,isTarget=equipDrag.target?.slot===index&&equipDrag.target.key===key&&!isSource;return <div className={`fgo-equip-slot ${stored?'has-clear':''} ${isSource?'equip-drag-placeholder':''} ${isTarget?'equip-drop-target':''}`} key={key} data-equip-key={key} onPointerDownCapture={drag.onPointerDownCapture} onPointerMove={drag.onPointerMove} onPointerUp={drag.onPointerEnd} onPointerCancel={drag.onPointerCancel} onLostPointerCapture={drag.onLostPointerCapture} onClickCapture={drag.onClickCapture} onDragStart={drag.onDragStart} onDragEnd={drag.onDragEnd} onDragOver={drag.onDragOver} onDrop={drag.onDrop}><button className={`fgo-equip-button ${!ce?'empty':''}`} disabled={!member&&!defaultSupport} draggable={!!ce} onPointerDown={drag.onPointerStart} onClick={()=>onEquip(key)} title={ce?'拖动调整整组候选；点击更换首选，其余候选在详细设置中选择':undefined} aria-label={`选择槽位 ${index+1} 礼装 ${i+1}${item?`：${item.name}`:''}`}>
    <CandidateArtwork member={member} equipKey={key} catalog={catalog} paused={isSource||dragging}/>
   </button>{stored&&<button type="button" className="fgo-equip-clear" data-no-drag="" draggable={false} aria-label={`清除槽位 ${index+1} 礼装 ${i+1} 要求`} title={`清除礼装 ${i+1} 要求（不要求礼装）`} onPointerDown={event=>event.stopPropagation()} onDragStart={event=>{event.preventDefault();event.stopPropagation();}} onClick={event=>{event.stopPropagation();onClearEquip(key);}}><X size={14} aria-hidden="true"/></button>}</div>;})}
  </div>
 </article>;
}
