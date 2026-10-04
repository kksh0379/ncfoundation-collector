const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('static/js/app.js','utf8');
function setup(){
 const elements={'login-fields':{children:[],replaceChildren(){this.children=[]},appendChild(el){this.children.push(el)}},'login-test-info':{},'login-other':{},'login-submit':{}};
 const document={querySelectorAll:()=>[],getElementById:id=>elements[id]||elements['login-fields'].children.find(e=>e.id===id),createElement:()=>({value:'',setAttribute(){}})};
 const ctx=vm.createContext({document,loginErr:{}});vm.runInContext(src.slice(src.indexOf('let loginRole = "user";'),src.indexOf('function openLogin()',src.indexOf('let loginRole = "user";'))),ctx);return {ctx,elements,document};
}
test('default test login has no credential inputs but preserves existing authentication',()=>{const {ctx,elements}=setup();ctx.setLoginRole('user');assert.equal(elements['login-fields'].children.length,0);assert.equal(JSON.stringify(ctx.loginRequestBody()),JSON.stringify({role:'user',username:'test1',pw:'1234'}));assert.equal(elements['login-test-info'].hidden,false)});
test('admin requires an empty real password input without a prefilled test password',()=>{const {ctx,document}=setup();ctx.setLoginRole('admin');const pw=document.getElementById('login-pw');assert.equal(pw.type,'password');assert.equal(pw.value,'');pw.value='entered';assert.equal(ctx.loginRequestBody().pw,'entered');ctx.setLoginRole('user');assert.equal(document.getElementById('login-pw'),undefined)});
test('other accounts remain available and credentials disappear on mode change',()=>{const {ctx,document,elements}=setup();ctx.setLoginRole('user',true);document.getElementById('login-id').value=' alice ';document.getElementById('login-pw').value='entered';assert.equal(ctx.loginRequestBody().username,'alice');assert.equal(ctx.loginRequestBody().pw,'entered');ctx.setLoginRole('admin');assert.equal(document.getElementById('login-id'),undefined);assert.equal(document.getElementById('login-pw').value,'');ctx.setLoginRole('user');assert.equal(elements['login-fields'].children.length,0)});
