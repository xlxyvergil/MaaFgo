export type BattleLibraryItem={
 name:string;title?:string;servants?:(number|null)[];turnCount?:number;
 modifiedAt?:string|null;createdAt?:string|null;createdAtSource?:'birthtime'|'firstSeen'|'unknown';
 favorite?:boolean;error?:string;
};
export const BATTLE_LIBRARY_STORAGE_KEY='maafgo.battleEditor.library.v1';
export const BATTLE_LIBRARY_SORT_OPTIONS=[
 {key:'modifiedAt',label:'修改时间'},
 {key:'createdAt',label:'创建时间'},
 {key:'name',label:'名称'},
] as const;
export type BattleLibrarySortKey=typeof BATTLE_LIBRARY_SORT_OPTIONS[number]['key'];
export type BattleLibrarySortDirection='asc'|'desc';
export type BattleLibraryPreferences={version:1;sortKey:BattleLibrarySortKey;direction:BattleLibrarySortDirection};

export function defaultBattleLibraryPreferences():BattleLibraryPreferences {
 return {version:1,sortKey:'modifiedAt',direction:'desc'};
}

export function normalizeBattleLibraryPreferences(value:unknown):BattleLibraryPreferences {
 const defaults=defaultBattleLibraryPreferences();
 if(!value||typeof value!=='object'||Array.isArray(value))return defaults;
 const item=value as Record<string,unknown>;
 if(item.version!==1)return defaults;
 return {version:1,
  sortKey:BATTLE_LIBRARY_SORT_OPTIONS.some(option=>option.key===item.sortKey)?item.sortKey as BattleLibrarySortKey:defaults.sortKey,
  direction:item.direction==='asc'||item.direction==='desc'?item.direction:defaults.direction,
 };
}

function browserStorage():Storage|null {
 try{return typeof window==='undefined'?null:window.localStorage;}catch{return null;}
}
export function loadBattleLibraryPreferences(storage?:Pick<Storage,'getItem'>|null):BattleLibraryPreferences {
 try{const raw=(storage===undefined?browserStorage():storage)?.getItem(BATTLE_LIBRARY_STORAGE_KEY);return raw?normalizeBattleLibraryPreferences(JSON.parse(raw)):defaultBattleLibraryPreferences();}
 catch{return defaultBattleLibraryPreferences();}
}
export function saveBattleLibraryPreferences(preferences:BattleLibraryPreferences,storage?:Pick<Storage,'setItem'>|null):boolean {
 try{const target=storage===undefined?browserStorage():storage;if(!target)return false;target.setItem(BATTLE_LIBRARY_STORAGE_KEY,JSON.stringify(normalizeBattleLibraryPreferences(preferences)));return true;}
 catch{return false;}
}
export function changeBattleLibrarySort(preferences:BattleLibraryPreferences,sortKey:BattleLibrarySortKey):BattleLibraryPreferences {
 return {...preferences,sortKey,direction:sortKey==='name'?'asc':'desc'};
}

export function battleLibraryTitle(item:BattleLibraryItem):string {return item.title?.trim()||item.name;}
const collator=new Intl.Collator('zh-CN',{numeric:true,sensitivity:'base'});
function fileOrder(a:string,b:string):number {return collator.compare(a,b)||(a<b?-1:a>b?1:0);}
function timestamp(value:unknown):number|null {
 if(typeof value!=='string'||!value.trim())return null;
 const date=Date.parse(value);return Number.isFinite(date)?date:null;
}

/** Favorite groups always lead; unknown times stay last in either direction. */
export function filterAndSortBattleLibrary(items:readonly BattleLibraryItem[],query:string,preferences:BattleLibraryPreferences):BattleLibraryItem[] {
 const search=query.trim().toLocaleLowerCase('zh-CN'),direction=preferences.direction==='asc'?1:-1;
 return items.filter(item=>!search||`${item.name} ${item.title??''}`.toLocaleLowerCase('zh-CN').includes(search)).sort((a,b)=>{
  const favorite=Number(b.favorite===true)-Number(a.favorite===true);if(favorite)return favorite;
  if(preferences.sortKey==='name')return collator.compare(battleLibraryTitle(a),battleLibraryTitle(b))*direction||fileOrder(a.name,b.name);
  const first=preferences.sortKey==='createdAt'&&a.createdAtSource==='unknown'?null:timestamp(a[preferences.sortKey]),second=preferences.sortKey==='createdAt'&&b.createdAtSource==='unknown'?null:timestamp(b[preferences.sortKey]);
  if(first===null&&second!==null)return 1;if(first!==null&&second===null)return -1;
  return first!==null&&second!==null&&first!==second?(first-second)*direction:fileOrder(a.name,b.name);
 });
}

export function battleLibraryTimeText(item:BattleLibraryItem,sortKey:BattleLibrarySortKey):string {
 const creation=sortKey==='createdAt',label=creation?(item.createdAtSource==='firstSeen'?'首次收录':'创建'):'修改';
 const value=creation&&item.createdAtSource==='unknown'?null:timestamp(creation?item.createdAt:item.modifiedAt);
 if(value===null)return `${label}时间未记录`;
 return `${label} ${new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}`;
}
