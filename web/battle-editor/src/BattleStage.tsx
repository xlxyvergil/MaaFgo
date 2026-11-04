import {useEffect,useMemo,useState} from 'react';
import {ArrowLeftRight,Check,Clock3,Info,Settings2,Shield,UserRoundMinus,X} from 'lucide-react';
import {canChange,turnsOf,hasServantIdentity,isUnspecifiedSupport,memberLabel} from './model';
import type {Catalog,Entry,Json} from './model';
import {isAutoAttackCard} from './attackCards';
import {previewTurn,npAt,skillTargetMode,allowedAllyTargets,hasEnemyTarget} from './battlePreview';
import type {PreviewSkill} from './battlePreview';
import {Modal} from './EditorModal';
import {SkillIcon} from './PartyCard';
import {NPCardPreview} from './NPCardPreview';
import {cardPreviewRegion as resolveCardPreviewRegion} from './npCardRequest';
import type {CardPreviewConfig} from './npCardRequest';
import {createManualReadyState,manualReadyKeys,syncManualReady,grantManualReady,revokeManualReady,acceptManualReadyCommit} from './manualReady';
import type {StageCommit} from './manualReady';
import {specialSkillChoiceFor,setEntrySpecialSkillChoice} from './specialSkillChoices';
import type {SpecialSkillChoiceGroup} from './specialSkillChoices';
import {SpecialSkillOptions} from './SpecialSkillOptions';
import './retirement.css';

interface Pending {svt:number|null;skill:number;key:string;ally?:number;enemy:number;mode:'ally'|'none'|'unknown';meta?:Json;swap?:boolean;front?:number;backup?:number;specialGroup?:SpecialSkillChoiceGroup;specialValue?:number}
type ContextMenu = {kind:'skill';key:string;name:string;x:number;y:number}|{kind:'retire';svt:number;name:string;x:number;y:number};
const occupied=(member:Json|null|undefined)=>hasServantIdentity(member)||isUnspecifiedSupport(member);
const remaining=(skill:PreviewSkill)=>skill.remainingMin==null||skill.remainingMax==null?'CD 未知':skill.remainingMin===skill.remainingMax?`CD ${skill.remainingMax}`:`CD ${skill.remainingMin}–${skill.remainingMax}`;

export function BattleStage({data,catalog,turnIndex,onAppend,onNP,onMystic,onLevel,onError,npCardPreview,cardPreviewRegion,onCardPreviewRegion}:{data:Json;catalog:Catalog;turnIndex:number;onAppend:(entry:Entry)=>StageCommit|null;onNP:(svt:number)=>StageCommit|null;onMystic:()=>void;onLevel:(level:number|undefined)=>void;onError:(message:string)=>void;npCardPreview?:CardPreviewConfig;cardPreviewRegion?:string;onCardPreviewRegion?:(region:string)=>void}) {
 const preview=useMemo(()=>previewTurn(data,catalog,turnIndex),[data,catalog,turnIndex]);
 const [localRegion,setLocalRegion]=useState(()=>{try{return localStorage.getItem('maafgo-card-preview-region');}catch{return null;}});
 const region=resolveCardPreviewRegion(cardPreviewRegion??localRegion,npCardPreview);
 const changeRegion=(value:string)=>{if(onCardPreviewRegion)onCardPreviewRegion(value);else{setLocalRegion(value);try{localStorage.setItem('maafgo-card-preview-region',value);}catch{}}};
 const [manualReady,setManualReady]=useState(()=>createManualReadyState(data,turnIndex)),[pending,setPending]=useState<Pending|null>(null),[menu,setMenu]=useState<ContextMenu|null>(null);
 const ready=manualReadyKeys(manualReady,data,turnIndex);
 useEffect(()=>{setManualReady(state=>syncManualReady(state,data,turnIndex));setPending(null);setMenu(null);},[data,turnIndex]);
 useEffect(()=>{if(!menu)return;const close=(event:KeyboardEvent)=>{if(event.key==='Escape'||event.key==='Tab')setMenu(null);};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[menu]);
 const attack=turnsOf(data,catalog)[turnIndex]?.entries.find(e=>e.raw?.type==='attack')?.raw;
 const skillClick=(svt:number|null,index:number,state:PreviewSkill,key:string)=>{
  if(svt!=null&&(!hasServantIdentity(preview.front[svt]?.member)||preview.front[svt]?.retired)){onError('请先在编队页为这个前排位置指定未退场从者，才能安排技能。');return;}
  if(svt==null&&!data.team?.mysticCode?.mysticCodeId){onError('请先选择御主礼装。');return;}
  if(!state.ready&&!ready.has(key))return;
  const meta=state.skill,mode=skillTargetMode(meta),swap=svt==null&&index===2&&canChange(data);
  const actor=svt==null?undefined:preview.front[svt],specialGroup=actor?specialSkillChoiceFor(actor.member,actor.servant,index,catalog):undefined;
  const next:Pending={svt,skill:index,key,enemy:2,mode,meta,swap,specialGroup};
  if(swap||specialGroup||mode!=='none'){setPending(next);return;}
  commit(next);
 };
 const commit=(item:Pending)=>{
  let accepted:StageCommit|null;
  if(item.swap){
   if(item.front==null||item.backup==null||!occupied(preview.front[item.front]?.member)||!occupied(preview.roster[item.backup+3]?.member)||preview.front[item.front]?.retired||preview.roster[item.backup+3]?.retired){onError('请从当前前排和候补各选择一位未退场成员。');return;}
   accepted=onAppend({raw:{type:'skill',svt:null,skill:2},replacement:[item.front,item.backup],paired:true,index:0});
  }else{
   if(item.svt!=null&&!hasServantIdentity(preview.front[item.svt]?.member)){onError('未指定从者，无法安排这个位置的技能。');return;}
   const options:Json={};if(item.ally!=null)options.playerTarget=item.ally;if(hasEnemyTarget(item.meta))options.enemyTarget=item.enemy;
   const raw:Json={type:'skill',svt:item.svt,skill:item.skill};if(Object.keys(options).length)raw.options=options;
   const entry:Entry={raw,index:0};
   if(item.specialGroup){
    if(item.specialValue==null){onError('请先选择这次特殊技能的效果。');return;}
    setEntrySpecialSkillChoice(entry,item.specialGroup,item.specialValue);
   }
   accepted=onAppend(entry);
  }
  if(!accepted||accepted.previous===accepted.next)return;
  setManualReady(state=>acceptManualReadyCommit(state,accepted,{consume:item.key,resetAll:!!item.swap}));
  setPending(null);
 };
 const chooseSpecial=(value:number)=>{
  if(!pending?.specialGroup)return;
  const option=pending.specialGroup.options.find(choice=>choice.value===value);
  if(!option)return;
  const meta={...pending.meta,...(option.target?{target:option.target}:{}),...(option.targets?{targets:option.targets}:{})};
  const next={...pending,specialValue:value,meta,mode:skillTargetMode(meta)};
  if(next.mode==='none')commit(next);else setPending(next);
 };
 const appendNP=(svt:number)=>{if(!hasServantIdentity(preview.front[svt]?.member)||preview.front[svt]?.retired){onError('未指定未退场从者，无法安排这个位置的宝具。');return;}const accepted=onNP(svt);if(accepted)setManualReady(state=>acceptManualReadyCommit(state,accepted));};
 const showMenu=(key:string,name:string,event?:{clientX:number;clientY:number;preventDefault:()=>void})=>{event?.preventDefault();setMenu({kind:'skill',key,name,x:Math.min(event?.clientX??window.innerWidth/2,window.innerWidth-245),y:Math.min(event?.clientY??window.innerHeight/2,window.innerHeight-150)});};
 const showRetirement=(svt:number,event:{clientX:number;clientY:number;preventDefault:()=>void})=>{event.preventDefault();const actor=preview.front[svt];if(!hasServantIdentity(actor?.member)||actor?.retired)return;setMenu({kind:'retire',svt,name:memberLabel(actor.member,catalog),x:Math.min(event.clientX,window.innerWidth-285),y:Math.min(event.clientY,window.innerHeight-245)});};
 const appendRetirement=(svt:number)=>{const accepted=onAppend({raw:{type:'retire',svt},index:0});if(accepted)setManualReady(state=>acceptManualReadyCommit(state,accepted,{resetAll:true}));setMenu(null);};
 const skillButton=(svt:number|null,state:PreviewSkill,key:string)=>{
  const actorPresent=svt==null?!!data.team?.mysticCode?.mysticCodeId:hasServantIdentity(preview.front[svt]?.member)&&!preview.front[svt]?.retired;
  const manuallyReady=actorPresent&&ready.has(key),available=actorPresent&&(state.ready||manuallyReady),name=svt==null&&state.index===2&&canChange(data)?'换人':state.skill?.name??`技能 ${state.index+1}`;
  const displaySkill=state.skill&&name!==state.skill.name?{...state.skill,name}:state.skill;
  return <div className={`battle-skill ${available?'ready':'cooling'} ${manuallyReady?'manual-ready':''}`} key={state.index}>
   <button className="battle-skill-button" disabled={!actorPresent} aria-label={`${svt==null?'御主':`前排 ${(svt??0)+1}`} 技能 ${state.index+1}：${name}${available?'':'，冷却中'}`} aria-disabled={!available} onClick={()=>skillClick(svt,state.index,state,key)} onContextMenu={event=>{if(actorPresent)showMenu(key,name,event);}} title={!actorPresent?(svt==null?'请先选择御主礼装':'未指定从者，无法安排技能'):`${name}\n${available?(manuallyReady?'临时置为就绪':'点击安排技能'):remaining(state)}\n${manuallyReady?'右键可取消就绪':'右键可在编辑器中置为就绪'}`}>
    <SkillIcon skill={displaySkill} index={state.index}/><span className="battle-skill-number">{state.index+1}</span>{!available&&<span className="battle-skill-cooldown">{actorPresent?<><span>CD</span>{' '}<span>{remaining(state).slice(3)}</span></>:<span>—</span>}</span>}{manuallyReady&&<span className="battle-skill-reset"><Check size={12}/></span>}
   </button>
   <div className="battle-skill-caption"><span title={name}>{name}</span><button className="skill-ready-menu" disabled={!actorPresent} aria-label={`设置${svt==null?'御主':`前排 ${(svt??0)+1}`}技能 ${state.index+1} 就绪状态`} title={`技能操作：${ready.has(key)?'取消就绪':'置为就绪'}`} aria-haspopup="menu" onClick={event=>showMenu(key,name,{clientX:event.currentTarget.getBoundingClientRect().left,clientY:event.currentTarget.getBoundingClientRect().bottom,preventDefault:()=>{}})}><Clock3 size={13}/></button></div>
   <small>{!actorPresent?(svt==null?'未配置御主礼装':'未指定从者'):manuallyReady?'手动就绪':state.usedThisTurn?'本回合已用':!available?remaining(state):'可安排'}</small>
  </div>;
 };
 const choices=pending?allowedAllyTargets(pending.meta,pending.svt??-1,preview.roster):[];
 return <section className="battle-stage" aria-label="当前战斗技能区">
  <div className="battle-stage-heading"><div><span className="eyebrow">COMMAND PHASE</span><h2>当前前排 <small>技能队列末尾 · 攻击之前</small></h2></div><div className="battle-preview-controls"><label><span>卡面文字</span><select aria-label="卡面文字地区，仅影响预览" value={region} onChange={event=>changeRegion(event.target.value)}>{(npCardPreview?.regions??['JP','CN']).filter(value=>value==='JP'||value==='CN').map(value=><option key={value} value={value}>{value==='JP'?'日服 JP':'国服 CN'}</option>)}</select><small>仅预览</small></label><span className="battle-phase">{turnsOf(data,catalog)[turnIndex]?`TURN ${String(turnIndex+1).padStart(2,'0')}`:'阵容预览'}</span></div></div>
  <div className="battle-stage-scroll"><div className="battle-servants">{preview.front.map((entry,i)=>{
   const np=npAt(entry.member,entry.servant),selected=!!attack?.attacks?.some((c:Json)=>!isAutoAttackCard(c)&&c?.isTD&&c.svt===i),full=(attack?.attacks?.length??0)>=3&&!attack?.attacks?.slice(0,3).some(isAutoAttackCard);
   return <article className={`battle-servant ${!hasServantIdentity(entry.member)?'empty':''}`} key={entry.key}>
    <div className="battle-servant-status"><span className="battle-front-number">{i+1}</span>{hasServantIdentity(entry.member)?<button type="button" className={`battle-servant-avatar ${entry.servant?.statusImage?'status-art':'status-face'}`} aria-label={`前排 ${i+1} ${memberLabel(entry.member,catalog)} 从者操作`} aria-haspopup="menu" disabled={entry.retired} title="右键头像可添加从者退场标记" onContextMenu={event=>showRetirement(i,event)} onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();showRetirement(i,{clientX:rect.left,clientY:rect.bottom,preventDefault:()=>{}});}}>{entry.servant?.statusImage||entry.servant?.image?<img src={entry.servant.statusImage||entry.servant.image!} alt={entry.servant.name}/>:<Shield size={30}/>}</button>:<span className="status-placeholder"><Shield size={30}/></span>}<div className="battle-status-label"><strong>{memberLabel(entry.member,catalog)}</strong><small>{isUnspecifiedSupport(entry.member)?'从者身份未指定 · 仅要求礼装':hasServantIdentity(entry.member)?`${entry.servant?.className??'从者'} · Lv.${entry.member?.lv??'—'}`:'请先在编队页设置'}</small></div></div>
    <div className="battle-servant-commands"><div className="battle-three-skills">{entry.skills.map(state=>skillButton(i,state,`${entry.key}:${state.index}`))}</div><NPCardPreview member={entry.member} np={np} region={region} config={npCardPreview} slot={i} selected={selected} full={full} onClick={()=>appendNP(i)}/></div>
   </article>;
  })}</div></div>
  <div className="battle-master-bar"><div className="battle-master-equipment"><button className="master-equipment-button" aria-label="选择回合御主礼装" onClick={onMystic}>{preview.master.mystic?.image?<img src={preview.master.mystic.image} alt={preview.master.mystic.name}/>:<Shield size={30}/>}<span><small>MASTER EQUIPMENT</small><strong>{preview.master.mystic?.name??'选择御主礼装'}</strong><span><Settings2 size={12}/>更换御主服</span></span></button><label className="master-level"><span>礼装等级</span><input type="number" min={1} max={10} aria-label="回合御主礼装等级" placeholder="未指定" value={data.team?.mysticCode?.level??''} onChange={e=>onLevel(e.target.value===''?undefined:Number(e.target.value))}/></label></div><div className="battle-master-skills">{preview.master.skills.map(state=>skillButton(null,state,`master:${state.index}`))}</div></div>
  <div className="battle-stage-note"><Info size={14}/><span>右键从者头像可标记退场。基础 CD 仅供排程预览；右键技能或时钟按钮可临时置为就绪。候补 CD 暂停，不模拟减 CD、NP 条和特殊再使用效果。</span></div>
  {preview.warnings.length>0&&<details className="battle-preview-warnings"><summary>{preview.warnings.length} 项排程提示 · 已有动作完整保留</summary>{preview.warnings.map((warning,i)=><p key={i}>{warning.message}</p>)}</details>}
  {menu&&<div className="battle-context-backdrop" onClick={()=>setMenu(null)} onContextMenu={event=>{event.preventDefault();setMenu(null);}}><div className={`battle-context-menu ${menu.kind==='retire'?'retirement-context-menu':''}`} role="menu" aria-label={menu.kind==='retire'?'从者操作':'技能操作'} style={{left:Math.max(8,menu.x),top:Math.max(8,menu.y)}} onClick={event=>event.stopPropagation()}><strong>{menu.name}</strong>{menu.kind==='retire'?<><button role="menuitem" autoFocus className="retirement-menu-action" onClick={()=>appendRetirement(menu.svt)}><UserRoundMinus size={17}/>从者退场</button><small>加入技能队列，首位候补递补到此位置，退场者移至排序末尾。</small><small>用于标记游戏中已发生的退场。技能立即退场可排在技能后；宝具后退场请排在下一回合开头。</small></>:<><button role="menuitem" autoFocus onClick={()=>{setManualReady(state=>(ready.has(menu.key)?revokeManualReady:grantManualReady)(state,data,turnIndex,menu.key));setMenu(null);}}>{ready.has(menu.key)?<><X size={16}/>取消就绪</>:<><Clock3 size={16}/>在编辑器中置为就绪</>}</button><small>{ready.has(menu.key)?'仅取消此技能的临时就绪，保留已排动作。':'保留已排动作；不向游戏重置 CD。'}</small></>}</div></div>}
  {pending&&<Modal title={pending.swap?'御主技能 3 · 换人':`${pending.svt==null?'御主':`前排 ${pending.svt+1}`} · ${pending.meta?.name??`技能 ${pending.skill+1}`}`} onClose={()=>setPending(null)}><div className="modal-content battle-target-dialog">
   {pending.swap?<><p>选择此时的前排和候补，确认后按当前顺序加入换人步骤。</p><div className="swap-choice-section"><strong>换出前排</strong><div className="target-choices">{preview.front.map((entry,i)=><button key={i} className={pending.front===i?'selected':''} disabled={!occupied(entry.member)||entry.retired} onClick={()=>setPending({...pending,front:i})}>{entry.servant?.image&&<img src={entry.servant.image} alt=""/>}<span>{i+1} · {memberLabel(entry.member,catalog)}</span></button>)}</div></div><ArrowLeftRight className="swap-choice-arrow" size={22}/><div className="swap-choice-section"><strong>换入候补</strong><div className="target-choices">{preview.roster.slice(3,6).map((entry,i)=><button key={i} className={pending.backup===i?'selected':''} disabled={!occupied(entry.member)||entry.retired} onClick={()=>setPending({...pending,backup:i})}>{entry.servant?.image&&<img src={entry.servant.image} alt=""/>}<span>{i+1} · {memberLabel(entry.member,catalog)}</span></button>)}</div></div></>:<>
    {pending.specialGroup&&pending.specialValue==null?<SpecialSkillOptions group={pending.specialGroup} onSelect={chooseSpecial}/>:<>
    {pending.specialGroup&&<div className="special-choice-heading"><span className="special-choice-selected">{pending.specialGroup.options.find(option=>option.value===pending.specialValue)?.label}</span><button className="text-button" onClick={()=>setPending({...pending,specialValue:undefined})}>重新选择效果</button></div>}
    <p>{pending.mode==='ally'?'点击当前前排头像，直接加入技能队列。':'目标元数据不完整。点击头像指定目标，或点击“不指定我方目标”直接加入。'}</p>
    {hasEnemyTarget(pending.meta)&&<label className="field"><span>敌方目标 · 先调整，再点击我方目标加入</span><select value={pending.enemy} onChange={e=>setPending({...pending,enemy:Number(e.target.value)})}><option value={2}>敌方 1 · 左侧</option><option value={1}>敌方 2 · 中间</option><option value={0}>敌方 3 · 右侧</option></select></label>}
    <div className="target-choices">{preview.front.map((entry,i)=><button key={i} disabled={!choices.includes(i)} onClick={()=>commit({...pending,ally:i})}>{entry.servant?.image&&<img src={entry.servant.image} alt=""/>}<span>前排 {i+1}</span><strong>{memberLabel(entry.member,catalog)}</strong></button>)}</div>{pending.mode==='unknown'&&<button className="button target-unspecified" onClick={()=>commit({...pending,ally:undefined})}>不指定我方目标</button>}
    </>}
   </>}
   <div className="modal-bottom"><button className="button" onClick={()=>setPending(null)}>取消</button>{pending.swap&&<button className="button primary" disabled={pending.front==null||pending.backup==null} onClick={()=>commit(pending)}><Check size={16}/>加入技能队列</button>}</div>
  </div></Modal>}
 </section>;
}
