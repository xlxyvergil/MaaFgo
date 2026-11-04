import {useState} from 'react';
import {ChevronDown,ChevronUp,Info,SlidersHorizontal,X} from 'lucide-react';
import type {Catalog} from './model';
import {equipAdvancedFilterCount,newEquipAdvancedFilters} from './equipFilters';
import type {EquipAdvancedFilters} from './equipFilters';
import './equipFilters.css';

function RangeField({label,min,max,limit,onChange}:{label:string;min?:number;max?:number;limit:number;onChange:(min:number|undefined,max:number|undefined)=>void}) {
 const number=(value:string)=>value===''?undefined:Number(value);
 return <div className="field equip-range-field"><span>{label}</span><div>
  <input type="number" min={0} max={limit} aria-label={`${label}下限`} placeholder="不限" value={min??''} onChange={event=>onChange(number(event.target.value),max)}/>
  <span>至</span><input type="number" min={0} max={limit} aria-label={`${label}上限`} placeholder="不限" value={max??''} onChange={event=>onChange(min,number(event.target.value))}/>
 </div></div>;
}

export function AdvancedEquipFilters({value,onChange,catalog,kind,onKindChange}:{value:EquipAdvancedFilters;onChange:(value:EquipAdvancedFilters)=>void;catalog:Catalog;kind:string;onKindChange:(value:string)=>void}) {
 const [open,setOpen]=useState(false),count=equipAdvancedFilterCount(value)+(kind?1:0);
 const metadata=catalog.equipFilters;
 const groups=[...new Set((metadata?.effects??[]).map(effect=>effect.group||'其他效果'))];
 const toggle=(id:string)=>onChange({...value,effects:value.effects.includes(id)?value.effects.filter(effect=>effect!==id):[...value.effects,id]});
 return <section className={`servant-advanced-filters equip-advanced-filters ${open?'open':''}`} aria-label="礼装高级筛选">
  <div className="advanced-filter-heading"><button type="button" className="advanced-filter-toggle" aria-expanded={open} aria-controls="advanced-equip-options" onClick={()=>setOpen(!open)}><SlidersHorizontal size={15}/><strong>高级筛选</strong><span>[{open?'收起':'展开'}]</span>{count>0&&<b>已选 {count} 项</b>}<small>{value.basis==='mlb'?'按满破效果':'按未满破效果'}{value.includeConditional?' · 含条件效果':''}</small>{open?<ChevronUp size={15}/>:<ChevronDown size={15}/>}</button><button type="button" className="advanced-filter-clear" disabled={!count&&value.basis==='base'&&!value.includeConditional&&value.effectMode==='all'} onClick={()=>{onChange(newEquipAdvancedFilters());onKindChange('');}}><X size={13}/>清除高级</button></div>
  {open&&<div className="advanced-filter-options" id="advanced-equip-options">
   <div className="equip-facet-row equip-advanced-kind" role="group" aria-label="礼装类型筛选"><span>类型</span><div><button type="button" className={!kind?'selected':''} aria-pressed={!kind} onClick={()=>onKindChange('')}>全部</button>{(metadata?.kinds??[]).map(option=><button type="button" key={option.id} className={kind===option.id?'selected':''} aria-pressed={kind===option.id} onClick={()=>onKindChange(kind===option.id?'':option.id)}>{option.label}</button>)}</div></div>
   <div className="advanced-filter-fields">
    <label className="field"><span>效果基准</span><select aria-label="礼装效果基准" value={value.basis} onChange={event=>onChange({...value,basis:event.target.value as 'base'|'mlb'})}><option value="base">未满破效果</option><option value="mlb">满破效果</option></select></label>
    <label className="field"><span>属性倾向</span><select aria-label="礼装属性倾向" value={value.statType} onChange={event=>onChange({...value,statType:event.target.value as EquipAdvancedFilters['statType']})}><option value="">不限</option><option value="atk">纯 ATK</option><option value="hp">纯 HP</option><option value="mixed">ATK / HP 混合</option><option value="none">无属性加成</option><option value="unknown">资料未收录</option></select></label>
    <RangeField label="COST" min={value.costMin} max={value.costMax} limit={100} onChange={(costMin,costMax)=>onChange({...value,costMin,costMax})}/>
    <RangeField label="初始 NP（%）" min={value.initialNpMin} max={value.initialNpMax} limit={300} onChange={(initialNpMin,initialNpMax)=>onChange({...value,initialNpMin,initialNpMax})}/>
    <div className="equip-np-presets" role="group" aria-label="初始NP快捷数值"><span>常用数值</span><div>{[30,50,80,100].map(np=><button type="button" key={np} className={value.initialNpMin===np&&value.initialNpMax==null?'selected':''} aria-pressed={value.initialNpMin===np&&value.initialNpMax==null} onClick={()=>onChange({...value,initialNpMin:np,initialNpMax:undefined})}>≥{np}%</button>)}<button type="button" className={value.initialNpMin===0&&value.initialNpMax===0?'selected':''} aria-pressed={value.initialNpMin===0&&value.initialNpMax===0} onClick={()=>onChange({...value,initialNpMin:0,initialNpMax:0})}>无初始 NP</button></div></div>
   </div>
   {value.initialNpMin!=null&&value.initialNpMax!=null&&value.initialNpMin>value.initialNpMax&&<p className="equip-filter-range-warning" role="status">初始 NP 下限高于上限，请调整范围。</p>}
   {value.costMin!=null&&value.costMax!=null&&value.costMin>value.costMax&&<p className="equip-filter-range-warning" role="status">COST 下限高于上限，请调整范围。</p>}
   <div className="equip-effect-heading"><strong>礼装效果</strong><label>多选匹配<select aria-label="礼装效果匹配方式" value={value.effectMode} onChange={event=>onChange({...value,effectMode:event.target.value as 'all'|'any'})}><option value="all">全部满足</option><option value="any">任一满足</option></select></label></div>
   {groups.map(group=><div className="advanced-traits equip-effect-group" key={group} role="group" aria-label={`礼装效果：${group}`}><span>{group}</span><div>{metadata!.effects.filter(effect=>(effect.group||'其他效果')===group).map(effect=>{const selected=value.effects.includes(effect.id);return <button type="button" key={effect.id} className={selected?'selected':''} aria-pressed={selected} onClick={()=>toggle(effect.id)}>{selected&&<span aria-hidden="true">✓</span>}{effect.label}</button>;})}</div></div>)}
   {!metadata?.effects?.length&&<p className="advanced-filter-help">效果资料尚未加载，可先按类型、来源、星级或 COST 查找。</p>}
   <label className="check-field advanced-condition-toggle"><input type="checkbox" checked={value.includeConditional} onChange={event=>onChange({...value,includeConditional:event.target.checked})}/>包含有触发条件的效果</label>
   <p className="advanced-filter-help"><Info size={13} aria-hidden="true"/>初始 NP 数值只计无条件开场充能。效果基准用于查询，不会修改编队要求的满破状态；个人库存仍无法确认实际满破。条件效果请查看礼装详情。</p>
  </div>}
 </section>;
}
