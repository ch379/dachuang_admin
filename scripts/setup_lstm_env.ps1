param(
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $root (".venv" + $PythonVersion.Replace(".", ""))

function Resolve-PythonInterpreter {
    param([string]$Version)

    try {
        $null = & py -$Version -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $exe = & py -$Version -c "import sys; print(sys.executable)"
            return $exe.Trim()
        }
    } catch {}

    $candidates = @(
        "$env:LocalAppData\Programs\Python\Python311\python.exe",
        "$env:LocalAppData\Programs\Python\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python312\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

$pythonExe = Resolve-PythonInterpreter -Version $PythonVersion
if (-not $pythonExe) {
    Write-Host "No Python $PythonVersion interpreter found." -ForegroundColor Yellow
    Write-Host "Install Python 3.11 or 3.12 first, then rerun this script." -ForegroundColor Yellow
    Write-Host "Expected commands after install:" -ForegroundColor Cyan
    Write-Host "  .\scripts\setup_lstm_env.ps1 -PythonVersion 3.11" -ForegroundColor Cyan
    exit 1
}

Write-Host "Using interpreter: $pythonExe" -ForegroundColor Cyan
& $pythonExe -m venv $venvPath

$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
& $venvPython -m pip install -r (Join-Path $root "requirements-ml.txt")

Write-Host "LSTM environment is ready: $venvPath" -ForegroundColor Green
Write-Host "Next step:" -ForegroundColor Cyan
Write-Host "  .\scripts\run_lstm_compare.ps1 -PythonVersion $PythonVersion" -ForegroundColor Cyan
