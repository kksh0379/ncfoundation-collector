"""엔씨문화재단 콜렉터 - 모바일 웹 (Flask).

탭1: 네이버 뉴스 (엔씨문화재단/NC문화재단)
탭2: 재단 서비스 게시판

수동 실행 방식: 화면의 "수집 실행" 버튼을 누르면 해당 탭의 크롤러가 동작한다.
"""
import json
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request, session

from collector import boards, db, dedup, fetcher, google_news, social

app = Flask(__name__)
# 초안 단계: 브라우저가 옛 JS/CSS를 캐시해 혼란을 주지 않도록 정적파일 캐시를 끈다.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = os.environ.get("SECRET_KEY", "ncfoundation-collector-secret-key")

KST = timezone(timedelta(hours=9))  # 마지막 수집 일시는 서버에서 KST로 기록

# 관리자 로그인: 로그인해야 상태확인/수집 실행이 보이고 동작한다(뷰어는 조회만).
ADMIN_PW = os.environ.get("ADMIN_PW", "rlatkdghk12#")


def _admin_ok():
    return bool(session.get("admin"))


def _now_kst():
    return datetime.now(KST).strftime("%Y.%m.%d %H:%M:%S")


# DB 초기화는 '지연(lazy)'으로 한다. import(부팅) 시점에 Neon에 동기 접속하면,
# Neon 무료가 자고 있을 때(cold start) 연결이 최대 수십 초~2분 걸려 gunicorn 워커가
# 강제 종료되고 → Render 배포가 통째로 실패(이전 버전으로 남음)한다. 그래서 부팅은
# DB와 무관하게 즉시 끝내고, 첫 요청 때 한 번만 테이블을 준비한다.
_db_ready = False
_db_lock = threading.Lock()
_db_last_try = 0.0
_DB_RETRY_COOLDOWN = 30  # 초: DB 연결 실패 시 이 시간 동안은 재시도 안 함(요청을 매번 막지 않게)


def _ensure_db(force=False):
    """DB 테이블 준비를 보장한다.
    - force=False(기본, 웹 조회용): '요청을 절대 오래 막지 않는다'. Neon이 자고 있으면
      연결이 수십 초 걸리는데 매 요청마다 시도하면 스레드가 막혀 502가 난다. → 실패하면
      쿨다운 동안 그냥 넘어가고 즉시 반환.
    - force=True(수집/초기화용): 쿨다운 무시하고 실제로 접속을 '기다려' Neon을 깨운다.
      (백그라운드 작업/관리자 동작이라 몇 초~수십 초 기다려도 됨.)
    반환값: 준비됐으면 True."""
    global _db_ready, _db_last_try
    if _db_ready:
        return True
    if not force and time.time() - _db_last_try < _DB_RETRY_COOLDOWN:
        return False  # 최근에 실패 → 지금은 시도 안 함(빠르게 반환)
    # force면 락을 '기다려서라도' 잡는다(진짜 깨워야 하므로). 아니면 안 막고 넘어감.
    if not _db_lock.acquire(blocking=force):
        return False
    try:
        if _db_ready:
            return True
        _db_last_try = time.time()
        db.init_db()
        _db_ready = True
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[startup] init_db 실패: {e}", flush=True)
        return False
    finally:
        _db_lock.release()


@app.get("/healthz")
def healthz():
    """킵얼라이브용 초경량 엔드포인트(DB 접속 안 함). 외부 크론이 이걸 주기적으로
    호출하면 Render 무료 앱이 잠들지 않아 방문자가 cold start를 안 겪는다."""
    return "ok", 200


@app.get("/api/me")
def me():
    return jsonify({"admin": _admin_ok()})


@app.get("/api/notes")
def notes():
    """개발노트/패치내역 문서 반환(관리자 전용)."""
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    base = os.path.dirname(os.path.abspath(__file__))

    def _read(name):
        try:
            with open(os.path.join(base, name), encoding="utf-8") as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            return f"({name} 읽기 실패: {e})"

    return jsonify({"devnote": _read("DEVNOTE.md"), "changelog": _read("CHANGELOG.md")})


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    if data.get("pw") == ADMIN_PW:
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "비밀번호가 올바르지 않습니다."}), 401


@app.post("/api/logout")
def logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.post("/api/admin/purge")
def admin_purge():
    """수집 데이터(DB)를 비운다(관리자 전용).
    body: {"scope": "all"|"news"|"boards"|"social", "recollect": true|false}
    recollect=true면 비운 뒤 즉시 재수집 시작(뉴스는 days 파라미터, 기본 RECENT_DAYS=2년)."""
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db(force=True):  # 실제로 접속을 기다려 Neon 깨우기(안 되면 JSON 에러)
        return jsonify({"ok": False, "error": "DB에 연결할 수 없어요(Neon 깨는 중일 수 있음). 20초 뒤 다시 시도해 주세요."}), 503
    data = request.get_json(silent=True) or {}
    scope = data.get("scope", "all")
    tables = ["news", "boards", "social"] if scope == "all" else [scope]
    try:
        deleted = db.clear_tables(tables)
    except Exception as e:  # noqa: BLE001
        print(f"[purge] 삭제 실패: {e}", flush=True)
        return jsonify({"ok": False, "error": f"삭제 실패: {e}"}), 500
    started = []
    if data.get("recollect"):
        days = data.get("days")
        for group in ("news", "boards", "social"):
            if group in tables and _start_job(group, days=days if group == "news" else None):
                started.append(group)
    return jsonify({"ok": True, "deleted": deleted, "recollect_started": started})


@app.get("/api/dbcheck")
def dbcheck():
    """DB 연결을 직접 시도해 실제 에러 원문을 반환(진단용).
    비밀번호는 마스킹되고 연결 성공/실패·에러 종류만 나오므로 로그인 없이도 열 수 있게 둔다
    (DB가 죽으면 로그인 자체가 애매할 수 있어 진단 접근성을 우선)."""
    return jsonify(db.diagnose())


@app.get("/api/runlog")
def runlog():
    """수집 실행 로그(관리자 전용): 언제·무엇·성공/실패."""
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db():
        return jsonify([])
    try:
        return jsonify(db.list_run_log())
    except Exception as e:  # noqa: BLE001
        print(f"[runlog] 조회 실패: {e}", flush=True)
        return jsonify([])


@app.get("/api/peek")
def peek():
    """지정 URL을 한 번만 받아 구조를 가볍게 요약(진단용). /api/inspect보다 안전(단일 요청)."""
    import re as _re
    url = request.args.get("url", "").strip()
    if not url.startswith("http"):
        return jsonify({"error": "url 파라미터 필요"}), 400
    out = {"url": url}
    try:
        resp = fetcher.get(url, retries=0, timeout=12)
        html = resp.text or ""
        out["status"] = resp.status_code
        out["final_url"] = resp.url
        out["len"] = len(html)
        out["content_type"] = resp.headers.get("content-type", "")
        # /community/all/숫자 형태 링크
        links = _re.findall(r'href=["\'](/community/all/\d+[^"\']*)["\']', html)
        links += _re.findall(r'href=["\'](https?://[^"\']*?/community/all/\d+[^"\']*)["\']', html)
        out["community_links_count"] = len(links)
        out["community_links_sample"] = list(dict.fromkeys(links))[:15]
        out["has_next_data"] = "__NEXT_DATA__" in html
        out["has_nuxt"] = "__NUXT__" in html
        out["json_script_count"] = len(_re.findall(r'type=["\']application/json["\']', html))
        # embedded 추출이 몇 건 잡는지 미리보기
        cfg = next((s for s in boards.SOURCES if s["service"] == "대표 홈페이지"), None)
        if cfg:
            emb = boards._extract_embedded(html, cfg)
            out["embedded_found"] = len(emb)
            out["embedded_sample"] = emb[:3]
        # <a> 전체 중 앞부분 샘플(패턴 파악용)
        out["any_links_sample"] = list(dict.fromkeys(_re.findall(r'href=["\']([^"\']+)["\']', html)))[:25]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return jsonify(out)


@app.get("/api/apihunt")
def apihunt():
    """SPA(CRA 등) 페이지의 JS 번들을 받아 그 안에 박힌 API 주소 후보를 찾아 반환(진단용)."""
    import re as _re
    from urllib.parse import urljoin as _join
    url = request.args.get("url", "").strip()
    if not url.startswith("http"):
        return jsonify({"error": "url 파라미터 필요"}), 400
    out = {"url": url, "js": [], "candidates": []}
    try:
        html = (fetcher.get(url, retries=0, timeout=12).text or "")
        srcs = _re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
        js_urls = [_join(url, s) for s in srcs if ".js" in s]
        # main 번들 우선, 최대 4개까지만
        js_urls = sorted(set(js_urls), key=lambda u: (0 if "main" in u else 1, len(u)))[:4]
        out["js"] = js_urls
        cand = set()
        for ju in js_urls:
            try:
                body = (fetcher.get(ju, retries=0, timeout=15).text or "")[:4_000_000]
            except Exception:  # noqa: BLE001
                continue
            for pat in (
                r'https?://[a-zA-Z0-9.\-]+/[a-zA-Z0-9/_\-]*(?:api|community|board|news|post)[a-zA-Z0-9/_\-]*',
                r'["\'`](/(?:api|v1|v2)/[a-zA-Z0-9/_\-{}.:]+)["\'`]',
                r'["\'`](/[a-zA-Z0-9/_\-]*community[a-zA-Z0-9/_\-]*)["\'`]',
                r'baseURL\s*[:=]\s*["\'`]([^"\'`]+)["\'`]',
                r'["\'`](https?://api\.[a-zA-Z0-9.\-]+[^"\'`]*)["\'`]',
            ):
                for m in _re.findall(pat, body):
                    if isinstance(m, tuple):
                        m = m[0]
                    if 3 < len(m) < 200:
                        cand.add(m)
        out["candidates"] = sorted(cand)[:60]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return jsonify(out)


@app.get("/api/visitlog")
def visitlog():
    """접속자 로그(관리자 전용): 방문 시각·IP·기기(UA)·경로 등."""
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db():
        return jsonify([])
    try:
        return jsonify(db.list_visits())
    except Exception as e:  # noqa: BLE001
        print(f"[visitlog] 조회 실패: {e}", flush=True)
        return jsonify([])


_BOT_UA = ("bot", "spider", "crawler", "go-http-client", "uptimerobot",
           "render", "pingdom", "python-requests", "curl", "headless")


def _log_visit():
    """방문 1건을 백그라운드로 기록한다(페이지 로딩을 막지 않도록 별도 스레드).
    관리자 자신·명백한 봇/헬스체크는 제외."""
    try:
        ua = (request.headers.get("User-Agent") or "")
        if _admin_ok():
            return  # 관리자 본인 접속은 기록 안 함
        low = ua.lower()
        if any(b in low for b in _BOT_UA):
            return
        xff = request.headers.get("X-Forwarded-For", "")
        ip = (xff.split(",")[0].strip() if xff else (request.remote_addr or ""))
        data = (_now_kst(), ip, ua[:400], request.path,
                (request.headers.get("Referer") or "")[:300],
                (request.headers.get("Accept-Language") or "")[:80])
    except Exception:  # noqa: BLE001
        return

    def _w():
        try:
            if _ensure_db():
                db.add_visit(*data)
        except Exception as e:  # noqa: BLE001
            print(f"[visit] 기록 실패: {e}", flush=True)

    threading.Thread(target=_w, daemon=True).start()


@app.get("/api/meta")
def meta():
    if not _ensure_db():
        return jsonify({"news": None, "boards": None, "social": None,
                        "storage": db.BACKEND, "db_down": True})
    try:
        m = db.get_all_meta()
    except Exception:  # noqa: BLE001
        return jsonify({"news": None, "boards": None, "social": None,
                        "storage": db.BACKEND, "db_down": True})
    return jsonify({
        "news": m.get("last_crawl_news"),
        "boards": m.get("last_crawl_boards"),
        "social": m.get("last_crawl_social"),
        "storage": db.BACKEND,  # postgres(영구) / sqlite(임시)
    })


# 수집 구현 현황(화면 뱃지용). 완료 / 구현 중 / 구현 예정
BOARD_STATUS = {"나의AAC": "완료", "프로젝토리": "구현 중", "FAIR AI": "구현 예정", "대표 홈페이지": "구현 중"}
SOCIAL_STATUS = {"유튜브": "완료", "인스타그램": "구현 예정"}


@app.route("/")
def index():
    _log_visit()  # 방문 기록(백그라운드, 페이지 로딩 안 막음)
    services = sorted({s["service"] for s in boards.SOURCES})
    channels = sorted({s["channel"] for s in social.SOURCES})
    board_status = [{"name": n, "status": BOARD_STATUS.get(n, "완료")} for n in services]
    social_status = [{"name": n, "status": SOCIAL_STATUS.get(n, "완료")} for n in channels]
    news_categories = list(google_news.CATEGORIES.keys())  # 재단 / 본사
    return render_template(
        "index.html", services=services, channels=channels,
        board_status=board_status, social_status=social_status,
        news_categories=news_categories,
    )


# ---------------------------- 조회 API ----------------------------
# DB가 죽어 있으면(_ensure_db 실패) 빈 목록을 '즉시' 반환한다. 그래야 화면이 45초씩
# 멈추거나 502가 나지 않고, 목록만 비어 보인다(Neon 복구되면 자동으로 채워짐).
def _safe_list(fetch):
    if not _ensure_db():
        return jsonify([])
    try:
        return jsonify(fetch())
    except Exception as e:  # noqa: BLE001
        print(f"[api] 조회 실패(DB): {e}", flush=True)
        return jsonify([])


@app.get("/api/news")
def get_news():
    category = request.args.get("category", "all")
    return _safe_list(lambda: db.list_news(category=category))


@app.get("/api/boards")
def get_boards():
    service = request.args.get("service", "all")
    return _safe_list(lambda: db.list_boards(service=service))


@app.get("/api/social")
def get_social():
    channel = request.args.get("channel", "all")
    return _safe_list(lambda: db.list_social(channel=channel))


# ---------------------------- 진단 API ----------------------------
@app.get("/api/diag")
def diag():
    """각 대상 사이트에 이 서버가 실제로 접속되는지 빠르게 점검한다.
    브라우저로 /api/diag 를 열면 사이트별 응답 상태/에러를 즉시 확인할 수 있다.
    """
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    group = request.args.get("group", "all")  # news | boards | all
    targets = []
    if group in ("all", "news"):
        targets.append(
            ("구글 뉴스(RSS)", google_news.RSS_URL + "?q=NC%EB%AC%B8%ED%99%94%EC%9E%AC%EB%8B%A8&hl=ko&gl=KR&ceid=KR:ko", None)
        )
    if group in ("all", "boards"):
        seen = set()
        for s in boards.SOURCES:
            if s["list_url"] not in seen:
                seen.add(s["list_url"])
                targets.append((f"{s['service']} · {s['category']}", s["list_url"], s.get("item_link_sel")))
    if group in ("all", "social"):
        for s in social.SOURCES:
            if s.get("url"):
                targets.append((f"{s['channel']} · {s['account']}", s["url"], None))

    def check(item):
        name, url, sel = item
        t0 = _t.time()
        try:
            r = fetcher.get(url, retries=0)
            body = r.text or ""
            res = {
                "name": name, "url": url, "ok": True,
                "status": r.status_code, "bytes": len(body),
                "looks_html": "<html" in body.lower() or "<!doctype" in body.lower(),
                "sec": round(_t.time() - t0, 1),
            }
            # 게시판은 '연결됨'만이 아니라 설정된 선택자로 실제 글이 몇 개 잡히는지 센다.
            # (연결 정상인데 글 0개면 = SPA이거나 선택자 불일치 → 스크래핑 안 되는 원인)
            if sel:
                from bs4 import BeautifulSoup
                res["items"] = len(boards._select_any(BeautifulSoup(body, "lxml"), sel))
            return res
        except Exception as e:  # noqa: BLE001
            return {
                "name": name, "url": url, "ok": False,
                "error": type(e).__name__ + ": " + str(e)[:160],
                "sec": round(_t.time() - t0, 1),
            }

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, targets))
    return jsonify(results)


@app.get("/api/inspect")
def inspect():
    """사이트 구조 분석.
    /api/inspect            → 모든 게시판 목록 페이지를 분석
    /api/inspect?url=...     → 지정한 한 페이지만 분석 (뉴스 소스 점검 등)
    """
    from concurrent.futures import ThreadPoolExecutor

    from collector import inspect as inspector

    url = request.args.get("url")
    if url:
        return jsonify(inspector.inspect_url(url))

    seen, urls = set(), []
    for s in boards.SOURCES:
        if s["list_url"] not in seen:
            seen.add(s["list_url"])
            urls.append(s["list_url"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        return jsonify(list(pool.map(inspector.inspect_url, urls)))


# ---------------------------- 수집 API (동기 방식) ----------------------------
# 수집은 요청 한 번에 끝까지 처리하고 결과를 바로 반환한다. (구조가 단순해 어떤
# 버전의 프론트엔드 JS가 캐시돼 있어도 호환되며, 무료 호스팅 재시작에도 안전)
# 마지막 결과는 /api/crawl/status 폴링형 프론트와의 호환을 위해 보관한다.
_last_result = {"news": None, "boards": None, "social": None}


# 저장 정책: 키 = 원문 URL.
#  - 뉴스는 '동일 기사(여러 매체 배포)'도 전부 저장한다(중복 제거 X). 대신 저장 후
#    전체를 본문/제목 유사도로 클러스터링해 group_key를 부여 → 화면에서 아코디언 묶음.
def _save_news(items):
    for item in items:
        item["content_hash"] = dedup.content_hash(item.get("content", ""))
    new, updated = db.upsert_news_many(items)

    # 저장된 전체 뉴스를 대상으로 '같은 기사' 그룹화(group_key 부여)
    rows = db.all_news_min()
    keys = dedup.cluster_items(rows)
    url_to_key = {r["url"]: k for r, k in zip(rows, keys) if r.get("url")}
    db.set_news_group_keys(url_to_key)
    groups = len(set(url_to_key.values()))
    return {"new": new, "updated": updated, "duplicates": 0, "groups": groups}


def _purge_news_noise(progress=None):
    """이미 저장된 뉴스 중 본사 카테고리의 노이즈(야구·백화점 등, 게임 문맥 없는 NC)를
    현재 필터 기준으로 재평가해 삭제한다. (필터 강화 이전에 쌓인 것 정리용)"""
    try:
        rows = db.all_news_for_filter()
        bad = [r["url"] for r in rows
               if r.get("category") == "본사" and not google_news._passes_filters(r, days=10 ** 6)]
        if bad:
            db.delete_news_by_urls(bad)
            print(f"[crawl] 본사 노이즈 {len(bad)}건 정리", flush=True)
            if progress:
                progress(f"노이즈 {len(bad)}건 정리")
    except Exception as e:  # noqa: BLE001
        print(f"[crawl] 노이즈 정리 실패: {e}", flush=True)


IMG_ENRICH_MAX = int(os.environ.get("IMG_ENRICH_MAX", "200"))  # 수집 1회당 보강 개수 상한


def _enrich_news_images(progress=None):
    """이미지가 없거나 본문이 짧은(=RSS 요약뿐) 최근 뉴스 일부의 원문을 열어
    대표 이미지(og:image)와 실제 요약(og:description/본문)을 함께 채운다.
    대량 백필은 속도 때문에 원문을 안 여니, 수집 때마다 최근 것부터 조금씩 보강한다."""
    try:
        rows = db.news_needs_enrich(limit=IMG_ENRICH_MAX)
        if not rows:
            return
        if progress:
            progress(f"본문·이미지 보강 0/{len(rows)}")
        data = google_news.enrich_articles(rows, progress=progress)
        n = db.apply_news_enrich(data)
        print(f"[crawl] 본문·이미지 보강 {n}건", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[crawl] 보강 실패: {e}", flush=True)


def _save_boards(items):
    new, updated = db.upsert_board_many(items)
    return {"new": new, "updated": updated, "duplicates": 0}


def _save_social(items):
    new, updated = db.upsert_social_many(items)
    return {"new": new, "updated": updated, "duplicates": 0}


_CRAWLERS = {
    "news": (google_news.crawl, _save_news),
    "boards": (boards.crawl_all, _save_boards),
    "social": (social.crawl_all, _save_social),
}


def _do_crawl(group, progress=None, days=None):
    """수집 1회 실행(수동 버튼·배치 공용). 결과 dict 반환.
    progress(msg): 진행상황 콜백(선택). days: 뉴스 수집 기간(최근 N일)."""
    progress = progress or (lambda m: None)
    crawl_fn, save_fn = _CRAWLERS[group]
    progress("DB 연결 중…")
    _ensure_db(force=True)  # 실제로 접속을 기다려 Neon을 깨운다(수집은 DB가 꼭 필요)
    try:
        if group == "news":
            # 예전엔 여기서 기존 URL을 전부 로드해 증분 비교했는데, 큰 테이블 + Neon
            # cold start에서 이 '기존 데이터 확인'이 너무 느렸다. 이제는 그 단계를 없애고,
            # 저장 시 ON CONFLICT로 중복을 걸러 새 기사만 추가한다(신규 건수는 COUNT 차이).
            items = crawl_fn(progress=progress, days=days)
        else:
            items = crawl_fn(progress=progress)
        progress("저장·그룹화 중…")
        counts = save_fn(items)
        result = {"crawled": len(items), **counts}
        if group == "news":
            _purge_news_noise(progress)  # 기존에 쌓인 본사 노이즈(야구/백화점 등) 정리
            _enrich_news_images(progress)  # 이미지 없는 최근 기사에 대표 이미지(og:image) 보강
        progress(f"완료 · 신규 {result.get('new', 0)}건 · 갱신 {result.get('updated', 0)}건")
    except Exception as e:  # noqa: BLE001
        print(f"[crawl] {group} 오류: {e}", flush=True)
        result = {"crawled": 0, "new": 0, "updated": 0, "duplicates": 0, "error": str(e)}
        progress(f"오류: {e}")
    result["last_crawled_at"] = _now_kst()  # 서버 기준 마지막 수집 일시
    db.set_meta(f"last_crawl_{group}", result["last_crawled_at"])
    _last_result[group] = result
    # 실행 로그 기록(관리자가 언제·성공/실패를 볼 수 있게)
    try:
        if result.get("error"):
            status, detail = "실패", str(result["error"])[:200]
        else:
            status = "성공"
            detail = (f"신규 {result.get('new', 0)} · 갱신 {result.get('updated', 0)} "
                      f"· 수집 {result.get('crawled', 0)}")
        db.add_run_log(group, status, detail, result["last_crawled_at"])
    except Exception as e:  # noqa: BLE001
        print(f"[runlog] 기록 실패: {e}", flush=True)
    return result


# ---------------------------- 수집 작업(서버 백그라운드) ----------------------------
# 수집은 브라우저 연결과 분리된 서버 스레드로 돌린다. 그래서 휴대폰 화면이 꺼지거나
# 브라우저가 백그라운드로 가도 수집은 서버에서 끝까지 진행된다. 프론트는 상태를
# 폴링해서 진행률/결과를 표시하고, 돌아왔을 때 자동으로 다시 붙는다.
_JOBS = {}  # group -> {running, progress, result, started_at}


def _job_run(group, days=None):
    st = _JOBS[group]

    def cb(msg):
        st["progress"] = msg

    try:
        st["result"] = _do_crawl(group, progress=cb, days=days)
        st["progress"] = st["result"].get("error") and f"오류: {st['result']['error']}" or "완료"
    except Exception as e:  # noqa: BLE001
        st["result"] = {"error": str(e), "crawled": 0}
        st["progress"] = f"오류: {e}"
    finally:
        st["running"] = False


_JOB_STALE_SEC = 1800  # 30분 넘게 '실행 중'이면 멈춘 것으로 간주(재시작 허용/UI 해제)


def _start_job(group, days=None):
    """백그라운드 수집 작업 시작. 이미 진행 중이면 False.
    단, 30분 넘게 진행 중(멈춘 것으로 추정)이면 새로 시작한다."""
    st = _JOBS.get(group)
    if st and st.get("running") and (time.time() - st.get("started_ts", 0) < _JOB_STALE_SEC):
        return False
    _JOBS[group] = {"running": True, "progress": "수집 대기…", "result": None,
                    "started_at": _now_kst(), "started_ts": time.time()}
    threading.Thread(target=_job_run, args=(group, days), daemon=True).start()
    print(f"[crawl] {group} 백그라운드 수집 시작 (days={days})", flush=True)
    return True


@app.post("/api/crawl/<group>/start")
def crawl_start(group):
    if group not in _CRAWLERS:
        return jsonify({"error": "unknown group"}), 404
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    days = request.args.get("days", type=int)  # 뉴스 수집 기간(최근 N일)
    if not _start_job(group, days):
        return jsonify({"running": True, "already": True})  # 이미 진행 중이면 중복 실행 안 함
    return jsonify({"running": True})


@app.get("/api/crawl/<group>/status")
def crawl_job_status(group):
    st = _JOBS.get(group)
    if not st:
        # 이번 세션에 실행 이력이 없으면 마지막 저장 결과만 참고로 반환
        return jsonify({"running": False, "progress": None, "result": _last_result.get(group)})
    # 30분 넘게 '실행 중'이면 멈춘 것으로 간주 → UI가 풀리도록 완료 처리
    if st.get("running") and (time.time() - st.get("started_ts", 0) > _JOB_STALE_SEC):
        st["running"] = False
        st["progress"] = "중단됨(시간 초과) — 다시 시도해 주세요"
    return jsonify(st)


@app.post("/api/crawl/news")
def crawl_news():
    print("[crawl] /api/crawl/news 시작 (구글 뉴스 RSS)", flush=True)
    return jsonify(_do_crawl("news"))


@app.post("/api/crawl/boards")
def crawl_boards():
    print("[crawl] /api/crawl/boards 시작", flush=True)
    return jsonify(_do_crawl("boards"))


@app.post("/api/crawl/social")
def crawl_social():
    print("[crawl] /api/crawl/social 시작", flush=True)
    return jsonify(_do_crawl("social"))


@app.get("/api/crawl/status")
def crawl_status():
    # 폴링형(구버전) 프론트 호환용. 동기 방식이라 항상 미실행 상태이며,
    # 마지막 수집 결과를 함께 돌려준다.
    group = request.args.get("group", "news")
    return jsonify({"running": False, "log": [], "result": _last_result.get(group)})


# ---------------------------- 배치 스케줄 ----------------------------
# 정기 배치(스케줄러/크론)는 최근 것만 훑어 새 기사를 보충한다(최초 5년 백필과 별개).
BATCH_DAYS = int(os.environ.get("BATCH_DAYS", "30"))  # 정기 배치 뉴스 수집 창(최근 N일)


def _batch_all():
    """뉴스/게시판/소셜을 백그라운드 작업으로 시작(비차단). 시작된 그룹 목록 반환.
    뉴스는 최근 BATCH_DAYS(기본 30일)만 훑는다(증분이라 겹쳐도 새 기사만 저장)."""
    print(f"[batch] 수집 배치 시작 (뉴스 최근 {BATCH_DAYS}일)", flush=True)
    started = []
    for group in ("news", "boards", "social"):
        days = BATCH_DAYS if group == "news" else None
        if _start_job(group, days=days):
            started.append(group)
    return started


def _start_scheduler():
    if os.environ.get("ENABLE_SCHEDULER", "1") != "1":
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] APScheduler 미설치: {e}", flush=True)
        return
    sched = BackgroundScheduler(daemon=True, timezone=KST)
    # 첫 실행은 4시간 뒤. 즉시 수집은 관리자가 버튼으로.
    sched.add_job(_batch_all, "interval", hours=4, id="crawl_all", coalesce=True, max_instances=1)
    sched.start()
    print("[scheduler] 4시간 주기 수집 배치 시작", flush=True)


@app.route("/api/cron", methods=["GET", "POST"])
def cron():
    """외부 크론(예: cron-job.org)이 토큰으로 배치를 트리거. 무료 호스팅이 잠들어
    내부 스케줄러가 멈추는 경우의 대안(앱을 깨우며 백그라운드 수집 시작).
    CRON_TOKEN 환경변수 미설정 시 비활성. GET/POST 모두 허용(크론 서비스 호환)."""
    token = os.environ.get("CRON_TOKEN")
    if not token or request.args.get("token") != token:
        return jsonify({"error": "unauthorized"}), 401
    started = _batch_all()
    return jsonify({"ok": True, "started": started, "at": _now_kst()})


def _auto_backfill():
    """앱 시작 시 뉴스 DB가 비어 있으면 자동으로 대량 수집(백필)한다.
    ⚠ 기본 OFF: 무료 512MB에서 부팅 직후 2년치 대량 수집이 메모리를 초과(OOM)시켜
    프로세스가 죽고 재시작을 반복(크래시 루프 → 사이트 접속 불가)하는 문제가 있어서다.
    데이터는 관리자 수집 버튼/🗑 초기화, 또는 cron으로 채운다. 켜려면 AUTO_BACKFILL=1."""
    if os.environ.get("AUTO_BACKFILL", "0") != "1":
        return
    try:
        _ensure_db()
        has_news = db.news_count() > 0
    except Exception as e:  # noqa: BLE001
        print(f"[backfill] DB 확인 실패, 건너뜀: {e}", flush=True)
        return
    if has_news:
        print("[backfill] 기존 데이터 있음 → 백필 건너뜀", flush=True)
        return
    days = int(os.environ.get("BACKFILL_DAYS", "1825"))  # 기본 5년(뉴스 필터 RECENT_DAYS와 일치)
    print(f"[backfill] DB 비어있음 → 자동 백필 시작(뉴스 최근 {days}일 + 게시판/소셜)", flush=True)
    _start_job("news", days=days)
    _start_job("boards")
    _start_job("social")


# DB keep-alive: Neon 무료는 5분 놀면 잠든다. 앱이 상시 가동(Render Starter)이면,
# 주기적으로 DB에 가벼운 쿼리를 날려 잠들지 않게 유지 → 방문자가 잠든 DB를 안 만난다.
# DB_KEEPALIVE_SEC=0 이면 끔. (Neon 무료는 compute 사용시간 한도가 있으니, 한도가 걱정되면
# 끄거나 Supabase처럼 상시 켜짐 DB로 바꾸면 된다.)
DB_KEEPALIVE_SEC = int(os.environ.get("DB_KEEPALIVE_SEC", "240"))  # 기본 4분


def _db_keepalive():
    if db.BACKEND != "postgres" or DB_KEEPALIVE_SEC <= 0:
        return
    print(f"[keepalive] DB 유지 시작({DB_KEEPALIVE_SEC}초 주기)", flush=True)
    while True:
        time.sleep(DB_KEEPALIVE_SEC)
        try:
            _ensure_db(force=True)
            with db.get_conn() as conn:
                conn.execute("SELECT 1")
        except Exception as e:  # noqa: BLE001
            print(f"[keepalive] 실패(무시): {e}", flush=True)


_start_scheduler()
# 백필은 DB를 건드리므로(cold start로 느릴 수 있음) 백그라운드 스레드에서 돌려
# import(부팅)를 절대 막지 않게 한다. → Render 배포가 DB 상태와 무관하게 성공.
threading.Thread(target=_auto_backfill, daemon=True).start()
threading.Thread(target=_db_keepalive, daemon=True).start()


if __name__ == "__main__":
    # 로컬 실행. 호스팅 환경은 gunicorn이 app 객체를 직접 띄운다(Procfile 참고).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
