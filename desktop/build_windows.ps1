# Build the Windows app on Windows (GitHub's Windows runner, or any PC with Python 3.13,
# MSYS2's FriBiDi and Inno Setup 6):
#   desktop\dist\Reel Framer\Reel Framer.exe   the app folder
#   desktop\dist\ReelFramer-portable.zip       that folder zipped: unzip and run, no install
#   desktop\dist\ReelFramer-Setup.exe          per-user installer (no admin rights needed)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path .venv-win\Scripts\python.exe)) { python -m venv .venv-win }
$py = ".venv-win\Scripts\python.exe"
& $py -m pip install --quiet --prefer-binary -r requirements.txt -r desktop\requirements.txt
if ($LASTEXITCODE) { throw "pip install failed" }

Remove-Item -Recurse -Force desktop\build, desktop\dist -ErrorAction SilentlyContinue
Write-Host "1/4 ffmpeg and FriBiDi"
& $py desktop\fetch_windows_deps.py desktop\build
if ($LASTEXITCODE) { throw "fetching ffmpeg / FriBiDi failed" }
Write-Host "2/4 icon"
& $py desktop\make_icon.py desktop\build\ReelFramer.ico
if ($LASTEXITCODE) { throw "icon failed" }
Write-Host "3/4 app"
& $py -m PyInstaller --noconfirm --clean --log-level WARN --distpath desktop\dist --workpath desktop\build\pyinstaller desktop\reel_framer.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

Write-Host "4/4 zip and installer"
$env:REEL_FRAMER_VERSION = (Get-Content desktop\VERSION -Raw).Trim()  # the one place the version is set
Compress-Archive -Path "desktop\dist\Reel Framer" -DestinationPath desktop\dist\ReelFramer-portable.zip -Force
$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) { $iscc = Get-Item "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" -ErrorAction SilentlyContinue }
if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found: choco install innosetup" }
& $iscc desktop\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem desktop\dist | Format-Table Name, Length
