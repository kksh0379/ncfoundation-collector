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

    # 연결 문자열 정리:
    #  - channel_binding 파라미터 제거(Neon 등 pooler/PgBouncer 조합에서 연결 실패 유발)
    #  - sslmode=require 보장(호스팅 Postgres는 SSL 필수)
    _p = urlsplit(DATABASE_URL)
    _qs = [(k, v) for k, v in parse_qsl(_p.query) if k.lower() != "channel_binding"]
    if not any(k.lower() == "sslmode" for k, _ in _qs):
        _qs.append(("sslmode", "require"))
    _CONNINFO = urlunsplit((_p.scheme, _p.netloc, _p.path, urlencode(_qs), _p.fragment))

    # 연결 방식: 연결 풀(psycopg_pool) 대신 '요청마다 직접 접속'을 쓴다.
    # 이 앱에서 풀이 계속 연결 획득에 실패(couldn't get a connection)했는데, 같은 문자열로
    # 직접 psycopg.connect는 성공했다. 트래픽이 적은 앱이라 직접 접속이 더 단순·확실하다.
    # prepare_threshold=None: PgBouncer 트랜잭션 풀러(Supabase 6543 등) 호환.
    _CONN_KW = {"row_factory": dict_row, "connect_timeout": 10, "prepare_threshold": None}


def _q(sql):
    """플레이스홀더 방언 변환: SQLite는 '?', Postgres는 '%s'."""
    return sql.replace("?", "%s") if _PG else sql


def diagnose():
    """DB 연결을 '풀을 거치지 않고' 직접 한 번 시도해서 실제 원인을 돌려준다.
    반환: {ok, backend, host, error}. 비밀번호는 마스킹. (관리자 진단용)"""
    import re as _re
    info = {"ok": False, "backend": BACKEND}
    if not _PG:
        # SQLite: 파일 열기만 확인
        try:
            with get_conn() as conn:
                conn.execute("SELECT 1")
            info["ok"] = True
        except Exception as e:  # noqa: BLE001
            info["error"] = f"{type(e).__name__}: {e}"
        return info
    info["host"] = urlsplit(_CONNINFO).hostname
    try:
        # 풀/재시도 없이 딱 한 번, 짧은 타임아웃으로 직접 접속 → 진짜 에러가 그대로 나옴
        conn = psycopg.connect(_CONNINFO, connect_timeout=8)
        try:
            conn.execute("SELECT 1")
            info["ok"] = True
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        msg = _re.sub(r":npg_[^@]+@", ":***@", msg)  # 혹시 모를 비번 노출 마스킹
        info["error"] = f"{type(e).__name__}: {msg}"
    return info


@contextmanager
def get_conn():
    if _PG:
        # 요청마다 직접 접속. Neon cold start로 첫 연결이 실패할 수 있어 몇 번 재시도
        # (그 사이 Neon이 깨어난다). 성공 시 commit, 오류 시 rollback, 항상 close.
        conn, last = None, None
        for attempt in range(3):
            try:
                conn = psycopg.connect(_CONNINFO, **_CONN_KW)
                break
            except Exception as e:  # noqa: BLE001  (주로 OperationalError: cold start)
                last = e
                time.sleep(1 + attempt)
        if conn is None:
            raise last
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
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
        category TEXT, image_url TEXT, section TEXT, collected_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS boards (
        id {_AUTO_PK}, service TEXT, category TEXT, title TEXT, published_at TEXT,
        author TEXT, content TEXT, url TEXT UNIQUE, image_url TEXT, collected_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS social (
        id {_AUTO_PK}, channel TEXT, account TEXT, title TEXT, published_at TEXT,
        content TEXT, url TEXT UNIQUE, image_url TEXT, collected_at TEXT
    )""",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    f"""CREATE TABLE IF NOT EXISTS run_log (
        id {_AUTO_PK}, grp TEXT, status TEXT, detail TEXT, ran_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS visit_log (
        id {_AUTO_PK}, ts TEXT, ip TEXT, ua TEXT, path TEXT, referer TEXT, lang TEXT
    )""",
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
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS image_url TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS section TEXT")
            conn.execute("ALTER TABLE boards ADD COLUMN IF NOT EXISTS image_url TEXT")
            conn.execute("ALTER TABLE social ADD COLUMN IF NOT EXISTS image_url TEXT")
        else:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(news)").fetchall()}
            if "group_key" not in cols:
                conn.execute("ALTER TABLE news ADD COLUMN group_key TEXT")
            if "source_url" not in cols:
                conn.execute("ALTER TABLE news ADD COLUMN source_url TEXT")
            if "category" not in cols:
                conn.execute("ALTER TABLE news ADD COLUMN category TEXT")
            if "image_url" not in cols:
                conn.execute("ALTER TABLE news ADD COLUMN image_url TEXT")
            if "section" not in cols:
                conn.execute("ALTER TABLE news ADD COLUMN section TEXT")
            bcols = {r["name"] for r in conn.execute("PRAGMA table_info(boards)").fetchall()}
            if "image_url" not in bcols:
                conn.execute("ALTER TABLE boards ADD COLUMN image_url TEXT")
            scols = {r["name"] for r in conn.execute("PRAGMA table_info(social)").fetchall()}
            if "image_url" not in scols:
                conn.execute("ALTER TABLE social ADD COLUMN image_url TEXT")


def set_meta(key, value):
    with get_conn() as conn:
        conn.execute(
            _q("INSERT INTO meta (key, value) VALUES (?, ?) "
               "ON CONFLICT (key) DO UPDATE SET value = excluded.value"),
            (key, value),
        )


def add_run_log(group, status, detail, ran_at):
    """수집 실행 1건 기록(언제·무엇·성공/실패·상세). 오래된 기록은 자동 정리."""
    with get_conn() as conn:
        conn.execute(
            _q("INSERT INTO run_log (grp, status, detail, ran_at) VALUES (?, ?, ?, ?)"),
            (group, status, detail, ran_at),
        )
        # 최근 200건만 유지(무한 증가 방지)
        conn.execute(
            _q("DELETE FROM run_log WHERE id NOT IN "
               "(SELECT id FROM run_log ORDER BY id DESC LIMIT 200)")
        )


def list_run_log(limit=60):
    with get_conn() as conn:
        rows = conn.execute(
            _q("SELECT grp, status, detail, ran_at FROM run_log ORDER BY id DESC LIMIT ?"),
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_visit(ts, ip, ua, path, referer, lang):
    """방문 1건 기록. 최근 1000건만 유지."""
    with get_conn() as conn:
        conn.execute(
            _q("INSERT INTO visit_log (ts, ip, ua, path, referer, lang) VALUES (?, ?, ?, ?, ?, ?)"),
            (ts, ip, ua, path, referer, lang),
        )
        conn.execute(
            _q("DELETE FROM visit_log WHERE id NOT IN "
               "(SELECT id FROM visit_log ORDER BY id DESC LIMIT 1000)")
        )


def list_visits(limit=100):
    with get_conn() as conn:
        rows = conn.execute(
            _q("SELECT ts, ip, ua, path, referer, lang FROM visit_log ORDER BY id DESC LIMIT ?"),
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_meta():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ---- 배치 upsert (키=url, ON CONFLICT로 한 번에 처리 → 원격 DB에서도 빠름) ----
_NEWS_COLS = ("title", "published_at", "author", "content", "url",
              "content_hash", "group_key", "source_url", "category", "image_url", "section", "collected_at")
_BOARD_COLS = ("service", "category", "title", "published_at", "author",
               "content", "url", "image_url", "collected_at")
_SOCIAL_COLS = ("channel", "account", "title", "published_at", "content", "url", "image_url", "collected_at")


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
        # 신규 건수는 저장 전후 총 개수 차이로 계산한다(COUNT는 인덱스로 빨라서, 큰 테이블/
        # Neon cold start에서도 전체 URL을 끌어오던 예전 방식보다 훨씬 빠르다).
        before = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        cur = conn.cursor()
        cur.executemany(sql, rows)
        after = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    new = int(after) - int(before)
    return (new, max(0, len(items) - new))


def upsert_news_many(items):
    return _upsert_many("news", _NEWS_COLS, items)


def upsert_board_many(items):
    return _upsert_many("boards", _BOARD_COLS, items)


def upsert_social_many(items):
    return _upsert_many("social", _SOCIAL_COLS, items)


def all_news_min(section="nc"):
    """클러스터링용으로 뉴스의 (url, title, content)만 가볍게 로드. section별(nc/cat)."""
    with get_conn() as conn:
        if section == "cat":
            rows = conn.execute("SELECT url, title, content FROM news WHERE section = 'cat'").fetchall()
        else:
            rows = conn.execute(
                "SELECT url, title, content FROM news WHERE COALESCE(section,'nc') = 'nc'").fetchall()
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


def news_needs_enrich(limit=200):
    """이미지가 없거나 본문이 너무 짧은(=RSS 요약뿐) 뉴스를 최근순으로 로드. 보강 대상."""
    with get_conn() as conn:
        rows = conn.execute(
            _q("SELECT url, source_url, content FROM news "
               "WHERE image_url IS NULL OR image_url = '' OR content IS NULL OR LENGTH(content) < 80 "
               "ORDER BY published_at DESC, id DESC LIMIT ?"), (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def apply_news_enrich(url_to_data):
    """url→{image_url?, content?, source_url?} 로 뉴스 보강 값 일괄 반영. 반환: 갱신 건수."""
    n = 0
    with get_conn() as conn:
        cur = conn.cursor()
        for u, d in (url_to_data or {}).items():
            sets, vals = [], []
            for col in ("image_url", "content", "source_url"):
                if d.get(col):
                    sets.append(f"{col}=?")
                    vals.append(d[col])
            if not sets:
                continue
            vals.append(u)
            cur.execute(_q(f"UPDATE news SET {', '.join(sets)} WHERE url=?"), tuple(vals))
            n += 1
    return n


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


def _count(table):
    with get_conn() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"]) if row else 0


def clear_tables(tables):
    """지정한 테이블(news/boards/social)을 통째로 비운다. 반환: {테이블: 삭제된 행 수}.
    관리자 'DB 비우기' 기능용. meta(마지막 수집 일시 등)는 건드리지 않는다."""
    allowed = {"news", "boards", "social"}
    tables = [t for t in tables if t in allowed]
    result = {}
    with get_conn() as conn:
        for t in tables:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()
            n = int(row["n"]) if row else 0
            conn.execute(f"DELETE FROM {t}")
            result[t] = n
    return result


def existing_board_urls(service=None):
    """본문 요약이 이미 채워진 게시판 글 URL 집합(증분용). service 지정 시 해당 서비스만.
    본문이 빈 글은 제외 → 다음 수집 때 상세를 다시 시도해 채운다(자가 복구)."""
    cond = "(content IS NOT NULL AND content != '') OR (image_url IS NOT NULL AND image_url != '')"
    with get_conn() as conn:
        if service:
            rows = conn.execute(
                _q(f"SELECT url FROM boards WHERE service = ? AND {cond}"), (service,)
            ).fetchall()
        else:
            rows = conn.execute(f"SELECT url FROM boards WHERE {cond}").fetchall()
        return {r["url"] for r in rows}


def board_content_map(service):
    """게시판 기존 글의 {url: content} 맵(증분용 — 이미 본문 있는 글은 재요청 안 하려고)."""
    with get_conn() as conn:
        rows = conn.execute(
            _q("SELECT url, content FROM boards WHERE service = ?"), (service,)
        ).fetchall()
        return {r["url"]: (r["content"] or "") for r in rows}


def existing_board_titles(service):
    with get_conn() as conn:
        rows = conn.execute(_q("SELECT title FROM boards WHERE service = ?"), (service,)).fetchall()
        return {r["title"] for r in rows}


def list_news(limit=3000, category=None, section="nc"):
    sec_sql = "section = 'cat'" if section == "cat" else "COALESCE(section,'nc') = 'nc'"
    with get_conn() as conn:
        if category and category != "all":
            rows = conn.execute(
                _q(f"SELECT * FROM news WHERE {sec_sql} AND category = ? "
                   "ORDER BY published_at DESC, id DESC LIMIT ?"),
                (category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                _q(f"SELECT * FROM news WHERE {sec_sql} ORDER BY published_at DESC, id DESC LIMIT ?"),
                (limit,),
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
