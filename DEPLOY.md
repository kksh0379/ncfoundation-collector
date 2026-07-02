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

## ⚠️ 무료 호스팅에서 알아둘 점

- **콜드 스타트**: 무료 플랜은 일정 시간 미사용 시 잠들어, 다시 접속할 때 첫 로딩이
  30초~1분 걸릴 수 있습니다. (공유받은 사람이 처음 열 때 느릴 수 있음)
- **데이터 초기화**: 저장은 SQLite 파일(`data/collector.db`)이라 재배포·재시작 시
  수집 데이터가 초기화됩니다. (데모/검토용으로는 충분. 영구 보관이 필요하면 추후
  PostgreSQL 같은 외부 DB로 전환)
- **크롤러 정확도는 별도 작업**: 배포 서버는 인터넷이 열려 있어 실제 수집이 시도되지만,
  각 사이트 CSS 선택자는 아직 추정값입니다. "수집 실행"을 눌러도 결과가 비거나 일부만
  나올 수 있으며, 실제 HTML에 맞춰 `collector/naver_news.py`·`collector/boards.py`를
  보정해야 안정적으로 수집됩니다.
- **네이버/anti-bot**: 네이버는 데이터센터 IP의 자동 요청을 차단할 수 있습니다.
  필요 시 요청 헤더·간격 조정 또는 헤드리스 브라우저(Playwright) 도입이 필요할 수 있습니다.
- **SPA 사이트**: fairai·projectory 등 JS 렌더링 사이트는 `requests`로 목록이 비면
  Playwright 기반으로 전환해야 합니다. (이 경우 Render는 Docker 배포로 변경 권장)
