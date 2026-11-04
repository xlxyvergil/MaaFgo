import test from 'node:test';
import assert from 'node:assert/strict';
import {attackCardDropTarget,attackCardTarget,finishAttackCardDrag,startAttackCardDrag,updateAttackCardDrag} from './attackCardDrag';
test('attack cards only start from occupied first-three slots within their own attack group',()=>{
 const occupied=[true,false,true,true];assert.equal(attackCardTarget(0,occupied,true),0);assert.equal(attackCardTarget(2,occupied,true),2);
 for(const index of [1,3,-1,null,1.5,NaN])assert.equal(attackCardTarget(index,occupied,true),null);assert.equal(attackCardTarget(2,occupied,false),null);
});
test('all first-three slots accept drops including empty default-card positions in the same group',()=>{
 for(const index of [0,1,2])assert.equal(attackCardDropTarget(index,true),index);
 for(const index of [3,-1,null,1.5,NaN])assert.equal(attackCardDropTarget(index,true),null);
 assert.equal(attackCardDropTarget(2,false),null);
});
test('card drag uses the same six-pixel click threshold and keeps clicks from swapping',()=>{
 const click=updateAttackCardDrag(startAttackCardDrag(1,0,20,20),23,24,2);assert.equal(click.active,false);assert.equal(click.target,null);assert.equal(finishAttackCardDrag(click,[true,true,true]),null);
 const active=updateAttackCardDrag(click,26,20,2);assert.deepEqual(finishAttackCardDrag(active,[true,true,true]),[0,2]);
});
test('a single card can move to either default-card position without allowing empty sources',()=>{
 const active=updateAttackCardDrag(startAttackCardDrag(1,0,20,20),100,100,2);
 assert.deepEqual(finishAttackCardDrag(active,[true,false,false]),[0,2]);
 assert.deepEqual(finishAttackCardDrag({...active,target:1},[true,false,false]),[0,1]);
 assert.equal(finishAttackCardDrag({...active,from:1},[true,false,false]),null);
 assert.equal(finishAttackCardDrag(active,[false,false,false]),null);
});
test('same source, outside-group and invalid source releases do not exchange',()=>{
 const active=updateAttackCardDrag(startAttackCardDrag(1,0,20,20),100,100,2);assert.deepEqual(finishAttackCardDrag(active,[true,true,true]),[0,2]);
 assert.equal(finishAttackCardDrag({...active,target:0},[true,true,true]),null);assert.equal(finishAttackCardDrag({...active,target:null},[true,true,true]),null);assert.equal(finishAttackCardDrag({...active,target:3},[true,true,true]),null);assert.equal(finishAttackCardDrag({...active,from:3},[true,true,true,true]),null);
});
