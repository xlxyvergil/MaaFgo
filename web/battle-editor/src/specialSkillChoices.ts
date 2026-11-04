import type {Catalog,Entry,Json,Servant} from './model';
import {advanceActionRoster,initialActionRoster,isRetire,resolveActionActor} from './actionRoster';

export type SpecialSkillChoiceField='tdTypeChanges'|'skillActSelectSelections';
export interface SpecialSkillChoiceOption {value:number;label:string;target?:'ally'|'none';targets?:string[]}
export interface SpecialSkillChoiceGroup {id:string;servantIds:(string|number)[];skillNumbers:number[];field:SpecialSkillChoiceField;options:SpecialSkillChoiceOption[];defaultValue:number;skillIds?:number[]}
export interface SpecialSkillChoiceCatalog {schemaVersion:1;groups:SpecialSkillChoiceGroup[]}
/** Editor-only binding. Only raw actions and delegate queues are serialized. */
export interface SpecialSkillChoiceBinding {groupId:string;field:SpecialSkillChoiceField;value:unknown;source:'queue'|'default'|'explicit';sourceQueueIndex?:number;sourceActionIndex?:number;missing?:boolean}
export interface SpecialSkillChoiceDiagnostic {code:string;severity:'warning'|'error';message:string;field?:SpecialSkillChoiceField;actionIndex?:number}
const fields:SpecialSkillChoiceField[]=['tdTypeChanges','skillActSelectSelections'];
const copy=<T,>(value:T):T=>structuredClone(value);
const positive=(value:unknown):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>0;
const active=(value:unknown)=>value!=null&&(!Array.isArray(value)||value.length>0);
const groupsOf=(catalog?:Catalog)=>catalog?.specialSkillChoices?.schemaVersion===1&&Array.isArray(catalog.specialSkillChoices.groups)?catalog.specialSkillChoices.groups:[];
const rosterOf=(data:Json):any[]=>[...(data.team?.onFieldSvts??[]).slice(0,3),...Array(3).fill(null)].slice(0,3).concat([...(data.team?.backupSvts??[]).slice(0,3),...Array(3).fill(null)].slice(0,3));
const swapAction=(data:Json,raw:any)=>[20,210,440].includes(data.team?.mysticCode?.mysticCodeId)&&raw?.type==='skill'&&raw.svt==null&&raw.skill===2;
const servantOf=(member:any,catalog?:Catalog)=>catalog?.servants.find(s=>Number(s.id)===Number(member?.svtId)||s.aliases?.some(id=>Number(id)===Number(member?.svtId)));

function eligibility(member:Json|null|undefined,servant:Servant|undefined,index:number,catalog?:Catalog):{group:SpecialSkillChoiceGroup;supported:boolean}|undefined {
 if(!positive(member?.svtId)||!Number.isInteger(index)||index<0||index>2)return;
 const identities=[member.svtId,servant?.id,...(servant?.aliases??[])].filter(id=>id!=null).map(Number);
 const group=groupsOf(catalog).find(g=>g.skillNumbers.includes(index+1)&&g.servantIds.some(id=>identities.includes(Number(id))));
 if(!group)return;
 if(!group.skillIds?.length)return {group,supported:true};
 const selected=member.skillIds?.[index];
 const defaultSkill=servant?.skills?.find(s=>(s as Json).num===index+1);
 const skillId=selected==null||selected===0?defaultSkill?.id:selected;
 return {group,supported:positive(skillId)&&group.skillIds.includes(skillId)};
}
export function specialSkillChoiceFor(member:Json|null|undefined,servant:Servant|undefined,skillIndex:number,catalog?:Catalog):SpecialSkillChoiceGroup|undefined {
 const match=eligibility(member,servant,skillIndex,catalog);return match?.supported?match.group:undefined;
}
export const entrySpecialSkillChoice=(entry:Entry):SpecialSkillChoiceBinding|undefined=>entry.specialChoice;
export function setEntrySpecialSkillChoice(entry:Entry,group:SpecialSkillChoiceGroup,value:number):void {
 if(!group.options.some(option=>option.value===value))throw new Error('这个特殊技能选项不可用，请重新选择。');
 const previous=entry.specialChoice;
 entry.specialChoice={groupId:group.id,field:group.field,value,source:'explicit',...(previous?.sourceActionIndex!=null?{sourceActionIndex:previous.sourceActionIndex}:{}),...(previous?.sourceQueueIndex!=null?{sourceQueueIndex:previous.sourceQueueIndex}:{})};
}
const optionValue=(value:unknown,field:SpecialSkillChoiceField):unknown=>field==='tdTypeChanges'&&typeof value==='string'?({arts:1,buster:2,quick:3} as Record<string,number>)[value]??value:value;
export function specialSkillChoiceLabel(entry:Entry,catalog?:Catalog):string {
 const binding=entry.specialChoice;if(!binding)return '';
 if(binding.missing)return '缺少技能选项';
 const group=groupsOf(catalog).find(g=>g.id===binding.groupId&&g.field===binding.field);
 const label=group?.options.find(option=>option.value===optionValue(binding.value,binding.field))?.label;
 return label?`${label}${binding.source==='default'?'（默认）':''}`:`未支持的选项 ${String(binding.value)}`;
}

interface Event {entry:Entry;group:SpecialSkillChoiceGroup;actionIndex:number}
interface Projection {events:Record<SpecialSkillChoiceField,Event[]>;uncertain:Set<SpecialSkillChoiceField>;diagnostics:SpecialSkillChoiceDiagnostic[]}
function project(data:Json,entries:Entry[],catalog?:Catalog,bind=false):Projection {
 const result:Projection={events:{tdTypeChanges:[],skillActSelectSelections:[]},uncertain:new Set(),diagnostics:[]};
 const groups=groupsOf(catalog),roster=initialActionRoster(data),retirementPlan=entries.some(entry=>isRetire(entry.raw))||(data.actions??[]).some(isRetire);
 const enabled=fields.filter(field=>active(data.delegate?.[field])||entries.some(e=>e.specialChoice?.field===field&&e.specialChoice.source==='explicit'));
 if(!groups.length){
  for(const field of enabled)result.uncertain.add(field);
  if(enabled.length)result.diagnostics.push({code:'special_metadata_unavailable',severity:'warning',message:'特殊技能资料尚未加载，已保留选项队列，暂不重新配对。'});
  return result;
 }
 for(const [actionIndex,entry] of entries.entries()){
  const raw=entry.raw;
  if(raw?.type==='skill'&&raw.svt!=null){
   const member=resolveActionActor(roster,raw,retirementPlan?entry.actorBinding:undefined).actor?.member,servant=servantOf(member,catalog);
   if(!Number.isInteger(raw.svt)||raw.svt<0||raw.svt>2||!positive(member?.svtId)){
    for(const field of enabled)result.uncertain.add(field);
    if(enabled.length)result.diagnostics.push({code:'special_actor_unresolved',severity:'error',message:'无法确定技能执行者，不能安全配对后续特殊技能选项。',actionIndex});
   }else{
    const match=eligibility(member,servant,raw.skill,catalog);
    if(match&&!match.supported){
     result.uncertain.add(match.group.field);
     result.diagnostics.push({code:'special_choice_variant_unresolved',severity:enabled.includes(match.group.field)?'error':'warning',message:'当前技能版本未确认具有特殊选项，请核对强化前后的技能。',field:match.group.field,actionIndex});
    }else if(match){
     const {group}=match,queue=data.delegate?.[group.field],ordinal=result.events[group.field].length;
     if(bind&&!result.uncertain.has(group.field))entry.specialChoice={groupId:group.id,field:group.field,value:Array.isArray(queue)&&ordinal<queue.length?copy(queue[ordinal]):group.defaultValue,source:Array.isArray(queue)&&ordinal<queue.length?'queue':'default',sourceQueueIndex:ordinal,sourceActionIndex:actionIndex,...(Array.isArray(queue)&&queue.length>0&&ordinal>=queue.length?{missing:true}:{})};
     result.events[group.field].push({entry,group,actionIndex});
    }
   }
  }
  const lifecycleIssue=advanceActionRoster(data,roster,entry);
  if(lifecycleIssue&&lifecycleIssue.code!=='retired-actor'&&lifecycleIssue.code!=='actor-mismatch'){
    for(const field of enabled)result.uncertain.add(field);
    if(enabled.length)result.diagnostics.push({code:'special_roster_unresolved',severity:'error',message:lifecycleIssue.message,actionIndex});
  }
 }
 return result;
}
function plainEntries(data:Json):Entry[]{
 let pair=0;return (Array.isArray(data.actions)?data.actions:[]).map((raw:any,index:number)=>{
  const entry:Entry={raw,index};if(swapAction(data,raw)){const replacements=data.delegate?.replaceMemberIndexes;if(Array.isArray(replacements)&&pair<replacements.length){entry.replacement=replacements[pair];entry.paired=true;}pair++;}return entry;
 });
}
export function bindSpecialSkillChoices(data:Json,entries:Entry[],catalog?:Catalog):void {project(data,entries,catalog,true);}
export function specialSkillChoiceDiagnostics(data:Json,catalog?:Catalog):SpecialSkillChoiceDiagnostic[]{
 const projection=project(data,plainEntries(data),catalog),diagnostics=[...projection.diagnostics];
 if(!groupsOf(catalog).length)return diagnostics;
 for(const field of fields){
  const queue=data.delegate?.[field],events=projection.events[field];
  if(queue!=null&&!Array.isArray(queue)){diagnostics.push({code:'special_queue_invalid',severity:'error',message:`${field} 必须是数组；原值已保留。`,field});continue;}
  if(!active(queue)||projection.uncertain.has(field))continue;
  if(queue.length<events.length)diagnostics.push({code:'special_queue_exhausted',severity:'error',message:'特殊技能选项队列不足，已有内容已保留，请逐项设置缺少的选项。',field});
  if(queue.length>events.length)diagnostics.push({code:'special_queue_unused',severity:'warning',message:'特殊技能队列存在尚未消费的尾部选项，已原样保留。',field});
  for(const [index,event] of events.entries())if(index<queue.length&&!event.group.options.some(option=>option.value===optionValue(queue[index],field)))diagnostics.push({code:'special_option_unsupported',severity:'error',message:'特殊技能队列包含当前技能不支持的选项，原值已保留。',field,actionIndex:event.actionIndex});
 }
 return diagnostics;
}
function signature(data:Json,entries:Entry[]):string {
 return JSON.stringify({roster:rosterOf(data).map(member=>member&&({svtId:member.svtId,skillIds:member.skillIds,transformVal:member.transformVal,limitCount:member.limitCount})),mystic:data.team?.mysticCode?.mysticCodeId,skills:entries.filter(entry=>entry.raw?.type==='skill'||entry.raw?.type==='retire').map(entry=>({raw:entry.raw,replacement:entry.replacement,paired:entry.paired}))});
}
export function rebuildSpecialSkillChoices(before:Json,oldEntries:Entry[],nextEntries:Entry[],catalog?:Catalog,after:Json=before):{updates:Partial<Record<SpecialSkillChoiceField,unknown[]>>;diagnostics:SpecialSkillChoiceDiagnostic[]} {
 const oldProjection=project(before,oldEntries,catalog),nextProjection=project(after,nextEntries,catalog),updates:Partial<Record<SpecialSkillChoiceField,unknown[]>>={},diagnostics=[...nextProjection.diagnostics];
 if(!groupsOf(catalog).length){
  if(fields.some(field=>active(before.delegate?.[field]))&&signature(before,oldEntries)!==signature(after,nextEntries))throw new Error('特殊技能资料尚未加载，暂时不能安全排序技能或更改相关阵容，请稍后重试。');
  return {updates,diagnostics};
 }
 for(const field of fields){
  const oldQueue=before.delegate?.[field],oldEvents=oldProjection.events[field],events=nextProjection.events[field];
  const explicit=events.some(event=>event.entry.specialChoice?.field===field&&event.entry.specialChoice.source==='explicit');
  if(!active(oldQueue)&&!explicit)continue;
  const changed=signature(before,oldEntries)!==signature(after,nextEntries);
  if((oldQueue!=null&&!Array.isArray(oldQueue))||oldProjection.uncertain.has(field)||nextProjection.uncertain.has(field)){
   if(changed||explicit)throw new Error('特殊技能队列尚无法可靠配对，请先核对从者、技能版本及换人配置，再修改相关技能步骤。');
   continue;
  }
  const queue:Array<unknown>=Array.isArray(oldQueue)?oldQueue:[],tail=queue.slice(oldEvents.length),origins=new Map<number,number>();
  for(const event of oldEvents){const origin=event.entry.specialChoice?.sourceActionIndex;if(origin!=null)origins.set(origin,(origins.get(origin)??0)+1);}
  let added=false;
  for(const event of events){
   const binding=event.entry.specialChoice;
   if(binding&&(binding.groupId!==event.group.id||binding.field!==field))throw new Error('技能执行者或技能选项类型已改变，请先移除这个技能步骤，再重新添加并选择效果。');
   const origin=binding?.sourceActionIndex,count=origin==null?0:origins.get(origin)??0;
   if(origin==null||count===0)added=true;else origins.set(origin,count-1);
  }
  if(added&&!explicit&&queue.length<oldEvents.length)throw new Error('已有特殊技能选项队列不完整，请先为缺少的技能明确选择效果，再新增或复制技能步骤。');
  const materialize=explicit||(active(oldQueue)&&added),values:unknown[]=[];let hole=false;
  for(const event of events){
   const binding=event.entry.specialChoice;
   if(materialize)values.push(binding?copy(binding.value):event.group.defaultValue);
   else if(binding?.source==='queue'){
    if(hole)throw new Error('已有特殊技能选项队列不完整，移动后会产生选项错位。请先为缺少的技能明确选择效果。');
    values.push(copy(binding.value));
   }else hole=true;
  }
  if(materialize&&events.some(event=>!event.entry.specialChoice||event.entry.specialChoice.source==='default'))diagnostics.push({code:'special_defaults_materialized',severity:'warning',message:'本次设置已按技能默认效果补齐同类队列的其他步骤，避免选项错位。',field});
  updates[field]=[...values,...copy(tail)];
 }
 return {updates,diagnostics};
}
const rebuildState=(data:Json)=>JSON.stringify([signature(data,plainEntries(data)),...fields.map(field=>data.delegate?.[field])]);
const rebuiltObjects=new WeakMap<object,{diagnostics:SpecialSkillChoiceDiagnostic[];state:string}>();
export function markSpecialSkillChoicesRebuilt(data:Json,diagnostics:SpecialSkillChoiceDiagnostic[]=[]):void {rebuiltObjects.set(data,{diagnostics,state:rebuildState(data)});}
/** Covers team/variant changes that do not go through writeTurns. */
export function reconcileSpecialSkillChoices(before:Json,after:Json,catalog?:Catalog):SpecialSkillChoiceDiagnostic[]{
 const previousRebuild=rebuiltObjects.get(after);rebuiltObjects.delete(after);
 if(previousRebuild?.state===rebuildState(after))return [...previousRebuild.diagnostics,...specialSkillChoiceDiagnostics(after,catalog)];
 if(fields.some(field=>JSON.stringify(before.delegate?.[field])!==JSON.stringify(after.delegate?.[field])))return specialSkillChoiceDiagnostics(after,catalog);
 const oldEntries=plainEntries(before),nextEntries=plainEntries(after);bindSpecialSkillChoices(before,oldEntries,catalog);
 if(oldEntries.length!==nextEntries.length){
  if(fields.some(field=>active(before.delegate?.[field]))&&groupsOf(catalog).length)throw new Error('特殊技能动作数量发生变化，请使用技能或回合编辑功能以保留选项配对。');
  return specialSkillChoiceDiagnostics(after,catalog);
 }
 for(const [index,entry] of nextEntries.entries())if(oldEntries[index].specialChoice)entry.specialChoice=copy(oldEntries[index].specialChoice);
 const rebuilt=rebuildSpecialSkillChoices(before,oldEntries,nextEntries,catalog,after);
 if(Object.keys(rebuilt.updates).length){after.delegate??={};Object.assign(after.delegate,rebuilt.updates);}
 return [...rebuilt.diagnostics,...specialSkillChoiceDiagnostics(after,catalog)];
}
