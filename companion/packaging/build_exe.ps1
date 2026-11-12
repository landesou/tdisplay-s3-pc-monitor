# build_exe.ps1 —— 用 PyInstaller 打单文件 windowed exe（产物 companion/dist/pcmond.exe）
# 用法：powershell -ExecutionPolicy Bypass -File .\packaging\build_exe.ps1 [-SkipInstall]
param([switch]$SkipInstall)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot   # companion/
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "未找到 .venv，请先运行 companion\setup.ps1"
}

if (-not $SkipInstall) {
    Write-Host "[1/3] 安装构建依赖 PyInstaller..."
    & $python -m pip install --disable-pip-version-check -q "pyinstaller>=6,<7"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败（可加 -SkipInstall 跳过）" }
} else {
    Write-Host "[1/3] 跳过 PyInstaller 安装"
}

Write-Host "[2/3] 生成程序化图标 assets\pcmond.ico ..."
& $python packaging\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "生成图标失败" }

Write-Host "[3/3] PyInstaller 打包（onefile / windowed）..."
& $python -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $root "dist") `
    --workpath (Join-Path $root "build") `
    (Join-Path $root "packaging\pcmond.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$exe = Join-Path $root "dist\pcmond.exe"
if (-not (Test-Path $exe)) { throw "没有找到产物：$exe" }
$sizeMb = (Get-Item $exe).Length / 1MB
Write-Host ("完成：{0}（{1:N1} MB）" -f $exe, $sizeMb) -ForegroundColor Green
Write-Host "验证：& '$exe' --once"
