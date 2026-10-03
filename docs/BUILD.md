# 실행/빌드 매뉴얼

## 배포본 구성 (PyInstaller onedir)
```
ClearWeb\
  ClearWeb.exe            실행 파일 (더블클릭)
  _internal\              파이썬 런타임, 라이브러리, 화면 파일, 규칙 파일, 페이지 수집 스크립트, Playwright 드라이버
  browsers\               Chromium(headless shell) — 검사에 쓰는 브라우저
  사용설명서.md, LICENSE.txt, LICENSE-Pretendard.txt
  result.json             (검사 후 생성) 적격평가용 결과
  result_extra.json       (이미지 검사를 켠 검사 후 생성) 추가 탐지 결과
  runs\                   (검사 후 생성) 화면용 검사 기록과 이미지 미리보기
```
크기: 폴더 약 420MB, zip 약 176MB(2026-09-30 빌드 기준). Python 설치가 필요 없도록 런타임을 포함합니다.

## 빌드 환경
- Windows 11, Python 3.13, 저장소 최상위에 가상환경 `.venv`
- 인터넷 연결(의존성과 Chromium 내려받기 — 빌드 때만 필요, 실행 때는 필요 없음)

## 빌드 방법
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```
출력은 프로그램 버전(`clearweb\__init__.py`의 `VERSION`)으로 이름이 정해집니다: `dist\<버전>\ClearWeb\ClearWeb.exe`, `dist\<버전>\ClearWeb-<버전>-win64.zip` (예: `dist\0.1.1\`). 같은 버전 폴더가 이미 있으면 덮어쓰지 않고 멈춥니다(그 안의 `runs\` 실행 기록 보호). 동작이 바뀐 새 빌드는 `VERSION`을 올린 뒤 빌드하고, 같은 버전을 일부러 다시 만들 때만 `-Force`를 붙입니다.

`packaging\build.ps1`이 하는 일:
1. 의존성과 PyInstaller 설치
2. `packaging\clearweb.spec`으로 onedir 빌드 → `dist\<버전>\ClearWeb\`
3. `dist\<버전>\ClearWeb\browsers\`에 Chromium(headless shell) 설치 (`PLAYWRIGHT_BROWSERS_PATH`)
4. 사용설명서·라이선스 복사, 남아 있는 결과 파일 제거
5. `dist\<버전>\ClearWeb-<버전>-win64.zip` 생성

주의: `build.ps1`은 한글 파일명 때문에 UTF-8(BOM 포함)으로 저장되어 있습니다. BOM 없이 다시 저장하면 Windows PowerShell 5에서 문서 복사 단계가 실패합니다.

## 포함 항목과 이유 (`packaging\clearweb.spec`)
- 데이터: `clearweb/collector.js`, `clearweb/selector.js`, `clearweb/rules/`, `clearweb/web/`(화면·글꼴)
- 숨은 의존성: `uvicorn` 하위 모듈, `winrt` 하위 모듈(Windows 내장 OCR 연결 — 코드에서 늦게 불러오므로 명시), `PIL`
- 브라우저: 실행 시 `ClearWeb.exe` 옆 `browsers\` 폴더를 먼저 사용. 없으면 PC에 설치된 Microsoft Edge로 대체하고 검사 기록에 남김.

## 속도 비교 (개발용, `tools/replay_bench.py`)
실제 사이트는 실행마다 응답 속도가 달라 설정 변경의 효과를 재기 어렵습니다. 한 번 녹화한 응답으로 같은 페이지들을 다시 검사해 비교합니다(녹화본은 저장소 밖에 두고 커밋하지 않음).
```powershell
.venv\Scripts\python tools\replay_bench.py record https://www.example.go.kr D:\rec\site --pages 200
.venv\Scripts\python tools\replay_bench.py replay D:\rec\site --label 1_before --repeat 2 --expand legacy
.venv\Scripts\python tools\replay_bench.py replay D:\rec\site --label 2_after --repeat 2
.venv\Scripts\python tools\replay_bench.py compare D:\rec\site
```

## 실행 확인 절차 (배포 전 점검)
1. zip을 개발 폴더가 아닌 곳에 풀기
2. `ClearWeb.exe` 더블클릭 → 콘솔 창에 화면 주소 표시, 기본 브라우저가 열림(8765가 사용 중이면 다음 빈 포트)
3. 로컬 테스트 사이트 또는 점검 대상 주소로 검사 → `ClearWeb.exe`와 같은 폴더에 `result.json` 생성 확인
4. '검사 설정'에서 이미지 속 글자 검사를 켜고 검사 → `result_extra.json` 생성 확인, 다시 끄고 검사 → 이전 `result_extra.json`이 남지 않는지 확인
5. 검사 중 '중단' → '중단됨' 표시와 `result.json` 생성 확인
6. 화면 없이: `ClearWeb.exe scan <주소> [--no-follow] [--ocr]`

## 검증 기록 (2026-09-30, 커밋 094f743 빌드)
- 환경: 개발 PC(Windows 11 Pro 10.0.26200, 한국어). zip을 `%TEMP%\ClearWebTest-…\ClearWeb`에 풀고, PATH에서 Python 관련 경로를 뺀 상태로 실행.
- 확인: 인자 없이 실행(더블클릭과 같은 방식) → 화면 서버 시작(8765 사용 중이어서 8766), 결과 폴더 = 실행 파일 폴더. 텍스트 검사 17건·`result.json`(BOM 없음), 이미지 검사 켬 → 하위 페이지·iframe 이미지 포함 의심 15건·`result_extra.json`, 이미지 검사 끄고 재검사 → 이전 `result_extra.json` 제거, 검사 중 중단 1초 내 종료·결과 파일 생성, `ClearWeb.exe scan` 종료 코드 0. 동봉 Chromium 사용(Edge 대체 없음), 배포본에서 Windows OCR 사용 가능.
- **미검증**: Python이 설치되지 않은 별도 Windows PC(이 PC에 Windows Sandbox가 없어 확인 불가 — PATH에서 Python을 뺀 실행은 부분 확인일 뿐임), 한국어 OCR 구성 요소가 없는 Windows(개발 환경에서 '사용 불가' 상황을 흉내 내어 텍스트 검사가 계속되는 것만 확인), 다른 백신 제품에서의 오진 여부.
- 확인(2026-10-03, 사용자 데스크톱, 0.1.8): 내려받은 zip을 풀어 탐색기에서 더블클릭하면 SmartScreen "Windows의 PC 보호" 창이 뜸(코드 서명 없음). '추가 정보 → 실행' 후 정상 실행, 이후 같은 파일은 다시 묻지 않음. 사용설명서 첫 부분에 zip '차단 해제' / '추가 정보 → 실행' 안내 추가, 서명 필요 여부는 주최 측 질문으로 남김.
- 알려진 문제: 화면 없이 실행할 때 출력을 다른 프로그램으로 넘기면 한글 일부가 깨져 보일 수 있음(결과 파일에는 영향 없음).
