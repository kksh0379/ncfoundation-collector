const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/app.js','utf8');
const helpers = source.slice(source.indexOf('function updateStorageBadge'),source.indexOf('// Keep the last content clear'));
function setup(fetchData) {
  const badge = {textContent:'DB 확인 중',dataset:{},style:{}};
  const ctx = vm.createContext({document:{getElementById:id=>id==='storage-badge'?badge:{textContent:''}},fetchData,fmtLast:()=>''});
  vm.runInContext(helpers,ctx);
  return {ctx,badge};
}
test('footer explicitly distinguishes connected, disconnected, temporary and unknown states',()=>{
  const {ctx,badge} = setup();
  for(const [meta,state,text] of [[{storage:'postgres'},'connected','DB 연결됨'],[{storage:'postgres',db_down:true},'down','DB 연결 끊김'],[{storage:'sqlite'},'temporary','임시 저장 모드'],[null,'unknown','DB 상태 확인 불가']]) {
    ctx.updateStorageBadge(meta);
    assert.equal(badge.dataset.state,state);
    assert.equal(badge.textContent,text);
    assert.ok(badge.title.length>0);
  }
});
test('metadata fetch failure replaces a previous healthy status with unknown',async()=>{
  const {ctx,badge}=setup(async()=>{throw Error('offline');});
  ctx.updateStorageBadge({storage:'postgres'});
  await ctx.loadMeta();
  assert.equal(badge.dataset.state,'unknown');
  assert.equal(badge.textContent,'DB 상태 확인 불가');
});
test('merged feed without board and social labels still shows healthy database status',async()=>{
  const {ctx,badge}=setup(async()=>({json:async()=>({storage:'postgres',biz:'2026.10.04 04:02:28'})}));
  const labels = {'last-biz': {textContent:''}};
  ctx.document.getElementById = id => id === 'storage-badge' ? badge : (labels[id] || null);
  ctx.fmtLast = ts => ts || '';
  await ctx.loadMeta();
  assert.equal(badge.dataset.state,'connected');
  assert.equal(labels['last-biz'].textContent,'2026.10.04 04:02:28');
});
