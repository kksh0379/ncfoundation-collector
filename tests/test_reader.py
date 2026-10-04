import socket
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from collector import db, reader


TEXT = "지역 사회의 새로운 문화교육 프로그램이 시작됩니다. 참여자들은 함께 배우며 다양한 경험을 나눕니다. " * 5
HTML = f'<html><article><h2>문화교육 소식</h2><p>{TEXT}</p><div class="ad">광고문구</div><script>alert(1)</script><p>두 번째 문단입니다.</p></article></html>'


class ReaderTests(unittest.TestCase):
    def setUp(self):
        reader._cache.clear()
        reader._summary_sources.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = patch.object(db, "DB_PATH", str(Path(self.tmp.name) / "test.db"))
        self.db_path.start()
        db.init_db()
        with db.get_conn() as conn:
            conn.execute("INSERT INTO news (url, source_url, title, content) VALUES (?, ?, ?, ?)",
                         ("https://news.example/rss/1", "https://publisher.example/story/1", "테스트 기사", "저장된 요약"))
        app = Flask(__name__)
        app.register_blueprint(reader.bp)
        self.client = app.test_client()

    def tearDown(self):
        self.db_path.stop()
        self.tmp.cleanup()

    def test_article_removes_ads_and_scripts_keeps_paragraphs(self):
        paragraphs = reader.extract_paragraphs(HTML)
        self.assertIn(TEXT.strip(), paragraphs)
        self.assertIn("두 번째 문단입니다.", paragraphs)
        self.assertNotIn("광고문구", " ".join(paragraphs))
        self.assertNotIn("alert", " ".join(paragraphs))

    def test_publisher_div_bodies_are_extracted_without_page_navigation(self):
        for marker in ('id="article-view-content-div"', 'id="news_body_area"', 'id="article_txt"', 'class="article-text"'):
            with self.subTest(marker=marker):
                html = f'<nav>{TEXT}</nav><div {marker}><p>{TEXT}</p></div>'
                self.assertEqual(reader.extract_paragraphs(html), [TEXT.strip()])

    def test_rejects_navigation_only(self):
        with self.assertRaises(reader.ReaderUnavailable):
            reader.extract_paragraphs('<main><a href="/">' + TEXT + '</a></main>')

    def test_rejects_paywall(self):
        for marker in ('<script type="application/ld+json">{"isAccessibleForFree":false}</script>', '<div class="paywall">구독</div>'):
            with self.subTest(marker=marker), self.assertRaises(reader.ReaderUnavailable):
                reader.extract_paragraphs(marker + HTML)

    def test_korean_legacy_encoding(self):
        paragraphs = reader.extract_paragraphs(('<meta charset="euc-kr">' + HTML).encode("euc-kr"))
        self.assertIn("두 번째 문단입니다.", paragraphs)

    def test_rejects_private_loopback_and_mixed_dns(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"):
            answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                       (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.subTest(address=address), patch.object(socket, "getaddrinfo", return_value=answers):
                with self.assertRaises(reader.ReaderUnavailable):
                    reader.public_target("https://publisher.example/story")

    def test_rejects_bad_schemes_credentials_and_ports(self):
        for url in ("file:///etc/passwd", "http://user:pw@example.com/", "https://example.com:9000/", "http://example.com/\n"):
            with self.subTest(url=url), self.assertRaises(reader.ReaderUnavailable):
                reader.public_target(url)

    def test_public_target_pins_resolved_address(self):
        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            self.assertEqual(reader.public_target("https://publisher.example/a")[3], "93.184.216.34")

    def test_unknown_url_does_not_fetch(self):
        with patch.object(reader, "fetch_html") as fetch:
            self.assertEqual(self.client.get('/api/reader?url=https://unknown.example/').status_code, 404)
            fetch.assert_not_called()

    def test_database_failure_is_recoverable(self):
        with patch.object(db, "reader_item", side_effect=RuntimeError("private details")):
            response = self.client.get('/api/reader?url=https://publisher.example/story/1')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private details", response.get_data(as_text=True))

    def test_success_and_cache_by_canonical_url(self):
        with patch.object(reader, "fetch_html", return_value=HTML) as fetch:
            first = self.client.get('/api/reader?url=https://news.example/rss/1').get_json()
            second = self.client.get('/api/reader?url=https://publisher.example/story/1').get_json()
            self.assertEqual(first["mode"], "article")
            self.assertEqual(first, second)
            fetch.assert_called_once_with("https://publisher.example/story/1")

    def test_google_news_resolves_before_safe_fetch(self):
        item = {"url": "https://news.google.com/rss/articles/test", "title": "기사"}
        with patch("collector.google_news._decode_google_url", return_value="https://publisher.example/story"), patch.object(reader, "fetch_html", return_value=HTML) as fetch:
            data = reader.read_article(item)
            self.assertEqual(data["mode"], "article")
            self.assertEqual(data["url"], "https://publisher.example/story")
            fetch.assert_called_once_with("https://publisher.example/story")

    def test_timeout_returns_labelled_excerpt(self):
        with patch.object(reader, "fetch_html", side_effect=TimeoutError):
            data = self.client.get('/api/reader?url=https://publisher.example/story/1').get_json()
            self.assertEqual(data["mode"], "excerpt")
            self.assertEqual(data["paragraphs"], ["저장된 요약"])
            self.assertIn("수집된 내용", data["notice"])

    def test_rejects_redirect_to_internal_network(self):
        class RedirectConnection:
            def __init__(self, *args): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return self
            status = 302
            def getheader(self, name): return "http://127.0.0.1/secret"
            def close(self): pass
        def resolve(host, port, **kwargs):
            return [(2, 1, 6, "", (("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"), port))]
        with patch.object(socket, "getaddrinfo", side_effect=resolve), patch.object(reader, "_PinnedHTTP", RedirectConnection):
            with self.assertRaises(reader.ReaderUnavailable):
                reader.fetch_html("https://publisher.example/story")

    def test_response_size_and_mime_limits(self):
        class Connection:
            sock = None
            def __init__(self, *args): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return self
            status = 200
            headers = Message()
            def getheader(self, name, default=""):
                return "text/html" if name == "Content-Type" else default
            def read1(self, size): return b"a" * 32768
            def close(self): pass
        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]), patch.object(reader, "_PinnedHTTP", Connection):
            with self.assertRaisesRegex(reader.ReaderUnavailable, "large"):
                reader.fetch_html("https://publisher.example/story")
            with patch.object(Connection, "getheader", return_value="application/pdf"):
                with self.assertRaisesRegex(reader.ReaderUnavailable, "Not HTML"):
                    reader.fetch_html("https://publisher.example/story")

    def test_boards_and_events_lookup(self):
        with db.get_conn() as conn:
            conn.execute("INSERT INTO boards (url, title) VALUES (?, ?)", ("https://board.example/1", "게시판"))
            conn.execute("INSERT INTO events (url, source_url, title) VALUES (?, ?, ?)", ("https://event.example/1", "https://publisher.example/event", "행사"))
        self.assertEqual(db.reader_item("https://board.example/1")["title"], "게시판")
        self.assertEqual(db.reader_item("https://publisher.example/event")["title"], "행사")


if __name__ == "__main__":
    unittest.main()
