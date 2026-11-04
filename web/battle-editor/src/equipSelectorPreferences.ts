import type {Catalog} from './model';
import {EQUIP_SORT_OPTIONS,newEquipAdvancedFilters,normalizeEquipAdvancedFilters} from './equipFilters';
import type {EquipAdvancedFilters,EquipSortKey} from './equipFilters';

// Retain the old key so existing v1 choices migrate without a second storage entry.
export const EQUIP_SELECTOR_STORAGE_KEY='maafgo.battleEditor.equipSelector.v1';

export interface EquipSelectorPreferences {
 version:2;
 rarity:string;
 common:boolean;
 owned:boolean;
 kind:string;
 source:string;
 advanced:EquipAdvancedFilters;
 sortKey:EquipSortKey;
 direction:'asc'|'desc';
}

export function defaultEquipSelectorPreferences():EquipSelectorPreferences {
 return {version:2,rarity:'',common:true,owned:false,kind:'',source:'',advanced:newEquipAdvancedFilters(),sortKey:'id',direction:'asc'};
}

/** Keep only supported filter fields; selection and search belong to the current dialog. */
export function normalizeEquipSelectorPreferences(value:unknown,catalog?:Catalog):EquipSelectorPreferences {
 const defaults=defaultEquipSelectorPreferences();
 if(!value||typeof value!=='object'||Array.isArray(value))return defaults;
 const candidate=value as Record<string,unknown>;
 if(candidate.version!==1&&candidate.version!==2)return defaults;
 const category=(key:'kind'|'source')=>{
  if(candidate.version===1)return '';
  const value=candidate[key],options=key==='kind'?catalog?.equipFilters?.kinds:catalog?.equipFilters?.sources;
  return typeof value==='string'&&value.length<=100&&value===value.trim()&&(options===undefined||options.some(option=>option.id===value))?value:'';
 };
 return {
  version:2,
  rarity:typeof candidate.rarity==='string'&&/^[0-5]$/.test(candidate.rarity)?candidate.rarity:'',
  common:typeof candidate.common==='boolean'?candidate.common:defaults.common,
  owned:typeof candidate.owned==='boolean'?candidate.owned:defaults.owned,
  kind:category('kind'),source:category('source'),
  advanced:candidate.version===2?normalizeEquipAdvancedFilters(candidate.advanced,catalog):newEquipAdvancedFilters(),
  sortKey:candidate.version===2&&EQUIP_SORT_OPTIONS.some(option=>option.key===candidate.sortKey)?candidate.sortKey as EquipSortKey:defaults.sortKey,
  direction:candidate.version===2&&candidate.direction==='desc'?'desc':'asc',
 };
}

function browserStorage():Storage|null {
 try{return typeof window==='undefined'?null:window.localStorage;}catch{return null;}
}

export function loadEquipSelectorPreferences(storage?:Pick<Storage,'getItem'>|null,catalog?:Catalog):EquipSelectorPreferences {
 try {
  const raw=(storage===undefined?browserStorage():storage)?.getItem(EQUIP_SELECTOR_STORAGE_KEY);
  return raw?normalizeEquipSelectorPreferences(JSON.parse(raw),catalog):defaultEquipSelectorPreferences();
 }catch{return defaultEquipSelectorPreferences();}
}

/** A denied or full store must not interrupt selecting a craft essence. */
export function saveEquipSelectorPreferences(preferences:EquipSelectorPreferences,storage?:Pick<Storage,'setItem'>|null):boolean {
 try {
  const target=storage===undefined?browserStorage():storage;
  if(!target)return false;
  target.setItem(EQUIP_SELECTOR_STORAGE_KEY,JSON.stringify(normalizeEquipSelectorPreferences(preferences)));
  return true;
 }catch{return false;}
}
