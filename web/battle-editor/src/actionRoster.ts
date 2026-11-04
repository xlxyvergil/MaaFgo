import type {Entry,Json} from './model';

export interface ActionActor {initialSlot:number;svtId:number}
export interface RosterActor {initialSlot:number;member:Json|null;retired:boolean}
export interface ActorCardBinding {card:unknown;actor:ActionActor}
export interface LifecycleIssue {code:'invalid-actor'|'retired-actor'|'actor-mismatch'|'invalid-retire'|'missing-replacement'|'invalid-swap';message:string}
export const isRetire=(raw:any):boolean=>raw?.type==='retire';
const slot=(value:unknown,max:number):value is number=>typeof value==='number'&&Number.isInteger(value)&&value>=0&&value<max;
const identified=(member:any)=>typeof member?.svtId==='number'&&Number.isSafeInteger(member.svtId)&&member.svtId>0;
export const availableActionActor=(actor:RosterActor|undefined)=>!!actor?.member&&!actor.retired&&(identified(actor.member)||actor.member.supportType==='friend'&&(actor.member.svtId==null||actor.member.svtId===0));
const copy=<T,>(value:T):T=>structuredClone(value);
const owns=(value:any,key:string)=>value!=null&&Object.prototype.hasOwnProperty.call(value,key);
export function initialActionRoster(data:Json):RosterActor[]{
 const front=Array.isArray(data.team?.onFieldSvts)?data.team.onFieldSvts:[],back=Array.isArray(data.team?.backupSvts)?data.team.backupSvts:[];
 return Array.from({length:6},(_,initialSlot)=>({initialSlot,member:(initialSlot<3?front[initialSlot]:back[initialSlot-3])??null,retired:false}));
}
export function actorBinding(actor:RosterActor|undefined):ActionActor|undefined {return identified(actor?.member)?{initialSlot:actor!.initialSlot,svtId:actor!.member!.svtId}:undefined;}
export function validActorBinding(value:any):value is ActionActor {return !!value&&typeof value==='object'&&!Array.isArray(value)&&slot(value.initialSlot,6)&&typeof value.svtId==='number'&&Number.isSafeInteger(value.svtId)&&value.svtId>0;}
export function usesActorGuards(data:Json):boolean {return (Array.isArray(data.actions)?data.actions:[]).some((raw:any)=>isRetire(raw)||owns(raw,'actor')||raw?.type==='attack'&&Array.isArray(raw.attacks)&&raw.attacks.some((card:any)=>owns(card,'actor')));}
export function resolveActionActor(roster:RosterActor[],raw:any,binding?:ActionActor):{actor?:RosterActor;current?:RosterActor;issue?:LifecycleIssue}{
 const current=slot(raw?.svt,3)?roster[raw.svt]:undefined,guard=owns(raw,'actor')?raw.actor:binding;
 if(guard!==undefined&&!validActorBinding(guard))return {current,issue:{code:'invalid-actor',message:'动作的从者身份记录无效，请重新添加此动作。'}};
 const actor=guard?roster.find(item=>item.initialSlot===guard.initialSlot):current;
 if(actor?.retired)return {actor,current,issue:{code:'retired-actor',message:'此动作绑定的从者已退场，不能继续使用其技能或宝具。'}};
 if(guard&&(!actor?.member||guard.svtId!==undefined&&actor.member.svtId!==guard.svtId))return {actor,current,issue:{code:'actor-mismatch',message:'此动作绑定的原编队从者已改变，请重新添加此动作。'}};
 if(guard&&actor!==current)return {actor,current,issue:{code:'actor-mismatch',message:'此动作绑定的从者不在记录的前排位置，请检查退场、换人与动作顺序。'}};
 return {actor,current};
}
export function advanceActionRoster(data:Json,roster:RosterActor[],entry:Entry):LifecycleIssue|undefined {
 const raw=entry.raw;
 if(isRetire(raw)){
  const resolved=resolveActionActor(roster,raw,entry.actorBinding);if(resolved.issue)return resolved.issue;
  if(!slot(raw.svt,3)||!availableActionActor(resolved.actor))return {code:'invalid-retire',message:'退场动作没有有效的当前前排从者。'};
  const outgoing=resolved.actor!,replacementIndex=roster.findIndex((actor,index)=>index>=3&&availableActionActor(actor));
  if(replacementIndex<0)return {code:'missing-replacement',message:'没有可递补的未退场候补成员，请先配置候补从者。'};
  outgoing.retired=true;roster[raw.svt]=roster[replacementIndex];roster.splice(replacementIndex,1);roster.push(outgoing);
 }else if([20,210,440].includes(data.team?.mysticCode?.mysticCodeId)&&raw?.type==='skill'&&raw.svt==null&&raw.skill===2){
  const pair=entry.replacement,front=pair?.[0],back=pair?.[1]<3?pair[1]+3:pair?.[1];
  const retirementPlan=(data.actions??[]).some(isRetire);
  if(entry.paired&&Array.isArray(pair)&&pair.length===2&&slot(front,3)&&slot(back,6)&&back>=3&&!roster[front].retired&&!roster[back].retired&&(!retirementPlan||availableActionActor(roster[front])&&availableActionActor(roster[back])))[roster[front],roster[back]]=[roster[back],roster[front]];
  else return {code:'invalid-swap',message:'换人双方缺失、为空或已退场，请检查换人配置。'};
 }
}
export function actionEntries(data:Json):Entry[]{
 let pair=0;return (Array.isArray(data.actions)?data.actions:[]).map((raw:any,index:number)=>{
  const entry:Entry={raw,index};if([20,210,440].includes(data.team?.mysticCode?.mysticCodeId)&&raw?.type==='skill'&&raw.svt==null&&raw.skill===2){const replacements=data.delegate?.replaceMemberIndexes;if(Array.isArray(replacements)&&pair<replacements.length){entry.replacement=replacements[pair];entry.paired=true;}pair++;}return entry;
 });
}
export function actionRosterAt(data:Json,index:number):RosterActor[]{const roster=initialActionRoster(data);for(const entry of actionEntries(data)){if(entry.index>=index)break;advanceActionRoster(data,roster,entry);}return roster;}
const explicitCard=(card:any)=>card&&typeof card==='object'&&card.isAuto!==true&&slot(card.svt,3);
/** Bind the old action sequence without writing editor state into original JSON. */
export function bindActionActors(data:Json,entries:Entry[]):void {
 const roster=initialActionRoster(data);
 for(const entry of entries){
  if((entry.raw?.type==='skill'&&entry.raw.svt!=null)||isRetire(entry.raw))entry.actorBinding=validActorBinding(entry.raw.actor)?copy(entry.raw.actor):actorBinding(roster[entry.raw.svt]);
  if(entry.raw?.type==='attack')entry.cardActorBindings=(Array.isArray(entry.raw.attacks)?entry.raw.attacks:[]).flatMap((card:any)=>{const actor=validActorBinding(card?.actor)?copy(card.actor):explicitCard(card)?actorBinding(roster[card.svt]):undefined;return actor?[{card:copy(card),actor}]:[];});
  advanceActionRoster(data,roster,entry);
 }
}
/** Persist guards only in retirement plans, preserving old plain Chaldea objects. */
export function materializeActionActors(data:Json,entries:Entry[],force:boolean):void {
 const roster=initialActionRoster(data);
 for(const entry of entries){
  const raw=entry.raw;
  if((raw?.type==='skill'&&raw.svt!=null)||isRetire(raw)){
   entry.actorBinding??=validActorBinding(raw.actor)?copy(raw.actor):actorBinding(roster[raw.svt]);
   if(force&&!owns(raw,'actor')&&entry.actorBinding)raw.actor=copy(entry.actorBinding);
  }
  if(force&&raw?.type==='attack')for(const card of Array.isArray(raw.attacks)?raw.attacks:[])if(explicitCard(card)&&!owns(card,'actor')){
   const binding=entry.cardActorBindings?.find(item=>JSON.stringify(item.card)===JSON.stringify(card))?.actor??actorBinding(roster[card.svt]);if(binding)card.actor=copy(binding);
  }
  advanceActionRoster(data,roster,entry);
 }
}
