import {useCallback,useEffect,useRef,useState} from 'react';
import type {DragEvent,MouseEvent,PointerEvent} from 'react';
import {attackCardDropTarget,attackCardTarget,finishAttackCardDrag,startAttackCardDrag,updateAttackCardDrag} from './attackCardDrag';
import type {AttackCardGesture} from './attackCardDrag';
import {partyFloatingTransform,partyGrabOffset} from './partyDrag';
interface Session {gesture:AttackCardGesture;element:HTMLElement;card:HTMLElement;group:HTMLElement;offset:{x:number;y:number};point:{x:number;y:number};ghost:HTMLElement|null;frame:number|null}
export function useAttackCardDrag({occupied,onSwap,resetKey,enabled=true}:{occupied:readonly boolean[];onSwap:(from:number,to:number)=>void;resetKey?:unknown;enabled?:boolean}) {
 const [dragged,setDragged]=useState<number|null>(null),[target,setTarget]=useState<number|null>(null);
 const pointer=useRef<Session|null>(null),native=useRef<{from:number;group:HTMLElement}|null>(null),suppress=useRef(false),blockedOrigin=useRef(false),latest=useRef({occupied,onSwap,enabled});latest.current={occupied,onSwap,enabled};
 const clear=useCallback((update=true)=>{
  const session=pointer.current;pointer.current=null;native.current=null;
  if(session){if(session.gesture.active)suppress.current=true;if(session.frame!=null)cancelAnimationFrame(session.frame);session.ghost?.remove();try{if(session.element.hasPointerCapture(session.gesture.pointerId))session.element.releasePointerCapture(session.gesture.pointerId);}catch{}}
  document.body.classList.remove('attack-pointer-drag-active');if(update){setDragged(null);setTarget(null);}
 },[]);
 const hit=useCallback((session:Session,x:number,y:number)=>{const card=document.elementFromPoint(x,y)?.closest<HTMLElement>('[data-attack-card]');return attackCardDropTarget(card?Number(card.dataset.attackCard):null,!!card&&card.closest('[data-attack-group]')===session.group);},[]);
 const move=useCallback((event:PointerEvent<HTMLElement>)=>{
  const session=pointer.current;if(!session||session.gesture.pointerId!==event.pointerId)return;
  if(!latest.current.enabled){clear();return;}
  session.point={x:event.clientX,y:event.clientY};session.gesture=updateAttackCardDrag(session.gesture,event.clientX,event.clientY,hit(session,event.clientX,event.clientY));if(!session.gesture.active)return;
  event.preventDefault();
  if(!session.ghost){
   const ghost=session.card.cloneNode(true) as HTMLElement,bounds=session.card.getBoundingClientRect();
   for(const node of [ghost,...ghost.querySelectorAll<HTMLElement>('*')]){node.removeAttribute('id');node.removeAttribute('data-attack-card');node.removeAttribute('data-attack-group');node.removeAttribute('draggable');if(node.matches('button,a,input,select,textarea,[tabindex]'))node.setAttribute('tabindex','-1');}
   ghost.classList.remove('attack-drag-placeholder','attack-drop-target');ghost.classList.add('attack-card-ghost');ghost.setAttribute('aria-hidden','true');ghost.setAttribute('inert','');ghost.inert=true;
   Object.assign(ghost.style,{position:'fixed',left:'0',top:'0',width:`${bounds.width}px`,height:`${bounds.height}px`,margin:'0',pointerEvents:'none',transform:partyFloatingTransform(session.point,session.offset)});document.body.appendChild(ghost);session.ghost=ghost;document.body.classList.add('attack-pointer-drag-active');
  }
  if(session.frame==null)session.frame=requestAnimationFrame(()=>{session.frame=null;if(pointer.current===session&&session.ghost)session.ghost.style.transform=partyFloatingTransform(session.point,session.offset);});
  setDragged(session.gesture.from);setTarget(session.gesture.target);
 },[clear,hit]);
 const end=useCallback((event:PointerEvent<HTMLElement>)=>{const session=pointer.current;if(!session||session.gesture.pointerId!==event.pointerId)return;move(event);if(pointer.current!==session)return;const result=latest.current.enabled?finishAttackCardDrag(session.gesture,latest.current.occupied):null;clear();if(result){event.preventDefault();latest.current.onSwap(...result);}},[clear,move]);
 useEffect(()=>{clear();},[clear,resetKey,enabled]);
 useEffect(()=>{const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&(pointer.current||native.current)){event.preventDefault();clear();}},blur=()=>clear(),hidden=()=>{if(document.hidden)clear();};window.addEventListener('keydown',escape);window.addEventListener('blur',blur);document.addEventListener('visibilitychange',hidden);return()=>{window.removeEventListener('keydown',escape);window.removeEventListener('blur',blur);document.removeEventListener('visibilitychange',hidden);clear(false);};},[clear]);
 const start=(index:number,event:PointerEvent<HTMLElement>)=>{
  if(!latest.current.enabled||event.button!==0||!event.isPrimary||pointer.current||blockedOrigin.current||event.target instanceof Element&&event.target.closest('[data-no-drag]')||attackCardTarget(index,latest.current.occupied,true)==null)return;
  const card=event.currentTarget.closest<HTMLElement>('[data-attack-card]'),group=card?.closest<HTMLElement>('[data-attack-group]');if(!card||!group)return;
  const point={x:event.clientX,y:event.clientY};suppress.current=false;native.current=null;pointer.current={gesture:startAttackCardDrag(event.pointerId,index,point.x,point.y),element:event.currentTarget,card,group,offset:partyGrabOffset(card.getBoundingClientRect(),point),point,ghost:null,frame:null};try{event.currentTarget.setPointerCapture(event.pointerId);}catch{pointer.current=null;}
 };
 const bindCard=(index:number)=>({
  onPointerDownCapture:(event:PointerEvent<HTMLElement>)=>{if(event.button===0&&event.isPrimary&&!pointer.current){suppress.current=false;blockedOrigin.current=event.target instanceof Element&&!!event.target.closest('[data-no-drag]');}},
  onPointerMove:move,onPointerUp:end,onPointerCancel:()=>clear(),onLostPointerCapture:(event:PointerEvent<HTMLElement>)=>{if(pointer.current?.gesture.pointerId===event.pointerId)clear();},
  onClickCapture:(event:MouseEvent<HTMLElement>)=>{if(suppress.current&&event.detail>0){event.preventDefault();event.stopPropagation();suppress.current=false;}},
  onDragStart:(event:DragEvent<HTMLElement>)=>{if(pointer.current||!latest.current.enabled||blockedOrigin.current||event.target instanceof Element&&event.target.closest('[data-no-drag]')||attackCardTarget(index,latest.current.occupied,true)==null){event.preventDefault();return;}const group=event.currentTarget.closest<HTMLElement>('[data-attack-group]');if(!group)return;native.current={from:index,group};event.dataTransfer.setData('text/plain',`attack-card:${index}`);event.dataTransfer.effectAllowed='move';const bounds=event.currentTarget.getBoundingClientRect();event.dataTransfer.setDragImage(event.currentTarget,event.clientX-bounds.left,event.clientY-bounds.top);setDragged(index);},
  onDragOver:(event:DragEvent<HTMLElement>)=>{const session=native.current;if(!latest.current.enabled||!session||attackCardTarget(session.from,latest.current.occupied,true)==null||attackCardDropTarget(index,session.group===event.currentTarget.closest('[data-attack-group]'))==null)return;event.preventDefault();event.dataTransfer.dropEffect='move';setTarget(index);},
  onDrop:(event:DragEvent<HTMLElement>)=>{const session=native.current;if(!session)return;event.preventDefault();const to=attackCardDropTarget(index,session.group===event.currentTarget.closest('[data-attack-group]')),valid=attackCardTarget(session.from,latest.current.occupied,true)!=null&&event.dataTransfer.getData('text/plain')===`attack-card:${session.from}`;clear();if(latest.current.enabled&&valid&&to!=null&&to!==session.from)latest.current.onSwap(session.from,to);},
  onDragEnd:()=>{if(!pointer.current)clear();},
 });
 return {dragged,target,start,bindCard,cancel:()=>clear()};
}
