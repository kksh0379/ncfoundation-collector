# 🛠 개발 노트 — 휴 스코프

> 📌 **고정 메모 (베타 공개 전 할 것)**
> - **진단 엔드포인트 잠그기**: `/api/peek`, `/api/apihunt`, `/api/grep`, `/api/grepall`, `/api/dbcheck`, `/api/ytcheck`, `/api/img` 가 현재 공개 상태다. peek/grep/img 계열은 우리 서버로 임의 URL을 대신 요청하는 오픈 프록시라 악용 위험 → 관리자 전용(`_admin_ok`)으로 잠그거나 제거할 것.
> - **테스트 계정 정리**: 일반 로그인 `test1 / 1234` (로그인창 자동입력) 는 테스트용이며 힌트가 노출돼 있다. 실제 공개 전에는 힌트 제거·비번 변경 또는 정식 계정 체계로 교체할 것.
> - **AI 리포트**는 `ANTHROPIC_API_KEY` 가 있어야 동작한다(유료 API). 없으면 리포트 탭의 "분석 실행"만 비활성이고 나머지는 정상.

휴 스코프의 구조·운영·설정을 정리한 문서입니다. 재단/업계 **뉴스·게시판·유튜브 데이터**를 자동 수집해 모바일 웹으로 보여주고, 그 데이터로 **AI 동향 분석 리포트**까지 생성하는 서비스입니다.

---

## 0. 한눈에 보기

- **앱**: 파이썬 Flask 하나로 동작(웹 화면 + 수집기 + 분석 엔진).
- **호스팅**: Render (웹 서비스). 유료 인스턴스라 잠들지 않음.
- **데이터베이스**: 외부 Postgres(`DATABASE_URL`). 서버가 재시작돼도 데이터 유지.
- **정기 수집**: 앱 내부 스케줄러 + 외부 크론(cron-job.org)이 주기적으로 수집 실행.
- **출처**: 구글 뉴스 RSS(뉴스류·행사), 각 기관 내부 API(게시판), 유튜브 Data API(재단YT), 행사장 공식 일정(코엑스·킨텍스·벡스코·대전컨벤션센터·aT센터·수원메쎄·세텍).
- **공식 행사 일정**: `collector/venue_sources.py`가 향후 6개월 목록을 페이지별 수집. `VENUE_SOURCES_OFF=1`로 새 6개 출처 비활성화. 배포 초기 1회 수집 완료 표시는 DB `venue_sources_version=1`, 정기 갱신은 기존 행사 수집 배치. `venue_sources_status`에 초기 출처 진단 저장. 대전은 HTTPS 응답 실패 시 동일 공식 사이트의 공개 HTTP 목록 사용(TLS 검증 해제 없음).
- **AI 분석**: Anthropic(Claude) API로 재단 동향 인텔리전스 리포트 생성.
- **서체**: 유니버설 디자인 서체 **KoddiUD 온고딕**(한국장애인개발원, CC BY-SA)을 앱에 직접 호스팅(`static/fonts/`). 가독성·접근성 우선. 출처 표기: 소개 페이지 하단.

---

## 0.5 전체 프로세스 도식 (프론트 → 서버 API → 수집·분석 → 외부 → DB)

> 아래 다이어그램은 GitHub·개발노트 팝업에서 그림으로 렌더됩니다. 화살표는 데이터/호출 방향입니다.

```mermaid
flowchart TB
  subgraph CLIENT["📱 프론트엔드 · templates/index.html + static/js/app.js"]
    UI["탭 화면<br/>냥·게임·nc뉴스·업계·행사·게시판·재단YT"]
    ACT["🔍 AI 리포트 · 🔖 스크랩 · 로그인/로그아웃"]
  end

  subgraph SERVER["🖥 Flask 서버 · app.py"]
    READ["조회 API GET<br/>/api/catnews · /gamenews · /news · /biznews · /secnews<br/>/api/events · /boards · /social · /meta"]
    RPTAPI["리포트·보안AI API<br/>/api/report/list · /get · /status · /run<br/>/api/security/analyze · /status"]
    AUTH["계정·개인화 API<br/>/api/login · /logout · /me · /mydata<br/>/api/scrap · /read · /groups · /scrap/groups"]
    CRAWLAPI["수집 API 관리자<br/>/api/crawl/:group/start · /status<br/>/api/admin/purge · /api/notes"]
    SCHED["내부 스케줄러 + 외부 크론<br/>/api/cron"]
  end

  subgraph COLLECT["🧲 수집·분석 · collector/*"]
    GNEWS["google_news.py<br/>뉴스류·행사 RSS 수집"]
    EV["events.py<br/>행사 날짜·장소 추출"]
    BRD["boards.py<br/>게시판 수집"]
    SOC["social.py<br/>재단YT 수집"]
    DED["dedup.py<br/>동일기사 묶기"]
    ANA["analysis.py<br/>AI 리포트 생성"]
    SECAI["security_ai.py<br/>보안뉴스 AI 태깅·중요도"]
  end

  subgraph EXT["🌐 외부 서비스"]
    GRSS["Google News RSS"]
    YTAPI["YouTube Data API"]
    BAPI["기관 내부 API<br/>대표홈·프로젝토리·나의AAC·FAIR AI"]
    CLAUDE["Anthropic Claude API"]
  end

  DB[("🗄 DB · Postgres Neon / SQLite<br/>news·boards·social·events<br/>user_state·report_snapshot")]

  UI -->|fetch 조회| READ
  ACT -->|fetch| AUTH
  ACT -->|리포트 열람| RPTAPI
  READ --> DB
  AUTH --> DB
  RPTAPI --> DB
  CRAWLAPI -->|수집 실행| COLLECT
  SCHED -->|정기 배치| COLLECT
  RPTAPI -->|분석 실행 관리자| ANA
  RPTAPI -->|보안 AI 분석 관리자| SECAI
  SECAI --> CLAUDE
  SECAI -->|태깅·중요도 저장| DB
  GNEWS --> GRSS
  EV --> GRSS
  SOC --> YTAPI
  BRD --> BAPI
  ANA --> CLAUDE
  GNEWS --> DED
  ANA -->|데이터 읽기| DB
  COLLECT -->|upsert 저장| DB
```

**흐름 요약**
1. **조회**: 프론트가 각 탭 진입 시 `조회 API(GET)`를 호출 → 서버가 **DB**에서 읽어 카드/캘린더로 렌더. (비로그인도 가능)
2. **개인화**: 로그인 후 스크랩/읽음/그룹은 `계정 API`로 **`user_state`** 에 계정별 저장·동기화.
3. **수집**: 관리자 "수집 실행"(`/api/crawl/:group/start`) 또는 **스케줄러/크론**(`/api/cron`)이 `collector/*` 실행 → **외부 소스**(구글뉴스 RSS·유튜브 API·기관 내부 API)에서 가져와 dedup 후 **DB에 upsert**.
4. **AI 리포트**: 관리자 "분석 실행"(`/api/report/run`) → `analysis.py`가 DB의 뉴스·재단YT를 정리해 **Claude API** 호출 → 결과 JSON을 **`report_snapshot`** 에 스냅샷 저장. 열람(`/get`·`/list`)은 누구나.

---

## 0.7 화면 구조(하단 대메뉴)

- **하단 탭 내비게이션(대메뉴)**: `뉴스(콜렉터)` · `맛집(점심 음식점)` · `리포트` · `스크랩`.
  - **뉴스(`#view-collector`)**: 기존 8개 탭(냥/게임/nc/업계/보안/행사/게시판/재단YT)을 하나로 묶은 대메뉴. 내부 상단 탭바로 전환.
  - **맛집(`#view-food`)**: 위치(사업장) 기준 주변 점심 식당 검색·평점·후기·AI 추천 → **§0.8** 참고. 하위 본문 뷰 2개: 평점·후기(`#view-lunch-reviews`), AI 추천(`#view-lunch-ai`).
  - **리포트·스크랩**: 각각 기존 풀팝업(모달)을 여는 푸터 탭(본문 전환 아님, 오버레이). 관리자 표시설정 off 시 일반 사용자에겐 숨김.
- 로그인/로그아웃·관리자 버튼은 **상단 헤더**로 이동. 저작권/버전은 스크롤 하단 정적 표시.

## 0.8 맛집(점심) 도메인 — Phase 1

> "오늘 점심 뭐 먹지?" 개인 창작물. 특정 회사/사내 서비스가 아님(중립적 '이용자/방문자' 표현).

- **모듈**: 수집 어댑터 `collector/lunch.py`(현재 **카카오 로컬** 어댑터), 저장/집계 `collector/db.py`의 `lunch_*` 헬퍼, API `app.py`의 `/api/lunch/*`, 프론트 `static/js/app.js`의 `initLunch()` IIFE.
- **위치 4곳**: NC문화재단 사옥(기본, 종로구 이화장길 100) / NC 판교R&D센터 / 프로젝토리 성남지점 / 개발자 동네. `lunch_location`에 시드, 주소→좌표 지오코딩(카카오 주소검색). 상단 드롭다운으로 전환(실주소 비노출, 반경만), 위치별 반경(m) 보유. `lunch_sync_locations`가 **순번(sort) 기준**으로 이름/주소를 갱신(재배포 시 이름 변경도 중복 없이 반영).
- **수집(관리자 수동 + 주 1회 자동)**: 카카오 키워드검색(`category_group_code=FD6`)을 여러 검색어로 좌표 반경 내 조회 → `place_id` 중복 제거. **확보 필드**: 상호·카테고리·주소·좌표·전화·`place_url`(카카오맵)만.
  - **자동 재수집**: 뉴스와 달리 식당은 잘 안 바뀌므로 **주 1회**(`LUNCH_REFRESH_DAYS`, 기본 7일). 정기 배치(4시간 스케줄러 + 외부 크론)가 돌 때마다 호출되지만 `meta.lunch_last_refresh` 타임스탬프로 억제해 **실제 수집은 주기당 1회**. 3개 위치를 순차 재수집(upsert). 폐업 자동 삭제는 안 함(원칙: 조작 없음, `last_checked`만 갱신).
- **원칙(데이터 신뢰)**: 메뉴·가격·영업시간·사진·외부평점은 **공식 API로 못 얻는다 → 지어내지 않는다.** 상세는 **카카오맵 링크 랜딩**(기사 원문 링크처럼)으로 대체.
- **이용자 평점·후기(우리 앱 누적)**: `lunch_review`(별점 1~5 + 후기 300자), `lunch_visit`(오늘 방문). 목록/후기 뷰에서 평균 별점·후기수·방문수 집계. **로그인 사용자만 작성**.
- **AI 추천(`/api/lunch/recommend`) — 가중 점수 엔진**: 클라이언트가 **성향(persona) 1개 + 회피 카테고리 + 기분 키워드 + 후보 id**를 전달 → 서버가 후보별 점수 계산 후 **상위 5곳에서 가중 랜덤** 추첨(같은 집만 나오는 것 방지). 응답에 pick·reason·tags·alternatives.
  - 점수 구성(가중치 합≈100): 신뢰도(평점·후기)·거리·상황(요일/시간)·메뉴 다양성(최근 먹은 카테고리 감점)·미방문 탐험·팀 방문도·랜덤. 성향/기분이 가중치·카테고리 보너스를 조정.
  - 성향: 안전빵/모험/월급루팡/제대로/빨리먹자/세계여행/숨은맛집/오랜만이야. 로그인 시 최근 3일 방문 식당 제외, 최근 3·7일 카테고리 감점.
  - **미보유 데이터(메뉴·가격·영업시간·날씨)는 점수에 쓰지 않음**(원칙: 조작 없음). 날씨 연동은 향후 과제.
- **카테고리 정규화**: 카카오 `category_name` → 한식/고기/면요리/분식/중식/일식/돈까스/양식/아시아음식/생선·해산물/샐러드·건강식/패스트푸드/카페·디저트/기타. 도보시간 ≈ 거리/67m·분.
- **관리자 도구**: 주변 식당 수집(백그라운드 스레드 + `/collect/status` 폴링), 직접 추가(`/restaurant`), 숨기기/해제(`/exclude`).
- **어댑터 분리 이유**: 특정 서비스 종속 방지. 네이버/구글 등 다른 소스로 교체·추가 시 `lunch.py`만 바꾸면 됨(스키마·프론트 불변).

## 1. 탭 구성

| 탭 | 내용 | 수집 방식 |
|---|---|---|
| 🐈 냥정보 | 반려묘 뉴스(사료·행동·제도 등 5개 카테고리) | 구글 뉴스 RSS |
| 🕹 게임정보 | 게임 뉴스(신작·업데이트·e스포츠·업계·콘솔·인디) | 구글 뉴스 RSS |
| nc 뉴스 | NC문화재단/엔씨소프트/자회사(전체·재단·본사·자회사 체크박스) | 구글 뉴스 RSS |
| 📊 업계동향 | 문화·공익 재단 및 주요 재단 뉴스(NC 제외) | 구글 뉴스 RSS |
| 🛡 보안뉴스 | 개인정보·정보보안(유출·해킹·취약점·정책·트렌드, 5개 분류 체크박스) | 구글 뉴스 RSS |
| 📅 행사일정 | 국내 행사(앨범/캘린더, 날짜·장소 자동추출) | 구글 뉴스 RSS + 본문 파싱 |
| 📋 재단게시판 | 대표홈페이지·프로젝토리·나의AAC·FAIR AI | 각 사이트 내부 API |
| ▶ 재단YT | NC문화재단 채널(재단) + 주요 재단 유튜브 검색(주요 재단) | 유튜브 Data API |
| 🧠 리포트 | AI 재단 동향 분석 리포트 **(관리자 전용)** | LLM(Anthropic) |

- 뉴스류(냥정보/게임정보/nc/업계동향/보안뉴스/행사)는 모두 `news` 테이블을 쓰고 `section`(cat/game/nc/biz/sec/event) 컬럼으로 구분한다.

---

## 2. 계정 / 권한 / 개인화

- **로그인 유형 2가지** (푸터 우측 로그인 버튼 → 팝업에서 선택):
  - **관리자**: 비밀번호(`ADMIN_PW`). 상태 확인·수집 실행·DB 초기화·개발노트·로그·🧠 리포트가 노출·동작.
  - **일반**: 아이디+비번. 테스트 계정 `test1 / 1234`(로그인창에 자동입력, 바로 로그인). 스크랩 등 개인 기능만.
- **개인화(로그인 필요, 서버 계정별 저장)**:
  - **스크랩**: 카드 우상단 북마크. 헤더 🔖 스크랩 → 풀팝업에서 목록, **검색 + 커스텀 그룹(다중 소속)** 관리.
  - **읽음 표시**: 원문 링크 클릭 시 읽음 처리(카드 흐리게).
  - 같은 계정이면 다른 기기에서도 동기화된다(`user_state` 테이블).
- **비로그인(방문자)**: 모든 탭 조회·검색·링크 복사 가능. 스크랩/읽음/리포트는 불가.
- **표시 설정(관리자, 헤더 `⚙ 표시`)**: 각 탭(8개)·AI 리포트·스크랩을 **온오프**. 끈 항목은 일반/방문자에게 숨김, **관리자는 항상 노출**(미리보기). `meta.feature_flags` JSON 저장(전 사용자 공통). 조회 `/api/features`(공개)·저장 `/api/features`(관리자). 활성 탭이 숨겨지면 첫 노출 탭으로 자동 전환.
- **보기 방식**: 리스트/카드 토글(뉴스류·게시판), 행사일정은 앨범/캘린더 토글. 선택은 브라우저에 저장.
- **공통 UI**: 탭별 검색(제목·본문·출처, 조사 제거), 게시물 수 표시, 오늘 등록 글 N 딱지, 카드 링크 복사, 빈 화면 다시 불러오기, 탭바 가로 스크롤+끝 페이드, **스크롤 다운 시 상단/푸터 자동 숨김·업 시 표시(유튜브식)**.

---

## 3. 환경변수 (Render → Environment)

- **`DATABASE_URL`** — Postgres 연결 문자열. 있어야 데이터가 영구 저장된다.
- **`ADMIN_PW`** — 관리자 로그인 비밀번호.
- **`SECRET_KEY`** — 로그인 세션 서명 키(긴 무작위 문자열 권장).
- **`CRON_TOKEN`** — 외부 크론 인증 토큰. cron-job.org URL의 `token=` 값과 동일해야 한다.
- **`YOUTUBE_API_KEY`** — 재단YT(NC 채널 전 영상 + 주요 재단 유튜브 검색)에 필요. 없으면 재단은 RSS 최신만, 주요 재단은 건너뜀.
- **`ANTHROPIC_API_KEY`** — 🧠 AI 리포트 + 🍚 맛집 AI 추천(LLM)에 필요(Anthropic Console에서 발급, 유료). 없으면 리포트 생성 불가, 맛집 추천은 랜덤 폴백으로 동작.
- **`KAKAO_REST_KEY`** — 🍚 맛집 주변 식당 수집·지오코딩(카카오 로컬 API, REST 키). Kakao Developers에서 발급. 없으면 자동 수집 불가(관리자 직접 추가만 가능).
- **`LUNCH_REFRESH_DAYS`** — 🍚 맛집 주변 식당 **자동 재수집 주기(일)**. 기본 `7`(주 1회), `0`이면 자동 재수집 끔(수동만). (선택)
- **`ANALYSIS_MODEL`** — 리포트에 쓸 모델. 기본 `claude-3-5-sonnet-latest`. (선택)
- **`SEC_AI_LIMIT`** — 보안뉴스 🧠 AI 분석 1회 실행당 분석할 기사 수 상한. 기본 `60`. (선택)
- **`SEC_AI_BATCH`** — AI 분석 1회 LLM 호출당 기사 수. 기본 `10`. (선택)
- **`SEC_AI_MAX_TOKENS`** — AI 분석 응답 토큰 상한. 기본 `8000`. (선택)
- **`SEC_AI_CRAWL_MAX`** — 보안뉴스 수집 1회당 자동 AI 분석 총 상한. 기본 `200`. (선택)
- **`SECREPORT_MAX_TOKENS`** / **`SECREPORT_ARTICLES`** — 월간 보안 리포트 응답 토큰 상한(기본 `10000`)·입력 기사 수 상한(기본 `45`). (선택)
- **`SECREPORT_AUTO`** — 월간 보안 리포트 **월초 자동 생성** 여부. 기본 켜짐(`1`), `0`이면 수동만. (`ANTHROPIC_API_KEY` 있을 때만 동작)
- **`BACKFILL_DAYS`** — 최초 자동 백필 기간(일). 기본 `1825`(5년).
- **`BATCH_DAYS`** — 정기 배치의 뉴스 수집 창(일). 기본 `30`.
- **`AUTO_BACKFILL`** — 부팅 시 DB 비면 자동 수집할지. 기본 꺼짐(`0`).
- **`ENABLE_SCHEDULER`** — 앱 내부 스케줄러 사용 여부. 기본 켜짐(`1`).
- **`DB_KEEPALIVE_SEC`** — DB keep-alive 주기(초). **기본 `0`(꺼짐)**. ⚠️ Neon 무료는 compute 사용시간 한도가 있어, 켜두면 DB가 상시 가동돼 한도 소진→`quota exceeded`로 정지될 수 있음(실제 발생). 무료 플랜은 끄고(콜드스타트는 자동 재시도로 처리) 상시 켜짐 유료 DB에서만 값을 준다.
- **`IMG_ENRICH_MAX`** — 수집 1회당 대표 이미지 보강 개수 상한. 기본 `200`.
- **`EVENT_BODY_MAX`** — 행사일정에서 날짜 추출용으로 본문을 여는 후보 상한. 기본 `400`.

> 비밀값(DB 연결 문자열·비밀번호·토큰·API 키)은 코드나 저장소가 아니라 환경변수에만 둔다.

---

## 4. 수집 동작 (공통)

1. **최초 자동 백필** — DB가 비어 있을 때만(옵션, 기본 꺼짐) 최근 `BACKFILL_DAYS`를 수집.
2. **정기 배치** — 내부 스케줄러 + 외부 크론이 최근 `BATCH_DAYS`만 훑어 새 글 보충.
3. **수동 수집** — 관리자가 각 탭 "수집 실행". 뉴스류는 최근 5년(`RECENT_DAYS`) 기준.

- **중복 방지**: 저장 시 URL 기준 upsert(신규 수 = 저장 전후 개수 차이).
- **탭별 초기화**: 관리자 🗑 초기화는 **현재 탭만** 지운다(뉴스류는 해당 section만).
- **멈춤 방지**: 30분 넘게 진행 중이면 자동 해제. 진행은 서버 백그라운드 + 프론트 폴링.

---

## 5. 탭별 수집 상세

### 뉴스류(구글 뉴스 RSS)
- 기간을 120일 구간으로 나눠 `after:`/`before:` 날짜 검색으로 깊은 과거까지 수집.
- 구글 링크는 리다이렉트라 원문 URL 복원(batchexecute) 후 본문 요약·대표 이미지(og:image) 추출, 실패 시 RSS 요약. 저장 키(url)는 구글 링크로 고정, 실제 기사 주소는 `source_url`.
- 신규가 많으면(>150) 속도를 위해 요약만 저장, 이미지·본문은 이후 조금씩 보강.
- 동일 기사 묶음: 제목/본문 유사도로 그룹화 → 대표 1건 + "N개 매체" 아코디언.
- **nc 뉴스 분류**: 재단(엔씨문화재단) / 본사(엔씨소프트) / 자회사(NC AI·QA·IDS 등)는 클라이언트에서 키워드로 구분(체크박스 다중 선택).

### 행사일정
- AI 한정 없이 국내 행사 전반. 행사장·지역·분야 앵커 검색어.
- 국내 판별: 기본 국내(gl=KR 소스)로 보되, 해외 행사명(CES/MWC)·도시·"해외국가+참가/현지"면 제외.
- 성료(종료) 기사·1년 넘은 기사 제외. **날짜가 확인되는 행사만** 수집(요약에 없으면 본문을 열어 추출, 상한 `EVENT_BODY_MAX`).
- 연도 추정은 **기사 작성일 기준**(오늘 기준이면 과거 기사를 미래로 오인).
- 앨범(카드) + 캘린더(월 그리드 색막대 + 아젠다 목록, 클릭 시 원문).

### 보안뉴스
- 개인정보보호·정보보안 전반: 실제 사고(유출·해킹·랜섬웨어)뿐 아니라 취약점(CVE·제로데이), 정책·법령(개인정보보호법·ISMS·과징금), 보안 트렌드(AI/클라우드 보안·제로트러스트 등)까지.
- **단순 키워드 노출이 아니라 "사고 키워드 + 행위/결과 키워드" 조합**으로 검색(예: `개인정보+유출`, `랜섬웨어+피해`, `취약점+악용`)해 실무 참고가치 위주로 수집.
- 6개 분류(체크박스, 다중선택): **개인정보 / 해킹·침해 / 취약점 / 정책·규제 / 개보위·처분 / 보안트렌드**. 검색어 그룹명을 `category`로 저장하고, 화면에서 체크박스로 필터.
- **개보위·처분**: 개인정보보호위원회(개보위)·공정거래위원회(공정위)의 **의결·처분·과징금·시정명령·조사** 중심 수집(담당자 핵심). 보도 기준으로 최대한 포착.
- gl=KR·hl=ko RSS라 국내 매체 중심(국내 관련성)이며, 국내에도 영향이 큰 글로벌 벤더(MS/Google/Apple/AWS 등) 사고도 자연히 포함.
- 광고·홍보·시세성(할인·프로모션·코인 시세·주가·채용 등) 제목은 제외. 동일 사건은 `news` 클러스터링으로 "N개 매체" 묶음.
- **AI 후처리(수집 시 자동)**: 보안뉴스를 **수집/초기화 재수집할 때마다** 서버가 자동으로 Claude API **배치 분석**을 이어서 실행해 기사별 태깅·중요도·시사점을 채운다(별도 수동 버튼 없음). 아직 분석 안 된 기사만(증분), 수집 1회당 최대 `SEC_AI_CRAWL_MAX`(기본 200)건, 나머지는 다음 수집에서 이어서. `ANTHROPIC_API_KEY` 없으면 수집은 정상 진행하고 분석만 건너뜀.
  - 태그(복수): 개인정보/개인정보유출/해킹/랜섬웨어/악성코드/피싱·스미싱/취약점/제로데이/계정탈취/데이터유출/공급망공격/클라우드보안/AI보안/보안정책/법령·규제/과징금·제재/보안기술/보안트렌드.
  - 중요도: CRITICAL(심각)/HIGH(높음)/MEDIUM(보통)/LOW(낮음) — 카드에 색 배지. 탭 상단 **최신순/중요도순 정렬 토글**.
  - 시사점: 보안담당자 관점 시사점·조직 확인사항·예방조치(카드에 표시), keep(참고가치 여부) 저장.
  - 세부 필드: 관련 기업/기관·공격유형·CVE·유출/피해 규모·조치/대응(기사 근거 있을 때만, 카드에 칩으로 표시).
  - 저장: `news`의 `ai_tags/ai_importance/ai_insight/ai_at` 컬럼(섹션 재사용, 스키마 마이그레이션 자동). 이미 분석된 기사는 건너뛰고 신규만(증분). 1회 상한 `SEC_AI_LIMIT`.
  - 엔진: `collector/security_ai.py` (리포트와 동일한 `ANTHROPIC_API_KEY`·모델 자동선택 재사용). 키 없으면 수집·필터까지만 동작.

### 재단게시판 (모두 내부 API 연동 완료)
- 대표홈페이지·프로젝토리·나의AAC·FAIR AI. 각 사이트가 목록을 JS로 렌더하는 SPA라 정적 HTML 대신 실제 호출되는 내부 API(JSON)로 수집.

### 재단YT
- **재단** = NC문화재단 채널(@nccf). 키 있으면 업로드 재생목록 전체(Data API), 없으면 RSS 최신.
- **주요 재단** = 업계동향과 동일한 기관 목록으로 유튜브 **검색**(Data API). 공식 채널이 아닌 영상이 섞일 수 있음(키워드 방식). 계정명(account)으로 재단/주요재단 분류.
- (구) 인스타그램 수집은 제거함.

---

## 6. AI 재단 동향 분석 리포트 (🧠 리포트 탭)

목적은 "뉴스 요약"이 아니라 **비교·변화·추세·이상징후·신규신호·근거·검토질문** 중심의 인텔리전스 리포트.

- **파이프라인**: 수집 데이터(우리=nc 재단 + NC문화재단 유튜브 / 동종=업계동향 + 주요 재단 유튜브) → 정리·중복보도 묶기(**미디어 노출량 ≠ 활동 수**) → LLM 분석 → 리포트 JSON → **스냅샷 저장** → 다음 실행 때 지난 리포트와 비교.
- **리포트 9섹션**: ① Executive Brief ② 지난 리포트 이후 변화(NEW/UP/DOWN/CONTINUED/DISAPPEARED) ③ 업계 동향 ④ 재단별 움직임 ⑤ Trend ⑥ Emerging Signals(판단 근거 포함) ⑦ 우리 재단 Position ⑧ Benchmark ⑨ 검토 과제. + 모든 결론에 **근거 원문 드릴다운**.
- **저장**: `report_snapshot` 테이블. **키=기간+분석일자(`pkey`)** — 같은 날 같은 기간을 다시 돌리면 새로 쌓지 않고 **교체(업데이트)**, 다른 날/다른 기간은 별도 보존(비교용). 비교('지난 리포트')는 같은 기간·다른 시점 최신 스냅샷과 수행.
- **삭제**(관리자, 리포트 팝업 상단): `🗑 삭제`(현재 선택 스냅샷) · `전체 초기화`(현재 종류만). 엔드포인트 `/api/report/purge` (`{"id":N}` 또는 `{"all":true,"kind":...}`).
- **리포트 2종**(팝업 상단 토글): `재단 동향`(기존) / `🛡 보안(월간)`. `report_snapshot.kind`='foundation'|'security'로 구분 저장·조회.

### 월간 보안 리포트 (🛡, 정보보안·개인정보보호 담당자용)
- 지난달(기본) 국내 보안뉴스(section='sec')를 종합해 **월간 보안 브리핑** 생성. 담당자가 바로 활용하도록 실무적으로.
- 섹션: ① 이번 달 요약(집계 타일: 전체/심각/높음/CVE + 하이라이트) ② 주요 사고·이슈(중요도·시사점) ③ 주요 취약점(CVE) ④ **개보위·규제기관 처분**(기관·과징금 금액·대상 우선 추출) ⑤ 보안 트렌드 ⑥ **담당자 점검·대응 권고**.
- 면책 문구(confidence_note): 개보위·공정위 관련 발표를 '언론 보도 기준'으로 반영하되, **공식 의결서 전문·기술적 침해 원인은 원문 확인 필요**임을 명시하도록 유도. (개보위 공식 사이트 원문 직접 수집은 후속 옵션)
- 단위: **1개월**(월초에 지난달). 스냅샷 키 `secmonth:YYYY-MM` → 같은 달 재실행 시 교체. 열람은 누구나, 생성은 관리자.
- 엔진 `collector/security_report.py`(analysis의 LLM 연결부 재사용). 엔드포인트: 생성 `/api/report/run?kind=security[&month=YYYY-MM]`, 상태 `/api/report/status?kind=security`, 목록/열람은 `/api/report/list?kind=security`·`/get`.
- **자동 생성**: 정기 배치(스케줄러 4시간·외부 크론)가 돌 때마다 "지난달 리포트가 없으면 생성"(idempotent) → 월초에 지난달치가 자동 생성되고, 이미 있으면 skip해 **월 1회만** LLM 사용. `SECREPORT_AUTO=0`으로 끌 수 있고, `ANTHROPIC_API_KEY` 없으면 자동으로 건너뜀. 관리자 수동 생성(`지난달 분석`)도 그대로 가능.
- **엔진**: `ANTHROPIC_API_KEY` 필요. 모델 `ANALYSIS_MODEL`(기본 sonnet).
- **1차 구현 범위**: 끝단 동작(탭·분석·스냅샷·리포트·드릴다운). 심화(정교한 Activity 클러스터링, 1·3·6개월/1·3·5년 기간축 UI, Gap 전용 화면, 신뢰도 지표 표시)는 다음 단계.

---

## 7. 자주 겪는 문제

- **⚠ 임시저장 배지**: `DATABASE_URL` 미반영. 환경변수 확인 후 재배포.
- **DB 연결 실패**: DB 컴퓨트 sleep 또는 연결 문자열 불일치. `/api/dbcheck`(현재 공개)로 원인 확인.
- **행사 0건/적음**: 요약에 날짜 없는 건 제외됨. 본문 파싱 상한(`EVENT_BODY_MAX`)·검색어 확장으로 조정. 뉴스 기반이라 구조적 상한 존재.
- **리포트 "키 없음"**: `ANTHROPIC_API_KEY` 미설정. **모델명 오류**면 `ANALYSIS_MODEL`을 최신 별칭으로.
- **수집 안 됨**: 30분 후 자동 해제. Render Logs에서 `[crawl]`/`[google]`/`[social]` 확인.

---

## 8. 데이터 보존 주의

- `DATABASE_URL` 삭제 시 임시 SQLite로 돌아가 재시작 때 초기화. DB 콘솔에서 프로젝트/DB 삭제 금지.
- 관리자 "DB 초기화"는 해당 탭 데이터를 지운 뒤 재수집한다(되돌릴 수 없음). 리포트 스냅샷·개인 스크랩은 별도 테이블이라 영향 없음.

---

## 9. 링크

- 사이트: https://ncfoundation-collector.onrender.com
- 소개(공유용) 페이지: https://ncfoundation-collector.onrender.com/intro
- 저장소: https://github.com/kksh0379/ncfoundation-collector
- Render: https://dashboard.render.com
- Anthropic Console(API 키): https://console.anthropic.com
- cron-job.org: https://console.cron-job.org

---

## 10. 코드 맵

- `app.py` — 라우트(화면·API), 로그인/계정, DB 보장·keep-alive, 수집 작업·상태, 스케줄러/크론/백필, DB 초기화, 개인화(스크랩/읽음/그룹), 리포트 실행.
- `collector/google_news.py` — 뉴스류 수집(재단/본사·냥정보·게임·업계동향·보안뉴스 카테고리, 구간 분할, 본문/이미지, 필터).
- `collector/events.py` — 행사일정(국내 판별·날짜/장소 추출·본문 파싱 폴백).
- `collector/boards.py` — 게시판 수집(사이트별 내부 API).
- `collector/social.py` — 재단YT(NC 채널 Data API + 주요 재단 유튜브 검색).
- `collector/analysis.py` — AI 리포트(데이터 정리·중복 묶기·LLM 호출·JSON 스키마).
- `collector/security_ai.py` — 보안뉴스 AI 후처리(배치 태깅·중요도·시사점, analysis의 LLM 연결부 재사용).
- `collector/security_report.py` — 월간 보안 리포트(정보보안·개인정보 담당자용, 지난달 종합·LLM 브리핑).
- `collector/dedup.py` — 동일 기사 그룹화.
- `collector/lunch.py` — 🍚 맛집 수집 어댑터(카카오 로컬: 지오코딩·키워드검색·카테고리 정규화·거리계산). 상세는 링크 랜딩, 데이터 조작 없음.
- `collector/db.py` — 저장소(Postgres/SQLite, 직접 접속), news/boards/social/events/user_state/report_snapshot + lunch_location/restaurant/review/visit.
- `collector/fetcher.py` / `collector/extractor.py` — HTTP 헬퍼 / 본문·이미지 추출.
- `templates/index.html`, `static/js/app.js`, `static/css/style.css` — 앱 화면.
- `collector/reader.py`(+`static/js/reader.js`, `static/css/reader.css`) — 인라인 리더(디테일뷰): 원문으로 나가지 않고 앱 안에서 기사 본문을 읽는 기능.
- `templates/intro.html`, `static/intro/*.png` — 서비스 소개(랜딩) 페이지(`/intro`, 로그인·DB 없이 정적).
- `DEVNOTE.md`(이 문서), `CHANGELOG.md`(변경 이력).


## 인라인 리더 / 디테일뷰 (2026-10-01)

- 카드에서 **원문 사이트로 이탈하지 않고** 앱 안에서 기사 본문을 읽는 뷰. Blueprint `collector/reader.py`(`app.register_blueprint`), 엔드포인트 **`/api/reader`**, 프론트 `static/js/reader.js`·`static/css/reader.css`.
- 동작: 원문 URL의 HTML을 서버가 받아(`fetch_html`) 본문 문단만 추출(`extract_paragraphs`), 실패 시 폴백(`fallback`). 구글뉴스 링크는 **실제 기사 URL로 먼저 해석**한 뒤 추출. 응답 크기 상한 `MAX_BYTES`.
- 리더는 수집/스케줄러와 무관한 **조회 기능**(배치에 영향 없음).


## DB 조회 최적화 (2026-10-01)

- `collector/read_cache.py`: 프로세스별 캐시 최대 32키, 갱신 동시 실행 3개, 실패 후 5초 대기. 오래된 정상 결과는 갱신 중에도 제공. 캐시 무효화 중 실행되던 쿼리는 이전 결과를 다시 저장하지 않는다.
- 위치 목록은 별도 1키/1작업 캐시(TTL 300초)로 다른 뉴스 조회와 경쟁하지 않는다. `ENABLE_DB_PREWARM=0`으로 시작 시 준비를 끌 수 있다. 기존 DB keepalive 정책은 유지한다.
- 식당 목록 TTL 30초, 공개 목록·메타·리포트 TTL 45초. 사용자별 스크랩·읽음·그룹은 공용 캐시에 넣지 않는다. 여러 프로세스로 늘리면 캐시는 프로세스별이며 변경 전파는 TTL에 따른다(현재 Render는 worker 1개).
- 최초 요청에서 아직 데이터가 없으면 `X-Data-Pending: 1`로 표시한다. 브라우저는 최대 8회 재조회한다. 위치 API는 `db_waking` 필드를 사용한다.
- `Server-Timing: app;dur=...`는 네트워크를 제외한 요청 처리 시간(ms). DB 쿼리 시간만을 뜻하지 않는다.
- 검증: `python -m unittest discover -s tests -v`, `node --test tests/test_frontend_data.cjs`.
- 재배포 후 첫 DB 초기화에서 조회 인덱스를 만든다. 추가 인덱스는 기존 데이터/스키마와 호환되므로 코드 롤백 시 제거할 필요가 없다.


## 재무세무 beta (v2.57)

하단 신규 재무세무 탭과 독립 `/api/finance/*` 어댑터를 추가했습니다. 환경변수, 공식 출처, 대체 데이터 정책, 세무 일정 보류 이유는 [FINANCE.md](FINANCE.md)를 참고하세요. 사업자번호는 저장하지 않으며 키가 없을 때 실제 상태를 만들어내지 않습니다.

### v3.42 행사일정 표시
- 두 콘텐츠 탭: 일반 일정 / AI 큐레이션(기본). 일반 일정 아이콘: 리스트 / 앨범 / 캘린더. `eventScheduleView` localStorage로 일반 일정 보기만 기억하고, 다른 뉴스의 `nvView`와 분리. 활성 탭에 맞춰 body 보기 클래스를 투영하므로 캘린더 하단 앨범과 큐레이션 썸네일도 유지.

### v3.43 추천 호출
- EventDiscovery.curation은 로드된 목록을 localRecommendations로 즉시 순위화. 초기 진입·탭 이동·검색·관심사 적용은 AI/추가 추천 API 호출 없음. [AI 추천받기]만 기존 POST 추천 작업을 요청하며 진행 중 중복 클릭 차단·기존 배너 유지.
