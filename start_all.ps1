param(
    [switch]$InstallDeps,
    [switch]$ImportData
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command not found: $Name. Please install it and add it to PATH."
    }
}

Assert-Command "python"
Assert-Command "npm"

if ($InstallDeps) {
    Write-Host "[1/4] Installing backend dependencies..." -ForegroundColor Cyan
    python -m pip install -r "$Root\requirements.txt"

    Write-Host "[2/4] Installing frontend dependencies..." -ForegroundColor Cyan
    Push-Location "$Root\traffic-monitoring"
    npm install
    Pop-Location
}

$dbPath = Join-Path $Root "traffic.db"
if ($ImportData -or -not (Test-Path $dbPath)) {
    Write-Host "[3/4] Importing Excel data into database..." -ForegroundColor Cyan
    python -m scripts.import_traffic_excel --excel "$Root\traffic_turning_5min.xlsx" --truncate
}

Write-Host "[4/4] Starting backend and frontend..." -ForegroundColor Cyan

$backendCmd = "Set-Location '$Root'; python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
$frontendCmd = "Set-Location '$Root\traffic-monitoring'; npm start"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd | Out-Null
Start-Sleep -Seconds 1
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd | Out-Null

Write-Host "Started successfully:" -ForegroundColor Green
Write-Host "- Backend: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "- Frontend: http://localhost:3000" -ForegroundColor Green
Write-Host "To stop, run: .\stop_all.ps1" -ForegroundColor Yellow

