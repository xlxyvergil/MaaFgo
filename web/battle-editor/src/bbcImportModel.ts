import {assertEditable,members,memberLabel,servantFor} from './model';
import type {Catalog,Json} from './model';
import {equipCandidatesForDisplay} from './equipRequirements';
import {grandBondEffectLabel} from './grandBondEffect';

export const BBC_FILE_ACCEPT='.json,application/json';
export const MAX_BBC_BYTES=3*1024*1024;
export interface BBCImportResult {data:Json;warnings:string[];validation:Json|null;sourceName:string}

/** A Grand bond effect is a filter, not a named CE or an ordinary image slot. */
export function grandRequirementPreview(member:Json|null) {
 const grand=member?.grandSvt===true,effect=member?.classBoardData?.grandBondEquipSkillChange;
 const bondEffect=grand?`羁绊礼装效果：${typeof effect==='boolean'||effect==null?grandBondEffectLabel(member):'未知，请核对'}`:null;
 return {grand,bondEffect,emptyEquipLabel:grand?'未指定礼装卡面':'不要求礼装'};
}

export async function readBBCFile(file:{size:number;text:()=>Promise<string>}):Promise<Json> {
 if(file.size<=0||file.size>MAX_BBC_BYTES)throw new Error('BBC 配置文件为空或超过3 MiB，请选择单个 BBC JSON 配置。');
 let value:unknown;
 try{value=JSON.parse((await file.text()).replace(/^\uFEFF/,''));}catch{throw new Error('无法读取 BBC JSON，请选择 BBC settings 目录中的单个配置文件。');}
 if(value==null||typeof value!=='object'||Array.isArray(value))throw new Error('BBC 配置顶层必须是 JSON 对象。');
 return value as Json;
}

export function readBBCImportResult(value:any):BBCImportResult {
 if(!value||!value.data||!Array.isArray(value.warnings)||value.warnings.some((warning:unknown)=>typeof warning!=='string')||typeof value.sourceName!=='string')throw new Error('BBC 转换结果不完整，请重新选择配置。');
 assertEditable(value.data);
 if(!value.data.team||!Array.isArray(value.data.actions))throw new Error('BBC 转换结果缺少编队或回合，请重新选择配置。');
 return {data:value.data,warnings:[...value.warnings],validation:value.validation??null,sourceName:value.sourceName};
}

export function bbcPreview(data:Json,catalog:Catalog) {
 const roster=members(data).map((member,index)=>{
  const servant=member?.svtId?servantFor(catalog,member.svtId):undefined;
  const equips=['equip1','equip2','equip3'].flatMap((key,equipIndex)=>{
   if(member?.grandSvt===true&&key==='equip2')return [];
   const candidates=equipCandidatesForDisplay(member,key),label=member?.grandSvt?['普通礼装','羁绊礼装','报酬礼装'][equipIndex]:`礼装 ${equipIndex+1}`;
   return candidates.map((requirement,candidateIndex)=>{
    const item=catalog.equips.find(e=>e.id===requirement.id),baseName=item?.name??(requirement.id?`礼装 #${requirement.id}`:'未指定候选');
    const details=[requirement.limitBreak===true?'满破':requirement.limitBreak===false?'非满破':'不要求满破',...(requirement.lv? [`Lv.${requirement.lv}`]:[])].join(' · ');
    return {id:requirement.id,name:candidates.length>1?`${label} · ${candidateIndex?'或':'候选'} ${candidateIndex+1}/${candidates.length}：${baseName} · ${details}`:baseName,image:item?.image,sourceKey:key,candidateIndex,candidateCount:candidates.length};
   });
  });
  return {slot:index,name:member?memberLabel(member,catalog):'不要求此位置',image:servant?.image,support:['friend','npc','fixed'].includes(member?.supportType),equips,...grandRequirementPreview(member)};
 });
 const actions:Json[]=data.actions??[],mysticId=data.team?.mysticCode?.mysticCodeId;
 return {roster,title:typeof data.team?.name==='string'?data.team.name:'BBC 导入方案',turns:actions.filter(a=>a.type==='attack').length,skills:actions.filter(a=>a.type==='skill').length,cards:actions.filter(a=>a.type==='attack').reduce((count,a)=>count+(Array.isArray(a.attacks)?a.attacks.filter((card:Json)=>card.isAuto!==true).length:0),0),mystic:catalog.mysticCodes.find(m=>m.id===mysticId)?.name??(mysticId?`御主服 #${mysticId}`:'未指定御主服')};
}
