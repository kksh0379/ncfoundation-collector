"""Finance adapters. Secrets stay server-side; unavailable data is never a verdict."""
import os
import re
import io
import math
import time
import zipfile
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, quote, urlencode
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, jsonify, request
from lxml import etree
from collector.read_cache import ReadCache
from collector import reader

bp = Blueprint('finance', __name__, url_prefix='/api/finance')
cache = ReadCache(max_entries=16, workers=2)
# 출처별 최근 성공분 보관(일시 실패 시 빈 섹션 대신 직전 기사 유지).
_feed_cache = {}
_feed_lock = threading.Lock()
# 리더가 등록 목록에 없어도 현재 브리핑 기사를 찾도록 하는 URL→기사 색인.
_article_index = {}
KST = timezone(timedelta(hours=9))


def _gnews(query):
    """공식 RSS를 확인하지 못한 출처는 구글 뉴스 RSS(공개·안정 엔드포인트)로 주제별 수집한다.
    운영자가 FINANCE_RSS_<코드>에 검증된 공식 RSS를 지정하면 그 값이 항상 우선한다."""
    return 'https://news.google.com/rss/search?' + urlencode(
        {'q': query, 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'})


# 공식 RSS가 확인된 출처(MOEF)는 그대로, 나머지는 구글 뉴스 주제 RSS로 기본 연결.
# 임의의 비공개 엔드포인트를 추측하지 않으며, 운영자 환경변수로 공식 RSS를 지정하면 대체된다.
SOURCES = [
    ('MOEF', '재정경제부(구 기획재정부)', 'policy', 'https://mofe.go.kr/'),
    ('NTS', '국세청·국세 뉴스', 'policy', 'https://www.nts.go.kr/'),
    ('PWC', '삼일회계법인(PwC) 뉴스', 'guide', 'https://www.pwc.com/kr/ko.html'),
    ('KPMG', '삼정KPMG 뉴스', 'guide', 'https://kpmg.com/kr/ko/home.html'),
    ('JOSEILBO', '조세일보', 'guide', 'https://www.joseilbo.com/'),
    ('ASSEMBLY', '세법 입법 동향', 'legislation', 'https://pal.assembly.go.kr/'),
]
DEFAULT_FEEDS = {
    'MOEF': 'https://mofe.go.kr/com/detailRssTagService.do?bbsId=MOSFBBS_000000000028',
    'NTS': _gnews('국세청 세금 세정'),
    'PWC': _gnews('삼일회계법인 PwC 세무'),
    'KPMG': _gnews('삼정KPMG 세무'),
    'JOSEILBO': _gnews('조세일보'),
    'ASSEMBLY': _gnews('세법 개정 입법예고'),
}
CATEGORIES = {'policy': '세법·보도자료', 'guide': '회계·세무 가이드', 'legislation': '입법예고'}
# 리더 본문 추출이 어려운 출처는 리더뷰 대신 원문으로 연다(재정경제부 등).
ORIGINAL_SOURCES = {'MOEF'}
INDICATORS = [('731Y001', '0000001', '원/달러 환율', '원', 1350.0, ''),
              ('731Y001', '0000002', '원/엔(100엔)', '원', 900.0, ''),
              ('731Y001', '0000003', '원/유로', '원', 1450.0, ''),
              ('731Y001', '0000053', '원/위안', '원', 190.0, ''),
              ('722Y001', '0101000', '한국은행 기준금리', '%', 3.0,
               '한국은행 금융통화위원회가 정하는 정책금리입니다. 예금·대출 등 시중금리의 기준이 됩니다.'),
              ('817Y002', '010502000', 'CD 91일', '%', 3.0,
               'CD(양도성예금증서) 91일물 금리입니다. 은행이 발행하는 단기 예금증서의 금리로, 단기 시장금리·대출금리의 기준으로 쓰입니다.'),
              ('817Y002', '010200000', '국고채 3년', '%', 2.8,
               '정부가 발행하는 만기 3년 국고채의 유통수익률입니다. 시장금리 수준과 채권시장을 보는 대표 지표입니다.'),
              ('901Y009', '0', '소비자물가(지수)', '', 115.0,
               '소비자물가지수(2020=100)입니다. 전월 대비 증감을 함께 표시하며, 물가 상승 흐름을 봅니다.', 'M')]
# 국세 법정 신고·납부 기한. 모두 세법에 명시된 고정 기한이며, 추측이 아닌 확정 규칙입니다.
# 기한이 주말이면 다음 영업일로 조정합니다(국세기본법 제5조). 공휴일이 겹치면 추가 연장되나,
# 공휴일(특히 음력 명절) 날짜는 검증 없이 하드코딩하지 않고 안내 문구로만 처리합니다.
TAX_MONTHLY = [(10, '원천세 신고·납부', '전월 원천징수분(반기납부 승인 사업자 제외)')]
TAX_ANNUAL = [
    (1, 25, '부가가치세 제2기 확정신고·납부', '직전 과세기간(7~12월) 분'),
    (1, 25, '간이과세자 부가가치세 신고·납부', '직전 1년(1~12월) 분'),
    (2, 10, '면세사업자 사업장현황신고', '개인 면세사업자 직전 연도 수입금액'),
    (3, 31, '법인세 신고·납부', '12월 말 결산 법인'),
    (4, 25, '부가가치세 제1기 예정신고·납부', '법인사업자(개인 일반과세자는 예정고지)'),
    (5, 31, '종합소득세·개인지방소득세 확정신고·납부', '직전 연도 귀속분'),
    (6, 30, '성실신고확인대상자 종합소득세 신고·납부', '성실신고확인서 제출 대상'),
    (7, 25, '부가가치세 제1기 확정신고·납부', '1~6월 분'),
    (8, 31, '법인세 중간예납 신고·납부', '12월 말 결산 법인'),
    (10, 25, '부가가치세 제2기 예정신고·납부', '법인사업자(개인 일반과세자는 예정고지)'),
    (11, 30, '종합소득세 중간예납 납부', '고지분(11월 중 고지서 수령)'),
]
NTS_CALENDAR_URL = 'https://www.nts.go.kr/'


def now():
    return datetime.now(KST)


def safe_url(url):
    try:
        p = urlsplit(url)
        return url if p.scheme in ('https', 'http') and p.hostname and not p.username and not p.password else ''
    except ValueError:
        return ''


def get_json(url, **kwargs):
    response = requests.get(url, timeout=(3, 7), allow_redirects=False, **kwargs)
    response.raise_for_status()
    return response.json()


def plain(text):
    return BeautifulSoup(text or '', 'html.parser').get_text(' ', strip=True)


def parse_feed(content, source):
    # 많은 피드가 URL·본문의 &를 escape하지 않아 XML이 깨진다. 유효 엔티티가 아닌 &만 바이트 수준에서
    # &amp;로 바꿔 URL 손상 없이 복구하고(인코딩 무관), 파서도 관대 모드로 둔다. XXE는 계속 차단.
    content = re.sub(rb'&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)', b'&amp;', content or b'')
    root = etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=True))
    if root is None or etree.QName(root).localname not in ('rss', 'feed', 'RDF'):
        raise ValueError('Not a feed')
    items = []
    for node in root.xpath('//*[local-name()="item" or local-name()="entry"]')[:50]:
        def field(*names):
            for name in names:
                found = node.xpath('./*[local-name()=$name]', name=name)
                if found:
                    return ''.join(found[0].itertext()).strip()
            return ''
        url = field('link')
        if not url:
            links = node.xpath('./*[local-name()="link"][@href]')
            url = next((x.get('href') for x in links if x.get('rel', 'alternate') == 'alternate'), '')
        url = safe_url(url)
        # http 링크는 https로 올린다(재정경제부 등은 http 접근을 메인으로 돌리는데, https 직링크는
        # 딥링크 파라미터를 유지한다). 포트가 명시된 경우는 그대로 둔다.
        if url.startswith('http://') and ':' not in url[len('http://'):].split('/', 1)[0]:
            url = 'https://' + url[len('http://'):]
        title = plain(field('title'))[:250]
        if title and url:
            summary = plain(field('description', 'summary'))
            # content:encoded(정부 RSS 본문)·Atom content가 있으면 전체 본문을 확보해 리더 대체 표시에 쓴다.
            body = plain(field('encoded', 'content')) or summary
            items.append(dict(category=source[2], source=source[1], title=title,
                              description=summary[:500], content=body[:6000],
                              url=url, pub_date=field('pubDate', 'published', 'updated', 'date')[:80],
                              open_original=source[0] in ORIGINAL_SOURCES, mode='live'))
    return items


def _fetch_feed(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                             '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
               'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8',
               'Accept-Language': 'ko-KR,ko;q=0.9',
               # 구글 뉴스는 데이터센터 IP에 동의 페이지를 띄운다. 동의 쿠키로 RSS를 바로 받는다.
               'Cookie': 'CONSENT=YES+'}
    # 느린 피드 하나가 대시보드 전체를 막지 않도록 빠르게 실패시킨다(실패분은 최근 성공분으로 대체).
    with requests.get(url, timeout=(4, 8), stream=True, allow_redirects=True, headers=headers) as response:
        response.raise_for_status()
        if urlsplit(response.url).scheme != 'https':
            raise ValueError('Insecure redirect')
        chunks, size = [], 0
        for chunk in response.iter_content(32768):
            size += len(chunk)
            if size > 2 * 1024 * 1024:
                raise ValueError('Feed too large')
            chunks.append(chunk)
    return b''.join(chunks)


def collect_source(source):
    env = 'FINANCE_RSS_' + source[0]
    url = os.getenv(env, DEFAULT_FEEDS.get(source[0], '')).strip()
    status = dict(name=source[1], category=source[2], url=source[3], mode='unconfigured')
    if not url:
        return [], status
    # Operator-controlled URL only(공식 RSS 또는 구글 뉴스); 브라우저 입력은 받지 않는다.
    if not safe_url(url) or urlsplit(url).scheme != 'https':
        return [], status
    try:
        items = parse_feed(_fetch_feed(url), source)
        with _feed_lock:
            _feed_cache[source[0]] = (time.monotonic(), items)
        status.update(mode='live', count=len(items))
        return items, status
    except Exception:
        pass
    # 실패 시 최근 성공분(최대 6시간)을 유지해 섹션이 빈 채로 비지 않게 한다.
    with _feed_lock:
        cached = _feed_cache.get(source[0])
    if cached and (time.monotonic() - cached[0]) < 6 * 3600 and cached[1]:
        status.update(mode='live', count=len(cached[1]), stale=True)
        return list(cached[1]), status
    status['mode'] = 'unavailable'
    return [], status


def news():
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        results = list(executor.map(collect_source, SOURCES))
    items, sources, seen = [], [], set()
    for rows, status in results:
        sources.append(status)
        for row in rows:
            if row['url'] not in seen:
                seen.add(row['url'])
                items.append(row)
    return dict(items=items, sources=sources, fetched_at=now().isoformat())


def sample_history(values, points=26):
    """최근 6개월 추이를 과밀하지 않게 균등 샘플링. 가장 최신 값은 항상 포함한다."""
    if len(values) <= points:
        sampled = values
    else:
        step = (len(values) - 1) / (points - 1)
        index = sorted({round(i * step) for i in range(points)} | {len(values) - 1})
        sampled = [values[i] for i in index]
    return [dict(date=d, value=v) for d, v in sampled]


def indicator(spec):
    # spec: (table, code, name, unit, sample, desc[, freq]) — freq 생략 시 일(D).
    table, code, name, unit, sample, desc = spec[:6]
    freq = spec[6] if len(spec) > 6 else 'D'
    result = dict(code=f'{table}/{code}', name=name, unit=unit, value=sample,
                  change=None, ratio=None, date=None, mode='demo', history=[], desc=desc)
    key = os.getenv('ECOS_API_KEY')
    if not key:
        return result
    try:
        end = now().date()
        # ECOS는 주기별로 날짜 형식이 다르다: 일 YYYYMMDD, 월 YYYYMM, 년 YYYY.
        if freq == 'M':
            s, e = (end - timedelta(days=430)).strftime('%Y%m'), end.strftime('%Y%m')
        elif freq == 'A':
            s, e = (end - timedelta(days=1830)).strftime('%Y'), end.strftime('%Y')
        else:
            s, e = (end - timedelta(days=190)).strftime('%Y%m%d'), end.strftime('%Y%m%d')
        url = (f'https://ecos.bok.or.kr/api/StatisticSearch/{quote(key, safe="")}/json/kr/1/700/'
               f'{table}/{freq}/{s}/{e}/{code}')
        rows = get_json(url)['StatisticSearch']['row']
        rows = sorted(rows, key=lambda x: x['TIME'])
        values = [(r['TIME'], float(r['DATA_VALUE'])) for r in rows if r.get('DATA_VALUE') not in (None, '')]
        if not values or not all(math.isfinite(v) for _, v in values):
            raise ValueError('No observations')
        last_time = values[-1][0]
        # 월/년 주기는 기준일 표기를 '2025.09'·'2025'처럼 다듬는다(일 주기는 YYYYMMDD 그대로 → 프론트에서 변환).
        disp = (f'{last_time[:4]}.{last_time[4:6]}' if freq == 'M' and len(last_time) >= 6
                else last_time[:4] if freq == 'A' else last_time)
        result.update(value=values[-1][1], date=disp, mode='live',
                      change=round(values[-1][1]-values[-2][1], 4) if len(values)>1 else None,
                      history=sample_history(values))
    except Exception:
        result.update(mode='unavailable', value=None)
    return result


def _num(value):
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def _parse_fchart_series(content):
    """네이버 fchart XML(일별 시세)에서 (날짜, 종가) 리스트를 뽑는다.
    data="날짜|시가|고가|저가|종가|거래량". 외부 엔티티·네트워크 차단, 손상 복구 허용."""
    root = etree.fromstring(content, parser=etree.XMLParser(
        resolve_entities=False, no_network=True, recover=True))
    values = []
    for item in root.xpath('//item'):
        parts = (item.get('data') or '').split('|')
        close = _num(parts[4]) if len(parts) >= 5 else None
        if parts and parts[0].isdigit() and close is not None and math.isfinite(close):
            values.append((parts[0], close))
    return values


def _series_quote(url, result, digits=0):
    """fchart형 일별 종가 시계열을 받아 값·등락·6개월 추이로 result를 채운다.
    실패하면 값을 지어내지 않고 mode='unavailable'로 둔다."""
    try:
        if urlsplit(url).scheme != 'https':
            raise ValueError('HTTPS required')
        response = requests.get(url, timeout=(3, 7), allow_redirects=False,
                                headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        values = _parse_fchart_series(response.content)
        if len(values) < 2:
            raise ValueError('No series')
        values.sort()
        last, prev = values[-1][1], values[-2][1]
        change = last - prev
        rnd = (lambda x: round(x, digits)) if digits else round
        result.update(mode='live', value=rnd(last), date=values[-1][0],
                      change=rnd(change), ratio=round(change / prev * 100, 2) if prev else None,
                      history=sample_history(values))
    except Exception:
        result.update(mode='unavailable', value=None, change=None, ratio=None, history=[])
    return result


def nc_stock():
    """(주)엔씨 주가와 추이. 일별 종가 시계열로 값·등락·6개월 추이를 만든다. 실패 시 값을 지어내지 않는다."""
    code = os.getenv('NC_STOCK_CODE', '036570')
    name = os.getenv('NC_STOCK_NAME', '(주)엔씨')
    result = dict(code='KRX/' + code, name=name, unit='원', value=None,
                  change=None, ratio=None, date=None, mode='unavailable', history=[], desc='')
    # 네이버 일별 시세(종가 시계열) XML. requestType=0 → data="날짜|시가|고가|저가|종가|거래량".
    url = os.getenv('NC_STOCK_URL') or ('https://fchart.stock.naver.com/sise.nhn?symbol='
                                        + quote(code, safe='') + '&timeframe=day&count=140&requestType=0')
    return _series_quote(url, result)


def next_business_day(day):
    # 주말이면 다음 영업일(월요일)로 이동. 공휴일은 안내 문구로만 처리한다.
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def tax_calendar(today=None):
    today = (today or now().date())
    horizon = today + timedelta(days=120)
    raw = []
    # 매월 반복 기한(원천세 등)을 향후 5개월까지 생성.
    for offset in range(0, 5):
        year, month = divmod(today.year * 12 + today.month - 1 + offset, 12)
        month += 1
        for day, title, note in TAX_MONTHLY:
            raw.append((datetime(year, month, day).date(), title, note))
    # 연 1회 고정 기한은 올해와 내년 모두 생성해 연말·연초 경계를 포함.
    for year in (today.year, today.year + 1):
        for month, day, title, note in TAX_ANNUAL:
            raw.append((datetime(year, month, day).date(), title, note))
    events, seen = [], set()
    for statutory, title, note in sorted(raw):
        due = next_business_day(statutory)
        if not (today <= due <= horizon):
            continue
        key = (due.isoformat(), title)
        if key in seen:
            continue
        seen.add(key)
        events.append(dict(date=due.isoformat(), statutory_date=statutory.isoformat(),
                           shifted=due != statutory, days_left=(due - today).days,
                           title=title, note=note, url=NTS_CALENDAR_URL))
    events.sort(key=lambda e: (e['date'], e['title']))
    return dict(mode='reference', source_url=NTS_CALENDAR_URL, events=events[:8],
        message='세법에 명시된 국세 신고·납부 법정기한입니다. 주말은 다음 영업일로 조정했으며, '
                '공휴일이 겹치면 기한이 하루 이상 연장될 수 있으니 확정 일정은 국세청 홈택스에서 확인하세요.')


def dashboard():
    # 엔씨 주가는 자주 바뀌므로 대시보드 캐시와 분리(/stock 엔드포인트, 짧은 캐시)해 방문마다 갱신한다.
    with ThreadPoolExecutor(max_workers=3) as executor:
        indicators = list(executor.map(indicator, INDICATORS))
    data = news()
    data.update(indicators=indicators, calendar=tax_calendar())
    return data


@bp.get('/dashboard')
def dashboard_route():
    data = cache.get('dashboard', dashboard, ttl=900, wait=0)
    if data is None:
        indicators = [dict(code=s[0]+'/'+s[1], name=s[2], unit=s[3], value=s[4], change=None,
            ratio=None, date=None, mode='demo', history=[], desc=s[5]) for s in INDICATORS]
        return jsonify(dict(pending=True, indicators=indicators,
            items=[], sources=[], calendar=tax_calendar()))
    # 브리핑 기사를 리더(본문 읽기·AI 요약)로 열 수 있도록 메모리에 등록한다.
    entries = {r['url']: dict(title=r['title'], author=r.get('source', ''),
        published_at=r.get('pub_date', ''), content=r.get('content') or r.get('description', ''))
        for r in data.get('items', [])}
    reader.register_external([dict(url=u, **v) for u, v in entries.items()])
    _article_index.update(entries)
    if len(_article_index) > 3000:  # 오래된 항목 정리(메모리 보호).
        for key in list(_article_index)[:len(_article_index)-3000]:
            _article_index.pop(key, None)
    return jsonify(dict(data, pending=False))


# 등록 목록이 비어도(상한·TTL·재시작) 현재 브리핑 기사면 리더가 찾도록 보조 조회자 연결.
reader.register_resolver(lambda url: _article_index.get(url))


@bp.get('/stock')
def stock_route():
    # 엔씨 주가는 60초 캐시로 방문·수동 새로고침마다 최신에 가깝게 제공한다.
    data = cache.get('nc', nc_stock, ttl=60, wait=0)
    if data is None:
        return jsonify(dict(pending=True, code='KRX/'+os.getenv('NC_STOCK_CODE', '036570'),
            name=os.getenv('NC_STOCK_NAME', '(주)엔씨'), unit='원', value=None, change=None,
            ratio=None, date=None, mode='loading', history=[], desc=''))
    return jsonify(dict(data, pending=False))


@bp.get('/ecoscheck')
def ecoscheck():
    """지표(ECOS) 출처 진단: 각 통계코드를 직접 조회해 건수·최신값 또는 ECOS 에러 원문을 보여준다.
    어떤 코드가 실제로 되는지 확인용(API 키는 노출하지 않음)."""
    key = os.getenv('ECOS_API_KEY')
    end = now().date()
    out = []
    for spec in INDICATORS:
        table, code, name = spec[0], spec[1], spec[2]
        freq = spec[6] if len(spec) > 6 else 'D'
        row = {'name': name, 'table': table, 'code': code, 'freq': freq}
        if not key:
            row['note'] = 'ECOS_API_KEY 미설정'
            out.append(row)
            continue
        try:
            if freq == 'M':
                s, e = (end - timedelta(days=430)).strftime('%Y%m'), end.strftime('%Y%m')
            elif freq == 'A':
                s, e = (end - timedelta(days=1830)).strftime('%Y'), end.strftime('%Y')
            else:
                s, e = (end - timedelta(days=190)).strftime('%Y%m%d'), end.strftime('%Y%m%d')
            url = (f'https://ecos.bok.or.kr/api/StatisticSearch/{quote(key, safe="")}/json/kr/1/5/'
                   f'{table}/{freq}/{s}/{e}/{code}')
            payload = get_json(url)
            ss = payload.get('StatisticSearch') if isinstance(payload, dict) else None
            if ss and ss.get('row'):
                r = ss['row']
                row.update(ok=True, count=len(r), latest=r[-1].get('DATA_VALUE'), time=r[-1].get('TIME'))
            else:  # ECOS 에러 블록(RESULT.CODE/MESSAGE 등)만 노출(키 제외)
                row.update(ok=False, response=str(payload)[:300])
        except Exception as ex:  # noqa: BLE001
            row.update(ok=False, error=f'{type(ex).__name__}: {ex}')
        out.append(row)
    return jsonify(dict(has_key=bool(key), indicators=out))


@bp.post('/business-status')
def business_status():
    body = request.get_json(silent=True)
    raw = body.get('number', '') if isinstance(body, dict) else ''
    if not isinstance(raw, str) or not re.fullmatch(r'(?:[0-9]{10}|[0-9]{3}-[0-9]{2}-[0-9]{5})', raw):
        return jsonify(error='사업자등록번호 10자리를 입력해 주세요.'), 400
    number = raw.replace('-', '')
    key = os.getenv('NTS_API_KEY')
    if not key:
        return jsonify(mode='unconfigured', status='조회 불가', message='조회 서비스 연결 전입니다. 실제 사업자 상태는 확인되지 않았습니다.')
    try:
        response = requests.post('https://api.odcloud.kr/api/nts-businessman/v1/status',
            params={'serviceKey': key, 'returnType': 'JSON'}, json={'b_no': [number]}, timeout=(3, 7), allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if payload.get('status_code') != 'OK':
            raise ValueError('Provider failure')
        row = payload['data'][0]
        if row.get('b_no') != number:
            raise ValueError('Mismatched response')
        return jsonify(mode='live', status=row.get('b_stt') or '등록 상태 확인 불가',
            tax_type=row.get('tax_type', ''), end_date=row.get('end_dt', ''), checked_at=now().isoformat())
    except Exception:
        return jsonify(mode='unavailable', status='조회 실패', message='조회 기관 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.')


@bp.after_request
def private_response(response):
    if request.path.endswith('/business-status'):
        response.headers['Cache-Control'] = 'no-store'
    return response


# 종목코드(6자리) → DART 고유번호(8자리) 매핑. DART 공시조회 API는 고유번호만 받기 때문에,
# 상장사 종목코드로 조회하려면 이 변환표가 필요하다(corpCode.xml, 상장사만 종목코드 보유).
_corp_map_cache = {'at': 0.0, 'map': {}}


def _stock_to_corp():
    """{종목코드: 고유번호} 매핑을 24시간 캐시로 제공. 실패 시 기존 캐시(없으면 빈 dict)."""
    key = os.getenv('DART_API_KEY')
    if not key:
        return {}
    if _corp_map_cache['map'] and time.time() - _corp_map_cache['at'] < 86400:
        return _corp_map_cache['map']
    try:
        resp = requests.get('https://opendart.fss.or.kr/api/corpCode.xml',
                            params={'crtfc_key': key}, timeout=(3, 20))
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        root = etree.fromstring(zf.read(zf.namelist()[0]), parser=etree.XMLParser(
            resolve_entities=False, no_network=True, recover=True))
        m = {}
        for el in root.findall('.//list'):
            sc = (el.findtext('stock_code') or '').strip()
            cc = (el.findtext('corp_code') or '').strip()
            if len(sc) == 6 and sc.isdigit() and cc:
                m[sc] = cc
        if m:
            _corp_map_cache.update(map=m, at=time.time())
        return _corp_map_cache['map']
    except Exception:
        return _corp_map_cache['map']


def disclosures(corp):
    key = os.getenv('DART_API_KEY')
    if not key:
        return dict(mode='unconfigured', items=[], message='공시 서비스 연결 전입니다. Open DART에서 확인할 수 있습니다.')
    code = (corp or '').strip()
    if len(code) == 6:   # 종목코드 → 고유번호 변환(상장사만)
        mapped = _stock_to_corp().get(code)
        if not mapped:
            return dict(mode='unavailable', items=[],
                        message='해당 종목코드의 DART 고유번호를 찾지 못했어요. 상장사가 아니거나 종목코드가 다를 수 있어요.')
        code = mapped
    end = now().date()
    try:
        params = dict(crtfc_key=key, bgn_de=(end-timedelta(days=90)).strftime('%Y%m%d'),
                      end_de=end.strftime('%Y%m%d'), page_count=30, sort='date', sort_mth='desc')
        if code:
            params['corp_code'] = code
        data = get_json('https://opendart.fss.or.kr/api/list.json', params=params)
        if data.get('status') == '013':
            return dict(mode='live', items=[], message='최근 90일 공시가 없습니다.')
        if data.get('status') != '000':
            raise ValueError('Provider failure')
        items = [dict(company=r['corp_name'], title=r['report_nm'], date=r['rcept_dt'],
                      url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+quote(r['rcept_no'], safe='')) for r in data.get('list', [])]
        return dict(mode='live', items=items, fetched_at=now().isoformat())
    except Exception:
        return dict(mode='unavailable', items=[], message='공시를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.')


@bp.get('/disclosures')
def disclosures_route():
    corp = request.args.get('corp_code', '').strip()
    if corp and not re.fullmatch(r'[0-9]{6}|[0-9]{8}', corp):
        return jsonify(error='종목코드 6자리 또는 DART 고유번호 8자리를 입력해 주세요.'), 400
    data = cache.get('dart:'+corp, lambda: disclosures(corp), ttl=300, wait=0)
    return jsonify(data or dict(mode='loading', pending=True, items=[], message='공시를 불러오고 있습니다.'))
