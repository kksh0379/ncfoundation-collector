import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["AUTO_BACKFILL"] = "0"
os.environ["ENABLE_DB_PREWARM"] = "0"
os.environ["DB_KEEPALIVE_SEC"] = "0"

from collector import db
from collector.read_cache import ReadCache
import app as web


class CacheTests(unittest.TestCase):
    def test_concurrent_cold_requests_do_one_query(self):
        cache = ReadCache()
        started, release = threading.Event(), threading.Event()
        calls = []
        def load():
            calls.append(1)
            started.set()
            release.wait(2)
            return ["location"]
        with ThreadPoolExecutor(max_workers=10) as pool:
            jobs = [pool.submit(cache.get, "locations", load, wait=.8) for _ in range(10)]
            self.assertTrue(started.wait(1))
            release.set()
            self.assertEqual([j.result() for j in jobs], [["location"]] * 10)
        self.assertEqual(len(calls), 1)

    def test_stale_read_never_waits_for_database(self):
        cache = ReadCache()
        cache.get("key", lambda: [1])
        started, release = threading.Event(), threading.Event()
        def slow():
            started.set()
            release.wait(2)
            return [2]
        before = time.monotonic()
        self.assertEqual(cache.get("key", slow, ttl=0), [1])
        self.assertLess(time.monotonic() - before, .1)
        self.assertTrue(started.wait(1))
        event = cache.pending["key"]
        release.set()
        event.wait(1)
        self.assertEqual(cache.get("key", slow), [2])

    def test_invalidation_drops_inflight_result(self):
        cache = ReadCache()
        release = threading.Event()
        cache.get("lunch:1", lambda: (release.wait(2), ["old"])[1], wait=0)
        event = cache.pending["lunch:1"]
        cache.invalidate("lunch:")
        release.set()
        self.assertTrue(event.wait(1))
        self.assertEqual(cache.get("lunch:1", lambda: ["new"]), ["new"])

    def test_failures_back_off_and_keep_stale_value(self):
        cache = ReadCache()
        cache.get("key", lambda: [1])
        calls = []
        def fail():
            calls.append(1)
            raise RuntimeError("offline")
        self.assertEqual(cache.get("key", fail, ttl=0), [1])
        with cache.lock:
            pending = cache.pending.get("key")
        if pending:
            pending.wait(1)
        for _ in range(10):
            self.assertEqual(cache.get("key", fail, ttl=0), [1])
        self.assertEqual(len(calls), 1)

    def test_fork_resets_inherited_pending_work(self):
        cache = ReadCache(workers=1)
        cache.pending["key"] = threading.Event()
        cache.slots.acquire()
        cache._after_fork()
        self.assertEqual(cache.get("key", lambda: ["worker"]), ["worker"])

    def test_cache_is_bounded_and_empty_is_valid(self):
        cache = ReadCache(max_entries=3)
        for n in range(10):
            self.assertEqual(cache.get(str(n), lambda: []), [])
        self.assertEqual(len(cache.entries), 3)


class DatabasePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(db, "DB_PATH", str(Path(self.tmp.name) / "data.db"))
        self.path_patch.start()
        db.init_db()
        self.patches = [patch.object(web, "_PUBLIC_READS", ReadCache()),
                        patch.object(web, "_LOCATION_READS", ReadCache(max_entries=1, workers=1)),
                        patch.object(web, "_db_ready", True), patch.object(web, "_lunch_synced", True)]
        for p in self.patches:
            p.start()
        db.lunch_seed_locations([{"name": "지역 A"}, {"name": "지역 B"}])
        db.lunch_upsert_restaurants(1, [{"place_id": "a", "name": "식당 A"}, {"place_id": "b", "name": "식당 B"}])
        db.lunch_upsert_restaurants(2, [{"place_id": "c", "name": "다른 지역"}])
        self.client = web.app.test_client()

    def tearDown(self):
        for cache in (web._PUBLIC_READS, web._LOCATION_READS):
            with cache.lock:
                pending = list(cache.pending.values())
            for event in pending:
                event.wait(2)
        for p in reversed(self.patches):
            p.stop()
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_location_endpoint_returns_immediately_during_slow_db(self):
        gate, started = threading.Event(), threading.Event()
        def load():
            started.set()
            gate.wait(2)
            return [{"id": 1, "name": "지역 A"}]
        with patch.object(web, "_load_lunch_locations", side_effect=load) as query:
            before = time.monotonic()
            response = self.client.get("/api/lunch/locations")
            self.assertLess(time.monotonic() - before, .15)
            self.assertTrue(response.json["db_waking"])
            self.assertTrue(started.wait(1))
            for _ in range(20):
                self.client.get("/api/lunch/locations")
            self.assertEqual(query.call_count, 1)
            event = web._LOCATION_READS.pending["locations"]
            gate.set()
            event.wait(1)
            before = time.monotonic()
            for _ in range(100):
                self.assertFalse(self.client.get("/api/lunch/locations").json["db_waking"])
            elapsed = time.monotonic() - before
            print(f"\n100 warm location requests: {elapsed:.3f}s, database calls: {query.call_count}")
            self.assertEqual(query.call_count, 1)
            self.assertLess(elapsed, 1)

    def test_news_endpoint_loads_with_months_in_background_worker(self):
        # 회귀 방지: months/category 등 request 의존 값을 람다 '안'에서 읽으면
        # 캐시가 백그라운드 워커에서 로드할 때 "Working outside of request context."로
        # 매번 실패 → 영구 pending([]) → '불러오지 못했어요'. (v2.93에서 발생했던 버그)
        db.upsert_news_many([{
            "title": "엔씨 소식", "published_at": "2026-10-03T09:00:00", "author": "",
            "content": "요약", "url": "https://example.com/a", "content_hash": "h1",
            "group_key": "g1", "source_url": "https://example.com/a", "category": "all",
            "image_url": "", "section": "nc", "collected_at": "2026-10-03T09:00:00",
        }])
        # months를 붙여야(초기 로딩 경로) 과거 버그가 재현되는 조건이 된다.
        resp = self.client.get("/api/news?months=3")
        # 백그라운드 로드가 끝나도록 한 번 더(캐시 pending 완료 대기 후 재조회).
        for _ in range(8):
            if resp.headers.get("X-Data-Pending") != "1" and resp.get_json():
                break
            time.sleep(0.3)
            resp = self.client.get("/api/news?months=3")
        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(resp.headers.get("X-Data-Pending"), "1")
        data = resp.get_json()
        self.assertTrue(data and data[0]["title"] == "엔씨 소식")
        # 캐시에 '요청 컨텍스트' 관련 실패가 남아서는 안 된다.
        self.assertFalse(any("request context" in v for v in web._PUBLIC_READS.last_errors().values()))

    def test_metadata_and_features_share_one_read(self):
        with patch.object(db, "get_all_meta", wraps=db.get_all_meta) as query:
            self.client.get("/api/meta")
            self.client.get("/api/features")
            self.assertEqual(query.call_count, 1)

    def test_restaurant_data_uses_one_connection_and_correct_aggregates(self):
        rows = db.lunch_list_restaurants(1)
        rid = rows[0]["id"]
        db.lunch_add_review(rid, "one", 3, "")
        db.lunch_add_review(rid, "two", 5, "")
        for _ in range(3):
            db.lunch_add_visit(rid, "one")
        with patch.object(db, "get_conn", wraps=db.get_conn) as connect:
            loc, result = db.lunch_restaurant_data(1)
            self.assertEqual(connect.call_count, 1)
        self.assertEqual(len(result), 2)
        rated = next(r for r in result if r["id"] == rid)
        self.assertEqual((rated["review_count"], rated["avg_rating"], rated["visit_count"]), (2, 4, 3))
        unrated = next(r for r in result if r["id"] != rid)
        self.assertEqual((unrated["review_count"], unrated["avg_rating"], unrated["visit_count"]), (0, None, 0))

    def test_admin_results_never_leak_to_visitors(self):
        rid = db.lunch_list_restaurants(1)[0]["id"]
        db.lunch_set_excluded(rid, True)
        with self.client.session_transaction() as session:
            session["admin"] = True
        self.assertEqual(len(self.client.get("/api/lunch/restaurants?loc=1").json["restaurants"]), 2)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(len(self.client.get("/api/lunch/restaurants?loc=1").json["restaurants"]), 1)

    def test_review_invalidates_cached_ratings(self):
        rid = db.lunch_list_restaurants(1)[0]["id"]
        self.client.get("/api/lunch/restaurants?loc=1")
        with self.client.session_transaction() as session:
            session["user"] = "tester"
        self.assertEqual(self.client.post("/api/lunch/review", json={"rid": rid, "rating": 5}).status_code, 200)
        rows = self.client.get("/api/lunch/restaurants?loc=1").json["restaurants"]
        self.assertEqual(next(r for r in rows if r["id"] == rid)["avg_rating"], 5)

    def test_personal_state_is_one_query_and_user_scoped(self):
        db.user_state_upsert("one", "a", "read", "{}", 1)
        db.user_state_upsert("two", "b", "read", "{}", 2)
        with patch.object(db, "get_conn", wraps=db.get_conn) as connect:
            data = db.user_state_bundle("one")
        self.assertEqual(connect.call_count, 1)
        self.assertEqual([x["ukey"] for x in data["read"]], ["a"])

    def test_combined_recommendation_history_matches_old_queries(self):
        rid = db.lunch_list_restaurants(1)[0]["id"]
        with db.get_conn() as conn:
            conn.execute("UPDATE lunch_restaurant SET cat_norm='한식' WHERE id=?", (rid,))
        db.lunch_add_visit(rid, "one")
        expected = (db.lunch_recent_visited_ids("one", 3), db.lunch_recent_visited_cats("one", 3),
                    db.lunch_recent_visited_cats("one", 7), db.lunch_all_visited_ids("one"))
        with patch.object(db, "get_conn", wraps=db.get_conn) as connect:
            actual = db.lunch_visit_context("one")
        self.assertEqual(actual, expected)
        self.assertEqual(connect.call_count, 1)

    def test_news_filter_uses_composite_index(self):
        with db.get_conn() as conn:
            plan = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM news WHERE COALESCE(section,'nc')=? ORDER BY published_at DESC, id DESC LIMIT 3000", ("cat",)).fetchall()
        self.assertIn("idx_news_section_date", str([tuple(r) for r in plan]))

    def test_location_sync_preserves_coordinates_and_skips_unchanged_updates(self):
        offices = [{"name": "지역 A", "address": "서울 A", "radius": 500},
                   {"name": "지역 B", "address": "서울 B", "radius": 700}]
        db.lunch_sync_locations(offices)
        locs = db.lunch_list_locations()
        db.lunch_set_location_coords(locs[0]["id"], 37.5, 127.0)
        statements = []
        real_connect = db.sqlite3.connect
        def connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn
        with patch.object(db.sqlite3, "connect", side_effect=connect):
            db.lunch_sync_locations(offices)
        self.assertFalse(any(sql.startswith("UPDATE") for sql in statements))
        self.assertEqual(db.lunch_list_locations()[0]["lat"], 37.5)

    def test_feature_write_invalidates_flags_without_losing_other_settings(self):
        db.set_meta("feature_flags", '{"game":false,"social":false}')
        self.assertFalse(self.client.get("/api/features").json["game"])
        with self.client.session_transaction() as session:
            session["admin"] = True
        self.client.post("/api/features", json={"game": True})
        flags = self.client.get("/api/features").json
        self.assertTrue(flags["game"])
        self.assertFalse(flags["social"])

    def test_report_cache_invalidates_after_deletion(self):
        sid = db.save_report_snapshot("test", "2026-10-01", "test", "test", '{"summary":"test"}')
        self.assertEqual(self.client.get(f"/api/report/get?id={sid}").json["id"], sid)
        with self.client.session_transaction() as session:
            session["admin"] = True
        self.assertEqual(self.client.post("/api/report/purge", json={"id": sid}).status_code, 200)
        self.assertTrue(self.client.get(f"/api/report/get?id={sid}").json["empty"])


if __name__ == "__main__":
    unittest.main()
