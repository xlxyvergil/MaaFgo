import {Check,Layers} from 'lucide-react';
import type {SpecialSkillChoiceGroup} from './specialSkillChoices';
import './specialSkillChoices.css';

/** The same choices are used while adding a skill and editing a recorded use. */
export function SpecialSkillOptions({group,value,onSelect}:{group:SpecialSkillChoiceGroup;value?:unknown;onSelect:(value:number)=>void}) {
 return <div className="special-skill-options" aria-label="特殊技能选项">
  <p>选择这次技能的效果。{group.field==='tdTypeChanges'?'该选择将改变宝具颜色。':'点击选项后继续安排技能。'}</p>
  <div className="special-skill-choice-grid">{group.options.map(option=>{
   const color=/Arts/i.test(option.label)?'arts':/Buster/i.test(option.label)?'buster':/Quick/i.test(option.label)?'quick':'';
   return <button type="button" key={option.value} className={`special-skill-choice ${color} ${value===option.value?'selected':''}`} aria-pressed={value===option.value} onClick={()=>onSelect(option.value)}>
    <span className="special-choice-symbol" aria-hidden="true">{color?color[0].toUpperCase():<Layers size={21}/>}</span>
    <strong>{option.label}</strong>{value===option.value&&<Check size={17} className="special-choice-check" aria-hidden="true"/>}
   </button>;
  })}</div>
 </div>;
}
