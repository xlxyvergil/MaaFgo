/** Pointer gestures keep click selection and drag swapping separate. */
export interface PartyDragGesture {pointerId:number;from:number;x:number;y:number;active:boolean;target:number|null}
export interface PartyDragPoint {x:number;y:number}
export function partyGrabOffset(bounds:{left:number;top:number},point:PartyDragPoint):PartyDragPoint {return {x:point.x-bounds.left,y:point.y-bounds.top};}
export function partyFloatingPosition(point:PartyDragPoint,offset:PartyDragPoint):{left:number;top:number} {return {left:point.x-offset.x,top:point.y-offset.y};}
export function partyFloatingTransform(point:PartyDragPoint,offset:PartyDragPoint):string {const {left,top}=partyFloatingPosition(point,offset);return `translate3d(${left}px, ${top}px, 0)`;}
export function partyDragTarget(slot:number|null,sameBoard:boolean):number|null {return sameBoard&&slot!=null&&Number.isInteger(slot)&&slot>=0&&slot<6?slot:null;}
export function startPartyDrag(pointerId:number,from:number,x:number,y:number):PartyDragGesture{return {pointerId,from,x,y,active:false,target:null};}
export function updatePartyDrag(gesture:PartyDragGesture,x:number,y:number,target:number|null):PartyDragGesture {
 const active=gesture.active||Math.hypot(x-gesture.x,y-gesture.y)>=6;
 return {...gesture,active,target:active?partyDragTarget(target,true):null};
}
export function finishPartyDrag(gesture:PartyDragGesture):[number,number]|null {
 return gesture.active&&gesture.target!=null&&partyDragTarget(gesture.from,true)!=null&&gesture.from!==gesture.target?[gesture.from,gesture.target]:null;
}
