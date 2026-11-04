import type {Json} from './model';

const equipKeys=['equip1','equip2','equip3'] as const;
const legacyKeys=['ceId','ceLimitBreak','ceLv'] as const;
const own=(value:Json,key:string):boolean=>Object.prototype.hasOwnProperty.call(value,key);
const record=(value:unknown):value is Json=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const positiveId=(value:unknown):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>0;

function assertEquipKey(key:string):void {
 if(!(equipKeys as readonly string[]).includes(key))throw new Error('礼装槽位必须是 equip1、equip2 或 equip3。');
}

/** An explicit clear removes the requirement and the first slot's legacy fallback. */
export function clearEquipRequirement(member:Json,key:string):void {
 assertEquipKey(key);
 if(!record(member))throw new Error('请先选择有效的从者。');
 delete member[key];
 if(key==='equip1')for(const legacy of legacyKeys)delete member[legacy];
}

/** Even an imported null/zero requirement can be explicitly cleaned up. */
export function hasStoredEquipRequirement(member:Json|null,key:string):boolean {
 assertEquipKey(key);
 return record(member)&&(own(member,key)||(key==='equip1'&&legacyKeys.some(legacy=>own(member,legacy))));
}

/** Display compatibility only: never normalize or change an imported member. */
export function equipForDisplay(member:Json|null,key:string):Json|undefined {
 assertEquipKey(key);
 if(!record(member))return undefined;
 const stored=member[key];
 if(stored!=null&&!record(stored))return undefined;
 const id=stored?.id;
 // A zero ID explicitly disables a requirement; only an absent/null ID falls back.
 const selected=positiveId(id)?id:id==null&&key==='equip1'&&positiveId(member.ceId)?member.ceId:undefined;
 if(selected===undefined)return undefined;
 const display:Json=stored?structuredClone(stored):{};
 display.id=selected;
 if(key==='equip1')for(const [current,legacy] of [['lv','ceLv'],['limitBreak','ceLimitBreak']]){
  if(!own(display,current)&&own(member,legacy))display[current]=structuredClone(member[legacy]);
 }
 return display;
}

export const MAX_EQUIP_CANDIDATES=3;

export function supportsEquipCandidates(member:Json|null,key:string):boolean {
 assertEquipKey(key);
 return !!member&&['friend','npc','fixed'].includes(member.supportType)&&(key==='equip1'||(key==='equip3'&&member.grandSvt===true));
}

/** Three visible slots, retaining malformed imported positions until explicitly edited. */
export function equipCandidatesForDisplay(member:Json|null,key:string):Json[] {
 const primary=equipForDisplay(member,key),stored=record(member)?member[key]:undefined;
 const extras=record(stored)&&Array.isArray(stored.candidates)?stored.candidates:[];
 if(!primary&&!extras.length)return [];
 const first=primary??(record(stored)?structuredClone(stored):{});
 delete first.candidates;
 return [first,...extras.slice(0,MAX_EQUIP_CANDIDATES-1).map((value:unknown)=>record(value)?structuredClone(value):{})];
}

function editableRequirement(member:Json,key:string):Json {
 assertEquipKey(key);
 if(!record(member))throw new Error('请先选择有效的从者或助战。');
 const stored=member[key];
 if(stored!=null&&!record(stored))throw new Error('礼装记录无效，请先在原始 JSON 中修正。');
 if(stored?.candidates!=null&&(!Array.isArray(stored.candidates)||stored.candidates.length>2||stored.candidates.some((c:unknown)=>!record(c))))throw new Error('礼装候选记录无效或超过3项，请先在原始 JSON 中修正。');
 return equipForDisplay(member,key)??(record(stored)?structuredClone(stored):{});
}

/** Explicit edits materialize the legacy first CE once, without rewriting other members. */
export function setEquipCandidate(member:Json,key:string,index:number,id:number):void {
 if(!Number.isInteger(index)||index<0||index>=MAX_EQUIP_CANDIDATES||!positiveId(id))throw new Error('候选礼装或位置无效。');
 if(index>0&&!supportsEquipCandidates(member,key))throw new Error('当前礼装槽不支持助战多候选筛选。');
 const next=editableRequirement(member,key),extras:Json[]=Array.isArray(next.candidates)?next.candidates:[];
 if(index>0&&(!positiveId(next.id)||index>extras.length+1))throw new Error('请先添加前一个候选礼装。');
 const all=[next,...extras];
 if(all.some((candidate,i)=>i!==index&&candidate.id===id))throw new Error('这个礼装已经是该槽的候选，请选择其他礼装。');
 if(index===0)next.id=id;
 else{extras[index-1]={...(extras[index-1]??{}),id};next.candidates=extras;}
 member[key]=next;
 if(key==='equip1')for(const legacy of legacyKeys)delete member[legacy];
}

export function updateEquipCandidate(member:Json,key:string,index:number,changes:Json):void {
 if(!Number.isInteger(index)||index<0||index>=MAX_EQUIP_CANDIDATES)throw new Error('候选礼装位置无效。');
 const next=editableRequirement(member,key),target=index===0?next:next.candidates?.[index-1];
 if(!record(target)||!positiveId(target.id))throw new Error('请先选择候选礼装。');
 for(const field of ['lv','limitBreak'])if(own(changes,field)){if(changes[field]===undefined)delete target[field];else target[field]=changes[field];}
 member[key]=next;
 if(key==='equip1')for(const legacy of legacyKeys)delete member[legacy];
}

export function removeEquipCandidate(member:Json,key:string,index:number):void {
 if(!Number.isInteger(index)||index<0||index>=MAX_EQUIP_CANDIDATES)throw new Error('候选礼装位置无效。');
 const next=editableRequirement(member,key),extras:Json[]=Array.isArray(next.candidates)?next.candidates:[];
 if(index===0){
  if(!extras.length){clearEquipRequirement(member,key);return;}
  const promoted=extras.shift()!;
  delete promoted.candidates;
  if(extras.length)promoted.candidates=extras;
  member[key]=promoted;
 }else{
  if(index>extras.length)return;
  extras.splice(index-1,1);
  if(extras.length)next.candidates=extras;else delete next.candidates;
  member[key]=next;
 }
 if(key==='equip1')for(const legacy of legacyKeys)delete member[legacy];
}
