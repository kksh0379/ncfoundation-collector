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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from . import extractor, fetcher

RSS_URL = "https://news.google.com/rss/search"
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
# 뉴스 카테고리별 검색 키워드. (재단=엔씨문화재단, 본사=엔씨소프트 및 자회사)
CATEGORIES = {
    "재단": ["엔씨문화재단", "NC문화재단"],
    "본사": [
        "엔씨소프트", "NCSOFT", "NC", "엔씨",
        # 자회사(2024~2025 분사): AI/QA/IDS + 게임 스튜디오
        "엔씨에이아이", "NC AI", "엔씨QA", "엔씨IDS",
        "퍼스트스파크 게임즈", "빅파이어 게임즈", "루디우스 게임즈",
    ],
}
# 본사에서 걸러낼 노이즈: NC 다이노스(야구) + NC백화점(뉴코어) 등 무관 기사.
# 제목/본문에 이 중 하나라도 있으면 본사 카테고리에서 제외.
EXCLUDE = {
    "본사": [
        # 야구(NC 다이노스)
        "다이노스", "프로야구", "야구", "kbo", "구단", "선발", "타자", "투수",
        "홈런", "이닝", "마운드", "타석", "감독", "타선", "안타", "승리투수",
        # 백화점/유통(NC백화점=뉴코어, 이랜드 계열)
        "백화점", "뉴코어", "아울렛", "이랜드", "쇼핑몰", "몰",
    ],
}
# 확실한 본사 키워드(이게 있으면 게임회사 기사로 확정 → 문맥어 없어도 통과)
STRONG_HQ = ["엔씨소프트", "ncsoft", "엔씨에이아이", "nc ai", "엔씨qa", "엔씨ids",
             "퍼스트스파크", "빅파이어", "루디우스"]
# 모호한 키워드(NC/엔씨)만 걸렸을 때, 게임회사 문맥이 있어야 인정할 문맥어
CONTEXT_HQ = ["게임", "게임즈", "소프트", "리니지", "아이온", "블레이드", "블소",
              "쓰론", "throne", "tl", "김택진", "판교", "mmorpg", "신작", "게임사",
              "게임업계", "게임주", "엔터", "ip", "출시", "앱마켓", "모바일게임", "pc게임"]
NEWS_TIMEOUT = 6  # 뉴스 원문 해석은 빨리 실패시켜(스냅샷 폴백) 전체 수집을 지연시키지 않음
FULLBODY_MAX = 150  # 새 기사가 이보다 많으면 원문 해석 생략(스냅샷만) → 대량 백필 폭주 방지
# 진단 등 호환용 평면 키워드 목록
KEYWORDS = [kw for kws in CATEGORIES.values() for kw in kws]
RECENT_DAYS = 1825  # 최근 5년 기사 수집
# 원문 본문 추출 시도 여부. 구글 링크는 리다이렉트라 대부분 실패하면서 느려지므로
# 기본은 끄고 RSS 요약을 본문으로 쓴다. (속도·안정성 우선)
FETCH_FULL_BODY = True  # 원문 기사로 풀리면 요약 추출 시도(실패 시 RSS 요약 사용)


WINDOW_DAYS = 120  # 구글 뉴스 RSS는 요청당 ~100건 제한 → 기간을 이 간격으로 쪼개 깊게 수집


def _feed_params(query, after=None, before=None):
    # after/before(YYYY-MM-DD) 구간으로 검색해 구글의 100건 제한을 우회(구간별 100건).
    q = f"{query} after:{after} before:{before}" if (after and before) else query
    return {"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}


def _date_windows(days, window=WINDOW_DAYS):
    """오늘부터 days일 전까지를 window일 간격 [(after, before), ...] 로 나눈다(최근→과거)."""
    from datetime import date
    today = date.today()
    limit = today - timedelta(days=int(days))
    windows, end = [], today
    while end > limit:
        start = max(limit, end - timedelta(days=window))
        # before는 하루 여유를 줘 경계 기사 누락 방지(중복은 URL로 제거)
        windows.append((start.isoformat(), (end + timedelta(days=1)).isoformat()))
        end = start
    return windows or [(limit.isoformat(), (today + timedelta(days=1)).isoformat())]


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


def _collect_items(query, after=None, before=None):
    try:
        resp = fetcher.get(RSS_URL, params=_feed_params(query, after, before))
    except Exception as e:  # noqa: BLE001
        print(f"[google] RSS 요청 실패 ({query} {after}~{before}): {e}", flush=True)
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
        page = fetcher.get(f"https://news.google.com/rss/articles/{token}",
                           retries=0, timeout=NEWS_TIMEOUT)
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
            retries=0, timeout=NEWS_TIMEOUT,
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
        resp = fetcher.get(real or entry["url"], retries=0, timeout=NEWS_TIMEOUT)  # 리다이렉트 따라감
        final = resp.url or ""
        if "news.google." not in final and "consent.google" not in final:
            # 저장 키(url)는 RSS의 '구글 링크'로 고정(증분 수집이 되게).
            # 복원/리다이렉트로 얻은 실제 기사 URL은 표시(원문 보기)용으로만 보관.
            if final:
                entry["source_url"] = final
            page_soup = BeautifulSoup(resp.text, "lxml")
            img = extractor.extract_image(page_soup)
            if img:
                entry["image_url"] = img  # 기사 대표 이미지(og:image)
            art = extractor.extract_article(page_soup, final)
            if art.get("content") and len(art["content"]) > 120:
                summary = extractor.summarize(art["content"])
                entry["published_at"] = entry.get("published_at") or art.get("published_at")
    except Exception:  # noqa: BLE001
        pass
    # 원문 추출에 실패하면(구글 리다이렉트라 대부분 실패) RSS 요약(snippet)을
    # 본문으로 사용한다. 그래야 본문이 비어 키워드 필터에서 탈락하는 일이 없다.
    entry["content"] = summary or entry.get("snippet") or ""
    return entry


def _enrich_one(row):
    """저장된 뉴스 1건의 원문을 열어 대표 이미지(og:image)와 요약(og:description/본문)을
    함께 복원한다. 반환: {"image_url": ..., "content": ..., "source_url": ...} (없는 값은 생략)."""
    out = {}
    try:
        gl = row.get("url", "")
        real = row.get("source_url") or _decode_google_url(gl) or gl
        resp = fetcher.get(real, retries=0, timeout=NEWS_TIMEOUT)
        final = resp.url or ""
        if "news.google." in final or "consent.google" in final:
            return out
        if final and not row.get("source_url"):
            out["source_url"] = final
        soup = BeautifulSoup(resp.text, "lxml")
        img = extractor.extract_image(soup)
        if img:
            out["image_url"] = img
        # 요약: og:description 우선, 없으면 본문에서 요약
        summ = extractor.extract_summary(soup)
        if not summ:
            art = extractor.extract_article(soup, final)
            if art.get("content") and len(art["content"]) > 120:
                summ = extractor.summarize(art["content"])
        if summ and len(summ) > len(row.get("content") or ""):
            out["content"] = summ  # 기존(짧은 RSS 요약)보다 길 때만 교체
    except Exception:  # noqa: BLE001
        pass
    return out


def enrich_articles(rows, max_workers=12, progress=None):
    """이미지/요약이 부실한 뉴스들의 원문을 열어 og:image·요약을 채운다.
    반환: {url: {image_url?, content?, source_url?}} (실제로 얻은 값만)."""
    progress = progress or (lambda m: None)
    if not rows:
        return {}
    out, done = {}, 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_enrich_one, r): r for r in rows}
        for fut in as_completed(futs):
            r = futs[fut]
            data = fut.result()
            if data:
                out[r["url"]] = data
            done += 1
            if done % 20 == 0 or done == len(rows):
                progress(f"본문·이미지 보강 {done}/{len(rows)} · 확보 {len(out)}건")
    return out


def _kw_match(haystack, kw):
    """키워드 매칭. 영문/숫자 키워드(NC, NCSOFT, NC AI)는 '단어 단위'로만 일치시킨다
    (announce·finance 속 'nc' 오탐 방지). 한글 키워드는 공백 무시 부분 일치."""
    k = kw.lower()
    if re.fullmatch(r"[a-z0-9 ]+", k):
        pat = r"(?<![a-z0-9])" + re.escape(k).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        return re.search(pat, haystack) is not None
    return k.replace(" ", "") in haystack.replace(" ", "")


def _passes_filters(item, days=RECENT_DAYS):
    cat = item.get("category")
    kws = CATEGORIES.get(cat, KEYWORDS)
    haystack = f"{item.get('title', '')}\n{item.get('content', '')}".lower()
    # (1) 카테고리 키워드가 있어야 통과
    if not any(_kw_match(haystack, k) for k in kws):
        return False
    # (2) 노이즈(야구·백화점 등) 제외어가 있으면 탈락
    if any(ex.lower() in haystack for ex in EXCLUDE.get(cat, [])):
        return False
    # (2-2) 본사: 확실한 키워드(엔씨소프트/자회사)가 없고 모호한 NC/엔씨만 걸렸다면,
    #       게임회사 문맥어(게임/소프트/리니지 등)가 있어야 인정 → NC백화점·NC다이노스 등 배제
    if cat == "본사":
        if not any(s in haystack for s in STRONG_HQ):
            if not any(ctx in haystack for ctx in CONTEXT_HQ):
                return False
    # (3) 선택한 기간(최근 N일)만. 날짜를 아는 경우에만 필터(모르면 통과).
    pub = item.get("published_at")
    if pub:
        try:
            when = datetime.fromisoformat(pub.replace(" ", "T"))
            if when < datetime.now() - timedelta(days=days):
                return False
        except ValueError:
            pass
    return True


def _passes_date(item, days=RECENT_DAYS):
    """날짜 필터만(키워드 필터 없음). 고양이 뉴스처럼 쿼리 자체가 조건인 경우용."""
    pub = item.get("published_at")
    if pub:
        try:
            when = datetime.fromisoformat(pub.replace(" ", "T"))
            if when < datetime.now() - timedelta(days=days):
                return False
        except ValueError:
            pass
    return True


def crawl(max_workers=24, max_items=0, progress=None, known_urls=None, days=None,
          categories=None, keyword_filter=True, title_exclude=None):
    """뉴스 수집 실행. 파싱된 기사 리스트 반환(그룹화/저장은 호출측).
    categories: {카테고리명: [검색어...]} (기본 CATEGORIES=재단/본사).
    keyword_filter=True면 NC 키워드/노이즈 필터를 적용하고, False면 날짜만 필터
    (고양이 뉴스처럼 검색어 자체가 조건인 경우).
    days: 수집 기간(최근 N일). 미지정 시 기본 RECENT_DAYS.
    progress(msg): 진행상황 콜백(선택).
    """
    progress = progress or (lambda m: None)
    known_urls = known_urls or set()
    days = int(days) if days else RECENT_DAYS
    categories = categories or CATEGORIES
    t0 = time.time()

    # 기간을 구간으로 쪼개 (검색어 × 구간)마다 RSS 수집 → 구글 100건 제한 우회(깊은 과거까지).
    windows = _date_windows(days)
    tasks = [(cat, kw, af, bf) for cat, kws in categories.items()
             for kw in kws for (af, bf) in windows]
    progress(f"RSS 수집 중… (검색어 {sum(len(v) for v in categories.values())}개 × 구간 {len(windows)}개)")

    def _fetch(task):
        cat, kw, af, bf = task
        return cat, _collect_items(kw, af, bf)

    seen, entries = set(), []
    done_tasks = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, 12)) as pool:
        for cat, rows in pool.map(_fetch, tasks):
            done_tasks += 1
            for e in rows:
                u = e["url"]
                if u not in seen:
                    seen.add(u)
                    e["category"] = cat
                    entries.append(e)
                elif cat == "재단":
                    # 같은 URL이 본사로 먼저 잡혔어도 재단이 우선
                    for prev in entries:
                        if prev["url"] == u:
                            prev["category"] = "재단"
                            break
            if done_tasks % 10 == 0 or done_tasks == len(tasks):
                progress(f"RSS 수집 {done_tasks}/{len(tasks)} 구간 · 누적 {len(entries)}건")
    print(f"[google] RSS 수집 완료: {len(tasks)}개 요청 → {len(entries)}건", flush=True)

    # 이미 저장된 URL은 재해석하지 않는다(증분). 새 기사만 남긴다.
    total = len(entries)
    entries = [e for e in entries if e["url"] not in known_urls]
    if max_items and max_items > 0:
        entries = entries[:max_items]
    print(f"[google] RSS {total}개 중 신규 {len(entries)}개 처리(기존 {total - len(entries)}개 건너뜀)", flush=True)

    items = []
    if entries:
        n = len(entries)
        if n > FULLBODY_MAX:
            # 대량(백필 등): 원문 해석(batchexecute) 생략하고 RSS 요약(snippet)만 사용.
            # → 수천 건도 빠르게 처리, 구글 rate-limit/차단 회피, 서버 부하 급감.
            progress(f"새 기사 {n}건 — 요약(스냅샷)만 사용(대량)")
            processed = []
            for e in entries:
                e["content"] = e.get("snippet") or ""
                processed.append(e)
        else:
            # 소량(일상 증분): 원문 해석 시도(병렬)로 본문 요약 품질 확보.
            progress(f"새 기사 요약 처리 0/{n}")
            processed = []
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(_summary_from_article, e) for e in entries]
                done = 0
                for fut in as_completed(futures):
                    processed.append(fut.result())
                    done += 1
                    if done % 5 == 0 or done == n:
                        progress(f"새 기사 요약 처리 {done}/{n}")
        _flt = _passes_filters if keyword_filter else _passes_date

        def _ok(e):
            if not _flt(e, days):
                return False
            if title_exclude:  # 제목에 제외어가 있으면 탈락(제목만 검사)
                t = (e.get("title") or "").lower()
                if any(x.lower() in t for x in title_exclude):
                    return False
            return True
        items = [
            {k: e.get(k) for k in ("title", "published_at", "author", "content",
                                    "url", "source_url", "category", "image_url")}
            for e in processed if _ok(e)
        ]

    msg = f"구글뉴스 수집 {len(items)}건 / {time.time() - t0:.1f}s"
    print("[google] " + msg, flush=True)
    progress(msg)
    return items


# ---- 고양이(반려묘) 뉴스: 별도 카테고리·검색어(구글 불리언 쿼리) ----
CAT_CATEGORIES = {
    "사료·영양": ["(고양이 OR 반려묘) (사료 OR 캔 OR 간식) (리콜 OR 성분 OR 부작용)"],
    "행동·심리": ["(고양이 OR 반려묘) (행동학 OR 스트레스 OR 시그널 OR 공격성)"],
    "업계 트렌드": ["(고양이 OR 반려묘) (펫테크 OR 헬스케어 OR AI OR 신제품)"],
    "사회·제도": ["(고양이 OR 길고양이) (동물보호법 OR 학대 OR TNR OR 등록제)"],
    "반려묘 보험": ["(고양이 OR 반려묘) (펫보험 OR 실손보험 OR 보장)"],
}


def crawl_cat(max_workers=24, max_items=0, progress=None, known_urls=None, days=None):
    """고양이(반려묘) 뉴스 수집. 뉴스와 같은 방식이나 검색어가 곧 조건이라 키워드 필터는 끈다."""
    return crawl(max_workers=max_workers, max_items=max_items, progress=progress,
                 known_urls=known_urls, days=days,
                 categories=CAT_CATEGORIES, keyword_filter=False)


# ---- 업계동향: (A) 업계 키워드 OR (B) 지정 기관명. 제목에 제외어 있으면 버림 ----
BIZ_CATEGORIES = {
    "업계동향": [
        # A. 업계 키워드
        "(문화재단 OR 공익재단 OR 비영리재단 OR 기업재단 OR 사회공헌재단 OR 공익법인 OR 비영리법인)",
        # B. 지정 기관명(기타 기관은 여기에 OR로 추가하면 됨)
        "(아산나눔재단 OR 삼성문화재단 OR CJ문화재단 OR 롯데문화재단 OR \"현대차 정몽구 재단\" "
        "OR 포스코청암재단 OR 두산연강재단 OR LG연암문화재단 OR 카카오임팩트 OR 네이버문화재단)",
    ],
}
BIZ_EXCLUDE_TITLE = ["채용", "채용공고", "입찰", "입찰공고", "휴관", "티켓"]


def crawl_biz(max_workers=24, max_items=0, progress=None, known_urls=None, days=None):
    """업계동향 뉴스 수집. 검색어가 조건(키워드/기관명)이라 키워드 필터는 끄고,
    제목에 제외어(채용·입찰·휴관·티켓 등)가 있으면 버린다."""
    return crawl(max_workers=max_workers, max_items=max_items, progress=progress,
                 known_urls=known_urls, days=days, categories=BIZ_CATEGORIES,
                 keyword_filter=False, title_exclude=BIZ_EXCLUDE_TITLE)
