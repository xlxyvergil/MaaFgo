import {isAutoAttackCard,setAttackCard} from './attackCards';
import {defaultAdvancedStrategy} from './advancedStrategy';
import {setEquipCandidate} from './equipRequirements';
import {bindSpecialSkillChoices,rebuildSpecialSkillChoices,markSpecialSkillChoicesRebuilt} from './specialSkillChoices';
import type {SpecialSkillChoiceBinding,SpecialSkillChoiceCatalog} from './specialSkillChoices';
import {matchEquipAdvanced} from './equipFilters';
import type {EquipAdvancedFilters,EquipFilterCatalog,EquipFilterMetadata} from './equipFilters';
import {actionRosterAt,actorBinding,availableActionActor,bindActionActors,isRetire,materializeActionActors,resolveActionActor,usesActorGuards,validActorBinding} from './actionRoster';
import type {ActionActor,ActorCardBinding} from './actionRoster';
export {isRetire} from './actionRoster';

export type Json = Record<string, any>;
export interface ServantBond {tags?:string[]|null;tags_by_ascension?:Record<string,string[]>|null;tags_by_costume?:string[]|Record<string,string[]>|null;alignment?:string|null;attribute?:string|null;gender?:string|null;sub_attribute?:string|null;[key:string]:unknown}
export interface NPFilterVariant {treasureDeviceId?:number;name_jp?:string;type?:string;card?:string;conditions_jp?:string[];[key:string]:unknown}
export interface ServantNPFilter {types?:string[];cards?:string[];variants?:NPFilterVariant[]}
export interface Servant { id:number; name:string; nameJp?:string; collectionNo?:number; classKey?:string; className?:string; rarity?:number;cost?:number|null; image?:string|null; portrait?:string|null; statusImage?:string|null; classIcon?:string|null; cards?:number[]; aliases?:(string|number)[]; searchAliases?:string[]; skills?:{id:number;num?:number;name:string;icon?:string;target?:string;targets?:string[];coolDown?:number[]}[]; skillVariants?:Json[]; appendSkills?:Json[]; np?:Json; npVariants?:Json[];bond?:ServantBond|null;npFilter?:ServantNPFilter|null }
export interface Equip {id:number;name:string;nameJp?:string;collectionNo?:number;rarity?:number;cost?:number|null;image?:string|null;imageWidth?:number;imageHeight?:number;recognizable?:boolean;supportRecognition?:{limitBreak:boolean;nonLimitBreak:boolean};common?:boolean;commonSources?:string[];filters?:EquipFilterMetadata}
export interface Mystic {id:number;name:string;image?:string|null;skills?:Json[];orderChange?:boolean}
export interface Catalog {servants:Servant[];equips:Equip[];mysticCodes:Mystic[];uiAssets?:{classes?:Record<string,string>;support?:string|null;background?:string|null;npFrames?:{arts?:string;buster?:string;quick?:string};masterSkill?:string|null;masterFrame?:string|null;masterEquipFrame?:string|null};inventory?:{servants?:number[];equips?:number[];[key:string]:any};version?:string|Json;specialSkillChoices?:SpecialSkillChoiceCatalog;equipFilters?:EquipFilterCatalog}
export interface Entry {raw: any; replacement?:any; paired?:boolean; index:number;specialChoice?:SpecialSkillChoiceBinding;actorBinding?:ActionActor;cardActorBindings?:ActorCardBinding[]}
export interface Turn {entries:Entry[]}
export const clone = <T,>(v:T):T => structuredClone(v);
export const newBattle = ():Json => ({team:{name:'未命名方案',onFieldSvts:[null,null,null],backupSvts:[null,null,null],mysticCode:{mysticCodeId:210,level:10}},actions:[],maaAdvancedStrategy:defaultAdvancedStrategy()});
export const newServantDefaults=():Json=>({supportType:'none',skillLvs:[10,10,10],appendLvs:[0,0,0,0,0]});
export function hasServantIdentity(member:Json|null|undefined):member is Json&{svtId:number} {
 return typeof member?.svtId==='number'&&Number.isSafeInteger(member.svtId)&&member.svtId>0;
}
export function isUnspecifiedSupport(member:Json|null|undefined):boolean {
 return member?.supportType==='friend'&&(member.svtId==null||member.svtId===0);
}
export function memberLabel(member:Json|null|undefined,catalog:Catalog):string {
 if(isUnspecifiedSupport(member))return '助战（仅要求礼装）';
 if(hasServantIdentity(member))return servantFor(catalog,member!.svtId)?.name??`从者 #${member!.svtId}`;
 return '未配置从者';
}
/** Explicitly remove identity requirements, retaining CE and unrelated imported fields. */
export function clearSupportServantRequirement(member:Json):void {
 if(member?.supportType!=='friend')throw new Error('仅好友助战支持不要求从者。');
 for(const key of ['svtId','limitCount','transformVal','skillLvs','skillIds','appendLvs','tdId','tdLv','lv','atkFou','hpFou','fixedAtk','fixedHp','cardStrengthens','commandCodeIds','disabledExtraSkills','allowedExtraSkills','customPassives','customPassiveLvs'])delete member[key];
}
export function setMemberEquipRequirement(data:Json,slot:number,key:string,id:number,newTeamDefault=false):void {
 if(!Number.isInteger(slot)||slot<0||slot>=6||!['equip1','equip2','equip3'].includes(key)||!Number.isSafeInteger(id)||id<=0)throw new Error('礼装或槽位无效，请重新选择。');
 let member=members(data)[slot];
 if(!member){
  if(newTeamSupportSlot(data,newTeamDefault)!==slot)throw new Error('请先选择从者，或将这个位置设为仅要求礼装的助战。');
  member={supportType:'friend'};
 }
 setEquipCandidate(member,key,0,id);setMember(data,slot,member);
}
export function assertEditable(value:any):asserts value is Json {
 const object=(v:any)=>v!=null&&typeof v==='object'&&!Array.isArray(v);
 if(!object(value))throw new Error('顶层必须是 JSON 对象。');
 if(value.team!=null&&!object(value.team))throw new Error('team 必须是对象或 null；请先在原文件修正。');
 for(const key of ['onFieldSvts','backupSvts']){const list=value.team?.[key];if(list!=null&&!Array.isArray(list))throw new Error(`team.${key} 必须是数组。`);for(const m of list??[]){if(m!=null&&!object(m))throw new Error(`team.${key} 包含非对象槽位，请先修正。`);for(const ce of ['equip1','equip2','equip3','classBoardData'])if(m?.[ce]!=null&&!object(m[ce]))throw new Error(`${ce} 必须是对象或 null。`);for(const a of ['skillLvs','appendLvs'])if(m?.[a]!=null&&!Array.isArray(m[a]))throw new Error(`${a} 必须是数组。`);}}
 if(value.team?.mysticCode!=null&&!object(value.team.mysticCode))throw new Error('mysticCode 必须是对象或 null。');
 if(value.actions!=null&&!Array.isArray(value.actions))throw new Error('actions 必须是数组。');
 if(value.delegate!=null&&!object(value.delegate))throw new Error('delegate 必须是对象或 null。');
 for(const a of value.actions??[]){if(a?.type==='attack'&&a.attacks!=null){if(!Array.isArray(a.attacks))throw new Error('attack.attacks 必须是数组。');if(a.attacks.some((c:any)=>!object(c)))throw new Error('attack.attacks 包含无效出卡项，请先修正。');}if(['skill','attack'].includes(a?.type)&&a.options!=null&&!object(a.options))throw new Error('动作 options 必须是对象或 null。');}
}
export const members = (data:Json):any[] => [...Array.from({length:3},(_,i)=>data.team?.onFieldSvts?.[i]??null),...Array.from({length:3},(_,i)=>data.team?.backupSvts?.[i]??null)];
/** A new editor session may suggest support without recording a member in an empty slot. */
export function newTeamSupportSlot(data:Json,enabled:boolean):0|null {
 const roster=members(data);
 return enabled===true&&roster[0]===null&&!roster.some(m=>m&&['friend','npc','fixed'].includes(m.supportType))?0:null;
}
export function restoreNewTeamFirstSupport(value:unknown,sourceName?:unknown):boolean {
 return value===true&&(sourceName==null||sourceName==='');
}
export function newTeamMemberDefaults(data:Json,slot:number,enabled:boolean,defaults:Json=newServantDefaults()):Json {
 const next=clone(defaults);
 if(slot===newTeamSupportSlot(data,enabled))next.supportType='friend';
 return next;
}
export function setMember(data:Json,slot:number,value:any) {data.team??={}; const key=slot<3?'onFieldSvts':'backupSvts';data.team[key]??=[null,null,null];data.team[key][slot%3]=value;}
// A drag exchanges the complete recorded member. Battle actions retain position semantics.
export function swapMembers(data:Json,first:number,second:number) {
 if(![first,second].every(n=>Number.isInteger(n)&&n>=0&&n<6))throw new Error('编队槽位必须在 1～6 之间。');
 if(first===second)return;
 const roster=members(data);setMember(data,first,roster[second]);setMember(data,second,roster[first]);
}
export interface ServantAdvancedFilters {alignment?:string;attribute?:string;gender?:string;subAttribute?:string;npType?:string;npCard?:string;tags?:string[];includeConditional?:boolean}
export const newServantAdvancedFilters=():ServantAdvancedFilters=>({alignment:'',attribute:'',gender:'',subAttribute:'',npType:'',npCard:'',tags:[],includeConditional:false});
export function advancedFilterCount(filters:ServantAdvancedFilters):number {return ['alignment','attribute','gender','subAttribute','npType','npCard'].filter(key=>!!filters[key as keyof ServantAdvancedFilters]).length+new Set(filters.tags??[]).size;}
export interface ServantAdvancedMatch {matches:boolean;conditionalOnly:boolean;matchedForms:string[]}
const stringList=(value:unknown):string[]=>Array.isArray(value)?value.filter((item):item is string=>typeof item==='string'):[];
const alignmentValues=new Set(['lawful','neutral','chaotic']);
const attributeValues=new Set(['good','balanced','evil','summer','madness']);
const genderValues=new Set(['male','female','unknown']);
const subAttributeValues=new Set(['sky','earth','human','star','beast']);
interface BondProjection {tags:Set<string>;alignment:Set<string>;attribute:Set<string>;gender:Set<string>;subAttribute:Set<string>}
function bondTagProjection(tags:unknown):BondProjection {
 const result:BondProjection={tags:new Set(),alignment:new Set(),attribute:new Set(),gender:new Set(),subAttribute:new Set()};
 for(const raw of stringList(tags)){
  result.tags.add(raw);
  if(alignmentValues.has(raw))result.alignment.add(raw);
  if(attributeValues.has(raw))result.attribute.add(raw);
  if(genderValues.has(raw))result.gender.add(raw);
  // Only known alignment compounds are decomposed. star_power and beast tags
  // must never be mistaken for a sub-attribute or class/species.
  const compound=raw.match(/^(lawful|neutral|chaotic)_(good|neutral|balanced|evil|summer|madness|male|female|unknown)$/);
  if(compound){result.alignment.add(compound[1]);const value=compound[2]==='neutral'?'balanced':compound[2];result.tags.add(compound[1]);result.tags.add(value);if(attributeValues.has(value))result.attribute.add(value);if(genderValues.has(value))result.gender.add(value);}
 }
 return result;
}
function baseBondProjection(bond:ServantBond|undefined|null):BondProjection {
 const result=bondTagProjection(bond?.tags);
 if(bond?.alignment&&alignmentValues.has(bond.alignment))result.alignment.add(bond.alignment);
 const attribute=bond?.attribute==='neutral'?'balanced':bond?.attribute;
 if(attribute&&attributeValues.has(attribute))result.attribute.add(attribute);
 if(bond?.gender&&genderValues.has(bond.gender))result.gender.add(bond.gender);
 if(bond?.sub_attribute&&subAttributeValues.has(bond.sub_attribute))result.subAttribute.add(bond.sub_attribute);
 return result;
}
function projectionMatches(projection:BondProjection,filters:ServantAdvancedFilters):boolean {
 return (!filters.alignment||projection.alignment.has(filters.alignment))&&(!filters.attribute||projection.attribute.has(filters.attribute))&&(!filters.gender||projection.gender.has(filters.gender))&&(!filters.subAttribute||projection.subAttribute.has(filters.subAttribute))&&(filters.tags??[]).every(tag=>projection.tags.has(tag));
}
const normalizedNPType=(value:unknown):string=>typeof value==='string'?(value==='辅助'?'辅助宝具':value):'';
const normalizedNPCard=(value:unknown):string=>typeof value==='string'?({arts:'Arts',buster:'Buster',quick:'Quick'} as Record<string,string>)[value.toLowerCase()]??'':'';
export function matchesNPFilter(servant:Servant,filters:ServantAdvancedFilters):boolean {
 const type=normalizedNPType(filters.npType),card=normalizedNPCard(filters.npCard);
 if(!filters.npType&&!filters.npCard)return true;
 if(filters.npCard&&!card)return false;
 const facts=servant.npFilter,variants=Array.isArray(facts?.variants)?facts.variants:[];
 if(variants.length)return variants.some(variant=>variant&&typeof variant==='object'&&(!type||normalizedNPType(variant.type)===type)&&(!card||normalizedNPCard(variant.card)===card));
 // Summary arrays do not establish a type/card pair, even if both have entries.
 if(type&&card)return false;
 return type?stringList(facts?.types).some(value=>normalizedNPType(value)===type):stringList(facts?.cards).some(value=>normalizedNPCard(value)===card);
}
export function npFilterSummary(servant:Servant):string {
 const facts=servant.npFilter;if(!facts)return '';
 const shortType=(value:string)=>({'单体输出':'单体','群体输出':'群体','辅助宝具':'辅助'} as Record<string,string>)[value]??value;
 if(Array.isArray(facts.variants)&&facts.variants.length){
  const pairs=facts.variants.flatMap(variant=>{const type=normalizedNPType(variant?.type),card=normalizedNPCard(variant?.card);return type&&card?[`${shortType(type)} ${card}`]:[];});
  return [...new Set(pairs)].join(' / ');
 }
 const types=[...new Set(stringList(facts.types).map(normalizedNPType))].map(shortType).filter(Boolean);
 const cards=[...new Set(stringList(facts.cards).map(normalizedNPCard))].filter(Boolean);
 return [types.length?`类型参考：${types.join(' / ')}`:'',cards.length?`色卡参考：${cards.join(' / ')}`:''].filter(Boolean).join(' · ');
}
function cloneProjection(value:BondProjection):BondProjection {return {tags:new Set(value.tags),alignment:new Set(value.alignment),attribute:new Set(value.attribute),gender:new Set(value.gender),subAttribute:new Set(value.subAttribute)};}
function conditionalProjections(base:BondProjection,groups:[string,string[]][]):[string,BondProjection][] {
 const conditions=groups.map(([name,tags])=>({name,projection:bondTagProjection(tags)}));
 const seed=cloneProjection(base),touchedTags=new Set(conditions.flatMap(item=>[...item.projection.tags]));
 for(const tag of touchedTags)seed.tags.delete(tag);
 if(conditions.some(item=>item.projection.alignment.size))seed.alignment.clear();
 if(conditions.some(item=>item.projection.attribute.size))seed.attribute.clear();
 // A redundant lawful_female tag does not negate an explicit scalar female in
 // another ascension. Rebuild gender only when conditions give a conflicting
 // value; male/female tags and unknown can legitimately coexist in the base.
 const conflictingGender=conditions.some(item=>[...item.projection.gender].some(value=>!base.gender.has(value)));
 if(conflictingGender)seed.gender.clear();
 return conditions.map(({name,projection})=>{
  const result=cloneProjection(seed);
  for(const key of ['tags','alignment','attribute','gender'] as const)for(const value of projection[key])result[key].add(value);
  return [name,result];
 });
}
export function matchServantAdvanced(servant:Servant,filters:ServantAdvancedFilters):ServantAdvancedMatch {
 if(!matchesNPFilter(servant,filters))return {matches:false,conditionalOnly:false,matchedForms:[]};
 const bond=servant.bond,base=baseBondProjection(bond),baseMatches=projectionMatches(base,filters),matchedForms:string[]=baseMatches?['基础']:[];
 if(filters.includeConditional&&bond){
  const asc=bond.tags_by_ascension;
  const ascGroups:[string,string[]][]=asc&&typeof asc==='object'&&!Array.isArray(asc)?Object.entries(asc).filter(([,tags])=>Array.isArray(tags)).map(([name,tags])=>[`灵基 ${name}`,stringList(tags)]):[];
  for(const [name,projection] of conditionalProjections(base,ascGroups))if(projectionMatches(projection,filters))matchedForms.push(name);
  const costumes=bond.tags_by_costume;
  // A bare costume list establishes one extra condition, not a known costume
  // ID or a relation to a particular ascension. Do not cross-join those forms.
  const costumeGroups:[string,string[]][]=Array.isArray(costumes)?(costumes.length?[['灵衣条件（未指定灵衣）',stringList(costumes)]]:[]):costumes&&typeof costumes==='object'?Object.entries(costumes).filter(([,tags])=>Array.isArray(tags)).map(([name,tags])=>[`灵衣条件 ${name}`,stringList(tags)]):[];
  for(const [name,projection] of conditionalProjections(base,costumeGroups))if(projectionMatches(projection,filters))matchedForms.push(name);
 }
 return {matches:matchedForms.length>0,conditionalOnly:!baseMatches&&matchedForms.length>0,matchedForms};
}
export interface CatalogFilters {query?:string;classKey?:string;rarity?:string;owned?:boolean;common?:boolean;advanced?:ServantAdvancedFilters;equipKind?:string;equipSource?:string;equipAdvanced?:EquipAdvancedFilters}
export function filterCatalog(type:'servant'|'equip'|'mystic',catalog:Catalog,filters:CatalogFilters):(Servant|Equip|Mystic)[] {
 const list=type==='servant'?catalog.servants:type==='equip'?catalog.equips.filter(e=>e.recognizable===true&&!!e.image&&(!filters.equipKind||(e.filters?.kind??'unknown')===filters.equipKind)&&(!filters.equipSource||(e.filters?.source??'unknown')===filters.equipSource)&&(!filters.equipAdvanced||matchEquipAdvanced(e,filters.equipAdvanced).matches)):catalog.mysticCodes;
 const ownIds=type==='equip'?catalog.inventory?.equips:catalog.inventory?.servants;
 const query=filters.query?.trim().toLowerCase();
 return list.filter(item=>{
  const s=item as Servant,bondOwner=type==='equip'?(item as Equip).filters?.bondOwner:undefined,owner=query&&bondOwner?servantFor(catalog,bondOwner):undefined;
  const searchText=`${item.name} ${s.nameJp??''} ${item.id} ${s.collectionNo??''} ${s.searchAliases?.join(' ')??''} ${bondOwner??''} ${owner?.name??''} ${owner?.nameJp??''} ${owner?.id??''} ${owner?.aliases?.join(' ')??''} ${owner?.searchAliases?.join(' ')??''}`;
  return (!query||searchText.toLowerCase().includes(query))&&(!filters.classKey||s.classKey===filters.classKey)&&(!filters.rarity||String(s.rarity)===filters.rarity)&&(!filters.common||type!=='equip'||(item as Equip).common===true)&&(!filters.owned||ownIds?.includes(item.id)||s.aliases?.some(alias=>ownIds?.includes(Number(alias))))&&(type!=='servant'||!filters.advanced||matchServantAdvanced(s,filters.advanced).matches);
 });
}
export const canChange = (data:Json):boolean => [20,210,440].includes(data.team?.mysticCode?.mysticCodeId);
export const isSwap = (data:Json,raw:any):boolean => canChange(data)&&raw?.type==='skill'&&raw.svt==null&&raw.skill===2;
export function turnsOf(data:Json,catalog?:Catalog):Turn[] {
 const turns:Turn[]=[];let current:Entry[]=[];let pair=0;
 for(const [index,raw] of (Array.isArray(data.actions)?data.actions:[]).entries()) {
  const entry:Entry={raw,index};if(isSwap(data,raw)){const replacements=data.delegate?.replaceMemberIndexes;if(Array.isArray(replacements)&&pair<replacements.length){entry.replacement=replacements[pair];entry.paired=true;}pair++;}
  current.push(entry);if(raw?.type==='attack'){turns.push({entries:current});current=[];}
 }if(current.length)turns.push({entries:current});const entries=turns.flatMap(turn=>turn.entries);bindActionActors(data,entries);bindSpecialSkillChoices(data,entries,catalog);return turns;
}
export function writeTurns(data:Json,turns:Turn[],catalog?:Catalog) {
 const oldEntries=turnsOf(data,catalog).flatMap(turn=>turn.entries),nextEntries=clone(turns.flatMap(turn=>turn.entries));
 materializeActionActors(data,nextEntries,usesActorGuards(data)||usesActorGuards({actions:nextEntries.map(entry=>entry.raw)}));
 const rebuilt=rebuildSpecialSkillChoices(data,oldEntries,nextEntries,catalog);
 const oldPairs=data.delegate?.replaceMemberIndexes;const consumed=(Array.isArray(data.actions)?data.actions:[]).filter((a:any)=>isSwap(data,a)).length;
 data.actions=nextEntries.map(entry=>entry.raw);
 const replacements=nextEntries.filter(e=>isSwap(data,e.raw)&&e.paired).map(e=>clone(e.replacement));
 if(Array.isArray(oldPairs)||replacements.length){data.delegate??={};data.delegate.replaceMemberIndexes=[...replacements,...(Array.isArray(oldPairs)?oldPairs.slice(consumed):[])];}
 if(Object.keys(rebuilt.updates).length){data.delegate??={};Object.assign(data.delegate,rebuilt.updates);}markSpecialSkillChoicesRebuilt(data,rebuilt.diagnostics);return rebuilt.diagnostics;
}
export function appendSkillEntry(data:Json,turnIndex:number,entry:Entry,catalog?:Catalog):number {
 const turns=clone(turnsOf(data,catalog));
 if(!Number.isInteger(turnIndex)||turnIndex<0||turnIndex>turns.length)throw new Error('回合不存在，请重新选择回合。');
 if(entry.raw?.type==='attack')throw new Error('技能步骤不能替代本回合的攻击组。');
 if(!turns[turnIndex])turns.push({entries:[{raw:{type:'attack',attacks:[]},index:Array.isArray(data.actions)?data.actions.length:0}]});
 const list=turns[turnIndex].entries,attack=list.findIndex(e=>e.raw?.type==='attack'),at=attack<0?list.length:attack;
 if(entry.raw?.type==='skill'&&entry.raw.svt!=null){
  const position=list[at]?.index??((list.at(-1)?.index??-1)+1),actor=entry.raw.svt;
  if(!Number.isInteger(actor)||actor<0||actor>=3||!hasServantIdentity(rosterAt(data,position)[actor]))throw new Error('请先为这个前排位置指定从者，才能安排主动技能。');
 }
 list.splice(at,0,entry);writeTurns(data,turns,catalog);return at;
}
export function appendNPEntry(data:Json,turnIndex:number,svt:number,catalog:Catalog):number {
 const turns=clone(turnsOf(data,catalog));
 if(!Number.isInteger(turnIndex)||turnIndex<0||turnIndex>turns.length)throw new Error('回合不存在，请重新选择回合。');
 if(!turns[turnIndex])turns.push({entries:[{raw:{type:'attack',attacks:[]},index:Array.isArray(data.actions)?data.actions.length:0}]});
 const list=turns[turnIndex].entries;let at=list.findIndex(e=>e.raw?.type==='attack');
 if(at<0){at=list.length;list.push({raw:{type:'attack',attacks:[]},index:Array.isArray(data.actions)?data.actions.length:0});}
 const attack=list[at];attack.raw.attacks??=[];
 if(attack.raw.attacks.length>=3&&!attack.raw.attacks.slice(0,3).some(isAutoAttackCard))throw new Error('每回合最多安排三个出卡意图。');
 const roster=rosterAt(data,attack.index);
 const card=makeCard(svt,'np',roster,catalog,attack.raw.attacks);bindCardActor(card,data,attack.index);setAttackCard(attack.raw,card);writeTurns(data,turns,catalog);return at;
}
export function rosterAt(data:Json,index:number):any[] {
 return actionRosterAt(data,index).map(actor=>actor.member);
}
export function actionMemberAt(data:Json,index:number,raw:any=data.actions?.[index]):Json|null {
 if(validActorBinding(raw?.actor)){const member=members(data)[raw.actor.initialSlot]??null;return raw.actor.svtId!==undefined&&member?.svtId!==raw.actor.svtId?{svtId:raw.actor.svtId}:member;}
 return actionRosterAt(data,index)[raw?.svt]?.member??null;
}
export function bindEntryActor(entry:Entry,data:Json,svt:number|null):void {
 const old=entry.actorBinding??entry.raw.actor;entry.raw.svt=svt;
 if(svt===null){delete entry.raw.actor;delete entry.actorBinding;delete entry.specialChoice;return;}
 const binding=actorBinding(actionRosterAt(data,entry.index)[svt]);
 if(!binding)throw new Error('当前前排没有可绑定的从者，请重新选择。');
 if(old?.initialSlot!==binding.initialSlot||old?.svtId!==binding.svtId)delete entry.specialChoice;
 entry.actorBinding=binding;
 if(Object.hasOwn(entry.raw,'actor')||usesActorGuards(data))entry.raw.actor=clone(binding);
}
export function bindCardActor(card:Json,data:Json,index:number):void {
 if(isAutoAttackCard(card))return;
 const binding=actorBinding(actionRosterAt(data,index)[card.svt]);
 if(!binding)throw new Error('当前前排没有可绑定的出卡从者。');
 if(Object.hasOwn(card,'actor')||usesActorGuards(data))card.actor=clone(binding);
}
export function retirementInfoAt(data:Json,index:number,_catalog?:Catalog):{slot:number;member:Json|null;replacement:Json|null;initialSlot:number|null;replacementInitialSlot:number|null;valid:boolean;error?:string}{
 const raw=data.actions?.[index],roster=actionRosterAt(data,index),resolved=resolveActionActor(roster,raw),replacement=roster.find((actor,slot)=>slot>=3&&availableActionActor(actor)),error=!isRetire(raw)?'不是退场动作':resolved.issue?.message??(!availableActionActor(resolved.actor)?'退场位置没有从者':!replacement?'没有可递补的未退场候补成员':undefined);
 return {slot:raw?.svt,member:actionMemberAt(data,index,raw),replacement:replacement?.retired?null:replacement?.member??null,initialSlot:resolved.actor?.initialSlot??null,replacementInitialSlot:replacement?.retired?null:replacement?.initialSlot??null,valid:!error,...(error?{error}:{})};
}
export const titleOf=(data:Json)=>String(data.team?.name||'未命名方案');
export const colorOf=(value:any):number => ({arts:1,buster:2,quick:3} as any)[String(value).toLowerCase()]??Number(value);
export const colorName=(value:any):string => ({1:'Arts · 蓝卡',2:'Buster · 红卡',3:'Quick · 绿卡'} as any)[colorOf(value)]||`未知色种 ${String(value)}`;
export const colorClass=(value:any):string => ({1:'arts',2:'buster',3:'quick'} as any)[colorOf(value)]||'';
export const servantFor=(catalog:Catalog,id:any):Servant|undefined=>catalog.servants.find(s=>s.id===id||s.aliases?.some(alias=>Number(alias)===Number(id)));
export function replaceServant(old:Json|null,id:number,catalog:Catalog,defaults:Json=newServantDefaults()):Json {
 const next=old===null?clone(defaults):isUnspecifiedSupport(old)?{...clone(defaults),...clone(old)}:clone(old);
 if(old&&(servantFor(catalog,old.svtId)?.id??old.svtId)!==id)for(const key of ['skillIds','tdId','limitCount','transformVal'])delete next[key];
 next.svtId=id;return next;
}
/** Explicit user action only: importing or replacing an existing member never calls this. */
export function fillMissingTaskSkillDefaults(member:Json):void {
 const defaults=newServantDefaults();
 const keys=['skillLvs','appendLvs'] as const;
 // Check both fields before editing either one, so malformed imported data is
 // not partly rewritten if the user asks to fill an unsupported container.
 for(const key of keys)if(member[key]!=null&&!Array.isArray(member[key]))throw new Error(`${key} 必须是数组或为空，无法补齐技能预设。`);
 for(const key of keys){member[key]??=[];for(let i=0;i<defaults[key].length;i++)if(member[key][i]==null)member[key][i]=defaults[key][i];}
}
export function skillAt(member:Json|undefined,servant:Servant|undefined,index:number):Json|undefined {
 const chosen=member?.skillIds?.[index];
 if(chosen!=null)return servant?.skillVariants?.find(s=>s.id===chosen)??servant?.skills?.find(s=>s.id===chosen)??{id:chosen,name:`技能 #${chosen}`,target:'unknown'};
 return servant?.skills?.[index];
}
export function appendSkillAt(servant:Servant|undefined,index:number):Json|undefined {
 const list=servant?.appendSkills;
 return list?.find(s=>s.num===index+1)??list?.find((s,i)=>s.num==null&&i===index);
}
export function makeCard(svt:number,type:number|'np',roster:any[],catalog:Catalog,used:Json[]=[],explicitIndex?:number):Json {
 if(!Number.isInteger(svt)||svt<0||svt>=3||!hasServantIdentity(roster[svt]))throw new Error('请先为这个前排位置选择从者。');
 if(type==='np'){if(used.some(c=>c?.svt===svt&&c?.isTD))throw new Error('同一回合不能重复指定同一位从者的宝具。');return {svt,isTD:true,card:-1};}
 const cards=servantFor(catalog,roster[svt]?.svtId)?.cards;
 if(explicitIndex!==undefined){
  if(!cards||cards.length!==5)throw new Error('缺少该从者的完整五张牌组元数据，无法指定牌组卡。');
  if(!Number.isInteger(explicitIndex)||explicitIndex<0||explicitIndex>4)throw new Error('指定牌组卡必须是该从者的第 1～5 张牌（合法索引 0～4）。');
  if(used.some(u=>u?.svt===svt&&!u?.isTD&&u?.card===explicitIndex))throw new Error('这张牌组卡已在当前出卡顺序中使用，请选择不同的牌组卡。');
  return {svt,isTD:false,card:explicitIndex,cardType:type};
 }
 const card=cards?.length===5?cards.findIndex((c,i)=>colorOf(c)===type&&!used.some(u=>u?.svt===svt&&!u?.isTD&&u?.card===i)):-1;
 if(card<0)throw new Error('该从者缺少所选颜色的完整牌组元数据，或该颜色可用面卡已全部使用。请改选其他卡色或宝具。');
 return {svt,isTD:false,card,cardType:type};
}
export function move<T>(array:T[],from:number,to:number):T[] {const a=[...array];if(to<0||to>=a.length)return a;const [v]=a.splice(from,1);a.splice(to,0,v);return a;}
export function actionTitle(entry:Entry,data:Json,catalog:Catalog):string {
 const a=entry.raw;if(a?.type==='attack')return '攻击 · 回合结束';if(isRetire(a))return `${memberLabel(actionMemberAt(data,entry.index,a),catalog)} · 从者退场`;if(isSwap(data,a))return '御主技能 3 · 换人';if(a?.type!=='skill')return `保留动作 · ${a?.type??'未知类型'}`;
 if(a.svt==null){const skills=catalog.mysticCodes.find(m=>m.id===data.team?.mysticCode?.mysticCodeId)?.skills;const skill=skills?.find(s=>s.num===a.skill+1)??skills?.find((s,i)=>s.num==null&&i===a.skill);return `御主 · ${skill?.name??`技能 ${Number(a.skill)+1}`}`;}const member=actionMemberAt(data,entry.index,a),servant=servantFor(catalog,member?.svtId);const name=memberLabel(member,catalog);if(!hasServantIdentity(member))return `前排 ${Number(a.svt)+1} · ${name} · 未指定从者，无法安排技能`;return `前排 ${Number(a.svt)+1} · ${name} · ${skillAt(member,servant,a.skill)?.name??`技能 ${Number(a.skill)+1}`}`;
}
export function remapSlots(data:Json,first:number,second:number) { // Only safe while no order changes or unknown actions exist.
 if((data.actions??[]).some((a:any)=>!['attack','skill'].includes(a?.type)||isSwap(data,a)))throw new Error('存在换人或未知动作，不能可靠重映射角色。请使用保留槽位指向。');
 if(first>=3||second>=3) {if((data.actions??[]).length)throw new Error('前排与候补交换会改变执行资格，不能自动重映射。请逐项检查后保留槽位指向。');}
 const map=(n:any)=>n===first?second:n===second?first:n;
 const remapActor=(raw:any)=>{if(validActorBinding(raw?.actor))raw.actor.initialSlot=map(raw.actor.initialSlot);};
 for(const a of data.actions??[]){if(a.type==='skill'){if(a.svt!=null)a.svt=map(a.svt);remapActor(a);if(a.options?.playerTarget!=null)a.options.playerTarget=map(a.options.playerTarget);}else if(a.type==='attack')for(const card of a.attacks??[])if(!isAutoAttackCard(card)){card.svt=map(card.svt);remapActor(card);}}
 const roster=members(data);setMember(data,first,roster[second]);setMember(data,second,roster[first]);
}
