# One-time setup for Email Triage. Right-click > Run with PowerShell, or run from a terminal:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== Email Triage setup ==" -ForegroundColor Cyan

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "Python 3.11+ is required. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then re-run." -ForegroundColor Red
    exit 1
}
$ver = & $py.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
Write-Host "Python $ver found at $($py.Source)"

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    & $py.Source -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Write-Host "Installing dependencies..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Created .env. Add your keys:" -ForegroundColor Yellow
    Write-Host "  TYPESAFE_API_KEY  (https://console.typesafe.ai/)"
    Write-Host "  OPENAI_API_KEY    (optional, enables reply drafts)"
    notepad .env
    Write-Host "Press Enter once you've saved .env..."
    [void][System.Console]::ReadLine()
}

Write-Host ""
Write-Host "Checking connections..." -ForegroundColor Cyan
& $venvPy -m emailtriage check
if ($LASTEXITCODE -ne 0) {
    Write-Host "Fix the items above and re-run setup.ps1 (or: .venv\Scripts\python -m emailtriage check)." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Learning your writing style from Sent Items (stays on this PC)..." -ForegroundColor Cyan
& $venvPy -m emailtriage learn-style

Write-Host ""
Write-Host "Done. Start the dashboard any time with start.cmd" -ForegroundColor Green
Write-Host "Tip: run '.venv\Scripts\python -m emailtriage sample --hours 24' to preview decisions without saving anything."
