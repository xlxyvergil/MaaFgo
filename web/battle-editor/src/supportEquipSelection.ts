import type {Equip,Json} from './model';
import {equipCandidatesForDisplay,supportsEquipCandidates} from './equipRequirements';

export type SupportEquipRequirement='limitBreak'|'nonLimitBreak'|'any';

/** Only the card-image support slots are restricted, never a grand bond-effect slot. */
export function supportEquipRequirement(member:Json|null|undefined,key:string,index=0,defaultSupport=false):SupportEquipRequirement|null {
 const target=member??(defaultSupport?{supportType:'friend'}:null);
 if(!supportsEquipCandidates(target,key))return null;
 const requirement=equipCandidatesForDisplay(target,key)[index]?.limitBreak;
 return requirement===true?'limitBreak':requirement===false?'nonLimitBreak':'any';
}

/** Missing capability metadata from an old server fails closed until explicitly disabled. */
export function matchesSupportEquip(equip:Equip,requirement:SupportEquipRequirement|null,onlyRecognizable=true):boolean {
 if(requirement===null||!onlyRecognizable)return true;
 const known=equip.supportRecognition;
 return requirement==='any'?(known?.limitBreak===true||known?.nonLimitBreak===true):known?.[requirement]===true;
}
