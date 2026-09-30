# NOTE: saved as UTF-8 with BOM so Windows PowerShell 5 reads the Korean file name correctly.
# Build the Windows 11 distribution (PyInstaller onedir) of ClearWeb.
# usage (repository root, PowerShell):  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
param([string]$Python = ".venv\Scripts\python.exe")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/5] dependencies"
& $Python -m pip install -q -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "[2/5] PyInstaller (onedir)"
foreach ($d in @("dist\ClearWeb", "build\ClearWeb")) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
& $Python -m PyInstaller packaging\clearweb.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "[3/5] Chromium (headless shell) into dist\ClearWeb\browsers"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root "dist\ClearWeb\browsers"
& $Python -m playwright install --only-shell chromium
$code = $LASTEXITCODE
Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH
if ($code -ne 0) { throw "playwright install failed" }

Write-Host "[4/5] documents"
$dist = Join-Path $root "dist\ClearWeb"
Copy-Item docs\MANUAL.md (Join-Path $dist "사용설명서.md")
Copy-Item LICENSE (Join-Path $dist "LICENSE.txt")
Copy-Item clearweb\web\fonts\LICENSE-Pretendard.txt (Join-Path $dist "LICENSE-Pretendard.txt")
foreach ($f in @("result.json", "result_extra.json", "runs")) { $p = Join-Path $dist $f; if (Test-Path $p) { Remove-Item -Recurse -Force $p } }

Write-Host "[5/5] zip"
$version = (& $Python -c "import clearweb; print(clearweb.VERSION)").Trim()
$zip = Join-Path $root "dist\ClearWeb-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $dist -DestinationPath $zip
$size = [math]::Round(((Get-ChildItem -Recurse $dist | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "done: $dist ($size MB), $zip"
