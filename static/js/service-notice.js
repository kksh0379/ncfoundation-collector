/* 이용 전 안내: 방문(세션)당 1회만 표시. 외부 원문 링크에서 돌아올 때(iOS는 pageshow가
   재발생) 반복해서 뜨던 문제를 막기 위해 세션 동안 '이미 표시' 여부를 기억한다.
   세션 저장이라 탭/앱을 닫았다 새로 열면(진짜 새 방문) 다시 표시된다. */
(() => {
  const notice = document.getElementById("service-notice");
  if (!notice) return;
  const root = document.documentElement;
  const KEY = "svc-notice-shown";
  let shown = false;
  try { shown = sessionStorage.getItem(KEY) === "1"; } catch (e) { /* 프라이빗 모드 등 */ }
  const markShown = () => { shown = true; try { sessionStorage.setItem(KEY, "1"); } catch (e) { /* noop */ } };
  let previousFocus;
  const openNotice = () => {
    if (notice.open || shown) return;   // 세션 내 1회만 — 뒤로가기·bfcache 복귀 시 재표시 안 함
    markShown();
    previousFocus = document.activeElement;
    notice.showModal();
    root.classList.add("service-notice-open");
    document.getElementById("service-notice-title").focus();
  };
  notice.addEventListener("cancel", event => event.preventDefault());
  notice.addEventListener("close", () => {
    root.classList.remove("service-notice-open");
    if (previousFocus && previousFocus !== document.body && previousFocus.isConnected) {
      previousFocus.focus({preventScroll: true});
    }
  });
  window.addEventListener("pageshow", openNotice);  // 세션 플래그로 가드되어 중복 표시되지 않음
  openNotice();
})();
