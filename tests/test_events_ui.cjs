const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/js/events-ui.js','utf8');
function setup(){const root={innerHTML:'',querySelector:()=>({focus(){}})};const c=vm.createContext({window:{},document:{getElementById:()=>root,createElement:()=>({className:'',dataset:{},innerHTML:'',get outerHTML(){return `<li class="${this.className}">${this.innerHTML}</li>`;}})},localStorage:{getItem:()=>null},Date,Intl,URL,escapeHtml:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),newBadgeHtml:()=>'',NEWS_NEW_DATE:'',copyBtnHtml:()=>'',eventCategories:()=>['IT·기술'],registerItem:()=> 'key',readClass:()=>'',scrapBtnHtml:()=>'<button>스크랩</button>',eventSrcBadge:()=>'',eventDateBadge:s=>s.start_date,eventPlace:s=>s.venue||''});const app=fs.readFileSync('static/js/app.js','utf8');vm.runInContext(app.slice(app.indexOf('function eventAlbumCard('),app.indexOf('function renderEventAlbum(')),c);vm.runInContext(source,c);return {root,c};}
function click(root,data){root.onclick({target:{closest:()=>({dataset:data})}});}
test('dense calendar has counts, selected-day agenda and a seven-day expanded strip',()=>{
 const {root,c}=setup();const iso=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());
 const events=Array.from({length:25},(_,i)=>({title:'event '+i,start_date:iso,end_date:'2099-12-31',url:'https://example.com/'+i}));
 c.window.EventDiscovery.calendar(events);assert.match(root.innerHTML,/25건/);assert.match(root.innerHTML,/album-grid ed-agenda-list/);assert.match(root.innerHTML,/본문 읽기/);assert.equal((root.innerHTML.match(/<li class="card event-card/g)||[]).length,25);assert.doesNotMatch(root.innerHTML,/cal-bar|popover=/);
 click(root,{cal:'expand'});assert.equal((root.innerHTML.match(/data-date=/g)||[]).length,7);assert.match(root.innerHTML,/달력 펼치기/);
 click(root,{cal:'next'});assert.equal((root.innerHTML.match(/<li class="card event-card/g)||[]).length,25);
 click(root,{cal:'today'});assert.match(root.innerHTML,new RegExp(iso));
});
test('calendar escapes data and rejects non-http source links',()=>{
 const {root,c}=setup();const iso=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());
 c.window.EventDiscovery.calendar([{title:'<script>bad</script>',venue:'<img src=x>',start_date:iso,url:'javascript:alert(1)'}]);
 assert.doesNotMatch(root.innerHTML,/<script>|<img src=x>|href="javascript:/);assert.match(root.innerHTML,/&lt;script/);
});
