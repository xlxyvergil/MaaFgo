import {servantFor} from './model';
import type {Catalog,Json} from './model';

export type NamingSource='local'|'chaldea'|'bbc';

export function restoreNamingSource(value:unknown):NamingSource {
 return value==='chaldea'||value==='bbc'?value:'local';
}

/** Count UTF-16 units while keeping each complete Unicode code point. */
function truncate(value:string,limit:number):string {
 let result='';
 for(const character of value){if(result.length+character.length>limit)break;result+=character;}
 return result;
}

function cleanName(value:string):string {
 const cleaned=value.replace(/[<>:"/\\|?*]|\p{Cc}/gu,'-').replace(/^[\s.]+|[\s.]+$/gu,'');
 return truncate(cleaned,40).replace(/[\s.]+$/gu,'');
}

const reservedName=(value:string):boolean=>/^(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])$/iu.test(value.split('.')[0]);

/** A Battle filename is a basename, never an uploaded file's original path. */
export function canonicalBattleFileName(value:string):string {
 if(typeof value!=='string')throw new Error('请填写 JSON 文件名。');
 const trimmed=value.trim();
 if(!trimmed||trimmed.startsWith('.')||trimmed.endsWith('.'))throw new Error('文件名不能为空、以点开头或以点结尾。');
 if(/[<>:"/\\|?*\u0000-\u001f]/u.test(trimmed))throw new Error('文件名包含路径或 Windows 非法字符。');
 const name=/\.json$/iu.test(trimmed)?trimmed:trimmed+'.json';
 if(name.length>160)throw new Error('JSON 文件名不能超过 160 个字符。');
 if(reservedName(name))throw new Error('文件名不能使用 Windows 保留名称。');
 return name;
}

export function sameBattleFileName(a:string,b:string):boolean {
 try{return canonicalBattleFileName(a).toLowerCase()===canonicalBattleFileName(b).toLowerCase();}
 catch{return false;}
}

function inheritedName(value:unknown,fromFile:boolean):string|null {
 if(typeof value!=='string')return null;
 const original=value.trim();
 // Browsers expose File.name as a basename. Do not trust paths restored from
 // arbitrary metadata, or turn them into a different overwrite target.
 if(fromFile&&(/[\\/]/u.test(original)||/^[a-z]:/iu.test(original)))return null;
 const extension=/\.json$/iu.test(original)?original.slice(-5):'.json';
 let stem=(/\.json$/iu.test(original)?original.slice(0,-5):original)
  .replace(/[<>:"/\\|?*]|\p{Cc}/gu,'-').replace(/^[\s.]+|[\s.]+$/gu,'');
 if(!stem||(!fromFile&&stem==='未命名方案'))return null;
 if(reservedName(stem))stem='方案_'+stem;
 stem=truncate(stem,160-extension.length).replace(/[\s.]+$/gu,'');
 if(!stem)return null;
 try{return canonicalBattleFileName(stem+extension);}catch{return null;}
}

/** Inheritance is editor metadata; it never rewrites the Chaldea document. */
export function importedBattleFileName(data:Json,originalName?:unknown):string|null {
 return inheritedName(originalName,true)??inheritedName(data.team?.name,false);
}

/** Explicit Save As retains a useful name and adds randomness only on collision. */
export function availableBattleFileName(preferred:string,existingNames:readonly string[],makeSuffix:()=>string):string {
 const base=canonicalBattleFileName(preferred),occupied=new Set<string>();
 for(const name of existingNames){try{occupied.add(canonicalBattleFileName(name).toLowerCase());}catch{/* An invalid entry cannot be a Battle target. */}}
 if(!occupied.has(base.toLowerCase()))return base;
 const extension=base.slice(-5),stem=base.slice(0,-5);
 const random=String(makeSuffix()).toLowerCase().replace(/[^0-9a-f]/gu,'').slice(0,6).padEnd(6,'0');
 let ordinal=1;
 while(true){
  const suffix=`_${random}${ordinal===1?'':`_${ordinal}`}`;
  const name=truncate(stem,160-extension.length-suffix.length).replace(/[\s.]+$/gu,'')+suffix+extension;
  if(!occupied.has(name.toLowerCase()))return name;
  ordinal++;
 }
}

function frontName(data:Json,catalog:Catalog,index:number):string {
 const member=data.team?.onFieldSvts?.[index];
 if(member==null)return `空位${index+1}`;
 const id=member.svtId,fallback=cleanName(`从者${id??'未知'}`)||'未知从者';
 const name=id!=null?servantFor(catalog,id)?.name:undefined;
 return typeof name==='string'?cleanName(name)||fallback:fallback;
}

/** Suggest a basename only; provenance remains separate from the Chaldea tree. */
export function suggestBattleFileName(data:Json,catalog:Catalog,source:NamingSource,
 existingNames:readonly string[],makeSuffix:()=>string):string {
 const stem=`${restoreNamingSource(source)}_${[0,1,2].map(index=>frontName(data,catalog,index)).join('_')}`;
 const occupied=new Set(existingNames.filter(name=>typeof name==='string').map(name=>name.toLowerCase()));
 const candidate=(suffix:string)=>`${truncate(stem,160-5-suffix.length)}${suffix}.json`;
 const base=candidate('');
 if(!occupied.has(base.toLowerCase()))return base;
 // Only a collision consumes randomness, once per suggestion; arbitrary factory
 // output cannot introduce a path, an extension, or an unbounded suffix.
 const suffix=String(makeSuffix()).toLowerCase().replace(/[^0-9a-f]/g,'').slice(0,6).padEnd(6,'0');
 let ordinal=1;
 while(true){
  const name=candidate(`_${suffix}${ordinal===1?'':`_${ordinal}`}`);
  if(!occupied.has(name.toLowerCase()))return name;
  ordinal++;
 }
}
