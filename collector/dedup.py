"""중복 판단 로직.

- 뉴스(탭1): 본문 내용 유사성으로 판단. **글자 단위(char n-gram) TF-IDF + 코사인
  유사도**를 쓴다. 같은 보도자료를 여러 매체가 그대로 배포하면 글자 구성이 거의 같아
  유사도가 높게 나온다. (형태소분석기 없이 동작 — 메모리가 작은 무료 호스팅에서도 안전.
  형태소분석 기반이 필요하면 유료 인스턴스에서 단어 TF-IDF로 교체 가능)
- 게시판(탭2): 제목으로 판단. 정규화한 제목이 같으면 동일 글로 본다.
"""
import hashlib
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# 본문 코사인 유사도 임계값. (calibrate 기준: 재배포 기사 ~0.6+, 다른 사건 기사 ~0.1)
SIMILARITY_THRESHOLD = 0.55
NGRAM_RANGE = (2, 4)  # 글자 n-gram 범위


def normalize_text(text):
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^0-9a-z가-힣]", "", text)
    return text


def content_hash(text):
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def _tfidf_max_similarity(new_content, existing_contents):
    """신규 본문 vs 기존 본문들의 최대 코사인 유사도 (char n-gram TF-IDF)."""
    existing = [normalize_text(c) for c in existing_contents]
    existing = [c for c in existing if c]
    new_norm = normalize_text(new_content)
    if not existing or not new_norm:
        return 0.0

    docs = existing + [new_norm]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=NGRAM_RANGE, min_df=1)
    try:
        matrix = vectorizer.fit_transform(docs)
    except ValueError:
        return 0.0
    sims = cosine_similarity(matrix[-1], matrix[:-1])
    return float(sims.max()) if sims.size else 0.0


def is_duplicate_news(new_content, existing_items, threshold=SIMILARITY_THRESHOLD):
    """기존 뉴스들과 비교해 본문이 유사하면 True.

    existing_items: db.all_news_fingerprints() 결과 (id, content_hash, content).
    """
    if not new_content or not new_content.strip():
        return False

    new_hash = content_hash(new_content)
    for item in existing_items:
        if item.get("content_hash") and item["content_hash"] == new_hash:
            return True  # 완전 동일 본문

    existing_contents = [item.get("content") or "" for item in existing_items]
    return _tfidf_max_similarity(new_content, existing_contents) >= threshold


def dedup_news_items(items, existing_contents, threshold=SIMILARITY_THRESHOLD):
    """뉴스 묶음을 한 번의 벡터화로 효율적으로 중복 제거한다.

    기존 기사 + 신규 기사 전체를 한 번만 TF-IDF 벡터화하고, 신규 기사를 순서대로
    보며 (기존 + 이미 채택한 신규)와의 최대 유사도가 임계값 이상이면 버린다.
    items별로 매번 벡터라이저를 새로 학습하던 O(N^2) 비용을 없애 무료 CPU에서도
    빠르게 동작한다. 반환: (남길 items, 중복 수)
    """
    if not items:
        return [], 0
    existing_norm = [normalize_text(c or "") for c in existing_contents]
    item_norm = [normalize_text(it.get("content", "")) for it in items]
    corpus = existing_norm + item_norm
    if not any(corpus):
        return list(items), 0  # 비교할 텍스트가 없으면 전부 신규로

    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=NGRAM_RANGE, min_df=1)
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        return list(items), 0

    n_exist = len(existing_norm)
    kept_items, kept_rows, dup = [], list(range(n_exist)), 0
    for i, it in enumerate(items):
        row = n_exist + i
        if item_norm[i] and kept_rows:
            sims = cosine_similarity(matrix[row], matrix[kept_rows])
            if sims.size and sims.max() >= threshold:
                dup += 1
                continue
        kept_items.append(it)
        kept_rows.append(row)
    return kept_items, dup


def normalize_title(title):
    return normalize_text(title)


def is_duplicate_title(title, existing_titles):
    """게시판 글 제목 기반 중복 판단."""
    norm = normalize_title(title)
    return norm in {normalize_title(t) for t in existing_titles}
