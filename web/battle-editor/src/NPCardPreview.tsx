import {useEffect,useRef,useState} from 'react';
import {Check,Swords} from 'lucide-react';
import {apiPNG} from './api';
import {artworkColor,cardArtworkRequest} from './npCardRequest';
import type {CardArtworkInput,CardPreviewConfig} from './npCardRequest';
import type {Json} from './model';
import {hasServantIdentity} from './model';

interface ArtworkState {key:string;phase:'loading'|'decoding'|'ready'|'error';url?:string;reason?:string;portraitSource?:string|null}
export function CardArtwork(props:CardArtworkInput&{className?:string}) {
 const request=cardArtworkRequest(props),[state,setState]=useState<ArtworkState>({key:'',phase:'loading'}),ownedURLs=useRef(new Set<string>());
 const release=(url:string)=>{if(ownedURLs.current.delete(url))URL.revokeObjectURL(url);};
 useEffect(()=>{
  if(!request.url)return;
  const controller=new AbortController();let live=true,objectURL:string|undefined;
  const revoke=()=>{if(objectURL){release(objectURL);objectURL=undefined;}};
  setState({key:request.key,phase:'loading'});
  void apiPNG(request.url,controller.signal).then(result=>{
   if(!live)return;
   objectURL=URL.createObjectURL(result.blob);ownedURLs.current.add(objectURL);setState({key:request.key,phase:'decoding',url:objectURL,portraitSource:result.portraitSource});
  }).catch(error=>{if(live&&!controller.signal.aborted){revoke();setState({key:request.key,phase:'error',reason:error instanceof Error?error.message:'卡图生成失败。'});}});
  return()=>{live=false;controller.abort();revoke();};
 },[request.key,request.url]);
 const current=state.key===request.key?state:undefined,loaded=!!request.url&&current?.phase==='ready',name=props.kind==='np'?(props.np?.name??'宝具'):`${artworkColor(props.color)?.replace(/^./,c=>c.toUpperCase())??'未知'} 色卡`;
 const reason=request.reason??(current?.phase==='error'?current.reason:current?.phase==='decoding'?'正在载入卡图…':'正在生成完整卡图…');
 const title=loaded&&current?.portraitSource==='preview-default'?'默认卡面，仅预览':loaded?name:reason;
 return <span className={`card-artwork ${loaded?'ready':'fallback'} ${props.className??''}`} title={title}>
  {current?.url&&request.url&&<img src={current.url} alt={name} draggable={false} className={loaded?'':'card-artwork-loading-image'} onLoad={event=>{if(event.currentTarget.naturalWidth>0)setState(old=>old.key===request.key&&old.url===current.url?{...old,phase:'ready'}:old);}} onError={()=>{release(current.url!);setState(old=>old.key===request.key&&old.url===current.url?{key:old.key,phase:'error',reason:'完整卡图无法解码；仍可安排这张卡。'}:old);}}/>}
  {!loaded&&<span className="card-artwork-message"><Swords size={18} aria-hidden="true"/><strong>{name}</strong><small role={current?.phase==='error'?'status':undefined}>{reason}</small></span>}
 </span>;
}

export function NPCardPreview({member,np,region,config,slot,selected,full,onClick}:{member:Json|null;np?:Json;region:string;config?:CardPreviewConfig;slot:number;selected:boolean;full:boolean;onClick:()=>void}) {
 const actorPresent=hasServantIdentity(member),name=actorPresent?(np?.name??'宝具'):'未指定从者';
 return <button className={`np-composite-button ${actorPresent&&selected?'selected':''}`} disabled={!actorPresent||selected||full} aria-label={`安排前排 ${slot+1} 宝具：${name}`} aria-pressed={actorPresent&&selected} title={!actorPresent?'请先指定从者，才能安排宝具':selected?'本回合已安排这位从者的宝具':full?'本回合出卡意图已满三项':`安排${name}`} onClick={()=>{if(actorPresent)onClick();}}>
  {actorPresent&&selected&&<span className="np-composite-selected"><Check size={14}/><span className="sr-only">已安排</span></span>}
  {actorPresent?<CardArtwork member={member} np={np} kind="np" region={region} config={config}/>:<span className="card-artwork fallback"><span className="card-artwork-message"><Swords size={18}/><strong>未指定从者</strong><small>无法安排宝具</small></span></span>}<span className="np-composite-caption">{actorPresent?name:'无法安排宝具'}</span>
 </button>;
}
