import type {Json} from './model';

export type GrandBondEffect='any'|'np50'|'original';

/** The class-board flag is independent of a named bond CE, including old imports. */
export function grandBondEffect(member:Json|null):GrandBondEffect {
 const value=member?.classBoardData?.grandBondEquipSkillChange;
 return value===true?'np50':value===false?'original':'any';
}

export function grandBondEffectLabel(member:Json|null):string {
 return {any:'不限制',np50:'NP 50% 替换效果',original:'原始效果'}[grandBondEffect(member)];
}

/** An explicit edit changes only this effect, retaining imported CE and board data. */
export function setGrandBondEffect(member:Json,value:GrandBondEffect):void {
 if(member.grandSvt!==true)throw new Error('仅冠位从者可设置羁绊礼装效果。');
 if(!['any','np50','original'].includes(value))throw new Error('羁绊礼装效果无效，请重新选择。');
 const board=member.classBoardData;
 if(board!=null&&(typeof board!=='object'||Array.isArray(board)))throw new Error('冠位记录无效，请先在原始 JSON 中修正。');
 if(value==='any'){
  if(board)delete board.grandBondEquipSkillChange;
 }else{
  member.classBoardData??={};
  member.classBoardData.grandBondEquipSkillChange=value==='np50';
 }
}
