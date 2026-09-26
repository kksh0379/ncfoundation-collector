# 배포 가이드 (URL 공유용)

파이썬 설치 없이, **브라우저 클릭만으로** 배포해서 제3자에게 공유할 수 있는 URL을 만드는 방법입니다.
저장소가 이미 GitHub(`kksh0379/ncfoundation-collector`)에 올라가 있으므로 터미널이 필요 없습니다.

추천 호스팅: **Render** (무료 플랜, GitHub 연동, 설정 파일 자동 인식)

---

## Render로 배포하기 (약 5분)

1. https://render.com 접속 → **GitHub 계정으로 가입/로그인**
2. 우측 상단 **New +** → **Blueprint** 클릭
3. 저장소 목록에서 **`ncfoundation-collector`** 선택
   - 저장소가 안 보이면 **Configure account**로 Render에 저장소 접근 권한을 부여
4. 저장소의 `render.yaml`을 자동으로 읽어 설정이 채워집니다 → **Apply / Create** 클릭
5. 빌드가 시작됩니다 (처음 빌드는 형태소 분석기·scikit-learn 설치로 몇 분 소요)
6. 완료되면 `https://ncfoundation-collector-xxxx.onrender.com` 형태의 **공개 URL**이 생깁니다
   → 이 URL을 누구에게나 공유하면 접속해서 화면을 볼 수 있습니다

> Blueprint 대신 **New + → Web Service**로 만들 수도 있습니다.
> 그때는 다음만 직접 입력하세요.
> - Build Command: `pip install -r requirements.txt`
> - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300`
> - Environment Variable: `PYTHON_VERSION = 3.11.9`

---

## 대안: Railway

1. https://railway.app → GitHub 로그인
2. **New Project** → **Deploy from GitHub repo** → 이 저장소 선택
3. `Procfile`을 자동 인식합니다. 배포 후 **Settings → Networking → Generate Domain**으로 공개 URL 생성

---

## 수집 데이터 영구 보관 (외부 무료 Postgres 연결)

무료 호스팅은 재시작·재배포 때 로컬 SQLite가 초기화됩니다. **환경변수 `DATABASE_URL`**에
외부 Postgres 연결 문자열을 넣으면, 그쪽에 저장돼 **재시작에도 데이터가 유지**됩니다.
(`DATABASE_URL`이 없으면 기존처럼 로컬 SQLite로 동작합니다 — 코드 수정 불필요.)

### 1) 무료 Postgres 만들기 (둘 중 하나)
- **Supabase**: https://supabase.com → New project → Project Settings → **Database** →
  **Connection string(URI)** 복사. 형식: `postgresql://postgres:<PW>@db.<ref>.supabase.co:5432/postgres`
- **Neon**: https://neon.tech → 프로젝트 생성 → **Connection string** 복사.
  형식: `postgresql://<user>:<PW>@ep-xxx.neon.tech/neondb?sslmode=require`

### 2) Render에 환경변수 등록
- Render 서비스 → **Environment** → **Add Environment Variable**
  - Key: `DATABASE_URL`
  - Value: 위에서 복사한 연결 문자열 (비밀번호 포함)
- 저장하면 자동 재배포됩니다. 앱이 뜰 때 테이블을 자동 생성(`init_db`)하고, 이후 수집분이
  Postgres에 쌓입니다. (SSL은 자동으로 `sslmode=require` 처리)

> 연결 문자열에는 **DB 비밀번호가 포함**되니, 코드/깃에 넣지 말고 **환경변수로만** 두세요.

---

## ⚠️ 무료 호스팅에서 알아둘 점

- **콜드 스타트**: 무료 플랜은 일정 시간 미사용 시 잠들어, 다시 접속할 때 첫 로딩이
  30초~1분 걸릴 수 있습니다. (공유받은 사람이 처음 열 때 느릴 수 있음)
- **데이터 초기화**: 기본은 SQLite 파일(`data/collector.db`)이라 재배포·재시작 시
  수집 데이터가 초기화됩니다. **`DATABASE_URL`로 외부 Postgres를 연결하면 영구 보관**됩니다
  (위 섹션 참고).
- **크롤러 정확도는 별도 작업**: 배포 서버는 인터넷이 열려 있어 실제 수집이 시도되지만,
  각 사이트 CSS 선택자는 아직 추정값입니다. "수집 실행"을 눌러도 결과가 비거나 일부만
  나올 수 있으며, 실제 HTML에 맞춰 `collector/naver_news.py`·`collector/boards.py`를
  보정해야 안정적으로 수집됩니다.
- **네이버/anti-bot**: 네이버는 데이터센터 IP의 자동 요청을 차단할 수 있습니다.
  필요 시 요청 헤더·간격 조정 또는 헤드리스 브라우저(Playwright) 도입이 필요할 수 있습니다.
- **SPA 사이트**: fairai·projectory 등 JS 렌더링 사이트는 `requests`로 목록이 비면
  Playwright 기반으로 전환해야 합니다. (이 경우 Render는 Docker 배포로 변경 권장)

---

## 자동 수집(외부 크론) + 관리자 전용 버튼

### A. 자동 수집 — 외부 크론 (무료 Render에서 확실한 방법)
무료 인스턴스는 미사용 시 잠들어 내부 스케줄러가 멈춘다. 외부 크론이 주기적으로
`/api/cron`을 호출하면 앱을 깨우며 백그라운드 수집을 시작한다(결과는 DB에 저장).

1. Render → Environment → `CRON_TOKEN` = 아무 긴 문자열(비밀) 추가 → 저장
2. 무료 크론 서비스(예: **cron-job.org**) 가입 → 새 크론잡:
   - URL: `https://ncfoundation-collector.onrender.com/api/cron?token=<CRON_TOKEN>`
   - 주기: 예) 6시간마다 (원하는 대로)
   - 메서드: GET (또는 POST) 둘 다 됨
3. 저장하면 그때부터 자동으로 수집·저장된다(응답은 즉시 200, 수집은 백그라운드 진행).

### B. 상태확인/수집 버튼을 관리자 전용으로
`ADMIN_KEY`를 설정하면 일반 방문자는 조회만, 관리자만 상태확인/수집 실행 가능.

1. Render → Environment → `ADMIN_KEY` = 아무 비밀 문자열 추가 → 저장
2. 관리자는 브라우저에서 **한 번** `https://<사이트>/?admin=<ADMIN_KEY>` 로 접속
   → 그 브라우저에 저장되어 이후 관리자 버튼이 보인다.
3. 일반 방문자는 버튼 없이 **조회만** 가능(수집/상태확인 API도 키 없이는 401).
   - `ADMIN_KEY` 미설정 시에는 기존처럼 누구나 버튼 사용 가능(게이트 없음).
