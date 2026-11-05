import {useEffect,useMemo,useRef,useState} from 'react';
import {AlertTriangle,ArrowDownWideNarrow,CalendarDays,CloudDownload,Globe,LoaderCircle,RefreshCw,Shield,Sparkles,ThumbsUp,Upload,UserRound,Users} from 'lucide-react';
import {api} from './api';
import {Modal} from './EditorModal';
import type {Catalog,Equip,Servant} from './model';
import {appendCommonTeams,commonTeamDate,commonTeamQuery,GRAND_CLASS_ICONS,readCommonTeamCatalog,readCommonTeamPage} from './commonTeamsModel';
import type {CommonTeam,CommonTeamCatalog,CommonTeamMember,CommonTeamPage,CommonTeamRequest,CommonTeamSort} from './commonTeamsModel';
import './commonTeams.css';

type Props={catalog:Catalog;onClose:()=>void;onImport:(teamId:number)=>Promise<boolean>};

function Portrait({src,label,kind='servant'}:{src?:string|null;label:string;kind?:'servant'|'equip'|'class'}){
 const [failed,setFailed]=useState(false);useEffect(()=>setFailed(false),[src]);
 return <span className={`common-team-image ${kind}`} title={label}>{src&&!failed?<img src={src} alt={label} loading="lazy" draggable={false} onError={()=>setFailed(true)}/>:kind==='class'?<Shield size={27} aria-label={label}/>:<span className="common-team-image-fallback" aria-label={label}>{kind==='equip'?'◇':label==='空位'?'—':'?'}</span>}</span>;
}

function RosterSlot({member,position,servants,equips}:{member:CommonTeamMember|null;position:number;servants:Map<number,Servant>;equips:Map<number,Equip>}){
 const servant=member?servants.get(member.svtId):undefined,equip=member?equips.get(member.equipId):undefined;
 const name=member?(servant?.name||(member.svtId?`从者 #${member.svtId}`:member.support?'仅礼装助战':'未指定从者')):'空位';
 const equipName=member?.equipId?(equip?.name??`礼装 #${member.equipId}`):'未配置礼装';
 return <li className={`common-team-slot ${member?'':'empty'}`} aria-label={`位置 ${position}：${name}，${equipName}${member?.support?'，助战':''}`}>
  <div className="common-team-face"><Portrait src={servant?.image} label={name}/>{member?.support&&<span className="common-team-support">助战</span>}{member?.grandSvt&&<span className="common-team-grand" title="冠位从者" aria-label="冠位从者">冠</span>}</div>
  <span className="common-team-servant-name" title={name}>{name}</span>
  {member?.equipId?<Portrait kind="equip" src={equip?.image} label={equipName}/>:<span className="common-team-no-equip" title={equipName}>—</span>}
 </li>;
}

function TeamCard({team,servants,equips,importing,blocked,onImport}:{team:CommonTeam;servants:Map<number,Servant>;equips:Map<number,Equip>;importing:boolean;blocked:boolean;onImport:()=>void}){
 return <li className={`common-team-card ${importing?'importing':''}`}>
  <div className="common-team-card-heading"><div><h3 title={team.name}>{team.name}</h3><span className="common-team-author"><UserRound size={12} aria-hidden="true"/><span title={team.author}>{team.author}</span><code>#{team.id}</code></span></div><span className="common-team-votes" title={`${team.upvotes} 个赞，${team.downvotes} 个踩`}><ThumbsUp size={14} aria-hidden="true"/>{team.upvotes}</span></div>
  <ol className="common-team-roster">{team.servants.map((member,index)=><RosterSlot key={index} member={member} position={index+1} servants={servants} equips={equips}/>)}</ol>
  <div className="common-team-card-footer"><div className="common-team-metadata"><span><Sparkles size={13} aria-hidden="true"/>{team.turnCount===null?'回合未记录':`${team.turnCount} 回合`}</span><span><CalendarDays size={13} aria-hidden="true"/>{commonTeamDate(team.createdAt)}</span></div><button type="button" className="button primary common-team-import" disabled={blocked} aria-label={`导入 ${team.name}，Chaldea 队伍 ${team.id}`} onClick={onImport}>{importing?<LoaderCircle size={15} className="common-teams-spinner"/>:<Upload size={15}/>}<span>{importing?'正在导入…':'导入'}</span></button></div>
 </li>;
}

export function CommonTeamsDialog({catalog,onClose,onImport}:Props){
 const [directory,setDirectory]=useState<CommonTeamCatalog|null>(null),[catalogLoading,setCatalogLoading]=useState(true),[catalogError,setCatalogError]=useState(''),[catalogAttempt,setCatalogAttempt]=useState(0);
 const [classId,setClassId]=useState(''),[questId,setQuestId]=useState(''),[sort,setSort]=useState<CommonTeamSort>('upvotes');
 const [teams,setTeams]=useState<CommonTeam[]>([]),[page,setPage]=useState<CommonTeamPage|null>(null),[loading,setLoading]=useState(false),[listError,setListError]=useState(''),[importError,setImportError]=useState(''),[importing,setImporting]=useState<number|null>(null),[attempt,setAttempt]=useState(0);
 const mounted=useRef(false),generation=useRef(0),request=useRef<AbortController|null>(null),pending=useRef(false),importPending=useRef(false);
 const servants=useMemo(()=>new Map(catalog.servants.flatMap(item=>[[item.id,item] as const,...(item.aliases??[]).map(alias=>[Number(alias),item] as const)])),[catalog.servants]);
 const equips=useMemo(()=>new Map(catalog.equips.map(item=>[item.id,item])),[catalog.equips]);
 const quests=directory?.quests.filter(item=>item.classId===classId)??[],selectedQuest=quests.find(item=>item.id===questId);

 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;generation.current++;request.current?.abort();};},[]);
 useEffect(()=>{
  const controller=new AbortController();setCatalogLoading(true);setCatalogError('');
  void api('/common-teams/catalog','GET',undefined,controller.signal).then(value=>{
   if(!mounted.current||controller.signal.aborted)return;
   const next=readCommonTeamCatalog(value),firstClass=next.classes.find(item=>next.quests.some(quest=>quest.classId===item.id))!;
   setDirectory(next);setClassId(firstClass.id);setQuestId(next.quests.find(item=>item.classId===firstClass.id)!.id);
  }).catch(cause=>{if(mounted.current&&!controller.signal.aborted)setCatalogError((cause as Error).message||'无法读取冠位战副本目录，请点击“重试”。');}).finally(()=>{if(mounted.current&&!controller.signal.aborted)setCatalogLoading(false);});
  return()=>controller.abort();
 },[catalogAttempt]);

 useEffect(()=>{
  request.current?.abort();const sequence=++generation.current;pending.current=false;
  setTeams([]);setPage(null);setListError('');setImportError('');
  if(!questId){setLoading(false);return;}
  const controller=new AbortController();request.current=controller;pending.current=true;setLoading(true);
  const query={questId,sort,offset:0};
  void api(commonTeamQuery(query),'GET',undefined,controller.signal).then(value=>{
   if(!mounted.current||controller.signal.aborted||sequence!==generation.current)return;
   const result=readCommonTeamPage(value,query);setTeams(appendCommonTeams([],result.teams));setPage(result);
  }).catch(cause=>{if(mounted.current&&!controller.signal.aborted&&sequence===generation.current)setListError((cause as Error).message||'无法连接 Chaldea，队伍列表尚未获取。请检查网络后点击“重试”。');}).finally(()=>{
   if(mounted.current&&!controller.signal.aborted&&sequence===generation.current){pending.current=false;setLoading(false);}
  });
  return()=>controller.abort();
 },[questId,sort,attempt]);

 const loadMore=async()=>{
  if(pending.current||importPending.current||!page?.hasMore||page.nextOffset===null)return;
  const query:CommonTeamRequest={questId,sort,offset:page.nextOffset},sequence=generation.current,controller=new AbortController();
  request.current=controller;pending.current=true;setLoading(true);setListError('');
  try{
   const value=await api(commonTeamQuery(query),'GET',undefined,controller.signal);
   if(!mounted.current||controller.signal.aborted||sequence!==generation.current)return;
   const result=readCommonTeamPage(value,query);setTeams(current=>appendCommonTeams(current,result.teams));setPage(result);
  }catch(cause){if(mounted.current&&!controller.signal.aborted&&sequence===generation.current)setListError((cause as Error).message||'没有获取到更多队伍，已保留当前列表。请检查网络后点击“重试”。');}
  finally{if(mounted.current&&!controller.signal.aborted&&sequence===generation.current){pending.current=false;setLoading(false);}}
 };
 const changingFilter=()=>{request.current?.abort();generation.current++;pending.current=true;setLoading(true);setTeams([]);setPage(null);setListError('');setImportError('');};
 const selectClass=(id:string)=>{if(importPending.current||id===classId)return;changingFilter();setClassId(id);setQuestId(directory?.quests.find(item=>item.classId===id)?.id??'');};
 const importTeam=async(id:number)=>{
  if(importPending.current||pending.current)return;importPending.current=true;setImporting(id);setImportError('');
  try{const accepted=await onImport(id);if(mounted.current&&!accepted)setImportError('未切换方案，当前编辑内容已保留。请处理当前草稿的问题后重新导入。');}
  catch(cause){if(mounted.current)setImportError(`导入失败：${(cause as Error).message||'请检查网络后重试。'}`);}
  finally{importPending.current=false;if(mounted.current)setImporting(null);}
 };
 const locked=importing!==null,blocked=locked||loading;
 const retryList=()=>{if(page?.hasMore)void loadMore();else setAttempt(value=>value+1);};

 return <Modal title="常用队伍" wide onClose={()=>{if(!importPending.current)onClose();}} closeDisabled={locked}>
  <section className="common-teams-dialog" aria-label="查找 Chaldea 冠位战队伍" aria-busy={catalogLoading||loading||locked}>
   <div className="common-teams-intro"><Globe size={17} aria-hidden="true"/><span>Chaldea 共享队伍<span className="common-teams-intro-dot">·</span>选择冠位战副本，导入编队与战斗行动</span></div>
   {catalogLoading?<div className="common-teams-state"><LoaderCircle className="common-teams-spinner" size={28}/><strong>正在读取副本目录…</strong></div>:catalogError?<div className="common-teams-state error" role="alert"><AlertTriangle size={28}/><p>{catalogError}</p><button type="button" className="button" onClick={()=>setCatalogAttempt(value=>value+1)}><RefreshCw size={15}/>重试</button></div>:directory&&<>
    <div className="common-teams-filters">
     <div className="common-teams-class-label"><span>冠位职介</span><small>GRAND CLASS</small></div>
     <div className="common-teams-classes" aria-label="冠位职介筛选">{directory.classes.map(item=><button type="button" className={classId===item.id?'selected':''} disabled={locked||!directory.quests.some(quest=>quest.classId===item.id)} key={item.id} aria-pressed={classId===item.id} onClick={()=>selectClass(item.id)}><Portrait kind="class" src={catalog.uiAssets?.classes?.[GRAND_CLASS_ICONS[item.id]??item.id]} label=""/><span>{item.label}</span></button>)}</div>
     <div className="common-teams-query-row"><label className="field common-teams-quest"><span>冠位战副本</span><select aria-label="冠位战副本" value={questId} disabled={locked} onChange={event=>{changingFilter();setQuestId(event.target.value);}}>{quests.map(item=><option key={item.id} value={item.id}>{item.label}{item.difficulty&&!item.label.includes(item.difficulty)?` · ${item.difficulty}`:''}</option>)}</select></label><label className="field common-teams-sort"><span><ArrowDownWideNarrow size={14} aria-hidden="true"/>队伍排序</span><select aria-label="队伍排序" value={sort} disabled={locked} onChange={event=>{changingFilter();setSort(event.target.value as CommonTeamSort);}}><option value="upvotes">高赞优先</option><option value="created">最新发布</option></select></label></div>
    </div>
    <div className="common-teams-results-heading"><span>{selectedQuest?.label??'请选择副本'}</span><small role="status" aria-live="polite">{locked?'正在导入方案…':loading&&!page?'正在联网查询…':page?`已展示 ${teams.length} / ${page.total} 个队伍`:'按点赞数或发布时间查询'}</small></div>
    <div className="common-teams-results">
     {importError&&<div className="common-teams-error" role="alert"><AlertTriangle size={17} aria-hidden="true"/><span>{importError}</span></div>}
     {teams.length>0&&<ul className="common-teams-list">{teams.map(team=><TeamCard key={team.id} team={team} servants={servants} equips={equips} importing={importing===team.id} blocked={blocked} onImport={()=>void importTeam(team.id)}/>)}</ul>}
     {listError&&<div className="common-teams-error" role="alert"><AlertTriangle size={17} aria-hidden="true"/><span>{listError}</span><button type="button" className="button" disabled={blocked} onClick={retryList}><RefreshCw size={14}/>重试</button></div>}
     {loading&&!page&&<div className="common-teams-state"><LoaderCircle className="common-teams-spinner" size={30}/><strong>正在获取 Chaldea 队伍…</strong><p>每次最多获取 12 个队伍</p></div>}
     {!loading&&!listError&&page&&teams.length===0&&<div className="common-teams-state"><Users size={34} aria-hidden="true"/><strong>{page.hasMore?'这一页没有可展示的队伍':page.total?'暂时没有可展示的队伍':'这个副本暂时没有共享队伍'}</strong><p>{page.hasMore?'可以点击下方“加载更多队伍”，继续读取下一页。':'可以选择其他副本，或稍后再试。'}</p>{!page.hasMore&&<button type="button" className="button" disabled={locked} onClick={()=>setAttempt(value=>value+1)}><RefreshCw size={15}/>重新查询</button>}</div>}
     {page&&(teams.length>0||page.hasMore)&&!listError&&<div className="common-teams-pagination">{page.hasMore?<button type="button" className="button common-teams-load-more" disabled={blocked} onClick={()=>void loadMore()}>{loading?<LoaderCircle size={16} className="common-teams-spinner"/>:<CloudDownload size={16}/>}<span>{loading?'正在加载更多…':'加载更多队伍'}</span><small>每次最多 12 个</small></button>:<span>已展示全部队伍</span>}</div>}
    </div>
    <footer className="common-teams-footer"><Shield size={14} aria-hidden="true"/><p>导入后会进入编辑器草稿并检查方案；确认编队与行动后再保存。</p></footer>
   </>}
  </section>
 </Modal>;
}
