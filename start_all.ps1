param(
    [switch]$InstallDeps,
    [switch]$ImportData,
    [switch]$IncludeUser
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[1/3] Starting backend..." -ForegroundColor Cyan
& "$Root\start_backend.ps1" -InstallDeps:$InstallDeps -ImportData:$ImportData -Port 8000

Start-Sleep -Seconds 1

Write-Host "[2/3] Starting admin frontend..." -ForegroundColor Cyan
& "$Root\start_admin.ps1" -InstallDeps:$InstallDeps -Port 3001

if ($IncludeUser) {
    Start-Sleep -Seconds 1
    Write-Host "[3/3] Starting user frontend..." -ForegroundColor Cyan
    & "$Root\start_user.ps1" -InstallDeps:$InstallDeps -Port 3002
}
else {
    Write-Host "[3/3] User frontend skipped. Use -IncludeUser when it is ready." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Started successfully:" -ForegroundColor Green
Write-Host "- Backend: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "- Admin frontend: http://localhost:3001" -ForegroundColor Green
if ($IncludeUser) {
    Write-Host "- User frontend: http://localhost:3002" -ForegroundColor Green
}
Write-Host "To stop all, run: .\stop_all.ps1" -ForegroundColor Yellow
