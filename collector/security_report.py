"""월간 보안 리포트 — 정보보안·개인정보보호 담당자용.

지난달(기본) 국내 보안뉴스(news.section='sec')를 종합해 담당자 관점의 월간 보안
브리핑을 생성한다. 기사별 AI 태깅(security_ai)과 독립적으로 자체 LLM 종합을 수행한다.
LLM 연결부(모델 자동선택·응답 파싱)는 collector.analysis의 것을 재사용.
ANTHROPIC_API_KEY 가 있을 때만 동작한다.
"""
import datetime
import json
import os
from collections import Counter

from . import analysis, db, security_ai

MAX_TOKENS = int(os.environ.get("SECREPORT_MAX_TOKENS", "10000"))
ARTICLES_MAX = int(os.environ.get("SECREPORT_ARTICLES", "45"))
_IMP_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def prev_month(today=None):
    """지난달 'YYYY-MM' (월초에 지난달 리포트를 만드는 기본 대상)."""
    today = today or datetime.date.today()
    first = today.replace(day=1)
    last_prev = first - datetime.timedelta(days=1)
    return f"{last_prev.year:04d}-{last_prev.month:02d}"


def month_bounds(ym):
    """'YYYY-MM' → (start_iso, end_iso) 반열림 구간 [start, end)."""
    y, m = int(ym[:4]), int(ym[5:7])
    start = datetime.date(y, m, 1)
    end = datetime.date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
    return start.isoformat(), end.isoformat()


def _collect(ym):
    start, end = month_bounds(ym)
    rows = db.list_news(section="sec", category="all", limit=8000)
    return [r for r in rows
            if start <= (r.get("published_at") or "")[:10] < end]


def _stats(items):
    cat = Counter((r.get("category") or "기타") for r in items)
    imp = Counter((r.get("ai_importance") or "").upper() for r in items if r.get("ai_importance"))
    cve = sum(1 for r in items
              if "CVE" in (r.get("ai_insight") or "") or "CVE" in (r.get("title") or ""))
    return {
        "total": len(items),
        "by_category": dict(cat),
        "by_importance": {k: imp.get(k, 0) for k in security_ai.IMPORTANCE},
        "cve_mentions": cve,
        "analyzed": sum(1 for r in items if r.get("ai_at")),
    }


def build_input(ym):
    items = _collect(ym)
    stats = _stats(items)
    # 우선순위: (1) 중요도 높은 순 (2) 최신 순
    items.sort(key=lambda r: (r.get("published_at") or ""), reverse=True)
    items.sort(key=lambda r: _IMP_ORDER.get((r.get("ai_importance") or "").upper(), 4))
    condensed = [{
        "i": k,
        "title": r.get("title") or "",
        "cat": r.get("category") or "",
        "tags": r.get("ai_tags") or "",
        "imp": (r.get("ai_importance") or ""),
        "date": (r.get("published_at") or "")[:10],
        "snippet": (r.get("content") or "")[:140],
        "url": r.get("source_url") or r.get("url") or "",
    } for k, r in enumerate(items[:ARTICLES_MAX])]
    return {"month": ym, "stats": stats, "articles": condensed}


SYSTEM_PROMPT = (
    "너는 국내 정보보안·개인정보보호 동향 분석가다. 아래 '지난달 국내 보안뉴스 목록'을 종합해 "
    "기업의 정보보안/개인정보보호 담당자를 위한 '월간 보안 브리핑'을 제3자 관점(객관)으로 작성한다. "
    "홍보·과장 없이 사실 기반으로, 담당자가 바로 활용할 수 있게 실무적으로 쓴다.\n"
    "아래 JSON 객체 하나만 출력한다(설명·코드펜스 없이, 반드시 끝까지 닫을 것). 한국어:\n"
    '{"summary":"이번 달 국내 보안·개인정보 동향 개요 3~4문장",'
    '"highlights":["핵심 요점 3~5개"],'
    '"top_incidents":[{"title":"사고/이슈","importance":"CRITICAL|HIGH|MEDIUM|LOW",'
    '"category":"분류","impact":"영향/담당자 시사점 1문장","url":"원문"}],'
    '"vulnerabilities":[{"name":"취약점/제품","cve":"CVE 번호(있으면, 없으면 빈값)","note":"조치 권고 1문장"}],'
    '"regulatory":[{"title":"법/제도/처분","note":"담당자 관점 요점 1문장"}],'
    '"trends":[{"topic":"보안 트렌드","note":"1문장"}],'
    '"actions":[{"task":"이번 달 점검/대응 권고","detail":"구체 실행 1문장"}],'
    '"confidence_note":"데이터 한계 안내 1문장"}\n'
    "top_incidents는 최대 6개, 나머지 배열은 각각 최대 5개. 근거 없는 내용은 넣지 않는다."
)


def _payload_text(inp):
    s = inp["stats"]
    lines = [
        f"[대상 월] {inp['month']}",
        f"[집계] 총 {s['total']}건 · 분류별 {json.dumps(s['by_category'], ensure_ascii=False)} · "
        f"중요도별 {json.dumps(s['by_importance'], ensure_ascii=False)} · CVE 언급 {s['cve_mentions']}건",
        "[기사 목록] (중요도·최신 우선)",
    ]
    for a in inp["articles"]:
        lines.append(
            f"{a['i']}. [{a['imp'] or '-'}|{a['cat']}] {a['title']} "
            f"({a['date']}) tags:{a['tags']}\n   {a['snippet']}\n   {a['url']}"
        )
    return "\n".join(lines)


def _post(key, model, user):
    import requests
    body = {"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}], "thinking": {"type": "disabled"}}
    return requests.post(analysis.API_URL, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, data=json.dumps(body), timeout=200)


def _call_llm(key, model, inp):
    user = "[지난달 보안뉴스 분석 입력]\n" + _payload_text(inp)
    used = model
    for attempt in range(2):
        try:
            r = _post(key, used, user)
        except Exception as e:  # noqa: BLE001
            return None, None, f"LLM 요청 실패: {e}"
        if r.status_code == 400 and "thinking" in (r.text or ""):
            # thinking 미지원 모델: 파라미터 없이 재시도
            try:
                import requests as _rq
                r = _rq.post(analysis.API_URL, headers={
                    "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
                }, data=json.dumps({"model": used, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
                                    "messages": [{"role": "user", "content": user}]}), timeout=200)
            except Exception as e:  # noqa: BLE001
                return None, None, f"LLM 요청 실패: {e}"
        if r.status_code == 404 and attempt == 0:
            alt = analysis._pick_model(analysis.list_models(key))
            if alt and alt != used:
                used = alt
                continue
        if r.status_code >= 400:
            avail = ", ".join(analysis.list_models(key)[:6]) or "(모델 조회 실패)"
            return None, None, f"LLM 오류 {r.status_code}: {r.text[:160]} · 가용 모델 예: {avail}"
        try:
            j = r.json()
        except Exception as e:  # noqa: BLE001
            return None, None, f"응답 파싱 실패: {e}"
        txt = analysis._text_from_response(j)
        data = analysis._extract_json(txt)
        if not isinstance(data, dict):
            if j.get("stop_reason") == "max_tokens":
                return None, None, "출력이 잘렸어요(max_tokens). SECREPORT_MAX_TOKENS를 올려 주세요."
            return None, None, "리포트 JSON을 해석하지 못했어요."
        return data, used, None
    return None, None, "리포트 생성 실패"


def run(ym=None, progress=None):
    """월간 보안 리포트 1회 생성. 반환 (data, error). ym 미지정 시 지난달."""
    progress = progress or (lambda m: None)
    ym = ym or prev_month()
    progress("보안뉴스 정리 중…")
    inp = build_input(ym)
    if inp["stats"]["total"] == 0:
        return None, f"{ym} 기간에 분석할 보안뉴스가 없습니다. 먼저 보안뉴스를 수집해 주세요."
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None, "ANTHROPIC_API_KEY 환경변수가 없습니다(관리자가 Render에 설정 필요)."
    model = analysis.resolve_model(key)
    progress(f"{ym} 월간 보안 리포트 생성 중…(수십 초)")
    data, used, err = _call_llm(key, model, inp)
    if err:
        return None, err
    data["_meta"] = {"month": ym, "counts": inp["stats"],
                     "model": used or model, "as_of": datetime.date.today().isoformat()}
    return data, None
