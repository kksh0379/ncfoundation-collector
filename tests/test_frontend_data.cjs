const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/app.js', 'utf8');
const helper = source.slice(0, source.indexOf('// ===== 인증'));
function context(fetch) {
  return vm.createContext({document:{addEventListener(){}},fetch, AbortController, Response,
    setTimeout: (fn, ms) => setTimeout(fn, ms < 3000 ? 0 : ms), clearTimeout});
}
test('parallel data reads share one request and independently consume responses', async () => {
  let count = 0;
  const ctx = context(async () => { count++; return new Response('[1,2]'); });
  vm.runInContext(helper, ctx);
  const responses = await Promise.all([vm.runInContext('fetchData("/api/news")',ctx), vm.runInContext('fetchData("/api/news")',ctx)]);
  assert.equal(count,1);
  assert.deepEqual(await responses[0].json(),[1,2]);
  assert.deepEqual(await responses[1].json(),[1,2]);
});
test('pending database responses are retried, not rendered as empty data', async () => {
  let count = 0;
  const ctx = context(async () => ++count === 1 ? new Response('[]',{headers:{'X-Data-Pending':'1'}}) : new Response('[3]'));
  vm.runInContext(helper,ctx);
  const response = await vm.runInContext('fetchData("/api/news")',ctx);
  assert.deepEqual(await response.json(),[3]);
  assert.equal(count,2);
});
test('a slow previous location cannot overwrite the latest selection',async () => {
  const start = source.indexOf('  async function loadRestaurants(useCache');
  const end = source.indexOf('\n  function catCounts()',start);
  const pending = [];
  const state = {curLoc:{id:1},rows:[]};
  const ctx = vm.createContext({LUNCH:state, isAdmin:()=>false, $:()=>({innerHTML:''}),
    catSpin:()=>'',renderCats:()=>{},renderList:()=>{},renderLocBar:()=>{},msg:()=>{},
    getJSON:()=>new Promise(resolve=>pending.push(resolve))});
  vm.runInContext('let restaurantSequence=0; const restaurantCache=new Map();'+source.slice(start,end),ctx);
  const first=vm.runInContext('loadRestaurants()',ctx);
  state.curLoc={id:2};
  const second=vm.runInContext('loadRestaurants()',ctx);
  pending[1]({location:{id:2},restaurants:[{name:'second'}]});
  await second;
  pending[0]({location:{id:1},restaurants:[{name:'first'}]});
  await first;
  assert.equal(state.curLoc.id,2);
  assert.equal(state.rows[0].name,'second');
});

