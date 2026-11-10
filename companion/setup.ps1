# setup.ps1 —— 创建 .venv 并安装 pcmond 依赖（不需要管理员权限）
# 用法：powershell -ExecutionPolicy Bypass -File .\setup.ps1 [-WithTray]
#   -WithTray  额外安装系统托盘依赖（pystray + Pillow，默认不装）
param([switch]$WithTray)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$basePython = "C:\Users\Lenovo\miniconda3\python.exe"
if (-not (Test-Path $basePython)) {
    Write-Host "[warn] 未找到 $basePython，改用 PATH 里的 python" -ForegroundColor Yellow
    $basePython = "python"
}

Write-Host "[1/3] 基础 Python：$basePython"
& $basePython -c "import sys; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "基础 Python 不可用" }

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Host "[2/3] 复用已有 .venv"
} else {
    Write-Host "[2/3] 创建虚拟环境 .venv ..."
    & $basePython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建 .venv 失败（退出码 $LASTEXITCODE）" }
}

Write-Host "[3/3] 安装依赖（默认源失败时自动换清华镜像）..."
& $venvPython -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[warn] 默认源失败，改用清华镜像重试" -ForegroundColor Yellow
    & $venvPython -m pip install --disable-pip-version-check -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}

& $venvPython -c "import psutil, serial; print('psutil', psutil.__version__, '| pyserial', serial.__version__)"

if ($WithTray) {
    Write-Host "安装可选托盘依赖（pystray + Pillow）..."
    & $venvPython -m pip install --disable-pip-version-check -r requirements-tray.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[warn] 默认源失败，改用清华镜像重试" -ForegroundColor Yellow
        & $venvPython -m pip install --disable-pip-version-check -r requirements-tray.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        if ($LASTEXITCODE -ne 0) { throw "托盘依赖安装失败" }
    }
    & $venvPython -c "import pystray, PIL; print('pystray', pystray.__version__, '| Pillow', PIL.__version__)"
    Write-Host "托盘依赖已安装（pythonw 启动时会出现托盘图标）" -ForegroundColor Green
} else {
    Write-Host "未安装托盘依赖（想要托盘：.\setup.ps1 -WithTray）"
}

Write-Host "完成。验证：.\.venv\Scripts\python.exe -m pcmond --once" -ForegroundColor Green
Write-Host "后台启动：powershell -ExecutionPolicy Bypass -File .\run.ps1"
