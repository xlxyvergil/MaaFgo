import test from 'node:test';
import assert from 'node:assert/strict';
import {artworkColor,cardArtworkRequest,cardPreviewRegion} from './npCardRequest';
import type {CardArtworkInput,CardPreviewConfig} from './npCardRequest';
import {apiPNG,ApiError,session} from './api';

const config:CardPreviewConfig={enabled:true,regions:['JP','CN'],defaultRegion:'JP',endpoint:'/api/np-card',faceEndpoint:'/api/command-card'};
const input:CardArtworkInput={member:{svtId:100100},np:{id:100101,name:'宝具'},kind:'np',region:'JP',config};
const query=(input:CardArtworkInput)=>new URL(cardArtworkRequest(input).url!,'http://localhost').searchParams;
test('NP requests retain raw servant identity and exact selected NP rather than a catalog alias',()=>{
 const request={...input,member:{svtId:100100012},np:{id:999901}};assert.equal(query(request).get('svtId'),'100100012');assert.equal(query(request).get('tdId'),'999901');assert.equal(query(request).get('region'),'JP');
});
test('explicit zero portrait parameters survive while absent/null parameters stay absent',()=>{
 const params=query({...input,member:{svtId:100100,limitCount:0,transformVal:0}});assert.equal(params.get('limitCount'),'0');assert.equal(params.get('transformVal'),'0');
 assert.deepEqual([...query({...input,member:{svtId:100100,limitCount:null}}).keys()],['svtId','region','tdId']);
});
test('ordinary cards use exact requested color and their own endpoint, without any NP/file/size parameters',()=>{
 const request=cardArtworkRequest({...input,kind:'face',color:'BUSTER'}),params=new URL(request.url!,'http://localhost').searchParams;assert.ok(request.url!.startsWith('/api/command-card?'));assert.deepEqual([...params.keys()],['svtId','region','color']);assert.equal(params.get('color'),'buster');
 for(const [value,expected] of [[1,'arts'],['Quick','quick'],[2,'buster'],['red',null],[true,null]] as const)assert.equal(artworkColor(value),expected);
});
test('invalid card identity or variant stays a text fallback instead of requesting default artwork',()=>{
 for(const member of [{svtId:0},{svtId:-1},{svtId:true},{svtId:'1x'},{svtId:100100,limitCount:-1},{svtId:100100,limitCount:1.5},{svtId:100100,transformVal:'bad'}])assert.equal(cardArtworkRequest({...input,member}).url,null);
 for(const np of [undefined,{id:0},{id:'unknown'},{id:false}])assert.equal(cardArtworkRequest({...input,np}).url,null);
 assert.equal(cardArtworkRequest({...input,kind:'face',color:0}).url,null);
});
test('region preference defaults to JP, stays local to supported capability regions and cannot invent a region',()=>{
 assert.equal(cardPreviewRegion(null,config),'JP');assert.equal(cardPreviewRegion('CN',config),'CN');assert.equal(cardPreviewRegion('EN',config),'JP');
 assert.equal(cardPreviewRegion('CN',{...config,regions:['JP']}),'JP');assert.equal(cardArtworkRequest({...input,region:'EN'}).url,null);
 assert.equal(cardArtworkRequest({...input,config:{...config,endpoint:'https://example.com/api/np-card'}}).url,null);
 assert.equal(cardArtworkRequest({...input,config:{...config,enabled:false}}).url,null);
});
test('request keys isolate raw member, exact NP, region, ascension, transform and command color',()=>{
 const base=cardArtworkRequest(input).key;
 for(const changed of [{...input,member:{svtId:100101}},{...input,np:{id:100102}},{...input,region:'CN'},{...input,member:{svtId:100100,limitCount:0}},{...input,member:{svtId:100100,transformVal:0}},{...input,kind:'face' as const,color:1}])assert.notEqual(cardArtworkRequest(changed).key,base);
 const member=structuredClone(input.member),np=structuredClone(input.np);cardArtworkRequest(input);assert.deepEqual(input.member,member);assert.deepEqual(input.np,np);
});
test('binary requests reuse session header, AbortSignal and return Blob with portrait provenance',async()=>{
 const original=globalThis.fetch,controller=new AbortController();let calls=0;
 globalThis.fetch=async(path,options)=>{calls++;if(path==='/health')return new Response(JSON.stringify({service:'maafgo-battle-editor',protocolVersion:1,projectId:'a'.repeat(64)}),{headers:{'Content-Type':'application/json'}});if(path==='/api/session')return new Response(JSON.stringify({token:'local-test-token'}),{headers:{'Content-Type':'application/json'}});assert.equal(path,'/api/np-card?svtId=100100');assert.equal((options?.headers as Record<string,string>)['X-Editor-Token'],'local-test-token');assert.equal(options?.signal,controller.signal);assert.equal(options?.credentials,'same-origin');return new Response(new Uint8Array([137,80,78,71]),{headers:{'Content-Type':'image/png','X-Editor-Card-Portrait-Source':'preview-default'}});};
 try{await session();const result=await apiPNG('/api/np-card?svtId=100100',controller.signal);assert.equal(result.blob.size,4);assert.equal(result.portraitSource,'preview-default');assert.equal(calls,3);}finally{globalThis.fetch=original;}
});
test('binary errors retain service reasons and reject nonlocal, non-PNG and empty bodies',async()=>{
 const original=globalThis.fetch;
 try{
  globalThis.fetch=async()=>new Response(JSON.stringify({error:{code:'np_unknown',message:'没有这个宝具的卡面。'}}),{status:404,headers:{'Content-Type':'application/json'}});
  await assert.rejects(apiPNG('/api/np-card'),error=>error instanceof ApiError&&error.code==='np_unknown'&&error.message==='没有这个宝具的卡面。');
  globalThis.fetch=async()=>new Response('not a png',{headers:{'Content-Type':'text/plain'}});await assert.rejects(apiPNG('/api/np-card'),/图片格式/);
  globalThis.fetch=async()=>new Response(null,{headers:{'Content-Type':'image/png'}});await assert.rejects(apiPNG('/api/np-card'),/空图片/);
  await assert.rejects(apiPNG('https://example.com/api/np-card'),/本地服务/);
 }finally{globalThis.fetch=original;}
});
