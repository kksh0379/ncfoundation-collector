"""HTTP 요청 헬퍼.

모바일 UA로 요청하고, 실패 시 가볍게 재시도한다. 일부 사이트는
정적 HTML이 아니라 JS로 렌더링되는 SPA일 수 있는데, 그 경우 requests로는
목록이 비어 보일 수 있다. 그때는 fetcher만 Playwright 기반으로 교체하면
나머지 크롤러 로직은 그대로 재사용할 수 있도록 분리해 두었다.
"""
import time

import requests
import urllib3

# SSL 검증을 끄고 재시도할 때 나오는 InsecureRequestWarning을 로그에서 억제한다.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# (연결+읽기) 타임아웃. 국내 기관 사이트는 응답이 느린 편이라 너무 짧으면
# 상태확인/수집이 ReadTimeout으로 실패한다. 무료 호스팅 gunicorn timeout(300s)
# 안에서 안전한 선에서 넉넉히 둔다.
TIMEOUT = 15


def get(url, params=None, headers=None, retries=1):
    last_err = None
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    for attempt in range(retries + 1):
        # verify=True로 먼저 시도하고, 인증서 검증 실패(체인 누락 등 국내 사이트에
        # 흔함) 시에만 verify=False로 재시도한다. 정상 사이트의 검증은 유지된다.
        for verify in (True, False):
            try:
                resp = requests.get(
                    url, params=params, headers=merged, timeout=TIMEOUT, verify=verify
                )
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or resp.encoding
                return resp
            except requests.exceptions.SSLError as e:
                last_err = e
                continue  # verify=False로 한 번 더
            except requests.RequestException as e:  # noqa: PERF203
                last_err = e
                break  # SSL 외 오류는 verify=False가 의미 없음 → 다음 재시도로
        if attempt < retries:
            time.sleep(0.8 * (attempt + 1))
    raise last_err


def get_json(url, params=None, headers=None, retries=2):
    return get(url, params=params, headers=headers, retries=retries).json()


def post(url, data=None, headers=None, retries=1):
    """POST 요청 헬퍼. 구글 뉴스 batchexecute 같은 폼 전송에 쓴다."""
    last_err = None
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, data=data, headers=merged, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:  # noqa: PERF203
            last_err = e
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    raise last_err
