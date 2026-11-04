export interface AttackCardGesture {pointerId:number;from:number;x:number;y:number;active:boolean;target:number|null}
export function attackCardTarget(index:number|null,occupied:readonly boolean[],sameGroup:boolean):number|null {
 return attackCardDropTarget(index,sameGroup)!=null&&occupied[index!]===true?index:null;
}
export function attackCardDropTarget(index:number|null,sameGroup:boolean):number|null {
 return sameGroup&&index!=null&&Number.isInteger(index)&&index>=0&&index<3?index:null;
}
export function startAttackCardDrag(pointerId:number,from:number,x:number,y:number):AttackCardGesture{return {pointerId,from,x,y,active:false,target:null};}
export function updateAttackCardDrag(gesture:AttackCardGesture,x:number,y:number,target:number|null):AttackCardGesture {
 const active=gesture.active||Math.hypot(x-gesture.x,y-gesture.y)>=6;return {...gesture,active,target:active?target:null};
}
export function finishAttackCardDrag(gesture:AttackCardGesture,occupied:readonly boolean[]):[number,number]|null {
 return gesture.active&&attackCardTarget(gesture.from,occupied,true)!=null&&attackCardDropTarget(gesture.target,true)!=null&&gesture.from!==gesture.target?[gesture.from,gesture.target!]:null;
}
