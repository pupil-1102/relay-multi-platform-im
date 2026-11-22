# Relay one-command launcher (Windows PowerShell)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "[Relay] creating virtual environment..." -ForegroundColor Cyan
  if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { python -m venv .venv }
  } else {
    python -m venv .venv
  }
}

& ".\.venv\Scripts\python.exe" -c "import fastapi, sqlalchemy" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "[Relay] installing backend dependencies..." -ForegroundColor Cyan
  & ".\.venv\Scripts\python.exe" -m pip install -r "backend\requirements-dev.txt"
}

if (-not (Test-Path "frontend\dist\index.html")) {
  Write-Host "[Relay] building frontend..." -ForegroundColor Cyan
  npm --prefix frontend install --no-audit --no-fund
  npm --prefix frontend run build
}

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "[Relay] created .env from .env.example (demo mode)" -ForegroundColor DarkGray
}

Write-Host "[Relay] applying database migrations..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m backend.app.migrate
if ($LASTEXITCODE -ne 0) {
  Write-Host "[Relay] migration failed; refusing to start with a partial schema." -ForegroundColor Red
  exit 1
}

Write-Host ""
Write-Host "  Relay is starting at http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "  Demo login: click '进入演示工作台'" -ForegroundColor DarkGray
Write-Host ""

Start-Job -ScriptBlock {
  Start-Sleep -Seconds 3
  Start-Process "http://127.0.0.1:8000"
} | Out-Null

& ".\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
