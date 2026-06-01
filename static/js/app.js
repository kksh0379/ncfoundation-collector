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
    <div class="card-body">${escapeHtml(item.content || "본문 없음")}</div>
    <div class="card-actions">
      <button class="toggle">본문 보기</button>
      ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
    </div>`;
  const body = li.querySelector(".card-body");
  const toggle = li.querySelector(".toggle");
  toggle.addEventListener("click", () => {
    li.classList.toggle("open");
    toggle.textContent = li.classList.contains("open") ? "접기" : "본문 보기";
  });
  return li;
}

function renderList(el, items, opts) {
  el.innerHTML = "";
  if (!items.length) {
    el.innerHTML = `<li class="empty">수집된 데이터가 없습니다.<br>"수집 실행"을 눌러주세요.</li>`;
    return;
  }
  items.forEach((item) => el.appendChild(renderCard(item, {
    badge: opts.badgeKey ? item[opts.badgeKey] : null,
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
  renderList(document.getElementById("list-boards"), items, { badgeKey: "category" });
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

async function runStatus(btn, group, panel) {
  btn.disabled = true;
  panel.hidden = false;
  panel.innerHTML = '<div class="status-loading">접속 상태 확인 중…</div>';
  try {
    const res = await fetch("/api/diag?group=" + group);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    panel.innerHTML = data.map(statusRow).join("");
  } catch (e) {
    panel.innerHTML = `<div class="status-loading" style="color:#dc2626">상태 확인 실패: ${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("status-news-btn").addEventListener("click", (e) =>
  runStatus(e.currentTarget, "news", document.getElementById("status-news"))
);
document.getElementById("status-boards-btn").addEventListener("click", (e) =>
  runStatus(e.currentTarget, "boards", document.getElementById("status-boards"))
);

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
      msgEl.textContent = `수집 ${d.crawled}건 · 신규 ${d.saved}건 저장 · 중복 ${d.duplicates}건 제외`;
    }
    await reload();
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
document.getElementById("filter-service").addEventListener("change", loadBoards);

// ----------------------------- 초기 로드 -----------------------------
loadNews();
loadBoards();
