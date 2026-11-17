import {LoaderCircle,Upload} from 'lucide-react';
import './supportCEInstall.css';

export function SupportCEInstall({busy,onOpen}:{busy:boolean;onOpen:()=>void}){
 return <span className="support-ce-install" aria-busy={busy||undefined}>
  <button type="button" className="text-button support-ce-install-button" disabled={busy} onClick={onOpen}>{busy?<LoaderCircle size={14} className="support-ce-install-spinner" aria-hidden="true"/>:<Upload size={14} aria-hidden="true"/>}{busy?'正在安装礼装扩展…':'安装礼装扩展'}</button>
 </span>;
}
