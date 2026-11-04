import {members} from './model';
import type {Catalog,Json,Servant} from './model';
import {equipForDisplay} from './equipRequirements';

export const DEFAULT_PARTY_COST_LIMIT=117;
export const PARTY_COST_STORAGE_KEY='maafgo.battleEditor.partyCost.v1';

export function validPartyCostLimit(value:unknown):value is number {
 return typeof value==='number'&&Number.isInteger(value)&&value>=1&&value<=300;
}

function browserStorage():Storage|null {
 try{return typeof window==='undefined'?null:window.localStorage;}catch{return null;}
}

export function loadPartyCostLimit(storage?:Pick<Storage,'getItem'>|null):number {
 try {
  const raw=(storage===undefined?browserStorage():storage)?.getItem(PARTY_COST_STORAGE_KEY);
  const value:unknown=raw?JSON.parse(raw):null;
  if(value&&typeof value==='object'&&!Array.isArray(value)){
   const candidate=value as Record<string,unknown>;
   if(candidate.version===1&&validPartyCostLimit(candidate.limit))return candidate.limit;
  }
 }catch{/* A blocked store or malformed value must not prevent team editing. */}
 return DEFAULT_PARTY_COST_LIMIT;
}

export function savePartyCostLimit(value:number,storage?:Pick<Storage,'setItem'>|null):boolean {
 if(!validPartyCostLimit(value))return false;
 try {
  const target=storage===undefined?browserStorage():storage;
  if(!target)return false;
  target.setItem(PARTY_COST_STORAGE_KEY,JSON.stringify({version:1,limit:value}));
  return true;
 }catch{return false;}
}

export interface PartyCostIssue {slot:number;message:string}
export interface PartyCost {knownTotal:number;complete:boolean;issues:PartyCostIssue[]}
export interface PartyCostWarning {severity:'warning';code:'party_cost_over_limit';path:'team';message:string}

/** The browser's chosen limit is advisory and never changes structural validity. */
export function partyCostLimitWarning(summary:PartyCost,limit:number):PartyCostWarning|null {
 if(!validPartyCostLimit(limit)||summary.knownTotal<=limit)return null;
 const amount=summary.knownTotal-limit;
 const message=summary.complete
  ?`编队 COST ${summary.knownTotal} 超过当前上限 ${limit}（超出 ${amount}）。请核对编队或调整上限；此提醒不影响保存。`
  :`编队已知 COST 小计 ${summary.knownTotal} 已超过当前上限 ${limit}（至少超出 ${amount}），另有部分 COST 待确认。请核对编队或调整上限；此提醒不影响保存。`;
 return {severity:'warning',code:'party_cost_over_limit',path:'team',message};
}
const record=(value:unknown):value is Json=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const positiveId=(value:unknown):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>0;
const validCost=(value:unknown):value is number=>typeof value==='number'&&Number.isInteger(value)&&value>=0&&value<=100;
const emptyId=(value:unknown):boolean=>value==null||value===0;

function matchesServant(servant:Servant,id:number):boolean {
 return servant.id===id||!!servant.aliases?.some(alias=>{
  const value=typeof alias==='string'&&/^\d+$/.test(alias)?Number(alias):alias;
  return positiveId(value)&&value===id;
 });
}

/** Check the effective stored ID before the display helper hides malformed imports. */
function mainEquip(member:Json):{id?:number;issue?:string} {
 const stored=member.equip1;
 if(stored!=null&&!record(stored))return {issue:'主礼装记录无效，无法确定 COST。'};
 if(stored?.candidates!=null&&(!Array.isArray(stored.candidates)||stored.candidates.length>0))return {issue:'自有从者配置了无法执行的多候选礼装，请调整成员类型或仅保留首选礼装。'};
 const currentId=stored?.id;
 if(currentId!=null&&currentId!==0&&!positiveId(currentId))return {issue:'主礼装 ID 无效，无法确定 COST。'};
 // An explicit current zero disables the legacy requirement, even a malformed one.
 if(currentId===0)return {};
 if(currentId==null&&member.ceId!=null&&member.ceId!==0&&!positiveId(member.ceId))return {issue:'旧版礼装 ID 无效，无法确定 COST。'};
 const selected=equipForDisplay(member,'equip1');
 return selected?{id:selected.id}:{};
}

/**
 * Sum confirmed costs only. Slot indices follow members() (0–5), and no plan or
 * catalog fields are normalized or rewritten while inspecting imported data.
 */
export function partyCost(data:Json,catalog:Catalog):PartyCost {
 let knownTotal=0;
 const issues:PartyCostIssue[]=[];
 const issue=(slot:number,message:string)=>{issues.push({slot,message});};
 for(const [slot,member] of members(data).entries()){
  if(member==null)continue;
  if(!record(member)){issue(slot,'从者槽位记录无效，无法确定 COST。');continue;}
  if(['friend','npc','fixed'].includes(member.supportType))continue;
  if(member.supportType!=null&&member.supportType!=='none'){
   issue(slot,'助战类型未知，无法确定该位置是否计入 COST。');continue;
  }
  if(member.grandSvt!=null&&member.grandSvt!==false&&member.grandSvt!==true){
   issue(slot,'冠位从者标记无效，无法确定该位置的 COST。');continue;
  }
  const equip=mainEquip(member),id=member.svtId;
  if(emptyId(id)){
   // A CE on an unspecified servant cannot establish an actual occupied slot.
   const activeExtra=member.grandSvt===true&&['equip2','equip3'].some(key=>{
    const stored=member[key];return stored!=null&&(!record(stored)||!emptyId(stored.id));
   });
   if(equip.issue||equip.id!==undefined||activeExtra)issue(slot,'尚未指定从者，已记录的礼装无法确定 COST。');
   continue;
  }
  if(!positiveId(id))issue(slot,'从者 ID 无效，无法确定从者 COST。');
  if(member.grandSvt===true){
   // Grand discounts depend on game rules and equipped CE kind. Do not present
   // the undiscounted servant/three-CE sum as an actual, reliable total.
   issue(slot,'冠位从者及其礼装的折扣 COST 尚未确定，请以游戏内实际 COST 为准。');
   continue;
  }
  if(positiveId(id)){
   const servant=catalog.servants.find(item=>item.id===id)??catalog.servants.find(item=>matchesServant(item,id));
   if(!servant)issue(slot,`图鉴缺少从者 #${id}，无法确定从者 COST。`);
   else if(!validCost(servant.cost))issue(slot,`${servant.name} 缺少有效的从者 COST。`);
   else knownTotal+=servant.cost;
  }
  if(equip.issue)issue(slot,equip.issue);
  else if(equip.id!==undefined){
   const essence=catalog.equips.find(item=>item.id===equip.id);
   if(!essence)issue(slot,`图鉴缺少礼装 #${equip.id}，无法确定礼装 COST。`);
   else if(!validCost(essence.cost))issue(slot,`${essence.name} 缺少有效的礼装 COST。`);
   else knownTotal+=essence.cost;
  }
  // Extra CE slots are inactive unless grandSvt is exactly true.
 }
 return {knownTotal,complete:issues.length===0,issues};
}
