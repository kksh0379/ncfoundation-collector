const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('static/js/app.js','utf8');
function setup(saved='list') {
 const storage=new Map([['nvView','list'],['eventScheduleView',saved]]),classes=new Set(['view-list']);
 const button=dataset=>({dataset,attrs:{},active:false,classList:{toggle(_,v){this.owner.active=v;}},setAttribute(k,v){this.attrs[k]=v;},addEventListener(_,fn){this.click=fn;}});
 const modes=['schedule','curation'].map(emode=>button({emode})),views=['list','album','calendar'].map(eview=>button({eview}));
 [...modes,...views].forEach(b=>b.classList.owner=b);
 const picker={hidden:true};let active='event',renders=0;
 const c=vm.createContext({localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},document:{body:{classList:{toggle:(k,v)=>v?classes.add(k):classes.delete(k)}},getElementById:()=>picker,querySelectorAll:s=>s.includes('mode')?modes:views},activeTab:()=>active,renderTab:()=>renders++});
 vm.runInContext(src.slice(src.indexOf('function applyViewClasses'),src.indexOf('function setView'))+src.slice(src.indexOf('let eventView ='),src.indexOf('let eventCalYM'))+src.slice(src.indexOf('function syncEventViewControls'),src.indexOf('// ----------------------------- 상태 확인')),c);
 return {c,storage,classes,modes,views,picker,setActive:v=>active=v,renders:()=>renders};
}
test('two tabs preserve all three ordinary schedule views across curation visits',()=>{
 const s=setup();assert.equal(s.picker.hidden,true);assert.equal(s.modes[1].attrs['aria-selected'],'true');
 s.modes[0].click();assert.equal(s.views[0].attrs['aria-pressed'],'true');assert.ok(s.classes.has('view-list'));
 for(const v of s.views){v.click();s.modes[1].click();assert.equal(s.picker.hidden,true);s.modes[0].click();assert.equal(v.active,true);assert.equal(s.storage.get('eventScheduleView'),v.dataset.eview);}
 assert.equal(s.storage.get('nvView'),'list');
});
test('calendar and album use full cards regardless of news list preference',()=>{
 const s=setup('calendar');s.modes[0].click();assert.ok(s.classes.has('view-card'));assert.ok(!s.classes.has('view-list'));s.views[1].click();assert.ok(s.classes.has('view-card'));
});
test('loading event controls in the background never changes the active news layout',()=>{
 const s=setup();s.setActive('cat');s.classes.clear();s.classes.add('view-list');vm.runInContext('syncEventViewControls()',s.c);assert.ok(s.classes.has('view-list'));assert.ok(!s.classes.has('view-card'));
});
