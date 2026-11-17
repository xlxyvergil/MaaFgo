import type {Json,Servant} from './model';

/** Display facts for one coherent build; only standard Chaldea fields are saved. */
export interface ServantForm {
 key:string;label:string;limitCount:number;limitCounts:number[];rarity:number;cost:number;
 skillIds:number[];tdId:number;defaultForLimit?:boolean;
 image?:string|null;portrait?:string|null;classIcon?:string|null;
}

export function selectedServantForm(servant:Servant|undefined,member:Json|null|undefined):ServantForm|undefined {
 const forms=servant?.forms;if(!forms?.length)return undefined;
 // Chaldea's SvtSaveData defaults an omitted/null limitCount to ascension 4.
 const limit=member?.limitCount??4;
 const candidates=forms.filter(form=>form.limitCounts.includes(limit));
 if(!candidates.length)return undefined;
 const score=(form:ServantForm)=>{
  let matched=0;
  if(Array.isArray(member?.skillIds))for(let i=0;i<3;i++)if(member.skillIds[i]>0&&member.skillIds[i]===form.skillIds[i])matched++;
  const tdId=member?.tdId;if(tdId>0&&tdId===form.tdId)matched++;
  return matched;
 };
 return [...candidates].sort((a,b)=>score(b)-score(a)||Number(!!b.defaultForLimit)-Number(!!a.defaultForLimit))[0];
}

/** Never materializes defaults or edits an imported member while displaying it. */
export function projectServantForm(servant:Servant|undefined,member:Json|null|undefined):Servant|undefined {
 if(!servant?.forms?.length)return servant;
 const form=selectedServantForm(servant,member);
 if(!form)return {...servant,rarity:undefined,cost:null,skills:[],np:undefined,formLabel:'形态待确认'};
 return {...servant,rarity:form.rarity,cost:form.cost,formLabel:form.label,
  image:form.image??servant.image,portrait:form.portrait??null,statusImage:form.image??servant.statusImage,
  classIcon:form.classIcon??servant.classIcon,
  skills:form.skillIds.map((id,index)=>{const skill=servant.skillVariants?.find(skill=>skill.id===id)??servant.skills?.find(skill=>skill.id===id);return {...skill,id,name:skill?.name??`技能 ${index+1}（资料待补充）`,target:skill?.target??'unknown'};}),
  np:servant.npVariants?.find(np=>np.id===form.tdId)??(servant.np?.id===form.tdId?servant.np:undefined)};
}

/** An explicit form choice updates the existing Chaldea configuration in place. */
export function setServantForm(member:Json,servant:Servant,key:string):void {
 const form=servant.forms?.find(item=>item.key===key);
 if(!form)throw new Error('所选马修形态的资料暂不可用，无法应用到编队。请关闭选择窗口，重新打开“选择从者”后重试。');
 member.limitCount=form.limitCount;member.skillIds=[...form.skillIds];member.tdId=form.tdId;
 delete member.transformVal;
}
