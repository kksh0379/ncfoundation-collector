"""탭3: 소셜 채널 수집.

대상
- 인스타그램(재단), 인스타그램(프로젝토리)
- 유튜브(NC문화재단)

유튜브는 채널 RSS 피드로 최신 영상을 수집한다.
  https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>

인스타그램은 공개 스크래핑이 차단(로그인 월/봇 차단)되어 있어, 공식 Graph API
(비즈니스 계정 + 액세스 토큰) 없이는 수집할 수 없다. 핸들/토큰이 설정되기 전까지는
건너뛴다(unsupported=True). 수집 항목·중복 기준은 게시판과 동일하게 맞춘다.
"""
import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from . import extractor, fetcher

START_DATE = datetime(2026, 1, 1)

SOURCES = [
    {
        "channel": "인스타그램", "account": "재단",
        "type": "instagram",
        "url": "https://www.instagram.com/nccf.official/",
        "unsupported": True,  # 공식 Graph API 토큰 없이는 수집 불가(스크래핑 차단)
    },
    {
        "channel": "인스타그램", "account": "프로젝토리",
        "type": "instagram",
        "url": "https://www.instagram.com/projectory_official/",
        "unsupported": True,
    },
    {
        "channel": "유튜브", "account": "NC문화재단",
        "type": "youtube",
        "url": "https://www.youtube.com/@nccf",
    },
]

YT_FEED = "https://www.youtube.com/feeds/videos.xml"


def _youtube_channel_id(url):
    """유튜브 채널 URL에서 channel_id(UC...)를 알아낸다."""
    if not url:
        return None
    m = re.search(r"/channel/(UC[\w-]+)", url) or re.search(r"channel_id=(UC[\w-]+)", url)
    if m:
        return m.group(1)
    try:
        resp = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        print(f"[social] 유튜브 채널 조회 실패: {url} ({e})", flush=True)
        return None
    html = resp.text
    for pat in (
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]+)"',
        r'"channelId":"(UC[\w-]+)"',
        r'"externalId":"(UC[\w-]+)"',
        r"youtube\.com/channel/(UC[\w-]+)",
        r"channel_id=(UC[\w-]+)",
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def _crawl_youtube(cfg, max_items=15):
    cid = _youtube_channel_id(cfg["url"])
    if not cid:
        print(f"[social] {cfg['channel']}·{cfg['account']}: channel_id 못 찾음 ({cfg['url']})", flush=True)
        return []
    print(f"[social] 유튜브 channel_id={cid}", flush=True)
    try:
        resp = fetcher.get(YT_FEED, params={"channel_id": cid})
    except Exception as e:  # noqa: BLE001
        print(f"[social] 유튜브 RSS 실패: {e}", flush=True)
        return []

    soup = BeautifulSoup(resp.content, "xml")
    entries = soup.find_all("entry")
    print(f"[social] 유튜브 RSS 영상 {len(entries)}개", flush=True)
    items = []
    for entry in entries[:max_items]:
        title_el = entry.find("title")
        vid_el = entry.find("videoId")
        link_el = entry.find("link")
        pub_el = entry.find("published")
        desc_el = entry.find("description")  # media:description

        if vid_el and vid_el.text:
            link = "https://www.youtube.com/watch?v=" + vid_el.text.strip()
        else:
            link = link_el.get("href") if link_el else None

        published = pub_el.text[:16].replace("T", " ") if (pub_el and pub_el.text) else None
        if published:
            try:
                if datetime.fromisoformat(published.replace(" ", "T")) < START_DATE:
                    continue  # 2026-01-01 이전 제외
            except ValueError:
                pass

        items.append({
            "channel": cfg["channel"],
            "account": cfg["account"],
            "title": extractor.clean_text(title_el.text) if title_el else None,
            "published_at": published,
            "content": extractor.summarize(desc_el.text if desc_el else ""),
            "url": link,
        })
    return items


def crawl_source(cfg, max_items=10):
    label = f"{cfg['channel']} · {cfg['account']}"
    if cfg.get("unsupported") or not cfg.get("url"):
        print(f"[social] {label}: 수집 불가(공식 API/핸들 필요)", flush=True)
        return []
    t0 = time.time()
    if cfg["type"] == "youtube":
        items = _crawl_youtube(cfg, max_items=max_items)
    else:
        items = []
    print(f"[social] {label}: 수집 {len(items)}건 / {time.time() - t0:.1f}s", flush=True)
    return items


def crawl_all(max_items=10, progress=None):
    progress = progress or (lambda m: None)
    t0 = time.time()
    results = []
    for cfg in SOURCES:
        items = crawl_source(cfg, max_items=max_items)
        results.extend(items)
        progress(f"{cfg['channel']} · {cfg['account']}: {len(items)}건")
    print(f"[social] 전체 완료: 총 {len(results)}건 / {time.time() - t0:.1f}s", flush=True)
    return results
