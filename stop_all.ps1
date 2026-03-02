$ErrorActionPreference = "SilentlyContinue"

$targets = Get-CimInstance Win32_Process | Where-Object {
    ($_.CommandLine -like "*uvicorn app.main:app*") -or
    ($_.CommandLine -like "*react-scripts start*")
}

if (-not $targets) {
    Write-Host "未发现运行中的前后端进程。"
    exit 0
}

foreach ($p in $targets) {
    Stop-Process -Id $p.ProcessId -Force
    Write-Host "已停止 PID=$($p.ProcessId)"
}

Write-Host "已关闭前后端进程。"

