param(
    [switch]$InstallDeps,
    [int]$Port = 3001
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AdminRoot = Join-Path $Root "admin-frontend"

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command not found: $Name. Please install it and add it to PATH."
    }
}

Assert-Command "npm"

if (-not (Test-Path $AdminRoot)) {
    throw "admin-frontend directory not found: $AdminRoot"
}

if ($InstallDeps -or -not (Test-Path (Join-Path $AdminRoot "node_modules\react-scripts"))) {
    Write-Host "[admin] Installing npm dependencies..." -ForegroundColor Cyan
    Push-Location $AdminRoot
    try {
        npm install
    } finally {
        Pop-Location
    }
}

$adminCmd = "Set-Location '$AdminRoot'; `$env:PORT='$Port'; npm start"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $adminCmd | Out-Null

Write-Host "Admin frontend started: http://localhost:$Port" -ForegroundColor Green
