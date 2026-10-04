/* Finance is lazy loaded independently of news and the database. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const labels = {policy:'세법·보도자료',guide:'회계·세무 가이드',legislation:'입법예고'};
  const modes = {live:'실데이터',demo:'예시 데이터',unconfigured:'연결 준비',unavailable:'일시 중단',loading:'불러오는 중'};
  let data = null, category = 'all', loading = false, dartVersion = 0, ncData = null;
  const NC_CODE = '036570';
  const NC_CORP = '00261443';   // (주)엔씨 DART 고유번호(화면에 노출하지 않고 내부에서만 사용)
  const ncPlaceholder = () => ({code:'KRX/'+NC_CODE, name:'(주)엔씨', unit:'원', value:null, change:null, ratio:null, date:null, mode:'loading', history:[], desc:''});
  const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmtDate = value => {
    const s = String(value ?? '');
    if (/^\d{8}$/.test(s)) return s.slice(0,4)+'.'+s.slice(4,6)+'.'+s.slice(6,8);
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? `${m[1]}.${m[2]}.${m[3]}` : s;
  };
  function link(url, title) {
    try { const u = new URL(url); if (!['https:','http:'].includes(u.protocol)) return esc(title); }
    catch { return esc(title); }
    return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)} ↗</a>`;
  }
  async function json(url, options = {}) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, {...options, signal:controller.signal});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '요청을 처리하지 못했습니다.');
      return result;
    } finally { clearTimeout(timer); }
  }
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const LOADING_MSG = '재무세무 소식을 불러오고 있어요…';
  const loadingHtml = () => `<p class="finance-empty finance-loading">${typeof window.catRunInline === 'function' ? window.catRunInline(LOADING_MSG) : esc(LOADING_MSG)}</p>`;
  function renderNews() {
    if (!data) return;
    if (data.pending) { $('finance-news').innerHTML = loadingHtml(); return; }  // 로딩 중 표시
    const query = $('finance-search').value.trim();
    // 다른 검색과 동일한 한글 자모 유사 매칭(부분·초성·오타). 전역 koreanMatchAll 사용, 없으면 부분일치.
    const match = (hay, q) => !q ? true : (typeof koreanMatchAll === 'function' ? koreanMatchAll(hay, q) : hay.toLowerCase().includes(q.toLowerCase()));
    const rows = data.items.filter(r => (category === 'all' || r.category === category) && match(`${r.title} ${r.description} ${r.source}`, query));
    $('finance-news').innerHTML = rows.length ? rows.map(r => {
      const key = r.url;
      // 메인 뉴스 카드와 동일하게 읽음·스크랩·링크복사를 재사용(전역 헬퍼). 본문은 리더로 연다.
      if (typeof registerItem === 'function') registerItem({url:r.url, source_url:r.url, title:r.title,
        published_at:r.pub_date, author:r.source, content:r.description, category:r.category}, 'finance', r.url);
      const read = (typeof isRead === 'function' && isRead(key)) ? ' is-read' : '';
      const scrap = typeof scrapBtnHtml === 'function' ? scrapBtnHtml(key) : '';
      const copy = typeof copyBtnHtml === 'function' ? copyBtnHtml(r.url) : '';
      // 제목 = 원문 직접 링크. 리더 추출이 불가한 출처(재정경제부 등)는 본문 읽기 버튼 대신 PC 전용 안내.
      const titleLink = `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.title)} ↗</a>`;
      const actions = r.open_original
        ? `<span class="finance-pc-only" title="이 출처는 모바일에서 원문이 메인 페이지로 이동합니다. PC에서 원문을 열어 주세요.">💻 PC에서 원문 열람</span>${copy}`
        : `<a class="read-action reader-open" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer" data-reader>본문 읽기(앱)</a>${copy}`;
      return `<article class="finance-news-item card${read}" data-key="${esc(key)}">${scrap}<span class="finance-mode">${esc(labels[r.category])}</span><h3 class="card-title">${titleLink}</h3><p class="card-summary">${esc(r.description)}</p><p class="finance-news-meta">${esc(r.source)}${r.pub_date ? ' · '+esc(fmtDate(r.pub_date)) : ''}</p><div class="card-actions">${actions}</div></article>`;
    }).join('') : '<p class="finance-empty">표시할 소식이 없습니다.<br>검색 조건 또는 아래 출처의 연결 상태를 확인해 주세요.</p>';
  }
  function renderCalendar() {
    const cal = data && data.calendar, list = $('finance-calendar-list');
    if (!list) return;
    const events = cal && Array.isArray(cal.events) ? cal.events : [];
    const days = '일월화수목금토';
    list.innerHTML = events.length ? events.map(e => {
      // Date is already a KST business day from the server; parse its parts so the
      // viewer's timezone never shifts the day or weekday.
      const [y, m, dd] = String(e.date || '').split('-').map(Number);
      const label = (y && m && dd) ? `${m}.${dd}(${days[new Date(Date.UTC(y, m-1, dd)).getUTCDay()]})` : esc(e.date);
      const dday = e.days_left === 0 ? 'D-DAY' : (e.days_left > 0 ? 'D-'+e.days_left : '');
      const shift = e.shifted ? '<span class="finance-calendar-shift">주말 순연</span>' : '';
      return `<li class="finance-calendar-item"><span class="finance-calendar-dday${e.days_left===0?' today':''}">${esc(dday)}</span><div><strong>${esc(label)} · ${esc(e.title)}</strong>${shift}<p>${esc(e.note||'')}</p></div></li>`;
    }).join('') : '<li class="finance-calendar-empty">다가오는 신고·납부 기한이 없습니다.</li>';
    $('finance-calendar-message').textContent = cal ? (cal.message || '') : '';
  }
  function indicatorCardHtml(r) {
    let chart = '';
    if (r.history && r.history.length > 1) {
      // 값 범위로 정규화해 세로 4~32 영역에 맞춘다(환율처럼 큰 수도 박스를 벗어나지 않게).
      const vals = r.history.map(x => x.value), low = Math.min(...vals), range = Math.max(...vals)-low || 1;
      const points = vals.map((v,i) => `${(i*160/(vals.length-1)).toFixed(1)},${(32-(v-low)/range*28).toFixed(1)}`).join(' ');
      chart = `<svg viewBox="0 0 160 36" role="img" aria-label="${esc(r.name)} 최근 6개월 추이"><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="1.5"/></svg><span class="finance-spark-label">최근 6개월 추이</span>`;
    }
    const help = r.desc ? `<button type="button" class="finance-help" aria-label="${esc(r.name)} 설명 보기" aria-expanded="false" data-tip="${esc(r.desc)}">?</button>` : '';
    const dateLine = r.date ? esc(fmtDate(r.date))+' 기준' : (r.mode === 'loading' ? '불러오는 중…' : '실제 시세 아님');
    let changeLine;
    if (r.change == null) changeLine = r.mode === 'loading' ? '주가를 불러오고 있습니다.' : '비교 데이터 없음';
    else if (r.ratio != null) changeLine = `전일 종가 대비 ${r.change>0?'+':''}${esc(Number(r.change).toLocaleString('ko-KR'))}원 (${r.ratio>0?'+':''}${esc(r.ratio)}%)`;
    else changeLine = `직전 관측 대비 ${r.change>0?'+':''}${esc(r.change)}${r.unit==='%'?'%p':esc(r.unit)}`;
    const isNc = String(r.code||'').startsWith('KRX');
    const idAttr = isNc ? ' id="finance-nc"' : '';
    const refresh = isNc ? `<button type="button" class="finance-stock-refresh" aria-label="주가 새로고침" title="주가 새로고침">↻</button>` : '';
    // '실데이터' 딱지는 숨기고(요청), '예시 데이터·일시 중단·불러오는 중'만 경고로 표시(데이터 정직성).
    const badge = r.mode === 'live' ? '' : `<span class="finance-mode">${esc(modes[r.mode])}</span>`;
    return `<article class="finance-indicator"${idAttr}>${badge}${refresh}<h3>${esc(r.name)}${help}</h3><strong>${r.value == null ? '—' : Number(r.value).toLocaleString('ko-KR',{maximumFractionDigits:2})}</strong><span class="finance-unit">${esc(r.unit)}</span>${chart}<p>${dateLine}</p><p>${changeLine}</p></article>`;
  }
  function render(result) {
    data = result;
    // 지표 3종(ECOS) + 엔씨 주가(별도 /stock 로드). 엔씨 카드는 ncData를 사용해 폴링에도 유지.
    $('finance-indicators').innerHTML = data.indicators.map(indicatorCardHtml).join('') + indicatorCardHtml(ncData || ncPlaceholder());
    renderCalendar();
    $('finance-sources').innerHTML = data.sources.map(s => `<div class="finance-source">${link(s.url,s.name)}<span class="finance-mode">${esc(modes[s.mode])}${s.mode==='live' ? ' · '+s.count+'건' : ''}</span></div>`).join('');
    const hasDemo = data.indicators.some(r => r.mode==='demo');
    $('finance-status').textContent = data.pending ? '연결 상태를 확인하고 있습니다. 아래 숫자는 화면 예시입니다.' : (hasDemo ? '예시 데이터가 포함되어 있습니다. 실제 시세·판단 근거로 사용할 수 없습니다.' : '지표별 기준일과 출처별 연결 상태를 확인하세요.');
    // 데이터를 가져온 시점(연·월·일 시:분)을 표기. 초는 생략.
    const fe = $('finance-fetched');
    if (fe) {
      const ft = (!data.pending && data.fetched_at) ? new Date(data.fetched_at) : null;
      const p2 = n => String(n).padStart(2, '0');
      fe.textContent = ft && !isNaN(ft) ? `불러온 시각 ${ft.getFullYear()}.${p2(ft.getMonth()+1)}.${p2(ft.getDate())} ${p2(ft.getHours())}:${p2(ft.getMinutes())}` : '';
    }
    renderNews();
  }
  async function load() {
    if (loading) return;
    loading=true;
    if (!data || !data.items || !data.items.length) $('finance-news').innerHTML = loadingHtml();  // 즉시 로딩 표시
    try {
      for (let attempt=0; attempt<12; attempt++) {
        const result=await json('/api/finance/dashboard'); render(result);
        if (!result.pending) return;
        await pause(2000);
      }
      $('finance-status').textContent='연결 확인이 지연되고 있습니다.\n잠시 후 새로고침해 주세요.\n표시된 숫자는 예시입니다.';
    } catch { $('finance-status').textContent='정보를 불러오지 못했습니다.\n새로고침으로 다시 시도해 주세요.'; }
    finally { loading=false; }
  }
  async function loadStock() {
    const spin = add => { const b=document.querySelector('.finance-stock-refresh'); if(b) b.classList.toggle('spin', add); };
    spin(true);
    try {
      for (let attempt=0; attempt<8; attempt++) {
        const result = await json('/api/finance/stock');
        if (!result.pending) { ncData = result; const el=$('finance-nc'); if(el) el.outerHTML = indicatorCardHtml(ncData); return; }
        await pause(1500);
      }
    } catch { /* 실패 시 직전 값 유지 */ }
    finally { spin(false); }
  }
  async function loadDart(corp) {
    const version=++dartVersion;
    const input=$('finance-corp-code');
    // corp===null → 전체(회사 미지정) 조회. 그 외엔 지정 코드/입력값, 없으면 전체(빈값).
    const code = corp===null ? '' : (corp || (input && input.value.trim()) || '');
    $('finance-dart-status').textContent='공시를 불러오는 중입니다.';
    $('finance-dart-rows').innerHTML='';
    try {
      for(let attempt=0; attempt<8; attempt++) {
        const result=await json('/api/finance/disclosures?corp_code='+encodeURIComponent(code));
        if(version!==dartVersion) return;
        if(result.pending) { await pause(1500); continue; }
        $('finance-dart-status').textContent=result.message || '실데이터 · 최근 공시 '+result.items.length+'건';
        $('finance-dart-rows').innerHTML=result.items.length ? result.items.map(r=>`<tr><td>${esc(r.company)}</td><td>${link(r.url,r.title)}</td><td>${esc(fmtDate(r.date))}</td></tr>`).join('') : '<tr><td colspan="3">표시할 공시가 없습니다.</td></tr>';
        return;
      }
      $('finance-dart-status').textContent='조회가 지연되고 있습니다.\n다시 조회해 주세요.';
    } catch(e) { if(version===dartVersion) $('finance-dart-status').textContent=e.name==='AbortError' ? '조회 시간이 초과되었습니다.\n다시 시도해 주세요.' : e.message; }
  }
  $('finance-filters').addEventListener('click',event=>{
    const button=event.target.closest('[data-category]'); if(!button) return;
    category=button.dataset.category;
    $('finance-filters').querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});
    renderNews();
  });
  function closeTips(){
    document.querySelectorAll('.finance-tip').forEach(t=>t.remove());
    document.querySelectorAll('.finance-help[aria-expanded="true"]').forEach(b=>b.setAttribute('aria-expanded','false'));
  }
  $('finance-indicators').addEventListener('click',event=>{
    if(event.target.closest('.finance-stock-refresh')){ event.preventDefault(); loadStock(); return; }
    const btn=event.target.closest('.finance-help'); if(!btn) return;
    event.preventDefault(); event.stopPropagation();
    const wasOpen=btn.getAttribute('aria-expanded')==='true';
    closeTips();
    if(wasOpen) return;
    const host=$('finance-indicators'), tip=document.createElement('span');
    tip.className='finance-tip'; tip.setAttribute('role','tooltip'); tip.textContent=btn.dataset.tip||'';
    host.appendChild(tip); btn.setAttribute('aria-expanded','true');
    const r=btn.getBoundingClientRect(), hr=host.getBoundingClientRect();
    tip.style.maxWidth=Math.max(180,Math.min(260,host.clientWidth-8))+'px';
    let left=r.left-hr.left+r.width/2-tip.offsetWidth/2;
    left=Math.max(4,Math.min(left,host.clientWidth-tip.offsetWidth-4));
    tip.style.left=left+'px';
    tip.style.top=(r.bottom-hr.top+7)+'px';
    tip.style.setProperty('--arrow',(r.left-hr.left+r.width/2-left)+'px');
  });
  document.addEventListener('click',closeTips);
  $('finance-search').addEventListener('input',renderNews);
  const dartNc=document.getElementById('finance-dart-nc');   // 체크박스: 체크=(주)엔씨만 / 해제=전체 공시
  const setNc=on=>{ if(dartNc) dartNc.checked=!!on; };
  if(dartNc) dartNc.addEventListener('change',()=> loadDart(dartNc.checked ? NC_CORP : null));
  $('finance-dart-form').addEventListener('submit',event=>{   // 입력한 종목코드·고유번호로 조회(검색)
    event.preventDefault();
    const v=($('finance-corp-code').value||'').trim();
    setNc(v===NC_CORP || v==='036570');                       // 엔씨를 직접 조회하면 체크, 그 외/빈값은 해제
    loadDart();
  });
  $('finance-business-form').addEventListener('submit',async event=>{
    event.preventDefault(); const button=event.currentTarget.querySelector('button'); button.disabled=true;
    $('finance-business-result').textContent='국세청 사업자 상태를 확인하고 있습니다.';
    try {
      const r=await json('/api/finance/business-status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({number:$('finance-business-number').value.trim()})});
      $('finance-business-result').textContent=r.mode==='live' ? [r.status,r.tax_type,r.end_date ? '폐업일 '+r.end_date : '',r.checked_at ? '조회 '+new Date(r.checked_at).toLocaleString('ko-KR') : ''].filter(Boolean).join(' · ') : r.message;
    } catch(e) { $('finance-business-result').textContent=e.name==='AbortError' ? '조회 시간이 초과되었습니다.\n다시 시도해 주세요.' : e.message; }
    finally {button.disabled=false;}
  });
  // ===== 상단 탭 분류(지표/계산기/세무·공시/브리핑) — 보기 전환만, 데이터는 그대로 로드 =====
  const fintabs=$('fin-tabs');
  if(fintabs){
    fintabs.addEventListener('click',event=>{
      const b=event.target.closest('[data-fintab]'); if(!b) return;
      const key=b.dataset.fintab;
      fintabs.querySelectorAll('[data-fintab]').forEach(x=>{const on=x===b;x.classList.toggle('active',on);x.setAttribute('aria-selected',String(on));});
      document.querySelectorAll('#view-finance .fin-panel').forEach(p=>{p.hidden=p.dataset.panel!==key;});
    });
  }

  // ===== 돈 계산기 (순수 계산 — 외부 데이터 없음) =====
  const won=n=>Math.round(n).toLocaleString('ko-KR')+'원';
  const man=n=>{ const v=Math.round(n/10000); return v.toLocaleString('ko-KR')+'만원'; };
  // 결과를 정의목록(dl)로. rows: [라벨, 값, 강조?]
  const dlHtml=rows=>`<dl>${rows.map(([l,v,hl])=>`<dt>${esc(l)}</dt><dd${hl?' class="calc-hl"':''}>${esc(v)}</dd>`).join('')}</dl>`;
  const num=id=>{ const el=$(id); return el?Number(el.value):0; };

  // 근로소득공제(2025)
  function earnedDeduction(g){
    let d;
    if(g<=5000000) d=g*0.7;
    else if(g<=15000000) d=3500000+(g-5000000)*0.4;
    else if(g<=45000000) d=7500000+(g-15000000)*0.15;
    else if(g<=100000000) d=12000000+(g-45000000)*0.05;
    else d=14750000+(g-100000000)*0.02;
    return Math.min(d,20000000);
  }
  // 종합소득세 산출세액(2025 세율·누진공제)
  function progressiveTax(b){
    const t=[[14000000,0.06,0],[50000000,0.15,1260000],[88000000,0.24,5760000],[150000000,0.35,15440000],[300000000,0.38,19940000],[500000000,0.40,25940000],[1000000000,0.42,35940000],[Infinity,0.45,65940000]];
    for(const [lim,rate,ded] of t){ if(b<=lim) return Math.max(0,b*rate-ded); }
    return 0;
  }
  function calcSalary(){
    const out=$('calc-salary-out'); if(!out) return;
    const annual=(num('calc-salary-annual')||0)*10000;
    if(annual<=0){ out.innerHTML=''; return; }
    const family=Math.max(1, Math.floor(num('calc-salary-family'))||1);
    const monthly=annual/12;
    const nontax=Math.min(Math.max(0,num('calc-salary-nontax')), monthly);
    const taxable=Math.max(0, monthly-nontax);
    // 4대보험(근로자 부담, 2025 요율)
    const pension=taxable*0.045, health=taxable*0.03545, care=health*0.1295, employ=taxable*0.009;
    const insurance=pension+health+care+employ;
    // 소득세(근사 — 연 산출세액 ÷ 12)
    const gross=taxable*12;
    const base=Math.max(0, gross-earnedDeduction(gross) - 1500000*family - pension*12);
    const calc=progressiveTax(base);
    let credit=calc<=1300000 ? calc*0.55 : 715000+(calc-1300000)*0.30;
    let cap; if(gross<=33000000) cap=740000; else if(gross<=70000000) cap=Math.max(660000,740000-(gross-33000000)*0.008); else cap=Math.max(500000,660000-(gross-70000000)*0.5);
    credit=Math.min(credit,cap);
    const incomeTax=Math.max(0,calc-credit)/12, localTax=incomeTax*0.1;
    const take=monthly-insurance-incomeTax-localTax;
    out.innerHTML=dlHtml([
      ['월 실수령액', won(take)+' (약 '+man(take)+')', true],
      ['월 급여 (세전)', won(monthly)],
      ['4대보험', '−'+won(insurance)],
      ['소득세+지방세', '−'+won(incomeTax+localTax)],
      ['연 실수령 (대략)', won(take*12)],
    ]);
  }
  function calcSave(){
    const out=$('calc-save-out'); if(!out) return;
    const type=$('calc-save-type').value, amount=Math.max(0,num('calc-save-amount')), rate=Math.max(0,num('calc-save-rate'))/100, n=Math.max(0,Math.floor(num('calc-save-months'))), comp=$('calc-save-comp').value;
    if(amount<=0||n<=0){ out.innerHTML=''; return; }
    const i=rate/12; let principal, interest;
    if(type==='lump'){
      principal=amount;
      interest = comp==='monthly' ? amount*(Math.pow(1+i,n)-1) : amount*rate*(n/12);
    } else {
      principal=amount*n;
      if(comp==='monthly'){ let fv=0; for(let k=1;k<=n;k++) fv+=amount*Math.pow(1+i,n-k+1); interest=fv-principal; }
      else interest=amount*i*(n*(n+1)/2);
    }
    const tax=interest*0.154, net=interest-tax;
    out.innerHTML=dlHtml([
      ['만기 수령액', won(principal+net), true],
      ['원금 합계', won(principal)],
      ['세전 이자', won(interest)],
      ['이자소득세 (15.4%)', '−'+won(tax)],
      ['세후 이자', won(net)],
    ]);
  }
  function calcLoan(){
    const out=$('calc-loan-out'); if(!out) return;
    const P=Math.max(0,num('calc-loan-amount')), rate=Math.max(0,num('calc-loan-rate'))/100, n=Math.max(0,Math.floor(num('calc-loan-months'))), type=$('calc-loan-type').value;
    if(P<=0||n<=0){ out.innerHTML=''; return; }
    const i=rate/12;
    if(type==='annuity'){
      const pay = i>0 ? P*i*Math.pow(1+i,n)/(Math.pow(1+i,n)-1) : P/n;
      const total=pay*n;
      out.innerHTML=dlHtml([['월 상환액', won(pay), true],['총 상환액', won(total)],['총 이자', won(total-P)]]);
    } else {
      const part=P/n; let bal=P, totInt=0, first=0, last=0;
      for(let k=0;k<n;k++){ const int=bal*i, pay=part+int; if(k===0)first=pay; if(k===n-1)last=pay; totInt+=int; bal-=part; }
      out.innerHTML=dlHtml([['첫 달 상환액', won(first), true],['마지막 달', won(last)],['총 이자', won(totInt)],['총 상환액', won(P+totInt)]]);
    }
  }
  ['calc-salary-annual','calc-salary-family','calc-salary-nontax'].forEach(id=>{const el=$(id); if(el)el.addEventListener('input',calcSalary);});
  ['calc-save-amount','calc-save-rate','calc-save-months'].forEach(id=>{const el=$(id); if(el)el.addEventListener('input',calcSave);});
  ['calc-save-type','calc-save-comp'].forEach(id=>{const el=$(id); if(el)el.addEventListener('change',calcSave);});
  ['calc-loan-amount','calc-loan-rate','calc-loan-months'].forEach(id=>{const el=$(id); if(el)el.addEventListener('input',calcLoan);});
  { const el=$('calc-loan-type'); if(el)el.addEventListener('change',calcLoan); }
  { const t=$('calc-save-type'); if(t)t.addEventListener('change',()=>{ const lb=$('calc-save-amount-label'); if(lb)lb.textContent = t.value==='lump'?'예치금 (원)':'월 납입액 (원)'; }); }

  // 재무세무 진입 시: 체크되어 있으면 (주)엔씨, 아니면(기본) 전체 공시를 조회.
  window.onShowFinance=()=>{load();loadStock(); loadDart(dartNc && dartNc.checked ? NC_CORP : null);};
})();
