const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/app.js','utf8');
const helpers = source.slice(source.indexOf('function lunchCandidates'),source.indexOf('function showLoading'));
const rows = [
  {id:1,name:'한식집',cat_norm:'한식',avg_rating:4.5,review_count:10,dist_m:200},
  {id:2,name:'국수집',cat_norm:'분식',avg_rating:4.2,review_count:5,walk_min:3},
  {id:3,name:'숨긴 곳',cat_norm:'한식',excluded:true},
];
function ctx(extra={}) {const c=vm.createContext({AbortController,setTimeout,clearTimeout,...extra});vm.runInContext(helpers,c);return c;}
test('fallback returns a real candidate and keeps hidden and avoided restaurants out',()=>{
  const c=ctx(); const r=c.fallbackLunchRecommendation(rows,{avoid_cats:['한식'],moods:[]},'safe');
  assert.equal(r.ok,true);assert.equal(r.pick.id,2);assert.match(r.pick.reason,/도보 3분/);
});
test('empty eligible pool gives condition guidance rather than fabricating a restaurant',()=>{
  const r=ctx().fallbackLunchRecommendation(rows,{avoid_cats:['한식','분식']},'');
  assert.equal(r.empty,true);assert.equal(r.pick,undefined);assert.match(r.error,/제외 항목/);
});
test('malformed or ineligible server picks are rejected, including alternate items',()=>{
  const c=ctx();assert.equal(c.usableLunchRecommendation({ok:true},rows,{}),null);
  assert.equal(c.usableLunchRecommendation({ok:true,pick:{id:3}},rows,{}),null);
  const r=c.usableLunchRecommendation({ok:true,pick:{id:1},tags:'bad',alternatives:[{id:2},{id:3}]},rows,{});
  assert.equal(r.pick.name,'한식집');assert.equal(r.tags.length,0);assert.equal(r.alternatives[0].name,'국수집');assert.equal(r.alternatives.length,1);
});
test('recommendation network failure still renders a real fallback result',async()=>{
  const rendered=[]; const c=ctx({recommendationRequest:0,previousRecommendationId:null,LUNCH:{curLoc:{id:9},rows,aiPersona:''},showView:()=>{},$:()=>({innerHTML:''}),foodSlotHtml:()=>'',gatherConditions:()=>({avoid_cats:['한식'],moods:[]}),settleFoodSlot:async()=>{},renderRecommend:r=>rendered.push(r)});
  c.requestLunchRecommendation=async()=>{throw Error('offline')};
  vm.runInContext(source.slice(source.indexOf('  async function runRecommend()'),source.indexOf('  function renderRecommend(res)')),c);
  await c.runRecommend();assert.equal(rendered[0].ok,true);assert.equal(rendered[0].pick.id,2);
});
test('a hung request is bounded and aborted so the fallback can run',async()=>{
  let signal;const c=ctx({api:(_p,_b,o)=>{signal=o.signal;return new Promise(()=>{});}});
  await assert.rejects(c.requestLunchRecommendation({},15),/timeout/);
  assert.equal(signal.aborted,true);
});
test('multiple fallback candidates avoid immediately repeating the previous pick',()=>{
  const r=ctx().fallbackLunchRecommendation(rows,{avoid_cats:[]},'',1);
  assert.equal(r.pick.id,2);
});
