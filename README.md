# ClearWeb — 공공 웹사이트 불법광고 점검 도구

공공 웹사이트에 숨기거나 변형해 심어 둔 불법광고(도박·성인·불법대출 등)를 찾아, 발견 위치와 판단 근거를 보여주는 점검 도구입니다.
「공공 웹사이트 불법광고 탐지 도구 개발 공모전」(행정안전부·한국지능정보사회진흥원) 출품을 목표로 개발 중입니다.

> 현재 상태: 개발 중. Windows 11 개발 환경(Python)에서만 실행을 확인했으며, Windows 실행 파일(배포 패키지)은 아직 없습니다.

## 주요 기능
- **필수 탐지 4종**
  | 기법 | 결과 코드 | 판단 근거 |
  |---|---|---|
  | 닮은꼴 글자 위장 | `HOMOGLYPH` | 전각·키릴·그리스 문자, 숫자로 대체한 글자를 복원했을 때 드러나는 광고 단어 |
  | 자모 분해 | `JAMO` | 분해된 자음·모음을 조합했을 때 드러나는 광고 단어 |
  | 투명 텍스트 | `TRANSPARENT` | 계산된 스타일 기준 opacity 0, 투명 글자색, 배경과 같은 글자색 등 |
  | 화면 밖 은닉 | `OFFSCREEN` | display:none, 0~1px 글자, 문서 밖 좌표, clip, 잘림 영역 등 |
- **광고성 판단과 은닉 판단 분리**: 숨겨져 있다는 사실만으로 광고로 보지 않습니다. 주제어·유도 표현·연락 수단·가입코드를 한 콘텐츠 안에서 함께 확인하고, 예방·보도·교육 문맥은 제외합니다. 서로 다른 목록 항목의 단어는 합치지 않습니다.
- **정확한 위치**: 요소를 하나만 가리키는 CSS 선택자를 만들고 원래 문서에서 다시 조회해 검증합니다. iframe 안은 `iframe[src="절대주소"] >>> 선택자` 형식입니다.
- **하위 페이지 탐색**: 같은 사이트 안에서 본문 링크 우선으로 탐색합니다(기본 깊이 2, 최대 50페이지, 전체 25분). 로그아웃·삭제 같은 상태 변경 링크는 방문하지 않고, 안전한 ‘더보기’만 펼칩니다.
- **로컬 화면**: 127.0.0.1에서만 열리는 점검 화면에서 진행 상황, 탐지 결과, 원문·복원 문구·판단 이유·발견 위치를 확인합니다.
- 외부 AI API 없이 CPU만 사용합니다.

## 결과 파일
| 파일 | 위치 | 내용 |
|---|---|---|
| `result.json` | 프로그램 폴더(개발 환경에서는 저장소 최상위) | 공식 스키마의 적격평가용 결과. 탐지 0건·중단·로딩 실패여도 항상 생성(UTF-8, BOM 없음) |
| `runs/<검사시각>/scan.json` | 저장소 최상위 `runs/` | 화면용 상세 기록(페이지별 상태, 판단 근거 등) |

## 설치 (Windows 11, 개발 환경)
```powershell
python -m venv .venv
.venv\Scripts\pip install playwright fastapi uvicorn pytest
.venv\Scripts\python -m playwright install chromium
```

## 실행
```powershell
# 화면: http://127.0.0.1:8765/ 이 자동으로 열립니다
.venv\Scripts\python -m clearweb

# 화면 없이 검사
.venv\Scripts\python -m clearweb scan https://www.example.go.kr/ [--max-depth 2] [--max-pages 50] [--budget 1500] [--no-follow]
```

## 테스트
정답이 있는 로컬 테스트 사이트와 채점 도구가 포함되어 있습니다. 채점은 공식 규칙대로 URL·요소·기법이 모두 맞아야 정답으로 셉니다.

```powershell
# 1) 테스트 사이트 켜기 (dev: 은닉 4종·iframe / crawl: 하위 페이지·게시판·함정 링크 / fp: 오탐 회귀)
.venv\Scripts\python -m testsites.server --site dev   --port 8811
.venv\Scripts\python -m testsites.server --site crawl --port 8821
.venv\Scripts\python -m testsites.server --site fp    --port 8831

# 2) 검사 후 채점 (예: dev)
.venv\Scripts\python -m clearweb scan http://127.0.0.1:8811/
.venv\Scripts\python tools\score.py --answers testsites\dev\answers.json --result result.json --base http://127.0.0.1:8811/

# 3) 단위 테스트
.venv\Scripts\python -m pytest -q tests
```
테스트 사이트는 개발자가 직접 만든 것이라, 이 점수는 일반화 성능의 근거가 아닙니다.

## 프로젝트 구조
```
clearweb/          탐지 도구 본체
  collector.js       페이지 안에서 요소·계산 스타일·위치 수집
  selector.js        유일한 CSS 선택자 생성과 재검증
  textnorm.py        자모 조합·닮은꼴 문자 복원
  detect.py          광고성 판단과 기법 판정
  crawl.py           탐색 범위·우선순위·제외 규칙
  scanner.py         브라우저 제어, 페이지·프레임 순회, 결과 저장
  result_writer.py   result.json 생성과 스키마 검사
  server.py, web/    로컬 화면
  rules/ad_rules.json  광고성 판단 규칙
testsites/         정답이 있는 로컬 테스트 사이트
tools/score.py     공식 규칙 기반 채점 도구
tests/             단위 테스트
docs/              진행 상황(PROGRESS.md), 규격 메모와 가정(SPEC_NOTES.md)
```

## 브랜치
- `main`: 필수 4종 탐지, 하위 페이지 탐색, 로컬 화면, 테스트 사이트
- `feature/image-ocr`: 진입 페이지 이미지 속 글자 검사(선택 기능, Windows 내장 OCR), 추가 의심 광고 목록, 사용설명서. 검토 중이며 아직 `main`에 합치지 않았습니다.

## 알려진 한계
- 공식 4종 기법 없이 그대로 보이는 광고 문구는 `result.json`에 넣지 않습니다(공식 규격 기준).
- 스크립트 함수로만 이동하는 게시판 링크는 찾지 못할 수 있습니다.
- 보안 확인(봇 차단) 화면을 띄우는 사이트는 검사할 수 없으며, 우회하지 않습니다.
- 공식 규격에서 확정되지 않은 부분의 가정은 `docs/SPEC_NOTES.md`에 정리했습니다.

## 라이선스
[MIT](LICENSE). 화면 글꼴 Pretendard는 SIL Open Font License를 따릅니다(`clearweb/web/fonts/LICENSE-Pretendard.txt`).
