import type {Json} from './model';

export interface CardPreviewConfig {enabled:boolean;regions:string[];defaultRegion:string;endpoint:string;faceEndpoint?:string}
export type NPCardPreviewCapability=CardPreviewConfig;
export interface CardArtworkInput {member:Json|null|undefined;np?:Json;color?:unknown;kind:'np'|'face';region:string;config?:CardPreviewConfig}
export interface CardArtworkRequest {key:string;url:string|null;reason:string|null}
const regions=['JP','CN'];
export function cardPreviewRegion(preference:string|null|undefined,config?:CardPreviewConfig):string {
 const allowed=config?.regions?.filter(region=>regions.includes(region))??regions;
 return preference&&allowed.includes(preference)?preference:allowed.includes(config?.defaultRegion??'JP')?(config?.defaultRegion??'JP'):allowed[0]??'JP';
}
function integer(value:unknown,minimum:number):string|null {
 if(typeof value!=='number'&&typeof value!=='string')return null;
 if(typeof value==='string'&&!/^\d+$/.test(value))return null;
 const number=Number(value);return Number.isSafeInteger(number)&&number>=minimum?String(number):null;
}
export function artworkColor(value:unknown):string|null {
 const normalized=typeof value==='string'?value.toLowerCase():value;
 return normalized===1||normalized==='1'||normalized==='arts'?'arts':normalized===2||normalized==='2'||normalized==='buster'?'buster':normalized===3||normalized==='3'||normalized==='quick'?'quick':null;
}
/** Keep preview identity separate from execution JSON; never invent a card or portrait variant. */
export function cardArtworkRequest({member,np,color,kind,region,config}:CardArtworkInput):CardArtworkRequest {
 const endpoint=kind==='np'?config?.endpoint:config?.faceEndpoint;
 const key=JSON.stringify([kind,member?.svtId,kind==='np'?np?.id:color,region,member?.limitCount,member?.transformVal,config?.enabled,endpoint]);
 const failure=(reason:string):CardArtworkRequest=>({key,url:null,reason});
 if(!member)return failure('请先配置这个位置的从者。');
 if(!config?.enabled)return failure('完整卡图预览尚未启用。');
 if(!regions.includes(region)||!config.regions.includes(region))return failure('这个地区的卡图暂不可用。');
 if(!endpoint||!/^\/api\/[a-z0-9-]+$/i.test(endpoint))return failure('这个卡图预览接口暂不可用。');
 const svtId=integer(member.svtId,1);if(svtId==null)return failure('从者编号无效，无法生成卡图。');
 const query=new URLSearchParams({svtId,region});
 if(kind==='np'){const tdId=integer(np?.id,1);if(tdId==null)return failure('缺少准确的宝具资料，无法生成卡图。');query.set('tdId',tdId);}
 else{const cardColor=artworkColor(color);if(!cardColor)return failure('未知色卡，保留原出卡内容。');query.set('color',cardColor);}
 for(const name of ['limitCount','transformVal'])if(member[name]!=null){const value=integer(member[name],0);if(value==null)return failure(`${name==='limitCount'?'灵基阶段':'变身参数'}无效，无法确定卡面。`);query.set(name,value);}
 return {key,url:`${endpoint}?${query}`,reason:null};
}
