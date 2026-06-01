"""중복 판단 로직.

- 뉴스(탭1): 본문 내용 유사성으로 판단. 여러 매체가 같은 보도자료를 그대로
  배포하므로, **단어 TF-IDF + 코사인 유사도**로 본문을 비교한다.
    1. Kiwi 형태소 분석으로 본문을 의미 형태소(명사/동사/형용사/외국어/숫자)로
       토큰화한다. 동사·형용사는 원형으로 환원되어("밝혔다"→"밝히") 어미 변형에
       강하다.
    2. 기존 기사 + 신규 기사를 코퍼스로 TF-IDF 벡터화한다(IDF로 흔한 단어 약화).
    3. 신규 기사와 기존 기사 간 코사인 유사도가 임계값 이상이면 동일 내용으로 본다.
- 게시판(탭2): 제목으로 판단. 정규화한 제목이 같으면 동일 글로 본다.
"""
import hashlib
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# 본문 코사인 유사도 임계값. 같은 보도자료를 옮긴 기사면 단어 구성이 거의 같아
# 0.8 이상으로 높게 나오고, 다른 사건 기사는 크게 낮아진다. (calibrate_threshold 참고)
SIMILARITY_THRESHOLD = 0.75

# TF-IDF 토큰으로 사용할 형태소 품사 (Kiwi 태그 기준)
#   N* 명사, VV 동사, VA 형용사, MAG 일반부사, SL 외국어, SH 한자, SN 숫자
_KEEP_TAGS = ("NNG", "NNP", "NNB", "NR", "NP", "VV", "VA", "MAG", "SL", "SH", "SN")

_kiwi = None


def _get_kiwi():
    """Kiwi 인스턴스를 지연 생성한다(첫 호출 시 1회 로드)."""
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi

        _kiwi = Kiwi()
    return _kiwi


# 같은 본문을 반복 형태소 분석하지 않도록 토큰화 결과를 캐싱한다
# (배치 크롤링 시 기존 기사들을 매 신규 기사마다 다시 분석하는 비용 제거).
_token_cache = {}


def tokenize_str(text):
    """tokenize 결과를 공백으로 이어 붙인 문자열(캐싱됨)."""
    key = content_hash(text)
    cached = _token_cache.get(key)
    if cached is None:
        cached = " ".join(tokenize(text))
        _token_cache[key] = cached
    return cached


def tokenize(text):
    """본문을 의미 형태소 리스트로 변환. 동사/형용사는 원형으로 환원된다."""
    if not text:
        return []
    tokens = []
    for tok in _get_kiwi().tokenize(text):
        if tok.tag not in _KEEP_TAGS:
            continue
        # 한 글자 토큰은 노이즈가 많아 제외하되, 외국어/한자/숫자는 살린다(AI, 21 등)
        if len(tok.form) > 1 or tok.tag in ("SL", "SH", "SN"):
            tokens.append(tok.form.lower())
    return tokens


# ----------------------------- 정규화/해시 -----------------------------
def normalize_text(text):
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^0-9a-z가-힣]", "", text)
    return text


def content_hash(text):
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


# ----------------------------- 뉴스 본문 유사도 -----------------------------
def _tfidf_max_similarity(new_content, existing_contents):
    """신규 본문 vs 기존 본문들의 최대 코사인 유사도."""
    existing_contents = [c for c in existing_contents if c and c.strip()]
    if not existing_contents or not (new_content and new_content.strip()):
        return 0.0

    corpus = existing_contents + [new_content]
    # 형태소 토큰화(캐싱) 결과를 공백으로 이어 TfidfVectorizer에 넘긴다.
    docs = [tokenize_str(doc) for doc in corpus]
    if not any(docs) or not docs[-1].strip():
        return 0.0

    vectorizer = TfidfVectorizer(token_pattern=r"(?u)\S+")
    try:
        matrix = vectorizer.fit_transform(docs)
    except ValueError:
        # 어휘가 비어있는 경우 등
        return 0.0

    new_vec = matrix[-1]
    existing_matrix = matrix[:-1]
    sims = cosine_similarity(new_vec, existing_matrix)
    return float(sims.max()) if sims.size else 0.0


def is_duplicate_news(new_content, existing_items, threshold=SIMILARITY_THRESHOLD):
    """기존 뉴스들과 비교해 본문이 유사하면 True.

    existing_items: db.all_news_fingerprints() 결과 (id, content_hash, content).
    """
    if not new_content or not new_content.strip():
        return False  # 본문이 비면 판단 불가 → 신규로 둠

    # 1) 완전 동일 본문은 해시로 빠르게 차단
    new_hash = content_hash(new_content)
    for item in existing_items:
        if item.get("content_hash") and item["content_hash"] == new_hash:
            return True

    # 2) 근접 중복은 TF-IDF 코사인 유사도로 판단
    existing_contents = [item.get("content") or "" for item in existing_items]
    return _tfidf_max_similarity(new_content, existing_contents) >= threshold


# ----------------------------- 게시판 제목 -----------------------------
def normalize_title(title):
    return normalize_text(title)


def is_duplicate_title(title, existing_titles):
    """게시판 글 제목 기반 중복 판단."""
    norm = normalize_title(title)
    return norm in {normalize_title(t) for t in existing_titles}
