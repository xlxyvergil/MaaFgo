export const BOND_OPTIMIZER_STORAGE_KEY='maafgo.battleEditor.bondOptimizer.v1';

export interface BondOptimizerPreferences {
 version:1;
 bondBase:number;
 preferredRarity:number;
 ownedServantsOnly:boolean;
 ownedEquipsOnly:boolean;
 modifyEquips:boolean;
 confirmedMlbOnly:boolean;
 reservedSupportSlot:number|null;
 excludeServantIds:number[];
}

export function defaultBondOptimizerPreferences():BondOptimizerPreferences {
 return {version:1,bondBase:1200,preferredRarity:5,ownedServantsOnly:true,ownedEquipsOnly:true,modifyEquips:false,confirmedMlbOnly:false,reservedSupportSlot:null,excludeServantIds:[]};
}
const integer=(value:unknown,min:number,max:number):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=min&&value<=max;

/** Only reusable execution options belong here; query, results and COST are separate. */
export function normalizeBondOptimizerPreferences(value:unknown):BondOptimizerPreferences {
 const defaults=defaultBondOptimizerPreferences();
 if(!value||typeof value!=='object'||Array.isArray(value))return defaults;
 const candidate=value as Record<string,unknown>;
 if(candidate.version!==1)return defaults;
 return {
  version:1,
  bondBase:integer(candidate.bondBase,1,1_000_000)?candidate.bondBase:defaults.bondBase,
  preferredRarity:integer(candidate.preferredRarity,0,5)?candidate.preferredRarity:defaults.preferredRarity,
  ownedServantsOnly:typeof candidate.ownedServantsOnly==='boolean'?candidate.ownedServantsOnly:defaults.ownedServantsOnly,
  ownedEquipsOnly:typeof candidate.ownedEquipsOnly==='boolean'?candidate.ownedEquipsOnly:defaults.ownedEquipsOnly,
  modifyEquips:typeof candidate.modifyEquips==='boolean'?candidate.modifyEquips:defaults.modifyEquips,
  confirmedMlbOnly:typeof candidate.confirmedMlbOnly==='boolean'?candidate.confirmedMlbOnly:defaults.confirmedMlbOnly,
  reservedSupportSlot:integer(candidate.reservedSupportSlot,0,5)?candidate.reservedSupportSlot:null,
  excludeServantIds:Array.isArray(candidate.excludeServantIds)?[...new Set(candidate.excludeServantIds.filter((id):id is number=>integer(id,1,Number.MAX_SAFE_INTEGER)))].slice(0,1000):[],
 };
}
function browserStorage():Storage|null {
 try{return typeof window==='undefined'?null:window.localStorage;}catch{return null;}
}
export function loadBondOptimizerPreferences(storage?:Pick<Storage,'getItem'>|null):BondOptimizerPreferences {
 try {
  const raw=(storage===undefined?browserStorage():storage)?.getItem(BOND_OPTIMIZER_STORAGE_KEY);
  return raw?normalizeBondOptimizerPreferences(JSON.parse(raw)):defaultBondOptimizerPreferences();
 }catch{return defaultBondOptimizerPreferences();}
}
/** Call only after the user executes; canceled dialogs must never save a draft. */
export function saveBondOptimizerPreferences(preferences:BondOptimizerPreferences,storage?:Pick<Storage,'setItem'>|null):boolean {
 try {
  const target=storage===undefined?browserStorage():storage;
  if(!target)return false;
  target.setItem(BOND_OPTIMIZER_STORAGE_KEY,JSON.stringify(normalizeBondOptimizerPreferences(preferences)));
  return true;
 }catch{return false;}
}
