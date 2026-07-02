"""탭1: 구글 뉴스(RSS) 크롤러.

구글 뉴스 홈(news.google.com)은 Angular SPA라 직접 크롤링이 어렵다. 대신 공식
RSS 검색 피드를 사용한다 — 제목/링크/작성일/언론사/요약이 XML로 깔끔히 제공된다.
  https://news.google.com/rss/search?q=<키워드>&hl=ko&gl=KR&ceid=KR:ko

규칙
- 날짜 제한 없음(전체 기간 수집)
- 재단 키워드(엔씨문화재단/NC문화재단) 포함만 → 본사(엔씨소프트) 단독 기사 제외
- 수집 항목: 제목, 작성일, 작성자(언론사), 본문, 원문 URL
- 중복: 본문(요약) 유사도 (dedup.is_duplicate_news)

한계: 구글 RSS의 링크는 구글 리다이렉트 URL이라 원문 전체 본문 추출이 항상
보장되지 않는다. 원문 추출을 시도하되, 실패 시 RSS 요약(snippet)으로 대체한다.
"""
import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from . import extractor, fetcher

RSS_URL = "https://news.google.com/rss/search"
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
KEYWORDS = ["엔씨문화재단", "NC문화재단"]
# 원문 본문 추출 시도 여부. 구글 링크는 리다이렉트라 대부분 실패하면서 느려지므로
# 기본은 끄고 RSS 요약을 본문으로 쓴다. (속도·안정성 우선)
FETCH_FULL_BODY = True  # 원문 기사로 풀리면 요약 추출 시도(실패 시 RSS 요약 사용)


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


def _decode_google_url(url):
    """구글 뉴스 리다이렉트 링크(/articles/<token>)에서 원문 URL을 복원 시도.

    1) 구형 포맷: 토큰을 base64로 풀면 평문 URL이 들어있다 → 그대로 사용.
    2) 신형 포맷(AU_yqL...): 평문 URL이 없다. 구글 뉴스 batchexecute API에
       (signature/timestamp와 함께) 질의해 원문 URL을 받아온다.
    실패하면 None을 돌려주고, 호출측은 RSS 요약으로 폴백한다.
    """
    m = re.search(r"news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_\-]+)", url or "")
    if not m:
        return None
    token = m.group(1)

    # 1) 구형 포맷: base64 안의 평문 URL
    padded = token + "=" * (-len(token) % 4)
    try:
        text = base64.urlsafe_b64decode(padded).decode("latin-1", "ignore")
        m2 = re.search(r"https?://[^\s\"'<>\\]+", text)
        if m2 and "google.com" not in m2.group(0):
            return m2.group(0)
    except Exception:  # noqa: BLE001
        pass

    # 2) 신형 포맷: batchexecute API로 복원
    return _decode_via_batchexecute(token)


def _decode_via_batchexecute(token):
    """구글 뉴스 신형 기사 토큰을 원문 URL로 복원한다.
    기사 페이지에서 서명(data-n-a-sg)/타임스탬프(data-n-a-ts)를 읽어
    내부 RPC(Fbv4je/garturlreq)를 호출한다."""
    try:
        page = fetcher.get(f"https://news.google.com/rss/articles/{token}")
        div = BeautifulSoup(page.text, "lxml").select_one("c-wiz > div")
        if not div:
            return None
        sig, ts = div.get("data-n-a-sg"), div.get("data-n-a-ts")
        if not (sig and ts):
            return None
        inner = json.dumps([
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
              None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            token, int(ts), sig,
        ])
        f_req = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        resp = fetcher.post(
            BATCH_URL,
            data={"f.req": f_req},
            headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        for line in resp.text.splitlines():
            if "wrb.fr" in line and "garturlres" in line:
                arr = json.loads(line)
                decoded = json.loads(arr[0][2])
                if isinstance(decoded, list) and len(decoded) > 1:
                    return decoded[1]
        return None
    except Exception:  # noqa: BLE001
        return None


def _summary_from_article(entry):
    """구글 링크를 원문으로 복원해 본문에서 요약을 추출한다.
    출처(언론사)는 본문 요약에 넣지 않는다. 복원 실패 시 요약은 비워 둔다."""
    summary = ""
    try:
        real = _decode_google_url(entry.get("url", ""))
        resp = fetcher.get(real or entry["url"])  # requests가 리다이렉트를 따라감
        final = resp.url or ""
        if "news.google." not in final and "consent.google" not in final:
            art = extractor.extract_article(BeautifulSoup(resp.text, "lxml"), final)
            if art.get("content") and len(art["content"]) > 120:
                summary = extractor.summarize(art["content"])
                entry["published_at"] = entry.get("published_at") or art.get("published_at")
            if real:
                entry["url"] = final  # 원문 URL을 알면 원문보기를 그쪽으로
    except Exception:  # noqa: BLE001
        pass
    # 원문 추출에 실패하면(구글 리다이렉트라 대부분 실패) RSS 요약(snippet)을
    # 본문으로 사용한다. 그래야 본문이 비어 키워드 필터에서 탈락하는 일이 없다.
    entry["content"] = summary or entry.get("snippet") or ""
    return entry


def _passes_filters(item):
    # 재단 키워드 포함만 통과(본사 단독 기사 제외). 날짜 제한은 두지 않는다.
    haystack = f"{item.get('title', '')}\n{item.get('content', '')}".lower()
    return any(k.lower() in haystack for k in KEYWORDS)


def crawl(max_workers=5, max_items=15, progress=None):
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
        progress(f"요약 처리 중… ({len(entries)}건)")
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            processed = list(pool.map(_summary_from_article, entries))
        with_body = sum(1 for e in processed if e.get("content"))
        print(f"[google] 본문(요약) 추출 성공 {with_body}/{len(processed)}건", flush=True)
        items = [
            {k: e.get(k) for k in ("title", "published_at", "author", "content", "url")}
            for e in processed if _passes_filters(e)
        ]

    msg = f"구글뉴스 수집 {len(items)}건 / {time.time() - t0:.1f}s"
    print("[google] " + msg, flush=True)
    progress(msg)
    return items
