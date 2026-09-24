"""탭3: 소셜 채널 수집.

대상
- 인스타그램(재단), 인스타그램(프로젝토리)
- 유튜브(NC문화재단)

유튜브는 채널 RSS 피드로 최신 영상을 수집한다.
  https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>

인스타그램은 로그인 없이 쓸 수 있는 공개 프로필 JSON 엔드포인트
(web_profile_info)로 베스트-에포트 수집한다. 인스타가 데이터센터 IP(무료 호스팅)
요청을 차단하면 0건이 될 수 있는데, 그 경우 사유를 로그로 남기고 건너뛴다.
확실한 수집이 필요하면 공식 Graph API(비즈니스 계정 + 토큰)로 교체한다.
"""
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from . import extractor, fetcher

SOURCES = [
    {
        "channel": "인스타그램", "account": "재단",
        "type": "instagram",
        "url": "https://www.instagram.com/nccf.official/",
    },
    {
        "channel": "인스타그램", "account": "프로젝토리",
        "type": "instagram",
        "url": "https://www.instagram.com/projectory_official/",
    },
    {
        "channel": "유튜브", "account": "NC문화재단",
        "type": "youtube",
        "url": "https://www.youtube.com/@nccf",
    },
]

YT_FEED = "https://www.youtube.com/feeds/videos.xml"
# 인스타 공개 웹앱 app id(로그인 없는 web_profile_info 호출에 필요).
IG_APP_ID = "936619743392459"
IG_PROFILE_API = "https://www.instagram.com/api/v1/users/web_profile_info/"


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

        image = None
        if vid_el and vid_el.text:
            vid = vid_el.text.strip()
            link = "https://www.youtube.com/watch?v=" + vid
            image = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"  # 영상 썸네일
        else:
            link = link_el.get("href") if link_el else None
        # RSS media:thumbnail 이 있으면 우선 사용
        thumb_el = entry.find("thumbnail")
        if thumb_el and thumb_el.get("url"):
            image = thumb_el.get("url")

        # 날짜 제한은 두지 않는다(영상 수가 많지 않아 전체 수집).
        published = pub_el.text[:16].replace("T", " ") if (pub_el and pub_el.text) else None

        items.append({
            "channel": cfg["channel"],
            "account": cfg["account"],
            "title": extractor.clean_text(title_el.text) if title_el else None,
            "published_at": published,
            "content": extractor.summarize(desc_el.text if desc_el else ""),
            "url": link,
            "image_url": image,
        })
    return items


def _instagram_username(url):
    m = re.search(r"instagram\.com/([^/?#]+)", url or "")
    return m.group(1) if m else None


def _fetch_instagram_json(user):
    """공개 web_profile_info JSON을 받아온다.

    헤더만으로 호출하면 401을 자주 맞으므로, 먼저 인스타 홈/프로필을 한 번 쳐서
    세션 쿠키(csrftoken)를 확보한 뒤 그 쿠키와 함께 API를 호출한다.
    그래도 데이터센터 IP(무료 호스팅)는 차단될 수 있으며, 실패 시 None."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": fetcher.DEFAULT_HEADERS["User-Agent"],
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    try:
        # 1) 쿠키(csrftoken) 확보
        s.get(f"https://www.instagram.com/{user}/", timeout=fetcher.TIMEOUT)
        csrf = s.cookies.get("csrftoken", "")
        # 2) 공개 프로필 API
        r = s.get(
            IG_PROFILE_API,
            params={"username": user},
            headers={
                "x-ig-app-id": IG_APP_ID,
                "x-csrftoken": csrf,
                "x-requested-with": "XMLHttpRequest",
                "Accept": "application/json",
                "Referer": f"https://www.instagram.com/{user}/",
            },
            timeout=fetcher.TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[social] 인스타 차단/실패({user}): {type(e).__name__}: {str(e)[:140]}", flush=True)
        return None
    finally:
        s.close()


def _crawl_instagram(cfg, max_items=12):
    """공개 프로필 JSON(web_profile_info)으로 최근 게시물을 수집한다.
    인스타가 차단하면 0건을 돌려준다(사유는 서버 로그에 기록)."""
    user = _instagram_username(cfg["url"])
    if not user:
        print(f"[social] 인스타 사용자명 파싱 실패: {cfg['url']}", flush=True)
        return []
    data = _fetch_instagram_json(user)
    if not data:
        return []

    edges = (((data or {}).get("data") or {}).get("user") or {}) \
        .get("edge_owner_to_timeline_media", {}).get("edges", [])
    print(f"[social] 인스타 {user} 게시물 {len(edges)}개", flush=True)
    items = []
    for edge in edges[:max_items]:
        node = edge.get("node", {})
        shortcode = node.get("shortcode")
        if not shortcode:
            continue
        cap = node.get("edge_media_to_caption", {}).get("edges", [])
        caption = cap[0]["node"]["text"] if cap else ""
        ts = node.get("taken_at_timestamp")
        published = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else None
        first_line = caption.splitlines()[0] if caption.strip() else "(이미지 게시물)"
        items.append({
            "channel": cfg["channel"],
            "account": cfg["account"],
            "title": extractor.clean_text(first_line)[:80],
            "published_at": published,
            "content": extractor.summarize(caption),
            "url": f"https://www.instagram.com/p/{shortcode}/",
            "image_url": node.get("thumbnail_src") or node.get("display_url"),
        })
    return items


def crawl_source(cfg, max_items=10):
    label = f"{cfg['channel']} · {cfg['account']}"
    if not cfg.get("url"):
        print(f"[social] {label}: URL 없음, 건너뜀", flush=True)
        return []
    t0 = time.time()
    if cfg["type"] == "youtube":
        items = _crawl_youtube(cfg, max_items=max_items)
    elif cfg["type"] == "instagram":
        items = _crawl_instagram(cfg, max_items=max_items)
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
