const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/js/app.js','utf8');
const fn = source.slice(source.indexOf('  function filtered() {'), source.indexOf('  function stars(avg) {'));
test('review filter combines with category and text search without excluding unrated rows by default', () => {
 const LUNCH = {cat:'전체',q:'',reviewedOnly:false,rows:[
  {id:1,name:'국밥 A',cat_norm:'한식',review_count:0},
  {id:2,name:'국밥 B',cat_norm:'한식',review_count:'1'},
  {id:3,name:'샌드위치',cat_norm:'양식',review_count:2}]};
 const ctx = {LUNCH}; vm.createContext(ctx); vm.runInContext(fn,ctx);
 const ids = () => Array.from(ctx.filtered(),x=>x.id);
 assert.deepEqual(ids(),[1,2,3]);
 LUNCH.reviewedOnly=true; assert.deepEqual(ids(),[2,3]);
 LUNCH.cat='한식'; assert.deepEqual(ids(),[2]);
 LUNCH.q='국밥 A'; assert.deepEqual(ids(),[]);
 LUNCH.reviewedOnly=false; assert.deepEqual(ids(),[1]);
});
