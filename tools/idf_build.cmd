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
set "IDF_ROOT=D:\ESP32-S3\Espressif\frameworks\esp-idf-v5.5.3"
set "IDF_TOOLS_PATH=D:\ESP32-S3\Espressif"
set "IDF_PATH=%IDF_ROOT%"
set "PYENV=D:\ESP32-S3\Espressif\python_env\idf5.5_py3.11_env"
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

"%PY%" "%IDF_ROOT%\tools\idf_tools.py" export --format key-value > "%TEMP%\idfenv_build.txt" 2>nul
for /f "usebackq delims=" %%L in ("%TEMP%\idfenv_build.txt") do set "%%L"
set "PATH=%PATH%;%PATH_ORIG%"

set "PROJ=%~dp0..\firmware"
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=build"
shift

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
    set "PORT=%~1"
    if "%PORT%"=="" (
        echo [idf_build] usage: idf_build.cmd flash COMx
        exit /b 2
    )
    "%PY%" "%IDF_ROOT%\tools\idf.py" -C "%PROJ%" %BUILDARG% %SDKCONFARG% %TARGETARG% -p %PORT% flash
) else (
    "%PY%" "%IDF_ROOT%\tools\idf.py" -C "%PROJ%" %BUILDARG% %SDKCONFARG% %TARGETARG% %ACTION%
)
exit /b %ERRORLEVEL%
