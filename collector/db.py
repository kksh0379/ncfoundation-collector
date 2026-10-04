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
from contextlib import contextmanager, nullcontext
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


def _section_cond(section):
    """섹션(nc/cat/biz…) WHERE 조건과 인자. 레거시(NULL)는 nc로 취급."""
    if section == "nc":
        return "COALESCE(section,'nc') = 'nc'", ()
    return "COALESCE(section,'nc') = ?", (section,)


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
    _u = urlsplit(_CONNINFO)
    info["host"] = _u.hostname
    info["port"] = _u.port
    info["user"] = _u.username                    # 아이디는 확인용으로 노출(비밀 아님)
    info["pw_len"] = len(_u.password or "")        # 비번은 '길이'만(값은 안 보여줌)
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
        category TEXT, image_url TEXT, section TEXT, collected_at TEXT,
        ai_tags TEXT, ai_importance TEXT, ai_insight TEXT, ai_at TEXT
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
    # AI 재단 동향 분석 리포트 스냅샷(실행 시점별로 누적 저장 → 변화 비교).
    f"""CREATE TABLE IF NOT EXISTS report_snapshot (
        id {_AUTO_PK}, created_at TEXT, label TEXT, period TEXT, model TEXT, data TEXT, pkey TEXT,
        kind TEXT
    )""",
    # 행사일정(AI 국내 행사). start_date/end_date=행사 기간(ISO), venue/region=장소.
    f"""CREATE TABLE IF NOT EXISTS events (
        id {_AUTO_PK}, title TEXT, published_at TEXT, author TEXT, content TEXT,
        url TEXT UNIQUE, source_url TEXT, venue TEXT, region TEXT,
        start_date TEXT, end_date TEXT, image_url TEXT, source TEXT, collected_at TEXT
    )""",
    # 아이디별 개인 상태(스크랩/읽음). kind='scrap'|'read', snapshot=스크랩 스냅샷 JSON.
    """CREATE TABLE IF NOT EXISTS user_state (
        username TEXT, ukey TEXT, kind TEXT, snapshot TEXT, ts BIGINT,
        PRIMARY KEY (username, ukey, kind)
    )""",
    # ---- 점심 맛집(lunch) ----
    f"""CREATE TABLE IF NOT EXISTS lunch_location (
        id {_AUTO_PK}, name TEXT, address TEXT, lat REAL, lng REAL,
        radius INTEGER, sort INTEGER, created_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS lunch_restaurant (
        id {_AUTO_PK}, loc_id INTEGER, source TEXT, place_id TEXT,
        name TEXT, category TEXT, cat_norm TEXT, sub_cat TEXT,
        address TEXT, road_address TEXT, lat REAL, lng REAL,
        phone TEXT, place_url TEXT, excluded INTEGER DEFAULT 0,
        first_seen TEXT, last_checked TEXT,
        UNIQUE(loc_id, place_id)
    )""",
    f"""CREATE TABLE IF NOT EXISTS lunch_review (
        id {_AUTO_PK}, restaurant_id INTEGER, username TEXT,
        rating INTEGER, comment TEXT, created_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS lunch_visit (
        id {_AUTO_PK}, restaurant_id INTEGER, username TEXT, visited_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_lunch_rest_loc ON lunch_restaurant(loc_id)",
    "CREATE INDEX IF NOT EXISTS idx_lunch_review_rid ON lunch_review(restaurant_id)",
    "CREATE INDEX IF NOT EXISTS idx_lunch_visit_rid ON lunch_visit(restaurant_id)",
    "CREATE INDEX IF NOT EXISTS idx_news_hash ON news(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_news_group ON news(group_key)",
    "CREATE INDEX IF NOT EXISTS idx_boards_title ON boards(service, title)",
    "CREATE INDEX IF NOT EXISTS idx_social_url ON social(url)",
    "CREATE INDEX IF NOT EXISTS idx_userstate ON user_state(username, kind)",
    "CREATE INDEX IF NOT EXISTS idx_news_section_date ON news(COALESCE(section,'nc'), published_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_news_section_category_date ON news(COALESCE(section,'nc'), category, published_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_boards_service_date ON boards(service, published_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_social_channel_date ON social(channel, published_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_events_date ON events(published_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lunch_visit_user_date ON lunch_visit(username, visited_at)",
    "CREATE INDEX IF NOT EXISTS idx_news_source_url ON news(source_url)",
    "CREATE INDEX IF NOT EXISTS idx_events_source_url ON events(source_url)",
]


def init_db():
    with get_conn() as conn:
        perf_indexes = [stmt for stmt in _DDL if stmt.startswith("CREATE INDEX") and any(name in stmt for name in ("idx_news_section", "idx_boards_service_date", "idx_social_channel_date", "idx_events_date", "idx_lunch_visit_user_date", "idx_news_source_url", "idx_events_source_url"))]
        for stmt in _DDL:
            if stmt not in perf_indexes:
                conn.execute(stmt)
        # 구버전 DB 마이그레이션: news에 없는 컬럼 추가
        if _PG:
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS group_key TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS source_url TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS category TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS image_url TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS section TEXT")
            # 보안뉴스 AI 후처리(자동 태깅/중요도/시사점)
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS ai_tags TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS ai_importance TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS ai_insight TEXT")
            conn.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS ai_at TEXT")
            conn.execute("ALTER TABLE report_snapshot ADD COLUMN IF NOT EXISTS pkey TEXT")
            conn.execute("ALTER TABLE report_snapshot ADD COLUMN IF NOT EXISTS kind TEXT")
            conn.execute("ALTER TABLE boards ADD COLUMN IF NOT EXISTS image_url TEXT")
            conn.execute("ALTER TABLE social ADD COLUMN IF NOT EXISTS image_url TEXT")
            conn.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS source TEXT")
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
            for _c in ("ai_tags", "ai_importance", "ai_insight", "ai_at"):
                if _c not in cols:
                    conn.execute(f"ALTER TABLE news ADD COLUMN {_c} TEXT")
            rcols = {r["name"] for r in conn.execute("PRAGMA table_info(report_snapshot)").fetchall()}
            if "pkey" not in rcols:
                conn.execute("ALTER TABLE report_snapshot ADD COLUMN pkey TEXT")
            if "kind" not in rcols:
                conn.execute("ALTER TABLE report_snapshot ADD COLUMN kind TEXT")
            bcols = {r["name"] for r in conn.execute("PRAGMA table_info(boards)").fetchall()}
            if "image_url" not in bcols:
                conn.execute("ALTER TABLE boards ADD COLUMN image_url TEXT")
            scols = {r["name"] for r in conn.execute("PRAGMA table_info(social)").fetchall()}
            if "image_url" not in scols:
                conn.execute("ALTER TABLE social ADD COLUMN image_url TEXT")
            ecols = {r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "source" not in ecols:
                conn.execute("ALTER TABLE events ADD COLUMN source TEXT")

        for stmt in perf_indexes:
            conn.execute(stmt)

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


def get_meta(key, default=None):
    with get_conn() as conn:
        r = conn.execute(_q("SELECT value FROM meta WHERE key=?"), (key,)).fetchone()
        return r["value"] if r else default


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ---- 배치 upsert (키=url, ON CONFLICT로 한 번에 처리 → 원격 DB에서도 빠름) ----
_NEWS_COLS = ("title", "published_at", "author", "content", "url",
              "content_hash", "group_key", "source_url", "category", "image_url", "section", "collected_at")
_BOARD_COLS = ("service", "category", "title", "published_at", "author",
               "content", "url", "image_url", "collected_at")
_SOCIAL_COLS = ("channel", "account", "title", "published_at", "content", "url", "image_url", "collected_at")
_EVENT_COLS = ("title", "published_at", "author", "content", "url", "source_url",
               "venue", "region", "start_date", "end_date", "image_url", "source", "collected_at")


def _upsert_many(table, cols, items):
    """items를 url 기준으로 일괄 upsert. 반환: (신규수, 갱신수)."""
    if not items:
        return (0, 0)
    now = _now()
    # url은 키라서, section은 최초 소속을 유지(재수집이 다른 탭으로 뺏지 않게) 갱신에서 제외.
    _no_update = {"url", "section"}
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in _no_update)
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


def upsert_event_many(items):
    return _upsert_many("events", _EVENT_COLS, items)


def list_events(limit=1000):
    """행사 목록. 이미 종료된 행사(end_date<오늘)는 제외(미상은 유지).
    정렬: 시작일 빠른 순(미상은 뒤), 최근 수집 순."""
    import datetime as _dt
    today = _dt.date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(_q(
            "SELECT * FROM events "
            "WHERE end_date IS NULL OR end_date='' OR end_date >= ? "
            "ORDER BY CASE WHEN start_date IS NULL OR start_date='' THEN 1 ELSE 0 END, "
            "start_date ASC, id DESC LIMIT ?"), (today, limit)).fetchall()
        return [dict(r) for r in rows]


def clear_events():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        n = int(row["n"]) if row else 0
        conn.execute("DELETE FROM events")
    return n


# ---------------------------- 분석 리포트 스냅샷 ----------------------------
def save_report_snapshot(created_at, label, period, model, data_json, pkey=None, kind="foundation"):
    """리포트 스냅샷 저장. pkey(종류+기간+일자)가 같은 기존 스냅샷이 있으면
    새로 쌓지 않고 교체(업데이트)한다. kind='foundation'(재단 동향)|'security'(보안 월간)."""
    with get_conn() as conn:
        if pkey:
            conn.execute(_q("DELETE FROM report_snapshot WHERE pkey = ?"), (pkey,))
        conn.execute(_q(
            "INSERT INTO report_snapshot (created_at, label, period, model, data, pkey, kind) "
            "VALUES (?,?,?,?,?,?,?)"),
            (created_at, label, period, model, data_json, pkey, kind))
        row = conn.execute("SELECT MAX(id) AS id FROM report_snapshot").fetchone()
        return int(row["id"]) if row and row["id"] is not None else None


def _kind_cond(kind):
    """리포트 종류 조건. 레거시(kind NULL)는 foundation으로 취급."""
    if kind == "security":
        return "kind = 'security'"
    return "COALESCE(kind,'foundation') = 'foundation'"


def latest_report_snapshot_excluding(pkey):
    """비교용 '지난 리포트' 선택. 같은 기간(window)·다른 시점의 최신 스냅샷을 우선하고,
    없으면(레거시 pkey 없음 등) 현재 키와 다른 최신 스냅샷을 쓴다."""
    prefix = (pkey.split(":", 1)[0] + ":") if (pkey and ":" in pkey) else None
    fcond = _kind_cond("foundation")  # 재단 동향 리포트 비교(보안 스냅샷 배제)
    with get_conn() as conn:
        if prefix:
            r = conn.execute(_q(
                f"SELECT * FROM report_snapshot WHERE {fcond} AND pkey LIKE ? AND pkey <> ? "
                "ORDER BY id DESC LIMIT 1"), (prefix + "%", pkey)).fetchone()
            if r:
                return dict(r)
        r = conn.execute(_q(
            f"SELECT * FROM report_snapshot WHERE {fcond} AND (pkey IS NULL OR pkey <> ?) "
            "ORDER BY id DESC LIMIT 1"), (pkey,)).fetchone()
        return dict(r) if r else None


def clear_report_snapshots(kind=None):
    """리포트 스냅샷 삭제. kind 지정 시 해당 종류만, 아니면 전체. 반환: 삭제 건수."""
    with get_conn() as conn:
        if kind:
            cond = _kind_cond(kind)
            row = conn.execute(f"SELECT COUNT(*) AS n FROM report_snapshot WHERE {cond}").fetchone()
            n = int(row["n"]) if row else 0
            conn.execute(f"DELETE FROM report_snapshot WHERE {cond}")
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM report_snapshot").fetchone()
            n = int(row["n"]) if row else 0
            conn.execute("DELETE FROM report_snapshot")
    return n


def delete_report_snapshot(sid):
    """스냅샷 1건 삭제. 반환: 삭제 건수(0/1)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("DELETE FROM report_snapshot WHERE id = ?"), (sid,))
    return 1


def list_report_snapshots(limit=30, kind=None):
    cond = ("WHERE " + _kind_cond(kind)) if kind else ""
    with get_conn() as conn:
        rows = conn.execute(_q(
            f"SELECT id, created_at, label, period, model, kind FROM report_snapshot {cond} "
            "ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_report_snapshot(sid):
    with get_conn() as conn:
        r = conn.execute(_q("SELECT * FROM report_snapshot WHERE id=?"), (sid,)).fetchone()
        return dict(r) if r else None


def report_snapshot_exists(pkey):
    """해당 pkey의 스냅샷이 이미 있는지(자동 생성 중복 방지용)."""
    with get_conn() as conn:
        r = conn.execute(_q("SELECT 1 FROM report_snapshot WHERE pkey=? LIMIT 1"), (pkey,)).fetchone()
        return bool(r)


def latest_report_snapshot(offset=0, kind=None):
    cond = ("WHERE " + _kind_cond(kind)) if kind else ""
    with get_conn() as conn:
        r = conn.execute(_q(f"SELECT * FROM report_snapshot {cond} ORDER BY id DESC LIMIT 1 OFFSET ?"),
                         (offset,)).fetchone()
        return dict(r) if r else None


def all_news_min(section="nc"):
    """클러스터링용으로 뉴스의 (url, title, content)만 가볍게 로드. section별(nc/cat/biz)."""
    cond, args = _section_cond(section)
    with get_conn() as conn:
        rows = conn.execute(
            _q(f"SELECT url, title, content FROM news WHERE {cond}"), args).fetchall()
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
               "WHERE image_url IS NULL OR image_url = '' OR image_url LIKE 'http://%' "
               "OR content IS NULL OR LENGTH(content) < 80 "
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


def reader_item(url):
    """Only already collected news, boards and events can be fetched by Reader."""
    with get_conn() as conn:
        for table in ("news", "boards", "events"):
            condition = "url = ?" if table == "boards" else "(url = ? OR source_url = ?)"
            params = (url,) if table == "boards" else (url, url)
            row = conn.execute(_q(f"SELECT * FROM {table} WHERE {condition} LIMIT 1"), params).fetchone()
            if row:
                return dict(row)
    return None


def news_count():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM news").fetchone()
        return int(row["n"]) if row else 0


def _count(table):
    with get_conn() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"]) if row else 0


def clear_news_section(section):
    """news 테이블에서 특정 섹션(nc/cat/biz)만 삭제. 반환: 삭제된 행 수."""
    cond, args = _section_cond(section)
    with get_conn() as conn:
        row = conn.execute(_q(f"SELECT COUNT(*) AS n FROM news WHERE {cond}"), args).fetchone()
        n = int(row["n"]) if row else 0
        conn.execute(_q(f"DELETE FROM news WHERE {cond}"), args)
    return n


def prune_news(keep=5000):
    """섹션별로 최신 keep건만 남기고 오래된 기사를 삭제한다(DB 무한 증가 방지, 화면 조회 한도와 일치).
    최신 기준은 published_at·id 내림차순(화면 정렬과 동일). 반환: 삭제 건수."""
    deleted = 0
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT COALESCE(section,'nc') AS sec FROM news").fetchall()
        for r in rows:
            sec = dict(r)["sec"]
            cur = conn.execute(_q(
                "DELETE FROM news WHERE COALESCE(section,'nc') = ? AND id NOT IN "
                "(SELECT id FROM news WHERE COALESCE(section,'nc') = ? "
                "ORDER BY published_at DESC, id DESC LIMIT ?)"),
                (sec, sec, keep))
            try:
                deleted += max(0, cur.rowcount or 0)
            except Exception:  # noqa: BLE001
                pass
    return deleted


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


# ---------------------------- 개인 상태(스크랩/읽음) ----------------------------
def user_state_upsert(username, ukey, kind, snapshot, ts):
    """아이디별 스크랩/읽음 저장(있으면 갱신)."""
    with get_conn() as conn:
        conn.execute(_q(
            "INSERT INTO user_state (username, ukey, kind, snapshot, ts) VALUES (?,?,?,?,?) "
            "ON CONFLICT (username, ukey, kind) DO UPDATE SET snapshot=excluded.snapshot, ts=excluded.ts"),
            (username, ukey, kind, snapshot, ts))


def user_state_delete(username, ukey, kind):
    with get_conn() as conn:
        conn.execute(_q("DELETE FROM user_state WHERE username=? AND ukey=? AND kind=?"),
                     (username, ukey, kind))


def user_state_bundle(username):
    with get_conn() as conn:
        rows = conn.execute(_q("SELECT * FROM user_state WHERE username=? ORDER BY ts DESC"), (username,)).fetchall()
        result = {"scrap": [], "read": [], "grouplist": []}
        for row in rows:
            item = dict(row)
            if item["kind"] in result:
                result[item["kind"]].append(item)
        return result


def user_state_list(username, kind):
    """해당 아이디의 kind(스크랩/읽음) 목록을 최신순으로."""
    with get_conn() as conn:
        rows = conn.execute(_q(
            "SELECT ukey, snapshot, ts FROM user_state WHERE username=? AND kind=? ORDER BY ts DESC"),
            (username, kind)).fetchall()
        return [dict(r) for r in rows]


def user_state_get(username, ukey, kind):
    """단건 조회. 없으면 None."""
    with get_conn() as conn:
        row = conn.execute(_q(
            "SELECT ukey, snapshot, ts FROM user_state WHERE username=? AND ukey=? AND kind=?"),
            (username, ukey, kind)).fetchone()
        return dict(row) if row else None


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


def list_news(limit=5000, category=None, section="nc", months=None):
    """뉴스 목록(최신순). months가 있으면 초기 로딩 가속을 위해 '최신 일부'만 반환한다.
    날짜 비교 대신 개수로 제한(발행일 형식/누락과 무관하게 안전) — 검색 시 전체를 받아 수행한다."""
    cond, cargs = _section_cond(section)
    if months:
        limit = min(limit, 800)   # 초기엔 최신 800건만(≈ 최근 몇 개월), 검색은 전체
    with get_conn() as conn:
        if category and category != "all":
            rows = conn.execute(
                _q(f"SELECT * FROM news WHERE {cond} AND category = ? "
                   "ORDER BY published_at DESC, id DESC LIMIT ?"),
                (*cargs, category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                _q(f"SELECT * FROM news WHERE {cond} ORDER BY published_at DESC, id DESC LIMIT ?"),
                (*cargs, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def security_needs_ai(limit=60):
    """AI 후처리(태깅/중요도/시사점)가 아직 안 된 보안뉴스 rows(최신순)."""
    with get_conn() as conn:
        rows = conn.execute(
            _q("SELECT url, title, content, category, published_at FROM news "
               "WHERE section = ? AND (ai_at IS NULL OR ai_at = '') "
               "ORDER BY published_at DESC, id DESC LIMIT ?"),
            ("sec", limit),
        ).fetchall()
        return [dict(r) for r in rows]


def apply_security_ai(data, at=None):
    """data: {url: {tags:[...], importance, insight(dict/str)}} → news에 저장. 반환 저장 건수."""
    import json as _json
    from datetime import datetime as _dt
    at = at or _dt.now().isoformat(timespec="minutes")
    n = 0
    with get_conn() as conn:
        cur = conn.cursor()
        for url, d in (data or {}).items():
            tags = d.get("tags") or []
            tags_s = ",".join(t for t in tags if t) if isinstance(tags, list) else str(tags)
            imp = (d.get("importance") or "").upper()
            insight = d.get("insight")
            insight_s = insight if isinstance(insight, str) else _json.dumps(insight or {}, ensure_ascii=False)
            cur.execute(
                _q("UPDATE news SET ai_tags=?, ai_importance=?, ai_insight=?, ai_at=? WHERE url=?"),
                (tags_s, imp, insight_s, at, url),
            )
            n += 1
    return n


def security_ai_stats():
    """보안뉴스 총건수/AI 분석 완료 건수."""
    with get_conn() as conn:
        total = conn.execute(_q("SELECT COUNT(*) AS c FROM news WHERE section = ?"), ("sec",)).fetchone()
        done = conn.execute(
            _q("SELECT COUNT(*) AS c FROM news WHERE section = ? AND ai_at IS NOT NULL AND ai_at <> ''"),
            ("sec",)).fetchone()
        return {"total": (dict(total)["c"] if total else 0), "analyzed": (dict(done)["c"] if done else 0)}


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


# ============ 점심 맛집(lunch) ============
def lunch_seed_locations(offices):
    """사업장이 하나도 없으면 초기 3개소 등록. offices: [{name,address,radius}] (좌표는 나중에 지오코딩)."""
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM lunch_location").fetchone()
        if (dict(row)["c"] if row else 0) > 0:
            return 0
        cur = conn.cursor()
        for i, o in enumerate(offices):
            cur.execute(_q("INSERT INTO lunch_location (name, address, lat, lng, radius, sort, created_at) "
                           "VALUES (?,?,?,?,?,?,?)"),
                        (o["name"], o.get("address", ""), o.get("lat"), o.get("lng"),
                         int(o.get("radius", 500)), i, _now()))
        return len(offices)


def _lunch_merge_restaurants(conn, from_loc, to_loc):
    """from_loc의 식당을 to_loc으로 이동(place_id 충돌 시 중복 삭제). 위치 병합용."""
    rows = conn.execute(_q("SELECT id, place_id FROM lunch_restaurant WHERE loc_id=?"), (from_loc,)).fetchall()
    for r in rows:
        r = dict(r)
        rid, pid = r["id"], r["place_id"]
        dup = conn.execute(_q("SELECT id FROM lunch_restaurant WHERE loc_id=? AND place_id=?"),
                           (to_loc, pid)).fetchone()
        if dup:  # 이미 대상 위치에 같은 place_id → 이쪽(중복) 삭제
            conn.execute(_q("DELETE FROM lunch_review WHERE restaurant_id=?"), (rid,))
            conn.execute(_q("DELETE FROM lunch_visit WHERE restaurant_id=?"), (rid,))
            conn.execute(_q("DELETE FROM lunch_restaurant WHERE id=?"), (rid,))
        else:
            conn.execute(_q("UPDATE lunch_restaurant SET loc_id=? WHERE id=?"), (to_loc, rid))


def lunch_sync_locations(offices):
    """위치를 offices와 정확히 일치시킨다(재배포 시 이름 변경·주소 교정·중복 정리).
    - 주소 기준으로 기존 행을 매칭(주소는 사업장 고유·안정 키). 주소 변경분은 순번(sort)으로 재사용.
    - 같은 주소의 중복 위치는 식당을 하나로 병합 후 삭제(중복 명칭 버그 해결).
    - offices에 없는 잔여 위치는 식당 0곳이면 삭제, 있으면 보존.
    - 주소가 동일하면 좌표 보존, 바뀌면 좌표 리셋(재지오코딩)."""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM lunch_location ORDER BY sort, id").fetchall()]
        used = set()
        canon_by_addr = {}

        def take(pred):  # 조건에 맞는 첫 미사용 행 선택
            for r in rows:
                if r["id"] not in used and pred(r):
                    used.add(r["id"])
                    return r
            return None

        for i, o in enumerate(offices):
            new_addr = o.get("address", "")
            rad = int(o.get("radius", 500))
            # 1) 같은 주소 행 → 2) 해당 순번 행 → 3) 임의 미사용 행 → 4) 신규
            match = (take(lambda r, a=new_addr: (r.get("address") or "") == a)
                     or take(lambda r, s=i: r.get("sort") == s)
                     or take(lambda r: True))
            if not match:
                inserted = conn.execute(_q("INSERT INTO lunch_location (name, address, lat, lng, radius, sort, created_at) "
                                "VALUES (?,?,?,?,?,?,?) RETURNING id"),
                             (o["name"], new_addr, o.get("lat"), o.get("lng"), rad, i, _now())).fetchone()
                canon_by_addr[new_addr] = inserted["id"]
                continue
            if (match.get("address") or "") != new_addr:  # 주소 변경 → 좌표 리셋
                conn.execute(_q("UPDATE lunch_location SET name=?, address=?, lat=NULL, lng=NULL, radius=?, sort=? WHERE id=?"),
                             (o["name"], new_addr, rad, i, match["id"]))
            elif (match.get("name"), match.get("radius"), match.get("sort")) != (o["name"], rad, i):
                conn.execute(_q("UPDATE lunch_location SET name=?, radius=?, sort=? WHERE id=?"),
                             (o["name"], rad, i, match["id"]))
            match["address"] = new_addr
            canon_by_addr[new_addr] = match["id"]

        # 잔여(미사용) 위치 정리: 같은 주소의 정식 위치가 있으면 식당 병합 후 삭제, 없고 비었으면 삭제
        for r in rows:
            if r["id"] in used:
                continue
            keep = canon_by_addr.get(r.get("address") or "")
            if keep:
                _lunch_merge_restaurants(conn, r["id"], keep)
                conn.execute(_q("DELETE FROM lunch_location WHERE id=?"), (r["id"],))
            else:
                cnt = conn.execute(_q("SELECT COUNT(*) AS c FROM lunch_restaurant WHERE loc_id=?"), (r["id"],)).fetchone()
                if (dict(cnt)["c"] if cnt else 0) == 0:
                    conn.execute(_q("DELETE FROM lunch_location WHERE id=?"), (r["id"],))


def lunch_list_locations():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM lunch_location ORDER BY sort, id").fetchall()
        return [dict(r) for r in rows]


def lunch_get_location(loc_id, conn=None):
    with (get_conn() if conn is None else nullcontext(conn)) as conn:
        r = conn.execute(_q("SELECT * FROM lunch_location WHERE id=?"), (loc_id,)).fetchone()
        return dict(r) if r else None


def lunch_set_location_coords(loc_id, lat, lng):
    with get_conn() as conn:
        conn.execute(_q("UPDATE lunch_location SET lat=?, lng=? WHERE id=?"), (lat, lng, loc_id))


def lunch_set_location_radius(loc_id, radius):
    with get_conn() as conn:
        conn.execute(_q("UPDATE lunch_location SET radius=? WHERE id=?"), (int(radius), loc_id))


def lunch_upsert_restaurants(loc_id, items):
    """카카오/수동 수집 결과 저장(loc_id+place_id 기준 중복 방지). 반환 (신규, 갱신)."""
    new = upd = 0
    with get_conn() as conn:
        known = {row["place_id"] for row in conn.execute(_q("SELECT place_id FROM lunch_restaurant WHERE loc_id=?"), (loc_id,)).fetchall()}
        cur = conn.cursor()
        for it in items:
            pid = it.get("place_id")
            if not pid:
                continue
            if pid in known:
                cur.execute(_q("UPDATE lunch_restaurant SET name=?, category=?, cat_norm=?, sub_cat=?, "
                               "address=?, road_address=?, lat=?, lng=?, phone=?, place_url=?, last_checked=? "
                               "WHERE loc_id=? AND place_id=?"),
                            (it.get("name"), it.get("category"), it.get("cat_norm"), it.get("sub_cat"),
                             it.get("address"), it.get("road_address"), it.get("lat"), it.get("lng"),
                             it.get("phone"), it.get("place_url"), _now(), loc_id, pid))
                upd += 1
            else:
                cur.execute(_q("INSERT INTO lunch_restaurant (loc_id, source, place_id, name, category, "
                               "cat_norm, sub_cat, address, road_address, lat, lng, phone, place_url, "
                               "excluded, first_seen, last_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)"),
                            (loc_id, it.get("source", "kakao"), pid, it.get("name"), it.get("category"),
                             it.get("cat_norm"), it.get("sub_cat"), it.get("address"), it.get("road_address"),
                             it.get("lat"), it.get("lng"), it.get("phone"), it.get("place_url"), _now(), _now()))
                known.add(pid)
                new += 1
    return new, upd


def lunch_list_restaurants(loc_id, include_excluded=False, conn=None):
    """Aggregate only this location's reviews/visits, with no row multiplication."""
    cond = "" if include_excluded else "AND r.excluded=0"
    with (get_conn() if conn is None else nullcontext(conn)) as conn:
        rows = conn.execute(_q(
            "SELECT r.*, COALESCE(v.review_count,0) AS review_count, v.avg_rating, COALESCE(v.sample_review_count,0) AS sample_review_count, "
            "COALESCE(t.visit_count,0) AS visit_count FROM lunch_restaurant r "
            "LEFT JOIN (SELECT v.restaurant_id, COUNT(*) AS review_count, AVG(v.rating) AS avg_rating, "
            "SUM(CASE WHEN v.username='방문기록 · 예시 리뷰' THEN 1 ELSE 0 END) AS sample_review_count "
            "FROM lunch_review v JOIN lunch_restaurant lr ON lr.id=v.restaurant_id "
            "WHERE lr.loc_id=? GROUP BY v.restaurant_id) v ON v.restaurant_id=r.id "
            "LEFT JOIN (SELECT t.restaurant_id, COUNT(*) AS visit_count FROM lunch_visit t "
            "JOIN lunch_restaurant lr ON lr.id=t.restaurant_id WHERE lr.loc_id=? "
            "GROUP BY t.restaurant_id) t ON t.restaurant_id=r.id "
            f"WHERE r.loc_id=? {cond} ORDER BY r.id DESC"),
            (loc_id, loc_id, loc_id)).fetchall()
        return [dict(r) for r in rows]


def lunch_restaurant_data(loc_id, include_excluded=False):
    with get_conn() as conn:
        loc = lunch_get_location(loc_id, conn=conn)
        return loc, lunch_list_restaurants(loc_id, include_excluded, conn=conn) if loc else []


def lunch_visit_context(username):
    """Recommendation history: four per-user lookups become one grouped query."""
    from datetime import timedelta
    cutoff3 = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    cutoff7 = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    with get_conn() as conn:
        rows = conn.execute(_q(
            "SELECT v.restaurant_id, r.cat_norm, MAX(v.visited_at) AS last_visit "
            "FROM lunch_visit v LEFT JOIN lunch_restaurant r ON r.id=v.restaurant_id "
            "WHERE v.username=? GROUP BY v.restaurant_id, r.cat_norm"), (username,)).fetchall()
    recent_ids, cats3, cats7, all_ids = set(), set(), set(), set()
    for row in rows:
        all_ids.add(row["restaurant_id"])
        if row["last_visit"] >= cutoff3:
            recent_ids.add(row["restaurant_id"])
            if row["cat_norm"]:
                cats3.add(row["cat_norm"])
        if row["last_visit"] >= cutoff7 and row["cat_norm"]:
            cats7.add(row["cat_norm"])
    return recent_ids, cats3, cats7, all_ids


def lunch_purge_location(loc_id):
    """해당 위치의 식당/후기/방문 데이터 전부 삭제(맛집 메뉴 초기화). 반환: 삭제된 식당 수."""
    with get_conn() as conn:
        rids = [dict(r)["id"] for r in conn.execute(
            _q("SELECT id FROM lunch_restaurant WHERE loc_id=?"), (loc_id,)).fetchall()]
        for rid in rids:
            conn.execute(_q("DELETE FROM lunch_review WHERE restaurant_id=?"), (rid,))
            conn.execute(_q("DELETE FROM lunch_visit WHERE restaurant_id=?"), (rid,))
        conn.execute(_q("DELETE FROM lunch_restaurant WHERE loc_id=?"), (loc_id,))
        return len(rids)


def lunch_get_restaurant(rid):
    with get_conn() as conn:
        r = conn.execute(_q("SELECT * FROM lunch_restaurant WHERE id=?"), (rid,)).fetchone()
        return dict(r) if r else None


def lunch_manual_add(loc_id, data):
    """관리자 수동 추가. place_id 없으면 manual:<name>로 생성."""
    pid = data.get("place_id") or ("manual:" + (data.get("name") or ""))
    data = dict(data); data["place_id"] = pid; data.setdefault("source", "manual")
    return lunch_upsert_restaurants(loc_id, [data])


def lunch_set_excluded(rid, excluded):
    with get_conn() as conn:
        conn.execute(_q("UPDATE lunch_restaurant SET excluded=? WHERE id=?"), (1 if excluded else 0, rid))


def lunch_add_review(rid, username, rating, comment):
    with get_conn() as conn:
        conn.execute(_q("INSERT INTO lunch_review (restaurant_id, username, rating, comment, created_at) "
                        "VALUES (?,?,?,?,?)"), (rid, username, int(rating), comment or "", _now()))


def lunch_list_reviews(rid, limit=100):
    with get_conn() as conn:
        rows = conn.execute(_q("SELECT username, rating, comment, created_at FROM lunch_review "
                               "WHERE restaurant_id=? ORDER BY id DESC LIMIT ?"), (rid, limit)).fetchall()
        return [dict(r) for r in rows]


def lunch_add_visit(rid, username):
    with get_conn() as conn:
        conn.execute(_q("INSERT INTO lunch_visit (restaurant_id, username, visited_at) VALUES (?,?,?)"),
                     (rid, username, _now()))


def lunch_recent_visited_ids(username, days=14):
    """최근 N일 내 이 사용자가 방문한 restaurant_id 집합(추천 회피용)."""
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.now() - _td(days=days)).isoformat(timespec="seconds")
    with get_conn() as conn:
        rows = conn.execute(_q("SELECT DISTINCT restaurant_id FROM lunch_visit "
                               "WHERE username=? AND visited_at>=?"), (username, cutoff)).fetchall()
        return {dict(r)["restaurant_id"] for r in rows}


def lunch_recent_visited_cats(username, days=7):
    """최근 N일 내 이 사용자가 방문한 식당의 카테고리(cat_norm) 집합(메뉴 다양성용)."""
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.now() - _td(days=days)).isoformat(timespec="seconds")
    with get_conn() as conn:
        rows = conn.execute(_q(
            "SELECT DISTINCT r.cat_norm AS c FROM lunch_visit v "
            "JOIN lunch_restaurant r ON r.id = v.restaurant_id "
            "WHERE v.username=? AND v.visited_at>=?"), (username, cutoff)).fetchall()
        return {dict(r)["c"] for r in rows if dict(r).get("c")}


def lunch_all_visited_ids(username):
    """이 사용자가 (기간 무관) 한 번이라도 방문한 restaurant_id 집합('오랜만이야' 페르소나용)."""
    with get_conn() as conn:
        rows = conn.execute(_q("SELECT DISTINCT restaurant_id FROM lunch_visit WHERE username=?"),
                            (username,)).fetchall()
        return {dict(r)["restaurant_id"] for r in rows}
