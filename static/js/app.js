"use strict";

// 관리자 여부는 서버 세션(로그인)으로 판단. (뷰어는 조회만, 관리자만 상태확인/수집)
function setAdmin(isAdmin) {
  document.body.classList.toggle("is-admin", !!isAdmin);
}
function savedPw() { try { return localStorage.getItem("adminPw") || ""; } catch (e) { return ""; } }
async function tryLogin(pw) {
  try {
    const d = await (await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pw }),
    })).json();
    return !!d.ok;
  } catch (e) { return false; }
}
// 세션이 살아있으면 관리자 유지. (자동 로그인은 하지 않음 — 비번은 입력창 자동채움용으로만 저장)
async function initAdmin() {
  let admin = false;
  try { admin = (await (await fetch("/api/me")).json()).admin; } catch (e) {}
  setAdmin(admin);
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

// ----------------------------- 탭 전환 -----------------------------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("panel-" + tab.dataset.tab).classList.add("active");
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
function showLoading(el) {
  el.innerHTML = `<li class="empty"><span class="mini-spin"></span> 불러오는 중…</li>`;
}

// units 배열을 15개씩 렌더하고, 끝 센티넬이 화면에 들어오면 다음 묶음을 이어붙인다.
function renderInfinite(el, units, makeNode, emptyMsg) {
  el.innerHTML = "";
  if (!units.length) { el.innerHTML = `<li class="empty">${emptyMsg}</li>`; return; }
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
  const meta = [];
  if (opts.badge) meta.push(`<span class="badge">${escapeHtml(opts.badge)}</span>`);
  meta.push(escapeHtml(fmtDate(item.published_at)));
  if (item.author) meta.push(escapeHtml(item.author));

  const t = escapeHtml(item.title || "(제목 없음)");
  const titleHtml = item.url
    ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${t}</a>` : t;
  const li = document.createElement("li");
  li.className = "card";
  li.innerHTML = `
    <h3 class="card-title">${titleHtml}</h3>
    <div class="card-meta">${meta.join(" · ")}</div>
    <p class="card-summary">${escapeHtml(item.content || "요약 없음")}</p>
    <div class="card-actions">
      ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
    </div>`;
  return li;
}

function renderList(el, items, opts) {
  renderInfinite(el, items,
    (item) => renderCard(item, { badge: opts.badgeFn ? opts.badgeFn(item) : null }),
    `수집된 데이터가 없습니다.`);
}

// ----------------------------- 데이터 로드 -----------------------------
async function loadNews() {
  const el = document.getElementById("list-news");
  showLoading(el);
  const category = ddValue("dd-news-category");
  try {
    const res = await fetch("/api/news?category=" + encodeURIComponent(category));
    renderNewsGroups(el, await res.json());
  } catch (e) { el.innerHTML = `<li class="empty">불러오기 실패</li>`; }
}

// 뉴스 카드 썸네일 HTML. 대표 이미지(og:image)가 있을 때만 표시(없으면 아무것도 안 보임).
function newsThumb(rep) {
  if (!rep.image_url) return "";
  return `<div class="card-thumb"><img class="thumb-img" loading="lazy" src="${escapeHtml(rep.image_url)}" alt=""
    onerror="this.closest('.card-thumb').remove()"></div>`;
}

// 뉴스 그룹 1개 → 카드 노드(아코디언 핸들러 포함)
function newsGroupNode(arr) {
  const rep = arr[0];  // 그룹 내 최신(작성일 내림차순 첫 항목)
  const repLink = rep.source_url || rep.url;  // 원문 보기: 실제 기사 URL 우선
  const meta = [escapeHtml(fmtDate(rep.published_at))];
  if (rep.author) meta.push(escapeHtml(rep.author));
  if (arr.length > 1) meta.push(`<span class="badge">${arr.length}개 매체</span>`);

  const t = escapeHtml(rep.title || "(제목 없음)");
  const titleHtml = repLink
    ? `<a href="${escapeHtml(repLink)}" target="_blank" rel="noopener">${t}</a>` : t;
  const li = document.createElement("li");
  li.className = "card card-news";
  let html = `
    <div class="card-main">
      ${newsThumb(rep)}
      <div class="card-body">
        <h3 class="card-title">${titleHtml}</h3>
        <div class="card-meta">${meta.join(" · ")}</div>
        <p class="card-summary">${escapeHtml(rep.content || "요약 없음")}</p>
        <div class="card-actions">
          ${repLink ? `<a href="${escapeHtml(repLink)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
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
  renderInfinite(el, Array.from(map.values()), newsGroupNode,
    `수집된 데이터가 없습니다.<br>"수집 실행"을 눌러주세요.`);
}

// 커스텀 드롭다운: 현재 선택값 읽기
function ddValue(id) {
  const dd = document.getElementById(id);
  return dd ? (dd.dataset.value || "all") : "all";
}

async function loadBoards() {
  const el = document.getElementById("list-boards");
  showLoading(el);
  const service = ddValue("dd-service");
  try {
    const res = await fetch("/api/boards?service=" + encodeURIComponent(service));
    renderList(el, await res.json(), { badgeFn: (it) => `${it.service} · ${it.category}` });
  } catch (e) { el.innerHTML = `<li class="empty">불러오기 실패</li>`; }
}

async function loadSocial() {
  const el = document.getElementById("list-social");
  showLoading(el);
  const channel = ddValue("dd-channel");
  try {
    const res = await fetch("/api/social?channel=" + encodeURIComponent(channel));
    renderList(el, await res.json(), { badgeFn: (it) => `${it.channel} · ${it.account}` });
  } catch (e) { el.innerHTML = `<li class="empty">불러오기 실패</li>`; }
}

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

document.getElementById("status-news-btn").addEventListener("click", () => runStatus("news"));
document.getElementById("status-boards-btn").addEventListener("click", () => runStatus("boards"));
document.getElementById("status-social-btn").addEventListener("click", () => runStatus("social"));

// ----------------------------- 수집 실행 -----------------------------
// 수집은 서버 백그라운드 작업으로 돌고, 프론트는 상태를 폴링해 진행률/결과를 보여준다.
// → 휴대폰 화면이 꺼지거나 브라우저가 백그라운드로 가도 서버 수집은 끊기지 않으며,
//   돌아오면(또는 새로고침해도) 진행 상태에 자동으로 다시 붙는다.
const CRAWL_UI = {
  news: { btn: "collect-news", msg: "msg-news", reload: () => loadNews() },
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

document.getElementById("collect-news").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "news", document.getElementById("msg-news"), loadNews)
);
document.getElementById("collect-boards").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "boards", document.getElementById("msg-boards"), loadBoards)
);
document.getElementById("collect-social").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "social", document.getElementById("msg-social"), loadSocial)
);
// ----------------------------- DB 비우기(관리자) -----------------------------
async function purgeDb() {
  const days = 1825;  // 최근 5년(서버 기본값과 동일)
  if (!confirm(
    "수집한 데이터(뉴스·게시판·소셜)를 모두 삭제합니다.\n" +
    "삭제 후 곧바로 최근 5년치로 새로 수집을 시작합니다.\n\n계속할까요?"
  )) return;
  const btn = document.getElementById("purge-db-btn");
  const msgEl = document.getElementById("msg-news");
  btn.disabled = true;
  msgEl.style.color = "";
  msgEl.innerHTML = '<span class="mini-spin"></span> DB 비우는 중…';
  try {
    const r = await fetch("/api/admin/purge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: "all", recollect: true, days: Number(days) }),
    });
    let j = {};
    try { j = await r.json(); } catch (_) { /* 응답이 JSON이 아닐 때 대비 */ }
    if (!r.ok || !j.ok) throw new Error(j.error || ("서버 오류 " + r.status));
    const d = j.deleted || {};
    msgEl.innerHTML = "🗑 삭제 완료(뉴스 " + (d.news || 0) + "·게시판 " +
      (d.boards || 0) + "·소셜 " + (d.social || 0) + "건). 새 수집을 시작했어요.";
    // 목록 즉시 비우고, 재수집 폴링 시작
    ["news", "boards", "social"].forEach((g) => {
      const el = document.getElementById("list-" + g);
      if (el) el.innerHTML = "";
    });
    (j.recollect_started || []).forEach((g) => _startPolling(g));
  } catch (e) {
    msgEl.style.color = "#c0392b";
    msgEl.textContent = "DB 비우기 실패: " + e.message;
  } finally {
    btn.disabled = false;
  }
}
document.getElementById("purge-db-btn").addEventListener("click", purgeDb);

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
setupDropdown("dd-news-category", loadNews);
setupDropdown("dd-service", loadBoards);
setupDropdown("dd-channel", loadSocial);

// ----------------------------- 마지막 수집 일시 -----------------------------
function fmtLast(ts) {
  return ts ? `마지막 수집: ${ts} (서버 기준)` : "아직 수집 기록 없음";
}
async function loadMeta() {
  try {
    const r = await fetch("/api/meta");
    const m = await r.json();
    document.getElementById("last-news").textContent = fmtLast(m.news);
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
document.getElementById("login-btn").addEventListener("click", () => {
  loginErr.textContent = "";
  document.getElementById("login-pw").value = savedPw();  // 저장된 비번 미리 채움
  loginModal.hidden = false;
});
document.getElementById("login-close").addEventListener("click", () => (loginModal.hidden = true));
loginModal.addEventListener("click", (e) => { if (e.target === loginModal) loginModal.hidden = true; });
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  loginErr.textContent = "";
  const pw = document.getElementById("login-pw").value;
  const ok = await tryLogin(pw);
  if (ok) {
    try { localStorage.setItem("adminPw", pw); } catch (e2) {}  // 비번 저장(다음에 입력창 자동채움용)
    setAdmin(true);
    loginModal.hidden = true;
  } else {
    loginErr.textContent = "비밀번호가 올바르지 않습니다.";
  }
});
document.getElementById("logout-btn").addEventListener("click", async () => {
  try { await fetch("/api/logout", { method: "POST" }); } catch (e) {}
  setAdmin(false);
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
  (md || "").split(/\r?\n/).forEach((raw) => {
    const line = raw.replace(/\s+$/, "");
    const t = line.replace(/^\s+/, "");
    if (!t) { closeList(); return; }
    if (/^#{1,6}\s/.test(t)) { closeList(); const lv = Math.min(t.match(/^#+/)[0].length + 1, 6); html += `<h${lv}>${inline(t.replace(/^#+\s/, ""))}</h${lv}>`; return; }
    if (/^---+$/.test(t)) { closeList(); html += "<hr>"; return; }
    if (/^>\s?/.test(t)) { closeList(); html += `<blockquote>${inline(t.replace(/^>\s?/, ""))}</blockquote>`; return; }
    if (/^([-*]|\d+\.)\s/.test(t)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(t.replace(/^([-*]|\d+\.)\s/, ""))}</li>`; return; }
    closeList(); html += `<p>${inline(t)}</p>`;
  });
  closeList();
  return html;
}

function showNotes(which) {
  document.getElementById("notes-content").innerHTML = mdToHtml(_notesData[which] || "(내용 없음)");
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

// ----------------------------- 초기 로드 -----------------------------
initAdmin();
loadMeta();
loadNews();
loadBoards();
loadSocial();
resumeCrawls();  // 진행 중이던 수집이 있으면 폴링 재개(화면 껐다 켜도 이어짐)
