import {useEffect,useState} from 'react';
import {ArrowLeft,Check,Info} from 'lucide-react';
import {Modal} from './EditorModal';
import {servantFor} from './model';
import type {Catalog,Equip} from './model';
import {equipEffectSummary} from './equipFilters';
import type {EquipEffectBasis} from './equipFilters';

function EquipArtwork({equip}:{equip:Equip}) {
 const [failed,setFailed]=useState(false);useEffect(()=>setFailed(false),[equip.image]);
 return <div className="equip-result-artwork"><div style={{aspectRatio:equip.imageWidth&&equip.imageHeight?`${equip.imageWidth}/${equip.imageHeight}`:'150/68'}}>{equip.image&&!failed?<img src={equip.image} alt="" loading="lazy" draggable={false} onError={()=>setFailed(true)}/>:<span>礼</span>}</div><span className="catalog-rarity" role="img" aria-label={`${equip.rarity??'未知'}星`}>{typeof equip.rarity==='number'&&equip.rarity>=0&&equip.rarity<=5?equip.rarity===0?'☆':'★'.repeat(equip.rarity):'—'}</span></div>;
}

export function EquipCatalogCard({equip,catalog,basis,conditionalOnly,onSelect,onDetails}:{equip:Equip;catalog:Catalog;basis:EquipEffectBasis;conditionalOnly:boolean;onSelect:()=>void;onDetails:()=>void}) {
 const summary=equipEffectSummary(equip,basis,catalog);
 const owner=equip.filters?.bondOwner,ownerName=owner?servantFor(catalog,owner)?.name??`从者 #${owner}`:'';
 const details=summary.lines.map(line=>`${line.conditional?'[条件] ':''}${line.text}${line.condition?`（${line.condition}）`:''}`).join('\n');
 return <article className="catalog-card equip-result-card">
  <button type="button" className="equip-select-result" aria-label={`选择礼装：${equip.name}`} onClick={onSelect}>
   <EquipArtwork equip={equip}/><div className="equip-result-copy"><strong title={equip.name}>{equip.name}</strong><small className="catalog-equip-meta">{equip.filters?.kindLabel??'概念礼装'}{equip.filters?.sourceLabel?` · ${equip.filters.sourceLabel}`:''}</small><small className={`equip-effect-summary ${summary.known?'':'equip-filter-unknown'}`} title={details||summary.text}>{summary.text}</small>{conditionalOnly&&<em className="conditional-form-match">仅条件效果匹配</em>}{ownerName&&<small className="equip-owner">{ownerName} · 羁绊礼装</small>}<code>{equip.collectionNo?`No.${equip.collectionNo} · `:''}{equip.id} · COST {equip.cost??'未知'}</code></div>
  </button><button type="button" className="equip-details-button" aria-label={`查看${equip.name}效果详情`} title="查看完整效果、条件与属性" onClick={onDetails}><Info size={16}/></button>
 </article>;
}

export function EquipDetails({equip,catalog,basis,onClose,onSelect}:{equip:Equip;catalog:Catalog;basis:EquipEffectBasis;onClose:()=>void;onSelect:()=>void}) {
 const [displayBasis,setDisplayBasis]=useState(basis),summary=equipEffectSummary(equip,displayBasis,catalog);
 const owner=equip.filters?.bondOwner,ownerName=owner?servantFor(catalog,owner)?.name??`从者 #${owner}`:null;
 return <Modal title={`${equip.name} · 礼装效果`} onClose={onClose}><div className="modal-content equip-detail-content">
  <div className="equip-detail-heading"><EquipArtwork equip={equip}/><div><strong>{equip.name}</strong><p>{equip.filters?.kindLabel??'概念礼装'}{equip.filters?.sourceLabel?` · ${equip.filters.sourceLabel}`:''}</p><small>{summary.statLabel} · 最大 ATK {equip.filters?.atkMax??'未知'} / HP {equip.filters?.hpMax??'未知'} · COST {equip.cost??'未知'}</small>{ownerName&&<small>对应从者：{ownerName}</small>}</div></div>
  <label className="field"><span>查看效果</span><select aria-label="礼装详情效果基准" value={displayBasis} onChange={event=>setDisplayBasis(event.target.value as EquipEffectBasis)}><option value="base">未满破效果</option><option value="mlb">满破效果</option></select></label>
  <p className="equip-detail-np">无条件初始 NP：<strong>{summary.initialNp==null?'资料未收录':`${summary.initialNp}%`}</strong></p>
  <div className="equip-detail-tags">{summary.labels.map(label=><span key={label}>{label}</span>)}{summary.conditionalLabels.map(label=><span className="conditional" key={`conditional:${label}`}>{label} · 有条件</span>)}</div>
  {summary.lines.length?<ul className="equip-effect-lines">{summary.lines.map((line,index)=><li className={line.conditional?'conditional':''} key={index}><span>{line.text}</span>{line.target&&line.target!=='self'&&line.target!=='unknown'&&<small>作用对象：{line.target}</small>}{line.conditional&&<small>触发条件：{line.condition||'满足礼装说明中的条件时生效'}</small>}</li>)}</ul>:<p className="muted">效果资料尚未收录，请以游戏内说明为准。</p>}
  {!!equip.filters?.[displayBasis]?.details?.length&&<details className="equip-original-details"><summary>完整效果说明（日服原文）</summary>{equip.filters[displayBasis].details!.map((text,index)=><p key={index}>{text}</p>)}</details>}
  <p className="advanced-filter-help">此处切换只用于查看数值。选择礼装后，满破要求仍在编队属性中设置。</p>
  <div className="modal-bottom"><button type="button" className="button" onClick={onClose}><ArrowLeft size={15}/>返回筛选</button><button type="button" className="button primary" onClick={onSelect}><Check size={15}/>选择这张礼装</button></div>
 </div></Modal>;
}
