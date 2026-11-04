import {useState} from 'react';
import {Check,RotateCcw,Settings2} from 'lucide-react';
import {Modal} from './EditorModal';
import {DEFAULT_PARTY_COST_LIMIT,validPartyCostLimit} from './partyCost';
import type {partyCost} from './partyCost';
import './partyCost.css';

type Summary=ReturnType<typeof partyCost>;

export function PartyCostDisplay({summary,limit,loading,onClick}:{summary:Summary;limit:number;loading:boolean;onClick:()=>void}) {
 const exceeded=summary.knownTotal>limit;
 const value=loading?'…':summary.complete?String(summary.knownTotal):'?';
 return <button type="button" className={`party-cost-display ${!loading&&exceeded?'over-limit':''}`} onClick={onClick} aria-label={`编队COST ${value} / ${limit}，修改上限`} title="查看编队COST并修改上限">
  <span className="party-cost-heading">TOTAL COST<Settings2 size={14} aria-hidden="true"/></span>
  <span className="party-cost-values"><strong>{value}</strong><span>/</span><b>{limit}</b></span>
  <small>{loading?'正在读取COST资料':exceeded?`${summary.complete?'':'已知部分'}超出上限 ${summary.knownTotal-limit}`:summary.complete?'已配置 · 点击调整上限':'部分COST待确认 · 点击查看'}</small>
 </button>;
}

export function PartyCostSettings({summary,limit,onSave,onClose}:{summary:Summary;limit:number;onSave:(limit:number)=>void;onClose:()=>void}) {
 const [value,setValue]=useState(String(limit));
 const parsed=/^\d+$/.test(value)?Number(value):NaN,valid=validPartyCostLimit(parsed);
 return <Modal title="编队COST设置" onClose={onClose}>
  <form className="modal-content party-cost-settings" onSubmit={event=>{event.preventDefault();if(valid)onSave(parsed);}}>
   <div className="party-cost-summary"><span>{summary.complete?'当前已配置COST':'当前可确认小计'}</span><strong>{summary.knownTotal}{!summary.complete&&<small> + 未知</small>}</strong></div>
   <p>只统计已配置的从者与礼装，助战不计费；未要求的位置和礼装请在游戏中核对。</p>
   {summary.issues.length>0&&<ul className="party-cost-issues">{summary.issues.map((issue,index)=><li key={`${issue.slot}-${index}`}>第{issue.slot+1}槽：{issue.message}</li>)}</ul>}
   <label className="field"><span>队伍COST上限</span><input aria-label="队伍COST上限" aria-invalid={!valid} aria-describedby="party-cost-limit-help" type="number" min={1} max={300} step={1} value={value} onChange={event=>setValue(event.target.value)}/><small id="party-cost-limit-help">默认117，填写1–300的整数；当前浏览器记忆，并同步到自动羁绊最大化。</small></label>
   {!valid&&<p className="party-cost-invalid" role="alert">请输入1–300的整数。</p>}
   <div className="modal-bottom"><button className="button subtle" type="button" onClick={()=>setValue(String(DEFAULT_PARTY_COST_LIMIT))}><RotateCcw size={15}/>恢复117</button><div><button className="button" type="button" onClick={onClose}>取消</button><button className="button primary" type="submit" disabled={!valid}><Check size={16}/>保存上限</button></div></div>
  </form>
 </Modal>;
}
