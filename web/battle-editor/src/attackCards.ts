import type {Json} from './model';

const isRecord=(card:unknown):card is Json=>!!card&&typeof card==='object'&&!Array.isArray(card);
const hasValidAutoFlag=(card:Json):boolean=>!Object.hasOwn(card,'isAuto')||typeof card.isAuto==='boolean';

/** Maa's automatic face-card extension keeps a hole before a later NP or face card. */
export function isAutoAttackCard(card:unknown):boolean {
 return isRecord(card)&&card.isAuto===true;
}

function attackCards(attack:Json):Json[] {
 if(attack?.type!=='attack'||!Array.isArray(attack.attacks))throw new Error('这个动作没有可编辑的出卡。');
 return attack.attacks;
}

function isEditableCard(card:unknown):card is Json {
 return isRecord(card)&&hasValidAutoFlag(card);
}

function trimAutomaticTail(cards:Json[],hadImportedTail:boolean):void {
 if(hadImportedTail)return;
 while(cards.length&&isAutoAttackCard(cards.at(-1))&&Object.keys(cards.at(-1)!).length===1)cards.pop();
}

/** Swap complete card records; never rewrite deck indexes, targets, or imported fields. */
export function swapAttackCardPositions(attack:Json,from:number,to:number):void {
 const cards=attackCards(attack);
 if(!Number.isInteger(from)||!Number.isInteger(to)||from<0||to<0||from>=3||to>=3||from>=cards.length)throw new Error('请在前三个出卡位之间移动。');
 if(!isEditableCard(cards[from])||isAutoAttackCard(cards[from]))throw new Error('请选择已设定的宝具或普通卡。');
 if(to<cards.length&&!isEditableCard(cards[to]))throw new Error('目标出卡记录无法编辑，请先检查原始 JSON。');
 if(from===to)return;
 const hadImportedTail=cards.length>3;
 while(cards.length<=to)cards.push({isAuto:true});
 [cards[from],cards[to]]=[cards[to],cards[from]];
 trimAutomaticTail(cards,hadImportedTail);
}

/** Remove one recorded first-three card; later imported cards retain their order. */
export function removeAttackCard(attack:Json,index:number):void {
 const cards=attackCards(attack);
 if(!Number.isInteger(index)||index<0||index>=3||index>=cards.length)throw new Error('请选择前三个已设定的出卡位。');
 if(!isEditableCard(cards[index])||isAutoAttackCard(cards[index]))throw new Error('自动补齐空位或不支持的记录不能删除。');
 const hadImportedTail=cards.length>3;
 cards.splice(index,1);
 trimAutomaticTail(cards,hadImportedTail);
}

/** Fill the earliest automatic slot before appending; explicit updates preserve imported fields. */
export function setAttackCard(attack:Json,card:Json,index?:number):void {
 const cards=attackCards(attack);
 if(!isEditableCard(card)||isAutoAttackCard(card))throw new Error('请选择有效的宝具或普通卡。');
 let target=index;
 if(target==null){
  target=cards.slice(0,3).findIndex(isAutoAttackCard);
  if(target<0){
   if(cards.length>=3)throw new Error('一个回合最多设置三张出卡。');
   target=cards.length;
  }
 }else if(!Number.isInteger(target)||target<0||target>=3||target>=cards.length){
  throw new Error('请选择前三个已有的出卡位。');
 }
 if(target<cards.length&&!isEditableCard(cards[target]))throw new Error('目标出卡记录无法编辑，请先检查原始 JSON。');
 const next={...(cards[target]??{}),...card};
 delete next.isAuto;
 if(next.isTD)delete next.cardType;
 cards[target]=next;
}
