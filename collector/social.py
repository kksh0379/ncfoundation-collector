"""탭: 재단YT(유튜브) 수집.

대상
- 재단: NC문화재단 유튜브 채널(@nccf) — 채널 업로드 전체(Data API) 또는 RSS 최신.
- 주요 재단: 업계동향과 동일한 주요 재단/공익법인명으로 유튜브 검색(Data API).
  (채널ID를 몰라도 되도록 검색 방식. YOUTUBE_API_KEY 필요 — 없으면 주요 재단은 건너뜀)
"""
import os
import re
import time

from bs4 import BeautifulSoup

from . import extractor, fetcher

YT_DATA_API = "https://www.googleapis.com/youtube/v3/playlistItems"
YT_SEARCH_API = "https://www.googleapis.com/youtube/v3/search"

# 재단(NC문화재단) 채널
SOURCES = [
    {
        "channel": "유튜브", "account": "NC문화재단",
        "type": "youtube",
        "url": "https://www.youtube.com/@nccf",
    },
]

# 주요 재단(업계동향 B 목록과 동일) — 유튜브 검색어로 사용
MAJOR_FOUNDATIONS = [
    "아산나눔재단", "삼성문화재단", "CJ문화재단", "롯데문화재단", "현대차 정몽구 재단",
    "포스코청암재단", "두산연강재단", "LG연암문화재단", "카카오임팩트", "네이버문화재단",
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


def _crawl_youtube_api(cfg, cid, max_items):
    """YouTube Data API v3로 채널의 '업로드 재생목록'을 페이지네이션해 전 영상을 수집한다.
    (RSS는 최신 ~15개만 주므로 과거 영상까지 받으려면 이 방식이 필요) 키 없으면 호출 안 함."""
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        return None  # 키 없음 → 호출측이 RSS로 폴백
    uploads = "UU" + cid[2:]  # 업로드 재생목록 id = 채널id의 UC→UU
    label = f"{cfg['channel']}·{cfg['account']}"
    cap = max(max_items, 1000)
    items, token = [], None
    for _ in range(40):  # 최대 40페이지(50개씩=2000개) 안전장치
        params = {"part": "snippet", "maxResults": 50, "playlistId": uploads, "key": key}
        if token:
            params["pageToken"] = token
        try:
            j = fetcher.get(YT_DATA_API, params=params, retries=1, timeout=12).json()
        except Exception as e:  # noqa: BLE001
            print(f"[social] 유튜브 Data API 실패({label}): {e}", flush=True)
            return None if not items else items  # 첫 페이지부터 실패면 RSS 폴백
        for it in j.get("items", []):
            sn = it.get("snippet", {})
            vid = (sn.get("resourceId") or {}).get("videoId")
            title = extractor.clean_text(sn.get("title") or "")
            if not vid or title in ("Private video", "Deleted video", ""):
                continue
            pub = (sn.get("publishedAt") or "")[:16].replace("T", " ")
            th = sn.get("thumbnails") or {}
            img = ((th.get("high") or th.get("medium") or th.get("default") or {}).get("url")
                   or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
            items.append({
                "channel": cfg["channel"], "account": cfg["account"],
                "title": title, "published_at": pub,
                "content": extractor.summarize(sn.get("description") or ""),
                "url": "https://www.youtube.com/watch?v=" + vid, "image_url": img,
            })
        token = j.get("nextPageToken")
        if not token or len(items) >= cap:
            break
    print(f"[social] 유튜브 Data API {label}: {len(items)}개", flush=True)
    return items


def _crawl_youtube(cfg, max_items=15):
    cid = _youtube_channel_id(cfg["url"])
    if not cid:
        print(f"[social] {cfg['channel']}·{cfg['account']}: channel_id 못 찾음 ({cfg['url']})", flush=True)
        return []
    print(f"[social] 유튜브 channel_id={cid}", flush=True)
    api_items = _crawl_youtube_api(cfg, cid, max_items)  # 키 있으면 전 영상, 없으면 None
    if api_items is not None:
        return api_items
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


def _crawl_youtube_search(query, account, max_items=8):
    """YouTube Data API v3 search로 주요 재단명 관련 최신 영상을 수집한다.
    채널ID를 몰라도 되도록 검색 방식 사용. 키 없으면 빈 리스트."""
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        return []
    params = {
        "part": "snippet", "q": query, "type": "video", "order": "date",
        "maxResults": max_items, "regionCode": "KR", "relevanceLanguage": "ko", "key": key,
    }
    try:
        j = fetcher.get(YT_SEARCH_API, params=params, retries=1, timeout=12).json()
    except Exception as e:  # noqa: BLE001
        print(f"[social] 유튜브 검색 실패({account}): {e}", flush=True)
        return []
    if j.get("error"):
        print(f"[social] 유튜브 검색 오류({account}): {str(j['error'])[:160]}", flush=True)
        return []
    items = []
    for it in j.get("items", []):
        vid = (it.get("id") or {}).get("videoId")
        if not vid:
            continue
        sn = it.get("snippet", {})
        title = extractor.clean_text(sn.get("title") or "")
        if not title:
            continue
        pub = (sn.get("publishedAt") or "")[:16].replace("T", " ")
        th = sn.get("thumbnails") or {}
        img = ((th.get("high") or th.get("medium") or th.get("default") or {}).get("url")
               or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
        items.append({
            "channel": "유튜브", "account": account,
            "title": title, "published_at": pub,
            "content": extractor.summarize(sn.get("description") or ""),
            "url": "https://www.youtube.com/watch?v=" + vid, "image_url": img,
        })
    print(f"[social] 유튜브 검색 {account}: {len(items)}개", flush=True)
    return items


def crawl_source(cfg, max_items=10):
    label = f"{cfg['channel']} · {cfg['account']}"
    if not cfg.get("url"):
        print(f"[social] {label}: URL 없음, 건너뜀", flush=True)
        return []
    t0 = time.time()
    items = _crawl_youtube(cfg, max_items=max_items) if cfg["type"] == "youtube" else []
    print(f"[social] {label}: 수집 {len(items)}건 / {time.time() - t0:.1f}s", flush=True)
    return items


def crawl_all(max_items=10, progress=None):
    progress = progress or (lambda m: None)
    t0 = time.time()
    results = []
    # 1) 재단: NC문화재단 채널
    for cfg in SOURCES:
        items = crawl_source(cfg, max_items=max_items)
        results.extend(items)
        progress(f"{cfg['account']}: {len(items)}건")
    # 2) 주요 재단: 기관명으로 유튜브 검색(키 있을 때만)
    if os.environ.get("YOUTUBE_API_KEY", "").strip():
        for i, name in enumerate(MAJOR_FOUNDATIONS, 1):
            items = _crawl_youtube_search(name, name, max_items=8)
            results.extend(items)
            progress(f"주요 재단 {i}/{len(MAJOR_FOUNDATIONS)} · {name}: {len(items)}건")
    else:
        progress("주요 재단: YOUTUBE_API_KEY 없음 → 건너뜀")
    print(f"[social] 전체 완료: 총 {len(results)}건 / {time.time() - t0:.1f}s", flush=True)
    return results
