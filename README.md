# ncfoundation-collector

엔씨문화재단 관련 **뉴스/기사**와 **재단 서비스 게시판 글**을 수집하는 모바일 웹 (초안).

## 기능

탭 2개로 구성된 모바일 웹입니다.

### 탭1 · 뉴스 · 기사
- 네이버 뉴스에서 `엔씨문화재단` / `NC문화재단` 키워드 기사 수집
- 2026-01-01 이후 기사만
- 본사(엔씨소프트) 단독 기사 제외 (재단 키워드 포함 기사만)
- 광고/댓글/배너 제외 (본문 영역만 추출)
- 수집 항목: 제목, 작성일, 작성자(언론사), 본문, 원문 URL
- **중복 제거: 본문 내용 유사도** (여러 매체가 같은 보도자료를 배포하는 경우 1건만 저장)

### 탭2 · 서비스 게시판
- 나의AAC(소식·커뮤니티), FAIR AI(공지사항·인사이트), 대표 홈페이지(재단소식),
  프로젝토리(공지·이야기·갤러리) 게시판 수집
- 2026-01-01 이후 글만
- 수집 항목: 제목, 작성일, 작성자, 본문, 원문 URL
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

## 구조

```
app.py                  # Flask 앱 (라우트 / 수집·조회 API)
collector/
  db.py                 # SQLite 저장소 (news / boards 테이블)
  dedup.py              # 중복 판단 (본문 유사도 / 제목)
  fetcher.py            # HTTP 요청 헬퍼 (모바일 UA, 재시도)
  naver_news.py         # 탭1 네이버 뉴스 크롤러
  boards.py             # 탭2 게시판 크롤러 (사이트별 설정)
templates/index.html    # 모바일 UI (탭 2개)
static/css, static/js   # 스타일 / 동작
data/collector.db       # 수집 데이터 (자동 생성)
```

## ⚠️ 초안 단계에서 알아둘 점

- **크롤러 CSS 선택자는 추정값입니다.** 개발 환경에서 대상 사이트 접근이
  차단되어 실제 HTML을 확인하지 못했습니다. 로컬에서 한 번 실행한 뒤
  `collector/naver_news.py`, `collector/boards.py`의 선택자를 실제 구조에
  맞게 보정해야 합니다.
- **SPA(JS 렌더링) 사이트 대응**: `fairai.or.kr`, `projectory.or.kr` 등은
  목록이 JS로 그려질 수 있습니다. `requests`로 목록이 비면
  `collector/fetcher.py`를 Playwright 기반으로 교체하거나, 사이트의 내부
  JSON API를 찾아 파서를 바꾸면 됩니다.
- 네이버는 과도한 요청 시 차단될 수 있으니 페이지 수/요청 간격에 유의하세요.
