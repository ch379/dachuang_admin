$ErrorActionPreference = "SilentlyContinue"

$targets = Get-CimInstance Win32_Process | Where-Object {
    ($_.CommandLine -like "*uvicorn app.main:app*") -or
    ($_.CommandLine -like "*admin-frontend*react-scripts start*") -or
    ($_.CommandLine -like "*user-frontend*react-scripts start*") -or
    ($_.CommandLine -like "*traffic-monitoring*react-scripts start*")
}

if (-not $targets) {
    Write-Host "No backend or frontend processes found."
    exit 0
}

foreach ($p in $targets) {
    Stop-Process -Id $p.ProcessId -Force
    Write-Host "Stopped PID=$($p.ProcessId)"
}

Write-Host "All matched backend/frontend processes have been closed."
