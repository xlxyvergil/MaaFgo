import {useState} from 'react';
import {ChevronDown,ChevronUp,SlidersHorizontal,X} from 'lucide-react';
import {advancedFilterCount,newServantAdvancedFilters} from './model';
import type {ServantAdvancedFilters} from './model';

const fields:{key:'alignment'|'attribute'|'gender'|'subAttribute'|'npType'|'npCard';label:string;options:[string,string][]}[]=[
 {key:'alignment',label:'阵营',options:[['lawful','秩序'],['neutral','中立'],['chaotic','混沌']]},
 {key:'attribute',label:'善恶属性',options:[['good','善'],['balanced','中庸'],['evil','恶'],['summer','夏'],['madness','狂']]},
 {key:'gender',label:'性别特性',options:[['male','男性特性'],['female','女性特性'],['unknown','不明']]},
 {key:'subAttribute',label:'天地人星兽',options:[['sky','天'],['earth','地'],['human','人'],['star','星'],['beast','兽']]},
 {key:'npType',label:'宝具类型',options:[['单体输出','单体输出'],['群体输出','群体输出'],['辅助宝具','辅助']]},
 {key:'npCard',label:'宝具色卡',options:[['Buster','Buster · 红'],['Arts','Arts · 蓝'],['Quick','Quick · 绿']]},
];
const traits:[string,string][]=[['has_sprite','拥有灵衣之人'],['beast_species','兽科'],['living_human','活在当下的人类'],['fsn','FSN'],['star_power','星之力']];

export function AdvancedServantFilters({value,onChange}:{value:ServantAdvancedFilters;onChange:(filters:ServantAdvancedFilters)=>void}) {
 const [open,setOpen]=useState(false),count=advancedFilterCount(value);
 return <section className={`servant-advanced-filters ${open?'open':''}`} aria-label="从者高级筛选">
  <div className="advanced-filter-heading"><button className="advanced-filter-toggle" aria-expanded={open} aria-controls="advanced-servant-options" onClick={()=>setOpen(!open)}><SlidersHorizontal size={15}/><strong>高级筛选</strong><span>[{open?'收起':'展开'}]</span>{count>0&&<b>已选 {count} 项</b>}{value.includeConditional&&<small>含条件形态</small>}{open?<ChevronUp size={15}/>:<ChevronDown size={15}/>}</button><button className="advanced-filter-clear" disabled={!count&&!value.includeConditional} onClick={()=>onChange(newServantAdvancedFilters())}><X size={13}/>清除高级</button></div>
  {open&&<div id="advanced-servant-options" className="advanced-filter-options"><div className="advanced-filter-fields">{fields.map(field=><label className="field" key={field.key}><span>{field.label}</span><select aria-label={`高级筛选：${field.label}`} value={value[field.key]??''} onChange={event=>onChange({...value,[field.key]:event.target.value})}><option value="">不限</option>{field.options.map(([key,label])=><option value={key} key={key}>{label}</option>)}</select></label>)}</div>
   <div className="advanced-traits" role="group" aria-label="羁绊特性筛选"><span>羁绊特性 <small>多选时同时满足</small></span><div>{traits.map(([tag,label])=>{const selected=value.tags?.includes(tag)??false;return <button className={selected?'selected':''} key={tag} aria-pressed={selected} onClick={()=>onChange({...value,tags:selected?(value.tags??[]).filter(item=>item!==tag):[...(value.tags??[]),tag]})}>{selected&&<span aria-hidden="true">✓</span>}{label}</button>;})}</div></div>
   <label className="check-field advanced-condition-toggle"><input type="checkbox" checked={!!value.includeConditional} onChange={event=>onChange({...value,includeConditional:event.target.checked})}/>包含灵基 / 灵衣条件</label><p className="advanced-filter-help">默认只筛基础资料；条件形态逐个匹配。宝具类型与色卡须来自同一变体。候选不会自动切换灵基、灵衣或宝具模式，请核对实际形态。</p>
  </div>}
 </section>;
}
