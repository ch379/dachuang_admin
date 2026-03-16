param(
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $root (".venv" + $PythonVersion.Replace(".", ""))
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "LSTM venv not found: $venvPath" -ForegroundColor Yellow
    Write-Host "Run .\scripts\setup_lstm_env.ps1 -PythonVersion $PythonVersion first." -ForegroundColor Yellow
    exit 1
}

Push-Location $root
try {
    & $venvPython scripts\train_lstm.py
    & $venvPython scripts\compare_models.py
} finally {
    Pop-Location
}
