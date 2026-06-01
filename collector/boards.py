"""탭2: 재단 서비스 게시판 크롤러.

대상 게시판
- 나의AAC    : 소식, 커뮤니티
- FAIR AI     : 공지사항, 인사이트
- 대표 홈페이지 : 재단소식
- 프로젝토리   : 공지, 프로젝토리 이야기, 갤러리

규칙
- 2026-01-01 이후 글만 수집 (START_DATE)
- 수집 항목: 제목, 작성일, 작성자, 본문, 원문 URL
- 중복: 제목 기반 (dedup.is_duplicate_title) — 저장 단계에서 판단
- 수동 실행

================================ 중요 ================================
아래 SOURCES의 CSS 선택자는 "현재 구조 추정값"이다. 이 컨테이너에서는
해당 사이트들이 네트워크 차단되어 실제 HTML을 확인하지 못했다.
로컬에서 한 번 돌려보고 selector를 실제 구조에 맞게 보정해야 한다.

또한 fairai.or.kr / projectory.or.kr 처럼 경로가 SPA(예: /news/notice-list)
형태인 사이트는 JS 렌더링일 가능성이 높다. requests로 목록이 비면
fetcher를 Playwright 기반으로 교체하거나, 해당 사이트의 내부 JSON API를
찾아 list_parser를 JSON 파서로 바꾸면 된다.
=====================================================================
"""
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import fetcher

START_DATE = datetime(2026, 1, 1)


def _text(el):
    return el.get_text(strip=True) if el else None


def _parse_date_str(raw):
    if not raw:
        return None
    raw = raw.strip()
    import re

    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d).isoformat(timespec="minutes")
        except ValueError:
            return None
    return None


# 각 게시판 설정.
#   list_url      : 목록 페이지 URL
#   base_url      : 상세 링크 상대경로 보정용
#   row_sel       : 목록에서 글 한 행(또는 카드) 선택자
#   link_sel      : 행 안에서 상세 링크 <a>
#   title_sel     : 행 안에서 제목 (없으면 link 텍스트 사용)
#   date_sel      : 행 안에서 작성일
#   detail_*_sel  : 상세 페이지 선택자
SOURCES = [
    {
        "service": "나의AAC",
        "category": "소식",
        "list_url": "https://www.myaac.or.kr/info/announcement.do",
        "base_url": "https://www.myaac.or.kr",
        "row_sel": "table.board_list tbody tr, ul.board_list li",
        "link_sel": "a",
        "title_sel": "td.title, .title, a",
        "date_sel": "td.date, .date",
        "detail_title_sel": ".view_title, .board_view .title, h3",
        "detail_date_sel": ".view_info .date, .date",
        "detail_author_sel": ".view_info .writer, .writer",
        "detail_content_sel": ".view_content, .board_view_content, .content",
    },
    {
        "service": "나의AAC",
        "category": "커뮤니티",
        "list_url": "https://www.myaac.or.kr/info/community.do",
        "base_url": "https://www.myaac.or.kr",
        "row_sel": "table.board_list tbody tr, ul.board_list li",
        "link_sel": "a",
        "title_sel": "td.title, .title, a",
        "date_sel": "td.date, .date",
        "detail_title_sel": ".view_title, .board_view .title, h3",
        "detail_date_sel": ".view_info .date, .date",
        "detail_author_sel": ".view_info .writer, .writer",
        "detail_content_sel": ".view_content, .board_view_content, .content",
    },
    {
        "service": "FAIR AI",
        "category": "공지사항",
        "list_url": "https://fairai.or.kr/about/notices",
        "base_url": "https://fairai.or.kr",
        "row_sel": "li, tr, .board-item, article",
        "link_sel": "a",
        "title_sel": ".title, h3, a",
        "date_sel": ".date, time",
        "detail_title_sel": "h1, h2, .post-title, .title",
        "detail_date_sel": ".date, time, .post-date",
        "detail_author_sel": ".author, .writer",
        "detail_content_sel": ".post-content, .content, article",
    },
    {
        "service": "FAIR AI",
        "category": "인사이트",
        "list_url": "https://fairai.or.kr/embedded-ethics/insight-plus",
        "base_url": "https://fairai.or.kr",
        "row_sel": "li, tr, .board-item, article",
        "link_sel": "a",
        "title_sel": ".title, h3, a",
        "date_sel": ".date, time",
        "detail_title_sel": "h1, h2, .post-title, .title",
        "detail_date_sel": ".date, time, .post-date",
        "detail_author_sel": ".author, .writer",
        "detail_content_sel": ".post-content, .content, article",
    },
    {
        "service": "대표 홈페이지",
        "category": "재단소식",
        "list_url": "https://www.ncfoundation.or.kr/community/",
        "base_url": "https://www.ncfoundation.or.kr",
        "row_sel": "table tbody tr, ul.board_list li, .post-item",
        "link_sel": "a",
        "title_sel": ".title, td.subject, a",
        "date_sel": ".date, td.date",
        "detail_title_sel": ".view_title, h1, h3, .title",
        "detail_date_sel": ".date, .view_info .date",
        "detail_author_sel": ".writer, .author",
        "detail_content_sel": ".view_content, .content, article",
    },
    {
        "service": "프로젝토리",
        "category": "공지",
        "list_url": "https://www.projectory.or.kr/news/notice-list",
        "base_url": "https://www.projectory.or.kr",
        "row_sel": "li, tr, .list-item, .card",
        "link_sel": "a",
        "title_sel": ".title, h3, a",
        "date_sel": ".date, time",
        "detail_title_sel": "h1, h2, .title",
        "detail_date_sel": ".date, time",
        "detail_author_sel": ".author, .writer",
        "detail_content_sel": ".content, article, .view-content",
    },
    {
        "service": "프로젝토리",
        "category": "프로젝토리 이야기",
        "list_url": "https://www.projectory.or.kr/news/projectory-story-list",
        "base_url": "https://www.projectory.or.kr",
        "row_sel": "li, tr, .list-item, .card",
        "link_sel": "a",
        "title_sel": ".title, h3, a",
        "date_sel": ".date, time",
        "detail_title_sel": "h1, h2, .title",
        "detail_date_sel": ".date, time",
        "detail_author_sel": ".author, .writer",
        "detail_content_sel": ".content, article, .view-content",
    },
    {
        "service": "프로젝토리",
        "category": "갤러리",
        "list_url": "https://www.projectory.or.kr/news/gallery-list",
        "base_url": "https://www.projectory.or.kr",
        "row_sel": "li, tr, .list-item, .card, .gallery-item",
        "link_sel": "a",
        "title_sel": ".title, h3, a",
        "date_sel": ".date, time",
        "detail_title_sel": "h1, h2, .title",
        "detail_date_sel": ".date, time",
        "detail_author_sel": ".author, .writer",
        "detail_content_sel": ".content, article, .view-content",
    },
]


def _first(soup, selector):
    """콤마로 나열한 후보 선택자 중 처음 매칭되는 요소."""
    for sel in selector.split(","):
        el = soup.select_one(sel.strip())
        if el:
            return el
    return None


def _parse_detail(url, cfg):
    try:
        resp = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        print(f"[board] 상세 요청 실패: {url} ({e})")
        return None
    soup = BeautifulSoup(resp.text, "lxml")

    title = _text(_first(soup, cfg["detail_title_sel"]))
    published = _parse_date_str(_text(_first(soup, cfg["detail_date_sel"])))
    author = _text(_first(soup, cfg["detail_author_sel"]))

    content_el = _first(soup, cfg["detail_content_sel"])
    content = None
    if content_el:
        for junk in content_el.select("script, style"):
            junk.decompose()
        content = content_el.get_text("\n", strip=True)

    return {"title": title, "published_at": published, "author": author, "content": content}


def crawl_source(cfg, max_items=30):
    """게시판 1개 크롤링 → 글 dict 리스트."""
    items = []
    try:
        resp = fetcher.get(cfg["list_url"])
    except Exception as e:  # noqa: BLE001
        print(f"[board] 목록 요청 실패: {cfg['service']}/{cfg['category']} ({e})")
        return items

    soup = BeautifulSoup(resp.text, "lxml")
    rows = []
    for sel in cfg["row_sel"].split(","):
        rows = soup.select(sel.strip())
        if rows:
            break

    for row in rows[:max_items]:
        link_el = row.select_one(cfg["link_sel"])
        if not link_el or not link_el.get("href"):
            continue
        href = urljoin(cfg["base_url"], link_el["href"])
        if href in (cfg["list_url"], cfg["base_url"]):
            continue

        list_title = _text(_first(row, cfg["title_sel"])) or _text(link_el)
        list_date = _parse_date_str(_text(_first(row, cfg["date_sel"])))

        detail = _parse_detail(href, cfg) or {}
        title = detail.get("title") or list_title
        if not title:
            continue

        published = detail.get("published_at") or list_date
        # 2026-01-01 이후만
        if published:
            try:
                if datetime.fromisoformat(published) < START_DATE:
                    continue
            except ValueError:
                pass

        items.append(
            {
                "service": cfg["service"],
                "category": cfg["category"],
                "title": title,
                "published_at": published,
                "author": detail.get("author"),
                "content": detail.get("content"),
                "url": href,
            }
        )
    return items


def crawl_all(max_items=30):
    """모든 게시판 크롤링. 호출측에서 제목 기반 중복 판단 후 저장."""
    results = []
    for cfg in SOURCES:
        results.extend(crawl_source(cfg, max_items=max_items))
    return results
