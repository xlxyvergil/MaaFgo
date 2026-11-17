import {useEffect,useMemo,useRef,useState} from 'react';
import {AlertTriangle,ArrowDownWideNarrow,ArrowUpWideNarrow,BookOpen,Copy,ExternalLink,FolderOpen,LoaderCircle,Plus,RefreshCw,Search,Star,Upload} from 'lucide-react';
import {Modal} from './EditorModal';
import type {Catalog,Servant} from './model';
import {BATTLE_LIBRARY_SORT_OPTIONS,battleLibraryTimeText,battleLibraryTitle,changeBattleLibrarySort,filterAndSortBattleLibrary,loadBattleLibraryPreferences,saveBattleLibraryPreferences} from './battleLibraryModel';
import type {BattleLibraryItem,BattleLibrarySortKey} from './battleLibraryModel';
import './battleLibrary.css';

export type BattleLibraryProps={
 catalog:Catalog;battles:BattleLibraryItem[];draftCount:number;battleDirectory?:string;busy?:boolean;warning?:string;
 query:string;onQuery:(value:string)=>void;onClose:()=>void;
 onOpen:(name:string)=>Promise<unknown>;onCopy:(name:string)=>Promise<unknown>;onNew:()=>Promise<unknown>;
 onImport:()=>void;onDrafts:()=>void;onRefresh:()=>Promise<void>;
 onFavorite:(name:string,favorite:boolean)=>Promise<{name:string;favorite:boolean}>;
};

function LibraryAvatar({servant}:{servant?:Servant}){
 const [failed,setFailed]=useState(false);useEffect(()=>setFailed(false),[servant?.image]);
 return <span className="avatar small" title={servant?.name}>{servant?.image&&!failed?<img src={servant.image} alt={servant.name} loading="lazy" onError={()=>setFailed(true)}/>:<span>{servant?.name?.slice(0,1)||'◇'}</span>}</span>;
}

export function BattleLibrary({catalog,battles,draftCount,battleDirectory,busy=false,warning,query,onQuery,onClose,onOpen,onCopy,onNew,onImport,onDrafts,onRefresh,onFavorite}:BattleLibraryProps){
 const [preferences,setPreferences]=useState(loadBattleLibraryPreferences),[error,setError]=useState(''),[action,setAction]=useState(''),[favorites,setFavorites]=useState<Set<string>>(()=>new Set());
 const mounted=useRef(false),actionPending=useRef(false),favoritePending=useRef(new Set<string>());
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 useEffect(()=>{saveBattleLibraryPreferences(preferences);},[preferences]);
 const rows=useMemo(()=>filterAndSortBattleLibrary(battles,query,preferences),[battles,query,preferences]);
 const servants=useMemo(()=>new Map(catalog.servants.flatMap(servant=>[[servant.id,servant] as const,...(servant.aliases??[]).map(alias=>[Number(alias),servant] as const)])),[catalog.servants]);
 const locked=busy||!!action;
 const run=async(name:string,operation:()=>Promise<unknown>)=>{
  if(actionPending.current||busy||(name==='refresh'&&favoritePending.current.size))return;
  actionPending.current=true;setAction(name);setError('');
  try{const result=await operation();if(mounted.current&&result===false)setError('方案未切换，当前编辑内容已保留。请处理提示后重试。');}
  catch(cause){if(mounted.current)setError((cause as Error).message||'方案操作未完成，请刷新方案库后重试。');}
  finally{actionPending.current=false;if(mounted.current)setAction('');}
 };
 const toggleFavorite=async(item:BattleLibraryItem)=>{
  if(favoritePending.current.has(item.name)||actionPending.current||busy)return;
  const next=item.favorite!==true;favoritePending.current.add(item.name);setFavorites(new Set(favoritePending.current));setError('');
  try{
   const result=await onFavorite(item.name,next);
   if(result.name!==item.name||result.favorite!==next)throw new Error('收藏结果与所选方案不一致，请刷新方案库后重试。');
  }catch(cause){if(mounted.current)setError(`收藏未更新：${(cause as Error).message||'请检查本地编辑服务后重试。'}`);}
  finally{favoritePending.current.delete(item.name);if(mounted.current)setFavorites(new Set(favoritePending.current));}
 };
 const directionLabel=preferences.sortKey==='name'?(preferences.direction==='asc'?'名称 A → Z':'名称 Z → A'):(preferences.direction==='desc'?'最新在前':'最早在前');

 return <Modal title="方案库" wide closeDisabled={locked} onClose={()=>{if(!actionPending.current&&!busy)onClose();}}>
  <section className="battle-library-body" aria-label="本地方案库">
   <div className="library-toolbar">
    <label className="search-field"><Search size={18}/><input value={query} onChange={event=>onQuery(event.target.value)} placeholder="搜索队名或文件名" aria-label="搜索方案"/></label>
    <button type="button" className="button" disabled={locked} onClick={onDrafts}><BookOpen size={16}/>草稿 {draftCount}</button>
    <button type="button" className="button" disabled={locked} onClick={onImport}><Upload size={16}/>导入</button>
    <button type="button" className="button primary" disabled={locked} onClick={()=>void run('new',onNew)}><Plus size={16}/>新建方案</button>
   </div>
   <div className="battle-library-order">
    <label><span>排序</span><select aria-label="方案排序" value={preferences.sortKey} onChange={event=>setPreferences(previous=>changeBattleLibrarySort(previous,event.target.value as BattleLibrarySortKey))}>{BATTLE_LIBRARY_SORT_OPTIONS.map(option=><option value={option.key} key={option.key}>{option.label}</option>)}</select></label>
    <button type="button" className="button battle-library-direction" aria-label={`切换排序方向，当前${directionLabel}`} title="切换升序 / 降序" onClick={()=>setPreferences(previous=>({...previous,direction:previous.direction==='asc'?'desc':'asc'}))}>{preferences.direction==='desc'?<ArrowDownWideNarrow size={15}/>:<ArrowUpWideNarrow size={15}/>}<span>{directionLabel}</span></button>
    <span className="battle-library-count"><Star size={12} aria-hidden="true"/>收藏优先<span>·</span>{rows.length} / {battles.length} 个方案</span>
   </div>
   {warning&&<div className="battle-library-message warning" role="status"><AlertTriangle size={16}/><span>{warning}</span></div>}
   {error&&<div className="battle-library-message error" role="alert"><AlertTriangle size={16}/><span>{error}</span></div>}
   <div className="library-list">
    {rows.map(item=>{
     const title=battleLibraryTitle(item),favorited=item.favorite===true,pending=favorites.has(item.name);
     return <div className={`library-item ${item.error?'damaged ':''}${favorited?'battle-library-favorited':''}`} key={item.name}>
      <div className="library-portraits">{(item.servants??[]).slice(0,3).map((id,index)=><LibraryAvatar key={index} servant={servants.get(id??0)}/>)}</div>
      <div className="library-info"><h3 title={title}>{title}</h3><span>{item.error?String(item.error):`${item.name} · ${item.turnCount??0} 回合 · ${battleLibraryTimeText(item,preferences.sortKey)}`}</span></div>
      <button type="button" className={`icon battle-library-favorite ${favorited?'active':''}`} aria-label={`${favorited?'取消收藏':'收藏'} ${title}`} aria-pressed={favorited} aria-busy={pending||undefined} title={pending?'正在保存收藏…':favorited?'取消收藏':'收藏方案并置顶'} disabled={locked||pending} onClick={()=>void toggleFavorite(item)}>{pending?<LoaderCircle size={19} className="battle-library-spinner"/>:<Star size={20} fill={favorited?'currentColor':'none'}/>}</button>
      <button type="button" className="icon battle-library-copy" title="复制到新草稿" aria-label={`复制 ${title}`} disabled={locked||!!item.error} onClick={()=>void run(`copy:${item.name}`,()=>onCopy(item.name))}><Copy size={17}/></button>
      <button type="button" className="button battle-library-open" disabled={locked||!!item.error} onClick={()=>void run(`open:${item.name}`,()=>onOpen(item.name))}>打开 <ExternalLink size={14}/></button>
     </div>;
    })}
    {!rows.length&&<div className="large-empty"><FolderOpen size={35}/><h3>{battles.length?'没有匹配的方案':'还没有本地战斗方案'}</h3><p>{battles.length?'可以调整搜索内容后重试。':'新建编队，或导入已有 Chaldea JSON。'}</p>{query&&<button type="button" className="button" onClick={()=>onQuery('')}>清除搜索</button>}</div>}
   </div>
   <div className="modal-bottom"><span className="muted">{battleDirectory}</span><button type="button" className="text-button" disabled={locked||favorites.size>0} onClick={()=>void run('refresh',onRefresh)}><RefreshCw size={14} className={action==='refresh'?'battle-library-spinner':''}/>刷新</button></div>
  </section>
 </Modal>;
}
