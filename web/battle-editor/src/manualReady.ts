import type {Json} from './model';

export interface StageCommit {previous:Json;next:Json;turnIndex:number}
export interface ManualReadyState {data:Json;turnIndex:number;keys:ReadonlySet<string>}

export function createManualReadyState(data:Json,turnIndex:number):ManualReadyState {
 return {data,turnIndex,keys:new Set<string>()};
}
/** A permission belongs to this exact plan snapshot and selected turn. */
export function manualReadyKeys(state:ManualReadyState,data:Json,turnIndex:number):ReadonlySet<string> {
 return state.data===data&&state.turnIndex===turnIndex?state.keys:new Set<string>();
}
export function syncManualReady(state:ManualReadyState,data:Json,turnIndex:number):ManualReadyState {
 return state.data===data&&state.turnIndex===turnIndex?state:createManualReadyState(data,turnIndex);
}
export function grantManualReady(state:ManualReadyState,data:Json,turnIndex:number,key:string):ManualReadyState {
 const current=syncManualReady(state,data,turnIndex);
 return current.keys.has(key)?current:{...current,keys:new Set([...current.keys,key])};
}
export function revokeManualReady(state:ManualReadyState,data:Json,turnIndex:number,key:string):ManualReadyState {
 const current=syncManualReady(state,data,turnIndex);
 if(!current.keys.has(key))return current;
 const keys=new Set(current.keys);keys.delete(key);
 return {...current,keys};
}
/** Only a confirmed Stage commit can carry unused permissions to its new snapshot. */
export function acceptManualReadyCommit(state:ManualReadyState,commit:StageCommit|null,options:{consume?:string;resetAll?:boolean}={}):ManualReadyState {
 if(!commit||commit.previous===commit.next)return state;
 const inherited=!options.resetAll&&state.data===commit.previous&&state.turnIndex===commit.turnIndex;
 const keys=new Set(inherited?state.keys:[]);
 if(options.consume!==undefined)keys.delete(options.consume);
 return {data:commit.next,turnIndex:commit.turnIndex,keys};
}
