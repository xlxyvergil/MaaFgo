import test from 'node:test';
import assert from 'node:assert/strict';
import {acceptManualReadyCommit,createManualReadyState,grantManualReady,revokeManualReady,manualReadyKeys,syncManualReady} from './manualReady';
import type {ManualReadyState,StageCommit} from './manualReady';
import type {Json} from './model';

const plan=():Json=>({team:{onFieldSvts:[{svtId:100100},null,null]},actions:[],future:{retained:['data']}});
const grant=(data:Json,turn=0)=>['initial:0:0','initial:0:1','master:1'].reduce((state,key)=>grantManualReady(state,data,turn,key),createManualReadyState(data,turn));
const keys=(state:ManualReadyState)=>[...state.keys].sort();
const commit=(previous:Json,turnIndex=0):StageCommit=>({previous,next:{...previous,actions:[...previous.actions,{type:'skill'}]},turnIndex});

test('manual ready permissions start empty and sync is idempotent within the exact context',()=>{
 const data=plan(),state=createManualReadyState(data,0);assert.equal(state.data,data);assert.equal(state.turnIndex,0);assert.equal(state.keys.size,0);assert.equal(syncManualReady(state,data,0),state);assert.equal(manualReadyKeys(state,data,0),state.keys);
 const granted=grantManualReady(state,data,0,'initial:0:0');assert.deepEqual(keys(granted),['initial:0:0']);assert.equal(grantManualReady(granted,data,0,'initial:0:0'),granted);
});
test('successive servant and master casts consume only their own permission and carry the rest',()=>{
 const data=plan(),state=grant(data),first=commit(data),afterA=acceptManualReadyCommit(state,first,{consume:'initial:0:0'});
 assert.equal(afterA.data,first.next);assert.deepEqual(keys(afterA),['initial:0:1','master:1']);assert.deepEqual(keys(state),['initial:0:0','initial:0:1','master:1']);
 const second=commit(first.next),afterMaster=acceptManualReadyCommit(afterA,second,{consume:'master:1'});assert.deepEqual(keys(afterMaster),['initial:0:1']);assert.equal(afterMaster.data,second.next);
 const third=commit(second.next),afterB=acceptManualReadyCommit(afterMaster,third,{consume:'initial:0:1'});assert.equal(afterB.keys.size,0);
});

test('revoking one ready skill preserves every other permission and can be granted again',()=>{
 const data=plan(),before=structuredClone(data),state=grant(data),revoked=revokeManualReady(state,data,0,'initial:0:0');
 assert.deepEqual(keys(revoked),['initial:0:1','master:1']);assert.deepEqual(keys(state),['initial:0:0','initial:0:1','master:1']);assert.notEqual(revoked.keys,state.keys);assert.deepEqual(data,before);
 assert.equal(revokeManualReady(revoked,data,0,'initial:0:0'),revoked);
 const restored=grantManualReady(revoked,data,0,'initial:0:0');assert.deepEqual(keys(restored),keys(state));
 const masterRevoked=revokeManualReady(restored,data,0,'master:1');assert.deepEqual(keys(masterRevoked),['initial:0:0','initial:0:1']);
});

test('revoking in another plan or turn discards stale permissions without reviving old keys',()=>{
 const data=plan(),state=grant(data),other=structuredClone(data),changed=revokeManualReady(state,other,0,'initial:0:0');
 assert.equal(changed.data,other);assert.equal(changed.keys.size,0);assert.equal(syncManualReady(changed,data,0).keys.size,0);
 const nextTurn=revokeManualReady(state,data,1,'initial:0:0');assert.equal(nextTurn.turnIndex,1);assert.equal(nextTurn.keys.size,0);assert.equal(syncManualReady(nextTurn,data,0).keys.size,0);
});
test('a confirmed NP append keeps every unconsumed permission in the same selected turn',()=>{
 const data=plan(),state=grant(data),next={...data,actions:[{type:'attack',attacks:[{svt:0,isTD:true,card:-1}]}]},accepted=acceptManualReadyCommit(state,{previous:data,next,turnIndex:0});
 assert.deepEqual(keys(accepted),keys(state));assert.equal(accepted.data,next);assert.equal(manualReadyKeys(accepted,next,0),accepted.keys);assert.equal(syncManualReady(accepted,next,0),accepted);
});
test('cancel, failed append and unchanged-data no-op never consume or transfer permissions',()=>{
 const data=plan(),state=grant(data);assert.equal(acceptManualReadyCommit(state,null,{consume:'initial:0:0'}),state);
 assert.equal(acceptManualReadyCommit(state,{previous:data,next:data,turnIndex:0},{consume:'master:1',resetAll:true}),state);
 const unrelated=plan();assert.equal(acceptManualReadyCommit(state,{previous:unrelated,next:unrelated,turnIndex:7},{consume:'initial:0:1'}),state);assert.deepEqual(keys(state),['initial:0:0','initial:0:1','master:1']);
});
test('a confirmed Order Change clears every override while binding to the resulting plan',()=>{
 const data=plan(),state=grant(data),change=commit(data),accepted=acceptManualReadyCommit(state,change,{consume:'master:2',resetAll:true});assert.equal(accepted.keys.size,0);assert.equal(accepted.data,change.next);assert.equal(accepted.turnIndex,0);
});
test('switching turns hides permissions during render and sync never revives them on switching back',()=>{
 const data=plan(),state=grant(data);assert.equal(manualReadyKeys(state,data,1).size,0);
 const next=syncManualReady(state,data,1);assert.equal(next.keys.size,0);assert.equal(next.turnIndex,1);assert.equal(manualReadyKeys(next,data,0).size,0);assert.equal(syncManualReady(next,data,0).keys.size,0);
});
test('new references, undo and redo invalidate permissions even when contents or lengths match',()=>{
 const data=plan(),state=grant(data),equalCopy=structuredClone(data);assert.equal(manualReadyKeys(state,equalCopy,0).size,0);assert.equal(syncManualReady(state,equalCopy,0).keys.size,0);
 const change=commit(data),accepted=acceptManualReadyCommit(state,change,{consume:'initial:0:0'});assert.equal(manualReadyKeys(accepted,data,0).size,0);
 const undone=syncManualReady(accepted,data,0);assert.equal(undone.keys.size,0);const redone=syncManualReady(undone,change.next,0);assert.equal(redone.keys.size,0);
});
test('stale previous references or turn mismatches never inherit a different context permission',()=>{
 const data=plan(),state=grant(data),stale=commit(structuredClone(data)),accepted=acceptManualReadyCommit(state,stale);assert.equal(accepted.keys.size,0);assert.equal(accepted.data,stale.next);
 const otherTurn=commit(data,1),changed=acceptManualReadyCommit(state,otherTurn,{consume:'initial:0:0'});assert.equal(changed.keys.size,0);assert.equal(changed.turnIndex,1);assert.equal(changed.data,otherTurn.next);
});
test('granting in a changed member or plan context discards old keys before adding the new permission',()=>{
 const data=plan(),state=grant(data),replacement=structuredClone(data);replacement.team.onFieldSvts[0].svtId=100200;
 const fresh=grantManualReady(state,replacement,0,'initial:0:2');assert.deepEqual(keys(fresh),['initial:0:2']);assert.equal(fresh.data,replacement);
 const newTurn=grantManualReady(state,data,1,'master:0');assert.deepEqual(keys(newTurn),['master:0']);assert.equal(newTurn.turnIndex,1);
});
test('grants and accepted commits preserve input JSON and older Set snapshots without aliasing',()=>{
 const data=plan(),next=structuredClone(data),beforeData=structuredClone(data),beforeNext=structuredClone(next),state=grant(data),oldKeys=keys(state);
 const granted=grantManualReady(state,data,0,'initial:1:0'),accepted=acceptManualReadyCommit(state,{previous:data,next,turnIndex:0},{consume:'master:1'});
 assert.deepEqual(data,beforeData);assert.deepEqual(next,beforeNext);assert.deepEqual(keys(state),oldKeys);assert.notEqual(granted.keys,state.keys);assert.notEqual(accepted.keys,state.keys);assert.notEqual(granted.keys,accepted.keys);
 assert.equal(manualReadyKeys(accepted,data,0).size,0);assert.deepEqual(keys(accepted),['initial:0:0','initial:0:1']);
});
