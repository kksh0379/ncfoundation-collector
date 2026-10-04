/* Event discovery: compact month/day agenda and preference-driven banner feed. */
(() => {
  const topics = ['IT·기술','AI·데이터','AI 윤리','산업·비즈니스','문화·전시','교육·공익','반려동물','기타'];
  const esc = value => escapeHtml(String(value || ''));
  const isoToday = () => new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Seoul'}).format(new Date());
  const isoDate = date => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  const dayEvents = (list, iso) => list.filter(s => s.start_date && s.start_date <= iso && (s.end_date || s.start_date) >= iso).sort((a,b)=>(b.start_date||'').localeCompare(a.start_date||''));
  function safeLink(value) { try {const u=new URL(value);return /^https?:$/.test(u.protocol)?u.href:'';}catch(_){return ''; } }
  function eventCard(s, banner=false, position=0) {
    const link=safeLink(s.source_url || s.url);
    const image=safeLink(s.image_url);
    const index=position%6;
    if(banner){
      const art=`<div class="ed-banner-visual" aria-hidden="true">${image?`<img loading="lazy" src="/api/img?u=${encodeURIComponent(image)}" alt="" onerror="this.remove()">`:''}</div><div class="ed-banner-copy"><span class="ed-banner-category">${esc(eventCategories(s)[0])}</span><h3>${esc(s.title || '행사')}</h3><div class="ed-banner-facts"><span>${esc(eventDateBadge(s))}</span>${eventPlace(s)?`<span>${esc(eventPlace(s))}</span>`:''}</div></div>${link?'<span class="ed-banner-arrow" aria-hidden="true">↗</span>':''}`;
      return `<li class="ed-card ed-banner">${link?`<a class="ed-art ed-art-${index}" href="${esc(link)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(s.title || '행사')} 원문 보기">${art}</a>`:`<div class="ed-art ed-art-${index}">${art}</div>`}</li>`;
    }
    return eventAlbumCard({...s,url:safeLink(s.url),source_url:link,image_url:image}).outerHTML;
  }
  let month=null,selected=null,expanded=false,todayPulse=false,calendarItems=[];
  function calendar(list) {
    calendarItems=list;
    const today=isoToday();
    if(!month) month=today.slice(0,7);
    const [y,m]=month.split('-').map(Number),last=new Date(y,m,0).getDate();
    const monthList=list.filter(s=>s.start_date && s.start_date<=`${month}-${last}` && (s.end_date||s.start_date)>=`${month}-01`);
    if(!selected || !selected.startsWith(month)) selected=today.startsWith(month)?today:(monthList.map(s=>s.start_date<`${month}-01`?`${month}-01`:s.start_date).sort()[0] || `${month}-01`);
    const counts=Array.from({length:last},(_,i)=>dayEvents(monthList,`${month}-${String(i+1).padStart(2,'0')}`).length);
    const max=Math.max(1,...counts);
    function cell(iso,day,label='') {
      const n=dayEvents(list,iso).length,level=n?Math.ceil(n/max*4):0;
      return `<button type="button" class="ed-day ed-heat-${level}${iso===selected?' selected':''}${iso===today?' today':''}${iso===today&&todayPulse?' ed-today-highlight':''}" data-date="${iso}" aria-pressed="${iso===selected}" aria-label="${esc(iso)} 행사 ${n}건"><strong>${day}</strong>${label||iso===today?`<small>${label || '오늘'}</small>`:''}<span>${n?n+'건':'—'}</span></button>`;
    }
    let grid='';
    if(!expanded){
      for(let i=0;i<new Date(y,m-1,1).getDay();i++)grid+='<span></span>';
      for(let d=1;d<=last;d++)grid+=cell(`${month}-${String(d).padStart(2,'0')}`,d);
    }else{
      const date=new Date(selected+'T12:00:00');date.setDate(date.getDate()-date.getDay());
      for(let i=0;i<7;i++){const iso=isoDate(date);grid+=cell(iso,date.getDate(),['일','월','화','수','목','금','토'][i]);date.setDate(date.getDate()+1);}
    }
    const chosen=dayEvents(list,selected);
    const title=new Intl.DateTimeFormat('ko-KR',{month:'long',day:'numeric',weekday:'long'}).format(new Date(selected+'T12:00:00'));
    const root=document.getElementById('cal-event');
    root.innerHTML=`<div class="ed-cal-head"><button type="button" data-cal="prev" aria-label="이전 달">‹</button><h3>${y}년 ${m}월 <small>${monthList.length}개 행사</small></h3><button type="button" data-cal="next" aria-label="다음 달">›</button><button type="button" data-cal="today">오늘</button></div>
      ${!expanded?'<div class="ed-dows">'+['일','월','화','수','목','금','토'].map(d=>`<span>${d}</span>`).join('')+'</div>':''}
      <div class="ed-month-grid${expanded?' ed-week':''}">${grid}</div>
      ${!expanded?'<p class="ed-hint">진한 색일수록 행사가 많아요.<br>날짜를 누르면 아래에 상세 목록을 보여줘요.</p>':''}
      <div class="ed-agenda-head"><h3>${esc(title)} <small>${chosen.length}개 행사</small></h3><button type="button" data-cal="expand">${expanded?'달력 펼치기 ↓':'목록 크게 보기 ↑'}</button></div>
      <p class="ed-hint">선택한 날짜에 진행 중인 여러 날 행사도 포함해요.</p>
      <ul class="list album-grid ed-agenda-list">${chosen.length?chosen.map(s=>eventCard(s)).join(''):'<li class="ed-empty">이 날짜에는 선택 분야의 수집 행사가 없어요.</li>'}</ul>
      ${list.some(s=>!s.start_date)?'<p class="ed-hint">일정 미정 행사는 앨범에서 확인할 수 있어요.</p>':''}`;
    root.onclick=e=>{
      const b=e.target.closest('button[data-date],button[data-cal]');if(!b)return;
      todayPulse=b.dataset.cal==='today';
      if(b.dataset.date){selected=b.dataset.date;month=selected.slice(0,7);}
      if(b.dataset.cal==='expand')expanded=!expanded;
      if(b.dataset.cal==='today'){month=today.slice(0,7);selected=today;}
      if(['prev','next'].includes(b.dataset.cal)){month=isoDate(new Date(y,m-1+(b.dataset.cal==='next'?1:-1),1)).slice(0,7);selected=null;}
      calendar(calendarItems);
      const focus=root.querySelector(b.dataset.cal==='today'?`[data-date="${today}"]`:b.dataset.date?`[data-date="${selected}"]`:`[data-cal="${b.dataset.cal}"]`);focus?.focus({preventScroll:true});
      if(b.dataset.cal==='today')focus?.scrollIntoView?.({block:'nearest',behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    };
  }
  // Instant ranking from already loaded events; never calls an AI or a network API.
  function localRecommendations(list, interests) {
    const today=isoToday(),compact=t=>String(t||'').replace(/\s+/g,'').toLowerCase();
    const ranked=list.filter(s=>(s.end_date||s.start_date||today)>=today).map(s=>{
      const tags=eventCategories(s),text=compact((s.title||'')+' '+(s.content||''));
      const fields=interests.topics.filter(t=>tags.includes(t));
      const words=interests.keywords.filter(k=>text.includes(compact(k)));
      return {item:s,tags,score:words.length*5+fields.length*2};
    }).filter(r=>!interests.topics.length&&!interests.keywords.length||r.score>0);
    ranked.sort((a,b)=>b.score-a.score||((a.item.start_date||'9999')<today?today:a.item.start_date||'9999').localeCompare((b.item.start_date||'9999')<today?today:b.item.start_date||'9999'));
    let rows=ranked;
    if(!interests.topics.length&&!interests.keywords.length){
      const selected=[],seen=new Set();
      for(const topic of topics){let n=0;for(const row of ranked){if(row.tags.includes(topic)&&!seen.has(row.item.url)){selected.push(row);seen.add(row.item.url);if(++n===2)break;}}}
      rows=selected.concat(ranked.filter(r=>!seen.has(r.item.url)));
    }
    return {status:'ready',mode:'rules',items:rows.slice(0,12).map(r=>r.item)};
  }
  let prefs={topics:[],keywords:[]},initialized=false,available=[],result=null,busy=false,requestVersion=0;
  try {const saved=JSON.parse(localStorage.getItem('event-interests')||'null');if(saved&&Array.isArray(saved.topics)&&Array.isArray(saved.keywords))prefs={topics:saved.topics.filter(t=>topics.includes(t)),keywords:saved.keywords.filter(k=>typeof k==='string'&&k.length<=40).slice(0,8)};}catch(_){}
  function persist(){try{localStorage.setItem('event-interests',JSON.stringify(prefs));}catch(_){}}
  function renderResult(){
    const target=document.getElementById('ed-results');if(!target)return;
    const notice=document.getElementById('ed-status');
    if(busy){notice.textContent='AI 추천 중…';notice.hidden=false;}
    if(!result){notice.textContent='';notice.hidden=true;target.innerHTML='';return;}
    if(!busy) notice.textContent=(result.ai_error || !result.items?.length ? result.notice||'' : '').replace(/([가-힣][.!?]) +(?=[가-힣])/g,'$1\n');notice.hidden=!notice.textContent;notice.dataset.aiError=result.ai_error||'';
    if(!busy&&result.ai_error==='credit_balance'){
      const recharge=document.createElement('a');recharge.href='https://platform.claude.com/settings/billing';recharge.target='_blank';recharge.rel='noopener noreferrer';recharge.className='ed-recharge';recharge.textContent='[충전하기]';notice.append(' ',recharge);
    }
    const keys=new Set(available.map(s=>s.url));
    const items=(result.items||[]).filter(s=>keys.has(s.url));
    target.innerHTML=items.length?items.map((s,i)=>eventCard(s,true,i)).join(''):'<li class="ed-empty">표시할 추천이 없어요.<br>관심사나 검색어를 바꿔 보세요.</li>';
    document.getElementById('ed-mode').textContent=result.mode==='ai'?'AI 추천':result.mode==='rules'?'관심사 기반 추천':'';
  }
  async function recommend(){
    if(busy)return;
    const version=++requestVersion;busy=true;renderResult();
    const snapshot=JSON.parse(JSON.stringify(prefs));
    try{
      for(let i=0;i<50;i++){
        if(version!==requestVersion)return;
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
        let r;try{r=await fetch('/api/events/recommend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(snapshot),signal:controller.signal});}finally{clearTimeout(timer);}
        if(!r.ok)throw Error('추천을 요청하지 못했어요. 잠시 후 다시 시도해 주세요.');
        const data=await r.json();if(version!==requestVersion)return;
        if(data.status==='pending'){await new Promise(resolve=>setTimeout(resolve,1500));continue;}
        result=data;return;
      }
      throw Error('추천이 지연되고 있어요. 잠시 후 다시 추천해 주세요.');
    }catch(e){if(version===requestVersion)result={notice:e.message||'추천을 불러오지 못했어요.',items:[]};}
    finally{if(version===requestVersion){busy=false;renderResult();}}
  }
  function chips(){
    document.getElementById('ed-keywords').innerHTML=prefs.keywords.map((k,i)=>`<button type="button" class="ed-chip selected" data-remove="${i}" aria-label="${esc(k)} 키워드 제거">${esc(k)} ×</button>`).join('');
  }
  function curation(list){
    available=list;
    if(!busy&&(!result||result.mode==='rules'&&!result.ai_error))result=localRecommendations(list,prefs);
    if(initialized){renderResult();return;}
    initialized=true;
    const root=document.getElementById('event-curation');
    root.innerHTML=`<div class="ed-curation-toolbar"><span id="ed-mode" class="ed-mode"></span><button type="button" id="ed-settings">관심사 설정</button><button type="button" id="ed-ai-recommend">AI 추천받기</button></div><p id="ed-status" role="status" aria-live="polite" hidden></p><ul id="ed-results" class="ed-feed"></ul><dialog id="ed-dialog" aria-labelledby="ed-dialog-title"><section class="ed-preferences"><div class="ed-dialog-head"><h3 id="ed-dialog-title">관심사 설정</h3><button type="button" id="ed-close" aria-label="설정 닫기">×</button></div><p>미선택 시 전체 행사에서 추천해요.</p>
      <fieldset><legend>관심 분야 · 여러 개 선택</legend><div class="ed-topics">${topics.map(t=>`<label><input type="checkbox" value="${esc(t)}" ${prefs.topics.includes(t)?'checked':''}>${esc(t)}</label>`).join('')}</div></fieldset>
      <label for="ed-keyword-input" class="ed-label">관심 키워드</label><div id="ed-keywords" class="ed-chips"></div>
      <form id="ed-keyword-form"><input id="ed-keyword-input" maxlength="40" placeholder="키워드 직접 입력" aria-label="관심 키워드"><button type="submit">추가</button></form>
      <div class="ed-chips ed-suggestions">${['AI 거버넌스','정보보안','생성형 AI','접근성','클라우드','개발자'].map(k=>`<button type="button" class="ed-chip" data-keyword="${esc(k)}">${esc(k)}</button>`).join('')}</div>
      <button type="button" class="ed-primary" id="ed-recommend">적용하기</button></section></dialog>`;
    const addKeyword=k=>{k=k.trim();if(!k)return;if(prefs.keywords.length>=8){toast('키워드는 8개까지 선택할 수 있어요.');return;}if(!prefs.keywords.includes(k))prefs.keywords.push(k);persist();chips();};
    root.querySelector('.ed-topics').onchange=()=>{prefs.topics=Array.from(root.querySelectorAll('.ed-topics input:checked')).map(b=>b.value);persist();};
    root.onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.keyword)addKeyword(b.dataset.keyword);if(b.dataset.remove!==undefined){prefs.keywords.splice(+b.dataset.remove,1);persist();chips();}};
    root.querySelector('#ed-keyword-form').onsubmit=e=>{e.preventDefault();const input=root.querySelector('#ed-keyword-input');addKeyword(input.value);input.value='';};
    const dialog=root.querySelector('#ed-dialog');
    root.querySelector('#ed-settings').onclick=()=>dialog.showModal();
    root.querySelector('#ed-close').onclick=()=>dialog.close();
    dialog.onclick=e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}};
    root.querySelector('#ed-recommend').onclick=()=>{dialog.close();requestVersion++;busy=false;result=localRecommendations(available,prefs);renderResult();};
    root.querySelector('#ed-ai-recommend').onclick=()=>recommend();
    chips();renderResult();
  }
  window.EventDiscovery={calendar,curation,localRecommendations};
})();
