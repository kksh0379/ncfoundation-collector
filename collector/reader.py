"""Text-only reader for collected articles; never serves publisher HTML or scripts."""
import http.client
import ipaddress
import os
import re
import socket
import ssl
import threading
import time
from collections import OrderedDict
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from flask import Blueprint, jsonify, request

from collector import db
from collector.reader_summary import article_summary

bp = Blueprint("reader", __name__)
MAX_BYTES = 2 * 1024 * 1024
_cache = OrderedDict()
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(3)
_summary_sources = OrderedDict()
# 재무세무 브리핑 등 DB에 저장하지 않는 수집 항목을 리더가 읽을 수 있도록 메모리에 등록한다.
_external = OrderedDict()


def _remember_summary_source(url, article):
    with _lock:
        _summary_sources[url] = (time.monotonic() + 600, article)
        _summary_sources.move_to_end(url)
        while len(_summary_sources) > 64:
            _summary_sources.popitem(last=False)


def register_external(items):
    """URL로 리더 조회가 가능하도록 임시 항목을 등록(TTL 1시간).
    상한은 모든 출처의 현재 기사(출처×최대건수)를 넉넉히 담아, 먼저 등록된 출처(재정경제부 등)가
    상한 초과로 밀려나 404가 나지 않게 한다."""
    now = time.monotonic()
    with _lock:
        for item in items:
            url = item.get("url")
            if not url or len(url) > 4096:
                continue
            _external[url] = (now + 3600, {
                "url": url, "source_url": url,
                "title": item.get("title") or "제목 없음",
                "author": item.get("author") or "",
                "published_at": item.get("published_at") or "",
                "content": item.get("content") or ""})
            _external.move_to_end(url)
        while len(_external) > 1200:
            _external.popitem(last=False)


def _external_item(url):
    with _lock:
        hit = _external.get(url)
        if hit and hit[0] > time.monotonic():
            _external.move_to_end(url)
            return dict(hit[1])
        if hit:
            _external.pop(url, None)
    return None


_resolvers = []


def register_resolver(resolver):
    """등록 목록에 없어도 URL로 항목을 찾는 보조 조회자(예: 재무세무 대시보드 캐시)."""
    _resolvers.append(resolver)


def _resolve_external(url):
    item = _external_item(url)
    if item:
        return item
    for resolver in _resolvers:
        try:
            got = resolver(url)
        except Exception:
            got = None
        if got:
            return {"url": url, "source_url": url,
                    "title": got.get("title") or "제목 없음",
                    "author": got.get("author") or "",
                    "published_at": got.get("published_at") or "",
                    "content": got.get("content") or ""}
    return None


class ReaderUnavailable(ValueError):
    pass


def public_target(url):
    """Resolve once and connect to that public IP (including every redirect)."""
    try:
        p = urlsplit(url)
        if (p.scheme not in ("http", "https") or not p.hostname or p.username
                or p.password or p.port not in (None, 80, 443)
                or any(ord(c) < 33 for c in url) or len(url) > 4096):
            raise ReaderUnavailable("Unsupported URL")
        host = p.hostname.encode("idna").decode("ascii")
        port = p.port or (443 if p.scheme == "https" else 80)
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise ReaderUnavailable("No address")
        for entry in addresses:
            address = ipaddress.ip_address(entry[4][0])
            if not address.is_global or (address.version == 6 and
                    (address.ipv4_mapped or address.sixtofour or address.teredo)):
                raise ReaderUnavailable("Non-public address")
        return p, host, port, addresses[0][4][0]
    except (ValueError, UnicodeError, OSError) as exc:
        raise ReaderUnavailable("Invalid destination") from exc


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address, self.secure = address, secure

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        if self.secure:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


def fetch_html(url):
    deadline = time.monotonic() + 10
    for _ in range(4):
        p, host, port, address = public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReaderUnavailable("Timeout")
        conn = _PinnedHTTP(host, port, address, p.scheme == "https", min(4, remaining))
        try:
            path = (p.path or "/") + (("?" + p.query) if p.query else "")
            # PC 브라우저처럼 요청한다(재정경제부 등은 모바일·직접접근을 메인으로 돌림).
            # 데스크톱 UA + 같은 사이트에서 넘어온 듯한 Referer를 보내 PC 기사 페이지를 받는다.
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                          "image/webp,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                "Referer": "%s://%s/" % (p.scheme, host),
                "Upgrade-Insecure-Requests": "1", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ReaderUnavailable("Missing redirect")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ReaderUnavailable("Publisher unavailable")
            if response.getheader("Content-Type", "").split(";", 1)[0].lower() not in (
                    "text/html", "application/xhtml+xml"):
                raise ReaderUnavailable("Not HTML")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ReaderUnavailable("Unsupported encoding")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ReaderUnavailable("Timeout")
                if conn.sock:
                    conn.sock.settimeout(min(4, remaining))
                chunk = response.read1(32768)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ReaderUnavailable("Page too large")
                chunks.append(chunk)
            raw = b"".join(chunks)
            charset = response.headers.get_content_charset()
            return raw.decode(charset, errors="replace") if charset else raw
        finally:
            conn.close()
    raise ReaderUnavailable("Too many redirects")


def extract_paragraphs(html):
    soup = BeautifulSoup(html, "lxml")
    # Respect access restrictions even when article text happens to be in the DOM.
    for script in soup.select('script[type="application/ld+json"]'):
        if re.search(r'"isAccessibleForFree"\s*:\s*(false|"false")', script.get_text(), re.I):
            raise ReaderUnavailable("Restricted article")
    if soup.select_one('[class*="paywall"], [id*="paywall"], [class*="subscription-wall"]'):
        raise ReaderUnavailable("Restricted article")
    for node in soup.select("script,style,iframe,nav,header,footer,aside,form,button,"
                            "noscript,svg,figure,[hidden],[aria-hidden=true],"
                            ".ad,.ads,.advertisement,.banner,.share,.sns,.related,.comments,.copyright"):
        node.decompose()
    # Prefer explicit article bodies over surrounding layouts, menus and sidebars.
    selectors = ("#dic_area, #articeBody, #articleBody, #article-body, #newsct_article,"
                 "[itemprop=articleBody], .article-body, .article_body, .article-view-content-div,"
                 ".view_content, .board_view, .post-content, .content_view,"
                 "#article-view-content-div, #newsEndContents, #news_body_area, #news_body_id,"
                 "#article_txt, #articleText, #artText, #news_content, #article_content,"
                 ".article_content, .article-text, .news_body, .news-body, .news_text,"
                 ".view_cont, .view_con, .article_view, .articleView,"
                 # 정부·공공기관 표준 CMS(재정경제부 등) 게시글 본문 컨테이너.
                 ".bbs_content, .board_con, .board_cont, .cont_view, .view_area, .view_body,"
                 "#viewContents, #bbsView, .bd_viewcont, .cont_body, .dbData, .cont",
                 "article", "main")
    for selector in selectors:
        candidates = []
        for node in soup.select(selector):
            for br in node.select("br"):
                br.replace_with("\n")
            blocks = node.select("p, h2, h3, blockquote, li")
            # Some publishers use only div + br for article paragraphs.
            if sum(len(b.get_text(strip=True)) for b in blocks) < 180:
                lines = node.get_text("\n", strip=True).splitlines()
            else:
                lines = [b.get_text(" ", strip=True) for b in blocks if not b.find_parent(["blockquote", "li"])]
            paragraphs = [re.sub(r"\s+", " ", line).strip() for line in lines]
            paragraphs = [p for p in paragraphs if p]
            length = sum(map(len, paragraphs))
            link_length = sum(len(a.get_text(strip=True)) for a in node.select("a"))
            if length >= 180 and link_length < length * .5:
                candidates.append((length, paragraphs))
        if candidates:
            paragraphs = max(candidates, key=lambda c: c[0])[1]
            if sum(map(len, paragraphs)) > 100000:
                raise ReaderUnavailable("Article too large")
            return paragraphs
    raise ReaderUnavailable("No article body")


_ERROR_SIGNS = ("페이지를 찾을 수가 없습니다", "페이지를 찾을 수 없", "주소가 잘못 입력",
                "변경 혹은 삭제", "요청하신 페이지", "존재하지 않는 페이지", "잘못된 접근",
                "sorry...", "page not found", "404 not found")


def looks_like_error_page(paragraphs):
    text = " ".join(paragraphs)[:1500].lower()
    return any(sign.lower() in text for sign in _ERROR_SIGNS)


def fetch_via_jina(url):
    """JS 렌더링 외부 리더(r.jina.ai)로 본문 텍스트를 받아 문단으로 반환. READER_JS_FALLBACK로만 활성화."""
    p, host, port, address = public_target("https://r.jina.ai/" + url)
    conn = _PinnedHTTP(host, port, address, True, 15)
    try:
        path = (p.path or "/") + (("?" + p.query) if p.query else "")
        conn.request("GET", path, headers={"User-Agent": "HuscopeReader/1.0",
            "Accept": "text/plain, text/markdown, */*", "Accept-Encoding": "identity",
            "X-Return-Format": "text"})
        response = conn.getresponse()
        if response.status != 200:
            raise ReaderUnavailable("Jina unavailable")
        chunks, size = [], 0
        while True:
            chunk = response.read1(32768)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                break
            chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8", "replace")
    finally:
        conn.close()
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)              # 이미지 마크다운 제거
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)          # 링크는 텍스트만
    paragraphs = [re.sub(r"^[#>*\-\s]+", "", ln).strip() for ln in re.split(r"\n{2,}", text)]
    paragraphs = [pp for pp in paragraphs if len(pp) >= 2]
    if sum(map(len, paragraphs)) < 120 or looks_like_error_page(paragraphs):
        raise ReaderUnavailable("Jina empty")
    return paragraphs[:400]


def read_article(item):
    url = item.get("source_url") or item["url"]
    result = {"title": item.get("title") or "제목 없음", "url": url,
              "author": item.get("author") or urlsplit(url).hostname,
              "published_at": item.get("published_at") or ""}
    with _lock:
        cached = _cache.get(url)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(url)
            return dict(result, **cached[1])
    if not _slots.acquire(blocking=False):
        return dict(result, **fallback(item))
    try:
        target = url
        try:
            if urlsplit(url).hostname == "news.google.com":
                from collector.google_news import _decode_google_url
                target = _decode_google_url(url) or url
            # Resolved destinations still pass the same pinned public-IP checks.
            paragraphs = extract_paragraphs(fetch_html(target))
            if looks_like_error_page(paragraphs):   # 사이트가 오류/안내 페이지를 준 경우
                raise ReaderUnavailable("Error/landing page")
            body = {"mode": "article", "paragraphs": paragraphs, "url": target}
        except (ReaderUnavailable, OSError, http.client.HTTPException, UnicodeError, LookupError):
            body = None
            if os.getenv("READER_JS_FALLBACK"):     # JS 렌더링 외부 리더(선택, env로 활성화)
                try:
                    body = {"mode": "article", "paragraphs": fetch_via_jina(target), "url": target}
                except Exception:
                    body = None
            if body is None:
                body = fallback(item)
        with _lock:
            _cache[url] = (time.monotonic() + (600 if body["mode"] == "article" else 60), body)
            _cache.move_to_end(url)
            while len(_cache) > 64:
                _cache.popitem(last=False)
        return dict(result, **body)
    finally:
        _slots.release()


def fallback(item):
    text = BeautifulSoup(item.get("content") or "", "lxml").get_text("\n", strip=True)[:20000]
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    if len(paragraphs) <= 1 and len(text) > 240:
        # 단일 블록이면 문장 단위로 묶어 가독성 확보(약 180자 문단).
        paragraphs, buffer = [], ""
        for sentence in re.split(r"(?<=다\.)\s+|(?<=[.!?])\s+", text):
            buffer += sentence + " "
            if len(buffer) >= 180:
                paragraphs.append(buffer.strip())
                buffer = ""
        if buffer.strip():
            paragraphs.append(buffer.strip())
    return {"mode": "excerpt", "paragraphs": paragraphs or ([text] if text else []),
            "notice": "원문 사이트의 본문을 자동으로 불러오지 못해 수집된 내용을 표시합니다. 첨부파일·전체 내용은 원문에서 확인해 주세요."}


@bp.get("/api/reader")
def reader_article():
    url = request.args.get("url", "")
    if not url or len(url) > 4096:
        return jsonify(error="읽을 글의 주소가 올바르지 않습니다."), 400
    db_error = False
    try:
        item = db.reader_item(url)
    except Exception:
        item, db_error = None, True
    if not item:
        item = _resolve_external(url)
    if not item:
        if db_error:
            return jsonify(error="저장소에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요."), 503
        return jsonify(error="저장된 글을 찾지 못했습니다. 원문 사이트에서 확인해 주세요."), 404
    article = read_article(item)
    _remember_summary_source(url, article)
    return jsonify(article)


@bp.get("/api/reader-summary")
def reader_summary():
    url = request.args.get("url", "")
    if not url or len(url) > 4096:
        return jsonify(error="읽을 글의 주소가 올바르지 않습니다."), 400
    with _lock:
        cached = _summary_sources.get(url)
        article = cached[1] if cached and cached[0] > time.monotonic() else None
    if article is None:
        db_error = False
        try:
            item = db.reader_item(url)
        except Exception:
            item, db_error = None, True
        if not item:
            item = _resolve_external(url)
        if not item:
            if db_error:
                return jsonify(error="저장소에 연결하지 못했습니다."), 503
            return jsonify(error="저장된 글을 찾지 못했습니다."), 404
        article = read_article(item)
        _remember_summary_source(url, article)
    result = article_summary(article)
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response, 202 if result["status"] == "pending" else 200
