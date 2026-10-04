"""점심 맛집 도메인: 카카오 로컬 API 수집 어댑터 + 카테고리 정규화 + 거리계산.

- 상세 정보(메뉴·영업시간·사진·평점)는 수집/저장하지 않고 카카오맵 링크로 랜딩한다.
- 우리가 확보하는 건 최소 신뢰 데이터: 상호·카테고리·주소·좌표·전화·place_url.
- 이용자 평점/후기는 우리 앱에 직접 누적(외부 평점 아님).
- 특정 서비스 종속 방지를 위해 수집은 어댑터로 분리(현재 kakao). 키는 KAKAO_REST_KEY.
"""
import math
import os

from . import fetcher

KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"

# 카카오 category_name(예: "음식점 > 한식 > 국밥") → 서비스 내부 카테고리
CATEGORY_RULES = [
    ("한식", ["한식", "백반", "국밥", "찌개", "탕", "해장", "곰탕", "설렁탕", "감자탕", "쌈밥", "한정식", "죽"]),
    ("고기", ["고기", "구이", "삼겹", "갈비", "곱창", "막창", "정육", "바베큐", "스테이크"]),
    ("면요리", ["국수", "칼국수", "냉면", "면", "우동", "라멘", "라면", "소바", "메밀"]),
    ("분식", ["분식", "떡볶이", "김밥", "튀김", "순대"]),
    ("중식", ["중식", "중국", "짜장", "짬뽕", "마라"]),
    ("일식", ["일식", "일본", "초밥", "스시", "회", "사시미", "덮밥", "돈부리", "우나기"]),
    ("돈까스", ["돈까스", "돈가스", "카츠"]),
    ("양식", ["양식", "이탈리", "파스타", "피자", "스파게티", "브런치", "스테이크하우스"]),
    ("아시아음식", ["아시아", "베트남", "쌀국수", "태국", "인도", "커리", "포"]),
    ("생선/해산물", ["해산물", "생선", "조개", "회", "물회", "생선구이", "해물"]),
    ("샐러드/건강식", ["샐러드", "샌드위치", "건강", "포케", "비건", "채식"]),
    ("패스트푸드", ["패스트푸드", "버거", "햄버거", "치킨", "핫도그"]),
    ("카페/디저트", ["카페", "디저트", "베이커리", "제과", "빵"]),
]


def normalize_category(kakao_category_name):
    """카카오 category_name에서 내부 카테고리 1개를 뽑는다. 못 찾으면 '기타'.
    ⚠️ '음식점>한식>국수'처럼 상위에 '한식'이 있으면 국수(면요리)가 한식으로 잘못 잡히던 문제 →
    가장 구체적인 '마지막 토큰'을 먼저 판정하고, 없으면 전체 문자열로 폴백한다."""
    raw = kakao_category_name or ""
    parts = [p.strip() for p in raw.split(">") if p.strip()]
    last = parts[-1] if parts else ""
    for target in (last, raw):          # 구체적(마지막) → 전체 순
        c = target.replace(" ", "")
        if not c:
            continue
        for name, kws in CATEGORY_RULES:
            if any(kw.replace(" ", "") in c for kw in kws):
                return name
    return "기타"


def sub_category(kakao_category_name):
    """세부 카테고리(원본 마지막 토큰). 예: '음식점 > 한식 > 국밥' → '국밥'."""
    parts = [p.strip() for p in (kakao_category_name or "").split(">") if p.strip()]
    return parts[-1] if parts else ""


def haversine_m(lat1, lng1, lat2, lng2):
    """두 좌표(WGS84) 사이 거리(미터)."""
    try:
        r = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lng2 - lng1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(min(1.0, math.sqrt(a)))
    except Exception:  # noqa: BLE001
        return None


def walk_minutes(dist_m):
    """예상 도보시간(분). 보행 속도 ~67m/분(4km/h) 가정. 없으면 None."""
    if dist_m is None:
        return None
    return max(1, round(dist_m / 67.0))


def _key():
    return os.environ.get("KAKAO_REST_KEY", "").strip()


def has_key():
    return bool(_key())


def _headers():
    return {"Authorization": "KakaoAK " + _key()}


# 마지막 지오코딩 진단(관리자 오류 메시지·로그용). reason: ok|no_key|unauthorized|no_result|error
LAST_GEO = {}


def geocode(query):
    """주소/장소명 → (lat, lng). 주소검색 → 키워드검색 순. 실패 시 None.
    실패 원인은 LAST_GEO에 기록(키 거부 401/403 vs 결과 없음 구분)."""
    LAST_GEO.clear()
    if not has_key() or not query:
        LAST_GEO.update(reason="no_key")
        return None
    attempts = []
    # 주소검색 → 키워드검색 순으로 시도(주소가 아니어도 장소명으로 잡히게)
    for label, url, params in (
        ("address", KAKAO_ADDRESS_URL, {"query": query}),
        ("keyword", KAKAO_KEYWORD_URL, {"query": query, "size": 1}),
    ):
        try:
            r = fetcher.get(url, params=params, headers=_headers(),
                            retries=1, timeout=8, raise_status=False)
            code = r.status_code
            try:
                j = r.json() or {}
            except Exception:  # noqa: BLE001
                j = {}
            docs = j.get("documents") or []
            attempts.append(f"{label}={code}/{len(docs)}")
            if code in (401, 403):  # 키가 거부됨 → 더 시도해도 동일
                body = ""
                try:
                    body = (r.text or "")[:200]
                except Exception:  # noqa: BLE001
                    pass
                LAST_GEO.update(reason="unauthorized", status=code, attempts=attempts, body=body)
                return None
            if docs:
                d = docs[0]
                LAST_GEO.update(reason="ok", via=label, attempts=attempts)
                return (float(d["y"]), float(d["x"]))  # y=lat, x=lng
        except Exception as e:  # noqa: BLE001
            attempts.append(f"{label}=err")
            LAST_GEO.update(error=str(e)[:120])
    LAST_GEO.setdefault("reason", "no_result")
    LAST_GEO["attempts"] = attempts
    return None


def diag():
    """관리자 진단용: 카카오 키 상태 + 라이브 테스트 호출 1회 결과. 키 값은 마스킹."""
    raw = os.environ.get("KAKAO_REST_KEY", "")
    key = _key()
    # 원본 값에 공백/따옴표/줄바꿈이 섞였는지(흔한 실수) 감지
    dirty = (raw != raw.strip()) or (" " in raw.strip()) or any(c in raw for c in '"\'\n\r\t')
    out = {"has_key": bool(key), "key_len": len(key),
           "key_head": (key[:4] + "…") if key else "", "key_has_space": bool(dirty)}
    if not key:
        out["hint"] = "KAKAO_REST_KEY가 비어 있어요(Render 환경변수 확인)."
        return out
    try:
        r = fetcher.get(KAKAO_KEYWORD_URL, headers=_headers(), retries=0, timeout=8,
                        raise_status=False, params={"query": "김밥", "x": 127.0, "y": 37.5,
                                                    "radius": 500, "size": 1})
        out["status"] = r.status_code
        try:
            out["body"] = (r.text or "")[:300]
        except Exception:  # noqa: BLE001
            out["body"] = ""
        if r.status_code == 200:
            out["result"] = "성공 — 키 정상"
        elif r.status_code in (401, 403):
            out["result"] = "거부(401/403) — 키/권한/허용IP 문제"
        elif r.status_code == 429:
            out["result"] = "한도초과(429) — 쿼터 소진"
        else:
            out["result"] = f"기타 오류({r.status_code})"
    except Exception as e:  # noqa: BLE001
        out["status"] = "conn_error"
        out["result"] = f"연결 실패: {str(e)[:150]}"
    return out


# 점심 식당 수집용 검색어(카테고리 다양성 확보). 각 검색어를 좌표 반경으로 조회.
SEARCH_TERMS = ["맛집", "한식", "백반", "국밥", "김치찌개", "칼국수", "국수", "분식", "김밥",
                "중식", "짜장면", "일식", "초밥", "돈까스", "덮밥", "고기", "쌀국수",
                "파스타", "피자", "버거", "샐러드", "카페"]


def collect(lat, lng, radius_m, progress=None, max_terms=0):
    """좌표 기준 반경 내 음식점 수집(카카오 키워드검색). place_id로 중복 제거한 리스트 반환.
    각 원소: {place_id, name, category, cat_norm, sub_cat, address, road_address,
              lat, lng, phone, place_url, dist_m}. 키 없으면 빈 리스트."""
    progress = progress or (lambda m: None)
    if not has_key():
        progress("카카오 키가 없어요(KAKAO_REST_KEY 미설정) — 수동 등록만 가능")
        return []
    radius = max(1, min(int(radius_m or 500), 20000))  # 카카오 최대 20km
    terms = SEARCH_TERMS[:max_terms] if max_terms else SEARCH_TERMS
    seen, out = set(), []
    for i, term in enumerate(terms):
        page = 1
        while page <= 3:  # 페이지당 15개, 최대 45개/검색어
            try:
                r = fetcher.get(KAKAO_KEYWORD_URL, headers=_headers(), retries=1, timeout=8,
                                raise_status=False, params={
                                    "query": term, "x": lng, "y": lat, "radius": radius,
                                    "page": page, "size": 15, "sort": "distance",
                                    "category_group_code": "FD6",  # 음식점
                                })
                j = r.json() or {}
            except Exception as e:  # noqa: BLE001
                progress(f"수집 오류({term}): {e}")
                break
            docs = j.get("documents") or []
            for d in docs:
                pid = d.get("id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                try:
                    rlat, rlng = float(d["y"]), float(d["x"])
                except (KeyError, TypeError, ValueError):
                    continue
                dist = haversine_m(lat, lng, rlat, rlng)
                if dist is not None and dist > radius:
                    continue
                cat_name = d.get("category_name") or ""
                out.append({
                    "place_id": pid,
                    "name": d.get("place_name") or "",
                    "category": cat_name,
                    "cat_norm": normalize_category(cat_name),
                    "sub_cat": sub_category(cat_name),
                    "address": d.get("address_name") or "",
                    "road_address": d.get("road_address_name") or "",
                    "lat": rlat, "lng": rlng,
                    "phone": d.get("phone") or "",
                    "place_url": d.get("place_url") or "",
                    "dist_m": round(dist) if dist is not None else None,
                })
            if (j.get("meta") or {}).get("is_end", True):
                break
            page += 1
        if (i + 1) % 5 == 0 or i == len(terms) - 1:
            progress(f"카카오 수집 {i + 1}/{len(terms)} 검색어 · 누적 {len(out)}곳")
    progress(f"수집 완료 · {len(out)}곳")
    return out
