"""탭1: 네이버 뉴스 크롤러.

엔씨문화재단 / NC문화재단 키워드로 네이버 뉴스를 검색해 기사를 수집한다.

규칙
- 2026-01-01 이후 기사만 수집 (START_DATE)
- 본사(엔씨소프트/엔씨) 관련 기사는 제외 → 재단 키워드가 본문/제목에 있어야 함
- 광고/댓글/배너 등은 본문 영역(#dic_area 등)만 추출해 자연히 배제
- 수집 항목: 제목, 작성일, 작성자(언론사), 본문, 원문 URL
- 중복: 본문 유사도(dedup.is_duplicate_news)로 저장 단계에서 판단

주의: 네이버 검색 결과 HTML 구조는 자주 바뀐다. 선택자는 현재 기준 추정값이며,
로컬 실행 시 실제 응답으로 검증/보정이 필요하다. (이 컨테이너는 네이버 접근 차단)
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from bs4 import BeautifulSoup

from . import fetcher

SEARCH_URL = "https://search.naver.com/search.naver"
KEYWORDS = ["엔씨문화재단", "NC문화재단"]
EXCLUDE_KEYWORDS = ["엔씨소프트", "ncsoft"]  # 본사 관련 글 배제용
START_DATE = datetime(2026, 1, 1)


def _build_params(query, start):
    """네이버 뉴스 검색 파라미터. 기간(2026.01.01~오늘)으로 제한."""
    today = datetime.now()
    ds = START_DATE.strftime("%Y.%m.%d")
    de = today.strftime("%Y.%m.%d")
    nso = f"so:dd,p:from{START_DATE.strftime('%Y%m%d')}to{today.strftime('%Y%m%d')}"
    return {
        "where": "news",
        "query": query,
        "sort": "1",      # 최신순
        "pd": "3",        # 직접입력 기간
        "ds": ds,
        "de": de,
        "nso": nso,
        "start": start,   # 페이지네이션 (1, 11, 21, ...)
    }


def _collect_article_links(query, max_pages=5):
    """검색 결과에서 '네이버뉴스' 기사 링크(n.news.naver.com)를 모은다."""
    links = []
    seen = set()
    for page in range(max_pages):
        start = page * 10 + 1
        try:
            resp = fetcher.get(SEARCH_URL, params=_build_params(query, start))
        except Exception as e:  # noqa: BLE001
            print(f"[naver] 검색 요청 실패 (start={start}): {e}")
            break
        soup = BeautifulSoup(resp.text, "lxml")

        # 네이버뉴스 본문 링크. 구조 변경 대비해 여러 후보를 시도한다.
        candidates = soup.select("a.info") or soup.select("a[href*='n.news.naver.com']")
        page_links = [
            a["href"]
            for a in candidates
            if a.get("href", "").startswith("http")
            and "n.news.naver.com" in a.get("href", "")
        ]
        new_links = [l for l in page_links if l not in seen]
        if not new_links:
            break
        for l in new_links:
            seen.add(l)
            links.append(l)
    return links


def _parse_date(soup):
    """기사 작성일 파싱 → ISO 문자열. 실패 시 None."""
    el = soup.select_one("span.media_end_head_info_datestamp_time")
    if el:
        raw = el.get("data-date-time") or el.get_text(strip=True)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d. %p %I:%M", "%Y.%m.%d. %H:%M"):
            try:
                return datetime.strptime(raw.strip(), fmt).isoformat(timespec="minutes")
            except ValueError:
                continue
        m = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", raw)
        if m:
            y, mo, d = map(int, m.groups())
            return datetime(y, mo, d).isoformat(timespec="minutes")
    return None


def _parse_article(url):
    """기사 1건 파싱. 본문 영역만 추출하므로 광고/댓글/배너는 자연히 제외된다."""
    try:
        resp = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        print(f"[naver] 기사 요청 실패: {url} ({e})")
        return None
    soup = BeautifulSoup(resp.text, "lxml")

    title_el = soup.select_one("#title_area span") or soup.select_one("h2#title_area")
    title = title_el.get_text(strip=True) if title_el else None

    press_el = soup.select_one(".media_end_head_top_logo img")
    author = press_el.get("alt").strip() if press_el and press_el.get("alt") else None
    if not author:
        byline = soup.select_one(".media_end_head_journalist_name")
        author = byline.get_text(strip=True) if byline else None

    body_el = soup.select_one("#dic_area") or soup.select_one("#newsct_article")
    content = None
    if body_el:
        # 본문 내 스크립트/광고성 요소 제거 후 텍스트만
        for junk in body_el.select("script, style, .ad, .link_news, .vod_player_wrap, .end_photo_org"):
            junk.decompose()
        content = body_el.get_text("\n", strip=True)

    published_at = _parse_date(soup)

    if not title or not content:
        return None
    return {
        "title": title,
        "published_at": published_at,
        "author": author,
        "content": content,
        "url": url,
    }


def _passes_filters(item):
    """재단 키워드 포함 + 본사 단독 기사 배제."""
    haystack = f"{item.get('title', '')}\n{item.get('content', '')}".lower()

    # 1) 재단 키워드가 반드시 있어야 한다.
    if not any(k.lower() in haystack for k in KEYWORDS):
        return False

    # 2) 날짜 필터: 2026-01-01 이후만
    pub = item.get("published_at")
    if pub:
        try:
            if datetime.fromisoformat(pub) < START_DATE:
                return False
        except ValueError:
            pass
    return True


def crawl(max_pages=2, max_workers=6):
    """뉴스 수집 실행. 파싱된 기사 리스트를 반환(중복 판단/저장은 호출측에서)."""
    t0 = time.time()
    seen_urls = set()
    links = []
    for kw in KEYWORDS:
        kw_links = _collect_article_links(kw, max_pages=max_pages)
        print(f"[naver] '{kw}' 검색 결과 링크 {len(kw_links)}개", flush=True)
        for url in kw_links:
            if url not in seen_urls:
                seen_urls.add(url)
                links.append(url)

    print(f"[naver] 기사 {len(links)}개 본문 파싱 시작 (병렬 {max_workers})", flush=True)
    items = []
    if links:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for item in pool.map(_parse_article, links):
                if item and _passes_filters(item):
                    items.append(item)

    print(f"[naver] 완료: 수집 {len(items)}건 / 소요 {time.time() - t0:.1f}s", flush=True)
    return items
