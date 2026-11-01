import {useEffect,useState} from 'react';
import {Plus,X} from 'lucide-react';
import type {Catalog,Json} from './model';
import {equipCandidatesForDisplay,hasStoredEquipRequirement,supportsEquipCandidates} from './equipRequirements';
import {GrandBondEffectControl} from './GrandBondEffectControl';
import type {GrandBondEffect} from './grandBondEffect';
import './equipCandidateEditor.css';

function CandidateAvatar({src,name}:{src?:string|null;name:string}) {
 const [failed,setFailed]=useState(false);
 useEffect(()=>setFailed(false),[src]);
 return <div className="equip-candidate-avatar">{src&&!failed?<img src={src} alt={name} onError={()=>setFailed(true)}/>:<Plus size={24}/>}</div>;
}

export function EquipCandidateEditor({member,catalog,onChoose,onRemove,onClear,onUpdate,onBondEffectChange,onError}:{
 member:Json;catalog:Catalog;onChoose:(key:string,index:number)=>void;onRemove:(key:string,index:number)=>void;onClear:(key:string)=>void;
 onUpdate:(key:string,index:number,changes:Json)=>void;onError:(message:string)=>void;
 onBondEffectChange:(value:GrandBondEffect)=>void;
}) {
 const keys=member.grandSvt||member.equip2||member.equip3?['equip1','equip2','equip3']:['equip1'];
 const safely=(fn:()=>void)=>{try{fn();}catch(error){onError((error as Error).message);}};
 return <section className="equip-candidates-editor" aria-label="概念礼装候选设置">
  <div className="subsection-title">概念礼装 <small>{member.grandSvt?'冠位三礼装':'普通礼装'}</small></div>
  {keys.map((key,slotIndex)=>{
   if(key==='equip2'&&member.grandSvt===true)return <div className="equip-candidate-slot" key={key}><div className="equip-candidate-slot-heading"><strong>礼装 2 · 羁绊礼装</strong></div><GrandBondEffectControl member={member} onChange={value=>safely(()=>onBondEffectChange(value))}/></div>;
   const choices=equipCandidatesForDisplay(member,key),allowed=supportsEquipCandidates(member,key);
   const rawCount=Array.isArray(member[key]?.candidates)?member[key].candidates.length:0;
   return <div className="equip-candidate-slot" key={key}>
    <div className="equip-candidate-slot-heading"><strong>礼装 {slotIndex+1}{member.grandSvt?` · ${['普通礼装','羁绊礼装','追加礼装'][slotIndex]}`:''}</strong>{hasStoredEquipRequirement(member,key)&&<button className="text-button" aria-label={`清除礼装 ${slotIndex+1} 全部候选`} onClick={()=>onClear(key)}>清除全部</button>}</div>
    <div className="equip-candidate-grid" role="group" aria-label={`礼装 ${slotIndex+1} 候选`}>
     {[0,1,2].map(index=>{
      const ce=choices[index],item=catalog.equips.find(e=>e.id===ce?.id),name=item?.name??(ce?.id?`礼装 #${ce.id}`:'未添加礼装');
      const addable=index===0||allowed&&index<=choices.length;
      return <article className={`equip-candidate-editor ${ce?'configured':'empty'}`} key={index}>
       <header><strong>候选 {index+1}</strong><small>{index===0?'首选':'或'}</small>{ce&&<button className="icon" aria-label={`移除礼装 ${slotIndex+1} 候选 ${index+1}`} title="移除此候选" onClick={()=>safely(()=>onRemove(key,index))}><X size={16}/></button>}</header>
       <button className="equip-candidate-choose" disabled={!addable} aria-label={`${ce?'更换':'添加'}礼装 ${slotIndex+1} 候选 ${index+1}`} onClick={()=>onChoose(key,index)}><CandidateAvatar src={item?.image} name={name}/><strong title={name}>{name}</strong><span>{ce?'点击更换':addable?'添加礼装':!allowed?'此槽仅支持首选':'先添加前一个候选'}</span></button>
       {ce&&<div className="equip-candidate-fields"><label className="field"><span>等级</span><input type="number" aria-label={`礼装 ${slotIndex+1} 候选 ${index+1} 等级`} min={0} max={100} placeholder="未指定" value={ce.lv??''} onChange={e=>safely(()=>onUpdate(key,index,{lv:e.target.value===''?undefined:Number(e.target.value)}))}/></label><label className="field"><span>满破要求</span><select aria-label={`礼装 ${slotIndex+1} 候选 ${index+1} 满破要求`} value={ce.limitBreak===true?'yes':ce.limitBreak===false?'no':'any'} onChange={e=>safely(()=>onUpdate(key,index,{limitBreak:e.target.value==='any'?undefined:e.target.value==='yes'}))}><option value="any">不限制</option><option value="yes">满破</option><option value="no">非满破</option></select></label></div>}
      </article>;
     })}
    </div>
    <p className="equip-candidate-help">{allowed?'同一礼装槽命中任一候选即可，每个候选独立设置要求。':'此槽使用首选礼装；额外候选用于普通助战礼装和冠位助战的普通、追加礼装槽。'}等级用于记录与方案检查。</p>
    {rawCount>2&&<p className="import-error">原方案此槽有 {rawCount+1} 个候选，超过当前3项上限；数据已保留，请在原始 JSON 中调整。</p>}
   </div>;
  })}
 </section>;
}
