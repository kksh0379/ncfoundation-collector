const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/js/service-notice.js', 'utf8');
function setup() {
  const events = {}, windowEvents = {}, classes = new Set();
  const notice = {open:false, calls:0, showModal(){this.open=true;this.calls++},addEventListener(k,f){events[k]=f}};
  const prior = {isConnected:true, calls:0,focus(){this.calls++}};
  const title = {calls:0,focus(){this.calls++}};
  const document = {body:{},activeElement:prior,documentElement:{classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)}},getElementById:id=>id==='service-notice'?notice:title};
  const window = {addEventListener:(k,f)=>windowEvents[k]=f};
  // No storage API provided: any accidental persistence fails the test.
  vm.runInNewContext(source,{document,window});
  return {notice,prior,title,events,windowEvents,classes};
}
test('each fresh page entry shows the notice and focuses its title',()=>{
  for(let i=0;i<2;i++){const s=setup();assert.equal(s.notice.calls,1);assert.equal(s.title.calls,1);assert.ok(s.classes.has('service-notice-open'));s.windowEvents.pageshow();assert.equal(s.notice.calls,1)}
});
test('confirmation unlocks scrolling and history restoration shows again',()=>{
 const s=setup();s.notice.open=false;s.events.close();assert.equal(s.classes.size,0);assert.equal(s.prior.calls,1);s.windowEvents.pageshow({persisted:true});assert.equal(s.notice.calls,2);assert.ok(s.classes.has('service-notice-open'));
});
test('escape requests explicit confirmation instead of dismissing silently',()=>{
 const s=setup();let prevented=false;s.events.cancel({preventDefault(){prevented=true}});assert.ok(prevented);assert.ok(s.notice.open);
});
