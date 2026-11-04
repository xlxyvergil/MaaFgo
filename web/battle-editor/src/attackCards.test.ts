import test from 'node:test';
import assert from 'node:assert/strict';
import type {Json} from './model';
import {isAutoAttackCard,removeAttackCard,setAttackCard,swapAttackCardPositions} from './attackCards';

test('swaps whole records and preserves targets, recolor indexes, unknown fields and imported tail',()=>{
 const np={svt:0,isTD:true,card:-1,future:{x:1}},face={svt:1,isTD:false,card:4,cardType:2,future:['keep']},third={svt:2,card:0},tail={future:'fourth'};
 const attack={type:'attack',attacks:[np,face,third,tail],options:{enemyTarget:2},future:'action'};
 swapAttackCardPositions(attack,0,2);
 assert.deepEqual(attack,{type:'attack',attacks:[third,face,np,tail],options:{enemyTarget:2},future:'action'});
 assert.equal(attack.attacks[2],np);
 swapAttackCardPositions(attack,0,2);
 assert.deepEqual(attack.attacks,[np,face,third,tail]);
});
test('empty, malformed, fourth, out-of-range and noninteger drops fail without mutation',()=>{
 for(const [from,to] of [[0,3],[0,-1],[0,0.5],[NaN,1],[0,1]]){
  const attack={type:'attack',attacks:[{svt:0},null]},before=JSON.stringify(attack);
  assert.throws(()=>swapAttackCardPositions(attack,from,to));assert.equal(JSON.stringify(attack),before);
 }
 for(const attacks of [[{svt:0},null],[{svt:0},[]],[{svt:0},'unknown']]){
  const attack={type:'attack',attacks},before=JSON.stringify(attack);
  assert.throws(()=>swapAttackCardPositions(attack,0,1));assert.equal(JSON.stringify(attack),before);
 }
 assert.throws(()=>swapAttackCardPositions({type:'skill',attacks:[{},{}]},0,1));
});

test('one NP can move to either later position and back without losing any card fields',()=>{
 for(const target of [1,2]){
  const np={svt:0,isTD:true,card:-1,future:{keep:['np']}},options={enemyTarget:2},attack:Json={type:'attack',attacks:[np],options};
  const cards=attack.attacks;
  swapAttackCardPositions(attack,0,target);
  assert.deepEqual(attack.attacks,[...Array.from({length:target},()=>({isAuto:true})),np]);
  assert.equal(attack.attacks[target],np);assert.equal(attack.attacks,cards);assert.equal(attack.options,options);
  if(target===2)assert.notEqual(attack.attacks[0],attack.attacks[1]);
  swapAttackCardPositions(attack,target,0);
  assert.deepEqual(attack.attacks,[np]);assert.equal(attack.attacks[0],np);
 }
});

test('two cards exchange with an automatic slot and imported automatic tail stays untouched',()=>{
 const np={svt:0,isTD:true,card:-1},face={svt:1,isTD:false,card:4,cardType:2},tail={isAuto:true},extra={future:'fifth'};
 const attack:Json={type:'attack',attacks:[np,face,{isAuto:true},tail,extra]};
 swapAttackCardPositions(attack,0,2);
 assert.deepEqual(attack.attacks,[{isAuto:true},face,np,tail,extra]);
 assert.equal(attack.attacks[3],tail);assert.equal(attack.attacks[4],extra);
 swapAttackCardPositions(attack,1,2);
 assert.deepEqual(attack.attacks,[{isAuto:true},np,face,tail,extra]);
 const tailOnly:Json={type:'attack',attacks:[np,{isAuto:true},{isAuto:true},tail]};
 removeAttackCard(tailOnly,0);
 assert.deepEqual(tailOnly.attacks,[{isAuto:true},{isAuto:true},tail]);assert.equal(tailOnly.attacks[2],tail);
});

test('moving cards trims only pure automatic tail records and retains unknown placeholder fields',()=>{
 const np={svt:0,isTD:true,card:-1},future={isAuto:true,future:{keep:1}},attack:Json={type:'attack',attacks:[{isAuto:true},np,future]};
 swapAttackCardPositions(attack,1,0);
 assert.deepEqual(attack.attacks,[np,{isAuto:true},future]);assert.equal(attack.attacks[2],future);
 removeAttackCard(attack,0);
 assert.deepEqual(attack.attacks,[{isAuto:true},future]);assert.equal(attack.attacks[1],future);
 const clean:Json={type:'attack',attacks:[{isAuto:true},{isAuto:true},np]};
 removeAttackCard(clean,2);assert.deepEqual(clean.attacks,[]);
});

test('automatic cards require a boolean true and cannot be dragged or removed directly',()=>{
 for(const card of [undefined,null,0,[],{}, {isAuto:false},{isAuto:'true'},{isAuto:1}])assert.equal(isAutoAttackCard(card),false);
 assert.equal(isAutoAttackCard({isAuto:true}),true);
 assert.equal(isAutoAttackCard({isAuto:true,future:1}),true);
 for(const card of [{isAuto:true},{isAuto:true,future:1},{isAuto:'true'},{isAuto:null},{isAuto:1}]){
  const attack:Json={type:'attack',attacks:[card,{svt:0}]},before=structuredClone(attack);
  assert.throws(()=>swapAttackCardPositions(attack,0,1));assert.deepEqual(attack,before);
  assert.throws(()=>removeAttackCard(attack,0));assert.deepEqual(attack,before);
 }
});

test('unknown automatic flags and malformed existing targets reject before extending or changing cards',()=>{
 for(const bad of [null,[],0,'unknown',{isAuto:'true'},{isAuto:0},{isAuto:null}]){
  const attack:Json={type:'attack',attacks:[{svt:0},bad]},before=structuredClone(attack);
  assert.throws(()=>swapAttackCardPositions(attack,0,1));assert.deepEqual(attack,before);
  assert.throws(()=>setAttackCard(attack,{svt:2,isTD:true,card:-1},1));assert.deepEqual(attack,before);
 }
 const sparse:Json={type:'attack',attacks:[{svt:0},...Array(1)]};
 delete sparse.attacks[1];
 assert.throws(()=>swapAttackCardPositions(sparse,0,1));assert.equal(1 in sparse.attacks,false);
});

test('adding fills automatic slots before appending and preserves their unknown fields',()=>{
 const np={svt:0,isTD:true,card:-1},placeholder={isAuto:true,future:{keep:1}},face={svt:1,isTD:false,card:4,cardType:2};
 const attack:Json={type:'attack',attacks:[{isAuto:true},placeholder,np]};
 setAttackCard(attack,face);
 assert.deepEqual(attack.attacks,[face,placeholder,np]);
 setAttackCard(attack,{svt:2,isTD:true,card:-1});
 assert.deepEqual(attack.attacks,[face,{future:{keep:1},svt:2,isTD:true,card:-1},np]);
 assert.equal((attack.attacks[1] as Json).future,placeholder.future);
 const short:Json={type:'attack',attacks:[]};
 setAttackCard(short,np);setAttackCard(short,face);
 assert.deepEqual(short.attacks,[np,face]);
});

test('updating a card merges unknown fields, removes auto and drops face color when changed to NP',()=>{
 const attack:Json={type:'attack',attacks:[{svt:1,isTD:false,card:4,cardType:2,future:{keep:1},isAuto:false}]};
 setAttackCard(attack,{svt:0,isTD:true,card:-1},0);
 assert.deepEqual(attack.attacks,[{svt:0,isTD:true,card:-1,future:{keep:1}}]);
 setAttackCard(attack,{svt:2,isTD:false,card:3,cardType:1},0);
 assert.deepEqual(attack.attacks,[{svt:2,isTD:false,card:3,cardType:1,future:{keep:1}}]);
});

test('adding cannot create a fourth card while imported extra records survive filling a prior hole',()=>{
 const tail={isAuto:true,future:'fourth'},extra={future:'fifth'},attack:Json={type:'attack',attacks:[{isAuto:true},{svt:1},{svt:2},tail,extra]};
 setAttackCard(attack,{svt:0,isTD:true,card:-1});
 assert.deepEqual(attack.attacks,[{svt:0,isTD:true,card:-1},{svt:1},{svt:2},tail,extra]);
 const before=structuredClone(attack);
 assert.throws(()=>setAttackCard(attack,{svt:1,isTD:false,card:0,cardType:2}));assert.deepEqual(attack,before);
 assert.equal(attack.attacks[3],tail);assert.equal(attack.attacks[4],extra);
});

test('invalid additions or update positions preserve their complete input',()=>{
 for(const index of [-1,0.5,NaN,Infinity,1,3,4]){
  const attack:Json={type:'attack',attacks:[{svt:0}]},before=structuredClone(attack);
  assert.throws(()=>setAttackCard(attack,{svt:1},index));assert.deepEqual(attack,before);
 }
 for(const card of [null,[],0,'unknown',{isAuto:true},{isAuto:'true'},{isAuto:null}]){
  const attack:Json={type:'attack',attacks:[{svt:0}]},before=structuredClone(attack);
  assert.throws(()=>setAttackCard(attack,card as Json));assert.deepEqual(attack,before);
 }
 for(const attack of [null,{}, {type:'skill',attacks:[]}, {type:'attack'}, {type:'attack',attacks:null}]){
  const before=structuredClone(attack);assert.throws(()=>setAttackCard(attack as Json,{svt:0}));assert.deepEqual(attack,before);
 }
});
test('same position is a no-op and two cards can swap without a third card',()=>{
 const first={svt:0},second={svt:2},attack={type:'attack',attacks:[first,second]};
 swapAttackCardPositions(attack,0,0);assert.deepEqual(attack.attacks,[first,second]);
 swapAttackCardPositions(attack,0,1);assert.deepEqual(attack.attacks,[second,first]);
});

test('removing a NP or face card shifts later complete records and preserves the imported tail and options',()=>{
 for(const index of [0,1,2]){
  const np={svt:0,isTD:true,card:-1,future:{np:1}},face={svt:1,isTD:false,card:4,cardType:2,future:['face']},
   third={svt:2,card:0},tail={future:'fourth'},extra={future:'fifth'},options={enemyTarget:2,future:{keep:1}};
  const cards=[np,face,third,tail,extra],attack:Json={type:'attack',attacks:[...cards],options,future:{action:1}};
  removeAttackCard(attack,index);
  assert.deepEqual(attack,{type:'attack',attacks:cards.filter((_,i)=>i!==index),options,future:{action:1}});
  assert.equal(attack.options,options);assert.equal(attack.attacks.at(-2),tail);assert.equal(attack.attacks.at(-1),extra);
 }
});

test('removing the final card leaves an empty attack action with its exact options',()=>{
 const options={enemyTarget:0,future:['keep']},attack:Json={type:'attack',attacks:[{svt:0,isTD:true,card:-1}],options,future:7};
 const originalArray=attack.attacks;
 removeAttackCard(attack,0);assert.deepEqual(attack,{type:'attack',attacks:[],options,future:7});
 assert.equal(attack.attacks,originalArray);assert.equal(attack.options,options);
 assert.throws(()=>removeAttackCard(attack,0));
});

test('invalid card removal never rewrites unsupported actions, automatic slots or malformed records',()=>{
 for(const attack of [null,{}, {type:'skill',attacks:[{}]}, {type:'attack'}, {type:'attack',attacks:null},
  {type:'attack',attacks:{}}, {type:'attack',attacks:[null]}, {type:'attack',attacks:[[]]},
  {type:'attack',attacks:['unknown']}, {type:'attack',attacks:[0]}]){
  const before=structuredClone(attack);assert.throws(()=>removeAttackCard(attack as Json,0));assert.deepEqual(attack,before);
 }
 for(const index of [-1,0.5,NaN,Infinity,3,4]){
  const attack:Json={type:'attack',attacks:[{svt:0},{svt:1},{svt:2},{future:'fourth'}]},before=structuredClone(attack);
  assert.throws(()=>removeAttackCard(attack,index));assert.deepEqual(attack,before);
 }
 const short:Json={type:'attack',attacks:[{svt:0}]};assert.throws(()=>removeAttackCard(short,1));assert.deepEqual(short.attacks,[{svt:0}]);
 const sparse:Json={type:'attack',attacks:Array(2)};assert.throws(()=>removeAttackCard(sparse,0));assert.equal(0 in sparse.attacks,false);
});
