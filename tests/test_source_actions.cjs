const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/app.js','utf8');
const ctx=vm.createContext({document:{createElement:()=>({dataset:{}})},registerItem:()=> 'key',escapeHtml:s=>String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'),fmtDate:()=> '2026-10-02',readClass:()=>'',scrapBtnHtml:()=>'',newsThumb:()=>'',newBadgeHtml:()=>'',copyBtnHtml:()=>''});
vm.runInContext(source.slice(source.indexOf('function opensOriginalSource'),source.indexOf('\nfunction renderList',source.indexOf('function opensOriginalSource'))),ctx);
test('foundation board title and button open original source directly',()=>{
 const card=ctx.renderCard({title:'재단 소식',url:'https://example.com/list',source_url:'https://example.com/post'},{tab:'boards'});
 assert.doesNotMatch(card.innerHTML,/data-reader/);assert.match(card.innerHTML,/원문 보기 ↗/);assert.equal((card.innerHTML.match(/href="https:\/\/example.com\/post"/g)||[]).length,2);
});
test('news keeps the inline reader while videos open their original',()=>{
 const item={title:'기사',url:'https://example.com/post'};
 const news=ctx.renderCard(item,{tab:'news'});assert.match(news.innerHTML,/data-reader/);assert.match(news.innerHTML,/본문 읽기/);
 const video=ctx.renderCard(item,{tab:'social'});assert.doesNotMatch(video.innerHTML,/data-reader/);assert.match(video.innerHTML,/원문 보기/);
});
