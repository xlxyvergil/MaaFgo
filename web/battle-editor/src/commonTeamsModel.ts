export type CommonTeamSort='upvotes'|'created';
export type GrandTeamClass={id:string;label:string};
export type GrandTeamQuest={id:string;classId:string;label:string;difficulty:string;phase:number;questIds:number[]};
export type CommonTeamCatalog={classes:GrandTeamClass[];quests:GrandTeamQuest[]};
export type CommonTeamEquip={id:number;lv:number|null;limitBreak:boolean|null};
export type CommonTeamMember={svtId:number;support:boolean;equipId:number;grandSvt:boolean;equips:[CommonTeamEquip|null,CommonTeamEquip|null,CommonTeamEquip|null];bondEffect:'np50'|'original'|'any'};
export type CommonTeam={id:number;name:string;author:string;createdAt:number|null;upvotes:number;downvotes:number;turnCount:number|null;servants:(CommonTeamMember|null)[]};
export type CommonTeamPage={questId:string;phase:number;sort:CommonTeamSort;offset:number;pageSize:number;total:number;hasMore:boolean;nextOffset:number|null;teams:CommonTeam[]};
export type CommonTeamRequest={questId:string;sort:CommonTeamSort;offset:number};

const object=(value:unknown):value is Record<string,unknown>=>value!=null&&typeof value==='object'&&!Array.isArray(value);
const integer=(value:unknown,min=0):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=min;
const text=(value:unknown,max=200)=>typeof value==='string'?value.trim().slice(0,max):'';
const key=(value:unknown):value is string=>typeof value==='string'&&/^[a-zA-Z0-9_-]{1,100}$/.test(value);
const responseError=()=>new Error('常用队伍列表返回不完整，无法继续读取。请点击“重试”；若仍失败，请稍后再试。');

export function readCommonTeamCatalog(value:unknown):CommonTeamCatalog {
 if(!object(value)||!Array.isArray(value.classes)||!Array.isArray(value.quests))throw responseError();
 const classes:GrandTeamClass[]=[],classIds=new Set<string>();
 for(const item of value.classes){
  if(!object(item)||!key(item.id)||!text(item.label)||classIds.has(item.id))throw responseError();
  classes.push({id:item.id,label:text(item.label,50)});classIds.add(item.id);
 }
 const quests:GrandTeamQuest[]=[],questIds=new Set<string>();
 for(const item of value.quests){
  if(!object(item)||!key(item.id)||!key(item.classId)||!classIds.has(item.classId)||!text(item.label)||!integer(item.phase,1)||!Array.isArray(item.questIds)||!item.questIds.length||!item.questIds.every(id=>integer(id,1))||questIds.has(item.id))throw responseError();
  quests.push({id:item.id,classId:item.classId,label:text(item.label),difficulty:text(item.difficulty,50),phase:item.phase,questIds:[...new Set(item.questIds as number[])]});questIds.add(item.id);
 }
 if(!classes.length||!quests.length)throw new Error('冠位战副本目录为空，暂时无法查询队伍。请更新完整资源包后重新打开编辑器。');
 return {classes,quests};
}

function readTeam(value:unknown):CommonTeam {
 if(!object(value)||!integer(value.id,1)||!Array.isArray(value.servants)||value.servants.length!==6||!integer(value.upvotes)||!integer(value.downvotes))throw responseError();
 const servants=value.servants.map(item=>{
  if(item===null)return null;
  if(!object(item)||!integer(item.svtId)||!integer(item.equipId)||typeof item.support!=='boolean'||typeof item.grandSvt!=='boolean')throw responseError();
  // Older running services may still send the original primary-CE-only summary.
  const raw=item.equips===undefined?[item.equipId?{id:item.equipId,lv:null,limitBreak:null}:null,null,null]:item.equips;
  if(!Array.isArray(raw)||raw.length!==3)throw responseError();
  const equips=raw.map(equip=>{
   if(equip===null)return null;
   if(!object(equip)||!integer(equip.id)||(equip.lv!==null&&!integer(equip.lv,1))||(equip.limitBreak!==null&&typeof equip.limitBreak!=='boolean'))throw responseError();
   return {id:equip.id,lv:equip.lv as number|null,limitBreak:equip.limitBreak as boolean|null};
  }) as CommonTeamMember['equips'];
  const bondEffect=item.bondEffect??'any';
  if(!['np50','original','any'].includes(bondEffect as string))throw responseError();
  if(!item.grandSvt){equips[1]=null;equips[2]=null;}
  return {svtId:item.svtId,equipId:item.equipId,support:item.support,grandSvt:item.grandSvt,equips,bondEffect:(item.grandSvt?bondEffect:'any') as CommonTeamMember['bondEffect']};
 });
 return {id:value.id,name:text(value.name)||`Chaldea 队伍 #${value.id}`,author:text(value.author,100)||'未署名',createdAt:integer(value.createdAt,1)?value.createdAt:null,upvotes:value.upvotes,downvotes:value.downvotes,turnCount:integer(value.turnCount)?value.turnCount:null,servants};
}

/** The unique bond CE can be pictured without adding a CE requirement to the plan. */
export function commonTeamEquipId(member:CommonTeamMember,index:0|1|2,bondOwners:ReadonlyMap<number,number>,canonicalServantId=member.svtId):number {
 if(index!==0&&!member.grandSvt)return 0;
 const configured=member.equips[index]?.id??0;
 return configured||(index===1&&member.grandSvt?bondOwners.get(canonicalServantId)??0:0);
}

/** Validate pagination identity before replacing or appending a visible list. */
export function readCommonTeamPage(value:unknown,request:CommonTeamRequest):CommonTeamPage {
 if(!object(value)||value.questId!==request.questId||value.sort!==request.sort||value.offset!==request.offset||!integer(value.phase,1)||!integer(value.pageSize,1)||value.pageSize>12||!integer(value.total)||typeof value.hasMore!=='boolean'||!Array.isArray(value.teams)||value.teams.length>value.pageSize)throw responseError();
 const nextOffset=value.nextOffset;
 if(value.hasMore?(!integer(nextOffset,request.offset+1)||nextOffset!==request.offset+value.pageSize||nextOffset>=value.total):nextOffset!==null)throw responseError();
 return {questId:request.questId,phase:value.phase,sort:request.sort,offset:request.offset,pageSize:value.pageSize,total:value.total,hasMore:value.hasMore,nextOffset:nextOffset as number|null,teams:value.teams.map(readTeam)};
}

/** Keep server ranking and suppress repeated rows across live-changing pages. */
export function appendCommonTeams(current:CommonTeam[],incoming:CommonTeam[]):CommonTeam[] {
 const result:CommonTeam[]=[],seen=new Set<number>();
 for(const team of [...current,...incoming])if(!seen.has(team.id)){seen.add(team.id);result.push(team);}
 return result;
}

export function commonTeamQuery(request:CommonTeamRequest):string {
 return `/common-teams?${new URLSearchParams({quest:request.questId,sort:request.sort,offset:String(request.offset)})}`;
}

export function commonTeamDate(value:number|null):string {
 if(value===null)return '日期未记录';
 const date=new Date(value<1e12?value*1000:value);
 return Number.isNaN(date.getTime())?'日期未记录':date.toLocaleDateString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit'});
}

export const GRAND_CLASS_ICONS:Record<string,string>={saber:'saber',archer:'archer',lancer:'lancer',rider:'rider',caster:'caster',assassin:'assassin',berserker:'berserker',ex1:'ruler',ex2:'alterEgo',extra:'ruler'};
