param(
    [switch]$InstallDeps,
    [int]$Port = 3002
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$UserRoot = Join-Path $Root "user-frontend"
$PackageJson = Join-Path $UserRoot "package.json"

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command not found: $Name. Please install it and add it to PATH."
    }
}

Assert-Command "npm"

if (-not (Test-Path $PackageJson)) {
    Write-Host "[user] user-frontend is not ready yet. Skipped." -ForegroundColor Yellow
    exit 0
}

if ($InstallDeps -or -not (Test-Path (Join-Path $UserRoot "node_modules\react-scripts"))) {
    Write-Host "[user] Installing npm dependencies..." -ForegroundColor Cyan
    Push-Location $UserRoot
    try {
        npm install
    } finally {
        Pop-Location
    }
}

$userCmd = "Set-Location '$UserRoot'; `$env:PORT='$Port'; npm start"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $userCmd | Out-Null

Write-Host "User frontend started: http://localhost:$Port" -ForegroundColor Green
