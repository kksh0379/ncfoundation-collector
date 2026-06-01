"""탭2: 재단 서비스 게시판 크롤러.

대상 게시판
- 나의AAC    : 소식, 커뮤니티        (서버렌더)
- 프로젝토리   : 공지, 이야기, 갤러리   (서버렌더)
- FAIR AI     : 공지사항, 인사이트      (Nuxt SPA → 정적 HTML에 목록 없음)
- 대표 홈페이지 : 재단소식             (React SPA → 정적 HTML에 목록 없음)

규칙
- 2026-01-01 이후 글만 수집 (START_DATE)
- 수집 항목: 제목, 작성일, 작성자, 본문, 원문 URL
- 중복: 제목 기반 (dedup.is_duplicate_title) — 저장 단계에서 판단
- 수동 실행

선택자는 /api/inspect 로 확인한 실제 구조 기준이다. SPA 사이트(FAIR AI, 대표홈페이지)는
정적 HTML에 목록이 없어 현재 방식으로는 수집되지 않으며, 추후 사이트 내부 API 또는
헤드리스 브라우저(Playwright) 도입이 필요하다(spa=True로 표시).
"""
import time
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import extractor, fetcher

START_DATE = datetime(2026, 1, 1)

SOURCES = [
    {
        "service": "나의AAC", "category": "소식",
        "list_url": "https://www.myaac.or.kr/info/announcement.do",
        "base_url": "https://www.myaac.or.kr",
        "item_link_sel": "a.notice_title_wrap, a.board_title_wrap",
        "title_sel": "b.notice_title, strong.board_title",
        "desc_sel": "p.notice_desc, p.board_desc",
    },
    {
        "service": "나의AAC", "category": "커뮤니티",
        "list_url": "https://www.myaac.or.kr/info/community.do",
        "base_url": "https://www.myaac.or.kr",
        "item_link_sel": "a.board_title_wrap, a.notice_title_wrap",
        "title_sel": "strong.board_title, b.notice_title",
        "desc_sel": "p.board_desc, p.notice_desc",
    },
    {
        "service": "프로젝토리", "category": "공지",
        "list_url": "https://www.projectory.or.kr/news/notice-list",
        "base_url": "https://www.projectory.or.kr",
        "item_link_sel": "a.info__title-link",
        "title_sel": "dt.info__title",
        "desc_sel": "dd.info__desc",
    },
    {
        "service": "프로젝토리", "category": "프로젝토리 이야기",
        "list_url": "https://www.projectory.or.kr/news/projectory-story-list",
        "base_url": "https://www.projectory.or.kr",
        "item_link_sel": "a.info__title-link",
        "title_sel": "dt.info__title",
        "desc_sel": "dd.info__desc",
    },
    {
        "service": "프로젝토리", "category": "갤러리",
        "list_url": "https://www.projectory.or.kr/news/gallery-list",
        "base_url": "https://www.projectory.or.kr",
        "item_link_sel": "a.info__title-link",
        "title_sel": "dt.info__title",
        "desc_sel": "dd.info__desc",
    },
    {
        "service": "FAIR AI", "category": "공지사항",
        "list_url": "https://fairai.or.kr/about/notices",
        "base_url": "https://fairai.or.kr",
        "item_link_sel": "a.notice-item, a[href*=notice]",
        "title_sel": ".title",
        "desc_sel": ".desc",
        "spa": True,
    },
    {
        "service": "FAIR AI", "category": "인사이트",
        "list_url": "https://fairai.or.kr/embedded-ethics/insight-plus",
        "base_url": "https://fairai.or.kr",
        "item_link_sel": "a.insight-item, a[href*=insight]",
        "title_sel": ".title",
        "desc_sel": ".desc",
        "spa": True,
    },
    {
        "service": "대표 홈페이지", "category": "재단소식",
        "list_url": "https://www.ncfoundation.or.kr/community/",
        "base_url": "https://www.ncfoundation.or.kr",
        "item_link_sel": "a[href*=community], a.post-item",
        "title_sel": ".title",
        "desc_sel": ".desc",
        "spa": True,
    },
]


def _first(scope, selector):
    for sel in selector.split(","):
        el = scope.select_one(sel.strip())
        if el:
            return el
    return None


def _select_any(soup, selector):
    for sel in selector.split(","):
        found = soup.select(sel.strip())
        if found:
            return found
    return []


def crawl_source(cfg, max_items=10):
    """게시판 1개 크롤링 → 글 dict 리스트.

    무료 호스팅(메모리/시간 제한)에서 안전하도록 목록 페이지 한 번만 받아
    제목·날짜·요약을 추출한다(상세 페이지는 받지 않음). 본문은 목록 요약을 사용한다.
    SPA 사이트는 정적 목록이 없으므로 아예 건너뛴다.
    """
    label = f"{cfg['service']} · {cfg['category']}"
    if cfg.get("spa"):
        print(f"[board] {label}: SPA라 건너뜀 (API/Playwright 필요)", flush=True)
        return []

    t0 = time.time()
    try:
        resp = fetcher.get(cfg["list_url"])
    except Exception as e:  # noqa: BLE001
        print(f"[board] {label} 목록 요청 실패: {e}", flush=True)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    anchors = _select_any(soup, cfg["item_link_sel"])
    if not anchors:
        print(f"[board] {label}: 글 링크 0개", flush=True)
        return []

    items, seen = [], set()
    for a in anchors:
        href = a.get("href")
        if not href:
            continue
        url = urljoin(cfg["base_url"], href)
        if url in seen:
            continue
        seen.add(url)

        container = a.find_parent(["li", "dd", "tr", "article", "div"]) or a
        title = None
        if cfg.get("title_sel"):
            t = _first(container, cfg["title_sel"]) or _first(a, cfg["title_sel"])
            title = t.get_text(strip=True) if t else None
        title = title or a.get_text(strip=True)
        if not title:
            continue

        published = extractor.parse_date(container.get_text(" ", strip=True))
        if published:
            try:
                if datetime.fromisoformat(published.replace(" ", "T")) < START_DATE:
                    continue  # 2026-01-01 이전 제외
            except ValueError:
                pass

        desc_el = _first(container, cfg.get("desc_sel", "")) if cfg.get("desc_sel") else None
        items.append({
            "service": cfg["service"],
            "category": cfg["category"],
            "title": title,
            "published_at": published,
            "author": None,
            "content": desc_el.get_text(strip=True) if desc_el else None,
            "url": url,
        })
        if len(items) >= max_items:
            break

    print(f"[board] {label}: 수집 {len(items)}건 / {time.time() - t0:.1f}s", flush=True)
    return items


def crawl_all(max_items=10, progress=None):
    """모든 게시판 크롤링. 호출측에서 제목 기반 중복 판단 후 저장.
    progress(msg): 진행상황 콜백(선택).
    """
    progress = progress or (lambda m: None)
    t0 = time.time()
    results = []
    for cfg in SOURCES:
        items = crawl_source(cfg, max_items=max_items)
        results.extend(items)
        progress(f"{cfg['service']} · {cfg['category']}: {len(items)}건")
    msg = f"게시판 전체 {len(results)}건 / {time.time() - t0:.1f}s"
    print("[board] " + msg, flush=True)
    progress(msg)
    return results
