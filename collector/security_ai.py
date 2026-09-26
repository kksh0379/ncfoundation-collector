"""보안뉴스 AI 후처리: 자동 태깅 + 중요도 평가 + 보안담당자 시사점 요약.

수집(구글 뉴스 RSS + 키워드/제외 필터)으로 모인 보안뉴스에 대해, Claude API로
아래를 '기사 여러 건을 한 번에' 배치 분석한다(비용·속도 최적화):
  - tags       : 자동 분류(복수 허용, 아래 CATEGORY enum)
  - importance : CRITICAL / HIGH / MEDIUM / LOW
  - keep       : 보안 실무 참고가치 최종 판단(명세 10번)
  - insight    : {summary(핵심요약), implication(보안담당자 시사점),
                  check(조직 확인사항), prevention(유사사고 예방)}

LLM 연결부(모델 자동선택·응답 파싱·JSON 추출)는 collector.analysis의 것을 재사용.
ANTHROPIC_API_KEY 가 있을 때만 동작한다(없으면 호출측이 '키 없음' 메시지).
"""
import json
import os

from . import analysis  # list_models / resolve_model / _pick_model / _text_from_response / _extract_json / API_URL

# 명세 6번 CATEGORY enum(복수 태그 허용)
TAGS = [
    "개인정보", "개인정보유출", "해킹", "랜섬웨어", "악성코드", "피싱/스미싱",
    "취약점", "제로데이", "계정탈취", "데이터유출", "공급망공격", "클라우드보안",
    "AI보안", "보안정책", "법령/규제", "과징금/제재", "보안기술", "보안트렌드",
]
IMPORTANCE = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

BATCH = int(os.environ.get("SEC_AI_BATCH", "10"))            # 1회 호출당 기사 수
MAX_TOKENS = int(os.environ.get("SEC_AI_MAX_TOKENS", "8000"))
SNIPPET = 220  # 기사당 본문 요약 길이 제한(토큰 절약)

SYSTEM_PROMPT = (
    "너는 국내 개인정보보호·정보보안 뉴스 분석가다. 아래 기사 목록 각각에 대해 "
    "'제3자 관점'으로 객관적으로 태깅·중요도·시사점을 만든다. 홍보·과장 없이 사실 기반으로.\n"
    "각 기사에 대해 다음을 판단한다:\n"
    f"- tags: 다음 중 1~3개(복수 허용): {', '.join(TAGS)}\n"
    "- importance: CRITICAL(대규모 개인정보 유출·국내 주요기관 해킹·실제 악용 제로데이·대규모 랜섬웨어), "
    "HIGH(개인정보위 주요 처분·중요 취약점·주요 정책/법령 변경·새로운 공격기법), "
    "MEDIUM(보안 트렌드·연구보고서·통계·새 보안기술), LOW(일반 참고성 정보) 중 하나.\n"
    "- keep: 보안 실무자가 참고할 가치가 있으면 true, 단순 제품홍보·광고·시세·루머면 false.\n"
    "- insight.summary: 사건/내용 핵심요약 1~2문장.\n"
    "- insight.implication: 기업 보안담당자 관점의 시사점 1문장.\n"
    "- insight.check: 조직에서 즉시 확인할 사항 1개(짧게).\n"
    "- insight.prevention: 유사사고 예방/대응 조치 1개(짧게).\n\n"
    "출력은 '완결된 JSON 배열' 하나만. 각 원소는 "
    '{"i": <입력번호>, "tags": [..], "importance": "..", "keep": true/false, '
    '"insight": {"summary": "..", "implication": "..", "check": "..", "prevention": ".."}} '
    "형태. 설명·코드펜스 없이 배열만, 반드시 끝까지 닫을 것. 한국어로."
)


def _post(key, model, user):
    import requests
    body = {"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "disabled"}}
    return requests.post(analysis.API_URL, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, data=json.dumps(body), timeout=180)


def _batch_prompt(items):
    lines = ["[분석 대상 기사 목록] 각 번호(i)별로 판단하라.", ""]
    for it in items:
        lines.append(
            f"{it['i']}. 제목: {it.get('title', '')}\n"
            f"   분류후보: {it.get('category', '')} · 날짜: {(it.get('published_at') or '')[:10]}\n"
            f"   요약: {(it.get('content') or '')[:SNIPPET]}"
        )
    return "\n".join(lines)


def _call_batch(key, model, items):
    """한 배치 호출 → {i: {tags, importance, keep, insight}}. 실패 시 (None, err)."""
    user = _batch_prompt(items)
    try:
        r = _post(key, model, user)
    except Exception as e:  # noqa: BLE001
        return None, f"요청 실패: {e}"
    if r.status_code == 400 and "thinking" in (r.text or ""):
        # thinking 미지원 모델: 파라미터 없이 재시도
        try:
            import requests  # noqa: F401
            body = {"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user}]}
            import requests as _rq
            r = _rq.post(analysis.API_URL, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            }, data=json.dumps(body), timeout=180)
        except Exception as e:  # noqa: BLE001
            return None, f"요청 실패: {e}"
    if r.status_code >= 400:
        return None, f"LLM 오류 {r.status_code}: {r.text[:160]}"
    try:
        j = r.json()
    except Exception as e:  # noqa: BLE001
        return None, f"응답 파싱 실패: {e}"
    txt = analysis._text_from_response(j)
    arr = _extract_array(txt)
    if not isinstance(arr, list):
        return None, "JSON 배열 없음"
    out = {}
    for el in arr:
        if isinstance(el, dict) and "i" in el:
            try:
                out[int(el["i"])] = el
            except (TypeError, ValueError):
                pass
    return out, None


def _extract_array(txt):
    txt = (txt or "").strip()
    if txt.startswith("```"):
        import re
        txt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", txt).strip()
    try:
        return json.loads(txt)
    except Exception:  # noqa: BLE001
        pass
    import re
    m = re.search(r"\[.*\]", txt, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None
    return None


def _norm(el):
    tags = [t for t in (el.get("tags") or []) if t in TAGS][:3]
    imp = (el.get("importance") or "").upper()
    if imp not in IMPORTANCE:
        imp = "MEDIUM"
    ins = el.get("insight") or {}
    insight = {k: (ins.get(k) or "") for k in ("summary", "implication", "check", "prevention")}
    insight["keep"] = bool(el.get("keep", True))
    return {"tags": tags, "importance": imp, "insight": insight}


def analyze(rows, progress=None):
    """rows(security_needs_ai 결과) → {url: {tags, importance, insight}}. (분석 못 한 건은 생략)"""
    progress = progress or (lambda m: None)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return {}, "ANTHROPIC_API_KEY 환경변수가 없습니다(관리자가 Render에 설정 필요)."
    rows = [r for r in rows if r.get("url")]
    if not rows:
        return {}, None
    model = analysis.resolve_model(key)
    out, errs, done = {}, [], 0
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        items = [{"i": start + k, **chunk[k]} for k in range(len(chunk))]
        res, err = _call_batch(key, model, items)
        if err and res is None:
            # 모델 문제면 한 번 대체 모델로 재시도
            alt = analysis._pick_model(analysis.list_models(key))
            if alt and alt != model:
                model = alt
                res, err = _call_batch(key, model, items)
        if res is None:
            errs.append(err or "배치 실패")
        else:
            for k in range(len(chunk)):
                el = res.get(start + k)
                if isinstance(el, dict):
                    out[chunk[k]["url"]] = _norm(el)
        done += len(chunk)
        progress(f"AI 분석 {done}/{len(rows)} · 완료 {len(out)}건")
    err = None
    if not out and errs:
        avail = ", ".join(analysis.list_models(key)[:6]) or "(모델 조회 실패)"
        err = "AI 분석 실패 — " + " / ".join(errs[:3]) + f" · 가용 모델 예: {avail}"
    return out, err
