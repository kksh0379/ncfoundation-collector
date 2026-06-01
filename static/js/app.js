"use strict";

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

// ----------------------------- 카드 렌더링 -----------------------------
function renderCard(item, opts) {
  const meta = [];
  if (opts.badge) meta.push(`<span class="badge">${escapeHtml(opts.badge)}</span>`);
  meta.push(escapeHtml(fmtDate(item.published_at)));
  if (item.author) meta.push(escapeHtml(item.author));

  const li = document.createElement("li");
  li.className = "card";
  li.innerHTML = `
    <h3 class="card-title">${escapeHtml(item.title || "(제목 없음)")}</h3>
    <div class="card-meta">${meta.join(" · ")}</div>
    <p class="card-summary">${escapeHtml(item.content || "요약 없음")}</p>
    <div class="card-actions">
      ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
    </div>`;
  return li;
}

function renderList(el, items, opts) {
  el.innerHTML = "";
  if (!items.length) {
    el.innerHTML = `<li class="empty">수집된 데이터가 없습니다.<br>"수집 실행"을 눌러주세요.</li>`;
    return;
  }
  items.forEach((item) => el.appendChild(renderCard(item, {
    badge: opts.badgeFn ? opts.badgeFn(item) : null,
  })));
}

// ----------------------------- 데이터 로드 -----------------------------
async function loadNews() {
  const res = await fetch("/api/news");
  renderList(document.getElementById("list-news"), await res.json(), {});
}

async function loadBoards() {
  const service = document.getElementById("filter-service").value;
  const res = await fetch("/api/boards?service=" + encodeURIComponent(service));
  const items = await res.json();
  renderList(document.getElementById("list-boards"), items, {
    badgeFn: (it) => `${it.service} · ${it.category}`,
  });
}

async function loadSocial() {
  const res = await fetch("/api/social");
  const items = await res.json();
  renderList(document.getElementById("list-social"), items, {
    badgeFn: (it) => `${it.channel} · ${it.account}`,
  });
}

// ----------------------------- 상태 확인 -----------------------------
function statusRow(t) {
  // 접속 성공 + 200 + HTML이면 정상, 그 외는 주의/실패
  let cls = "bad", label = "실패";
  if (t.ok && t.status === 200) {
    if (t.looks_html === false) { cls = "warn"; label = "주의"; }
    else { cls = "good"; label = "정상"; }
  }
  const detail = t.ok
    ? `${t.status} · ${(t.bytes / 1024).toFixed(0)}KB · ${t.sec}s`
    : (t.error || "오류");
  return `<div class="status-row">
      <span class="dot ${cls}"></span>
      <span class="st-name">${escapeHtml(t.name)}</span>
      <span class="st-badge ${cls}">${label}</span>
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
// 수집은 동기 방식: 요청 한 번으로 끝까지 처리하고 결과를 받는다.
async function runCrawl(btn, group, msgEl, reload) {
  btn.disabled = true;
  msgEl.style.color = "";
  msgEl.innerHTML = '<span class="mini-spin"></span> 수집 중… (최대 1~2분 걸릴 수 있어요)';
  try {
    const res = await fetch("/api/crawl/" + group, { method: "POST" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    if (d.error) {
      msgEl.style.color = "#dc2626";
      msgEl.textContent = `수집 실패: ${d.error}`;
    } else {
      msgEl.style.color = "#16a34a";
      const dupTxt = d.duplicates ? ` · 중복 ${d.duplicates}건 제외` : "";
      msgEl.textContent = `수집 ${d.crawled}건 · 신규 ${d.new ?? 0}건 · 갱신 ${d.updated ?? 0}건${dupTxt}`;
    }
    await reload();
    loadMeta();  // 마지막 수집 일시 갱신
  } catch (e) {
    msgEl.style.color = "#dc2626";
    msgEl.textContent = "수집 실패: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("collect-news").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "news", document.getElementById("msg-news"), loadNews)
);
document.getElementById("collect-boards").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "boards", document.getElementById("msg-boards"), loadBoards)
);
document.getElementById("collect-social").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "social", document.getElementById("msg-social"), loadSocial)
);
document.getElementById("filter-service").addEventListener("change", loadBoards);

// ----------------------------- 관리자 로그인 -----------------------------
const loginModal = document.getElementById("login-modal");
const loginErr = document.getElementById("login-err");

function setAdmin(isAdmin) {
  document.body.classList.toggle("is-admin", !!isAdmin);
}

async function checkMe() {
  try {
    const r = await fetch("/api/me");
    const d = await r.json();
    setAdmin(d.admin);
  } catch (e) { setAdmin(false); }
}

document.getElementById("login-btn").addEventListener("click", () => {
  loginErr.textContent = "";
  document.getElementById("login-id").value = "";
  document.getElementById("login-pw").value = "";
  loginModal.hidden = false;
});
document.getElementById("login-close").addEventListener("click", () => (loginModal.hidden = true));
loginModal.addEventListener("click", (e) => { if (e.target === loginModal) loginModal.hidden = true; });

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  loginErr.textContent = "";
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: document.getElementById("login-id").value,
        pw: document.getElementById("login-pw").value,
      }),
    });
    const d = await res.json();
    if (res.ok && d.ok) {
      setAdmin(true);
      loginModal.hidden = true;
    } else {
      loginErr.textContent = d.error || "로그인 실패";
    }
  } catch (err) {
    loginErr.textContent = "로그인 요청 실패: " + err.message;
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  try { await fetch("/api/logout", { method: "POST" }); } catch (e) {}
  setAdmin(false);
});

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
  } catch (e) {}
}

// ----------------------------- 초기 로드 -----------------------------
checkMe();
loadMeta();
loadNews();
loadBoards();
loadSocial();
