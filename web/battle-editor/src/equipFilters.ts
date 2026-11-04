import type {Catalog,Equip} from './model';

export type EquipEffectBasis='base'|'mlb';
export type EquipStatType='atk'|'hp'|'mixed'|'none'|'unknown';
export interface EquipFilterOption {id:string;label:string;group?:string}
export interface EquipFilterCatalog {schemaVersion:1;kinds:EquipFilterOption[];sources:EquipFilterOption[];effects:EquipFilterOption[];version?:unknown}
export interface EquipEffect {tags:string[];description:string;conditional:boolean;target:string;condition?:string;value?:number|null;unit?:string}
export interface EquipEffectSet {tags:string[];initialNp:number|null;effects:EquipEffect[];details?:string[]}
export interface EquipFilterMetadata {kind:string;kindLabel:string;source:string;sourceLabel:string;bondOwner?:number;atkMax:number|null;hpMax:number|null;statType:EquipStatType;base:EquipEffectSet;mlb:EquipEffectSet}
export interface EquipAdvancedFilters {effects:string[];effectMode:'all'|'any';basis:EquipEffectBasis;includeConditional:boolean;initialNpMin?:number;initialNpMax?:number;statType:''|EquipStatType;costMin?:number;costMax?:number}
export interface EquipAdvancedMatch {matches:boolean;conditionalOnly:boolean;matchedEffects:string[];unknown:string[]}
export interface EquipEffectSummary {known:boolean;initialNp:number|null;labels:string[];conditionalLabels:string[];lines:{text:string;conditional:boolean;target:string;condition?:string}[];statLabel:string;text:string}
export const EQUIP_SORT_OPTIONS=[{key:'id',label:'ID'},{key:'collectionNo',label:'图鉴编号'},{key:'rarity',label:'星级'},{key:'cost',label:'COST'}] as const;
export type EquipSortKey=typeof EQUIP_SORT_OPTIONS[number]['key'];
const statLabels:Record<EquipStatType,string>={atk:'纯 ATK',hp:'纯 HP',mixed:'ATK / HP 混合',none:'无属性',unknown:'属性未知'};
const record=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
const amount=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
const strings=(value:unknown):string[]=>Array.isArray(value)?[...new Set(value.filter((item):item is string=>typeof item==='string'&&item.length>0&&item.length<=100&&item===item.trim()))]:[];
const rangeKeys=['initialNpMin','initialNpMax','costMin','costMax'] as const;

export function newEquipAdvancedFilters():EquipAdvancedFilters {return {effects:[],effectMode:'all',basis:'base',includeConditional:false,statType:''};}
/** An unloaded catalog retains selections; a loaded index can discard obsolete tags. */
export function normalizeEquipAdvancedFilters(value:unknown,catalog?:Catalog):EquipAdvancedFilters {
 const defaults=newEquipAdvancedFilters();if(!record(value))return defaults;
 const available=catalog?.equipFilters?.schemaVersion===1?catalog.equipFilters.effects.map(row=>row.id):undefined;
 const filters:EquipAdvancedFilters={effects:strings(value.effects).filter(id=>available===undefined||available.includes(id)),effectMode:value.effectMode==='any'?'any':'all',basis:value.basis==='mlb'?'mlb':'base',includeConditional:value.includeConditional===true,statType:typeof value.statType==='string'&&Object.hasOwn(statLabels,value.statType)?value.statType as EquipStatType:''};
 for(const key of rangeKeys)if(amount(value[key])&&value[key]<=1_000_000)filters[key]=value[key];
 // Keep contradictory ranges visible as an empty result, instead of silently swapping them.
 return filters;
}
export function equipAdvancedFilterCount(filters:EquipAdvancedFilters):number {
 return filters.effects.length+(filters.initialNpMin!==undefined||filters.initialNpMax!==undefined?1:0)+(filters.costMin!==undefined||filters.costMax!==undefined?1:0)+(filters.statType?1:0);
}
function sortValue(equip:Pick<Equip,'id'|'collectionNo'|'rarity'|'cost'>,key:EquipSortKey):number|null {
 const value=equip[key];if(!amount(value))return null;return key==='id'||key==='collectionNo'?value>0?value:null:value;
}
export function sortEquips<T extends Pick<Equip,'id'|'collectionNo'|'rarity'|'cost'>>(items:readonly T[],key:EquipSortKey,direction:'asc'|'desc'):T[]{
 const sign=direction==='desc'?-1:1;return [...items].sort((left,right)=>{
  const a=sortValue(left,key),b=sortValue(right,key);
  if(a===null&&b!==null)return 1;if(a!==null&&b===null)return -1;if(a!==null&&b!==null&&a!==b)return (a-b)*sign;
  const leftId=sortValue(left,'id'),rightId=sortValue(right,'id');return leftId===null?rightId===null?0:1:rightId===null?-1:leftId-rightId;
 });
}
function view(equip:Equip,basis:EquipEffectBasis):EquipEffectSet|undefined {const value=equip.filters?.[basis];return value&&Array.isArray(value.tags)&&Array.isArray(value.effects)?value:undefined;}
function inRange(value:unknown,minimum:number|undefined,maximum:number|undefined,unknown:string[],name:string):boolean {
 if(minimum===undefined&&maximum===undefined)return true;
 if(!amount(value)){unknown.push(name);return false;}
 return (minimum===undefined||value>=minimum)&&(maximum===undefined||value<=maximum);
}
/** Conditional tags are opt-in; conditional charge never enters the initial-NP threshold. */
export function matchEquipAdvanced(equip:Equip,filters:EquipAdvancedFilters):EquipAdvancedMatch {
 const info=view(equip,filters.basis),unconditional=new Set(strings(info?.tags)),conditional=new Set((info?.effects??[]).filter(effect=>effect.conditional===true).flatMap(effect=>strings(effect.tags)));
 const available=new Set([...unconditional,...(filters.includeConditional?conditional:[])]),matchedEffects=filters.effects.filter(id=>available.has(id)),unknown:string[]=[];
 const effectsMatch=!filters.effects.length||(filters.effectMode==='any'?matchedEffects.length>0:matchedEffects.length===filters.effects.length);
 if(filters.effects.length&&!info)unknown.push('effects');
 const npMatch=inRange(info?.initialNp,filters.initialNpMin,filters.initialNpMax,unknown,'initialNp');
 const costMatch=inRange(equip.cost,filters.costMin,filters.costMax,unknown,'cost');
 let statMatch=true;
 if(filters.statType){const actual=equip.filters?.statType??'unknown';statMatch=actual===filters.statType;if(actual==='unknown')unknown.push('statType');}
 const matches=effectsMatch&&npMatch&&costMatch&&statMatch;
 const conditionalOnly=matches&&matchedEffects.length>0&&(filters.effectMode==='any'?!matchedEffects.some(id=>unconditional.has(id)):matchedEffects.some(id=>!unconditional.has(id)));
 return {matches,conditionalOnly,matchedEffects,unknown};
}
/** Summaries expose unknown facts, target and conditions instead of inventing zero values. */
export function equipEffectSummary(equip:Equip,basis:EquipEffectBasis='base',catalog?:Catalog):EquipEffectSummary {
 const info=view(equip,basis),labelsById=new Map(catalog?.equipFilters?.effects.map(effect=>[effect.id,effect.label])??[]);
 const label=(id:string)=>labelsById.get(id)??id;
 const tags=strings(info?.tags),conditional=[...new Set((info?.effects??[]).filter(effect=>effect.conditional===true).flatMap(effect=>strings(effect.tags)))];
 const labels=tags.map(label),conditionalLabels=conditional.map(label),initialNp=amount(info?.initialNp)?info.initialNp:null;
 const lines=(info?.effects??[]).filter(effect=>typeof effect.description==='string'&&effect.description.trim()).map(effect=>({text:effect.description,conditional:effect.conditional===true,target:typeof effect.target==='string'?effect.target:'unknown',...(typeof effect.condition==='string'&&effect.condition?{condition:effect.condition}:{})}));
 const statLabel=equip.filters?.statType?statLabels[equip.filters.statType]??statLabels.unknown:statLabels.unknown;
 const compact=(id:string,conditional:boolean)=>info?.effects.find(effect=>effect.conditional===conditional&&effect.tags.includes(id)&&amount(effect.value)&&typeof effect.description==='string'&&effect.description.trim())?.description??label(id);
 const summaryLabels=[...new Set(tags.filter(id=>id!=='npStart').map(id=>compact(id,false)))];
 const parts=[...(initialNp===null?['初始 NP 未知']:initialNp>0?[`初始 NP ${initialNp}%`]:[]),...summaryLabels,...(conditionalLabels.length?[`条件效果：${conditionalLabels.join('、')}`]:[])];
 if(!parts.length)parts.push(lines.length?'效果详见说明':'初始 NP 0%');
 const text=!info?'效果资料未收录':parts.join(' · ');
 return {known:!!info,initialNp,labels,conditionalLabels,lines,statLabel,text};
}
