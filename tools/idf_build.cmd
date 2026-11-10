@echo off
rem =====================================================================
rem  ESP-IDF build entry point (Windows / cmd.exe only)
rem
rem  Usage:
rem    tools\idf_build.cmd                 -> build
rem    tools\idf_build.cmd flash COM5      -> flash
rem    tools\idf_build.cmd menuconfig      -> menuconfig
rem    set BUILD_DIR=build-foo + call      -> isolated build dir (parallel work)
rem
rem  Why this wrapper exists (see docs/journal.md):
rem    1) exporting idf_tools.py's Windows-style PATH into bash kills coreutils;
rem    2) exporting IDF_PYTHON_ENV_PATH from bash gets mangled by MSYS and idf.py
rem       then falsely reports "python.exe doesn't exist";
rem    3) the exported PATH has no System32, so the original PATH must be
rem       appended back before using copy/dir/etc.
rem
rem  KEEP THIS FILE ASCII-ONLY. UTF-8 Chinese inside a .cmd is parsed as cp936
rem  mojibake and cmd then executes garbage (we lost a whole evening to this).
rem =====================================================================
setlocal
set "PATH_ORIG=%PATH%"
rem Paths are overridable by environment variables (handy when the repo is
rem cloned to another machine). Defaults are this dev box's ESP-IDF install.
if not defined ESP_IDF_DIR set "ESP_IDF_DIR=D:\ESP32-S3\Espressif\frameworks\esp-idf-v5.5.3"
if not defined ESP_IDF_TOOLS_DIR set "ESP_IDF_TOOLS_DIR=D:\ESP32-S3\Espressif"
if not defined ESP_IDF_PYENV set "ESP_IDF_PYENV=%ESP_IDF_TOOLS_DIR%\python_env\idf5.5_py3.11_env"
set "IDF_ROOT=%ESP_IDF_DIR%"
set "IDF_TOOLS_PATH=%ESP_IDF_TOOLS_DIR%"
set "IDF_PATH=%IDF_ROOT%"
set "PYENV=%ESP_IDF_PYENV%"
set "PY=%PYENV%\Scripts\python.exe"
set "TARGET=esp32s3"

if not exist "%PY%" (
    echo [idf_build] FATAL: IDF python env not found: %PY%
    exit /b 2
)
if not exist "%IDF_ROOT%\tools\idf.py" (
    echo [idf_build] FATAL: ESP-IDF not found at %IDF_ROOT%
    exit /b 2
)

rem Take the IDF tool environment (cmake/ninja/toolchain/IDF_PYTHON_ENV_PATH ...).
rem NOTE: per-process temp file. Two parallel builds (two agents at once) used to
rem race on one shared file and get a truncated env, which surfaced as the bogus
rem "cmake must be available on the PATH" error.
if not defined TEMP set "TEMP=%TMP%"
set "IDF_ENV_TMP=%TEMP%\idfenv_build_%RANDOM%%RANDOM%%RANDOM%.txt"
"%PY%" "%IDF_ROOT%\tools\idf_tools.py" export --format key-value > "%IDF_ENV_TMP%" 2>nul
for /f "usebackq delims=" %%L in ("%IDF_ENV_TMP%") do set "%%L"
del /q "%IDF_ENV_TMP%" 2>nul
set "PATH=%PATH%;%PATH_ORIG%"

set "PROJ=%~dp0..\firmware"
set "ACTION=%~1"
set "PORT=%~2"
if "%ACTION%"=="" set "ACTION=build"

set "BUILDARG="
set "SDKCONFARG="
set "TARGETARG="
if defined BUILD_DIR (
    rem isolated build dir for parallel agent work: absolute paths matter here,
    rem because idf.py resolves -B and SDKCONFIG relative to the CALLER's cwd
    set "BUILDARG=-B %PROJ%\%BUILD_DIR%"
    set "SDKCONFARG=-D SDKCONFIG=%PROJ%\%BUILD_DIR%\sdkconfig"
    set "TARGETARG=-D IDF_TARGET=%TARGET%"
)

if not exist "%PROJ%\sdkconfig" if not defined BUILD_DIR (
    echo [idf_build] first run: set-target %TARGET%
    "%PY%" "%IDF_ROOT%\tools\idf.py" -C "%PROJ%" set-target %TARGET%
    if errorlevel 1 exit /b 3
)

if /i "%ACTION%"=="flash" (
    if "%PORT%"=="" (
        echo [idf_build] usage: idf_build.cmd flash COMn
        exit /b 2
    )
    "%PY%" "%IDF_ROOT%\tools\idf.py" -C "%PROJ%" %BUILDARG% %SDKCONFARG% %TARGETARG% -p %PORT% flash
    if errorlevel 1 exit /b 4
    rem ESP32-S3 native USB: a USB-triggered reset always lands in the ROM
    rem downloader (GPIO0 is pulled low by the USB-JTAG reset path), so after
    rem flashing the board would otherwise sit in download mode until a physical
    rem power cycle. A watchdog reset lets the ROM boot the app again, so one
    rem flash = one running app, no cable replug needed.
    echo [idf_build] booting the app back from download mode (watchdog reset)
    "%PY%" "%IDF_ROOT%\tools\idf.py" --version >nul 2>&1
    "%PY%" -m esptool --chip esp32s3 -p %PORT% --before no_reset --after watchdog_reset flash_id >nul 2>&1
) else (
    "%PY%" "%IDF_ROOT%\tools\idf.py" -C "%PROJ%" %BUILDARG% %SDKCONFARG% %TARGETARG% %ACTION%
)
exit /b %ERRORLEVEL%
