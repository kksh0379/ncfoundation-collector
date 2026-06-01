"""엔씨문화재단 콜렉터 - 모바일 웹 (Flask).

탭1: 네이버 뉴스 (엔씨문화재단/NC문화재단)
탭2: 재단 서비스 게시판

수동 실행 방식: 화면의 "수집 실행" 버튼을 누르면 해당 탭의 크롤러가 동작한다.
"""
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, jsonify, render_template, request, session

from collector import boards, db, dedup, fetcher, google_news, social

app = Flask(__name__)
# 초안 단계: 브라우저가 옛 JS/CSS를 캐시해 혼란을 주지 않도록 정적파일 캐시를 끈다.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = os.environ.get("SECRET_KEY", "ncfoundation-collector-secret-key")

# 관리자 계정 (요청에 따라 하드코딩). 운영 시에는 환경변수로 분리 권장.
ADMIN_ID = os.environ.get("ADMIN_ID", "kksh0378")
ADMIN_PW = os.environ.get("ADMIN_PW", "Abcde12#")

KST = timezone(timedelta(hours=9))  # 마지막 수집 일시는 서버에서 KST로 기록


def _now_kst():
    return datetime.now(KST).strftime("%Y.%m.%d %H:%M:%S")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


# gunicorn 등으로 띄울 때도 테이블이 준비되도록 import 시점에 초기화한다.
db.init_db()


# ---------------------------- 인증 API ----------------------------
@app.get("/api/me")
def me():
    return jsonify({"admin": bool(session.get("admin"))})


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    if data.get("id") == ADMIN_ID and data.get("pw") == ADMIN_PW:
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "아이디 또는 비밀번호가 올바르지 않습니다."}), 401


@app.post("/api/logout")
def logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.get("/api/meta")
def meta():
    m = db.get_all_meta()
    return jsonify({
        "news": m.get("last_crawl_news"),
        "boards": m.get("last_crawl_boards"),
        "social": m.get("last_crawl_social"),
    })


@app.route("/")
def index():
    services = sorted({s["service"] for s in boards.SOURCES})
    return render_template("index.html", services=services)


# ---------------------------- 조회 API ----------------------------
@app.get("/api/news")
def get_news():
    return jsonify(db.list_news())


@app.get("/api/boards")
def get_boards():
    service = request.args.get("service", "all")
    return jsonify(db.list_boards(service=service))


@app.get("/api/social")
def get_social():
    channel = request.args.get("channel", "all")
    return jsonify(db.list_social(channel=channel))


# ---------------------------- 진단 API ----------------------------
@app.get("/api/diag")
@admin_required
def diag():
    """각 대상 사이트에 이 서버가 실제로 접속되는지 빠르게 점검한다.
    브라우저로 /api/diag 를 열면 사이트별 응답 상태/에러를 즉시 확인할 수 있다.
    """
    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    group = request.args.get("group", "all")  # news | boards | all
    targets = []
    if group in ("all", "news"):
        targets.append(
            ("구글 뉴스(RSS)", google_news.RSS_URL + "?q=NC%EB%AC%B8%ED%99%94%EC%9E%AC%EB%8B%A8&hl=ko&gl=KR&ceid=KR:ko")
        )
    if group in ("all", "boards"):
        seen = set()
        for s in boards.SOURCES:
            if s["list_url"] not in seen:
                seen.add(s["list_url"])
                targets.append((f"{s['service']} · {s['category']}", s["list_url"]))
    if group in ("all", "social"):
        for s in social.SOURCES:
            if s.get("url"):
                targets.append((f"{s['channel']} · {s['account']}", s["url"]))

    def check(item):
        name, url = item
        t0 = _t.time()
        try:
            r = fetcher.get(url, retries=0)
            body = r.text or ""
            return {
                "name": name, "url": url, "ok": True,
                "status": r.status_code, "bytes": len(body),
                "looks_html": "<html" in body.lower() or "<!doctype" in body.lower(),
                "sec": round(_t.time() - t0, 1),
            }
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


# 저장 정책(테스트 단계): 키 = 원문 URL.
#  - 같은 URL이면 본문 등 전체 필드를 갱신(updated)
#  - 새 URL이면 신규 저장(new). 뉴스는 '서로 다른 URL의 동일 보도자료'만 본문
#    유사도로 걸러 한 건만 남긴다.
def _save_news(items):
    existing = db.all_news_fingerprints()
    existing_urls = {it.get("url") for it in existing}
    existing_contents = [it.get("content") or "" for it in existing]

    new, updated, dup = 0, 0, 0
    fresh = []  # 새 URL 후보
    for item in items:
        if item.get("url") in existing_urls:
            item["content_hash"] = dedup.content_hash(item.get("content", ""))
            db.upsert_news(item)  # 기존 기사 본문 전체 갱신
            updated += 1
        else:
            fresh.append(item)

    # 새 URL들에 대해서만 보도자료 중복(서로 다른 URL) 제거
    kept, dup = dedup.dedup_news_items(fresh, existing_contents)
    for item in kept:
        item["content_hash"] = dedup.content_hash(item.get("content", ""))
        db.upsert_news(item)
        new += 1
    return {"new": new, "updated": updated, "duplicates": dup}


def _save_boards(items):
    new = updated = 0
    for item in items:
        if db.upsert_board(item) == "updated":
            updated += 1
        else:
            new += 1
    return {"new": new, "updated": updated, "duplicates": 0}


def _save_social(items):
    new = updated = 0
    for item in items:
        if db.upsert_social(item) == "updated":
            updated += 1
        else:
            new += 1
    return {"new": new, "updated": updated, "duplicates": 0}


_CRAWLERS = {
    "news": (google_news.crawl, _save_news),
    "boards": (boards.crawl_all, _save_boards),
    "social": (social.crawl_all, _save_social),
}


def _do_crawl(group):
    """수집 1회 실행(수동 버튼·배치 공용). 결과 dict 반환."""
    crawl_fn, save_fn = _CRAWLERS[group]
    try:
        items = crawl_fn()
        counts = save_fn(items)
        result = {"crawled": len(items), **counts}
    except Exception as e:  # noqa: BLE001
        print(f"[crawl] {group} 오류: {e}", flush=True)
        result = {"crawled": 0, "new": 0, "updated": 0, "duplicates": 0, "error": str(e)}
    result["last_crawled_at"] = _now_kst()  # 서버 기준 마지막 수집 일시
    db.set_meta(f"last_crawl_{group}", result["last_crawled_at"])
    _last_result[group] = result
    return result


@app.post("/api/crawl/news")
@admin_required
def crawl_news():
    print("[crawl] /api/crawl/news 시작 (구글 뉴스 RSS)", flush=True)
    return jsonify(_do_crawl("news"))


@app.post("/api/crawl/boards")
@admin_required
def crawl_boards():
    print("[crawl] /api/crawl/boards 시작", flush=True)
    return jsonify(_do_crawl("boards"))


@app.post("/api/crawl/social")
@admin_required
def crawl_social():
    print("[crawl] /api/crawl/social 시작", flush=True)
    return jsonify(_do_crawl("social"))


@app.get("/api/crawl/status")
def crawl_status():
    # 폴링형(구버전) 프론트 호환용. 동기 방식이라 항상 미실행 상태이며,
    # 마지막 수집 결과를 함께 돌려준다.
    group = request.args.get("group", "news")
    return jsonify({"running": False, "log": [], "result": _last_result.get(group)})


# ---------------------------- 배치 스케줄 (4시간) ----------------------------
def _batch_all():
    print("[batch] 4시간 주기 수집 시작", flush=True)
    for group in ("news", "boards", "social"):
        try:
            r = _do_crawl(group)
            print(f"[batch] {group}: {r}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[batch] {group} 오류: {e}", flush=True)


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


@app.post("/api/cron")
def cron():
    """외부 크론(예: cron-job.org)이 토큰으로 배치를 트리거. 무료 호스팅이 잠들어
    내부 스케줄러가 멈추는 경우의 대안. CRON_TOKEN 환경변수 미설정 시 비활성."""
    token = os.environ.get("CRON_TOKEN")
    if not token or request.args.get("token") != token:
        return jsonify({"error": "unauthorized"}), 401
    _batch_all()
    return jsonify({"ok": True, "at": _now_kst()})


_start_scheduler()


if __name__ == "__main__":
    # 로컬 실행. 호스팅 환경은 gunicorn이 app 객체를 직접 띄운다(Procfile 참고).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
