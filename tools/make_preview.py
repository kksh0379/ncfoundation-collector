"""결과물 미리보기 생성기.

실제 크롤링은 네트워크 환경에서만 가능하므로, UI가 어떻게 보이는지 확인할 수
있도록 샘플 데이터를 넣은 미리보기를 만든다.
- preview/preview.html : 단일 파일 인터랙티브 미리보기 (탭/펼치기 동작, 서버 불필요)
- preview/preview.pdf  : 모바일 폭으로 렌더한 스냅샷 (두 탭 모두 표시)
"""
import html
import os

BASE = os.path.dirname(os.path.dirname(__file__))
CSS = open(os.path.join(BASE, "static", "css", "style.css"), encoding="utf-8").read()
OUT = os.path.join(BASE, "preview")
os.makedirs(OUT, exist_ok=True)

# ----------------------------- 샘플 데이터 -----------------------------
NEWS = [
    {
        "title": "엔씨문화재단, 장애인 의사소통 보조 서비스 '나의AAC' 신규 기능 공개",
        "published_at": "2026-05-28 10:20",
        "author": "연합뉴스",
        "url": "https://n.news.naver.com/article/001/0000000001",
        "content": "엔씨문화재단은 28일 장애인 의사소통 보조 서비스 '나의AAC'의 신규 기능을 공개했다고 밝혔다. 이번 업데이트로 사용자는 더 빠르게 상징 카드를 검색하고 문장을 구성할 수 있다. 재단 관계자는 \"디지털 접근성 향상에 지속적으로 기여하겠다\"고 말했다.",
    },
    {
        "title": "NC문화재단 '프로젝토리', 청소년 미디어아트 전시 개최",
        "published_at": "2026-05-20 14:05",
        "author": "뉴시스",
        "url": "https://n.news.naver.com/article/003/0000000002",
        "content": "엔씨문화재단이 운영하는 창작공간 프로젝토리가 청소년을 대상으로 한 미디어아트 전시를 개최한다. 전시는 다음 달까지 이어지며 누구나 무료로 관람할 수 있다. 참여 청소년들이 직접 기획·제작한 작품 20여 점이 전시된다.",
    },
    {
        "title": "FAIR AI 센터, 인공지능 윤리 가이드라인 보고서 발간",
        "published_at": "2026-04-30 09:00",
        "author": "전자신문",
        "url": "https://n.news.naver.com/article/030/0000000003",
        "content": "엔씨문화재단 산하 FAIR AI 센터가 인공지능 윤리 가이드라인 보고서를 발간했다. 보고서는 AI 개발 단계별 점검 항목과 사례를 담고 있으며, 개발자와 기획자가 실무에 바로 활용할 수 있도록 구성됐다.",
    },
]

BOARDS = [
    {
        "service": "나의AAC", "category": "소식",
        "title": "나의AAC 5월 정기 업데이트 안내",
        "published_at": "2026-05-27", "author": "운영팀",
        "url": "https://www.myaac.or.kr/info/announcement.do?id=101",
        "content": "5월 정기 업데이트가 적용되었습니다. 상징 카드 검색 속도가 개선되고 신규 카테고리가 추가되었습니다.",
    },
    {
        "service": "나의AAC", "category": "커뮤니티",
        "title": "사용 후기를 공유해요",
        "published_at": "2026-05-15", "author": "이OO",
        "url": "https://www.myaac.or.kr/info/community.do?id=88",
        "content": "아이와 함께 나의AAC를 사용한 지 두 달이 되었어요. 의사소통이 한결 수월해졌습니다.",
    },
    {
        "service": "FAIR AI", "category": "공지사항",
        "title": "FAIR AI 2026 상반기 워크숍 참가자 모집",
        "published_at": "2026-05-10", "author": "FAIR AI",
        "url": "https://fairai.or.kr/about/notices/12",
        "content": "AI 윤리에 관심 있는 분들을 위한 상반기 워크숍 참가자를 모집합니다. 신청은 홈페이지에서 가능합니다.",
    },
    {
        "service": "FAIR AI", "category": "인사이트",
        "title": "임베디드 윤리란 무엇인가",
        "published_at": "2026-04-22", "author": "연구팀",
        "url": "https://fairai.or.kr/embedded-ethics/insight-plus/7",
        "content": "임베디드 윤리는 AI 개발 과정 안에 윤리적 검토를 내재화하는 접근입니다. 본 글에서는 그 개념과 적용 사례를 소개합니다.",
    },
    {
        "service": "대표 홈페이지", "category": "재단소식",
        "title": "엔씨문화재단 2026년 사업 계획 발표",
        "published_at": "2026-03-31", "author": "재단",
        "url": "https://www.ncfoundation.or.kr/community/55",
        "content": "올해 재단은 디지털 접근성, AI 윤리, 청소년 창작 지원 세 축을 중심으로 사업을 운영합니다.",
    },
    {
        "service": "프로젝토리", "category": "공지",
        "title": "프로젝토리 5월 운영 일정 안내",
        "published_at": "2026-05-01", "author": "프로젝토리",
        "url": "https://www.projectory.or.kr/news/notice-list/33",
        "content": "5월 운영 일정 및 예약 방법을 안내드립니다. 가족 단위 방문도 환영합니다.",
    },
    {
        "service": "프로젝토리", "category": "갤러리",
        "title": "4월 창작 워크숍 현장 스케치",
        "published_at": "2026-04-18", "author": "프로젝토리",
        "url": "https://www.projectory.or.kr/news/gallery-list/21",
        "content": "지난 4월 진행된 창작 워크숍의 생생한 현장을 사진으로 담았습니다.",
    },
]

SERVICES = sorted({b["service"] for b in BOARDS})


def esc(s):
    return html.escape(s or "")


def card(item, badge=None):
    meta = []
    if badge:
        meta.append(f'<span class="badge">{esc(badge)}</span>')
    meta.append(esc(item.get("published_at") or "작성일 미상"))
    if item.get("author"):
        meta.append(esc(item["author"]))
    url = item.get("url")
    link = f'<a href="{esc(url)}" target="_blank" rel="noopener">원문 보기 ↗</a>' if url else ""
    return f"""    <li class="card">
      <h3 class="card-title">{esc(item.get('title'))}</h3>
      <div class="card-meta">{' · '.join(meta)}</div>
      <div class="card-body">{esc(item.get('content'))}</div>
      <div class="card-actions"><button class="toggle">본문 보기</button>{link}</div>
    </li>"""


news_cards = "\n".join(card(n) for n in NEWS)
board_cards = "\n".join(card(b, badge=b["category"]) for b in BOARDS)
service_options = "\n".join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in SERVICES)

TOGGLE_JS = """
<script>
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('panel-'+t.dataset.tab).classList.add('active');
}));
document.querySelectorAll('.toggle').forEach(b=>b.addEventListener('click',()=>{
  const c=b.closest('.card'); c.classList.toggle('open');
  b.textContent=c.classList.contains('open')?'접기':'본문 보기';
}));
document.querySelectorAll('.btn-collect').forEach(b=>b.addEventListener('click',()=>{
  alert('미리보기에서는 실제 수집이 동작하지 않습니다.\\n로컬에서 python app.py 실행 후 사용하세요.');
}));
</script>
"""


def page(extra_css="", interactive=True):
    body_class = "" if interactive else " class='snapshot'"
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>엔씨문화재단 콜렉터 · 미리보기</title>
<style>{CSS}
{extra_css}</style></head>
<body{body_class}>
  <header class="app-header"><h1>엔씨문화재단 콜렉터</h1></header>
  <nav class="tabs">
    <button class="tab active" data-tab="news">뉴스 · 기사</button>
    <button class="tab" data-tab="boards">서비스 게시판</button>
  </nav>
  <section class="panel active" id="panel-news">
    <div class="toolbar">
      <span class="hint">네이버 뉴스 · 엔씨문화재단/NC문화재단 · 2026.01.01~</span>
      <button class="btn-collect">수집 실행</button>
    </div>
    <div class="result-msg">수집 3건 · 신규 3건 저장 · 중복 1건 제외 (예시)</div>
    <ul class="list">
{news_cards}
    </ul>
  </section>
  <section class="panel" id="panel-boards">
    <div class="toolbar">
      <select><option>전체 서비스</option>
{service_options}
      </select>
      <button class="btn-collect">수집 실행</button>
    </div>
    <div class="result-msg">수집 7건 · 신규 7건 저장 · 중복 0건 제외 (예시)</div>
    <ul class="list">
{board_cards}
    </ul>
  </section>
{TOGGLE_JS if interactive else ''}
</body></html>"""


# 1) 인터랙티브 미리보기
with open(os.path.join(OUT, "preview.html"), "w", encoding="utf-8") as f:
    f.write(page(interactive=True))

# 2) 스냅샷용: 두 탭을 모두 펼쳐 보여주고, 본문도 펼친 상태로 렌더
snapshot_css = """
.snapshot .panel { display: block !important; }
.snapshot .panel + .panel { border-top: 8px solid #e9ecf2; margin-top: 8px; }
.snapshot .card-body { max-height: none !important; margin-top: 8px; }
.snapshot .tabs { display: none; }
.snapshot .panel::before {
  display: block; font-weight: 700; font-size: 14px; color: #2d6cdf;
  padding: 4px 0 10px;
}
#panel-news::before { content: "▌탭1 · 뉴스 · 기사"; }
#panel-boards::before { content: "▌탭2 · 서비스 게시판"; }
@page { size: 412px 1900px; margin: 0; }
body { width: 412px; }
"""
snapshot_html = page(extra_css=snapshot_css, interactive=False)
with open(os.path.join(OUT, "snapshot.html"), "w", encoding="utf-8") as f:
    f.write(snapshot_html)

# PDF 렌더 (weasyprint)
try:
    from weasyprint import HTML

    HTML(string=snapshot_html).write_pdf(os.path.join(OUT, "preview.pdf"))
    print("PDF 생성 완료:", os.path.join(OUT, "preview.pdf"))
except Exception as e:  # noqa: BLE001
    print("PDF 생성 건너뜀:", e)

print("HTML 생성 완료:", os.path.join(OUT, "preview.html"))
