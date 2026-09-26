"""AI 재단 동향 분석 리포트 엔진.

수집된 뉴스/유튜브(우리 재단=NC문화재단, 동종 재단=업계동향+재단YT)를
정리해 LLM에 넘기고, 기획서 원칙에 따라 '비교·변화·추세·신호·근거·검토질문'
중심의 인텔리전스 리포트(JSON)를 생성한다.

- 실제 분석(LLM)은 ANTHROPIC_API_KEY 환경변수가 있을 때만 동작한다.
- 모델은 ANALYSIS_MODEL(기본 claude-3-5-sonnet-latest)로 지정.
- 뉴스는 group_key로 중복 보도를 묶어 '미디어 노출량'과 '실제 활동'을 구분한다.
"""
import datetime
import json
import os
import re

from . import db, social

OUR = "NC문화재단"
OUR_ALIASES = ["엔씨문화재단", "NC문화재단", "ncfoundation"]
API_URL = "https://api.anthropic.com/v1/messages"
MODELS_URL = "https://api.anthropic.com/v1/models"


def list_models(key=None):
    """키로 사용 가능한 모델 id 목록을 조회. 실패 시 빈 리스트."""
    key = (key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    if not key:
        return []
    import requests
    try:
        r = requests.get(MODELS_URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                         timeout=20)
        if r.status_code >= 400:
            return []
        return [m.get("id") for m in r.json().get("data", []) if m.get("id")]
    except Exception:  # noqa: BLE001
        return []


def _pick_model(ids):
    """신뢰성 우선: haiku(빠르고 추론이 적어 잘 완결) > sonnet > opus, 같은 급이면 최신.
    (Claude 5 sonnet/opus는 추론에 출력·시간을 많이 써 타임아웃/잘림이 잦음)"""
    if not ids:
        return None

    def score(mid):
        tier = 3 if "haiku" in mid else (2 if "sonnet" in mid else (1 if "opus" in mid else 0))
        return (tier, mid)
    return sorted(ids, key=score, reverse=True)[0]


def resolve_model(key=None):
    """ANALYSIS_MODEL 지정 시 그걸, 아니면 계정에서 사용 가능한 모델 자동 선택."""
    env = os.environ.get("ANALYSIS_MODEL", "").strip()
    if env:
        return env
    return _pick_model(list_models(key)) or "claude-3-5-sonnet-latest"


def _d10(s):
    return (s or "")[:10]


def _org_hint(title, content):
    tc = f"{title} {content}"
    for name in social.MAJOR_FOUNDATIONS:
        if name in tc:
            return name
    return "기타"


def _norm_news(rows, ours):
    """뉴스 rows → 정규화 + group_key 대표만(중복 보도 묶기). 반환 (items, exposure)."""
    groups = {}
    for r in rows:
        k = r.get("group_key") or r.get("url")
        groups.setdefault(k, []).append(r)
    items = []
    for k, arr in groups.items():
        arr.sort(key=lambda x: x.get("published_at") or "", reverse=True)
        rep = arr[0]
        title = rep.get("title") or ""
        content = rep.get("content") or ""
        items.append({
            "org": OUR if ours else _org_hint(title, content),
            "type": "news",
            "title": title,
            "date": _d10(rep.get("published_at")),
            "snippet": content[:180],
            "url": rep.get("source_url") or rep.get("url"),
            "exposure": len(arr),  # 미디어 노출량(같은 사건 보도 수)
        })
    return items


def _norm_social(rows, ours):
    out = []
    for r in rows:
        acc = r.get("account") or ""
        is_ours = acc == OUR
        if ours != is_ours:
            continue
        out.append({
            "org": acc or OUR,
            "type": "youtube",
            "title": r.get("title") or "",
            "date": _d10(r.get("published_at")),
            "snippet": (r.get("content") or "")[:180],
            "url": r.get("url"),
            "exposure": 1,
        })
    return out


def _quarter(dstr):
    try:
        y, m = int(dstr[:4]), int(dstr[5:7])
        return f"{y}-Q{(m - 1) // 3 + 1}"
    except Exception:  # noqa: BLE001
        return "unknown"


def build_input(window_days=90):
    """분석 입력 구성. 반환: dict(recent, baseline_summary, counts)."""
    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=window_days)).isoformat()

    our = _norm_news(db.list_news(section="nc", limit=6000, category="재단"), True) \
        + _norm_social(db.list_social(limit=4000), True)
    peers = _norm_news(db.list_news(section="biz", limit=8000), False) \
        + _norm_social(db.list_social(limit=4000), False)

    def split(items):
        recent = [x for x in items if x["date"] and x["date"] >= cutoff]
        recent.sort(key=lambda x: x["date"], reverse=True)
        return recent

    recent_our = split(our)
    recent_peers = split(peers)

    # 베이스라인(5년) 분기별 활동 수 집계 — 기관/구분별로 압축
    def quarter_counts(items):
        q = {}
        for x in items:
            q[_quarter(x["date"])] = q.get(_quarter(x["date"]), 0) + 1
        return dict(sorted(q.items()))

    peer_org_counts = {}
    for x in peers:
        peer_org_counts[x["org"]] = peer_org_counts.get(x["org"], 0) + 1
    # 동종 재단 수: 이름이 식별된 고유 기관 수('기타'=미상 제외)
    peer_orgs = len([k for k in peer_org_counts if k and k != "기타"])

    def trim(items):
        return [{k: (v[:110] if k == "snippet" and isinstance(v, str) else v)
                 for k, v in x.items()} for x in items]

    return {
        "as_of": today.isoformat(),
        "window_days": window_days,
        "recent_our": trim(recent_our[:50]),
        "recent_peers": trim(recent_peers[:90]),
        "baseline": {
            "our_quarters": quarter_counts(our),
            "peer_quarters": quarter_counts(peers),
            "peer_org_totals": dict(sorted(peer_org_counts.items(), key=lambda kv: -kv[1])[:12]),
            "our_total": len(our), "peer_total": len(peers), "peer_orgs": peer_orgs,
        },
    }


SYSTEM_PROMPT = """당신은 비영리 재단 업계를 관찰하는 외부 애널리스트다. 수집된 뉴스/유튜브 데이터로
NC문화재단과 동종 재단들의 활동을 분석해 인텔리전스 리포트를 만든다.

[시점] 반드시 제3자(외부 관찰자) 시점으로 쓴다. '우리 재단', '우리' 같은 1인칭 표현을 쓰지 말고
항상 'NC문화재단'으로 지칭한다. 특정 재단을 편들지 않고 중립적으로 서술한다.

핵심 원칙(반드시 지킬 것):
- 단순 요약/키워드 빈도/기사 건수 순위 같은 나이브한 분석을 핵심 인사이트로 쓰지 않는다.
- 같은 사업을 여러 매체가 보도한 것은 하나의 활동으로 본다(미디어 노출량 ≠ 활동 수).
- 데이터로 확인되지 않는 사실을 추정해 단정하지 않는다. 근거가 약하면 신중하게 표현한다.
- 요약보다 '비교·변화·추세·이상징후·신규신호·검토질문'을 우선한다.
- 모든 주요 결론에는 근거가 된 콘텐츠(evidence: 실제 입력에 존재하는 title/url)를 붙인다.
- 우열 평가('잘한다/뒤처진다') 대신 '활동이 상대적으로 많다/최근 증가한다/현재 데이터에선 확인되지 않는다'처럼 근거 중심으로 쓴다.
- 검토 과제는 결정을 대신하지 말고, 의사결정에 필요한 '질문'을 제시한다.

출력은 아래 JSON 스키마 '하나만' 출력한다(설명/코드펜스 없이 순수 JSON):
{
 "period_label": "예: 최근 3개월(2026-07~09)",
 "summary": "이번 기간 전반을 제3자 관점으로 3~4문장 요약(핵심 변화·업계 흐름·NC문화재단의 위치 중심). 리포트 맨 앞에 놓일 개괄.",
 "brief": {"foundations": 정수, "recent_contents": 정수, "activities": 정수, "new_activities": 정수,
           "highlights": ["이번 기간 가장 중요한 변화 3~5개(문장)"]},
 "changes_since_last": [{"title":"", "status":"NEW|UP|DOWN|CONTINUED|DISAPPEARED", "detail":"", "evidence":[{"title":"","org":"","type":"","date":"","url":""}]}],
 "industry_trends": [{"area":"", "direction":"증가|감소|확대|전환 등", "detail":"변화의 방향을 서술", "evidence":[...]}],
 "foundation_moves": [{"org":"", "moves":[{"kind":"신규사업|변화|협력|발표 등","detail":"","evidence":[...]}]}],
 "trends": [{"topic":"", "state":"장기상승|장기하락|최근상승|최근하락|일시급증|반복성|신규등장", "detail":"1·3·6개월/1·3·5년 관점 비교", "evidence":[...]}],
 "emerging_signals": [{"name":"", "desc":"", "recent_change":"", "foundations":["..."], "basis":"왜 신호로 판단했는지 근거", "evidence":[...]}],
 "our_position": {"strong":["NC문화재단이 상대적으로 활발한 영역"], "similar":["업계와 유사한 영역"], "less":["업계선 증가하나 NC문화재단에선 적게 확인되는 영역"], "unique":["NC문화재단에는 있으나 타 재단엔 드문 영역"], "recent_change":"NC문화재단의 최근 방향 변화(제3자 서술)", "evidence":[...]},
 "benchmarks": [{"org":"", "name":"활동/사업명", "summary":"", "distinct":"기존과 다른 점", "question":"NC문화재단이 검토할 만한 질문(제3자 제안)", "evidence":[...]}],
 "review_tasks": [{"background":"", "change":"발견된 변화", "basis":"근거 데이터", "foundations":["..."], "question":"NC문화재단이 검토할 만한 질문(제3자 제안, 결정 강요 금지)"}],
 "confidence_note": "근거가 부족한 항목에 대한 주의 문구(있으면)"
}
evidence의 url/title은 반드시 입력 데이터에 실제 존재하는 것만 사용한다. 한국어로 작성한다.

[분량 제한 — 반드시 지킬 것]
- 각 배열(changes/trends/signals/benchmarks/review_tasks 등)은 가장 중요한 것 위주로 최대 5개.
- 각 항목의 문장은 1~2문장으로 간결하게. highlights는 최대 4개.
- evidence는 항목당 최대 2개.
- 사고 과정·설명·코드펜스 없이, 완결된 JSON 객체 하나만 출력한다(반드시 끝까지 닫을 것)."""


def _post_messages(key, model, user, no_think=True):
    import requests
    max_tokens = int(os.environ.get("ANALYSIS_MAX_TOKENS", "16000"))
    body = {"model": model, "max_tokens": max_tokens, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}]}
    if no_think:
        # 내부 추론(thinking)을 꺼서 출력 예산을 JSON에만 쓰게 한다(잘림 방지 + 비용 절감).
        body["thinking"] = {"type": "disabled"}
    return requests.post(API_URL, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }, data=json.dumps(body), timeout=240)


def _text_from_response(j):
    """Messages 응답에서 텍스트 블록만 모아 반환(thinking 등 비텍스트 블록은 건너뜀)."""
    parts = []
    for b in (j.get("content") or []):
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            parts.append(b["text"])
    if not parts:  # 폴백: type 없이 text만 있는 경우
        for b in (j.get("content") or []):
            if isinstance(b, dict) and b.get("text"):
                parts.append(b["text"])
    return "".join(parts)


def _pick_haiku(key):
    for mid in list_models(key):
        if "haiku" in mid:
            return mid
    return None


def _attempt(key, model, user):
    """1회 호출 결과를 dict로: {data, stop, text, http, err}."""
    try:
        r = _post_messages(key, model, user, no_think=True)
    except Exception as e:  # noqa: BLE001
        return {"err": f"LLM 요청 실패: {e}"}
    # thinking 파라미터를 모델이 거부(400)하면 파라미터 없이 재시도
    if r.status_code == 400 and "thinking" in (r.text or ""):
        try:
            r = _post_messages(key, model, user, no_think=False)
        except Exception as e:  # noqa: BLE001
            return {"err": f"LLM 요청 실패: {e}"}
    if r.status_code == 404 and "not_found" in (r.text or ""):
        return {"http": 404, "not_found": True}
    if r.status_code >= 400:
        return {"http": r.status_code, "err": f"LLM 오류 {r.status_code}: {r.text[:200]}"}
    try:
        j = r.json()
    except Exception as e:  # noqa: BLE001
        return {"err": f"LLM 응답 파싱 실패: {e}"}
    txt = _text_from_response(j)
    return {"data": _extract_json(txt), "stop": j.get("stop_reason"), "text": txt}


# 리포트를 두 번에 나눠 생성(각 절반) → 출력이 잘리지 않게. 어떤 모델·추론 상태에서도 완결.
_PART1 = ["period_label", "summary", "brief", "changes_since_last", "industry_trends", "trends"]
_PART2 = ["emerging_signals", "foundation_moves", "our_position", "benchmarks", "review_tasks", "confidence_note"]


def _call_llm(payload_text, prev_text):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None, None, "ANTHROPIC_API_KEY 환경변수가 없습니다(관리자가 Render에 설정 필요)."
    base = "[분석 입력 데이터]\n" + payload_text
    if prev_text:
        base += "\n\n[직전 리포트 요약(변화 비교용)]\n" + prev_text
    base += "\n\nrecent_our/recent_peers가 이번 기간, baseline이 과거 흐름이다."

    model = resolve_model(key)
    merged, errs, used = {}, [], model
    for idx, fields in enumerate((_PART1, _PART2), 1):
        u = base + ("\n\n[이번 출력 범위] 아래 키만 포함한 '완결된' JSON 객체 하나만 출력하라"
                    "(나머지 키·설명·코드펜스 없이): " + ", ".join(fields))
        res = _attempt(key, used, u)
        if res.get("not_found"):
            alt = _pick_model(list_models(key))
            if alt and alt != used:
                used = alt
                res = _attempt(key, used, u)
        if res.get("err"):
            avail = ", ".join(list_models(key)[:8]) or "(목록 조회 실패)"
            errs.append(f"파트{idx} {res['err']} · 가용 모델 예: {avail}")
            continue
        if isinstance(res.get("data"), dict):
            merged.update(res["data"])
        elif res.get("stop") == "max_tokens":
            errs.append(f"파트{idx} 출력 잘림")
        else:
            errs.append(f"파트{idx} JSON 없음")
    if not merged:
        return None, None, ("리포트 생성 실패 — " + " / ".join(errs) if errs else "빈 응답")
    return merged, used, None


def _extract_json(txt):
    txt = txt.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", txt).strip()
    try:
        return json.loads(txt)
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\{.*\}", txt, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None
    return None


def run(window_days=90, progress=None):
    """분석 1회 실행. 반환 (data, error). data엔 meta(입력 규모) 포함."""
    progress = progress or (lambda m: None)
    progress("데이터 정리 중…")
    inp = build_input(window_days)
    counts = {
        "recent_our": len(inp["recent_our"]),
        "recent_peers": len(inp["recent_peers"]),
        "our_total": inp["baseline"]["our_total"],
        "peer_total": inp["baseline"]["peer_total"],
        "peer_orgs": inp["baseline"].get("peer_orgs", 0),
    }
    if counts["recent_our"] + counts["recent_peers"] == 0:
        return None, "최근 기간에 분석할 콘텐츠가 없습니다. 먼저 뉴스/재단YT를 수집해 주세요."
    # 분석 키 = 기간(window)+분석일자. 같은 키면 저장 시 교체된다. 비교('지난 리포트')는
    # 같은 키(같은 날 재실행)를 건너뛰고 서로 다른 시점의 최신 스냅샷과 한다.
    pkey = f"{window_days}d:{inp['as_of']}"
    prev = db.latest_report_snapshot_excluding(pkey)
    prev_text = ""
    if prev:
        try:
            pj = json.loads(prev["data"] or "{}")
            prev_text = json.dumps(pj.get("brief", {}), ensure_ascii=False)[:1500]
        except Exception:  # noqa: BLE001
            prev_text = ""
    progress("AI 분석 중…(수십 초 소요)")
    payload_text = json.dumps(inp, ensure_ascii=False)
    data, used_model, err = _call_llm(payload_text, prev_text)
    if err:
        return None, err
    data["_meta"] = {"counts": counts, "window_days": window_days,
                     "model": used_model or resolve_model(), "as_of": inp["as_of"], "pkey": pkey}
    return data, None
