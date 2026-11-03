# Creates .venv in the repo with the ESPHome version this firmware targets.
# Run once:  double-click tools\setup-env.cmd
#      or:   powershell -NoProfile -ExecutionPolicy Bypass -File tools\setup-env.ps1
# No admin rights needed; nothing outside the repo folder is changed.

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo '.venv'
$esphomeVersion = '2026.10.2'

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Fail 'The Python launcher (py) was not found. Install Python 3.13 from python.org or winget.'
}

# ESPHome 2026.10 needs Python 3.12-3.14.
$pyver = $null
foreach ($v in '3.13', '3.12', '3.14') {
  & py "-$v" -c "import sys" *> $null
  if ($LASTEXITCODE -eq 0) { $pyver = $v; break }
}
if (-not $pyver) { Fail 'No Python 3.12, 3.13 or 3.14 found. Install Python 3.13 first.' }

$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) {
  Write-Host "Creating $venv with Python $pyver ..."
  & py "-$pyver" -m venv $venv
  if ($LASTEXITCODE -ne 0) { Fail 'Could not create the virtual environment.' }
}

Write-Host 'Updating pip ...'
& $python -m pip install --upgrade pip
Write-Host "Installing ESPHome $esphomeVersion (a few minutes) ..."
& $python -m pip install "esphome==$esphomeVersion"
if ($LASTEXITCODE -ne 0) { Fail 'ESPHome install failed; see the messages above.' }

& (Join-Path $venv 'Scripts\esphome.exe') version
Write-Host ''
Write-Host 'Done. In a terminal in the repo, activate it with:  .\.venv\Scripts\Activate.ps1' -ForegroundColor Green
