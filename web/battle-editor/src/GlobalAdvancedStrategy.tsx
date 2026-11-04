import {useEffect,useState} from 'react';
import {SlidersHorizontal} from 'lucide-react';
import type {Json} from './model';
import {advancedStrategyOf,advancedStrategyIssues,parseNativeStrategyOverrides,strategyInteger,STRATEGY_PRESETS} from './advancedStrategy';

function StrategyNumber({label,value,min,max,onCommit,onError,onValidity,onInvalid,optional=false}:{label:string;value:number|null;min:number;max:number;onCommit:(value:number|null)=>void;onError:(message:string)=>void;onValidity:(message:string)=>void;onInvalid:(text:string)=>void;optional?:boolean}){
 const [text,setText]=useState(value==null?'':String(value));
 useEffect(()=>{setText(value==null?'':String(value));onValidity('');},[value]);
 return <label className="field"><span>{label}</span><input aria-label={label} type="number" min={min} max={max} step={1} placeholder={optional?'未指定':undefined} value={text} onChange={e=>{setText(e.target.value);try{if(!optional||e.target.value.trim())strategyInteger(e.target.value,min,max,label);onValidity('');}catch(error){onValidity((error as Error).message);onInvalid(e.target.value);}}} onBlur={()=>{try{onCommit(optional&&!text.trim()?null:strategyInteger(text,min,max,label));}catch(e){onError((e as Error).message);}}}/></label>;
}

export function GlobalAdvancedStrategy({data,onChange,onError,onValidityChange}:{data:Json;onChange:(patch:Json)=>void;onError:(message:string)=>void;onValidityChange?:(message:string)=>void}){
 const value=advancedStrategyOf(data),opening=value.openingHand,documentIssues=advancedStrategyIssues(data);
 const encoded=typeof value.nativeStrategyOverrides==='string'?value.nativeStrategyOverrides:JSON.stringify(value.nativeStrategyOverrides,null,2)??'{}';
 const [overrides,setOverrides]=useState(encoded),[servantId,setServantId]=useState(opening.servantId??''),[colors,setColors]=useState(Array.isArray(opening.requiredColors)?opening.requiredColors.join(', '):String(opening.requiredColors??''));
 const [draftIssues,setDraftIssues]=useState<Record<string,string>>({});
 const validity=(key:string,message:string)=>setDraftIssues(previous=>({...previous,[key]:message}));
 useEffect(()=>onValidityChange?.(Object.values(draftIssues).filter(Boolean).join('；')),[draftIssues,onValidityChange]);
 useEffect(()=>()=>onValidityChange?.(''),[onValidityChange]);
 useEffect(()=>{setOverrides(encoded);validity('overrides','');},[encoded]);
 useEffect(()=>{setServantId(opening.servantId??'');validity('servantId','');},[opening.servantId]);
 const colorKey=JSON.stringify(opening.requiredColors);
 useEffect(()=>{setColors(Array.isArray(opening.requiredColors)?opening.requiredColors.join(', '):String(opening.requiredColors??''));validity('colors','');},[colorKey]);
 const openingChange=(patch:Json)=>onChange({openingHand:patch});
 const issues=[...documentIssues,...Object.values(draftIssues).filter(Boolean)];
 const saveId=()=>{const id=String(servantId).trim();if(id&&(!/^\d{1,12}$/.test(id)||Number(id)<=0)){onError('目标从者 ID 必须是正整数，可留空使用当前前排位置。');return;}openingChange({servantId:id||null});};
 const saveColors=()=>{const list=colors.toUpperCase().split(/[\s,，、]+/).filter(Boolean);if(!list.length||list.length>3||list.some(c=>!['B','A','Q'].includes(c))){onError('所需牌色请填写 1～3 项 B、A、Q，例如 B, Q；重复牌色表示需要多张。');return;}openingChange({requiredColors:list});};
 const saveOverrides=()=>{try{onChange({nativeStrategyOverrides:parseNativeStrategyOverrides(overrides)});}catch(e){onError((e as Error).message);}};
 return <details className="battle-action-inspector global-advanced-strategy" id="global-advanced-strategy">
  <summary><SlidersHorizontal size={18}/><span>全局高级策略</span><small>{STRATEGY_PRESETS.find(([key])=>key===value.preset)?.[1]??'待修正'} · 最多 {String(value.maxTurns)} 回合</small></summary>
  <div className="global-strategy-body">
   <p className="global-strategy-help">作用于整场战斗。MXU 的「高级策略」选择「使用编辑器/chaldea配置」后生效；明确安排的技能、宝具和出卡顺序优先。未设置回合时也可使用。</p>
   {issues.length>0&&<div role="alert" className="global-strategy-errors">{issues.map(message=><p key={message}>{message}</p>)}</div>}
   <div className="global-strategy-grid"><label className="field"><span>原生补牌策略</span><select aria-label="全局原生补牌策略" value={value.preset} onChange={e=>onChange({preset:e.target.value})}>{!STRATEGY_PRESETS.some(([key])=>key===value.preset)&&<option value={value.preset}>无效预设：{String(value.preset)}</option>}{STRATEGY_PRESETS.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><StrategyNumber label="最大战斗回合" value={value.maxTurns} min={1} max={100} onCommit={maxTurns=>onChange({maxTurns})} onError={onError} onValidity={message=>validity('maxTurns',message)} onInvalid={maxTurns=>onChange({maxTurns})}/></div>
   <label className="check-field"><input type="checkbox" checked={value.autoServantSkills===true} onChange={e=>onChange({autoServantSkills:e.target.checked})}/>自动补放从者技能</label><p className="global-strategy-help">只在当前没有固定回合方案时自动使用已识别为可用的从者技能；需要指定目标的技能建议明确编排。</p>
   <label className="check-field"><input type="checkbox" checked={opening.enabled===true} onChange={e=>openingChange({enabled:e.target.checked})}/>开局等待关键牌</label>
   {opening.enabled===true&&<fieldset className="global-opening-settings"><legend>开局等牌条件</legend><p className="global-strategy-help">在原计划首回合放技能前检查目标从者的牌。没有回合方案时跳过等待；有方案时仅适合从新战斗主界面启动。目标从者 ID 优先于前排位置，也可在微调中指定目标。</p><div className="global-strategy-grid"><label className="field"><span>目标从者 ID</span><input aria-label="开局等牌目标从者 ID" placeholder="未指定，例如 605300" value={servantId} onChange={e=>{const id=e.target.value;setServantId(id);if(id.trim()&&(!/^\d{1,12}$/.test(id.trim())||Number(id)<=0))openingChange({servantId:id});validity('servantId',id.trim()&&(!/^\d{1,12}$/.test(id.trim())||Number(id)<=0)?'目标从者 ID 必须是正整数。':'');}} onBlur={saveId}/></label><StrategyNumber label="目标当前前排位置" value={opening.servantSlot} min={1} max={3} optional onCommit={servantSlot=>openingChange({servantSlot})} onError={onError} onValidity={message=>validity('servantSlot',message)} onInvalid={servantSlot=>openingChange({servantSlot})}/><label className="field"><span>所需牌色</span><input aria-label="开局等牌所需牌色" placeholder="B, Q" value={colors} onChange={e=>{setColors(e.target.value);const list=e.target.value.toUpperCase().split(/[\s,，、]+/).filter(Boolean);if(!list.length||list.length>3||list.some(c=>!['B','A','Q'].includes(c)))openingChange({requiredColors:e.target.value});validity('colors',!list.length||list.length>3||list.some(c=>!['B','A','Q'].includes(c))?'所需牌色必须为 1～3 项 B、A、Q。':'');}} onBlur={saveColors}/><small>B 红 / A 蓝 / Q 绿；重复表示多张</small></label><StrategyNumber label="最多普攻等待回合" value={opening.maxWaitTurns} min={0} max={5} onCommit={maxWaitTurns=>openingChange({maxWaitTurns})} onError={onError} onValidity={message=>validity('maxWaitTurns',message)} onInvalid={maxWaitTurns=>openingChange({maxWaitTurns})}/></div><p className="global-strategy-help">等待期间不释放原计划技能和宝具；0 表示只检查首回合，不满足即停止。</p></fieldset>}
   <label className="field global-strategy-json"><span>补牌策略微调（JSON）</span><textarea aria-label="全局补牌策略 JSON" rows={5} spellCheck={false} value={overrides} onChange={e=>{setOverrides(e.target.value);try{parseNativeStrategyOverrides(e.target.value);validity('overrides','');}catch(error){validity('overrides',(error as Error).message);onChange({nativeStrategyOverrides:e.target.value});}}} onBlur={saveOverrides}/></label><p className="global-strategy-help">留空或 {'{}'} 使用预设。可指定主输出、宝具颜色和链权重，例如 {'{"preferred_slots":[2],"brave_chain_weight":20}'}。只补方案未固定的普通卡；宝具颜色需要明确配置，当前不根据敌方血量、暴击率或增益图标判断。</p>
  </div>
 </details>;
}
