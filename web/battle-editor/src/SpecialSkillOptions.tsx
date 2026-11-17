import {Check,Layers} from 'lucide-react';
import type {SpecialSkillChoiceGroup} from './specialSkillChoices';
import {specialSkillChoiceColor,specialSkillChoiceNP} from './specialSkillChoices';
import type {Json,Servant} from './model';
import {npAt} from './battlePreview';
import {CardArtwork} from './NPCardPreview';
import {cardPreviewRegion} from './npCardRequest';
import type {CardPreviewConfig} from './npCardRequest';
import './specialSkillChoices.css';

/** The same choices are used while adding a skill and editing a recorded use. */
export function SpecialSkillOptions({group,value,onSelect,member,servant,region,config}:{group:SpecialSkillChoiceGroup;value?:unknown;onSelect:(value:number)=>void;member?:Json|null;servant?:Servant;region?:string;config?:CardPreviewConfig}) {
 const initialNP=npAt(member,servant),selectedRegion=cardPreviewRegion(region,config);
 return <div className="special-skill-options" aria-label="特殊技能选项">
  <p>选择这次技能的效果。{group.field==='tdTypeChanges'?'该选择将切换宝具的类型或效果。':'点击选项后继续安排技能。'}</p>
  <div className="special-skill-choice-grid">{group.options.map(option=>{
   const color=specialSkillChoiceColor(option);
   const np=specialSkillChoiceNP(option,initialNP,servant),artwork=!!member&&!!np;
   return <button type="button" key={option.value} className={`special-skill-choice ${color} ${artwork?'with-artwork':''} ${value===option.value?'selected':''}`} aria-pressed={value===option.value} onClick={()=>onSelect(option.value)}>
    {artwork?<CardArtwork member={member} np={np} kind="np" region={selectedRegion} config={config} className="special-choice-artwork"/>:<span className="special-choice-symbol" aria-hidden="true">{color?color[0].toUpperCase():<Layers size={21}/>}</span>}
    <strong>{option.label}</strong>{value===option.value&&<Check size={17} className="special-choice-check" aria-hidden="true"/>}
   </button>;
  })}</div>
 </div>;
}
