import {useEffect,useState} from 'react';
import {Search,Shield,X} from 'lucide-react';
import {servantFor} from './model';
import type {Catalog} from './model';

export function ServantPickerButton({value,catalog,label,placeholder,emptyHelp='打开图鉴选择从者',onChoose,onClear,clearLabel='清除从者选择'}:{value:unknown;catalog?:Catalog;label:string;placeholder:string;emptyHelp?:string;onChoose?:()=>void;onClear?:()=>void;clearLabel?:string}){
 const selected=value==null?'':String(value),empty=value==null||value===''||value==='0';
 const servant=!empty&&catalog?servantFor(catalog,Number(selected)):undefined;
 const [imageFailed,setImageFailed]=useState(false);
 useEffect(()=>setImageFailed(false),[servant?.image]);
 const name=empty?placeholder:servant?.name??`从者 #${selected}`;
 return <div className="strategy-servant-picker-row">
  <button type="button" className="strategy-servant-picker" aria-label={label} aria-haspopup="dialog" title={name} disabled={!onChoose} onClick={onChoose}>
   <span className="strategy-servant-avatar">{servant?.image&&!imageFailed?<img src={servant.image} alt="" onError={()=>setImageFailed(true)}/>:<Shield size={24} aria-hidden="true"/>}</span>
   <span className="strategy-servant-name"><strong>{name}</strong><small>{empty?emptyHelp:servant?`${servant.className??'从者'} · ID ${selected}`:`目录中暂无资料 · ID ${selected}`}</small></span>
   <Search size={18} aria-hidden="true"/>
  </button>
  {onClear&&value!=null&&<button type="button" className="icon strategy-servant-clear" aria-label={clearLabel} title={clearLabel} onClick={onClear}><X size={18} aria-hidden="true"/></button>}
 </div>;
}
