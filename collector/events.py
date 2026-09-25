"""행사일정(AI 행사) 수집기.

구글 뉴스에서 'AI 관련 + 행사' 후보 기사를 모은 뒤,
아래 규칙으로 '국내에서 실제 참여·신청 가능한 미래 AI 행사'만 남긴다.
  AI 관련 = TRUE AND 행사 관련 = TRUE AND 국내 행사 = TRUE
  AND 행사 종료 = FALSE AND 중복 = FALSE

날짜·장소는 기사 제목/본문에서 규칙(정규식·키워드)으로 추출하므로
100% 정확하진 않다(뉴스 자유서술 기반). 캘린더 표시는 날짜가 잡힌 행사만,
앨범은 날짜 미상이어도 노출한다. 조건은 상수로 빼서 조정하기 쉽게 했다.
"""
import datetime
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from . import extractor, fetcher, google_news

# 국내 행사 후보 검색어(검색어 자체가 1차 조건 → keyword_filter=False).
# 국내 전반 행사를 폭넓게 모으려고 '행사장·지역 앵커 + 일반 행사어'로 확장.
EVENT_CATEGORIES = {
    "행사": [
        "(컨퍼런스 OR 콘퍼런스 OR 세미나 OR 포럼 OR 박람회 OR 전시회 OR 엑스포 OR 서밋) 개최",
        "(코엑스 OR 킨텍스 OR 벡스코 OR 엑스코 OR 송도컨벤시아 OR 대전컨벤션 OR aT센터) (개최 OR 박람회 OR 전시회 OR 컨퍼런스)",
        "(서울 OR 부산 OR 대구 OR 인천 OR 대전 OR 광주 OR 경기 OR 제주) (박람회 OR 전시회 OR 포럼 OR 컨퍼런스 OR 페어) 개최",
        "(밋업 OR 해커톤 OR 데모데이 OR 페어 OR 콘퍼런스 OR 심포지엄 OR 워크숍) (개최 OR 참가신청 OR 사전등록)",
        "(스타트업 OR 테크 OR 산업 OR 과학 OR 문화 OR 예술 OR 취업 OR 채용) (컨퍼런스 OR 포럼 OR 박람회 OR 페스티벌) 개최",
        "(AI OR 인공지능 OR 디지털 OR 반도체 OR 바이오 OR 게임) (컨퍼런스 OR 세미나 OR 포럼 OR 엑스포 OR 박람회) 개최",
        # 확장: '개최 예정/열린다/개막/참가신청' 표현 + 연도 앵커로 후보 확대
        "(컨퍼런스 OR 세미나 OR 포럼 OR 박람회 OR 전시회 OR 엑스포) (개최 예정 OR 열린다 OR 개막)",
        "(참가 신청 OR 사전 등록 OR 참가자 모집) (컨퍼런스 OR 세미나 OR 포럼 OR 박람회 OR 엑스포 OR 웨비나)",
        "(수원컨벤션 OR 김대중컨벤션 OR 제주국제컨벤션 OR 누리꿈스퀘어 OR DDP) (개최 OR 박람회 OR 컨퍼런스 OR 전시회)",
        "(2026 OR 2027) (국제 OR 대한민국 OR 코리아) (컨퍼런스 OR 박람회 OR 엑스포 OR 포럼 OR 전시회) 개최",
        "(의료 OR 헬스케어 OR 금융 OR 물류 OR 뷰티 OR 식품 OR 교육 OR 관광 OR 건설 OR 로봇 OR 모빌리티) (박람회 OR 전시회 OR 컨퍼런스 OR 포럼) 개최",
        "(채용박람회 OR 취업박람회 OR 창업 OR 벤처 OR 투자) (박람회 OR 포럼 OR 데모데이 OR 페어) 개최",
    ],
}

# 행사 관련성 판정용 키워드(제목·본문에 하나 이상)
EVENT_WORDS = ["컨퍼런스", "콘퍼런스", "세미나", "포럼", "박람회", "전시회", "엑스포", "expo",
               "밋업", "해커톤", "웨비나", "심포지엄", "워크숍", "워크샵", "데모데이",
               "콘퍼런스", "컨벤션", "페어", "summit", "conference", "forum"]
# AI 관련성 판정용
AI_WORDS = ["ai", "인공지능", "생성형", "llm", "머신러닝", "딥러닝", "챗gpt", "gpt",
            "생성ai", "genai", "에이아이"]

# 국내 지역명(광역 + 주요 시·군)
DOMESTIC_REGIONS = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    "수원", "성남", "고양", "용인", "부천", "안산", "안양", "남양주", "화성", "평택", "의정부",
    "청주", "천안", "아산", "전주", "포항", "구미", "김해", "창원", "진주", "원주", "춘천",
    "강릉", "목포", "여수", "순천", "군산", "경주", "제천", "충주", "당진", "서산", "광명",
    "판교", "일산", "송도", "마곡", "상암", "양재", "대덕", "오송",
]
# 국내 주요 행사장·컨벤션(영문 표기 포함)
DOMESTIC_VENUES = [
    "코엑스", "coex", "킨텍스", "kintex", "벡스코", "bexco", "엑스코", "exco",
    "ddp", "동대문디자인플라자", "at센터", "aT센터", "세텍", "setec",
    "송도컨벤시아", "convensia", "대전컨벤션", "dcc", "김대중컨벤션", "kdjcenter",
    "수원컨벤션", "제주국제컨벤션", "icc제주", "누리꿈스퀘어", "세종대", "삼성동",
    "서울무역전시", "aT", "그랜드워커힐", "코트야드", "노들섬", "동대문",
]
# 해외 개최를 강하게 시사하는 토큰(있으면 제외) — 해외 컨퍼런스/도시/행사명
OVERSEAS_TOKENS = [
    "ces", "mwc", "gtc", "neurips", "iclr", "cvpr", "computex", "슬러시", "웹서밋",
    "라스베이거스", "라스베가스", "베가스", "바르셀로나", "도쿄", "오사카", "요코하마",
    "베이징", "상하이", "선전", "광저우", "싱가포르", "샌프란시스코", "실리콘밸리",
    "산호세", "시애틀", "뉴욕", "보스턴", "런던", "파리", "베를린", "뮌헨", "하노버",
    "암스테르담", "두바이", "아부다비", "타이베이", "홍콩", "마카오", "방콕",
    "쿠알라룸푸르", "호치민", "자카르타", "뭄바이", "벵갈루루", "토론토", "시드니",
]
# 해외 국가(장소 근거가 국내로 없으면 제외)
OVERSEAS_COUNTRIES = [
    "미국", "일본", "중국", "대만", "영국", "프랑스", "독일", "네덜란드", "스페인",
    "베트남", "인도", "캐나다", "호주", "아랍에미리트", "사우디", "브라질",
    "이탈리아", "스위스", "핀란드", "스웨덴", "태국", "말레이시아", "인도네시아",
]
# 온라인 행사 신호
ONLINE_TOKENS = ["웨비나", "온라인", "비대면", "라이브", "zoom", "줌 ", "유튜브 라이브"]
# 국내 주최·주관 신호(온라인 행사의 국내 인정 근거)
DOMESTIC_ORG_HINTS = [
    "과학기술정보통신부", "과기정통부", "정보통신산업진흥원", "nipa", "한국지능정보사회진흥원",
    "nia", "정보통신기획평가원", "iitp", "한국정보통신기술협회", "tta", "etri",
    "한국전자통신연구원", "중소벤처기업부", "산업통상자원부", "서울시", "경기도",
    "진흥원", "협회", "재단", "연구원", "대학교", "kaist", "카이스트", "한국",
]
# 제목에 있으면 행사가 아닌(제외) 노이즈
TITLE_EXCLUDE = ["채용", "수강생 모집", "공모전", "부고", "인사", "칼럼", "기고", "사설"]
# 이미 끝난(성료) 행사 회고 기사 신호 → 제외(아직 종료 안 된 행사만 수집)
PAST_EVENT_TOKENS = [
    "성료", "성황리", "폐막", "막을 내렸", "막을 내린", "마무리됐", "마무리했",
    "마쳤", "종료됐", "종료된", "열렸다", "개최됐", "개최했", "진행됐", "진행했",
    "성공적으로 마", "성황리에", "뜨거운 관심 속", "참관객", "관람객", "성료했",
    "참가했", "참관했", "참석했", "다녀왔", "선보였", "전시했",
]


def _has(text_low, words):
    return any(w.lower() in text_low for w in words)


def _first_hit(text, words):
    low = text.lower()
    for w in words:
        if w.lower() in low:
            return w
    return ""


# 해외 참가/개최 기사임을 시사하는 문맥어(해외 국가와 함께 나오면 제외)
OVERSEAS_CONTEXT = ["참가", "참관", "참석", "현지", "해외", "개최지", "방문", "진출", "출장", "특파원"]


def is_domestic(title, content):
    """국내 행사 판별. 소스가 한국 뉴스(gl=KR)라 기본은 '국내'로 보고, 해외 신호가 있을 때만 제외."""
    text = f"{title}  {content}"
    low = text.lower()
    # 해외 행사명·도시(CES/MWC/라스베이거스 등) 명시 → 제외(참가 기사 포함)
    if _has(low, [t.lower() for t in OVERSEAS_TOKENS]):
        return False
    venue = _has(low, [v.lower() for v in DOMESTIC_VENUES])
    region = any(r in text for r in DOMESTIC_REGIONS)
    # 해외 국가 + 참가/현지 등 문맥 + 국내 장소 근거 없음 → 해외 참가 기사로 제외
    if any(c in text for c in OVERSEAS_COUNTRIES) and any(w in text for w in OVERSEAS_CONTEXT):
        if not (venue or region):
            return False
    return True   # 기본: 국내


def _mk(y, m, d):
    try:
        return datetime.date(y, m, d)
    except ValueError:
        return None


def _infer_year(m, d, anchor):
    """연도 없는 날짜의 연도 추정: '기사 작성일(anchor)' 기준으로 가장 가까운 연도.
    (오늘 기준으로 하면 과거 기사의 날짜를 미래로 잘못 밀어버림 → 작성일 기준이 정확)"""
    cand = _mk(anchor.year, m, d)
    if not cand:
        return _mk(anchor.year + 1, m, d)
    # 작성일보다 두 달 이상 전이면(예: 12월 기사의 '1월') 다음 해로
    if cand < anchor - datetime.timedelta(days=60):
        return _mk(anchor.year + 1, m, d) or cand
    # 작성일보다 한참(400일 초과) 뒤면 전년으로 보정
    if cand > anchor + datetime.timedelta(days=400):
        return _mk(anchor.year - 1, m, d) or cand
    return cand


def extract_dates(title, content, today=None, pub=None):
    """기사에서 (시작, 종료) 날짜(ISO 'YYYY-MM-DD')를 추출. 없으면 (None, None).
    한국어 자유서술 패턴 위주. 연도 없는 날짜는 기사 작성일(pub) 기준으로 연도 추정."""
    today = today or datetime.date.today()
    anchor = pub or today                       # 연도 추정 기준(작성일 우선)
    text = f"{title}  {content}"

    # 1) YYYY년 M월 D일 [~ (YYYY년)? (M월)? D일]
    m = re.search(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
                  r"(?:\s*(?:[~\-∼]|부터)\s*(?:(20\d{2})\s*년\s*)?(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일)?", text)
    if m:
        sy, sm, sd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        start = _mk(sy, sm, sd)
        end = start
        if m.group(6):
            ey = int(m.group(4)) if m.group(4) else sy
            em = int(m.group(5)) if m.group(5) else sm
            end = _mk(ey, em, int(m.group(6))) or start
        if start:
            return start.isoformat(), (end or start).isoformat()

    # 2) M월 D일 [~/부터 (M월)? D일 (까지)?]
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일"
                  r"(?:\s*(?:[~\-∼]|부터)\s*(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일)?", text)
    if m:
        sm, sd = int(m.group(1)), int(m.group(2))
        start = _infer_year(sm, sd, anchor)
        end = start
        if m.group(4):
            em = int(m.group(3)) if m.group(3) else sm
            ed = int(m.group(4))
            if start:
                ey = start.year + (1 if em < sm else 0)
                end = _mk(ey, em, ed) or start
        if start:
            return start.isoformat(), (end or start).isoformat()

    # 3) YYYY.MM.DD / YYYY-MM-DD [~ (MM.DD | DD)]
    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})"
                  r"(?:\s*[~\-∼]\s*(?:(\d{1,2})[.\-/])?(\d{1,2}))?", text)
    if m:
        sy, sm, sd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        start = _mk(sy, sm, sd)
        end = start
        if m.group(5):
            em = int(m.group(4)) if m.group(4) else sm
            end = _mk(sy, em, int(m.group(5))) or start
        if start:
            return start.isoformat(), (end or start).isoformat()

    # 4) '일시/기간/일정' 라벨 뒤 숫자 날짜: 일시 11.3 / 기간 11/3~11/5 (연도는 작성일 기준)
    m = re.search(r"(?:일시|기간|일정|날짜|개최일)\s*[:：]?\s*(?:20\d{2}[.\-/])?"
                  r"(\d{1,2})[.\-/](\d{1,2})(?:\s*[~\-∼]\s*(?:(\d{1,2})[.\-/])?(\d{1,2}))?", text)
    if m:
        sm, sd = int(m.group(1)), int(m.group(2))
        start = _infer_year(sm, sd, anchor)
        end = start
        if m.group(4) and start:
            em = int(m.group(3)) if m.group(3) else sm
            ey = start.year + (1 if em < sm else 0)
            end = _mk(ey, em, int(m.group(4))) or start
        if start:
            return start.isoformat(), (end or start).isoformat()

    return None, None


def _norm_title(t):
    return re.sub(r"[^0-9a-z가-힣]", "", (t or "").lower())[:24]


EVENT_BODY_MAX = int(os.environ.get("EVENT_BODY_MAX", "400"))  # 본문 조회 상한(날짜 없는 후보만)


def _fetch_body(entry):
    """구글 링크를 원문으로 복원해 본문 전체 텍스트를 가져온다.
    반환: (본문텍스트, 최종URL, 대표이미지) — 실패 시 ('', None, None)."""
    try:
        real = google_news._decode_google_url(entry.get("url", "")) or entry.get("source_url") or entry.get("url")
        if not real:
            return "", None, None
        resp = fetcher.get(real, retries=0, timeout=google_news.NEWS_TIMEOUT)
        final = resp.url or ""
        if "news.google." in final or "consent.google" in final:
            return "", None, None
        soup = BeautifulSoup(resp.text, "lxml")
        art = extractor.extract_article(soup, final)
        return (art.get("content") or ""), (final or None), extractor.extract_image(soup)
    except Exception:  # noqa: BLE001
        return "", None, None


def crawl(max_workers=24, max_items=0, progress=None, known_urls=None, days=None):
    """국내 행사 수집. google_news 후보 → 필터 + 날짜추출(요약에 없으면 본문 조회).
    반환: 행사 dict 리스트(venue/region/start_date/end_date 포함)."""
    progress = progress or (lambda m: None)
    today = datetime.date.today()
    cand = google_news.crawl(
        max_workers=max_workers, max_items=0, progress=progress,
        known_urls=None, days=(int(days) if days else 180),
        categories=EVENT_CATEGORIES, keyword_filter=False,
    )
    progress(f"행사 후보 {len(cand)}건 판별 중…")

    def _pub(e):
        pubs = (e.get("published_at") or "")[:10]
        try:
            return datetime.date.fromisoformat(pubs)
        except ValueError:
            return None

    # 1차(요약 기준): 제목노이즈·성료어 제외 + 행사 관련성. 요약에 날짜가 있으면 본문 불필요.
    pool, need_body = [], []
    for e in cand:
        title = e.get("title") or ""
        snip = e.get("content") or ""
        tc = f"{title} {snip}".lower()
        if any(x in title for x in TITLE_EXCLUDE):
            continue
        if any(x in tc for x in [p.lower() for p in PAST_EVENT_TOKENS]):
            continue
        if not _has(tc, [w.lower() for w in EVENT_WORDS]):
            continue
        e["_pub"] = _pub(e)
        e["_snip_start"] = extract_dates(title, snip, today, e["_pub"])[0]
        pool.append(e)
        if not e["_snip_start"]:
            need_body.append(e)

    # 날짜가 요약에 없는 후보만 본문 조회(최신순 우선, 상한)
    need_body.sort(key=lambda e: (e.get("_pub") or datetime.date.min), reverse=True)
    fetchset = need_body[:EVENT_BODY_MAX]
    bodies = {}
    if fetchset:
        progress(f"본문 열어 일정 확인 0/{len(fetchset)}")
        with ThreadPoolExecutor(max_workers=min(max_workers, 12)) as tp:
            futs = {tp.submit(_fetch_body, e): e for e in fetchset}
            done = 0
            for fut in as_completed(futs):
                e = futs[fut]
                bodies[e.get("url")] = fut.result()
                done += 1
                if done % 20 == 0 or done == len(fetchset):
                    progress(f"본문 확인 {done}/{len(fetchset)}")

    out, seen = [], set()
    for e in pool:
        title = e.get("title") or ""
        snip = e.get("content") or ""
        body, final, img = bodies.get(e.get("url"), ("", None, None))
        fulltext = body if len(body) > len(snip) else snip
        low = f"{title} {fulltext}".lower()
        if any(x in low for x in [p.lower() for p in PAST_EVENT_TOKENS]):
            continue
        if not is_domestic(title, fulltext):
            continue
        pub = e.get("_pub")
        start = e.get("_snip_start")
        end = None
        if start:
            _, end = extract_dates(title, snip, today, pub)
        else:
            start, end = extract_dates(title, fulltext, today, pub)
        if not start:
            continue
        if pub and pub < today - datetime.timedelta(days=365):
            continue
        try:
            if datetime.date.fromisoformat(end or start) < today:
                continue
        except ValueError:
            continue
        key = _norm_title(title) + "|" + start
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": title,
            "published_at": e.get("published_at"),
            "author": e.get("author"),
            "content": extractor.summarize(fulltext) if body else snip,
            "url": e.get("url"),
            "source_url": final or e.get("source_url"),
            "image_url": img or e.get("image_url"),
            "venue": _first_hit(f"{title} {fulltext}", DOMESTIC_VENUES),
            "region": _first_hit(f"{title} {fulltext}", DOMESTIC_REGIONS),
            "start_date": start,
            "end_date": end,
        })
        if max_items and len(out) >= max_items:
            break
    progress(f"국내 행사 {len(out)}건")
    return out
