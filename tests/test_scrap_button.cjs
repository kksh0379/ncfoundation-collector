const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/app.js','utf8');
const helpers=source.slice(source.indexOf('function updateScrapButton'),source.indexOf('function newBadgeHtml'));
function setup(){const saved=new Set();const ctx=vm.createContext({isScrapped:key=>saved.has(key),escapeHtml:s=>s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')});vm.runInContext(helpers,ctx);return {saved,ctx};}
test('icon-only markup retains accessible saved and unsaved state',()=>{const {saved,ctx}=setup();let html=ctx.scrapBtnHtml('one');assert.match(html,/aria-pressed="false"/);assert.doesNotMatch(html,/<span|scrap-btn-label/);assert.match(html,/aria-label="스크랩하기"/);saved.add('one');html=ctx.scrapBtnHtml('one');assert.match(html,/aria-pressed="true"/);assert.doesNotMatch(html,/<span|scrap-btn-label/);assert.match(html,/누르면 취소/);assert.match(ctx.scrapBtnHtml('a"<'),/data-key="a&quot;&lt;"/)});
test('state synchronization updates icon class, accessible state and rollback',()=>{const {saved,ctx}=setup();const attributes={},classes=new Set();const button={dataset:{key:'one'},classList:{toggle(k,on){on?classes.add(k):classes.delete(k)}},setAttribute(k,v){attributes[k]=v}};for(const on of [false,true,false,true,false]){if(on)saved.add('one');else saved.delete('one');ctx.updateScrapButton(button);assert.equal(classes.has('on'),on);assert.equal(attributes['aria-pressed'],String(on));assert.equal(button.title,on?'스크랩됨 — 누르면 취소':'스크랩하기')}});
