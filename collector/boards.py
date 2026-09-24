"""탭2: 재단 서비스 게시판 크롤러.

대상 게시판
- 나의AAC    : 소식, 커뮤니티        (서버렌더)
- 프로젝토리   : 공지, 이야기, 갤러리   (서버렌더)
- FAIR AI     : 공지사항, 인사이트      (Nuxt SPA → 정적 HTML에 목록 없음)
- 대표 홈페이지 : 재단소식             (React SPA → 정적 HTML에 목록 없음)

규칙
- 수집 항목: 제목, 작성일, 작성자, 본문, 원문 URL
- 중복: 제목 기반 (dedup.is_duplicate_title) — 저장 단계에서 판단
- 날짜 제한 없음(글 수가 많지 않아 전체 수집)
- 수동 실행

선택자는 /api/inspect 로 확인한 실제 구조 기준이다. SPA 사이트(FAIR AI, 대표홈페이지)는
정적 HTML에 목록이 없어 현재 방식으로는 수집되지 않으며, 추후 사이트 내부 API 또는
헤드리스 브라우저(Playwright) 도입이 필요하다(spa=True로 표시).
"""
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import extractor, fetcher

SOURCES = [
    {
        "service": "나의AAC", "category": "소식",
        "list_url": "https://www.myaac.or.kr/info/announcement.do",
        "base_url": "https://www.myaac.or.kr",
        "item_link_sel": "a.notice_title_wrap, a.board_title_wrap",
        "title_sel": "b.notice_title, strong.board_title",
        "desc_sel": "p.notice_desc, p.board_desc",
        "detail_url": "https://www.myaac.or.kr/info/announcementDetail.do?seq={seq}",
    },
    {
        "service": "나의AAC", "category": "커뮤니티",
        "list_url": "https://www.myaac.or.kr/info/community.do",
        "base_url": "https://www.myaac.or.kr",
        "item_link_sel": "a.board_title_wrap, a.notice_title_wrap",
        "title_sel": "strong.board_title, b.notice_title",
        "desc_sel": "p.board_desc, p.notice_desc",
        "detail_url": "https://www.myaac.or.kr/info/communityDetail.do?seq={seq}",
    },
    {
        "service": "프로젝토리", "category": "공지",
        "base_url": "https://m.projectory.or.kr",
        "projectory_api": "https://m.projectory.or.kr/news/notice-list",
    },
    {
        "service": "프로젝토리", "category": "프로젝토리 이야기",
        "base_url": "https://m.projectory.or.kr",
        "projectory_api": "https://m.projectory.or.kr/news/projectory-story-list",
    },
    {
        "service": "프로젝토리", "category": "갤러리",
        "base_url": "https://m.projectory.or.kr",
        "projectory_api": "https://m.projectory.or.kr/news/gallery-list",
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
        "list_url": "https://ncfoundation.or.kr/community/all",
        "base_url": "https://ncfoundation.or.kr",
        # 이 사이트는 CRA SPA라 내부 API(JSON)를 직접 호출한다.
        "api": "https://api.ncfoundation.or.kr/community/all",
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


def _parse_detail(url):
    try:
        resp = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        print(f"[board] 상세 요청 실패: {url} ({e})", flush=True)
        return {}
    soup = BeautifulSoup(resp.text, "lxml")
    art = extractor.extract_article(soup, url)
    art["image_url"] = extractor.extract_image(soup)  # 대표 이미지(og:image)
    return art


def _resolve_url(a, cfg):
    """글 링크를 실제 http URL로 해석. javascript: 링크면 seq를 찾아 상세 URL을 만든다."""
    href = a.get("href") or ""
    if href and not href.startswith(("javascript", "#")):
        url = urljoin(cfg["base_url"], href)
        if url.startswith("http"):
            return url
    # javascript 링크: onclick/href/data-* 에서 글 번호(seq)를 찾아 상세 URL 구성
    if cfg.get("detail_url"):
        blob = " ".join([href, a.get("onclick") or "", " ".join(str(v) for v in a.attrs.values())])
        m = (re.search(r"goDetail\(\s*['\"]?(\d{1,9})", blob)   # goDetail('270')
             or re.search(r"seq['\"=:\s]*?(\d{1,9})", blob)     # seq=270 / seq:270
             or re.search(r"\(\s*['\"]?(\d{1,9})['\"]?\s*\)", blob))  # ('270') / (270)
        if m:
            return cfg["detail_url"].format(seq=m.group(1))
    return None


def _walk_posts(node, depth=0):
    """중첩 JSON을 훑어 '글'처럼 보이는 dict(제목 키가 있는)를 찾아 yield."""
    if depth > 8:
        return
    if isinstance(node, dict):
        keys = {k.lower() for k in node.keys()}
        if keys & {"title", "subject", "tit", "posttitle", "boardtitle"}:
            yield node
        for v in node.values():
            yield from _walk_posts(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_posts(v, depth + 1)


def _extract_embedded(html, cfg):
    """정적 목록이 없을 때, 페이지에 박힌 JSON(__NEXT_DATA__/application-json/__NUXT__)에서
    글 목록을 뽑아 본다. 반환: entries 리스트(build가 쓰는 형태)."""
    import json
    candidates = []
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        candidates.append(m.group(1))
    for mm in re.finditer(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S):
        candidates.append(mm.group(1))
    m = re.search(r'window\.__NUXT__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.S)
    if m:
        candidates.append(m.group(1))

    entries, seen = [], set()
    for c in candidates:
        try:
            data = json.loads(c)
        except Exception:  # noqa: BLE001
            continue
        for obj in _walk_posts(data):
            title = obj.get("title") or obj.get("subject") or obj.get("tit") \
                or obj.get("postTitle") or obj.get("boardTitle")
            title = extractor.clean_text(str(title)) if title else None
            if not title or len(title) < 2 or title in seen:
                continue
            # 글 번호/슬러그 후보 → 상세 URL 추정
            pid = None
            for k in ("id", "idx", "seq", "postId", "boardId", "no", "slug"):
                if obj.get(k) not in (None, ""):
                    pid = obj.get(k)
                    break
            if pid is not None:
                path = (cfg.get("detail_path") or "/{id}").format(id=pid)
                url = urljoin(cfg["base_url"], path)
            else:
                url = None
            # 날짜 후보
            pub = None
            for k in ("createdAt", "created_at", "regDate", "date", "publishedAt", "created"):
                if obj.get(k):
                    pub = extractor.parse_date(str(obj[k])) or str(obj[k])[:16].replace("T", " ")
                    break
            desc = obj.get("summary") or obj.get("desc") or obj.get("content")
            seen.add(title)
            entries.append({
                "url": url, "title": title, "published_at": pub,
                "desc": extractor.clean_text(str(desc)) if desc else None,
            })
    return entries


def _build_entry(e, cfg):
    """목록 항목 1건 → 저장용 dict. http 링크면 상세에서 본문 보강, 아니면 고유 키 생성."""
    content = e["desc"]
    published = e["published_at"]
    author = None
    image = None
    if e["url"]:
        link = e["url"]
        detail = _parse_detail(e["url"])
        if detail.get("content") and len(detail["content"]) > 60:
            content = detail["content"]
        published = published or detail.get("published_at")
        author = detail.get("author")
        image = detail.get("image_url")
    else:
        # 원문 링크를 못 풀면 저장 키(url)가 목록 URL로 겹쳐 글들이 뭉개지므로,
        # 목록 URL + 제목 해시로 고유 키를 만든다.
        slug = hashlib.md5(
            f"{cfg['service']}|{cfg['category']}|{e['title']}".encode("utf-8")
        ).hexdigest()[:12]
        link = f"{cfg['list_url']}#{slug}"
    return {
        "service": cfg["service"],
        "category": cfg["category"],
        "title": e["title"],
        "published_at": published,
        "author": author,
        "content": extractor.summarize(content or ""),  # 카드용 요약
        "url": link,
        "image_url": image,
    }


def _find_str_by_keys(node, keys, depth=0):
    """중첩 JSON에서 keys에 해당하는 첫 문자열 값을 찾는다."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in keys and isinstance(v, str) and len(v) > 20:
                return v
        for v in node.values():
            r = _find_str_by_keys(v, keys, depth + 1)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_str_by_keys(v, keys, depth + 1)
            if r:
                return r
    return None


_DETAIL_KEYS = {"contents", "content", "body", "contenthtml", "html",
                "desc", "description", "text", "bodyhtml",
                "boardcontents", "boardcontent", "cont", "detail", "memo"}


def _ncf_detail_summary(api_base, dtype, pid):
    """대표 홈페이지 상세 API에서 본문을 받아 카드용 요약으로. 실패 시 빈 문자열."""
    try:
        resp = fetcher.get(f"{api_base}/community/{dtype}/{pid}", retries=0, timeout=8)
        try:
            j = resp.json()
            raw = _find_str_by_keys(j, _DETAIL_KEYS)
        except ValueError:
            raw = extractor.extract_main_text(BeautifulSoup(resp.text, "lxml"))
        if raw:
            text = extractor.clean_text(raw)
            if text and len(text) >= 10:
                return extractor.summarize(text)
        # 본문 텍스트가 없으면 빈 문자열(이미지 글은 카드에 썸네일이 대신 표시됨)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _crawl_json_api(cfg, max_items, max_workers=5):
    """내부 JSON API로 글 목록을 받는 게시판(대표 홈페이지 등).
    응답: {"count": N, "list": [{id, subject, createDt, dtype, ...}]}.
    본문 요약은 상세 API에서 보강하되, 이미 저장된 글은 건너뛴다(증분)."""
    label = f"{cfg['service']} · {cfg['category']}"
    t0 = time.time()
    try:
        data = fetcher.get(cfg["api"], retries=1, timeout=15).json()
    except Exception as e:  # noqa: BLE001
        print(f"[board] {label} API 실패: {e}", flush=True)
        return []
    rows = data.get("list") or (data if isinstance(data, list) else [])
    cap = max(max_items, 300)

    # api base: "https://api.ncfoundation.or.kr/community/all" → "https://api.ncfoundation.or.kr"
    from urllib.parse import urlsplit
    sp = urlsplit(cfg["api"])
    api_base = f"{sp.scheme}://{sp.netloc}"

    try:
        known_content = db.board_content_map(cfg["service"])
    except Exception:  # noqa: BLE001
        known_content = {}

    entries = []
    for it in rows[:cap]:
        subject = extractor.clean_text(str(it.get("subject") or it.get("title") or ""))
        if not subject:
            continue
        pid = it.get("id")
        dtype = str(it.get("dtype") or "all").lower()
        url = urljoin(cfg["base_url"], f"/community/{dtype}/{pid}") if pid is not None else cfg["list_url"]
        pub = it.get("createDt") or it.get("modifyDt") or ""
        published = extractor.parse_date(str(pub)) or (str(pub)[:16].replace("T", " ") if pub else None)
        # 썸네일 이미지(목록 API가 제공) → 카드에 표시
        th = it.get("thumbnail") or {}
        fp = th.get("fullPath") if isinstance(th, dict) else None
        image = urljoin(api_base, fp) if fp else None
        entries.append({"subject": subject, "url": url, "dtype": dtype, "pid": pid,
                        "pub": published, "image_url": image})

    # 본문이 아직 없는 글만 상세 API로 요약 보강(이미 요약된 글은 재요청 안 함).
    # 썸네일은 목록에서 오므로 전 글에 채워진다. 전 글을 upsert(기존 요약은 보존).
    need = [e for e in entries if not (known_content.get(e["url"]) or "").strip() and e["pid"] is not None]

    def _summ(e):
        return _ncf_detail_summary(api_base, e["dtype"], e["pid"])
    fetched = {}
    if need:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for e, s in zip(need, pool.map(_summ, need)):
                fetched[e["url"]] = s

    items = []
    for e in entries:
        content = (known_content.get(e["url"]) or "").strip() or fetched.get(e["url"], "")
        items.append({
            "service": cfg["service"],
            "category": cfg["category"],
            "title": e["subject"],
            "published_at": e["pub"],
            "author": "NC문화재단",
            "content": content,
            "url": e["url"],
            "image_url": e.get("image_url"),
        })
    print(f"[board] {label}: {len(items)}건(본문보강 {len(need)}) / {time.time() - t0:.1f}s", flush=True)
    return items


def _pick(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _crawl_projectory(cfg, max_items):
    """프로젝토리(모바일 m.projectory.or.kr): axios.get(url, {params})로 목록 JSON을 받는다.
    params={lastIdx, regDay, searchVal, pg}로 페이지네이션. 응답 {boardList:[...], search:{totalCnt}}."""
    label = f"{cfg['service']} · {cfg['category']}"
    t0 = time.time()
    base, api = cfg["base_url"], cfg["projectory_api"]
    accept = {"Accept": "application/json, text/plain, */*", "Referer": api}
    out, seen = [], set()
    last_idx, reg_day, pg, list_count = -1, "", 1, 10
    cap = max(max_items, 300)
    for _ in range(30):  # 최대 30페이지 안전장치
        params = {"lastIdx": last_idx, "regDay": reg_day or "", "searchVal": "", "pg": pg}
        try:
            j = fetcher.get(api, params=params, headers=accept, retries=0, timeout=12).json()
        except Exception as e:  # noqa: BLE001
            print(f"[board] {label} API 실패(pg={pg}): {e}", flush=True)
            break
        blist = j.get("boardList") or []
        if not blist:
            break
        for b in blist:
            idx = b.get("boardIdx")
            if idx in seen:
                continue
            seen.add(idx)
            title = _pick(b, ("subject", "title", "boardTitle", "boardSubject", "boardNm", "tit"))
            if not title:
                continue
            fu = b.get("fileUrl") or b.get("thumbnail")
            image = urljoin(base, fu) if fu else None
            reg = b.get("regDay") or b.get("regDate") or b.get("createDt") or ""
            out.append({
                "idx": idx,
                "service": cfg["service"], "category": cfg["category"],
                "title": extractor.clean_text(title),
                "published_at": extractor.parse_date(str(reg)) or (str(reg)[:16].replace("T", " ") or None),
                "author": "프로젝토리", "content": "",
                "url": f"{base}/news/news-view?boardIdx={idx}",
                "image_url": image,
            })
        last = blist[-1]
        last_idx = last.get("boardIdx", last_idx)
        reg_day = last.get("regDay") or reg_day
        pg += 1
        total = (j.get("search") or {}).get("totalCnt")
        if len(blist) < list_count or len(out) >= cap or (total and len(out) >= total):
            break

    # 본문 요약 보강: 이미 요약 있는 글은 건너뛰고, 없는 글만 상세 API에서 가져온다(증분).
    try:
        known_content = db.board_content_map(cfg["service"])
    except Exception:  # noqa: BLE001
        known_content = {}
    need = [e for e in out if not (known_content.get(e["url"]) or "").strip()]

    def _summ(e):
        return _projectory_detail_summary(base, e["idx"])
    fetched = {}
    if need:
        with ThreadPoolExecutor(max_workers=5) as pool:
            for e, s in zip(need, pool.map(_summ, need)):
                fetched[e["url"]] = s
    for e in out:
        e["content"] = (known_content.get(e["url"]) or "").strip() or fetched.get(e["url"], "")
        e.pop("idx", None)

    print(f"[board] {label}: {len(out)}건(본문보강 {len(need)}) / {time.time() - t0:.1f}s", flush=True)
    return out


def _projectory_detail_summary(base, board_idx):
    """프로젝토리 상세 API(news-view?boardIdx=)에서 본문을 받아 요약. 실패 시 빈 문자열."""
    try:
        j = fetcher.get(f"{base}/news/news-view", params={"boardIdx": board_idx},
                        headers={"Accept": "application/json, text/plain, */*"},
                        retries=0, timeout=8).json()
        raw = _find_str_by_keys(j, _DETAIL_KEYS)
        if raw:
            return extractor.summarize(extractor.clean_text(raw))
    except Exception:  # noqa: BLE001
        pass
    return ""


def crawl_source(cfg, max_items=8, max_workers=3):
    """게시판 1개 크롤링 → 글 dict 리스트.

    내부 JSON API가 있는 사이트는 그걸 직접 호출한다(대표 홈페이지·프로젝토리).
    그 외에는 목록 페이지에서 글 링크/제목/날짜/요약을 뽑고 상세로 본문을 보강한다.
    """
    label = f"{cfg['service']} · {cfg['category']}"
    if cfg.get("projectory_api"):
        return _crawl_projectory(cfg, max_items)
    if cfg.get("api"):
        return _crawl_json_api(cfg, max_items)
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
    # 푸터/내비만 제거 (오시는 길·문의 등 푸터 링크가 글로 잘못 잡히는 것 방지).
    # 본문 영역을 지우지 않도록 header/aside 등 광범위 제거는 하지 않는다.
    for chrome in soup.select("footer, .footer, [class*=footer], .gnb, .lnb, "
                              ".sticky-menu, .sticky-menu__item, nav.gnb"):
        chrome.decompose()

    anchors = _select_any(soup, cfg["item_link_sel"])
    if not anchors:
        # 정적 목록이 없으면 페이지에 박힌 JSON에서 시도(Next.js 등 SPA 대응)
        if cfg.get("try_embedded"):
            emb = _extract_embedded(resp.text, cfg)[:max_items]
            if emb:
                print(f"[board] {label}: embedded JSON에서 {len(emb)}건", flush=True)
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    return list(pool.map(lambda e: _build_entry(e, cfg), emb))
        print(f"[board] {label}: 글 링크 0개", flush=True)
        return []

    # 1) 목록에서 항목 메타 수집
    # 제목은 '행 전체'가 아니라 '링크 자체'에서 뽑는다. (행에서 뽑으면 여러 글이 같은
    # 제목으로 잡혀 과도하게 중복 제거되던 버그 → 링크 단위로 추출해 해결)
    entries, seen = [], set()
    for a in anchors:
        title = None
        if cfg.get("title_sel"):
            t = _first(a, cfg["title_sel"])
            title = t.get_text(strip=True) if t else None
        title = extractor.clean_text(title or a.get_text(" ", strip=True))
        if not title:
            continue

        url = _resolve_url(a, cfg)
        key = url or title
        if key in seen:
            continue
        seen.add(key)

        # 날짜/요약은 행(li/dd/tr/article) 범위에서만 찾는다(전체 목록 X)
        # 날짜 제한은 두지 않는다(글 수가 많지 않아 전체 수집).
        row = a.find_parent(["li", "dd", "tr", "article"]) or a
        published = extractor.parse_date(row.get_text(" ", strip=True))

        desc_el = _first(row, cfg.get("desc_sel", "")) if cfg.get("desc_sel") else None
        entries.append({
            "url": url,
            "title": title,
            "published_at": published,
            "desc": extractor.clean_text(desc_el.get_text(" ", strip=True)) if desc_el else None,
        })
        if len(entries) >= max_items:
            break

    # 2) 링크가 정상 http면 상세 페이지에서 요약을 보강한다
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        items = list(pool.map(lambda e: _build_entry(e, cfg), entries))

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
