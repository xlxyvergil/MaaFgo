import {ArrowLeft,Info} from 'lucide-react';
import {Modal} from './EditorModal';
import {SkillIcon} from './PartyCard';
import {projectServantForm,selectedServantForm} from './servantForms';
import type {ServantForm} from './servantForms';
import type {Json,Servant} from './model';
import './servantForms.css';

export function ServantFormPicker({servant,member,onSelect,onBack,onClose}:{servant:Servant;member?:Json|null;onSelect:(form:ServantForm)=>void;onBack:()=>void;onClose:()=>void}) {
 const selected=member?selectedServantForm(servant,member):undefined;
 return <Modal key="servant-forms" wide title={`选择${servant.name}的形态`} onClose={onClose}>
  <section className="servant-form-picker" aria-label="马修形态选择">
   <p className="hint"><Info size={16}/><span>选择后会一起设置星级、COST、技能与宝具。请按游戏中准备使用的形态选择。</span></p>
   <div className="servant-form-grid">{servant.forms?.map(form=>{
    const view=projectServantForm(servant,{limitCount:form.limitCount,skillIds:form.skillIds,tdId:form.tdId})!;
    return <button type="button" key={form.key} className={`servant-form-card ${selected?.key===form.key?'selected':''}`} aria-label={`选择形态：${form.label}`} aria-pressed={selected?.key===form.key} onClick={()=>onSelect(form)}>
     <span className="servant-form-heading">{view.image&&<img src={view.image} alt=""/>}<span><strong>{form.label}</strong><span className="servant-form-rarity" aria-label={`${form.rarity}星`}>{'★'.repeat(form.rarity)}</span><b className="servant-form-cost">COST {form.cost}</b></span>{selected?.key===form.key&&<small>当前</small>}</span>
     <span className="servant-form-skills">{view.skills?.map((skill,index)=><span key={index}><SkillIcon skill={skill} index={index}/><span>{skill.name}</span></span>)}</span>
     <span className="servant-form-np"><b>宝具</b><span>{view.np?.name??'宝具资料待补充'}</span></span>
    </button>;
   })}</div>
   <div className="modal-bottom"><button className="button" onClick={onBack}><ArrowLeft size={16}/>返回从者列表</button><span className="muted">点击形态即可加入编队</span></div>
  </section>
 </Modal>;
}
