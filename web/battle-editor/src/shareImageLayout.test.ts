import test from 'node:test';
import assert from 'node:assert/strict';
import {shareImageModel} from './shareImageModel';
import {measureShareImageLayout} from './shareImageRender';
import type {EncodedShareImage} from './shareImageRender';
import type {Catalog} from './model';

const catalog:Catalog={servants:[{id:1,name:'示例从者',className:'剑(saber)',rarity:5}],equips:[],mysticCodes:[]};
const model=()=>shareImageModel({team:{onFieldSvts:[{svtId:1}],backupSvts:[]},actions:[]},catalog);
function context():CanvasRenderingContext2D {
 const fake={font:'',measureText(value:string){const size=Number(/(\d+)px/.exec(fake.font)?.[1]??12);return {width:Array.from(value).reduce((sum,char)=>sum+size*(/^[\x00-\x7f]$/.test(char)?.55:1),0)};}};
 return fake as unknown as CanvasRenderingContext2D;
}
function packet(size=21):EncodedShareImage {
 const matrix=(n:number)=>Array.from({length:n},()=> '0'.repeat(n));
 return {schema:1,format:'maafgo-share-image',anchor:{matrix:matrix(21)},markers:[{index:0,total:1,matrix:matrix(size)}]};
}

test('a Grand bond effect uses a compact text row while a missing ordinary CE retains its artwork slot',()=>{
 const effect=model(),ordinary=model();
 effect.members[0].equips=[{kind:'bond-effect',label:'羁绊礼装',name:'羁绊礼装',details:'原始效果'}];
 ordinary.members[0].equips=[{label:'羁绊礼装',name:'羁绊礼装',details:'原始效果'}];
 assert.ok(measureShareImageLayout(context(),effect,packet()).formation<measureShareImageLayout(context(),ordinary,packet()).formation);
});

test('long class and title text reserve extra lines instead of colliding with following content',()=>{
 const baseline=model(),longClass=structuredClone(baseline),longTitle=structuredClone(baseline);
 longClass.members[0].className='降(foreigner) · '.repeat(30);
 longTitle.title='很长的方案名称与从者组合 · '.repeat(18);
 const base=measureShareImageLayout(context(),baseline,packet());
 assert.ok(measureShareImageLayout(context(),longClass,packet()).formation>base.formation);
 assert.ok(measureShareImageLayout(context(),longTitle,packet()).header>base.header);
});

test('QR columns account for real footer padding and preserve quiet space at the boundary',()=>{
 assert.equal(measureShareImageLayout(context(),model(),packet(95)).qrColumns,2);
 assert.equal(measureShareImageLayout(context(),model(),packet(101)).qrColumns,1);
});

test('oversized visible content still fails explicitly instead of being cropped',()=>{
 const oversized=model();oversized.members[0].className='超长信息'.repeat(4000);
 assert.throws(()=>measureShareImageLayout(context(),oversized,packet()),/超出单张图片限制/);
});
