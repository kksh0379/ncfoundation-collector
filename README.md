# ncfoundation-collector

엔씨문화재단 관련 **뉴스/기사**와 **재단 서비스 게시판 글**을 수집하는 모바일 웹 (초안).

## 기능

탭 2개로 구성된 모바일 웹입니다.

### 탭1 · 뉴스 · 기사
- 구글 뉴스 RSS 검색 피드에서 `엔씨문화재단` / `NC문화재단` 키워드 기사 수집
- 2026-01-01 이후 기사만
- 본사(엔씨소프트) 단독 기사 제외 (재단 키워드 포함 기사만)
- 광고/댓글/배너 제외 (본문 영역만 추출)
- 수집 항목: 제목, 작성일, 작성자(언론사), 본문, 원문 URL
  - ⚠️ 구글 뉴스 링크는 리다이렉트 URL이라 원문 전체 본문 추출이 항상 보장되지
    않는다. 원문 추출을 시도하되 실패 시 RSS 요약(snippet)을 본문으로 사용한다.
- **중복 제거: 본문 내용 유사도 (글자 n-gram TF-IDF + 코사인 유사도)** — 여러 매체가
  같은 보도자료를 배포하는 경우 1건만 저장. 본문을 글자 단위 n-gram으로 TF-IDF
  벡터화하여 코사인 유사도를 비교한다. (임계값 `SIMILARITY_THRESHOLD = 0.55`,
  `collector/dedup.py`)
  - 무료 호스팅(512MB)에서 안전하도록 형태소분석기(Kiwi)는 쓰지 않는다. 형태소 기반
    단어 TF-IDF가 필요하면 메모리가 큰(유료) 인스턴스에서 교체 가능.

### 탭2 · 서비스 게시판
- 나의AAC(소식·커뮤니티), FAIR AI(공지사항·인사이트), 대표 홈페이지(재단소식),
  프로젝토리(공지·이야기·갤러리) 게시판 수집
- 2026-01-01 이후 글만
- 수집 항목: 제목, 작성일, 본문(목록 요약), 원문 URL
  - 무료 호스팅 안정성을 위해 목록 페이지만 받아 추출하며(상세 페이지 미접속), 본문은
    목록 요약을 사용한다. SPA 사이트(FAIR AI, 대표홈페이지)는 정적 목록이 없어 건너뛴다.
- **중복 제거: 제목 기준**

> 첨부파일은 수집하지 않습니다. 수집은 화면의 **"수집 실행"** 버튼으로 **수동** 실행합니다.

## 실행 방법

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

브라우저(또는 같은 네트워크의 모바일)에서 `http://localhost:5000` 접속.

## 배포 (URL 공유)

파이썬 설치 없이 브라우저 클릭만으로 배포해 공개 URL을 만드는 방법은 [`DEPLOY.md`](DEPLOY.md) 참고.
(Render/Railway 무료 플랜, GitHub 연동)

## 구조

```
app.py                  # Flask 앱 (라우트 / 수집·조회 / 진단·분석 API)
collector/
  db.py                 # SQLite 저장소 (news / boards 테이블)
  dedup.py              # 중복 판단 (단어 TF-IDF 본문 유사도 / 제목)
  fetcher.py            # HTTP 요청 헬퍼 (모바일 UA, 재시도)
  extractor.py          # 범용 본문/메타 추출기 (readability 방식)
  google_news.py        # 탭1 구글 뉴스(RSS) 크롤러
  boards.py             # 탭2 게시판 크롤러 (사이트별 설정)
  inspect.py            # 사이트 구조 분석 (선택자 결정용)
templates/index.html    # 모바일 UI (탭 2개 + 상태 확인)
static/css, static/js   # 스타일 / 동작
data/collector.db       # 수집 데이터 (자동 생성)
```

### 진단/분석 API
- `GET /api/diag?group=news|boards|all` — 각 대상 사이트 접속 가능 여부 점검 (UI "상태 확인" 버튼)
- `GET /api/inspect[?url=...]` — 사이트 구조 분석(후보 선택자/SPA 여부 등), 선택자 보정용

## ⚠️ 초안 단계에서 알아둘 점

- **게시판 선택자는 `/api/inspect`로 확인한 실제 구조 기준**입니다. 서버렌더 사이트
  (나의AAC 2개, 프로젝토리 3개)는 수집되며, **SPA 사이트(FAIR AI 2개, 대표홈페이지)는
  정적 HTML에 목록이 없어** 현재 방식으로는 수집되지 않습니다(추후 사이트 API 또는
  Playwright 필요).
- **구글 뉴스 본문**: 링크가 구글 리다이렉트라 원문 전체 본문 추출이 항상 보장되지
  않으며, 실패 시 RSS 요약으로 대체됩니다.
- **SPA(JS 렌더링) 사이트 대응**: `fairai.or.kr`(Nuxt), `ncfoundation.or.kr`(React)는
  목록이 JS로 그려져 정적 HTML에 없습니다. `collector/fetcher.py`를 Playwright 기반으로
  교체하거나, 사이트의 내부 JSON API를 찾아 파서를 바꾸면 됩니다.
