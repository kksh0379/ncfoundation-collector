const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const app=fs.readFileSync('static/js/app.js','utf8');
const script=fs.readFileSync('static/js/events-ui.js','utf8');
function setup(saved={topics:[],keywords:[]}) {
 const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',hidden:false,dataset:{},append(){},showModal(){},close(){},querySelector:sel=>el(sel),querySelectorAll:()=>[]});return elements.get(id);};
 let requests=0;const escapeHtml=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const c=vm.createContext({Date,Intl,URL,escapeHtml,eventDateBadge:s=>s.start_date||'',eventPlace:s=>s.venue||'',localStorage:{getItem:()=>JSON.stringify(saved),setItem(){}},window:{},document:{getElementById:el},fetch:()=>{requests++;return Promise.reject(Error('unexpected network'));}});
 vm.runInContext(app.slice(app.indexOf('function eventCategories'),app.indexOf('const filterEventsByCategory'))+script,c);
 return {api:c.window.EventDiscovery,el,requests:()=>requests};
}
const future='2099-10-30';
const events=[{url:'https://events.example/cat',title:'궁디팡팡 캣페스타',start_date:future},{url:'https://events.example/ai',title:'생성형 AI 컨퍼런스',start_date:future},{url:'https://events.example/old',title:'고양이 행사',start_date:'2000-01-01'}];
test('initial curation, repeat tab visits and interest apply display banners without network calls',()=>{
 const s=setup({topics:['반려동물'],keywords:[]});
 s.api.curation(events);assert.match(s.el('ed-results').innerHTML,/궁디팡팡/);assert.doesNotMatch(s.el('ed-results').innerHTML,/AI 컨퍼런스|고양이 행사/);
 s.api.curation(events);s.el('#ed-recommend').onclick();assert.equal(s.requests(),0);assert.equal(s.el('ed-status').hidden,true);assert.match(s.el('ed-results').innerHTML,/궁디팡팡/);
});
test('instant recommendations use current search results and omit expired or unrelated events',()=>{
 const s=setup({topics:[],keywords:['궁디팡팡']});s.api.curation(events);s.api.curation([events[1]]);assert.doesNotMatch(s.el('ed-results').innerHTML,/궁디팡팡/);assert.match(s.el('ed-results').innerHTML,/표시할 추천이 없어요/);assert.equal(s.requests(),0);
});
test('unselected interests show a bounded diverse feed without calling AI',()=>{
 const s=setup();const rows=s.api.localRecommendations(events,{topics:[],keywords:[]});assert.equal(rows.mode,'rules');assert.equal(rows.items.length,2);assert.equal(s.requests(),0);
});
