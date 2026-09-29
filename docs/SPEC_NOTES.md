# 규격 메모 (공식 모집요강 기준 + 미확정 부분의 가정)

출처: NIA 공고 첨부 `공공_웹사이트_불법광고_탐지_도구_개발_공모전_모집요강.hwpx` (2026-09-28 확인)

## 공식 규격에서 확인한 것
- 입력: 진입 URL 1개. 처리 시간 = URL 입력·"탐지 시작" → "탐지 완료". 상한 30분(초과 시 시간 항목 0점).
- result.json: 실행 파일이 있는 폴더에 생성(다른 곳이면 사용설명서 첫 장에 경로 명시). UTF-8 BOM 없음.
- 최상위 `meta`, `findings`만. 0건이어도 `findings: []`.
- meta: topic("TOPIC"), entry_url, started_at, finished_at(ISO 8601), elapsed_sec(number), tool_version(선택).
- findings: id(파일 내 유일), url, is_violation(boolean), location, evidence_text(정규화 전 원문), technique(HOMOGLYPH/JAMO/TRANSPARENT/OFFSCREEN 중 1개).
- 검출 단위 url+location+technique. 복합 기법은 기법별 객체. 같은 요소·같은 기법 여러 문자열은 1건.
- 채점: 세 필드 모두 일치해야 정답. URL은 끝 `/`, http/https, `#` 이하 무시. 쿼리 값이 다르면 다른 페이지, 순서만 다르면 같은 페이지.
- location은 같은 요소를 가리키면 표기 무관. 2개 이상 가리키면 오답. iframe은 `iframe[src="절대주소"] >>> 선택자`, 중첩 시 반복.
- 정확도 = 전체 탐지 건수 대비 정탐 비율 → 오탐은 직접 감점.
- 추가 유형은 result_extra.json, technique "ETC" + extra_finding.
- 접수 마감 2026-10-23 18시, 적격평가 10/26~28.

## 가정 (규격에 명시되지 않음)
1. is_violation: 광고로 판정한 항목만 출력하므로 항상 true.
2. 은닉 스타일을 가진 상위 요소가 광고 문구만 담고 있으면 그 상위 요소(은닉 루트)를 location으로 사용. 다른 내용도 섞여 있으면 광고 문구를 담은 최소 요소. — 공식 원문에 근거 없음(정의는 "불법광고가 검출된 요소의 위치"뿐, 예시는 숨김 요소=문구 요소인 경우). 주최 측 확인 요청 중(docs/ORGANIZER_QUESTIONS.md). eval 정답은 문구 요소 기준이라 이 차이로 누락·오탐 각 1건.
3. 복합 기법은 같은 location으로 통일하고 기법별 객체 생성.
4. iframe 내부 발견은 부모 페이지 URL로 기록. iframe src 페이지를 별도 페이지로 중복 보고하지 않음.
5. url은 리다이렉트 이후 최종 주소(fragment 제거). 쿼리는 모두 보존.
6. visibility:hidden → TRANSPARENT. clip·sr-only(1px+overflow)·음수 text-indent·문서 영역 밖 좌표·display:none·font-size ≤1px → OFFSCREEN. 문서 안에서 스크롤 아래에 있는 것은 은닉 아님.
7. 은닉 기법 없이 그대로 보이는 광고는 공식 기법 코드가 없으므로 result.json에 넣지 않음.
8. 탐색 범위는 진입 주소와 같은 사이트(www 유무, 하위 도메인 포함). 진입 페이지가 다른 호스트로 리다이렉트되면 그 호스트도 범위에 추가.
9. iframe의 src 페이지는 별도 페이지로 방문하지 않음(부모 페이지 + `>>>`로만 보고). iframe 안의 링크는 따라감.
10. 리다이렉트 결과가 이미 점검한 페이지면 다시 보고하지 않음(중복으로 기록).
11. 쿼리는 모두 보존하되, 내용이 바뀌지 않는 것이 확실한 추적 파라미터(utm_*, fbclid, gclid, msclkid 등 crawl.TRACKING)만 탐색 시 제거함. 그 밖의 추가 파라미터(예: t=cv, page=1)가 붙은 같은 글은 공식 규칙상 다른 페이지이므로 버리지 않고, 다른 페이지를 먼저 검사한 뒤 한도가 남으면 방문함. 같은 글이 여러 주소로 방문되면 같은 요소가 URL별로 보고될 수 있음(정답셋 기준 미확인).
12. '글쓰기' 같은 입력 양식 페이지는 GET으로 열람만 함(제출하지 않음). 로그아웃·삭제 등 주소에 상태 변경 단어가 있는 링크는 방문하지 않음.
13. 목록의 다음 쪽 이동(page, pageIndex 등만 다른 주소)은 깊이를 늘리지 않음.
14. 목록 항목·카드처럼 독립된 콘텐츠의 문구는 하나의 광고 근거로 합치지 않음. 한 블록 안에서 인라인 요소로 나뉜 문장은 합쳐서 판단하고, 그 블록을 location으로 사용.
15. 이미지 속 글자(OCR)는 필수 4종 기법이 아니므로 result.json에 넣지 않음. 모집요강 붙임1·3·4에 따라 추가 탐지는 result_extra.json(technique "ETC", extra_finding)에 기록 가능하므로, 이미지 한 문구 영역만으로 광고 기준을 충족한 '광고 의심'만 기록. location은 img 요소 선택자, evidence_text는 "OCR 추출문: … (이미지: 주소)". 공식 예시에 is_violation이 없어 extra에도 넣지 않음.
16. OCR 출력의 자모·닮은꼴 형태는 OCR 오인식일 수 있어 JAMO/HOMOGLYPH로 판정하지 않음.
17. 도메인은 최상위 도메인 목록 없이 형식으로 인식하되 소문자 주소만 인정(문장 속 "Mr.Kim" 같은 오인 방지). 대문자로만 쓴 도메인(BET365.COM)은 연락 수단으로 인식하지 못함.
18. 경로형 목록 쪽 이동(list-2.html, /page/3/)은 링크가 페이지 이동 영역(paging·pagination 등 이름, 또는 숫자 링크 묶음) 안에 있고 같은 목록 경로에서 숫자만 다를 때만 깊이를 늘리지 않음. 목록별 최대 10쪽까지.
