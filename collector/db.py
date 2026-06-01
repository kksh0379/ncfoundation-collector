"""SQLite 저장소.

두 종류의 데이터를 보관한다.
- news   : 탭1, 네이버 뉴스 기사 (본문 유사도로 중복 제거)
- boards : 탭2, 재단 서비스 게시판 글 (제목으로 중복 제거)
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "collector.db")


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS news (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT,
                published_at TEXT,          -- 작성일 (ISO 문자열)
                author       TEXT,          -- 작성자/언론사
                content      TEXT,          -- 본문
                url          TEXT UNIQUE,   -- 원문 URL
                content_hash TEXT,          -- 본문 정규화 해시 (완전 동일 중복 차단)
                collected_at TEXT
            );

            CREATE TABLE IF NOT EXISTS boards (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                service      TEXT,          -- 서비스명 (나의AAC, FAIR AI, ...)
                category     TEXT,          -- 게시판명 (소식, 공지사항, ...)
                title        TEXT,
                published_at TEXT,
                author       TEXT,
                content      TEXT,
                url          TEXT UNIQUE,
                collected_at TEXT
            );

            CREATE TABLE IF NOT EXISTS social (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                channel      TEXT,          -- 인스타그램 / 유튜브
                account      TEXT,          -- 재단 / 프로젝토리 / NC문화재단
                title        TEXT,
                published_at TEXT,
                content      TEXT,
                url          TEXT UNIQUE,
                collected_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_news_hash ON news(content_hash);
            CREATE INDEX IF NOT EXISTS idx_boards_title ON boards(service, title);
            CREATE INDEX IF NOT EXISTS idx_social_url ON social(url);

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )


def set_meta(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_meta():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}


# 수집 정보의 고유 키 = 원문 URL.
# 테스트 단계에서는 본문이 계속 보정되므로, 같은 URL이면 기존 행의 모든 필드를
# 갱신(upsert)한다. 새 URL이면 신규 삽입한다. 반환: "inserted" | "updated".

def upsert_news(item):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM news WHERE url = ?", (item.get("url"),)).fetchone()
        if row:
            conn.execute(
                """UPDATE news SET title=?, published_at=?, author=?, content=?, content_hash=?, collected_at=?
                   WHERE url=?""",
                (item.get("title"), item.get("published_at"), item.get("author"),
                 item.get("content"), item.get("content_hash"), now, item.get("url")),
            )
            return "updated"
        conn.execute(
            """INSERT INTO news (title, published_at, author, content, url, content_hash, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (item.get("title"), item.get("published_at"), item.get("author"),
             item.get("content"), item.get("url"), item.get("content_hash"), now),
        )
        return "inserted"


def upsert_board(item):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM boards WHERE url = ?", (item.get("url"),)).fetchone()
        if row:
            conn.execute(
                """UPDATE boards SET service=?, category=?, title=?, published_at=?, author=?, content=?, collected_at=?
                   WHERE url=?""",
                (item.get("service"), item.get("category"), item.get("title"), item.get("published_at"),
                 item.get("author"), item.get("content"), now, item.get("url")),
            )
            return "updated"
        conn.execute(
            """INSERT INTO boards (service, category, title, published_at, author, content, url, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (item.get("service"), item.get("category"), item.get("title"), item.get("published_at"),
             item.get("author"), item.get("content"), item.get("url"), now),
        )
        return "inserted"


def upsert_social(item):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM social WHERE url = ?", (item.get("url"),)).fetchone()
        if row:
            conn.execute(
                """UPDATE social SET channel=?, account=?, title=?, published_at=?, content=?, collected_at=?
                   WHERE url=?""",
                (item.get("channel"), item.get("account"), item.get("title"),
                 item.get("published_at"), item.get("content"), now, item.get("url")),
            )
            return "updated"
        conn.execute(
            """INSERT INTO social (channel, account, title, published_at, content, url, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (item.get("channel"), item.get("account"), item.get("title"),
             item.get("published_at"), item.get("content"), item.get("url"), now),
        )
        return "inserted"


def all_news_fingerprints():
    """중복 비교용으로 기존 뉴스의 (id, url, content_hash, content)만 가볍게 로드."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, url, content_hash, content FROM news").fetchall()
        return [dict(r) for r in rows]


def existing_board_titles(service):
    """해당 서비스에 이미 저장된 제목 집합 (제목 기반 중복 제거용)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT title FROM boards WHERE service = ?", (service,)
        ).fetchall()
        return {r["title"] for r in rows}


def list_social(channel=None, limit=200):
    with get_conn() as conn:
        if channel and channel != "all":
            rows = conn.execute(
                "SELECT * FROM social WHERE channel = ? ORDER BY published_at DESC, id DESC LIMIT ?",
                (channel, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM social ORDER BY published_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def list_news(limit=200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM news ORDER BY published_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_boards(service=None, limit=200):
    with get_conn() as conn:
        if service and service != "all":
            rows = conn.execute(
                "SELECT * FROM boards WHERE service = ? ORDER BY published_at DESC, id DESC LIMIT ?",
                (service, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM boards ORDER BY published_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
