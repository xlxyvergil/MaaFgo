import {useEffect,useRef,useState} from 'react';
import {Check,Clipboard,Download,LoaderCircle,RefreshCw} from 'lucide-react';
import {api} from './api';
import {Modal} from './EditorModal';
import {titleOf} from './model';
import type {Catalog,Json} from './model';
import type {CardPreviewConfig} from './npCardRequest';
import {renderShareImage} from './shareImageRender';
import './shareImageExport.css';

type ExportedImage={blob:Blob;width:number;height:number;warnings:string[];url:string};
export function ShareImageExport({data,catalog,region,npCardPreview,costLimit,onClose}:{data:Json;catalog:Catalog;region:string;npCardPreview?:CardPreviewConfig;costLimit:number;onClose:()=>void}) {
 const [result,setResult]=useState<ExportedImage|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(true),[copying,setCopying]=useState(false),[copied,setCopied]=useState(false),[attempt,setAttempt]=useState(0);
 const active=useRef(true);
 useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
 useEffect(()=>{
  const controller=new AbortController();let current=true,url='';setBusy(true);setError('');setResult(null);setCopied(false);
  void (async()=>{
   try {
    const [encoded,validation]=await Promise.all([api('/share-image/encode','POST',{data},controller.signal),api('/validate','POST',{data},controller.signal)]);
    const image=await renderShareImage({data,catalog,encoded,region,cardPreviewConfig:npCardPreview,validation,costLimit,signal:controller.signal});
    if(!current||controller.signal.aborted)return;
    url=URL.createObjectURL(image.blob);setResult({...image,url});
   } catch(cause) {if(current&&!controller.signal.aborted)setError((cause as Error).message||'图片生成失败，请重试。');}
   finally{if(current)setBusy(false);}
  })();
  return()=>{current=false;controller.abort();if(url)URL.revokeObjectURL(url);};
 },[data,catalog,region,npCardPreview,costLimit,attempt]);
 const filename=`${Array.from(titleOf(data).replace(/[<>:"/\\|?*]|\p{Cc}/gu,'_').trim()||'MaaFGO编队').slice(0,70).join('')}_一图流.png`;
 const download=()=>{if(!result)return;const link=document.createElement('a');link.href=result.url;link.download=filename;link.click();};
 const copy=async()=>{
  if(!result||copying)return;setCopying(true);setCopied(false);setError('');
  try{if(!navigator.clipboard?.write||typeof ClipboardItem==='undefined')throw new Error('当前浏览器不支持复制图片，请下载 PNG 后分享。');await navigator.clipboard.write([new ClipboardItem({'image/png':result.blob})]);if(active.current)setCopied(true);}
  catch(cause){if(active.current)setError(`${(cause as Error).message} 可点击“下载 PNG”保存图片。`);}
  finally{if(active.current)setCopying(false);}
 };
 return <Modal title="导出编队与战斗一图流" wide onClose={onClose}><div className="modal-content share-export-dialog">
  <p className="share-export-description">包含编队、礼装、御主服和逐回合行动。保留底部识别区，可在“导入”中上传或粘贴图片，还原完整方案。</p>
  {busy&&<div className="share-export-loading" role="status"><LoaderCircle size={28} className="local-import-spinner"/><strong>正在生成一图流…</strong><span>正在排版，并加载本地头像、技能和卡图</span></div>}
  {error&&<p role="alert" className="share-export-error">{error}</p>}
  {result&&<><div className="share-export-image" aria-label="一图流图片预览"><img src={result.url} alt="编队与逐回合战斗行动一图流"/></div><div className="share-export-details"><span>PNG · {result.width} × {result.height} · {(result.blob.size/1024/1024).toFixed(2)} MiB</span><span>识别区含完整方案数据；不会额外加入本地保存路径或编辑器会话信息。</span></div>{result.warnings.length>0&&<div className="share-export-warnings"><strong>图片提示</strong><ul>{result.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul></div>}</>}
  {!busy&&!result&&<button className="button" onClick={()=>setAttempt(value=>value+1)}><RefreshCw size={16}/>重新生成</button>}
  <div className="share-export-actions"><button className="button" onClick={onClose}>关闭</button><span/><button className="button" disabled={!result||busy||copying} onClick={()=>void copy()}>{copied?<Check size={16}/>:<Clipboard size={16}/>} {copying?'正在复制…':copied?'已复制图片':'复制图片'}</button><button className="button primary" disabled={!result||busy} onClick={download}><Download size={16}/>下载 PNG</button></div>
 </div></Modal>;
}
