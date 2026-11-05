import {assertEditable,members,memberLabel,servantFor,turnsOf} from './model';
import type {Catalog,Json} from './model';
import {equipCandidatesForDisplay} from './equipRequirements';

export const IMAGE_ACCEPT='image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp';
export const MAX_IMAGE_BYTES=16*1024*1024;
export interface ScreenshotSlot {slot:number;servantId:number|null;status:'empty'|'identified'|'unknown';support:boolean;equipId?:number|null;equipStatus?:'empty'|'identified'|'unknown';score?:number;equipScore?:number}
export interface ScreenshotResult {kind?:'screenshot';slots:ScreenshotSlot[];warnings:string[];elapsedMs?:number;sourceSize?:number[]}
export interface ShareImageResult {kind:'share';schema:1;data:Json;warnings:string[];validation:Json|null;title?:string;elapsedMs?:number}
export interface ChaldeaImageResult {kind:'chaldea';schema:1;teamId:number;source:string;data:Json;warnings:string[];validation:Json|null;title?:string;elapsedMs?:number}
export type RestoredImageResult=ShareImageResult|ChaldeaImageResult;
export type ImageImportResult=ScreenshotResult|RestoredImageResult;
export function imageMime(file:{type:string;name?:string;size:number}):string {
 if(file.size<=0||file.size>MAX_IMAGE_BYTES)throw new Error('图片为空或超过16 MiB，请换一张较小的截图。');
 const mime=file.type.toLowerCase();
 if(['image/png','image/jpeg','image/webp'].includes(mime))return mime;
 if(!mime){const ext=file.name?.split('.').pop()?.toLowerCase();if(ext==='png')return 'image/png';if(ext==='jpg'||ext==='jpeg')return 'image/jpeg';if(ext==='webp')return 'image/webp';}
 throw new Error('支持 PNG、JPG/JPEG、WebP 静态图片。');
}
export function clipboardImage(items:ArrayLike<{kind:string;type:string;getAsFile:()=>File|null}>):File|null {
 const images=Array.from(items).filter(item=>item.kind==='file'&&item.type.startsWith('image/'));
 if(images.length>1)throw new Error('请每次粘贴一张编队截图或一图流。');
 return images[0]?.getAsFile()??null;
}
export function readScreenshotResult(value:any):ScreenshotResult {
 const id=(v:any)=>v==null||(typeof v==='number'&&Number.isSafeInteger(v)&&v>0);
 if(!value||!Array.isArray(value.slots)||value.slots.length!==6||value.slots.some((s:any,i:number)=>!s||s.slot!==i||!id(s.servantId)||!id(s.equipId)||!['empty','identified','unknown'].includes(s.status)||typeof s.support!=='boolean'))throw new Error('识别结果不完整，请重新识别。');
 return {...value,slots:value.slots.map((s:ScreenshotSlot)=>({...s})),warnings:Array.isArray(value.warnings)?value.warnings.filter((s:any)=>typeof s==='string'):[]};
}
/** Full plans from share markers or Chaldea IDs must never pass through screenshotTeam. */
export function readImageImportResult(value:any):ImageImportResult {
 if(value?.kind==='share'||value?.kind==='chaldea') {
  const label=value.kind==='chaldea'?'Chaldea 图片':'一图流';
  if(value.schema!==1||!Array.isArray(value.warnings)||value.warnings.some((warning:unknown)=>typeof warning!=='string'))throw new Error(`${label}识别结果不完整或版本不受支持，请使用原始分享图片。`);
  if(value.kind==='chaldea'&&(!Number.isSafeInteger(value.teamId)||value.teamId<=0||value.source!==`https://link.chaldea.center/laplace/share?id=${value.teamId}`))throw new Error('Chaldea 图片的 Team ID 或分享链接无效，请重新识别。');
  assertEditable(value.data);
  if(!value.data.team||typeof value.data.team!=='object'||Array.isArray(value.data.team))throw new Error(`${label}中缺少完整编队数据，请重新选择图片。`);
  const validation=value.validation??null;
  if(validation!==null&&(typeof validation!=='object'||Array.isArray(validation)||typeof validation.valid!=='boolean'||!Array.isArray(validation.issues)||validation.issues.some((issue:unknown)=>!issue||typeof issue!=='object'||typeof (issue as Json).message!=='string')))throw new Error(`${label}方案检查结果无效，请重新识别。`);
  const common={schema:1 as const,data:value.data,warnings:[...value.warnings],validation,...(typeof value.title==='string'?{title:value.title}:{}),...(typeof value.elapsedMs==='number'&&Number.isFinite(value.elapsedMs)?{elapsedMs:value.elapsedMs}:{})};
  return value.kind==='chaldea'?{...common,kind:'chaldea',teamId:value.teamId,source:value.source}:{...common,kind:'share'};
 }
 if(value?.kind!=null&&value.kind!=='screenshot')throw new Error('暂不支持这种图片识别结果，请更新编辑器或重新选择图片。');
 return readScreenshotResult(value);
}
export function shareImagePreview(data:Json,catalog:Catalog) {
 const roster=members(data).map((member,slot)=>{
  const servant=member?.svtId?servantFor(catalog,member.svtId,member):undefined;
  const supportType=member?.supportType;
  const supportLabel=supportType==='friend'?'好友助战':supportType==='npc'?'NPC 助战':supportType==='fixed'?'固定助战':'';
  const equips=['equip1','equip2','equip3'].flatMap((key,equipIndex)=>{
   if(member?.grandSvt===true&&key==='equip2')return [];
   const candidates=equipCandidatesForDisplay(member,key),label=member?.grandSvt?['普通礼装','羁绊礼装','报酬礼装'][equipIndex]:`礼装 ${equipIndex+1}`;
   return candidates.map((requirement,candidateIndex)=>{
    const equip=catalog.equips.find(item=>item.id===requirement.id),baseName=equip?.name??(requirement.id?`礼装 #${requirement.id}`:'未指定候选');
    const details=[requirement.limitBreak===true?'满破':requirement.limitBreak===false?'非满破':'不要求满破',...(requirement.lv?[`Lv.${requirement.lv}`]:[])].join(' · ');
    return {id:requirement.id,name:candidates.length>1?`${label} · ${candidateIndex?'或':'候选'} ${candidateIndex+1}/${candidates.length}：${baseName} · ${details}`:baseName,image:equip?.image,sourceKey:key,candidateIndex,candidateCount:candidates.length};
   });
  });
  return {slot,name:member?memberLabel(member,catalog):'不要求此位置',image:servant?.image,support:!!supportLabel,supportLabel,equips};
 });
 const actions:Json[]=Array.isArray(data.actions)?data.actions:[],mysticId=data.team?.mysticCode?.mysticCodeId;
 return {roster,title:typeof data.team?.name==='string'&&data.team.name?data.team.name:'一图流导入方案',turns:turnsOf(data).length,skills:actions.filter(action=>action?.type==='skill').length,cards:actions.reduce((count,action)=>count+(action?.type==='attack'&&Array.isArray(action.attacks)?action.attacks.filter((card:Json)=>card?.isAuto!==true).length:0),0),mystic:catalog.mysticCodes.find(item=>item.id===mysticId)?.name??(mysticId?`御主服 #${mysticId}`:'未指定御主服')};
}
export function screenshotTeam(slots:ScreenshotSlot[],catalog:Catalog):Json {
 const result=readScreenshotResult({slots});
 if(result.slots.filter(s=>s.support).length>1)throw new Error('请选择至多一个助战位置。');
 let count=0;
 const roster=result.slots.map(s=>{
  if(s.servantId!=null&&!catalog.servants.some(m=>m.id===s.servantId))throw new Error(`槽位${s.slot+1}的从者不在当前图鉴中，请重新选择。`);
  if(s.equipId!=null&&!catalog.equips.some(e=>e.id===s.equipId&&e.recognizable===true))throw new Error(`槽位${s.slot+1}的礼装没有工程识别资源，请重新选择。`);
  if(!s.servantId&&!s.support){if(s.equipId)throw new Error(`槽位${s.slot+1}只选择了礼装，请补选从者或将它设为助战。`);return null;}
  const member:Json={supportType:s.support?'friend':'none'};
  if(s.servantId)member.svtId=s.servantId;
  if(s.equipId)member.equip1={id:s.equipId};
  count++;return member;
 });
 if(!count)throw new Error('请至少选择一位从者或一个助战要求。');
 return {team:{name:'截图导入编队',onFieldSvts:roster.slice(0,3),backupSvts:roster.slice(3)},actions:[]};
}
