"""구조화된 국내 행사 소스 어댑터(공공 Open API 등).

구글 뉴스 기반 수집(events.crawl)은 '기사화된 행사'만 잡혀 누락이 많다. 이 모듈은
행사를 '목록으로 직접' 제공하는 구조화 소스에서 날짜·장소가 확정된 행사를 모은다.

원칙(gold 교훈):
  - 키/URL은 env로만 받는다. 미설정이면 해당 소스를 건너뛴다(추측·날조 없음).
  - 실패하면 빈 리스트로 degrade — 전체 수집을 막지 않는다.
  - 응답 구조는 운영에서 /api/admin/eventcheck(diagnose)로 먼저 확인한 뒤 매핑을 보정한다.

반환 항목 shape는 events.crawl과 동일:
  title, published_at, author, content, url, source_url, image_url, venue, region,
  start_date(YYYY-MM-DD), end_date(YYYY-MM-DD)
"""
import os
import datetime

import requests
from lxml import etree


def _fmt8(s):
    """YYYYMMDD → YYYY-MM-DD. 아니면 None."""
    s = str(s or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


def _item(title, start, end, venue="", region="", url="", content="", image="", source="", author=""):
    return {
        "title": (title or "").strip(),
        "published_at": datetime.date.today().isoformat(),
        "author": author or source,
        "content": content or "",
        "url": url or "",
        "source_url": url or "",
        "image_url": image or "",
        "venue": venue or "",
        "region": region or "",
        "start_date": start,
        "end_date": end or start,
        "source": source or "",
    }


def _rows(data):
    """data.go.kr 공통 응답에서 items.item 리스트를 뽑는다(단건 dict도 리스트로)."""
    try:
        items = data["response"]["body"]["items"]
        if not items:
            return []
        it = items.get("item") if isinstance(items, dict) else items
        if it is None:
            return []
        return it if isinstance(it, list) else [it]
    except Exception:
        return []


# ---------------------------- 한국관광공사 TourAPI: 행사/축제 ----------------------------
def tour_festivals(progress=None):
    """TourAPI searchFestival(행사/축제). TOURAPI_KEY(디코딩된 서비스키) 필요.
    최근 2주 이후 시작 행사까지 포함(진행 중 포함). 종료 행사는 DB list_events가 걸러낸다."""
    key = os.getenv("TOURAPI_KEY")
    if not key:
        return []
    out = []
    try:
        base = os.getenv("TOURAPI_FESTIVAL_URL",
                         "https://apis.data.go.kr/B551011/KorService2/searchFestival2")
        start = (datetime.date.today() - datetime.timedelta(days=14)).strftime("%Y%m%d")
        params = {"serviceKey": key, "MobileOS": "ETC", "MobileApp": "hscope",
                  "_type": "json", "arrange": "A",
                  "eventStartDate": start, "numOfRows": 300, "pageNo": 1}
        data = requests.get(base, params=params, timeout=(3, 12)).json()
        for it in _rows(data):
            sd = _fmt8(it.get("eventstartdate"))
            if not sd or not (it.get("title") or "").strip():
                continue
            addr = (it.get("addr1") or "").strip()
            cid = it.get("contentid") or ""
            out.append(_item(
                it.get("title", ""), sd, _fmt8(it.get("eventenddate")) or sd,
                venue=addr, region=(addr.split()[0] if addr else ""),
                url=(f"https://korean.visitkorea.or.kr/detail/ms_detail.do?cotid={cid}" if cid else ""),
                image=it.get("firstimage") or "", source="관광공사"))
    except Exception:
        return out
    if progress:
        progress(f"관광공사 축제 {len(out)}건")
    return out


# ------------------------------ 문화포털/공공데이터: 문화행사 ------------------------------
# '행사(컨퍼런스·전시·박람)' 탭 성격상 순수 공연류는 제외해 노이즈를 줄인다(전시·축제·행사는 유지).
_CULTURE_SKIP_REALM = {"연극", "뮤지컬", "무용", "음악", "국악", "클래식", "콘서트", "영화", "대중음악"}


def _culture_url():
    """CULTURE_API_URL을 쓰되, B553457 '한눈에보는문화정보' 베이스(.../cultureinfo)만 넣었으면
    기간별 조회 오퍼레이션(/period2)을 자동으로 붙인다. 전체 오퍼레이션 URL이면 그대로 사용."""
    url = (os.getenv("CULTURE_API_URL") or "").strip().rstrip("/")
    if url.endswith("cultureinfo"):
        url += "/period2"
    return url


def _culture_date(s):
    """'YYYY.MM.DD'·'YYYY-MM-DD'·'YYYYMMDD' → YYYY-MM-DD."""
    s = str(s or "").strip().replace(".", "").replace("-", "")
    return _fmt8(s)


def _culture_rows(resp):
    """응답 형태가 제공처마다 달라 모두 처리:
    - JSON items.item (data.go.kr 공통)
    - XML <perforInfo> (culture.go.kr 공연전시)
    - XML <item><col name="TITLE">값</col>… (한국문화정보원/KCISA '한눈에보는문화정보')"""
    try:
        rows = _rows(resp.json())
        if rows:
            return rows
    except Exception:
        pass
    try:
        root = etree.fromstring(resp.content, parser=etree.XMLParser(
            resolve_entities=False, no_network=True, recover=True))
        nodes = root.xpath("//perforInfo") or root.xpath("//item") or root.xpath("//perforList/*")
        out = []
        for node in nodes:
            d = {}
            cols = node.xpath("./col[@name]")
            if cols:  # KCISA: <col name="...">값</col>
                for c in cols:
                    d[(c.get("name") or "").strip()] = (c.text or "").strip()
            else:
                for ch in node:
                    d[etree.QName(ch).localname] = (ch.text or "").strip()
            if d:
                out.append(d)
        return out
    except Exception:
        return []


def culture_events(progress=None):
    """문화 행사 API(한눈에보는문화정보/KCISA 등) — 국내 전시·공연·행사를 날짜·장소 구조화로.
    CULTURE_API_KEY(디코딩 서비스키) + CULTURE_API_URL(해당 API '요청주소') 둘 다 필요.
    응답이 XML/JSON, KCISA <col name> 형식 모두 처리한다."""
    key = os.getenv("CULTURE_API_KEY")
    url = _culture_url()
    if not key or not url:
        return []
    out = []

    def g(it, *names):
        for n in names:
            v = it.get(n)
            if v:
                return v
        return ""

    def parse_rows(rows):
        for it in rows:
            if not isinstance(it, dict):
                continue
            realm = g(it, "realmName", "DESCRIPTION")
            if realm in _CULTURE_SKIP_REALM:   # 순수 공연류(연극·뮤지컬·음악 등)는 '행사' 탭에서 제외
                continue
            title = g(it, "title", "TITLE", "fstvlNm")
            sd = _culture_date(g(it, "startDate", "eventstartdate", "STRTDATE"))
            ed = _culture_date(g(it, "endDate", "eventenddate", "END_DATE"))
            if not sd:   # KCISA는 PERIOD에 'A ~ B'로 합쳐 올 때가 있음
                parts = [p.strip() for p in str(g(it, "PERIOD", "period")).replace("~", "-#-").split("-#-")]
                if parts and parts[0]:
                    sd = _culture_date(parts[0])
                    ed = ed or (_culture_date(parts[1]) if len(parts) > 1 else None)
            place = g(it, "place", "EVENT_SITE", "SPATIAL_COVERAGE", "addr1", "rdnmadr")
            if title and sd:
                out.append(_item(title, sd, ed or sd, venue=place,
                                 region=(g(it, "area", "SPATIAL_COVERAGE") or (place.split()[0] if place else "")),
                                 url=g(it, "url", "URL", "REFERENCE_IDENTIFIER", "homepageUrl"),
                                 image=g(it, "thumbnail", "IMAGE_OBJECT", "imageObject"),
                                 content=realm, source="문화포털"))

    try:
        today = datetime.date.today()
        frm = (today - datetime.timedelta(days=14)).strftime("%Y%m%d")
        to = (today + datetime.timedelta(days=180)).strftime("%Y%m%d")
        try:
            max_pages = max(1, min(60, int(os.getenv("CULTURE_MAX_PAGES", "20"))))
        except (TypeError, ValueError):
            max_pages = 20
        for page in range(1, max_pages + 1):
            # 응답은 페이지당 기본 10건. numOfRows가 먹으면 한 번에 더 받고, 아니면 페이지로 넘긴다.
            params = {"serviceKey": key, "from": frm, "to": to,
                      "numOfRows": 100, "PageNo": page, "pageNo": page, "rows": 100, "cPage": page}
            resp = requests.get(url, params=params, timeout=(3, 12))
            rows = _culture_rows(resp)
            if not rows:
                break
            parse_rows(rows)
            if len(rows) < 10:   # 마지막 페이지
                break
    except Exception:
        return out
    if progress:
        progress(f"문화행사 {len(out)}건")
    return out


# ------------------------------------ 코엑스 행사 일정 ------------------------------------
COEX_URL = "https://www.coex.co.kr/event/full-schedules/"


def coex_events(progress=None):
    """코엑스 행사 일정(SSR HTML) — 전시·컨벤션·행사를 직접 수집(기사 없이도). 기본 1개월 창.
    COEX_OFF=1이면 비활성, COEX_SCHEDULE_URL로 교체 가능. 구조:
    <a class='BlogEventItem-link'> … .BlogEventItemCont-tit/-date/-hall/-cate, img.BlogEventItemHover-img."""
    if os.getenv("COEX_OFF"):
        return []
    url = os.getenv("COEX_SCHEDULE_URL", COEX_URL)
    out = []
    try:
        import re as _re
        r = requests.get(url, timeout=(3, 12), headers={"User-Agent": "Mozilla/5.0"})
        root = etree.HTML(r.content, etree.HTMLParser(encoding="utf-8"))
        if root is None:
            return out
        seen = set()
        for a in root.xpath("//a[contains(@class,'BlogEventItem-link')]"):
            def first(cls):
                nodes = a.xpath(f".//*[contains(@class,'{cls}')]")
                return "".join(nodes[0].itertext()).strip() if nodes else ""
            title = first("BlogEventItemCont-tit")
            if not title or title in seen:
                continue
            dates = _re.findall(r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}", first("BlogEventItemCont-date"))
            sd = _culture_date(dates[0]) if dates else None
            if not sd:
                continue
            seen.add(title)
            ed = _culture_date(dates[1]) if len(dates) > 1 else sd
            hall = first("BlogEventItemCont-hall")
            cate = first("BlogEventItemCont-cate")
            hrefs = a.get("href") or url
            imgs = a.xpath(".//img[contains(@class,'BlogEventItemHover-img')]/@src")
            out.append(_item(title, sd, ed or sd,
                             venue=("코엑스" + (" " + hall if hall else "")), region="서울",
                             url=hrefs, image=(imgs[0] if imgs else ""),
                             content=cate, source="코엑스"))
    except Exception:
        return out
    if progress:
        progress(f"코엑스 행사 {len(out)}건")
    return out


def collect(progress=None):
    """설정된 구조화 소스를 모두 모아 반환(미설정/실패는 자동 제외)."""
    items = []
    from . import venue_sources
    for fn in (tour_festivals, culture_events, coex_events, venue_sources.collect):
        try:
            items.extend(fn(progress) or [])
        except Exception:
            pass
    return items


# --------------------------------- 진단(운영 전용) ---------------------------------
def _probe(url, params):
    """원응답 상태·행수·첫 항목 키·본문 일부를 돌려준다(키는 노출하지 않음)."""
    try:
        r = requests.get(url, params=params, timeout=(3, 12))
        rows = _culture_rows(r)   # JSON(items.item)·XML(perforInfo) 모두 처리
        first = rows[0] if rows and isinstance(rows[0], dict) else None
        return {"status": r.status_code, "rows": len(rows),
                "first_keys": (list(first.keys())[:40] if first else None),
                "sample": (r.text or "")[:700]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def diagnose():
    """각 소스 설정/응답 진단. 운영에서 /api/admin/eventcheck로 호출(키 비노출)."""
    out = {}
    key = os.getenv("TOURAPI_KEY")
    row = {"configured": bool(key), "parsed": len(tour_festivals()) if key else 0}
    if key:
        start = (datetime.date.today() - datetime.timedelta(days=14)).strftime("%Y%m%d")
        row["raw"] = _probe(os.getenv("TOURAPI_FESTIVAL_URL",
                                      "https://apis.data.go.kr/B551011/KorService2/searchFestival2"),
                            {"serviceKey": key, "MobileOS": "ETC", "MobileApp": "hscope",
                             "_type": "json", "arrange": "A",
                             "eventStartDate": start, "numOfRows": 5, "pageNo": 1})
    out["tour_festivals"] = row
    ckey, curl = os.getenv("CULTURE_API_KEY"), _culture_url()
    crow = {"configured": bool(ckey and curl), "key_set": bool(ckey), "url_set": bool(curl),
            "resolved_url": curl, "parsed": len(culture_events()) if (ckey and curl) else 0}
    if ckey and curl:
        today = datetime.date.today()
        crow["raw"] = _probe(curl, {"serviceKey": ckey,
                                    "from": (today - datetime.timedelta(days=14)).strftime("%Y%m%d"),
                                    "to": (today + datetime.timedelta(days=180)).strftime("%Y%m%d"),
                                    "rows": 5, "cPage": 1, "numOfRows": 5, "pageNo": 1})
    out["culture_events"] = crow
    # 코엑스 행사 일정 페이지 구조 확인(어댑터 붙이기 전 SSR/SPA·HTML 구조 파악용)
    import re as _re
    try:
        cx = os.getenv("COEX_SCHEDULE_URL", "https://www.coex.co.kr/event/full-schedules/")
        r = requests.get(cx, timeout=(3, 12), headers={"User-Agent": "Mozilla/5.0"})
        txt = r.text or ""
        ms = list(_re.finditer(r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}", txt))
        pick = ms[min(20, len(ms) - 1)] if ms else None
        # 목록 구조를 찾는 여러 단서: 상세링크 주변 / 목록 컨테이너(List) / 중간 청크
        link_m = _re.search(r'<a[^>]+href="[^"]*(?:detail|exhibition|event|idx=)[^"]*"[^>]*>', txt, _re.I)
        cont_m = _re.search(r'<[^>]+class="[^"]*(?:List|Schedule|Event|Exhibition)[^"]*"', txt)
        out["coex_probe"] = {
            "status": r.status_code, "bytes": len(r.content), "date_like": len(ms),
            "parsed": len(coex_events()),
            "spa_hint": ("__NEXT_DATA__" in txt or "/_next/" in txt or "id=\"root\"" in txt or "ng-app" in txt),
            "row_sample": (txt[max(0, pick.start() - 700): pick.start() + 700] if pick else ""),
        }
    except Exception as e:  # noqa: BLE001
        out["coex_probe"] = {"error": f"{type(e).__name__}: {e}"}
    from . import venue_sources
    out['venue_schedules'] = venue_sources.diagnose()
    return out
