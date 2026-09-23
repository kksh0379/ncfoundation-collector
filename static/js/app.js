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
  renderNewsGroups(document.getElementById("list-news"), await res.json());
}

// 같은 기사(여러 매체 배포)를 group_key로 묶어 대표 카드 + 아코디언으로 표시
function renderNewsGroups(el, items) {
  el.innerHTML = "";
  if (!items.length) {
    el.innerHTML = `<li class="empty">수집된 데이터가 없습니다.<br>"수집 실행"을 눌러주세요.</li>`;
    return;
  }
  const groups = new Map();
  items.forEach((it, i) => {
    const k = it.group_key || it.url || ("row" + i);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(it);
  });
  groups.forEach((arr) => {
    const rep = arr[0];  // 그룹 내 최신(작성일 내림차순 정렬 기준 첫 항목)
    const meta = [escapeHtml(fmtDate(rep.published_at))];
    if (rep.author) meta.push(escapeHtml(rep.author));
    if (arr.length > 1) meta.push(`<span class="badge">${arr.length}개 매체</span>`);

    const li = document.createElement("li");
    li.className = "card";
    let html = `
      <h3 class="card-title">${escapeHtml(rep.title || "(제목 없음)")}</h3>
      <div class="card-meta">${meta.join(" · ")}</div>
      <p class="card-summary">${escapeHtml(rep.content || "요약 없음")}</p>
      <div class="card-actions">
        ${rep.url ? `<a href="${escapeHtml(rep.url)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
      </div>`;
    if (arr.length > 1) {
      html += `<button class="accordion-toggle" type="button">같은 기사 ${arr.length}건 매체별 보기 ▾</button>
        <ul class="accordion-body" hidden>` +
        arr.map((a) => `<li>
            <span class="src-name">${escapeHtml(a.author || "매체 미상")}</span>
            ${a.url ? `<a href="${escapeHtml(a.url)}" target="_blank" rel="noopener">${escapeHtml(a.title || "원문")} ↗</a>` : escapeHtml(a.title || "")}
            <span class="src-date">${escapeHtml(fmtDate(a.published_at))}</span>
          </li>`).join("") +
        `</ul>`;
    }
    li.innerHTML = html;
    el.appendChild(li);
  });
  el.querySelectorAll(".accordion-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const body = btn.nextElementSibling;
      const willOpen = body.hidden;
      body.hidden = !willOpen;
      btn.textContent = btn.textContent.replace(/[▾▴]\s*$/, willOpen ? "▴" : "▾");
    });
  });
}

// 커스텀 드롭다운: 현재 선택값 읽기
function ddValue(id) {
  const dd = document.getElementById(id);
  return dd ? (dd.dataset.value || "all") : "all";
}

async function loadBoards() {
  const service = ddValue("dd-service");
  const res = await fetch("/api/boards?service=" + encodeURIComponent(service));
  const items = await res.json();
  renderList(document.getElementById("list-boards"), items, {
    badgeFn: (it) => `${it.service} · ${it.category}`,
  });
}

async function loadSocial() {
  const channel = ddValue("dd-channel");
  const res = await fetch("/api/social?channel=" + encodeURIComponent(channel));
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
// 수집은 SSE 스트리밍: 서버가 단계별 진행상황을 흘려보내고, 화면에 실시간 표시한다.
function runCrawl(btn, group, msgEl, reload) {
  btn.disabled = true;
  msgEl.style.color = "";
  msgEl.innerHTML = '<span class="mini-spin"></span> 수집 시작…';
  let finished = false;

  const es = new EventSource("/api/crawl/" + group + "/stream");
  es.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch (_) { return; }
    if (d.type === "progress") {
      msgEl.style.color = "";
      msgEl.innerHTML = '<span class="mini-spin"></span> ' + escapeHtml(d.msg);
    } else if (d.type === "done") {
      finished = true;
      es.close();
      const r = d.result || {};
      if (r.error) {
        msgEl.style.color = "#dc2626";
        msgEl.textContent = "수집 실패: " + r.error;
      } else {
        msgEl.style.color = "#16a34a";
        const grpTxt = r.groups ? ` · 그룹 ${r.groups}개` : "";
        msgEl.textContent = `수집 완료 · 신규 ${r.new ?? 0}건 · 갱신 ${r.updated ?? 0}건${grpTxt}`;
      }
      reload();
      loadMeta();
      btn.disabled = false;
    }
  };
  es.onerror = () => {
    if (finished) return;      // 정상 종료 후의 close는 무시
    es.close();
    btn.disabled = false;
    msgEl.style.color = "#dc2626";
    msgEl.textContent = "수집 연결이 끊겼어요. 잠시 후 다시 시도해 주세요.";
  };
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
  } catch (e) {}
}

// ----------------------------- 초기 로드 -----------------------------
loadMeta();
loadNews();
loadBoards();
loadSocial();
