let token='';
let projectId:string|undefined;
let projectFromUrlRead=false;
const projectPattern=/^[a-f0-9]{64}$/;
export class ApiError extends Error {code:string;details:any;status:number;constructor(status:number,body:any){super(body?.error?.message||`服务请求失败 (${status})`);this.status=status;this.code=body?.error?.code;this.details=body?.error?.details;}}
function sessionHeaders():Record<string,string> {return {...(projectId?{'X-Editor-Project':projectId}:{}),...(token?{'X-Editor-Token':token}:{})};}
function projectMismatch(actualProject:unknown):ApiError {
 return new ApiError(409,{error:{code:'project_mismatch',message:'当前端口已切换到另一份 MaaFGO，旧页面不会连接或保存到新目录。当前内容仍保留，可下载 JSON 备份后从启动工具重新打开。',details:{expectedProject:projectId,actualProject}}});
}
function bindProjectFromUrl():void {
 if(projectFromUrlRead)return;
 const values=typeof window==='undefined'?[]:new URLSearchParams(window.location.search).getAll('project');
 if(values.length>1||(values.length===1&&!projectPattern.test(values[0])))throw new ApiError(400,{error:{code:'invalid_project',message:'编辑器地址中的安装目录标识无效，请从启动工具重新打开。'}});
 projectId=values[0];projectFromUrlRead=true;
}
export async function api<T=any>(path:string,method='GET',data?:any,signal?:AbortSignal):Promise<T> {
 const res=await fetch(`/api${path}`,{method,headers:{...sessionHeaders(),...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data),signal,credentials:'same-origin'});
 const body=await res.json();if(!res.ok)throw new ApiError(res.status,body);return body;
}
export async function session(){
 token='';bindProjectFromUrl();
 const res=await fetch('/health',{cache:'no-store',credentials:'same-origin'});
 let health:any;try{health=await res.json();}catch{throw new ApiError(502,{error:{code:'invalid_health',message:'本地服务没有返回有效的编队编辑器身份。'}});}
 if(!res.ok)throw new ApiError(res.status,health);
 if(health?.service!=='maafgo-battle-editor'||health?.protocolVersion!==1||typeof health?.projectId!=='string'||!projectPattern.test(health.projectId))throw new ApiError(409,{error:{code:'invalid_health',message:'当前端口不是兼容的 MaaFGO 编队编辑器，请从启动工具重新打开。'}});
 if(projectId&&projectId!==health.projectId)throw projectMismatch(health.projectId);
 projectId??=health.projectId;
 const result=await api('/session');
 if(result==null||typeof result!=='object'||Array.isArray(result))throw new ApiError(502,{error:{code:'invalid_session',message:'编辑器会话响应无效，请重新连接。'}});
 if(result.projectId!=null&&result.projectId!==projectId)throw projectMismatch(result.projectId);
 if(typeof result.token!=='string'||!result.token)throw new ApiError(502,{error:{code:'invalid_session',message:'编辑器会话响应无效，请重新连接。'}});
 token=result.token;return result;
}

/** Upload only to the authenticated loopback editor; no base64 JSON expansion. */
export async function apiImage(blob:Blob,mime:string,signal?:AbortSignal):Promise<any> {
 const res=await fetch('/api/import-image',{method:'POST',headers:{'Content-Type':mime,...sessionHeaders()},body:blob,signal,credentials:'same-origin'});
 const body=await res.json();if(!res.ok)throw new ApiError(res.status,body);return body;
}

/** Card artwork uses the same authenticated session as JSON, without putting its token in a URL. */
export async function apiPNG(path:string,signal?:AbortSignal):Promise<{blob:Blob;portraitSource:string|null}> {
 if(!/^\/api\/(?!\/)/.test(path)||path.includes('\\'))throw new Error('卡图地址必须来自本地服务。');
 const res=await fetch(path,{method:'GET',headers:sessionHeaders(),signal,credentials:'same-origin'});
 if(!res.ok){let body:any;try{body=await res.json();}catch{}throw new ApiError(res.status,body);}
 if(res.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase()!=='image/png')throw new Error('卡图服务返回了无法识别的图片格式。');
 const blob=await res.blob();if(!blob.size)throw new Error('卡图服务返回了空图片。');
 return {blob,portraitSource:res.headers.get('X-Editor-Card-Portrait-Source')??res.headers.get('X-Editor-NP-Portrait-Source')};
}
