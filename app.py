"""엔씨문화재단 콜렉터 - 모바일 웹 (Flask).

탭1: 네이버 뉴스 (엔씨문화재단/NC문화재단)
탭2: 재단 서비스 게시판

수동 실행 방식: 화면의 "수집 실행" 버튼을 누르면 해당 탭의 크롤러가 동작한다.
"""
import json
import os
import queue
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request

from collector import boards, db, dedup, fetcher, google_news, social

app = Flask(__name__)
# 초안 단계: 브라우저가 옛 JS/CSS를 캐시해 혼란을 주지 않도록 정적파일 캐시를 끈다.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = os.environ.get("SECRET_KEY", "ncfoundation-collector-secret-key")

KST = timezone(timedelta(hours=9))  # 마지막 수집 일시는 서버에서 KST로 기록

# 관리자 키: 설정 시 상태확인/수집 실행이 이 키를 가진 사람만 가능(뷰어는 조회만).
# 미설정("")이면 게이트 없음(누구나 가능) — 기존 동작과 호환.
ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()


def _admin_ok():
    return (not ADMIN_KEY) or (request.args.get("key") == ADMIN_KEY)


def _now_kst():
    return datetime.now(KST).strftime("%Y.%m.%d %H:%M:%S")


# gunicorn 등으로 띄울 때도 테이블이 준비되도록 import 시점에 초기화한다.
db.init_db()


@app.get("/api/meta")
def meta():
    m = db.get_all_meta()
    return jsonify({
        "news": m.get("last_crawl_news"),
        "boards": m.get("last_crawl_boards"),
        "social": m.get("last_crawl_social"),
        "storage": db.BACKEND,  # postgres(영구) / sqlite(임시)
        "admin_required": bool(ADMIN_KEY),  # true면 상태확인/수집은 관리자 키 필요
    })


# 수집 구현 현황(화면 뱃지용). 완료 / 구현 중 / 구현 예정
BOARD_STATUS = {"나의AAC": "완료", "프로젝토리": "구현 중", "FAIR AI": "구현 예정", "대표 홈페이지": "구현 예정"}
SOCIAL_STATUS = {"유튜브": "완료", "인스타그램": "구현 예정"}


@app.route("/")
def index():
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
@app.get("/api/news")
def get_news():
    category = request.args.get("category", "all")
    return jsonify(db.list_news(category=category))


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
    try:
        if group == "news":
            # 증분 수집: 이미 저장된 URL은 재해석(느린 원문 복원)하지 않고 새 기사만 처리
            known = {r.get("url") for r in db.all_news_fingerprints()}
            items = crawl_fn(known_urls=known, progress=progress, days=days)
        else:
            items = crawl_fn(progress=progress)
        progress("저장·그룹화 중…")
        counts = save_fn(items)
        result = {"crawled": len(items), **counts}
        progress(f"완료 · 신규 {result.get('new', 0)}건 · 갱신 {result.get('updated', 0)}건")
    except Exception as e:  # noqa: BLE001
        print(f"[crawl] {group} 오류: {e}", flush=True)
        result = {"crawled": 0, "new": 0, "updated": 0, "duplicates": 0, "error": str(e)}
        progress(f"오류: {e}")
    result["last_crawled_at"] = _now_kst()  # 서버 기준 마지막 수집 일시
    db.set_meta(f"last_crawl_{group}", result["last_crawled_at"])
    _last_result[group] = result
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


def _start_job(group, days=None):
    """백그라운드 수집 작업 시작. 이미 진행 중이면 False."""
    st = _JOBS.get(group)
    if st and st.get("running"):
        return False
    _JOBS[group] = {"running": True, "progress": "수집 대기…", "result": None, "started_at": _now_kst()}
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
def _batch_all():
    """뉴스/게시판/소셜을 백그라운드 작업으로 시작(비차단). 시작된 그룹 목록 반환."""
    print("[batch] 수집 배치 시작", flush=True)
    started = []
    for group in ("news", "boards", "social"):
        if _start_job(group):
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


_start_scheduler()


if __name__ == "__main__":
    # 로컬 실행. 호스팅 환경은 gunicorn이 app 객체를 직접 띄운다(Procfile 참고).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
