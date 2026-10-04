const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/app.js','utf8');
const classify=source.slice(source.indexOf('function eventCategories'),source.indexOf('const filterEventsByCategory'));
const filter=source.slice(source.indexOf('function makeCheckFilter'),source.indexOf('const filterCatByCat'));
function setup(){const ctx=vm.createContext({});vm.runInContext(classify,ctx);return ctx;}
test('AI ethics is separate from general AI and can overlap with other fields',()=>{
 const c=setup();
 assert.deepEqual(Array.from(c.eventCategories({title:'서울 AI 윤리·거버넌스 포럼'})),['AI·데이터','AI 윤리']);
 assert.deepEqual(Array.from(c.eventCategories({title:'Responsible AI Governance Summit'})),['AI·데이터','AI 윤리']);
 assert.deepEqual(Array.from(c.eventCategories({title:'생성형 AI 개발자 컨퍼런스'})),['IT·기술','AI·데이터']);
 assert.ok(!c.eventCategories({title:'AI 활용 세미나'}).includes('AI 윤리'));
 assert.ok(!c.eventCategories({title:'기업 윤리 세미나'}).includes('AI 윤리'));
});
test('existing source items classify from descriptions without venue or partial English false positives',()=>{
 const c=setup();
 assert.ok(c.eventCategories({title:'클라우드 보안 개발자 밋업'}).includes('IT·기술'));
 assert.ok(c.eventCategories({title:'가을 미술 전시',content:'문화 축제'}).includes('문화·전시'));
 assert.ok(c.eventCategories({title:'사회적 가치와 접근성 교육'}).includes('교육·공익'));
 assert.ok(c.eventCategories({title:'스타트업 투자 설명회'}).includes('산업·비즈니스'));
 assert.deepEqual(Array.from(c.eventCategories({title:'Rail Fair',venue:'AI센터',author:'IT뉴스'})),['기타']);
 assert.ok(c.eventCategories({title:'포럼',content:'인공지능 안전성과 규제'}).includes('AI 윤리'));
});
test('checkboxes select a union of multiple fields, clear all, restore all and rerender the event view',()=>{
 const c=setup(), cats=['all','IT·기술','AI·데이터','AI 윤리','산업·비즈니스','문화·전시','교육·공익','기타'];
 const boxes=cats.map(cat=>({dataset:{cat},checked:true}));let onChange,renders=0;
 const box={querySelectorAll:()=>boxes,querySelector:selector=>boxes.find(b=>selector.includes('"'+b.dataset.cat+'"')),addEventListener:(event,fn)=>{onChange=fn;}};
 Object.assign(c,{document:{getElementById:()=>box},CSS:{escape:x=>x},TAB_DATA:{event:{}},renderTab:tab=>{assert.equal(tab,'event');renders++;}});
 vm.runInContext(filter,c);
 const select=c.makeCheckFilter('event','event-cats',c.eventCategories);
 const items=[{title:'AI 윤리 포럼'},{title:'클라우드 개발자 밋업'},{title:'가을 미술 전시'},{title:'모임'}];
 assert.equal(select(items).length,4);
 onChange({target:{dataset:{cat:'all'},checked:false}});assert.equal(select(items).length,0);
 onChange({target:{dataset:{cat:'AI 윤리'},checked:true}});assert.equal(select(items)[0].title,'AI 윤리 포럼');
 onChange({target:{dataset:{cat:'IT·기술'},checked:true}});assert.equal(select(items).length,2);
 onChange({target:{dataset:{cat:'AI·데이터'},checked:true}});assert.equal(select(items).length,2);
 onChange({target:{dataset:{cat:'all'},checked:true}});assert.equal(select(items).length,4);assert.equal(renders,5);
});
test('pet field covers Korean cat fairs and English names without carpet false positives',()=>{
 const c=setup();
 for(const title of ['궁디팡팡 캣페스타 SUWON','2026 냥냥펀치캣쇼 in aT','케이펫페어','고양이 박람회','Pet Fair','Catfesta']) assert.ok(c.eventCategories({title}).includes('반려동물'),title);
 assert.ok(!c.eventCategories({title:'Carpet Design Fair'}).includes('반려동물'));
 assert.ok(!c.eventCategories({title:'미술 전시',venue:'고양이센터'}).includes('반려동물'));
 assert.match(fs.readFileSync('templates/index.html','utf8'),/data-cat="반려동물"/);
 assert.match(fs.readFileSync('static/js/events-ui.js','utf8'),/const topics = .*'반려동물'/);
});
