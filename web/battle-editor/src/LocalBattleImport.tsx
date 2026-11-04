import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {AlertTriangle,ArrowLeft,FileJson,FolderOpen,LoaderCircle,RefreshCw,Search,Upload} from 'lucide-react';
import {api} from './api';
import {Modal} from './EditorModal';
import type {Json} from './model';

export type LocalBattleRecord={name:string;data:Json;hash:string;path:string};
type BattleListItem={name:string;title?:string;turnCount?:number;modifiedAt?:string;error?:string};
type LocalBattleImportProps={
 battleDirectory?:string;
 busy?:boolean;
 onBack:()=>void;
 onClose:()=>void;
 onImport:(record:LocalBattleRecord)=>Promise<boolean>;
};

function modifiedTime(value?:string){
 if(!value)return '时间未记录';
 const date=new Date(value);
 return Number.isNaN(date.getTime())?'时间未记录':date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}

export function LocalBattleImport({battleDirectory,busy=false,onBack,onClose,onImport}:LocalBattleImportProps){
 const [items,setItems]=useState<BattleListItem[]>([]),[directory,setDirectory]=useState(''),[query,setQuery]=useState('');
 const [loading,setLoading]=useState(true),[loaded,setLoaded]=useState(false),[importing,setImporting]=useState(''),[submitting,setSubmitting]=useState(false),[error,setError]=useState('');
 const mounted=useRef(false),listRequest=useRef(0),readRequest=useRef(0),importPending=useRef(false),parentBusy=useRef(busy);
 parentBusy.current=busy;

 const refresh=useCallback(async()=>{
  if(!mounted.current||importPending.current)return;
  const request=++listRequest.current;
  setLoading(true);setError('');
  try{
   const response=await api<{battles:BattleListItem[];directory?:string}>('/battles');
   if(!mounted.current||request!==listRequest.current)return;
   if(!Array.isArray(response.battles))throw new Error('方案列表返回格式异常，请重试。');
   setItems(response.battles.filter(item=>item&&typeof item.name==='string'));
   if(typeof response.directory==='string')setDirectory(response.directory);
   setLoaded(true);
  }catch(cause){
   if(mounted.current&&request===listRequest.current)setError((cause as Error).message||'无法读取本地方案，请确认本地服务仍在运行。');
  }finally{
   if(mounted.current&&request===listRequest.current)setLoading(false);
  }
 },[]);

 useEffect(()=>{
  mounted.current=true;void refresh();
  return()=>{mounted.current=false;listRequest.current++;readRequest.current++;};
 },[refresh]);

 const importBattle=async(item:BattleListItem)=>{
  if(item.error||importPending.current||parentBusy.current||loading)return;
  importPending.current=true;
  const request=++readRequest.current;
  setImporting(item.name);setError('');
  try{
   // Only the server-listed basename is sent. The service owns the fixed Battle
   // directory, file validation and containment; this UI accepts no input path.
   const record=await api<LocalBattleRecord>(`/battles/${encodeURIComponent(item.name)}`);
   if(!mounted.current||request!==readRequest.current)return;
   setSubmitting(true);
   const accepted=await onImport(record);
   if(mounted.current&&request===readRequest.current&&!accepted)setError('未切换方案，当前编辑内容已保留。请处理当前草稿的问题后重试。');
  }catch(cause){
   if(mounted.current&&request===readRequest.current)setError(`导入失败：${(cause as Error).message||'请刷新文件列表后重试。'}`);
  }finally{
   importPending.current=false;
   if(mounted.current&&request===readRequest.current){setImporting('');setSubmitting(false);}
  }
 };

 const filtered=useMemo(()=>{
  const search=query.trim().toLowerCase();
  return search?items.filter(item=>`${item.name} ${item.title??''}`.toLowerCase().includes(search)):items;
 },[items,query]);
 const committing=busy||submitting;
 const blocked=committing||loading||!!importing;
 const path=directory||battleDirectory||'config/Battle（等待本地服务确认）';
 const available=items.filter(item=>!item.error).length;

 return <Modal title="从 config/Battle 导入" wide onClose={onClose} closeDisabled={committing}>
  <section className="local-battle-import" aria-label="从当前 Battle 目录导入" aria-busy={loading||!!importing||committing}>
   <div className="local-battle-directory"><FolderOpen size={20} aria-hidden="true"/><div><strong>当前战斗方案目录</strong><code>{path}</code><p>选择一个文件导入独立草稿；保存沿用原名，替换前会再次确认。</p></div></div>
   <div className="local-battle-toolbar"><label className="search-field"><Search size={17} aria-hidden="true"/><input autoFocus aria-label="搜索本地战斗方案" placeholder="搜索文件名或队名" value={query} onChange={event=>setQuery(event.target.value)}/></label><button type="button" className="button" disabled={blocked} onClick={()=>void refresh()}><RefreshCw size={16} className={loading?'local-import-spinner':''}/>刷新</button></div>
   {error&&<div className="local-battle-error" role="alert"><AlertTriangle size={17} aria-hidden="true"/><span>{error}</span></div>}
   <div className="local-battle-status" role="status" aria-live="polite">{committing?<><LoaderCircle size={15} className="local-import-spinner"/>正在提交草稿并切换，请稍候…</>:importing?<><LoaderCircle size={15} className="local-import-spinner"/>正在读取 {importing}…</>:loading?<><LoaderCircle size={15} className="local-import-spinner"/>正在读取本地文件列表…</>:loaded?`共 ${items.length} 个文件 · ${available} 个可导入${query.trim()?` · 匹配 ${filtered.length} 个`:''}`:'读取失败，可点击刷新重试。'}</div>
   <div className="local-battle-results">
    {filtered.length>0?<ul className="local-battle-list">{filtered.map(item=><li className={`local-battle-item ${item.error?'damaged':''}`} key={item.name}>
     <FileJson size={24} className="local-battle-file-icon" aria-hidden="true"/>
     <div className="local-battle-file-info"><h3>{item.title||item.name}</h3><code>{item.name}</code><div className="local-battle-file-meta"><span>{Number.isFinite(item.turnCount)?`${item.turnCount} 回合`:'回合未记录'}</span><time dateTime={item.modifiedAt}>{modifiedTime(item.modifiedAt)}</time></div>{item.error&&<p className="local-battle-damaged"><AlertTriangle size={13} aria-hidden="true"/>无法导入：{item.error}</p>}</div>
     <button type="button" className="button primary local-battle-import-button" disabled={blocked||!!item.error} title={item.error?'请先修复该文件后刷新列表':undefined} aria-label={`导入 ${item.name} 为独立草稿`} onClick={()=>void importBattle(item)}>{importing===item.name?<LoaderCircle size={15} className="local-import-spinner"/>:<Upload size={15}/>}<span>{importing===item.name?'正在导入…':'导入草稿'}</span></button>
    </li>)}</ul>:!loading&&loaded?<div className="local-battle-empty"><FolderOpen size={32} aria-hidden="true"/><strong>{items.length?'没有匹配的方案':'当前目录还没有 JSON 方案'}</strong><p>{items.length?'试试其他队名或文件名。':'可返回导入页，上传其他目录中的 Chaldea JSON。'}</p>{items.length>0&&<button type="button" className="text-button" onClick={()=>setQuery('')}>清空搜索</button>}</div>:null}
   </div>
   <footer className="local-battle-footer"><button type="button" className="button" disabled={committing} onClick={onBack}><ArrowLeft size={15}/>返回导入页</button><p>其他目录的 JSON，可返回后选择本地文件上传。</p></footer>
  </section>
 </Modal>;
}
