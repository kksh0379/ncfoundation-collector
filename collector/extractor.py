"""범용 본문/메타 추출기.

네이버 검색은 외부 언론사 기사로 직접 링크되고, 게시판 상세 페이지도 사이트마다
구조가 달라서, 사이트별 선택자 대신 "가장 본문다운 영역"을 추정해 텍스트를 뽑는
가벼운 readability 방식을 쓴다.
"""
import re
from datetime import datetime

# 본문이 아닌 영역(메뉴/광고/댓글 등)
_NOISE = "script, style, nav, header, footer, aside, form, button, .gnb, .lnb, " \
         ".footer, .header, .nav, .menu, .comment, .reply, .ad, .banner, .sns, .share"

_CONTENT_HINTS = [
    "article", ".view_content", ".board_view", ".board-view", ".post-content",
    ".post_content", ".content_view", ".view-content", "#content", ".content",
    "[class*=article]", "[class*=view]", "[itemprop=articleBody]", "#dic_area", "main",
]

_DATE_RE = re.compile(r"(20\d{2})[.\-/년 ]\s*(\d{1,2})[.\-/월 ]\s*(\d{1,2})")


def clean_text(s):
    """본문에 섞일 수 있는 HTML 태그/과도한 공백을 제거해 순수 텍스트로."""
    if not s:
        return s
    if "<" in s and ">" in s:
        from bs4 import BeautifulSoup

        s = BeautifulSoup(s, "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(text):
    """문자열에서 YYYY.MM.DD 형태를 찾아 ISO 문자열로. 실패 시 None."""
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    try:
        return datetime(y, mo, d).isoformat(timespec="minutes")
    except ValueError:
        return None


def extract_main_text(soup):
    """페이지에서 가장 본문다운 텍스트 블록을 추출한다."""
    for junk in soup.select(_NOISE):
        junk.decompose()

    best, best_len = "", 0
    for sel in _CONTENT_HINTS:
        for c in soup.select(sel):
            txt = c.get_text("\n", strip=True)
            if len(txt) > best_len:
                best, best_len = txt, len(txt)
    if best_len >= 150:
        return best

    # 폴백: 본문이 안 잡히면 <p> 텍스트를 모은다
    ps = [p.get_text(strip=True) for p in soup.select("p")]
    joined = "\n".join(p for p in ps if p)
    return joined or best


def _meta(soup, *names):
    for n in names:
        el = soup.select_one(f'meta[property="{n}"], meta[name="{n}"]')
        if el and el.get("content"):
            return el["content"].strip()
    return None


def extract_article(soup, url):
    """기사/글 1건에서 제목·작성일·작성자·본문을 추출."""
    title = _meta(soup, "og:title")
    if not title:
        h = soup.select_one("h1, h2, .title, #title_area")
        title = h.get_text(strip=True) if h else (soup.title.get_text(strip=True) if soup.title else None)

    published = _meta(soup, "article:published_time", "og:article:published_time")
    if published:
        published = published[:16].replace("T", " ")
    else:
        t = soup.select_one("time[datetime]")
        if t and t.get("datetime"):
            published = t["datetime"][:16].replace("T", " ")
        else:
            published = parse_date(soup.get_text(" ", strip=True)[:2000])

    author = _meta(soup, "og:site_name", "author")
    if not author:
        m = re.search(r"https?://(?:www\.|m\.)?([^/]+)", url or "")
        author = m.group(1) if m else None

    content = clean_text(extract_main_text(soup))
    return {"title": clean_text(title), "published_at": published, "author": author, "content": content}
