import {useEffect,useRef,useState} from 'react';
import {LoaderCircle,Upload} from 'lucide-react';
import './supportCEInstall.css';

export function SupportCEInstall({busy,disabled=false,common=false,onInstall}:{busy:boolean;disabled?:boolean;common?:boolean;onInstall:(file:File)=>Promise<string>}){
 const input=useRef<HTMLInputElement>(null),mounted=useRef(true),pending=useRef(false);
 const [message,setMessage]=useState(''),[error,setError]=useState('');
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const choose=async(file?:File)=>{
  if(!file||busy||disabled||pending.current)return;
  pending.current=true;setMessage('');setError('');
  try{
   const result=await onInstall(file);
   if(mounted.current)setMessage(result);
  }catch(cause){
   if(mounted.current)setError((cause as Error).message||'礼装扩展未能安装。请确认编队编辑器服务仍在运行，再次点击“安装礼装扩展”选择 ZIP 文件重试。');
  }finally{pending.current=false;}
 };
 return <span className="support-ce-install" aria-busy={busy||undefined}>
  <input ref={input} type="file" accept=".zip,application/zip" hidden tabIndex={-1} aria-label="选择礼装扩展 ZIP 压缩包" disabled={busy||disabled} onChange={event=>{const file=event.currentTarget.files?.[0];event.currentTarget.value='';void choose(file);}}/>
  <button type="button" className="text-button support-ce-install-button" disabled={busy||disabled} onClick={()=>input.current?.click()}>{busy?<LoaderCircle size={14} className="support-ce-install-spinner" aria-hidden="true"/>:<Upload size={14} aria-hidden="true"/>}{busy?'正在安装礼装扩展…':'安装礼装扩展'}</button>
  {error?<span className="support-ce-install-error" role="alert">{error}</span>:message?<span role="status">{message}{common?' 取消“常用礼装”可查看扩展礼装。':''}</span>:null}
 </span>;
}
