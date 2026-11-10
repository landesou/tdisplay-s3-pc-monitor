# install_startup.ps1 —— 当前用户登录自启（启动文件夹快捷方式，不需要管理员权限/不改注册表）
# 用法：powershell -ExecutionPolicy Bypass -File .\install_startup.ps1
#       -StartupDir 仅供测试指定别的目录
param([string]$StartupDir = [Environment]::GetFolderPath("Startup"))
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "未找到 .venv，请先运行：powershell -ExecutionPolicy Bypass -File .\setup.ps1"
}

if (-not (Test-Path $StartupDir)) {
    New-Item -ItemType Directory -Path $StartupDir -Force | Out-Null
}
$shortcutPath = Join-Path $StartupDir "pcmond.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m pcmond"
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7  # 最小化（pythonw 本身无窗口）
$shortcut.Description = "T-Display-S3 PC monitor daemon (pcmond)"
$shortcut.Save()

Write-Host "已创建登录自启：$shortcutPath" -ForegroundColor Green
Write-Host "目标：$pythonw -m pcmond"
Write-Host "工作目录：$root"
Write-Host "取消自启：powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1"
