import {members} from './model';
import type {Json} from './model';

export function reservedBondSupport(data:Json,preferredSlot?:number|null):number|null {
 const roster=members(data);
 const existing=roster.findIndex(m=>m&&['friend','npc','fixed'].includes(m.supportType));
 if(existing>=0)return existing;
 if(typeof preferredSlot==='number'&&Number.isInteger(preferredSlot)&&preferredSlot>=0&&preferredSlot<6&&!roster[preferredSlot]?.svtId)return preferredSlot;
 for(let i=5;i>=0;i--)if(!roster[i]?.svtId)return i;
 return null;
}
export function bondResultTeam(current:Json,snapshot:Json,response:Json):Json {
 if(current!==snapshot)throw new Error('规划期间方案已更改，旧结果未应用；请重新执行。');
 if(!response?.data?.team||typeof response.data.team!=='object'||Array.isArray(response.data.team))throw new Error('羁绊规划没有返回有效编队。');
 return structuredClone(response.data.team);
}
