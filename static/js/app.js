"use strict";

const loading = document.getElementById("loading");

function showLoading(on) {
  loading.hidden = !on;
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

// ----------------------------- 수집 실행 -----------------------------
async function runCrawl(btn, url, msgEl, reload) {
  btn.disabled = true;
  showLoading(true);
  msgEl.style.color = "";
  msgEl.textContent = "";

  // 서버가 응답 없이 멈춰도 스피너가 무한정 돌지 않도록 타임아웃을 건다.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 150000); // 150초
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    msgEl.textContent = `수집 ${data.crawled}건 · 신규 ${data.saved}건 저장 · 중복 ${data.duplicates}건 제외`;
    await reload();
  } catch (e) {
    msgEl.style.color = "#dc2626";
    msgEl.textContent =
      e.name === "AbortError"
        ? "시간 초과(150초): 서버 응답이 없습니다. Logs 탭을 확인하세요."
        : "수집 실패: " + e.message;
  } finally {
    clearTimeout(timer);
    btn.disabled = false;
    showLoading(false);
  }
}

document.getElementById("collect-news").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "/api/crawl/news", document.getElementById("msg-news"), loadNews)
);
document.getElementById("collect-boards").addEventListener("click", (e) =>
  runCrawl(e.currentTarget, "/api/crawl/boards", document.getElementById("msg-boards"), loadBoards)
);
document.getElementById("filter-service").addEventListener("change", loadBoards);

// ----------------------------- 초기 로드 -----------------------------
loadNews();
loadBoards();
