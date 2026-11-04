import test from 'node:test';
import assert from 'node:assert/strict';

type ApiModule=typeof import('./api');
const firstProject='a'.repeat(64),secondProject='b'.repeat(64);
const health=(changes:Record<string,unknown>={})=>({service:'maafgo-battle-editor',protocolVersion:1,projectId:firstProject,...changes});
const json=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
let moduleNumber=0;

async function withAPI(search:string|undefined,run:(api:ApiModule)=>Promise<void>){
 const originalFetch=globalThis.fetch,originalWindow=Object.getOwnPropertyDescriptor(globalThis,'window');
 if(search===undefined)Reflect.deleteProperty(globalThis,'window');
 else Object.defineProperty(globalThis,'window',{configurable:true,value:{location:{search}}});
 try{await run(await import(new URL(`./api.ts?project-binding-test=${++moduleNumber}`,import.meta.url).href));}
 finally{globalThis.fetch=originalFetch;if(originalWindow)Object.defineProperty(globalThis,'window',originalWindow);else Reflect.deleteProperty(globalThis,'window');}
}

test('launcher project binding is carried by session, JSON, image and PNG requests',async()=>{
 await withAPI(`?project=${firstProject}`,async api=>{
  const requests:{path:string;options:RequestInit|undefined}[]=[];
  const controller=new AbortController(),image=new Blob(['image'],{type:'image/png'});
  globalThis.fetch=async(path,options)=>{
   requests.push({path:String(path),options});
   if(path==='/health'){assert.equal(options?.cache,'no-store');return json(health());}
   const headers=options?.headers as Record<string,string>;
   assert.equal(headers['X-Editor-Project'],firstProject);
   assert.equal(options?.credentials,'same-origin');
   if(path==='/api/session'){assert.equal(headers['X-Editor-Token'],undefined);return json({token:'first-token',projectId:firstProject});}
   assert.equal(headers['X-Editor-Token'],'first-token');
   assert.equal(options?.signal,controller.signal);
   if(path==='/api/import-image'){assert.equal(options?.body,image);assert.equal(headers['Content-Type'],'image/png');return json({imported:true});}
   if(path==='/api/np-card?svtId=100100')return new Response('png',{headers:{'Content-Type':'image/png','X-Editor-Card-Portrait-Source':'preview-default'}});
   assert.equal(path,'/api/drafts/test');assert.equal(options?.method,'PUT');
   assert.deepEqual(JSON.parse(String(options?.body)),{data:{keep:null}});
   return json({saved:true});
  };
  await api.session();
  assert.deepEqual(await api.api('/drafts/test','PUT',{data:{keep:null}},controller.signal),{saved:true});
  assert.deepEqual(await api.apiImage(image,'image/png',controller.signal),{imported:true});
  const preview=await api.apiPNG('/api/np-card?svtId=100100',controller.signal);
  assert.equal(preview.blob.size,3);assert.equal(preview.portraitSource,'preview-default');
  assert.deepEqual(requests.map(r=>r.path),['/health','/api/session','/api/drafts/test','/api/import-image','/api/np-card?svtId=100100']);
 });
});

test('direct opening pins first health identity and reconnects only to the same installation',async()=>{
 await withAPI('',async api=>{
  let currentProject=firstProject,sessionCount=0;
  globalThis.fetch=async(path,options)=>{
   if(path==='/health')return json(health({projectId:currentProject}));
   const headers=options?.headers as Record<string,string>;
   assert.equal(headers['X-Editor-Project'],firstProject);
   if(path==='/api/session'){sessionCount++;assert.equal(headers['X-Editor-Token'],undefined);return json({token:`token-${sessionCount}`});}
   assert.equal(path,'/api/heartbeat');assert.equal(headers['X-Editor-Token'],undefined);return json({ok:true});
  };
  // Old compatible backends need not include projectId in their session response.
  assert.equal((await api.session()).token,'token-1');
  assert.equal((await api.session()).token,'token-2');
  currentProject=secondProject;
  await assert.rejects(api.session(),error=>error instanceof api.ApiError&&error.code==='project_mismatch'&&error.status===409);
  assert.equal(sessionCount,2,'cross-project reconnect must never acquire a new session');
  await api.api('/heartbeat','POST',{});
  currentProject=firstProject;
  assert.equal((await api.session()).token,'token-3');
 });
});

test('launcher URL rejects a different installation before requesting its token',async()=>{
 await withAPI(`?project=${firstProject}`,async api=>{
  const paths:string[]=[];
  globalThis.fetch=async path=>{paths.push(String(path));return json(health({projectId:secondProject}));};
  await assert.rejects(api.session(),error=>error instanceof api.ApiError&&error.code==='project_mismatch'&&/旧页面/.test(error.message));
  assert.deepEqual(paths,['/health']);
 });
});

test('invalid or ambiguous project parameters never contact a backend',async()=>{
 for(const search of ['?project=','?project=wrong',`?project=${firstProject.toUpperCase()}`,`?project=${firstProject}&project=${secondProject}`]){
  await withAPI(search,async api=>{
   let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('must not fetch');};
   await assert.rejects(api.session(),error=>error instanceof api.ApiError&&error.code==='invalid_project');
   assert.equal(requests,0);
  });
 }
});

test('unrelated, incompatible or malformed health responses cannot establish a session',async()=>{
 for(const value of [health({service:'other'}),health({protocolVersion:2}),health({projectId:undefined}),health({projectId:'wrong'}),null]){
  await withAPI('',async api=>{
   const paths:string[]=[];globalThis.fetch=async path=>{paths.push(String(path));return json(value);};
   await assert.rejects(api.session(),error=>error instanceof api.ApiError&&error.code==='invalid_health');
   assert.deepEqual(paths,['/health']);
  });
 }
 await withAPI('',async api=>{
  globalThis.fetch=async()=>new Response('<html>unrelated</html>');
  await assert.rejects(api.session(),error=>error instanceof api.ApiError&&error.code==='invalid_health');
 });
});

test('a failed session response never installs its token or changes the pinned project',async()=>{
 await withAPI('',async api=>{
  let response:unknown={token:'wrong-project-token',projectId:secondProject};
  globalThis.fetch=async(path,options)=>{
   if(path==='/health')return json(health());
   assert.equal((options?.headers as Record<string,string>)['X-Editor-Project'],firstProject);
   assert.equal((options?.headers as Record<string,string>)['X-Editor-Token'],undefined);
   if(path==='/api/session')return json(response);
   return json({ok:true});
  };
  await assert.rejects(api.session(),error=>error instanceof api.ApiError&&error.code==='project_mismatch');
  await api.api('/heartbeat','POST',{});
  for(const invalid of [null,[],{token:''},{token:123}]){
   response=invalid;
   await assert.rejects(api.session(),error=>error instanceof api.ApiError&&error.code==='invalid_session');
   await api.api('/heartbeat','POST',{});
  }
 });
});

test('session handshake also works without a browser window global',async()=>{
 await withAPI(undefined,async api=>{
  globalThis.fetch=async path=>path==='/health'?json(health()):json({token:'node-test'});
  assert.equal((await api.session()).token,'node-test');
 });
});
