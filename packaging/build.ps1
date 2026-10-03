# NOTE: saved as UTF-8 with BOM so Windows PowerShell 5 reads the Korean file name correctly.
# Build the Windows 11 distribution (PyInstaller onedir) of ClearWeb.
# usage (repository root, PowerShell):  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Output is named by the program version (clearweb\__init__.py VERSION):
#   dist\<version>\ClearWeb\ClearWeb.exe  and  dist\<version>\ClearWeb-<version>-win64.zip
# An existing build of the same version is never overwritten (its runs\ records may be in use):
# raise VERSION for a new build, or pass -Force to rebuild that version on purpose.
param([string]$Python = ".venv\Scripts\python.exe", [switch]$Force)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$version = (& $Python -c "import clearweb; print(clearweb.VERSION)").Trim()
if (-not $version) { throw "cannot read clearweb.VERSION" }
$Out = "dist\$version"
if ((Test-Path "$Out\ClearWeb") -and -not $Force) {
    throw "$Out\ClearWeb already exists. Raise VERSION in clearweb\__init__.py for a new build (or use -Force to rebuild $version)."
}
Write-Host "version $version -> $Out"

Write-Host "[1/5] dependencies"
& $Python -m pip install -q -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "[2/5] PyInstaller (onedir)"
foreach ($d in @("$Out\ClearWeb", "build\ClearWeb")) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
& $Python -m PyInstaller packaging\clearweb.spec --noconfirm --distpath $Out --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "[3/5] Chromium (headless shell) into $Out\ClearWeb\browsers"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root "$Out\ClearWeb\browsers"
& $Python -m playwright install --only-shell chromium
$code = $LASTEXITCODE
Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH
if ($code -ne 0) { throw "playwright install failed" }

Write-Host "[4/5] documents"
$dist = Join-Path $root "$Out\ClearWeb"
Copy-Item docs\MANUAL.md (Join-Path $dist "사용설명서.md")
Copy-Item LICENSE (Join-Path $dist "LICENSE.txt")
Copy-Item clearweb\web\fonts\LICENSE-Pretendard.txt (Join-Path $dist "LICENSE-Pretendard.txt")
foreach ($f in @("result.json", "result_extra.json", "runs")) { $p = Join-Path $dist $f; if (Test-Path $p) { Remove-Item -Recurse -Force $p } }

Write-Host "[5/5] zip"
$zip = Join-Path $root "$Out\ClearWeb-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $dist -DestinationPath $zip
$size = [math]::Round(((Get-ChildItem -Recurse $dist | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "done: $dist ($size MB), $zip"
