import {useState} from 'react';
import {members,servantFor} from './model';
import type {Catalog,Json} from './model';
import {preferredAttackerMode} from './advancedStrategy';

export function PreferredAttackerControl({value,data,catalog,onChange}:{value:unknown;data:Json;catalog?:Catalog;onChange:(value:string|number)=>void}){
 const mode=preferredAttackerMode(value),[search,setSearch]=useState('');
 const servants=catalog?.servants??[],selected=typeof value==='string'?value:'';
 const query=search.trim().toLocaleLowerCase();
 const shown=servants.filter(servant=>!query||[servant.id,servant.collectionNo,servant.name,servant.nameJp,servant.className,...(servant.searchAliases??[])].join(' ').toLocaleLowerCase().includes(query));
 const inTeam=new Set(members(data).flatMap(member=>member?.svtId?[catalog?servantFor(catalog,member.svtId)?.id??member.svtId:member.svtId]:[]));
 const selectedServant=catalog?servantFor(catalog,Number(selected)):undefined;
 return <div className="global-attacker-settings">
  <div className="global-strategy-grid">
   <label className="field"><span>主输出偏好</span><select aria-label="主输出偏好" value={mode} onChange={event=>{setSearch('');onChange(event.target.value==='slot'?1:event.target.value==='servant'?'0':'');}}>
    {mode==='invalid'&&<option value="invalid" disabled>待修正：{JSON.stringify(value)}</option>}
    <option value="inherit">沿用补牌策略</option><option value="slot">按前排位置</option><option value="servant">按从者</option>
   </select></label>
   {mode==='slot'&&<label className="field"><span>主输出位置</span><select aria-label="主输出位置" value={Number(value)} onChange={event=>onChange(Number(event.target.value))}>{['左','中','右'].map((position,index)=><option key={index+1} value={index+1}>前排 {index+1} 号位（{position}）</option>)}</select></label>}
   {mode==='servant'&&<div className="global-attacker-servant">
    <label className="field"><span>搜索主输出从者</span><input type="search" aria-label="搜索主输出从者" value={search} placeholder="名称、编号或职阶" onChange={event=>setSearch(event.target.value)}/></label>
    <label className="field"><span>主输出从者</span><select aria-label="主输出从者" value={selected} onChange={event=>onChange(event.target.value)}>
     <option value="0" disabled>请选择主输出从者</option>
     {selected!=='0'&&!shown.some(servant=>String(servant.id)===selected)&&<option value={selected}>{selectedServant?.name??`从者 #${selected}`}（{selectedServant?'当前选择':'目录中暂无资料'}）</option>}
     {shown.map(servant=><option key={servant.id} value={String(servant.id)}>{inTeam.has(servant.id)?'【编队中】':''}{servant.collectionNo?`${servant.collectionNo}. `:''}{servant.name} · {servant.className??'从者'}</option>)}
    </select></label>
    {!shown.length&&<p className="global-strategy-help">{servants.length?'没有匹配的从者，请调整搜索条件。':'从者列表尚未加载，请等待本地服务连接后重试。'}</p>}
   </div>}
  </div>
  <p className="global-strategy-help">提高主输出的普通指令卡在增强补牌时的优先级。按位置始终对应所选前排位置；按从者在换人后继续跟随该从者。沿用补牌策略时保留 JSON 微调中的主输出偏好。</p>
 </div>;
}
