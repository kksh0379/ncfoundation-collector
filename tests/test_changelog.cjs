const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/app.js','utf8');
const helpers=source.slice(source.indexOf('function mdToHtml'),source.indexOf('// 개발노트의 ```mermaid```'));
const ctx=vm.createContext({});vm.runInContext(helpers,ctx);
const markdown=fs.readFileSync('CHANGELOG.md','utf8');
test('all version headings become accordion rows with their own detail',()=>{
 const html=ctx.renderChangelog(markdown);
 const headings=markdown.split('\n').filter(line=>line.startsWith('### '));
 assert.equal((html.match(/<details class="cl-item">/g)||[]).length,headings.length);
 for(let v=10;v<=51;v++){
  const version=`v2.${String(v).padStart(2,'0')}`;
  const row=html.match(new RegExp(`<details class="cl-item"><summary>${version.replace('.','\\.')} · [\\s\\S]*?</details>`));
  assert.ok(row,version);assert.match(row[0],/<div class="cl-body"><ul><li>/);
 }
 assert.match(html,/v2\.08 · 260928/);assert.match(html,/리더뷰 · 261001/);
 assert.doesNotMatch(markdown,/^## .*v2\./m);
});
test('details preserve bullet separation, bold and inline code',()=>{
 const html=ctx.renderChangelog('## 2026-10-02\n### v2.43 · 261002 — 정리\n- **첫 항목**: 설명\n- 두 번째 `코드`\n');
 assert.equal((html.match(/<li>/g)||[]).length,2);assert.match(html,/<strong>첫 항목<\/strong>/);assert.match(html,/<code>코드<\/code>/);
});
test('patch content is escaped before formatting',()=>{
 const html=ctx.renderChangelog('### v2.43 — <img src=x onerror=alert(1)>\n- **<script>alert(1)</script>**\n');
 assert.doesNotMatch(html,/<img|<script/);assert.match(html,/&lt;img/);assert.match(html,/<strong>&lt;script/);
});
