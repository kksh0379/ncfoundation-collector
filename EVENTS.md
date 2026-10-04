# 행사 수집 (구조화 소스)

행사 탭은 기본적으로 **구글 뉴스 RSS**(기사화된 행사)에서 모으지만, 누락이 많다.
`collector/event_sources.py`가 **행사를 목록으로 직접 제공하는 공공 Open API**를 추가로 수집해
날짜·장소가 확정된 행사를 채운다. 각 소스는 **env 미설정 시 자동 skip, 실패 시 degrade**(날조 없음).

## 1) 한국관광공사 TourAPI — 행사/축제 (추천 1순위)

전국 축제·문화행사를 날짜·장소·이미지까지 구조화해 제공. 커버리지가 가장 크다.

- 발급: [공공데이터포털](https://www.data.go.kr) → **“한국관광공사_국문 관광정보 서비스”** 활용신청 → **서비스키(디코딩, Decoding)** 복사.
- Render(hscope) → Environment 에 추가 후 재배포:
  - `TOURAPI_KEY = <디코딩 서비스키>`
  - (선택) `TOURAPI_FESTIVAL_URL` — 기본값 `https://apis.data.go.kr/B551011/KorService2/searchFestival2`. 서비스 버전이 다르면(KorService1 등) 이 값으로 교체.
- 매핑: `eventstartdate/eventenddate`(YYYYMMDD) → 시작/종료일, `addr1` → 장소·지역, `firstimage` → 이미지, `contentid` → 상세 링크.

## 2) 문화포털 공연·전시정보 API — 국내 전시·공연 (추천)

문화포털(culture.go.kr) 공연·전시정보 API. 국내 **전시·공연·행사**를 날짜·장소 구조화로 제공
(코엑스·벡스코 등의 문화 전시 포함). 응답이 **XML**이라 XML/JSON 모두 처리한다.

- 발급: [공공데이터포털](https://www.data.go.kr) 또는 [문화포털 Open API](https://www.culture.go.kr)에서 **공연전시정보** 서비스키(디코딩) 발급.
- Render env: `CULTURE_API_KEY = <디코딩 서비스키>` (URL 기본값: `http://www.culture.go.kr/openapi/rest/publicperformancedisplays/period`, 다르면 `CULTURE_API_URL`로 교체)
- 매핑: `title`, `startDate/endDate`, `place`, `realmName`(전시/공연 분류), `area`, `thumbnail`, `url`.

> 참고: **GEP(gep.or.kr)는 ‘해외 전시’ 포털**이라 국내 행사엔 쓰지 않는다.

## 3) 코엑스 행사 일정 (B2B 전시·컨벤션·포럼)

코엑스(coex.co.kr/event/full-schedules/)는 **SSR HTML**이라 직접 파싱한다. `<a class='BlogEventItem-link'>` 안의
제목(`.BlogEventItemCont-tit`)·기간(`.BlogEventItemCont-date`)·장소(`.BlogEventItemCont-hall`)·분류(Convention/Exhibition 등)를 추출.
**별도 키 불필요**(공개 페이지). `COEX_OFF=1`로 끄거나 `COEX_SCHEDULE_URL`로 교체 가능. 기본 **1개월 창**(코엑스가 보여주는 범위).
Convention 카테고리도 포함돼 AI 윤리 포럼 같은 **학술·B2B 행사도 잡힌다**. (전시장 캘린더가 깨지면 `/api/eventcheck`의 `coex_probe.row_sample`로 구조 재확인.)

## 확인 · 진단

브라우저로 **`/api/eventcheck`** 를 열면(로그인 불필요) 소스별로 다음을 보여준다(서비스키 비노출):

- `configured` (env 설정 여부), `parsed` (파싱된 행사 수)
- `raw.status` (HTTP 상태), `raw.rows` (응답 행수), `raw.first_keys` (첫 항목 필드명), `raw.sample` (원문 일부)

`parsed > 0` 이면 성공. `configured`인데 `parsed=0`이면 `raw.first_keys`/`raw.sample`로 **필드 매핑을 보정**한다(엔드포인트가 틀리면 `raw.status`/`sample`에 오류가 보인다).

## 동작

- 수집 실행(행사) 또는 4시간 배치에서 `events.crawl`이 구글 뉴스 결과에 **구조화 소스 결과를 병합**(제목+시작일로 중복 제거)한다.
- 종료된 행사(end_date < 오늘)는 `db.list_events`가 자동으로 제외한다.

## 분야 체크박스 (v3.31)

제목·소개·기존 분류에 있는 키워드를 기준으로 화면에서 자동 분류합니다. 재수집이나 DB 변경은 필요하지 않습니다. 한 행사에 여러 분야가 붙으며 선택한 분야는 합집합으로 조회합니다. 기본은 전체 선택이고, 모두 해제하면 아무 행사도 보이지 않습니다. 검색과 앨범·캘린더는 동일한 필터 결과를 사용합니다.

| 분야 | 정의 |
|---|---|
| IT·기술 | 소프트웨어·개발·클라우드·보안·반도체·로봇·디지털 기술 |
| AI·데이터 | 인공지능·생성형 AI·머신러닝·데이터 기술과 활용 |
| AI 윤리 | AI 관련 윤리·거버넌스·책임·신뢰·안전·공정성·규제 |
| 산업·비즈니스 | 산업·창업·투자·경제·금융·채용 |
| 문화·전시 | 문화·예술·전시·축제·콘텐츠 |
| 교육·공익 | 교육·학습·복지·포용·접근성·환경·사회적 가치 |
| 기타 | 위 분야를 확인할 단서가 부족한 행사 |

키워드 기반이므로 오분류가 있을 수 있습니다. 장소명이나 언론사·수집 출처는 분야 근거로 사용하지 않습니다.

## AI 큐레이션·날짜 탐색 (v3.32)

행사일정 기본 화면은 AI 큐레이션입니다. 관심 분야·키워드는 브라우저 로컬 저장소에 보관합니다. 전체 후보와 관련성 높은 60개까지를 추려 서버의 Anthropic API가 최대 8개를 고릅니다. 저장된 행사 ID만 허용하며 기간·장소는 AI 응답으로 변경하지 않습니다. 종료된 행사와 관심사에 맞지 않는 후보는 제외합니다. 키가 없거나 AI 호출에 실패하면 관심사 일치 기준 추천임을 표시합니다.

- `ANTHROPIC_API_KEY`: 기존 AI 키 재사용. 선택 `EVENT_CURATION_MODEL`, 없으면 기존 모델 자동 선택.
- `POST /api/events/recommend`: topics(최대 7개), keywords(최대 8개, 각 40자). pending이면 같은 요청으로 재조회. 동일 설정 10분 캐시, 전역 동시 작업 2개.
- 행사 화면 진입 때만 자동 추천합니다. 관심사 설정은 팝업에서 변경합니다. 결과는 제목·기간·장소가 포함된 1열 배너 목록이며 배너 클릭으로 원문을 엽니다. 큐레이션에서는 추천 이유와 스크랩을 표시하지 않습니다. 원본 이미지 미보유 시 CSS 분야별 배너입니다.
- 캘린더는 날짜별 건수와 선택일 목록을 보여줍니다. 날짜에는 제목이나 막대를 넣지 않습니다. 목록 크게 보기는 월 그리드를 주간 날짜띠로 바꾸며 월 이동·오늘 이동·분야/검색 필터와 함께 동작합니다.
