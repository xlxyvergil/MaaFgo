import {useEffect,useMemo,useRef,useState} from 'react';
import {AlertTriangle,CheckCircle2,Heart,LoaderCircle,Play,Search,Shield,X} from 'lucide-react';
import {api} from './api';
import {Modal} from './EditorModal';
import {filterCatalog,members} from './model';
import type {Catalog,Json,Servant} from './model';
import {reservedBondSupport} from './bondOptimizerModel';
import {loadBondOptimizerPreferences,saveBondOptimizerPreferences} from './bondOptimizerPreferences';
import {savePartyCostLimit} from './partyCost';
import './bondOptimizer.css';

function ExcludedServantChoice({servant,catalog,checked,onChange}:{servant:Servant;catalog:Catalog;checked:boolean;onChange:(checked:boolean)=>void}) {
 const icon=servant.classIcon||catalog.uiAssets?.classes?.[servant.classKey??''];
 const [faceFailed,setFaceFailed]=useState(false),[classFailed,setClassFailed]=useState(false);
 useEffect(()=>setFaceFailed(false),[servant.image]);useEffect(()=>setClassFailed(false),[icon]);
 const knownRarity=typeof servant.rarity==='number'&&Number.isInteger(servant.rarity)&&servant.rarity>=0&&servant.rarity<=5;
 const rarityLabel=knownRarity?`${servant.rarity}星`:'星级未知',className=servant.className||'职介未知';
 return <label className={`bond-exclusion-card${checked?' selected':''}`}>
  <input type="checkbox" aria-label={`排除${servant.name}，${className}，${rarityLabel}，ID ${servant.id}`} checked={checked} onChange={event=>onChange(event.target.checked)}/>
  <span className="bond-exclusion-visual">
   <span className="bond-exclusion-avatar">{servant.image&&!faceFailed?<img src={servant.image} alt="" loading="lazy" draggable={false} onError={()=>setFaceFailed(true)}/>:<span aria-label="暂无头像">{servant.name.slice(0,1)||'◇'}</span>}</span>
   <span className={`catalog-rarity${knownRarity?'':' unknown'}`} role="img" aria-label={rarityLabel} title={rarityLabel}>{knownRarity?(servant.rarity===0?'☆':'★'.repeat(servant.rarity!)):'—'}</span>
  </span>
  <span className="bond-exclusion-info">
   <strong title={servant.name}>{servant.name}</strong>
   <span className="bond-exclusion-class">{icon&&!classFailed?<img src={icon} alt="" loading="lazy" draggable={false} onError={()=>setClassFailed(true)}/>:<Shield size={20} aria-hidden="true"/>}<span>{className}</span></span>
   <code>{servant.collectionNo!=null?`No.${servant.collectionNo} · `:''}{servant.id}</code>
  </span>
 </label>;
}

export function BondOptimizer({data,catalog,onApply,onClose,initialCostLimit,onCostLimitChange,preferredSupportSlot}:{data:Json;catalog:Catalog;onApply:(response:Json,snapshot:Json)=>void;onClose:()=>void;initialCostLimit:number;onCostLimitChange:(limit:number)=>void;preferredSupportSlot?:number|null}) {
 const [preferences]=useState(loadBondOptimizerPreferences);
 const [settings,setSettings]=useState<Json|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[result,setResult]=useState<Json|null>(null);
 const [costLimit,setCostLimit]=useState(String(initialCostLimit)),[bondBase,setBondBase]=useState(String(preferences.bondBase)),[rarity,setRarity]=useState(preferences.preferredRarity);
 const [ownedServants,setOwnedServants]=useState(preferences.ownedServantsOnly),[ownedEquips,setOwnedEquips]=useState(preferences.ownedEquipsOnly),[modifyEquips,setModifyEquips]=useState(preferences.modifyEquips),[confirmedMlb,setConfirmedMlb]=useState(preferences.confirmedMlbOnly);
 const [reserved,setReserved]=useState<number|null>(()=>reservedBondSupport(data,preferences.reservedSupportSlot??preferredSupportSlot)),[currentCost,setCurrentCost]=useState('');
 const [excluded,setExcluded]=useState<number[]>(preferences.excludeServantIds),[query,setQuery]=useState(''),[rememberFailed,setRememberFailed]=useState(false);
 const resultRef=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(result)resultRef.current?.scrollIntoView({block:'nearest'});},[result]);
 const roster=members(data),support=roster.findIndex(m=>m&&['friend','npc','fixed'].includes(m.supportType));
 const effectiveReserved=reservedBondSupport(data,reserved);
 const grand=roster.some((m,i)=>i!==support&&m?.grandSvt);
 useEffect(()=>{setCurrentCost('');},[data]);
 useEffect(()=>{let active=true;api('/bond/settings').then(value=>{if(active)setSettings(value);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[]);
 const excludedChoices=useMemo(()=>filterCatalog('servant',catalog,{query}) as Servant[],[catalog,query]);
 const inventory=settings?.inventory??{};
 const blocked=!!settings&&((ownedServants&&!inventory.servants?.available)||(ownedEquips&&!inventory.equips?.available));
 const validInteger=(value:string,min:number,max:number)=>/^\d+$/.test(value)&&Number(value)>=min&&Number(value)<=max;
 const invalid=!validInteger(costLimit,1,300)||!validInteger(bondBase,1,1000000)||(grand&&!validInteger(currentCost,0,1000))||effectiveReserved===null;
 const execute=async()=>{
  if(!settings||busy||blocked||invalid)return;
  const remembered=saveBondOptimizerPreferences({version:1,bondBase:Number(bondBase),preferredRarity:rarity,
   ownedServantsOnly:ownedServants,ownedEquipsOnly:ownedEquips,modifyEquips,confirmedMlbOnly:confirmedMlb,
   reservedSupportSlot:effectiveReserved,excludeServantIds:excluded});
  const costRemembered=savePartyCostLimit(Number(costLimit));
  setRememberFailed(!remembered||!costRemembered);onCostLimitChange(Number(costLimit));
  const snapshot=data;setBusy(true);setError('');setResult(null);
  try {
   const response=await api('/bond/optimize','POST',{data:snapshot,options:{costLimit:Number(costLimit),bondBase:Number(bondBase),preferredRarity:rarity,
    ownedServantsOnly:ownedServants,ownedEquipsOnly:ownedEquips,modifyEquips,confirmedMlbOnly:confirmedMlb,
    reservedSupportSlot:effectiveReserved,excludeServantIds:excluded,currentCost:grand?Number(currentCost):null}});
   onApply(response,snapshot);setResult(response);
  } catch(e){setError((e as Error).message);} finally {setBusy(false);}
 };
 return <Modal title="自动羁绊最大化" onClose={onClose} wide closeDisabled={busy}>
  <div className="bond-modal-body">
   <div className="bond-intro"><Heart size={21}/><div><strong>补齐空位，让剩余COST发挥作用</strong><p>保留已有从者及助战，使用工程现有羁绊算法搭配从者与常驻羁绊礼装。结果应用到当前草稿，可撤销。</p><p>点击执行后记住本次设置，下次打开编辑器时自动恢复；取消不覆盖已记住的设置。</p></div></div>
   {error&&<div className="bond-message error" role="alert"><AlertTriangle size={17}/><span>{error}</span></div>}
   {rememberFailed&&<div className="bond-message" role="status"><AlertTriangle size={17}/><span>当前浏览器无法保存全部设置，本次规划仍可执行；下次打开时可能无法恢复。</span></div>}
   {!settings&&!error&&<p className="muted" role="status">正在读取本地羁绊资料和库存…</p>}
   <fieldset disabled={busy} className="bond-options">
    <div className="bond-option-grid">
     <label className="field"><span>队伍COST上限</span><input aria-label="队伍COST上限" type="number" min={1} max={300} value={costLimit} onChange={e=>setCostLimit(e.target.value)}/><small>执行时同步到编队COST上限，并记住设置</small></label>
     <label className="field"><span>关卡基础羁绊</span><input aria-label="关卡基础羁绊" type="number" min={1} max={1000000} value={bondBase} onChange={e=>setBondBase(e.target.value)}/><small>用于比较固定点数与百分比礼装</small></label>
     <label className="field"><span>补齐从者优先星级</span><select aria-label="补齐从者优先星级" value={rarity} onChange={e=>setRarity(Number(e.target.value))}>{[5,4,3,2,1,0].map(n=><option value={n} key={n}>{n}星</option>)}</select><small>无合适候选时按5→0星回退</small></label>
    </div>
    <div className="bond-inventories">{([['servants','仅使用本地已持有从者',ownedServants,setOwnedServants],['equips','仅使用本地已持有礼装',ownedEquips,setOwnedEquips]] as const).map(([kind,label,checked,setter])=><div className="bond-inventory" key={kind}><label className="check-field"><input type="checkbox" checked={checked} onChange={e=>setter(e.target.checked)}/>{label}</label><small>{!settings?'正在读取…':inventory[kind]?.available?`${inventory[kind].count}项 · 扫描于 ${inventory[kind].generatedAt?new Date(inventory[kind].generatedAt).toLocaleString('zh-CN'):'未知时间'}`:'没有可用的完整扫描库存'}</small>{settings&&inventory[kind]?.catalogComplete===false&&<small>库存可能遗漏缺模板的对象</small>}</div>)}</div>
    {blocked&&<div className="bond-message" role="status"><AlertTriangle size={16}/><span>已启用的本地库不可用。请先在Maa构建个人从者礼装库，或取消对应选项，使用工程可识别范围规划。</span></div>}
    {(!ownedServants||!ownedEquips)&&<div className="bond-message"><AlertTriangle size={16}/><span>已取消部分持有限制，可能补入尚未持有的从者或礼装；礼装仍限工程可识别范围，请在执行后核对。</span></div>}
    <div className="bond-option-grid two">
     <label className="field"><span>助战槽位</span><select aria-label="助战槽位" value={effectiveReserved??''} disabled={support>=0} onChange={e=>setReserved(Number(e.target.value))}>{effectiveReserved===null&&<option value="">请先清出一个空位或指定助战</option>}{roster.map((m,i)=>(!m?.svtId||i===support)&&<option value={i} key={i}>第{i+1}槽{support===i?' · 沿用当前助战':' · 保留空位，稍后选择助战'}</option>)}</select><small>优先沿用当前助战；记住的槽位已占用时自动选择空位</small></label>
     {grand&&<label className="field"><span>已有编队实际COST（冠位）</span><input aria-label="已有编队实际COST" type="number" min={0} max={1000} value={currentCost} onChange={e=>setCurrentCost(e.target.value)} placeholder="填写游戏中当前已用COST"/><small>冠位三礼装保持，以实际COST起算</small></label>}
    </div>
    <label className="check-field"><input type="checkbox" checked={modifyEquips} onChange={e=>setModifyEquips(e.target.checked)}/>允许更换已有普通礼装</label>
    <p className="bond-option-help">默认保留已选礼装，只填未要求礼装的位置。开启后会重新搭配普通礼装，请核对战斗所需礼装；冠位三礼装和助战始终保留。</p>
    <details className="bond-advanced"><summary>排除从者与满破设置</summary>
     <label className="check-field"><input type="checkbox" checked={confirmedMlb} onChange={e=>setConfirmedMlb(e.target.checked)}/>需满破的礼装只选库存已确认满破者</label>
     <p className="bond-option-help">新增礼装采用工程满破效果并写入满破要求。库存未记录满破时，勾选会排除这些候选；未勾选需自行确认实际满破情况。</p>
     <label className="field"><span>排除补入从者（如羁绊已满）</span><div className="search-field"><Search size={16}/><input aria-label="搜索排除从者" value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索名称、别名、ID或图鉴编号"/></div></label>
     {excluded.length>0&&<div className="bond-excluded-chips">{excluded.map(id=>{const servant=catalog.servants.find(s=>s.id===id),label=servant?`${servant.name}${servant.className?` · ${servant.className}`:''}`:`#${id}`;return <button type="button" className="chip" key={id} title={`${label} · ID ${id}`} aria-label={`取消排除${label}，ID ${id}`} onClick={()=>setExcluded(ids=>ids.filter(i=>i!==id))}><span>{label}</span><X size={12}/></button>;})}</div>}
     <div className="bond-exclusion-list" role="group" aria-label="排除补入从者">{excludedChoices.length?excludedChoices.map(s=><ExcludedServantChoice key={s.id} servant={s} catalog={catalog} checked={excluded.includes(s.id)} onChange={checked=>setExcluded(ids=>checked?[...ids,s.id]:ids.filter(id=>id!==s.id))}/>):<p className="bond-exclusion-empty" role="status">没有找到匹配的从者</p>}</div>
    </details>
   </fieldset>
   <p className="bond-footnote">最多五名自有从者；助战不计入本地羁绊收益。库存不包含实时羁绊等级，满羁绊从者请在高级设置中排除。算法按星级优先进行有界优化，结果为估算。</p>
   {busy&&<div className="bond-running" role="status"><LoaderCircle size={18}/>正在规划从者与礼装组合，请稍候…</div>}
   {result&&<div ref={resultRef} className="bond-result" role="status"><h3><CheckCircle2 size={19}/>已应用到当前草稿</h3><div className="bond-result-grid"><span>COST<strong>{result.summary.beforeCost} → {result.summary.afterCost}<small> / {result.summary.costLimit}</small></strong></span><span>预估总羁绊<strong>{result.summary.beforeBond.toLocaleString()} → {result.summary.afterBond.toLocaleString()}</strong></span><span>预计增加<strong>+{result.summary.gain.toLocaleString()}</strong></span></div><p>补入{result.summary.addedServants}名从者，调整{result.summary.changedSlots}个槽位。关闭弹窗后可用Ctrl+Z撤销本次结果。</p><ul>{result.warnings.map((text:string)=><li key={text}>{text}</li>)}</ul></div>}
  </div>
  <div className="modal-bottom bond-modal-footer"><button className="button" disabled={busy} onClick={onClose}>{result?'完成':'取消'}</button><button className="button primary" disabled={!settings||busy||blocked||invalid} onClick={()=>void execute()}>{busy?<LoaderCircle size={16}/>:<Play size={16}/>}执行羁绊最大化</button></div>
 </Modal>;
}
