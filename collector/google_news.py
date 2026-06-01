"""탭1: 구글 뉴스(RSS) 크롤러.

구글 뉴스 홈(news.google.com)은 Angular SPA라 직접 크롤링이 어렵다. 대신 공식
RSS 검색 피드를 사용한다 — 제목/링크/작성일/언론사/요약이 XML로 깔끔히 제공된다.
  https://news.google.com/rss/search?q=<키워드>&hl=ko&gl=KR&ceid=KR:ko

규칙
- 2026-01-01 이후 기사만 (START_DATE)
- 재단 키워드(엔씨문화재단/NC문화재단) 포함만 → 본사(엔씨소프트) 단독 기사 제외
- 수집 항목: 제목, 작성일, 작성자(언론사), 본문, 원문 URL
- 중복: 본문(요약) 유사도 (dedup.is_duplicate_news)

한계: 구글 RSS의 링크는 구글 리다이렉트 URL이라 원문 전체 본문 추출이 항상
보장되지 않는다. 원문 추출을 시도하되, 실패 시 RSS 요약(snippet)으로 대체한다.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from . import extractor, fetcher

RSS_URL = "https://news.google.com/rss/search"
KEYWORDS = ["엔씨문화재단", "NC문화재단"]
START_DATE = datetime(2026, 1, 1)
# 원문 본문 추출 시도 여부. 구글 링크는 리다이렉트라 대부분 실패하면서 느려지므로
# 기본은 끄고 RSS 요약을 본문으로 쓴다. (속도·안정성 우선)
FETCH_FULL_BODY = False


def _feed_params(query):
    return {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}


def _parse_pubdate(text):
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).replace(tzinfo=None).isoformat(timespec="minutes")
    except (TypeError, ValueError):
        return None


def _clean_title(raw):
    # 구글 RSS 제목은 보통 "제목 - 언론사" 형식
    if raw and " - " in raw:
        return raw.rsplit(" - ", 1)[0].strip()
    return (raw or "").strip()


def _snippet(description_html):
    if not description_html:
        return ""
    return BeautifulSoup(description_html, "lxml").get_text(" ", strip=True)


def _collect_items(query):
    try:
        resp = fetcher.get(RSS_URL, params=_feed_params(query))
    except Exception as e:  # noqa: BLE001
        print(f"[google] RSS 요청 실패 ({query}): {e}", flush=True)
        return []
    soup = BeautifulSoup(resp.content, "xml")
    out = []
    for it in soup.find_all("item"):
        link = it.find("link")
        link = link.text if link else None
        if not link:
            continue
        source = it.find("source")
        title = it.find("title")
        pub = it.find("pubDate")
        desc = it.find("description")
        out.append({
            "title": _clean_title(title.text if title else None),
            "url": link,
            "published_at": _parse_pubdate(pub.text if pub else None),
            "author": source.text.strip() if source else None,
            "snippet": _snippet(desc.text if desc else None),
        })
    return out


def _fetch_body(entry):
    """원문 본문 추출 시도. 실패하면 RSS 요약을 본문으로 사용."""
    content = entry.get("snippet") or ""
    if FETCH_FULL_BODY and entry.get("url"):
        try:
            resp = fetcher.get(entry["url"])
            art = extractor.extract_article(BeautifulSoup(resp.text, "lxml"), resp.url)
            if art.get("content") and len(art["content"]) > 150:
                content = art["content"]
            entry["published_at"] = entry.get("published_at") or art.get("published_at")
        except Exception:  # noqa: BLE001
            pass
    entry["content"] = content
    return entry


def _passes_filters(item):
    haystack = f"{item.get('title', '')}\n{item.get('content', '')}".lower()
    if not any(k.lower() in haystack for k in KEYWORDS):
        return False
    pub = item.get("published_at")
    if pub:
        try:
            if datetime.fromisoformat(pub.replace(" ", "T")) < START_DATE:
                return False
        except ValueError:
            pass
    return True


def crawl(max_workers=6, max_items=40, progress=None):
    """뉴스 수집 실행. 파싱된 기사 리스트 반환(중복 판단/저장은 호출측).
    progress(msg): 진행상황 콜백(선택).
    """
    progress = progress or (lambda m: None)
    t0 = time.time()
    seen, entries = set(), []
    for kw in KEYWORDS:
        rows = _collect_items(kw)
        msg = f"구글뉴스 '{kw}' RSS 항목 {len(rows)}개"
        print("[google] " + msg, flush=True)
        progress(msg)
        for e in rows:
            if e["url"] not in seen:
                seen.add(e["url"])
                entries.append(e)
    entries = entries[:max_items]

    items = []
    if entries:
        if FETCH_FULL_BODY:
            progress(f"원문 본문 추출 중… ({len(entries)}건)")
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                processed = list(pool.map(_fetch_body, entries))
        else:
            for e in entries:
                e["content"] = e.get("snippet") or ""
            processed = entries
        items = [
            {k: e.get(k) for k in ("title", "published_at", "author", "content", "url")}
            for e in processed if _passes_filters(e)
        ]

    msg = f"구글뉴스 수집 {len(items)}건 / {time.time() - t0:.1f}s"
    print("[google] " + msg, flush=True)
    progress(msg)
    return items
