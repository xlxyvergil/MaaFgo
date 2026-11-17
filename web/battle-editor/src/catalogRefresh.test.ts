import test from 'node:test';
import assert from 'node:assert/strict';
import {LatestCatalogRequest} from './catalogRefresh';

function deferred<T>(){
 let resolve!:(value:T)=>void,reject!:(error:unknown)=>void;
 const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});
 return {promise,resolve,reject};
}

test('a successful catalog read publishes metadata once',async()=>{
 const requests=new LatestCatalogRequest<string>(),seen:string[]=[];
 assert.equal(await requests.refresh(async signal=>{assert.equal(signal.aborted,false);return 'installed';},value=>seen.push(value)),true);
 assert.deepEqual(seen,['installed']);
});

test('a slower earlier response cannot replace the latest catalog',async()=>{
 const requests=new LatestCatalogRequest<string>(),first=deferred<string>(),seen:string[]=[];
 let firstSignal!:AbortSignal;
 const old=requests.refresh(signal=>{firstSignal=signal;return first.promise;},value=>seen.push(value));
 assert.equal(await requests.refresh(async()=> 'new',value=>seen.push(value)),true);
 assert.equal(firstSignal.aborted,true);
 first.resolve('old');
 assert.equal(await old,false);
 assert.deepEqual(seen,['new']);
});

test('closing a selector aborts its read and prevents any later callback',async()=>{
 const requests=new LatestCatalogRequest<string>(),read=deferred<string>(),seen:string[]=[];
 let signal!:AbortSignal;
 const pending=requests.refresh(value=>{signal=value;return read.promise;},value=>seen.push(value));
 requests.invalidate();
 assert.equal(signal.aborted,true);
 read.resolve('closed');
 assert.equal(await pending,false);
 assert.equal(seen.length,0);
});

test('installation invalidation protects its new catalog from an old failing read',async()=>{
 const requests=new LatestCatalogRequest<string>(),read=deferred<string>(),seen:string[]=[];
 const old=requests.refresh(()=>read.promise,value=>seen.push(value));
 requests.invalidate();
 assert.equal(await requests.refresh(async()=> 'after-install',value=>seen.push(value)),true);
 read.reject(new Error('obsolete request failed'));
 assert.equal(await old,false);
 assert.deepEqual(seen,['after-install']);
});

test('an active failure is surfaced and a later retry remains possible',async()=>{
 const requests=new LatestCatalogRequest<string>(),seen:string[]=[];
 await assert.rejects(requests.refresh(async()=>{throw new Error('service unavailable');},value=>seen.push(value)),/service unavailable/);
 assert.equal(seen.length,0);
 assert.equal(await requests.refresh(async()=> 'recovered',value=>seen.push(value)),true);
 assert.deepEqual(seen,['recovered']);
});
