"""저장소 (SQLite 또는 Postgres).

`DATABASE_URL` 환경변수가 있으면 **Postgres**(예: Supabase/Neon 무료)를 쓰고,
없으면 로컬 **SQLite** 파일을 쓴다. 무료 호스팅은 재시작 시 로컬 디스크가
초기화되므로, 외부 Postgres를 연결하면 수집 데이터가 영구 보존된다.

보관 테이블
- news   : 탭1 뉴스 기사 (동일 기사 group_key로 그룹화, source_url=실제 기사 URL)
- boards : 탭2 게시판 글
- social : 탭3 소셜 채널
- meta   : 마지막 수집 일시 등 메타
"""
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "collector.db")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_PG = bool(DATABASE_URL)
BACKEND = "postgres" if _PG else "sqlite"  # 현재 저장소 종류(연결 확인용)

if _PG:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool, PoolTimeout

    # 연결 문자열 정리:
    #  - channel_binding 파라미터 제거(Neon 등 pooler/PgBouncer 조합에서 연결 실패 유발)
    #  - sslmode=require 보장(호스팅 Postgres는 SSL 필수)
    _p = urlsplit(DATABASE_URL)
    _qs = [(k, v) for k, v in parse_qsl(_p.query) if k.lower() != "channel_binding"]
    if not any(k.lower() == "sslmode" for k, _ in _qs):
        _qs.append(("sslmode", "require"))
    _CONNINFO = urlunsplit((_p.scheme, _p.netloc, _p.path, urlencode(_qs), _p.fragment))

    # 연결 풀: Neon 무료는 유휴 시 컴퓨트가 잠들어(cold start) 첫 연결이 느리고, 유휴
    # 연결이 끊길 수 있다. → min_size=0(유휴 연결 미보유), 사용 시 유효성 검사(check),
    # 연결 타임아웃 여유. cold start를 견디도록 대기(timeout)를 넉넉히 준다.
    _pool = ConnectionPool(
        _CONNINFO, min_size=0, max_size=5, timeout=45,
        kwargs={"row_factory": dict_row, "connect_timeout": 20},
        check=ConnectionPool.check_connection, open=True,
    )


def _q(sql):
    """플레이스홀더 방언 변환: SQLite는 '?', Postgres는 '%s'."""
    return sql.replace("?", "%s") if _PG else sql


@contextmanager
def get_conn():
    if _PG:
        # Neon cold start 등으로 연결 획득이 일시적으로 실패하면 잠깐 쉬고 재시도.
        last = None
        for attempt in range(3):
            try:
                with _pool.connection() as conn:  # 성공 시 자동 commit, 오류 시 rollback 후 반납
                    yield conn
                return
            except PoolTimeout as e:  # 연결을 못 얻음(획득 단계) → 재시도
                last = e
                time.sleep(2 + 2 * attempt)
        raise last
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


_AUTO_PK = "SERIAL PRIMARY KEY" if _PG else "INTEGER PRIMARY KEY AUTOINCREMENT"

_DDL = [
    f"""CREATE TABLE IF NOT EXISTS news (
        id {_AUTO_PK}, title TEXT, published_at TEXT, author TEXT, content TEXT,
        url TEXT UNIQUE, content_hash TEXT, group_key TEXT, source_url TEXT,
        category TEXT, collected_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS boards (
        id {_AUTO_PK}, service TEXT, category TEXT, title TEXT, published_at TEXT,
        author TEXT, content TEXT, url TEXT UNIQUE, collected_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS social (
        id {_AUTO_PK}, channel TEXT, account TEXT, title TEXT, published_at TEXT,
        content TEXT, url TEXT UNIQUE, collected_at TEXT
    )""",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_news_hash ON news(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_news_group ON news(group_key)",
    "CREATE INDEX IF NOT EXISTS idx_boards_title ON boards(service, title)",
    "CREATE INDEX IF NOT EXISTS idx_social_url ON social(url)",
]


def init_db():
    with get_conn() as conn:
        for stmt in _DDL:
            conn.execute(stmt)
        # 구버전 DB 마이그레이션: news에 없는 컬럼 추가
        if _PG:
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS group_key TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS source_url TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS category TEXT")
        else:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(news)").fetchall()}
            if "group_key" not in cols:
                conn.execute("ALTER TABLE news ADD COLUMN group_key TEXT")
            if "source_url" not in cols:
                conn.execute("ALTER TABLE news ADD COLUMN source_url TEXT")
            if "category" not in cols:
                conn.execute("ALTER TABLE news ADD COLUMN category TEXT")


def set_meta(key, value):
    with get_conn() as conn:
        conn.execute(
            _q("INSERT INTO meta (key, value) VALUES (?, ?) "
               "ON CONFLICT (key) DO UPDATE SET value = excluded.value"),
            (key, value),
        )


def get_all_meta():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ---- 배치 upsert (키=url, ON CONFLICT로 한 번에 처리 → 원격 DB에서도 빠름) ----
_NEWS_COLS = ("title", "published_at", "author", "content", "url",
              "content_hash", "group_key", "source_url", "category", "collected_at")
_BOARD_COLS = ("service", "category", "title", "published_at", "author",
               "content", "url", "collected_at")
_SOCIAL_COLS = ("channel", "account", "title", "published_at", "content", "url", "collected_at")


def _upsert_many(table, cols, items):
    """items를 url 기준으로 일괄 upsert. 반환: (신규수, 갱신수)."""
    if not items:
        return (0, 0)
    now = _now()
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "url")
    ph = ", ".join(["?"] * len(cols))
    sql = _q(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph}) "
             f"ON CONFLICT (url) DO UPDATE SET {set_clause}")
    rows = []
    for it in items:
        rows.append(tuple(now if c == "collected_at" else it.get(c) for c in cols))
    with get_conn() as conn:
        existing = {r["url"] for r in conn.execute(f"SELECT url FROM {table}").fetchall()}
        cur = conn.cursor()
        cur.executemany(sql, rows)
    new = sum(1 for it in items if it.get("url") not in existing)
    return (new, len(items) - new)


def upsert_news_many(items):
    return _upsert_many("news", _NEWS_COLS, items)


def upsert_board_many(items):
    return _upsert_many("boards", _BOARD_COLS, items)


def upsert_social_many(items):
    return _upsert_many("social", _SOCIAL_COLS, items)


def all_news_min():
    """클러스터링용으로 전체 뉴스의 (url, title, content)만 가볍게 로드."""
    with get_conn() as conn:
        rows = conn.execute("SELECT url, title, content FROM news").fetchall()
        return [dict(r) for r in rows]


def set_news_group_keys(url_to_key):
    """url→group_key 매핑으로 group_key를 일괄 갱신."""
    if not url_to_key:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.executemany(_q("UPDATE news SET group_key=? WHERE url=?"),
                        [(k, u) for u, k in url_to_key.items()])


def all_news_fingerprints():
    """증분/중복 비교용으로 기존 뉴스의 (id, url, content_hash, content) 로드."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, url, content_hash, content FROM news").fetchall()
        return [dict(r) for r in rows]


def all_news_urls():
    """증분 수집용으로 기존 뉴스 URL만 가볍게 로드(본문 미로딩 → 메모리 절약)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT url FROM news").fetchall()
        return {r["url"] for r in rows}


def all_news_for_filter():
    """기존 뉴스 노이즈 정리용으로 (url, title, content, category) 로드."""
    with get_conn() as conn:
        rows = conn.execute("SELECT url, title, content, category FROM news").fetchall()
        return [dict(r) for r in rows]


def delete_news_by_urls(urls):
    """url 목록에 해당하는 뉴스 삭제. 반환: 시도한 개수."""
    urls = [u for u in urls if u]
    if not urls:
        return 0
    with get_conn() as conn:
        cur = conn.cursor()
        cur.executemany(_q("DELETE FROM news WHERE url=?"), [(u,) for u in urls])
    return len(urls)


def news_count():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM news").fetchone()
        return int(row["n"]) if row else 0


def existing_board_titles(service):
    with get_conn() as conn:
        rows = conn.execute(_q("SELECT title FROM boards WHERE service = ?"), (service,)).fetchall()
        return {r["title"] for r in rows}


def list_news(limit=1000, category=None):
    with get_conn() as conn:
        if category and category != "all":
            rows = conn.execute(
                _q("SELECT * FROM news WHERE category = ? ORDER BY published_at DESC, id DESC LIMIT ?"),
                (category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                _q("SELECT * FROM news ORDER BY published_at DESC, id DESC LIMIT ?"), (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def list_boards(service=None, limit=500):
    with get_conn() as conn:
        if service and service != "all":
            rows = conn.execute(
                _q("SELECT * FROM boards WHERE service = ? ORDER BY published_at DESC, id DESC LIMIT ?"),
                (service, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                _q("SELECT * FROM boards ORDER BY published_at DESC, id DESC LIMIT ?"), (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def list_social(channel=None, limit=500):
    with get_conn() as conn:
        if channel and channel != "all":
            rows = conn.execute(
                _q("SELECT * FROM social WHERE channel = ? ORDER BY published_at DESC, id DESC LIMIT ?"),
                (channel, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                _q("SELECT * FROM social ORDER BY published_at DESC, id DESC LIMIT ?"), (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
