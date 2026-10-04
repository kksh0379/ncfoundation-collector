const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/app.js', 'utf8');
const ctx = vm.createContext({escapeHtml: s => String(s).replaceAll('<', '&lt;').replaceAll('>', '&gt;')});
vm.runInContext(source.slice(source.indexOf('function catRunSvg'), source.indexOf('function showLoading')), ctx);
test('every loader always includes both breeds and an accessible status', () => {
  for (const fn of ['catSpin', 'catRunInline']) {
    const html = vm.runInContext(`${fn}('불러오는 중…')`, ctx);
    assert.match(html, /koshort/);
    assert.match(html, /chinchilla/);
    assert.match(html, /role="status"/);
    assert.match(html, /aria-hidden="true"/);
  }
});
test('loader progress labels cannot inject HTML', () => {
  assert.doesNotMatch(vm.runInContext('catRunInline("<script>alert(1)</script>")',ctx), /<script>/);
});
test('old random and mini spinners are no longer emitted', () => {
  assert.doesNotMatch(source, /class="mini-spin"|class="catrun/);
});
test('loader uses isolated animated image and a reduced-motion still source', () => {
  const html = vm.runInContext('catSpin()', ctx);
  assert.match(html, /<picture>/);
  assert.match(html, /prefers-reduced-motion: reduce/);
  assert.match(html, /cats-loading-still-v2.28.webp/);
  assert.match(html, /cats-loading-smooth-v2.28.webp/);
  const css = fs.readFileSync('static/css/style.css', 'utf8');
  assert.doesNotMatch(css, /background-size:400%|step-end|@keyframes kitten-frames/);
  assert.match(css, /overflow:hidden/);
});
