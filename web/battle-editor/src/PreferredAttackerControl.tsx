import {useEffect,useState} from 'react';
import {Search,Shield} from 'lucide-react';
import {servantFor} from './model';
import type {Catalog} from './model';
import {preferredAttackerMode} from './advancedStrategy';

export function PreferredAttackerControl({value,catalog,onChange,onChooseServant}:{value:unknown;catalog?:Catalog;onChange:(value:string|number)=>void;onChooseServant?:()=>void}){
 const mode=preferredAttackerMode(value),selected=typeof value==='string'?value:'';
 const selectedServant=catalog?servantFor(catalog,Number(selected)):undefined;
 const [imageFailed,setImageFailed]=useState(false);
 useEffect(()=>setImageFailed(false),[selectedServant?.image]);
 const selectedName=selected==='0'?'请选择主输出从者':selectedServant?.name??`从者 #${selected}`;
 return <div className="global-attacker-settings">
  <div className="global-strategy-grid">
   <label className="field"><span>主输出偏好</span><select aria-label="主输出偏好" value={mode} onChange={event=>onChange(event.target.value==='slot'?1:event.target.value==='servant'?'0':'')}>
    {mode==='invalid'&&<option value="invalid" disabled>待修正：{JSON.stringify(value)}</option>}
    <option value="inherit">沿用补牌策略</option><option value="slot">按前排位置</option><option value="servant">按从者</option>
   </select></label>
   {mode==='slot'&&<label className="field"><span>主输出位置</span><select aria-label="主输出位置" value={Number(value)} onChange={event=>onChange(Number(event.target.value))}>{['左','中','右'].map((position,index)=><option key={index+1} value={index+1}>前排 {index+1} 号位（{position}）</option>)}</select></label>}
   {mode==='servant'&&<div className="field global-attacker-servant"><span>主输出从者</span>
    <button type="button" className="global-attacker-picker" aria-label="选择主输出从者" aria-haspopup="dialog" title={selectedName} disabled={!onChooseServant} onClick={onChooseServant}>
     <span className="global-attacker-avatar">{selectedServant?.image&&!imageFailed?<img src={selectedServant.image} alt="" onError={()=>setImageFailed(true)}/>:<Shield size={24} aria-hidden="true"/>}</span>
     <span className="global-attacker-name"><strong>{selectedName}</strong><small>{selected==='0'?'打开图鉴选择从者':selectedServant?`${selectedServant.className??'从者'} · ID ${selected}`:`目录中暂无资料 · ID ${selected}`}</small></span>
     <Search size={18} aria-hidden="true"/>
    </button>
   </div>}
  </div>
  <p className="global-strategy-help">提高主输出的普通指令卡在增强补牌时的优先级。按位置始终对应所选前排位置；按从者在换人后继续跟随该从者。沿用补牌策略时保留 JSON 微调中的主输出偏好。</p>
 </div>;
}
