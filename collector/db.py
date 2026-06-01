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
            """
        )


def insert_news(item):
    """뉴스 1건 저장. 성공 시 row id, URL 중복이면 None."""
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO news (title, published_at, author, content, url, content_hash, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("title"),
                    item.get("published_at"),
                    item.get("author"),
                    item.get("content"),
                    item.get("url"),
                    item.get("content_hash"),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def insert_board(item):
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO boards (service, category, title, published_at, author, content, url, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("service"),
                    item.get("category"),
                    item.get("title"),
                    item.get("published_at"),
                    item.get("author"),
                    item.get("content"),
                    item.get("url"),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def insert_social(item):
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO social (channel, account, title, published_at, content, url, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("channel"),
                    item.get("account"),
                    item.get("title"),
                    item.get("published_at"),
                    item.get("content"),
                    item.get("url"),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def all_news_fingerprints():
    """중복 비교용으로 기존 뉴스의 (id, content_hash, content)만 가볍게 로드."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, content_hash, content FROM news").fetchall()
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
