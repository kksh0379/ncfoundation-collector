const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('static/js/app.js','utf8');
const code=src.slice(src.indexOf('const SCRAP_REMOVALS'),src.indexOf('// 위임: 원문 링크'));
const tick=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve()};
function setup(reduced=false){
 const saved=new Set(),animations=[];
 const cards=['a','b'].map(key=>({dataset:{key},isConnected:true,contains:()=>false,classList:{add(){}},setAttribute(){},appendChild(){},getBoundingClientRect:()=>({height:160}),animate(frames,options){let resolve,reject;const finished=new Promise((r,j)=>{resolve=r;reject=j});const a={key,frames,options,finished,resolve,cancel(){reject(Error('cancel'))}};animations.push(a);return a}}));
 const view={hidden:false},list={querySelectorAll:selector=>selector.includes('scrap-btn')?[]:cards};
 const ctx=vm.createContext({renders:0,document:{activeElement:{},getElementById:id=>id==='view-scrap'?view:id==='scrap-list'?list:null,querySelectorAll:()=>[],createElement:()=>({})},window:{matchMedia:()=>({matches:reduced})},getComputedStyle:()=>({marginBottom:'12px',paddingTop:'16px',paddingBottom:'16px'}),isScrapped:key=>saved.has(key),updateScrapBadge(){},updateScrapButton(){},setTimeout});
 vm.runInContext(code+'\nfunction renderScraps(){if(!SCRAP_REMOVALS.size) renders++}',ctx);
 return {ctx,saved,cards,view,animations};
}
test('cancelled scrap stays present through notice, fade and collapse',async()=>{
 const s=setup();assert.equal(s.ctx.animateScrapRemoval('a'),true);assert.equal(s.ctx.renders,0);assert.ok(s.cards[0].inert);assert.equal(s.animations[0].options.duration,140);
 s.animations[0].resolve();await tick();assert.equal(s.animations[1].frames[1].opacity,0);assert.match(s.animations[1].frames[1].transform,/24px/);assert.equal(s.ctx.renders,0);
 s.animations[1].resolve();await tick();assert.equal(s.animations[2].frames[1].height,'0px');s.animations[2].resolve();await tick();assert.equal(s.ctx.renders,1);
});
test('saving failure cancels the exit and immediately restores the list',async()=>{
 const s=setup();s.ctx.animateScrapRemoval('a');s.saved.add('a');s.ctx.syncScrapUI('a');assert.equal(s.ctx.renders,1);await tick();assert.equal(s.animations.length,1);assert.equal(s.ctx.renders,1);
});
test('concurrent removals do not redraw away unfinished animations',async()=>{
 const s=setup(true);s.ctx.animateScrapRemoval('a');s.ctx.animateScrapRemoval('b');assert.equal(s.ctx.animateScrapRemoval('a'),false);
 s.animations[0].resolve();await tick();s.animations[2].resolve();await tick();assert.equal(s.ctx.renders,0);
 s.animations[1].resolve();await tick();s.animations[3].resolve();await tick();assert.equal(s.ctx.renders,1);assert.ok(s.animations.every(a=>a.frames.every(f=>!f.transform&&!f.height)));
});
test('no exit animation runs outside the scrap view',()=>{const s=setup();s.view.hidden=true;assert.equal(s.ctx.animateScrapRemoval('a'),false);assert.equal(s.animations.length,0)});
