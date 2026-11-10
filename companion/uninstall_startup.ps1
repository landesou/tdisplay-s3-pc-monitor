# uninstall_startup.ps1 —— 删除登录自启快捷方式，并停掉正在运行的 pcmond
# 用法：powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1
#       -KeepRunning 只删自启、不停进程
param(
    [string]$StartupDir = [Environment]::GetFolderPath("Startup"),
    [switch]$KeepRunning
)
$ErrorActionPreference = "Stop"

$shortcutPath = Join-Path $StartupDir "pcmond.lnk"
if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "已删除自启快捷方式：$shortcutPath" -ForegroundColor Green
} else {
    Write-Host "没有找到自启快捷方式：$shortcutPath"
}

if (-not $KeepRunning) {
    $running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*-m pcmond*" }
    if ($running) {
        foreach ($process in $running) {
            Stop-Process -Id $process.ProcessId -Force
            Write-Host "已停止进程 PID $($process.ProcessId)"
        }
    } else {
        Write-Host "没有正在运行的 pcmond 进程"
    }
}
