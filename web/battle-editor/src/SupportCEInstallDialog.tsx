import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,CloudDownload,ExternalLink,FolderOpen,LoaderCircle,PackageCheck} from 'lucide-react';
import {Modal} from './EditorModal';
import './supportCEInstallDialog.css';

const DOWNLOAD_URL='https://pan.quark.cn/s/e95d5e1d5056?pwd=P38F';
const ANNOUNCEMENTS_URL='https://space.bilibili.com/7442091/upload/opus';
const QR_IMAGE=new URL('./assets/support-ce-quark-qr.png',import.meta.url).href;

export function SupportCEInstallDialog({busy,disabled=false,common=false,onInstall,onClose}:{busy:boolean;disabled?:boolean;common?:boolean;onInstall:(file:File)=>Promise<string>;onClose:()=>void}){
 const input=useRef<HTMLInputElement>(null),mounted=useRef(true),pending=useRef(false);
 const [installing,setInstalling]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState(''),[fileName,setFileName]=useState('');
 const blocked=busy||installing;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const choose=async(file?:File)=>{
  if(!file||blocked||disabled||pending.current)return;
  pending.current=true;setInstalling(true);setMessage('');setError('');setFileName(file.name);
  try{
   const result=await onInstall(file);
   if(mounted.current)setMessage(result);
  }catch(cause){
   if(mounted.current)setError((cause as Error).message||'礼装扩展未能安装。请确认编队编辑器服务仍在运行，再点击“选择扩展包 ZIP”重新选择文件重试。');
  }finally{
   pending.current=false;
   if(mounted.current)setInstalling(false);
  }
 };
 return <Modal title="安装礼装扩展" onClose={onClose} closeDisabled={blocked}>
  <div className="modal-content support-ce-dialog">
   <p className="support-ce-dialog-intro">安装后可识别更多助战礼装。请先下载礼装扩展包 ZIP，再选择文件安装，无需自行解压。</p>
   <section className="support-ce-download" aria-label="获取礼装扩展包">
    <div className="support-ce-download-info">
     <h3><CloudDownload size={22} aria-hidden="true"/>获取礼装扩展包</h3>
     <p>在网盘中下载<strong>礼装扩展包（ZIP）</strong>，例如 <code>MaaFGO-助战礼装扩展.zip</code>。</p>
     <a className="button primary support-ce-quark-link" href={DOWNLOAD_URL} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} aria-hidden="true"/>夸克网盘：网盘下载</a>
     <p className="support-ce-extraction-code">提取码：<strong>P38F</strong></p>
    </div>
    <figure className="support-ce-download-qr">
     <img src={QR_IMAGE} width={196} height={192} alt="夸克网盘礼装扩展包下载二维码"/>
     <figcaption>也可扫码打开网盘</figcaption>
    </figure>
   </section>
   <p className="support-ce-announcements">如果网盘链接失效，请前往 <a href={ANNOUNCEMENTS_URL} target="_blank" rel="noopener noreferrer">UP 主主页的最新更新公告<ExternalLink size={12} aria-hidden="true"/></a>，获取最新的网盘地址。</p>
   <section className="support-ce-file-section" aria-label="安装已下载的扩展包">
    <h3><FolderOpen size={19} aria-hidden="true"/>安装已下载的扩展包</h3>
    <p>选择下载的礼装扩展 ZIP，编辑器会自动校验并安装，完成后刷新礼装列表。</p>
    <input ref={input} type="file" accept=".zip,application/zip" hidden tabIndex={-1} aria-label="选择礼装扩展 ZIP 压缩包" disabled={blocked||disabled} onChange={event=>{const file=event.currentTarget.files?.[0];event.currentTarget.value='';void choose(file);}}/>
    <button type="button" className="button primary support-ce-choose-file" data-autofocus disabled={blocked||disabled} onClick={()=>input.current?.click()}>{blocked?<LoaderCircle size={18} className="support-ce-install-spinner" aria-hidden="true"/>:<FolderOpen size={18} aria-hidden="true"/>}{blocked?'正在安装礼装扩展…':'选择扩展包 ZIP'}</button>
    {fileName&&<small className="support-ce-selected-file">已选择：{fileName}</small>}
    {disabled&&!blocked&&<p className="support-ce-dialog-error" role="alert">本地编辑服务未连接，暂时无法安装。请返回编辑器重新连接服务后重试。</p>}
    {error&&<p className="support-ce-dialog-error" role="alert">{error}</p>}
    {message&&<p className="support-ce-dialog-success" role="status"><PackageCheck size={18} aria-hidden="true"/><span>{message}{common?' 返回礼装列表后，取消“常用礼装”可查看扩展礼装。':''}</span></p>}
   </section>
   <div className="modal-bottom"><button type="button" className="button" disabled={blocked} onClick={onClose}><ArrowLeft size={16} aria-hidden="true"/>返回礼装选择</button></div>
  </div>
 </Modal>;
}
