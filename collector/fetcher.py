"""HTTP 요청 헬퍼.

모바일 UA로 요청하고, 실패 시 가볍게 재시도한다. 일부 사이트는
정적 HTML이 아니라 JS로 렌더링되는 SPA일 수 있는데, 그 경우 requests로는
목록이 비어 보일 수 있다. 그때는 fetcher만 Playwright 기반으로 교체하면
나머지 크롤러 로직은 그대로 재사용할 수 있도록 분리해 두었다.
"""
import time

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

TIMEOUT = 15


def get(url, params=None, headers=None, retries=2):
    last_err = None
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=merged, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp
        except requests.RequestException as e:  # noqa: PERF203
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last_err


def get_json(url, params=None, headers=None, retries=2):
    return get(url, params=params, headers=headers, retries=retries).json()
