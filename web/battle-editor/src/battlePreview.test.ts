import test from 'node:test';
import assert from 'node:assert/strict';
import {clone} from './model';
import type {Catalog,Json} from './model';
import {previewTurn,cooldownBounds,npAt,skillTargetMode,allowedAllyTargets,hasEnemyTarget} from './battlePreview';

const cd=[8,8,8,8,8,7,7,7,7,6];
const skill=(id:number,num:number,extra:Json={})=>({id,num,name:`技能 ${id}`,target:'none',targets:['self'],coolDown:cd,...extra});
const catalog:Catalog={equips:[],servants:[
  {id:1,name:'甲',aliases:[1001],skills:[skill(11,1),skill(12,2),skill(13,3)],
    skillVariants:[skill(91,1,{coolDown:[9,9,9,9,9,8,8,8,8,7],name:'旧技能'})],
    np:{id:101,name:'默认宝具',cardType:1},npVariants:[{id:102,name:'旧宝具',cardType:2}]},
  {id:2,name:'乙',skills:[skill(21,1),skill(22,2),skill(23,3)]},
],mysticCodes:[{id:20,name:'战斗服',orderChange:true,skills:[skill(31,1),skill(32,2),skill(33,3,{coolDown:[15,15,15,15,15,14,14,14,14,13]})]}]};
const fixture=():Json=>({team:{onFieldSvts:[{svtId:1,skillLvs:[10,10,10]}, {svtId:1,supportType:'friend',skillLvs:[10,10,10]},null],backupSvts:[{svtId:2,skillLvs:[10,10,10]},null,null],mysticCode:{mysticCodeId:20,level:10}},actions:[]});
const cast=(svt:number|null=0,index=0)=>({type:'skill',svt,skill:index});
const attack=()=>({type:'attack',attacks:[]});

test('preview is pure and its instance keys keep identical self/support copies independent',()=>{
  const data=fixture();data.actions=[cast(),attack()];data.unknown={nested:[null,{v:true}]};
  const before=clone(data),catalogBefore=clone(catalog),view=previewTurn(data,catalog,0);
  assert.deepEqual(data,before);assert.deepEqual(catalog,catalogBefore);
  assert.equal(view.front[0].key,'initial:0');assert.equal(view.front[1].key,'initial:1');
  assert.equal(view.front[0].skills[0].remainingMax,6);assert.equal(view.front[1].skills[0].remainingMax,0);
  assert.equal(view.front[0].skills[0].ready,false);assert.equal(view.front[1].skills[0].ready,true);
  assert.equal(view.beforeAttackIndex,1);
});

test('six-CD use on turn 1 is five on turn 2 and ready on turn 7, never decremented early',()=>{
  const data=fixture();data.actions=[cast(),...Array.from({length:7},attack)];
  assert.equal(previewTurn(data,catalog,0).front[0].skills[0].remainingMax,6);
  const second=previewTurn(data,catalog,1).front[0].skills[0];
  assert.equal(second.remainingMax,5);assert.equal(second.used,true);assert.equal(second.usedThisTurn,false);
  assert.equal(previewTurn(data,catalog,5).front[0].skills[0].remainingMax,1);
  const seventh=previewTurn(data,catalog,6).front[0].skills[0];
  assert.equal(seventh.remainingMax,0);assert.equal(seventh.ready,true);
});

test('unfinished turn previews the end of its skills and uses actions.length as insertion index',()=>{
  const data=fixture();data.actions=[attack(),cast(1,1)];
  const view=previewTurn(data,catalog,1);
  assert.equal(view.beforeAttackIndex,2);assert.equal(view.front[1].skills[1].remainingMax,6);
  assert.equal(view.front[0].skills[1].remainingMax,0);
});

test('known explicit skill variant determines name and cooldown without rewriting the member',()=>{
  const data=fixture();data.team.onFieldSvts[0].skillIds=[91];data.actions=[cast(),attack()];
  const state=previewTurn(data,catalog,0).front[0].skills[0];
  assert.equal(state.skill?.name,'旧技能');assert.equal(state.remainingMax,7);assert.equal(state.source,'exact');
  assert.deepEqual(data.team.onFieldSvts[0].skillIds,[91]);
});

test('unknown variant never borrows default cooldown, icon or targeting information',()=>{
  const data=fixture();data.team.onFieldSvts[0].skillIds=[999];data.actions=[cast(),attack()];
  const view=previewTurn(data,catalog,0),state=view.front[0].skills[0];
  assert.equal(state.skill?.id,999);assert.equal(state.skill?.icon,undefined);
  assert.equal(state.remainingMax,null);assert.equal(state.remainingMin,null);assert.equal(state.ready,false);
  assert.equal(state.source,'unknown');assert.equal(skillTargetMode(state.skill),'unknown');
  assert.equal(view.warnings[0].code,'unknown-cooldown');
});

test('unspecified or zero level shows exact real range, while invalid levels and partial tables stay unknown',()=>{
  for(const level of [undefined,null,0])assert.deepEqual(cooldownBounds(skill(1,1),level),{remainingMin:6,remainingMax:8,source:'level-range'});
  assert.deepEqual(cooldownBounds(skill(1,1),6),{remainingMin:7,remainingMax:7,source:'exact'});
  for(const level of [11,-1,1.5,'10',NaN])assert.equal(cooldownBounds(skill(1,1),level).source,'unknown');
  assert.equal(cooldownBounds({coolDown:[6,6]},0).source,'unknown');
  assert.equal(cooldownBounds({coolDown:[6,-1,6,6,6,6,6,6,6,6]},0).source,'unknown');
  assert.equal(cooldownBounds(undefined,10).source,'unknown');
});

test('unknown-level cooldown ranges tick together and become ready only when the upper bound reaches zero',()=>{
  const data=fixture();delete data.team.onFieldSvts[0].skillLvs;data.actions=[cast(),...Array.from({length:9},attack)];
  const first=previewTurn(data,catalog,0).front[0].skills[0];
  assert.deepEqual([first.remainingMin,first.remainingMax,first.source],[6,8,'level-range']);
  const seventh=previewTurn(data,catalog,6).front[0].skills[0];
  assert.deepEqual([seventh.remainingMin,seventh.remainingMax,seventh.ready],[0,2,false]);
  assert.equal(previewTurn(data,catalog,8).front[0].skills[0].ready,true);
});

test('Order Change carries member instances and pauses cooldown while the member is in backup',()=>{
  const data=fixture();data.actions=[cast(),cast(null,2),attack(),attack()];
  data.delegate={replaceMemberIndexes:[[0,0]],future:{keep:null}};
  const view=previewTurn(data,catalog,1);
  assert.equal(view.front[0].key,'initial:3');assert.equal(view.front[0].member?.svtId,2);
  assert.equal(view.roster[3].key,'initial:0');assert.equal(view.roster[3].skills[0].remainingMax,6);
  assert.equal(view.roster[3].skills[0].usedThisTurn,false);
  assert.equal(view.master.skills[2].remainingMax,12);
  assert.deepEqual(data.delegate,{replaceMemberIndexes:[[0,0]],future:{keep:null}});
});

test('a returning member resumes its own cooldown while the outgoing member keeps separate state',()=>{
  const data=fixture();data.actions=[cast(),cast(null,2),attack(),cast(0,1),cast(null,2),attack(),attack()];
  data.delegate={replaceMemberIndexes:[[0,0],[0,0]]};
  const view=previewTurn(data,catalog,2);
  assert.equal(view.front[0].key,'initial:0');assert.equal(view.front[0].skills[0].remainingMax,5);
  assert.equal(view.roster[3].key,'initial:3');assert.equal(view.roster[3].skills[1].remainingMax,6);
  assert.equal(view.master.skills[2].remainingMax,12);
  assert.ok(view.warnings.some(w=>w.code==='cooldown-skill'&&w.actionIndex===4));
});

test('legacy absolute backup indices work and invalid or empty swaps never guess member movement',()=>{
  const data=fixture();data.actions=[cast(null,2),attack()];data.delegate={replaceMemberIndexes:[[0,3]]};
  assert.equal(previewTurn(data,catalog,0).front[0].key,'initial:3');
  for(const pair of [[0,4],[3,0],[-1,0],[0,6],[0,0.5],null]){
    data.delegate.replaceMemberIndexes=[pair];const view=previewTurn(data,catalog,0);
    assert.equal(view.front[0].key,'initial:0');assert.ok(view.warnings.some(w=>w.code==='invalid-swap'));
  }
});

test('repeated servant and master casts remain in order, reset their own CD and generate preview warnings',()=>{
  const data=fixture();data.actions=[cast(),cast(),cast(),cast(null),cast(null),attack()];
  const before=clone(data),view=previewTurn(data,catalog,0);
  assert.deepEqual(data,before);assert.equal(view.front[0].skills[0].lastActionIndex,2);
  assert.equal(view.front[0].skills[0].remainingMax,6);assert.equal(view.master.skills[0].lastActionIndex,4);
  assert.equal(view.warnings.filter(w=>w.code==='repeat-skill').length,3);
});

test('deleting or moving a cast across Order Change recomputes the actual recipient rather than cached servant IDs',()=>{
  const data=fixture();data.actions=[cast(),cast(null,2),attack()];data.delegate={replaceMemberIndexes:[[0,0]]};
  assert.equal(previewTurn(data,catalog,0).roster[3].skills[0].remainingMax,6);
  [data.actions[0],data.actions[1]]=[data.actions[1],data.actions[0]];
  const moved=previewTurn(data,catalog,0);
  assert.equal(moved.front[0].skills[0].remainingMax,6);assert.equal(moved.roster[3].skills[0].remainingMax,0);
  data.actions.splice(1,1);
  assert.equal(previewTurn(data,catalog,0).front[0].skills[0].remainingMax,0);
});

test('missing NPC catalog facts and empty slots never borrow another servant skill data',()=>{
  const data=fixture();data.team.onFieldSvts[0]={svtId:987654,supportType:'npc'};data.actions=[cast(),cast(2),attack()];
  const view=previewTurn(data,catalog,0);
  assert.equal(view.front[0].servant,undefined);assert.equal(view.front[0].skills[0].skill,undefined);
  assert.equal(view.front[0].skills[0].remainingMax,null);assert.equal(view.front[2].skills[0].ready,false);
  assert.ok(view.warnings.some(w=>w.code==='missing-actor'&&w.actionIndex===1));
});

test('NP selection honors explicit variants and keeps unknown NP color unknown',()=>{
  const servant=catalog.servants[0];
  assert.equal(npAt({svtId:1},servant)?.cardType,1);assert.equal(npAt({svtId:1,tdId:102},servant)?.cardType,2);
  assert.deepEqual(npAt({svtId:1,tdId:999},servant),{id:999,name:'宝具 #999'});
  assert.equal(npAt({svtId:987},undefined),undefined);
});

test('an improved default never rewrites an imported explicit placeholder NP or future fields',()=>{
  const actual={id:21,name:'实际宝具',cardType:2},placeholder={id:99,name:'？？？',cardType:2},servant={id:1,name:'测试从者',np:actual,npVariants:[actual,placeholder]};
  const member:Json={svtId:1,tdId:99,future:{unchanged:[null,0]}},before=clone(member),metadata=clone(servant);
  assert.equal(npAt({svtId:1},servant),actual);assert.equal(npAt(member,servant),placeholder);
  assert.deepEqual(JSON.parse(JSON.stringify(member)),before);assert.deepEqual(servant,metadata);
  assert.deepEqual(npAt({...member,tdId:12345},servant),{id:12345,name:'宝具 #12345'});
});

test('target helpers distinguish a chosen ally, selected enemy, enemy all and unknown data',()=>{
  const roster=previewTurn(fixture(),catalog,0).roster;
  assert.equal(skillTargetMode({target:'ally'}),'ally');assert.equal(skillTargetMode({target:'none'}),'none');
  assert.equal(skillTargetMode(undefined),'unknown');
  assert.deepEqual(allowedAllyTargets({target:'ally',targets:['ptOne']},0,roster),[0,1]);
  assert.deepEqual(allowedAllyTargets({target:'ally',targets:['ptOneOther']},0,roster),[1]);
  assert.deepEqual(allowedAllyTargets({target:'ally',targets:['ptOneOther']},-1,roster),[0,1]);
  assert.deepEqual(allowedAllyTargets({target:'ally',targets:['ptOne','ptOneOther']},0,roster),[0,1]);
  assert.deepEqual(allowedAllyTargets(undefined,0,roster),[0,1]);
  assert.equal(hasEnemyTarget({target:'none',targets:['enemy','self']}),true);
  assert.equal(hasEnemyTarget({targets:['enemyAll']}),false);assert.equal(hasEnemyTarget(undefined),false);
});

test('empty plans clamp invalid turn indices and zero-CD actions still require explicit manual repeat in the same turn',()=>{
  for(const index of [-1,NaN,7]){
    const view=previewTurn(fixture(),catalog,index);assert.equal(view.turnIndex,0);assert.equal(view.beforeAttackIndex,0);
  }
  const zero=clone(catalog);(zero.servants[0].skills![0] as Json).coolDown=Array(10).fill(0);
  const data=fixture();data.actions=[cast(),attack(),attack()];
  const used=previewTurn(data,zero,0).front[0].skills[0];
  assert.equal(used.remainingMax,0);assert.equal(used.ready,false);assert.equal(used.usedThisTurn,true);
  assert.equal(previewTurn(data,zero,1).front[0].skills[0].ready,true);
});

test('essence-only support keeps an occupied target but never invents skills cooldowns or NP metadata',()=>{
  for(const svtId of [undefined,null,0]){
    const data=fixture();data.team.onFieldSvts[0]={supportType:'friend',svtId,equip1:{id:99},skillIds:[11,12,13],tdId:101,skillLvs:[10,10,10],future:{keep:null}};
    data.actions=[attack(),attack(),attack()];const before=clone(data),view=previewTurn(data,catalog,2);
    assert.equal(view.front[0].servant,undefined);
    for(const state of view.front[0].skills){
      assert.equal(state.skill,undefined);assert.equal(state.ready,false);assert.equal(state.used,false);
      assert.equal(state.remainingMin,null);assert.equal(state.remainingMax,null);assert.equal(state.source,'unknown');
    }
    assert.equal(npAt(view.front[0].member,catalog.servants[0]),undefined);
    assert.deepEqual(allowedAllyTargets({target:'ally'},1,view.roster),[0,1]);
    assert.deepEqual(data,before);
  }
});

test('existing skill references to essence-only support remain intact and produce a clear preview warning',()=>{
  const data=fixture();data.team.onFieldSvts[0]={supportType:'friend',equip1:{id:99},skillIds:[91]};
  data.actions=[cast(0),attack()];const before=clone(data),view=previewTurn(data,catalog,0);
  assert.ok(view.warnings.some(w=>w.code==='missing-actor'&&w.actionIndex===0&&w.message.includes('仅要求礼装')));
  assert.equal(view.front[0].skills[0].used,false);assert.equal(view.front[0].skills[0].skill,undefined);
  assert.deepEqual(data,before);
});

test('swapping a known member into an essence-only support position enables only the current known actor',()=>{
  const data=fixture();data.team.onFieldSvts[0]={supportType:'friend',equip1:{id:99}};
  data.actions=[cast(null,2),cast(0),attack(),cast(null,2),cast(0),attack()];
  data.delegate={replaceMemberIndexes:[[0,0],[0,0]]};const before=clone(data);
  const first=previewTurn(data,catalog,0);
  assert.equal(first.front[0].member?.svtId,2);assert.equal(first.front[0].skills[0].skill?.id,21);
  assert.equal(first.front[0].skills[0].used,true);assert.equal(first.roster[3].skills[0].ready,false);
  assert.equal(first.warnings.some(w=>w.code==='invalid-swap'),false);
  const second=previewTurn(data,catalog,1);
  assert.equal(second.front[0].member?.supportType,'friend');assert.equal(second.front[0].skills[0].ready,false);
  assert.equal(second.front[0].skills[0].remainingMax,null);
  assert.ok(second.warnings.some(w=>w.code==='missing-actor'&&w.actionIndex===4));
  assert.deepEqual(data,before);
});

test('missing or invalid servant identity cannot use a leftover chosen NP even when metadata is supplied',()=>{
  for(const svtId of [undefined,null,0,-1,1.5,'1',false,true]){
    const member={svtId,supportType:'friend',tdId:102};const before=clone(member);
    assert.equal(npAt(member,catalog.servants[0]),undefined);assert.deepEqual(member,before);
  }
});
