import {useId} from 'react';
import {Heart,Zap} from 'lucide-react';
import type {Json} from './model';
import {grandBondEffect,grandBondEffectLabel} from './grandBondEffect';
import type {GrandBondEffect} from './grandBondEffect';
import './grandBondEffect.css';

export function GrandBondEffectControl({member,onChange,compact=false,label='礼装 2 羁绊礼装效果'}:{
 member:Json;onChange:(value:GrandBondEffect)=>void;compact?:boolean;label?:string;
}) {
 const id=useId(),value=grandBondEffect(member);
 return <div className={`grand-bond-effect ${compact?'compact':''} effect-${value}`} data-no-drag="" draggable={false} onPointerDown={event=>event.stopPropagation()} onClick={event=>event.stopPropagation()} onDragStart={event=>{event.preventDefault();event.stopPropagation();}}>
  {compact&&<span className="grand-bond-symbol" aria-hidden="true">{value==='np50'?<Zap size={25}/>:<Heart size={25}/>}<b>{value==='np50'?'NP 50%':value==='original'?'原始效果':'不限制'}</b></span>}
  <label htmlFor={id}>{compact?'羁绊礼装':'替换效果'}</label>
  <select id={id} aria-label={label} title={grandBondEffectLabel(member)} value={value} onChange={event=>onChange(event.target.value as GrandBondEffect)}>
   <option value="any">{compact?'不限':'不限制'}</option><option value="np50">{compact?'NP50%':'使用 NP 50% 替换效果'}</option><option value="original">{compact?'原始':'保留原始效果'}</option>
  </select>
  <small>{compact?'礼装 2 · 无需选卡':'每位从者只有一张羁绊礼装，无需选择具体礼装。'}</small>
 </div>;
}
