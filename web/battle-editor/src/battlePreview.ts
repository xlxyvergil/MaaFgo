import {projectServantForm} from './servantForms';
import {members,servantFor,skillAt,turnsOf,isSwap,hasServantIdentity,isUnspecifiedSupport} from './model';
import type {Catalog,Entry,Json,Mystic,Servant} from './model';
import {advanceActionRoster,isRetire,resolveActionActor} from './actionRoster';
import {isAutoAttackCard} from './attackCards';
import {specialSkillChoiceFor,specialSkillChoiceNP} from './specialSkillChoices';

/** Display-only cooldown information. This module never edits BattleShareData. */
export interface CooldownBounds {
  remainingMin:number|null;
  remainingMax:number|null;
  source:'exact'|'level-range'|'unknown';
}
export interface PreviewSkill extends CooldownBounds {
  skill:Json|undefined;
  index:number;
  used:boolean;
  usedThisTurn:boolean;
  ready:boolean;
  lastActionIndex?:number;
}
export interface PreviewMember {
  key:string;
  initialSlot:number;
  slot:number;
  member:Json|null;
  servant:Servant|undefined;
  np:Json|undefined;
  npState?:{kind:'mash-sword-loaded';remainingTurns:number}|{kind:'skill-np-change';remainingTurns:number;np:Json};
  skills:PreviewSkill[];
  retired:boolean;
}
export interface PreviewWarning {
  code:'repeat-skill'|'cooldown-skill'|'unknown-cooldown'|'invalid-swap'|'missing-actor'|'invalid-actor'|'retired-actor'|'actor-mismatch'|'invalid-retire'|'missing-replacement';
  actionIndex:number;
  message:string;
}
export interface BattlePreview {
  turnIndex:number;
  roster:PreviewMember[];
  front:PreviewMember[];
  master:{mystic:Mystic|undefined;skills:PreviewSkill[]};
  beforeAttackIndex:number;
  warnings:PreviewWarning[];
}

const isCooldown=(value:unknown):value is number=>Number.isInteger(value)&&Number(value)>=0;

/** Unknown levels use the actual ten-level range, never an assumed level 10. */
export function cooldownBounds(skill:Json|undefined,level:unknown):CooldownBounds {
  const values:unknown=skill?.coolDown;
  if(!Array.isArray(values)||!values.length)return {remainingMin:null,remainingMax:null,source:'unknown'};
  if(typeof level==='number'&&Number.isInteger(level)&&level>=1&&level<=10){
    const value=values[level-1];
    return isCooldown(value)?{remainingMin:value,remainingMax:value,source:'exact'}:{remainingMin:null,remainingMax:null,source:'unknown'};
  }
  if(level!=null&&level!==0)return {remainingMin:null,remainingMax:null,source:'unknown'};
  // A partial table cannot bound all possible skill levels reliably.
  if(values.length!==10||!values.every(isCooldown))return {remainingMin:null,remainingMax:null,source:'unknown'};
  return {remainingMin:Math.min(...values),remainingMax:Math.max(...values),source:'level-range'};
}

const initialSkill=(skill:Json|undefined,index:number,level:unknown,hasActor:boolean):PreviewSkill=>({
  ...cooldownBounds(hasActor?skill:undefined,level),skill:hasActor?skill:undefined,index,used:false,usedThisTurn:false,
  // Start from the ordinary ready state; special starting cooldowns are outside
  // this basic preview, as are seals, append 5 and skill-effect cooldown changes.
  remainingMin:hasActor?0:null,remainingMax:hasActor?0:null,ready:hasActor,
});

export function npAt(member:Json|undefined|null,servant:Servant|undefined):Json|undefined {
  if(!hasServantIdentity(member))return undefined;
  const chosen=member?.tdId;servant=projectServantForm(servant,member);
  if(chosen!=null&&!(servant?.forms?.length&&chosen===0))return servant?.npVariants?.find(np=>np.id===chosen)
    ??(servant?.np?.id===chosen?servant?.np:undefined)
    ??{id:chosen,name:`宝具 #${chosen}`};
  return servant?.np??undefined;
}

/** Planned NP state immediately before this attack; never changes the saved tdId. */
export function npAtAction(data:Json,catalog:Catalog,actionIndex:number,slot:number,card?:Json):Json|undefined {
  if(!Number.isInteger(slot)||slot<0||slot>2||data.actions?.[actionIndex]?.type!=='attack')return undefined;
  const turnIndex=turnsOf(data).findIndex(turn=>turn.entries.some(entry=>entry.index===actionIndex));
  if(turnIndex<0)return undefined;
  const preview=previewTurn(data,catalog,turnIndex);
  if(card){
    if(card.svt!==slot)return undefined;
    const resolved=resolveActionActor(preview.roster,card);
    if(resolved.issue){
      // Invalid recorded actions still show their original owner, never the
      // replacement at this slot. Validation separately blocks their execution.
      const original=resolved.actor as PreviewMember|undefined;
      return original&&original.member?.svtId===card.actor?.svtId?original.np:undefined;
    }
  }
  return preview.front[slot]?.np;
}

function updateLoadedNP(actor:PreviewMember) {
  const initial=npAt(actor.member,actor.servant);
  actor.np=actor.npState?.remainingTurns
    ?actor.npState.kind==='skill-np-change'?actor.npState.np
      :actor.servant?.npVariants?.find(np=>np.id===800108)??{id:800108,name:'圣剑装填宝具（资料待补充）',cardType:2}
    :initial;
}

/** Replay the selected NP for display; saved team.tdId and delegate stay intact. */
function applySkillNPChange(actor:PreviewMember,entry:Entry,catalog:Catalog) {
  const group=specialSkillChoiceFor(actor.member,actor.servant,entry.raw.skill,catalog);
  const duration=group?.durationTurns;
  const binding=entry.specialChoice;
  if(group?.field!=='tdTypeChanges'||typeof duration!=='number'||!Number.isInteger(duration)||duration<=0||!binding||binding.groupId!==group.id||binding.missing)return;
  const rawValue=binding.value;
  const value=typeof rawValue==='string'?({arts:1,buster:2,quick:3} as Record<string,number>)[rawValue]:rawValue;
  const option=group.options.find(item=>item.value===value);
  if(!option)return;
  const base=npAt(actor.member,actor.servant);
  const np=specialSkillChoiceNP(option,base,actor.servant);
  if(!np)return;
  actor.npState={kind:'skill-np-change',remainingTurns:duration,np};
  updateLoadedNP(actor);
}

/** Atlas buff 5825 lasts three on-field turns; only NP 800107 grants it. */
function advanceNPState(roster:PreviewMember[],attack:Json) {
  const used=new Set<string>();
  for(const card of (Array.isArray(attack.attacks)?attack.attacks:[]).slice(0,3)){
    if(isAutoAttackCard(card)||card?.isTD!==true)continue;
    const resolved=resolveActionActor(roster,card),actor=resolved.actor as PreviewMember|undefined;
    if(resolved.issue||!actor||actor.retired||used.has(actor.key))continue;
    used.add(actor.key);
    if(actor.member?.svtId===800100&&actor.np?.id===800107){
      actor.npState={kind:'mash-sword-loaded',remainingTurns:3};
    }
  }
  // Like Chaldea's active-player buff clock, backup members retain their state.
  for(const actor of roster.slice(0,3)){
    if(actor.npState&&--actor.npState.remainingTurns<=0)delete actor.npState;
    updateLoadedNP(actor);
  }
}

export function skillTargetMode(skill:Json|undefined):'ally'|'none'|'unknown' {
  return skill?.target==='ally'?'ally':skill?.target==='none'?'none':'unknown';
}

/** Only explicit single-target enemy functions imply a selectable enemy. */
export function hasEnemyTarget(skill:Json|undefined):boolean {
  return Array.isArray(skill?.targets)&&skill.targets.includes('enemy');
}

/** Effect recipients do not restrict selection: ptOneOther excludes the chosen ally, not the caster. */
export function allowedAllyTargets(_skill:Json|undefined,_casterSlot:number,roster:readonly PreviewMember[]):number[] {
  return roster.slice(0,3).flatMap((entry,index)=>
    hasServantIdentity(entry.member)||isUnspecifiedSupport(entry.member)?[index]:[]);
}

function decrease(skills:PreviewSkill[]) {
  for(const state of skills){
    if(state.remainingMin!=null)state.remainingMin=Math.max(0,state.remainingMin-1);
    if(state.remainingMax!=null)state.remainingMax=Math.max(0,state.remainingMax-1);
    state.ready=state.remainingMax===0;
    state.usedThisTurn=false;
  }
}

/**
 * Replay through this turn's last skill, immediately before its attack group.
 * Identity belongs to each original formation instance, so two identical
 * servants and a friend's copy never share cooldowns. Order Change moves the
 * instance; only members on field at an attack boundary receive the basic tick.
 */
export function previewTurn(data:Json,catalog:Catalog,turnIndex:number):BattlePreview {
  const turns=turnsOf(data,catalog);
  const selected=Math.max(0,Math.min(Math.max(0,turns.length-1),Number.isInteger(turnIndex)?turnIndex:0));
  const roster:PreviewMember[]=members(data).map((member,initialSlot)=>{
    const actorPresent=hasServantIdentity(member),servant=actorPresent?servantFor(catalog,member?.svtId,member):undefined;
    return {key:`initial:${initialSlot}`,initialSlot,slot:initialSlot,member,servant,np:npAt(member,servant),retired:false,
      skills:[0,1,2].map(index=>initialSkill(actorPresent?skillAt(member??undefined,servant,index):undefined,index,member?.skillLvs?.[index],actorPresent))};
  });
  const mystic=catalog.mysticCodes.find(code=>code.id===data.team?.mysticCode?.mysticCodeId);
  const master={mystic,skills:[0,1,2].map(index=>initialSkill(
    mystic?.skills?.find(skill=>skill.num===index+1)??mystic?.skills?.find((skill,i)=>skill.num==null&&i===index),index,
    data.team?.mysticCode?.level,!!data.team?.mysticCode?.mysticCodeId))};
  const warnings:PreviewWarning[]=[];
  let beforeAttackIndex=Array.isArray(data.actions)?data.actions.length:0;
  for(let ti=0;ti<=selected;ti++){
    for(const entry of turns[ti]?.entries??[]){
      const action=entry.raw;
      if(action?.type==='attack'){
        if(ti===selected){beforeAttackIndex=entry.index;break;}
        advanceNPState(roster,action);
        for(const actor of roster.slice(0,3))if(hasServantIdentity(actor.member))decrease(actor.skills);
        decrease(master.skills);
        for(const actor of roster)for(const state of actor.skills)state.usedThisTurn=false;
        continue;
      }
      if(isRetire(action)){
        const issue=advanceActionRoster(data,roster,entry);roster.forEach((actor,slot)=>{actor.slot=slot;if(actor.retired)for(const state of actor.skills)state.ready=false;});
        if(issue)warnings.push({...issue,actionIndex:entry.index});continue;
      }
      if(action?.type!=='skill')continue;
      const skillIndex=action.skill;
      if(!Number.isInteger(skillIndex)||skillIndex<0||skillIndex>2)continue;
      const isMaster=action.svt==null;
      const binding=!isMaster?resolveActionActor(roster,action,entry.actorBinding):undefined;
      if(binding?.issue){warnings.push({...binding.issue,actionIndex:entry.index});continue;}
      const actor=!isMaster&&Number.isInteger(action.svt)&&action.svt>=0&&action.svt<3?roster[action.svt]:undefined;
      const state=isMaster?master.skills[skillIndex]:hasServantIdentity(actor?.member)?actor?.skills[skillIndex]:undefined;
      if(!state){warnings.push({code:'missing-actor',actionIndex:entry.index,message:isUnspecifiedSupport(actor?.member)?'该技能引用仅要求礼装的助战，未指定从者，无法安排技能。':'该技能动作时点的从者槽位为空或未指定有效从者。'});continue;}
      if(state.usedThisTurn)warnings.push({code:'repeat-skill',actionIndex:entry.index,message:'同一回合重复施法；需要实战存在相应减 CD 或再次使用机制。'});
      else if(!state.ready)warnings.push({code:'cooldown-skill',actionIndex:entry.index,message:'此动作超出基础 CD 预览；计划保留，需要实战减 CD 等机制。'});
      const level=isMaster?data.team?.mysticCode?.level:actor?.member?.skillLvs?.[skillIndex];
      Object.assign(state,cooldownBounds(state.skill,level),{used:true,usedThisTurn:true,lastActionIndex:entry.index});
      // A click is one recorded use even for a rare zero-CD skill. Another use
      // in this turn still requires the explicit manual-ready interaction.
      state.ready=false;
      if(actor)applySkillNPChange(actor,entry,catalog);
      if(state.remainingMax==null)warnings.push({code:'unknown-cooldown',actionIndex:entry.index,message:'缺少可靠技能或等级 CD 元数据；冷却保持未知，可手动右键就绪。'});
      if(isSwap(data,action)){
        const pair=entry.replacement,front=pair?.[0],back=pair?.[1]<3?pair[1]+3:pair?.[1];
        if(!roster[front]?.member||!roster[back]?.member){warnings.push({code:'invalid-swap',actionIndex:entry.index,message:'换人配对缺失、无效或包含空槽；不推测换人后的成员。'});continue;}
        const issue=advanceActionRoster(data,roster,entry);roster.forEach((actor,slot)=>{actor.slot=slot;});
        if(issue)warnings.push({...issue,actionIndex:entry.index});
      }
    }
  }
  return {turnIndex:selected,roster,front:roster.slice(0,3),master,beforeAttackIndex,warnings};
}
