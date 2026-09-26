"use strict";

// ===== 인증 상태(아이디 기반) =====
let CURRENT_USER = null;      // null=미로그인, "admin" 또는 "tester1"…
function isLoggedIn() { return !!CURRENT_USER; }
function applyAuthUI(user, admin) {
  CURRENT_USER = user || null;
  document.body.classList.toggle("is-admin", !!admin);
  document.body.classList.toggle("is-loggedin", !!user);
  const fu = document.getElementById("foot-user");
  if (fu) fu.textContent = user ? (user === "admin" ? "관리자" : user) + " 님" : "";
}
// 세션 확인 → 로그인 상태면 개인 데이터(스크랩/읽음) 로드
async function initAuth() {
  let me = { user: null, admin: false };
  try { me = await (await fetch("/api/me")).json(); } catch (e) { /* 무시 */ }
  applyAuthUI(me.user, me.admin);
  if (me.user) { await loadMyData(); }
}

function escapeHtml(s) {
  if (!s) return "";
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDate(iso) {
  if (!iso) return "작성일 미상";
  return iso.replace("T", " ");
}

// ===== 읽음 여부 / 스크랩 (아이디 기반, 서버 저장) =====
let READ = new Set();     // 읽은 글 key
let SCRAP = {};           // key -> 스냅샷(각 스냅샷에 groups:[] 포함)
let GROUPS = [];          // 커스텀 그룹 [{id,name}]
let pendingScrapKey = null;  // 미로그인 상태에서 스크랩 시도 → 로그인 후 이어서 처리
let scrapQuery = "";      // 나의 스크랩 검색어
let scrapFilterGroup = "all";  // 활성 그룹 필터(all 또는 group id)
// 게시글 고유키: 원문/URL 기준
function keyOf(it) { return String(it.url || it.source_url || it.title || "").trim(); }
function isRead(k) { return READ.has(k); }
function isScrapped(k) { return !!SCRAP[k]; }
async function api(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.ok ? r.json() : Promise.reject(r);
}
// 로그인 시 개인 데이터 로드 → 화면 반영
async function loadMyData() {
  try {
    const d = await (await fetch("/api/mydata")).json();
    READ = new Set(d.reads || []);
    SCRAP = {};
    (d.scraps || []).forEach((s) => { if (s.key) { s.groups = s.groups || []; SCRAP[s.key] = s; } });
    GROUPS = d.groups || [];
  } catch (e) { READ = new Set(); SCRAP = {}; GROUPS = []; }
  applyUserStateToDom();
  updateScrapBadge();
}
// 이미 렌더된 카드에 읽음/스크랩 상태를 반영(로그인 직후 등)
function applyUserStateToDom() {
  document.querySelectorAll(".card[data-key]").forEach((c) => {
    c.classList.toggle("is-read", isRead(c.dataset.key));
  });
  document.querySelectorAll(".scrap-btn[data-key]").forEach((b) => {
    b.classList.toggle("on", isScrapped(b.dataset.key));
  });
}
// 읽음 처리(로그인 사용자만, 서버 저장)
function markRead(k) {
  if (!k || !isLoggedIn() || READ.has(k)) return;
  READ.add(k);
  api("/api/read", { key: k }).catch(() => { /* 실패해도 화면은 유지 */ });
}
function todayStr() { const n = new Date(), p = (x) => String(x).padStart(2, "0"); return n.getFullYear() + "-" + p(n.getMonth() + 1) + "-" + p(n.getDate()); }
function isToday(iso) { return !!iso && String(iso).slice(0, 10) === todayStr(); }

// 화면에 렌더된 항목의 스냅샷 보관(스크랩 저장·복원용)
const ITEM_INDEX = {};
function registerItem(it, tab, link) {
  const k = keyOf(it); if (!k) return k;
  ITEM_INDEX[k] = {
    key: k, title: it.title || "", link: link || it.source_url || it.url || "", url: it.url || "",
    date: it.published_at || "", author: it.author || it.source || "", tab: tab || "",
    service: it.service || "", category: it.category || "", channel: it.channel || "",
    account: it.account || "", content: it.content || "",
  };
  return k;
}
// 카드 우상단 스크랩 버튼 + 오늘글 N딱지 HTML
function scrapBtnHtml(key) {
  return `<button class="scrap-btn${isScrapped(key) ? " on" : ""}" type="button" data-key="${escapeHtml(key)}" aria-label="스크랩" title="스크랩">`
    + `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M6 3h12c.55 0 1 .45 1 1v17l-7-3.9L5 21V4c0-.55.45-1 1-1z"/></svg></button>`;
}
function newBadgeHtml(iso) { return isToday(iso) ? `<span class="badge-new" title="오늘 등록">N</span>` : ""; }
function readClass(key) { return isRead(key) ? " is-read" : ""; }
// 카드 '링크 복사' 버튼
function copyBtnHtml(url) {
  if (!url) return "";
  return `<button type="button" class="copy-btn" data-url="${escapeHtml(url)}">🔗 링크 복사</button>`;
}
async function copyToClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch (e) { /* 폴백으로 진행 */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) { return false; }
}
// 위임: 링크 복사 버튼 클릭
document.addEventListener("click", (e) => {
  const b = e.target.closest(".copy-btn");
  if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const url = b.dataset.url;
  if (!url) return;
  copyToClipboard(url).then((ok) => toast(ok ? "링크를 복사했어요" : "복사 실패 — 원문을 길게 눌러 복사해 주세요"));
});

// 짧은 토스트 메시지
let _toastTimer = null;
function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(_toastTimer); _toastTimer = setTimeout(() => t.classList.remove("show"), 1400);
}
// 위험 동작(삭제/초기화) 확인: 모바일에서 native confirm()이 막히는 경우가 있어
// '한 번 더 눌러 확정'(두 번 탭) 방식으로 대체한다. 첫 탭=무장(빨간 확정 상태)·둘째 탭=실행.
function armConfirm(btn, armedText, onConfirm) {
  if (!btn) { onConfirm(); return; }
  if (btn._armed) {
    clearTimeout(btn._armTimer); btn._armed = false;
    if (btn._orig != null) btn.textContent = btn._orig;
    btn.classList.remove("armed");
    onConfirm();
    return;
  }
  if (btn._orig == null) btn._orig = btn.textContent;
  btn._armed = true; btn.textContent = armedText; btn.classList.add("armed");
  toast("한 번 더 누르면 실행돼요");
  btn._armTimer = setTimeout(() => {
    btn._armed = false;
    if (btn._orig != null) btn.textContent = btn._orig;
    btn.classList.remove("armed");
  }, 4000);
}
// 스크랩 토글(로그인 필요, 서버 저장, +토스트)
function toggleScrap(key) {
  if (!key) return;
  if (!isLoggedIn()) {            // 미로그인 → 로그인 유도(로그인 후 이어서 스크랩)
    pendingScrapKey = key;
    toast("로그인하면 스크랩할 수 있어요");
    openLogin();
    return;
  }
  const wasOn = isScrapped(key);
  if (wasOn) {
    const backup = SCRAP[key];
    delete SCRAP[key];
    syncScrapUI(key); toast("스크랩을 취소했어요");
    api("/api/scrap", { op: "del", key }).catch(() => { SCRAP[key] = backup; syncScrapUI(key); toast("저장 실패 — 다시 시도해 주세요"); });
  } else {
    const snap = ITEM_INDEX[key];
    if (!snap) return;
    SCRAP[key] = Object.assign({}, snap, { ts: Date.now() });
    syncScrapUI(key); toast("스크랩했어요 ⭐");
    api("/api/scrap", { op: "add", key, item: snap }).catch(() => { delete SCRAP[key]; syncScrapUI(key); toast("저장 실패 — 다시 시도해 주세요"); });
  }
}
function syncScrapUI(key) {
  document.querySelectorAll('.scrap-btn[data-key]').forEach((b) => {
    if (b.dataset.key === key) b.classList.toggle("on", isScrapped(key));
  });
  updateScrapBadge();
  const m = document.getElementById("scrap-modal");
  if (m && !m.hidden) renderScraps();
}

// 위임: 원문 링크 클릭 → 읽음 처리 / 스크랩 버튼 클릭 → 토글
document.addEventListener("click", (e) => {
  const sb = e.target.closest(".scrap-btn");
  if (sb) { e.preventDefault(); e.stopPropagation(); toggleScrap(sb.dataset.key); return; }
  const a = e.target.closest('a[target="_blank"]');
  if (a) { const card = a.closest("[data-key]"); if (card) { markRead(card.dataset.key); card.classList.add("is-read"); } }
});

// ----------------------------- 탭 전환 -----------------------------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("panel-" + tab.dataset.tab).classList.add("active");
    document.body.classList.toggle("tab-event", tab.dataset.tab === "event");
    document.body.classList.toggle("tab-report", tab.dataset.tab === "report");
    try { tab.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" }); } catch (e) { /* 무시 */ }
    if (typeof syncSearchInput === "function") syncSearchInput();
    if (typeof updateCount === "function") updateCount(tab.dataset.tab);
    if (tab.dataset.tab === "report" && typeof loadReport === "function") loadReport();
  });
});

// ----------------------------- 보기 방식(리스트/카드) -----------------------------
function setView(mode) {
  mode = mode === "list" ? "list" : "card";  // 기본 card
  document.body.classList.toggle("view-list", mode === "list");
  document.body.classList.toggle("view-card", mode === "card");
  document.querySelectorAll("#view-toggle button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === mode));
  try { localStorage.setItem("nvView", mode); } catch (e) { /* 무시 */ }
}
(function initView() {
  let saved = "card";
  try { saved = localStorage.getItem("nvView") || "card"; } catch (e) { /* 무시 */ }
  setView(saved);
  const seg = document.getElementById("view-toggle");
  if (seg) seg.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view)));
})();

// ----------------------------- 로딩 표시 / 무한 스크롤 -----------------------------
// 🐱 로딩 스피너: 춤추는 하얀 코숏(직접 그린 애니메이션 SVG). 애니메이션은 style.css.
function catSpin(label) {
  return `<div class="cat-load">
    <svg class="cat-dance" viewBox="0 0 100 100" role="img" aria-label="불러오는 중">
      <ellipse class="cd-shadow" cx="50" cy="93" rx="22" ry="3.6" fill="#000"/>
      <g class="cd-all">
        <!-- 꼬리 -->
        <path class="cd-tail" d="M37,80 C21,81 15,66 22,56 C25,51 31,53 31,59 C31,65 28,71 38,75 Z"
              fill="#fff" stroke="#e4e4ea" stroke-width="2" stroke-linejoin="round"/>
        <!-- 팔(양쪽으로 흔들흔들) -->
        <g class="cd-armR">
          <path d="M37,60 C28,60 23,54 22,47" fill="none" stroke="#fff" stroke-width="8" stroke-linecap="round"/>
          <circle cx="21.5" cy="46" r="4.6" fill="#fff" stroke="#e4e4ea" stroke-width="2"/>
        </g>
        <g class="cd-armL">
          <path d="M63,60 C72,60 77,54 78,47" fill="none" stroke="#fff" stroke-width="8" stroke-linecap="round"/>
          <circle cx="78.5" cy="46" r="4.6" fill="#fff" stroke="#e4e4ea" stroke-width="2"/>
        </g>
        <!-- 몸통 -->
        <path d="M34,86 C31,66 37,55 50,55 C63,55 69,66 66,86 C66,89 60,90.5 50,90.5 C40,90.5 34,89 34,86 Z"
              fill="#fff" stroke="#e4e4ea" stroke-width="2" stroke-linejoin="round"/>
        <ellipse cx="50" cy="77" rx="8.5" ry="11" fill="#f4f5f8"/>
        <!-- 발 -->
        <ellipse cx="43" cy="89.5" rx="5.4" ry="4.1" fill="#fff" stroke="#e4e4ea" stroke-width="2"/>
        <ellipse cx="57" cy="89.5" rx="5.4" ry="4.1" fill="#fff" stroke="#e4e4ea" stroke-width="2"/>
        <!-- 머리 -->
        <g class="cd-head">
          <path d="M31,29 L33,12 L47,25 Z" fill="#fff" stroke="#e4e4ea" stroke-width="2" stroke-linejoin="round"/>
          <path d="M69,29 L67,12 L53,25 Z" fill="#fff" stroke="#e4e4ea" stroke-width="2" stroke-linejoin="round"/>
          <path d="M34.5,26 L35.5,17 L43,25 Z" fill="#f6b8ce"/>
          <path d="M65.5,26 L64.5,17 L57,25 Z" fill="#f6b8ce"/>
          <circle cx="50" cy="37" r="20" fill="#fff" stroke="#e4e4ea" stroke-width="2"/>
          <ellipse cx="37" cy="43" rx="4" ry="2.6" fill="#f9ccdb"/>
          <ellipse cx="63" cy="43" rx="4" ry="2.6" fill="#f9ccdb"/>
          <path d="M40,37 q3,-4.5 6,0" fill="none" stroke="#4a4a52" stroke-width="2.4" stroke-linecap="round"/>
          <path d="M54,37 q3,-4.5 6,0" fill="none" stroke="#4a4a52" stroke-width="2.4" stroke-linecap="round"/>
          <path d="M48.4,41.5 h3.2 l-1.6,1.9 Z" fill="#f2879f"/>
          <path d="M50,43.4 q-2.3,2.4 -4.6,0 M50,43.4 q2.3,2.4 4.6,0" fill="none" stroke="#c98aa0" stroke-width="1.4" stroke-linecap="round"/>
          <path d="M32,39 H22 M33,43 H23.5" stroke="#dcdce3" stroke-width="1.3" stroke-linecap="round"/>
          <path d="M68,39 H78 M67,43 H76.5" stroke="#dcdce3" stroke-width="1.3" stroke-linecap="round"/>
        </g>
      </g>
    </svg>
    <span class="cat-load-label">${escapeHtml(label || "불러오는 중…")}</span>
  </div>`;
}

function showLoading(el) {
  el.innerHTML = `<li class="empty">${catSpin("불러오는 중…")}</li>`;
}

// 리스트 엘리먼트 id(list-cat 등)에서 탭 키를 얻는다.
function tabKeyFromEl(el) {
  const k = (el && el.id || "").replace(/^list-/, "");
  return (typeof CRAWL_UI !== "undefined" && CRAWL_UI[k]) ? k : "";
}

// 빈/실패 상태를 '오류처럼 보이지 않게' + 다시 불러오기 버튼과 함께 표시.
function emptyState(el, message) {
  const key = tabKeyFromEl(el);
  el.innerHTML = `<li class="empty">
    <div class="empty-msg">${message}</div>
    <button type="button" class="retry-btn" data-reload="${key}">↻ 다시 불러오기</button>
    <div class="empty-hint">계속 비어 있으면 페이지를 새로고침해 주세요.</div>
  </li>`;
}

// 다시 불러오기 버튼(위임): 해당 탭만 다시 로드, 탭을 모르면 전체 새로고침.
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".retry-btn");
  if (!btn) return;
  if (btn.dataset.clearSearch) { clearSearch(); return; }
  const key = btn.dataset.reload;
  if (key && CRAWL_UI[key] && CRAWL_UI[key].reload) CRAWL_UI[key].reload();
  else location.reload();
});

// units 배열을 15개씩 렌더하고, 끝 센티넬이 화면에 들어오면 다음 묶음을 이어붙인다.
function renderInfinite(el, units, makeNode, emptyMsg) {
  el.innerHTML = "";
  if (!units.length) { emptyState(el, emptyMsg); return; }
  const CHUNK = 15;
  let i = 0;
  const sentinel = document.createElement("li");
  sentinel.className = "scroll-sentinel";
  el.appendChild(sentinel);
  function more() {
    const frag = document.createDocumentFragment();
    for (let n = 0; n < CHUNK && i < units.length; n++, i++) frag.appendChild(makeNode(units[i]));
    el.insertBefore(frag, sentinel);
    if (i >= units.length) { obs.disconnect(); sentinel.remove(); }
  }
  const obs = new IntersectionObserver((ents) => { if (ents[0].isIntersecting) more(); }, { rootMargin: "300px" });
  obs.observe(sentinel);
  more();
}

// ----------------------------- 카드 렌더링 -----------------------------
function renderCard(item, opts) {
  const tab = opts.tab || "";
  const key = registerItem(item, tab, item.url);
  const meta = [];
  if (opts.badge) meta.push(`<span class="badge">${escapeHtml(opts.badge)}</span>`);
  meta.push(escapeHtml(fmtDate(item.published_at)));
  if (item.author) meta.push(escapeHtml(item.author));

  const t = escapeHtml(item.title || "(제목 없음)");
  const titleHtml = item.url
    ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${t}</a>` : t;
  // 요약이 있으면 표시, 없고 이미지도 없으면 '요약 없음', 이미지만 있으면 요약 줄 생략
  const summaryHtml = item.content
    ? `<p class="card-summary">${escapeHtml(item.content)}</p>`
    : (item.image_url ? "" : `<p class="card-summary">요약 없음</p>`);
  const li = document.createElement("li");
  li.className = "card card-news" + readClass(key);
  li.dataset.key = key;
  li.innerHTML = `
    ${scrapBtnHtml(key)}
    <div class="card-main">
      ${newsThumb(item)}
      <div class="card-body">
        <h3 class="card-title">${newBadgeHtml(item.published_at)}${titleHtml}</h3>
        <div class="card-meta">${meta.join(" · ")}</div>
        ${summaryHtml}
        <div class="card-actions">
          ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
          ${copyBtnHtml(item.url)}
        </div>
      </div>
    </div>`;
  return li;
}

function renderList(el, items, opts) {
  const tab = (el.id || "").replace(/^list-/, "");
  renderInfinite(el, items,
    (item) => renderCard(item, { badge: opts.badgeFn ? opts.badgeFn(item) : null, tab }),
    `표시할 데이터가 아직 없어요.`);
}

// ----------------------------- 탭별 검색(똑똑한 키워드) -----------------------------
// 각 탭의 원본(서버에서 받은 전체) 목록과 현재 검색어를 보관 → 검색은 클라이언트에서 즉시 필터.
const TAB_DATA = {};  // tab -> { el, items:[], render:(list)=>void, query:"" }

// 검색어를 토큰으로 분해. 조사(은/는/이/가…)는 떼어내 어간으로도 매칭(자연어 입력 대응).
function searchTokens(q) {
  const JOSA = /(으로|에서|에게|한테|까지|부터|보다|처럼|은|는|이|가|을|를|에|의|도|로|와|과|만|요)$/;
  return (q || "").toLowerCase().split(/\s+/).filter(Boolean).map((t) => {
    const cands = [t];
    const s = t.replace(JOSA, "");
    if (s.length >= 2 && s !== t) cands.push(s);
    return cands;
  });
}
// 검색 대상 텍스트(제목+본문+출처 등)를 공백 제거·소문자화해 부분일치가 잘 되게.
function searchHay(it) {
  return [it.title, it.content, it.author, it.service, it.category, it.channel, it.account]
    .filter(Boolean).join(" ").toLowerCase().replace(/\s+/g, "");
}
// 여러 단어는 AND(모두 포함), 각 단어는 원형/어간 중 하나라도 걸리면 매칭. 원본 순서 유지.
function smartFilter(items, q) {
  const toks = searchTokens(q);
  if (!toks.length) return items;
  return items.filter((it) => {
    const h = searchHay(it);
    return toks.every((cands) => cands.some((c) => h.includes(c)));
  });
}
function setTabData(tab, el, items, render) {
  const prev = TAB_DATA[tab];
  TAB_DATA[tab] = { el, items, render, query: (prev && prev.query) || "" };
  renderTab(tab);
}
function renderTab(tab) {
  const d = TAB_DATA[tab];
  if (!d) return;
  const base = d.prefilter ? d.prefilter(d.items) : d.items;  // 탭 고유 사전필터(예: 뉴스 분류 체크박스)
  const list = d.query ? smartFilter(base, d.query) : base;
  d.count = list.length;
  d.searching = !!d.query;
  if (tab === activeTab()) updateCount(tab);
  if (tab === "event") { d.render(list); return; }  // 행사일정은 자체 렌더(앨범/캘린더·빈상태 포함)
  if (d.query && !list.length) { searchEmpty(d.el, d.query); return; }
  d.render(list);
}

// 현재 노출 게시물 수 표시(검색 중이면 검색 결과 수).
function updateCount(tab) {
  const el = document.getElementById("result-count");
  if (!el) return;
  const d = TAB_DATA[tab];
  if (!d || typeof d.count !== "number") { el.innerHTML = ""; return; }
  const n = d.count.toLocaleString();
  el.innerHTML = d.searching ? `검색 <b>${n}</b>개` : `총 <b>${n}</b>개`;
}
function searchEmpty(el, q) {
  el.innerHTML = `<li class="empty">
    <div class="empty-msg">'${escapeHtml(q)}' 검색 결과가 없어요.</div>
    <button type="button" class="retry-btn" data-clear-search="1">검색 지우기</button>
  </li>`;
}
function clearSearch() {
  const tab = activeTab();
  if (TAB_DATA[tab]) TAB_DATA[tab].query = "";
  const inp = document.getElementById("tab-search");
  if (inp) inp.value = "";
  const cl = document.getElementById("search-clear");
  if (cl) cl.hidden = true;
  renderTab(tab);
}
// 현재 입력값으로 활성 탭을 즉시 검색.
function doSearch() {
  const inp = document.getElementById("tab-search");
  if (!inp) return;
  const tab = activeTab();
  const v = (inp.value || "").trim();
  const clr = document.getElementById("search-clear");
  if (clr) clr.hidden = !inp.value;
  if (TAB_DATA[tab]) { TAB_DATA[tab].query = v; renderTab(tab); }
}
// 검색창 초기화 — 입력(라이브)·엔터·검색버튼·지우기버튼 모두 연결
(function initSearch() {
  const inp = document.getElementById("tab-search");
  const clr = document.getElementById("search-clear");
  const sbtn = document.getElementById("search-btn");
  if (!inp) return;
  let timer = null;
  inp.addEventListener("input", () => {
    if (clr) clr.hidden = !inp.value;
    clearTimeout(timer);
    timer = setTimeout(doSearch, 180);       // 입력 중 라이브 필터
  });
  inp.addEventListener("keydown", (e) => {   // 엔터(물리/키패드) → 즉시 검색
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); clearTimeout(timer); doSearch(); inp.blur(); }
  });
  inp.addEventListener("search", () => { clearTimeout(timer); doSearch(); });  // 모바일 검색키/x
  if (sbtn) sbtn.addEventListener("click", () => { clearTimeout(timer); doSearch(); });  // 클릭·터치
  if (clr) clr.addEventListener("click", () => { clearSearch(); inp.focus(); });
})();
// 탭을 바꾸면 그 탭이 기억하던 검색어를 입력창에 복원
function syncSearchInput() {
  const inp = document.getElementById("tab-search");
  if (!inp) return;
  const d = TAB_DATA[activeTab()];
  inp.value = (d && d.query) || "";
  const cl = document.getElementById("search-clear");
  if (cl) cl.hidden = !inp.value;
}

// ----------------------------- 데이터 로드 -----------------------------
// NC뉴스 분류(체크박스): 재단 / 본사 / 자회사. 자회사는 본사 카테고리 중 자회사 키워드로 구분.
let newsCats = new Set(["재단"]);
const NEWS_SUB_KW = ["엔씨에이아이", "nc ai", "ncai", "엔씨qa", "ncqa", "엔씨ids", "ncids",
  "퍼스트스파크", "빅파이어", "루디우스", "자회사"];
function newsBucket(it) {
  if ((it.category || "") === "재단") return "재단";
  const tc = `${it.title || ""} ${it.content || ""}`.toLowerCase();
  return NEWS_SUB_KW.some((k) => tc.includes(k)) ? "자회사" : "본사";
}
function filterNewsByCat(items) {
  if (newsCats.size >= 3) return items;      // 전부 선택 = 전체
  if (newsCats.size === 0) return [];         // 모두 해제 = 없음
  return items.filter((it) => newsCats.has(newsBucket(it)));
}
async function loadNews() {
  const el = document.getElementById("list-news");
  showLoading(el);
  try {
    const res = await fetch("/api/news?category=all");   // 전체를 받아 분류는 클라이언트에서
    setTabData("news", el, await res.json(), (list) => renderNewsGroups(el, list));
    TAB_DATA.news.prefilter = filterNewsByCat;
    renderTab("news");
  } catch (e) { emptyState(el, "불러오지 못했어요. 잠시 후 다시 시도해 주세요."); }
}
// 체크박스 초기화(전체=모두 토글, 개별 토글, 전체상태 동기화)
(function initNewsCats() {
  const box = document.getElementById("news-cats");
  if (!box) return;
  const cats = ["재단", "본사", "자회사"];
  const allBox = box.querySelector('[data-cat="all"]');
  const catBoxes = cats.map((c) => box.querySelector(`[data-cat="${c}"]`));
  const syncUI = () => {
    catBoxes.forEach((cb) => { cb.checked = newsCats.has(cb.dataset.cat); });
    allBox.checked = cats.every((c) => newsCats.has(c));
  };
  box.addEventListener("change", (e) => {
    const cb = e.target;
    const cat = cb.dataset.cat;
    if (cat === "all") {
      newsCats = cb.checked ? new Set(cats) : new Set();
    } else if (cb.checked) {
      newsCats.add(cat);
    } else {
      newsCats.delete(cat);
    }
    syncUI();
    if (TAB_DATA.news) renderTab("news");
  });
  syncUI();
})();

// 보안뉴스 분류(체크박스): 개인정보 / 해킹·침해 / 취약점 / 정책·규제 / 보안트렌드.
// 서버가 검색어 그룹명을 category로 저장하므로, 저장된 category로 버킷을 정한다.
const SEC_CATS = ["개인정보", "해킹·침해", "취약점", "정책·규제", "개보위·처분", "보안트렌드"];
let secCats = new Set(SEC_CATS);
function secBucket(it) {
  const c = it.category || "";
  return SEC_CATS.includes(c) ? c : "보안트렌드"; // 알 수 없는 값은 트렌드로
}
const IMP_RANK = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
let secSort = "date";  // "date"(최신순) | "importance"(중요도순)
function sortSecurity(items) {
  if (secSort !== "importance") return items;  // 최신순: 서버가 이미 날짜 내림차순
  return items.slice().sort((a, b) => {
    const ra = IMP_RANK[(a.ai_importance || "").toUpperCase()] ?? 9;
    const rb = IMP_RANK[(b.ai_importance || "").toUpperCase()] ?? 9;
    if (ra !== rb) return ra - rb;
    return (b.published_at || "").localeCompare(a.published_at || "");  // 동급이면 최신 우선
  });
}
function filterSecByCat(items) {
  let out;
  if (secCats.size >= SEC_CATS.length) out = items;       // 전부 선택 = 전체
  else if (secCats.size === 0) out = [];                    // 모두 해제 = 없음
  else out = items.filter((it) => secCats.has(secBucket(it)));
  return sortSecurity(out);
}
async function loadSecurity() {
  const el = document.getElementById("list-security");
  showLoading(el);
  try {
    const res = await fetch("/api/secnews?category=all");   // 전체를 받아 분류는 클라이언트에서
    setTabData("security", el, await res.json(), (list) => renderNewsGroups(el, list));
    TAB_DATA.security.prefilter = filterSecByCat;
    renderTab("security");
  } catch (e) { emptyState(el, "불러오지 못했어요. 잠시 후 다시 시도해 주세요."); }
}
(function initSecCats() {
  const box = document.getElementById("sec-cats");
  if (!box) return;
  const allBox = box.querySelector('[data-cat="all"]');
  const catBoxes = SEC_CATS.map((c) => box.querySelector(`[data-cat="${c}"]`));
  const syncUI = () => {
    catBoxes.forEach((cb) => { cb.checked = secCats.has(cb.dataset.cat); });
    allBox.checked = SEC_CATS.every((c) => secCats.has(c));
  };
  box.addEventListener("change", (e) => {
    const cb = e.target;
    const cat = cb.dataset.cat;
    if (cat === "all") secCats = cb.checked ? new Set(SEC_CATS) : new Set();
    else if (cb.checked) secCats.add(cat);
    else secCats.delete(cat);
    syncUI();
    if (TAB_DATA.security) renderTab("security");
  });
  syncUI();
})();
(function initSecSort() {
  const seg = document.getElementById("sec-sort");
  if (!seg) return;
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sort]");
    if (!b) return;
    secSort = b.dataset.sort;
    seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    if (TAB_DATA.security) renderTab("security");
    // 중요도순인데 아직 AI 분석된 기사가 없으면 안내(=최신순과 동일하게 보임)
    if (secSort === "importance") {
      const items = (TAB_DATA.security && TAB_DATA.security.items) || [];
      if (!items.some((it) => it.ai_importance)) toast("먼저 🧠 AI 분석을 실행하면 중요도순이 적용돼요");
    }
  });
})();

// 체크박스(다중선택) 필터 공통: 체크바 DOM에서 분류 목록을 읽어 Set으로 관리하고
// prefilter 함수를 돌려준다. (냥정보·게임정보·재단게시판 등 동적 분류 탭 공용)
function makeCheckFilter(tab, boxId, bucketFn) {
  const box = document.getElementById(boxId);
  const cats = box ? Array.from(box.querySelectorAll("[data-cat]"))
    .map((cb) => cb.dataset.cat).filter((c) => c !== "all") : [];
  let selected = new Set(cats);  // 기본: 전체 선택
  if (box) {
    const allBox = box.querySelector('[data-cat="all"]');
    const catBoxes = cats.map((c) => box.querySelector(`[data-cat="${CSS.escape(c)}"]`));
    const syncUI = () => {
      catBoxes.forEach((cb) => { if (cb) cb.checked = selected.has(cb.dataset.cat); });
      if (allBox) allBox.checked = cats.every((c) => selected.has(c));
    };
    box.addEventListener("change", (e) => {
      const cb = e.target; const cat = cb.dataset.cat;
      if (cat === "all") selected = cb.checked ? new Set(cats) : new Set();
      else if (cb.checked) selected.add(cat); else selected.delete(cat);
      syncUI();
      if (TAB_DATA[tab]) renderTab(tab);
    });
    syncUI();
  }
  return (items) => {
    if (selected.size >= cats.length) return items;   // 전부 선택 = 전체
    if (selected.size === 0) return [];                // 모두 해제 = 없음
    return items.filter((it) => selected.has(bucketFn(it)));
  };
}
const filterCatByCat = makeCheckFilter("cat", "cat-cats", (it) => it.category || "");
const filterGameByCat = makeCheckFilter("game", "game-cats", (it) => it.category || "");
const filterBoardsBySvc = makeCheckFilter("boards", "board-cats", (it) => it.service || "");

async function loadCat() {
  const el = document.getElementById("list-cat");
  showLoading(el);
  try {
    const res = await fetch("/api/catnews?category=all");   // 전체 받아 분류는 클라이언트에서
    setTabData("cat", el, await res.json(), (list) => renderNewsGroups(el, list));
    TAB_DATA.cat.prefilter = filterCatByCat;
    renderTab("cat");
  } catch (e) { emptyState(el, "불러오지 못했어요. 잠시 후 다시 시도해 주세요."); }
}

async function loadGame() {
  const el = document.getElementById("list-game");
  showLoading(el);
  try {
    const res = await fetch("/api/gamenews?category=all");
    setTabData("game", el, await res.json(), (list) => renderNewsGroups(el, list));
    TAB_DATA.game.prefilter = filterGameByCat;
    renderTab("game");
  } catch (e) { emptyState(el, "불러오지 못했어요. 잠시 후 다시 시도해 주세요."); }
}

async function loadBiz() {
  const el = document.getElementById("list-biz");
  showLoading(el);
  try {
    const res = await fetch("/api/biznews");
    setTabData("biz", el, await res.json(), (list) => renderNewsGroups(el, list));
  } catch (e) { emptyState(el, "불러오지 못했어요. 잠시 후 다시 시도해 주세요."); }
}

// 뉴스 카드 썸네일 HTML. 대표 이미지가 있을 때만 표시(없으면 아무것도 안 보임).
// proxy=true(뉴스류)면 언론사 핫링크 차단 우회를 위해 서버 프록시(/api/img)로 불러온다.
function newsThumb(rep, proxy) {
  if (!rep.image_url) return "";
  const src = proxy ? ("/api/img?u=" + encodeURIComponent(rep.image_url)) : rep.image_url;
  return `<div class="card-thumb"><img class="thumb-img" loading="lazy" src="${escapeHtml(src)}" alt=""
    onerror="this.closest('.card-thumb').remove()"></div>`;
}

// 보안뉴스 AI 후처리(태깅·중요도·시사점) → 카드에 얹을 HTML. ai 필드 없으면 "".
const IMP_META = {
  CRITICAL: { ko: "심각", cls: "imp-crit" },
  HIGH: { ko: "높음", cls: "imp-high" },
  MEDIUM: { ko: "보통", cls: "imp-med" },
  LOW: { ko: "낮음", cls: "imp-low" },
};
function impBadgeHtml(rep) {
  const m = IMP_META[(rep.ai_importance || "").toUpperCase()];
  return m ? `<span class="imp-badge ${m.cls}">${m.ko}</span>` : "";
}
function secAiHtml(rep) {
  if (!rep.ai_at) return "";               // 아직 AI 분석 안 된 기사
  const tags = (rep.ai_tags || "").split(",").map((t) => t.trim()).filter(Boolean);
  const tagsHtml = tags.length
    ? `<div class="ai-tags">${tags.map((t) => `<span class="ai-tag">${escapeHtml(t)}</span>`).join("")}</div>` : "";
  let ins = {};
  try { ins = JSON.parse(rep.ai_insight || "{}"); } catch (e) { ins = {}; }
  const facts = [];
  if (ins.org) facts.push(`🏢 ${ins.org}`);
  if (ins.attack) facts.push(`💥 ${ins.attack}`);
  if (ins.cve) facts.push(`🆔 ${ins.cve}`);
  if (ins.scale) facts.push(`📊 ${ins.scale}`);
  if (ins.action) facts.push(`🛠 ${ins.action}`);
  const factsHtml = facts.length
    ? `<div class="ai-facts">${facts.map((f) => `<span class="ai-fact">${escapeHtml(f)}</span>`).join("")}</div>` : "";
  const rows = [];
  if (ins.implication) rows.push(`<div class="ai-row"><b>🛡 시사점</b> ${escapeHtml(ins.implication)}</div>`);
  if (ins.check) rows.push(`<div class="ai-row"><b>✔ 확인</b> ${escapeHtml(ins.check)}</div>`);
  if (ins.prevention) rows.push(`<div class="ai-row"><b>🧯 예방</b> ${escapeHtml(ins.prevention)}</div>`);
  const insightHtml = rows.length ? `<div class="ai-insight">${rows.join("")}</div>` : "";
  return tagsHtml + factsHtml + insightHtml;
}

// 뉴스 그룹 1개 → 카드 노드(아코디언 핸들러 포함)
function newsGroupNode(arr, tab) {
  const rep = arr[0];  // 그룹 내 최신(작성일 내림차순 첫 항목)
  const repLink = rep.source_url || rep.url;  // 원문 보기: 실제 기사 URL 우선
  const key = registerItem(rep, tab, repLink);
  const meta = [escapeHtml(fmtDate(rep.published_at))];
  if (rep.author) meta.push(escapeHtml(rep.author));
  if (arr.length > 1) meta.push(`<span class="badge">${arr.length}개 매체</span>`);

  const t = escapeHtml(rep.title || "(제목 없음)");
  const titleHtml = repLink
    ? `<a href="${escapeHtml(repLink)}" target="_blank" rel="noopener">${t}</a>` : t;
  const li = document.createElement("li");
  li.className = "card card-news" + readClass(key);
  li.dataset.key = key;
  // 뉴스류(뉴스/냥정보/업계동향)는 대표이미지가 언론사 핫링크 차단으로 들쭉날쭉해 표시 제거(텍스트 카드).
  let html = `
    ${scrapBtnHtml(key)}
    <div class="card-main">
      <div class="card-body">
        <h3 class="card-title">${impBadgeHtml(rep)}${newBadgeHtml(rep.published_at)}${titleHtml}</h3>
        <div class="card-meta">${meta.join(" · ")}</div>
        <p class="card-summary">${escapeHtml(rep.content || "요약 없음")}</p>
        ${secAiHtml(rep)}
        <div class="card-actions">
          ${repLink ? `<a href="${escapeHtml(repLink)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
          ${copyBtnHtml(repLink)}
        </div>
      </div>
    </div>`;
  if (arr.length > 1) {
    html += `<button class="accordion-toggle" type="button">같은 기사 ${arr.length}건 매체별 보기 ▾</button>
      <ul class="accordion-body" hidden>` +
      arr.map((a) => {
        const link = a.source_url || a.url;
        return `<li>
          <span class="src-name">${escapeHtml(a.author || "매체 미상")}</span>
          ${link ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener">${escapeHtml(a.title || "원문")} ↗</a>` : escapeHtml(a.title || "")}
          <span class="src-date">${escapeHtml(fmtDate(a.published_at))}</span>
        </li>`;
      }).join("") +
      `</ul>`;
  }
  li.innerHTML = html;
  const btn = li.querySelector(".accordion-toggle");
  if (btn) btn.addEventListener("click", () => {
    const body = btn.nextElementSibling;
    const willOpen = body.hidden;
    body.hidden = !willOpen;
    btn.textContent = btn.textContent.replace(/[▾▴]\s*$/, willOpen ? "▴" : "▾");
  });
  return li;
}

// 같은 기사(여러 매체)를 group_key로 묶어 대표 카드 + 아코디언, 무한 스크롤로 표시
function renderNewsGroups(el, items) {
  const map = new Map();
  items.forEach((it, i) => {
    const k = it.group_key || it.url || ("row" + i);
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(it);
  });
  const tab = (el.id || "").replace(/^list-/, "");
  renderInfinite(el, Array.from(map.values()), (arr) => newsGroupNode(arr, tab),
    `표시할 데이터가 아직 없어요.`);
}

// 커스텀 드롭다운: 현재 선택값 읽기
function ddValue(id) {
  const dd = document.getElementById(id);
  return dd ? (dd.dataset.value || "all") : "all";
}

async function loadBoards() {
  const el = document.getElementById("list-boards");
  showLoading(el);
  try {
    const res = await fetch("/api/boards?service=all");   // 전체 받아 분류는 클라이언트에서
    setTabData("boards", el, await res.json(),
      (list) => renderList(el, list, { badgeFn: (it) => `${it.service} · ${it.category}` }));
    TAB_DATA.boards.prefilter = filterBoardsBySvc;
    renderTab("boards");
  } catch (e) { emptyState(el, "불러오지 못했어요. 잠시 후 다시 시도해 주세요."); }
}

// 재단YT 분류(체크박스): 재단(NC문화재단 채널) / 주요재단(기관명 검색).
let socialCats = new Set(["재단"]);
function socialBucket(it) { return (it.account || "") === "NC문화재단" ? "재단" : "주요재단"; }
function filterSocialByCat(items) {
  if (socialCats.size >= 2) return items;
  if (socialCats.size === 0) return [];
  return items.filter((it) => socialCats.has(socialBucket(it)));
}
async function loadSocial() {
  const el = document.getElementById("list-social");
  showLoading(el);
  try {
    const res = await fetch("/api/social?channel=all");   // 전체 받아 분류는 클라이언트에서
    setTabData("social", el, await res.json(),
      (list) => renderList(el, list, { badgeFn: (it) => `${socialBucket(it) === "재단" ? "재단" : "주요재단"} · ${it.account}` }));
    TAB_DATA.social.prefilter = filterSocialByCat;
    renderTab("social");
  } catch (e) { emptyState(el, "불러오지 못했어요. 잠시 후 다시 시도해 주세요."); }
}
// 재단YT 체크박스(전체=모두 토글, 개별 토글, 전체 동기화)
(function initSocialCats() {
  const box = document.getElementById("social-cats");
  if (!box) return;
  const cats = ["재단", "주요재단"];
  const allBox = box.querySelector('[data-cat="all"]');
  const catBoxes = cats.map((c) => box.querySelector(`[data-cat="${c}"]`));
  const syncUI = () => {
    catBoxes.forEach((cb) => { cb.checked = socialCats.has(cb.dataset.cat); });
    allBox.checked = cats.every((c) => socialCats.has(c));
  };
  box.addEventListener("change", (e) => {
    const cb = e.target;
    const cat = cb.dataset.cat;
    if (cat === "all") socialCats = cb.checked ? new Set(cats) : new Set();
    else if (cb.checked) socialCats.add(cat);
    else socialCats.delete(cat);
    syncUI();
    if (TAB_DATA.social) renderTab("social");
  });
  syncUI();
})();

// ----------------------------- 행사일정(앨범 / 캘린더) -----------------------------
let eventView = "album";                 // album | calendar
let eventCalYM = null;                    // 캘린더가 보는 [year, month(0-11)]
async function loadEvent() {
  const el = document.getElementById("list-event");
  showLoading(el);
  try {
    const res = await fetch("/api/events");
    setTabData("event", el, await res.json(), renderEvents);
  } catch (e) { emptyState(el, "불러오지 못했어요. 잠시 후 다시 시도해 주세요."); }
}
function eventDateBadge(s) {
  if (!s.start_date) return "일정 미정";
  const a = s.start_date, b = s.end_date;
  return (b && b !== a) ? `${a} ~ ${b}` : a;
}
function eventPlace(s) {
  const p = [s.venue, s.region].filter(Boolean);
  return p.length ? p.join(" · ") : "";
}
function eventAlbumCard(s) {
  const key = registerItem({ url: s.url, source_url: s.source_url, title: s.title, published_at: s.published_at, author: s.author, content: s.content }, "event", s.source_url || s.url);
  const link = s.source_url || s.url;
  const t = escapeHtml(s.title || "(제목 없음)");
  const titleHtml = link ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener">${t}</a>` : t;
  const place = eventPlace(s);
  const li = document.createElement("li");
  li.className = "card event-card" + readClass(key);
  li.dataset.key = key;
  li.innerHTML = `
    ${scrapBtnHtml(key)}
    ${s.image_url ? `<div class="card-thumb"><img class="thumb-img" loading="lazy" src="/api/img?u=${encodeURIComponent(s.image_url)}" alt="" onerror="this.closest('.card-thumb').remove()"></div>` : ""}
    <div class="card-body">
      <div class="event-date">📅 ${escapeHtml(eventDateBadge(s))}</div>
      <h3 class="card-title">${newBadgeHtml(s.published_at)}${titleHtml}</h3>
      ${place ? `<div class="event-place">📍 ${escapeHtml(place)}</div>` : ""}
      ${s.content ? `<p class="card-summary">${escapeHtml(s.content)}</p>` : ""}
      <div class="card-actions">${link ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}${copyBtnHtml(link)}</div>
    </div>`;
  return li;
}
function renderEventAlbum(list) {
  const el = document.getElementById("list-event");
  if (!list.length) {
    el.innerHTML = `<li class="empty"><div class="empty-msg">표시할 행사가 아직 없어요.</div>`
      + `<button type="button" class="retry-btn" data-reload="event">↻ 다시 불러오기</button>`
      + `<div class="empty-hint">관리자가 '수집 실행'을 누르면 국내 행사가 모여요.</div></li>`;
    return;
  }
  el.innerHTML = "";
  const frag = document.createDocumentFragment();
  list.forEach((s) => frag.appendChild(eventAlbumCard(s)));
  el.appendChild(frag);
}
const CAL_COLORS = ["#2d6cdf", "#16a34a", "#dc2626", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#4b5563"];
function _mdRange(s) {  // 09.28 ~ 30 형태
  const a = s.start_date, b = s.end_date || s.start_date;
  const md = (d) => d.slice(5).replace("-", ".");
  if (!a) return "일정 미정";
  if (b === a) return md(a);
  return md(a).split(".")[0] === md(b).split(".")[0] ? `${md(a)}~${md(b).split(".")[1]}` : `${md(a)}~${md(b)}`;
}
function renderEventCalendar(list) {
  const cal = document.getElementById("cal-event");
  const dated = list.filter((s) => s.start_date);
  if (!eventCalYM) {
    let base = new Date();
    const up = dated.map((s) => s.start_date).sort();
    const future = up.find((d) => d >= new Date().toISOString().slice(0, 10)) || up[0];
    if (future) { const [y, m] = future.split("-"); base = new Date(+y, +m - 1, 1); }
    eventCalYM = [base.getFullYear(), base.getMonth()];
  }
  const [Y, M] = eventCalYM;
  const startDow = new Date(Y, M, 1).getDay();
  const daysInMonth = new Date(Y, M + 1, 0).getDate();
  const mStart = `${Y}-${String(M + 1).padStart(2, "0")}-01`;
  const mEnd = `${Y}-${String(M + 1).padStart(2, "0")}-${String(daysInMonth).padStart(2, "0")}`;
  // 이번 달과 겹치는 행사 → 시작일순, 색 부여
  const monthEvents = dated.filter((s) => (s.end_date || s.start_date) >= mStart && s.start_date <= mEnd)
    .sort((a, b) => (a.start_date < b.start_date ? -1 : a.start_date > b.start_date ? 1 : 0));
  const colorOf = {};
  monthEvents.forEach((s, i) => { colorOf[s.url] = CAL_COLORS[i % CAL_COLORS.length]; });
  // 날짜별 매핑(막대용)
  const byDay = {};
  monthEvents.forEach((s) => {
    let d = new Date(s.start_date + "T00:00"), end = new Date((s.end_date || s.start_date) + "T00:00"), g = 0;
    while (d <= end && g++ < 400) {
      if (d.getFullYear() === Y && d.getMonth() === M) (byDay[d.getDate()] = byDay[d.getDate()] || []).push(s);
      d.setDate(d.getDate() + 1);
    }
  });
  const todayIso = new Date().toISOString().slice(0, 10);
  const dows = ["일", "월", "화", "수", "목", "금", "토"];
  let cells = "";
  for (let i = 0; i < startDow; i++) cells += `<div class="cal-cell cal-empty"></div>`;
  for (let day = 1; day <= daysInMonth; day++) {
    const iso = `${Y}-${String(M + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const evs = byDay[day] || [];
    const bars = evs.slice(0, 3).map((s) =>
      `<span class="cal-bar" style="background:${colorOf[s.url]}"></span>`).join("");
    const more = evs.length > 3 ? `<span class="cal-more">+${evs.length - 3}</span>` : "";
    cells += `<div class="cal-cell${iso === todayIso ? " today" : ""}${evs.length ? " has" : ""}"><div class="cal-day">${day}</div><div class="cal-bars">${bars}${more}</div></div>`;
  }
  // 아젠다(이번 달 행사 상세 — 날짜·행사명·장소 모두 표시)
  let agenda;
  if (!monthEvents.length) {
    agenda = `<div class="agenda-empty">이 달에는 표시할 행사가 없어요. ${dated.length ? "‹ › 로 다른 달을 보세요." : "<button type=\"button\" class=\"retry-btn\" data-reload=\"event\">↻ 다시 불러오기</button>"}</div>`;
  } else {
    agenda = monthEvents.map((s) => {
      const link = s.source_url || s.url;
      const place = eventPlace(s);
      return `<a class="agenda-item" href="${escapeHtml(link)}" target="_blank" rel="noopener">
        <span class="agenda-date" style="background:${colorOf[s.url]}">${escapeHtml(_mdRange(s))}</span>
        <span class="agenda-main">
          <span class="agenda-title">${newBadgeHtml(s.published_at)}${escapeHtml(s.title || "(제목 없음)")}</span>
          ${place ? `<span class="agenda-place">📍 ${escapeHtml(place)}</span>` : ""}
        </span>
        <span class="agenda-go">↗</span>
      </a>`;
    }).join("");
  }
  const undated = list.length - dated.length;
  cal.innerHTML = `
    <div class="cal-head">
      <button class="cal-nav" id="cal-prev" aria-label="이전 달">‹</button>
      <div class="cal-title">${Y}년 ${M + 1}월 <span class="cal-cnt">${monthEvents.length}건</span></div>
      <button class="cal-nav" id="cal-next" aria-label="다음 달">›</button>
    </div>
    <div class="cal-grid cal-dow">${dows.map((d, i) => `<div class="cal-cell cal-dowc${i === 0 ? " sun" : ""}">${d}</div>`).join("")}</div>
    <div class="cal-grid">${cells}</div>
    <div class="agenda">${agenda}</div>
    ${undated ? `<div class="cal-note">날짜 미상 ${undated}건은 앨범에서 볼 수 있어요.</div>` : ""}`;
  document.getElementById("cal-prev").onclick = () => { eventCalYM = M === 0 ? [Y - 1, 11] : [Y, M - 1]; renderTab("event"); };
  document.getElementById("cal-next").onclick = () => { eventCalYM = M === 11 ? [Y + 1, 0] : [Y, M + 1]; renderTab("event"); };
}
function renderEvents(list) {
  const albumEl = document.getElementById("list-event");
  const calEl = document.getElementById("cal-event");
  const isCal = eventView === "calendar";
  albumEl.hidden = isCal;
  calEl.hidden = !isCal;
  if (isCal) renderEventCalendar(list); else renderEventAlbum(list);
}
// 앨범/캘린더 토글
(function initEventView() {
  const seg = document.getElementById("event-view-toggle");
  if (!seg) return;
  seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    eventView = b.dataset.eview === "calendar" ? "calendar" : "album";
    seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.eview === eventView));
    if (eventView === "calendar") eventCalYM = null;  // 열 때 다가오는 달로 재설정
    renderTab("event");
  }));
})();

// ----------------------------- 상태 확인 -----------------------------
function statusRow(t) {
  // 접속 성공 + 200 + HTML이면 정상, 그 외는 주의/실패
  let cls = "bad", label = "실패";
  if (t.ok && t.status === 200) {
    if (t.looks_html === false) { cls = "warn"; label = "주의"; }
    else { cls = "good"; label = "정상"; }
  }
  // 게시판: 연결은 됐어도 실제 추출 글이 0개면 '주의'(SPA/선택자 불일치로 스크래핑 안 됨)
  if (t.ok && typeof t.items === "number" && t.items === 0) {
    cls = "warn"; label = "글 0개";
  }
  let detail = t.ok
    ? `${t.status} · ${(t.bytes / 1024).toFixed(0)}KB · ${t.sec}s`
    : (t.error || "오류");
  if (t.ok && typeof t.items === "number") detail += ` · 글 ${t.items}개 추출`;
  return `<div class="status-row">
      <span class="dot ${cls}"></span>
      <span class="st-name">${escapeHtml(t.name)}</span>
      <span class="st-badge ${cls}">${escapeHtml(label)}</span>
      <span class="st-detail">${escapeHtml(detail)}</span>
    </div>`;
}

const statusModal = document.getElementById("status-modal");
const statusModalBody = document.getElementById("status-modal-body");

function closeStatus() {
  statusModal.hidden = true;
}
document.getElementById("status-close").addEventListener("click", closeStatus);
statusModal.addEventListener("click", (e) => {
  if (e.target === statusModal) closeStatus(); // 배경 클릭 시 닫기
});

async function runStatus(group) {
  statusModal.hidden = false;
  statusModalBody.innerHTML = '<div class="status-loading">접속 상태 확인 중…</div>';
  try {
    const res = await fetch("/api/diag?group=" + group);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    statusModalBody.innerHTML = data.map(statusRow).join("");
  } catch (e) {
    statusModalBody.innerHTML = `<div class="status-loading" style="color:#dc2626">상태 확인 실패: ${escapeHtml(e.message)}</div>`;
  }
}

document.getElementById("status-cat-btn").addEventListener("click", () => runStatus("cat"));
document.getElementById("status-game-btn").addEventListener("click", () => runStatus("game"));
document.getElementById("status-news-btn").addEventListener("click", () => runStatus("news"));
document.getElementById("status-biz-btn").addEventListener("click", () => runStatus("biz"));
document.getElementById("status-security-btn").addEventListener("click", () => runStatus("security"));
document.getElementById("status-event-btn").addEventListener("click", () => runStatus("event"));
document.getElementById("status-boards-btn").addEventListener("click", () => runStatus("boards"));
document.getElementById("status-social-btn").addEventListener("click", () => runStatus("social"));

// ----------------------------- 수집 실행 -----------------------------
// 수집은 서버 백그라운드 작업으로 돌고, 프론트는 상태를 폴링해 진행률/결과를 보여준다.
// → 휴대폰 화면이 꺼지거나 브라우저가 백그라운드로 가도 서버 수집은 끊기지 않으며,
//   돌아오면(또는 새로고침해도) 진행 상태에 자동으로 다시 붙는다.
const CRAWL_UI = {
  cat: { btn: "collect-cat", msg: "msg-cat", reload: () => loadCat() },
  game: { btn: "collect-game", msg: "msg-game", reload: () => loadGame() },
  news: { btn: "collect-news", msg: "msg-news", reload: () => loadNews() },
  biz: { btn: "collect-biz", msg: "msg-biz", reload: () => loadBiz() },
  security: { btn: "collect-security", msg: "msg-security", reload: () => loadSecurity() },
  event: { btn: "collect-event", msg: "msg-event", reload: () => loadEvent() },
  boards: { btn: "collect-boards", msg: "msg-boards", reload: () => loadBoards() },
  social: { btn: "collect-social", msg: "msg-social", reload: () => loadSocial() },
};
const _pollTimers = {};

function _renderCrawlState(group, st) {
  const ui = CRAWL_UI[group];
  const msgEl = document.getElementById(ui.msg);
  const btn = document.getElementById(ui.btn);
  if (st.running) {
    if (btn) btn.disabled = true;
    msgEl.style.color = "";
    msgEl.innerHTML = '<span class="mini-spin"></span> ' + escapeHtml(st.progress || "수집 중…");
    return false;
  }
  // 완료(또는 미실행)
  if (btn) btn.disabled = false;
  const r = st.result || {};
  if (r.error) {
    msgEl.style.color = "#dc2626";
    msgEl.textContent = "수집 실패: " + r.error;
  } else if (r.new !== undefined || r.crawled !== undefined) {
    msgEl.style.color = "#16a34a";
    const g = r.groups ? ` · 그룹 ${r.groups}개` : "";
    msgEl.textContent = `수집 완료 · 신규 ${r.new ?? 0}건 · 갱신 ${r.updated ?? 0}건${g}`;
  }
  return true;  // 종료됨
}

async function _pollCrawl(group) {
  try {
    const st = await (await fetch("/api/crawl/" + group + "/status")).json();
    const done = _renderCrawlState(group, st);
    if (done && _pollTimers[group]) {
      clearInterval(_pollTimers[group]);
      delete _pollTimers[group];
      CRAWL_UI[group].reload();
      loadMeta();
    }
  } catch (e) { /* 일시적 네트워크 끊김 — 다음 폴링에서 회복 */ }
}

function _startPolling(group) {
  if (_pollTimers[group]) clearInterval(_pollTimers[group]);
  _pollTimers[group] = setInterval(() => _pollCrawl(group), 1500);
  _pollCrawl(group);  // 즉시 1회
}

function runCrawl(btn, group, msgEl, reload) {
  btn.disabled = true;
  msgEl.style.color = "";
  msgEl.innerHTML = '<span class="mini-spin"></span> 수집 시작…';
  // 뉴스 수집 기간은 서버 기본값(최근 2년)을 사용한다(기간 선택 UI 제거).
  const url = "/api/crawl/" + group + "/start";
  fetch(url, { method: "POST" }).catch(() => {});
  _startPolling(group);
}

// 페이지 로드/복귀 시, 서버에서 진행 중인 수집이 있으면 폴링을 자동 재개한다.
async function resumeCrawls() {
  for (const group of Object.keys(CRAWL_UI)) {
    if (_pollTimers[group]) continue;
    try {
      const st = await (await fetch("/api/crawl/" + group + "/status")).json();
      if (st.running) _startPolling(group);
    } catch (e) { /* 무시 */ }
  }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) resumeCrawls(); });

document.getElementById("collect-cat").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "cat", document.getElementById("msg-cat"), loadCat)
);
document.getElementById("collect-game").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "game", document.getElementById("msg-game"), loadGame)
);
document.getElementById("collect-biz").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "biz", document.getElementById("msg-biz"), loadBiz)
);
document.getElementById("collect-security").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "security", document.getElementById("msg-security"), loadSecurity)
);
// (보안뉴스 AI 분석은 수집/재수집 시 서버에서 자동 실행 — 별도 수동 버튼 없음)
document.getElementById("collect-event").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "event", document.getElementById("msg-event"), loadEvent)
);
document.getElementById("collect-news").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "news", document.getElementById("msg-news"), loadNews)
);
document.getElementById("collect-boards").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "boards", document.getElementById("msg-boards"), loadBoards)
);
document.getElementById("collect-social").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "social", document.getElementById("msg-social"), loadSocial)
);
// ----------------------------- DB 비우기(관리자, 현재 탭만) -----------------------------
const TAB_KO = { cat: "냥정보", game: "게임정보", news: "NC뉴스", biz: "업계동향", security: "보안뉴스", event: "행사일정", boards: "재단게시판", social: "재단YT", report: "리포트" };
function activeTab() {
  const t = document.querySelector(".tab.active");
  return (t && t.dataset.tab) || "cat";
}
async function purgeDb() {
  const scope = activeTab();               // 현재 보고 있는 탭만 초기화
  const label = TAB_KO[scope] || scope;
  const days = 1825;                        // 뉴스/냥정보 재수집 기간(5년)
  const btn = document.getElementById("purge-db-btn");
  const msgEl = document.getElementById("msg-" + scope) || document.getElementById("msg-cat");
  btn.disabled = true;
  msgEl.style.color = "";
  msgEl.innerHTML = catSpin(`[${label}] DB 비우는 중…`);
  try {
    const r = await fetch("/api/admin/purge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: scope, recollect: true, days: Number(days) }),
    });
    let j = {};
    try { j = await r.json(); } catch (_) { /* 응답이 JSON이 아닐 때 대비 */ }
    if (!r.ok || !j.ok) throw new Error(j.error || ("서버 오류 " + r.status));
    const total = Object.values(j.deleted || {}).reduce((a, b) => a + (b || 0), 0);
    msgEl.innerHTML = `🗑 [${label}] 삭제 완료(${total}건). 새 수집을 시작했어요.`;
    const el = document.getElementById("list-" + scope);
    if (el) el.innerHTML = "";
    (j.recollect_started || []).forEach((g) => _startPolling(g));
  } catch (e) {
    msgEl.style.color = "#c0392b";
    msgEl.textContent = "DB 비우기 실패: " + e.message;
  } finally {
    btn.disabled = false;
  }
}
document.getElementById("purge-db-btn").addEventListener("click", () => {
  const btn = document.getElementById("purge-db-btn");
  const label = TAB_KO[activeTab()] || activeTab();
  armConfirm(btn, `[${label}] 초기화 확정`, purgeDb);
});

// ----------------------------- 커스텀 드롭다운 -----------------------------
function setupDropdown(id, onChange) {
  const dd = document.getElementById(id);
  if (!dd) return;
  const btn = dd.querySelector(".dropdown-btn");
  const menu = dd.querySelector(".dropdown-menu");
  const label = dd.querySelector(".dropdown-label");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const isOpen = !menu.hidden;
    document.querySelectorAll(".dropdown-menu").forEach((m) => (m.hidden = true));
    menu.hidden = isOpen;
    btn.setAttribute("aria-expanded", String(!isOpen));
  });
  menu.querySelectorAll("li").forEach((li) => {
    li.addEventListener("click", () => {
      dd.dataset.value = li.dataset.value;
      label.textContent = li.textContent.trim();
      menu.querySelectorAll("li").forEach((x) => x.classList.remove("selected"));
      li.classList.add("selected");
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      onChange();
    });
  });
}
// 바깥 클릭 시 모든 드롭다운 닫기
document.addEventListener("click", () =>
  document.querySelectorAll(".dropdown-menu").forEach((m) => (m.hidden = true))
);
// (냥정보/게임정보/재단게시판 분류는 셀렉트박스 → 체크박스 다중선택으로 이관됨)

// ----------------------------- 마지막 수집 일시 -----------------------------
function fmtLast(ts) {
  return ts ? `마지막 수집: ${ts} (서버 기준)` : "아직 수집 기록 없음";
}
async function loadMeta() {
  try {
    const r = await fetch("/api/meta");
    const m = await r.json();
    document.getElementById("last-cat").textContent = fmtLast(m.cat);
    document.getElementById("last-game").textContent = fmtLast(m.game);
    document.getElementById("last-news").textContent = fmtLast(m.news);
    document.getElementById("last-biz").textContent = fmtLast(m.biz);
    document.getElementById("last-security").textContent = fmtLast(m.security);
    document.getElementById("last-event").textContent = fmtLast(m.event);
    document.getElementById("last-boards").textContent = fmtLast(m.boards);
    document.getElementById("last-social").textContent = fmtLast(m.social);
    const badge = document.getElementById("storage-badge");
    if (badge) {
      if (m.db_down) {
        badge.textContent = "⛔ DB 연결 안 됨";
        badge.style.color = "#dc2626";
      } else if (m.storage === "postgres") {
        badge.textContent = "☁ 영구저장";
        badge.style.color = "#16a34a";
      } else {
        badge.textContent = "⚠ 임시저장";
        badge.style.color = "#b25e00";
      }
    }
  } catch (e) {}
}

// ----------------------------- 관리자 로그인 -----------------------------
const loginModal = document.getElementById("login-modal");
const loginErr = document.getElementById("login-err");
let loginRole = "user";   // 'user'(일반) | 'admin'(관리자)
function setLoginRole(role) {
  loginRole = role === "admin" ? "admin" : "user";
  document.querySelectorAll("#login-role button").forEach((b) =>
    b.classList.toggle("active", b.dataset.role === loginRole));
  // 일반=아이디+비번, 관리자=비번만
  const idWrap = document.getElementById("login-id-wrap");
  if (idWrap) idWrap.hidden = (loginRole === "admin");
  const pw = document.getElementById("login-pw");
  if (pw) pw.placeholder = loginRole === "admin" ? "관리자 비밀번호" : "비밀번호";
  loginErr.textContent = "";
}
function openLogin() {
  loginErr.textContent = "";
  document.getElementById("login-id").value = "";
  document.getElementById("login-pw").value = "";
  setLoginRole("user");
  loginModal.hidden = false;
  setTimeout(() => document.getElementById("login-id").focus(), 50);
}
function closeLogin() { loginModal.hidden = true; pendingScrapKey = null; }
document.getElementById("login-btn").addEventListener("click", openLogin);
document.getElementById("login-close").addEventListener("click", closeLogin);
loginModal.addEventListener("click", (e) => { if (e.target === loginModal) closeLogin(); });
document.querySelectorAll("#login-role button").forEach((b) =>
  b.addEventListener("click", () => setLoginRole(b.dataset.role)));

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  loginErr.textContent = "";
  const pw = document.getElementById("login-pw").value;
  const body = loginRole === "admin"
    ? { role: "admin", pw }
    : { role: "user", username: document.getElementById("login-id").value.trim(), pw };
  let res = null;
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    res = await r.json();
  } catch (err) { loginErr.textContent = "로그인 요청 실패. 잠시 후 다시 시도해 주세요."; return; }
  if (res && res.ok) {
    applyAuthUI(res.user, res.admin);
    await loadMyData();
    loginModal.hidden = true;
    toast((res.user === "admin" ? "관리자" : res.user) + " 님, 로그인되었어요");
    if (pendingScrapKey) { const k = pendingScrapKey; pendingScrapKey = null; toggleScrap(k); }
  } else {
    loginErr.textContent = (res && res.error) || "로그인에 실패했습니다.";
  }
});
document.getElementById("logout-btn").addEventListener("click", async () => {
  try { await fetch("/api/logout", { method: "POST" }); } catch (e) { /* 무시 */ }
  applyAuthUI(null, false);
  READ = new Set(); SCRAP = {};
  applyUserStateToDom(); updateScrapBadge();
  const m = document.getElementById("scrap-modal");
  if (m && !m.hidden) closeScraps();
  toast("로그아웃되었어요");
});

// ----------------------------- 개발노트/패치내역 -----------------------------
const notesModal = document.getElementById("notes-modal");
let _notesData = { devnote: "", changelog: "" };

// 아주 가벼운 마크다운 → HTML (제목/굵게/코드/목록/인용/구분선)
function mdToHtml(md) {
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  let html = "", inList = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  // 코드펜스(```) 처리: ```mermaid 는 다이어그램 div, 그 외는 <pre><code>. (다이어그램은 showNotes에서 렌더)
  let fence = null, fenceLang = "", fenceBuf = [];
  const lines = (md || "").split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const fm = raw.match(/^\s*```(\w*)\s*$/);
    if (fence !== null) {
      if (fm) { // 닫힘
        const body = fenceBuf.join("\n");
        if (fenceLang === "mermaid") html += `<div class="mermaid">${esc(body)}</div>`;
        else html += `<pre class="codeblock"><code>${esc(body)}</code></pre>`;
        fence = null; fenceLang = ""; fenceBuf = [];
      } else { fenceBuf.push(raw); }
      continue;
    }
    if (fm) { closeList(); fence = true; fenceLang = fm[1] || ""; fenceBuf = []; continue; }
    const line = raw.replace(/\s+$/, "");
    const t = line.replace(/^\s+/, "");
    if (!t) { closeList(); continue; }
    if (/^#{1,6}\s/.test(t)) { closeList(); const lv = Math.min(t.match(/^#+/)[0].length + 1, 6); html += `<h${lv}>${inline(t.replace(/^#+\s/, ""))}</h${lv}>`; continue; }
    if (/^---+$/.test(t)) { closeList(); html += "<hr>"; continue; }
    if (/^>\s?/.test(t)) { closeList(); html += `<blockquote>${inline(t.replace(/^>\s?/, ""))}</blockquote>`; continue; }
    if (/^([-*]|\d+\.)\s/.test(t)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(t.replace(/^([-*]|\d+\.)\s/, ""))}</li>`; continue; }
    closeList(); html += `<p>${inline(t)}</p>`;
  }
  closeList();
  return html;
}

// 패치내역: 버전별 아코디언(한 줄 요약 → 펼치면 상세). '### v… — 요약' + 다음 줄들=상세, '## 날짜'=구분.
function renderChangelog(md) {
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  let html = "", body = [], open = false;
  const flush = () => {
    if (open) { html += `<div class="cl-body">${esc(body.join(" ")).trim() || "(상세 없음)"}</div></details>`; open = false; body = []; }
  };
  (md || "").split(/\r?\n/).forEach((raw) => {
    const t = raw.trim();
    if (!t) return;
    if (t.startsWith("### ")) {
      flush();
      html += `<details class="cl-item"><summary>${esc(t.slice(4))}</summary>`;
      open = true;
    } else if (t.startsWith("## ")) {
      flush();
      html += `<div class="cl-date">${esc(t.slice(3))}</div>`;
    } else if (t.startsWith("# ")) {
      /* 제목 줄 무시 */
    } else if (open) {
      body.push(t);
    }
  });
  flush();
  return html;
}

// 개발노트의 ```mermaid``` 도식을 그림으로 렌더. mermaid.js는 처음 필요할 때만 CDN에서 로드.
let _mermaidLoad = null;
function _loadMermaid() {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  if (_mermaidLoad) return _mermaidLoad;
  _mermaidLoad = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js";
    s.onload = () => { try { window.mermaid.initialize({ startOnLoad: false, theme: "default" }); } catch (e) {} resolve(window.mermaid); };
    s.onerror = () => reject(new Error("mermaid load fail"));
    document.head.appendChild(s);
  });
  return _mermaidLoad;
}
async function _renderMermaid(container) {
  const nodes = container.querySelectorAll(".mermaid");
  if (!nodes.length) return;
  try {
    const m = await _loadMermaid();
    await m.run({ nodes });
  } catch (e) {
    // 오프라인 등으로 렌더 실패 시: 원본 소스를 코드블록으로라도 보이게 폴백.
    nodes.forEach((n) => { if (!n.querySelector("svg")) { const pre = document.createElement("pre"); pre.className = "codeblock"; pre.textContent = n.textContent; n.replaceWith(pre); } });
  }
}
function showNotes(which) {
  const el = document.getElementById("notes-content");
  if (which === "changelog") {
    el.innerHTML = `<div class="changelog">${renderChangelog(_notesData.changelog || "")}</div>`;
  } else {
    el.innerHTML = mdToHtml(_notesData[which] || "(내용 없음)");
    _renderMermaid(el);
  }
  document.querySelectorAll(".notes-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.notes === which));
}
document.getElementById("notes-btn").addEventListener("click", async () => {
  notesModal.hidden = false;
  document.getElementById("notes-content").textContent = "불러오는 중…";
  try {
    _notesData = await (await fetch("/api/notes")).json();
    showNotes("devnote");
  } catch (e) {
    document.getElementById("notes-content").textContent = "불러오기 실패: " + e.message;
  }
});
document.getElementById("notes-close").addEventListener("click", () => (notesModal.hidden = true));
notesModal.addEventListener("click", (e) => { if (e.target === notesModal) notesModal.hidden = true; });
document.querySelectorAll(".notes-tab").forEach((b) =>
  b.addEventListener("click", () => showNotes(b.dataset.notes)));

// ----------------------------- 나의 스크랩(풀팝업) -----------------------------
function updateScrapBadge() {
  const b = document.getElementById("scrap-badge");
  if (!b) return;
  const n = Object.keys(SCRAP).length;
  if (n) { b.textContent = n > 99 ? "99+" : n; b.hidden = false; } else { b.hidden = true; }
}
function groupName(gid) { const g = GROUPS.find((x) => x.id === gid); return g ? g.name : ""; }
function scrapCount(gid) {
  const vals = Object.values(SCRAP);
  return gid === "all" ? vals.length : vals.filter((s) => (s.groups || []).includes(gid)).length;
}
function groupChipsHtml(s) {
  return (s.groups || []).map((gid) => {
    const nm = groupName(gid); return nm ? `<span class="grp-chip">${escapeHtml(nm)}</span>` : "";
  }).join("");
}
function scrapCardNode(s) {
  const li = document.createElement("li");
  li.className = "card card-news" + readClass(s.key);
  li.dataset.key = s.key;
  const meta = [];
  if (s.tab) meta.push(escapeHtml(TAB_KO[s.tab] || s.tab));
  if (s.date) meta.push(escapeHtml(fmtDate(s.date)));
  if (s.author) meta.push(escapeHtml(s.author));
  const link = s.link || s.url;
  const t = escapeHtml(s.title || "(제목 없음)");
  const titleHtml = link ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener">${t}</a>` : t;
  const checks = GROUPS.length
    ? GROUPS.map((g) => `<label class="grp-check-item"><input type="checkbox" class="grp-check" data-key="${escapeHtml(s.key)}" data-gid="${g.id}"${(s.groups || []).includes(g.id) ? " checked" : ""}> ${escapeHtml(g.name)}</label>`).join("")
    : `<span class="grp-empty">아직 그룹이 없어요.</span>`;
  li.innerHTML = `
    ${scrapBtnHtml(s.key)}
    <div class="card-main"><div class="card-body">
      <h3 class="card-title">${newBadgeHtml(s.date)}${titleHtml}</h3>
      <div class="card-meta">${meta.join(" · ")}</div>
      ${s.content ? `<p class="card-summary">${escapeHtml(s.content)}</p>` : ""}
      <div class="grp-chips">${groupChipsHtml(s)}</div>
      <div class="card-actions">
        ${link ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
        ${copyBtnHtml(link)}
        <button class="grp-assign" type="button">🏷 그룹 지정</button>
      </div>
      <div class="grp-assign-panel">
        <div class="grp-assign-title">이 글을 넣을 그룹</div>
        <div class="grp-check-list">${checks}</div>
        <button class="grp-new" type="button" data-key="${escapeHtml(s.key)}">＋ 새 그룹 만들어 넣기</button>
      </div>
    </div></div>`;
  return li;
}
function renderScrapControls() {
  const c = document.getElementById("scrap-controls");
  if (!c) return;
  const chips = [`<button class="scrap-chip${scrapFilterGroup === "all" ? " active" : ""}" data-gid="all">전체 <b>${scrapCount("all")}</b></button>`];
  GROUPS.forEach((g) => {
    chips.push(`<button class="scrap-chip${scrapFilterGroup === g.id ? " active" : ""}" data-gid="${g.id}">${escapeHtml(g.name)} <b>${scrapCount(g.id)}</b></button>`);
  });
  chips.push(`<button class="scrap-chip-add" type="button" title="새 그룹">＋ 그룹</button>`);
  const manage = scrapFilterGroup !== "all"
    ? `<div class="grp-manage"><button class="grp-rename" type="button" data-gid="${scrapFilterGroup}">✎ 이름변경</button><button class="grp-del" type="button" data-gid="${scrapFilterGroup}">🗑 그룹삭제</button></div>`
    : "";
  c.innerHTML = `
    <div class="scrap-search">
      <input type="search" id="scrap-search" class="search-input" placeholder="스크랩에서 검색…" value="${escapeHtml(scrapQuery)}" autocomplete="off" enterkeyhint="search" />
      <button type="button" class="search-btn" id="scrap-search-btn" aria-label="검색">🔍</button>
    </div>
    <div class="scrap-chips">${chips.join("")}</div>
    ${manage}`;
}
function renderScrapList() {
  const el = document.getElementById("scrap-list");
  let items = Object.values(SCRAP);
  if (scrapFilterGroup !== "all") items = items.filter((s) => (s.groups || []).includes(scrapFilterGroup));
  if (scrapQuery) items = smartFilter(items, scrapQuery);
  items.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const h = document.getElementById("scrap-count-h");
  if (h) h.textContent = `(${items.length})`;
  if (!items.length) {
    const msg = Object.keys(SCRAP).length ? "조건에 맞는 스크랩이 없어요." : "아직 스크랩한 글이 없어요.";
    el.innerHTML = `<li class="empty"><div class="empty-msg">${msg}</div>`
      + `<div class="empty-hint">글 카드의 🔖 아이콘을 눌러 스크랩하세요.</div></li>`;
    return;
  }
  el.innerHTML = "";
  const frag = document.createDocumentFragment();
  items.forEach((s) => frag.appendChild(scrapCardNode(s)));
  el.appendChild(frag);
}
function renderScraps() { renderScrapControls(); renderScrapList(); }
function updateCardGroupChips(key) {
  document.querySelectorAll("#scrap-list .card").forEach((li) => {
    if (li.dataset.key !== key) return;
    const el = li.querySelector(".grp-chips");
    if (el) el.innerHTML = groupChipsHtml(SCRAP[key]);
  });
}
// 그룹 API
async function setMembership(key, groupIds) {
  SCRAP[key].groups = groupIds.slice();
  await api("/api/scrap/groups", { key, groups: groupIds });
}
async function toggleMembership(key, gid, on) {
  const set = new Set(SCRAP[key].groups || []);
  if (on) set.add(gid); else set.delete(gid);
  const arr = Array.from(set);
  try { await setMembership(key, arr); } catch (e) { toast("저장 실패 — 다시 시도해 주세요"); return; }
  renderScrapControls();
  updateCardGroupChips(key);
  if (scrapFilterGroup !== "all") renderScrapList();
}
async function createGroupFlow(assignKey) {
  const name = (prompt("새 그룹 이름을 입력하세요") || "").trim();
  if (!name) return;
  try {
    const r = await api("/api/groups", { op: "add", name });
    GROUPS = r.groups || GROUPS;
    if (assignKey && r.id) { const gs = (SCRAP[assignKey].groups || []).slice(); gs.push(r.id); await setMembership(assignKey, gs); }
    renderScraps(); toast("그룹을 만들었어요");
  } catch (e) { toast("그룹 생성 실패"); }
}
async function renameGroupFlow(gid) {
  const cur = groupName(gid);
  const name = (prompt("그룹 이름 변경", cur) || "").trim();
  if (!name || name === cur) return;
  try { const r = await api("/api/groups", { op: "rename", id: gid, name }); GROUPS = r.groups || GROUPS; renderScraps(); }
  catch (e) { toast("이름변경 실패"); }
}
async function deleteGroupFlow(gid) {
  try {
    const r = await api("/api/groups", { op: "del", id: gid });
    GROUPS = r.groups || [];
    Object.values(SCRAP).forEach((s) => { if (s.groups) s.groups = s.groups.filter((x) => x !== gid); });
    if (scrapFilterGroup === gid) scrapFilterGroup = "all";
    renderScraps(); toast("그룹을 삭제했어요");
  } catch (e) { toast("삭제 실패"); }
}
// 나의 스크랩 팝업 내 상호작용(위임)
document.addEventListener("click", (e) => {
  const chip = e.target.closest(".scrap-chip");
  if (chip) { scrapFilterGroup = chip.dataset.gid; renderScraps(); return; }
  if (e.target.closest(".scrap-chip-add")) { createGroupFlow(null); return; }
  const rn = e.target.closest(".grp-rename"); if (rn) { renameGroupFlow(rn.dataset.gid); return; }
  const dl = e.target.closest(".grp-del"); if (dl) { armConfirm(dl, "삭제 확정", () => deleteGroupFlow(dl.dataset.gid)); return; }
  const ng = e.target.closest(".grp-new"); if (ng) { createGroupFlow(ng.dataset.key); return; }
  const ab = e.target.closest(".grp-assign");
  if (ab) { const p = ab.closest(".card").querySelector(".grp-assign-panel"); if (p) p.classList.toggle("open"); return; }
  if (e.target.closest("#scrap-search-btn")) { const i = document.getElementById("scrap-search"); scrapQuery = (i ? i.value : "").trim(); renderScrapList(); return; }
});
document.addEventListener("change", (e) => {
  const cb = e.target.closest(".grp-check");
  if (cb) toggleMembership(cb.dataset.key, cb.dataset.gid, cb.checked);
});
document.addEventListener("input", (e) => {
  if (e.target && e.target.id === "scrap-search") { scrapQuery = e.target.value.trim(); renderScrapList(); }
});
function openScraps() {
  if (!isLoggedIn()) { toast("로그인하면 스크랩을 볼 수 있어요"); openLogin(); return; }
  scrapFilterGroup = "all"; scrapQuery = "";
  renderScraps();
  document.getElementById("scrap-modal").hidden = false;
  document.body.classList.add("modal-open");
}
function closeScraps() {
  document.getElementById("scrap-modal").hidden = true;
  document.body.classList.remove("modal-open");
}
document.getElementById("scrap-open-btn").addEventListener("click", openScraps);
document.getElementById("scrap-close").addEventListener("click", closeScraps);
updateScrapBadge();

// ----------------------------- 로그(관리자): 수집 / 접속 -----------------------------
const runlogModal = document.getElementById("runlog-modal");
const GRP_KO = { news: "뉴스", boards: "게시판", social: "소셜" };

// User-Agent를 사람이 읽기 쉬운 "기기·OS·브라우저"로 요약
function uaSummary(ua) {
  ua = ua || "";
  let os = "기타";
  if (/iphone|ipad|ipod/i.test(ua)) os = "iOS";
  else if (/android/i.test(ua)) os = "Android";
  else if (/windows/i.test(ua)) os = "Windows";
  else if (/mac os x|macintosh/i.test(ua)) os = "Mac";
  else if (/linux/i.test(ua)) os = "Linux";
  let br = "기타";
  if (/edg\//i.test(ua)) br = "Edge";
  else if (/samsungbrowser/i.test(ua)) br = "삼성인터넷";
  else if (/chrome\//i.test(ua) && !/edg\//i.test(ua)) br = "Chrome";
  else if (/firefox\//i.test(ua)) br = "Firefox";
  else if (/safari/i.test(ua) && !/chrome/i.test(ua)) br = "Safari";
  const dev = /mobile|iphone|android/i.test(ua) ? "📱 모바일" : "💻 PC";
  return `${dev} · ${os} · ${br}`;
}

function renderRunlog(rows) {
  const body = document.getElementById("runlog-body");
  if (!rows || !rows.length) { body.innerHTML = '<div class="empty">아직 수집 기록이 없어요.</div>'; return; }
  body.innerHTML = '<ul class="runlog-list">' + rows.map((r) => {
    const ok = r.status === "성공";
    return `<li class="runlog-row">
      <span class="runlog-badge ${ok ? "ok" : "fail"}">${ok ? "성공" : "실패"}</span>
      <div class="runlog-main">
        <div class="runlog-top">${escapeHtml(GRP_KO[r.grp] || r.grp || "")} · <span class="runlog-time">${escapeHtml(r.ran_at || "")}</span></div>
        <div class="runlog-detail">${escapeHtml(r.detail || "")}</div>
      </div>
    </li>`;
  }).join("") + "</ul>";
}

function renderVisit(rows) {
  const body = document.getElementById("runlog-body");
  if (!rows || !rows.length) { body.innerHTML = '<div class="empty">아직 접속 기록이 없어요.</div>'; return; }
  body.innerHTML = '<ul class="runlog-list">' + rows.map((r) => `
    <li class="runlog-row">
      <div class="runlog-main">
        <div class="runlog-top">${escapeHtml(uaSummary(r.ua))}</div>
        <div class="runlog-detail"><span class="runlog-time">${escapeHtml(r.ts || "")}</span> · IP ${escapeHtml(r.ip || "-")}${r.lang ? " · " + escapeHtml((r.lang || "").split(",")[0]) : ""}</div>
      </div>
    </li>`).join("") + "</ul>";
}

async function loadLog(which) {
  document.querySelectorAll("#runlog-modal .notes-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.log === which));
  const body = document.getElementById("runlog-body");
  body.innerHTML = '<div class="empty">불러오는 중…</div>';
  try {
    if (which === "visit") renderVisit(await (await fetch("/api/visitlog")).json());
    else renderRunlog(await (await fetch("/api/runlog")).json());
  } catch (e) {
    body.innerHTML = '<div class="empty">불러오기 실패: ' + escapeHtml(e.message) + "</div>";
  }
}
document.getElementById("runlog-btn").addEventListener("click", () => {
  runlogModal.hidden = false;
  loadLog("run");
});
document.getElementById("runlog-close").addEventListener("click", () => (runlogModal.hidden = true));
runlogModal.addEventListener("click", (e) => { if (e.target === runlogModal) runlogModal.hidden = true; });
document.querySelectorAll("#runlog-modal .notes-tab").forEach((b) =>
  b.addEventListener("click", () => loadLog(b.dataset.log)));

// ----------------------------- 맨 위로 플로팅 버튼 -----------------------------
const toTop = document.getElementById("to-top");
if (toTop) {
  window.addEventListener("scroll", () => {
    toTop.hidden = window.scrollY < 400;
  }, { passive: true });
  toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
}

// ----------------------------- AI 재단 동향 분석 리포트 -----------------------------
const RP_STATUS_KO = { NEW: "신규", UP: "증가", DOWN: "감소", CONTINUED: "지속", DISAPPEARED: "소멸" };
function rpStatusChip(s) {
  const k = (s || "").toUpperCase();
  return `<span class="rp-chip rp-${k || "NA"}">${RP_STATUS_KO[k] || k || ""}</span>`;
}
function rpEvidence(arr) {
  if (!arr || !arr.length) return "";
  const items = arr.map((e) => {
    const meta = [e.org, e.type === "youtube" ? "YT" : "뉴스", e.date].filter(Boolean).join(" · ");
    const t = escapeHtml(e.title || "(제목 없음)");
    const link = e.url ? `<a href="${escapeHtml(e.url)}" target="_blank" rel="noopener">${t} ↗</a>` : t;
    return `<li>${link}<span class="rp-ev-meta">${escapeHtml(meta)}</span></li>`;
  }).join("");
  return `<details class="rp-ev"><summary>근거 ${arr.length}건</summary><ul>${items}</ul></details>`;
}
function rpList(arr) { return (arr || []).map((x) => `<li>${escapeHtml(x)}</li>`).join(""); }
function rpSection(title, inner) {
  if (!inner) return "";
  return `<section class="rp-section"><h3 class="rp-h">${title}</h3>${inner}</section>`;
}
function renderReport(payload) {
  const el = document.getElementById("report-body");
  const d = payload && payload.data;
  if (!payload || payload.empty || !d || !Object.keys(d).length) {
    el.innerHTML = `<div class="empty"><div class="empty-msg">아직 생성된 리포트가 없어요.</div>`
      + `<div class="empty-hint">관리자가 '🧠 분석 실행'을 누르면 첫 리포트가 만들어져요.</div></div>`;
    return;
  }
  const m = d._meta || {}; const c = m.counts || {}; const b = d.brief || {};
  let h = "";
  // 00 요약(제3자 개괄)
  if (d.summary) h += `<div class="rp-summary">${escapeHtml(d.summary)}</div>`;
  // 01 Executive Brief — 서버가 실제로 아는 카운트로 항상 채운다(LLM 누락 시에도 0/– 방지).
  const num = (v) => (typeof v === "number" ? v.toLocaleString() : (v ?? "–"));
  const ourN = c.recent_our, peerN = c.recent_peers;
  const totalN = (c.our_total != null && c.peer_total != null) ? c.our_total + c.peer_total : null;
  const tiles = [
    ["동종 재단", b.foundations ?? c.peer_orgs],   // 이번 기간 식별된 동종 기관 수
    ["우리 콘텐츠", ourN],                          // NC재단+NC YT (최근 기간)
    ["동종 콘텐츠", peerN],                          // 동종/업계 (최근 기간)
    ["누적 데이터", totalN],                         // 전체 수집 누계
  ].map(([k, v]) => `<div class="rp-tile"><div class="rp-tile-n">${num(v)}</div><div class="rp-tile-k">${k}</div></div>`).join("");
  h += rpSection("01 · Executive Brief",
    `<div class="rp-tiles">${tiles}</div>`
    + (b.highlights && b.highlights.length ? `<ul class="rp-hl">${rpList(b.highlights)}</ul>` : ""));
  // 02 지난 리포트 이후 변화
  if (d.changes_since_last && d.changes_since_last.length) {
    h += rpSection("02 · 지난 리포트 이후 주요 변화", d.changes_since_last.map((x) =>
      `<div class="rp-item">${rpStatusChip(x.status)}<b>${escapeHtml(x.title || "")}</b>`
      + `<div class="rp-detail">${escapeHtml(x.detail || "")}</div>${rpEvidence(x.evidence)}</div>`).join(""));
  }
  // 03 업계 동향
  if (d.industry_trends && d.industry_trends.length) {
    h += rpSection("03 · 업계 주요 동향", d.industry_trends.map((x) =>
      `<div class="rp-item"><b>${escapeHtml(x.area || "")}</b> <span class="rp-dir">${escapeHtml(x.direction || "")}</span>`
      + `<div class="rp-detail">${escapeHtml(x.detail || "")}</div>${rpEvidence(x.evidence)}</div>`).join(""));
  }
  // 04 재단별 움직임
  if (d.foundation_moves && d.foundation_moves.length) {
    h += rpSection("04 · 재단별 주요 움직임", d.foundation_moves.map((f) =>
      `<div class="rp-item"><b>${escapeHtml(f.org || "")}</b>`
      + (f.moves || []).map((mv) => `<div class="rp-move">${escapeHtml(mv.kind || "")} — ${escapeHtml(mv.detail || "")}${rpEvidence(mv.evidence)}</div>`).join("")
      + `</div>`).join(""));
  }
  // 05 Trend
  if (d.trends && d.trends.length) {
    h += rpSection("05 · Trend", d.trends.map((x) =>
      `<div class="rp-item"><b>${escapeHtml(x.topic || "")}</b> <span class="rp-state">${escapeHtml(x.state || "")}</span>`
      + `<div class="rp-detail">${escapeHtml(x.detail || "")}</div>${rpEvidence(x.evidence)}</div>`).join(""));
  }
  // 06 Emerging Signals
  if (d.emerging_signals && d.emerging_signals.length) {
    h += rpSection("06 · Emerging Signals", d.emerging_signals.map((x) =>
      `<div class="rp-item rp-signal"><b>⚡ ${escapeHtml(x.name || "")}</b>`
      + `<div class="rp-detail">${escapeHtml(x.desc || "")}</div>`
      + (x.recent_change ? `<div class="rp-sub">최근 변화: ${escapeHtml(x.recent_change)}</div>` : "")
      + (x.foundations && x.foundations.length ? `<div class="rp-sub">관련 재단: ${escapeHtml(x.foundations.join(", "))}</div>` : "")
      + (x.basis ? `<div class="rp-basis">판단 근거: ${escapeHtml(x.basis)}</div>` : "")
      + rpEvidence(x.evidence) + `</div>`).join(""));
  }
  // 07 우리 재단 Position
  if (d.our_position) {
    const p = d.our_position;
    const col = (t, a) => (a && a.length) ? `<div class="rp-pos"><div class="rp-pos-k">${t}</div><ul>${rpList(a)}</ul></div>` : "";
    h += rpSection("07 · NC문화재단 포지션",
      `<div class="rp-poswrap">${col("상대적으로 활발", p.strong)}${col("업계와 유사", p.similar)}${col("업계↑·NC 확인 적음", p.less)}${col("NC 특화", p.unique)}</div>`
      + (p.recent_change ? `<div class="rp-detail">최근 변화: ${escapeHtml(p.recent_change)}</div>` : "") + rpEvidence(p.evidence));
  }
  // 08 Benchmark
  if (d.benchmarks && d.benchmarks.length) {
    h += rpSection("08 · Benchmark Cases", d.benchmarks.map((x) =>
      `<div class="rp-item"><b>${escapeHtml(x.org || "")} · ${escapeHtml(x.name || "")}</b>`
      + `<div class="rp-detail">${escapeHtml(x.summary || "")}</div>`
      + (x.distinct ? `<div class="rp-sub">다른 점: ${escapeHtml(x.distinct)}</div>` : "")
      + (x.question ? `<div class="rp-q">💬 ${escapeHtml(x.question)}</div>` : "")
      + rpEvidence(x.evidence) + `</div>`).join(""));
  }
  // 09 검토 과제
  if (d.review_tasks && d.review_tasks.length) {
    h += rpSection("09 · 검토 과제", d.review_tasks.map((x) =>
      `<div class="rp-item rp-task">`
      + (x.background ? `<div class="rp-sub">배경: ${escapeHtml(x.background)}</div>` : "")
      + (x.change ? `<div class="rp-sub">변화: ${escapeHtml(x.change)}</div>` : "")
      + (x.basis ? `<div class="rp-sub">근거: ${escapeHtml(x.basis)}</div>` : "")
      + `<div class="rp-q">💬 ${escapeHtml(x.question || "")}</div></div>`).join(""));
  }
  if (d.confidence_note) h += `<div class="rp-note">⚠️ ${escapeHtml(d.confidence_note)}</div>`;
  const meta = `분석 시점 ${escapeHtml(payload.created_at || "")} · ${escapeHtml(payload.period || "")}`
    + (m.model ? ` · ${escapeHtml(m.model)}` : "");
  el.innerHTML = `<div class="rp-topmeta">${meta}</div>` + h;
}
// 월간 보안 리포트(정보보안·개인정보 담당자용) 렌더
function renderSecurityReport(payload) {
  const el = document.getElementById("report-body");
  const d = payload && payload.data;
  if (!payload || payload.empty || !d || !Object.keys(d).length) {
    el.innerHTML = `<div class="empty"><div class="empty-msg">아직 생성된 보안 리포트가 없어요.</div>`
      + `<div class="empty-hint">관리자가 '지난달 분석'을 누르면 첫 월간 보안 리포트가 만들어져요.</div></div>`;
    return;
  }
  const m = d._meta || {}; const c = m.counts || {}; const byImp = c.by_importance || {};
  let h = "";
  if (d.summary) h += `<div class="rp-summary">${escapeHtml(d.summary)}</div>`;
  const tiles = [["전체 기사", c.total], ["심각", byImp.CRITICAL], ["높음", byImp.HIGH], ["CVE 언급", c.cve_mentions]]
    .map(([k, v]) => `<div class="rp-tile"><div class="rp-tile-n">${v ?? "–"}</div><div class="rp-tile-k">${k}</div></div>`).join("");
  h += rpSection("01 · 이번 달 요약", `<div class="rp-tiles">${tiles}</div>`
    + (d.highlights && d.highlights.length ? `<ul class="rp-hl">${rpList(d.highlights)}</ul>` : ""));
  if (d.top_incidents && d.top_incidents.length) {
    h += rpSection("02 · 주요 사고·이슈", d.top_incidents.map((x) => {
      const t = x.url ? `<a href="${escapeHtml(x.url)}" target="_blank" rel="noopener">${escapeHtml(x.title || "")}</a>` : escapeHtml(x.title || "");
      return `<div class="rp-item">${impBadgeHtml({ ai_importance: x.importance })}<b>${t}</b>`
        + (x.category ? ` <span class="rp-dir">${escapeHtml(x.category)}</span>` : "")
        + `<div class="rp-detail">${escapeHtml(x.impact || "")}</div></div>`;
    }).join(""));
  }
  if (d.vulnerabilities && d.vulnerabilities.length) {
    h += rpSection("03 · 주요 취약점", d.vulnerabilities.map((x) =>
      `<div class="rp-item"><b>${escapeHtml(x.name || "")}</b>${x.cve ? ` <span class="ai-fact">🆔 ${escapeHtml(x.cve)}</span>` : ""}`
      + `<div class="rp-detail">${escapeHtml(x.note || "")}</div></div>`).join(""));
  }
  if (d.regulatory && d.regulatory.length) {
    h += rpSection("04 · 개보위·규제기관 처분", d.regulatory.map((x) => {
      const facts = [];
      if (x.org) facts.push(`🏛 ${x.org}`);
      if (x.penalty) facts.push(`💰 ${x.penalty}`);
      if (x.target) facts.push(`🎯 ${x.target}`);
      const factsHtml = facts.length ? `<div class="ai-facts">${facts.map((f) => `<span class="ai-fact">${escapeHtml(f)}</span>`).join("")}</div>` : "";
      return `<div class="rp-item"><b>${escapeHtml(x.title || "")}</b>${factsHtml}`
        + `<div class="rp-detail">${escapeHtml(x.note || "")}</div></div>`;
    }).join(""));
  }
  if (d.trends && d.trends.length) {
    h += rpSection("05 · 보안 트렌드", d.trends.map((x) =>
      `<div class="rp-item"><b>${escapeHtml(x.topic || "")}</b><div class="rp-detail">${escapeHtml(x.note || "")}</div></div>`).join(""));
  }
  if (d.actions && d.actions.length) {
    h += rpSection("06 · 담당자 점검·대응 권고", d.actions.map((x) =>
      `<div class="rp-item rp-task"><b>✔ ${escapeHtml(x.task || "")}</b><div class="rp-detail">${escapeHtml(x.detail || "")}</div></div>`).join(""));
  }
  if (d.confidence_note) h += `<div class="rp-note">⚠️ ${escapeHtml(d.confidence_note)}</div>`;
  const meta = `대상 ${escapeHtml(m.month || "")} · 생성 ${escapeHtml(payload.created_at || "")}`
    + (m.model ? ` · ${escapeHtml(m.model)}` : "");
  el.innerHTML = `<div class="rp-topmeta">${meta}</div>` + h;
}
let reportKind = "foundation";
async function loadReport(id) {
  const el = document.getElementById("report-body");
  if (!el.dataset.loaded) el.innerHTML = `<li class="empty">${catSpin("리포트 불러오는 중…")}</li>`;
  try {
    const getUrl = id ? ("/api/report/get?id=" + id) : ("/api/report/get?kind=" + reportKind);
    const [snaps, rep] = await Promise.all([
      fetch("/api/report/list?kind=" + reportKind).then((r) => r.json()),
      fetch(getUrl).then((r) => r.json()),
    ]);
    const sel = document.getElementById("report-snap");
    if (sel) {
      sel.innerHTML = (snaps || []).map((s) => `<option value="${s.id}">${escapeHtml(s.created_at || "")} (${escapeHtml(s.period || "")})</option>`).join("")
        || `<option>스냅샷 없음</option>`;
      if (rep && rep.id) sel.value = rep.id;
    }
    if (reportKind === "security") renderSecurityReport(rep); else renderReport(rep);
    el.dataset.loaded = "1";
  } catch (e) { el.innerHTML = `<div class="empty"><div class="empty-msg">리포트를 불러오지 못했어요.</div></div>`; }
}
(function initReport() {
  const runBtn = document.getElementById("run-report");
  const sel = document.getElementById("report-snap");
  const msg = document.getElementById("msg-report");
  if (sel) sel.addEventListener("change", () => loadReport(sel.value));
  let timer = null;
  function poll() {
    fetch("/api/report/status?kind=" + reportKind).then((r) => r.json()).then((st) => {
      if (st.running) {
        msg.style.color = ""; msg.innerHTML = '<span class="mini-spin"></span> ' + escapeHtml(st.progress || "분석 중…");
      } else {
        clearInterval(timer); timer = null;
        if (runBtn) runBtn.disabled = false;
        const rs = st.result || {};
        if (rs.error) { msg.style.color = "#dc2626"; msg.textContent = "분석 실패: " + rs.error; }
        else if (rs.ok) { msg.style.color = "#16a34a"; msg.textContent = "분석 완료"; const el = document.getElementById("report-body"); if (el) delete el.dataset.loaded; loadReport(); }
      }
    }).catch(() => {});
  }
  if (runBtn) runBtn.addEventListener("click", () => {
    runBtn.disabled = true; msg.style.color = ""; msg.innerHTML = '<span class="mini-spin"></span> 분석 시작…';
    const url = reportKind === "security" ? "/api/report/run?kind=security" : "/api/report/run?window=90";
    fetch(url, { method: "POST" }).then((r) => r.json()).then(() => {
      if (!timer) timer = setInterval(poll, 2500); poll();
    }).catch(() => { runBtn.disabled = false; msg.textContent = "시작 실패"; });
  });
  // 리포트 종류 토글(재단 동향 / 보안 월간)
  const kindSeg = document.getElementById("rep-kind");
  function applyKindUI() {
    if (runBtn) runBtn.textContent = reportKind === "security" ? "지난달 분석" : "분석 실행";
    const title = document.getElementById("report-title");
    if (title) title.textContent = reportKind === "security" ? "🛡 월간 보안 리포트" : "🔍 AI 재단 동향 리포트";
    const nf = document.getElementById("report-note-foundation");
    const ns = document.getElementById("report-note-security");
    if (nf) nf.hidden = reportKind === "security";
    if (ns) ns.hidden = reportKind !== "security";
  }
  if (kindSeg) kindSeg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-kind]");
    if (!b || b.dataset.kind === reportKind) return;
    reportKind = b.dataset.kind;
    kindSeg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    applyKindUI();
    msg.textContent = "";
    const el = document.getElementById("report-body"); if (el) delete el.dataset.loaded;
    loadReport();
  });
  applyKindUI();
  // 스냅샷 삭제(현재 선택) / 전체 초기화
  async function purgeReport(body) {
    msg.style.color = ""; msg.innerHTML = '<span class="mini-spin"></span> 삭제 중…';
    try {
      const r = await fetch("/api/report/purge", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || ("서버 오류 " + r.status));
      msg.style.color = "#16a34a"; msg.textContent = `🗑 삭제 완료(${j.deleted || 0}건)`;
      const el = document.getElementById("report-body"); if (el) delete el.dataset.loaded;
      loadReport();
    } catch (e) { msg.style.color = "#dc2626"; msg.textContent = "삭제 실패: " + e.message; }
  }
  const delBtn = document.getElementById("report-del");
  if (delBtn) delBtn.addEventListener("click", () => {
    const id = sel && sel.value;
    if (!id || isNaN(Number(id))) { msg.style.color = "#dc2626"; msg.textContent = "삭제할 스냅샷이 없어요."; return; }
    armConfirm(delBtn, "한번 더!", () => purgeReport({ id: Number(id) }));
  });
  const purgeBtn = document.getElementById("report-purge");
  if (purgeBtn) purgeBtn.addEventListener("click", () =>
    armConfirm(purgeBtn, "전체삭제 확정", () => purgeReport({ all: true, kind: reportKind })));

  // 상단 버튼 → 풀팝업 열기/닫기
  const modal = document.getElementById("report-modal");
  const openBtn = document.getElementById("report-open-btn");
  const closeBtn = document.getElementById("report-close");
  if (openBtn && modal) openBtn.addEventListener("click", () => {
    modal.hidden = false; document.body.classList.add("modal-open"); loadReport();
    // 진행 중 분석이 있으면 폴링 재개
    if (!timer) fetch("/api/report/status").then((r) => r.json()).then((st) => { if (st.running && !timer) { timer = setInterval(poll, 2500); poll(); } }).catch(() => {});
  });
  if (closeBtn && modal) closeBtn.addEventListener("click", () => { modal.hidden = true; document.body.classList.remove("modal-open"); });
})();

// ----------------------------- 탭바 가로 스크롤 + 끝 블러(페이드) -----------------------------
(function initTabsFade() {
  const wrap = document.getElementById("tabs-wrap");
  const tabs = document.getElementById("tabs");
  if (!wrap || !tabs) return;
  const update = () => {
    const max = tabs.scrollWidth - tabs.clientWidth;
    wrap.classList.toggle("fade-left", tabs.scrollLeft > 4);
    wrap.classList.toggle("fade-right", tabs.scrollLeft < max - 4);
  };
  tabs.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  setTimeout(update, 0);
})();

// 분류 체크바: 스크롤 가능한 쪽 끝을 페이드(마스크)로 흐리게 → '더 있다'는 걸 명확히 인지.
(function initCheckbarFade() {
  const bars = Array.from(document.querySelectorAll(".checkbar"));
  if (!bars.length) return;
  const upd = (el) => {
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 2) { el.style.maskImage = el.style.webkitMaskImage = ""; return; }  // 스크롤 불필요
    const l = el.scrollLeft > 2, r = el.scrollLeft < max - 2;
    // 페이드 폭을 넓게(48px) + 가장자리는 거의 완전 투명하게 → 스크롤 인지 강화
    const g = `linear-gradient(to right, ${l ? "rgba(0,0,0,0.02)" : "#000"} 0, #000 48px, `
      + `#000 calc(100% - 48px), ${r ? "rgba(0,0,0,0.02)" : "#000"} 100%)`;
    el.style.maskImage = g; el.style.webkitMaskImage = g;
  };
  bars.forEach((el) => { upd(el); el.addEventListener("scroll", () => upd(el), { passive: true }); });
  // 탭 전환으로 숨겨졌다 보일 때(clientWidth 변화) 자동 갱신
  if (window.ResizeObserver) {
    const ro = new ResizeObserver((entries) => entries.forEach((e) => upd(e.target)));
    bars.forEach((el) => ro.observe(el));
  } else {
    window.addEventListener("resize", () => bars.forEach(upd));
  }
})();

// ----------------------------- 초기 로드 -----------------------------
initAuth();
loadMeta();
loadCat();
loadGame();
loadNews();
loadBiz();
loadSecurity();
loadEvent();
loadBoards();
loadSocial();
resumeCrawls();  // 진행 중이던 수집이 있으면 폴링 재개(화면 껐다 켜도 이어짐)
