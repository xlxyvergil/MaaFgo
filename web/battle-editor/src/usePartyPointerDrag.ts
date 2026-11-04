import {useCallback,useEffect,useRef,useState} from 'react';
import type {PointerEvent as ReactPointerEvent} from 'react';
import {finishPartyDrag,partyDragTarget,partyFloatingTransform,partyGrabOffset,startPartyDrag,updatePartyDrag} from './partyDrag';
import type {PartyDragGesture,PartyDragPoint} from './partyDrag';
import type {PartyCardDragBindings} from './PartyCard';

interface PointerSession {
 gesture:PartyDragGesture;element:HTMLElement;card:HTMLElement;board:HTMLElement;
 offset:PartyDragPoint;point:PartyDragPoint;width:number;height:number;ghost:HTMLElement|null;frame:number|null;
}
interface NativeSession {from:number;board:HTMLElement}
export interface UsePartyPointerDragOptions {onExchange:(from:number,to:number)=>void;resetKey?:unknown;enabled?:boolean}

/** The detached mirror is visual only. It cannot become a drop target or focus stop. */
function createMirror(session:PointerSession):HTMLElement {
 const mirror=session.card.cloneNode(true) as HTMLElement;
 for(const node of [mirror,...mirror.querySelectorAll<HTMLElement>('*')]){
  node.removeAttribute('id');node.removeAttribute('data-slot');
  if(node.hasAttribute('draggable'))node.setAttribute('draggable','false');
  if(node.matches('button,input,select,textarea,a,[tabindex]'))node.setAttribute('tabindex','-1');
 }
 mirror.classList.remove('dragging','party-drag-placeholder','drop-target');
 mirror.classList.add('party-drag-ghost');mirror.setAttribute('data-party-drag-ghost','');
 mirror.setAttribute('aria-hidden','true');mirror.setAttribute('role','presentation');mirror.setAttribute('inert','');mirror.inert=true;
 Object.assign(mirror.style,{position:'fixed',top:'0',left:'0',width:`${session.width}px`,height:`${session.height}px`,minWidth:'0',maxWidth:'none',maxHeight:'none',margin:'0',pointerEvents:'none',transform:partyFloatingTransform(session.point,session.offset)});
 document.body.appendChild(mirror);return mirror;
}

export function usePartyPointerDrag({onExchange,resetKey,enabled=true}:UsePartyPointerDragOptions) {
 const [draggedSlot,setDraggedSlot]=useState<number|null>(null),[dropSlot,setDropSlot]=useState<number|null>(null);
 const pointer=useRef<PointerSession|null>(null),native=useRef<NativeSession|null>(null),suppressClick=useRef(false),blockedOrigin=useRef(false);
 const exchange=useRef(onExchange),allowed=useRef(enabled);exchange.current=onExchange;allowed.current=enabled;

 const dispose=useCallback((suppress=true,updateState=true)=>{
  const session=pointer.current;pointer.current=null;native.current=null;
  if(session){
   if(suppress&&session.gesture.active)suppressClick.current=true;
   if(session.frame!=null)cancelAnimationFrame(session.frame);
   session.ghost?.remove();
   try{if(session.element.hasPointerCapture(session.gesture.pointerId))session.element.releasePointerCapture(session.gesture.pointerId);}catch{/* Capture can already be lost on browser cancellation. */}
  }
  document.body.classList.remove('party-pointer-drag-active');
  if(updateState){setDraggedSlot(null);setDropSlot(null);}
  return session;
 },[]);
 const cancel=useCallback(()=>{dispose(true);},[dispose]);
 const targetAt=useCallback((session:PointerSession,point:PartyDragPoint)=>{
  const hit=document.elementFromPoint(point.x,point.y)?.closest<HTMLElement>('.fgo-member-card[data-slot]');
  return partyDragTarget(hit?Number(hit.dataset.slot):null,!!hit&&hit.closest('.fgo-party-grid')===session.board);
 },[]);
 const paint=useCallback((session:PointerSession)=>{
  if(session.frame!=null)return;
  session.frame=requestAnimationFrame(()=>{session.frame=null;if(pointer.current===session&&session.ghost)session.ghost.style.transform=partyFloatingTransform(session.point,session.offset);});
 },[]);
 const move=useCallback((event:ReactPointerEvent<HTMLElement>)=>{
  const session=pointer.current;if(!session||session.gesture.pointerId!==event.pointerId)return;
  if(!allowed.current){cancel();return;}
  session.point={x:event.clientX,y:event.clientY};
  session.gesture=updatePartyDrag(session.gesture,event.clientX,event.clientY,targetAt(session,session.point));
  if(!session.gesture.active)return;
  event.preventDefault();
  if(!session.ghost){session.ghost=createMirror(session);document.body.classList.add('party-pointer-drag-active');}
  setDraggedSlot(session.gesture.from);setDropSlot(session.gesture.target);paint(session);
 },[cancel,paint,targetAt]);
 const finish=useCallback((event:ReactPointerEvent<HTMLElement>)=>{
  const session=pointer.current;if(!session||session.gesture.pointerId!==event.pointerId)return;
  move(event);if(pointer.current!==session)return;
  const result=allowed.current?finishPartyDrag(session.gesture):null;
  dispose(true);
  if(result){event.preventDefault();exchange.current(...result);}
 },[dispose,move]);

 useEffect(()=>{cancel();},[cancel,enabled,resetKey]);
 useEffect(()=>{
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&(pointer.current||native.current)){event.preventDefault();cancel();}};
  const blurred=()=>cancel(),hidden=()=>{if(document.hidden)cancel();};
  window.addEventListener('keydown',escape);window.addEventListener('blur',blurred);document.addEventListener('visibilitychange',hidden);
  return()=>{window.removeEventListener('keydown',escape);window.removeEventListener('blur',blurred);document.removeEventListener('visibilitychange',hidden);dispose(true,false);};
 },[cancel,dispose]);

 const bindCard=useCallback((index:number):PartyCardDragBindings=>({
  onPointerDownCapture:event=>{if(event.button===0&&event.isPrimary&&!pointer.current){suppressClick.current=false;blockedOrigin.current=event.target instanceof Element&&!!event.target.closest('[data-no-drag],[data-equip-key]');}},
  onPointerStart:event=>{
   if(!allowed.current||event.button!==0||!event.isPrimary||pointer.current||blockedOrigin.current||event.target instanceof Element&&event.target.closest('[data-no-drag]'))return;
   const card=event.currentTarget.closest<HTMLElement>('.fgo-member-card[data-slot]'),board=card?.closest<HTMLElement>('.fgo-party-grid');
   if(!card||!board||partyDragTarget(index,true)==null)return;
   const bounds=card.getBoundingClientRect(),point={x:event.clientX,y:event.clientY};
   suppressClick.current=false;native.current=null;
   pointer.current={gesture:startPartyDrag(event.pointerId,index,event.clientX,event.clientY),element:event.currentTarget,card,board,offset:partyGrabOffset(bounds,point),point,width:bounds.width,height:bounds.height,ghost:null,frame:null};
   try{event.currentTarget.setPointerCapture(event.pointerId);}catch{pointer.current=null;/* Native drag remains the fallback when capture is unavailable. */}
  },
  onPointerMove:move,onPointerEnd:finish,
  onPointerCancel:event=>{if(pointer.current?.gesture.pointerId===event.pointerId)cancel();},
  onLostPointerCapture:event=>{if(pointer.current?.gesture.pointerId===event.pointerId)cancel();},
  onClickCapture:event=>{if(suppressClick.current&&event.detail>0){event.preventDefault();event.stopPropagation();suppressClick.current=false;}},
  onDragStart:event=>{
   if(!allowed.current||pointer.current||blockedOrigin.current||event.target instanceof Element&&event.target.closest('[data-no-drag],[data-equip-key]')){event.preventDefault();return;}
   const card=event.currentTarget.closest<HTMLElement>('.fgo-member-card[data-slot]'),board=card?.closest<HTMLElement>('.fgo-party-grid');
   if(!card||!board||partyDragTarget(index,true)==null){event.preventDefault();return;}
   native.current={from:index,board};event.dataTransfer.setData('text/plain',`member:${index}`);event.dataTransfer.effectAllowed='move';
   const bounds=card.getBoundingClientRect();event.dataTransfer.setDragImage(card,event.clientX-bounds.left,event.clientY-bounds.top);
   setDraggedSlot(index);
  },
  onDragOver:event=>{
   const session=native.current;if(!session||event.currentTarget.closest('.fgo-party-grid')!==session.board)return;
   event.preventDefault();event.dataTransfer.dropEffect='move';setDropSlot(index);
  },
  onDrop:event=>{
   const session=native.current;if(!session)return;
   const sameBoard=event.currentTarget.closest('.fgo-party-grid')===session.board;
   const target=partyDragTarget(index,sameBoard),from=session.from,value=event.dataTransfer.getData('text/plain');
   event.preventDefault();dispose(true);
   if(allowed.current&&target!=null&&target!==from&&value===`member:${from}`)exchange.current(from,target);
  },
  onDragEnd:()=>{if(!pointer.current)dispose(true);},
 }),[cancel,dispose,finish,move]);

 return {draggedSlot,dropSlot,bindCard,cancel};
}
