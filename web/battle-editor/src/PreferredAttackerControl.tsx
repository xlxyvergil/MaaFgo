import type {Catalog} from './model';
import {preferredAttackerMode} from './advancedStrategy';
import type {FillPriority} from './advancedStrategy';
import {ServantPickerButton} from './ServantPickerButton';

export function PreferredAttackerControl({value,catalog,fillPriority='chain',onChange,onChooseServant}:{value:unknown;catalog?:Catalog;fillPriority?:FillPriority;onChange:(value:string|number)=>void;onChooseServant?:()=>void}){
 const mode=preferredAttackerMode(value);
 return <div className="global-attacker-settings">
  <div className="global-strategy-grid">
   <label className="field"><span>主输出偏好</span><select aria-label="主输出偏好" value={mode} onChange={event=>onChange(event.target.value==='slot'?1:event.target.value==='servant'?'0':'')}>
    {mode==='invalid'&&<option value="invalid" disabled>待修正：{JSON.stringify(value)}</option>}
    <option value="inherit">{fillPriority==='servant'?'自动选择主输出（可继承微调）':'沿用补牌策略'}</option><option value="slot">按前排位置</option><option value="servant">按从者</option>
   </select></label>
   {mode==='slot'&&<label className="field"><span>主输出位置</span><select aria-label="主输出位置" value={Number(value)} onChange={event=>onChange(Number(event.target.value))}>{['左','中','右'].map((position,index)=><option key={index+1} value={index+1}>前排 {index+1} 号位（{position}）</option>)}</select></label>}
   {mode==='servant'&&<div className="field global-attacker-servant"><span>主输出从者</span>
    <ServantPickerButton value={value} catalog={catalog} label="选择主输出从者" placeholder="请选择主输出从者" onChoose={onChooseServant}/>
   </div>}
  </div>
  <p className="global-strategy-help">{fillPriority==='servant'?'先补主输出的可用普通卡，再比较连携策略。未指定主输出且微调中也未配置时，自动选择当前前排最靠左的攻击宝具从者。':'先比较连携策略，评分相同时再匹配主输出；可以不指定主输出。'}按位置始终对应所选前排位置；按从者在换人后继续跟随该从者。未单独指定时保留 JSON 微调中的主输出偏好。</p>
 </div>;
}
