import type {Json} from './model';

export const ADVANCED_STRATEGY_FIELD='maaAdvancedStrategy';
export const STRATEGY_PRESETS=[
 ['balanced','平衡：三色与勇猛链'],['mighty','优先三色链'],['brave','优先同一从者勇猛链'],
 ['np','优先蓝卡与 NP 回收'],['legacy','关闭增强补牌，沿用原策略'],
] as const;
export type StrategyPreset=typeof STRATEGY_PRESETS[number][0];
export interface AdvancedStrategy extends Json {
 preset:StrategyPreset;
 openingHand:{enabled:boolean;servantId:string|number|null;servantSlot:number|null;requiredColors:string[];maxWaitTurns:number;[key:string]:unknown};
 nativeStrategyOverrides:Json;
 autoServantSkills:boolean;
 maxTurns:number;
}
export const defaultAdvancedStrategy=():AdvancedStrategy=>({preset:'balanced',openingHand:{enabled:false,servantId:null,servantSlot:null,requiredColors:['B','Q'],maxWaitTurns:2},nativeStrategyOverrides:{},autoServantSkills:true,maxTurns:40});
export const strategyObject=(value:unknown):value is Json=>value!==null&&typeof value==='object'&&!Array.isArray(value);

/** Viewing an old or partial document never materializes defaults in its tree. */
export function advancedStrategyOf(data:Json):AdvancedStrategy {
 const defaults=defaultAdvancedStrategy(),raw=strategyObject(data[ADVANCED_STRATEGY_FIELD])?data[ADVANCED_STRATEGY_FIELD]:{};
 return structuredClone({...defaults,...raw,openingHand:{...defaults.openingHand,...(strategyObject(raw.openingHand)?raw.openingHand:{})}});
}

/** Apply inside the editor's ordinary undo/redo transaction; retain unknown keys. */
export function patchAdvancedStrategy(data:Json,patch:Json):void {
 const raw=data[ADVANCED_STRATEGY_FIELD];
 if(raw!==undefined&&!strategyObject(raw))throw new Error('全局高级策略必须是 JSON 对象，请先在原始 JSON 中修正。');
 if(raw?.openingHand!==undefined&&!strategyObject(raw.openingHand))throw new Error('开局等牌配置必须是 JSON 对象，请先在原始 JSON 中修正。');
 const current=advancedStrategyOf(data);
 if(Object.entries(patch).every(([key,value])=>key==='openingHand'&&strategyObject(value)?Object.entries(value).every(([name,item])=>JSON.stringify(current.openingHand[name])===JSON.stringify(item)):JSON.stringify(current[key])===JSON.stringify(value)))return;
 const next={...current,...structuredClone(patch),openingHand:{...current.openingHand,...(patch.openingHand??{})}};
 data[ADVANCED_STRATEGY_FIELD]=next;
}

export function parseNativeStrategyOverrides(text:string):Json {
 let value:unknown;
 try{value=JSON.parse(text.trim()||'{}');}catch{throw new Error('补牌策略微调必须是有效的 JSON 对象。');}
 if(!strategyObject(value))throw new Error('补牌策略微调必须是 JSON 对象，不能使用数组、null 或单个数值。');
 return value;
}

export function strategyInteger(text:string,min:number,max:number,label:string):number {
 const value=Number(text);
 if(!text.trim()||!Number.isSafeInteger(value)||value<min||value>max)throw new Error(`${label}必须是 ${min}～${max} 的整数。`);
 return value;
}

/** Form feedback complements the authoritative shared Python validation. */
export function advancedStrategyIssues(data:Json):string[] {
 const raw=data[ADVANCED_STRATEGY_FIELD];
 if(raw===undefined)return [];
 if(!strategyObject(raw))return ['全局高级策略必须是 JSON 对象。'];
 const issues:string[]=[];
 if(raw.preset!==undefined&&!STRATEGY_PRESETS.some(([key])=>key===raw.preset))issues.push('补牌预设无效，请重新选择。');
 if(raw.autoServantSkills!==undefined&&typeof raw.autoServantSkills!=='boolean')issues.push('自动补放从者技能必须是布尔值。');
 if(raw.maxTurns!==undefined&&(!Number.isSafeInteger(raw.maxTurns)||raw.maxTurns<1||raw.maxTurns>100))issues.push('最大战斗回合必须是 1～100 的整数。');
 if(raw.nativeStrategyOverrides!==undefined&&!strategyObject(raw.nativeStrategyOverrides))issues.push('补牌策略微调必须是 JSON 对象。');
 if(raw.openingHand!==undefined){
  if(!strategyObject(raw.openingHand))issues.push('开局等牌配置必须是 JSON 对象。');
  else{
   const o=raw.openingHand;
   if(o.enabled!==undefined&&typeof o.enabled!=='boolean')issues.push('开局等待关键牌必须是布尔值。');
   if(o.servantId!=null&&!((Number.isSafeInteger(o.servantId)&&o.servantId>0&&o.servantId<1e12)||(typeof o.servantId==='string'&&/^\d{1,12}$/.test(o.servantId.trim())&&Number(o.servantId)>0)))issues.push('目标从者 ID 必须为正整数或数字文本。');
   if(o.servantSlot!=null&&(!Number.isSafeInteger(o.servantSlot)||o.servantSlot<1||o.servantSlot>3))issues.push('目标前排位置必须是 1～3。');
   if(o.requiredColors!==undefined&&(!Array.isArray(o.requiredColors)||o.requiredColors.length===0||o.requiredColors.length>3||o.requiredColors.some((c:unknown)=>typeof c!=='string'||!['B','A','Q'].includes(c))))issues.push('所需牌色必须是 B、A、Q 构成的 1～3 项数组。');
   if(o.maxWaitTurns!==undefined&&(!Number.isSafeInteger(o.maxWaitTurns)||o.maxWaitTurns<0||o.maxWaitTurns>5))issues.push('最多普攻等待回合必须是 0～5 的整数。');
  }
 }
 return issues;
}
