import {useEffect,useRef} from 'react';
import type {ReactNode} from 'react';
import {X} from 'lucide-react';
export function Modal({title,children,onClose,wide=false,closeDisabled=false}:{title:string;children:ReactNode;onClose:()=>void;wide?:boolean;closeDisabled?:boolean}) {
 const ref=useRef<HTMLDivElement>(null),closeRef=useRef(onClose),closeDisabledRef=useRef(closeDisabled);closeRef.current=onClose;closeDisabledRef.current=closeDisabled;
 useEffect(()=>{
  const old=document.activeElement as HTMLElement,el=ref.current;
  const focusable=()=>el?[...el.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]')].filter(item=>item.getClientRects().length>0):[];
  (el?.querySelector<HTMLElement>('[data-autofocus]:not([disabled])')??el?.querySelector<HTMLElement>('input:not([disabled]):not([hidden]),select:not([disabled]),textarea:not([disabled])')??focusable()[0])?.focus();
  const key=(e:KeyboardEvent)=>{
   if(e.key==='Escape'){if(closeDisabledRef.current)e.preventDefault();else closeRef.current();}
   if(e.key==='Tab'&&el){
    const elements=focusable();
    if(!elements.length){e.preventDefault();return;}
    const first=elements[0],last=elements[elements.length-1],active=document.activeElement as HTMLElement|null;
    if(!el.contains(active)||active?.matches(':disabled')){e.preventDefault();(e.shiftKey?last:first).focus();}
    else if(e.shiftKey&&active===first){e.preventDefault();last.focus();}
    else if(!e.shiftKey&&active===last){e.preventDefault();first.focus();}
   }
  };
  document.addEventListener('keydown',key);
  return()=>{document.removeEventListener('keydown',key);old?.focus();};
 },[]);
 return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget&&!closeDisabled)onClose();}}><div ref={ref} className={`modal ${wide?'wide':''}`} role="dialog" aria-modal="true" aria-label={title} aria-busy={closeDisabled||undefined}><header><div><span className="eyebrow">MAAFGO EDITOR</span><h2>{title}</h2></div><button className="icon" aria-label="关闭对话框" disabled={closeDisabled} title={closeDisabled?'正在处理，请稍候':undefined} onClick={onClose}><X size={20}/></button></header>{children}</div></div>;
}
