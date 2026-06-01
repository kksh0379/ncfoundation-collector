"""중복 판단 로직.

- 뉴스(탭1): 본문 내용 유사성으로 판단. 여러 매체가 같은 보도자료를 그대로
  배포하므로, 본문을 정규화해 (1) 완전 동일 해시, (2) 문자 n-gram 자카드
  유사도를 함께 본다. 임계값 이상이면 기존 기사와 동일한 내용으로 간주한다.
- 게시판(탭2): 제목으로 판단. 정규화한 제목이 같으면 동일 글로 본다.
"""
import hashlib
import re

# 본문 유사도 임계값.
# 실측 기준: 같은 보도자료를 매체별로 변형한 기사 0.71~0.87,
#            같은 재단의 다른 사건 기사 0.03~0.05 로 분별이 뚜렷하다.
# 0.65로 두면 변형 기사는 중복으로 잡고 다른 내용과는 충분한 여유를 둔다.
SIMILARITY_THRESHOLD = 0.65
SHINGLE_SIZE = 3  # 문자 단위 n-gram 크기 (한국어 본문 비교에 적합)


def normalize_text(text):
    """공백/특수문자/대소문자를 정규화해 비교 안정성을 높인다."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"\s+", "", text)          # 모든 공백 제거
    text = re.sub(r"[^0-9a-z가-힣]", "", text)  # 한/영/숫자만 남김
    return text


def content_hash(text):
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def _shingles(text, size=SHINGLE_SIZE):
    norm = normalize_text(text)
    if len(norm) < size:
        return {norm} if norm else set()
    return {norm[i : i + size] for i in range(len(norm) - size + 1)}


def jaccard_similarity(a, b):
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def is_duplicate_news(new_content, existing_items, threshold=SIMILARITY_THRESHOLD):
    """기존 뉴스들과 비교해 중복이면 True.

    existing_items: db.all_news_fingerprints() 결과 (id, content_hash, content).
    """
    new_hash = content_hash(new_content)
    new_shingles = _shingles(new_content)
    if not new_shingles:
        return False  # 본문이 비면 판단 불가 → 일단 신규로 둠

    for item in existing_items:
        # 1) 완전 동일 본문은 해시로 빠르게 차단
        if item.get("content_hash") and item["content_hash"] == new_hash:
            return True
        # 2) 근접 중복은 자카드 유사도로 판단
        existing_shingles = _shingles(item.get("content") or "")
        if not existing_shingles:
            continue
        inter = len(new_shingles & existing_shingles)
        union = len(new_shingles | existing_shingles)
        if union and (inter / union) >= threshold:
            return True
    return False


def normalize_title(title):
    return normalize_text(title)


def is_duplicate_title(title, existing_titles):
    """게시판 글 제목 기반 중복 판단."""
    norm = normalize_title(title)
    return norm in {normalize_title(t) for t in existing_titles}
