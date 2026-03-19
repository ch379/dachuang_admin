param(
    [switch]$InstallDeps,
    [switch]$ImportData,
    [int]$Port = 8000
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

if ($InstallDeps) {
    Write-Host "[backend] Installing Python dependencies..." -ForegroundColor Cyan
    python -m pip install -r "$Root\requirements.txt"
}

$dbPath = Join-Path $Root "traffic.db"
if ($ImportData -or -not (Test-Path $dbPath)) {
    Write-Host "[backend] Importing Excel data..." -ForegroundColor Cyan
    python -m scripts.import_traffic_excel --excel "$Root\traffic_turning_5min.xlsx" --truncate
}

$backendCmd = "Set-Location '$Root'; python -m uvicorn app.main:app --reload --host 0.0.0.0 --port $Port"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd | Out-Null

Write-Host "Backend started: http://127.0.0.1:$Port" -ForegroundColor Green
