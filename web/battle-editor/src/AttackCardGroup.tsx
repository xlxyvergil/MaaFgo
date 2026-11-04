import {ArrowLeft,ArrowRight,GripVertical,X} from 'lucide-react';
import {colorName,actionMemberAt,servantFor,hasServantIdentity,memberLabel} from './model';
import type {Catalog,Entry,Json} from './model';
import {npAt} from './battlePreview';
import {CardArtwork} from './NPCardPreview';
import type {CardPreviewConfig} from './npCardRequest';
import {cardPreviewRegion} from './npCardRequest';
import {useAttackCardDrag} from './useAttackCardDrag';
import {isAutoAttackCard} from './attackCards';

export function AttackCardGroup({data,catalog,entry,entryIndex,region,config,onSelect,onSwapCards,onRemoveCard}:{data:Json;catalog:Catalog;entry:Entry;entryIndex:number;region?:string;config?:CardPreviewConfig;onSelect:(index:number)=>void;onSwapCards?:(entryIndex:number,from:number,to:number)=>void;onRemoveCard?:(entryIndex:number,cardIndex:number)=>void}) {
 const cards=Array.isArray(entry.raw?.attacks)?entry.raw.attacks:[],occupied=[0,1,2].map(index=>!!cards[index]&&typeof cards[index]==='object'&&!Array.isArray(cards[index])&&!isAutoAttackCard(cards[index]));
 const drag=useAttackCardDrag({occupied,onSwap:(from,to)=>onSwapCards?.(entryIndex,from,to),resetKey:data,enabled:!!onSwapCards});
 return <div className="attack-card-group" data-attack-group="" aria-label="本回合出卡顺序">{[0,1,2].map(index=>{
  const card=occupied[index]?cards[index]:undefined,validSlot=Number.isInteger(card?.svt)&&card.svt>=0&&card.svt<3,member=validSlot?actionMemberAt(data,entry.index,card):undefined,actorPresent=hasServantIdentity(member),svt=actorPresent?servantFor(catalog,member?.svtId):undefined,np=card?.isTD?npAt(member,svt):undefined,label=card?(actorPresent?(card.isTD?'宝具 NP':colorName(card.cardType)):`未指定从者，无法安排${card.isTD?'宝具':'指定普通卡'}`):'默认选卡补齐';
  return <article key={index} className={`attack-card-slot ${occupied[index]?'filled':'empty'} ${drag.dragged===index?'attack-drag-placeholder':''} ${drag.target===index&&drag.dragged!==index?'attack-drop-target':''}`} data-attack-card={index} draggable={occupied[index]&&!!onSwapCards} {...drag.bindCard(index)}>
   <div className="attack-card-topline"><span className="attack-card-order">{index+1}</span>{occupied[index]&&onSwapCards&&<span className="attack-card-grip" onPointerDown={event=>drag.start(index,event)} title="拖动调整出卡位置，可放入默认补齐位置"><GripVertical size={15}/></span>}{occupied[index]&&onRemoveCard&&<button type="button" className="attack-card-remove" data-no-drag="" draggable={false} aria-label={`移除第 ${index+1} 张${card.isTD?'宝具':'色卡'}`} title={`移除第 ${index+1} 张出卡；后续卡片前移`} onPointerDown={event=>event.stopPropagation()} onDragStart={event=>{event.preventDefault();event.stopPropagation();}} onClick={event=>{event.stopPropagation();drag.cancel();onRemoveCard(entryIndex,index);}}><X size={18} strokeWidth={2.5} aria-hidden="true"/></button>}</div>
   <button className="attack-card-select" aria-label={`编辑第 ${index+1} 张卡：${label}`} onPointerDown={event=>{if(occupied[index])drag.start(index,event);}} onClick={()=>onSelect(entryIndex)}>
    {card?<>{actorPresent?<CardArtwork member={member} np={np} color={card.cardType} kind={card.isTD?'np':'face'} region={cardPreviewRegion(region,config)} config={config}/>:<span className="card-artwork fallback"><span className="card-artwork-message"><strong>未指定从者</strong><small>请先在编队页指定从者</small></span></span>}<strong>{label}</strong><small>{validSlot?`前排 ${card.svt+1}`:'无效前排'} · {memberLabel(member,catalog)}</small></>:<span className="attack-card-empty">默认选卡补齐</span>}
   </button>
   {occupied[index]&&onSwapCards&&<div className="attack-card-swap-tools"><button className="icon" aria-label={`将第 ${index+1} 张卡与左侧交换`} disabled={index===0} onClick={()=>onSwapCards(entryIndex,index,index-1)}><ArrowLeft size={16}/></button><button className="icon" aria-label={`将第 ${index+1} 张卡与右侧交换`} disabled={index===2} onClick={()=>onSwapCards(entryIndex,index,index+1)}><ArrowRight size={16}/></button></div>}
  </article>;
 })}</div>;
}
