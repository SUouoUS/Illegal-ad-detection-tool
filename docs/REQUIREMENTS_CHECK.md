# 공모전 요구사항 점검표 (모집요강 붙임1~4 대비)

작성: 2026-10-12, 대상 버전 0.2.2. 판정: ✅ 구현·확인, ⚠️ 구현했으나 확인 범위가 제한됨, ❌ 미구현.

## 붙임1 주제 상세 내용
| 요구사항 | 판정 | 구현 위치 | 확인 방법 |
|---|---|---|---|
| URL 1개 입력 → 하위 페이지 자동 탐색 | ✅ | `crawl.py`(같은 사이트 범위, 깊이·페이지·시간 한도, 우선순위, 목록 쪽 이동) | `testsites/crawl` 10/10, 실제 공공 사이트 검사 |
| 게시글·댓글 영역 | ✅ | 게시판 글 링크 추적, 스크립트로 불러오는 댓글은 '댓글 더보기' 등 안전한 버튼 클릭 후 수집(`scanner._expand`) | `testsites/crawl`의 댓글 광고, `test_expand_learning` |
| 페이지에 삽입된 iframe 내부(중첩 포함) | ✅ | 프레임 순회(`scanner._walk`), 위치 `iframe[src="절대주소"] >>> …` | `testsites/dev`(2단계 iframe), 공식 예시 사이트 |
| 탐지 결과 안내 화면 | ✅ | 로컬 화면(127.0.0.1): 목록·상세(원문·복원 문구·의심 이유·위치·기술 상세) | 화면 직접 확인 |
| **결과 안내·관리 기능**(본심사 실무 활용성) | ✅ (0.2.1, 0.2.2) | 검토 표시(광고 확인·오탐·미검토)+메모, 검사 기록 목록, CSV 내보내기, 인쇄용 보고서, 이전 검사 대비 신규·사라짐·확인 못 함(`manage.py`, 화면) | `tests/test_manage.py`, `tests/test_compare.py`, 화면 조작 확인 |
| 적격평가용 result.json | ✅ | `result_writer.py` | 아래 붙임4 |
| 추가 유형 → result_extra.json 별도 생성 | ✅ (0.2.1, 0.2.2 보완) | `VISIBLE_AD`(숨김 없이 노출된 광고 문구), `SEO_SPAM`(제목·meta·alt 등에만 있는 광고 문구), `HIDDEN_LINK`(숨겨진 광고 사이트 링크), `IMAGE_TEXT_AD`(이미지 속 글자, 선택) | `tests/test_scan_state.py`, `tests/test_extra_types.py`, `tests/test_spec_example.py` |
| 결과 파일은 구동 파일 폴더에 생성 | ✅ | `paths.result_dir()`(배포본: `ClearWeb.exe` 폴더) | 배포본 실행 확인(PATH에서 Python 제외) |
| 필수 4종: 닮은꼴 글자(키릴·전각·숫자 등) | ✅ | `textnorm.py`(닮은꼴 표, 전각, 숫자 대체), `detect.obfuscation` | 공식 예시 `ｍｅｇａ－ＢＥＴ`, dev·eval 사이트 |
| 필수 4종: 자모 분해 | ✅ | `textnorm.py` 자모 조합 | 공식 예시 `ㅋㅏㅈㅣㄴㅗ…` |
| 필수 4종: 투명 텍스트(배경색과 같은 색, opacity:0, color:transparent) | ✅ | `collector.js`(누적 투명도, 실제 배경색 대비, transparent, text-fill) | dev·eval·공식 예시 |
| 필수 4종: 화면 밖 은닉(font-size 0~1px, left:-9999px, display:none) | ✅ | `collector.js`(display:none, 글자 크기 ≤1px, 문서 밖 좌표, clip, 0크기, text-indent) | dev·eval·공식 예시 |

## 붙임2 평가기준
| 항목 | 대응 | 판정 |
|---|---|---|
| 정답 검출 개수(45) | 4종 탐지 + 넓은 크롤링(댓글·iframe·목록 이동·이어서 검사) | ⚠️ 비공개 테스트 사이트 성능은 알 수 없음. 자체 사이트는 탐지기와 같은 사람이 만들어 회귀 검증일 뿐 |
| 정답 추출 정확도(35, 오탐 감점) | 광고 판정과 은닉 판정 분리, 예방·보도 문맥 제외, 실제 사이트 오탐 사례별 수정, 위치 유일성 검증 | ✅/⚠️ 실제 공공 사이트에서 발견된 오탐은 모두 수정·회귀 테스트 |
| 검출 시간(20, 30분 상한) | 기본 시간 한도 25분(최대 29분 권장 안내), 시간 한도 도달 시 반드시 결과 저장, 동시 처리 자동 조절, 불필요한 클릭 학습 | ✅ |
| 실무 활용성(20) | 근거·위치 상세, 실패 사유 분류, 검토·메모·검사 기록·CSV·보고서 | ✅ |
| 도입 가능성(10) | 설치 없는 배포본, 외부 API·서버·GPU 없음(비용 0, 공고의 GPU 없는 평가 PC 조건과 맞음), 127.0.0.1 전용 | ⚠️ Python 없는 별도 PC 검증 전 |
| 창의성(20) | 추가 유형 4종(노출 광고, 검색엔진·속성 문구, 숨겨진 광고 링크, 이미지 속 글자), 이전 검사 비교, 녹화·재생 측정, 자동 동시 처리 | ✅ |

## 붙임3 평가 환경
| 요구사항 | 판정 | 내용 |
|---|---|---|
| Windows 11, GPU 없는 일반 PC, CPU·RAM만 | ✅ | GPU·모델 추론 없음. 규칙 기반 + Windows 내장 OCR |
| 외부 서버·AI API 사용 시 키·서비스명 기재 | 해당 없음 | 사용하지 않음(사용설명서에 명시) |
| 입력: 진입 URL 1개 | ✅ | 화면 입력란 / `ClearWeb.exe scan <URL>` |
| result.json 필수, result_extra.json 별도 | ✅ | 검사마다 둘 다 생성 |

## 붙임4 result.json 스키마
| 요구사항 | 판정 | 확인 |
|---|---|---|
| 파일명 result.json, UTF-8(BOM 없음), JSON | ✅ | `test_spec_example`(BOM 검사), `result_writer.validate` |
| 최상위 meta + findings | ✅ | 스키마 검증 함수 |
| 0건이어도 findings: [] | ✅ | 0건·실패·중단 시에도 생성(`test_scan_state`) |
| meta: topic·entry_url·started_at·finished_at(ISO 8601)·elapsed_sec(number)·tool_version | ✅/⚠️ | topic은 작성 예시대로 "TOPIC". 넣을 값이 따로 있는지 주최 측 확인 요청(`docs/ORGANIZER_QUESTIONS.md`) |
| 1건 = url + location + technique, 기법별 별도 객체, 같은 요소·같은 기법은 1건 | ✅ | 중복 제거, 복합 기법은 같은 위치로 기법마다 |
| id 고유, is_violation boolean, evidence_text 원문 그대로 | ✅ (0.2.2 수정) | 줄바꿈·공백 합치기와 500자 자르기를 없애고 원문을 그대로 기록(앞뒤 공백만 제거) |
| location: 태그.클래스 > …, 클래스 없으면 nth-of-type, 속성값 큰따옴표, iframe `>>>`, 상대 src는 절대 주소 | ✅ | 수집한 문서에서 유일성 재검증, 공식 예시 4건 위치 일치 |
| result_extra: technique "ETC" + extra_finding | ✅ | `validate_extra`, 예시대로 is_violation 없음 |
| URL 비교 규칙(끝 /, http/https, #, 쿼리 순서) | ✅ | 쿼리 값은 지우지 않음(추적용·세션 값만 중복 판단에서 제외), 채점 도구 `tools/score.py`가 같은 규칙 |

## 남은 확인 사항
- 위치 기준(숨김이 적용된 상위 요소 vs 문구를 담은 요소) 주최 측 답변 대기 — GitHub 이슈 #38
- `meta.topic`에 넣을 값 주최 측 확인 요청
- Python이 설치되지 않은 별도 PC에서 배포본 실행
- 제3자가 만든 새 평가 자료로 실제 성능 측정
