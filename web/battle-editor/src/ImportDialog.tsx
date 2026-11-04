import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,ChevronRight,Clipboard,FolderOpen,ImagePlus,Link,LoaderCircle,Upload,X} from 'lucide-react';
import {Modal} from './EditorModal';
import {api,apiImage} from './api';
import {members} from './model';
import type {Catalog,Json} from './model';
import {clipboardImage,IMAGE_ACCEPT,imageMime,readImageImportResult,screenshotTeam,shareImagePreview} from './screenshotImportModel';
import type {ScreenshotResult,ScreenshotSlot,RestoredImageResult} from './screenshotImportModel';
import {BBC_FILE_ACCEPT,bbcPreview,grandRequirementPreview,readBBCFile,readBBCImportResult} from './bbcImportModel';
import type {BBCImportResult} from './bbcImportModel';
import './importDialog.css';

export function ImportDialog({catalog,battleDirectory,onClose,onDirectory,onJSON,onShare,onImage,onBBC,onShareImage}:{catalog:Catalog;battleDirectory?:string;onClose:()=>void;onDirectory:()=>void;onJSON:()=>void;onShare:(source:string)=>Promise<boolean>;onImage:(data:Json)=>Promise<boolean>;onBBC:(data:Json,validation:Json|null)=>Promise<boolean>;onShareImage:(data:Json,validation:Json|null,kind:'share'|'chaldea')=>Promise<boolean>}) {
 const [source,setSource]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[submitting,setSubmitting]=useState(false),[preview,setPreview]=useState(''),[result,setResult]=useState<ScreenshotResult|null>(null),[editing,setEditing]=useState<{slot:number;type:'servant'|'equip'}|null>(null),[query,setQuery]=useState('');
 const [bbcResult,setBBCResult]=useState<BBCImportResult|null>(null),[loadingBBC,setLoadingBBC]=useState(false);
 const [shareResult,setShareResult]=useState<RestoredImageResult|null>(null);
 const errorRef=useRef<HTMLParagraphElement>(null);
 const input=useRef<HTMLInputElement>(null),mounted=useRef(true),pending=useRef(false),request=useRef<AbortController|null>(null),currentFile=useRef<File|null>(null),url=useRef('');
 const bbcInput=useRef<HTMLInputElement>(null),bbcActive=useRef(false);
 const loadRef=useRef<(file:File)=>Promise<void>>(async()=>{});
 useEffect(()=>{if(error)errorRef.current?.focus();},[error]);
 useEffect(()=>{mounted.current=true;const paste=(event:ClipboardEvent)=>{if(bbcActive.current)return;try{const file=clipboardImage(event.clipboardData?.items??[]);if(file){event.preventDefault();void loadRef.current(file);}}catch(cause){event.preventDefault();setError((cause as Error).message);}};document.addEventListener('paste',paste);return()=>{mounted.current=false;request.current?.abort();if(url.current)URL.revokeObjectURL(url.current);document.removeEventListener('paste',paste);};},[]);
 const recognize=async(file:File)=>{
  if(pending.current){setError('请等待当前识别完成后再选择图片。');return;}
  let mime:string;try{mime=imageMime(file);}catch(cause){setError((cause as Error).message);return;}
  pending.current=true;setBusy(true);setError('');setResult(null);setShareResult(null);setBBCResult(null);setEditing(null);currentFile.current=file;
  if(url.current)URL.revokeObjectURL(url.current);url.current=URL.createObjectURL(file);setPreview(url.current);
  const controller=new AbortController();request.current=controller;
  try{const value=readImageImportResult(await apiImage(file,mime,controller.signal));if(mounted.current&&!controller.signal.aborted){if(value.kind==='share'||value.kind==='chaldea')setShareResult(value);else setResult(value);}}
  catch(cause){if(mounted.current&&!controller.signal.aborted)setError((cause as Error).message||'截图识别失败，请重试。');}
  finally{pending.current=false;if(mounted.current)setBusy(false);}
 };
 loadRef.current=recognize;
 const pasteButton=async()=>{
  try{if(!navigator.clipboard?.read)throw new Error('请在本弹窗内按 Ctrl+V 粘贴图片。');const entries=await navigator.clipboard.read();const item=entries.find(entry=>entry.types.some(type=>type.startsWith('image/')));if(!item)throw new Error('剪贴板中没有图片，请先复制截图，再按 Ctrl+V。');const type=item.types.find(type=>['image/png','image/jpeg','image/webp'].includes(type));if(!type)throw new Error('支持 PNG、JPG/JPEG、WebP 静态图片。');const blob=await item.getType(type);if(mounted.current)await recognize(new File([blob],'剪贴板截图',{type}));}
  catch(cause){if(mounted.current)setError(`${(cause as Error).message} 若浏览器未允许读取，请直接按 Ctrl+V。`);}
 };
 const update=(index:number,changes:Partial<ScreenshotSlot>)=>setResult(old=>old?{...old,slots:old.slots.map((s,i)=>i===index?{...s,...changes}:s)}:old);
 const chooseSupport=(index:number,checked:boolean)=>setResult(old=>old?{...old,slots:old.slots.map((s,i)=>({...s,support:i===index?checked:false}))}:old);
 const commit=async()=>{
  if(!result||pending.current)return;
  try{const data=screenshotTeam(result.slots,catalog);pending.current=true;setSubmitting(true);setError('');const accepted=await onImage(data);if(mounted.current&&!accepted)setError('当前草稿尚未安全保存，已保留原方案。请处理提示后重试。');}
  catch(cause){if(mounted.current)setError((cause as Error).message);}
  finally{pending.current=false;if(mounted.current)setSubmitting(false);}
 };
 const importShare=async()=>{if(pending.current)return;pending.current=true;setSubmitting(true);setError('');try{const ok=await onShare(source.trim());if(mounted.current&&!ok)setError('未切换方案，请先处理当前草稿提示。');}catch(cause){if(mounted.current)setError((cause as Error).message);}finally{pending.current=false;if(mounted.current)setSubmitting(false);}};
 const importBBC=async(file:File)=>{
  if(pending.current)return;
  pending.current=true;bbcActive.current=true;setBusy(true);setLoadingBBC(true);setError('');setBBCResult(null);setShareResult(null);setPreview('');currentFile.current=null;if(url.current){URL.revokeObjectURL(url.current);url.current='';}
  try{const config=await readBBCFile(file);if(!mounted.current)return;const converted=readBBCImportResult(await api('/import-bbc','POST',{config,name:file.name}));if(mounted.current)setBBCResult(converted);}
  catch(cause){if(mounted.current)setError((cause as Error).message||'BBC 配置转换失败，请检查配置。');bbcActive.current=false;}
  finally{pending.current=false;if(mounted.current){setBusy(false);setLoadingBBC(false);}}
 };
 const commitBBC=async()=>{
  if(!bbcResult||pending.current)return;
  pending.current=true;setSubmitting(true);setError('');
  try{const accepted=await onBBC(bbcResult.data,bbcResult.validation);if(mounted.current&&!accepted)setError('当前草稿尚未安全保存，已保留原方案。请处理提示后重试。');}
  catch(cause){if(mounted.current)setError((cause as Error).message);}
  finally{pending.current=false;if(mounted.current)setSubmitting(false);}
 };
 const commitShareImage=async()=>{
  if(!shareResult||pending.current)return;
  pending.current=true;setSubmitting(true);setError('');
  try{const accepted=await onShareImage(shareResult.data,shareResult.validation,shareResult.kind);if(mounted.current&&!accepted)setError('当前草稿尚未安全保存，已保留原方案。请处理提示后重试。');}
  catch(cause){if(mounted.current)setError((cause as Error).message);}
  finally{pending.current=false;if(mounted.current)setSubmitting(false);}
 };
 const bbc=bbcResult?bbcPreview(bbcResult.data,catalog):null;
 const shared=shareResult?(()=>{const preview=shareImagePreview(shareResult.data,catalog),roster=members(shareResult.data);return {...preview,roster:preview.roster.map(slot=>({...slot,...grandRequirementPreview(roster[slot.slot])}))};})():null;
 const restoredTitle=shareResult?.kind==='chaldea'&&!shareResult.data.team?.name?`Chaldea 队伍 #${shareResult.teamId}`:shared?.title;
 const bbcIssues:Json[]=Array.isArray(bbcResult?.validation?.issues)?bbcResult.validation.issues.filter((issue:Json)=>issue.code!=='bbc_conversion_warning'):[];
 const showingResult=!!result||!!bbcResult||!!shareResult;
 const items=editing?(editing.type==='servant'?catalog.servants:catalog.equips.filter(e=>e.recognizable===true)).filter(item=>`${item.name} ${item.id} ${item.nameJp??''}`.toLowerCase().includes(query.trim().toLowerCase())).slice(0,40):[];
 return <Modal title={shareResult?(shareResult.kind==='chaldea'?'核对 Chaldea 队伍':'核对一图流内容'):bbcResult?'核对 BBC 转换结果':result?'核对截图识别结果':'导入战斗方案'} wide={showingResult} closeDisabled={submitting} onClose={onClose}><div className="modal-content import-dialog">
  {!showingResult&&<><button className="import-config-entry" data-autofocus="" disabled={busy||submitting} onClick={onDirectory}><span className="import-entry-icon"><FolderOpen size={27}/></span><span><strong>从 config/Battle 选择</strong><small>导入工程中已有的战斗方案</small></span><ChevronRight size={22}/></button><p className="import-config-path">{battleDirectory??'当前 MaaFGO 实例的 config/Battle 目录'}</p></>}
  {result&&<button autoFocus className="text-button" disabled={submitting} onClick={()=>{setResult(null);setEditing(null);setError('');}}><ArrowLeft size={16}/>返回导入方式</button>}
  {!showingResult&&<section className="import-image-section" aria-label="从编队截图导入"><div className="import-section-heading"><ImagePlus size={20}/><strong>从截图 / 一图流导入</strong><span>自动识别类型</span></div><div className="import-image-drop" tabIndex={0} aria-label="编队截图粘贴区，按 Ctrl+V 粘贴" onDragOver={e=>{e.preventDefault();}} onDrop={e=>{e.preventDefault();if(e.dataTransfer.files.length!==1){setError('请每次上传一张编队截图或一图流。');return;}void recognize(e.dataTransfer.files[0]);}}><p>拖入图片，或在本弹窗按 <kbd>Ctrl+V</kbd> 粘贴</p><div><button className="button" disabled={busy||submitting} onClick={()=>input.current?.click()}><Upload size={16}/>上传图片</button><button className="button" disabled={busy||submitting} onClick={()=>void pasteButton()}><Clipboard size={16}/>粘贴图片</button></div><small>PNG、JPG/JPEG、WebP · 静态图片 · 最大16 MiB</small></div><p className="import-help">支持三类图片：游戏六人编队确认页、本工具一图流、Chaldea 分享图。前两类可离线识别；Chaldea 图片在本机识别底部 Team ID 后联网获取完整方案，原图不会上传。请保留游戏从者及礼装区域、一图流回导标记或 Chaldea 页脚和 Team ID。</p></section>}
  {loadingBBC&&<p className="import-bbc-loading" role="status"><LoaderCircle size={18} className="local-import-spinner"/>正在离线转换 BBC 配置…</p>}
  {preview&&<figure className="import-image-preview"><img src={preview} alt="待识别的编队截图或一图流"/>{busy&&<figcaption role="status"><LoaderCircle size={18} className="local-import-spinner"/>正在定位图片类型；Chaldea 图片识别 Team ID 后将联网获取方案…</figcaption>}{!busy&&!result&&!shareResult&&<button className="button" disabled={submitting} onClick={()=>currentFile.current&&void recognize(currentFile.current)}>重新识别</button>}</figure>}
  {error&&<p ref={errorRef} tabIndex={-1} className="import-error" role="alert">{error}</p>}
  {shareResult&&shared&&<>
   <button autoFocus className="text-button" disabled={submitting} onClick={()=>{setShareResult(null);setError('');}}><ArrowLeft size={16}/>返回导入方式</button>
   <section className="import-bbc-summary" aria-label={shareResult.kind==='chaldea'?'Chaldea 图片导入摘要':'一图流恢复摘要'}><h3>{shareResult.title||restoredTitle}</h3>{shareResult.kind==='chaldea'?<p>已识别 Team ID：<strong>{shareResult.teamId}</strong>，并获取线上完整方案。<a href={shareResult.source} target="_blank" rel="noreferrer">打开 Chaldea 分享</a></p>:<p>已从本工具一图流恢复完整方案。</p>}<div><span>{shared.turns} 回合</span><span>{shared.skills} 次技能</span><span>{shared.cards} 张指定出卡</span><span>{shared.mystic}</span></div></section>
   <div className="screenshot-slot-grid">{shared.roster.map(slot=><article className={`screenshot-slot ${slot.support?'support':''}`} key={slot.slot}><header><strong>{slot.slot+1} · {slot.slot<3?'前排':'候补'}</strong>{slot.grand&&<span className="import-bbc-support">冠位</span>}{slot.support&&<span className="import-bbc-support">{slot.supportLabel}</span>}</header><div className="screenshot-person">{slot.image?<img src={slot.image} alt=""/>:<span className="screenshot-placeholder">{slot.support?'助':slot.name==='不要求此位置'?'空':'?'}</span>}<div><strong>{slot.name}</strong></div></div>{slot.bondEffect&&<div className="screenshot-equip"><span>{slot.bondEffect}</span></div>}<div>{slot.equips.length?slot.equips.map((equip,index)=><div className="screenshot-equip" key={index}>{equip.image&&<img src={equip.image} alt=""/>}<span>{equip.name}</span></div>):<div className="screenshot-equip"><span>{slot.emptyEquipLabel}</span></div>}</div></article>)}</div>
   {shareResult.warnings.length>0&&<section className="import-bbc-notices has-warnings" aria-label="一图流导入提示"><strong>图片导入提示（{shareResult.warnings.length}）</strong><ul>{shareResult.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul></section>}
   {Array.isArray(shareResult.validation?.issues)&&shareResult.validation.issues.length>0&&<section className="import-bbc-validation" aria-label="一图流方案检查"><strong>方案检查</strong><ul>{shareResult.validation.issues.map((issue:Json,index:number)=><li className={issue.severity==='error'?'blocking':''} key={index}>{issue.message}</li>)}</ul></section>}
   <p className="import-help">确认后建立独立草稿，保留从者、礼装、养成要求、御主服、完整回合及换人配置。{shareResult.kind==='chaldea'?'方案内容来自该 Team ID 对应的线上分享，请核对识别出的 ID 和编队。':'图片中的分享标记用于恢复数据。'}请检查方案提示后继续编辑和保存。</p>
   <button className="button primary full" disabled={submitting} onClick={()=>void commitShareImage()}>{submitting?'正在导入…':'确认并导入完整方案'}</button>
  </>}
  {bbcResult&&bbc&&<>
   <button autoFocus className="text-button" disabled={submitting} onClick={()=>{setBBCResult(null);bbcActive.current=false;setError('');}}><ArrowLeft size={16}/>返回导入方式</button>
   <section className="import-bbc-summary" aria-label="BBC 转换摘要"><h3>{bbc.title}</h3><p>{bbcResult.sourceName}</p><div><span>{bbc.turns} 回合</span><span>{bbc.skills} 次技能</span><span>{bbc.cards} 张指定出卡</span><span>{bbc.mystic}</span></div></section>
   <div className="screenshot-slot-grid bbc-slot-grid">{bbc.roster.map(slot=><article className={`screenshot-slot ${slot.support?'support':''}`} key={slot.slot}><header><strong>{slot.slot+1} · {slot.slot<3?'前排':'候补'}</strong>{slot.grand&&<span className="import-bbc-support">冠位</span>}{slot.support&&<span className="import-bbc-support">助战</span>}</header><div className="screenshot-person">{slot.image?<img src={slot.image} alt=""/>:<span className="screenshot-placeholder">{slot.support?'助':slot.name==='不要求此位置'?'空':'?'}</span>}<div><strong>{slot.name}</strong></div></div>{slot.bondEffect&&<div className="screenshot-equip"><span>{slot.bondEffect}</span></div>}<div className="bbc-equip-list">{slot.equips.length?slot.equips.map((equip,index)=><div className="screenshot-equip" key={index}>{equip.image&&<img src={equip.image} alt=""/>}<span>{equip.name}</span></div>):<div className="screenshot-equip"><span>{slot.emptyEquipLabel}</span></div>}</div></article>)}</div>
   <section className={`import-bbc-notices ${bbcResult.warnings.length?'has-warnings':''}`} aria-label="BBC 转换提示"><strong>{bbcResult.warnings.length?`转换提示（${bbcResult.warnings.length}）`:'BBC 配置转换完成'}</strong>{bbcResult.warnings.length?<ul>{bbcResult.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>:<p>未发现需要额外处理的转换项目。</p>}</section>
   {bbcIssues.length>0&&<section className="import-bbc-validation" aria-label="导入方案检查"><strong>方案检查</strong><ul>{bbcIssues.map((issue:Json,index:number)=><li className={issue.severity==='error'?'blocking':''} key={index}>{issue.message}</li>)}</ul></section>}
   <p className="import-help">确认后建立独立的 Chaldea 格式草稿，可继续编辑编队和回合；原 BBC 文件保持不变，保存方案时另建文件。</p>
   <button className="button primary full" disabled={submitting} onClick={()=>void commitBBC()}>{submitting?'正在导入…':'确认并导入 BBC 草稿'}</button>
  </>}
  {result&&<><p className="import-help">已完成识别（{((result.elapsedMs??0)/1000).toFixed(1)}秒）。请核对后导入，可在这里更换识别结果；未识别项目不会自动填写。</p><div className="screenshot-slot-grid">{result.slots.map((slot,i)=>{const servant=catalog.servants.find(s=>s.id===slot.servantId),equip=catalog.equips.find(e=>e.id===slot.equipId);return <article key={i} className={`screenshot-slot ${slot.support?'support':''}`}><header><strong>{i+1} · {i<3?'前排':'候补'}</strong><label><input type="checkbox" aria-label={`槽位 ${i+1} 为助战`} disabled={submitting} checked={slot.support} onChange={e=>chooseSupport(i,e.target.checked)}/>助战</label></header><div className="screenshot-person">{servant?.image?<img src={servant.image} alt={servant.name}/>:<span className="screenshot-placeholder">{slot.support?'助':slot.equipId?'?':slot.status==='empty'?'空':'?'}</span>}<div><strong>{servant?.name??(slot.support?'助战（仅要求礼装）':slot.equipId?'未选择从者':slot.status==='empty'?'不要求此位置':'未识别从者')}</strong><button className="text-button" disabled={submitting} onClick={()=>{setEditing({slot:i,type:'servant'});setQuery('');}}>选择 / 更换从者</button></div></div><div className="screenshot-equip">{equip?.image&&<img src={equip.image} alt={equip.name}/>}<span>{equip?.name??(slot.equipStatus==='unknown'?'未识别礼装':'不要求礼装')}</span>{slot.equipId&&<button className="icon" aria-label={`清除槽位 ${i+1} 截图礼装`} disabled={submitting} onClick={()=>update(i,{equipId:null,equipStatus:'empty'})}><X size={15}/></button>}</div><div className="screenshot-slot-tools"><button className="text-button" disabled={submitting} onClick={()=>{setEditing({slot:i,type:'equip'});setQuery('');}}>选择 / 更换礼装</button><button className="text-button" disabled={submitting} onClick={()=>update(i,{servantId:null,equipId:null,status:'empty',equipStatus:'empty',support:false})}>不要求此位置</button></div></article>;})}</div>
   {editing&&<section className="screenshot-picker" aria-label={`更正槽位 ${editing.slot+1} ${editing.type==='servant'?'从者':'礼装'}`}><div><strong>槽位 {editing.slot+1} · 选择{editing.type==='servant'?'从者':'礼装'}</strong><button className="icon" aria-label="关闭截图选择器" onClick={()=>setEditing(null)}><X size={17}/></button></div><input autoFocus aria-label="搜索截图识别图鉴" placeholder="名称 / ID" value={query} onChange={e=>setQuery(e.target.value)}/><div className="screenshot-picker-items">{items.map(item=><button key={item.id} disabled={submitting} onClick={()=>{update(editing.slot,editing.type==='servant'?{servantId:item.id,status:'identified'}:{equipId:item.id,equipStatus:'identified'});setEditing(null);}}>{item.image&&<img src={item.image} alt=""/>}<span>{item.name}<small>{item.id}</small></span></button>)}{!items.length&&<p>没有找到对应项目。</p>}</div><small>只展示前40项，请输入名称或ID缩小范围；礼装只列出工程可识别的资源。</small></section>}
   {result.warnings.length>0&&<p className="import-help">原始识别提示：{result.warnings.join(' ')} 已更正的项目以当前选择为准。</p>}<p className="import-help">仅导入从者、礼装和助战要求，不识别等级、技能、宝具、冠位或御主服；请在编队页补充。图片只在本机处理。</p><button className="button primary full" disabled={submitting} onClick={()=>void commit()}>{submitting?'正在导入…':'确认并导入编队草稿'}</button></>}
  {!showingResult&&<><section className="import-share"><label className="field"><span>Chaldea 分享链接 / ID</span><div><input aria-label="Chaldea 分享链接 / ID" value={source} disabled={busy||submitting} onChange={e=>setSource(e.target.value)} placeholder="粘贴分享链接或 ID"/><button className="button" disabled={!source.trim()||busy||submitting} onClick={()=>void importShare()}><Link size={16}/>导入分享</button></div></label><small>线上分享和 Chaldea 图片导入需要联网；游戏截图、本工具一图流和本地方案可离线导入。</small></section><section className="import-files" aria-label="从文件导入"><div><button className="button" disabled={busy||submitting} onClick={onJSON}><Upload size={16}/>导入 Chaldea JSON</button><button className="button" disabled={busy||submitting} onClick={()=>bbcInput.current?.click()}><Upload size={16}/>导入 BBC 配置</button></div><small>JSON 文件 · BBC 配置最大3 MiB · 导入独立草稿，不覆盖原文件。</small></section></>}
  <input ref={input} type="file" className="visually-hidden" accept={IMAGE_ACCEPT} aria-label="上传编队截图" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void recognize(file);}}/>
  <input ref={bbcInput} type="file" className="visually-hidden" accept={BBC_FILE_ACCEPT} aria-label="上传 BBC JSON 配置" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void importBBC(file);}}/>
 </div></Modal>;
}
