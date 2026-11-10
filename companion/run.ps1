# run.ps1 —— 用 venv 的 pythonw 无窗口启动 pcmond
# 用法：powershell -ExecutionPolicy Bypass -File .\run.ps1
#       powershell -ExecutionPolicy Bypass -File .\run.ps1 -Foreground   # 前台调试
param([switch]$Foreground)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "未找到 .venv，请先运行：powershell -ExecutionPolicy Bypass -File .\setup.ps1"
}

if ($Foreground) {
    & $python -m pcmond --verbose
    exit $LASTEXITCODE
}

$process = Start-Process -FilePath $pythonw -ArgumentList "-m", "pcmond" -WorkingDirectory $root -PassThru
Write-Host "pcmond 已在后台启动（PID $($process.Id)）"
Write-Host "日志：$env:USERPROFILE\.tdisplay-pcmon\pcmond.log"
