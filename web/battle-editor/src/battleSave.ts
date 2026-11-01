import type {Json} from './model';
import {canonicalBattleFileName,sameBattleFileName} from './battleFileName';

export interface BattleSource {name:string;hash:string;path:string}
export interface PreparedBattleSave {name:string;data:Json;target:BattleSource|null}

/** Only server-returned records establish an overwrite identity. */
export function battleSourceRecord(record:unknown):BattleSource|null {
 if(!record||typeof record!=='object'||Array.isArray(record))return null;
 const value=record as Record<string,unknown>;
 if(typeof value.name!=='string'||typeof value.hash!=='string'||!/^[a-f0-9]{64}$/.test(value.hash)||typeof value.path!=='string'||!value.path)return null;
 try{if(canonicalBattleFileName(value.name)!==value.name)return null;}catch{return null;}
 return {name:value.name,hash:value.hash,path:value.path};
}

/** Preparing a save only reads; the caller must obtain confirmation before PUT. */
export async function prepareBattleSave(name:string,data:Json,source:BattleSource|null,
 findTarget:(name:string)=>Promise<BattleSource|null>):Promise<PreparedBattleSave> {
 name=canonicalBattleFileName(name);
 const snapshot=structuredClone(data);
 // Retain the original read hash, rather than silently accepting a newer file.
 const target=source&&sameBattleFileName(name,source.name)?{...source}:await findTarget(name);
 return {name:target?.name??name,data:snapshot,target:target?{...target}:null};
}

export function preparedSaveMatches(save:PreparedBattleSave,current:Json):boolean {
 return JSON.stringify(save.data)===JSON.stringify(current);
}

export function battleSaveRequest(save:PreparedBattleSave):{path:string;method:'POST'|'PUT';body:Json} {
 return save.target
  ?{path:`/battles/${encodeURIComponent(save.target.name)}`,method:'PUT',body:{data:save.data,expectedHash:save.target.hash}}
  :{path:'/battles',method:'POST',body:{name:save.name,data:save.data}};
}
