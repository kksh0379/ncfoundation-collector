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


# ---------------------------- 수집 API (동기 방식) ----------------------------
# 수집은 요청 한 번에 끝까지 처리하고 결과를 바로 반환한다. (구조가 단순해 어떤
# 버전의 프론트엔드 JS가 캐시돼 있어도 호환되며, 무료 호스팅 재시작에도 안전)
# 마지막 결과는 /api/crawl/status 폴링형 프론트와의 호환을 위해 보관한다.
_last_result = {"news": None, "boards": None}


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


def _run_crawl(group, crawl_fn, save_fn):
    try:
        items = crawl_fn()
        saved, dup = save_fn(items)
        result = {"crawled": len(items), "saved": saved, "duplicates": dup}
    except Exception as e:  # noqa: BLE001
        print(f"[crawl] {group} 오류: {e}", flush=True)
        result = {"crawled": 0, "saved": 0, "duplicates": 0, "error": str(e)}
    _last_result[group] = result
    return jsonify(result)


@app.post("/api/crawl/news")
def crawl_news():
    print("[crawl] /api/crawl/news 시작 (구글 뉴스 RSS)", flush=True)
    return _run_crawl("news", google_news.crawl, _save_news)


@app.post("/api/crawl/boards")
def crawl_boards():
    print("[crawl] /api/crawl/boards 시작", flush=True)
    return _run_crawl("boards", boards.crawl_all, _save_boards)


@app.get("/api/crawl/status")
def crawl_status():
    # 폴링형(구버전) 프론트 호환용. 동기 방식이라 항상 미실행 상태이며,
    # 마지막 수집 결과를 함께 돌려준다.
    group = request.args.get("group", "news")
    return jsonify({"running": False, "log": [], "result": _last_result.get(group)})


if __name__ == "__main__":
    # 로컬 실행. 호스팅 환경은 gunicorn이 app 객체를 직접 띄운다(Procfile 참고).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
