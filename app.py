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
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, g, jsonify, render_template, request, session

from collector import (analysis, boards, db, dedup, event_curation, event_sources, events, fetcher,
                       google_news, lunch, security_ai, security_report, social, venue_sources)

from collector.reader import bp as reader_bp
from collector.finance import bp as finance_bp
from collector.read_cache import ReadCache

_PUBLIC_READS = ReadCache()
_LOCATION_READS = ReadCache(max_entries=1, workers=1)

app = Flask(__name__)
app.register_blueprint(reader_bp)
app.register_blueprint(finance_bp)


@app.before_request
def _start_request_timer():
    g.request_started = time.perf_counter()
    _start_worker_jobs()
    _start_read_prewarm()


@app.after_request
def _record_request_timing(response):
    elapsed = (time.perf_counter() - g.request_started) * 1000
    response.headers.add("Server-Timing", f"app;dur={elapsed:.2f}")
    return response

# 초안 단계: 브라우저가 옛 JS/CSS를 캐시해 혼란을 주지 않도록 정적파일 캐시를 끈다.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = os.environ.get("SECRET_KEY", "ncfoundation-collector-secret-key")

KST = timezone(timedelta(hours=9))  # 마지막 수집 일시는 서버에서 KST로 기록

# 관리자 로그인: 로그인해야 상태확인/수집 실행이 보이고 동작한다(뷰어는 조회만).
ADMIN_PW = os.environ.get("ADMIN_PW", "rlatkdghk12#")
# 일반 사용자(테스트용) 계정: tester1~tester10 / 비번 1234
TEST_USERS = {"test1": "1234"}   # 일반 테스트 계정 1개(로그인창 자동입력)


def _admin_ok():
    return bool(session.get("admin"))


def _current_user():
    return session.get("user")


def _now_kst():
    return datetime.now(KST).strftime("%Y.%m.%d %H:%M:%S")


# DB 초기화는 '지연(lazy)'으로 한다. import(부팅) 시점에 Neon에 동기 접속하면,
# Neon 무료가 자고 있을 때(cold start) 연결이 최대 수십 초~2분 걸려 gunicorn 워커가
# 강제 종료되고 → Render 배포가 통째로 실패(이전 버전으로 남음)한다. 그래서 부팅은
# DB와 무관하게 즉시 끝내고, 첫 요청 때 한 번만 테이블을 준비한다.
_db_ready = False
_db_lock = threading.Lock()
_db_last_try = 0.0
_DB_RETRY_COOLDOWN = 6  # 초: DB 연결 실패 후 재시도 억제(짧게 → '다시 불러오기'가 빨리 회복)


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
    if not force:
        _db_wake_async()
        return False  # HTTP 조회에서는 테이블 준비/DB 연결을 기다리지 않는다.
    # 실제 초기화는 백그라운드 작업/명시적 쓰기에서만 직렬 실행한다.
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


_db_waking = False
_db_wake_lock = threading.Lock()


def _db_wake_async():
    """DB가 자고 있을 때(read 요청이 빈 결과를 받을 때) 백그라운드로 Neon을 깨운다.
    스레드 1개만 깨우기 시도(중복 방지). 요청 자체는 막지 않음 → 클라이언트가 잠시 후 재시도."""
    global _db_waking
    with _db_wake_lock:
        if _db_ready or _db_waking or time.time() - _db_last_try < _DB_RETRY_COOLDOWN:
            return
        _db_waking = True

    def _w():
        global _db_waking
        try:
            _ensure_db(force=True)
        finally:
            with _db_wake_lock:
                _db_waking = False

    threading.Thread(target=_w, daemon=True).start()


@app.get("/healthz")
def healthz():
    """킵얼라이브용 초경량 엔드포인트(DB 접속 안 함). 외부 크론이 이걸 주기적으로
    호출하면 Render 무료 앱이 잠들지 않아 방문자가 cold start를 안 겪는다."""
    return "ok", 200


@app.get("/api/me")
def me():
    return jsonify({"user": session.get("user"), "admin": _admin_ok(),
                    "logged_in": bool(session.get("user"))})


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
    """role='admin'(관리자, 비번만) 또는 role='user'(일반, 아이디+비번). 세션에 user/admin 기록."""
    data = request.get_json(silent=True) or {}
    role = data.get("role", "admin")
    pw = data.get("pw", "")
    if role == "admin":
        if pw == ADMIN_PW:
            session["user"] = "admin"
            session["admin"] = True
            return jsonify({"ok": True, "user": "admin", "admin": True})
        return jsonify({"ok": False, "error": "비밀번호가 올바르지 않습니다."}), 401
    username = (data.get("username") or "").strip().lower()
    if username in TEST_USERS and pw == TEST_USERS[username]:
        session["user"] = username
        session["admin"] = False
        return jsonify({"ok": True, "user": username, "admin": False})
    return jsonify({"ok": False, "error": "아이디 또는 비밀번호가 올바르지 않습니다."}), 401


@app.post("/api/logout")
def logout():
    session.pop("admin", None)
    session.pop("user", None)
    return jsonify({"ok": True})


# ---------------------------- 개인 데이터(스크랩/읽음/그룹) ----------------------------
def _get_groups(u):
    rows = db.user_state_list(u, "grouplist")
    if not rows:
        return []
    try:
        return json.loads(rows[0]["snapshot"] or "[]")
    except Exception:  # noqa: BLE001
        return []


def _save_groups(u, groups):
    db.user_state_upsert(u, "_", "grouplist", json.dumps(groups, ensure_ascii=False),
                         int(time.time() * 1000))


@app.get("/api/mydata")
def mydata():
    """로그인한 사용자의 스크랩·읽음·그룹 목록. 미로그인=401."""
    u = _current_user()
    if not u:
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db():
        response = jsonify({"scraps": [], "reads": [], "groups": []})
        response.headers["X-Data-Pending"] = "1"
        return response
    state = db.user_state_bundle(u)
    scraps = []
    for r in state["scrap"]:
        try:
            snap = json.loads(r["snapshot"] or "{}")
        except Exception:  # noqa: BLE001
            snap = {}
        snap["ts"] = r["ts"]
        if not isinstance(snap.get("groups"), list):
            snap["groups"] = []
        scraps.append(snap)
    reads = [r["ukey"] for r in state["read"]]
    try:
        groups = json.loads(state["grouplist"][0]["snapshot"] or "[]") if state["grouplist"] else []
    except (ValueError, TypeError):
        groups = []
    return jsonify({"scraps": scraps, "reads": reads, "groups": groups})


@app.post("/api/groups")
def groups_api():
    """커스텀 그룹 관리. body: {op:'add'|'rename'|'del', id?, name?}. 반환: {ok, groups}."""
    u = _current_user()
    if not u:
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db():
        return jsonify({"ok": False}), 503
    data = request.get_json(silent=True) or {}
    op = data.get("op")
    groups = _get_groups(u)
    if op == "add":
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "이름 없음"}), 400
        gid = uuid.uuid4().hex[:8]
        groups.append({"id": gid, "name": name[:40]})
        _save_groups(u, groups)
        return jsonify({"ok": True, "groups": groups, "id": gid})
    if op == "rename":
        gid = data.get("id")
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "이름 없음"}), 400
        for g in groups:
            if g.get("id") == gid:
                g["name"] = name[:40]
        _save_groups(u, groups)
        return jsonify({"ok": True, "groups": groups})
    if op == "del":
        gid = data.get("id")
        groups = [g for g in groups if g.get("id") != gid]
        _save_groups(u, groups)
        # 모든 스크랩에서 해당 그룹 소속 제거(정리)
        for r in db.user_state_list(u, "scrap"):
            try:
                snap = json.loads(r["snapshot"] or "{}")
            except Exception:  # noqa: BLE001
                continue
            gs = snap.get("groups") or []
            if gid in gs:
                snap["groups"] = [x for x in gs if x != gid]
                db.user_state_upsert(u, r["ukey"], "scrap",
                                     json.dumps(snap, ensure_ascii=False), r["ts"])
        return jsonify({"ok": True, "groups": groups})
    return jsonify({"ok": False, "error": "op"}), 400


@app.post("/api/scrap/groups")
def scrap_groups():
    """특정 스크랩의 소속 그룹 설정. body: {key, groups:[id,...]}. 미로그인=401."""
    u = _current_user()
    if not u:
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db():
        return jsonify({"ok": False}), 503
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False}), 400
    row = db.user_state_get(u, key, "scrap")
    if not row:
        return jsonify({"ok": False, "error": "스크랩 아님"}), 404
    try:
        obj = json.loads(row["snapshot"] or "{}")
    except Exception:  # noqa: BLE001
        obj = {}
    obj["groups"] = [str(x) for x in (data.get("groups") or [])]
    db.user_state_upsert(u, key, "scrap", json.dumps(obj, ensure_ascii=False), row["ts"])
    return jsonify({"ok": True})


@app.post("/api/scrap")
def scrap():
    """스크랩 추가/삭제. body: {op:'add'|'del', key, item?}. 미로그인=401."""
    u = _current_user()
    if not u:
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db():
        return jsonify({"ok": False}), 503
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "key 없음"}), 400
    if data.get("op") == "del":
        db.user_state_delete(u, key, "scrap")
    else:
        item = data.get("item") or {}
        db.user_state_upsert(u, key, "scrap", json.dumps(item, ensure_ascii=False),
                             int(time.time() * 1000))
    return jsonify({"ok": True})


@app.post("/api/read")
def read():
    """읽음 처리. body: {key}. 미로그인=401."""
    u = _current_user()
    if not u:
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db():
        return jsonify({"ok": False}), 503
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False}), 400
    db.user_state_upsert(u, key, "read", "", int(time.time() * 1000))
    return jsonify({"ok": True})


@app.post("/api/admin/purge")
def admin_purge():
    """수집 데이터(DB)를 비운다(관리자 전용). 선택한 탭(scope)만 비우고 재수집한다.
    body: {"scope": "cat"|"news"|"boards"|"social"(|"all"), "recollect": bool, "days": int}
    - cat  : news 테이블의 section='cat'만 삭제 → crawl_cat 재수집
    - news : news 테이블의 section='nc'만 삭제 → 뉴스 재수집
    - boards/social : 해당 테이블 삭제 → 재수집"""
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db(force=True):
        return jsonify({"ok": False, "error": "DB에 연결할 수 없어요(Neon 깨는 중일 수 있음). 20초 뒤 다시 시도해 주세요."}), 503
    data = request.get_json(silent=True) or {}
    scope = data.get("scope", "all")
    groups = ["cat", "game", "news", "biz", "security", "event", "boards", "social"] if scope == "all" else [scope]
    _SEC = {"cat": "cat", "game": "game", "news": "nc", "biz": "biz", "security": "sec"}
    deleted = {}
    try:
        for g in groups:
            if g in _SEC:
                deleted[g] = db.clear_news_section(_SEC[g])
            elif g == "event":
                deleted["event"] = db.clear_events()
            elif g in ("boards", "social"):
                deleted.update(db.clear_tables([g]))
    except Exception as e:  # noqa: BLE001
        print(f"[purge] 삭제 실패: {e}", flush=True)
        return jsonify({"ok": False, "error": f"삭제 실패: {e}"}), 500
    _invalidate_read_cache()  # 비웠으니 조회 캐시도 비움
    started = []
    if data.get("recollect"):
        days = data.get("days")
        for g in groups:
            if _start_job(g, days=days if g in ("cat", "game", "news", "biz", "security", "event") else None):
                started.append(g)
    return jsonify({"ok": True, "deleted": deleted, "recollect_started": started})


@app.get("/api/dbcheck")
def dbcheck():
    """DB 연결을 직접 시도해 실제 에러 원문을 반환(진단용).
    비밀번호는 마스킹되고 연결 성공/실패·에러 종류만 나오므로 로그인 없이도 열 수 있게 둔다
    (DB가 죽으면 로그인 자체가 애매할 수 있어 진단 접근성을 우선)."""
    return jsonify(db.diagnose())


@app.get("/api/eventcheck")
def eventcheck():
    """행사 구조화 소스(관광공사 축제·문화행사) 진단: 소스별 설정·응답상태·행수·첫항목 키·
    원문 일부를 보여준다. 서비스키는 요청 파라미터로만 쓰여 응답에 노출되지 않으므로
    dbcheck/newscheck처럼 로그인 없이도 열 수 있게 둔다."""
    result = ({'venue_schedules': venue_sources.diagnose()}
              if request.args.get('venues') == '1' else event_sources.diagnose())
    # A completed initial import survives worker restarts in DB metadata.
    try:
        saved = json.loads(db.get_meta('venue_sources_status', '{}'))
        for source, state in result['venue_schedules'].items():
            if state.get('checked') is False and source in saved:
                result['venue_schedules'][source] = saved[source]
    except Exception:
        pass
    return jsonify(result)


@app.get("/api/newscheck")
def newscheck():
    """뉴스 목록 조회가 왜 비는지(‘불러오지 못했어요’) 진단한다.
    각 섹션 쿼리를 '캐시를 거치지 않고 직접' 실행해 건수/소요시간/실제 예외를 그대로 보여준다.
    + 읽기 캐시에 남은 최근 실패 사유도 함께 반환. 비밀·본문은 노출하지 않는다(건수/에러 종류만).
    DB 진단이 핵심이라 /api/dbcheck처럼 로그인 없이도 열 수 있게 둔다."""
    ready = _ensure_db(force=True)
    checks = {}
    probes = [
        ("news_nc_3mo", dict(section="nc", months=3)),
        ("news_nc_full", dict(section="nc", months=None)),
        ("cat_3mo", dict(section="cat", months=3)),
        ("game_3mo", dict(section="game", months=3)),
        ("biz_3mo", dict(section="biz", months=3)),
        ("sec_3mo", dict(section="sec", months=3)),
    ]
    for name, kw in probes:
        t0 = time.time()
        try:
            rows = db.list_news(**kw)
            checks[name] = {"ok": True, "count": len(rows), "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            checks[name] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                            "ms": int((time.time() - t0) * 1000)}
    return jsonify({
        "db_ready": bool(ready),
        "backend": db.BACKEND,
        "checks": checks,
        "cache_last_errors": _PUBLIC_READS.last_errors(),
    })


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
    # xhr=1 이면 AJAX 요청처럼 헤더를 붙여 JSON 응답을 유도
    hdrs = None
    if request.args.get("xhr") == "1":
        hdrs = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    ref = request.args.get("ref")  # Origin/Referer 검사형 API 테스트용
    if ref:
        hdrs = dict(hdrs or {})
        hdrs.update({"Origin": ref, "Referer": ref, "Accept": "application/json, text/plain, */*"})
    try:
        if request.args.get("method", "get").lower() == "post":
            import json as _json
            payload = request.args.get("body")
            body_data = payload if payload else "{}"
            phdrs = dict(hdrs or {})
            phdrs.update({"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"})
            resp = fetcher.post(url, data=body_data, headers=phdrs, retries=0, timeout=12)
        else:
            resp = fetcher.get(url, headers=hdrs, retries=0, timeout=12)
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
        # 본문 앞부분(JSON API 응답 구조 확인용)
        out["body_head"] = html[:1800]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return jsonify(out)


@app.get("/api/grep")
def grep_url():
    """지정 URL(주로 JS 파일)을 받아 검색어(q) 주변 텍스트를 반환(진단용)."""
    import re as _re
    url = request.args.get("url", "").strip()
    q = request.args.get("q", "").strip()
    if not url.startswith("http") or not q:
        return jsonify({"error": "url, q 파라미터 필요"}), 400
    out = {"url": url, "q": q, "hits": []}
    try:
        body = (fetcher.get(url, retries=0, timeout=15).text or "")[:4_000_000]
        low = body.lower()
        ql = q.lower()
        hits, start = [], 0
        while len(hits) < 25:
            i = low.find(ql, start)
            if i < 0:
                break
            snippet = body[max(0, i - 120): i + 180]
            hits.append(_re.sub(r"\s+", " ", snippet).strip())
            start = i + len(ql)
        out["hits"] = hits
        out["count"] = len(hits)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return jsonify(out)


@app.get("/api/grepall")
def grepall():
    """페이지의 모든 JS 청크를 받아 검색어(q) 주변 텍스트를 청크별로 반환(진단용)."""
    import re as _re
    from urllib.parse import urljoin as _join
    url = request.args.get("url", "").strip()
    q = request.args.get("q", "").strip()
    if not url.startswith("http") or not q:
        return jsonify({"error": "url, q 파라미터 필요"}), 400
    _LIB = ("vue", "axios", "moment", "jquery", "libs.min", "polyfill", "runtime")
    out = {"url": url, "q": q, "hits": []}
    try:
        html = (fetcher.get(url, retries=0, timeout=12).text or "")
        srcs = _re.findall(r'(?:src|href)=["\']([^"\']+\.js)["\']', html)
        srcs += _re.findall(r'import\(["\']([^"\']+\.js)["\']', html)
        js_urls = sorted(set(_join(url, s) for s in srcs if not any(l in s.lower() for l in _LIB)))[:30]
        ql = q.lower()
        hits = []
        for ju in js_urls:
            try:
                body = (fetcher.get(ju, retries=0, timeout=12).text or "")[:2_000_000]
            except Exception:  # noqa: BLE001
                continue
            low = body.lower()
            start, n = 0, 0
            while n < 3:
                i = low.find(ql, start)
                if i < 0:
                    break
                snip = _re.sub(r"\s+", " ", body[max(0, i - 140): i + 200]).strip()
                hits.append({"js": ju.rsplit("/", 1)[-1], "s": snip})
                start = i + len(ql)
                n += 1
            if len(hits) >= 30:
                break
        out["hits"] = hits
        out["count"] = len(hits)
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
    out = {"url": url, "js": [], "candidates": [], "script_hits": []}
    _LIB = ("vue", "axios", "moment", "jquery", "libs.min", "polyfill", "runtime")
    try:
        html = (fetcher.get(url, retries=0, timeout=12).text or "")
        # <script src>, <link href>(modulepreload; Nuxt/Vite), import('...') 전부에서 .js 수집
        srcs = _re.findall(r'(?:src|href)=["\']([^"\']+\.js)["\']', html)
        srcs += _re.findall(r'import\(["\']([^"\']+\.js)["\']', html)
        js_urls = [_join(url, s) for s in srcs if not any(l in s.lower() for l in _LIB)]
        js_urls = sorted(set(js_urls), key=lambda u: (0 if any(k in u.lower() for k in ("entry", "main")) else 1, len(u)))[:25]
        out["js"] = js_urls
        # 검색 대상: 페이지 HTML(인라인 스크립트 포함) + 앱 JS 파일
        bodies = [html]
        for ju in js_urls:
            try:
                bodies.append((fetcher.get(ju, retries=0, timeout=12).text or "")[:1_500_000])
            except Exception:  # noqa: BLE001
                continue
        cand = set()
        for body in bodies:
            for pat in (
                r'axios\.(?:get|post|put)\(\s*["\'`]([^"\'`]+)["\'`]',
                r'\$fetch\(\s*["\'`]([^"\'`]+)["\'`]',
                r'(?:url|api|endpoint|baseURL|baseUrl)\s*[:=]\s*["\'`]([^"\'`]{4,})["\'`]',
                r'["\'`](/(?:fair|search)/api[^"\'`]*)["\'`]',
                r'["\'`]([^"\'`]{2,50}(?:notice|board|news|insight|list|article)[^"\'`]{0,40})["\'`]',
                r'["\'`](https?://[a-zA-Z0-9.\-]+/[^"\'`]*(?:api|board|news|notice|list|insight)[^"\'`]*)["\'`]',
            ):
                for m in _re.findall(pat, body):
                    if isinstance(m, tuple):
                        m = m[0]
                    if 3 < len(m) < 200:
                        cand.add(m)
        out["candidates"] = sorted(cand)[:120]
        # 인라인 스크립트에서 axios/url/boardIdx 근처 줄을 그대로 보여줌(수동 확인용)
        hits = []
        for line in html.splitlines():
            low = line.lower()
            if any(k in low for k in ("axios", "boardidx", "notice-list", "ajax", ".json", "url:")):
                s = line.strip()
                if 6 < len(s) < 300:
                    hits.append(s)
        out["script_hits"] = list(dict.fromkeys(hits))[:40]
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


def _read_metadata():
    def load():
        if not _ensure_db(force=True):
            raise RuntimeError("DB unavailable")
        return db.get_all_meta()
    return _PUBLIC_READS.get("meta", load, ttl=45, wait=.15)


@app.get("/api/meta")
def meta():
    m = _read_metadata()
    if m is None:
        response = jsonify({"storage": db.BACKEND, "db_down": True})
        response.headers["X-Data-Pending"] = "1"
        return response
    response = jsonify({
        "cat": m.get("last_crawl_cat"),
        "game": m.get("last_crawl_game"),
        "news": m.get("last_crawl_news"),
        "biz": m.get("last_crawl_biz"),
        "security": m.get("last_crawl_security"),
        "event": m.get("last_crawl_event"),
        "boards": m.get("last_crawl_boards"),
        "social": m.get("last_crawl_social"),
        "storage": db.BACKEND,  # postgres(영구) / sqlite(임시)
    })

    return response


# ---- 기능/탭 표시 설정(관리자 온오프) ----
# 끈 항목은 일반/방문자에게 숨김(관리자는 항상 노출·미리보기). 기본 전체 ON.
_FEATURE_KEYS = ["cat", "game", "news", "biz", "security", "event", "boards", "social", "report", "scrap"]


def _load_features(fresh=False, metadata=None):
    try:
        raw = (db.get_meta("feature_flags", "") if fresh else (metadata or {}).get("feature_flags", ""))
        saved = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        saved = {}
    # 기본값 True, 저장된 값만 덮어씀(명시적으로 False인 것만 off)
    flags = {k: (False if saved.get(k) is False else True) for k in _FEATURE_KEYS}
    flags["cat"] = True  # 냥정보는 기본 탭 → 항상 노출(끌 수 없음). 전부 꺼도 최소 1개 유지.
    return flags


@app.get("/api/features")
def features_get():
    """현재 탭/기능 표시 설정(모든 클라이언트가 읽어 적용). 공개."""
    metadata = _read_metadata()
    response = jsonify(_load_features(metadata=metadata))
    if metadata is None:
        response.headers["X-Data-Pending"] = "1"
    return response


@app.post("/api/features")
def features_set():
    """표시 설정 저장(관리자). body: {키: bool, ...} 부분 갱신."""
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db(force=True):
        return jsonify({"ok": False, "error": "DB에 연결할 수 없어요."}), 503
    data = request.get_json(silent=True) or {}
    cur = _load_features(fresh=True)
    for k in _FEATURE_KEYS:
        if k in data:
            cur[k] = bool(data[k])
    db.set_meta("feature_flags", json.dumps(cur, ensure_ascii=False))
    _PUBLIC_READS.invalidate("meta")
    return jsonify({"ok": True, "features": cur})


# ---------------------------- 점심 맛집(lunch) ----------------------------
# 초기 사업장 3개소(좌표는 카카오 지오코딩으로 최초 조회 시 자동 채움 · 관리자 수정 가능)
LUNCH_OFFICES = [
    {"name": "NC문화재단 사옥", "address": "서울 종로구 이화장길 100", "radius": 500},
    {"name": "NC 판교R&D센터", "address": "경기 성남시 분당구 대왕판교로644번길 12", "radius": 500},
    {"name": "프로젝토리 성남지점", "address": "성남문화예술교육센터", "radius": 700},
    {"name": "개발자 동네", "address": "서울 강동구 양재대로110길 37-10", "radius": 500},
]
_LUNCH_JOB = {"running": False, "progress": "", "result": None, "started_ts": 0, "loc": None}


def _cur_user():
    return session.get("user")


def _lunch_geocode_if_needed(loc):
    if loc and (loc.get("lat") is None or loc.get("lng") is None) and lunch.has_key():
        co = lunch.geocode(loc.get("address") or loc.get("name"))
        if co:
            db.lunch_set_location_coords(loc["id"], co[0], co[1])
            _LOCATION_READS.invalidate()
            _invalidate_lunch_cache()
            loc["lat"], loc["lng"] = co[0], co[1]
    return loc


def _lunch_decorate(loc, rows):
    """식당 rows에 거리/도보시간/평점 정리 필드 추가."""
    out = []
    for r in rows:
        d = dict(r)
        dist = None
        if loc and loc.get("lat") and r.get("lat"):
            dist = lunch.haversine_m(loc["lat"], loc["lng"], r["lat"], r["lng"])
        d["dist_m"] = round(dist) if dist is not None else None
        d["walk_min"] = lunch.walk_minutes(dist)
        d["avg_rating"] = round(r["avg_rating"], 1) if r.get("avg_rating") is not None else None
        out.append(d)
    out.sort(key=lambda x: (x["dist_m"] is None, x["dist_m"] or 0))  # 가까운 순 기본
    return out


_lunch_synced = False           # 시드/동기화는 프로세스당 1회만(매 요청 X)
_lunch_geo_lock = threading.Lock()
_lunch_geo_tried = {}           # loc_id -> 마지막 지오코딩 시도 ts(실패 재시도 억제)


def _lunch_geocode_bg(locs):
    """좌표 없는 위치를 '백그라운드'로 지오코딩(읽기 응답을 막지 않음). 실패는 10분간 재시도 안 함."""
    if not lunch.has_key():
        return
    need = [l for l in locs if l.get("lat") is None
            and time.time() - _lunch_geo_tried.get(l["id"], 0) > 600]
    if not need or not _lunch_geo_lock.acquire(blocking=False):
        return

    def _w():
        try:
            for l in need:
                _lunch_geo_tried[l["id"]] = time.time()
                try:
                    co = lunch.geocode(l.get("address") or l.get("name"))
                    if co:
                        db.lunch_set_location_coords(l["id"], co[0], co[1])
                        _LOCATION_READS.invalidate()
                        _invalidate_lunch_cache()
                except Exception as e:  # noqa: BLE001
                    print(f"[lunch] bg 지오코딩 실패({l.get('name')}): {e}", flush=True)
        finally:
            _lunch_geo_lock.release()

    threading.Thread(target=_w, daemon=True).start()


def _load_lunch_locations():
    global _lunch_synced
    if not _ensure_db(force=True):
        raise RuntimeError("DB unavailable")
    if not _lunch_synced:
        # sync inserts missing locations itself; a separate seed pass is redundant.
        db.lunch_sync_locations(LUNCH_OFFICES)
        from collector.lunch_history import import_history, rewrite_history, rewrite_varied_history
        with db.get_conn() as conn:
            import_history(conn, db._q, db._now())
            rewrite_history(conn, db._q, db._now())
            rewrite_varied_history(conn, db._q)
        _invalidate_lunch_cache()
        _lunch_synced = True
    return db.lunch_list_locations()


def _invalidate_lunch_cache():
    _PUBLIC_READS.invalidate("lunch:")
    _PUBLIC_READS.invalidate("/api/lunch/reviews")


@app.get("/api/lunch/locations")
def lunch_locations():
    locs = _LOCATION_READS.get("locations", _load_lunch_locations, ttl=300, wait=0)
    if locs is not None:
        _lunch_geocode_bg(locs)
    response = jsonify({"locations": locs or [], "kakao": lunch.has_key(),
                        "db_waking": locs is None})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/lunch/restaurants")
def lunch_restaurants():
    loc_id = request.args.get("loc", type=int)
    if not loc_id:
        return jsonify({"restaurants": [], "error": "위치 없음"}), 400
    admin = _admin_ok()  # capture before the cache worker leaves request context

    def load():
        if not _ensure_db(force=True):
            raise RuntimeError("DB unavailable")
        loc, rows = db.lunch_restaurant_data(loc_id, include_excluded=admin)
        if not loc:
            return {"restaurants": [], "error": "위치 없음"}
        _lunch_geocode_bg([loc])
        return {"location": loc, "restaurants": _lunch_decorate(loc, rows)}

    data = _PUBLIC_READS.get(f"lunch:restaurants:{loc_id}:{int(admin)}", load, ttl=30, wait=.15)
    response = jsonify(data if data is not None else {"restaurants": [], "db_waking": True})
    response.headers["Cache-Control"] = "private, no-store"
    if data is None:
        response.headers["X-Data-Pending"] = "1"
    return response


def _lunch_collect_run(loc_id):
    st = _LUNCH_JOB
    try:
        _ensure_db(force=True)
        loc = _lunch_geocode_if_needed(db.lunch_get_location(loc_id))
        if not loc or not loc.get("lat"):
            geo = lunch.LAST_GEO or {}
            reason = geo.get("reason")
            if reason == "unauthorized":
                body = (geo.get("body") or "").strip()
                err = ("카카오 키가 거부됐어요(%s). ① REST API 키가 맞는지 ② 카카오 앱 '보안 → 허용 IP 주소'가 설정돼 있으면 삭제(서버 IP가 막힘) ③ 값에 공백/따옴표 없는지 확인. 카카오 응답: %s"
                       % (geo.get("status", "401/403"), body[:160] or "(본문 없음)"))
            elif reason == "no_result":
                err = f"주소를 좌표로 못 바꿨어요(지오코딩 결과 없음). 주소 확인 또는 '＋ 직접 추가'로 등록하세요. [{loc.get('address') if loc else ''}]"
            else:
                err = "위치 좌표를 확인할 수 없어요(카카오 응답 오류). 잠시 후 다시 시도하세요."
            st["result"] = {"ok": False, "error": err}
            st["progress"] = f"오류: 좌표 없음({reason})"
            print(f"[lunch] geocode 실패 loc={loc_id} {lunch.LAST_GEO}", flush=True)
            return
        items = lunch.collect(loc["lat"], loc["lng"], loc.get("radius", 500),
                              progress=lambda m: st.update(progress=m))
        for it in items:
            it["source"] = "kakao"
        new, upd = db.lunch_upsert_restaurants(loc_id, items)
        st["result"] = {"ok": True, "new": new, "updated": upd, "total": len(items)}
        st["progress"] = f"완료 · 신규 {new} · 갱신 {upd}"
    except Exception as e:  # noqa: BLE001
        st["result"] = {"ok": False, "error": str(e)}
        st["progress"] = f"오류: {e}"
    finally:
        _invalidate_lunch_cache()
        st["running"] = False


@app.post("/api/lunch/collect")
def lunch_collect():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not lunch.has_key():
        return jsonify({"ok": False, "error": "KAKAO_REST_KEY 미설정 — 수동 등록만 가능"}), 400
    if _LUNCH_JOB.get("running") and (time.time() - _LUNCH_JOB.get("started_ts", 0) < 1800):
        return jsonify({"running": True, "already": True})
    loc_id = request.args.get("loc", type=int)
    _LUNCH_JOB.update(running=True, progress="수집 준비…", result=None, started_ts=time.time(), loc=loc_id)
    threading.Thread(target=_lunch_collect_run, args=(loc_id,), daemon=True).start()
    return jsonify({"running": True})


@app.get("/api/lunch/diag")
def lunch_diag():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(lunch.diag())


@app.get("/api/lunch/collect/status")
def lunch_collect_status():
    st = dict(_LUNCH_JOB)
    if st.get("running") and (time.time() - st.get("started_ts", 0) > 1800):
        _LUNCH_JOB["running"] = False
        st["running"] = False
    return jsonify({"running": st.get("running"), "progress": st.get("progress"),
                    "result": st.get("result"), "loc": st.get("loc")})


@app.post("/api/lunch/restaurant")
def lunch_add_restaurant():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db(force=True):
        return jsonify({"ok": False, "error": "DB 연결 실패"}), 503
    d = request.get_json(silent=True) or {}
    loc_id = d.get("loc_id")
    if not loc_id or not d.get("name"):
        return jsonify({"ok": False, "error": "loc_id·name 필요"}), 400
    d["cat_norm"] = lunch.normalize_category(d.get("category") or d.get("cat_norm") or "")
    new, upd = db.lunch_manual_add(loc_id, d)
    _invalidate_lunch_cache()
    return jsonify({"ok": True, "new": new, "updated": upd})


@app.post("/api/lunch/purge")
def lunch_purge():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db(force=True):
        return jsonify({"ok": False, "error": "DB 연결 실패"}), 503
    d = request.get_json(silent=True) or {}
    loc_id = d.get("loc_id")
    if not loc_id:
        return jsonify({"ok": False, "error": "loc_id 필요"}), 400
    n = db.lunch_purge_location(loc_id)
    _invalidate_lunch_cache()
    return jsonify({"ok": True, "deleted": n})


@app.post("/api/lunch/exclude")
def lunch_exclude():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    db.lunch_set_excluded(d.get("id"), bool(d.get("excluded", True)))
    _invalidate_lunch_cache()
    return jsonify({"ok": True})


@app.get("/api/lunch/reviews")
def lunch_reviews():
    rid = request.args.get("rid", type=int)
    return _safe_list(lambda: db.lunch_list_reviews(rid), ttl=30)


@app.post("/api/lunch/review")
def lunch_review():
    user = _cur_user()
    if not user:
        return jsonify({"ok": False, "error": "로그인이 필요해요"}), 401
    if not _ensure_db(force=True):
        return jsonify({"ok": False, "error": "DB 연결 실패"}), 503
    d = request.get_json(silent=True) or {}
    rid = d.get("rid")
    rating = d.get("rating")
    if not rid or not rating:
        return jsonify({"ok": False, "error": "rid·rating 필요"}), 400
    rating = max(1, min(5, int(rating)))
    db.lunch_add_review(rid, user, rating, (d.get("comment") or "").strip()[:300])
    if d.get("visit"):
        db.lunch_add_visit(rid, user)
    _invalidate_lunch_cache()
    return jsonify({"ok": True})


@app.post("/api/lunch/visit")
def lunch_visit():
    user = _cur_user()
    if not user:
        return jsonify({"ok": False, "error": "로그인이 필요해요"}), 401
    if not _ensure_db(force=True):
        return jsonify({"ok": False, "error": "DB 연결 실패"}), 503
    d = request.get_json(silent=True) or {}
    if not d.get("rid"):
        return jsonify({"ok": False, "error": "rid 필요"}), 400
    db.lunch_add_visit(d["rid"], user)
    _invalidate_lunch_cache()
    return jsonify({"ok": True})


# ── 맛집 AI 추천 엔진: 가중 점수 + 상위5 가중랜덤(같은 집만 나오는 것 방지) ──
# 사용 가능한 실제 데이터만 사용(메뉴/가격/영업시간/날씨는 미보유 → 지어내지 않음).
LUNCH_PERSONAS = {
    "safe":     {"label": "🎯 안전빵"},
    "adventure": {"label": "🎲 모험"},
    "cheap":    {"label": "💸 월급루팡"},
    "premium":  {"label": "👑 오늘은 제대로"},
    "fast":     {"label": "🏃 빨리 먹자"},
    "world":    {"label": "🌏 세계여행"},
    "hidden":   {"label": "🕵️ 숨은 맛집"},
    "comeback": {"label": "🔄 오랜만이야"},
}
_CHEAP_CATS = {"분식", "면요리", "한식"}
_FAST_CATS = {"분식", "면요리", "돈까스", "한식"}
_HEARTY_CATS = {"고기", "한식", "돈까스"}
_MILD_CATS = {"한식", "면요리", "샐러드/건강식"}
_LIGHT_CATS = {"샐러드/건강식", "카페/디저트", "분식"}
_COMFORT_CATS = {"고기", "중식", "분식"}
_WORLD_CATS = {"중식", "일식", "아시아음식", "양식", "생선/해산물"}


def _lunch_score(cands, avoid_ids, all_visited, recent3, recent7, avoid_cats, moods, persona, ctx):
    """후보별 추천점수 계산 → (정렬된 [(score, cand)], relaxed?) 반환."""
    import random
    moods, avoid_cats = set(moods or []), set(avoid_cats or [])
    pool = [c for c in cands if c["id"] not in avoid_ids and (c.get("cat_norm") not in avoid_cats)]
    relaxed = False
    if not pool:  # 조건이 너무 좁으면 회피카테고리만 유지하고 완화
        pool = [c for c in cands if c.get("cat_norm") not in avoid_cats] or list(cands)
        relaxed = True
    # 기본 가중치(합 100) — 기분/페르소나로 조정
    # 기본값에서 리뷰·평점(trust) 비중은 낮게. '검증된 곳 우선'(trusted)/안전 성향에서만 크게 올린다.
    W = {"trust": 10.0, "dist": 16.0, "situ": 16.0, "variety": 18.0, "explore": 14.0, "team": 12.0, "rand": 14.0}
    if "near" in moods: W["dist"] *= 1.8
    if "trusted" in moods: W["trust"] *= 4.0   # 검증된 곳 우선 → 리뷰·평점 비중 크게
    if "explore" in moods: W["explore"] *= 2.0
    if persona == "safe": W["trust"] *= 3.0; W["explore"] *= 0.4; W["rand"] *= 0.6
    elif persona == "adventure": W["explore"] *= 2.4; W["trust"] *= 0.5; W["team"] *= 0.6
    elif persona == "premium": W["trust"] *= 1.7; W["dist"] *= 0.4; W["rand"] *= 0.6
    elif persona == "fast": W["dist"] *= 2.2
    elif persona == "hidden": W["trust"] *= 0.6
    scored = []
    for c in pool:
        cat = c.get("cat_norm") or "기타"
        rating = c.get("avg_rating") or 0
        rc = c.get("review_count") or 0
        vc = c.get("visit_count") or 0
        walk = c.get("walk_min")
        # 리뷰/평점 선호는 기본값에서 과하지 않게(무평점도 불이익 최소화). 강한 리뷰 선호는
        # '검증된 곳 우선'(trusted)·'안전'(safe) 옵션에서 가중치를 키워 적용한다.
        trust = ((rating / 5.0) if rating else 0.55) * (0.72 + 0.28 * min(rc, 8) / 8.0)
        dist = 1.0 - min(walk if walk is not None else 12, 20) / 20.0
        situ = 0.5
        if ctx["weekday"] == 0 and rating: situ += 0.2 * (rating / 5.0)          # 월: 검증된 곳
        if ctx["weekday"] == 4 and cat in {"고기", "양식", "일식", "생선/해산물"}: situ += 0.2  # 금: 특별
        if ctx["hour"] >= 13 and walk is not None: situ += 0.15 * (1 - min(walk, 15) / 15.0)  # 늦은 점심 가까이
        situ = max(0.0, min(1.0, situ))
        variety = 0.1 if cat in recent3 else (0.5 if cat in recent7 else 1.0)
        explore = (1.0 / (1.0 + vc)) if vc else (1.0 if rc == 0 else 0.7)
        team = min(vc, 10) / 10.0
        score = (W["trust"] * trust + W["dist"] * dist + W["situ"] * situ + W["variety"] * variety
                 + W["explore"] * explore + W["team"] * team + W["rand"] * random.random())
        # 페르소나/기분 카테고리 보너스(가감점)
        b = 0.0
        if persona == "cheap" and cat in _CHEAP_CATS: b += 18
        if persona == "fast" and cat in _FAST_CATS: b += 15
        if persona == "world":
            if cat in _WORLD_CATS: b += 25 if cat not in recent7 else 10
            else: b -= 12  # 한식/분식/패스트푸드 등 비(非)세계요리 감점
        if persona == "hidden" and rating: b += 45 * ((rating / 5.0) * (1.0 / (1.0 + rc)))
        if persona == "comeback" and (c["id"] in all_visited) and (c["id"] not in avoid_ids): b += 22
        if "quick" in moods and cat in _FAST_CATS: b += 10
        if "hearty" in moods and cat in _HEARTY_CATS: b += 10
        if "mild" in moods:
            b += 10 if cat in _MILD_CATS else 0
            if cat in {"고기", "중식", "양식", "패스트푸드"}: b -= 10
        if "comfort" in moods and cat in _COMFORT_CATS: b += 10
        if "light" in moods:
            b += 10 if cat in _LIGHT_CATS else 0
            if cat in {"고기", "돈까스"}: b -= 10
        if "spicy" in moods and cat in {"중식", "한식", "면요리"}: b += 10   # 얼큰(짬뽕/찌개/국밥/칼국수)
        if "rainy" in moods and cat in {"한식", "면요리"}: b += 10           # 비 오면 국물
        if "solo" in moods and cat in {"분식", "면요리", "한식", "돈까스"}: b += 8  # 혼밥 편한
        if "sweet" in moods and cat in {"카페/디저트"}: b += 14              # 단 거
        if "no_oily" in moods and cat in {"양식", "돈까스", "패스트푸드", "중식"}: b -= 12  # 느끼한 거 회피
        scored.append((score + b, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored, relaxed


def _lunch_reason(c, persona, moods, recent3, recent7):
    """추천 태그 + 한 줄 이유(있는 사실만: 거리·평점·방문·카테고리)."""
    moods = set(moods or [])
    tags = []
    pm = LUNCH_PERSONAS.get(persona)
    if pm: tags.append(pm["label"])
    if c.get("walk_min") is not None and c["walk_min"] <= 3: tags.append(f"🚶 도보 {c['walk_min']}분")
    if c.get("avg_rating"): tags.append(f"⭐ {c['avg_rating']}")
    if not c.get("review_count") and not c.get("visit_count"): tags.append("✨ 안 가본 곳")
    cat = c.get("cat_norm") or ""
    persona_line = {
        "safe": "실패 없는 선택 — 평점·방문이 검증된 곳이에요.",
        "adventure": "오늘은 모험! 안 가본 곳으로 질러봐요.",
        "cheap": "월급루팡 모드 — 부담 없이 가볍게 한 끼.",
        "premium": "오늘은 제대로 — 가격보다 만족도로 골랐어요.",
        "fast": "빨리 먹고 들어오기 좋게 가까운 곳으로.",
        "world": "입맛 여행 — 요즘 안 먹은 스타일이에요.",
        "hidden": "리뷰는 적지만 평이 좋은 숨은 집.",
        "comeback": "요즘 뜸했던 곳, 오랜만에 어때요?",
    }.get(persona, "")
    bits = []
    if cat: bits.append(cat)
    if c.get("walk_min") is not None: bits.append(f"도보 {c['walk_min']}분")
    if c.get("avg_rating"): bits.append(f"평점 {c['avg_rating']}({c.get('review_count', 0)})")
    if "near" in moods: bits.append("가까운 데")
    if "hearty" in moods: bits.append("든든")
    if "mild" in moods: bits.append("속 편한")
    if "light" in moods: bits.append("가볍게")
    if cat and cat not in recent7: bits.append("최근 안 먹은 메뉴")
    tail = " · ".join(bits)
    reason = (persona_line + (" " if persona_line else "") + (f"({tail})" if tail else "")).strip()
    return tags, (reason or "오늘 여기 어때요?")


def _lunch_ai_pick(cands, avoid_ids, all_visited, recent3, recent7, avoid_cats, moods, persona):
    import random
    if not cands:
        return {"ok": False, "error": "추천할 식당이 없어요. 먼저 수집하거나 추가해 주세요."}
    ctx = _lunch_now_ctx()
    scored, relaxed = _lunch_score(cands, avoid_ids, all_visited, recent3, recent7,
                                   avoid_cats, moods, persona, ctx)
    if not scored:
        return {"ok": False, "error": "조건에 맞는 식당이 없어요. 조건을 줄여보세요."}
    top = scored[:5]
    pick = random.choices([c for _, c in top], weights=[max(0.1, s) for s, _ in top], k=1)[0]
    alts = [c for _, c in top if c["id"] != pick["id"]][:2]
    tags, reason = _lunch_reason(pick, persona, moods, recent3, recent7)
    return {"ok": True, "engine": (persona or "mix"),
            "pick": {**pick, "reason": reason}, "alternatives": alts, "tags": tags, "relaxed": relaxed}


def _lunch_now_ctx():
    now = datetime.now(KST)
    return {"weekday": now.weekday(), "hour": now.hour}  # 월=0 … 일=6


@app.post("/api/lunch/recommend")
def lunch_recommend():
    if not _ensure_db():
        return jsonify({"ok": False, "error": "db"}), 503
    d = request.get_json(silent=True) or {}
    loc_id = d.get("loc_id")
    loc = _lunch_geocode_if_needed(db.lunch_get_location(loc_id)) if loc_id else None
    if not loc:
        return jsonify({"ok": False, "error": "위치 없음"}), 400
    cands = _lunch_decorate(loc, db.lunch_list_restaurants(loc_id))
    ids = d.get("candidate_ids")
    if ids:
        idset = set(ids)
        cands = [c for c in cands if c["id"] in idset]
    user = _cur_user()
    avoid_ids, recent3, recent7, all_visited = (db.lunch_visit_context(user) if user
                                                else (set(), set(), set(), set()))
    persona = (d.get("persona") or "").strip()
    if persona not in LUNCH_PERSONAS:
        persona = ""
    result = _lunch_ai_pick(cands, avoid_ids, all_visited, recent3, recent7,
                            d.get("avoid_cats"), d.get("moods"), persona)
    return jsonify(result)


# 수집 구현 현황(화면 뱃지용). 완료 / 구현 중 / 구현 예정
BOARD_STATUS = {"나의AAC": "완료", "프로젝토리": "완료", "FAIR AI": "완료", "대표 홈페이지": "완료"}
SOCIAL_STATUS = {"유튜브": "완료"}


@app.route("/favicon.ico")
def favicon():
    # 파비콘을 직접 요청하는 브라우저(/favicon.ico) 대비 → SVG 파비콘으로 넘김.
    from flask import redirect, url_for as _url_for
    return redirect(_url_for("static", filename="favicon.svg"), code=302)


def _asset_ver():
    """정적 파일(app.js/style.css) 수정 시각 기반 캐시 버스터. 파일이 바뀌면 자동으로 값이 달라져
    브라우저가 새 파일을 받아온다(옛 v=고정값으로 인한 스테일 캐시 방지)."""
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        mt = max(os.path.getmtime(os.path.join(base, "static", path))
                 for path in ("js/app.js", "css/style.css", "js/reader.js", "css/reader.css", "js/finance.js", "css/finance.css", "js/events-ui.js", "css/events-ui.css"))
        return str(int(mt))
    except Exception:  # noqa: BLE001
        return "1"


@app.route("/intro")
def intro():
    # 주변 테스터에게 공유하는 서비스 소개(랜딩) 페이지. 로그인·DB 없이 정적으로 뜬다.
    return render_template("intro.html")


@app.route("/")
def index():
    _log_visit()  # 방문 기록(백그라운드, 페이지 로딩 안 막음)
    services = sorted({s["service"] for s in boards.SOURCES})
    channels = sorted({s["channel"] for s in social.SOURCES})
    board_status = [{"name": n, "status": BOARD_STATUS.get(n, "완료")} for n in services]
    social_status = [{"name": n, "status": SOCIAL_STATUS.get(n, "완료")} for n in channels]
    news_categories = list(google_news.CATEGORIES.keys())  # 재단 / 본사
    cat_categories = list(google_news.CAT_CATEGORIES.keys())  # 고양이 뉴스 카테고리
    game_categories = list(google_news.GAME_CATEGORIES.keys())  # 게임 뉴스 카테고리
    return render_template(
        "index.html", services=services, channels=channels,
        board_status=board_status, social_status=social_status,
        news_categories=news_categories, cat_categories=cat_categories,
        game_categories=game_categories, asset_ver=_asset_ver(),
    )


# ---------------------------- 조회 API ----------------------------
# 조회 결과 인메모리 캐시(경로+쿼리 기준, 짧은 TTL). 이미 수집된 데이터는 자주 안 바뀌므로
# 반복 호출/재불러오기를 즉시 응답해 속도↑·DB부하↓. DB가 잠깐 죽어도 '마지막 캐시'를 내줘서
# 화면이 비지 않고, '다시 불러오기'를 연타할 필요가 없다. 수집/초기화 시 캐시를 비운다.
_READ_TTL = 45


def _invalidate_read_cache():
    _PUBLIC_READS.invalidate()


def _safe_list(fetch, ttl=_READ_TTL):
    # Ignore cache-busting/unknown parameters; only actual query filters form keys.
    names = (("kind", "id") if request.path.startswith("/api/report/") else
             ("rid",) if request.path == "/api/lunch/reviews" else
             ("service",) if request.path == "/api/boards" else
             ("channel",) if request.path == "/api/social" else ("category",))
    filters = tuple((key, request.args.get(key, "all")) for key in names)
    key = request.path + repr(filters)

    def load():
        if not _ensure_db(force=True):
            raise RuntimeError("DB unavailable")
        return fetch()

    # 첫 요청이 거의 항상 pending([])으로 떨어지지 않도록 대기시간을 늘림(백그라운드 로딩을 기다렸다 반환).
    data = _PUBLIC_READS.get(key, load, ttl=ttl, wait=3.0)
    response = jsonify(data if data is not None else [])
    response.headers["Cache-Control"] = "no-store"
    if data is None:
        response.headers["X-Data-Pending"] = "1"
    return response


def _months_arg():
    """months 쿼리 파라미터 → 최근 N개월(양수)만, 0/없음이면 전체. 초기 로딩 가속용."""
    try:
        m = int(request.args.get("months", 0))
        return m if m > 0 else None
    except (TypeError, ValueError):
        return None


# 주의: months/category 등 request 의존 값은 '요청 스레드'에서 미리 읽어 캡처한다.
# _safe_list의 로드는 백그라운드 워커 스레드에서 돌 수 있어, 람다 안에서 request를
# 건드리면 "Working outside of request context."로 매번 실패 → 영구 pending([]) → '불러오지 못했어요'.
@app.get("/api/news")
def get_news():
    category = request.args.get("category", "all")
    months = _months_arg()
    return _safe_list(lambda: db.list_news(category=category, section="nc", months=months))


@app.get("/api/catnews")
def get_catnews():
    category = request.args.get("category", "all")
    months = _months_arg()
    return _safe_list(lambda: db.list_news(category=category, section="cat", months=months))


@app.get("/api/gamenews")
def get_gamenews():
    category = request.args.get("category", "all")
    months = _months_arg()
    return _safe_list(lambda: db.list_news(category=category, section="game", months=months))


@app.get("/api/biznews")
def get_biznews():
    months = _months_arg()
    return _safe_list(lambda: db.list_news(section="biz", months=months))


@app.get("/api/secnews")
def get_secnews():
    category = request.args.get("category", "all")
    months = _months_arg()
    return _safe_list(lambda: db.list_news(category=category, section="sec", months=months))


@app.get("/api/events")
def get_events():
    return _safe_list(lambda: db.list_events())


@app.post("/api/events/recommend")
def recommend_events():
    try:
        prefs = event_curation.preferences(request.get_json(silent=True))
    except ValueError as error:
        return jsonify({"notice": str(error)}), 400
    response = jsonify(event_curation.recommend(prefs))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/img")
def img_proxy():
    """뉴스 대표이미지 프록시: 서버가 대신 받아 넘겨준다(언론사 핫링크 차단 우회).
    원본이 http여도, 외부 직접 로딩을 막아도 화면에 뜨게 한다."""
    from urllib.parse import urlsplit
    u = request.args.get("u", "")
    if not u.startswith("http"):
        return ("", 404)
    sp = urlsplit(u)
    ref = f"{sp.scheme}://{sp.netloc}/"
    try:
        r = fetcher.get(u, headers={"Referer": ref, "Accept": "image/avif,image/webp,image/*,*/*"},
                        retries=0, timeout=10, raise_status=False)
        ct = (r.headers.get("content-type") or "").split(";")[0].strip()
        if r.status_code >= 400 or not ct.startswith("image"):
            return ("", 404)  # 실패 시 404 → 화면에서 onerror로 썸네일 제거
        resp = Response(r.content, content_type=ct)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp
    except Exception:  # noqa: BLE001
        return ("", 404)


@app.get("/api/ytcheck")
def ytcheck():
    """유튜브 Data API 상태 진단: 키 설정 여부·채널·수집 가능 여부(비밀값은 노출 안 함)."""
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    out = {"key_set": bool(key), "key_len": len(key)}
    if not key:
        out["hint"] = "Render 환경변수 YOUTUBE_API_KEY 미설정 → RSS(최신 15개)만 수집됨"
        return jsonify(out)
    cfg = next((s for s in social.SOURCES if s.get("type") == "youtube"), None)
    if not cfg:
        return jsonify(out)
    try:
        import json as _json
        cid = social._youtube_channel_id(cfg["url"])
        out["channel_id"] = cid
        uploads = "UU" + cid[2:]
        r = fetcher.get(social.YT_DATA_API,
                        params={"part": "snippet", "maxResults": 3, "playlistId": uploads, "key": key},
                        retries=0, timeout=12, raise_status=False)
        out["http_status"] = r.status_code
        j = r.json()
        if r.status_code >= 400:
            out["error"] = (j.get("error") or {}).get("message", "")[:200]
        else:
            out["sample_count"] = len(j.get("items", []))
            out["total_estimate"] = (j.get("pageInfo") or {}).get("totalResults")
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return jsonify(out)


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
    if group in ("all", "cat"):
        from urllib.parse import quote as _quote
        targets.append(
            ("구글 뉴스(RSS) · 고양이", google_news.RSS_URL + "?q=" + _quote("고양이 반려묘") + "&hl=ko&gl=KR&ceid=KR:ko", None)
        )
    if group in ("all", "game"):
        from urllib.parse import quote as _quote
        targets.append(
            ("구글 뉴스(RSS) · 게임", google_news.RSS_URL + "?q=" + _quote("게임 신작 출시") + "&hl=ko&gl=KR&ceid=KR:ko", None)
        )
    if group in ("all", "biz"):
        from urllib.parse import quote as _quote
        targets.append(
            ("구글 뉴스(RSS) · 업계동향", google_news.RSS_URL + "?q=" + _quote("문화재단") + "&hl=ko&gl=KR&ceid=KR:ko", None)
        )
    if group in ("all", "security"):
        from urllib.parse import quote as _quote
        targets.append(
            ("구글 뉴스(RSS) · 보안뉴스", google_news.RSS_URL + "?q=" + _quote("개인정보 유출") + "&hl=ko&gl=KR&ceid=KR:ko", None)
        )
    if group in ("all", "event"):
        from urllib.parse import quote as _quote
        targets.append(
            ("구글 뉴스(RSS) · 행사일정", google_news.RSS_URL + "?q=" + _quote("인공지능 컨퍼런스 개최") + "&hl=ko&gl=KR&ceid=KR:ko", None)
        )
    if group in ("all", "boards"):
        import json as _json
        _fair_body = _json.dumps({"page": 1, "rowsPerPage": 5, "sortBy": "createdAt",
                                  "sortType": "desc", "keyword": None})
        seen = set()
        for s in boards.SOURCES:
            # API 방식 소스는 list_url 대신 api/projectory_api/fairai_api 주소로 점검
            chk = s.get("list_url") or s.get("api") or s.get("projectory_api") or s.get("fairai_api")
            if not chk or chk in seen:
                continue
            seen.add(chk)
            t = {"name": f"{s['service']} · {s['category']}", "url": chk, "sel": s.get("item_link_sel")}
            if s.get("fairai_api"):  # FAIR AI는 POST API라 POST로 점검
                t["method"], t["body"] = "post", _fair_body
            targets.append(t)
    if group in ("all", "social"):
        for s in social.SOURCES:
            if s.get("url"):
                targets.append({"name": f"{s['channel']} · {s['account']}", "url": s["url"], "sel": None})

    def check(item):
        # 튜플(뉴스/고양이) 또는 dict(게시판/소셜) 모두 지원
        if isinstance(item, tuple):
            item = {"name": item[0], "url": item[1], "sel": item[2]}
        name, url, sel = item["name"], item["url"], item.get("sel")
        t0 = _t.time()
        try:
            if item.get("method") == "post":
                r = fetcher.post(url, data=item.get("body"), retries=0, raise_status=False,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/plain, */*"})
            else:
                # raise_status=False: 4xx여도 '연결됨'으로 본다(서버가 응답했으니 도달 성공)
                r = fetcher.get(url, retries=0, raise_status=False)
            body = r.text or ""
            res = {
                "name": name, "url": url, "ok": True,  # 응답을 받았으면 연결 성공
                "status": r.status_code, "bytes": len(body),
                "looks_html": "<html" in body.lower() or "<!doctype" in body.lower(),
                "sec": round(_t.time() - t0, 1),
            }
            if sel:
                from bs4 import BeautifulSoup
                res["items"] = len(boards._select_any(BeautifulSoup(body, "lxml"), sel))
            return res
        except Exception as e:  # noqa: BLE001  (네트워크 자체 실패만 오류)
            return {
                "name": name, "url": url, "ok": False,
                "error": type(e).__name__ + ": " + str(e)[:160],
                "sec": round(_t.time() - t0, 1),
            }

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, targets))
    # 행사 구조화 소스(관광공사·문화포털)도 상태에 반영 — 설정된 것만, 서비스키는 노출하지 않음.
    if group in ("all", "event"):
        try:
            esd = event_sources.diagnose()
            for kname, label in (("tour_festivals", "관광공사 행사/축제"), ("culture_events", "문화포털 행사")):
                d = esd.get(kname) or {}
                if not d.get("configured"):
                    continue
                raw = d.get("raw") or {}
                results.append({"name": label, "url": "공공데이터포털 API", "ok": True,
                                "status": raw.get("status", 200), "bytes": 0,
                                "looks_html": True, "items": int(d.get("parsed", 0)), "sec": 0})
        except Exception:  # noqa: BLE001
            pass
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
        lu = s.get("list_url") or s.get("api") or s.get("projectory_api") or s.get("fairai_api")
        if lu and lu not in seen:
            seen.add(lu)
            urls.append(lu)
    with ThreadPoolExecutor(max_workers=8) as pool:
        return jsonify(list(pool.map(inspector.inspect_url, urls)))


# ---------------------------- 수집 API (동기 방식) ----------------------------
# 수집은 요청 한 번에 끝까지 처리하고 결과를 바로 반환한다. (구조가 단순해 어떤
# 버전의 프론트엔드 JS가 캐시돼 있어도 호환되며, 무료 호스팅 재시작에도 안전)
# 마지막 결과는 /api/crawl/status 폴링형 프론트와의 호환을 위해 보관한다.
_last_result = {"news": None, "cat": None, "biz": None, "security": None, "boards": None, "social": None}


# 저장 정책: 키 = 원문 URL.
#  - 뉴스는 '동일 기사(여러 매체 배포)'도 전부 저장한다(중복 제거 X). 대신 저장 후
#    전체를 본문/제목 유사도로 클러스터링해 group_key를 부여 → 화면에서 아코디언 묶음.
def _save_news(items, section="nc"):
    for item in items:
        item["content_hash"] = dedup.content_hash(item.get("content", ""))
        item["section"] = section
    new, updated = db.upsert_news_many(items)

    # 같은 섹션(nc/cat) 안에서만 '같은 기사' 그룹화(group_key 부여)
    rows = db.all_news_min(section)
    keys = dedup.cluster_items(rows)
    url_to_key = {r["url"]: k for r, k in zip(rows, keys) if r.get("url")}
    db.set_news_group_keys(url_to_key)
    groups = len(set(url_to_key.values()))
    return {"new": new, "updated": updated, "duplicates": 0, "groups": groups}


def _save_cat(items):
    return _save_news(items, section="cat")


def _save_game(items):
    return _save_news(items, section="game")


def _save_biz(items):
    return _save_news(items, section="biz")


def _save_security(items):
    return _save_news(items, section="sec")


def _purge_biz_nc(progress=None):
    """업계동향(biz)에 섞여 들어온 NC 관련 기사를 삭제(NC뉴스 탭과 분리). 필터 강화 이전 잔여분 정리."""
    try:
        ex = [x.lower() for x in google_news.BIZ_EXCLUDE_BODY]
        rows = db.list_news(section="biz", limit=5000)
        bad = [r["url"] for r in rows
               if any(x in f"{r.get('title', '')} {r.get('content', '')}".lower() for x in ex)]
        if bad:
            db.delete_news_by_urls(bad)
            print(f"[crawl] 업계동향 NC 노이즈 {len(bad)}건 정리", flush=True)
            if progress:
                progress(f"NC 노이즈 {len(bad)}건 정리")
    except Exception as e:  # noqa: BLE001
        print(f"[crawl] 업계동향 정리 실패: {e}", flush=True)


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


def _save_event(items):
    new, updated = db.upsert_event_many(items)
    return {"new": new, "updated": updated, "duplicates": 0}


_CRAWLERS = {
    "news": (google_news.crawl, _save_news),
    "cat": (google_news.crawl_cat, _save_cat),
    "game": (google_news.crawl_game, _save_game),
    "biz": (google_news.crawl_biz, _save_biz),
    "security": (google_news.crawl_security, _save_security),
    "event": (events.crawl, _save_event),
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
        if group in ("news", "cat", "game", "biz", "security", "event"):
            # 뉴스류(뉴스/냥정보/게임/업계동향/보안뉴스/행사일정): 수집 기간(days) 전달. 저장 시 ON CONFLICT로 중복 처리.
            items = crawl_fn(progress=progress, days=days)
        else:
            items = crawl_fn(progress=progress)
        progress("저장·그룹화 중…")
        counts = save_fn(items)
        result = {"crawled": len(items), **counts}
        if group == "news":
            _purge_news_noise(progress)  # 기존에 쌓인 본사 노이즈(야구/백화점 등) 정리
        if group == "biz":
            _purge_biz_nc(progress)  # 업계동향에 섞인 NC 기사 정리(NC뉴스와 분리)
        if group in ("news", "cat", "game", "biz", "security"):
            _enrich_news_images(progress)  # 이미지 없는 최근 기사에 대표 이미지(og:image) 보강
        if group == "security":
            _run_security_ai(progress)  # 보안뉴스는 수집 직후 AI 분석(태깅·중요도·시사점) 자동 실행
        _invalidate_read_cache()  # 새로 수집됐으니 조회 캐시 갱신(다음 조회에 즉시 반영)
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


# ---------------------------- AI 분석 리포트 ----------------------------
_REPORT_JOB = {"running": False, "progress": "", "result": None, "started_ts": 0}


def _report_run(window_days):
    st = _REPORT_JOB
    try:
        _ensure_db(force=True)
        data, err = analysis.run(window_days=window_days, progress=lambda m: st.update(progress=m))
        if err:
            fe = analysis.friendly_llm_error(err)
            st["result"] = {"error": fe}
            st["progress"] = f"오류: {fe}"
        else:
            label = _now_kst()
            period = data.get("period_label") or f"최근 {window_days}일"
            sid = db.save_report_snapshot(label, label, period,
                                          data.get("_meta", {}).get("model", ""),
                                          json.dumps(data, ensure_ascii=False),
                                          pkey=data.get("_meta", {}).get("pkey"))
            _PUBLIC_READS.invalidate("/api/report/")
            st["result"] = {"ok": True, "id": sid}
            st["progress"] = "완료"
    except Exception as e:  # noqa: BLE001
        st["result"] = {"error": str(e)}
        st["progress"] = f"오류: {e}"
    finally:
        st["running"] = False


_SECREPORT_JOB = {"running": False, "progress": "", "result": None, "started_ts": 0}


def _security_report_run(ym):
    st = _SECREPORT_JOB
    try:
        _ensure_db(force=True)
        data, err = security_report.run(ym=ym, progress=lambda m: st.update(progress=m))
        if err:
            fe = analysis.friendly_llm_error(err)
            st["result"] = {"error": fe}
            st["progress"] = f"오류: {fe}"
        else:
            m = data.get("_meta", {})
            label = _now_kst()
            month = m.get("month", ym)
            sid = db.save_report_snapshot(label, label, f"{month} 월간", m.get("model", ""),
                                          json.dumps(data, ensure_ascii=False),
                                          pkey=f"secmonth:{month}", kind="security")
            _PUBLIC_READS.invalidate("/api/report/")
            st["result"] = {"ok": True, "id": sid, "month": month}
            st["progress"] = "완료"
    except Exception as e:  # noqa: BLE001
        st["result"] = {"error": str(e)}
        st["progress"] = f"오류: {e}"
    finally:
        st["running"] = False


@app.post("/api/report/run")
def report_run():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    kind = request.args.get("kind", "foundation")
    if kind == "security":
        if _SECREPORT_JOB.get("running") and (time.time() - _SECREPORT_JOB.get("started_ts", 0) < 1800):
            return jsonify({"running": True, "already": True})
        ym = request.args.get("month") or security_report.prev_month()
        _SECREPORT_JOB.update(running=True, progress="보안 리포트 준비…", result=None, started_ts=time.time())
        threading.Thread(target=_security_report_run, args=(ym,), daemon=True).start()
        return jsonify({"running": True, "month": ym})
    if _REPORT_JOB.get("running") and (time.time() - _REPORT_JOB.get("started_ts", 0) < 1800):
        return jsonify({"running": True, "already": True})
    window = request.args.get("window", default=90, type=int)
    _REPORT_JOB.update(running=True, progress="분석 준비…", result=None, started_ts=time.time())
    threading.Thread(target=_report_run, args=(window,), daemon=True).start()
    return jsonify({"running": True})


@app.get("/api/report/status")
def report_status():
    job = _SECREPORT_JOB if request.args.get("kind") == "security" else _REPORT_JOB
    st = dict(job)
    if st.get("running") and (time.time() - st.get("started_ts", 0) > 1800):
        job["running"] = False
        st["running"] = False
        st["progress"] = "중단됨(시간 초과)"
    return jsonify({"running": st.get("running"), "progress": st.get("progress"),
                    "result": st.get("result")})


@app.get("/api/report/models")
def report_models():
    """이 키로 쓸 수 있는 LLM 모델 목록(진단용, 관리자)."""
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"models": analysis.list_models(), "picked": analysis.resolve_model()})


@app.get("/api/report/list")
def report_list():
    kind = request.args.get("kind")  # None=전체
    return _safe_list(lambda: db.list_report_snapshots(30, kind=kind))


@app.post("/api/report/purge")
def report_purge():
    """리포트 스냅샷 삭제(관리자). body: {"id": N} → 해당 1건, {"all": true} → 전체."""
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if not _ensure_db(force=True):
        return jsonify({"ok": False, "error": "DB에 연결할 수 없어요. 잠시 후 다시 시도해 주세요."}), 503
    data = request.get_json(silent=True) or {}
    try:
        if data.get("all"):
            n = db.clear_report_snapshots(kind=data.get("kind"))  # kind 지정 시 해당 종류만
            _PUBLIC_READS.invalidate("/api/report/")
            return jsonify({"ok": True, "deleted": n, "scope": "all"})
        sid = data.get("id")
        if sid is None:
            return jsonify({"ok": False, "error": "삭제할 스냅샷 id가 없어요."}), 400
        n = db.delete_report_snapshot(int(sid))
        _PUBLIC_READS.invalidate("/api/report/")
        return jsonify({"ok": True, "deleted": n, "scope": "one"})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"삭제 실패: {e}"}), 500


@app.get("/api/report/get")
def report_get():
    sid = request.args.get("id", type=int)
    kind = request.args.get("kind")  # id 없이 최신 요청 시 종류 지정
    def load():
        row = db.get_report_snapshot(sid) if sid else db.latest_report_snapshot(0, kind=kind)
        if not row:
            return {"error": "없음", "empty": True}
        try:
            data = json.loads(row["data"] or "{}")
        except Exception:  # noqa: BLE001
            data = {}
        # 직전 스냅샷 id(비교용) 함께 전달
        prev = None
        lst = db.list_report_snapshots(30, kind=(row.get("kind") or kind))
        ids = [r["id"] for r in lst]
        if row["id"] in ids:
            i = ids.index(row["id"])
            prev = ids[i + 1] if i + 1 < len(ids) else None
        return {"id": row["id"], "created_at": row["created_at"], "period": row["period"],
                        "model": row["model"], "prev_id": prev, "data": data}
    return _safe_list(load)


# ---------------------------- 보안뉴스 AI 후처리(태깅·중요도·시사점) ----------------------------
_SECAI_JOB = {"running": False, "progress": "", "result": None, "started_ts": 0}
SECAI_LIMIT = int(os.environ.get("SEC_AI_LIMIT", "60"))  # 1회 분석 배치 상한(비용 관리)
SECAI_CRAWL_MAX = int(os.environ.get("SEC_AI_CRAWL_MAX", "200"))  # 수집 1회당 자동분석 총 상한


def _run_security_ai(progress=None):
    """보안뉴스 수집 직후 자동 AI 분석. 아직 분석 안 된 기사만(증분), 수집 1회당 총 SECAI_CRAWL_MAX까지.
    ANTHROPIC_API_KEY 없으면 조용히 skip(수집 자체는 정상). 나머지 미분석분은 다음 수집에서 이어서."""
    progress = progress or (lambda m: None)
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return
    done = 0
    try:
        while done < SECAI_CRAWL_MAX:
            rows = db.security_needs_ai(min(SECAI_LIMIT, SECAI_CRAWL_MAX - done))
            if not rows:
                break
            progress(f"AI 분석 {done}건 처리…")
            data, err = security_ai.analyze(rows, progress=progress)
            if not data:
                break  # 키/모델 문제 등 → 중단(다음 수집에서 재시도)
            n = db.apply_security_ai(data)
            done += n
            if n < len(rows):
                break  # 일부만 성공 → 무한루프 방지(나머지는 다음 수집에서)
        if done:
            print(f"[secai] 수집 후 자동 분석 {done}건", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[secai] 자동 분석 실패: {e}", flush=True)


def _security_ai_run(limit):
    st = _SECAI_JOB
    try:
        _ensure_db(force=True)
        rows = db.security_needs_ai(limit)
        if not rows:
            st["result"] = {"ok": True, "analyzed": 0, "note": "새로 분석할 보안뉴스가 없어요."}
            st["progress"] = "완료 · 대상 없음"
            return
        st["progress"] = f"AI 분석 0/{len(rows)}"
        data, err = security_ai.analyze(rows, progress=lambda m: st.update(progress=m))
        if data:
            n = db.apply_security_ai(data)
            st["result"] = {"ok": True, "analyzed": n, "error": err}
            st["progress"] = f"완료 · {n}건 분석" + (" (일부 실패)" if err else "")
        else:
            st["result"] = {"ok": False, "error": err or "분석 결과 없음"}
            st["progress"] = f"오류: {err or '결과 없음'}"
    except Exception as e:  # noqa: BLE001
        st["result"] = {"ok": False, "error": str(e)}
        st["progress"] = f"오류: {e}"
    finally:
        st["running"] = False


@app.post("/api/security/analyze")
def security_analyze():
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 401
    if _SECAI_JOB.get("running") and (time.time() - _SECAI_JOB.get("started_ts", 0) < 1800):
        return jsonify({"running": True, "already": True})
    limit = request.args.get("limit", default=SECAI_LIMIT, type=int)
    _SECAI_JOB.update(running=True, progress="분석 준비…", result=None, started_ts=time.time())
    threading.Thread(target=_security_ai_run, args=(limit,), daemon=True).start()
    return jsonify({"running": True})


@app.get("/api/security/analyze/status")
def security_analyze_status():
    st = dict(_SECAI_JOB)
    if st.get("running") and (time.time() - st.get("started_ts", 0) > 1800):
        _SECAI_JOB["running"] = False
        st["running"] = False
        st["progress"] = "중단됨(시간 초과)"
    stats = {}
    try:
        if _ensure_db():
            stats = db.security_ai_stats()
    except Exception:  # noqa: BLE001
        stats = {}
    return jsonify({"running": st.get("running"), "progress": st.get("progress"),
                    "result": st.get("result"), "stats": stats})


# ---------------------------- 배치 스케줄 ----------------------------
# 정기 배치(스케줄러/크론)는 최근 것만 훑어 새 기사를 보충한다(최초 5년 백필과 별개).
BATCH_DAYS = int(os.environ.get("BATCH_DAYS", "30"))  # 정기 배치 뉴스 수집 창(최근 N일)


_BATCH_GROUPS = ("cat", "game", "news", "biz", "security", "event", "boards", "social")
_batch_state = {"running": False, "ts": 0}


def _auto_security_report():
    """월초에 '지난달' 보안 리포트를 자동 생성한다(관리자 수동 실행과 별개).
    - SECREPORT_AUTO=0 이면 끔. ANTHROPIC_API_KEY 없으면 skip.
    - 이미 해당 월(secmonth:YYYY-MM) 스냅샷이 있으면 skip → 한 달에 한 번만 LLM 사용(idempotent).
    - 지난달 보안뉴스가 아직 없으면 skip(다음 배치에서 재시도).
    배치(스케줄러/크론)가 돌 때마다 호출되지만, 위 조건 때문에 실제 생성은 월 1회뿐이다."""
    if os.environ.get("SECREPORT_AUTO", "1") == "0":
        return
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return
    try:
        if not _ensure_db():
            return
        ym = security_report.prev_month()
        if db.report_snapshot_exists(f"secmonth:{ym}"):
            return  # 이미 생성됨
        data, err = security_report.run(ym=ym)
        if err or not data:
            print(f"[secreport] 자동 생성 보류({ym}): {err}", flush=True)
            return
        m = data.get("_meta", {})
        label = _now_kst()
        db.save_report_snapshot(label, label, f"{ym} 월간", m.get("model", ""),
                                json.dumps(data, ensure_ascii=False),
                                pkey=f"secmonth:{ym}", kind="security")
        _PUBLIC_READS.invalidate("/api/report/")
        print(f"[secreport] {ym} 월간 보안 리포트 자동 생성 완료", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[secreport] 자동 생성 실패: {e}", flush=True)


def _batch_all():
    """전 탭을 '순차'로 수집(한 번에 하나씩) → 동시 다중 크롤링에 의한 메모리·DB 과부하 방지.
    비차단: 단일 백그라운드 스레드가 그룹을 차례로 처리한다. 뉴스류는 최근 BATCH_DAYS 창."""
    if _batch_state["running"] and (time.time() - _batch_state["ts"] < 3600):
        print("[batch] 이미 배치 진행 중 → 건너뜀", flush=True)
        return []
    _batch_state.update(running=True, ts=time.time())

    def _run():
        print(f"[batch] 순차 수집 배치 시작 (뉴스류 최근 {BATCH_DAYS}일)", flush=True)
        try:
            for group in _BATCH_GROUPS:
                try:
                    days = BATCH_DAYS if group in ("cat", "game", "news", "biz", "security", "event") else None
                    _do_crawl(group, days=days)
                except Exception as e:  # noqa: BLE001
                    print(f"[batch] {group} 오류: {e}", flush=True)
            try:
                removed = db.prune_news()  # 섹션별 최신 5000건만 유지, 오래된 기사 삭제
                if removed:
                    print(f"[batch] 오래된 뉴스 {removed}건 정리(섹션별 최신 5000 유지)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[batch] prune 오류: {e}", flush=True)
            try:
                _auto_security_report()  # 월초에 지난달 보안 리포트 자동 생성(이미 있으면 skip)
            except Exception as e:  # noqa: BLE001
                print(f"[batch] secreport 오류: {e}", flush=True)
            try:
                _lunch_refresh_all()      # 맛집 주변 식당 주 1회 자동 재수집(주기 안 지났으면 skip)
            except Exception as e:  # noqa: BLE001
                print(f"[batch] lunch refresh 오류: {e}", flush=True)
        finally:
            _batch_state["running"] = False   # 어떤 경우에도 플래그 해제(다음 배치 안 막히게)
            print("[batch] 순차 수집 배치 완료", flush=True)

    threading.Thread(target=_run, daemon=True).start()
    return list(_BATCH_GROUPS)


# 맛집(점심) 자동 재수집: 뉴스와 달리 식당은 잘 안 바뀌므로 주 1회만.
# 배치/크론이 4시간마다 돌아도 아래 주기(meta 타임스탬프)로 실제 수집은 주 1회로 억제.
LUNCH_REFRESH_DAYS = int(os.environ.get("LUNCH_REFRESH_DAYS", "7"))  # 0이면 자동 재수집 끔
_lunch_refresh_state = {"running": False}


def _lunch_should_refresh():
    if LUNCH_REFRESH_DAYS <= 0:
        return False
    last = db.get_meta("lunch_last_refresh")
    if not last:
        return True
    try:
        return (datetime.now() - datetime.fromisoformat(last)).total_seconds() >= LUNCH_REFRESH_DAYS * 86400
    except Exception:  # noqa: BLE001
        return True


def _lunch_refresh_all(force=False):
    """모든 위치의 주변 식당을 순차 재수집(기본 주 1회). 카카오 키 없으면 skip.
    폐업 자동 삭제는 하지 않고(원칙: 데이터 조작 없음) upsert만 — last_checked로 최신 확인 시각 갱신."""
    if not lunch.has_key() or _lunch_refresh_state["running"]:
        return
    if not force and not _lunch_should_refresh():
        return
    if not _ensure_db():
        return
    _lunch_refresh_state["running"] = True
    try:
        tot_new = tot_upd = 0
        for loc in db.lunch_list_locations():
            loc = _lunch_geocode_if_needed(loc)
            if not loc or not loc.get("lat"):
                print(f"[lunch] {(loc or {}).get('name', '?')} 좌표 없음 → skip", flush=True)
                continue
            items = lunch.collect(loc["lat"], loc["lng"], loc.get("radius", 500), progress=lambda m: None)
            for it in items:
                it["source"] = "kakao"
            new, upd = db.lunch_upsert_restaurants(loc["id"], items)
            _invalidate_lunch_cache()
            tot_new += new
            tot_upd += upd
            print(f"[lunch] {loc['name']} 재수집 · 신규 {new} · 갱신 {upd}", flush=True)
        db.set_meta("lunch_last_refresh", datetime.now().isoformat(timespec="seconds"))
        print(f"[lunch] 주간 재수집 완료 · 신규 {tot_new} · 갱신 {tot_upd}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[lunch] 주간 재수집 오류: {e}", flush=True)
    finally:
        _lunch_refresh_state["running"] = False


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
    print(f"[backfill] DB 비어있음 → 자동 백필 시작(뉴스류 최근 {days}일 + 게시판/소셜, 순차)", flush=True)

    def _run():
        for group in ("cat", "game", "news", "biz", "security", "event"):
            try:
                _do_crawl(group, days=days)
            except Exception as e:  # noqa: BLE001
                print(f"[backfill] {group} 오류: {e}", flush=True)
        for group in ("boards", "social"):
            try:
                _do_crawl(group)
            except Exception as e:  # noqa: BLE001
                print(f"[backfill] {group} 오류: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


def _bootstrap_venue_schedules():
    """One bounded import for newly added official sources; no news crawl or AI.

    Leave the marker unset on partial failures so a restart can retry. Existing
    records are upserted; collecting never removes user data or old sources.
    """
    if (os.environ.get('ENABLE_SCHEDULER', '1') != '1'
            or os.environ.get('VENUE_SOURCES_OFF') == '1'):
        return
    try:
        if not _ensure_db(force=True) or db.get_meta('venue_sources_version') == '1':
            return
        items = venue_sources.collect(lambda message: print('[venue] ' + message, flush=True))
        status = venue_sources.diagnose()
        if items:
            counts = _save_event(items)
            db.set_meta('last_crawl_event', _now_kst())
            _invalidate_read_cache()
            print(f"[venue] 공식 일정 저장 {len(items)}건 · 신규 {counts['new']}건", flush=True)
        db.set_meta('venue_sources_status', json.dumps(status, ensure_ascii=False))
        if all(row.get('ok') for row in status.values()):
            db.set_meta('venue_sources_version', '1')
    except Exception as exc:
        print(f'[venue] 초기 수집 실패: {type(exc).__name__}', flush=True)


# DB keep-alive: 주기적으로 DB에 가벼운 쿼리를 날려 잠들지 않게 유지.
# ⚠️ 기본 꺼짐(0). Neon 무료는 compute 사용시간 한도가 있는데, keepalive를 켜면 DB가 상시
# 가동돼 한도를 빠르게 소진 → 'quota exceeded'로 DB가 정지될 수 있다(실제 발생). 콜드스타트는
# get_conn 재시도 + 프론트 자동 재시도로 처리하므로 무료 플랜에선 끄는 게 안전.
# 상시 켜짐 유료 DB를 쓸 때만 값을 크게(예: 240) 주면 방문자가 잠든 DB를 덜 만난다.
DB_KEEPALIVE_SEC = int(os.environ.get("DB_KEEPALIVE_SEC", "0"))  # 기본 꺼짐


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


def _prewarm_reads():
    _LOCATION_READS.get("locations", _load_lunch_locations, ttl=300, wait=0)
    _read_metadata()


_prewarm_started = False
_prewarm_lock = threading.Lock()


def _start_read_prewarm():
    # Import may run in a preloading Gunicorn master: start threads only in a worker.
    global _prewarm_started
    if os.environ.get("ENABLE_DB_PREWARM", "1") != "1" or _prewarm_started:
        return
    with _prewarm_lock:
        if _prewarm_started:
            return
        _prewarm_started = True
    threading.Thread(target=_prewarm_reads, daemon=True).start()

_worker_jobs_pid = None
_worker_jobs_lock = threading.Lock()


def _start_worker_jobs():
    """Start once on a worker request, never in the pre-fork import process.

    Starting imports/DB connections in master threads can leave module locks and
    unfinished transactions in forked workers. Render's first health request
    starts these jobs without waiting for any collection or database operation.
    """
    global _worker_jobs_pid
    pid = os.getpid()
    if _worker_jobs_pid == pid:
        return
    with _worker_jobs_lock:
        if _worker_jobs_pid == pid:
            return
        _worker_jobs_pid = pid
        _start_scheduler()
        for target in (_auto_backfill, _bootstrap_venue_schedules, _db_keepalive):
            threading.Thread(target=target, daemon=True).start()


if __name__ == "__main__":
    # 로컬 실행. 호스팅 환경은 gunicorn이 app 객체를 직접 띄운다(Procfile 참고).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
