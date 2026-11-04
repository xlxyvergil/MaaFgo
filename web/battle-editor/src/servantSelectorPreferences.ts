import type {Servant} from './model';

export const SERVANT_SELECTOR_STORAGE_KEY='maafgo.battleEditor.servantSelector.v1';
/** Add supported catalog sort fields here; the picker consumes this same table. */
export const SERVANT_SORT_OPTIONS=[
 {key:'id',label:'ID'},
 {key:'collectionNo',label:'图鉴编号'},
 {key:'rarity',label:'星级'},
] as const;
export type ServantSortKey=typeof SERVANT_SORT_OPTIONS[number]['key'];
export type SortDirection='asc'|'desc';
export interface ServantSelectorPreferences {
 version:1;
 classKey:string;
 rarity:string;
 sortKey:ServantSortKey;
 direction:SortDirection;
}

export function defaultServantSelectorPreferences():ServantSelectorPreferences {
 return {version:1,classKey:'',rarity:'',sortKey:'id',direction:'asc'};
}

/** Undefined classes means the catalog is not ready; an empty list is authoritative. */
export function normalizeServantSelectorPreferences(value:unknown,validClassKeys?:readonly string[]):ServantSelectorPreferences {
 const defaults=defaultServantSelectorPreferences();
 if(!value||typeof value!=='object'||Array.isArray(value))return defaults;
 const candidate=value as Record<string,unknown>;
 if(candidate.version!==1)return defaults;
 const classKey=typeof candidate.classKey==='string'&&candidate.classKey.length<=100&&candidate.classKey===candidate.classKey.trim()?candidate.classKey:'';
 return {
  version:1,
  classKey:validClassKeys===undefined||validClassKeys.includes(classKey)?classKey:'',
  rarity:typeof candidate.rarity==='string'&&/^[0-5]$/.test(candidate.rarity)?candidate.rarity:'',
  sortKey:SERVANT_SORT_OPTIONS.some(option=>option.key===candidate.sortKey)?candidate.sortKey as ServantSortKey:defaults.sortKey,
  direction:candidate.direction==='asc'||candidate.direction==='desc'?candidate.direction:defaults.direction,
 };
}

function browserStorage():Storage|null {
 try{return typeof window==='undefined'?null:window.localStorage;}catch{return null;}
}

export function loadServantSelectorPreferences(validClassKeys?:readonly string[],storage?:Pick<Storage,'getItem'>|null):ServantSelectorPreferences {
 try {
  const raw=(storage===undefined?browserStorage():storage)?.getItem(SERVANT_SELECTOR_STORAGE_KEY);
  return raw?normalizeServantSelectorPreferences(JSON.parse(raw),validClassKeys):defaultServantSelectorPreferences();
 }catch{return defaultServantSelectorPreferences();}
}

/** A denied/quota-limited store must not interrupt selecting a servant. */
export function saveServantSelectorPreferences(preferences:ServantSelectorPreferences,storage?:Pick<Storage,'setItem'>|null):boolean {
 try {
  const target=storage===undefined?browserStorage():storage;
  if(!target)return false;
  target.setItem(SERVANT_SELECTOR_STORAGE_KEY,JSON.stringify(normalizeServantSelectorPreferences(preferences)));
  return true;
 }catch{return false;}
}

function sortValue(servant:Pick<Servant,'id'|'collectionNo'|'rarity'>,key:ServantSortKey):number|null {
 const value=servant[key];
 if(typeof value!=='number'||!Number.isFinite(value))return null;
 return key==='rarity'?value>=0?value:null:value>0?value:null;
}

/** Sort the complete filtered result before paging, without changing catalog order. */
export function sortServants<T extends Pick<Servant,'id'|'collectionNo'|'rarity'>>(items:readonly T[],sortKey:ServantSortKey,direction:SortDirection):T[] {
 const sign=direction==='desc'?-1:1;
 return [...items].sort((left,right)=>{
  const a=sortValue(left,sortKey),b=sortValue(right,sortKey);
  if(a===null&&b!==null)return 1;
  if(a!==null&&b===null)return -1;
  if(a!==null&&b!==null&&a!==b)return (a-b)*sign;
  const leftId=sortValue(left,'id'),rightId=sortValue(right,'id');
  if(leftId===null)return rightId===null?0:1;
  if(rightId===null)return -1;
  return leftId-rightId;
 });
}
