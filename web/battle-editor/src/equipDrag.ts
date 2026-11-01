import type {Json} from './model';
import {clearEquipRequirement,equipForDisplay,hasStoredEquipRequirement,supportsEquipCandidates} from './equipRequirements';

export type EquipDragKey='equip1'|'equip2'|'equip3';
export interface EquipDragPosition {slot:number;key:EquipDragKey}
const equipKeys:readonly string[]=['equip1','equip2','equip3'];
const legacyPairs=[['id','ceId'],['lv','ceLv'],['limitBreak','ceLimitBreak']] as const;
const mainKeys=['equip1','ceId','ceLv','ceLimitBreak'] as const;
const own=(value:Json,key:string):boolean=>Object.prototype.hasOwnProperty.call(value,key);
const record=(value:unknown):value is Json=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const emptyOrId=(value:unknown):boolean=>value==null||(typeof value==='number'&&Number.isSafeInteger(value)&&value>=0);

function memberAt(data:Json,slot:number):Json|undefined {
 const list=data?.team?.[slot<3?'onFieldSvts':'backupSvts'];
 const member=Array.isArray(list)?list[slot%3]:undefined;
 return record(member)?member:undefined;
}

/** Bond slot two is a servant-specific effect setting, never a movable CE. */
export function equipDragPosition(data:Json,value:unknown):EquipDragPosition|null {
 if(!record(value)||!Number.isInteger(value.slot)||value.slot<0||value.slot>=6||!equipKeys.includes(value.key))return null;
 const member=memberAt(data,value.slot);
 if(!member||value.key==='equip2'||(value.key==='equip3'&&member.grandSvt!==true))return null;
 return {slot:value.slot,key:value.key};
}

function wellFormed(member:Json,key:EquipDragKey):boolean {
 const stored=member[key];
 if(stored!=null&&!record(stored))return false;
 if(record(stored)&&!emptyOrId(stored.id))return false;
 if(record(stored)&&stored.candidates!=null){
  if(!Array.isArray(stored.candidates)||stored.candidates.length>2||stored.candidates.some((candidate:unknown)=>!record(candidate)||!Number.isSafeInteger(candidate.id)||candidate.id<=0))return false;
 }
 // An explicit zero suppresses the old ID. A current positive ID also wins.
 if(key==='equip1'&&stored?.id==null&&!emptyOrId(member.ceId))return false;
 return true;
}

/** Empty requirements can receive a CE, but cannot be picked up themselves. */
export function canStartEquipDrag(data:Json,value:unknown):boolean {
 const position=equipDragPosition(data,value);
 if(!position)return false;
 const member=memberAt(data,position.slot)!;
 try{return wellFormed(member,position.key)&&hasStoredEquipRequirement(member,position.key)&&!!equipForDisplay(member,position.key);}catch{return false;}
}

export function canDropEquipDrag(data:Json,from:unknown,to:unknown):boolean {
 const source=equipDragPosition(data,from),target=equipDragPosition(data,to);
 if(!source||!target||(source.slot===target.slot&&source.key===target.key)||!canStartEquipDrag(data,source))return false;
 const sourceMember=memberAt(data,source.slot)!,targetMember=memberAt(data,target.slot)!;
 const sourceMultiple=Array.isArray(sourceMember[source.key]?.candidates)&&sourceMember[source.key].candidates.length>0;
 const targetMultiple=Array.isArray(targetMember[target.key]?.candidates)&&targetMember[target.key].candidates.length>0;
 return wellFormed(targetMember,target.key)&&(!sourceMultiple||supportsEquipCandidates(targetMember,target.key))&&(!targetMultiple||supportsEquipCandidates(sourceMember,source.key));
}

interface StoredCell {present:boolean;value?:unknown}
function singleCell(member:Json,key:EquipDragKey):StoredCell {
 return own(member,key)?{present:true,value:structuredClone(member[key])}:{present:false};
}

/** A main cell needs its effective legacy fields when it moves to a sub-cell. */
function portableMainCell(member:Json):StoredCell {
 const display=equipForDisplay(member,'equip1');
 if(display)return {present:true,value:display};
 if(!legacyPairs.some(([,legacy])=>own(member,legacy)))return singleCell(member,'equip1');
 // Retain an empty imported placeholder's unknown fields as well. Explicit
 // zero/null/false values keep their existing meaning while aliases disappear.
 const normalized:Json=record(member.equip1)?structuredClone(member.equip1):{};
 for(const [current,legacy] of legacyPairs){
  if((current==='id'?normalized.id==null:!own(normalized,current))&&own(member,legacy))normalized[current]=structuredClone(member[legacy]);
 }
 return {present:true,value:normalized};
}

function mainPackage(member:Json):Json {
 const result:Json={};
 for(const key of mainKeys)if(own(member,key))result[key]=structuredClone(member[key]);
 return result;
}
function writeMainPackage(member:Json,packet:Json):void {
 clearEquipRequirement(member,'equip1');
 for(const key of mainKeys)if(own(packet,key))member[key]=packet[key];
}
function writeCell(member:Json,key:EquipDragKey,cell:StoredCell):void {
 clearEquipRequirement(member,key);
 if(cell.present)member[key]=cell.value;
}

/**
 * Exchange two concrete CE requirements only. Validation and both detached
 * payloads are prepared before either member is edited, so rejected drops do
 * not normalize imports or partly remove a CE. Actions retain slot semantics.
 */
export function swapEquipRequirements(data:Json,from:unknown,to:unknown):boolean {
 if(!canDropEquipDrag(data,from,to))return false;
 const source=equipDragPosition(data,from)!,target=equipDragPosition(data,to)!;
 const sourceMember=memberAt(data,source.slot)!,targetMember=memberAt(data,target.slot)!;
 if(source.key==='equip1'&&target.key==='equip1'){
  let sourcePacket:Json,targetPacket:Json;
  try{sourcePacket=mainPackage(sourceMember);targetPacket=mainPackage(targetMember);}catch{return false;}
  writeMainPackage(sourceMember,targetPacket);writeMainPackage(targetMember,sourcePacket);
 }else{
  let sourceCell:StoredCell,targetCell:StoredCell;
  try{
   sourceCell=source.key==='equip1'?portableMainCell(sourceMember):singleCell(sourceMember,source.key);
   targetCell=target.key==='equip1'?portableMainCell(targetMember):singleCell(targetMember,target.key);
  }catch{return false;}
  writeCell(sourceMember,source.key,targetCell);writeCell(targetMember,target.key,sourceCell);
 }
 return true;
}
