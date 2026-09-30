# 평가용 사이트(eval) — 도립 한빛박물관(가상)

`python -m testsites.server --site eval --port 8851` 로 제공. 페이지를 다시 만들려면 `python testsites/eval/build.py`.
모든 광고 문구·연락처·코드는 가짜 테스트 데이터입니다.

## 작성 방식과 한계
- 탐지기 코드와 dev 사이트를 만든 같은 개발 과정에서 작성함(독립 작성을 시도했으나 작성 에이전트가 콘텐츠 정책으로 중단). **완전히 독립된 평가가 아님.**
- dev와 주제·문구·DOM 구조(BEM식 클래스, dl·figure·table·카드, 중첩 iframe)를 다르게 함. 정답은 탐지기 실행 전에 고정.
- 위치 규칙: 숨긴 광고 문구를 담은 요소. 숨김 스타일이 문구만 담은 감싸는 요소에 있으면 안쪽 문구 요소를 정답으로 봄(n-202). 탐지기와 판단이 갈릴 수 있는 사례이며 의도적으로 포함.

## 페이지(16)
`/`, `/exhibitions.html`, `/about.html`, `/programs.html`(iframe → `widgets/banner.html` → 중첩 `widgets/inner.html`),
`/notice/list-1~3.html`(목록 쪽 이동), `/notice/n-201~207.html`(글·댓글), `/member/logout.html`(방문 금지 함정).

## 정답 25건 (answers.json)
HOMOGLYPH 5 · JAMO 4 · TRANSPARENT 7 · OFFSCREEN 9. 복합 기법 요소 4개(자모+1px, 전각+투명, 그리스 문자+font-size:0, 자모+opacity:0 중첩 iframe).
iframe 1단계 1건, 중첩 iframe 2건, 댓글 2건, 목록 2·3쪽 각 1건, 보이는 위장(전각·키릴·숫자 대체·자모) 5건.

## 결과에 나오면 안 되는 사례
- 도박 예방 배너(1336), 경찰 협약·검거 보도(카지노·텔레그램 언급), 상담 안내.
- 숨겨진 정상 UI: 드롭다운 메뉴, 본문 바로가기, '새 창' 표시(sr-only), FAQ 접힘 답변, 모달, 오프캔버스 메뉴, 비활성 탭.
- 비활성 탭의 뉴스 제목 목록(감사패 지급 / 주주 현금배당 / 사전예약 이벤트가 서로 다른 항목).
- 일반적인 전각 숫자(２０２６년), 키릴 문자 도시명(Москва, Київ).
- 보이는(숨김·위장 없는) 가짜 도박 홍보 댓글 1건(n-204) — 공식 4종 기법이 아니므로 정답 아님.
- 함정 페이지 `/member/logout.html`의 숨긴 광고 — 방문 자체가 오류.
