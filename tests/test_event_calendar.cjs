const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/app.js','utf8');
const helpers=source.slice(source.indexOf('const CAL_COLORS'),source.indexOf('function renderEvents'));
const facts=source.slice(source.indexOf('function eventDateBadge'),source.indexOf('function eventAlbumCard'));
const escapeHtml=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function context(extra={}){const c=vm.createContext({URL,Date,AbortController,escapeHtml,NEWS_NEW_DATE:'',...extra});vm.runInContext(facts+helpers,c);return c;}
const list=[{title:'월 경계 행사',start_date:'2026-09-29',end_date:'2026-10-03',url:'https://news.example/1'},
{title:'당일 행사',start_date:'2026-10-02',url:'https://news.example/2'},
{title:'예정 행사',start_date:'2026-10-04',url:'https://news.example/3'},
{title:'날짜 미정',url:'https://news.example/4'}];
test('date selection includes inclusive start/end days and multi-day events crossing months',()=>{
 const c=context();assert.equal(c.calendarDayEvents(list,'2026-10-02').length,2);
 assert.equal(c.calendarDayEvents(list,'2026-10-03').length,1);
 assert.equal(c.calendarDayEvents(list,'2026-10-04')[0].title,'예정 행사');
 assert.equal(c.calendarDayEvents(list,'2026-10-05').length,0);
});
test('preview safely escapes article metadata and links to the reader using canonical publisher URL',()=>{
 const c=context();const html=c.calendarPreviewHtml([{title:'<img src=x onerror=alert(1)>',start_date:'2026-10-02',venue:'<script>bad</script>',content:'<b>소개</b> '+ '본문 '.repeat(100),url:'https://news.example/rss',source_url:'https://publisher.example/article'}]);
 assert.doesNotMatch(html,/<img|<script|<b>/);assert.match(html,/&lt;img/);
 assert.match(html,/href="https:\/\/publisher.example\/article"/);assert.match(html,/data-reader/);
 assert.match(html,/…/);
});
test('invalid source schemes never create clickable previews and empty days have explicit feedback',()=>{
 const c=context();assert.doesNotMatch(c.calendarPreviewHtml([{title:'잘못된 주소',start_date:'2026-10-02',url:'javascript:alert(1)'}]),/<a/);
 assert.match(c.calendarPreviewHtml([]),/수집된 행사가 없어요/);
});
test('mobile left and right edge previews remain inside the viewport',()=>{
 const c=context();for(const left of [12,335]){
  const box=c.calendarPreviewLayout({left,width:43,top:200,bottom:244},366,400,{width:390,top:12,bottom:740});
  assert.ok(box.left>=12);assert.ok(box.left+366<=378);assert.ok(box.arrow>=20&&box.arrow<=346);
  assert.ok(box.top>=12&&box.top+Math.min(400,box.maxHeight)<=740);
 }
});
test('bottom dates open above the cell and respect bottom navigation space',()=>{
 const c=context();const box=c.calendarPreviewLayout({left:260,width:44,top:620,bottom:664},366,420,{width:390,top:12,bottom:690});
 assert.equal(box.above,true);assert.ok(box.top+420<620);assert.ok(box.top>=12);
});
test('desktop dates open below when there is room',()=>{
 const c=context();const box=c.calendarPreviewLayout({left:520,width:170,top:100,bottom:144},420,300,{width:1366,top:12,bottom:820});
 assert.equal(box.above,false);assert.equal(box.top,156);assert.equal(box.left,395);
});
test('calendar renders accessible selectable dates, binds current events and cleans up when month changes',()=>{
 const cal={innerHTML:''}, nodes={'cal-event':cal,'cal-prev':{},'cal-next':{}};
 const c=context({eventCalYM:[2026,9],newBadgeHtml:()=>'',document:{getElementById:id=>nodes[id]},renderTab:()=>{}});
 let bound,cleaned=0;c.bindCalendarPreview=(el,items)=>{bound=items;};
 vm.runInContext('eventCalendarCleanup = () => { cleanupCounter(); };',Object.assign(c,{cleanupCounter:()=>cleaned++}));
 c.renderEventCalendar(list);
 assert.equal(cleaned,1);assert.equal(bound.length,3);
 assert.equal((cal.innerHTML.match(/class="cal-cell cal-date/g)||[]).length,31);
 assert.match(cal.innerHTML,/data-date="2026-10-02" aria-label="2026년 10월 2일, 행사 2건"/);
 assert.match(cal.innerHTML,/popover="auto" role="dialog"/);
 assert.match(cal.innerHTML,/aria-controls="cal-day-preview"/);
 nodes['cal-next'].onclick();assert.deepEqual(Array.from(c.eventCalYM),[2026,10]);
});
