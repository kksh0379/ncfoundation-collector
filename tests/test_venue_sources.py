import ast
import datetime as dt
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests
from collector import venue_sources as venues


class VenueTests(unittest.TestCase):
    def fixture(self, name):
        return (Path(__file__).parent / 'fixtures' / 'venue_schedules' / (name + '.html')).read_bytes()

    def test_six_observed_official_row_formats(self):
        samples = [('킨텍스', 'kintexwide'), ('벡스코', 'bexcosearch'),
                   ('대전컨벤션센터', 'dccwide'), ('aT센터', 'atwide'),
                   ('수원메쎄', 'suwonwide'), ('세텍', 'setec2')]
        for source, name in samples:
            with self.subTest(source=source):
                rows, _ = venues.parse(source, self.fixture(name), venues.SOURCES[source][1])
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row['source'], source)
                self.assertTrue(row['venue'].startswith(source))
                self.assertTrue(row['url'].startswith('https://'))
                self.assertNotIn('javascript:', row['url'])
                self.assertLessEqual(row['start_date'], row['end_date'])
                self.assertNotIn('searchStart', row['url'])

    def test_cat_fairs_keep_full_cross_month_dates_and_official_links(self):
        rows, _ = venues.parse('수원메쎄', self.fixture('suwonwide'), venues.SOURCES['수원메쎄'][1])
        self.assertEqual((rows[0]['start_date'], rows[0]['end_date']), ('2026-10-30', '2026-11-01'))
        self.assertIn('uid=691&mod=document', rows[0]['url'])
        self.assertTrue(rows[0]['image_url'].startswith('https://'))
        rows, _ = venues.parse('세텍', self.fixture('setec2'), venues.SOURCES['세텍'][1])
        self.assertEqual((rows[0]['start_date'], rows[0]['end_date']), ('2026-12-04', '2026-12-06'))
        self.assertIn('sIdx=2307', rows[0]['url'])

    def test_invalid_dates_are_rejected_instead_of_invented(self):
        self.assertEqual(venues._dates('2026.02.30 ~ 2026.03.02'), [])
        data = self.fixture('suwonwide').replace(b'2026-11-01', b'2026-02-30')
        rows, _ = venues.parse('수원메쎄', data, venues.SOURCES['수원메쎄'][1])
        self.assertEqual(rows, [])

    def test_pagination_filters_expired_and_avoids_repeat_pages(self):
        response = Mock(status_code=200, content=self.fixture('setec2'))
        session = Mock(); session.get.return_value = response
        session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
        with patch.object(venues.requests, 'Session', return_value=session):
            rows = venues._collect_one('세텍', dt.date(2026, 10, 4))
        self.assertEqual(len(rows), 1)
        self.assertTrue(venues.diagnose()['세텍']['ok'])
        self.assertLessEqual(session.get.call_count, 2)
        with patch.object(venues.requests, 'Session', return_value=session):
            self.assertEqual(venues._collect_one('세텍', dt.date(2027, 1, 1)), [])

    def test_dcc_timeout_uses_public_http_and_never_disables_tls(self):
        response = Mock(status_code=200, content=self.fixture('dccwide'))
        session = Mock(); session.get.side_effect = [requests.exceptions.ReadTimeout(), response]
        session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
        with patch.object(venues.requests, 'Session', return_value=session):
            self.assertEqual(len(venues._collect_one('대전컨벤션센터', dt.date(2026, 10, 4))), 1)
        self.assertTrue(session.get.call_args_list[1].args[0].startswith('http://www.dcckorea.or.kr/'))
        self.assertTrue(all('verify' not in call.kwargs for call in session.get.call_args_list))

    def test_suwon_transient_timeout_retries_once_and_keeps_complete_dates(self):
        response = Mock(status_code=200, content=self.fixture('suwonwide'))
        session = Mock(); session.get.side_effect = [requests.exceptions.ReadTimeout(), response]
        session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
        with patch.object(venues.requests, 'Session', return_value=session):
            rows = venues._collect_one('수원메쎄', dt.date(2026, 10, 4))
        self.assertEqual(rows[0]['end_date'], '2026-11-01')
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args.kwargs['timeout'], (10, 50))
        self.assertTrue(venues.diagnose()['수원메쎄']['ok'])

    def test_error_page_is_reported_and_other_sources_continue(self):
        response = Mock(status_code=200, content=b'<html>Unexpected page</html>')
        session = Mock(); session.get.return_value = response
        session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
        with patch.object(venues.requests, 'Session', return_value=session):
            self.assertEqual(venues._collect_one('세텍', dt.date(2026, 10, 4)), [])
        self.assertFalse(venues.diagnose()['세텍']['ok'])
        self.assertEqual(venues.diagnose()['세텍']['error'], 'ValueError')
        with patch.object(venues, '_collect_one', side_effect=lambda s, d: [{'source': s}] if s != '세텍' else []):
            self.assertEqual(len(venues.collect()), 5)

    def test_bootstrap_is_once_only_and_retries_partial_failure(self):
        tree = ast.parse(Path('app.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_bootstrap_venue_schedules')
        import json
        database = Mock(); database.get_meta.return_value = None
        save = Mock(return_value={'new': 1}); invalidate = Mock()
        scope = dict(os=os, db=database, json=json, venue_sources=venues, _now_kst=lambda: 'now', _ensure_db=lambda **kw: True,
                     _save_event=save, _invalidate_read_cache=invalidate, print=lambda *a, **kw: None)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), scope)
        good = {source: {'ok': True} for source in venues.SOURCES}
        with patch.dict(os.environ, {'ENABLE_SCHEDULER': '1', 'VENUE_SOURCES_OFF': '0'}), \
             patch.object(venues, 'collect', return_value=[{'url': 'official'}]) as collect, \
             patch.object(venues, 'diagnose', return_value=good):
            scope['_bootstrap_venue_schedules']()
            save.assert_called_once(); invalidate.assert_called_once()
            database.set_meta.assert_any_call('venue_sources_version', '1')
            database.get_meta.return_value = '1'
            scope['_bootstrap_venue_schedules']()
            self.assertEqual(collect.call_count, 1)
            database.get_meta.return_value = None; database.set_meta.reset_mock()
            good['세텍'] = {'ok': False}
            scope['_bootstrap_venue_schedules']()
            self.assertNotIn('venue_sources_version', [call.args[0] for call in database.set_meta.call_args_list])

    def test_worker_jobs_start_once_after_fork_and_no_thread_starts_at_import(self):
        tree = ast.parse(Path('app.py').read_text())
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                self.assertNotIn('.start()', ast.unparse(node))
                self.assertNotEqual(ast.unparse(node.value.func), '_start_scheduler')
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_start_worker_jobs')
        scheduler = Mock(); thread = Mock()
        targets = [Mock(), Mock(), Mock()]
        import threading
        scope = dict(os=os, threading=Mock(Thread=thread), _worker_jobs_pid=None,
                     _worker_jobs_lock=threading.Lock(), _start_scheduler=scheduler,
                     _auto_backfill=targets[0], _bootstrap_venue_schedules=targets[1], _db_keepalive=targets[2])
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), scope)
        scope['_start_worker_jobs'](); scope['_start_worker_jobs']()
        scheduler.assert_called_once()
        self.assertEqual(thread.call_count, 3)
        self.assertEqual([call.kwargs['target'] for call in thread.call_args_list], targets)


if __name__ == '__main__':
    unittest.main()
