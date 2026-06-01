"""엔씨문화재단 콜렉터 - 모바일 웹 (Flask).

탭1: 네이버 뉴스 (엔씨문화재단/NC문화재단)
탭2: 재단 서비스 게시판

수동 실행 방식: 화면의 "수집 실행" 버튼을 누르면 해당 탭의 크롤러가 동작한다.
"""
import os

from flask import Flask, jsonify, render_template, request

from collector import boards, db, dedup, fetcher, google_news

app = Flask(__name__)
# 초안 단계: 브라우저가 옛 JS/CSS를 캐시해 혼란을 주지 않도록 정적파일 캐시를 끈다.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# gunicorn 등으로 띄울 때도 테이블이 준비되도록 import 시점에 초기화한다.
db.init_db()


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


# ---------------------------- 진단 API ----------------------------
@app.get("/api/diag")
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


# ---------------------------- 수집 API (백그라운드 작업) ----------------------------
# 수집은 시간이 걸리므로 백그라운드 스레드에서 실행하고, UI는 /api/crawl/status 를
# 폴링해 진행상황을 본다. (gunicorn workers=1 이라 작업 상태를 공유 메모리로 관리)
import threading  # noqa: E402

_jobs = {
    "news": {"running": False, "log": [], "result": None},
    "boards": {"running": False, "log": [], "result": None},
}
_jobs_lock = threading.Lock()


def _save_news(items):
    saved = dup = 0
    existing = db.all_news_fingerprints()
    for item in items:
        if dedup.is_duplicate_news(item.get("content", ""), existing):
            dup += 1
            continue
        item["content_hash"] = dedup.content_hash(item.get("content", ""))
        rid = db.insert_news(item)
        if rid:
            saved += 1
            existing.append(
                {"id": rid, "content_hash": item["content_hash"], "content": item.get("content")}
            )
        else:
            dup += 1
    return saved, dup


def _save_boards(items):
    saved = dup = 0
    title_cache = {}
    for item in items:
        service = item["service"]
        if service not in title_cache:
            title_cache[service] = db.existing_board_titles(service)
        if dedup.is_duplicate_title(item["title"], title_cache[service]):
            dup += 1
            continue
        rid = db.insert_board(item)
        if rid:
            saved += 1
            title_cache[service].add(item["title"])
        else:
            dup += 1
    return saved, dup


def _run_job(group, crawl_fn, save_fn):
    job = _jobs[group]

    def progress(msg):
        job["log"].append(msg)

    try:
        items = crawl_fn(progress=progress)
        progress("중복 판단·저장 중…")
        saved, dup = save_fn(items)
        job["result"] = {"crawled": len(items), "saved": saved, "duplicates": dup}
        progress(f"완료: 신규 {saved}건 저장 · 중복 {dup}건 제외")
    except Exception as e:  # noqa: BLE001
        job["result"] = {"error": str(e)}
        job["log"].append("오류: " + str(e))
        print(f"[crawl] {group} 작업 오류: {e}", flush=True)
    finally:
        job["running"] = False


def _start_job(group, crawl_fn, save_fn):
    job = _jobs[group]
    with _jobs_lock:
        if job["running"]:
            return jsonify({"started": False, "running": True, "log": job["log"][-12:]})
        job.update(running=True, log=["수집 시작…"], result=None)
    threading.Thread(target=_run_job, args=(group, crawl_fn, save_fn), daemon=True).start()
    return jsonify({"started": True, "running": True})


@app.post("/api/crawl/news")
def crawl_news():
    print("[crawl] /api/crawl/news 시작 (구글 뉴스 RSS)", flush=True)
    return _start_job("news", google_news.crawl, _save_news)


@app.post("/api/crawl/boards")
def crawl_boards():
    print("[crawl] /api/crawl/boards 시작", flush=True)
    return _start_job("boards", boards.crawl_all, _save_boards)


@app.get("/api/crawl/status")
def crawl_status():
    group = request.args.get("group", "news")
    job = _jobs.get(group, _jobs["news"])
    return jsonify({"running": job["running"], "log": job["log"][-12:], "result": job["result"]})


if __name__ == "__main__":
    # 로컬 실행. 호스팅 환경은 gunicorn이 app 객체를 직접 띄운다(Procfile 참고).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
