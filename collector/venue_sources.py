"""Public venue schedules, independent of news coverage or event organizers.

Fetch a six-month window with bounded pagination. Dates come from each listing,
never from calendar cells (which truncate events at a month boundary).
"""
import datetime as dt
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
from lxml import etree

SOURCES = {
    '킨텍스': ('경기', 'https://www.kintex.com/web/ko/event/list.do'),
    '벡스코': ('부산', 'https://www.bexco.co.kr/kor/CMS/EventScheduleMgr/list.do'),
    '대전컨벤션센터': ('대전', 'https://www.dcckorea.or.kr/event/calendarList.do'),
    'aT센터': ('서울', 'https://www.at.or.kr/ac/event/acko311100/listList.action'),
    '수원메쎄': ('경기', 'https://suwonmesse.com/event_schedule/event_list/'),
    '세텍': ('서울', 'https://www.setec.or.kr/front/schedule/list.do'),
}
_status = {}


def _text(node):
    return re.sub(r'\s+', ' ', ''.join(node.itertext())).strip()


def _field(node, cls):
    found = node.xpath(".//*[contains(concat(' ',normalize-space(@class),' '), ' " + cls + " ')]")
    return _text(found[0]) if found else ''


def _dates(text):
    dates = []
    for y, m, d in re.findall(r'(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})', text):
        try:
            dates.append(dt.date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            return []
    return dates


def parse(source, content, base):
    """Parse only observed venue row structures, preserving stable detail URLs."""
    from .event_sources import _item
    root = etree.HTML(content, etree.HTMLParser(encoding='utf-8'))
    if root is None:
        raise ValueError('empty_html')
    if source == '킨텍스':
        nodes = root.xpath('//a[contains(@href,"fnView") and .//*[contains(@class,"item-subject")]]')
    elif source == '벡스코':
        nodes = root.xpath('//a[contains(@href,"event_seq=") and .//*[contains(@class,"subject")]]')
    elif source == '대전컨벤션센터':
        nodes = root.xpath('//li[@data-start and @data-end]/a')
    elif source == 'aT센터':
        nodes = root.xpath('//tr[.//a[contains(@href,"posterView.action?eventId=")]]')
    elif source == '수원메쎄':
        nodes = root.xpath('//a[contains(@class,"event-content") and contains(@href,"uid=")]')
    else:
        nodes = root.xpath('//a[starts-with(@onclick,"fn_view(")]')
    items = []
    for node in nodes:
        hall = ''
        if source == '킨텍스':
            title, dates, hall = _field(node, 'item-subject'), _dates(_field(node, 'item-date')), _field(node, 'item-client')
            match = re.search(r"fnView\(['\"]\./view.do['\"],\s*(\d+)\)", unquote(node.get('href', '')))
            link = urljoin(base, 'view.do?seq=' + match[1]) if match else ''
        elif source == '벡스코':
            title, dates, hall = _field(node, 'subject'), _dates(_field(node, 'date')), _field(node, 'place')
            seq = parse_qs(urlsplit(node.get('href', '')).query).get('event_seq', [''])[0]
            link = urljoin(base, 'view.do?mCode=MN214&event_seq=' + seq) if seq.isdigit() else ''
        elif source == '대전컨벤션센터':
            title = _field(node, 'subject')
            dates = _dates(node.getparent().get('data-start', '') + ' ' + node.getparent().get('data-end', ''))
            link = urljoin(base, node.get('href', ''))
        elif source == 'aT센터':
            anchor = node.xpath('.//a[contains(@href,"eventId=")]')[0]
            title, dates = _text(anchor), _dates(_text(node))
            cells = node.xpath('./td')
            hall = _text(cells[-1]) if len(cells) > 1 else ''
            link = urljoin(base, anchor.get('href', ''))
        elif source == '수원메쎄':
            title, dates = _field(node, 'event-name'), _dates(_field(node, 'event-date'))
            uid = parse_qs(urlsplit(node.get('href', '')).query).get('uid', [''])[0]
            link = urljoin(base, '?uid=' + uid + '&mod=document') if uid.isdigit() else ''
        else:
            strong = node.xpath('.//strong')
            title, dates = (_text(strong[0]) if strong else ''), _dates(_text(node))
            places = re.search(r'장소\s*:\s*(.+)', _text(node))
            hall = places[1] if places else ''
            match = re.search(r"fn_view\(['\"](\d+)['\"]\)", node.get('onclick', ''))
            link = urljoin(base, 'view.do?sIdx=' + match[1]) if match else ''
        if not title or len(dates) != 2 or dates[1] < dates[0] or not link:
            continue
        images = node.xpath('.//img/@src')
        image = node.get('data-image') or (images[0] if images else '')
        items.append(_item(title, dates[0], dates[1],
            venue=source + (' ' + hall if hall else ''), region=SOURCES[source][0],
            url=link, image=urljoin(base, image) if image else '', source=source))
    return items, root


def _page_count(source, root):
    text = ' '.join(root.xpath('//a/@href') + root.xpath('//a/@onclick'))
    patterns = {'킨텍스': r'fnPaging\([\'\"](\d+)', '벡스코': r'[?&]page=(\d+)',
                '대전컨벤션센터': r'cfnPageLink\((\d+)\)', '세텍': r'fn_egov_link_page\((\d+)\)'}
    if source == '세텍':
        # The live pager uses fn_page rather than a normal hyperlink.
        numbers = re.findall(r'(?:fn_link_page|fn_page|fn_egov_link_page|fn_paging)\([\'\"]?(\d+)', text)
        numbers += re.findall(r'pageIndex=(\d+)', text)
    else:
        numbers = re.findall(patterns.get(source, r'(?!)'), text)
    return min(12, max([1] + [int(n) for n in numbers]))


def _collect_one(source, today):
    _, url = SOURCES[source]
    fetch_url = url
    until = today + dt.timedelta(days=183)
    start = today.replace(day=1).isoformat()
    end = until.isoformat()
    params = {
        '킨텍스': {'searchStartDt': start, 'searchEndDt': end, 'pageUnit': 100},
        '벡스코': {'mCode': 'MN214', 'schStartDate': start, 'schEndDate': end},
        '대전컨벤션센터': {'menuKey': 154, 'searchStartDt': start, 'searchEndDt': end},
        'aT센터': {'datepicker1': start, 'datepicker2': end},
        '수원메쎄': {'kboard_option_start_date': start, 'kboard_option_end_date': end},
        '세텍': {'searchSDate': start, 'searchEDate': end},
    }[source]
    page_key = {'킨텍스': 'pageIndex', '벡스코': 'page', '대전컨벤션센터': 'currentPageNo', '세텍': 'pageIndex'}.get(source)
    items, seen, pages, page = [], set(), 1, 1
    state = {'configured': True, 'url': url, 'parsed': 0, 'pages': 0, 'checked_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        with requests.Session() as session:
            session.headers.update({'User-Agent': 'Mozilla/5.0'})
            while page <= pages:
                if page_key:
                    params[page_key] = page
                try:
                    if source == '수원메쎄':
                        # Its WordPress listing is large and an uncached date
                        # search can exceed 20s. Retry a transient timeout once.
                        try:
                            response = session.get(fetch_url, params=params, timeout=(8, 35))
                        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                            response = session.get(fetch_url, params=params, timeout=(10, 50))
                    else:
                        response = session.get(fetch_url, params=params, timeout=(8, 20))
                except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                    if source != '대전컨벤션센터':
                        raise
                    fetch_url = url.replace('https:', 'http:', 1)
                    response = session.get(fetch_url, params=params, timeout=(8, 20))
                # DCC also publishes this non-sensitive schedule over HTTP. Do not
                # disable TLS certificate validation for either domain.
                if source == '대전컨벤션센터' and response.status_code >= 400:
                    fetch_url = url.replace('https:', 'http:', 1)
                    response = session.get(fetch_url, params=params, timeout=(8, 20))
                response.raise_for_status()
                state['transport'] = urlsplit(fetch_url).scheme
                rows, root = parse(source, response.content, url)
                if not rows and page == 1:
                    # A valid empty list and a broken/error page are distinguishable.
                    text = _text(root)
                    if not any(k in text for k in ('등록된', '검색된', '검색 결과가 없습니다', '행사가 없습니다', '일정이 없습니다')):
                        raise ValueError('schedule_structure_changed')
                pages = max(pages, _page_count(source, root))
                state['pages'] += 1
                fresh = [row for row in rows if row['url'] not in seen]
                if not fresh:
                    break
                seen.update(row['url'] for row in fresh)
                items.extend(row for row in fresh if row['end_date'] >= today.isoformat() and row['start_date'] <= end)
                page += 1
        state.update(ok=True, parsed=len(items))
    except Exception as exc:
        state.update(ok=False, parsed=len(items), error=type(exc).__name__)
    _status[source] = state
    return items


def collect(progress=None):
    if os.getenv('VENUE_SOURCES_OFF') == '1':
        return []
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    items = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_collect_one, source, today): source for source in SOURCES}
        for future in as_completed(futures):
            source = futures[future]
            rows = future.result()
            items.extend(rows)
            if progress:
                progress(f"{source} 공식 일정 {len(rows)}건" + ('' if _status[source].get('ok') else ' · 응답 오류'))
    return items


def diagnose():
    return {source: dict(_status.get(source, {'configured': os.getenv('VENUE_SOURCES_OFF') != '1', 'url': url, 'checked': False}))
            for source, (_, url) in SOURCES.items()}
