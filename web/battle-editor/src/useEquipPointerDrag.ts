import {useCallback,useEffect,useRef,useState} from 'react';
import type {PointerEvent as ReactPointerEvent} from 'react';
import type {Json} from './model';
import type {PartyCardDragBindings} from './PartyCard';
import {canDropEquipDrag,canStartEquipDrag,equipDragPosition} from './equipDrag';
import type {EquipDragPosition} from './equipDrag';
import {partyFloatingTransform,partyGrabOffset} from './partyDrag';
import type {PartyDragPoint} from './partyDrag';

interface Session {
 from:EquipDragPosition;target:EquipDragPosition|null;pointerId:number;active:boolean;
 start:PartyDragPoint;point:PartyDragPoint;offset:PartyDragPoint;item:HTMLElement;element:HTMLElement;board:HTMLElement;
 width:number;height:number;ghost:HTMLElement|null;frame:number|null;
}
interface NativeSession {from:EquipDragPosition;board:HTMLElement}
interface Options {data:Json;onExchange:(from:EquipDragPosition,to:EquipDragPosition)=>void;enabled?:boolean}
export const sameEquipPosition=(a:EquipDragPosition|null,b:EquipDragPosition|null):boolean=>!!a&&!!b&&a.slot===b.slot&&a.key===b.key;

function mirrorFor(session:Session):HTMLElement {
 const ghost=session.item.cloneNode(true) as HTMLElement;
 for(const node of [ghost,...ghost.querySelectorAll<HTMLElement>('*')]){
  for(const attr of ['id','data-slot','data-equip-key'])node.removeAttribute(attr);
  if(node.hasAttribute('draggable'))node.setAttribute('draggable','false');
  if(node.matches('button,input,select,textarea,a,[tabindex]'))node.setAttribute('tabindex','-1');
 }
 ghost.classList.remove('equip-drag-placeholder','equip-drop-target');ghost.classList.add('equip-drag-ghost');
 if(session.item.closest('.grand-equips'))ghost.classList.add('grand-equips');
 ghost.setAttribute('data-equip-drag-ghost','');ghost.setAttribute('aria-hidden','true');ghost.setAttribute('role','presentation');ghost.setAttribute('inert','');ghost.inert=true;
 Object.assign(ghost.style,{position:'fixed',top:'0',left:'0',width:`${session.width}px`,height:`${session.height}px`,minWidth:'0',maxWidth:'none',maxHeight:'none',margin:'0',pointerEvents:'none',transform:partyFloatingTransform(session.point,session.offset)});
 document.body.appendChild(ghost);return ghost;
}

/** A separate gesture keeps equipment drops from reaching whole-member handlers. */
export function useEquipPointerDrag({data,onExchange,enabled=true}:Options) {
 const [dragged,setDragged]=useState<EquipDragPosition|null>(null),[target,setTarget]=useState<EquipDragPosition|null>(null);
 const pointer=useRef<Session|null>(null),native=useRef<NativeSession|null>(null),suppressClick=useRef(false);
 const current=useRef(data),exchange=useRef(onExchange),allowed=useRef(enabled);current.current=data;exchange.current=onExchange;allowed.current=enabled;
 const dispose=useCallback((suppress=true,update=true)=>{
  const session=pointer.current;pointer.current=null;native.current=null;
  if(session){
   if(suppress&&session.active)suppressClick.current=true;
   if(session.frame!=null)cancelAnimationFrame(session.frame);session.ghost?.remove();
   try{if(session.element.hasPointerCapture(session.pointerId))session.element.releasePointerCapture(session.pointerId);}catch{/* Browser cancellation can release capture first. */}
  }
  document.body.classList.remove('equip-pointer-drag-active');if(update){setDragged(null);setTarget(null);}
 },[]);
 const cancel=useCallback(()=>dispose(true),[dispose]);
 const targetAt=useCallback((session:Session,point:PartyDragPoint):EquipDragPosition|null=>{
  const hit=document.elementFromPoint(point.x,point.y)?.closest<HTMLElement>('.fgo-equip-slot[data-equip-key]');
  if(!hit||hit.closest('.fgo-party-grid')!==session.board)return null;
  const card=hit.closest<HTMLElement>('.fgo-member-card[data-slot]');
  const position=equipDragPosition(current.current,{slot:Number(card?.dataset.slot),key:hit.dataset.equipKey});
  return position&&canDropEquipDrag(current.current,session.from,position)?position:null;
 },[]);
 const move=useCallback((event:ReactPointerEvent<HTMLElement>)=>{
  const session=pointer.current;if(!session||session.pointerId!==event.pointerId)return;
  if(!allowed.current){cancel();return;}
  session.point={x:event.clientX,y:event.clientY};
  session.active=session.active||Math.hypot(session.point.x-session.start.x,session.point.y-session.start.y)>=6;
  if(!session.active)return;
  event.preventDefault();session.target=targetAt(session,session.point);
  if(!session.ghost){session.ghost=mirrorFor(session);document.body.classList.add('equip-pointer-drag-active');}
  setDragged(session.from);setTarget(session.target);
  if(session.frame==null)session.frame=requestAnimationFrame(()=>{session.frame=null;if(pointer.current===session&&session.ghost)session.ghost.style.transform=partyFloatingTransform(session.point,session.offset);});
 },[cancel,targetAt]);
 const finish=useCallback((event:ReactPointerEvent<HTMLElement>)=>{
  const session=pointer.current;if(!session||session.pointerId!==event.pointerId)return;
  move(event);if(pointer.current!==session)return;
  const destination=allowed.current&&session.active?session.target:null;
  dispose(true);
  if(destination&&!sameEquipPosition(session.from,destination)){event.preventDefault();exchange.current(session.from,destination);}
 },[dispose,move]);
 useEffect(()=>{cancel();},[cancel,enabled,data]);
 useEffect(()=>{
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&(pointer.current||native.current)){event.preventDefault();cancel();}};
  const blur=()=>cancel(),hidden=()=>{if(document.hidden)cancel();};
  window.addEventListener('keydown',escape);window.addEventListener('blur',blur);document.addEventListener('visibilitychange',hidden);
  return()=>{window.removeEventListener('keydown',escape);window.removeEventListener('blur',blur);document.removeEventListener('visibilitychange',hidden);dispose(true,false);};
 },[cancel,dispose]);
 const bindEquip=useCallback((position:EquipDragPosition):PartyCardDragBindings=>({
  onPointerDownCapture:event=>{if(event.button===0&&event.isPrimary&&!pointer.current)suppressClick.current=false;},
  onPointerStart:event=>{
   if(!allowed.current||event.button!==0||!event.isPrimary||pointer.current||event.target instanceof Element&&event.target.closest('[data-no-drag]')||!canStartEquipDrag(current.current,position))return;
   const item=event.currentTarget.closest<HTMLElement>('.fgo-equip-slot[data-equip-key]'),board=item?.closest<HTMLElement>('.fgo-party-grid');
   if(!item||!board)return;
   const bounds=item.getBoundingClientRect(),point={x:event.clientX,y:event.clientY};native.current=null;
   pointer.current={from:position,target:null,pointerId:event.pointerId,active:false,start:point,point,offset:partyGrabOffset(bounds,point),item,element:event.currentTarget,board,width:bounds.width,height:bounds.height,ghost:null,frame:null};
   try{event.currentTarget.setPointerCapture(event.pointerId);}catch{pointer.current=null;}
  },
  onPointerMove:move,onPointerEnd:finish,
  onPointerCancel:event=>{if(pointer.current?.pointerId===event.pointerId)cancel();},
  onLostPointerCapture:event=>{if(pointer.current?.pointerId===event.pointerId)cancel();},
  onClickCapture:event=>{if(suppressClick.current&&event.detail>0){event.preventDefault();event.stopPropagation();suppressClick.current=false;}},
  onDragStart:event=>{
   event.stopPropagation();
   if(!allowed.current||pointer.current||event.target instanceof Element&&event.target.closest('[data-no-drag]')||!canStartEquipDrag(current.current,position)){event.preventDefault();return;}
   const item=event.currentTarget.closest<HTMLElement>('.fgo-equip-slot[data-equip-key]'),board=item?.closest<HTMLElement>('.fgo-party-grid');
   if(!item||!board){event.preventDefault();return;}
   native.current={from:position,board};event.dataTransfer.setData('text/plain',`equip:${position.slot}:${position.key}`);event.dataTransfer.effectAllowed='move';
   const bounds=item.getBoundingClientRect();event.dataTransfer.setDragImage(item,event.clientX-bounds.left,event.clientY-bounds.top);setDragged(position);
  },
  onDragOver:event=>{
   const session=native.current;if(!session)return;event.stopPropagation();
   if(event.currentTarget.closest('.fgo-party-grid')!==session.board||!canDropEquipDrag(current.current,session.from,position)){setTarget(null);return;}
   event.preventDefault();event.dataTransfer.dropEffect='move';setTarget(position);
  },
  onDrop:event=>{
   const session=native.current;if(!session)return;event.preventDefault();event.stopPropagation();
   const valid=allowed.current&&event.currentTarget.closest('.fgo-party-grid')===session.board&&canDropEquipDrag(current.current,session.from,position)&&event.dataTransfer.getData('text/plain')===`equip:${session.from.slot}:${session.from.key}`;
   dispose(true);if(valid&&!sameEquipPosition(session.from,position))exchange.current(session.from,position);
  },
  onDragEnd:()=>{if(!pointer.current)dispose(true);},
 }),[cancel,dispose,finish,move]);
 return {dragged,target,bindEquip,cancel};
}
