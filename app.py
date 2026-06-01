"""엔씨문화재단 콜렉터 - 모바일 웹 (Flask).

탭1: 네이버 뉴스 (엔씨문화재단/NC문화재단)
탭2: 재단 서비스 게시판

수동 실행 방식: 화면의 "수집 실행" 버튼을 누르면 해당 탭의 크롤러가 동작한다.
"""
import os

from flask import Flask, jsonify, render_template, request

from collector import boards, db, dedup, naver_news

app = Flask(__name__)

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


# ---------------------------- 수집 API ----------------------------
@app.post("/api/crawl/news")
def crawl_news():
    """탭1 수집. 본문 유사도로 중복을 제거하며 신규만 저장."""
    max_pages = int(request.json.get("max_pages", 5)) if request.is_json else 5
    items = naver_news.crawl(max_pages=max_pages)

    saved, dup = 0, 0
    existing = db.all_news_fingerprints()  # 비교 기준 (실행 중 누적 갱신)
    for item in items:
        if dedup.is_duplicate_news(item.get("content", ""), existing):
            dup += 1
            continue
        item["content_hash"] = dedup.content_hash(item.get("content", ""))
        rid = db.insert_news(item)
        if rid:
            saved += 1
            # 같은 실행 내에서의 중복도 잡도록 비교 목록에 추가
            existing.append(
                {"id": rid, "content_hash": item["content_hash"], "content": item.get("content")}
            )
        else:
            dup += 1  # URL 중복

    return jsonify({"crawled": len(items), "saved": saved, "duplicates": dup})


@app.post("/api/crawl/boards")
def crawl_boards():
    """탭2 수집. 제목 기반으로 중복을 제거하며 신규만 저장."""
    items = boards.crawl_all()

    saved, dup = 0, 0
    title_cache = {}  # service -> set(titles), 실행 중 누적
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
            dup += 1  # URL 중복

    return jsonify({"crawled": len(items), "saved": saved, "duplicates": dup})


if __name__ == "__main__":
    # 로컬 실행. 호스팅 환경은 gunicorn이 app 객체를 직접 띄운다(Procfile 참고).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
