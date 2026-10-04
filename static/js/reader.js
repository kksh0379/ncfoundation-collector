"use strict";

// Native modal keeps the feed mounted and its scroll position intact.
(() => {
  const dialog = document.getElementById("reader-view");
  const title = document.getElementById("reader-title");
  const meta = document.getElementById("reader-meta");
  const body = document.getElementById("reader-body");
  const status = document.getElementById("reader-status");
  const source = document.getElementById("reader-source");
  const retry = document.getElementById("reader-retry");
  let controller, summaryController, trigger, activeUrl, sequence = 0, fontSize = 18, oldOverflow;

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  }

  async function load(url) {
    if (controller) controller.abort();
    if (summaryController) summaryController.abort();
    controller = new AbortController();
    const currentController = controller;
    const current = ++sequence;
    body.replaceChildren();
    body.setAttribute("aria-busy", "true");
    // 본문 로딩: 텍스트 우측에 조그만 달리는 고양이(app.js의 catRunInline, 없으면 텍스트만)
    if (typeof window.catRunInline === "function") status.innerHTML = window.catRunInline("기사 본문을 불러오고 있어요…");
    else status.textContent = "기사 본문을 불러오고 있어요…";
    retry.hidden = true;
    const timer = setTimeout(() => currentController.abort(), 25000);
    try {
      const response = await fetch("/api/reader?url=" + encodeURIComponent(url), { signal: currentController.signal });
      const data = await response.json();
      if (current !== sequence || !dialog.open) return;
      if (!response.ok) throw new Error(data.error || "본문을 불러오지 못했습니다.");
      title.textContent = data.title;
      meta.textContent = [data.author, data.published_at].filter(Boolean).join(" · ");
      source.href = safeUrl(data.url) || url;
      status.textContent = data.mode === "excerpt" ? data.notice : "AI 요약을 준비하고 있어요…";
      retry.hidden = data.mode !== "excerpt";
      for (const text of data.paragraphs) {
        const p = document.createElement("p");
        p.textContent = text; // Never execute publisher HTML.
        body.append(p);
      }
      loadSummary(url, current);
    } catch (error) {
      if (current !== sequence || !dialog.open) return;
      status.textContent = error.name === "AbortError"
        ? "본문을 불러오는 데 시간이 걸리고 있어요. 다시 시도하거나 원문을 확인해 주세요."
        : (error instanceof SyntaxError || error instanceof TypeError)
          ? "본문을 불러오지 못했습니다. 다시 시도하거나 원문을 확인해 주세요."
          : error.message;
      retry.hidden = false;
    } finally {
      clearTimeout(timer);
      if (current === sequence) body.setAttribute("aria-busy", "false");
    }
  }

  function showSummary(points, notice, highlights, excerpt = false) {
    status.replaceChildren();
    const heading = document.createElement('strong');
    heading.className = 'reader-summary-title';
    heading.textContent = excerpt ? 'AI 요약 · 수집 정보 기준' : 'AI 핵심 요약';
    status.append(heading);
    if (points) {
      const list = document.createElement('ul');
      list.className = 'reader-summary-points';
      for (const [index, point] of points.entries()) {
        const item = document.createElement('li');
        appendSummaryHighlights(item, point, Array.isArray(highlights) ? highlights[index] : null);
        list.append(item);
      }
      status.append(list);
    }
    const note = document.createElement('p');
    note.className = 'reader-summary-note';
    note.textContent = String(notice || '').replace(/([가-힣][.!?]) +(?=[가-힣])/g, '$1\n');
    status.append(note);
  }

  function appendSummaryHighlights(item, point, phrases) {
    const ranges = [];
    let total = 0;
    for (const phrase of Array.isArray(phrases) ? phrases : []) {
      if (typeof phrase !== 'string' || phrase.length < 2 || phrase.length > 45) continue;
      const start = point.indexOf(phrase), end = start + phrase.length;
      if (start < 0 || ranges.length >= 2 || total + phrase.length > point.length / 2 || ranges.some(r => start < r.end && end > r.start)) continue;
      ranges.push({start, end});
      total += phrase.length;
    }
    if (!ranges.length) { item.textContent = point; return; }
    ranges.sort((a,b) => a.start - b.start);
    let offset = 0;
    for (const range of ranges) {
      item.append(document.createTextNode(point.slice(offset, range.start)));
      const key = document.createElement('strong');
      key.className = 'reader-summary-key';
      key.textContent = point.slice(range.start, range.end);
      item.append(key);
      offset = range.end;
    }
    item.append(document.createTextNode(point.slice(offset)));
  }

  async function loadSummary(url, current) {
    summaryController = new AbortController();
    const control = summaryController;
    const isCurrent = () => current === sequence && dialog.open && !control.signal.aborted;
    const timer = setTimeout(() => control.abort(), 60000);
    showSummary(null, '본문에서 핵심만 골라 정리하고 있어요…');
    status.setAttribute('aria-busy', 'true');
    try {
      while (isCurrent()) {
        const response = await fetch('/api/reader-summary?url=' + encodeURIComponent(url), {signal:control.signal});
        const data = await response.json();
        if (!isCurrent()) return;
        if (!response.ok) throw new Error('Summary unavailable');
        if (data.status === 'ready') {
          if (!Array.isArray(data.points) || data.points.length < 1 || data.points.length > 3 || data.points.some(p => typeof p !== 'string' || !p.trim())) throw new Error('Invalid summary');
          showSummary(data.points, data.source_kind === 'excerpt' ? '원문 전체를 확보하지 못해 수집된 기사 정보만 AI로 정리했어요. 전체 내용은 원문 보기에서 확인해 주세요.' : data.partial ? '긴 본문의 일부를 바탕으로 AI가 정리했어요. 전체 내용은 아래 본문에서 확인하세요.' : 'AI가 정리한 요약이에요. 자세한 내용은 아래 본문에서 확인하세요.', data.highlights, data.source_kind === 'excerpt');
          return;
        }
        if (data.status !== 'pending') {
          showSummary(null, typeof data.notice === 'string' ? data.notice : '지금은 요약을 만들지 못했어요.\n본문은 아래에서 읽을 수 있어요.');
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 1500));
      }
      if (current === sequence && dialog.open) showSummary(null, 'AI 요약이 지연되고 있어요. 본문은 아래에서 읽을 수 있어요.');
    } catch (_) {
      if (current === sequence && dialog.open) showSummary(null, 'AI 요약이 지연되거나 연결되지 않았어요. 본문은 아래에서 읽을 수 있어요.');
    } finally {
      clearTimeout(timer);
      if (current === sequence) status.setAttribute('aria-busy', 'false');
    }
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-reader]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = safeUrl(link.href);
    if (!url) { event.preventDefault(); return; }
    event.preventDefault();
    trigger = link;
    activeUrl = url;
    title.textContent = link.closest(".card, .finance-news-item")?.querySelector(".card-title, h3")?.textContent || "본문 읽기";
    meta.textContent = new URL(url).hostname;
    source.href = url;
    oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.showModal();
    dialog.scrollTop = 0;
    load(url);
  });
  document.getElementById("reader-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    ++sequence;
    if (controller) controller.abort();
    if (summaryController) summaryController.abort();
    document.body.style.overflow = oldOverflow || "";
    if (trigger?.isConnected) trigger.focus({ preventScroll: true });
  });
  retry.addEventListener("click", () => load(activeUrl));
  function applyFontSize() {
    body.style.fontSize = fontSize + "px";
    // AI 요약(.reader-status)도 본문과 함께 확대·축소. 기본 14px ↔ 본문 18px의 간격(-4) 유지.
    status.style.fontSize = (fontSize - 4) + "px";
  }
  document.querySelectorAll("[data-reader-size]").forEach((button) => button.addEventListener("click", () => {
    fontSize = Math.max(16, Math.min(24, fontSize + Number(button.dataset.readerSize)));
    applyFontSize();
    document.querySelector('[data-reader-size="-2"]').disabled = fontSize === 16;
    document.querySelector('[data-reader-size="2"]').disabled = fontSize === 24;
  }));

  // 다크(블랙) 테마 토글 — 선택은 브라우저에 기억
  const themeBtn = document.getElementById("reader-theme");
  function applyReaderTheme(dark) {
    dialog.classList.toggle("dark", dark);
    if (themeBtn) { themeBtn.textContent = dark ? "☀️" : "🌙"; themeBtn.setAttribute("aria-label", dark ? "라이트 테마 전환" : "다크 테마 전환"); }
    try { localStorage.setItem("readerDark", dark ? "1" : "0"); } catch (_) {}
  }
  if (themeBtn) themeBtn.addEventListener("click", () => applyReaderTheme(!dialog.classList.contains("dark")));
  try { if (localStorage.getItem("readerDark") === "1") applyReaderTheme(true); } catch (_) {}
})();
