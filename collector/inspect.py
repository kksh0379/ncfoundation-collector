"""사이트 구조 분석 도구.

배포 서버(인터넷 가능)에서 대상 페이지를 가져와, 크롤러 선택자를 정하는 데
필요한 정보를 요약해 돌려준다.
- 후보 행(row) 선택자별 매칭 개수
- 반복되는 class 조합 (목록 항목일 가능성이 높은 패턴)
- 샘플 링크 (목록 글 링크 형태 파악용)
- SPA 여부 (__NEXT_DATA__ / #__next / #root / __NUXT__ 등)
- Next.js __NEXT_DATA__가 있으면, 글 목록처럼 보이는 배열을 찾아 경로/키/샘플 제공
"""
import json
from collections import Counter

from bs4 import BeautifulSoup

from . import fetcher

COMMON_ROW_SELECTORS = [
    "table tbody tr", "table tr",
    "ul.board_list li", "ul.list li", "ul li", "ol li",
    "article", ".board-item", ".list-item", ".list_item",
    ".item", ".card", ".post", ".notice", ".gallery-item",
    "[class*=list] li", "[class*=item]", "[class*=card]",
]

_LIST_KEY_HINTS = (
    "title", "subject", "name", "id", "seq", "no", "date", "createdat",
    "created_at", "regdate", "reg_date", "writer", "author", "content",
)


def _class_key(el):
    cls = el.get("class")
    return el.name + ("." + ".".join(cls) if cls else "")


def _find_lists(data, path="", depth=0, found=None):
    """JSON 안에서 글 목록처럼 보이는 배열(딕셔너리 리스트)을 찾는다."""
    if found is None:
        found = []
    if depth > 8 or len(found) >= 8:
        return found
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            keys = list(data[0].keys())
            if any(k.lower() in _LIST_KEY_HINTS for k in keys):
                sample = {k: str(v)[:60] for k, v in list(data[0].items())[:8]}
                found.append(
                    {"path": path or "(root)", "len": len(data), "keys": keys[:15], "sample": sample}
                )
        for i, v in enumerate(data[:3]):
            _find_lists(v, f"{path}[{i}]", depth + 1, found)
    elif isinstance(data, dict):
        for k, v in data.items():
            _find_lists(v, f"{path}.{k}", depth + 1, found)
    return found


def inspect_url(url):
    try:
        r = fetcher.get(url, retries=0)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}

    html = r.text or ""
    soup = BeautifulSoup(html, "lxml")
    out = {
        "url": url,
        "ok": True,
        "status": r.status_code,
        "bytes": len(html),
        "page_title": (soup.title.get_text(strip=True) if soup.title else None),
    }

    out["spa_markers"] = {
        "__NEXT_DATA__": bool(soup.select_one("script#__NEXT_DATA__")),
        "#__next": bool(soup.select_one("#__next")),
        "#root": bool(soup.select_one("#root")),
        "#app": bool(soup.select_one("#app")),
        "__NUXT__": "__NUXT__" in html,
        "script_count": len(soup.select("script")),
    }

    # 후보 행 선택자별 개수
    cand = {}
    for sel in COMMON_ROW_SELECTORS:
        try:
            n = len(soup.select(sel))
        except Exception:  # noqa: BLE001
            continue
        if n >= 2:
            cand[sel] = n
    out["row_candidates"] = cand

    # 반복되는 class 조합 (목록 항목 후보)
    counter = Counter()
    for el in soup.find_all(True):
        if el.get("class"):
            counter[_class_key(el)] += 1
    out["repeated_classes"] = [
        {"sel": k, "count": v} for k, v in counter.most_common(15) if v >= 3
    ]

    # 샘플 링크
    links = []
    for a in soup.select("a[href]"):
        href = a.get("href")
        txt = a.get_text(strip=True)
        if txt and len(txt) > 4 and href and not href.startswith(("#", "javascript")):
            links.append({"text": txt[:50], "href": href[:120]})
        if len(links) >= 12:
            break
    out["sample_links"] = links

    # Next.js __NEXT_DATA__ 안의 목록 탐색
    nd = soup.select_one("script#__NEXT_DATA__")
    if nd and nd.string:
        try:
            out["next_data_lists"] = _find_lists(json.loads(nd.string))
        except Exception as e:  # noqa: BLE001
            out["next_data_error"] = str(e)[:120]

    return out
